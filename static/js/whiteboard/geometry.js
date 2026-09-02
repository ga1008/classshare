/**
 * 几何纯函数：距离、包围盒、命中测试、笔画简化。所有坐标为世界坐标。
 */
import { toFiniteNumber } from './state.js';

export const TEXT_LINE_HEIGHT = 1.28;

export function distance(a, b) {
    return Math.hypot(a.x - b.x, a.y - b.y);
}

export function pointToSegmentDistance(p, a, b) {
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const lengthSq = dx * dx + dy * dy;
    if (lengthSq === 0) return distance(p, a);
    const t = Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / lengthSq));
    return distance(p, { x: a.x + t * dx, y: a.y + t * dy });
}

export function getShapeBox(element) {
    const x1 = toFiniteNumber(element.x1, 0);
    const y1 = toFiniteNumber(element.y1, 0);
    let x2 = toFiniteNumber(element.x2, x1);
    let y2 = toFiniteNumber(element.y2, y1);
    if (element.shape === 'square' || element.shape === 'circle') {
        const dx = x2 - x1;
        const dy = y2 - y1;
        const side = Math.max(Math.abs(dx), Math.abs(dy));
        x2 = x1 + (dx < 0 ? -side : side);
        y2 = y1 + (dy < 0 ? -side : side);
    }
    return { x: Math.min(x1, x2), y: Math.min(y1, y2), width: Math.abs(x2 - x1), height: Math.abs(y2 - y1) };
}

export function textLines(element) {
    return String(element.text || '').replace(/\r\n/g, '\n').split('\n');
}

const fallbackMeasure = (text, size) => text.length * size * 0.6;

/**
 * @param {object} element
 * @param {(text:string, fontSize:number)=>number} measureWidth 文本量宽回调（浏览器用 canvas measureText）
 */
export function textBox(element, measureWidth = fallbackMeasure) {
    const fontSize = Math.max(toFiniteNumber(element.fontSize, 24), 4);
    const lines = textLines(element);
    const width = lines.reduce((max, line) => Math.max(max, measureWidth(line || ' ', fontSize)), 0);
    return {
        x: toFiniteNumber(element.x, 0),
        y: toFiniteNumber(element.y, 0),
        width,
        height: lines.length * fontSize * TEXT_LINE_HEIGHT,
    };
}

/** 单元素包围盒（含线宽），橡皮返回 null。 */
export function elementBounds(element, measureWidth = fallbackMeasure) {
    if (!element || typeof element !== 'object') return null;
    if (element.type === 'stroke') {
        const points = Array.isArray(element.points) ? element.points : [];
        if (!points.length) return null;
        const half = Math.max(toFiniteNumber(element.size, 2), 0.4) / 2;
        let minX = Infinity; let minY = Infinity; let maxX = -Infinity; let maxY = -Infinity;
        for (const p of points) {
            if (p.x < minX) minX = p.x;
            if (p.y < minY) minY = p.y;
            if (p.x > maxX) maxX = p.x;
            if (p.y > maxY) maxY = p.y;
        }
        return { x: minX - half, y: minY - half, width: maxX - minX + half * 2, height: maxY - minY + half * 2 };
    }
    if (element.type === 'shape') {
        const box = getShapeBox(element);
        const half = Math.max(toFiniteNumber(element.size, 2), 0.4) / 2;
        return { x: box.x - half, y: box.y - half, width: box.width + half * 2, height: box.height + half * 2 };
    }
    if (element.type === 'text') {
        if (!String(element.text || '').trim()) return null;
        return textBox(element, measureWidth);
    }
    return null;
}

export function unionBounds(a, b) {
    if (!a) return b;
    if (!b) return a;
    const x = Math.min(a.x, b.x);
    const y = Math.min(a.y, b.y);
    return {
        x,
        y,
        width: Math.max(a.x + a.width, b.x + b.width) - x,
        height: Math.max(a.y + a.height, b.y + b.height) - y,
    };
}

