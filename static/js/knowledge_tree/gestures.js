// 知识树画布手势：普通滚轮原生滚动，Ctrl+滚轮/触控板捏合以光标为锚缩放（按帧合并），
// 画布获焦时 +/−/0 缩放与适应，双击空白处适应，鼠标/笔按住空白处拖动平移（触屏原生滑动）。

/**
 * ctx: { viewport, ready() -> bool, zoomBy(factor, anchor|null), fit() }
 * Returns { dispose() } that cancels a pending zoom frame.
 */
export function bindViewportGestures({ viewport, ready, zoomBy, fit }) {
    let wheelFactor = 1;
    let wheelAnchor = null;
    let wheelFrame = 0;
    viewport.addEventListener('wheel', event => {
        if (!event.ctrlKey || !ready()) return;
        event.preventDefault();
        const rect = viewport.getBoundingClientRect();
        const delta = Math.max(-120, Math.min(120, event.deltaMode === 1 ? event.deltaY * 40 : event.deltaY));
        wheelFactor *= Math.exp(-delta * 0.0018);
        wheelAnchor = { x: event.clientX - rect.left, y: event.clientY - rect.top };
        if (!wheelFrame) wheelFrame = window.requestAnimationFrame(() => {
            wheelFrame = 0;
            zoomBy(wheelFactor, wheelAnchor);
            wheelFactor = 1;
        });
    }, { passive: false });
    viewport.addEventListener('keydown', event => {
        if (event.target !== viewport || !ready() || event.ctrlKey || event.metaKey || event.altKey) return;
        if (event.key === '+' || event.key === '=') { event.preventDefault(); zoomBy(1.2, null); }
        else if (event.key === '-' || event.key === '_') { event.preventDefault(); zoomBy(1 / 1.2, null); }
        else if (event.key === '0') { event.preventDefault(); fit(); }
    });
    viewport.addEventListener('dblclick', event => {
        if (!ready() || event.target.closest('.kt-node, .kt-fold')) return;
        fit();
    });

    let pan = null;
    viewport.addEventListener('pointerdown', event => {
        if (!ready() || event.pointerType === 'touch' || ![0, 1].includes(event.button)) return;
        if (event.target.closest('.kt-node, .kt-fold')) return;
        // Leave the native scrollbars alone.
        const rect = viewport.getBoundingClientRect();
        if (event.clientX - rect.left >= viewport.clientWidth || event.clientY - rect.top >= viewport.clientHeight) return;
        event.preventDefault();
        viewport.focus({ preventScroll: true });
        pan = { id: event.pointerId, x: event.clientX, y: event.clientY, left: viewport.scrollLeft, top: viewport.scrollTop };
        viewport.classList.add('is-panning');
        try { viewport.setPointerCapture(event.pointerId); } catch { /* pointer already gone */ }
    });
    viewport.addEventListener('pointermove', event => {
        if (!pan || event.pointerId !== pan.id) return;
        viewport.scrollLeft = pan.left - (event.clientX - pan.x);
        viewport.scrollTop = pan.top - (event.clientY - pan.y);
    });
    const endPan = event => {
        if (!pan || event.pointerId !== pan.id) return;
        try { viewport.releasePointerCapture(pan.id); } catch { /* already released */ }
        pan = null;
        viewport.classList.remove('is-panning');
    };
    viewport.addEventListener('pointerup', endPan);
    viewport.addEventListener('pointercancel', endPan);
    viewport.addEventListener('lostpointercapture', endPan);

    return { dispose() { window.cancelAnimationFrame(wheelFrame); wheelFrame = 0; } };
}
