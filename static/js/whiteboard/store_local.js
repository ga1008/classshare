/**
 * localStorage 写后缓存（v3 分键），含 v2/v1 迁移与安全裁剪。
 *
 * v2 把「整份 state（最多 24 块板的全部 elements）」塞进一个键，于是每次抬笔都要
 * 全量 `JSON.stringify` + 同步写盘 —— 这是大板「画着画着突然顿一下」的主因之一。
 * v3 拆成两类键：
 *   索引 `teacher-whiteboard:v3:{user}:{material}`            → settings + 板元信息（体积恒定、很小）
 *   板体 `teacher-whiteboard:v3b:{user}:{material}:{boardId}` → 单块板的 elements
 * 保存时只写索引 + 真正改动的那几块板，单次开销从 O(全部板) 降到 O(活动板)。
 * v2 / v1 的旧键一律保留不动，便于回滚。
 */
import {
    FAB_STORAGE_NAMESPACE, MAX_BOARDS, PERF_STORAGE_NAMESPACE, STORAGE_BOARD_NAMESPACE,
    STORAGE_INDEX_NAMESPACE, STORAGE_NAMESPACE, STORAGE_NAMESPACE_LEGACY,
} from './constants.js';
import { hasInkElements, isBoardEmpty, makeId, migrateLegacyState, normalizeState, sanitizeBoard } from './state.js';

// 仅运行时保存读取基线，防止另一个标签页写入后被旧索引覆盖及当成孤儿删除。
const localBaselines = new WeakMap();

function rememberIndex(state, index) {
    localBaselines.set(state, { revision: index?._localRevision || '', ids: new Set((index?.boards || []).map((board) => board.id)) });
}

function encodeKey(namespace, context) {
    return `${namespace}:${encodeURIComponent(context.userId)}:${encodeURIComponent(context.materialId)}`;
}

export function storageKeys(context) {
    return {
        index: encodeKey(STORAGE_INDEX_NAMESPACE, context),
        boardPrefix: `${encodeKey(STORAGE_BOARD_NAMESPACE, context)}:`,
        current: encodeKey(STORAGE_NAMESPACE, context),
        legacy: encodeKey(STORAGE_NAMESPACE_LEGACY, context),
        fab: `${FAB_STORAGE_NAMESPACE}:${encodeURIComponent(context.userId)}`,
        perf: `${PERF_STORAGE_NAMESPACE}:${encodeURIComponent(context.userId)}`,
    };
}

function storage() {
    return window.localStorage;
}

function readJson(key) {
    try {
        const raw = storage().getItem(key);
        return raw ? JSON.parse(raw) : null;
    } catch (error) {
        console.warn('Whiteboard local read failed:', key, error);
        return null;
    }
}

function boardKey(keys, boardId) {
    return `${keys.boardPrefix}${encodeURIComponent(String(boardId))}`;
}

/** 索引里只留元信息，elements 落在各自的板体键。 */
function toIndexEntry(board) {
    const { elements, ...meta } = board;
    return meta;
}

function hydrateBoard(meta, keys) {
    if (!meta || meta.elementsLoaded === false) return meta;
    const body = readJson(boardKey(keys, meta.id));
    if (Array.isArray(body?.elements)) return { ...meta, elements: body.elements };
    // 配额回收后索引写失败，或浏览器清掉部分键时，云端板必须重新拉取。
    // 不能把缺失的板体当作已载入的空白板，否则同版本的云端内容永远不会恢复。
    if (meta.remoteVersion > 0) return { ...meta, elements: [], elementsLoaded: false };
    return { ...meta, elements: [] };
}

function hydrate(index, keys, context) {
    const metas = Array.isArray(index?.boards) ? index.boards : [];
    const state = normalizeState({
        ...index,
        boards: metas.map((meta) => hydrateBoard(meta, keys)),
    }, context);
    rememberIndex(state, index);
    return state;
}

/** 读取 v3；没有则依次迁移 v2 / v1（保留旧键以便回滚），迁移后立刻按 v3 落盘。 */
export function loadLocalState(context) {
    const keys = storageKeys(context);
    const index = readJson(keys.index);
    if (index) return hydrate(index, keys, context);

    const v2 = readJson(keys.current);
    if (v2) {
        const state = normalizeState(v2, context);
        saveLocalState(context, state, { boardIds: state.boards.map((board) => board.id) });
        return state;
    }
    const v1 = readJson(keys.legacy);
    if (v1) {
        const state = migrateLegacyState(v1, context);
        saveLocalState(context, state, { boardIds: state.boards.map((board) => board.id) });
        return state;
    }
    const state = normalizeState(null, context);
    rememberIndex(state, null);
    return state;
}

