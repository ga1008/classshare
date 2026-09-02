/**
 * 白板状态：纯函数（创建、规范化、v1→v2 迁移、判空）。
 */
import {
    DEFAULT_SETTINGS, DEFAULT_COLOR, LEGACY_DEFAULT_COLOR, ELEMENT_TYPES, ERASER_MODES,
    LIMITS, MIN_ZOOM, MAX_ZOOM, SHAPES, STATE_VERSION, TOOLS,
} from './constants.js';

export function clamp(value, min, max) {
    return Math.min(max, Math.max(min, value));
}

export function toFiniteNumber(value, fallback) {
    const number = Number(value);
    return Number.isFinite(number) ? number : fallback;
}

export function makeId(prefix) {
    return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 9)}`;
}

export function nowIso() {
    return new Date().toISOString();
}

export function formatBoardTime(isoValue) {
    const date = new Date(isoValue || Date.now());
    if (Number.isNaN(date.getTime())) return '';
    const pad = (n) => String(n).padStart(2, '0');
    return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function formatRelativeTime(isoValue, now = Date.now()) {
    const date = new Date(isoValue || now);
    if (Number.isNaN(date.getTime())) return '';
    const diff = now - date.getTime();
    const minute = 60_000;
    if (diff < minute) return '刚刚';
    if (diff < 60 * minute) return `${Math.floor(diff / minute)} 分钟前`;
    const sameDay = new Date(now).toDateString() === date.toDateString();
    const pad = (n) => String(n).padStart(2, '0');
    const hm = `${pad(date.getHours())}:${pad(date.getMinutes())}`;
    if (sameDay) return `今天 ${hm}`;
    const yesterday = new Date(now - 86_400_000).toDateString() === date.toDateString();
    if (yesterday) return `昨天 ${hm}`;
    return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${hm}`;
}

function viewportDefaults() {
    const width = typeof window !== 'undefined' ? window.innerWidth : 1280;
    const height = typeof window !== 'undefined' ? window.innerHeight : 720;
    return { x: Math.round(width / 2), y: Math.round(height / 2), scale: 1 };
}

export function createViewport() {
    return viewportDefaults();
}

export function normalizeViewport(viewport = {}) {
    const defaults = viewportDefaults();
    return {
        x: toFiniteNumber(viewport?.x, defaults.x),
        y: toFiniteNumber(viewport?.y, defaults.y),
        scale: clamp(toFiniteNumber(viewport?.scale, 1), MIN_ZOOM, MAX_ZOOM),
    };
}

export function createBoard(name = '') {
    const createdAt = nowIso();
    return {
        id: makeId('board'),
        name: (name || `讲课白板 ${formatBoardTime(createdAt)}`).slice(0, LIMITS.boardNameLength),
        createdAt,
        updatedAt: createdAt,
        viewport: createViewport(),
        elements: [],
        elementsLoaded: true,
        elementCount: 0,
        remoteVersion: 0,
        syncedAt: null,
        dirty: false,
    };
}

export function cloneElements(elements) {
    return JSON.parse(JSON.stringify(Array.isArray(elements) ? elements : []));
}

export function countInkElements(elements) {
    return (Array.isArray(elements) ? elements : []).filter((el) => el && el.type !== 'eraser').length;
}

/** 空板 = 没有任何非橡皮元素；远端未加载的板按服务端计数判断。 */
export function isBoardEmpty(board) {
    if (!board) return true;
    if (board.elementsLoaded === false) return !(toFiniteNumber(board.elementCount, 0) > 0);
    return countInkElements(board.elements) === 0;
}

export function nextBoardName(materialName, boards) {
    const base = `${materialName || '课程材料'} · 白板`;
    const used = new Set((boards || []).map((board) => String(board.name || '')));
    let index = (boards || []).length + 1;
    let candidate = `${base} ${index}`;
    while (used.has(candidate)) {
        index += 1;
        candidate = `${base} ${index}`;
    }
    return candidate.slice(0, LIMITS.boardNameLength);
}

export function sanitizeElement(raw) {
    if (!raw || typeof raw !== 'object' || !ELEMENT_TYPES.includes(raw.type)) return null;
    return raw;
}

