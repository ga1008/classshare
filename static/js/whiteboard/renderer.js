/**
 * 元素渲染（画布上下文无关，可用于屏幕与离屏导出）。
 */
import { CANVAS_FONT_STACK, DEFAULT_COLOR } from './constants.js';
import { hexToRgba, toFiniteNumber } from './state.js';
import { boundsIntersectRect, cachedPaintBounds, getShapeBox, textLines, TEXT_LINE_HEIGHT } from './geometry.js';

export function roundedRectPath(ctx, x, y, width, height, radius) {
    const safeRadius = Math.min(Math.max(radius, 0), width / 2, height / 2);
    if (typeof ctx.roundRect === 'function') {
        ctx.roundRect(x, y, width, height, safeRadius);
        return;
    }
    ctx.moveTo(x + safeRadius, y);
    ctx.lineTo(x + width - safeRadius, y);
    ctx.quadraticCurveTo(x + width, y, x + width, y + safeRadius);
    ctx.lineTo(x + width, y + height - safeRadius);
    ctx.quadraticCurveTo(x + width, y + height, x + width - safeRadius, y + height);
    ctx.lineTo(x + safeRadius, y + height);
    ctx.quadraticCurveTo(x, y + height, x, y + height - safeRadius);
    ctx.lineTo(x, y + safeRadius);
    ctx.quadraticCurveTo(x, y, x + safeRadius, y);
    ctx.closePath();
}

function tracePolyline(ctx, points) {
    ctx.beginPath();
    ctx.moveTo(points[0].x, points[0].y);
    if (points.length === 2) {
        ctx.lineTo(points[1].x, points[1].y);
        return;
    }
    for (let index = 1; index < points.length - 1; index += 1) {
        const current = points[index];
        const next = points[index + 1];
        ctx.quadraticCurveTo(current.x, current.y, (current.x + next.x) / 2, (current.y + next.y) / 2);
    }
    const last = points[points.length - 1];
    ctx.lineTo(last.x, last.y);
}

export function drawStroke(ctx, element, options = {}) {
    const points = Array.isArray(element.points) ? element.points : [];
    if (!points.length) return;
    const size = Math.max(toFiniteNumber(element.size, 2), 0.4);
    ctx.save();
    ctx.globalAlpha = options.draft ? 0.82 : 1;
    ctx.strokeStyle = String(element.color || DEFAULT_COLOR);
    ctx.fillStyle = ctx.strokeStyle;
    ctx.lineWidth = size;
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    if (points.length === 1) {
        ctx.beginPath();
        ctx.arc(points[0].x, points[0].y, size / 2, 0, Math.PI * 2);
        ctx.fill();
    } else {
        tracePolyline(ctx, points);
        ctx.stroke();
    }
    ctx.restore();
}

/**
 * 软边橡皮的笔刷贴图缓存。
 *
 * 原来软边走 `ctx.filter = blur(...)`：canvas filter 在主流实现里是慢路径，每次描边都要
 * 额外分配中间层做卷积；更糟的是这些橡皮元素存在元素列表里，**每次重建都要把所有软边
 * 橡皮的模糊重放一遍**，一块板上用过几次软橡皮，此后每次重建都永久变慢。
 * 改成预生成一张径向渐变贴图沿路径盖章：贴图按 (size, hardness) 量化后复用，
 * 盖章是纯 drawImage，代价可预测。硬边（默认 hardness = 1）仍走单次描边，路径完全不变。
 */
const ERASER_SPRITES = new Map();
const SPRITE_CACHE_LIMIT = 24;
/** 贴图按 2 倍分辨率生成，放大绘制时不至于糊。 */
const SPRITE_OVERSAMPLE = 2;

function quantize(value, step) {
    return Math.round(value / step) * step;
}

export function eraserSpriteKey(size, hardness) {
    return `${quantize(size, 0.5)}|${quantize(hardness, 0.05)}`;
}

