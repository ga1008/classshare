import { beforeEach, describe, expect, test } from 'vitest';
import { dropLocalBoard, loadLocalState, saveLocalState, storageKeys } from './store_local.js';

class FakeStorage {
    constructor() { this.map = new Map(); this.writes = []; this.quota = Infinity; }
    get length() { return this.map.size; }
    key(index) { return [...this.map.keys()][index] ?? null; }
    getItem(key) { return this.map.has(key) ? this.map.get(key) : null; }
    setItem(key, value) {
        const next = [...this.map.entries()].reduce((sum, [k, v]) => sum + k.length + v.length, 0)
            + key.length + value.length - (this.map.get(key)?.length ?? 0);
        if (next > this.quota) throw new DOMExceptionLike('QuotaExceededError');
        this.map.set(key, value);
        this.writes.push(key);
    }
    removeItem(key) { this.map.delete(key); }
}

class DOMExceptionLike extends Error {}

let storage;
let counter = 0;

function makeContext() {
    counter += 1;
    return { userId: 'u1', materialId: `m${counter}`, materialName: '计算机网络' };
}

function stroke(x = 0) {
    return { type: 'stroke', color: '#f00', size: 4, points: [{ x, y: 0 }, { x: x + 10, y: 10 }] };
}

beforeEach(() => {
    storage = new FakeStorage();
    globalThis.window = { localStorage: storage };
});

describe('store_local: v3 分键存储', () => {
    test('保存只写指定板的板体，其他板体不动', () => {
        const context = makeContext();
        const state = loadLocalState(context);
        const first = state.boards[0];
        state.boards.push({ ...first, id: 'board-b', elements: [stroke(0)], elementsLoaded: true, elementCount: 1 });
        first.elements = [stroke(5)];
        saveLocalState(context, state, { boardIds: [first.id, 'board-b'] });

        storage.writes = [];
        first.elements = [stroke(5), stroke(9)];
        saveLocalState(context, state, { boardIds: [first.id] });

        const keys = storageKeys(context);
        expect(storage.writes.some((key) => key.endsWith(encodeURIComponent(first.id)))).toBe(true);
        expect(storage.writes.some((key) => key.endsWith('board-b'))).toBe(false);
        expect(storage.writes.includes(keys.index)).toBe(true);
    });

    test('索引里不含 elements，板体单独成键', () => {
        const context = makeContext();
        const state = loadLocalState(context);
        state.boards[0].elements = [stroke()];
        saveLocalState(context, state);

        const keys = storageKeys(context);
        const index = JSON.parse(storage.getItem(keys.index));
        expect(index.boards[0].elements).toBeUndefined();
        const body = JSON.parse(storage.getItem(`${keys.boardPrefix}${encodeURIComponent(state.boards[0].id)}`));
        expect(body.elements).toHaveLength(1);
    });

    test('重新载入可以还原每块板的 elements', () => {
        const context = makeContext();
        const state = loadLocalState(context);
        state.boards[0].elements = [stroke(1), stroke(2)];
        state.boards.push({
            ...state.boards[0], id: 'board-b', name: '第二块', elements: [stroke(3)], elementsLoaded: true, elementCount: 1,
        });
        saveLocalState(context, state);

        const reloaded = loadLocalState(context);
        expect(reloaded.boards).toHaveLength(2);
        expect(reloaded.boards[0].elements).toHaveLength(2);
        expect(reloaded.boards.find((board) => board.id === 'board-b').elements).toHaveLength(1);
    });

    test('远端 stub 不写板体，重载后仍是未加载状态', () => {
        const context = makeContext();
        const state = loadLocalState(context);
        state.boards.push({
            ...state.boards[0], id: 'remote-1', elements: [], elementsLoaded: false, elementCount: 7, remoteVersion: 3,
        });
        saveLocalState(context, state);
        const keys = storageKeys(context);
        expect(storage.getItem(`${keys.boardPrefix}remote-1`)).toBe(null);

        const reloaded = loadLocalState(context);
        const remote = reloaded.boards.find((board) => board.id === 'remote-1');
        expect(remote.elementsLoaded).toBe(false);
        expect(remote.elementCount).toBe(7);
    });

    test('板被移除后其板体会被回收', () => {
        const context = makeContext();
        const state = loadLocalState(context);
        state.boards.push({ ...state.boards[0], id: 'board-b', elements: [stroke()], elementsLoaded: true, elementCount: 1 });
        saveLocalState(context, state);
        const keys = storageKeys(context);
        expect(storage.getItem(`${keys.boardPrefix}board-b`)).toBeTruthy();

        state.boards = state.boards.filter((board) => board.id !== 'board-b');
        saveLocalState(context, state);
        expect(storage.getItem(`${keys.boardPrefix}board-b`)).toBe(null);
    });

    test('dropLocalBoard 立即删掉板体', () => {
        const context = makeContext();
        const state = loadLocalState(context);
        state.boards.push({ ...state.boards[0], id: 'board-c', elements: [stroke()], elementsLoaded: true, elementCount: 1 });
        saveLocalState(context, state);
        const keys = storageKeys(context);
        dropLocalBoard(context, 'board-c');
        expect(storage.getItem(`${keys.boardPrefix}board-c`)).toBe(null);
    });
});

