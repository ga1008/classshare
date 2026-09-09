/**
 * 讲课白板主类：编排 DOM、指针交互、撤销、面板、本地缓存与线上同步。
 * 设计真源：docs/whiteboard-upgrade-2026-09.md
 */
import { showToast } from '../ui.js';
import {
    CACHE, CAPACITY, ICONS, LIMITS, MAX_CANVAS_PIXELS, MAX_DPR, MAX_ZOOM, MIN_ZOOM, TIMING, TOOLS, UNDO_LIMIT,
} from './constants.js';
import { createMeasureWidth, renderElements } from './renderer.js';
import { RenderCache, screenRectToWorld, shouldRebuild } from './render_cache.js';
import {
    clamp, cloneElements, countInkElements, createBoard, createViewport, isBoardEmpty, nextBoardName,
    normalizeSettings, normalizeViewport, nowIso, sanitizeBoard,
} from './state.js';
import { dropLocalBoard, loadLocalState, loadPerfMode, pruneBoards, savePerfMode, saveLocalState } from './store_local.js';
import { resolveProfile } from './perf_profile.js';
import { createPerfProbe } from './perf_probe.js';
import { RemoteStore } from './store_remote.js';
import { SyncController } from './sync.js';
import { popoverManager } from './popover.js';
import { buildToolbarHtml } from './toolbar.js';
import { fabMixin } from './fab.js';
import { interactionMixin } from './interaction.js';
import { textEditorMixin } from './text_editor.js';
import { createBrushPopover, createTextPopover, createInkPopover, createBackgroundPopover } from './panels/style_popovers.js';
import { createEraserPopover } from './panels/eraser_popover.js';
import { openConfirm } from './panels/confirm_popover.js';
import { createSaveMenu } from './panels/save_menu.js';
import { createHistoryPanel } from './panels/history_panel.js';
import { createExportDialog } from './panels/export_dialog.js';

function normalizeContext(rawContext = {}) {
    return {
        userId: String(rawContext.userId ?? rawContext.user_id ?? 'teacher'),
        userRole: String(rawContext.userRole ?? rawContext.role ?? '').toLowerCase(),
        materialId: String(rawContext.materialId ?? rawContext.material_id ?? 'unknown'),
        materialName: String(rawContext.materialName ?? rawContext.material_name ?? document.title ?? '课程材料'),
    };
}

/** 允许使用白板的角色；放开学生只改这里（后端另有同名常量）。 */
export const WHITEBOARD_ALLOWED_ROLES = new Set(['teacher']);

export function isAllowedContext(context) {
    return WHITEBOARD_ALLOWED_ROLES.has(context.userRole);
}

const TOOL_KEYS = { b: 'brush', e: 'eraser', t: 'text', h: 'hand' };
const SYNC_LABELS = { local: '仅本机', synced: '已线上保存', dirty: '有未同步改动', saving: '保存中', error: '上次保存失败' };

export class TeacherWhiteboard {
    constructor(rawContext = {}) {
        this.context = normalizeContext(rawContext);
        this.state = null;
        this.activeBoard = null;
        this.settings = normalizeSettings({});
        this.viewport = createViewport();
        this.rootEl = null;
        this.stageEl = null;
        this.canvasEl = null;
        this.draftCanvasEl = null;
        this.ctx = null;
        this.draftCtx = null;
        this.toolbarEl = null;
        this.fabEl = null;
        this.eraserCursorEl = null;
        this.syncDotEl = null;
        this.panels = {};
        this.sync = null;
        this.isOpen = false;
        this.dpr = 1;
        this.canvasWidth = 0;
        this.canvasHeight = 0;
        this.renderFrame = null;
        this.inputFrame = null;
        this.inputQueue = [];
        this.saveTimer = null;
        this.saveIdleHandle = null;
        this.resizeTimer = null;
        this.bootstrapHandle = null;
        this.drawingReleaseTimer = null;
        this.closeTimer = null;
        this.stageRect = null;
        this.stageObserver = null;
        this.gridEl = null;
        this.gridScale = null;
        this.renderCache = new RenderCache();
        this.cacheSettleTimer = null;
        this.profile = resolveProfile('auto');
        this.probe = createPerfProbe();
        /** 每块板每场只提示一次容量，且只升不降。 */
        this.capacityNotices = new Map();
        /** 本地待落盘的板 id（活动板之外，被同步流程改过的板）。 */
        this.localDirtyBoardIds = new Set();
        this.saveErrorShown = false;
        this.activePointer = null;
        this.activeStroke = null;
        this.activeShape = null;
        this.activePan = null;
        this.activeEraser = null;
        this.eraseSession = null;
        this.transientTool = null;
        this.textEditor = null;
        this.undoStack = [];
        this.redoStack = [];
        this.fabDrag = null;
        this.ignoreNextFabClick = false;
        this.previousBodyOverflow = '';
        this.measureWidth = createMeasureWidth();
        this.boundResize = () => this.handleResize();
        this.boundKeydown = (event) => this.handleKeydown(event);
        this.boundKeyup = (event) => this.handleKeyup(event);
        this.boundVisibility = () => this.handleVisibilityChange();
        this.boundPageHide = () => this.persistAndFlush({ keepalive: true });
    }

