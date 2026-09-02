/**
 * 导出：包围盒取景、尺寸联动、离屏渲染、5MB 拟合、文件名。
 * 纯数学部分（exportFrame / lockAspect / fitUnderLimit / buildFileName）可在 node 下单测。
 */
import { EXPORT } from './constants.js';
import { boardBounds } from './geometry.js';
import { drawElement } from './renderer.js';

export const EXPORT_FORMATS = [
    { id: 'png-white', label: 'PNG 白底', mime: 'image/png', ext: 'png', background: '#ffffff' },
    { id: 'png-transparent', label: 'PNG 透明', mime: 'image/png', ext: 'png', background: null },
    { id: 'jpg', label: 'JPG', mime: 'image/jpeg', ext: 'jpg', background: '#ffffff' },
];

export function getFormat(id) {
    return EXPORT_FORMATS.find((format) => format.id === id) || EXPORT_FORMATS[0];
}

/** 取景框：包围盒 + 补白（世界坐标）。空板返回 null。 */
export function exportFrame(elements, measureWidth) {
    const bounds = boardBounds(elements, measureWidth);
    if (!bounds || !(bounds.width > 0) || !(bounds.height > 0)) return null;
    const pad = Math.max(24, Math.max(bounds.width, bounds.height) * 0.04);
    return {
        x: bounds.x - pad,
        y: bounds.y - pad,
        width: bounds.width + pad * 2,
        height: bounds.height + pad * 2,
    };
}

function clampEdge(value) {
    return Math.round(Math.min(EXPORT.MAX_EDGE, Math.max(EXPORT.MIN_EDGE, value)));
}

/**
 * 比例锁定：给宽算高或给高算宽。aspect = width / height。
 * 返回值同时满足单边上限与总像素上限。
 */
export function lockAspect({ width, height }, aspect) {
    const ratio = aspect > 0 ? aspect : 1;
    let w;
    let h;
    if (Number.isFinite(width) && width > 0) {
        w = clampEdge(width);
        h = clampEdge(w / ratio);
    } else {
        h = clampEdge(height);
        w = clampEdge(h * ratio);
    }
    if (w * h > EXPORT.MAX_PIXELS) {
        const scale = Math.sqrt(EXPORT.MAX_PIXELS / (w * h));
        w = Math.max(EXPORT.MIN_EDGE, Math.floor(w * scale));
        h = Math.max(EXPORT.MIN_EDGE, Math.floor(w / ratio));
        while (w * h > EXPORT.MAX_PIXELS && w > EXPORT.MIN_EDGE) {
            w -= 1;
            h = Math.max(EXPORT.MIN_EDGE, Math.floor(w / ratio));
        }
    }
    return { width: w, height: h };
}

export function defaultExportSize(aspect, longEdge = EXPORT.DEFAULT_LONG_EDGE) {
    return aspect >= 1 ? lockAspect({ width: longEdge }, aspect) : lockAspect({ height: longEdge }, aspect);
}

export function sanitizeFileStem(value) {
    return String(value || '')
        // eslint-disable-next-line no-control-regex
        .replace(/[\/:*?"<>|\u0000-\u001f]/g, '')
        .replace(/\s+/g, '-')
        .replace(/-+/g, '-')
        .replace(/^[-.]+|[-.]+$/g, '')
        .slice(0, EXPORT.FILE_NAME_MAX);
}

export function buildFileName(materialName, boardName, date = new Date()) {
    const pad = (n) => String(n).padStart(2, '0');
    const stamp = `${date.getFullYear()}${pad(date.getMonth() + 1)}${pad(date.getDate())}-${pad(date.getHours())}${pad(date.getMinutes())}`;
    const material = String(materialName || '').replace(/\.[a-z0-9]{1,6}$/i, '').trim();
    const board = String(boardName || '').replace(/[·•]/g, ' ').trim();
    const head = board && material && board.startsWith(material) ? board : [material, board].filter(Boolean).join('-');
    const stem = sanitizeFileStem(`${head || '白板'}-${stamp}`);
    return stem || `whiteboard-${stamp}`;
}

export function formatBytes(bytes) {
    if (!(bytes >= 0)) return '—';
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

function canvasToBlob(canvas, mime, quality) {
    return new Promise((resolve, reject) => {
        canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error('导出失败：无法生成图片'))), mime, quality);
    });
}

/**
 * 渲染导出图：先在透明层画元素（橡皮 destination-out），再合成到背景上，避免挖穿白底。
 */
export async function renderExportBlob({ elements, frame, width, height, formatId }) {
    const format = getFormat(formatId);
    const scale = width / frame.width;
    const ink = document.createElement('canvas');
    ink.width = width;
    ink.height = height;
    const inkCtx = ink.getContext('2d');
    if (!inkCtx) throw new Error('导出失败：画布不可用');
    inkCtx.setTransform(scale, 0, 0, scale, -frame.x * scale, -frame.y * scale);
    for (const element of elements || []) drawElement(inkCtx, element);

    if (!format.background) return canvasToBlob(ink, format.mime);
    const out = document.createElement('canvas');
    out.width = width;
    out.height = height;
    const outCtx = out.getContext('2d');
    outCtx.fillStyle = format.background;
    outCtx.fillRect(0, 0, width, height);
    outCtx.drawImage(ink, 0, 0);
    return canvasToBlob(out, format.mime, format.mime === 'image/jpeg' ? EXPORT.JPEG_QUALITY : undefined);
}

/**
 * 体积拟合：render(size) → blob；超限则按 sqrt 比例缩小重试。
 * @returns {Promise<{blob:Blob, width:number, height:number, rounds:number}>}
 */
export async function fitUnderLimit(render, size, aspect, limit = EXPORT.MAX_BYTES, rounds = EXPORT.FIT_ROUNDS) {
    let current = { ...size };
    let blob = await render(current);
    let round = 0;
    while (blob.size > limit && round < rounds) {
        const factor = Math.sqrt(limit / blob.size) * 0.95;
        const next = lockAspect({ width: Math.max(EXPORT.MIN_EDGE, current.width * factor) }, aspect);
        if (next.width >= current.width) break;
        current = next;
        blob = await render(current);
        round += 1;
    }
    return { blob, width: current.width, height: current.height, rounds: round };
}

export function downloadBlob(blob, fileName) {
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = fileName;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 2000);
}
