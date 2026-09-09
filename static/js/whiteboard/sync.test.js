import { describe, expect, test } from 'vitest';
import { REMOTE } from './constants.js';
import { byteLength, prepareElements } from './sync.js';

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
