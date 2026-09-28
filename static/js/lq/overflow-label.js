/** Explicit, delegated overflow-label enhancement. The caller owns the button,
 * its accessible name and its click/popover behavior. No discovery observer,
 * animation loop, content replacement or per-label event listener is installed.
 */
const OWNER = Symbol.for('lanshare.lq.overflow-label');
const TRIGGER = '[data-lq-overflow-label]';
const VIEWPORT = '[data-lq-overflow-viewport]';
const TEXT = '[data-lq-overflow-text]';
const properties = ['--lq-overflow-shift', '--lq-overflow-steps'];

export function bindOverflowLabels(root) {
    if (root?.nodeType !== 1) throw new TypeError('Overflow labels require an explicit Element owner');
    if (root[OWNER]) return root[OWNER];
    const win = root.ownerDocument.defaultView;
    const hover = win.matchMedia('(hover: hover) and (pointer: fine)');
    const reduced = win.matchMedia('(prefers-reduced-motion: reduce)');
    let active = null, hovered = null, focused = null, destroyed = false;

    const triggerFor = node => {
        const trigger = node?.closest?.(TRIGGER);
        return trigger && root.contains(trigger) ? trigger : null;
    };
    function stop() {
        if (!active) return;
        const { trigger, text, styles } = active;
        trigger.removeAttribute('data-lq-overflow-active');
        for (const [name, value, priority] of styles) {
            if (value) text.style.setProperty(name, value, priority);
            else text.style.removeProperty(name);
        }
        active = null;
    }
    function reveal(trigger) {
        if (destroyed || active?.trigger === trigger) return;
        stop();
        if (!trigger?.isConnected || !root.contains(trigger) || reduced.matches
            || trigger.matches(':disabled')
            || trigger.closest('[hidden], [inert], [aria-disabled="true"], [aria-busy="true"], [data-lq-disabled="true"]')) return;
        const viewport = trigger.querySelector(VIEWPORT);
        const text = viewport?.querySelector(TEXT);
        if (!text || text.closest(TRIGGER) !== trigger) return;
        const style = win.getComputedStyle(text);
        if (Number(style.getPropertyValue('--lq-overflow-enabled') || '1') === 0) return;
        // One read batch per interaction. Intrinsic text width is independent
        // of the transform; the button's own press/position transform is untouched.
        const distance = Math.ceil(Math.max(text.scrollWidth, text.getBoundingClientRect().width) - viewport.clientWidth);
        if (distance <= 1 || viewport.clientWidth <= 0) return;
        const styles = properties.map(name => [name, text.style.getPropertyValue(name), text.style.getPropertyPriority(name)]);
        active = { trigger, text, styles };
        text.style.setProperty('--lq-overflow-shift', `${style.direction === 'rtl' ? distance : -distance}px`);
        // Standard motion reads at about 50px/s, with a minimum 900ms reveal.
        // The shared duration continues to control speed, including live off.
        text.style.setProperty('--lq-overflow-steps', String(Math.max(5, distance / 9)));
        trigger.setAttribute('data-lq-overflow-active', '');
    }
    function pointerOver(event) {
        if (event.pointerType === 'touch' || !hover.matches) return;
        const trigger = triggerFor(event.target);
        if (!trigger || trigger.contains(event.relatedTarget)) return;
        hovered = trigger;
        reveal(trigger);
    }
    function pointerOut(event) {
        const trigger = triggerFor(event.target);
        if (!trigger || trigger.contains(event.relatedTarget)) return;
        if (hovered === trigger) hovered = null;
        reveal(focused || hovered);
    }
    function focusIn(event) {
        const trigger = triggerFor(event.target);
        if (!trigger?.matches(':focus-visible')) return;
        focused = trigger;
        reveal(trigger);
    }
    function focusOut(event) {
        const trigger = triggerFor(event.target);
        if (!trigger || trigger.contains(event.relatedTarget)) return;
        if (focused === trigger) focused = null;
        reveal(hovered);
    }
    function refresh() {
        hovered = null;
        focused = null;
        stop();
    }
    const pointerDown = event => { if (event.pointerType === 'touch') refresh(); };
    const listeners = { pointerover: pointerOver, pointerout: pointerOut, pointerdown: pointerDown, focusin: focusIn, focusout: focusOut };
    for (const [type, listener] of Object.entries(listeners)) root.addEventListener(type, listener);
    win.addEventListener('resize', refresh, { passive: true });
    win.addEventListener('blur', refresh);
    hover.addEventListener('change', refresh);
    const controller = { refresh, destroy() {
        if (destroyed) return;
        destroyed = true;
        refresh();
        for (const [type, listener] of Object.entries(listeners)) root.removeEventListener(type, listener);
        win.removeEventListener('resize', refresh);
        win.removeEventListener('blur', refresh);
        hover.removeEventListener('change', refresh);
        delete root[OWNER];
    } };
    root[OWNER] = controller;
    return controller;
}
