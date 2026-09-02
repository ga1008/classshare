/**
 * localStorage 写后缓存（v2），含 v1 迁移与安全裁剪。
 */
import { FAB_STORAGE_NAMESPACE, MAX_BOARDS, STORAGE_NAMESPACE, STORAGE_NAMESPACE_LEGACY } from './constants.js';
import { isBoardEmpty, migrateLegacyState, normalizeState } from './state.js';

function encodeKey(namespace, context) {
    return `${namespace}:${encodeURIComponent(context.userId)}:${encodeURIComponent(context.materialId)}`;
}

export function storageKeys(context) {
    return {
        current: encodeKey(STORAGE_NAMESPACE, context),
        legacy: encodeKey(STORAGE_NAMESPACE_LEGACY, context),
        fab: `${FAB_STORAGE_NAMESPACE}:${encodeURIComponent(context.userId)}`,
    };
}

function readJson(key) {
    try {
        const raw = window.localStorage.getItem(key);
        return raw ? JSON.parse(raw) : null;
    } catch (error) {
        console.warn('Whiteboard local read failed:', key, error);
        return null;
    }
}

/** 读取 v2；没有则迁移 v1（保留 v1 原件以便回滚）。 */
export function loadLocalState(context) {
    const keys = storageKeys(context);
    const v2 = readJson(keys.current);
    if (v2) return normalizeState(v2, context);
    const v1 = readJson(keys.legacy);
    if (v1) return migrateLegacyState(v1, context);
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

/** 保存；空间不足时先裁到 8 块再试。返回 {ok, pruned, state, error}。 */
export function saveLocalState(context, state) {
    const keys = storageKeys(context);
    const attempt = (payload) => {
        window.localStorage.setItem(keys.current, JSON.stringify(payload));
    };
    try {
        attempt(state);
        return { ok: true, pruned: false, state };
    } catch (firstError) {
        const pruned = { ...state, boards: pruneBoards(state.boards, state.activeBoardId, 8) };
        try {
            attempt(pruned);
            return { ok: true, pruned: true, state: pruned };
        } catch (error) {
            console.warn('Whiteboard local save failed after pruning:', firstError, error);
            return { ok: false, pruned: true, state: pruned, error };
        }
    }
}

export function loadFabPosition(context) {
    return readJson(storageKeys(context).fab);
}

export function saveFabPosition(context, position) {
    try {
        window.localStorage.setItem(storageKeys(context).fab, JSON.stringify(position));
    } catch (error) {
        console.warn('Failed to save whiteboard button position:', error);
    }
}
