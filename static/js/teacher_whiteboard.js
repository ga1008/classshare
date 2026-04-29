import { escapeHtml, showToast } from './ui.js';

const STORAGE_NAMESPACE = 'teacher-whiteboard:v1';
const FAB_STORAGE_NAMESPACE = 'teacher-whiteboard-fab:v1';
const MAX_BOARDS = 24;
const UNDO_LIMIT = 36;
const MIN_ZOOM = 0.35;
const MAX_ZOOM = 2.6;
const CANVAS_FONT_STACK = '"Segoe UI", "Microsoft YaHei", "PingFang SC", sans-serif';

const DEFAULT_SETTINGS = {
    tool: 'brush',
    shapeType: 'rectangle',
    brushColor: '#0f172a',
    brushSize: 5,
    textColor: '#0f172a',
    fontSize: 28,
    boardOpacity: 1,
    backgroundOpacity: 0.78,
};

const svg = (body) => `
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`;

const ICONS = {
    board: svg('<path d="M4 5.5A2.5 2.5 0 0 1 6.5 3h11A2.5 2.5 0 0 1 20 5.5v13A2.5 2.5 0 0 1 17.5 21h-11A2.5 2.5 0 0 1 4 18.5z"></path><path d="M8 8h8M8 12h5M8 16h7"></path>'),
    close: svg('<path d="M18 6 6 18M6 6l12 12"></path>'),
    plus: svg('<path d="M12 5v14M5 12h14"></path>'),
    hand: svg('<path d="M18 11.5V10a2 2 0 0 0-4 0v1"></path><path d="M14 10V8.5a2 2 0 0 0-4 0V12"></path><path d="M10 12V6.5a2 2 0 0 0-4 0v8.2"></path><path d="M18 11.5a2 2 0 0 1 4 0V15a7 7 0 0 1-7 7h-2.6a7 7 0 0 1-5-2.1L4 16.5a2 2 0 0 1 2.8-2.8L9 16"></path>'),
    pen: svg('<path d="m15.2 5.2 3.6 3.6"></path><path d="M4 20l4.2-1 10.6-10.6a2.5 2.5 0 0 0-3.5-3.5L4.7 15.5z"></path>'),
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
};

function normalizeContext(rawContext = {}) {
    return {
        userId: String(rawContext.userId ?? rawContext.user_id ?? 'teacher'),
        userRole: String(rawContext.userRole ?? rawContext.role ?? '').toLowerCase(),
        materialId: String(rawContext.materialId ?? rawContext.material_id ?? 'unknown'),
        materialName: String(rawContext.materialName ?? rawContext.material_name ?? document.title ?? '课程材料'),
    };
}

function isTeacherContext(context) {
    return context.userRole === 'teacher';
}

function clamp(value, min, max) {
    return Math.min(max, Math.max(min, value));
}

function toFiniteNumber(value, fallback) {
    const number = Number(value);
    return Number.isFinite(number) ? number : fallback;
}

