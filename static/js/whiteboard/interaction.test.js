import { describe, expect, test, vi } from 'vitest';
import { INPUT } from './constants.js';
import { interactionMixin } from './interaction.js';

/** 最小宿主：只提供 mixin 真正用到的字段与协作方法。 */
function makeHost(overrides = {}) {
    const host = {
        settings: { brushSize: 4, eraserSize: 20, eraserMode: 'stroke' },
        viewport: { x: 0, y: 0, scale: 1 },
        activeBoard: { elements: [] },
        measureWidth: (text, size) => text.length * size * 0.6,
        eraseSession: { pushed: false },
        undoSnapshots: 0,
        renders: 0,
        polylines: [],
        committed: [],
        repaintedRegions: [],
        pushUndoSnapshot() { this.undoSnapshots += 1; },
        scheduleRender() { this.renders += 1; },
        drawScreenPolyline(points) { this.polylines.push(points.slice()); },
        drawEraserSegmentLive(points) { this.polylines.push(points.slice()); },
        commitToCache(element) { this.committed.push(element); },
        repaintCacheRegion(rect) { this.repaintedRegions.push(rect); },
        ...overrides,
    };
    // 只补 host 上没有的方法，保留上面的绘制/撤销桩。
    for (const [name, fn] of Object.entries(interactionMixin)) {
        if (typeof fn === 'function' && !(name in host)) host[name] = fn;
    }
    return host;
}

function strokeAt(x, y, id) {
    return { id, type: 'stroke', color: '#f00', size: 2, points: [{ x, y }, { x: x + 10, y }] };
}

describe('interaction: 采点与抽稀参数', () => {
    test('最小采点间距随笔宽自适应并被夹在上下限内', () => {
        const thin = makeHost({ settings: { brushSize: 1, eraserSize: 20, eraserMode: 'stroke' } });
        const thick = makeHost({ settings: { brushSize: 32, eraserSize: 20, eraserMode: 'stroke' } });
        expect(thin.minPointDistance()).toBe(INPUT.MIN_POINT_DISTANCE);
        expect(thick.minPointDistance()).toBe(INPUT.MAX_POINT_DISTANCE);
        const mid = makeHost({ settings: { brushSize: 8, eraserSize: 20, eraserMode: 'stroke' } });
        expect(mid.minPointDistance()).toBeCloseTo(2, 6);
    });

    test('抽稀容差按当前缩放折算到世界坐标', () => {
        const host = makeHost();
        expect(host.commitTolerance()).toBeCloseTo(INPUT.COMMIT_SIMPLIFY_TOLERANCE, 6);
        host.viewport.scale = 2;
        expect(host.commitTolerance()).toBeCloseTo(INPUT.COMMIT_SIMPLIFY_TOLERANCE / 2, 6);
    });
});

describe('interaction: 笔画合批', () => {
    test('低于阈值的点被丢弃，本帧只画一条折线', () => {
        const host = makeHost();
        host.activeStroke = { color: '#f00', points: [{ x: 0, y: 0 }] };
        // 阈值 = max(1.2, 4 * 0.25) = 1.2
        host.flushStrokePoints([{ x: 0.5, y: 0 }, { x: 6, y: 0 }, { x: 6.4, y: 0 }, { x: 20, y: 0 }]);
        expect(host.activeStroke.points).toHaveLength(3);
        expect(host.polylines).toHaveLength(1);
        expect(host.polylines[0]).toHaveLength(3);
    });

    test('起点参与折线，保证与上一帧接得上', () => {
        const host = makeHost();
        host.activeStroke = { color: '#f00', points: [{ x: 100, y: 100 }] };
        host.flushStrokePoints([{ x: 140, y: 100 }]);
        expect(host.polylines[0][0]).toEqual({ x: 100, y: 100 });
    });
});

describe('interaction: 整笔橡皮', () => {
    test('一帧内的多个点只做一次元素遍历，命中的整笔被删除', () => {
        const host = makeHost();
        host.activeBoard.elements = [strokeAt(0, 0, 'a'), strokeAt(0, 500, 'b'), strokeAt(0, 900, 'c')];
        host.eraseStrokesAtPoints([{ x: 5, y: 0 }, { x: 5, y: 900 }]);
        expect(host.activeBoard.elements.map((element) => element.id)).toEqual(['b']);
        expect(host.undoSnapshots).toBe(1);
        expect(host.renders).toBe(1);
    });

    test('一次都没命中时不落撤销点、不请求重绘', () => {
        const host = makeHost();
        host.activeBoard.elements = [strokeAt(0, 0, 'a')];
        host.eraseStrokesAtPoints([{ x: 5, y: 4000 }]);
        expect(host.activeBoard.elements).toHaveLength(1);
        expect(host.undoSnapshots).toBe(0);
        expect(host.renders).toBe(0);
    });

    test('同一次擦除会话只落一个撤销点', () => {
        const host = makeHost();
        host.activeBoard.elements = [strokeAt(0, 0, 'a'), strokeAt(0, 500, 'b')];
        host.eraseStrokesAtPoints([{ x: 5, y: 0 }]);
        host.eraseStrokesAtPoints([{ x: 5, y: 500 }]);
        expect(host.activeBoard.elements).toHaveLength(0);
        expect(host.undoSnapshots).toBe(1);
    });

    test('橡皮元素本身永不被整笔擦命中', () => {
        const host = makeHost();
        host.activeBoard.elements = [{ id: 'e', type: 'eraser', size: 10, points: [{ x: 0, y: 0 }] }];
        host.eraseStrokesAtPoints([{ x: 0, y: 0 }]);
        expect(host.activeBoard.elements).toHaveLength(1);
    });

    test('橡皮半径按缩放折算：放大后同样的屏幕半径覆盖更小的世界范围', () => {
        const near = makeHost();
        near.viewport.scale = 4;
        near.activeBoard.elements = [strokeAt(0, 8, 'a')];
        near.eraseStrokesAtPoints([{ x: 0, y: 0 }]);
        expect(near.activeBoard.elements).toHaveLength(1);

        const far = makeHost();
        far.activeBoard.elements = [strokeAt(0, 8, 'a')];
        far.eraseStrokesAtPoints([{ x: 0, y: 0 }]);
        expect(far.activeBoard.elements).toHaveLength(0);
    });
});