    // ------------------------------------------------------------------ init
    init() {
        if (!isAllowedContext(this.context) || document.getElementById('teacher-whiteboard-root')) return;
        this.state = loadLocalState(this.context);
        this.profile = resolveProfile(loadPerfMode(this.context));
        this.settings = normalizeSettings(this.state.settings);
        this.activeBoard = this.state.boards.find((board) => board.id === this.state.activeBoardId) || this.state.boards[0];
        this.viewport = normalizeViewport(this.activeBoard.viewport);
        this.buildDom();
        this.cacheDom();
        if (!this.ctx || !this.draftCtx) return;
        this.setupSync();
        this.buildPanels();
        this.bindEvents();
        this.syncChips();
        this.updateToolState();
        this.updateOpacityVariables();
        this.updateGridPosition();
        this.updateUndoRedoButtons();
        this.updateClearButton();
        this.updateSyncStatus();
        this.applyFabPosition();
        this.setFabOpenState(false);
        this.observeStage();
        window.addEventListener('resize', this.boundResize);
        document.addEventListener('keydown', this.boundKeydown);
        document.addEventListener('keyup', this.boundKeyup);
        document.addEventListener('visibilitychange', this.boundVisibility);
        window.addEventListener('pagehide', this.boundPageHide);
    }

    // ------------------------------------------------------------ 舞台矩形
    /**
     * 舞台是 `position:fixed; inset:0`，矩形只在窗口尺寸变化时才会变。
     * 缓存它，指针回调里就不再需要 getBoundingClientRect（原来每个 coalesced 点一次，
     * 与网格层的自定义属性写入叠加成典型的 layout thrashing）。
     */
    getStageRect() {
        if (!this.stageRect) this.stageRect = this.stageEl.getBoundingClientRect();
        return this.stageRect;
    }

    invalidateStageRect() {
        this.stageRect = null;
    }

    observeStage() {
        if (typeof ResizeObserver !== 'function' || !this.stageEl) return;
        this.stageObserver = new ResizeObserver(() => this.invalidateStageRect());
        this.stageObserver.observe(this.stageEl);
    }

    /**
     * 把开关状态与背景透明度广播出去。宿主壳页（material_render_shell.js）据此让
     * 下方的学习文档 iframe 降级：暂停动效；背景完全不透明时干脆把 iframe 藏起来，
     * 整棵文档树退出合成。
     */
    notifyHostState() {
        try {
            window.dispatchEvent(new CustomEvent('teacher-whiteboard:state', {
                detail: { open: this.isOpen, backgroundOpacity: this.settings.backgroundOpacity },
            }));
        } catch { /* CustomEvent 不可用时忽略，纯属锦上添花 */ }
    }

    /** 落笔期间给根元素挂 `is-drawing`，CSS 借此临时关掉工具栏的 backdrop-filter。 */
    setDrawingState(active) {
        if (!this.rootEl) return;
        window.clearTimeout(this.drawingReleaseTimer);
        if (active) {
            this.rootEl.classList.add('is-drawing');
            return;
        }
        this.drawingReleaseTimer = window.setTimeout(() => {
            if (!this.activePointer) this.rootEl?.classList.remove('is-drawing');
        }, TIMING.DRAWING_CLASS_RELEASE_MS);
    }

    buildDom() {
        const root = document.createElement('div');
        root.id = 'teacher-whiteboard-root';
        root.className = 'teacher-whiteboard-root twb-root';
        root.hidden = true;
        root.setAttribute('aria-hidden', 'true');
        root.dataset.tool = this.settings.tool;
        root.dataset.perf = this.profile.tier;
        root.innerHTML = `
            <div class="teacher-whiteboard-stage" id="teacher-whiteboard-stage">
                <div class="twb-grid" id="teacher-whiteboard-grid" aria-hidden="true"></div>
                <div class="teacher-whiteboard-canvas-layer" id="teacher-whiteboard-canvas-layer">
                    <canvas id="teacher-whiteboard-canvas"></canvas>
                    <canvas id="teacher-whiteboard-draft-canvas"></canvas>
                </div>
                <div class="twb-eraser-cursor" id="teacher-whiteboard-eraser-cursor" hidden></div>
            </div>
            ${buildToolbarHtml()}`;
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
        this.gridEl = document.getElementById('teacher-whiteboard-grid');
        this.canvasEl = document.getElementById('teacher-whiteboard-canvas');
        this.draftCanvasEl = document.getElementById('teacher-whiteboard-draft-canvas');
        this.toolbarEl = document.getElementById('teacher-whiteboard-toolbar');
        this.fabEl = document.getElementById('teacher-whiteboard-fab');
        this.eraserCursorEl = document.getElementById('teacher-whiteboard-eraser-cursor');
        this.syncDotEl = document.getElementById('teacher-whiteboard-sync-dot');
        this.ctx = this.canvasEl?.getContext('2d', { alpha: true, desynchronized: true }) || this.canvasEl?.getContext('2d');
        this.draftCtx = this.draftCanvasEl?.getContext('2d', { alpha: true, desynchronized: true }) || this.draftCanvasEl?.getContext('2d');
    }

    anchor(selector) {
        return this.toolbarEl?.querySelector(selector);
    }

    buildPanels() {
        const wrap = (entry, anchorEl) => {
            const previousClose = entry.popover.options.onClose;
            const previousOpen = entry.popover.options.onOpen;
            entry.popover.options.onClose = (reason) => {
                anchorEl?.classList.remove('is-open');
                previousClose?.(reason);
            };
            entry.popover.options.onOpen = () => {
                anchorEl?.classList.add('is-open');
                entry.refresh?.();
                previousOpen?.();
            };
            return entry;
        };
        const build = (factory, selector) => {
            const anchorEl = this.anchor(selector);
            return wrap(factory(this, anchorEl), anchorEl);
        };
        this.panels = {
            brush: build(createBrushPopover, '[data-whiteboard-chip="brush"]'),
            text: build(createTextPopover, '[data-whiteboard-chip="text"]'),
            ink: build(createInkPopover, '[data-whiteboard-chip="ink"]'),
            background: build(createBackgroundPopover, '[data-whiteboard-chip="background"]'),
            eraser: build(createEraserPopover, '[data-whiteboard-tool="eraser"]'),
            save: build(createSaveMenu, '[data-whiteboard-action="save-menu"]'),
            history: build(createHistoryPanel, '[data-whiteboard-action="history"]'),
            export: createExportDialog(this),
        };
    }

