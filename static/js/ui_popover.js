/** Configurable shared popover lifecycle; no editor or whiteboard state. */
export function createPopoverSystem({ prefix = 'ls' } = {}) {
/**
 * 统一浮窗系统：锚定、动效、唯一打开、关闭规则、焦点管理。
 * 形态：popover（小面板）| panel（列表）| dialog（居中 + 遮罩）| sheet（移动端贴底）
 */

const OPEN_MS = 160;
const CLOSE_MS = 120;
const VIEWPORT_MARGIN = 12;
const ANCHOR_GAP = 8;
const SHEET_BREAKPOINT = 760;

function reducedMotion() {
    return typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches;
}

class PopoverManager {
    constructor() {
        this.current = null;
        this.layerEl = null;
        this.boundPointerDown = (event) => this.handleDocumentPointerDown(event);
        this.boundResize = () => this.closeAll('resize');
        this.boundBlur = () => this.closeAll('blur');
        this.listening = false;
    }

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
        if (this.current && this.current !== popover) this.current.close('replaced');
        this.current = popover;
        this.listen();
    }

    released(popover) {
        if (this.current === popover) this.current = null;
        if (!this.current) this.unlisten();
    }

    closeAll(reason = 'manual') {
        if (this.current) this.current.close(reason);
    }

    isOpen() {
        return Boolean(this.current);
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
    panel.classList.add(prefix + '-popover', `${prefix}-popover--${kind}`);
    panel.hidden = true;
    panel.setAttribute('role', options.role || 'dialog');
    if (options.label) panel.setAttribute('aria-label', options.label);
    if (options.modal) panel.setAttribute('aria-modal', 'true');
    panel.tabIndex = -1;

    let backdrop = null;
    let closeTimer = null;
    let isOpen = false;
    let lastFocus = null;

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
        let left = options.placement === 'bottom-end' ? anchorRect.right - width : anchorRect.left;
        left = Math.max(VIEWPORT_MARGIN, Math.min(left, window.innerWidth - width - VIEWPORT_MARGIN));
        let top = anchorRect.bottom + ANCHOR_GAP;
        let flipped = false;
        if (top + height > window.innerHeight - VIEWPORT_MARGIN && anchorRect.top - ANCHOR_GAP - height >= VIEWPORT_MARGIN) {
            top = anchorRect.top - ANCHOR_GAP - height;
            flipped = true;
        }
        top = Math.max(VIEWPORT_MARGIN, Math.min(top, window.innerHeight - height - VIEWPORT_MARGIN));
        panel.style.left = `${Math.round(left)}px`;
        panel.style.top = `${Math.round(top)}px`;
        panel.classList.toggle(prefix + '-popover--flipped', flipped);
    }

    function focusFirst() {
        const target = panel.querySelector('[data-autofocus], input:not([type=hidden]), button, [tabindex]:not([tabindex="-1"])');
        (target || panel).focus?.({ preventScroll: true });
    }

    function trapTab(event) {
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
        if (isOpen) return;
        window.clearTimeout(closeTimer);
        isOpen = true;
        lastFocus = document.activeElement;
        const layer = popoverManager.layer();
        if (options.modal) {
            backdrop = document.createElement('div');
            backdrop.className = prefix + '-backdrop';
            backdrop.addEventListener('pointerdown', (event) => {
                if (event.target === backdrop) api.close('backdrop');
            });
            layer.appendChild(backdrop);
            panel.classList.add(prefix + '-popover--modal');
        }
        layer.appendChild(panel);
        panel.hidden = false;
        panel.classList.remove('is-open');
        panel.addEventListener('keydown', trapTab);
        api.anchor?.setAttribute('aria-expanded', 'true');
        popoverManager.open(api);
        position();
        options.onOpen?.();
        const reveal = () => {
            if (!isOpen) return;
            panel.classList.add('is-open');
            backdrop?.classList.add('is-open');
            focusFirst();
        };
        if (reducedMotion()) reveal();
        else window.requestAnimationFrame(() => window.requestAnimationFrame(reveal));
    };

    api.close = (reason = 'manual') => {
        if (!isOpen) return;
        isOpen = false;
        panel.classList.remove('is-open');
        backdrop?.classList.remove('is-open');
        panel.removeEventListener('keydown', trapTab);
        api.anchor?.setAttribute('aria-expanded', 'false');
        popoverManager.released(api);
        const finish = () => {
            panel.hidden = true;
            backdrop?.remove();
            backdrop = null;
            if (!popoverManager.isOpen() && lastFocus && typeof lastFocus.focus === 'function' && document.contains(lastFocus)) {
                lastFocus.focus({ preventScroll: true });
            }
        };
        closeTimer = window.setTimeout(finish, reducedMotion() ? 0 : CLOSE_MS);
        options.onClose?.(reason);
    };

    api.toggle = () => (isOpen ? api.close('toggle') : api.open());
    api.reposition = position;
    api.destroy = () => {
        api.close('destroy');
        panel.remove();
    };
    return api;
}

return { popoverManager, createPopover };

}
export const POPOVER_TIMING = { OPEN_MS: 160, CLOSE_MS: 120 };
