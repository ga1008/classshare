/**
 * 本地缓存 ↔ 线上同步控制器：合并、dirty 队列、定时/事件触发、冲突保留副本。
 * 宿主（board.js）通过回调提供状态访问，本模块不直接操作 DOM。
 */
import { REMOTE, SYNC_STATUS } from './constants.js';
import { simplifyStroke } from './geometry.js';
import { isBoardEmpty, makeId, nowIso } from './state.js';
import { RemoteError, remoteToBoard } from './store_remote.js';

/**
 * 上传前抽稀的结果按元素缓存。
 *
 * 元素提交后不可变（见 `state.cloneElements` 的契约），所以抽稀结果是稳定的。
 * 原来每次同步都要把整块板重算一遍 RDP —— 30 秒一次的周期性长任务，板越大越明显。
 * 用 WeakMap 而不是挂在元素上，避免运行时字段被写进 localStorage 或上传。
 */
const PREPARED_CACHE = new WeakMap();

function prepareElement(element) {
    if (!element || typeof element !== 'object') return element;
    const cached = PREPARED_CACHE.get(element);
    if (cached !== undefined) return cached;
    let prepared = element;
    if ((element.type === 'stroke' || element.type === 'eraser') && Array.isArray(element.points) && element.points.length > 2) {
        const points = simplifyStroke(element.points, REMOTE.SIMPLIFY_TOLERANCE);
        // 点数没减少就别造新对象，省一次分配也省一份内存。
        if (points.length !== element.points.length) prepared = { ...element, points };
    }
    PREPARED_CACHE.set(element, prepared);
    return prepared;
}

export function prepareElements(elements) {
    return (elements || []).map(prepareElement);
}

