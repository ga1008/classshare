import { describe, expect, test } from 'vitest';
import {
    boardBounds, elementBounds, getShapeBox, hitTestElement, pointToSegmentDistance, simplifyStroke,
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
