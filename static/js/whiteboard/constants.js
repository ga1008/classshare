/**
 * 讲课白板常量：默认设置、限额、图标。
 * 设计真源：docs/whiteboard-upgrade-2026-09.md
 */

export const STORAGE_NAMESPACE_LEGACY = 'teacher-whiteboard:v1';
export const STORAGE_NAMESPACE = 'teacher-whiteboard:v2';
export const FAB_STORAGE_NAMESPACE = 'teacher-whiteboard-fab:v1';
export const STATE_VERSION = 2;
export const MAX_BOARDS = 24;
export const UNDO_LIMIT = 36;
export const MIN_ZOOM = 0.35;
export const MAX_ZOOM = 2.6;
export const CANVAS_FONT_STACK = '"Segoe UI", "Microsoft YaHei", "PingFang SC", sans-serif';

export const LEGACY_DEFAULT_COLOR = '#0f172a';
export const DEFAULT_COLOR = '#ff0000';

export const TOOLS = ['hand', 'brush', 'eraser', 'text', 'shape'];
export const SHAPES = ['circle', 'square', 'rectangle', 'rounded', 'diamond'];
export const ELEMENT_TYPES = ['stroke', 'shape', 'text', 'eraser'];
export const ERASER_MODES = ['pixel', 'stroke'];

export const LIMITS = {
    brushSize: [1, 32],
    fontSize: [12, 72],
    boardOpacity: [0.35, 1],
    backgroundOpacity: [0, 0.95],
    eraserSize: [4, 120],
    eraserHardness: [0, 1],
    boardNameLength: 60,
};

export const DEFAULT_SETTINGS = Object.freeze({
    tool: 'brush',
    shapeType: 'rectangle',
    brushColor: DEFAULT_COLOR,
    brushSize: 5,
    textColor: DEFAULT_COLOR,
    fontSize: 28,
    boardOpacity: 1,
    backgroundOpacity: 0.78,
    eraserMode: 'pixel',
    eraserSize: 28,
    eraserHardness: 1,
});

export const COLOR_SWATCHES = [
    { value: '#ff0000', label: '正红' },
    { value: '#f97316', label: '橙' },
    { value: '#facc15', label: '黄' },
    { value: '#16a34a', label: '绿' },
    { value: '#0e7490', label: '青' },
    { value: '#2563eb', label: '蓝' },
    { value: '#7c3aed', label: '紫' },
    { value: '#0f172a', label: '墨黑' },
];

export const EXPORT = Object.freeze({
    DEFAULT_LONG_EDGE: 512,
    MIN_EDGE: 64,
    MAX_EDGE: 8192,
    MAX_PIXELS: 32_000_000,
    MAX_BYTES: 5 * 1024 * 1024,
    JPEG_QUALITY: 0.92,
    PRESETS: [512, 1024, 2048, 4096],
    FIT_ROUNDS: 3,
    PREVIEW_DEBOUNCE_MS: 300,
    FILE_NAME_MAX: 80,
});

export const REMOTE = Object.freeze({
    MAX_JSON_BYTES: 2 * 1024 * 1024,
    AUTO_SYNC_INTERVAL_MS: 30_000,
    SIMPLIFY_TOLERANCE: 0.35,
});

export const SYNC_STATUS = Object.freeze({
    LOCAL: 'local',
    SYNCED: 'synced',
    DIRTY: 'dirty',
    SAVING: 'saving',
    ERROR: 'error',
});

