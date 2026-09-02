/**
 * 内联确认浮窗（清屏 / 删除白板）：Enter 确认、Esc 取消。
 */
import { createPopover } from '../popover.js';
import { h } from './controls.js';

/**
 * @param {object} options
 * @param {HTMLElement} options.anchor
 * @param {string} options.title
 * @param {string} [options.body]
 * @param {string} [options.confirmLabel]
 * @param {boolean} [options.danger]
 * @param {() => void|Promise<void>} options.onConfirm
 */
export function openConfirm({ anchor, title, body = '', confirmLabel = '确定', danger = true, onConfirm, placement = 'bottom-end' }) {
    let popover = null;
    const confirmButton = h('button', {
        type: 'button',
        className: `twb-btn ${danger ? 'twb-btn--danger' : 'twb-btn--primary'}`,
        text: confirmLabel,
        'data-autofocus': true,
        onClick: async () => {
            confirmButton.disabled = true;
            try {
                await onConfirm();
            } finally {
                popover?.close('confirm');
            }
        },
    });
    const panel = h('div', { className: 'twb-panel-body twb-confirm' }, [
        h('div', { className: 'twb-confirm-title', text: title }),
        body ? h('div', { className: 'twb-confirm-body', text: body }) : null,
        h('div', { className: 'twb-confirm-actions' }, [
            h('button', { type: 'button', className: 'twb-btn', text: '取消', onClick: () => popover?.close('cancel') }),
            confirmButton,
        ]),
    ]);
    panel.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') {
            event.preventDefault();
            confirmButton.click();
        }
    });
    popover = createPopover({
        anchor,
        panel,
        kind: 'popover',
        placement,
        label: title,
        role: 'alertdialog',
        onClose: () => window.setTimeout(() => panel.remove(), 200),
    });
    popover.open();
    return popover;
}
