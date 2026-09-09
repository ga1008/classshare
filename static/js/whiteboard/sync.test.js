import { describe, expect, test, vi } from 'vitest';
import { REMOTE } from './constants.js';
import { byteLength, prepareElements, SyncController } from './sync.js';
import { createBoard } from './state.js';
import { RemoteError } from './store_remote.js';

function densePoints(count) {
    // 近似直线上的密集点：RDP 会把中间点几乎全部去掉。
    return Array.from({ length: count }, (_, index) => ({ x: index, y: index * 0.001 }));
}

describe('sync: 上传前抽稀', () => {
    test('密集点会被抽稀，且首尾保留', () => {
        const element = { id: 's', type: 'stroke', color: '#f00', size: 2, points: densePoints(50) };
        const [prepared] = prepareElements([element]);
        expect(prepared).not.toBe(element);
        expect(prepared.points.length).toBeLessThanOrEqual(element.points.length);
        expect(prepared.points[0]).toEqual(element.points[0]);
        expect(prepared.points.at(-1)).toEqual(element.points.at(-1));
        // 未变的字段原样带上
        expect(prepared.id).toBe('s');
        expect(prepared.color).toBe('#f00');
    });

    test('同一个元素重复调用直接命中缓存（不重算 RDP）', () => {
        const element = { id: 's', type: 'stroke', size: 2, points: densePoints(80) };
        const first = prepareElements([element])[0];
        const second = prepareElements([element])[0];
        expect(second).toBe(first);
    });

    test('抽不掉任何点时原样返回，不造新对象', () => {
        const element = { id: 's', type: 'stroke', size: 2, points: [{ x: 0, y: 0 }, { x: 0, y: 60 }, { x: 60, y: 0 }] };
        expect(prepareElements([element])[0]).toBe(element);
    });

    test('橡皮同样参与抽稀，其他类型原样通过', () => {
        const eraser = { type: 'eraser', size: 10, points: densePoints(40) };
        expect(prepareElements([eraser])[0].points.length).toBeLessThan(40);

        const text = { type: 'text', text: '板书', x: 0, y: 0 };
        const shape = { type: 'shape', shape: 'circle', x1: 0, y1: 0, x2: 10, y2: 10 };
        expect(prepareElements([text, shape])).toEqual([text, shape]);
    });

    test('点数不超过 2 的笔画不动', () => {
        const element = { type: 'stroke', size: 2, points: [{ x: 0, y: 0 }, { x: 5, y: 5 }] };
        expect(prepareElements([element])[0]).toBe(element);
    });

    test('空输入安全', () => {
        expect(prepareElements(undefined)).toEqual([]);
        expect(prepareElements([])).toEqual([]);
    });
});

describe('sync: 体积计量', () => {
    test('按 UTF-8 字节数算，中文不再被低估', () => {
        expect(byteLength('abc')).toBe(3);
        expect(byteLength('白板')).toBe(6);
        // 这正是原来用 String.length 的问题：字符数只有字节数的三分之一
        expect('白板'.length).toBe(2);
    });

    test('上限仍是 2MB', () => {
        expect(REMOTE.MAX_JSON_BYTES).toBe(2 * 1024 * 1024);
    });
});

function deferred() {
    let resolve; let reject;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    return { promise, resolve, reject };
}

function syncFixture() {
    const board = createBoard('课堂板书');
    board.elements = [{ type: 'text', text: '第一笔', x: 1, y: 2 }];
    board.dirty = true;
    const boards = [board];
    const host = {
        getBoards: () => boards,
        patchBoard: (id, patch) => Object.assign(boards.find((item) => item.id === id), patch),
        upsertLocalBoard: (value) => boards.push(value),
        persistLocal: vi.fn(), onStatus: vi.fn(), notify: vi.fn(),
        store: {
            upsert: vi.fn().mockResolvedValue({ version: 1 }),
            get: vi.fn(), remove: vi.fn().mockResolvedValue({}),
        },
    };
    return { board, boards, host, sync: new SyncController(host) };
}

