/**
 * 元素渲染（画布上下文无关，可用于屏幕与离屏导出）。
 */
import { CANVAS_FONT_STACK, DEFAULT_COLOR } from './constants.js';
import { hexToRgba, toFiniteNumber } from './state.js';
import { getShapeBox, textLines, TEXT_LINE_HEIGHT } from './geometry.js';

let filterSupport = null;
export function supportsCanvasFilter(ctx) {
    if (filterSupport !== null) return filterSupport;
    try {
        filterSupport = typeof ctx?.filter === 'string';
    } catch {
        filterSupport = false;
    }
    return filterSupport;
}

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

/** 橡皮软边的绘制层次：硬度 1 单层；支持 filter 时模糊；否则三层递减 alpha 退化。 */
export function eraserPasses(size, hardness, canFilter) {
    if (hardness >= 0.999) return [{ width: size, alpha: 1, blur: 0 }];
    if (canFilter) return [{ width: size, alpha: 1, blur: size * (1 - hardness) * 0.35 }];
    const soft = 1 - hardness;
    return [
        { width: size * (1 - soft * 0.4), alpha: 1, blur: 0 },
        { width: size * (1 - soft * 0.15), alpha: 0.5, blur: 0 },
        { width: size * (1 + soft * 0.15), alpha: 0.22, blur: 0 },
    ];
}

/** 像素橡皮：destination-out，只擦除其之前绘制的内容。 */
export function drawEraser(ctx, element) {
    const points = Array.isArray(element.points) ? element.points : [];
    if (!points.length) return;
    const size = Math.max(toFiniteNumber(element.size, 8), 1);
    const hardness = Math.min(1, Math.max(0, toFiniteNumber(element.hardness, 1)));
    ctx.save();
    ctx.globalCompositeOperation = 'destination-out';
    ctx.strokeStyle = 'rgba(0,0,0,1)';
    ctx.fillStyle = 'rgba(0,0,0,1)';
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    for (const pass of eraserPasses(size, hardness, supportsCanvasFilter(ctx))) {
        ctx.globalAlpha = pass.alpha;
        ctx.lineWidth = pass.width;
        if (pass.blur > 0) ctx.filter = `blur(${pass.blur.toFixed(2)}px)`;
        if (points.length === 1) {
            ctx.beginPath();
            ctx.arc(points[0].x, points[0].y, pass.width / 2, 0, Math.PI * 2);
            ctx.fill();
        } else {
            tracePolyline(ctx, points);
            ctx.stroke();
        }
        if (pass.blur > 0) ctx.filter = 'none';
    }
    ctx.restore();
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

/** 在已设置好屏幕变换的 ctx 上按视口渲染整组元素。 */
export function renderElements(ctx, elements, viewport) {
    ctx.save();
    ctx.translate(viewport.x, viewport.y);
    ctx.scale(viewport.scale, viewport.scale);
    for (const element of elements || []) drawElement(ctx, element);
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
