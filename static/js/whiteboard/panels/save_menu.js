/**
 * 保存二级菜单：当前板名 + 同步状态、线上保存、导出本地。
 */
import { escapeHtml } from '../../ui.js';
import { SYNC_STATUS } from '../constants.js';
import { createPopover } from '../popover.js';
import { formatBoardTime, isBoardEmpty } from '../state.js';
import { h, menuItem } from './controls.js';

export const STATUS_TEXT = {
    [SYNC_STATUS.LOCAL]: '仅保存在本机',
    [SYNC_STATUS.SYNCED]: '已线上保存',
    [SYNC_STATUS.DIRTY]: '有未同步的改动',
    [SYNC_STATUS.SAVING]: '正在保存到云端…',
    [SYNC_STATUS.ERROR]: '上次线上保存失败',
};

export function createSaveMenu(board, anchor) {
    const header = h('div', { className: 'twb-menu-header' });
    let popover = null;
    const cloudItem = menuItem({ icon: 'cloud', label: '线上保存', hint: 'Ctrl+S', onClick: () => { popover.close('action'); board.saveOnline(); } });
    const exportItem = menuItem({ icon: 'download', label: '导出本地…', hint: 'Ctrl+Shift+E', onClick: () => { popover.close('action'); board.openExport(); } });
    const panel = h('div', { className: 'twb-menu' }, [
        header,
        h('div', { className: 'twb-menu-divider' }),
        cloudItem,
        exportItem,
    ]);

    function refresh() {
        const active = board.activeBoard;
        const status = board.sync.statusOf(active);
        const empty = isBoardEmpty(active);
        const when = active?.syncedAt && status === SYNC_STATUS.SYNCED ? `（${formatBoardTime(active.syncedAt)}）` : '';
        header.innerHTML = `
            <div class="twb-menu-title">${escapeHtml(active?.name || '未命名白板')}</div>
            <div class="twb-menu-status" data-status="${status}"><i class="twb-status-dot"></i>${escapeHtml((STATUS_TEXT[status] || '') + when)}</div>
            ${empty ? '<div class="twb-menu-note">白板为空，先画点什么吧</div>' : ''}`;
        cloudItem.disabled = empty && (active?.remoteVersion || 0) === 0;
        exportItem.disabled = empty;
    }

    popover = createPopover({ anchor, panel, kind: 'popover', role: 'menu', label: '保存', onOpen: refresh });
    return { popover, refresh };
}
