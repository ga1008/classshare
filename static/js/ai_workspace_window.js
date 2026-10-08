import { cancelOverlayMotion, setOverlayOpen } from './ui_overlay_motion.js';

/** Modeless window geometry/lifecycle shared by every authenticated page. */
export function createAssistantWindow({ modal, container, fab, state, onOpen, onClose }) {
    let gesture = null, gestureFrame = null, pendingPointer = null;
    // Keep the first closed frame unrendered until the shared presence owner
    // reveals it. The legacy display:none alone can be cleared before priming.
    modal.hidden = true;
    modal.dataset.lqPresence = 'domain';
    container.setAttribute('data-ui-overlay-surface', '');
    let generation = 0;
    let maximized = false;
    let open = false;
    const abort = new AbortController();
    const opts = { signal: abort.signal };
    const viewport = () => ({ width: window.visualViewport?.width || innerWidth, height: window.visualViewport?.height || innerHeight });
    function constrain(value = {}) {
        const { width: vw, height: vh } = viewport();
        const gap = vw < 600 ? 8 : 16;
        const width = Math.min(Math.max(Number(value.width) || Math.min(520, vw - gap * 2), Math.min(320, vw - gap * 2)), vw - gap * 2);
        const height = Math.min(Math.max(Number(value.height) || Math.min(650, vh * .82), Math.min(360, vh - gap * 2)), vh - gap * 2);
        const left = Math.max(gap, Math.min(Number.isFinite(value.left) ? value.left : vw - width - gap, vw - width - gap));
        const top = Math.max(gap, Math.min(Number.isFinite(value.top) ? value.top : vh - height - gap, vh - height - gap));
        return { left, top, width, height };
    }
    function apply(rect, persist = true) {
        const next = constrain(rect);
        for (const [key, value] of Object.entries(next)) {
            if (container.style[key] !== `${value}px`) container.style[key] = `${value}px`;
        }
        if (container.style.right !== 'auto') container.style.right = 'auto';
        if (container.style.bottom !== 'auto') container.style.bottom = 'auto';
        if (persist) saveRect(next);
        return next;
    }
    function saveRect(rect) {
        if (Object.keys(rect).some(key => state.value.rect?.[key] !== rect[key])) state.patch({ rect });
    }
    function maximize(value) {
        stop();
        maximized = value;
        container.classList.toggle('fullscreen', value);
        const button = container.querySelector('#ai-chat-btn-fullscreen');
        button?.setAttribute('aria-pressed', String(value));
        button?.setAttribute('aria-label', value ? '还原窗口' : '最大化');
        if (button) button.title = value ? '还原窗口' : '最大化';
        if (value) {
            const v = viewport();
            Object.assign(container.style, { left: '8px', top: '8px', width: `${v.width - 16}px`, height: `${v.height - 16}px`, right: 'auto', bottom: 'auto' });
        } else apply(state.value.rect);
    }
    function show({ focus = true } = {}) {
        stop();
        generation++;
        open = true;
        modal.style.display = 'block'; modal.setAttribute('aria-hidden', 'false');
        container.inert = false;
        fab.style.display = 'none';
        if (maximized) maximize(true); else apply(state.value.rect);
        void setOverlayOpen(modal, true);
        state.patch({ open: true });
        window.dispatchEvent(new CustomEvent('ai-workspace:opened'));
        onOpen?.({ focus });
    }
    function close({ persist = true } = {}) {
        if (!open) return;
        stop();
        const returnFocus = container.contains(document.activeElement);
        const ticket = ++generation;
        open = false;
        if (persist) state.patch({ open: false });
        onClose?.();
        container.inert = true;
        const finish = () => {
            if (open || ticket !== generation) return;
            modal.style.display = 'none'; modal.setAttribute('aria-hidden', 'true');
            fab.style.display = ''; container.inert = false;
            maximize(false);
            if (returnFocus && [document.body, document.documentElement, modal].includes(document.activeElement)) {
                fab.focus({ preventScroll: true });
            }
            window.dispatchEvent(new CustomEvent('ai-workspace:closed'));
        };
        void setOverlayOpen(modal, false).then(completed => { if (completed) finish(); });
    }
    function start(event) {
        const handle = event.target.closest('.resizer');
        if (!handle && (!event.target.closest('.ai-workspace-header') || event.target.closest('button,a,input,select,textarea'))) return;
        if (event.button > 0 || maximized || gesture) return;
        event.preventDefault();
        gesture = { id: event.pointerId, x: event.clientX, y: event.clientY, rect: container.getBoundingClientRect(), direction: handle?.dataset.resize || '', target: event.target };
        container.classList.add('is-manipulating');
        container.setPointerCapture(event.pointerId);
    }
    function move(event) {
        if (!gesture || event.pointerId !== gesture.id) return;
        pendingPointer = { x: event.clientX, y: event.clientY };
        if (gestureFrame === null) gestureFrame = window.requestAnimationFrame(flushGesture);
    }
    function flushGesture() {
        gestureFrame = null;
        if (!gesture || !pendingPointer) return;
        const point = pendingPointer;
        pendingPointer = null;
        const { rect, direction: d } = gesture;
        const dx = point.x - gesture.x, dy = point.y - gesture.y;
        let { width, height, left, top } = rect;
        const v = viewport();
        const minWidth = Math.min(320, v.width - 16), minHeight = Math.min(360, v.height - 16);
        if (!d) { left += dx; top += dy; }
        else {
            if (d.includes('left')) { left = Math.max(8, Math.min(rect.right - minWidth, rect.left + dx)); width = rect.right - left; }
            if (d.includes('right')) width = Math.max(minWidth, Math.min(v.width - left - 8, rect.width + dx));
            if (d.includes('top')) { top = Math.max(8, Math.min(rect.bottom - minHeight, rect.top + dy)); height = rect.bottom - top; }
            if (d.includes('bottom')) height = Math.max(minHeight, Math.min(v.height - top - 8, rect.height + dy));
        }
        // Keep real geometry (and therefore the glass's backdrop sampling), but
        // paint at most once per frame and leave synchronous storage to stop().
        gesture.latest = apply({ left, top, width, height }, false);
    }
    function stop(event) {
        if (event?.pointerId !== undefined && event.pointerId !== gesture?.id) return;
        if (gesture && event?.type === 'pointerup') pendingPointer = { x: event.clientX, y: event.clientY };
        if (gestureFrame !== null) window.cancelAnimationFrame(gestureFrame);
        flushGesture();
        const ended = gesture;
        gesture = null;
        pendingPointer = null;
        if (ended?.latest) saveRect(ended.latest);
        container.classList.remove('is-manipulating');
        if (ended && container.hasPointerCapture(ended.id)) container.releasePointerCapture(ended.id);
    }
    for (const resizer of container.querySelectorAll('.resizer')) {
        resizer.dataset.resize = [...resizer.classList].find(c => c.startsWith('resizer-'))?.slice(8) || 'right';
    }
    modal.dataset.aiWindow = 'modeless';
    container.setAttribute('aria-modal', 'false');
    // A root portal avoids page shells becoming backdrop/stacking ancestors.
    document.body.append(modal);
    fab.addEventListener('click', () => show(), opts);
    container.querySelector('#ai-chat-btn-close')?.addEventListener('click', () => close(), opts);
    container.querySelector('#ai-chat-btn-fullscreen')?.addEventListener('click', () => maximize(!maximized), opts);
    container.addEventListener('pointerdown', start, opts);
    container.addEventListener('pointermove', move, opts);
    container.addEventListener('pointerup', stop, opts);
    container.addEventListener('pointercancel', stop, opts);
    container.addEventListener('lostpointercapture', stop, opts);
    const resize = () => { stop(); if (maximized) maximize(true); else apply(state.value.rect, false); };
    window.addEventListener('resize', resize, opts);
    window.visualViewport?.addEventListener('resize', resize, opts);
    window.addEventListener('pagehide', stop, opts);
    container.addEventListener('keydown', event => {
        const blocking = [...document.querySelectorAll('[aria-modal="true"],dialog[open]')].some(node => node !== container && node.getClientRects().length && getComputedStyle(node).visibility !== 'hidden');
        if (event.key === 'Escape' && !event.defaultPrevented && !blocking) {
            event.preventDefault(); event.stopPropagation(); close();
        }
    }, opts);
    return {
        open: show, close, toggleFullscreen: () => maximize(!maximized),
        ensureVisible: resize,
        get isOpen() { return open; },
        restore() { if (state.value.open) show({ focus: false }); },
        suspend() { modal.style.visibility = 'hidden'; fab.style.visibility = 'hidden'; },
        resume() { modal.style.visibility = ''; fab.style.visibility = ''; },
        destroy() { abort.abort(); generation++; cancelOverlayMotion(modal); stop(); },
    };
}