function makeId(prefix) {
    return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 9)}`;
}

function nowIso() {
    return new Date().toISOString();
}

function formatBoardTime(isoValue) {
    const date = new Date(isoValue || Date.now());
    if (Number.isNaN(date.getTime())) return '';
    const month = String(date.getMonth() + 1).padStart(2, '0');
    const day = String(date.getDate()).padStart(2, '0');
    const hour = String(date.getHours()).padStart(2, '0');
    const minute = String(date.getMinutes()).padStart(2, '0');
    return `${month}-${day} ${hour}:${minute}`;
}

function createViewport() {
    return {
        x: Math.round(window.innerWidth / 2),
        y: Math.round(window.innerHeight / 2),
        scale: 1,
    };
}

function normalizeViewport(viewport = {}) {
    return {
        x: toFiniteNumber(viewport.x, Math.round(window.innerWidth / 2)),
        y: toFiniteNumber(viewport.y, Math.round(window.innerHeight / 2)),
        scale: clamp(toFiniteNumber(viewport.scale, 1), MIN_ZOOM, MAX_ZOOM),
    };
}

function createBoard(name = '') {
    const createdAt = nowIso();
    return {
        id: makeId('board'),
        name: name || `讲课白板 ${formatBoardTime(createdAt)}`,
        createdAt,
        updatedAt: createdAt,
        viewport: createViewport(),
        elements: [],
    };
}

function cloneElements(elements) {
    return JSON.parse(JSON.stringify(Array.isArray(elements) ? elements : []));
}

function normalizeSettings(rawSettings = {}) {
    return {
        tool: ['hand', 'brush', 'text', 'shape'].includes(rawSettings.tool) ? rawSettings.tool : DEFAULT_SETTINGS.tool,
        shapeType: ['circle', 'square', 'rectangle', 'rounded', 'diamond'].includes(rawSettings.shapeType)
            ? rawSettings.shapeType
            : DEFAULT_SETTINGS.shapeType,
        brushColor: String(rawSettings.brushColor || DEFAULT_SETTINGS.brushColor),
        brushSize: clamp(toFiniteNumber(rawSettings.brushSize, DEFAULT_SETTINGS.brushSize), 1, 32),
        textColor: String(rawSettings.textColor || DEFAULT_SETTINGS.textColor),
        fontSize: clamp(toFiniteNumber(rawSettings.fontSize, DEFAULT_SETTINGS.fontSize), 12, 72),
        boardOpacity: clamp(toFiniteNumber(rawSettings.boardOpacity, DEFAULT_SETTINGS.boardOpacity), 0.35, 1),
        backgroundOpacity: clamp(toFiniteNumber(rawSettings.backgroundOpacity, DEFAULT_SETTINGS.backgroundOpacity), 0, 0.95),
    };
}

function sanitizeBoard(rawBoard, fallbackIndex = 1) {
    if (!rawBoard || typeof rawBoard !== 'object') {
        return createBoard(`讲课白板 ${fallbackIndex}`);
    }

    const createdAt = rawBoard.createdAt || nowIso();
    return {
        id: String(rawBoard.id || makeId('board')),
        name: String(rawBoard.name || `讲课白板 ${fallbackIndex}`).slice(0, 60),
        createdAt,
        updatedAt: rawBoard.updatedAt || createdAt,
        viewport: normalizeViewport(rawBoard.viewport),
        elements: Array.isArray(rawBoard.elements) ? rawBoard.elements : [],
    };
}

function normalizeState(rawState, context) {
    const fallbackBoard = createBoard(`${context.materialName || '课程材料'} 白板`);
    if (!rawState || typeof rawState !== 'object') {
        return {
            version: 1,
            activeBoardId: fallbackBoard.id,
            boards: [fallbackBoard],
            settings: { ...DEFAULT_SETTINGS },
        };
    }

    const boards = Array.isArray(rawState.boards)
        ? rawState.boards.map((board, index) => sanitizeBoard(board, index + 1)).filter(Boolean)
        : [];
    if (!boards.length) boards.push(fallbackBoard);

    let activeBoardId = String(rawState.activeBoardId || '');
    if (!boards.some((board) => board.id === activeBoardId)) {
        activeBoardId = boards[0].id;
    }

    return {
        version: 1,
        activeBoardId,
        boards,
        settings: normalizeSettings(rawState.settings),
    };
}

function hexToRgba(value, alpha) {
    let hex = String(value || '').trim();
    if (!hex.startsWith('#')) return `rgba(15, 23, 42, ${alpha})`;
    hex = hex.slice(1);
    if (hex.length === 3) hex = hex.split('').map((char) => char + char).join('');
    if (hex.length !== 6) return `rgba(15, 23, 42, ${alpha})`;
    const number = Number.parseInt(hex, 16);
    if (!Number.isFinite(number)) return `rgba(15, 23, 42, ${alpha})`;
    return `rgba(${(number >> 16) & 255}, ${(number >> 8) & 255}, ${number & 255}, ${alpha})`;
}

function distance(a, b) {
    return Math.hypot(a.x - b.x, a.y - b.y);
}

class TeacherWhiteboard {
    constructor(rawContext = {}) {
        this.context = normalizeContext(rawContext);
        this.storageKey = `${STORAGE_NAMESPACE}:${encodeURIComponent(this.context.userId)}:${encodeURIComponent(this.context.materialId)}`;
        this.fabStorageKey = `${FAB_STORAGE_NAMESPACE}:${encodeURIComponent(this.context.userId)}`;
        this.state = normalizeState(null, this.context);
        this.activeBoard = this.state.boards[0];
        this.settings = { ...DEFAULT_SETTINGS };
        this.viewport = createViewport();
        this.rootEl = null;
        this.stageEl = null;
        this.canvasLayerEl = null;
        this.canvasEl = null;
        this.draftCanvasEl = null;
        this.ctx = null;
        this.draftCtx = null;
        this.toolbarEl = null;
        this.fabEl = null;
        this.controls = {};
        this.isOpen = false;
        this.dpr = 1;
        this.canvasWidth = 0;
        this.canvasHeight = 0;
        this.renderFrame = null;
        this.draftFrame = null;
        this.saveTimer = null;
        this.nameSaveTimer = null;
        this.closeTimer = null;
        this.saveErrorShown = false;
        this.activePointer = null;
        this.activeStroke = null;
        this.activeShape = null;
        this.activePan = null;
        this.textEditor = null;
        this.undoStack = [];
        this.redoStack = [];
        this.fabDrag = null;
        this.ignoreNextFabClick = false;
        this.previousBodyOverflow = '';
        this.boundResize = () => this.handleResize();
        this.boundKeydown = (event) => this.handleKeydown(event);
    }

    init() {
        if (!isTeacherContext(this.context) || document.getElementById('teacher-whiteboard-root')) return;
        this.state = this.loadState();
        this.settings = normalizeSettings(this.state.settings);
        this.activeBoard = this.state.boards.find((board) => board.id === this.state.activeBoardId) || this.state.boards[0];
        this.viewport = normalizeViewport(this.activeBoard.viewport);
        this.buildDom();
        this.cacheDom();
        if (!this.ctx || !this.draftCtx) return;
        this.bindEvents();
        this.syncControlsFromSettings();
        this.updateBoardControls();
        this.updateToolState();
        this.updateOpacityVariables();
        this.updateGridPosition();
        this.updateUndoRedoButtons();
        this.applyFabPosition();
        this.setFabOpenState(false);
        window.addEventListener('resize', this.boundResize);
        document.addEventListener('keydown', this.boundKeydown);
    }

    buildDom() {
        const root = document.createElement('div');
        root.id = 'teacher-whiteboard-root';
        root.className = 'teacher-whiteboard-root';
        root.hidden = true;
        root.setAttribute('aria-hidden', 'true');
        root.dataset.tool = this.settings.tool;
        root.innerHTML = `
            <div class="teacher-whiteboard-stage" id="teacher-whiteboard-stage">
                <div class="teacher-whiteboard-canvas-layer" id="teacher-whiteboard-canvas-layer">
                    <canvas id="teacher-whiteboard-canvas"></canvas>
                    <canvas id="teacher-whiteboard-draft-canvas"></canvas>
                </div>
            </div>
            <div class="teacher-whiteboard-toolbar" id="teacher-whiteboard-toolbar" role="toolbar" aria-label="讲课白板工具栏">
                <div class="teacher-whiteboard-group is-board">
                    <select id="teacher-whiteboard-board-select" class="teacher-whiteboard-select" title="历史画板" aria-label="历史画板"></select>
                    <input id="teacher-whiteboard-name-input" class="teacher-whiteboard-name" type="text" maxlength="60" aria-label="画板名称">
                    <button type="button" class="teacher-whiteboard-btn" data-whiteboard-action="new-board" title="新建画板" aria-label="新建画板">${ICONS.plus}</button>
                </div>
                <div class="teacher-whiteboard-group is-tools" aria-label="工具">
                    <button type="button" class="teacher-whiteboard-btn" data-whiteboard-tool="hand" title="拖动画布" aria-label="拖动画布">${ICONS.hand}</button>
                    <button type="button" class="teacher-whiteboard-btn" data-whiteboard-tool="brush" title="画笔" aria-label="画笔">${ICONS.pen}</button>
                    <button type="button" class="teacher-whiteboard-btn" data-whiteboard-tool="text" title="文字" aria-label="文字">${ICONS.text}</button>
                    <button type="button" class="teacher-whiteboard-btn" data-whiteboard-shape="circle" title="圆形" aria-label="圆形">${ICONS.circle}</button>
                    <button type="button" class="teacher-whiteboard-btn" data-whiteboard-shape="square" title="正方形" aria-label="正方形">${ICONS.square}</button>
                    <button type="button" class="teacher-whiteboard-btn" data-whiteboard-shape="rectangle" title="长方形" aria-label="长方形">${ICONS.rectangle}</button>
                    <button type="button" class="teacher-whiteboard-btn" data-whiteboard-shape="rounded" title="圆角矩形" aria-label="圆角矩形">${ICONS.rounded}</button>
                    <button type="button" class="teacher-whiteboard-btn" data-whiteboard-shape="diamond" title="菱形" aria-label="菱形">${ICONS.diamond}</button>
                </div>
                <div class="teacher-whiteboard-group">
                    <label class="teacher-whiteboard-control" title="画笔颜色"><span>画笔</span><input id="teacher-whiteboard-brush-color" class="teacher-whiteboard-color" type="color" aria-label="画笔颜色"></label>
                    <label class="teacher-whiteboard-control" title="画笔粗细"><input id="teacher-whiteboard-brush-size" class="teacher-whiteboard-range" type="range" min="1" max="32" step="1" aria-label="画笔粗细"><output id="teacher-whiteboard-brush-size-value" class="teacher-whiteboard-value"></output></label>
                </div>
                <div class="teacher-whiteboard-group">
                    <label class="teacher-whiteboard-control" title="文字颜色"><span>文字</span><input id="teacher-whiteboard-text-color" class="teacher-whiteboard-color" type="color" aria-label="文字颜色"></label>
                    <label class="teacher-whiteboard-control" title="字体大小"><input id="teacher-whiteboard-font-size" class="teacher-whiteboard-range" type="range" min="12" max="72" step="1" aria-label="字体大小"><output id="teacher-whiteboard-font-size-value" class="teacher-whiteboard-value"></output></label>
                </div>
                <div class="teacher-whiteboard-group">
                    <label class="teacher-whiteboard-control" title="白板笔迹透明度"><span>笔迹</span><input id="teacher-whiteboard-ink-opacity" class="teacher-whiteboard-range" type="range" min="35" max="100" step="5" aria-label="白板笔迹透明度"><output id="teacher-whiteboard-ink-opacity-value" class="teacher-whiteboard-value"></output></label>
                    <label class="teacher-whiteboard-control" title="背景透明度"><span>背景</span><input id="teacher-whiteboard-bg-opacity" class="teacher-whiteboard-range" type="range" min="0" max="95" step="5" aria-label="背景透明度"><output id="teacher-whiteboard-bg-opacity-value" class="teacher-whiteboard-value"></output></label>
                </div>
                <div class="teacher-whiteboard-group is-actions" aria-label="画板操作">
                    <button type="button" class="teacher-whiteboard-btn" data-whiteboard-action="undo" title="撤销" aria-label="撤销">${ICONS.undo}</button>
                    <button type="button" class="teacher-whiteboard-btn" data-whiteboard-action="redo" title="重做" aria-label="重做">${ICONS.redo}</button>
                    <button type="button" class="teacher-whiteboard-btn" data-whiteboard-action="zoom-out" title="缩小" aria-label="缩小">${ICONS.zoomOut}</button>
                    <button type="button" class="teacher-whiteboard-btn" data-whiteboard-action="zoom-in" title="放大" aria-label="放大">${ICONS.zoomIn}</button>
                    <button type="button" class="teacher-whiteboard-btn" data-whiteboard-action="reset-view" title="回到中心" aria-label="回到中心">${ICONS.resetView}</button>
                    <button type="button" class="teacher-whiteboard-btn" data-whiteboard-action="clear" title="清屏" aria-label="清屏">${ICONS.clear}</button>
                </div>
            </div>`;

        const fab = document.createElement('button');
        fab.id = 'teacher-whiteboard-fab';
        fab.type = 'button';
        fab.className = 'teacher-whiteboard-fab';
        fab.title = '讲课白板';
        fab.setAttribute('aria-label', '打开讲课白板');
        fab.setAttribute('aria-pressed', 'false');
        fab.innerHTML = ICONS.board;
        document.body.append(root, fab);
    }

    cacheDom() {
        this.rootEl = document.getElementById('teacher-whiteboard-root');
        this.stageEl = document.getElementById('teacher-whiteboard-stage');
        this.canvasLayerEl = document.getElementById('teacher-whiteboard-canvas-layer');
        this.canvasEl = document.getElementById('teacher-whiteboard-canvas');
        this.draftCanvasEl = document.getElementById('teacher-whiteboard-draft-canvas');
        this.toolbarEl = document.getElementById('teacher-whiteboard-toolbar');
        this.fabEl = document.getElementById('teacher-whiteboard-fab');
        this.ctx = this.canvasEl?.getContext('2d', { alpha: true, desynchronized: true })
            || this.canvasEl?.getContext('2d');
        this.draftCtx = this.draftCanvasEl?.getContext('2d', { alpha: true, desynchronized: true })
            || this.draftCanvasEl?.getContext('2d');
        this.controls = {
            boardSelect: document.getElementById('teacher-whiteboard-board-select'),
            nameInput: document.getElementById('teacher-whiteboard-name-input'),
            brushColor: document.getElementById('teacher-whiteboard-brush-color'),
            brushSize: document.getElementById('teacher-whiteboard-brush-size'),
            brushSizeValue: document.getElementById('teacher-whiteboard-brush-size-value'),
            textColor: document.getElementById('teacher-whiteboard-text-color'),
            fontSize: document.getElementById('teacher-whiteboard-font-size'),
            fontSizeValue: document.getElementById('teacher-whiteboard-font-size-value'),
            inkOpacity: document.getElementById('teacher-whiteboard-ink-opacity'),
            inkOpacityValue: document.getElementById('teacher-whiteboard-ink-opacity-value'),
            backgroundOpacity: document.getElementById('teacher-whiteboard-bg-opacity'),
            backgroundOpacityValue: document.getElementById('teacher-whiteboard-bg-opacity-value'),
        };
    }

    bindEvents() {
        this.toolbarEl?.addEventListener('pointerdown', (event) => event.stopPropagation());
        this.toolbarEl?.addEventListener('click', (event) => this.handleToolbarClick(event));
        this.controls.boardSelect?.addEventListener('change', () => this.selectBoard(this.controls.boardSelect.value));
        this.controls.nameInput?.addEventListener('input', () => this.handleNameInput());

        this.controls.brushColor?.addEventListener('input', () => {
            this.settings.brushColor = this.controls.brushColor.value;
            this.scheduleSave();
        });
        this.controls.brushSize?.addEventListener('input', () => {
            this.settings.brushSize = clamp(Number(this.controls.brushSize.value), 1, 32);
            this.updateRangeLabels();
            this.scheduleSave();
        });
        this.controls.textColor?.addEventListener('input', () => {
            this.settings.textColor = this.controls.textColor.value;
            if (this.textEditor?.element) this.textEditor.element.style.color = this.settings.textColor;
            this.scheduleSave();
        });
        this.controls.fontSize?.addEventListener('input', () => {
            this.settings.fontSize = clamp(Number(this.controls.fontSize.value), 12, 72);
            if (this.textEditor?.element) this.textEditor.element.style.fontSize = `${this.settings.fontSize}px`;
            this.updateRangeLabels();
            this.scheduleSave();
        });
        this.controls.inkOpacity?.addEventListener('input', () => {
            this.settings.boardOpacity = clamp(Number(this.controls.inkOpacity.value) / 100, 0.35, 1);
            this.updateOpacityVariables();
            this.updateRangeLabels();
            this.scheduleSave();
        });
        this.controls.backgroundOpacity?.addEventListener('input', () => {
            this.settings.backgroundOpacity = clamp(Number(this.controls.backgroundOpacity.value) / 100, 0, 0.95);
            this.updateOpacityVariables();
            this.updateRangeLabels();
            this.scheduleSave();
        });

        this.stageEl?.addEventListener('pointerdown', (event) => this.handleStagePointerDown(event));
        this.stageEl?.addEventListener('pointermove', (event) => this.handleStagePointerMove(event));
        this.stageEl?.addEventListener('pointerup', (event) => this.handleStagePointerUp(event));
        this.stageEl?.addEventListener('pointercancel', (event) => this.handleStagePointerCancel(event));
        this.stageEl?.addEventListener('wheel', (event) => this.handleWheel(event), { passive: false });
        this.fabEl?.addEventListener('pointerdown', (event) => this.handleFabPointerDown(event));
        this.fabEl?.addEventListener('pointermove', (event) => this.handleFabPointerMove(event));
        this.fabEl?.addEventListener('pointerup', (event) => this.handleFabPointerUp(event));
        this.fabEl?.addEventListener('pointercancel', (event) => this.handleFabPointerCancel(event));
        this.fabEl?.addEventListener('click', (event) => this.handleFabClick(event));
    }

    loadState() {
        try {
            const rawValue = window.localStorage.getItem(this.storageKey);
            return normalizeState(rawValue ? JSON.parse(rawValue) : null, this.context);
        } catch (error) {
            console.warn('Failed to load teacher whiteboard state:', error);
            return normalizeState(null, this.context);
        }
    }

    saveNow() {
        if (!this.activeBoard) return;
        this.activeBoard.viewport = { ...this.viewport };
        this.activeBoard.updatedAt = this.activeBoard.updatedAt || nowIso();
        this.state.settings = { ...this.settings };
        this.state.activeBoardId = this.activeBoard.id;
        this.pruneBoards();

        try {
            window.localStorage.setItem(this.storageKey, JSON.stringify(this.state));
            this.saveErrorShown = false;
        } catch (error) {
            console.warn('Failed to save teacher whiteboard state:', error);
            this.pruneBoards(8);
            try {
                window.localStorage.setItem(this.storageKey, JSON.stringify(this.state));
            } catch (retryError) {
                console.warn('Failed to save teacher whiteboard state after pruning:', retryError);
                if (!this.saveErrorShown) {
                    this.saveErrorShown = true;
                    showToast('白板内容过大，浏览器本地保存失败。请清理旧画板后继续。', 'warning', 4200);
                }
            }
        }
    }

    scheduleSave(delay = 450) {
        window.clearTimeout(this.saveTimer);
        this.saveTimer = window.setTimeout(() => {
            this.saveTimer = null;
            this.saveNow();
        }, delay);
    }

    pruneBoards(limit = MAX_BOARDS) {
        if (!Array.isArray(this.state.boards) || this.state.boards.length <= limit) return;
        const activeId = this.activeBoard?.id || this.state.activeBoardId;
        const sorted = [...this.state.boards].sort((a, b) => String(b.updatedAt || '').localeCompare(String(a.updatedAt || '')));
        const keep = new Set([activeId]);
        for (const board of sorted) {
            if (keep.size >= limit) break;
            keep.add(board.id);
        }
        this.state.boards = this.state.boards.filter((board) => keep.has(board.id));
    }

    syncControlsFromSettings() {
        if (this.controls.brushColor) this.controls.brushColor.value = this.settings.brushColor;
        if (this.controls.brushSize) this.controls.brushSize.value = String(this.settings.brushSize);
        if (this.controls.textColor) this.controls.textColor.value = this.settings.textColor;
        if (this.controls.fontSize) this.controls.fontSize.value = String(this.settings.fontSize);
        if (this.controls.inkOpacity) this.controls.inkOpacity.value = String(Math.round(this.settings.boardOpacity * 100));
        if (this.controls.backgroundOpacity) this.controls.backgroundOpacity.value = String(Math.round(this.settings.backgroundOpacity * 100));
        this.updateRangeLabels();
    }

    updateRangeLabels() {
        if (this.controls.brushSizeValue) this.controls.brushSizeValue.textContent = `${Math.round(this.settings.brushSize)}px`;
        if (this.controls.fontSizeValue) this.controls.fontSizeValue.textContent = `${Math.round(this.settings.fontSize)}px`;
        if (this.controls.inkOpacityValue) this.controls.inkOpacityValue.textContent = `${Math.round(this.settings.boardOpacity * 100)}%`;
        if (this.controls.backgroundOpacityValue) this.controls.backgroundOpacityValue.textContent = `${Math.round(this.settings.backgroundOpacity * 100)}%`;
    }

    updateOpacityVariables() {
        this.rootEl?.style.setProperty('--teacher-whiteboard-bg-alpha', String(this.settings.backgroundOpacity));
        this.rootEl?.style.setProperty('--teacher-whiteboard-ink-alpha', String(this.settings.boardOpacity));
    }

    updateGridPosition() {
        if (!this.rootEl) return;
        const gridSize = 40 * this.viewport.scale;
        const majorGridSize = 200 * this.viewport.scale;
        this.rootEl.style.setProperty('--teacher-whiteboard-pan-x', `${this.viewport.x % gridSize}px`);
        this.rootEl.style.setProperty('--teacher-whiteboard-pan-y', `${this.viewport.y % gridSize}px`);
        this.rootEl.style.setProperty('--teacher-whiteboard-grid-size', `${gridSize}px`);
        this.rootEl.style.setProperty('--teacher-whiteboard-major-grid-size', `${majorGridSize}px`);
    }

    updateBoardControls() {
        if (!this.controls.boardSelect || !this.controls.nameInput) return;
        const boards = [...this.state.boards].sort((a, b) => String(b.updatedAt || '').localeCompare(String(a.updatedAt || '')));
        this.controls.boardSelect.innerHTML = boards.map((board) => {
            const label = `${board.name || '未命名画板'} · ${formatBoardTime(board.updatedAt || board.createdAt)}`;
            return `<option value="${escapeHtml(board.id)}">${escapeHtml(label)}</option>`;
        }).join('');
        this.controls.boardSelect.value = this.activeBoard.id;
        this.controls.nameInput.value = this.activeBoard.name || '未命名画板';
    }

    updateActiveBoardOption() {
        if (!this.controls.boardSelect || !this.activeBoard) return;
        const option = Array.from(this.controls.boardSelect.options).find((item) => item.value === this.activeBoard.id);
        if (option) option.textContent = `${this.activeBoard.name || '未命名画板'} · ${formatBoardTime(this.activeBoard.updatedAt || this.activeBoard.createdAt)}`;
    }

    handleNameInput() {
        if (!this.activeBoard || !this.controls.nameInput) return;
        const nextName = this.controls.nameInput.value.trim().slice(0, 60) || '未命名画板';
        this.activeBoard.name = nextName;
        this.activeBoard.updatedAt = nowIso();
        this.updateActiveBoardOption();
        window.clearTimeout(this.nameSaveTimer);
        this.nameSaveTimer = window.setTimeout(() => this.scheduleSave(0), 260);
    }

    handleToolbarClick(event) {
        const button = event.target.closest('[data-whiteboard-tool], [data-whiteboard-shape], [data-whiteboard-action]');
        if (!button) return;
        const tool = button.dataset.whiteboardTool;
        const shape = button.dataset.whiteboardShape;
        const action = button.dataset.whiteboardAction;
        if (tool) {
            this.setTool(tool);
            return;
        }
        if (shape) {
            this.settings.shapeType = shape;
            this.setTool('shape');
            return;
        }
        if (action) this.handleAction(action);
    }

    handleAction(action) {
        switch (action) {
            case 'new-board':
                this.createNewBoard();
                break;
            case 'undo':
                this.undo();
                break;
            case 'redo':
                this.redo();
                break;
            case 'zoom-in':
                this.zoomBy(1.12);
                break;
            case 'zoom-out':
                this.zoomBy(1 / 1.12);
                break;
            case 'reset-view':
                this.resetView();
                break;
            case 'clear':
                this.clearBoard();
                break;
        }
    }

    setTool(tool) {
        if (!['hand', 'brush', 'text', 'shape'].includes(tool)) return;
        if (tool !== 'text') this.commitTextEditor();
        this.settings.tool = tool;
        this.updateToolState();
        this.scheduleSave();
    }

    updateToolState() {
        if (this.rootEl) this.rootEl.dataset.tool = this.settings.tool;
        this.toolbarEl?.querySelectorAll('[data-whiteboard-tool], [data-whiteboard-shape]').forEach((button) => {
            const tool = button.dataset.whiteboardTool;
            const shape = button.dataset.whiteboardShape;
            const active = tool
                ? this.settings.tool === tool
                : this.settings.tool === 'shape' && this.settings.shapeType === shape;
            button.classList.toggle('is-active', active);
            button.setAttribute('aria-pressed', active ? 'true' : 'false');
        });
    }

    createNewBoard() {
        this.commitTextEditor();
        this.saveNow();
        const board = createBoard(`${this.context.materialName || '课程材料'} 白板`);
        this.state.boards.unshift(board);
        this.activeBoard = board;
        this.state.activeBoardId = board.id;
        this.viewport = normalizeViewport(board.viewport);
        this.undoStack = [];
        this.redoStack = [];
        this.updateBoardControls();
        this.updateUndoRedoButtons();
        this.updateGridPosition();
        this.clearDraftCanvas();
        this.scheduleRender(true);
        this.scheduleSave(0);
        showToast('已新建画板', 'success', 1800);
    }

    selectBoard(boardId) {
        const nextBoard = this.state.boards.find((board) => board.id === boardId);
        if (!nextBoard || nextBoard.id === this.activeBoard?.id) return;
        this.commitTextEditor();
        this.saveNow();
        this.activeBoard = nextBoard;
        this.state.activeBoardId = nextBoard.id;
        this.viewport = normalizeViewport(nextBoard.viewport);
        this.undoStack = [];
        this.redoStack = [];
        this.updateBoardControls();
        this.updateUndoRedoButtons();
        this.updateGridPosition();
        this.clearDraftCanvas();
        this.scheduleRender(true);
        this.scheduleSave(0);
    }

    pushUndoSnapshot() {
        this.undoStack.push(cloneElements(this.activeBoard.elements));
        if (this.undoStack.length > UNDO_LIMIT) this.undoStack.shift();
        this.redoStack = [];
        this.updateUndoRedoButtons();
    }

    undo() {
        if (!this.undoStack.length || !this.activeBoard) return;
        this.commitTextEditor();
        this.redoStack.push(cloneElements(this.activeBoard.elements));
        this.activeBoard.elements = this.undoStack.pop();
        this.activeBoard.updatedAt = nowIso();
        this.updateUndoRedoButtons();
        this.scheduleRender(true);
        this.scheduleSave();
    }

    redo() {
        if (!this.redoStack.length || !this.activeBoard) return;
        this.commitTextEditor();
        this.undoStack.push(cloneElements(this.activeBoard.elements));
        this.activeBoard.elements = this.redoStack.pop();
        this.activeBoard.updatedAt = nowIso();
        this.updateUndoRedoButtons();
        this.scheduleRender(true);
        this.scheduleSave();
    }

    updateUndoRedoButtons() {
        const undoButton = this.toolbarEl?.querySelector('[data-whiteboard-action="undo"]');
        const redoButton = this.toolbarEl?.querySelector('[data-whiteboard-action="redo"]');
        if (undoButton) undoButton.disabled = !this.undoStack.length;
        if (redoButton) redoButton.disabled = !this.redoStack.length;
    }

    clearBoard() {
        if (!this.activeBoard || !this.activeBoard.elements.length) return;
        const confirmed = window.confirm('确定清空当前画板内容吗？此操作可立即撤销。');
        if (!confirmed) return;
        this.commitTextEditor();
        this.pushUndoSnapshot();
        this.activeBoard.elements = [];
        this.activeBoard.updatedAt = nowIso();
        this.clearDraftCanvas();
        this.scheduleRender(true);
        this.scheduleSave();
        showToast('当前画板已清空', 'success', 1800);
    }

    open() {
        if (this.isOpen || !this.rootEl) return;
        this.isOpen = true;
        window.clearTimeout(this.closeTimer);
        this.rootEl.hidden = false;
        this.rootEl.setAttribute('aria-hidden', 'false');
        this.previousBodyOverflow = document.body.style.overflow;
        document.body.style.overflow = 'hidden';
        this.setFabOpenState(true);
        window.requestAnimationFrame(() => {
            this.rootEl?.classList.add('is-open');
            this.resizeCanvases();
            this.scheduleRender(true);
        });
    }

    close() {
        if (!this.isOpen || !this.rootEl) return;
        this.commitTextEditor();
        this.finishPointerState();
        this.isOpen = false;
        this.rootEl.classList.remove('is-open', 'is-panning');
        this.rootEl.setAttribute('aria-hidden', 'true');
        document.body.style.overflow = this.previousBodyOverflow || '';
        this.setFabOpenState(false);
        this.saveNow();
        window.clearTimeout(this.closeTimer);
        this.closeTimer = window.setTimeout(() => {
            if (!this.isOpen && this.rootEl) this.rootEl.hidden = true;
        }, 190);
    }

    toggleOpen() {
        if (this.isOpen) this.close();
        else this.open();
    }

    setFabOpenState(open) {
        if (!this.fabEl) return;
        this.fabEl.classList.toggle('is-open', open);
        this.fabEl.setAttribute('aria-pressed', open ? 'true' : 'false');
        this.fabEl.setAttribute('aria-label', open ? '关闭讲课白板' : '打开讲课白板');
        this.fabEl.title = open ? '关闭讲课白板' : '讲课白板';
        this.fabEl.innerHTML = open ? ICONS.close : ICONS.board;
    }

    handleFabClick(event) {
        if (this.ignoreNextFabClick) {
            event.preventDefault();
            this.ignoreNextFabClick = false;
            return;
        }
        this.toggleOpen();
    }

    handleFabPointerDown(event) {
        if (event.button !== 0 || !this.fabEl) return;
        const rect = this.fabEl.getBoundingClientRect();
        this.fabDrag = {
            pointerId: event.pointerId,
            startX: event.clientX,
            startY: event.clientY,
            startLeft: rect.left,
            startTop: rect.top,
            moved: false,
        };
        try {
            this.fabEl.setPointerCapture(event.pointerId);
        } catch {
            // Pointer capture is optional.
        }
    }

    handleFabPointerMove(event) {
        if (!this.fabDrag || this.fabDrag.pointerId !== event.pointerId || !this.fabEl) return;
        const dx = event.clientX - this.fabDrag.startX;
        const dy = event.clientY - this.fabDrag.startY;
        if (!this.fabDrag.moved && Math.hypot(dx, dy) > 4) {
            this.fabDrag.moved = true;
            this.fabEl.classList.add('is-dragging');
        }
        if (!this.fabDrag.moved) return;
        const size = this.fabEl.offsetWidth || 62;
        const left = clamp(this.fabDrag.startLeft + dx, 8, window.innerWidth - size - 8);
        const top = clamp(this.fabDrag.startTop + dy, 8, window.innerHeight - size - 8);
        this.fabEl.style.left = `${left}px`;
        this.fabEl.style.top = `${top}px`;
        this.fabEl.style.right = 'auto';
        this.fabEl.style.bottom = 'auto';
        event.preventDefault();
    }

    handleFabPointerUp(event) {
        if (!this.fabDrag || this.fabDrag.pointerId !== event.pointerId) return;
        const moved = this.fabDrag.moved;
        this.finishFabDrag(event);
        if (moved) {
            this.ignoreNextFabClick = true;
            this.saveFabPosition();
        }
    }

    handleFabPointerCancel(event) {
        this.finishFabDrag(event);
    }

    finishFabDrag(event) {
        if (!this.fabEl || !this.fabDrag) return;
        try {
            if (event && this.fabEl.hasPointerCapture?.(event.pointerId)) {
                this.fabEl.releasePointerCapture(event.pointerId);
            }
        } catch {
            // Ignore release failures.
        }
        this.fabEl.classList.remove('is-dragging');
        this.fabDrag = null;
    }

    applyFabPosition() {
        if (!this.fabEl) return;
        try {
            const rawValue = window.localStorage.getItem(this.fabStorageKey);
            if (!rawValue) return;
            const position = JSON.parse(rawValue);
            const size = this.fabEl.offsetWidth || 62;
            const left = clamp(toFiniteNumber(position.left, window.innerWidth - size - 20), 8, window.innerWidth - size - 8);
            const top = clamp(toFiniteNumber(position.top, 20), 8, window.innerHeight - size - 8);
            this.fabEl.style.left = `${left}px`;
            this.fabEl.style.top = `${top}px`;
            this.fabEl.style.right = 'auto';
            this.fabEl.style.bottom = 'auto';
        } catch (error) {
            console.warn('Failed to load whiteboard button position:', error);
        }
    }

    saveFabPosition() {
        if (!this.fabEl) return;
        const rect = this.fabEl.getBoundingClientRect();
        try {
            window.localStorage.setItem(this.fabStorageKey, JSON.stringify({
                left: Math.round(rect.left),
                top: Math.round(rect.top),
            }));
        } catch (error) {
            console.warn('Failed to save whiteboard button position:', error);
        }
    }

    handleResize() {
        this.clampFabToViewport();
        if (this.isOpen) {
            this.resizeCanvases();
            this.updateGridPosition();
            this.scheduleRender(true);
        }
    }

    clampFabToViewport() {
        if (!this.fabEl || !this.fabEl.style.left) return;
        const rect = this.fabEl.getBoundingClientRect();
        const size = this.fabEl.offsetWidth || rect.width || 62;
        const left = clamp(rect.left, 8, window.innerWidth - size - 8);
        const top = clamp(rect.top, 8, window.innerHeight - size - 8);
        this.fabEl.style.left = `${left}px`;
        this.fabEl.style.top = `${top}px`;
        this.fabEl.style.right = 'auto';
        this.fabEl.style.bottom = 'auto';
        this.saveFabPosition();
    }

    handleKeydown(event) {
        if (!this.isOpen) return;
        if (this.textEditor?.element && document.activeElement === this.textEditor.element) return;
        const target = event.target;
        const isTyping = target instanceof HTMLInputElement
            || target instanceof HTMLTextAreaElement
            || target instanceof HTMLSelectElement
            || target?.isContentEditable;
        if (isTyping) return;

        if (event.key === 'Escape') {
            event.preventDefault();
            this.close();
            return;
        }

        const key = event.key.toLowerCase();
        if ((event.ctrlKey || event.metaKey) && key === 'z') {
            event.preventDefault();
            if (event.shiftKey) this.redo();
            else this.undo();
            return;
        }
        if ((event.ctrlKey || event.metaKey) && key === 'y') {
            event.preventDefault();
            this.redo();
        }
    }

    resizeCanvases() {
        if (!this.stageEl || !this.canvasEl || !this.draftCanvasEl) return;
        const rect = this.stageEl.getBoundingClientRect();
        const width = Math.max(1, Math.round(rect.width));
        const height = Math.max(1, Math.round(rect.height));
        const dpr = clamp(window.devicePixelRatio || 1, 1, 2.5);
        if (this.canvasWidth === width && this.canvasHeight === height && this.dpr === dpr) return;

        this.canvasWidth = width;
        this.canvasHeight = height;
        this.dpr = dpr;
        [this.canvasEl, this.draftCanvasEl].forEach((canvas) => {
            canvas.width = Math.round(width * dpr);
            canvas.height = Math.round(height * dpr);
            canvas.style.width = `${width}px`;
            canvas.style.height = `${height}px`;
        });
    }

    setScreenTransform(ctx) {
        ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    }

    scheduleRender(force = false) {
        if (!this.isOpen && !force) return;
        if (this.renderFrame !== null) return;
        this.renderFrame = window.requestAnimationFrame(() => {
            this.renderFrame = null;
            this.drawMainCanvas();
        });
    }

    drawMainCanvas() {
        if (!this.ctx || !this.canvasWidth || !this.canvasHeight) return;
        const ctx = this.ctx;
        this.setScreenTransform(ctx);
        ctx.clearRect(0, 0, this.canvasWidth, this.canvasHeight);
        ctx.save();
        ctx.translate(this.viewport.x, this.viewport.y);
        ctx.scale(this.viewport.scale, this.viewport.scale);
        for (const element of this.activeBoard.elements || []) {
            this.drawElement(ctx, element);
        }
        ctx.restore();
    }

    clearDraftCanvas() {
        if (!this.draftCtx || !this.canvasWidth || !this.canvasHeight) return;
        this.setScreenTransform(this.draftCtx);
        this.draftCtx.clearRect(0, 0, this.canvasWidth, this.canvasHeight);
    }

    drawScreenSegment(from, to, color, size) {
        if (!this.draftCtx) return;
        const ctx = this.draftCtx;
        this.setScreenTransform(ctx);
        ctx.save();
        ctx.strokeStyle = color;
        ctx.lineWidth = size;
        ctx.lineCap = 'round';
        ctx.lineJoin = 'round';
        ctx.beginPath();
        ctx.moveTo(from.x, from.y);
        ctx.lineTo(to.x, to.y);
        ctx.stroke();
        ctx.restore();
    }

    drawScreenDot(point, color, size) {
        if (!this.draftCtx) return;
        const ctx = this.draftCtx;
        this.setScreenTransform(ctx);
        ctx.save();
        ctx.fillStyle = color;
        ctx.beginPath();
        ctx.arc(point.x, point.y, Math.max(size / 2, 1), 0, Math.PI * 2);
        ctx.fill();
        ctx.restore();
    }

    scheduleDraftShapeRender() {
        if (this.draftFrame !== null) return;
        this.draftFrame = window.requestAnimationFrame(() => {
            this.draftFrame = null;
            this.drawDraftShape();
        });
    }

    drawDraftShape() {
        if (!this.activeShape || !this.draftCtx) return;
        this.clearDraftCanvas();
        const ctx = this.draftCtx;
        this.setScreenTransform(ctx);
        ctx.save();
        ctx.translate(this.viewport.x, this.viewport.y);
        ctx.scale(this.viewport.scale, this.viewport.scale);
        this.drawElement(ctx, this.activeShape, { draft: true });
        ctx.restore();
    }

    drawElement(ctx, element, options = {}) {
        if (!element || typeof element !== 'object') return;
        if (element.type === 'stroke') {
            this.drawStroke(ctx, element, options);
            return;
        }
        if (element.type === 'shape') {
            this.drawShape(ctx, element, options);
            return;
        }
        if (element.type === 'text') this.drawText(ctx, element, options);
    }

    drawStroke(ctx, element, options = {}) {
        const points = Array.isArray(element.points) ? element.points : [];
        if (!points.length) return;
        const size = Math.max(toFiniteNumber(element.size, 2), 0.4);
        ctx.save();
        ctx.globalAlpha = options.draft ? 0.82 : 1;
        ctx.strokeStyle = String(element.color || DEFAULT_SETTINGS.brushColor);
        ctx.fillStyle = ctx.strokeStyle;
        ctx.lineWidth = size;
        ctx.lineCap = 'round';
        ctx.lineJoin = 'round';

        if (points.length === 1) {
            ctx.beginPath();
            ctx.arc(points[0].x, points[0].y, size / 2, 0, Math.PI * 2);
            ctx.fill();
            ctx.restore();
            return;
        }

        ctx.beginPath();
        ctx.moveTo(points[0].x, points[0].y);
        if (points.length === 2) {
            ctx.lineTo(points[1].x, points[1].y);
        } else {
            for (let index = 1; index < points.length - 1; index += 1) {
                const current = points[index];
                const next = points[index + 1];
                ctx.quadraticCurveTo(current.x, current.y, (current.x + next.x) / 2, (current.y + next.y) / 2);
            }
            const last = points[points.length - 1];
            ctx.lineTo(last.x, last.y);
        }
        ctx.stroke();
        ctx.restore();
    }

    drawShape(ctx, element, options = {}) {
        const box = this.getShapeBox(element);
        if (box.width < 0.5 || box.height < 0.5) return;
        const color = String(element.color || DEFAULT_SETTINGS.brushColor);
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
                this.roundedRectPath(ctx, box.x, box.y, box.width, box.height, Math.min(box.width, box.height) * 0.18);
                break;
            case 'diamond':
                ctx.moveTo(box.x + box.width / 2, box.y);
                ctx.lineTo(box.x + box.width, box.y + box.height / 2);
                ctx.lineTo(box.x + box.width / 2, box.y + box.height);
                ctx.lineTo(box.x, box.y + box.height / 2);
                ctx.closePath();
                break;
            case 'square':
            case 'rectangle':
            default:
                ctx.rect(box.x, box.y, box.width, box.height);
                break;
        }
        ctx.fill();
        ctx.stroke();
        ctx.restore();
    }

    drawText(ctx, element, options = {}) {
        const text = String(element.text || '');
        if (!text.trim()) return;
        const fontSize = Math.max(toFiniteNumber(element.fontSize, 24), 4);
        const lines = text.replace(/\r\n/g, '\n').split('\n');
        const lineHeight = fontSize * 1.28;
        ctx.save();
        ctx.globalAlpha = options.draft ? 0.82 : 1;
        ctx.fillStyle = String(element.color || DEFAULT_SETTINGS.textColor);
        ctx.font = `${fontSize}px ${CANVAS_FONT_STACK}`;
        ctx.textBaseline = 'top';
        ctx.textAlign = 'left';
        lines.forEach((line, index) => {
            ctx.fillText(line || ' ', element.x, element.y + index * lineHeight);
        });
        ctx.restore();
    }

    roundedRectPath(ctx, x, y, width, height, radius) {
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

    getShapeBox(element) {
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
        return {
            x: Math.min(x1, x2),
            y: Math.min(y1, y2),
            width: Math.abs(x2 - x1),
            height: Math.abs(y2 - y1),
        };
    }

    getStagePoint(event) {
        const rect = this.stageEl.getBoundingClientRect();
        return {
            x: event.clientX - rect.left,
            y: event.clientY - rect.top,
        };
    }

    screenToWorld(point) {
        return {
            x: (point.x - this.viewport.x) / this.viewport.scale,
            y: (point.y - this.viewport.y) / this.viewport.scale,
        };
    }

    worldToScreen(point) {
        return {
            x: point.x * this.viewport.scale + this.viewport.x,
            y: point.y * this.viewport.scale + this.viewport.y,
        };
    }

    handleStagePointerDown(event) {
        if (!this.isOpen || event.button !== 0 || event.target.closest('.teacher-whiteboard-text-editor')) return;
        this.resizeCanvases();
        this.commitTextEditor();
        const screenPoint = this.getStagePoint(event);
        const worldPoint = this.screenToWorld(screenPoint);
        this.activePointer = event.pointerId;

        if (this.settings.tool === 'hand') {
            this.activePan = {
                startX: event.clientX,
                startY: event.clientY,
                viewportX: this.viewport.x,
                viewportY: this.viewport.y,
            };
            this.rootEl?.classList.add('is-panning');
        } else if (this.settings.tool === 'brush') {
            this.activeStroke = {
                id: makeId('stroke'),
                type: 'stroke',
                color: this.settings.brushColor,
                size: this.settings.brushSize / this.viewport.scale,
                points: [worldPoint],
                createdAt: nowIso(),
            };
            this.clearDraftCanvas();
            this.drawScreenDot(screenPoint, this.settings.brushColor, this.settings.brushSize);
        } else if (this.settings.tool === 'shape') {
            this.activeShape = {
                id: makeId('shape'),
                type: 'shape',
                shape: this.settings.shapeType,
                color: this.settings.brushColor,
                size: this.settings.brushSize / this.viewport.scale,
                x1: worldPoint.x,
                y1: worldPoint.y,
                x2: worldPoint.x,
                y2: worldPoint.y,
                createdAt: nowIso(),
            };
            this.clearDraftCanvas();
        } else if (this.settings.tool === 'text') {
            this.activePointer = null;
            this.openTextEditor(worldPoint);
            return;
        }

        try {
            this.stageEl.setPointerCapture(event.pointerId);
        } catch {
            // Pointer capture is optional.
        }
        event.preventDefault();
    }

    handleStagePointerMove(event) {
        if (!this.activePointer || this.activePointer !== event.pointerId) return;
        if (this.activePan) {
            this.viewport.x = this.activePan.viewportX + (event.clientX - this.activePan.startX);
            this.viewport.y = this.activePan.viewportY + (event.clientY - this.activePan.startY);
            this.activeBoard.viewport = { ...this.viewport };
            this.updateGridPosition();
            this.scheduleRender(true);
            event.preventDefault();
            return;
        }
        if (this.activeStroke) {
            const pointerEvents = typeof event.getCoalescedEvents === 'function' ? event.getCoalescedEvents() : [event];
            for (const pointerEvent of pointerEvents) this.addStrokePoint(this.getStagePoint(pointerEvent));
            event.preventDefault();
            return;
        }
        if (this.activeShape) {
            const point = this.screenToWorld(this.getStagePoint(event));
            this.activeShape.x2 = point.x;
            this.activeShape.y2 = point.y;
            this.scheduleDraftShapeRender();
            event.preventDefault();
        }
    }

    handleStagePointerUp(event) {
        if (!this.activePointer || this.activePointer !== event.pointerId) return;
        this.handleStagePointerMove(event);
        this.finishDrawing(event);
    }

    handleStagePointerCancel(event) {
        if (this.activePointer !== event.pointerId) return;
        this.finishPointerState(event);
        this.clearDraftCanvas();
    }

    addStrokePoint(screenPoint) {
        if (!this.activeStroke) return;
        const points = this.activeStroke.points;
        const lastWorld = points[points.length - 1];
        const lastScreen = this.worldToScreen(lastWorld);
        if (distance(lastScreen, screenPoint) < 0.8) return;
        const worldPoint = this.screenToWorld(screenPoint);
        points.push(worldPoint);
        this.drawScreenSegment(lastScreen, screenPoint, this.activeStroke.color, this.settings.brushSize);
    }

    finishDrawing(event) {
        if (this.activePan) {
            this.activeBoard.viewport = { ...this.viewport };
            this.activeBoard.updatedAt = nowIso();
            this.scheduleSave();
        }
        if (this.activeStroke) {
            const stroke = this.activeStroke;
            if (stroke.points.length) {
                this.pushUndoSnapshot();
                this.activeBoard.elements.push(stroke);
                this.activeBoard.updatedAt = nowIso();
                this.clearDraftCanvas();
                this.scheduleRender(true);
                this.scheduleSave();
            }
        }
        if (this.activeShape) {
            const shape = this.activeShape;
            const start = this.worldToScreen({ x: shape.x1, y: shape.y1 });
            const end = this.worldToScreen({ x: shape.x2, y: shape.y2 });
            if (distance(start, end) > 5) {
                this.pushUndoSnapshot();
                this.activeBoard.elements.push(shape);
                this.activeBoard.updatedAt = nowIso();
                this.scheduleSave();
            }
            this.clearDraftCanvas();
            this.scheduleRender(true);
        }
        this.finishPointerState(event);
    }

    finishPointerState(event = null) {
        if (this.stageEl && event) {
            try {
                if (this.stageEl.hasPointerCapture?.(event.pointerId)) {
                    this.stageEl.releasePointerCapture(event.pointerId);
                }
            } catch {
                // Ignore release failures.
            }
        }
        this.rootEl?.classList.remove('is-panning');
        this.activePointer = null;
        this.activeStroke = null;
        this.activeShape = null;
        this.activePan = null;
    }

    handleWheel(event) {
        if (!this.isOpen || event.ctrlKey || event.metaKey) return;
        event.preventDefault();
        const factor = event.deltaY > 0 ? 0.94 : 1.06;
        this.zoomBy(factor, this.getStagePoint(event));
    }

    zoomBy(factor, focalScreenPoint = null) {
        const currentScale = this.viewport.scale;
        const nextScale = clamp(currentScale * factor, MIN_ZOOM, MAX_ZOOM);
        if (Math.abs(nextScale - currentScale) < 0.001) return;
        const focal = focalScreenPoint || {
            x: this.canvasWidth / 2,
            y: this.canvasHeight / 2,
        };
        const before = this.screenToWorld(focal);
        this.viewport.scale = nextScale;
        this.viewport.x = focal.x - before.x * nextScale;
        this.viewport.y = focal.y - before.y * nextScale;
        this.activeBoard.viewport = { ...this.viewport };
        this.updateGridPosition();
        this.scheduleRender(true);
        this.scheduleSave();
    }

    resetView() {
        this.viewport = createViewport();
        this.activeBoard.viewport = { ...this.viewport };
        this.updateGridPosition();
        this.scheduleRender(true);
        this.scheduleSave();
    }

    openTextEditor(worldPoint) {
        this.closeTextEditor(false);
        const screenPoint = this.worldToScreen(worldPoint);
        const editor = document.createElement('textarea');
        editor.className = 'teacher-whiteboard-text-editor';
        editor.rows = 2;
        editor.placeholder = '输入文字';
        editor.style.left = `${clamp(screenPoint.x, 8, Math.max(8, this.canvasWidth - 220))}px`;
        editor.style.top = `${clamp(screenPoint.y, 8, Math.max(8, this.canvasHeight - 80))}px`;
        editor.style.color = this.settings.textColor;
        editor.style.fontSize = `${this.settings.fontSize}px`;
        editor.addEventListener('pointerdown', (pointerEvent) => pointerEvent.stopPropagation());
        editor.addEventListener('keydown', (keyEvent) => {
            if (keyEvent.key === 'Escape') {
                keyEvent.preventDefault();
                this.closeTextEditor(false);
                return;
            }
            if (keyEvent.key === 'Enter' && !keyEvent.shiftKey) {
                keyEvent.preventDefault();
                this.commitTextEditor();
            }
        });
        editor.addEventListener('blur', () => {
            window.setTimeout(() => this.commitTextEditor(), 0);
        }, { once: true });

        this.stageEl.appendChild(editor);
        this.textEditor = {
            element: editor,
            worldPoint,
            fontSize: this.settings.fontSize / this.viewport.scale,
            color: this.settings.textColor,
        };
        window.requestAnimationFrame(() => editor.focus());
    }

    commitTextEditor() {
        if (!this.textEditor?.element) return;
        const editor = this.textEditor.element;
        const text = editor.value.trim();
        const data = this.textEditor;
        this.closeTextEditor(false);
        if (!text) return;
        this.pushUndoSnapshot();
        this.activeBoard.elements.push({
            id: makeId('text'),
            type: 'text',
            text,
            x: data.worldPoint.x,
            y: data.worldPoint.y,
            color: data.color,
            fontSize: data.fontSize,
            createdAt: nowIso(),
        });
        this.activeBoard.updatedAt = nowIso();
        this.scheduleRender(true);
        this.scheduleSave();
    }

    closeTextEditor() {
        if (!this.textEditor?.element) return;
        const editor = this.textEditor.element;
        this.textEditor = null;
        editor.remove();
    }
}

class ExamDrawingWhiteboard {
    constructor(options = {}) {
        this.rootId = options.rootId || 'exam-drawing-whiteboard-root';
        this.rootEl = null;
        this.stageEl = null;
        this.canvasEl = null;
        this.ctx = null;
        this.controls = {};
        this.dpr = 1;
        this.canvasWidth = 0;
        this.canvasHeight = 0;
        this.isOpen = false;
        this.isDrawing = false;
        this.hasContent = false;
        this.history = [];
        this.redoStack = [];
        this.maxHistory = 24;
        this.resolveOpen = null;
        this.context = {};
        this.settings = {
            brushColor: DEFAULT_SETTINGS.brushColor,
            brushSize: DEFAULT_SETTINGS.brushSize,
            tool: 'brush',
        };
        this.boundResize = () => this.resizeCanvas({ preserve: true });
        this.boundKeydown = (event) => this.handleKeydown(event);
    }

    init() {
        if (this.rootEl) return this;
        this.buildDom();
        this.cacheDom();
        this.bindEvents();
        return this;
    }

    buildDom() {
        const root = document.createElement('div');
        root.id = this.rootId;
        root.className = 'teacher-whiteboard-root exam-drawing-whiteboard-root';
        root.hidden = true;
        root.setAttribute('aria-hidden', 'true');
        root.dataset.tool = 'brush';
        root.innerHTML = `
            <div class="teacher-whiteboard-stage exam-drawing-stage" id="${this.rootId}-stage">
                <div class="teacher-whiteboard-canvas-layer">
                    <canvas id="${this.rootId}-canvas"></canvas>
                </div>
            </div>
            <div class="teacher-whiteboard-toolbar exam-drawing-toolbar" role="toolbar" aria-label="答题绘图板工具">
                <div class="teacher-whiteboard-group is-board exam-drawing-title">
                    <strong id="${this.rootId}-title">题目附图</strong>
                    <span id="${this.rootId}-subtitle"></span>
                </div>
                <div class="teacher-whiteboard-group is-tools" aria-label="工具">
                    <button type="button" class="teacher-whiteboard-btn is-active" data-exam-drawing-tool="brush" title="画笔" aria-label="画笔">${ICONS.pen}</button>
                    <button type="button" class="teacher-whiteboard-btn" data-exam-drawing-tool="eraser" title="橡皮擦" aria-label="橡皮擦">${ICONS.clear}</button>
                </div>
                <div class="teacher-whiteboard-group">
                    <label class="teacher-whiteboard-control" title="画笔颜色"><span>画笔</span><input id="${this.rootId}-brush-color" class="teacher-whiteboard-color" type="color" aria-label="画笔颜色"></label>
                    <label class="teacher-whiteboard-control" title="笔触粗细"><input id="${this.rootId}-brush-size" class="teacher-whiteboard-range" type="range" min="1" max="32" step="1" aria-label="笔触粗细"><output id="${this.rootId}-brush-size-value" class="teacher-whiteboard-value"></output></label>
                </div>
                <div class="teacher-whiteboard-group is-actions" aria-label="操作">
                    <button type="button" class="teacher-whiteboard-btn" data-exam-drawing-action="undo" title="撤销" aria-label="撤销">${ICONS.undo}</button>
                    <button type="button" class="teacher-whiteboard-btn" data-exam-drawing-action="redo" title="重做" aria-label="重做">${ICONS.redo}</button>
                    <button type="button" class="teacher-whiteboard-btn" data-exam-drawing-action="clear" title="清空" aria-label="清空">${ICONS.clear}</button>
                    <button type="button" class="teacher-whiteboard-btn" data-exam-drawing-action="cancel" title="关闭" aria-label="关闭">${ICONS.close}</button>
                </div>
                <div class="teacher-whiteboard-group is-actions" aria-label="保存">
                    <button type="button" class="btn btn-primary btn-sm" data-exam-drawing-action="save">保存附图</button>
                </div>
            </div>`;
        document.body.append(root);
    }

    cacheDom() {
        this.rootEl = document.getElementById(this.rootId);
        this.stageEl = document.getElementById(`${this.rootId}-stage`);
        this.canvasEl = document.getElementById(`${this.rootId}-canvas`);
        this.ctx = this.canvasEl?.getContext('2d', { alpha: true, desynchronized: true })
            || this.canvasEl?.getContext('2d');
        this.controls = {
            title: document.getElementById(`${this.rootId}-title`),
            subtitle: document.getElementById(`${this.rootId}-subtitle`),
            brushColor: document.getElementById(`${this.rootId}-brush-color`),
            brushSize: document.getElementById(`${this.rootId}-brush-size`),
            brushSizeValue: document.getElementById(`${this.rootId}-brush-size-value`),
            undo: this.rootEl?.querySelector('[data-exam-drawing-action="undo"]'),
            redo: this.rootEl?.querySelector('[data-exam-drawing-action="redo"]'),
        };
    }

    bindEvents() {
        this.controls.brushColor.value = this.settings.brushColor;
        this.controls.brushSize.value = String(this.settings.brushSize);
        this.updateRangeLabel();
        this.updateToolButtons();
        this.updateHistoryButtons();

        this.controls.brushColor?.addEventListener('input', () => {
            this.settings.brushColor = this.controls.brushColor.value || DEFAULT_SETTINGS.brushColor;
        });
        this.controls.brushSize?.addEventListener('input', () => {
            this.settings.brushSize = clamp(Number(this.controls.brushSize.value), 1, 32);
            this.updateRangeLabel();
        });
        this.rootEl?.addEventListener('click', (event) => this.handleToolbarClick(event));
        this.stageEl?.addEventListener('pointerdown', (event) => this.handlePointerDown(event));
        this.stageEl?.addEventListener('pointermove', (event) => this.handlePointerMove(event));
        this.stageEl?.addEventListener('pointerup', (event) => this.handlePointerUp(event));
        this.stageEl?.addEventListener('pointercancel', (event) => this.handlePointerUp(event));
    }

    open(context = {}) {
        this.init();
        this.context = { ...context };
        this.isOpen = true;
        this.rootEl.hidden = false;
        this.rootEl.setAttribute('aria-hidden', 'false');
        this.rootEl.classList.add('is-open');
        this.setTitle();
        window.addEventListener('resize', this.boundResize);
        document.addEventListener('keydown', this.boundKeydown);
        window.requestAnimationFrame(async () => {
            this.resizeCanvas({ preserve: false });
            this.clearCanvas({ silent: true });
            if (context.dataUrl || context.imageUrl) {
                await this.loadImage(context.dataUrl || context.imageUrl);
            }
            this.history = [];
            this.redoStack = [];
            this.updateHistoryButtons();
        });

        return new Promise((resolve) => {
            this.resolveOpen = resolve;
        });
    }

    close(result = null) {
        if (!this.isOpen) return;
        this.isOpen = false;
        this.rootEl.classList.remove('is-open');
        this.rootEl.setAttribute('aria-hidden', 'true');
        window.removeEventListener('resize', this.boundResize);
        document.removeEventListener('keydown', this.boundKeydown);
        const resolver = this.resolveOpen;
        this.resolveOpen = null;
        window.setTimeout(() => {
            if (!this.isOpen) this.rootEl.hidden = true;
        }, 180);
        if (resolver) resolver(result);
    }

    setTitle() {
        const questionId = this.context.questionId ? `第 ${this.context.questionId} 题` : '题目附图';
        const paperTitle = this.context.paperTitle || '';
        if (this.controls.title) this.controls.title.textContent = questionId;
        if (this.controls.subtitle) {
            this.controls.subtitle.textContent = paperTitle
                ? `${paperTitle} · ${this.context.fileName || ''}`
                : (this.context.fileName || '');
        }
    }

    resizeCanvas({ preserve = true } = {}) {
        if (!this.canvasEl || !this.stageEl || !this.ctx) return;
        const snapshot = preserve && this.hasContent ? this.canvasEl.toDataURL('image/png') : '';
        const rect = this.stageEl.getBoundingClientRect();
        const width = Math.max(320, Math.round(rect.width || window.innerWidth));
        const height = Math.max(240, Math.round(rect.height || window.innerHeight));
        const dpr = clamp(window.devicePixelRatio || 1, 1, 2.5);
        this.canvasWidth = width;
        this.canvasHeight = height;
        this.dpr = dpr;
        this.canvasEl.width = Math.round(width * dpr);
        this.canvasEl.height = Math.round(height * dpr);
        this.canvasEl.style.width = `${width}px`;
        this.canvasEl.style.height = `${height}px`;
        this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        this.ctx.lineCap = 'round';
        this.ctx.lineJoin = 'round';
        if (snapshot) this.restoreSnapshot(snapshot);
    }

    getPoint(event) {
        const rect = this.canvasEl.getBoundingClientRect();
        return {
            x: event.clientX - rect.left,
            y: event.clientY - rect.top,
        };
    }

    handlePointerDown(event) {
        if (!this.isOpen || event.button !== 0) return;
        event.preventDefault();
        this.canvasEl.setPointerCapture?.(event.pointerId);
        this.pushHistory();
        this.isDrawing = true;
        const point = this.getPoint(event);
        this.ctx.beginPath();
        this.ctx.moveTo(point.x, point.y);
        this.ctx.lineTo(point.x, point.y);
        this.applyStrokeStyle();
        this.ctx.stroke();
        this.hasContent = true;
    }

    handlePointerMove(event) {
        if (!this.isDrawing) return;
        event.preventDefault();
        const point = this.getPoint(event);
        this.applyStrokeStyle();
        this.ctx.lineTo(point.x, point.y);
        this.ctx.stroke();
        this.hasContent = true;
    }

    handlePointerUp(event) {
        if (!this.isDrawing) return;
        event.preventDefault();
        this.isDrawing = false;
        this.ctx.closePath();
        this.canvasEl.releasePointerCapture?.(event.pointerId);
        this.updateHistoryButtons();
    }

    applyStrokeStyle() {
        if (this.settings.tool === 'eraser') {
            this.ctx.globalCompositeOperation = 'destination-out';
            this.ctx.strokeStyle = 'rgba(0,0,0,1)';
            this.ctx.lineWidth = Math.max(this.settings.brushSize * 2.2, 8);
        } else {
            this.ctx.globalCompositeOperation = 'source-over';
            this.ctx.strokeStyle = this.settings.brushColor;
            this.ctx.lineWidth = this.settings.brushSize;
        }
    }

    handleToolbarClick(event) {
        const toolButton = event.target.closest('[data-exam-drawing-tool]');
        if (toolButton) {
            this.settings.tool = toolButton.dataset.examDrawingTool || 'brush';
            this.rootEl.dataset.tool = this.settings.tool;
            this.updateToolButtons();
            return;
        }

        const actionButton = event.target.closest('[data-exam-drawing-action]');
        if (!actionButton) return;
        const action = actionButton.dataset.examDrawingAction;
        if (action === 'undo') this.undo();
        if (action === 'redo') this.redo();
        if (action === 'clear') this.clearCanvas();
        if (action === 'cancel') this.close(null);
        if (action === 'save') this.saveDrawing();
    }

    handleKeydown(event) {
        if (!this.isOpen) return;
        if (event.key === 'Escape') {
            event.preventDefault();
            this.close(null);
        }
        if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z') {
            event.preventDefault();
            event.shiftKey ? this.redo() : this.undo();
        }
        if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'y') {
            event.preventDefault();
            this.redo();
        }
    }

    updateRangeLabel() {
        if (this.controls.brushSizeValue) this.controls.brushSizeValue.textContent = `${this.settings.brushSize}px`;
    }

    updateToolButtons() {
        this.rootEl?.querySelectorAll('[data-exam-drawing-tool]').forEach((button) => {
            button.classList.toggle('is-active', button.dataset.examDrawingTool === this.settings.tool);
        });
    }

    updateHistoryButtons() {
        if (this.controls.undo) this.controls.undo.disabled = !this.history.length;
        if (this.controls.redo) this.controls.redo.disabled = !this.redoStack.length;
    }

    pushHistory() {
        if (!this.canvasEl) return;
        this.history.push(this.canvasEl.toDataURL('image/png'));
        if (this.history.length > this.maxHistory) this.history.shift();
        this.redoStack = [];
        this.updateHistoryButtons();
    }

    restoreSnapshot(dataUrl) {
        if (!dataUrl || !this.ctx) return;
        const image = new Image();
        image.onload = () => {
            this.ctx.clearRect(0, 0, this.canvasWidth, this.canvasHeight);
            this.ctx.globalCompositeOperation = 'source-over';
            this.ctx.drawImage(image, 0, 0, this.canvasWidth, this.canvasHeight);
            this.hasContent = true;
        };
        image.src = dataUrl;
    }

    undo() {
        if (!this.history.length) return;
        this.redoStack.push(this.canvasEl.toDataURL('image/png'));
        const snapshot = this.history.pop();
        this.restoreSnapshot(snapshot);
        this.updateHistoryButtons();
    }

    redo() {
        if (!this.redoStack.length) return;
        this.history.push(this.canvasEl.toDataURL('image/png'));
        const snapshot = this.redoStack.pop();
        this.restoreSnapshot(snapshot);
        this.updateHistoryButtons();
    }

    clearCanvas({ silent = false } = {}) {
        if (!this.ctx) return;
        if (!silent) this.pushHistory();
        this.ctx.clearRect(0, 0, this.canvasWidth, this.canvasHeight);
        this.hasContent = false;
        this.updateHistoryButtons();
    }

    async loadImage(source) {
        if (!source || !this.ctx) return;
        await new Promise((resolve) => {
            const image = new Image();
            image.onload = () => {
                this.ctx.clearRect(0, 0, this.canvasWidth, this.canvasHeight);
                this.ctx.globalCompositeOperation = 'source-over';
                const scale = Math.min(this.canvasWidth / image.width, this.canvasHeight / image.height, 1);
                const width = image.width * scale;
                const height = image.height * scale;
                const x = (this.canvasWidth - width) / 2;
                const y = (this.canvasHeight - height) / 2;
                this.ctx.drawImage(image, x, y, width, height);
                this.hasContent = true;
                resolve();
            };
            image.onerror = () => resolve();
            image.src = source;
        });
    }

    exportDataUrl() {
        const exportCanvas = document.createElement('canvas');
        exportCanvas.width = Math.round(this.canvasWidth * this.dpr);
        exportCanvas.height = Math.round(this.canvasHeight * this.dpr);
        const exportCtx = exportCanvas.getContext('2d');
        exportCtx.fillStyle = '#ffffff';
        exportCtx.fillRect(0, 0, exportCanvas.width, exportCanvas.height);
        exportCtx.drawImage(this.canvasEl, 0, 0, exportCanvas.width, exportCanvas.height);
        return exportCanvas.toDataURL('image/png');
    }

    saveDrawing() {
        if (!this.hasContent) {
            showToast('请先完成绘图后再保存附图。', 'warning');
            return;
        }
        this.close({
            dataUrl: this.exportDataUrl(),
            width: this.canvasWidth,
            height: this.canvasHeight,
            fileName: this.context.fileName || 'exam-drawing.png',
        });
    }
}

function initTeacherWhiteboard(context = window.MATERIAL_VIEWER_CONTEXT || {}) {
    const app = new TeacherWhiteboard(context);
    app.init();
    if (isTeacherContext(app.context)) {
        window.teacherWhiteboard = app;
    }
    return app;
}

function bootstrap() {
    initTeacherWhiteboard(window.MATERIAL_VIEWER_CONTEXT || {});
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bootstrap, { once: true });
} else {
    bootstrap();
}

function initExamDrawingWhiteboard(options = {}) {
    const app = new ExamDrawingWhiteboard(options);
    app.init();
    window.examDrawingWhiteboard = app;
    return app;
}

export { initTeacherWhiteboard, initExamDrawingWhiteboard };
