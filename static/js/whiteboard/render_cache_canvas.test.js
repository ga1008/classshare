import { beforeEach, describe, expect, test } from 'vitest';
import { RenderCache } from './render_cache.js';

/** 记录所有调用的假 2D 上下文（不真的做变换，所以坐标参数保持传入时的值）。 */
function recordingCtx() {
    const calls = [];
    const store = { filter: '', __calls: calls };
    return new Proxy(store, {
        get(target, prop) {
            if (prop in target) return target[prop];
            return (...args) => { calls.push([prop, ...args]); };
        },
        set(target, prop, value) { target[prop] = value; return true; },
    });
}

function callsOf(ctx, name) {
    return ctx.__calls.filter((call) => call[0] === name).map((call) => call.slice(1));
}

let created;

beforeEach(() => {
    created = [];
    globalThis.document = {
        createElement() {
            const ctx = recordingCtx();
            const canvas = { width: 0, height: 0, getContext: () => ctx, __ctx: ctx };
            created.push(canvas);
            return canvas;
        },
    };
});

function stroke(x, y) {
    return { type: 'stroke', color: '#f00', size: 3, points: [{ x, y }, { x: x + 20, y }] };
}

function makeCache(cssWidth = 800, cssHeight = 600, dpr = 2) {
    const cache = new RenderCache();
    cache.resize(cssWidth, cssHeight, dpr);
    return cache;
}

describe('RenderCache: 尺寸与失效', () => {
    test('resize 按 dpr 分配后备存储并使缓存失效', () => {
        const cache = makeCache(800, 600, 2);
        expect(cache.canvas.width).toBe(1600);
        expect(cache.canvas.height).toBe(1200);
        expect(cache.valid).toBe(false);

        cache.rebuild([], { x: 0, y: 0, scale: 1 });
        expect(cache.valid).toBe(true);
        cache.resize(800, 600, 1);
        expect(cache.valid).toBe(false);
    });

    test('尺寸没变时 resize 不重新分配、不失效', () => {
        const cache = makeCache(800, 600, 2);
        cache.rebuild([], { x: 0, y: 0, scale: 1 });
        expect(cache.resize(800, 600, 2)).toBe(false);
        expect(cache.valid).toBe(true);
    });

    test('失效后 geometryFor 返回 null', () => {
        const cache = makeCache();
        cache.rebuild([], { x: 0, y: 0, scale: 1 });
        cache.invalidate();
        expect(cache.geometryFor({ x: 0, y: 0, scale: 1 })).toBe(null);
    });
});

describe('RenderCache: rebuild', () => {
    test('清空整屏、按视口变换绘制、并记下烘焙视口', () => {
        const cache = makeCache(800, 600, 2);
        const viewport = { x: 120, y: -30, scale: 1.5 };
        cache.rebuild([stroke(0, 0)], viewport);

        const ctx = cache.canvas.__ctx;
        expect(callsOf(ctx, 'setTransform')[0]).toEqual([2, 0, 0, 2, 0, 0]);
        expect(callsOf(ctx, 'clearRect')[0]).toEqual([0, 0, 800, 600]);
        expect(callsOf(ctx, 'translate')[0]).toEqual([120, -30]);
        expect(callsOf(ctx, 'scale')[0]).toEqual([1.5, 1.5]);
        expect(cache.baked).toEqual({ x: 120, y: -30, scale: 1.5 });
    });

    test('屏幕外的元素不参与烘焙', () => {
        const cache = makeCache(800, 600, 1);
        cache.rebuild([stroke(10, 10), stroke(50000, 10)], { x: 0, y: 0, scale: 1 });
        expect(callsOf(cache.canvas.__ctx, 'moveTo')).toEqual([[10, 10]]);
    });
});