/**
 * 另一标签页有新提交时先合并，避免旧索引覆盖掉其新增白板。
 * 同一块板内容分叉时保留独立副本，由正常同步队列上传；不替换当前画布引用。
 * 只在索引版本变化时读取其他板体，单标签的普通保存仍只序列化目标板。
 */
function mergeOtherTab(keys, state, targets) {
    const latest = readJson(keys.index);
    if (!latest || !Array.isArray(latest.boards)) return;
    const baseline = localBaselines.get(state);
    if (baseline && baseline.revision === (latest._localRevision || '')) return;
    const byId = new Map(state.boards.map((board) => [board.id, board]));
    for (const meta of latest.boards) {
        const local = byId.get(meta.id);
        // 本标签明确删除或裁剪的板沿用删除结果。
        if (!local && baseline?.ids.has(meta.id)) continue;
        const incoming = sanitizeBoard(hydrateBoard(meta, keys));
        if (!local) {
            state.boards.push(incoming);
            byId.set(incoming.id, incoming);
            continue;
        }
        const sameContent = local.name === incoming.name
            && local.elementsLoaded !== false && incoming.elementsLoaded !== false
            && JSON.stringify(local.elements) === JSON.stringify(incoming.elements);
        if (sameContent) {
            // 相同内容可以接收已经确认的较新云端版本，避免无谓的版本冲突。
            if (incoming.remoteVersion > local.remoteVersion) {
                local.remoteVersion = incoming.remoteVersion;
                local.syncedAt = incoming.syncedAt;
            }
            continue;
        }
        if (incoming.elementsLoaded === false || isBoardEmpty(incoming)) continue;
        const copyName = `${incoming.name}（其他标签副本）`.slice(0, 60);
        const incomingElements = JSON.stringify(incoming.elements);
        // 两个标签可能反复交替保存同一对分叉。已保留的相同副本应复用，
        // 否则每次索引版本变化都会再造一块，最终撑爆本地存储。
        if (state.boards.some((board) => board.id !== local.id && board.name === copyName
            && board.elementsLoaded !== false && JSON.stringify(board.elements) === incomingElements)) continue;
        const copy = {
            ...incoming, id: makeId('board'), name: copyName,
            remoteVersion: 0, syncedAt: null, dirty: true,
        };
        state.boards.push(copy);
        targets.add(copy.id);
    }
}

/**
 * 裁剪本地板数：保留活动板与所有「本地未同步且非空」的板，其余按更新时间保留到上限。
 * 返回新数组（不改原数组）。
 */
export function pruneBoards(boards, activeId, limit = MAX_BOARDS) {
    if (!Array.isArray(boards) || boards.length <= limit) return boards;
    const mustKeep = new Set(
        boards.filter((board) => board.id === activeId || board.dirty || (board.remoteVersion === 0 && !isBoardEmpty(board))).map((board) => board.id),
    );
    const sorted = [...boards].sort((a, b) => String(b.updatedAt || '').localeCompare(String(a.updatedAt || '')));
    const keep = new Set(mustKeep);
    for (const board of sorted) {
        if (keep.size >= limit) break;
        keep.add(board.id);
    }
    return boards.filter((board) => keep.has(board.id));
}

/** 板集合签名，用于跳过绝大多数不必要的孤儿键扫描。 */
const lastBoardSignature = new Map();

function sweepOrphanBodies(keys, state, { force = false } = {}) {
    const ids = state.boards.map((board) => String(board.id));
    const signature = ids.join('|');
    if (!force && lastBoardSignature.get(keys.index) === signature) return;
    const keep = new Set(ids.map((id) => boardKey(keys, id)));
    const store = storage();
    for (let index = store.length - 1; index >= 0; index -= 1) {
        const key = store.key(index);
        if (key && key.startsWith(keys.boardPrefix) && !keep.has(key)) store.removeItem(key);
    }
    lastBoardSignature.set(keys.index, signature);
}

function writeBoardBodies(keys, state, targets) {
    for (const board of state.boards) {
        if (!targets.has(board.id)) continue;
        // 板被降级成远端 stub（bootstrap 发现云端更新）时要连带回收旧板体，
        // 否则那份可能上兆的内容会一直占着配额，而它已经不会再被读取。
        if (board.elementsLoaded === false) {
            storage().removeItem(boardKey(keys, board.id));
            continue;
        }
        storage().setItem(boardKey(keys, board.id), JSON.stringify({ elements: board.elements || [] }));
    }
}

