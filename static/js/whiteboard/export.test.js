import { describe, expect, test } from 'vitest';
import { EXPORT } from './constants.js';
import { buildFileName, defaultExportSize, exportFrame, fitUnderLimit, lockAspect, sanitizeFileStem } from './export.js';

describe('export: frame', () => {
    test('empty board has no frame', () => {
        expect(exportFrame([])).toBeNull();
    });

    test('frame pads bounds by at least 24 world px', () => {
        const frame = exportFrame([{ type: 'stroke', size: 2, points: [{ x: 0, y: 0 }, { x: 100, y: 50 }] }]);
        expect(frame.x).toBe(-25);
        expect(frame.width).toBe(102 + 48);
    });
});

describe('export: aspect lock', () => {
    test('width drives height and vice versa', () => {
        expect(lockAspect({ width: 512 }, 2)).toEqual({ width: 512, height: 256 });
        expect(lockAspect({ height: 300 }, 1.5)).toEqual({ width: 450, height: 300 });
    });

    test('default size puts the long edge at 512', () => {
        expect(defaultExportSize(2)).toEqual({ width: 512, height: 256 });
        expect(defaultExportSize(0.5)).toEqual({ width: 256, height: 512 });
    });

    test('respects edge and pixel caps', () => {
        const capped = lockAspect({ width: 99999 }, 1);
        expect(capped.width).toBeLessThanOrEqual(EXPORT.MAX_EDGE);
        expect(capped.width * capped.height).toBeLessThanOrEqual(EXPORT.MAX_PIXELS);
        expect(lockAspect({ width: 1 }, 1).width).toBe(EXPORT.MIN_EDGE);
    });
});

describe('export: file names', () => {
    test('strips reserved characters and keeps CJK', () => {
        expect(sanitizeFileStem('计算机网络: a/b?c*d|e"f<g>h')).toBe('计算机网络-abcdefgh');
    });

    test('builds a stamped default name', () => {
        const name = buildFileName('计算机网络', '白板 1', new Date(2026, 8, 2, 14, 5));
        expect(name).toBe('计算机网络-白板-1-20260902-1405');
    });
});

describe('export: size fitting', () => {
    test('shrinks until under the limit', async () => {
        const render = async ({ width, height }) => ({ size: width * height });
        const result = await fitUnderLimit(render, { width: 4000, height: 2000 }, 2, 1_000_000, 5);
        expect(result.blob.size).toBeLessThanOrEqual(1_000_000);
        expect(result.rounds).toBeGreaterThan(0);
        expect(result.width / result.height).toBeCloseTo(2, 1);
    });

    test('returns immediately when already under limit', async () => {
        const render = async () => ({ size: 10 });
        const result = await fitUnderLimit(render, { width: 512, height: 256 }, 2, 1000);
        expect(result).toMatchObject({ width: 512, height: 256, rounds: 0 });
    });
});
