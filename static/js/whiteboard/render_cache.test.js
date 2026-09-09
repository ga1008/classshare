import { describe, expect, test } from 'vitest';
import { CACHE } from './constants.js';
import {
    blitGeometry, screenRectToWorld, shouldRebuild, subtractRect, viewportWorldRect, worldRectToScreen,
} from './render_cache.js';

const W = 1000;
const H = 600;

function area(rects) {
    return rects.reduce((sum, rect) => sum + rect.width * rect.height, 0);
}

function overlaps(a, b) {
    return a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height;
}

describe('render_cache: subtractRect', () => {
    test('完全覆盖时没有露出', () => {
        expect(subtractRect({ x: -10, y: -10, width: W + 20, height: H + 20 }, W, H)).toEqual([]);
    });

    test('完全不相交时露出整屏', () => {
        expect(subtractRect({ x: 5000, y: 0, width: 100, height: 100 }, W, H))
            .toEqual([{ x: 0, y: 0, width: W, height: H }]);
    });

    test('部分覆盖：露出面积 = 屏幕面积 - 交叠面积，且各条带互不重叠', () => {
        const covered = { x: -200, y: 100, width: W, height: H };
        const rects = subtractRect(covered, W, H);
        const overlapWidth = Math.min(W, covered.x + covered.width) - Math.max(0, covered.x);
        const overlapHeight = Math.min(H, covered.y + covered.height) - Math.max(0, covered.y);
        expect(area(rects)).toBeCloseTo(W * H - overlapWidth * overlapHeight, 6);
        for (let i = 0; i < rects.length; i += 1) {
            for (let j = i + 1; j < rects.length; j += 1) {
                expect(overlaps(rects[i], rects[j])).toBe(false);
            }
        }
    });

    test('露出条带全部落在屏幕内', () => {
        for (const covered of [
            { x: -300, y: -200, width: W, height: H },
            { x: 120, y: 80, width: W, height: H },
            { x: 0, y: -50, width: W, height: H },
        ]) {
            for (const rect of subtractRect(covered, W, H)) {
                expect(rect.x >= 0 && rect.y >= 0).toBe(true);
                expect(rect.x + rect.width <= W).toBe(true);
                expect(rect.y + rect.height <= H).toBe(true);
                expect(rect.width > 0 && rect.height > 0).toBe(true);
            }
        }
    });
});

describe('render_cache: blitGeometry', () => {
    const baked = { x: 300, y: 200, scale: 1 };

    test('视口没变：完全命中，无需补画', () => {
        const geometry = blitGeometry(baked, { ...baked }, W, H);
        expect(geometry.exact).toBe(true);
        expect(geometry.coverage).toBe(1);
        expect(geometry.exposed).toEqual([]);
    });

    test('平移后缓存整体位移，露出的是同宽的一条', () => {
        const geometry = blitGeometry(baked, { x: 300 - 120, y: 200, scale: 1 }, W, H);
        expect(geometry.scale).toBe(1);
        expect(geometry.covered.x).toBeCloseTo(-120, 6);
        expect(geometry.exact).toBe(false);
        expect(area(geometry.exposed)).toBeCloseTo(120 * H, 6);
    });

    test('缩放后覆盖范围按比例变化', () => {
        const geometry = blitGeometry(baked, { x: 300, y: 200, scale: 2 }, W, H);
        expect(geometry.scale).toBe(2);
        expect(geometry.covered.width).toBeCloseTo(W * 2, 6);
        // 放大之后缓存反而盖得更满
        expect(geometry.coverage).toBeGreaterThan(0.9);
    });

    test('缩小到看见缓存之外的区域时覆盖率下降', () => {
        const geometry = blitGeometry(baked, { x: 300, y: 200, scale: 0.5 }, W, H);
        expect(geometry.coverage).toBeCloseTo(0.25, 6);
    });

    test('世界坐标不变：缓存里的世界点仍落在同一个屏幕位置', () => {
        const current = { x: 120, y: -40, scale: 1.35 };
        const geometry = blitGeometry(baked, current, W, H);
        const worldPoint = { x: 42, y: -17 };
        // 该世界点在缓存图上的 CSS 位置
        const inCache = { x: worldPoint.x * baked.scale + baked.x, y: worldPoint.y * baked.scale + baked.y };
        // 经过 blit 变换后应落到它在当前视口下的屏幕位置
        expect(inCache.x * geometry.scale + geometry.covered.x)
            .toBeCloseTo(worldPoint.x * current.scale + current.x, 6);
        expect(inCache.y * geometry.scale + geometry.covered.y)
            .toBeCloseTo(worldPoint.y * current.scale + current.y, 6);
    });
});

describe('render_cache: shouldRebuild', () => {
    const baked = { x: 0, y: 0, scale: 1 };

    test('小幅平移继续走 blit', () => {
        expect(shouldRebuild(blitGeometry(baked, { x: 40, y: 20, scale: 1 }, W, H))).toBe(false);
    });

    test('露出过多时改为重建', () => {
        expect(shouldRebuild(blitGeometry(baked, { x: W * 0.6, y: 0, scale: 1 }, W, H))).toBe(true);
    });

    test('缩放偏离过大时改为重建（位图会糊）', () => {
        const tooBig = CACHE.MAX_BLIT_SCALE + 0.2;
        expect(shouldRebuild(blitGeometry(baked, { x: 0, y: 0, scale: tooBig }, W, H))).toBe(true);
        expect(shouldRebuild(blitGeometry(baked, { x: 0, y: 0, scale: 1 / tooBig }, W, H))).toBe(true);
    });
});

describe('render_cache: 坐标换算', () => {
    test('屏幕矩形与世界矩形互为逆变换', () => {
        const viewport = { x: -220, y: 90, scale: 1.75 };
        const screen = { x: 12, y: 34, width: 200, height: 120 };
        const roundTrip = worldRectToScreen(screenRectToWorld(screen, viewport), viewport);
        expect(roundTrip.x).toBeCloseTo(screen.x, 6);
        expect(roundTrip.y).toBeCloseTo(screen.y, 6);
        expect(roundTrip.width).toBeCloseTo(screen.width, 6);
        expect(roundTrip.height).toBeCloseTo(screen.height, 6);
    });

    test('整屏世界矩形覆盖四个角', () => {
        const viewport = { x: 100, y: 50, scale: 2 };
        const rect = viewportWorldRect(viewport, W, H);
        expect(rect.x).toBeCloseTo(-50, 6);
        expect(rect.y).toBeCloseTo(-25, 6);
        expect(rect.width).toBeCloseTo(W / 2, 6);
        expect(rect.height).toBeCloseTo(H / 2, 6);
    });
});
