/**
 * 本地缓存 ↔ 线上同步控制器：合并、dirty 队列、定时/事件触发、冲突保留副本。
 * 宿主（board.js）通过回调提供状态访问，本模块不直接操作 DOM。
 */
import { REMOTE, SYNC_STATUS } from './constants.js';
import { simplifyStroke } from './geometry.js';
import { isBoardEmpty, makeId, nowIso } from './state.js';
import { RemoteError, remoteToBoard } from './store_remote.js';

function prepareElements(elements) {
    return (elements || []).map((element) => {
        if ((element.type === 'stroke' || element.type === 'eraser') && Array.isArray(element.points) && element.points.length > 2) {
            return { ...element, points: simplifyStroke(element.points, REMOTE.SIMPLIFY_TOLERANCE) };
        }
        return element;
    });
}

export class SyncController {
    /**
     * @param {object} host
     * @param {import('./store_remote.js').RemoteStore} host.store
     * @param {() => object[]} host.getBoards
     * @param {(board: object) => void} host.upsertLocalBoard  按 id 替换或追加
     * @param {(id: string, patch: object) => void} host.patchBoard
     * @param {(status: string, detail?: object) => void} host.onStatus
     * @param {(message: string, type?: string) => void} host.notify
     * @param {() => void} host.persistLocal
     */
    constructor(host) {
        this.host = host;
        this.timer = null;
        this.inFlight = new Map();
        this.lastError = null;
        this.bootstrapped = false;
        this.enabled = true;
    }

    start() {
        if (this.timer) return;
        this.timer = window.setInterval(() => this.flushDirty({ silent: true }), REMOTE.AUTO_SYNC_INTERVAL_MS);
    }

    stop() {
        window.clearInterval(this.timer);
        this.timer = null;
    }

    statusOf(board) {
        if (!board) return SYNC_STATUS.LOCAL;
        if (this.inFlight.has(board.id)) return SYNC_STATUS.SAVING;
        if (this.lastError?.boardId === board.id) return SYNC_STATUS.ERROR;
        if (board.remoteVersion > 0 && !board.dirty) return SYNC_STATUS.SYNCED;
        if (board.remoteVersion > 0 && board.dirty) return SYNC_STATUS.DIRTY;
        if (board.elementsLoaded === false) return SYNC_STATUS.SYNCED;
        return SYNC_STATUS.LOCAL;
    }

    /** 打开白板时：拉远端列表并与本地合并。失败静默（本地照常）。 */
    async bootstrap() {
        if (this.bootstrapped || !this.enabled) return;
        this.bootstrapped = true;
        let rows;
        try {
            rows = await this.host.store.list();
        } catch (error) {
            this.bootstrapped = false;
            this.noteError(null, error, { silent: true });
            return;
        }
        const localById = new Map(this.host.getBoards().map((board) => [board.id, board]));
        for (const row of rows) {
            const remote = remoteToBoard(row);
            const local = localById.get(remote.id);
            if (!local) {
                this.host.upsertLocalBoard(remote);
                continue;
            }
            if (remote.remoteVersion > local.remoteVersion && !local.dirty) {
                // 远端更新：丢弃本地元素，标记为未加载，选中时再拉。
                this.host.patchBoard(local.id, {
                    name: remote.name,
                    updatedAt: remote.updatedAt,
                    elements: [],
                    elementsLoaded: false,
                    elementCount: remote.elementCount,
                    remoteVersion: remote.remoteVersion,
                    syncedAt: remote.syncedAt,
                    dirty: false,
                });
            }
        }
        this.host.persistLocal();
        this.host.onStatus(SYNC_STATUS.SYNCED, { reason: 'bootstrap' });
        await this.flushDirty({ silent: true });
    }

    /** 保证某板元素已加载（远端 stub → 拉取）。 */
    async ensureLoaded(board) {
        if (!board || board.elementsLoaded !== false) return board;
        const row = await this.host.store.get(board.id);
        if (!row) throw new RemoteError('云端未找到该白板', { status: 404 });
        const remote = remoteToBoard(row, { withElements: true });
        this.host.patchBoard(board.id, {
            elements: remote.elements,
            elementsLoaded: true,
            elementCount: remote.elementCount,
            remoteVersion: remote.remoteVersion,
            viewport: remote.viewport || board.viewport,
            syncedAt: remote.syncedAt,
            dirty: false,
        });
        this.host.persistLocal();
        return this.host.getBoards().find((item) => item.id === board.id) || board;
    }

