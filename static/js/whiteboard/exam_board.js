/**
 * 考试答题附图画板（位图）。自 teacher_whiteboard.js 原样迁移，行为不变。
 * 复用 teacher-whiteboard-* 类名；exam_take.html 有覆盖样式，勿改类名。
 */
import { showToast } from '../ui.js';
import { ICONS, LEGACY_DEFAULT_COLOR } from './constants.js';
import { clamp } from './state.js';

const DEFAULT_SETTINGS = { brushColor: LEGACY_DEFAULT_COLOR, brushSize: 5 };

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

export function initExamDrawingWhiteboard(options = {}) {
    const app = new ExamDrawingWhiteboard(options);
    app.init();
    window.examDrawingWhiteboard = app;
    return app;
}

export { ExamDrawingWhiteboard };
