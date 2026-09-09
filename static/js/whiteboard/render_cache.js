/**
 * 提交层位图缓存。
 *
 * 关键前提：白板的元素模型是**追加式**的 —— 新元素永远画在最上面，像素橡皮的
 * `destination-out` 也只影响它之前的内容。所以「已提交的全部元素」可以安全地烘焙成一张
 * 位图，新元素增量叠加即可，不必每帧从头重放。
 * 一旦将来引入「选中 / 移动 / 编辑已有元素」，这个前提就不成立了，必须同时补上失效逻辑。
 *
 * 缓存按**某个视口**烘焙（`baked`）。视口变化时不立刻重建：
 *   1. 先把缓存按两视口之间的差量变换 blit 过去（纯位图搬运）；
 *   2. 屏幕上缓存没盖住的「露出条带」用裁剪后的实时渲染补画；
 *   3. 手势停下来之后再回正重建一张清晰的缓存。
 * 这样平移/缩放的每帧代价只与露出面积相关，而不是与总元素数相关。
 *
 * 纯几何部分（blitGeometry / subtractRect / screenRectToWorld / viewportWorldRect）
 * 不碰 canvas，可以在 node 下单测。
 */
import { CACHE } from './constants.js';
import { renderElements } from './renderer.js';

function clampRange(value, min, max) {
    return Math.min(max, Math.max(min, value));
}

/** 屏幕矩形（CSS 像素）→ 世界矩形。 */
export function screenRectToWorld(rect, viewport) {
    const scale = viewport.scale || 1;
    return {
        x: (rect.x - viewport.x) / scale,
        y: (rect.y - viewport.y) / scale,
        width: rect.width / scale,
        height: rect.height / scale,
    };
}

/** 当前视口对应的世界矩形（整屏）。 */
export function viewportWorldRect(viewport, cssWidth, cssHeight) {
    return screenRectToWorld({ x: 0, y: 0, width: cssWidth, height: cssHeight }, viewport);
}

/** 世界矩形 → 屏幕矩形（CSS 像素）。 */
export function worldRectToScreen(rect, viewport) {
    const scale = viewport.scale || 1;
    return {
        x: rect.x * scale + viewport.x,
        y: rect.y * scale + viewport.y,
        width: rect.width * scale,
        height: rect.height * scale,
    };
}

/**
 * 整屏 [0,W]×[0,H] 减去 `covered` 之后剩下的矩形（最多 4 条带，互不重叠）。
 * covered 完全不与屏幕相交时返回整屏。
 */
export function subtractRect(covered, cssWidth, cssHeight) {
    const left = clampRange(covered.x, 0, cssWidth);
    const right = clampRange(covered.x + covered.width, 0, cssWidth);
    const top = clampRange(covered.y, 0, cssHeight);
    const bottom = clampRange(covered.y + covered.height, 0, cssHeight);
    if (right <= left || bottom <= top) {
        return [{ x: 0, y: 0, width: cssWidth, height: cssHeight }];
    }
    const rects = [];
    if (left > 0) rects.push({ x: 0, y: 0, width: left, height: cssHeight });
    if (right < cssWidth) rects.push({ x: right, y: 0, width: cssWidth - right, height: cssHeight });
    if (top > 0) rects.push({ x: left, y: 0, width: right - left, height: top });
    if (bottom < cssHeight) rects.push({ x: left, y: bottom, width: right - left, height: cssHeight - bottom });
    return rects;
}

/**
 * 缓存（烘焙于 baked 视口）在 current 视口下的落点与覆盖情况。
 * 缓存里的 CSS 点 c 映射到当前屏幕：`scale * c + offset`。
 */
export function blitGeometry(baked, current, cssWidth, cssHeight) {
    const scale = (current.scale || 1) / (baked.scale || 1);
    const covered = {
        x: current.x - baked.x * scale,
        y: current.y - baked.y * scale,
        width: cssWidth * scale,
        height: cssHeight * scale,
    };
    const exposed = subtractRect(covered, cssWidth, cssHeight);
    const exposedArea = exposed.reduce((sum, rect) => sum + rect.width * rect.height, 0);
    const total = Math.max(1, cssWidth * cssHeight);
    return {
        scale,
        covered,
        exposed,
        coverage: Math.max(0, 1 - exposedArea / total),
        exact: scale === 1 && covered.x === 0 && covered.y === 0,
    };
}

/** 该不该放弃 blit 直接重建：露出太多，或缩放偏离太远（放大后的位图会糊）。 */
export function shouldRebuild(geometry) {
    if (geometry.coverage < CACHE.MIN_COVERAGE) return true;
    return geometry.scale > CACHE.MAX_BLIT_SCALE || geometry.scale < 1 / CACHE.MAX_BLIT_SCALE;
}