const svg = (body) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`;

export const ICONS = {
    board: svg('<path d="M4 5.5A2.5 2.5 0 0 1 6.5 3h11A2.5 2.5 0 0 1 20 5.5v13A2.5 2.5 0 0 1 17.5 21h-11A2.5 2.5 0 0 1 4 18.5z"></path><path d="M8 8h8M8 12h5M8 16h7"></path>'),
    close: svg('<path d="M18 6 6 18M6 6l12 12"></path>'),
    plus: svg('<path d="M12 5v14M5 12h14"></path>'),
    menu: svg('<path d="M4 7h16M4 12h16M4 17h16"></path>'),
    save: svg('<path d="M5 3h11l3 3v15H5z"></path><path d="M8 3v6h8V3"></path><path d="M8 21v-7h8v7"></path>'),
    cloud: svg('<path d="M7 18a4.5 4.5 0 0 1-.6-8.96A6 6 0 0 1 18 8a4 4 0 0 1 0 10H7z"></path><path d="M12 12v6M9.5 14.5 12 12l2.5 2.5"></path>'),
    download: svg('<path d="M12 4v11"></path><path d="m7.5 10.5 4.5 4.5 4.5-4.5"></path><path d="M4 19h16"></path>'),
    chevron: svg('<path d="m6 9 6 6 6-6"></path>'),
    check: svg('<path d="m5 12 5 5 9-10"></path>'),
    edit: svg('<path d="m15.2 5.2 3.6 3.6"></path><path d="M4 20l4.2-1 10.6-10.6a2.5 2.5 0 0 0-3.5-3.5L4.7 15.5z"></path>'),
    trash: svg('<path d="M3 6h18M8 6V4h8v2M6 6l1 15h10l1-15"></path><path d="M10 11v6M14 11v6"></path>'),
    hand: svg('<path d="M18 11.5V10a2 2 0 0 0-4 0v1"></path><path d="M14 10V8.5a2 2 0 0 0-4 0V12"></path><path d="M10 12V6.5a2 2 0 0 0-4 0v8.2"></path><path d="M18 11.5a2 2 0 0 1 4 0V15a7 7 0 0 1-7 7h-2.6a7 7 0 0 1-5-2.1L4 16.5a2 2 0 0 1 2.8-2.8L9 16"></path>'),
    pen: svg('<path d="m15.2 5.2 3.6 3.6"></path><path d="M4 20l4.2-1 10.6-10.6a2.5 2.5 0 0 0-3.5-3.5L4.7 15.5z"></path>'),
    eraser: svg('<path d="m7 21 -3.5-3.5a2 2 0 0 1 0-2.8l9.6-9.6a2 2 0 0 1 2.8 0l4.4 4.4a2 2 0 0 1 0 2.8L13.4 19"></path><path d="M7 21h13"></path><path d="m9.5 10.5 6 6"></path>'),
    text: svg('<path d="M4 7V5h16v2M9 20h6M12 5v15"></path>'),
    circle: svg('<circle cx="12" cy="12" r="7"></circle>'),
    square: svg('<path d="M7 7h10v10H7z"></path>'),
    rectangle: svg('<path d="M4 8h16v8H4z"></path>'),
    rounded: svg('<rect x="4" y="7" width="16" height="10" rx="3"></rect>'),
    diamond: svg('<path d="m12 4 8 8-8 8-8-8z"></path>'),
    undo: svg('<path d="M9 14 4 9l5-5"></path><path d="M4 9h10a6 6 0 0 1 0 12h-1"></path>'),
    redo: svg('<path d="m15 14 5-5-5-5"></path><path d="M20 9H10a6 6 0 0 0 0 12h1"></path>'),
    clear: svg('<path d="M3 6h18M8 6V4h8v2M6 6l1 15h10l1-15"></path><path d="M10 11v6M14 11v6"></path>'),
    zoomIn: svg('<circle cx="11" cy="11" r="7"></circle><path d="m20 20-4-4M11 8v6M8 11h6"></path>'),
    zoomOut: svg('<circle cx="11" cy="11" r="7"></circle><path d="m20 20-4-4M8 11h6"></path>'),
    resetView: svg('<path d="M3 12a9 9 0 1 0 3-6.7"></path><path d="M3 4v6h6"></path>'),
    ink: svg('<path d="M12 3s6 6.5 6 11a6 6 0 0 1-12 0c0-4.5 6-11 6-11z"></path>'),
    grid: svg('<path d="M4 4h16v16H4z"></path><path d="M4 12h16M12 4v16"></path>'),
    spinner: svg('<path d="M12 3a9 9 0 1 0 9 9"></path>'),
};