function eraserSprite(size, hardness) {
    const key = eraserSpriteKey(size, hardness);
    const hit = ERASER_SPRITES.get(key);
    if (hit) return hit;
    if (typeof document === 'undefined') return null;
    const pixels = Math.max(4, Math.ceil(size * SPRITE_OVERSAMPLE));
    const canvas = document.createElement('canvas');
    canvas.width = pixels;
    canvas.height = pixels;
    const ctx = canvas.getContext('2d');
    if (!ctx) return null;
    const radius = pixels / 2;
    const gradient = ctx.createRadialGradient(radius, radius, radius * hardness, radius, radius, radius);
    gradient.addColorStop(0, 'rgba(0,0,0,1)');
    gradient.addColorStop(1, 'rgba(0,0,0,0)');
    ctx.fillStyle = gradient;
    ctx.fillRect(0, 0, pixels, pixels);
    if (ERASER_SPRITES.size >= SPRITE_CACHE_LIMIT) {
        ERASER_SPRITES.delete(ERASER_SPRITES.keys().next().value);
    }
    ERASER_SPRITES.set(key, canvas);
    return canvas;
}

/** 一条橡皮路径最多盖多少章：防止「极细橡皮 + 极长路径」退化成几万次 drawImage。 */
export const MAX_ERASER_STAMPS = 4000;

function polylineLength(points) {
    let total = 0;
    for (let index = 1; index < points.length; index += 1) {
        total += Math.hypot(points[index].x - points[index - 1].x, points[index].y - points[index - 1].y);
    }
    return total;
}

/** 沿折线按固定间距取盖章点（含首尾）。 */
export function stampPoints(points, spacing) {
    if (points.length === 1) return [points[0]];
    const total = polylineLength(points);
    const step = Math.max(spacing, 0.05, total / MAX_ERASER_STAMPS);
    const stamps = [points[0]];
    let carry = 0;
    for (let index = 1; index < points.length; index += 1) {
        const from = points[index - 1];
        const to = points[index];
        const length = Math.hypot(to.x - from.x, to.y - from.y);
        if (length === 0) continue;
        let travelled = step - carry;
        while (travelled <= length) {
            const t = travelled / length;
            stamps.push({ x: from.x + (to.x - from.x) * t, y: from.y + (to.y - from.y) * t });
            travelled += step;
        }
        carry = (length - (travelled - step)) % step;
    }
    const last = points[points.length - 1];
    const tail = stamps[stamps.length - 1];
    if (Math.hypot(last.x - tail.x, last.y - tail.y) > 1e-6) stamps.push(last);
    return stamps;
}

/** 像素橡皮：destination-out，只擦除其之前绘制的内容。 */
export function drawEraser(ctx, element) {
    const points = Array.isArray(element.points) ? element.points : [];
    if (!points.length) return;
    const size = Math.max(toFiniteNumber(element.size, 8), 1);
    const hardness = Math.min(1, Math.max(0, toFiniteNumber(element.hardness, 1)));
    ctx.save();
    ctx.globalCompositeOperation = 'destination-out';

    if (hardness >= 0.999) {
        ctx.strokeStyle = 'rgba(0,0,0,1)';
        ctx.fillStyle = 'rgba(0,0,0,1)';
        ctx.lineCap = 'round';
        ctx.lineJoin = 'round';
        ctx.lineWidth = size;
        if (points.length === 1) {
            ctx.beginPath();
            ctx.arc(points[0].x, points[0].y, size / 2, 0, Math.PI * 2);
            ctx.fill();
        } else {
            tracePolyline(ctx, points);
            ctx.stroke();
        }
        ctx.restore();
        return;
    }

    const sprite = eraserSprite(size, hardness);
    if (!sprite) {
        // 拿不到贴图（无 document）时退回三层递减 alpha 的近似，不再用 filter。
        ctx.lineCap = 'round';
        ctx.lineJoin = 'round';
        ctx.strokeStyle = 'rgba(0,0,0,1)';
        for (const pass of eraserFallbackPasses(size, hardness)) {
            ctx.globalAlpha = pass.alpha;
            ctx.lineWidth = pass.width;
            tracePolyline(ctx, points);
            ctx.stroke();
        }
        ctx.restore();
        return;
    }
    const half = size / 2;
    for (const stamp of stampPoints(points, Math.max(size * 0.18, 0.5))) {
        ctx.drawImage(sprite, stamp.x - half, stamp.y - half, size, size);
    }
    ctx.restore();
}