describe('sync: 保存并发和离线恢复', () => {
    test('同毫秒追加笔迹仍保留 dirty，下一次上传带最新元素和版本', async () => {
        const { board, host, sync } = syncFixture();
        const pending = deferred();
        host.store.upsert.mockReturnValueOnce(pending.promise);
        const firstSave = sync.flush(board);
        await Promise.resolve();
        board.elements.push({ type: 'text', text: '第二笔' });
        pending.resolve({ version: 4 });
        await firstSave;
        expect(board.dirty).toBe(true);
        expect(board.remoteVersion).toBe(4);
        expect(JSON.parse(host.store.upsert.mock.calls[0][1].serialized).elements).toHaveLength(1);
        await sync.flushDirty();
        expect(JSON.parse(host.store.upsert.mock.calls[1][1].serialized)).toMatchObject({ base_version: 4, elements: board.elements });
        expect(board.dirty).toBe(false);
    });

    test('在途显式保存会等待并补存新增改动', async () => {
        const { board, host, sync } = syncFixture();
        const pending = deferred();
        host.store.upsert.mockReturnValueOnce(pending.promise);
        const firstSave = sync.flush(board);
        board.elements = [...board.elements, { type: 'text', text: '第二笔' }];
        const explicitSave = sync.flush(board, { explicit: true });
        pending.resolve({ version: 1 });
        await Promise.all([firstSave, explicitSave]);
        expect(host.store.upsert).toHaveBeenCalledTimes(2);
        expect(board.dirty).toBe(false);
    });

    test('清空已线上保存的板仍自动上传，纯本地空板不上传', async () => {
        const { board, host, sync } = syncFixture();
        board.elements = [];
        await sync.flushDirty();
        expect(host.store.upsert).not.toHaveBeenCalled();
        board.remoteVersion = 3;
        await sync.flushDirty();
        expect(JSON.parse(host.store.upsert.mock.calls[0][1].serialized)).toMatchObject({ elements: [], base_version: 3 });
        expect(board.dirty).toBe(false);
    });

    test('离线重命名保留 dirty，恢复网络后自动上传新名称', async () => {
        const { board, host, sync } = syncFixture();
        board.remoteVersion = 2;
        board.dirty = false;
        board.name = '离线新名称';
        host.store.upsert.mockRejectedValueOnce(new RemoteError('离线'));
        await sync.rename(board);
        expect(board.dirty).toBe(true);
        await sync.flushDirty();
        expect(JSON.parse(host.store.upsert.mock.calls[1][1].serialized).name).toBe('离线新名称');
        expect(board.dirty).toBe(false);
    });

    test('重命名远端历史板先加载内容，不会覆盖为空板', async () => {
        const { board, host, sync } = syncFixture();
        board.remoteVersion = 2; board.elementsLoaded = false; board.elements = []; board.dirty = false;
        board.name = '新的历史名称';
        host.store.get.mockResolvedValue({ board_key: board.id, name: '旧名', version: 3, elements: [{ type: 'text', text: '云端原笔迹' }], element_count: 1 });
        await sync.rename(board);
        expect(JSON.parse(host.store.upsert.mock.calls[0][1].serialized)).toMatchObject({ name: '新的历史名称', base_version: 3, elements: [{ type: 'text', text: '云端原笔迹' }] });
    });

    test('历史板离线重命名在网络恢复后自动下载并上传，无需用户先选中', async () => {
        const { board, host, sync } = syncFixture();
        board.remoteVersion = 2; board.elementsLoaded = false; board.elements = []; board.dirty = false;
        board.name = '离线改名的历史板';
        host.store.get.mockRejectedValueOnce(new RemoteError('离线')).mockResolvedValue({ board_key: board.id, version: 2, elements: [{ type: 'text', text: '原有笔迹' }], element_count: 1 });
        await sync.rename(board);
        expect(board.dirty).toBe(true);
        expect(host.store.upsert).not.toHaveBeenCalled();
        await sync.flushDirty();
        expect(JSON.parse(host.store.upsert.mock.calls[0][1].serialized)).toMatchObject({ name: '离线改名的历史板', elements: [{ type: 'text', text: '原有笔迹' }] });
        expect(board.dirty).toBe(false);
    });

    test('自动恢复与用户选中同一历史板共用下载，不让迟到响应覆盖已加载内容', async () => {
        const { board, host, sync } = syncFixture();
        board.remoteVersion = 2; board.elementsLoaded = false; board.elements = [];
        const pending = deferred();
        host.store.get.mockReturnValue(pending.promise);
        const first = sync.ensureLoaded(board);
        const second = sync.ensureLoaded(board);
        expect(host.store.get).toHaveBeenCalledTimes(1);
        pending.resolve({ board_key: board.id, version: 2, elements: [{ type: 'text', text: '历史笔迹' }] });
        expect((await first).elements[0].text).toBe('历史笔迹');
        board.elements.push({ type: 'text', text: '刚追加' });
        await second;
        expect(board.elements).toHaveLength(2);
        expect(sync.loading.size).toBe(0);
    });

    test('删除等待首次上传结束，再执行云端删除', async () => {
        const { board, host, sync } = syncFixture();
        const pending = deferred();
        host.store.upsert.mockReturnValueOnce(pending.promise);
        const firstSave = sync.flush(board);
        const removed = sync.remove(board);
        expect(host.store.remove).not.toHaveBeenCalled();
        pending.resolve({ version: 1 });
        await firstSave;
        expect(await removed).toBe(true);
        expect(host.store.remove).toHaveBeenCalledWith(board.id);
    });

    test('同步适配器同步抛错也清除在途标记，允许恢复重试', async () => {
        const { board, host, sync } = syncFixture();
        host.store.upsert.mockImplementationOnce(() => { throw new RemoteError('离线'); });
        expect(await sync.flush(board)).toBe(false);
        expect(sync.inFlight.size).toBe(0);
        expect(board.dirty).toBe(true);
        expect(await sync.flush(board)).toBe(true);
    });

    test('版本冲突保留包含在途新笔迹的本机副本及云端板', async () => {
        const { board, boards, host, sync } = syncFixture();
        const originalId = board.id;
        const pending = deferred();
        host.store.upsert.mockReturnValueOnce(pending.promise);
        const saved = sync.flush(board);
        board.elements.push({ type: 'text', text: '在途追加' });
        pending.reject(new RemoteError('冲突', { status: 409, payload: { board: { board_key: originalId, version: 8, elements: [{ type: 'text', text: '远端改动' }] } } }));
        expect(await saved).toBe(false);
        expect(board.id).not.toBe(originalId);
        expect(board.elements).toHaveLength(2);
        expect(board.dirty).toBe(true);
        expect(boards.find((item) => item.id === originalId).elements[0].text).toBe('远端改动');
    });
});
