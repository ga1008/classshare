/**
 * 悬浮按钮（FAB）mixin：开关态、拖拽定位、位置持久化。挂到 TeacherWhiteboard.prototype。
 */
import { ICONS } from './constants.js';
import { clamp, toFiniteNumber } from './state.js';
import { loadFabPosition, saveFabPosition } from './store_local.js';

export const fabMixin = {
    setFabOpenState(open) {
        if (!this.fabEl) return;
        this.fabEl.classList.toggle('is-open', open);
        this.fabEl.setAttribute('aria-pressed', open ? 'true' : 'false');
        this.fabEl.setAttribute('aria-label', open ? '关闭讲课白板' : '打开讲课白板');
        this.fabEl.title = open ? '关闭讲课白板' : '讲课白板';
        this.fabEl.innerHTML = open ? ICONS.close : ICONS.board;
    },

    handleFabClick(event) {
        if (this.ignoreNextFabClick) {
            event.preventDefault();
            this.ignoreNextFabClick = false;
            return;
        }
        this.toggleOpen();
    },

    handleFabPointerDown(event) {
        if (event.button !== 0 || !this.fabEl) return;
        const rect = this.fabEl.getBoundingClientRect();
        this.fabDrag = { pointerId: event.pointerId, startX: event.clientX, startY: event.clientY, startLeft: rect.left, startTop: rect.top, moved: false };
        try { this.fabEl.setPointerCapture(event.pointerId); } catch { /* optional */ }
    },

    handleFabPointerMove(event) {
        if (!this.fabDrag || this.fabDrag.pointerId !== event.pointerId || !this.fabEl) return;
        const dx = event.clientX - this.fabDrag.startX;
        const dy = event.clientY - this.fabDrag.startY;
        if (!this.fabDrag.moved && Math.hypot(dx, dy) > 4) {
            this.fabDrag.moved = true;
            this.fabEl.classList.add('is-dragging');
        }
        if (!this.fabDrag.moved) return;
        this.placeFab(this.fabDrag.startLeft + dx, this.fabDrag.startTop + dy);
        event.preventDefault();
    },

    handleFabPointerUp(event) {
        if (!this.fabDrag || this.fabDrag.pointerId !== event.pointerId) return;
        const moved = this.fabDrag.moved;
        this.finishFabDrag(event);
        if (moved) {
            this.ignoreNextFabClick = true;
            this.saveFabPosition();
        }
    },

    finishFabDrag(event) {
        if (!this.fabEl || !this.fabDrag) return;
        try {
            if (event && this.fabEl.hasPointerCapture?.(event.pointerId)) this.fabEl.releasePointerCapture(event.pointerId);
        } catch { /* ignore */ }
        this.fabEl.classList.remove('is-dragging');
        this.fabDrag = null;
    },

    placeFab(left, top) {
        const size = this.fabEl.offsetWidth || 62;
        this.fabEl.style.left = `${clamp(left, 8, window.innerWidth - size - 8)}px`;
        this.fabEl.style.top = `${clamp(top, 8, window.innerHeight - size - 8)}px`;
        this.fabEl.style.right = 'auto';
        this.fabEl.style.bottom = 'auto';
    },

    applyFabPosition() {
        const position = loadFabPosition(this.context);
        if (!position || !this.fabEl) return;
        const size = this.fabEl.offsetWidth || 62;
        this.placeFab(toFiniteNumber(position.left, window.innerWidth - size - 20), toFiniteNumber(position.top, 20));
    },

    saveFabPosition() {
        if (!this.fabEl) return;
        const rect = this.fabEl.getBoundingClientRect();
        saveFabPosition(this.context, { left: Math.round(rect.left), top: Math.round(rect.top) });
    },
};