export class RenderCache {
    constructor() {
        this.canvas = null;
        this.ctx = null;
        this.cssWidth = 0;
        this.cssHeight = 0;
        this.dpr = 1;
        /** 烘焙时的视口；null 表示缓存内容无效。 */
        this.baked = null;
    }

    get valid() {
        return Boolean(this.baked && this.ctx);
    }

    invalidate() {
        this.baked = null;
    }

    /** 尺寸变化会重新分配后备存储并清空内容，因此必然连带失效。 */
    resize(cssWidth, cssHeight, dpr) {
        if (this.canvas && this.cssWidth === cssWidth && this.cssHeight === cssHeight && this.dpr === dpr) return false;
        if (!this.canvas) {
            if (typeof document === 'undefined') return false;
            this.canvas = document.createElement('canvas');
            this.ctx = this.canvas.getContext('2d');
        }
        this.cssWidth = cssWidth;
        this.cssHeight = cssHeight;
        this.dpr = dpr;
        this.canvas.width = Math.max(1, Math.round(cssWidth * dpr));
        this.canvas.height = Math.max(1, Math.round(cssHeight * dpr));
        this.invalidate();
        return true;
    }

    /** 把缓存坐标系设成「与主画布一致」：设备像素 → CSS 像素。 */
    resetTransform() {
        this.ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    }

    rebuild(elements, viewport, measureWidth) {
        if (!this.ctx) return false;
        this.resetTransform();
        this.ctx.clearRect(0, 0, this.cssWidth, this.cssHeight);
        renderElements(this.ctx, elements, viewport, {
            worldClip: viewportWorldRect(viewport, this.cssWidth, this.cssHeight),
            measureWidth,
        });
        this.baked = { x: viewport.x, y: viewport.y, scale: viewport.scale };
        return true;
    }

    /**
     * 增量提交一个新元素。
     * 用**烘焙时**的视口变换绘制 —— 缓存是「全部元素在 baked 视口下的渲染结果」，
     * 追加一个同样在 baked 下渲染的元素仍然自洽，与当前视口在哪儿无关。
     */
    commit(element, measureWidth) {
        if (!this.valid) return false;
        this.resetTransform();
        renderElements(this.ctx, [element], this.baked, {
            worldClip: viewportWorldRect(this.baked, this.cssWidth, this.cssHeight),
            measureWidth,
        });
        return true;
    }

    /**
     * 局部重画（整笔橡皮删元素后用）。
     * 在这块区域里清空再按顺序重放所有与之相交的元素 —— 区域内的合成结果只取决于
     * 碰到它的那些元素及其先后顺序，所以局部重放与全量重放等价（橡皮也一样）。
     */
    repaintRegion(elements, worldRect, measureWidth) {
        if (!this.valid) return false;
        const screen = worldRectToScreen(worldRect, this.baked);
        // 向外扩一像素，避免抗锯齿边缘残留。
        const pad = 1;
        const rect = {
            x: screen.x - pad, y: screen.y - pad, width: screen.width + pad * 2, height: screen.height + pad * 2,
        };
        if (rect.width <= 0 || rect.height <= 0) return true;
        this.resetTransform();
        this.ctx.save();
        this.ctx.beginPath();
        this.ctx.rect(rect.x, rect.y, rect.width, rect.height);
        this.ctx.clip();
        this.ctx.clearRect(rect.x, rect.y, rect.width, rect.height);
        renderElements(this.ctx, elements, this.baked, {
            worldClip: screenRectToWorld(rect, this.baked),
            measureWidth,
        });
        this.ctx.restore();
        return true;
    }

    /** 缓存在给定视口下的落点与露出情况（不画，只算，供调用方决定是 blit 还是重建）。 */
    geometryFor(viewport) {
        if (!this.valid) return null;
        return blitGeometry(this.baked, viewport, this.cssWidth, this.cssHeight);
    }

    /** 按两视口差量把缓存搬到目标画布上。 */
    blitTo(targetCtx, geometry) {
        targetCtx.save();
        targetCtx.setTransform(
            this.dpr * geometry.scale, 0, 0, this.dpr * geometry.scale,
            this.dpr * geometry.covered.x, this.dpr * geometry.covered.y,
        );
        targetCtx.imageSmoothingEnabled = true;
        targetCtx.imageSmoothingQuality = 'low';
        targetCtx.drawImage(this.canvas, 0, 0, this.cssWidth, this.cssHeight);
        targetCtx.restore();
    }
}
