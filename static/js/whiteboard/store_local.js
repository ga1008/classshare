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
import { hasInkElements, isBoardEmpty, migrateLegacyState, normalizeState } from './state.js';

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

function hydrate(index, keys, context) {
    const metas = Array.isArray(index?.boards) ? index.boards : [];
    return normalizeState({
        ...index,
        boards: metas.map((meta) => {
            // 远端 stub 本地没有板体，选中时再从服务端拉。
            if (!meta || meta.elementsLoaded === false) return meta;
            const body = readJson(boardKey(keys, meta.id));
            return { ...meta, elements: Array.isArray(body?.elements) ? body.elements : [] };
        }),
    }, context);
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
    return normalizeState(null, context);
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
    storage().setItem(keys.index, JSON.stringify({
        version: state.version,
        activeBoardId: state.activeBoardId,
        settings: state.settings,
        boards: state.boards.map(toIndexEntry),
    }));
}

/**
 * 保存。默认只写索引 + `boardIds` 指定的板体（不传则写全部）。
 * 空间不足时先裁到 8 块、清掉被裁板的板体再试。返回 {ok, pruned, state, error}。
 */
export function saveLocalState(context, state, { boardIds = null } = {}) {
    const keys = storageKeys(context);
    const targets = boardIds ? new Set(boardIds) : new Set(state.boards.map((board) => board.id));
    try {
        writeBoardBodies(keys, state, targets);
        writeIndex(keys, state);
        sweepOrphanBodies(keys, state);
        return { ok: true, pruned: false, state };
    } catch (firstError) {
        const pruned = { ...state, boards: pruneBoards(state.boards, state.activeBoardId, 8) };
        try {
            // 先删掉被裁掉的板体腾出配额，再重写。
            sweepOrphanBodies(keys, pruned, { force: true });
            writeBoardBodies(keys, pruned, new Set(pruned.boards.map((board) => board.id)));
            writeIndex(keys, pruned);
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
