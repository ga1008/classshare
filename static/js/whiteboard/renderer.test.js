import { describe, expect, test } from 'vitest';
import { boundsIntersectRect, cachedPaintBounds } from './geometry.js';
import { renderElements } from './renderer.js';

/** 记录 moveTo 的假 2D 上下文：每个 stroke/eraser 恰好一次 beginPath + moveTo(首点)。 */
function fakeCtx() {
    const moves = [];
    const target = { filter: '', __moves: moves };
    return new Proxy(target, {
        get(store, prop) {
            if (prop in store) return store[prop];
            if (prop === 'moveTo') return (x, y) => { moves.push({ x, y }); };
            return () => {};
        },
        set(store, prop, value) { store[prop] = value; return true; },
    });
}

function stroke(x, y) {
    return { type: 'stroke', color: '#f00', size: 3, points: [{ x, y }, { x: x + 20, y: y + 8 }] };
}

const viewport = { x: 0, y: 0, scale: 1 };

describe('renderer: 视口裁剪', () => {
    test('不给裁剪矩形时全部绘制，且保持数组顺序', () => {
        const elements = [stroke(0, 0), stroke(400, 0), stroke(800, 0)];
        const ctx = fakeCtx();
        renderElements(ctx, elements, viewport);
        expect(ctx.__moves).toEqual([{ x: 0, y: 0 }, { x: 400, y: 0 }, { x: 800, y: 0 }]);
    });

    test('只绘制与裁剪矩形相交的元素', () => {
        const elements = [stroke(0, 0), stroke(400, 0), stroke(800, 0)];
        const ctx = fakeCtx();
        renderElements(ctx, elements, viewport, { worldClip: { x: 380, y: -50, width: 100, height: 100 } });
        expect(ctx.__moves).toEqual([{ x: 400, y: 0 }]);
    });

    test('裁剪后顺序仍与原数组一致（橡皮的先后语义依赖它）', () => {
        const elements = [stroke(60, 0), stroke(5000, 0), stroke(10, 0), stroke(30, 0)];
        const ctx = fakeCtx();
        renderElements(ctx, elements, viewport, { worldClip: { x: -100, y: -100, width: 400, height: 400 } });
        expect(ctx.__moves).toEqual([{ x: 60, y: 0 }, { x: 10, y: 0 }, { x: 30, y: 0 }]);
    });

    test('橡皮元素同样参与绘制与裁剪', () => {
        const eraser = { type: 'eraser', size: 20, hardness: 1, points: [{ x: 100, y: 100 }, { x: 130, y: 100 }] };
        const inside = fakeCtx();
        renderElements(inside, [eraser], viewport, { worldClip: { x: 90, y: 90, width: 60, height: 40 } });
        expect(inside.__moves).toHaveLength(1);

        const outside = fakeCtx();
        renderElements(outside, [eraser], viewport, { worldClip: { x: -900, y: -900, width: 100, height: 100 } });
        expect(outside.__moves).toHaveLength(0);
    });

    test('裁剪判定与包围盒相交判定完全一致（随机用例）', () => {
        let seed = 424242;
        const random = () => {
            seed = (seed * 1103515245 + 12345) % 2147483648;
            return seed / 2147483648;
        };
        const elements = Array.from({ length: 40 }, () => stroke(random() * 1200 - 600, random() * 800 - 400));
        for (let round = 0; round < 60; round += 1) {
            const clip = {
                x: random() * 1200 - 600,
                y: random() * 800 - 400,
                width: 40 + random() * 400,
                height: 40 + random() * 300,
            };
            const expected = elements
                .filter((element) => boundsIntersectRect(cachedPaintBounds(element), clip))
                .map((element) => ({ x: element.points[0].x, y: element.points[0].y }));
            const ctx = fakeCtx();
            renderElements(ctx, elements, viewport, { worldClip: clip });
            expect(ctx.__moves).toEqual(expected);
        }
    });

    test('缩放不影响裁剪结果（裁剪矩形本来就是世界坐标）', () => {
        const elements = [stroke(0, 0), stroke(400, 0)];
        const clip = { x: -10, y: -10, width: 60, height: 60 };
        for (const scale of [0.35, 1, 2.6]) {
            const ctx = fakeCtx();
            renderElements(ctx, elements, { x: 137, y: -42, scale }, { worldClip: clip });
            expect(ctx.__moves).toEqual([{ x: 0, y: 0 }]);
        }
    });
});
