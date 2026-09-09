import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { TeacherWhiteboard } from './board.js';
import { createBoard, normalizeSettings } from './state.js';
import { LIMITS } from './constants.js';
import { saveLocalState } from './store_local.js';

vi.mock('../ui.js', () => ({ showToast: vi.fn(), escapeHtml: (value) => String(value) }));
vi.mock('./store_local.js', async (importOriginal) => ({ ...await importOriginal(), saveLocalState: vi.fn() }));

function deferred() {
    let resolve;
    const promise = new Promise((done) => { resolve = done; });
    return { promise, resolve };
}

function host() {
    const app = new TeacherWhiteboard({ userId: '1', userRole: 'teacher', materialId: '1', materialName: '学习文档' });
    app.activeBoard = createBoard('本机');
    app.viewport = { x: 0, y: 0, scale: 1 };
    app.state = { boards: [app.activeBoard], activeBoardId: app.activeBoard.id, settings: app.settings };
    app.isOpen = true;
    app.rootEl = { classList: { remove: vi.fn() }, setAttribute: vi.fn() };
    app.sync = { flush: vi.fn(), flushDirty: vi.fn(), stop: vi.fn() };
    for (const name of ['persistLocal', 'scheduleSave', 'scheduleRender', 'clearDraftCanvas', 'commitToCache',
        'updateUndoRedoButtons', 'updateClearButton', 'updateGridPosition', 'updateSyncStatus', 'setDrawingState',
        'setFabOpenState', 'notifyHostState', 'cancelBootstrap', 'cancelCacheSettle', 'drawScreenPolyline']) app[name] = vi.fn();
    return app;
}

beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal('window', {
        clearTimeout, setTimeout, cancelAnimationFrame: vi.fn(), requestAnimationFrame: vi.fn(() => 1),
    });
    vi.stubGlobal('document', { body: { style: { overflow: 'hidden' } }, createElement: () => ({ getContext: () => null }) });
    vi.mocked(saveLocalState).mockReset();
});

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

describe('whiteboard: 关闭与切板的数据边界', () => {
    test('背景支持 100% 并保留半透明设置，宿主隐藏 iframe 的条件可以达到', () => {
        expect(LIMITS.backgroundOpacity[1]).toBe(1);
        expect(normalizeSettings({ backgroundOpacity: 1 }).backgroundOpacity).toBe(1);
        expect(normalizeSettings({ backgroundOpacity: 0.95 }).backgroundOpacity).toBe(0.95);
    });
    test('关闭前把合批队列里的笔迹提交且保存，重开没有幽灵草稿', () => {
        const app = host();
        app.activePointer = 1;
        app.activeStroke = { id: 's', type: 'stroke', color: '#f00', size: 4, points: [{ x: 0, y: 0 }] };
        app.inputQueue = [{ x: 80, y: 20 }];
        app.close();
        expect(app.activeBoard.elements[0].points.at(-1)).toEqual({ x: 80, y: 20 });
        expect(app.activeBoard.dirty).toBe(true);
        expect(app.activeStroke).toBeNull();
        expect(app.persistLocal).toHaveBeenCalled();
        expect(app.sync.flushDirty).toHaveBeenCalled();
    });

    test('切板先把草稿提交到原板，再切换活动板', () => {
        const app = host();
        const original = app.activeBoard;
        const target = createBoard('目标');
        app.state.boards.push(target);
        app.activePointer = 1;
        app.activeStroke = { id: 's', type: 'stroke', color: '#f00', size: 4, points: [{ x: 0, y: 0 }] };
        app.inputQueue = [{ x: 80, y: 20 }];
        app.activateBoard(target);
        expect(original.elements).toHaveLength(1);
        expect(original.dirty).toBe(true);
        expect(app.activeBoard).toBe(target);
        expect(target.elements).toEqual([]);
    });

    test('慢加载的旧选择不能盖过新的切板选择', async () => {
        const app = host();
        const slow = { ...createBoard('慢'), elementsLoaded: false };
        const latest = createBoard('最后选择');
        const load = deferred();
        app.state.boards.push(slow, latest);
        app.sync.ensureLoaded = vi.fn(() => load.promise);
        const pending = app.selectBoard(slow.id);
        await app.selectBoard(latest.id);
        load.resolve();
        await pending;
        expect(app.activeBoard).toBe(latest);
    });

    test.each(['ink', 'pointer', 'close'])('首次云板加载后不能覆盖加载期间的本机行为：%s', async (action) => {
        const app = host();
        const original = app.activeBoard;
        const remote = { ...createBoard('云板'), elementsLoaded: false, elementCount: 1, remoteVersion: 1 };
        const load = deferred();
        app.state.boards.push(remote);
        app.sync.ensureLoaded = vi.fn(() => load.promise);
        const pending = app.adoptRemoteBoardIfFresh();
        if (action === 'ink') original.elements.push({ type: 'stroke', points: [{ x: 1, y: 2 }], size: 2 });
        if (action === 'pointer') app.activePointer = 1;
        if (action === 'close') app.close();
        load.resolve();
        await pending;
        expect(app.activeBoard).toBe(original);
        expect(app.state.boards).toContain(original);
    });

    test('缩放前提交队列中的世界坐标，后续绘制不会错位', () => {
        const app = host();
        app.activePointer = 1;
        app.activeStroke = { id: 's', type: 'stroke', color: '#f00', size: 4, points: [{ x: 0, y: 0 }] };
        app.inputQueue = [{ x: 80, y: 20 }];
        app.zoomBy(2, { x: 0, y: 0 });
        expect(app.activeBoard.elements[0].points.at(-1)).toEqual({ x: 80, y: 20 });
        expect(app.viewport.scale).toBe(2);
        expect(app.activePointer).toBeNull();
    });

    test('本地保存失败时保留非活动板的待写标记，成功重试后再清理', () => {
        const app = host();
        const other = createBoard('同步期间更改的板');
        app.state.boards.push(other);
        app.localDirtyBoardIds.add(other.id);
        vi.mocked(saveLocalState).mockReturnValueOnce({ ok: false }).mockReturnValueOnce({ ok: true });
        TeacherWhiteboard.prototype.persistLocal.call(app);
        expect(app.localDirtyBoardIds.has(other.id)).toBe(true);
        TeacherWhiteboard.prototype.persistLocal.call(app);
        expect(saveLocalState.mock.calls[1][2].boardIds).toContain(other.id);
        expect(app.localDirtyBoardIds.size).toBe(0);
    });
});