    setupSync() {
        this.sync = new SyncController({
            store: new RemoteStore(this.context.materialId),
            getBoards: () => this.state.boards,
            upsertLocalBoard: (board) => this.upsertLocalBoard(board),
            patchBoard: (id, patch) => this.patchBoard(id, patch),
            onStatus: () => this.updateSyncStatus(),
            notify: (message, type = 'info') => showToast(message, type, 3200),
            persistLocal: () => this.persistLocal(),
            // 落笔期间不要发起后台同步，把这一帧完整让给绘制。
            isBusy: () => Boolean(this.activePointer),
        });
    }

    bindEvents() {
        this.toolbarEl?.addEventListener('pointerdown', (event) => event.stopPropagation());
        this.toolbarEl?.addEventListener('click', (event) => this.handleToolbarClick(event));
        this.stageEl?.addEventListener('pointerdown', (event) => this.handleStagePointerDown(event));
        this.stageEl?.addEventListener('pointermove', (event) => this.handleStagePointerMove(event));
        this.stageEl?.addEventListener('pointerup', (event) => this.handleStagePointerUp(event));
        this.stageEl?.addEventListener('pointercancel', (event) => this.handleStagePointerCancel(event));
        this.stageEl?.addEventListener('pointerleave', () => this.hideEraserCursor());
        this.stageEl?.addEventListener('wheel', (event) => this.handleWheel(event), { passive: false });
        this.fabEl?.addEventListener('pointerdown', (event) => this.handleFabPointerDown(event));
        this.fabEl?.addEventListener('pointermove', (event) => this.handleFabPointerMove(event));
        this.fabEl?.addEventListener('pointerup', (event) => this.handleFabPointerUp(event));
        this.fabEl?.addEventListener('pointercancel', (event) => this.finishFabDrag(event));
        this.fabEl?.addEventListener('click', (event) => this.handleFabClick(event));
    }

    // -------------------------------------------------------------- settings
    get currentTool() {
        return this.transientTool || this.settings.tool;
    }

    updateSettings(patch) {
        this.settings = normalizeSettings({ ...this.settings, ...patch });
        if (this.textEditor?.element) {
            this.textEditor.element.style.color = this.settings.textColor;
            this.textEditor.element.style.fontSize = `${this.settings.fontSize}px`;
        }
        this.updateOpacityVariables();
        this.syncChips();
        if (patch.backgroundOpacity !== undefined) this.notifyHostState();
        this.scheduleSave();
    }

    syncChips() {
        const set = (id, value) => { const el = document.getElementById(id); if (el) el.textContent = value; };
        const brushSwatch = document.getElementById('twb-chip-brush-swatch');
        if (brushSwatch) brushSwatch.style.background = this.settings.brushColor;
        const textSwatch = document.getElementById('twb-chip-text-swatch');
        if (textSwatch) textSwatch.style.color = this.settings.textColor;
        set('twb-chip-brush-value', `${Math.round(this.settings.brushSize)}`);
        set('twb-chip-text-value', `${Math.round(this.settings.fontSize)}`);
        set('twb-chip-ink-value', `${Math.round(this.settings.boardOpacity * 100)}%`);
        set('twb-chip-background-value', `${Math.round(this.settings.backgroundOpacity * 100)}%`);
    }

    updateOpacityVariables() {
        this.rootEl?.style.setProperty('--teacher-whiteboard-bg-alpha', String(this.settings.backgroundOpacity));
        this.rootEl?.style.setProperty('--teacher-whiteboard-ink-alpha', String(this.settings.boardOpacity));
        // 墨迹不透明时不要给画布层加 opacity：那会把两张 canvas 强制拉进一个合成组，
        // 每帧多一次全屏 blit。默认值就是 1，所以这条覆盖的是绝大多数使用场景。
        this.rootEl?.classList.toggle('has-ink-alpha', this.settings.boardOpacity < 0.99);
    }

    /**
     * 网格改成「独立元素 + transform 驱动」。
     *
     * 原来是给根元素写 4 个自定义属性去推 `background-position`：自定义属性变更会让整棵
     * 子树样式失效（工具栏几十个按钮跟着重算），而 `background-position` 变化又要把全屏
     * 4 层渐变重新栅格化 —— 每一帧平移都付这两笔。
     * 现在网格元素比视口大出一个主网格周期，平移只改 `transform`，是纯合成操作；
     * 只有缩放才需要改网格尺寸（低频）。
     */
    updateGridPosition() {
        if (!this.gridEl) return;
        const scale = this.viewport.scale;
        const major = 200 * scale;
        if (this.gridScale !== scale) {
            this.gridScale = scale;
            const style = this.gridEl.style;
            style.setProperty('--teacher-whiteboard-grid-size', `${40 * scale}px`);
            style.setProperty('--teacher-whiteboard-major-grid-size', `${major}px`);
        }
        // 归一化到 [0, major)：网格元素向外多出一个周期，这个范围内平移永远有内容可露。
        const wrap = (value) => ((value % major) + major) % major;
        this.gridEl.style.transform = `translate3d(${wrap(this.viewport.x)}px, ${wrap(this.viewport.y)}px, 0)`;
    }

