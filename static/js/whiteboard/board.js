/**
 * 讲课白板主类：编排 DOM、指针交互、撤销、面板、本地缓存与线上同步。
 * 设计真源：docs/whiteboard-upgrade-2026-09.md
 */
import { showToast } from '../ui.js';
import { ICONS, LIMITS, MAX_ZOOM, MIN_ZOOM, TOOLS, UNDO_LIMIT } from './constants.js';
import { createMeasureWidth, renderElements } from './renderer.js';
import {
    clamp, cloneElements, createBoard, createViewport, isBoardEmpty, nextBoardName,
    normalizeSettings, normalizeViewport, nowIso, sanitizeBoard,
} from './state.js';
import { loadLocalState, pruneBoards, saveLocalState } from './store_local.js';
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
        this.draftFrame = null;
        this.saveTimer = null;
        this.closeTimer = null;
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
        window.addEventListener('resize', this.boundResize);
        document.addEventListener('keydown', this.boundKeydown);
        document.addEventListener('keyup', this.boundKeyup);
        document.addEventListener('visibilitychange', this.boundVisibility);
        window.addEventListener('pagehide', this.boundPageHide);
    }

    buildDom() {
        const root = document.createElement('div');
        root.id = 'teacher-whiteboard-root';
        root.className = 'teacher-whiteboard-root twb-root';
        root.hidden = true;
        root.setAttribute('aria-hidden', 'true');
        root.dataset.tool = this.settings.tool;
        root.innerHTML = `
            <div class="teacher-whiteboard-stage" id="teacher-whiteboard-stage">
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
    }

    updateGridPosition() {
        if (!this.rootEl) return;
        const gridSize = 40 * this.viewport.scale;
        this.rootEl.style.setProperty('--teacher-whiteboard-pan-x', `${this.viewport.x % gridSize}px`);
        this.rootEl.style.setProperty('--teacher-whiteboard-pan-y', `${this.viewport.y % gridSize}px`);
        this.rootEl.style.setProperty('--teacher-whiteboard-grid-size', `${gridSize}px`);
        this.rootEl.style.setProperty('--teacher-whiteboard-major-grid-size', `${200 * this.viewport.scale}px`);
    }

    // ----------------------------------------------------------- persistence
    persistLocal() {
        if (!this.activeBoard) return;
        this.activeBoard.viewport = { ...this.viewport };
        this.state.settings = { ...this.settings };
        this.state.activeBoardId = this.activeBoard.id;
        this.state.boards = pruneBoards(this.state.boards, this.activeBoard.id);
        const result = saveLocalState(this.context, this.state);
        if (result.pruned) this.state = result.state;
        if (result.ok) {
            this.saveErrorShown = false;
        } else if (!this.saveErrorShown) {
            this.saveErrorShown = true;
            showToast('白板内容过大，浏览器本地保存失败；已线上保存的白板不受影响。', 'warning', 4200);
        }
    }

    scheduleSave(delay = 450) {
        window.clearTimeout(this.saveTimer);
        this.saveTimer = window.setTimeout(() => {
            this.saveTimer = null;
            this.persistLocal();
        }, delay);
    }

    markDirty() {
        if (!this.activeBoard) return;
        this.activeBoard.updatedAt = nowIso();
        this.activeBoard.dirty = true;
        this.activeBoard.elementCount = this.activeBoard.elements.filter((el) => el.type !== 'eraser').length;
        this.updateSyncStatus();
        this.updateClearButton();
        this.scheduleSave();
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
        if (this.panels.history?.popover?.isOpen) this.panels.history.refresh();
    }

    patchBoard(id, patch) {
        const board = this.state.boards.find((item) => item.id === id);
        if (!board) return;
        Object.assign(board, patch);
        if (board === this.activeBoard) {
            if (patch.viewport) {
                this.viewport = normalizeViewport(patch.viewport);
                this.updateGridPosition();
            }
            if (patch.elements) {
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
        if (this.undoStack.length > UNDO_LIMIT) this.undoStack.shift();
        this.redoStack = [];
        this.updateUndoRedoButtons();
    }

    undo() {
        if (!this.undoStack.length || !this.activeBoard) return;
        this.commitTextEditor();
        this.redoStack.push(cloneElements(this.activeBoard.elements));
        this.activeBoard.elements = this.undoStack.pop();
        this.updateUndoRedoButtons();
        this.scheduleRender(true);
        this.markDirty();
    }

    redo() {
        if (!this.redoStack.length || !this.activeBoard) return;
        this.commitTextEditor();
        this.undoStack.push(cloneElements(this.activeBoard.elements));
        this.activeBoard.elements = this.redoStack.pop();
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
        window.requestAnimationFrame(() => {
            this.rootEl?.classList.add('is-open');
            this.resizeCanvases();
            this.scheduleRender(true);
        });
        this.sync.start();
        this.sync.bootstrap().then(() => this.adoptRemoteBoardIfFresh());
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
        this.rootEl.classList.remove('is-open', 'is-panning');
        this.rootEl.setAttribute('aria-hidden', 'true');
        document.body.style.overflow = this.previousBodyOverflow || '';
        this.setFabOpenState(false);
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

    handleResize() {
        if (this.fabEl?.style.left) {
            const rect = this.fabEl.getBoundingClientRect();
            this.placeFab(rect.left, rect.top);
            this.saveFabPosition();
        }
        if (this.isOpen) {
            this.resizeCanvases();
            this.updateGridPosition();
            this.scheduleRender(true);
        }
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
        this.setScreenTransform(this.ctx);
        this.ctx.clearRect(0, 0, this.canvasWidth, this.canvasHeight);
        renderElements(this.ctx, this.activeBoard?.elements || [], this.viewport);
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
