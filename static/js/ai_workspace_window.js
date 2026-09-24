/** Modeless window geometry/lifecycle shared by every authenticated page. */
export function createAssistantWindow({ modal, container, fab, state, onOpen, onClose }) {
    let gesture = null, closeAnimation = null;
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
        for (const [key, value] of Object.entries(next)) container.style[key] = `${value}px`;
        container.style.right = container.style.bottom = 'auto';
        if (persist) state.patch({ rect: next });
    }
    function maximize(value) {
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
        const entering = !open;
        closeAnimation?.cancel(); closeAnimation = null;
        open = true;
        modal.style.display = 'block'; modal.setAttribute('aria-hidden', 'false');
        container.inert = false;
        fab.style.display = 'none';
        if (maximized) maximize(true); else apply(state.value.rect);
        if (entering && !matchMedia('(prefers-reduced-motion: reduce)').matches) {
            container.animate([{ opacity: 0, transform: 'translateY(12px) scale(.98)' }, { opacity: 1, transform: 'none' }], { duration: 180, easing: 'cubic-bezier(.2,.7,.3,1)' });
        }
        state.patch({ open: true });
        window.dispatchEvent(new CustomEvent('ai-workspace:opened'));
        onOpen?.({ focus });
    }
    function close({ persist = true } = {}) {
        if (!open) return;
        open = false;
        if (persist) state.patch({ open: false });
        onClose?.();
        container.inert = true;
        const finish = () => {
            if (open) return;
            modal.style.display = 'none'; modal.setAttribute('aria-hidden', 'true');
            fab.style.display = ''; container.inert = false;
            maximize(false);
            window.dispatchEvent(new CustomEvent('ai-workspace:closed'));
        };
        if (matchMedia('(prefers-reduced-motion: reduce)').matches) finish();
        else {
            closeAnimation = container.animate([{ opacity: 1, transform: 'translateY(0) scale(1)' }, { opacity: 0, transform: 'translateY(8px) scale(.98)' }], { duration: 120, easing: 'ease-in', fill: 'none' });
            closeAnimation.finished.then(finish).catch(() => {});
        }
    }
    function start(event) {
        const handle = event.target.closest('.resizer');
        if (!handle && (!event.target.closest('.ai-workspace-header') || event.target.closest('button,a,input,select,textarea'))) return;
        if (event.button > 0 || maximized) return;
        event.preventDefault();
        gesture = { id: event.pointerId, x: event.clientX, y: event.clientY, rect: container.getBoundingClientRect(), direction: handle?.dataset.resize || '', target: event.target };
        container.classList.add('is-manipulating');
        container.setPointerCapture(event.pointerId);
    }
    function move(event) {
        if (!gesture || event.pointerId !== gesture.id) return;
        const { rect, direction: d } = gesture;
        const dx = event.clientX - gesture.x, dy = event.clientY - gesture.y;
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
        apply({ left, top, width, height });
    }
    function stop() { gesture = null; container.classList.remove('is-manipulating'); }
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
    const resize = () => maximized ? maximize(true) : apply(state.value.rect, false);
    window.addEventListener('resize', resize, opts);
    window.visualViewport?.addEventListener('resize', resize, opts);
    container.addEventListener('keydown', event => {
        const blocking = [...document.querySelectorAll('[aria-modal="true"],dialog[open]')].some(node => node !== container && node.getClientRects().length && getComputedStyle(node).visibility !== 'hidden');
        if (event.key === 'Escape' && !event.defaultPrevented && !blocking) {
            event.preventDefault(); event.stopPropagation(); close(); fab.focus({ preventScroll: true });
        }
    }, opts);
    return {
        open: show, close, toggleFullscreen: () => maximize(!maximized),
        ensureVisible: resize,
        get isOpen() { return open; },
        restore() { if (state.value.open) show({ focus: false }); },
        suspend() { modal.style.visibility = 'hidden'; fab.style.visibility = 'hidden'; },
        resume() { modal.style.visibility = ''; fab.style.visibility = ''; },
        destroy() { abort.abort(); closeAnimation?.cancel(); stop(); },
    };
}