    // ----------------------------------------------------------- persistence
    cancelPendingSave() {
        window.clearTimeout(this.saveTimer);
        this.saveTimer = null;
        if (this.saveIdleHandle !== null && typeof window.cancelIdleCallback === 'function') {
            window.cancelIdleCallback(this.saveIdleHandle);
        }
        this.saveIdleHandle = null;
    }

    persistLocal() {
        if (!this.activeBoard) return;
        this.cancelPendingSave();
        this.activeBoard.viewport = { ...this.viewport };
        this.state.settings = { ...this.settings };
        this.state.activeBoardId = this.activeBoard.id;
        this.state.boards = pruneBoards(this.state.boards, this.activeBoard.id);
        // 只写活动板 + 同步流程刚改过的板，其余板体保持原样（v3 分键存储）。
        const boardIds = [this.activeBoard.id, ...this.localDirtyBoardIds];
        this.localDirtyBoardIds.clear();
        const result = saveLocalState(this.context, this.state, { boardIds });
        if (result.pruned) this.state = result.state;
        if (result.ok) {
            this.saveErrorShown = false;
        } else if (!this.saveErrorShown) {
            this.saveErrorShown = true;
            showToast('白板内容过大，浏览器本地保存失败；已线上保存的白板不受影响。', 'warning', 4200);
        }
    }

    /**
     * 防抖 + 空闲落盘。序列化与 localStorage 写入都是同步阻塞主线程的，
     * 放到空闲帧里做，避免和下一笔的绘制抢同一帧。
     */
    scheduleSave(delay = TIMING.SAVE_DEBOUNCE_MS) {
        this.cancelPendingSave();
        if (delay <= 0) {
            this.persistLocal();
            return;
        }
        this.saveTimer = window.setTimeout(() => {
            this.saveTimer = null;
            if (typeof window.requestIdleCallback === 'function') {
                this.saveIdleHandle = window.requestIdleCallback(
                    () => { this.saveIdleHandle = null; this.persistLocal(); },
                    { timeout: TIMING.SAVE_IDLE_TIMEOUT_MS },
                );
            } else {
                this.persistLocal();
            }
        }, delay);
    }

    markDirty() {
        if (!this.activeBoard) return;
        this.activeBoard.updatedAt = nowIso();
        this.activeBoard.dirty = true;
        this.activeBoard.elementCount = countInkElements(this.activeBoard.elements);
        this.maybeNoticeCapacity();
        this.updateSyncStatus();
        this.updateClearButton();
        this.scheduleSave();
    }

    /**
     * 笔迹太多时提醒新建一块。后端硬闸是 20000 个元素，但那个量级早就没法流畅了；
     * 这里在体感开始下滑之前先给出口，每块板每场最多提示两次（且只升不降）。
     */
    maybeNoticeCapacity() {
        const count = this.activeBoard.elementCount;
        const level = count >= CAPACITY.WARN ? 'warn' : (count >= CAPACITY.HINT ? 'hint' : '');
        if (!level) return;
        const seen = this.capacityNotices.get(this.activeBoard.id);
        if (seen === level || seen === 'warn') return;
        this.capacityNotices.set(this.activeBoard.id, level);
        if (level === 'warn') {
            showToast('这块白板笔迹很多了，新建一块会更流畅；旧的随时能在历史白板里翻回来', 'warning', 4200);
        } else {
            showToast('这块白板笔迹渐多，必要时可以新建一块', 'info', 3000);
        }
    }

    persistAndFlush({ keepalive = false } = {}) {
        if (!this.state) return;
        this.persistLocal();
        this.sync?.flushDirty({ silent: true, keepalive });
    }

    handleVisibilityChange() {
        if (document.visibilityState === 'hidden') this.persistAndFlush({ keepalive: true });
    }

    // -------------------------------------------------------------- sync host
    upsertLocalBoard(board) {
        const normalized = sanitizeBoard(board);
        const index = this.state.boards.findIndex((item) => item.id === normalized.id);
        if (index === -1) this.state.boards.push(normalized);
        else this.state.boards.splice(index, 1, normalized);
        this.localDirtyBoardIds.add(normalized.id);
        if (this.panels.history?.popover?.isOpen) this.panels.history.refresh();
    }

    patchBoard(id, patch) {
        const board = this.state.boards.find((item) => item.id === id);
        if (!board) return;
        const previousId = board.id;
        Object.assign(board, patch);
        // 板体键跟着 id 走：冲突处理会给本机副本换一个新 key。
        if (patch.id && patch.id !== previousId) dropLocalBoard(this.context, previousId);
        if (patch.elements || patch.id) this.localDirtyBoardIds.add(board.id);
        if (board === this.activeBoard) {
            if (patch.viewport) {
                this.viewport = normalizeViewport(patch.viewport);
                this.updateGridPosition();
            }
            if (patch.elements) {
                this.invalidateRenderCache();
                this.updateClearButton();
                this.scheduleRender(true);
            }
        }
        this.updateSyncStatus();
    }

    updateSyncStatus() {
        const status = this.sync?.statusOf(this.activeBoard) || 'local';
        if (this.syncDotEl) this.syncDotEl.dataset.status = status;
        const saveBtn = this.anchor('[data-whiteboard-action="save-menu"]');
        if (saveBtn) saveBtn.title = `保存 · ${SYNC_LABELS[status] || ''}`;
        if (this.panels.save?.popover?.isOpen) this.panels.save.refresh();
    }

    async saveOnline() {
        if (!this.activeBoard) return;
        this.commitTextEditor();
        this.persistLocal();
        await this.sync.flush(this.activeBoard, { explicit: true });
    }

    openExport() {
        this.commitTextEditor();
        this.panels.export.open();
    }