export function boardBounds(elements, measureWidth = fallbackMeasure) {
    let bounds = null;
    for (const element of Array.isArray(elements) ? elements : []) {
        bounds = unionBounds(bounds, elementBounds(element, measureWidth));
    }
    return bounds;
}

function polygonOutlineDistance(point, corners) {
    let min = Infinity;
    for (let i = 0; i < corners.length; i += 1) {
        min = Math.min(min, pointToSegmentDistance(point, corners[i], corners[(i + 1) % corners.length]));
    }
    return min;
}

function rectOutlineDistance(point, box) {
    return polygonOutlineDistance(point, [
        { x: box.x, y: box.y },
        { x: box.x + box.width, y: box.y },
        { x: box.x + box.width, y: box.y + box.height },
        { x: box.x, y: box.y + box.height },
    ]);
}

function diamondOutlineDistance(point, box) {
    const cx = box.x + box.width / 2;
    const cy = box.y + box.height / 2;
    return polygonOutlineDistance(point, [
        { x: cx, y: box.y },
        { x: box.x + box.width, y: cy },
        { x: cx, y: box.y + box.height },
        { x: box.x, y: cy },
    ]);
}

function ellipseOutlineDistance(point, box) {
    const rx = box.width / 2;
    const ry = box.height / 2;
    if (rx <= 0 || ry <= 0) return Infinity;
    const nx = (point.x - (box.x + rx)) / rx;
    const ny = (point.y - (box.y + ry)) / ry;
    // 归一化半径差乘以平均半径，近似到轮廓的距离。
    return Math.abs(Math.hypot(nx, ny) - 1) * ((rx + ry) / 2);
}

/** 指针（世界坐标）是否命中元素；橡皮元素永不命中。 */
export function hitTestElement(element, point, radius, measureWidth = fallbackMeasure) {
    if (!element || element.type === 'eraser') return false;
    if (element.type === 'stroke') {
        const points = Array.isArray(element.points) ? element.points : [];
        if (!points.length) return false;
        const tolerance = radius + Math.max(toFiniteNumber(element.size, 2), 0.4) / 2;
        if (points.length === 1) return distance(point, points[0]) <= tolerance;
        for (let i = 0; i < points.length - 1; i += 1) {
            if (pointToSegmentDistance(point, points[i], points[i + 1]) <= tolerance) return true;
        }
        return false;
    }
    if (element.type === 'shape') {
        const box = getShapeBox(element);
        const tolerance = radius + Math.max(toFiniteNumber(element.size, 2), 0.4) / 2;
        if (element.shape === 'circle') return ellipseOutlineDistance(point, box) <= tolerance;
        if (element.shape === 'diamond') return diamondOutlineDistance(point, box) <= tolerance;
        return rectOutlineDistance(point, box) <= tolerance;
    }
    if (element.type === 'text') {
        const box = textBox(element, measureWidth);
        return point.x >= box.x - radius && point.x <= box.x + box.width + radius
            && point.y >= box.y - radius && point.y <= box.y + box.height + radius;
    }
    return false;
}

/** Ramer–Douglas–Peucker 笔画简化（保留首尾）。 */
export function simplifyStroke(points, tolerance) {
    if (!Array.isArray(points) || points.length < 3 || !(tolerance > 0)) return points;
    const keep = new Uint8Array(points.length);
    keep[0] = 1;
    keep[points.length - 1] = 1;
    const stack = [[0, points.length - 1]];
    while (stack.length) {
        const [start, end] = stack.pop();
        let maxDist = 0;
        let index = -1;
        for (let i = start + 1; i < end; i += 1) {
            const d = pointToSegmentDistance(points[i], points[start], points[end]);
            if (d > maxDist) {
                maxDist = d;
                index = i;
            }
        }
        if (index !== -1 && maxDist > tolerance) {
            keep[index] = 1;
            stack.push([start, index], [index, end]);
        }
    }
    return points.filter((_, i) => keep[i] === 1);
}