/** UTF-8 字节数（服务端也是按字节卡 2MB；`String.length` 对中文会低估一半以上）。 */
export function byteLength(text) {
    if (typeof TextEncoder === 'function') return new TextEncoder().encode(text).length;
    return unescape(encodeURIComponent(text)).length;
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
        this.intervalMs = REMOTE.AUTO_SYNC_INTERVAL_MS;
        this.inFlight = new Map();
        this.loading = new Map();
        this.removing = new Set();
        this.lastError = null;
        this.bootstrapped = false;
        this.enabled = true;
    }

    start(intervalMs = REMOTE.AUTO_SYNC_INTERVAL_MS) {
        if (this.timer) return;
        this.intervalMs = Math.max(5_000, Number(intervalMs) || REMOTE.AUTO_SYNC_INTERVAL_MS);
        // 定时同步改到空闲帧里做，并且落笔期间一律让路 —— 否则每 30 秒会在某一帧里
        // 插进「遍历 + 序列化 + 发请求」，正好压在正在写的那一笔上。
        this.timer = window.setInterval(() => this.requestIdleFlush(), this.intervalMs);
    }

    requestIdleFlush() {
        const run = () => this.flushDirty({ silent: true, respectBusy: true });
        if (typeof window.requestIdleCallback === 'function') {
            window.requestIdleCallback(run, { timeout: REMOTE.IDLE_FLUSH_TIMEOUT_MS });
        } else {
            run();
        }
    }

    stop() {
        window.clearInterval(this.timer);
        this.timer = null;
    }

    /** 换周期（性能档位切换时用）。定时器没跑就什么也不做，`start` 时自然会用新值。 */
    restart(intervalMs) {
        if (!this.timer) return;
        this.stop();
        this.start(intervalMs);
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
        const id = board.id;
        if (this.loading.has(id)) return this.loading.get(id);
        const task = (async () => {
            const row = await this.host.store.get(id);
            if (!row) throw new RemoteError('云端未找到该白板', { status: 404 });
            const current = this.host.getBoards().find((item) => item.id === id);
            if (!current || current.elementsLoaded !== false) return current;
            const remote = remoteToBoard(row, { withElements: true });
            this.host.patchBoard(id, {
                elements: remote.elements,
                elementsLoaded: true,
                elementCount: remote.elementCount,
                remoteVersion: remote.remoteVersion,
                viewport: remote.viewport || current.viewport,
                syncedAt: remote.syncedAt,
                dirty: Boolean(current.dirty),
            });
            this.host.persistLocal();
            return this.host.getBoards().find((item) => item.id === id);
        })();
        this.loading.set(id, task);
        try {
            return await task;
        } finally {
            this.loading.delete(id);
        }
    }

    /** 上传单板。explicit=true 时用户可见反馈。 */
    async flush(board, { explicit = false, keepalive = false } = {}) {
        if (!this.enabled || !board || board.elementsLoaded === false || this.removing.has(board.id)) return false;
        if (!explicit && !board.dirty) return false;
        if (isBoardEmpty(board) && board.remoteVersion === 0) {
            if (explicit) this.host.notify('白板还是空的，先画点什么再保存吧', 'info');
            return false;
        }
        const flightKey = board.id;
        if (this.inFlight.has(flightKey)) {
            const saved = await this.inFlight.get(flightKey);
            return saved && explicit && board.dirty
                ? this.flush(board, { explicit, keepalive }) : saved;
        }

        // 请求等待期间仍可继续绘制。数组会追加元素，不能只比较数组引用，
        // 也不能只靠毫秒时间戳；保存的是提交时的快照，后续修改必须继续待同步。
        const snapshot = {
            name: board.name,
            updatedAt: board.updatedAt,
            viewport: { ...board.viewport },
            elements: board.elements.slice(),
            remoteVersion: board.remoteVersion,
        };

        // 只序列化一次：量体积和实际发送共用同一份字符串（原来是各 stringify 一遍）。
        const serialized = JSON.stringify({
            name: snapshot.name,
            viewport: snapshot.viewport,
            elements: prepareElements(snapshot.elements),
            schema_version: 2,
            base_version: snapshot.remoteVersion,
        });
        if (byteLength(serialized) > REMOTE.MAX_JSON_BYTES) {
            this.noteError(board.id, new RemoteError('白板内容过大（超过 2MB），已保留在本机，请拆分到新白板', { status: 413 }), { silent: !explicit });
            return false;
        }

        const task = (async () => {
            // 先登记 inFlight，再通知宿主或调用可能同步失败的存储适配器。
            await Promise.resolve();
            this.host.onStatus(SYNC_STATUS.SAVING, { boardId: board.id });
            try {
                const row = await this.host.store.upsert(flightKey, { serialized, keepalive });
                this.lastError = null;
                const current = this.host.getBoards().find((item) => item.id === flightKey);
                if (!current) return true;
                const changed = current.name !== snapshot.name || current.updatedAt !== snapshot.updatedAt
                    || current.elements.length !== snapshot.elements.length
                    || current.elements.some((element, index) => element !== snapshot.elements[index])
                    || ['x', 'y', 'scale'].some((key) => current.viewport?.[key] !== snapshot.viewport[key]);
                this.host.patchBoard(flightKey, {
                    remoteVersion: Number(row?.version || snapshot.remoteVersion + 1),
                    syncedAt: row?.updated_at || nowIso(),
                    dirty: changed,
                });
                this.host.persistLocal();
                this.host.onStatus(changed ? SYNC_STATUS.DIRTY : SYNC_STATUS.SYNCED, { boardId: flightKey });
                if (explicit) this.host.notify(changed ? '本次内容已保存，新增改动将继续自动同步' : '已保存到云端', 'success');
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

    async flushDirty({ silent = true, keepalive = false, respectBusy = false } = {}) {
        if (!this.enabled) return;
        if (respectBusy && this.host.isBusy?.()) return;
        const dirtyBoards = this.host.getBoards().filter((board) => board.dirty
            && (board.remoteVersion > 0 || !isBoardEmpty(board)));
        for (const board of dirtyBoards) {
            if (board.elementsLoaded === false) {
                try {
                    // 离线重命名的历史板可能尚未下载，网络恢复后也要能自动完成。
                    // eslint-disable-next-line no-await-in-loop
                    await this.ensureLoaded(board);
                } catch (error) {
                    this.noteError(board.id, error, { silent });
                    continue;
                }
            }
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
        this.host.notify(serverRow
            ? '云端已有更新的版本：你的改动已保留为「本机副本」，云端版本在历史白板中'
            : '云端白板已删除或发生变化：你的改动已保留为「本机副本」', 'warning');
        this.host.onStatus(SYNC_STATUS.DIRTY, { boardId: localBoard.id, conflict: true });
    }

    async rename(board) {
        if (!this.enabled || !board) return;
        // 重命名也属于可离线恢复的改动，并与整板保存共用版本和在途队列。
        // 单独 PATCH 可能被更早发出的 PUT 覆盖，网络失败也不会进入重试队列。
        this.host.patchBoard(board.id, { dirty: true });
        this.host.persistLocal();
        try {
            if (board.elementsLoaded === false) await this.ensureLoaded(board);
            const pending = this.inFlight.get(board.id);
            if (pending) await pending;
            if (board.dirty) await this.flush(board);
        } catch (error) {
            this.noteError(board.id, error, { silent: true });
        }
    }

    async remove(board) {
        if (!this.enabled || !board) return true;
        const originalId = board.id;
        this.removing.add(originalId);
        try {
            // 新板可能仍在首次上传；先等待结果，避免 DELETE 后迟到的 PUT 将它复活。
            const pending = this.inFlight.get(originalId);
            if (pending) await pending;
            if (board.id !== originalId) return false;
            if (board.remoteVersion > 0) await this.host.store.remove(originalId);
            return true;
        } catch (error) {
            if (error instanceof RemoteError && error.status === 404) return true;
            this.noteError(board.id, error, { silent: false });
            return false;
        } finally {
            this.removing.delete(originalId);
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