    // ---------------------------------------------------------------- boards
    activateBoard(board) {
        this.activeBoard = board;
        this.state.activeBoardId = board.id;
        this.viewport = normalizeViewport(board.viewport);
        this.undoStack = [];
        this.redoStack = [];
        this.updateUndoRedoButtons();
        this.updateClearButton();
        this.updateGridPosition();
        this.updateSyncStatus();
        this.invalidateRenderCache();
        this.clearDraftCanvas();
        this.scheduleRender(true);
        this.scheduleSave(0);
    }

    createNewBoard() {
        this.commitTextEditor();
        if (isBoardEmpty(this.activeBoard)) {
            showToast('当前白板还是空的，直接在上面画吧', 'info', 2200);
            return;
        }
        this.persistLocal();
        this.sync.flush(this.activeBoard, { explicit: false });
        const board = createBoard(nextBoardName(this.context.materialName, this.state.boards));
        this.state.boards.unshift(board);
        this.activateBoard(board);
        showToast('已新建白板', 'success', 1600);
    }

    async selectBoard(boardId) {
        const nextBoard = this.state.boards.find((board) => board.id === boardId);
        if (!nextBoard || nextBoard.id === this.activeBoard?.id) return;
        this.commitTextEditor();
        this.persistLocal();
        this.sync.flush(this.activeBoard, { explicit: false });
        if (nextBoard.elementsLoaded === false) {
            try {
                await this.sync.ensureLoaded(nextBoard);
            } catch (error) {
                showToast(error?.message || '云端白板加载失败', 'error');
                return;
            }
        }
        this.activateBoard(nextBoard);
    }

    renameBoard(boardId, name) {
        const board = this.state.boards.find((item) => item.id === boardId);
        if (!board) return;
        board.name = String(name || '').trim().slice(0, LIMITS.boardNameLength) || board.name;
        board.updatedAt = nowIso();
        this.persistLocal();
        this.sync.rename(board);
        this.updateSyncStatus();
    }

    async deleteBoard(boardId) {
        const board = this.state.boards.find((item) => item.id === boardId);
        if (!board) return;
        const removed = await this.sync.remove(board);
        if (!removed) return;
        this.state.boards = this.state.boards.filter((item) => item.id !== boardId);
        dropLocalBoard(this.context, boardId);
        if (board === this.activeBoard) {
            const next = [...this.state.boards].sort((a, b) => String(b.updatedAt || '').localeCompare(String(a.updatedAt || '')))[0];
            if (next) {
                if (next.elementsLoaded === false) {
                    try { await this.sync.ensureLoaded(next); } catch { /* 留空板兜底 */ }
                }
                this.activateBoard(next);
            } else {
                const fresh = createBoard(nextBoardName(this.context.materialName, []));
                this.state.boards.push(fresh);
                this.activateBoard(fresh);
            }
        }
        this.persistLocal();
        showToast('白板已删除', 'success', 1600);
    }

    // ------------------------------------------------------------------ undo
    pushUndoSnapshot() {
        this.undoStack.push(cloneElements(this.activeBoard.elements));
        const limit = Math.min(UNDO_LIMIT, this.profile.undoLimit);
        while (this.undoStack.length > limit) this.undoStack.shift();
        this.redoStack = [];
        this.updateUndoRedoButtons();
    }

    undo() {
        if (!this.undoStack.length || !this.activeBoard) return;
        this.commitTextEditor();
        this.redoStack.push(cloneElements(this.activeBoard.elements));
        this.activeBoard.elements = this.undoStack.pop();
        this.invalidateRenderCache();
        this.updateUndoRedoButtons();
        this.scheduleRender(true);
        this.markDirty();
    }

    redo() {
        if (!this.redoStack.length || !this.activeBoard) return;
        this.commitTextEditor();
        this.undoStack.push(cloneElements(this.activeBoard.elements));
        this.activeBoard.elements = this.redoStack.pop();
        this.invalidateRenderCache();
        this.updateUndoRedoButtons();
        this.scheduleRender(true);
        this.markDirty();
    }

    updateUndoRedoButtons() {
        const undoButton = this.anchor('[data-whiteboard-action="undo"]');
        const redoButton = this.anchor('[data-whiteboard-action="redo"]');
        if (undoButton) undoButton.disabled = !this.undoStack.length;
        if (redoButton) redoButton.disabled = !this.redoStack.length;
    }

    updateClearButton() {
        const clearButton = this.anchor('[data-whiteboard-action="clear"]');
        if (clearButton) clearButton.disabled = !this.activeBoard?.elements?.length;
    }

    clearBoard(anchorEl) {
        if (!this.activeBoard?.elements?.length) return;
        openConfirm({
            anchor: anchorEl || this.anchor('[data-whiteboard-action="clear"]'),
            title: '清空当前白板？',
            body: '可以用撤销（Ctrl+Z）恢复。',
            confirmLabel: '清空',
            onConfirm: () => {
                this.commitTextEditor();
                this.pushUndoSnapshot();
                this.activeBoard.elements = [];
                this.invalidateRenderCache();
                this.clearDraftCanvas();
                this.scheduleRender(true);
                this.markDirty();
                showToast('已清空，Ctrl+Z 可恢复', 'success', 2000);
            },
        });
    }

