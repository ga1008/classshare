import { findPopoverParent, getPopoverPosition } from './ui_popover_geometry.js';
import { cancelOverlayMotion, setOverlayOpen } from './ui_overlay_motion.js';
export { findPopoverParent, getPopoverPosition } from './ui_popover_geometry.js';

/** Configurable shared popover lifecycle; no editor or whiteboard state. */
export function createPopoverSystem({ prefix = 'ls' } = {}) {
/**
 * 统一浮窗系统：锚定、动效、唯一打开、关闭规则、焦点管理。
 * 形态：popover（小面板）| panel（列表）| dialog（居中 + 遮罩）| sheet（移动端贴底）
 */

const VIEWPORT_MARGIN = 12;
const ANCHOR_GAP = 8;
const SHEET_BREAKPOINT = 760;

class PopoverManager {
    constructor() {
        this.stack = [];
        this.layerEl = null;
        this.boundPointerDown = (event) => this.handleDocumentPointerDown(event);
        this.boundResize = () => {
            if(this.stack[0]?.options.preserveOnResize)this.stack.forEach(item=>item.reposition());
            else this.closeAll('resize');
        };
        this.boundBlur = () => this.closeAll('blur');
        this.listening = false;
    }

    get current() { return this.stack.at(-1) || null; }

    layer() {
        if (this.layerEl?.isConnected) return this.layerEl;
        const layer = document.createElement('div');
        layer.className = prefix + '-layer';
        layer.id = prefix + '-layer';
        document.body.appendChild(layer);
        this.layerEl = layer;
        return layer;
    }

    listen() {
        if (this.listening) return;
        this.listening = true;
        document.addEventListener('pointerdown', this.boundPointerDown, true);
        window.addEventListener('resize', this.boundResize);
        window.addEventListener('blur', this.boundBlur);
    }

    unlisten() {
        if (!this.listening) return;
        this.listening = false;
        document.removeEventListener('pointerdown', this.boundPointerDown, true);
        window.removeEventListener('resize', this.boundResize);
        window.removeEventListener('blur', this.boundBlur);
    }

    handleDocumentPointerDown(event) {
        const popover = this.current;
        if (!popover) return;
        const target = event.target;
        if (popover.panel.contains(target) || popover.anchor?.contains(target)) return;
        if (popover.options.modal) return; // 遮罩自行处理
        popover.close('outside');
    }

    open(popover) {
        // Nested controls opt in. Existing root popovers still replace one another.
        const parent = popover.options.parent === 'anchor'
            ? findPopoverParent(this.stack, popover.anchor) : null;
        if (parent) {
            while (this.current !== parent) this.current.close('replaced');
        } else this.closeAll('replaced');
        popover.parent = parent;
        this.stack.push(popover);
        popover.panel.style.zIndex = String(this.stack.length + 1);
        this.listen();
    }

    released(popover) {
        const index = this.stack.indexOf(popover);
        if (index < 0) return;
        while (this.current !== popover) this.current.close('parent-closed');
        this.stack.pop();
        if (!this.current) this.unlisten();
    }

    closeAll(reason = 'manual') {
        while (this.current) this.current.close(reason);
    }

    isOpen() {
        return Boolean(this.current);
    }

    hasAnchorWithin(root) { return this.stack.some(item => root.contains(item.anchor)); }
    closeAnchoredWithin(root) {
        for (const item of [...this.stack].reverse()) if (root.contains(item.anchor)) item.close('anchor-removed');
    }
}

const popoverManager = new PopoverManager();

/**
 * @param {object} options
 * @param {HTMLElement} [options.anchor]
 * @param {HTMLElement} options.panel  已构建的面板元素（会被移入浮窗层）
 * @param {'popover'|'panel'|'dialog'} [options.kind]
 * @param {'bottom-start'|'bottom-end'} [options.placement]
 * @param {boolean} [options.modal]  居中对话框 + 遮罩
 * @param {(reason:string)=>void} [options.onClose]
 * @param {()=>void} [options.onOpen]
 * @param {string} [options.label]  aria-label
 */
function createPopover(options) {
    const panel = options.panel;
    const kind = options.kind || 'popover';
    panel.classList.add('lq-domain-popover', prefix + '-popover', `${prefix}-popover--${kind}`);
    panel.hidden = true;
    panel.setAttribute('role', options.role || 'dialog');
    if (options.label) panel.setAttribute('aria-label', options.label);
    if (options.modal) panel.setAttribute('aria-modal', 'true');
    panel.tabIndex = -1;

    let backdrop = null;
    let isOpen = false;
    let lastFocus = null;
    let generation = 0;
    let destroyed = false;
    let closing = null;

    const api = {
        panel,
        anchor: options.anchor || null,
        options,
        get isOpen() {
            return isOpen;
        },
    };

    function position() {
        if (options.modal) return;
        const sheet = window.innerWidth <= SHEET_BREAKPOINT && kind !== 'dialog';
        panel.classList.toggle(prefix + '-popover--sheet', sheet);
        if (sheet) {
            panel.style.left = '';
            panel.style.top = '';
            return;
        }
        const anchorRect = api.anchor?.getBoundingClientRect()
            || { left: VIEWPORT_MARGIN, right: VIEWPORT_MARGIN, top: VIEWPORT_MARGIN, bottom: VIEWPORT_MARGIN };
        const rect = panel.getBoundingClientRect();
        const width = rect.width || panel.offsetWidth;
        const height = rect.height || panel.offsetHeight;
        const positioned = getPopoverPosition({ anchor: anchorRect, panel: { width, height }, width: window.innerWidth,
            height: window.innerHeight, placement: options.placement === 'bottom-end' ? 'bottom-end' : 'bottom-start',
            margin: VIEWPORT_MARGIN, gap: ANCHOR_GAP });
        panel.style.left = `${positioned.left}px`;
        panel.style.top = `${positioned.top}px`;
        panel.classList.toggle(prefix + '-popover--flipped', positioned.flipped);
    }

    function focusFirst() {
        const target = panel.querySelector('[data-autofocus]') || panel.querySelector('input:not([type=hidden]), button, [tabindex]:not([tabindex="-1"])');
        (target || panel).focus?.({ preventScroll: true });
    }

    function trapTab(event) {
        if (popoverManager.current !== api) return;
        if (event.key === 'Escape') {
            event.preventDefault(); event.stopPropagation(); api.close('escape'); return;
        }
        if (event.key !== 'Tab') return;
        const focusables = Array.from(panel.querySelectorAll('button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])'))
            .filter((el) => el.offsetParent !== null);
        if (!focusables.length) return;
        const first = focusables[0];
        const last = focusables[focusables.length - 1];
        if (event.shiftKey && document.activeElement === first) {
            event.preventDefault();
            last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first.focus();
        }
    }

    api.open = () => {
        if (isOpen || destroyed) return;
        generation++;
        isOpen = true;
        if (!panel.contains(document.activeElement)) lastFocus = document.activeElement;
        const layer = popoverManager.layer();
        if (options.modal && !backdrop) {
            backdrop = document.createElement('div');
            backdrop.className = prefix + '-backdrop';
            backdrop.hidden = true;
            backdrop.addEventListener('pointerdown', (event) => {
                if (event.target === backdrop) api.close('backdrop');
            });
            layer.appendChild(backdrop);
            panel.classList.add(prefix + '-popover--modal');
        }
        if (panel.parentNode !== layer) layer.appendChild(panel);
        panel.addEventListener('keydown', trapTab);
        api.anchor?.setAttribute('aria-expanded', 'true');
        popoverManager.open(api);
        panel.classList.add('is-open');
        backdrop?.classList.add('is-open');
        void setOverlayOpen(panel, true);
        if (backdrop) void setOverlayOpen(backdrop, true);
        position();
        options.onOpen?.();
        if (isOpen && !destroyed) focusFirst();
    };

    api.close = (reason = 'manual') => {
        if (!isOpen) return closing;
        const ticket = ++generation;
        isOpen = false;
        panel.classList.remove('is-open');
        backdrop?.classList.remove('is-open');
        panel.removeEventListener('keydown', trapTab);
        api.anchor?.setAttribute('aria-expanded', 'false');
        popoverManager.released(api);
        const leaving = [setOverlayOpen(panel, false)];
        if (backdrop) leaving.push(setOverlayOpen(backdrop, false));
        closing = Promise.all(leaving).then(completed => {
            if (destroyed || isOpen || ticket !== generation || completed.some(value => !value)) return false;
            backdrop?.remove();
            backdrop = null;
            if ((!popoverManager.isOpen() || popoverManager.current === api.parent) && reason !== 'outside' && lastFocus && typeof lastFocus.focus === 'function' && document.contains(lastFocus)) {
                lastFocus.focus({ preventScroll: true });
            }
            options.onAfterClose?.(reason);
            return true;
        });
        // Business cancellation is immediate; DOM teardown waits for the
        // actual shared presence operation instead of a guessed 120ms delay.
        options.onClose?.(reason);
        return closing;
    };

    api.toggle = () => (isOpen ? api.close('toggle') : api.open());
    api.reposition = position;
    api.destroy = () => {
        if (destroyed) return;
        api.close('destroy');
        destroyed = true; generation++;
        cancelOverlayMotion(panel);
        if (backdrop) cancelOverlayMotion(backdrop);
        backdrop?.remove(); backdrop = null;
        panel.remove();
    };
    return api;
}

return { popoverManager, createPopover };

}
export const POPOVER_TIMING = { OPEN_MS: 160, CLOSE_MS: 120 };
