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

export function openProcessMaterialModal(
    title,
    bodyHtml,
    { footerHtml = '', onMount, onClose, wide = false, closeAttr = 'data-pm-close', closeSelector = DEFAULT_CLOSE_SELECTOR, canClose } = {},
) {
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    // All consumers share one stack, including standalone process dialogs.
    const layer = getLayerSystem(document);
    const parentLayer = layer.top();
    let layerHandle = null;
    const overlay = document.createElement('div');
    overlay.className = 'lq-domain-region lp-modal-overlay';
    overlay.dataset.lqComponent = 'layer';
    overlay.dataset.lqPresence = 'domain';
    overlay.hidden = true;
    overlay.innerHTML = `
        <div data-lq-component="surface" data-lq-material="raised" class="lq-surface lq-domain-raised lp-modal${wide ? ' lp-modal--wide' : ''}" role="dialog" aria-modal="true">
            <header class="lp-modal__head">
                <h3>${escapeHtml(title)}</h3>
                <button data-lq-component="button" type="button" class="lq-btn lq-btn--sm lq-btn--glass lp-modal__close" ${closeAttr} aria-label="关闭">×</button>
            </header>
            <div class="lp-modal__body">${bodyHtml}</div>
            <footer class="lp-modal__foot">${footerHtml}</footer>
        </div>`;
    layer.getPortalHost({ trigger: previousFocus, parentLayer }).appendChild(overlay);

    let closed = false, forceClose = false;
    function finish() {
        if (closed) return;
        closed = true;
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
            forceClose ||= force;
            // A successful submit bypasses its busy guard, but still exits.
            // A close already checking an old veto must settle before retrying.
            return layer.close(layerHandle, force ? 'confirmed' : 'programmatic').then((completed) => {
                if (!completed && force && !closed) return layer.close(layerHandle, 'confirmed');
                return completed;
            });
        }
        if (!force && typeof canClose === 'function' && canClose() === false) return;
        finish();
    }

    overlay.addEventListener('click', (e) => {
        if (e.target === overlay || e.target.closest(closeSelector)) close();
    });
    if (onMount) onMount(overlay, close);
    if (closed) return { overlay, close };
    if (!overlay.isConnected || (parentLayer && (!parentLayer.root.isConnected
        || ['closed', 'destroyed'].includes(parentLayer.state)))) {
        finish();
        return { overlay, close };
    }
    try {
        layerHandle = layer.open(overlay, {
            type: 'modal', surface: overlay.querySelector('.lp-modal'),
            trigger: previousFocus, parentLayer,
            initialFocus: () => pickInitialFocusTarget(overlay),
            beforeClose: () => forceClose || typeof canClose !== 'function' || canClose() !== false,
            onClose: finish, onDestroy: finish,
        });
    } catch (error) {
        finish();
        throw error;
    }
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
        const confirmClass = tone === 'danger'
            ? 'lp-btn--danger lq-btn--destructive'
            : 'lp-btn--primary lq-btn--prominent';
        const body = `
            <div class="lp-confirm">
                <p class="lp-confirm__message">${escapeHtml(message)}</p>
                ${detail ? `<p class="lp-confirm__detail">${escapeHtml(detail)}</p>` : ''}
            </div>`;
        const footer = `
            <button data-lq-component="button" type="button" class="lq-btn lq-btn--sm lp-btn lp-btn--ghost lq-btn--ghost" data-pm-confirm-cancel>${escapeHtml(cancelText)}</button>
            <button data-lq-component="button" type="button" class="lq-btn lq-btn--sm lp-btn ${confirmClass}" data-pm-confirm-ok autofocus>${escapeHtml(confirmText)}</button>`;

        const settle = async (value, close) => {
            if (settled) return;
            settled = true;
            await close();
            resolve(value);
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