/** 无法生成贴图时的退化方案（三层递减 alpha）。 */
export function eraserFallbackPasses(size, hardness) {
    const soft = 1 - hardness;
    return [
        { width: size * (1 - soft * 0.4), alpha: 1 },
        { width: size * (1 - soft * 0.15), alpha: 0.5 },
        { width: size * (1 + soft * 0.15), alpha: 0.22 },
    ];
}

export function drawShape(ctx, element, options = {}) {
    const box = getShapeBox(element);
    if (box.width < 0.5 || box.height < 0.5) return;
    const color = String(element.color || DEFAULT_COLOR);
    ctx.save();
    ctx.globalAlpha = options.draft ? 0.86 : 1;
    ctx.strokeStyle = color;
    ctx.fillStyle = hexToRgba(color, options.draft ? 0.11 : 0.055);
    ctx.lineWidth = Math.max(toFiniteNumber(element.size, 2), 0.4);
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    ctx.beginPath();
    switch (element.shape) {
        case 'circle':
            ctx.ellipse(box.x + box.width / 2, box.y + box.height / 2, box.width / 2, box.height / 2, 0, 0, Math.PI * 2);
            break;
        case 'rounded':
            roundedRectPath(ctx, box.x, box.y, box.width, box.height, Math.min(box.width, box.height) * 0.18);
            break;
        case 'diamond':
            ctx.moveTo(box.x + box.width / 2, box.y);
            ctx.lineTo(box.x + box.width, box.y + box.height / 2);
            ctx.lineTo(box.x + box.width / 2, box.y + box.height);
            ctx.lineTo(box.x, box.y + box.height / 2);
            ctx.closePath();
            break;
        default:
            ctx.rect(box.x, box.y, box.width, box.height);
            break;
    }
    ctx.fill();
    ctx.stroke();
    ctx.restore();
}

export function canvasFont(fontSize) {
    return `${fontSize}px ${CANVAS_FONT_STACK}`;
}

export function drawText(ctx, element, options = {}) {
    const text = String(element.text || '');
    if (!text.trim()) return;
    const fontSize = Math.max(toFiniteNumber(element.fontSize, 24), 4);
    const lines = textLines(element);
    const lineHeight = fontSize * TEXT_LINE_HEIGHT;
    ctx.save();
    ctx.globalAlpha = options.draft ? 0.82 : 1;
    ctx.fillStyle = String(element.color || DEFAULT_COLOR);
    ctx.font = canvasFont(fontSize);
    ctx.textBaseline = 'top';
    ctx.textAlign = 'left';
    lines.forEach((line, index) => {
        ctx.fillText(line || ' ', element.x, element.y + index * lineHeight);
    });
    ctx.restore();
}

export function drawElement(ctx, element, options = {}) {
    if (!element || typeof element !== 'object') return;
    switch (element.type) {
        case 'stroke': drawStroke(ctx, element, options); break;
        case 'shape': drawShape(ctx, element, options); break;
        case 'text': drawText(ctx, element, options); break;
        case 'eraser': drawEraser(ctx, element); break;
        default: break;
    }
}

/**
 * 在已设置好屏幕变换的 ctx 上按视口渲染整组元素。
 *
 * `worldClip` 给定时按元素包围盒做视口裁剪：只是**跳过绘制**，不改变顺序，
 * 所以橡皮的 `destination-out` 语义不受影响（被跳过的橡皮本来也影响不到这块区域）。
 * 包围盒算不出来的元素一律照画，宁可多画也不能少画。
 */
export function renderElements(ctx, elements, viewport, options = {}) {
    const { worldClip = null, measureWidth = undefined } = options;
    ctx.save();
    ctx.translate(viewport.x, viewport.y);
    ctx.scale(viewport.scale, viewport.scale);
    for (const element of elements || []) {
        if (worldClip && !boundsIntersectRect(cachedPaintBounds(element, measureWidth), worldClip)) continue;
        drawElement(ctx, element);
    }
    ctx.restore();
}

/** 创建用于量宽的离屏 2D 上下文（浏览器环境）。 */
export function createMeasureWidth() {
    const fallback = (text, size) => text.length * size * 0.6;
    if (typeof document === 'undefined') return fallback;
    const canvas = document.createElement('canvas');
    const ctx = canvas.getContext('2d');
    if (!ctx) return fallback;
    return (text, size) => {
        ctx.font = canvasFont(size);
        return ctx.measureText(text).width;
    };
}