describe('store_local: 迁移', () => {
    test('v2 单键会被读出并按 v3 落盘', () => {
        const context = makeContext();
        const keys = storageKeys(context);
        storage.setItem(keys.current, JSON.stringify({
            version: 2,
            activeBoardId: 'legacy-1',
            boards: [{ id: 'legacy-1', name: '旧板', elements: [stroke()] }],
            settings: { brushColor: '#123456' },
        }));

        const state = loadLocalState(context);
        expect(state.boards[0].id).toBe('legacy-1');
        expect(state.boards[0].elements).toHaveLength(1);
        expect(state.settings.brushColor).toBe('#123456');
        // v2 原键保留，便于回滚
        expect(storage.getItem(keys.current)).toBeTruthy();
        expect(storage.getItem(keys.index)).toBeTruthy();
        expect(JSON.parse(storage.getItem(`${keys.boardPrefix}legacy-1`)).elements).toHaveLength(1);
    });

    test('v1 键仍可迁移，非空板标记为待上传', () => {
        const context = makeContext();
        const keys = storageKeys(context);
        storage.setItem(keys.legacy, JSON.stringify({
            version: 1,
            activeBoardId: 'v1-1',
            boards: [{ id: 'v1-1', name: '很旧的板', elements: [stroke()] }],
            settings: { brushColor: '#0f172a' },
        }));

        const state = loadLocalState(context);
        expect(state.boards[0].dirty).toBe(true);
        expect(state.settings.brushColor).toBe('#ff0000');
        expect(storage.getItem(keys.index)).toBeTruthy();
    });
});

describe('store_local: 配额', () => {
    test('写不下时裁到 8 块、回收多余板体后重试成功', () => {
        const context = makeContext();
        const base = loadLocalState(context).boards[0];
        const makeBoards = () => Array.from({ length: 12 }, (_, index) => ({
            ...base,
            id: `b${index}`,
            name: `板 ${index}`,
            updatedAt: `2026-09-${String(index + 10)}T00:00:00.000Z`,
            elements: [{ type: 'stroke', color: '#f00', size: 4, points: Array.from({ length: 60 }, (_, p) => ({ x: p, y: p })) }],
            elementsLoaded: true,
            elementCount: 1,
            dirty: false,
            remoteVersion: 1,
        }));

        const full = { ...loadLocalState(context), boards: makeBoards(), activeBoardId: 'b0' };
        saveLocalState(context, full);
        const usedBytes = [...storage.map.entries()].reduce((sum, [key, value]) => sum + key.length + value.length, 0);

        // 清空重来，把配额卡在「12 块放不下、8 块放得下」之间。
        storage.map.clear();
        storage.quota = Math.round(usedBytes * 0.8);
        const result = saveLocalState(context, { ...full, boards: makeBoards() });

        expect(result.ok).toBe(true);
        expect(result.pruned).toBe(true);
        expect(result.state.boards).toHaveLength(8);
        expect(result.state.boards.some((board) => board.id === 'b0')).toBe(true);
        // 被裁掉的板体不应残留
        const keys = storageKeys(context);
        const kept = new Set(result.state.boards.map((board) => `${keys.boardPrefix}${board.id}`));
        const bodies = [...storage.map.keys()].filter((key) => key.startsWith(keys.boardPrefix));
        expect(bodies.every((key) => kept.has(key))).toBe(true);
    });
});

describe('store_local: 远端降级', () => {
    test('板被降级为远端 stub 时旧板体被回收', () => {
        const context = makeContext();
        const state = loadLocalState(context);
        const board = state.boards[0];
        board.elements = [stroke(), stroke(3)];
        saveLocalState(context, state);
        const keys = storageKeys(context);
        expect(storage.getItem(`${keys.boardPrefix}${encodeURIComponent(board.id)}`)).toBeTruthy();

        // bootstrap 发现云端更新：丢掉本地元素，改为按需拉取。
        board.elements = [];
        board.elementsLoaded = false;
        board.elementCount = 9;
        saveLocalState(context, state, { boardIds: [board.id] });
        expect(storage.getItem(`${keys.boardPrefix}${encodeURIComponent(board.id)}`)).toBe(null);
        expect(loadLocalState(context).boards[0].elementCount).toBe(9);
    });
});
