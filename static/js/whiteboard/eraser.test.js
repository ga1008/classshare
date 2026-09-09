import { afterEach, describe, expect, test } from 'vitest';
import { drawEraser, eraserSpriteKey, MAX_ERASER_STAMPS, stampPoints } from './renderer.js';

function recordingCtx() {
    const calls = [];
    const sets = [];
    const store = { __calls: calls, __sets: sets };
    return new Proxy(store, {
        get(target, prop) {
            if (prop in target) return target[prop];
            return (...args) => { calls.push([prop, ...args]); };
        },
        set(target, prop, value) { sets.push(prop); target[prop] = value; return true; },
    });
}

const countOf = (ctx, name) => ctx.__calls.filter((call) => call[0] === name).length;

function installFakeDocument() {
    globalThis.document = {
        createElement() {
            const ctx = recordingCtx();
            return {
                width: 0,
                height: 0,
                getContext: () => new Proxy(ctx, {
                    get(target, prop) {
                        if (prop === 'createRadialGradient') return () => ({ addColorStop() {} });
                        return Reflect.get(target, prop);
                    },
                }),
            };
        },
    };
}

const originalDocument = globalThis.document;

afterEach(() => {
    globalThis.document = originalDocument;
});

describe('renderer: stampPoints', () => {
    test('单点只盖一次', () => {
        expect(stampPoints([{ x: 5, y: 5 }], 4)).toEqual([{ x: 5, y: 5 }]);
    });

    test('直线按间距取点，首尾都在', () => {
        const stamps = stampPoints([{ x: 0, y: 0 }, { x: 100, y: 0 }], 10);
        expect(stamps[0]).toEqual({ x: 0, y: 0 });
        expect(stamps[stamps.length - 1]).toEqual({ x: 100, y: 0 });
        expect(stamps).toHaveLength(11);
    });

    test('相邻盖章点的间隔不超过间距（不会漏擦）', () => {
        const points = [{ x: 0, y: 0 }, { x: 30, y: 40 }, { x: 30, y: 100 }, { x: 0, y: 100 }];
        const spacing = 7;
        const stamps = stampPoints(points, spacing);
        for (let index = 1; index < stamps.length; index += 1) {
            const gap = Math.hypot(stamps[index].x - stamps[index - 1].x, stamps[index].y - stamps[index - 1].y);
            expect(gap <= spacing + 1e-6).toBe(true);
        }
    });

    test('间距大于整条路径时退化成首尾两点', () => {
        expect(stampPoints([{ x: 0, y: 0 }, { x: 3, y: 4 }], 50))
            .toEqual([{ x: 0, y: 0 }, { x: 3, y: 4 }]);
    });

    test('重复点不会造成死循环', () => {
        const stamps = stampPoints([{ x: 1, y: 1 }, { x: 1, y: 1 }, { x: 1, y: 1 }], 5);
        expect(stamps).toHaveLength(1);
    });
});

describe('renderer: eraserSpriteKey', () => {
    test('相近的尺寸/硬度量化到同一张贴图', () => {
        expect(eraserSpriteKey(28.1, 0.51)).toBe(eraserSpriteKey(28.2, 0.49));
        expect(eraserSpriteKey(28, 0.5)).not.toBe(eraserSpriteKey(40, 0.5));
    });
});

describe('renderer: drawEraser', () => {
    test('硬边走单次描边，不用贴图', () => {
        installFakeDocument();
        const ctx = recordingCtx();
        drawEraser(ctx, { type: 'eraser', size: 20, hardness: 1, points: [{ x: 0, y: 0 }, { x: 50, y: 0 }] });
        expect(countOf(ctx, 'stroke')).toBe(1);
        expect(countOf(ctx, 'drawImage')).toBe(0);
        expect(ctx.globalCompositeOperation).toBe('destination-out');
    });

    test('硬边单点画圆', () => {
        const ctx = recordingCtx();
        drawEraser(ctx, { type: 'eraser', size: 20, hardness: 1, points: [{ x: 3, y: 4 }] });
        expect(countOf(ctx, 'arc')).toBe(1);
        expect(countOf(ctx, 'fill')).toBe(1);
    });

    test('软边改用贴图盖章，盖章次数与 stampPoints 一致', () => {
        installFakeDocument();
        const points = [{ x: 0, y: 0 }, { x: 100, y: 0 }];
        const size = 20;
        const ctx = recordingCtx();
        drawEraser(ctx, { type: 'eraser', size, hardness: 0.4, points });
        expect(countOf(ctx, 'drawImage')).toBe(stampPoints(points, Math.max(size * 0.18, 0.5)).length);
        expect(countOf(ctx, 'stroke')).toBe(0);
    });

    test('拿不到 document 时退回三层描边，且不再碰 ctx.filter', () => {
        delete globalThis.document;
        const ctx = recordingCtx();
        drawEraser(ctx, { type: 'eraser', size: 20, hardness: 0.3, points: [{ x: 0, y: 0 }, { x: 40, y: 0 }] });
        expect(countOf(ctx, 'stroke')).toBe(3);
        // 关键：不再走 canvas filter 的慢路径
        expect(ctx.__sets.includes('filter')).toBe(false);
    });

    test('空点集直接返回', () => {
        const ctx = recordingCtx();
        drawEraser(ctx, { type: 'eraser', size: 20, hardness: 0.5, points: [] });
        expect(ctx.__calls).toHaveLength(0);
        expect(ctx.__sets).toHaveLength(0);
    });
});

describe('renderer: 盖章数量兜底', () => {
    test('极细橡皮 + 极长路径不会退化成几万次盖章', () => {
        const stamps = stampPoints([{ x: 0, y: 0 }, { x: 200000, y: 0 }], 0.5);
        expect(stamps.length <= MAX_ERASER_STAMPS + 2).toBe(true);
        // 仍然覆盖完整路径
        expect(stamps[0]).toEqual({ x: 0, y: 0 });
        expect(stamps.at(-1)).toEqual({ x: 200000, y: 0 });
    });
});