export function sanitizeBoard(rawBoard, fallbackIndex = 1) {
    if (!rawBoard || typeof rawBoard !== 'object') return createBoard(`讲课白板 ${fallbackIndex}`);
    const createdAt = rawBoard.createdAt || nowIso();
    const elements = Array.isArray(rawBoard.elements) ? rawBoard.elements.map(sanitizeElement).filter(Boolean) : [];
    const elementsLoaded = rawBoard.elementsLoaded !== false;
    return {
        id: String(rawBoard.id || makeId('board')),
        name: String(rawBoard.name || `讲课白板 ${fallbackIndex}`).slice(0, LIMITS.boardNameLength),
        createdAt,
        updatedAt: rawBoard.updatedAt || createdAt,
        viewport: normalizeViewport(rawBoard.viewport),
        elements,
        elementsLoaded,
        elementCount: elementsLoaded ? countInkElements(elements) : Math.max(0, toFiniteNumber(rawBoard.elementCount, 0)),
        remoteVersion: Math.max(0, Math.floor(toFiniteNumber(rawBoard.remoteVersion, 0))),
        syncedAt: rawBoard.syncedAt || null,
        dirty: Boolean(rawBoard.dirty),
    };
}

export function normalizeSettings(rawSettings = {}) {
    const raw = rawSettings || {};
    return {
        tool: TOOLS.includes(raw.tool) ? raw.tool : DEFAULT_SETTINGS.tool,
        shapeType: SHAPES.includes(raw.shapeType) ? raw.shapeType : DEFAULT_SETTINGS.shapeType,
        brushColor: String(raw.brushColor || DEFAULT_SETTINGS.brushColor),
        brushSize: clamp(toFiniteNumber(raw.brushSize, DEFAULT_SETTINGS.brushSize), ...LIMITS.brushSize),
        textColor: String(raw.textColor || DEFAULT_SETTINGS.textColor),
        fontSize: clamp(toFiniteNumber(raw.fontSize, DEFAULT_SETTINGS.fontSize), ...LIMITS.fontSize),
        boardOpacity: clamp(toFiniteNumber(raw.boardOpacity, DEFAULT_SETTINGS.boardOpacity), ...LIMITS.boardOpacity),
        backgroundOpacity: clamp(toFiniteNumber(raw.backgroundOpacity, DEFAULT_SETTINGS.backgroundOpacity), ...LIMITS.backgroundOpacity),
        eraserMode: ERASER_MODES.includes(raw.eraserMode) ? raw.eraserMode : DEFAULT_SETTINGS.eraserMode,
        eraserSize: clamp(toFiniteNumber(raw.eraserSize, DEFAULT_SETTINGS.eraserSize), ...LIMITS.eraserSize),
        eraserHardness: clamp(toFiniteNumber(raw.eraserHardness, DEFAULT_SETTINGS.eraserHardness), ...LIMITS.eraserHardness),
    };
}

export function normalizeState(rawState, context) {
    const fallbackBoard = createBoard(`${context?.materialName || '课程材料'} · 白板 1`);
    if (!rawState || typeof rawState !== 'object') {
        return {
            version: STATE_VERSION,
            activeBoardId: fallbackBoard.id,
            boards: [fallbackBoard],
            settings: { ...DEFAULT_SETTINGS },
        };
    }
    const boards = Array.isArray(rawState.boards)
        ? rawState.boards.map((board, index) => sanitizeBoard(board, index + 1))
        : [];
    if (!boards.length) boards.push(fallbackBoard);
    let activeBoardId = String(rawState.activeBoardId || '');
    if (!boards.some((board) => board.id === activeBoardId)) activeBoardId = boards[0].id;
    return {
        version: STATE_VERSION,
        activeBoardId,
        boards,
        settings: normalizeSettings(rawState.settings),
    };
}

/** v1（仅本地、深墨默认色）→ v2：旧默认色改为正红，其余保留；非空板标记待上传。 */
export function migrateLegacyState(rawV1, context) {
    const state = normalizeState(rawV1, context);
    const settings = { ...state.settings };
    if (String(rawV1?.settings?.brushColor || LEGACY_DEFAULT_COLOR).toLowerCase() === LEGACY_DEFAULT_COLOR) {
        settings.brushColor = DEFAULT_COLOR;
    }
    if (String(rawV1?.settings?.textColor || LEGACY_DEFAULT_COLOR).toLowerCase() === LEGACY_DEFAULT_COLOR) {
        settings.textColor = DEFAULT_COLOR;
    }
    return {
        ...state,
        boards: state.boards.map((board) => ({ ...board, dirty: !isBoardEmpty(board), remoteVersion: 0 })),
        settings,
    };
}

export function hexToRgba(value, alpha) {
    let hex = String(value || '').trim();
    if (!hex.startsWith('#')) return `rgba(15, 23, 42, ${alpha})`;
    hex = hex.slice(1);
    if (hex.length === 3) hex = hex.split('').map((char) => char + char).join('');
    if (hex.length !== 6) return `rgba(15, 23, 42, ${alpha})`;
    const number = Number.parseInt(hex, 16);
    if (!Number.isFinite(number)) return `rgba(15, 23, 42, ${alpha})`;
    return `rgba(${(number >> 16) & 255}, ${(number >> 8) & 255}, ${number & 255}, ${alpha})`;
}