    // --------------------------------------------------------------- toolbar
    handleToolbarClick(event) {
        const button = event.target.closest('[data-whiteboard-tool], [data-whiteboard-shape], [data-whiteboard-action], [data-whiteboard-chip]');
        if (!button) return;
        const { whiteboardTool: tool, whiteboardShape: shape, whiteboardAction: action, whiteboardChip: chip } = button.dataset;
        if (chip) {
            this.panels[chip]?.popover.toggle();
            return;
        }
        if (tool) {
            if (tool === 'eraser' && this.settings.tool === 'eraser') {
                this.panels.eraser.popover.toggle();
                return;
            }
            this.setTool(tool);
            return;
        }
        if (shape) {
            this.settings.shapeType = shape;
            this.setTool('shape');
            return;
        }
        if (action) this.handleAction(action, button);
    }

    handleAction(action, button) {
        const actions = {
            'new-board': () => this.createNewBoard(),
            history: () => this.panels.history.popover.toggle(),
            'save-menu': () => this.panels.save.popover.toggle(),
            undo: () => this.undo(),
            redo: () => this.redo(),
            'zoom-in': () => this.zoomBy(1.12),
            'zoom-out': () => this.zoomBy(1 / 1.12),
            'reset-view': () => this.resetView(),
            clear: () => this.clearBoard(button),
        };
        actions[action]?.();
    }

    setTool(tool) {
        if (!TOOLS.includes(tool)) return;
        if (tool !== 'text') this.commitTextEditor();
        popoverManager.closeAll('tool');
        this.settings.tool = tool;
        this.updateToolState();
        this.scheduleSave();
    }

    updateToolState() {
        if (this.rootEl) this.rootEl.dataset.tool = this.currentTool;
        this.toolbarEl?.querySelectorAll('[data-whiteboard-tool], [data-whiteboard-shape]').forEach((button) => {
            const tool = button.dataset.whiteboardTool;
            const shape = button.dataset.whiteboardShape;
            const active = tool ? this.settings.tool === tool : this.settings.tool === 'shape' && this.settings.shapeType === shape;
            button.classList.toggle('is-active', active);
            button.setAttribute('aria-pressed', active ? 'true' : 'false');
        });
        if (this.currentTool !== 'eraser') this.hideEraserCursor();
    }

    // ------------------------------------------------------------ open/close
    open() {
        if (this.isOpen || !this.rootEl) return;
        this.isOpen = true;
        window.clearTimeout(this.closeTimer);
        this.rootEl.hidden = false;
        this.rootEl.setAttribute('aria-hidden', 'false');
        this.previousBodyOverflow = document.body.style.overflow;
        document.body.style.overflow = 'hidden';
        this.setFabOpenState(true);
        this.invalidateStageRect();
        window.requestAnimationFrame(() => {
            this.rootEl?.classList.add('is-open');
            this.invalidateStageRect();
            this.resizeCanvases();
            this.scheduleRender(true);
        });
        this.notifyHostState();
        this.probe?.start();
        this.sync.start(this.profile.syncIntervalMs);
        // 拉列表 → 合并 → 落盘 → 上传是一串同步+网络开销，正好会挤在老师落下的第一笔上。
        // 推到空闲帧再做，最迟 2.5s 兜底。
        this.scheduleBootstrap();
    }

    scheduleBootstrap() {
        const run = () => {
            this.bootstrapHandle = null;
            if (!this.isOpen) return;
            this.sync.bootstrap().then(() => this.adoptRemoteBoardIfFresh());
        };
        // idle 与 timeout 的句柄是两个独立的 id 空间，混着取消会误伤别人的定时器。
        this.bootstrapHandle = typeof window.requestIdleCallback === 'function'
            ? { type: 'idle', id: window.requestIdleCallback(run, { timeout: TIMING.BOOTSTRAP_IDLE_TIMEOUT_MS }) }
            : { type: 'timeout', id: window.setTimeout(run, 600) };
    }

    cancelBootstrap() {
        if (!this.bootstrapHandle) return;
        if (this.bootstrapHandle.type === 'idle') window.cancelIdleCallback(this.bootstrapHandle.id);
        else window.clearTimeout(this.bootstrapHandle.id);
        this.bootstrapHandle = null;
    }

    /** 新电脑首次打开：本地只有一块空板而云端有内容时，直接切到最近的云端白板。 */
    async adoptRemoteBoardIfFresh() {
        const active = this.activeBoard;
        if (!active) return;
        if (active.elementsLoaded === false) {
            await this.sync.ensureLoaded(active).catch(() => {});
            return;
        }
        if (!isBoardEmpty(active) || active.remoteVersion > 0) return;
        const candidate = [...this.state.boards]
            .filter((board) => board !== active && !isBoardEmpty(board))
            .sort((a, b) => String(b.updatedAt || '').localeCompare(String(a.updatedAt || '')))[0];
        if (!candidate) return;
        try {
            await this.sync.ensureLoaded(candidate);
        } catch {
            return;
        }
        this.state.boards = this.state.boards.filter((board) => board !== active);
        this.activateBoard(candidate);
    }

    close() {
        if (!this.isOpen || !this.rootEl) return;
        popoverManager.closeAll('close');
        this.commitTextEditor();
        this.finishPointerState();
        this.isOpen = false;
        this.rootEl.classList.remove('is-open', 'is-panning', 'is-drawing');
        this.rootEl.setAttribute('aria-hidden', 'true');
        document.body.style.overflow = this.previousBodyOverflow || '';
        this.setFabOpenState(false);
        this.notifyHostState();
        this.probe?.stop();
        this.probe?.report('讲课白板本次会话');
        this.cancelBootstrap();
        this.cancelInputFlush();
        this.cancelCacheSettle();
        window.clearTimeout(this.resizeTimer);
        window.clearTimeout(this.drawingReleaseTimer);
        this.persistAndFlush();
        this.sync.stop();
        window.clearTimeout(this.closeTimer);
        this.closeTimer = window.setTimeout(() => {
            if (!this.isOpen && this.rootEl) this.rootEl.hidden = true;
        }, 190);
    }

