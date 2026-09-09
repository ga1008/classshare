import { describe, expect, test } from 'vitest';
import {
    boardBounds, cachedElementBounds, elementBounds, getShapeBox, hitTestElement,
    pointOutsideBounds, pointToSegmentDistance, simplifyStroke,
} from './geometry.js';

const measure = (text, size) => text.length * size;

describe('geometry: bounds', () => {
    test('stroke bounds expand by half line width', () => {
        const bounds = elementBounds({ type: 'stroke', size: 4, points: [{ x: 10, y: 10 }, { x: 30, y: 20 }] });
        expect(bounds).toEqual({ x: 8, y: 8, width: 24, height: 14 });
    });

    test('square shape box is forced square from drag direction', () => {
        expect(getShapeBox({ shape: 'square', x1: 0, y1: 0, x2: 10, y2: -4 })).toEqual({ x: 0, y: -10, width: 10, height: 10 });
    });

    test('text bounds use measure callback and line count', () => {
        const bounds = elementBounds({ type: 'text', text: 'ab\ncdef', x: 5, y: 5, fontSize: 10 }, measure);
        expect(bounds.width).toBe(40);
        expect(bounds.height).toBeCloseTo(2 * 10 * 1.28);
    });

    test('eraser elements are excluded from board bounds; empty board is null', () => {
        expect(boardBounds([{ type: 'eraser', points: [{ x: 0, y: 0 }], size: 20 }])).toBeNull();
        const bounds = boardBounds([
            { type: 'stroke', size: 2, points: [{ x: 0, y: 0 }] },
            { type: 'shape', shape: 'rectangle', size: 2, x1: 100, y1: 50, x2: 120, y2: 70 },
            { type: 'eraser', points: [{ x: 900, y: 900 }], size: 40 },
        ]);
        expect(bounds).toEqual({ x: -1, y: -1, width: 122, height: 72 });
    });
});

describe('geometry: hit testing', () => {
    test('point to segment distance', () => {
        expect(pointToSegmentDistance({ x: 5, y: 3 }, { x: 0, y: 0 }, { x: 10, y: 0 })).toBe(3);
        expect(pointToSegmentDistance({ x: -4, y: 0 }, { x: 0, y: 0 }, { x: 10, y: 0 })).toBe(4);
    });

    test('stroke hit uses radius plus half size', () => {
        const stroke = { type: 'stroke', size: 4, points: [{ x: 0, y: 0 }, { x: 100, y: 0 }] };
        expect(hitTestElement(stroke, { x: 50, y: 6 }, 5)).toBe(true);
        expect(hitTestElement(stroke, { x: 50, y: 8 }, 5)).toBe(false);
    });

    test('circle hit follows the outline, not the interior', () => {
        const circle = { type: 'shape', shape: 'circle', size: 2, x1: 0, y1: 0, x2: 100, y2: 100 };
        expect(hitTestElement(circle, { x: 50, y: 2 }, 3)).toBe(true);
        expect(hitTestElement(circle, { x: 50, y: 50 }, 3)).toBe(false);
    });

    test('text hit uses its box; eraser elements never hit', () => {
        const text = { type: 'text', text: 'hello', x: 10, y: 10, fontSize: 10 };
        expect(hitTestElement(text, { x: 30, y: 15 }, 0, measure)).toBe(true);
        expect(hitTestElement(text, { x: 200, y: 15 }, 0, measure)).toBe(false);
        expect(hitTestElement({ type: 'eraser', size: 100, points: [{ x: 0, y: 0 }] }, { x: 0, y: 0 }, 50)).toBe(false);
    });
});

describe('geometry: simplify', () => {
    test('keeps endpoints and drops collinear points', () => {
        const points = [{ x: 0, y: 0 }, { x: 1, y: 0.01 }, { x: 2, y: 0 }, { x: 3, y: 0.02 }, { x: 4, y: 0 }];
        expect(simplifyStroke(points, 0.1)).toEqual([{ x: 0, y: 0 }, { x: 4, y: 0 }]);
    });

    test('keeps a real corner', () => {
        const points = [{ x: 0, y: 0 }, { x: 5, y: 0 }, { x: 10, y: 0 }, { x: 10, y: 5 }, { x: 10, y: 10 }];
        expect(simplifyStroke(points, 0.5)).toEqual([{ x: 0, y: 0 }, { x: 10, y: 0 }, { x: 10, y: 10 }]);
    });
});

describe('geometry: 包围盒缓存与 AABB 预筛', () => {
    test('cachedElementBounds 与 elementBounds 结果一致且复用同一对象', () => {
        const element = { type: 'stroke', size: 4, points: [{ x: 0, y: 0 }, { x: 30, y: 12 }] };
        const direct = elementBounds(element);
        const cached = cachedElementBounds(element);
        expect(cached).toEqual(direct);
        expect(cachedElementBounds(element)).toBe(cached);
    });

    test('AABB 预筛永远不会误杀真实命中（随机用例）', () => {
        let seed = 20260909;
        const random = () => {
            seed = (seed * 1103515245 + 12345) % 2147483648;
            return seed / 2147483648;
        };
        const elements = Array.from({ length: 60 }, () => {
            const x = random() * 400 - 200;
            const y = random() * 400 - 200;
            const kind = random();
            if (kind < 0.5) {
                return {
                    type: 'stroke',
                    size: 1 + random() * 8,
                    points: Array.from({ length: 5 }, (_, i) => ({ x: x + i * 12 * random(), y: y + i * 9 * random() })),
                };
            }
            if (kind < 0.8) {
                return { type: 'shape', shape: 'rectangle', size: 2, x1: x, y1: y, x2: x + 40, y2: y + 25 };
            }
            return { type: 'text', text: '命中测试', fontSize: 20, x, y };
        });

        for (let round = 0; round < 400; round += 1) {
            const point = { x: random() * 500 - 250, y: random() * 500 - 250 };
            const radius = 2 + random() * 20;
            for (const element of elements) {
                const truth = hitTestElement(element, point, radius);
                if (!truth) continue;
                // 预筛只允许排除「必然不命中」的元素。
                expect(pointOutsideBounds(cachedElementBounds(element), point, radius)).toBe(false);
            }
        }
    });

    test('明显在包围盒外的点会被预筛掉', () => {
        const element = { type: 'stroke', size: 2, points: [{ x: 0, y: 0 }, { x: 10, y: 0 }] };
        expect(pointOutsideBounds(cachedElementBounds(element), { x: 500, y: 500 }, 5)).toBe(true);
        expect(pointOutsideBounds(cachedElementBounds(element), { x: 5, y: 0 }, 5)).toBe(false);
    });

    test('没有包围盒的元素（橡皮）不参与预筛', () => {
        expect(pointOutsideBounds(null, { x: 0, y: 0 }, 1)).toBe(false);
    });
});
