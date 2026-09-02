/**
 * 历史白板浮窗：列表（倒序）、内联重命名、删除（内联确认）、当前高亮、同步状态。
 */
import { escapeHtml } from '../../ui.js';
import { ICONS, LIMITS, SYNC_STATUS } from '../constants.js';
import { createPopover } from '../popover.js';
import { formatRelativeTime, isBoardEmpty } from '../state.js';
import { openConfirm } from './confirm_popover.js';
import { h } from './controls.js';

const STATUS_LABEL = {
    [SYNC_STATUS.LOCAL]: '本机',
    [SYNC_STATUS.SYNCED]: '已同步',
    [SYNC_STATUS.DIRTY]: '未同步',
    [SYNC_STATUS.SAVING]: '保存中',
    [SYNC_STATUS.ERROR]: '同步失败',
};

export function createHistoryPanel(board, anchor) {
    const list = h('div', { className: 'twb-history-list', role: 'listbox', 'aria-label': '历史白板' });
    const count = h('span', { className: 'twb-history-count' });
    let popover = null;
    let editingId = null;

    const panel = h('div', { className: 'twb-panel-body twb-history' }, [
        h('div', { className: 'twb-history-head' }, [
            h('div', { className: 'twb-section-title' }, [h('span', { text: '历史白板' }), count]),
            h('button', {
                type: 'button',
                className: 'twb-btn twb-btn--ghost',
                html: `${ICONS.plus}<span>新建</span>`,
                onClick: () => { popover.close('action'); board.createNewBoard(); },
            }),
        ]),
        list,
    ]);

    function strokeCount(item) {
        return item.elementsLoaded === false ? item.elementCount : item.elements.filter((el) => el.type !== 'eraser').length;
    }

    function renderRow(item) {
        const isActive = item.id === board.activeBoard?.id;
        const status = board.sync.statusOf(item);
        const row = h('div', {
            className: `twb-history-row${isActive ? ' is-active' : ''}`,
            role: 'option',
            'aria-selected': isActive ? 'true' : 'false',
            tabindex: 0,
            dataset: { id: item.id },
        });
        const nameEl = h('div', { className: 'twb-history-name', text: item.name || '未命名白板' });
        const meta = h('div', {
            className: 'twb-history-meta',
            html: `${escapeHtml(formatRelativeTime(item.updatedAt || item.createdAt))} · ${strokeCount(item)} 笔 · `
                + `<span class="twb-history-status" data-status="${status}"><i class="twb-status-dot"></i>${escapeHtml(STATUS_LABEL[status] || '')}</span>`,
        });
        const actions = h('div', { className: 'twb-history-actions' }, [
            h('button', {
                type: 'button', className: 'twb-icon-btn', title: '重命名', 'aria-label': '重命名', html: ICONS.edit,
                onClick: (e) => { e.stopPropagation(); startRename(item, nameEl); },
            }),
            h('button', {
                type: 'button', className: 'twb-icon-btn is-danger', title: '删除', 'aria-label': '删除白板', html: ICONS.trash,
                onClick: (e) => { e.stopPropagation(); confirmDelete(item, e.currentTarget); },
            }),
        ]);
        row.append(h('div', { className: 'twb-history-main' }, [nameEl, meta]), actions);
        const select = () => {
            if (editingId === item.id) return;
            if (isActive) {
                popover.close('select');
                return;
            }
            row.classList.add('is-loading');
            board.selectBoard(item.id).finally(() => popover.close('select'));
        };
        row.addEventListener('click', select);
        row.addEventListener('keydown', (event) => {
            if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                select();
            }
        });
        return row;
    }

    function startRename(item, nameEl) {
        if (editingId) return;
        editingId = item.id;
        const input = h('input', {
            type: 'text',
            className: 'twb-input twb-history-rename',
            value: item.name || '',
            maxlength: LIMITS.boardNameLength,
            'aria-label': '白板名称',
        });
        nameEl.replaceWith(input);
        input.focus();
        input.select();
        const finish = (commit) => {
            if (editingId !== item.id) return;
            editingId = null;
            const next = input.value.trim().slice(0, LIMITS.boardNameLength);
            if (commit && next && next !== item.name) board.renameBoard(item.id, next);
            refresh();
        };
        input.addEventListener('keydown', (event) => {
            event.stopPropagation();
            if (event.key === 'Enter') { event.preventDefault(); finish(true); }
            if (event.key === 'Escape') { event.preventDefault(); finish(false); }
        });
        input.addEventListener('blur', () => finish(true));
        input.addEventListener('click', (event) => event.stopPropagation());
    }

    function confirmDelete(item, anchorEl) {
        openConfirm({
            anchor: anchorEl,
            title: `删除「${item.name}」？`,
            body: isBoardEmpty(item) ? '这块白板是空的。' : '本机与云端副本都会删除，且无法撤销。',
            confirmLabel: '删除',
            onConfirm: async () => {
                await board.deleteBoard(item.id);
                refresh();
                window.setTimeout(() => popover.open(), 160);
            },
        });
    }

    function refresh() {
        const boards = [...board.state.boards].sort((a, b) => String(b.updatedAt || '').localeCompare(String(a.updatedAt || '')));
        count.textContent = `（${boards.length}）`;
        list.replaceChildren(...boards.map(renderRow));
        if (popover?.isOpen) popover.reposition();
    }

    popover = createPopover({ anchor, panel, kind: 'panel', label: '历史白板', onOpen: refresh });
    return { popover, refresh };
}