function writeIndex(keys, state) {
    const index = {
        _localRevision: makeId('local'),
        version: state.version,
        activeBoardId: state.activeBoardId,
        settings: state.settings,
        boards: state.boards.map(toIndexEntry),
    };
    storage().setItem(keys.index, JSON.stringify(index));
    rememberIndex(state, index);
}

/** 索引提交前任何一块板写失败，恢复本批旧板体，不能半写后丢掉已保存的分叉。 */
function writeSnapshot(keys, state, targets) {
    const store = storage();
    const previousBodies = new Map();
    for (const board of state.boards) {
        if (targets.has(board.id)) {
            const key = boardKey(keys, board.id);
            previousBodies.set(key, store.getItem(key));
        }
    }
    try {
        writeBoardBodies(keys, state, targets);
        writeIndex(keys, state);
    } catch (error) {
        try {
            // 先释放本批新增键，再按占用减少优先恢复，保证原先可容纳的数据仍可放回。
            for (const [key, value] of previousBodies) {
                if (value === null) store.removeItem(key);
            }
            const originals = [...previousBodies].filter(([, value]) => value !== null)
                .sort(([leftKey, left], [rightKey, right]) => (left.length - (store.getItem(leftKey)?.length || 0))
                    - (right.length - (store.getItem(rightKey)?.length || 0)));
            for (const [key, value] of originals) {
                if (store.getItem(key) !== value) store.setItem(key, value);
            }
        } catch (rollbackError) {
            console.warn('Whiteboard local save rollback failed:', rollbackError);
        }
        throw error;
    }
}

/**
 * 保存。默认只写索引 + `boardIds` 指定的板体（不传则写全部）。
 * 空间不足时先裁到 8 块、清掉被裁板的板体再试。返回 {ok, pruned, state, error}。
 */
export function saveLocalState(context, state, { boardIds = null } = {}) {
    const keys = storageKeys(context);
    const targets = boardIds ? new Set(boardIds) : new Set(state.boards.map((board) => board.id));
    try {
        mergeOtherTab(keys, state, targets);
        writeSnapshot(keys, state, targets);
        sweepOrphanBodies(keys, state);
        return { ok: true, pruned: false, state };
    } catch (firstError) {
        const pruned = { ...state, boards: pruneBoards(state.boards, state.activeBoardId, 8) };
        try {
            // 先删掉被裁掉的板体腾出配额，再重写。
            sweepOrphanBodies(keys, pruned, { force: true });
            writeSnapshot(keys, pruned, new Set(pruned.boards.map((board) => board.id)));
            return { ok: true, pruned: true, state: pruned };
        } catch (error) {
            console.warn('Whiteboard local save failed after pruning:', firstError, error);
            return { ok: false, pruned: true, state: pruned, error };
        }
    }
}

/** 删除某块板的本地板体（删板时调用，避免等到下次签名变化才回收）。 */
export function dropLocalBoard(context, boardId) {
    try {
        storage().removeItem(boardKey(storageKeys(context), boardId));
    } catch (error) {
        console.warn('Whiteboard local board drop failed:', boardId, error);
    }
}

/** 板体是否真的有内容（诊断用，避免为判空去 hydrate 整块板）。 */
export function localBoardHasInk(context, boardId) {
    const body = readJson(boardKey(storageKeys(context), boardId));
    return hasInkElements(body?.elements);
}

export function loadFabPosition(context) {
    return readJson(storageKeys(context).fab);
}

export function saveFabPosition(context, position) {
    try {
        storage().setItem(storageKeys(context).fab, JSON.stringify(position));
    } catch (error) {
        console.warn('Failed to save whiteboard button position:', error);
    }
}

/** 性能档位按设备存：慢的是这台讲台机，不该跟着账号同步到别的机器上。 */
export function loadPerfMode(context) {
    try {
        return window.localStorage.getItem(storageKeys(context).perf) || '';
    } catch (error) {
        console.warn('Whiteboard perf mode read failed:', error);
        return '';
    }
}

export function savePerfMode(context, mode) {
    try {
        window.localStorage.setItem(storageKeys(context).perf, String(mode));
    } catch (error) {
        console.warn('Whiteboard perf mode save failed:', error);
    }
}
