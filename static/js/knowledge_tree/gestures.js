// 普通滚轮以光标为锚缩放（按帧合并）；鼠标/笔左键拖动空白处平移。
// Ctrl/Meta 滚轮保留浏览器原生行为，触屏保留原生滑动。

/**
 * ctx: { viewport, ready() -> bool, zoomBy(factor, anchor|null), fit() }
 * Returns { dispose() } that cancels a pending zoom frame.
 */
export function bindViewportGestures({ viewport, ready, zoomBy, fit }) {
    const listeners = new AbortController();
    const options = { signal: listeners.signal };
    let wheelFactor = 1;
    let wheelAnchor = null;
    let wheelFrame = 0;
    viewport.addEventListener('wheel', event => {
        if (event.ctrlKey || event.metaKey || !ready()) return;
        event.preventDefault();
        const rect = viewport.getBoundingClientRect();
        const unit = event.deltaMode === 1 ? 40 : event.deltaMode === 2 ? viewport.clientHeight : 1;
        const delta = Math.max(-120, Math.min(120, event.deltaY * unit));
        wheelFactor *= Math.exp(-delta * 0.0018);
        wheelAnchor = { x: event.clientX - rect.left - viewport.clientLeft, y: event.clientY - rect.top - viewport.clientTop };
        if (!wheelFrame) wheelFrame = window.requestAnimationFrame(() => {
            wheelFrame = 0;
            if (ready()) zoomBy(wheelFactor, wheelAnchor);
            wheelFactor = 1;
        });
    }, { ...options, passive: false });
    viewport.addEventListener('keydown', event => {
        if (event.target !== viewport || !ready() || event.ctrlKey || event.metaKey || event.altKey) return;
        if (event.key === '+' || event.key === '=') { event.preventDefault(); zoomBy(1.2, null); }
        else if (event.key === '-' || event.key === '_') { event.preventDefault(); zoomBy(1 / 1.2, null); }
        else if (event.key === '0') { event.preventDefault(); fit(); }
    }, options);
    viewport.addEventListener('dblclick', event => {
        if (!ready() || event.target.closest('.kt-node, .kt-fold')) return;
        fit();
    }, options);

    let pan = null;
    viewport.addEventListener('pointerdown', event => {
        if (!ready() || event.pointerType === 'touch' || event.button !== 0) return;
        if (event.target.closest('.kt-node, .kt-fold')) return;
        // Leave the native scrollbars alone.
        const rect = viewport.getBoundingClientRect();
        if (event.clientX - rect.left >= viewport.clientWidth || event.clientY - rect.top >= viewport.clientHeight) return;
        event.preventDefault();
        viewport.focus({ preventScroll: true });
        pan = { id: event.pointerId, x: event.clientX, y: event.clientY, left: viewport.scrollLeft, top: viewport.scrollTop };
        viewport.classList.add('is-panning');
        try { viewport.setPointerCapture(event.pointerId); } catch { /* pointer already gone */ }
    }, options);
    viewport.addEventListener('pointermove', event => {
        if (!pan || event.pointerId !== pan.id) return;
        viewport.scrollLeft = pan.left - (event.clientX - pan.x);
        viewport.scrollTop = pan.top - (event.clientY - pan.y);
    }, options);
    const endPan = event => {
        if (!pan || event.pointerId !== pan.id) return;
        try { viewport.releasePointerCapture(pan.id); } catch { /* already released */ }
        pan = null;
        viewport.classList.remove('is-panning');
    };
    viewport.addEventListener('pointerup', endPan, options);
    viewport.addEventListener('pointercancel', endPan, options);
    viewport.addEventListener('lostpointercapture', endPan, options);

    return { dispose() {
        listeners.abort();
        window.cancelAnimationFrame(wheelFrame);
        wheelFrame = 0;
        if (pan) endPan({ pointerId: pan.id });
    } };
}