    toggleOpen() {
        if (this.isOpen) this.close();
        else this.open();
    }

    /**
     * 窗口尺寸变化会重新分配画布后备存储并清空内容，拖窗口/切投影分辨率时会连续触发
     * 几十次「重分配 + 全量重绘」。悬浮球位置立即跟随（很便宜），画布改动防抖。
     */
    handleResize() {
        this.invalidateStageRect();
        if (this.fabEl?.style.left) {
            const rect = this.fabEl.getBoundingClientRect();
            this.placeFab(rect.left, rect.top);
            this.saveFabPosition();
        }
        if (!this.isOpen) return;
        window.clearTimeout(this.resizeTimer);
        this.resizeTimer = window.setTimeout(() => {
            this.resizeTimer = null;
            if (!this.isOpen) return;
            this.invalidateStageRect();
            this.resizeCanvases();
            this.updateGridPosition();
            this.scheduleRender(true);
        }, TIMING.RESIZE_DEBOUNCE_MS);
    }

    // -------------------------------------------------------------- keyboard
    isTypingTarget(target) {
        return target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement
            || target instanceof HTMLSelectElement || Boolean(target?.isContentEditable);
    }

    handleKeydown(event) {
        if (!this.isOpen) return;
        if (event.key === 'Escape') {
            event.preventDefault();
            if (popoverManager.isOpen()) popoverManager.closeAll('escape');
            else if (this.textEditor?.element) this.closeTextEditor();
            else this.close();
            return;
        }
        if (this.textEditor?.element && document.activeElement === this.textEditor.element) return;
        if (this.isTypingTarget(event.target)) return;
        const key = event.key.toLowerCase();
        const mod = event.ctrlKey || event.metaKey;
        if (mod && key === 'z') { event.preventDefault(); if (event.shiftKey) this.redo(); else this.undo(); return; }
        if (mod && key === 'y') { event.preventDefault(); this.redo(); return; }
        if (mod && key === 's') { event.preventDefault(); this.saveOnline(); return; }
        if (mod && event.shiftKey && key === 'e') { event.preventDefault(); this.openExport(); return; }
        if (mod) return;
        if (event.key === ' ' && !event.repeat && !this.activePointer) {
            event.preventDefault();
            this.transientTool = 'hand';
            this.updateToolState();
            return;
        }
        if (TOOL_KEYS[key]) { this.setTool(TOOL_KEYS[key]); return; }
        if (event.key === '[' || event.key === ']') this.nudgeSize(event.key === ']' ? 1 : -1);
    }

    handleKeyup(event) {
        if (event.key === ' ' && this.transientTool) {
            this.transientTool = null;
            this.updateToolState();
        }
    }

    nudgeSize(direction) {
        const tool = this.settings.tool;
        if (tool === 'eraser') this.updateSettings({ eraserSize: this.settings.eraserSize + direction * 4 });
        else if (tool === 'text') this.updateSettings({ fontSize: this.settings.fontSize + direction * 2 });
        else this.updateSettings({ brushSize: this.settings.brushSize + direction });
        const panel = tool === 'eraser' ? 'eraser' : tool === 'text' ? 'text' : 'brush';
        this.panels[panel]?.refresh?.();
    }

    // ---------------------------------------------------------------- canvas
    /**
     * 画布后备存储尺寸。除了单边倍率上限，还要卡总像素：
     * 4K 投影 × dpr 2.5 是 8300 万像素（约 133MB），集显机上光分配和清屏就吃掉整帧。
     */
    canvasDpr(width, height) {
        let dpr = clamp(window.devicePixelRatio || 1, 1, Math.min(MAX_DPR, this.profile.maxDpr));
        const pixels = width * height * dpr * dpr;
        if (pixels > MAX_CANVAS_PIXELS) {
            dpr = Math.max(1, dpr * Math.sqrt(MAX_CANVAS_PIXELS / pixels));
        }
        return Math.round(dpr * 100) / 100;
    }

