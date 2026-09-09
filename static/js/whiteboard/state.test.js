import { describe, expect, test } from 'vitest';
import { DEFAULT_COLOR, LEGACY_DEFAULT_COLOR } from './constants.js';
import {
    cloneElements, hasInkElements, isBoardEmpty, migrateLegacyState, nextBoardName,
    normalizeSettings, normalizeState, sanitizeElement,
} from './state.js';

const context = { materialName: '计算机网络' };

describe('state: emptiness', () => {
    test('board with only eraser elements is empty', () => {
        expect(isBoardEmpty({ elements: [{ type: 'eraser', points: [] }], elementsLoaded: true })).toBe(true);
        expect(isBoardEmpty({ elements: [{ type: 'stroke', points: [] }], elementsLoaded: true })).toBe(false);
    });

    test('remote stub uses server element count', () => {
        expect(isBoardEmpty({ elements: [], elementsLoaded: false, elementCount: 3 })).toBe(false);
        expect(isBoardEmpty({ elements: [], elementsLoaded: false, elementCount: 0 })).toBe(true);
    });
});

describe('state: naming', () => {
    test('next board name avoids collisions', () => {
        expect(nextBoardName('计算机网络', [{ name: '计算机网络 · 白板 2' }])).toBe('计算机网络 · 白板 3');
        expect(nextBoardName('计算机网络', [{ name: '计算机网络 · 白板 1' }, { name: '计算机网络 · 白板 3' }])).toBe('计算机网络 · 白板 4');
    });
});

describe('state: settings', () => {
    test('defaults are red and eraser fields are clamped', () => {
        const settings = normalizeSettings({ eraserSize: 999, eraserHardness: -1, eraserMode: 'nope', tool: 'eraser' });
        expect(settings.brushColor).toBe(DEFAULT_COLOR);
        expect(settings.eraserSize).toBe(120);
        expect(settings.eraserHardness).toBe(0);
        expect(settings.eraserMode).toBe('pixel');
        expect(settings.tool).toBe('eraser');
    });
});

describe('state: migration', () => {
    test('v1 legacy default colours become red, custom colours stay', () => {
        const migrated = migrateLegacyState({
            version: 1,
            boards: [{ id: 'b1', name: 'old', elements: [{ type: 'stroke', points: [{ x: 0, y: 0 }] }] }],
            settings: { brushColor: LEGACY_DEFAULT_COLOR, textColor: '#123456' },
        }, context);
        expect(migrated.version).toBe(2);
        expect(migrated.settings.brushColor).toBe(DEFAULT_COLOR);
        expect(migrated.settings.textColor).toBe('#123456');
        expect(migrated.boards[0]).toMatchObject({ id: 'b1', dirty: true, remoteVersion: 0, elementCount: 1 });
    });

    test('unknown element types are dropped on normalize', () => {
        const state = normalizeState({ boards: [{ id: 'x', elements: [{ type: 'alien' }, { type: 'text', text: 'hi' }] }] }, context);
        expect(state.boards[0].elements).toHaveLength(1);
        expect(state.activeBoardId).toBe('x');
    });
});

describe('state: 撤销快照（浅拷贝契约）', () => {
    test('快照是新数组但复用同一批元素对象', () => {
        const elements = [{ type: 'stroke', points: [] }, { type: 'text', text: 'hi' }];
        const snapshot = cloneElements(elements);
        expect(snapshot).not.toBe(elements);
        expect(snapshot[0]).toBe(elements[0]);
        expect(snapshot[1]).toBe(elements[1]);
    });

    test('对原数组增删不影响已取的快照', () => {
        const elements = [{ type: 'stroke', points: [] }];
        const snapshot = cloneElements(elements);
        elements.push({ type: 'text', text: 'later' });
        expect(snapshot).toHaveLength(1);
    });

    test('撤销 / 重做一个来回后回到原始元素序列', () => {
        const first = { type: 'stroke', points: [{ x: 0, y: 0 }] };
        const second = { type: 'stroke', points: [{ x: 9, y: 9 }] };
        let elements = [first];
        const undoStack = [];
        const redoStack = [];

        undoStack.push(cloneElements(elements));      // 落笔前快照
        elements = [...elements, second];

        redoStack.push(cloneElements(elements));      // undo
        elements = undoStack.pop();
        expect(elements).toEqual([first]);

        undoStack.push(cloneElements(elements));      // redo
        elements = redoStack.pop();
        expect(elements).toEqual([first, second]);
        expect(elements[1]).toBe(second);
    });

    test('非数组输入得到空数组', () => {
        expect(cloneElements(undefined)).toEqual([]);
        expect(cloneElements(null)).toEqual([]);
    });
});

describe('state: 元素字段白名单', () => {
    test('未知类型被丢弃', () => {
        expect(sanitizeElement({ type: 'alien' })).toBe(null);
        expect(sanitizeElement(null)).toBe(null);
        expect(sanitizeElement('nope')).toBe(null);
    });

    test('没有多余字段时原样返回（不额外分配）', () => {
        const element = { id: 's1', type: 'stroke', color: '#f00', size: 3, points: [], createdAt: 'now' };
        expect(sanitizeElement(element)).toBe(element);
    });

    test('运行时字段被剥离，已知字段完整保留', () => {
        const cleaned = sanitizeElement({
            id: 's1', type: 'stroke', color: '#f00', size: 3, points: [{ x: 1, y: 2 }], createdAt: 'now',
            _bbox: { x: 0 }, __simplified: [1, 2, 3],
        });
        expect(cleaned).toEqual({
            id: 's1', type: 'stroke', color: '#f00', size: 3, points: [{ x: 1, y: 2 }], createdAt: 'now',
        });
    });

    test('各类型各自的字段集互不串味', () => {
        const text = sanitizeElement({ type: 'text', text: 'hi', x: 1, y: 2, fontSize: 20, points: [1] });
        expect(text).toEqual({ type: 'text', text: 'hi', x: 1, y: 2, fontSize: 20 });
        const eraser = sanitizeElement({ type: 'eraser', size: 8, hardness: 0.5, points: [], color: '#000' });
        expect(eraser).toEqual({ type: 'eraser', size: 8, hardness: 0.5, points: [] });
        const shape = sanitizeElement({ type: 'shape', shape: 'circle', x1: 0, y1: 0, x2: 5, y2: 5, text: 'x' });
        expect(shape).toEqual({ type: 'shape', shape: 'circle', x1: 0, y1: 0, x2: 5, y2: 5 });
    });

    test('normalizeState 载入时就把脏字段清掉', () => {
        const state = normalizeState({
            boards: [{ id: 'b', elements: [{ type: 'stroke', points: [], _bbox: 1 }] }],
        }, context);
        expect(Object.keys(state.boards[0].elements[0])).toEqual(['type', 'points']);
    });
});

describe('state: hasInkElements', () => {
    test('只有橡皮不算有内容', () => {
        expect(hasInkElements([{ type: 'eraser' }])).toBe(false);
        expect(hasInkElements([{ type: 'eraser' }, { type: 'text' }])).toBe(true);
        expect(hasInkElements([])).toBe(false);
        expect(hasInkElements(undefined)).toBe(false);
    });
});
