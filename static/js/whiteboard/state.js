/**
 * 白板状态：纯函数（创建、规范化、v1→v2 迁移、判空）。
 */
import {
    DEFAULT_SETTINGS, DEFAULT_COLOR, LEGACY_DEFAULT_COLOR, ELEMENT_FIELDS, ERASER_MODES,
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

/**
 * 撤销/重做快照。
 *
 * **契约：元素一旦被推进 `board.elements` 就不再被修改**（绘制中的元素是独立对象，
 * 提交前才入列；`patchBoard` 换的是整个数组；从存储读回的是全新对象）。
 * 有了这个前提，快照只需要复制「引用数组」，不需要复制元素内容。
 *
 * 原来这里是 `JSON.parse(JSON.stringify(...))`：500 笔的板每笔要来回 3~5MB 文本、
 * 单次 30~150ms，36 层快照常驻内存是板体积的 37 倍（约 150MB），在 8GB 机器上直接
 * 引发周期性 GC 卡顿。改成浅拷贝后，一次快照是 n 个指针的 memcpy，几十微秒、几十 KB。
 *
 * 将来如果要支持「选中并编辑已有元素」，必须改成写时复制（替换元素而不是就地改），
 * 否则撤销会看到被改后的内容。
 */
export function cloneElements(elements) {
    return Array.isArray(elements) ? elements.slice() : [];
}

/** 非橡皮元素计数。热路径（每笔一次），用裸循环避免 filter 的中间数组分配。 */
export function countInkElements(elements) {
    const list = Array.isArray(elements) ? elements : [];
    let count = 0;
    for (let index = 0; index < list.length; index += 1) {
        if (list[index] && list[index].type !== 'eraser') count += 1;
    }
    return count;
}

/** 是否存在非橡皮元素。判空只需要「有没有」，提前返回避免遍历整块板。 */
export function hasInkElements(elements) {
    const list = Array.isArray(elements) ? elements : [];
    for (let index = 0; index < list.length; index += 1) {
        if (list[index] && list[index].type !== 'eraser') return true;
    }
    return false;
}

/** 空板 = 没有任何非橡皮元素；远端未加载的板按服务端计数判断。 */
export function isBoardEmpty(board) {
    if (!board) return true;
    if (board.elementsLoaded === false) return !(toFiniteNumber(board.elementCount, 0) > 0);
    return !hasInkElements(board.elements);
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

const ELEMENT_FIELD_SETS = new Map(
    Object.entries(ELEMENT_FIELDS).map(([type, fields]) => [type, new Set(fields)]),
);

/**
 * 类型 + 字段双重白名单。未知类型丢弃；已知类型剥掉未知字段。
 * 没有多余字段时原样返回，避免在载入路径上给每个元素都造一次新对象。
 */
export function sanitizeElement(raw) {
    if (!raw || typeof raw !== 'object') return null;
    const allowed = ELEMENT_FIELD_SETS.get(raw.type);
    if (!allowed) return null;
    const keys = Object.keys(raw);
    let hasExtra = false;
    for (let index = 0; index < keys.length; index += 1) {
        if (!allowed.has(keys[index])) { hasExtra = true; break; }
    }
    if (!hasExtra) return raw;
    const cleaned = {};
    for (const field of ELEMENT_FIELDS[raw.type]) {
        if (field in raw) cleaned[field] = raw[field];
    }
    return cleaned;
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