    resizeCanvases() {
        if (!this.stageEl || !this.canvasEl || !this.draftCanvasEl) return;
        const rect = this.getStageRect();
        const width = Math.max(1, Math.round(rect.width));
        const height = Math.max(1, Math.round(rect.height));
        const dpr = this.canvasDpr(width, height);
        if (this.canvasWidth === width && this.canvasHeight === height && this.dpr === dpr) return;
        this.canvasWidth = width;
        this.canvasHeight = height;
        this.dpr = dpr;
        this.invalidateRenderCache();
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

    /**
     * 主画布 = 提交层缓存的一次搬运 + 露出条带的实时补画。
     *
     * 原实现每帧都把全部元素重放一遍，代价与板上总笔数成正比；现在只有
     * 「缓存失效」或「露出太多 / 缩放偏离太远」时才真正重建，其余情况是一次位图 blit。
     */
    drawMainCanvas() {
        if (!this.ctx || !this.canvasWidth || !this.canvasHeight) return;
        const elements = this.activeBoard?.elements || [];
        const cache = this.renderCache;
        cache.resize(this.canvasWidth, this.canvasHeight, this.dpr);

        this.setScreenTransform(this.ctx);
        this.ctx.clearRect(0, 0, this.canvasWidth, this.canvasHeight);

        this.probe?.count('render');
        if (!cache.valid) {
            cache.rebuild(elements, this.viewport, this.measureWidth);
            this.probe?.count('rebuild');
        }
        if (!cache.valid) {
            // 拿不到离屏画布（极老的浏览器）时退回原来的全量重绘。
            renderElements(this.ctx, elements, this.viewport, {
                worldClip: null, measureWidth: this.measureWidth,
            });
            return;
        }

        // 先算再画：露出太多或缩放偏离太远时直接重建，不浪费一次无用的 blit。
        let geometry = cache.geometryFor(this.viewport);
        if (shouldRebuild(geometry)) {
            cache.rebuild(elements, this.viewport, this.measureWidth);
            this.probe?.count('rebuild');
            geometry = cache.geometryFor(this.viewport);
        }
        cache.blitTo(this.ctx, geometry);
        this.probe?.count('blit');
        for (const rect of geometry.exposed) this.paintScreenRect(rect, elements);
        if (geometry.exact) this.cancelCacheSettle();
        else this.scheduleCacheSettle();
    }

    /**
     * 补画缓存没盖住的一条屏幕区域。
     *
     * 向外扩 1px 并先 `clearRect`：blit 的边缘会有抗锯齿，直接压着画会留下一条缝。
     * 清掉再按顺序重放与该区域相交的元素是安全的 —— 一块区域内的合成结果只取决于
     * 碰到它的那些元素及其先后顺序。
     */
    paintScreenRect(rect, elements) {
        const x = Math.max(0, rect.x - 1);
        const y = Math.max(0, rect.y - 1);
        const width = Math.min(this.canvasWidth - x, rect.width + 2);
        const height = Math.min(this.canvasHeight - y, rect.height + 2);
        if (width <= 0 || height <= 0) return;
        this.probe?.count('patch');
        this.setScreenTransform(this.ctx);
        this.ctx.save();
        this.ctx.beginPath();
        this.ctx.rect(x, y, width, height);
        this.ctx.clip();
        this.ctx.clearRect(x, y, width, height);
        renderElements(this.ctx, elements, this.viewport, {
            worldClip: screenRectToWorld({ x, y, width, height }, this.viewport),
            measureWidth: this.measureWidth,
        });
        this.ctx.restore();
    }

    /** 手势停下来之后回正重建，把 blit 出来的（可能已被拉伸的）画面换成清晰的一版。 */
    scheduleCacheSettle() {
        window.clearTimeout(this.cacheSettleTimer);
        this.cacheSettleTimer = window.setTimeout(() => {
            this.cacheSettleTimer = null;
            if (!this.isOpen) return;
            // 手势还没结束就再等一轮，别把「回正」丢掉（否则缩放后会一直停在拉伸的位图上）。
            if (this.activePointer) { this.scheduleCacheSettle(); return; }
            this.renderCache.invalidate();
            this.scheduleRender(true);
        }, this.profile.settleMs || CACHE.SETTLE_MS);
    }

    cancelCacheSettle() {
        window.clearTimeout(this.cacheSettleTimer);
        this.cacheSettleTimer = null;
    }

    /** 新元素直接叠加进缓存，避免为一笔重放整块板。 */
    commitToCache(element) {
        this.probe?.count('commit');
        this.renderCache.commit(element, this.measureWidth);
    }

    /** 整笔橡皮删元素后只重画受影响的那块世界矩形。 */
    repaintCacheRegion(worldRect) {
        if (!worldRect) {
            this.renderCache.invalidate();
            return;
        }
        this.probe?.count('repaintRegion');
        this.renderCache.repaintRegion(this.activeBoard?.elements || [], worldRect, this.measureWidth);
    }

    /** 切换性能档位：立即生效（画布按新 DPR 重建），并记在这台设备上。 */
    setPerfMode(mode) {
        this.profile = resolveProfile(mode);
        savePerfMode(this.context, this.profile.mode);
        if (this.rootEl) this.rootEl.dataset.perf = this.profile.tier;
        this.gridScale = null;
        this.updateGridPosition();
        this.invalidateStageRect();
        this.resizeCanvases();
        this.invalidateRenderCache();
        this.scheduleRender(true);
        this.sync?.restart(this.profile.syncIntervalMs);
    }

    /** 控制台出口：`teacherWhiteboard.perfReport()`。 */
    perfReport() {
        if (!this.probe) return '性能埋点未开启：地址栏加 ?wbperf=1，或 localStorage 设 teacher-whiteboard-perf-probe=1 后刷新。';
        return this.probe.report('讲课白板');
    }

    invalidateRenderCache() {
        this.renderCache.invalidate();
        this.cancelCacheSettle();
    }

    clearDraftCanvas() {
        if (!this.draftCtx || !this.canvasWidth || !this.canvasHeight) return;
        this.setScreenTransform(this.draftCtx);
        this.draftCtx.clearRect(0, 0, this.canvasWidth, this.canvasHeight);
    }

    // ------------------------------------------------------------------ view
    handleWheel(event) {
        if (!this.isOpen || event.ctrlKey || event.metaKey) return;
        event.preventDefault();
        this.zoomBy(event.deltaY > 0 ? 0.94 : 1.06, this.getStagePoint(event));
    }

    zoomBy(factor, focalScreenPoint = null) {
        const currentScale = this.viewport.scale;
        const nextScale = clamp(currentScale * factor, MIN_ZOOM, MAX_ZOOM);
        if (Math.abs(nextScale - currentScale) < 0.001) return;
        const focal = focalScreenPoint || { x: this.canvasWidth / 2, y: this.canvasHeight / 2 };
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

}

Object.assign(TeacherWhiteboard.prototype, fabMixin, interactionMixin, textEditorMixin);

export function initTeacherWhiteboard(context = window.MATERIAL_VIEWER_CONTEXT || {}) {
    const app = new TeacherWhiteboard(context);
    app.init();
    if (isAllowedContext(app.context)) window.teacherWhiteboard = app;
    return app;
}