    /** 上传单板。explicit=true 时用户可见反馈。 */
    async flush(board, { explicit = false, keepalive = false } = {}) {
        if (!this.enabled || !board || board.elementsLoaded === false) return false;
        if (!explicit && !board.dirty) return false;
        if (isBoardEmpty(board) && board.remoteVersion === 0) {
            if (explicit) this.host.notify('白板还是空的，先画点什么再保存吧', 'info');
            return false;
        }
        const flightKey = board.id;
        if (this.inFlight.has(flightKey)) return this.inFlight.get(flightKey);

        const elements = prepareElements(board.elements);
        const payloadSize = JSON.stringify(elements).length;
        if (payloadSize > REMOTE.MAX_JSON_BYTES) {
            this.noteError(board.id, new RemoteError('白板内容过大（超过 2MB），已保留在本机，请拆分到新白板', { status: 413 }), { silent: !explicit });
            return false;
        }

        const task = (async () => {
            this.host.onStatus(SYNC_STATUS.SAVING, { boardId: board.id });
            try {
                const row = await this.host.store.upsert(board.id, {
                    name: board.name,
                    viewport: board.viewport,
                    elements,
                    baseVersion: board.remoteVersion,
                    keepalive,
                });
                this.lastError = null;
                this.host.patchBoard(board.id, {
                    remoteVersion: Number(row?.version || board.remoteVersion + 1),
                    syncedAt: row?.updated_at || nowIso(),
                    dirty: false,
                });
                this.host.persistLocal();
                this.host.onStatus(SYNC_STATUS.SYNCED, { boardId: board.id });
                if (explicit) this.host.notify('已保存到云端', 'success');
                return true;
            } catch (error) {
                if (error instanceof RemoteError && error.isConflict) {
                    this.resolveConflict(board, error.payload?.board);
                    return false;
                }
                this.noteError(board.id, error, { silent: !explicit });
                return false;
            } finally {
                this.inFlight.delete(flightKey);
                this.host.onStatus(this.statusOf(board), { boardId: board.id });
            }
        })();
        this.inFlight.set(flightKey, task);
        return task;
    }

    async flushDirty({ silent = true, keepalive = false } = {}) {
        if (!this.enabled) return;
        const dirtyBoards = this.host.getBoards().filter((board) => board.dirty && board.elementsLoaded !== false && !isBoardEmpty(board));
        for (const board of dirtyBoards) {
            // 顺序上传，避免并发写库
            // eslint-disable-next-line no-await-in-loop
            await this.flush(board, { explicit: !silent, keepalive });
        }
    }

    /**
     * 冲突：当前板保持内容与选中状态不变，只换一个新 key 并改名「（本机副本）」待上传；
     * 服务端版本以原 key 作为独立条目加入历史。永不静默丢数据，也不会替换用户正在看的画布。
     */
    resolveConflict(localBoard, serverRow) {
        const originalId = localBoard.id;
        this.host.patchBoard(originalId, {
            id: makeId('board'),
            name: `${localBoard.name}（本机副本）`.slice(0, 60),
            remoteVersion: 0,
            syncedAt: null,
            dirty: true,
        });
        if (serverRow) {
            this.host.upsertLocalBoard(remoteToBoard(serverRow, { withElements: Array.isArray(serverRow.elements) }));
        }
        this.host.persistLocal();
        this.host.notify('云端已有更新的版本：你的改动已保留为「本机副本」，云端版本在历史白板中', 'warning');
        this.host.onStatus(SYNC_STATUS.DIRTY, { boardId: localBoard.id, conflict: true });
    }

    async rename(board) {
        if (!this.enabled || !board || board.remoteVersion === 0) return;
        try {
            const row = await this.host.store.rename(board.id, board.name);
            if (row) this.host.patchBoard(board.id, { remoteVersion: Number(row.version || board.remoteVersion) });
        } catch (error) {
            this.noteError(board.id, error, { silent: true });
        }
    }

    async remove(board) {
        if (!this.enabled || !board || board.remoteVersion === 0) return true;
        try {
            await this.host.store.remove(board.id);
            return true;
        } catch (error) {
            this.noteError(board.id, error, { silent: false });
            return false;
        }
    }

    noteError(boardId, error, { silent }) {
        this.lastError = { boardId, error, at: Date.now() };
        console.warn('Whiteboard sync failed:', error);
        this.host.onStatus(SYNC_STATUS.ERROR, { boardId, error });
        if (!silent) {
            const message = error instanceof RemoteError && error.isNetwork
                ? '网络不可用，内容已保留在本机，恢复后会自动上传'
                : (error?.message || '线上保存失败');
            this.host.notify(message, 'error');
        }
    }
}