describe('interaction: 缓存脏区', () => {
    test('删掉的元素会给出一个覆盖它们的世界矩形', () => {
        const host = makeHost();
        host.activeBoard.elements = [strokeAt(0, 0, 'a'), strokeAt(0, 500, 'b'), strokeAt(0, 900, 'c')];
        host.eraseStrokesAtPoints([{ x: 5, y: 0 }, { x: 5, y: 900 }]);
        expect(host.repaintedRegions).toHaveLength(1);
        const region = host.repaintedRegions[0];
        // a 在 y≈0、c 在 y≈900，脏区必须同时包住两者
        expect(region.y <= 0).toBe(true);
        expect(region.y + region.height >= 900).toBe(true);
        expect(region.x <= 0).toBe(true);
        expect(region.x + region.width >= 10).toBe(true);
    });

    test('没命中就不产生脏区', () => {
        const host = makeHost();
        host.activeBoard.elements = [strokeAt(0, 0, 'a')];
        host.eraseStrokesAtPoints([{ x: 5, y: 4000 }]);
        expect(host.repaintedRegions).toHaveLength(0);
    });
});

describe('interaction: 指针生命周期', () => {
    function drawingHost(overrides = {}) {
        return makeHost({
            isOpen: true, activePointer: 0, currentTool: 'brush', inputQueue: [],
            activeStroke: { id: 'drawing', type: 'stroke', color: '#f00', size: 4, points: [{ x: 0, y: 0 }] },
            eraseSession: null, stageEl: null,
            getStageRect: () => ({ left: 0, top: 0 }),
            scheduleInputFlush: vi.fn(), cancelInputFlush: vi.fn(),
            setDrawingState: vi.fn(), clearDraftCanvas: vi.fn(), markDirty: vi.fn(),
            ...overrides,
        });
    }

    test('pointerup 空 coalesced 数组仍提交尾点，pointerId 0 有效', () => {
        const host = drawingHost();
        host.handleStagePointerUp({
            pointerId: 0, clientX: 80, clientY: 20, getCoalescedEvents: () => [], preventDefault: vi.fn(),
        });
        expect(host.activeBoard.elements[0].points.at(-1)).toEqual({ x: 80, y: 20 });
        expect(host.markDirty).toHaveBeenCalledTimes(1);
        expect(host.activePointer).toBeNull();
    });

    test('批量事件尚未包含最新事件位置时保留其尾点', () => {
        const host = drawingHost();
        host.handleStagePointerMove({
            pointerId: 0, clientX: 80, clientY: 20,
            getCoalescedEvents: () => [{ clientX: 40, clientY: 10 }], preventDefault: vi.fn(),
        });
        expect(host.inputQueue).toEqual([{ x: 40, y: 10 }, { x: 80, y: 20 }]);
    });

    test('第二触点不会覆盖首个触点的笔迹和未处理队列', () => {
        const host = drawingHost({ inputQueue: [{ x: 80, y: 20 }] });
        const stroke = host.activeStroke;
        host.handleStagePointerDown({ pointerId: 2, button: 0 });
        expect(host.activeStroke).toBe(stroke);
        expect(host.activePointer).toBe(0);
        expect(host.inputQueue).toEqual([{ x: 80, y: 20 }]);
    });

    test('取消或丢失 capture 提交已接收的点，不生成重复笔画', () => {
        const host = drawingHost({ inputQueue: [{ x: 80, y: 20 }] });
        host.handleStagePointerCancel({ pointerId: 0 });
        host.handleStagePointerCancel({ pointerId: 0 });
        expect(host.activeBoard.elements).toHaveLength(1);
        expect(host.activeBoard.elements[0].points.at(-1)).toEqual({ x: 80, y: 20 });
        expect(host.markDirty).toHaveBeenCalledTimes(1);
    });

    test('整笔擦在取消前的真实删除会被标记保存', () => {
        const host = drawingHost({ activeStroke: null, eraseSession: { pushed: false } });
        host.activeBoard.elements = [strokeAt(0, 0, 'erase-me')];
        host.eraseStrokesAtPoints([{ x: 5, y: 0 }]);
        host.handleStagePointerCancel({ pointerId: 0 });
        expect(host.activeBoard.elements).toEqual([]);
        expect(host.markDirty).toHaveBeenCalledTimes(1);
    });

    test('云端板体未加载时不能向占位板绘制', () => {
        const host = drawingHost({ activePointer: null, activeBoard: { elements: [], elementsLoaded: false } });
        host.handleStagePointerDown({ pointerId: 2, button: 0 });
        expect(host.activePointer).toBeNull();
    });
});