describe('RenderCache: commit', () => {
    test('用烘焙视口而不是当前视口绘制新元素', () => {
        const cache = makeCache(800, 600, 1);
        const baked = { x: 0, y: 0, scale: 1 };
        cache.rebuild([], baked);
        cache.canvas.__ctx.__calls.length = 0;

        // 板已经平移过了，但缓存仍然是按 baked 烘焙的
        cache.commit(stroke(30, 40));
        expect(callsOf(cache.canvas.__ctx, 'translate')[0]).toEqual([0, 0]);
        expect(callsOf(cache.canvas.__ctx, 'scale')[0]).toEqual([1, 1]);
        expect(callsOf(cache.canvas.__ctx, 'moveTo')).toEqual([[30, 40]]);
    });

    test('缓存无效时不提交', () => {
        const cache = makeCache();
        expect(cache.commit(stroke(0, 0))).toBe(false);
    });
});

describe('RenderCache: repaintRegion', () => {
    test('只清空并重放与该区域相交的元素', () => {
        const cache = makeCache(800, 600, 1);
        cache.rebuild([], { x: 0, y: 0, scale: 1 });
        cache.canvas.__ctx.__calls.length = 0;

        const elements = [stroke(100, 100), stroke(600, 500)];
        cache.repaintRegion(elements, { x: 90, y: 90, width: 60, height: 40 });

        const ctx = cache.canvas.__ctx;
        const [clear] = callsOf(ctx, 'clearRect');
        // 世界矩形 → 烘焙屏幕坐标，并向外扩 1px
        expect(clear).toEqual([89, 89, 62, 42]);
        expect(callsOf(ctx, 'moveTo')).toEqual([[100, 100]]);
    });

    test('缩放过的烘焙视口下区域换算正确', () => {
        const cache = makeCache(800, 600, 1);
        cache.rebuild([], { x: 50, y: 20, scale: 2 });
        cache.canvas.__ctx.__calls.length = 0;

        cache.repaintRegion([], { x: 10, y: 5, width: 30, height: 15 });
        // 屏幕 x = 10*2+50 = 70，宽 = 30*2 = 60，再各扩 1px
        expect(callsOf(cache.canvas.__ctx, 'clearRect')[0]).toEqual([69, 29, 62, 32]);
    });

    test('缓存无效时不做局部重画', () => {
        const cache = makeCache();
        expect(cache.repaintRegion([stroke(0, 0)], { x: 0, y: 0, width: 10, height: 10 })).toBe(false);
    });
});

describe('RenderCache: blitTo', () => {
    test('视口未变时是 1:1 搬运', () => {
        const cache = makeCache(800, 600, 2);
        const viewport = { x: 10, y: 20, scale: 1 };
        cache.rebuild([], viewport);
        const target = recordingCtx();
        cache.blitTo(target, cache.geometryFor(viewport));

        expect(callsOf(target, 'setTransform')[0]).toEqual([2, 0, 0, 2, 0, 0]);
        expect(callsOf(target, 'drawImage')[0]).toEqual([cache.canvas, 0, 0, 800, 600]);
    });

    test('平移后按差量偏移搬运', () => {
        const cache = makeCache(800, 600, 1);
        cache.rebuild([], { x: 100, y: 50, scale: 1 });
        const target = recordingCtx();
        cache.blitTo(target, cache.geometryFor({ x: 40, y: 50, scale: 1 }));
        // 缓存整体左移 60px
        expect(callsOf(target, 'setTransform')[0]).toEqual([1, 0, 0, 1, -60, 0]);
    });

    test('缩放后变换里带上比例，且 dpr 一并折算', () => {
        const cache = makeCache(800, 600, 2);
        cache.rebuild([], { x: 0, y: 0, scale: 1 });
        const target = recordingCtx();
        cache.blitTo(target, cache.geometryFor({ x: 0, y: 0, scale: 1.25 }));
        expect(callsOf(target, 'setTransform')[0]).toEqual([2.5, 0, 0, 2.5, 0, 0]);
    });
});
