import { describe, expect, test } from 'vitest';
import { DEFAULT_COLOR, LEGACY_DEFAULT_COLOR } from './constants.js';
import { isBoardEmpty, migrateLegacyState, nextBoardName, normalizeSettings, normalizeState } from './state.js';

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
