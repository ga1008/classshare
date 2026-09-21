import { escapeHtml } from './ui.js';
import { getLayerSystem } from './lq/layer.js';

const DEFAULT_CLOSE_SELECTOR = '[data-pm-close],[data-lp-close],[data-ap-close],[data-te-close]';
const FOCUSABLE_SELECTOR = '[autofocus]:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), button:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])';

function getFocusableElements(root) {
    return Array.from(root.querySelectorAll(FOCUSABLE_SELECTOR))
        .filter((element) => (
            element instanceof HTMLElement
            && !element.hidden
            && !element.closest('[hidden]')
            && element.getClientRects().length > 0
        ));
}

function pickInitialFocusTarget(overlay) {
    const focusable = getFocusableElements(overlay);
    const autofocusTarget = focusable.find((element) => element.hasAttribute('autofocus'));
    if (autofocusTarget) return autofocusTarget;
    const body = overlay.querySelector('.lp-modal__body');
    const bodyTarget = focusable.find((element) => body?.contains(element));
    if (bodyTarget) return bodyTarget;
    const footer = overlay.querySelector('.lp-modal__foot');
    return focusable.find((element) => footer?.contains(element)) || focusable[0] || null;
}

function trapModalFocus(event, overlay) {
    if (event.key !== 'Tab') return;
    const focusable = getFocusableElements(overlay);
    if (!focusable.length) {
        event.preventDefault();
        return;
    }
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    const active = document.activeElement;
    if (event.shiftKey && (active === first || !overlay.contains(active))) {
        event.preventDefault();
        last.focus({ preventScroll: true });
        return;
    }
    if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus({ preventScroll: true });
    }
}

export function openProcessMaterialModal(
    title,
    bodyHtml,
    { footerHtml = '', onMount, onClose, wide = false, closeAttr = 'data-pm-close', closeSelector = DEFAULT_CLOSE_SELECTOR, canClose } = {},
) {
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    // A legacy child opened from a coordinated modal must join that same stack.
    // Standalone process-material dialogs retain their existing owner until
    // their page family migrates; never run two focus/Escape controllers.
    const layer = getLayerSystem(document);
    const parentLayer = layer.top();
    let layerHandle = null;
    const overlay = document.createElement('div');
    overlay.className = 'lp-modal-overlay';
    overlay.innerHTML = `
        <div class="lp-modal${wide ? ' lp-modal--wide' : ''}" role="dialog" aria-modal="true">
            <header class="lp-modal__head">
                <h3>${escapeHtml(title)}</h3>
                <button type="button" class="lp-modal__close" ${closeAttr} aria-label="关闭">×</button>
            </header>
            <div class="lp-modal__body">${bodyHtml}</div>
            <footer class="lp-modal__foot">${footerHtml}</footer>
        </div>`;
    (parentLayer ? layer.getPortalHost({ trigger: previousFocus, parentLayer }) : document.body).appendChild(overlay);

    let closed = false;
    function onKeydown(e) {
        if (e.key === 'Escape') close();
        if (e.key === 'Tab') trapModalFocus(e, overlay);
    }
    function finish() {
        if (closed) return;
        closed = true;
        document.removeEventListener('keydown', onKeydown);
        overlay.remove();
        if (typeof onClose === 'function') onClose();
        if (!layerHandle && previousFocus && document.contains(previousFocus)) {
            previousFocus.focus({ preventScroll: true });
        }
    }
    function close(options = {}) {
        if (closed) return;
        const force = Boolean(options?.force);
        if (layerHandle) {
            if (force) {
                // Destroy also invalidates an already pending, vetoed close.
                // Updating beforeClose alone would reuse that old promise.
                layerHandle.destroy();
                if (layer.top() === parentLayer && previousFocus?.isConnected
                    && !previousFocus.closest('[hidden],[inert],[aria-hidden="true"]')) {
                    previousFocus.focus({ preventScroll: true });
                }
                return;
            }
            return layer.close(layerHandle, 'programmatic');
        }
        if (!force && typeof canClose === 'function' && canClose() === false) return;
        finish();
    }

    overlay.addEventListener('click', (e) => {
        if (e.target === overlay || e.target.closest(closeSelector)) close();
    });
    if (!parentLayer) document.addEventListener('keydown', onKeydown);
    if (onMount) onMount(overlay, close);
    if (closed) return { overlay, close };
    if (parentLayer) {
        if (!overlay.isConnected || !parentLayer.root.isConnected
            || ['closed', 'destroyed'].includes(parentLayer.state)) {
            finish();
            return { overlay, close };
        }
        try {
            layerHandle = layer.open(overlay, {
                type: 'modal', surface: overlay.querySelector('.lp-modal'),
                trigger: previousFocus, parentLayer,
                initialFocus: () => pickInitialFocusTarget(overlay),
                beforeClose: () => typeof canClose !== 'function' || canClose() !== false,
                onClose: finish, onDestroy: finish,
            });
        } catch (error) {
            finish();
            throw error;
        }
        return { overlay, close };
    }
    const focusTarget = pickInitialFocusTarget(overlay);
    if (focusTarget instanceof HTMLElement) focusTarget.focus({ preventScroll: true });
    return { overlay, close };
}

export function openProcessMaterialConfirm({
    title = '确认操作',
    message = '',
    detail = '',
    confirmText = '确认',
    cancelText = '取消',
    tone = 'primary',
} = {}) {
    return new Promise((resolve) => {
        let settled = false;
        const confirmClass = tone === 'danger' ? 'lp-btn--danger' : 'lp-btn--primary';
        const body = `
            <div class="lp-confirm">
                <p class="lp-confirm__message">${escapeHtml(message)}</p>
                ${detail ? `<p class="lp-confirm__detail">${escapeHtml(detail)}</p>` : ''}
            </div>`;
        const footer = `
            <button type="button" class="lp-btn lp-btn--ghost" data-pm-confirm-cancel>${escapeHtml(cancelText)}</button>
            <button type="button" class="lp-btn ${confirmClass}" data-pm-confirm-ok autofocus>${escapeHtml(confirmText)}</button>`;

        const settle = (value, close) => {
            if (settled) return;
            settled = true;
            resolve(value);
            close();
        };

        openProcessMaterialModal(title, body, {
            footerHtml: footer,
            onMount: (overlay, close) => {
                overlay.querySelector('[data-pm-confirm-cancel]')?.addEventListener('click', () => settle(false, close));
                overlay.querySelector('[data-pm-confirm-ok]')?.addEventListener('click', () => settle(true, close));
            },
            onClose: () => {
                if (settled) return;
                settled = true;
                resolve(false);
            },
        });
    });
}
