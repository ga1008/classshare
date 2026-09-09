/**
 * 考试答题附图画板（位图）。自 teacher_whiteboard.js 原样迁移，行为不变。
 * 复用 teacher-whiteboard-* 类名；exam_take.html 有覆盖样式，勿改类名。
 */
import { showToast } from '../ui.js';
import { ICONS, LEGACY_DEFAULT_COLOR, MAX_DPR } from './constants.js';
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
        /** 底图（载入的附图 / 清空后的空白），只在 clear / loadImage 时变化。 */
        this.baseline = null;
        /** 已完成的笔画（矢量，CSS 像素）。撤销栈存的是「底图引用 + 笔画引用数组」。 */
        this.strokes = [];
        this.activeStroke = null;
        /** 设计空间：打开时的画布尺寸。窗口尺寸变化时整体等比居中缩放，内容不会被裁掉也不会拉伸。 */
        this.designWidth = 0;
        this.designHeight = 0;
        this.canvasRect = null;
        this.history = [];
        this.redoStack = [];
        this.maxHistory = 50;
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
            this.designWidth = this.canvasWidth;
            this.designHeight = this.canvasHeight;
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

    get hasContent() {
        return Boolean(this.baseline) || this.strokes.length > 0 || Boolean(this.activeStroke);
    }

    resizeCanvas({ preserve = true } = {}) {
        if (!this.canvasEl || !this.stageEl || !this.ctx) return;
        this.canvasRect = null;
        const rect = this.stageEl.getBoundingClientRect();
        const width = Math.max(320, Math.round(rect.width || window.innerWidth));
        const height = Math.max(240, Math.round(rect.height || window.innerHeight));
        const dpr = clamp(window.devicePixelRatio || 1, 1, MAX_DPR);
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
        // 内容是矢量的，尺寸变化直接重放即可，既不用编码 PNG 也不会因位图缩放而糊掉。
        if (preserve) this.repaint();
    }

    /** 设计空间 → 当前画布的等比居中变换。 */
    viewTransform() {
        const designWidth = this.designWidth || this.canvasWidth || 1;
        const designHeight = this.designHeight || this.canvasHeight || 1;
        const scale = Math.min(this.canvasWidth / designWidth, this.canvasHeight / designHeight) || 1;
        return {
            scale,
            dx: (this.canvasWidth - designWidth * scale) / 2,
            dy: (this.canvasHeight - designHeight * scale) / 2,
        };
    }

    withView(draw) {
        const view = this.viewTransform();
        this.ctx.save();
        this.ctx.translate(view.dx, view.dy);
        this.ctx.scale(view.scale, view.scale);
        draw();
        this.ctx.restore();
    }

    getPoint(event) {
        if (!this.canvasRect) this.canvasRect = this.canvasEl.getBoundingClientRect();
        const view = this.viewTransform();
        return {
            x: (event.clientX - this.canvasRect.left - view.dx) / view.scale,
            y: (event.clientY - this.canvasRect.top - view.dy) / view.scale,
        };
    }

    handlePointerDown(event) {
        if (!this.isOpen || event.button !== 0) return;
        event.preventDefault();
        this.canvasRect = null;
        this.canvasEl.setPointerCapture?.(event.pointerId);
        this.pushHistory();
        this.isDrawing = true;
        const point = this.getPoint(event);
        this.activeStroke = {
            tool: this.settings.tool,
            color: this.settings.brushColor,
            size: this.settings.brushSize,
            points: [point],
        };
        this.strokeSegment(this.activeStroke, point, point);
    }

    handlePointerMove(event) {
        if (!this.isDrawing || !this.activeStroke) return;
        event.preventDefault();
        const points = this.activeStroke.points;
        const previous = points[points.length - 1];
        const point = this.getPoint(event);
        points.push(point);
        this.strokeSegment(this.activeStroke, previous, point);
    }

    /**
     * 只画新增的这一段。
     * 原实现是「累积路径 + 每次 stroke 全部重描」，一笔 N 个点就是 O(N^2) 的描边量。
     */
    strokeSegment(stroke, from, to) {
        this.withView(() => {
            this.applyStrokeStyle(stroke);
            this.ctx.beginPath();
            this.ctx.moveTo(from.x, from.y);
            this.ctx.lineTo(to.x, to.y);
            this.ctx.stroke();
        });
    }

    handlePointerUp(event) {
        if (!this.isDrawing) return;
        event.preventDefault();
        this.isDrawing = false;
        if (this.activeStroke) {
            this.strokes.push(this.activeStroke);
            this.activeStroke = null;
        }
        this.canvasEl.releasePointerCapture?.(event.pointerId);
        this.updateHistoryButtons();
    }

    /** 把底图与全部笔画重放一遍。只在撤销/重做/清空/载图/改尺寸时调用，不在绘制热路径上。 */
    repaint() {
        if (!this.ctx) return;
        this.ctx.clearRect(0, 0, this.canvasWidth, this.canvasHeight);
        this.withView(() => {
            this.drawBaseline();
            for (const stroke of this.strokes) this.drawStrokePath(stroke);
        });
        this.ctx.globalCompositeOperation = 'source-over';
    }

    drawBaseline() {
        const image = this.baseline?.canvas;
        if (!image) return;
        const boxWidth = this.designWidth || this.canvasWidth;
        const boxHeight = this.designHeight || this.canvasHeight;
        const scale = Math.min(boxWidth / image.width, boxHeight / image.height, 1);
        const width = image.width * scale;
        const height = image.height * scale;
        this.ctx.globalCompositeOperation = 'source-over';
        this.ctx.drawImage(image, (boxWidth - width) / 2, (boxHeight - height) / 2, width, height);
    }

    drawStrokePath(stroke) {
        const points = stroke?.points || [];
        if (!points.length) return;
        this.applyStrokeStyle(stroke);
        this.ctx.beginPath();
        this.ctx.moveTo(points[0].x, points[0].y);
        if (points.length === 1) this.ctx.lineTo(points[0].x, points[0].y);
        else for (let index = 1; index < points.length; index += 1) this.ctx.lineTo(points[index].x, points[index].y);
        this.ctx.stroke();
        this.ctx.closePath();
    }

    applyStrokeStyle(stroke = null) {
        const tool = stroke ? stroke.tool : this.settings.tool;
        const color = stroke ? stroke.color : this.settings.brushColor;
        const size = stroke ? stroke.size : this.settings.brushSize;
        if (tool === 'eraser') {
            this.ctx.globalCompositeOperation = 'destination-out';
            this.ctx.strokeStyle = 'rgba(0,0,0,1)';
            this.ctx.lineWidth = Math.max(size * 2.2, 8);
            return;
        }
        this.ctx.globalCompositeOperation = 'source-over';
        this.ctx.strokeStyle = color;
        this.ctx.lineWidth = size;
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

    /**
     * 撤销不再存位图快照。
     *
     * 原实现每落一笔就 `toDataURL('image/png')`：PNG 编码是同步的，1920×1080@2dpr 在集显机上
     * 单次 80~400ms，24 层历史还会以 base64 字符串常驻内存 —— 这就是考试页「点一下笔就卡住」。
     * 换成位图拷贝虽然去掉了编码，但未压缩快照按整屏算每张 20MB 以上，同样不可接受。
     * 这里改成「底图引用 + 笔画引用数组」的文档快照：底图只在清空/载图时才产生（很少），
     * 笔画是矢量，撤销栈里存的全是引用，一层快照只有几十字节。
     */
    currentDocument() {
        return { baseline: this.baseline, strokes: this.strokes.slice() };
    }

    restoreDocument(snapshot) {
        if (!snapshot) return;
        this.baseline = snapshot.baseline;
        this.strokes = snapshot.strokes.slice();
        this.activeStroke = null;
        this.isDrawing = false;
        this.repaint();
    }

    pushHistory() {
        this.history.push(this.currentDocument());
        if (this.history.length > this.maxHistory) this.history.shift();
        this.redoStack = [];
        this.updateHistoryButtons();
    }

    undo() {
        if (!this.history.length) return;
        this.redoStack.push(this.currentDocument());
        this.restoreDocument(this.history.pop());
        this.updateHistoryButtons();
    }

    redo() {
        if (!this.redoStack.length) return;
        this.history.push(this.currentDocument());
        this.restoreDocument(this.redoStack.pop());
        this.updateHistoryButtons();
    }

    clearCanvas({ silent = false } = {}) {
        if (!this.ctx) return;
        if (!silent) this.pushHistory();
        this.baseline = null;
        this.strokes = [];
        this.activeStroke = null;
        this.repaint();
        this.updateHistoryButtons();
    }

    /** 载入题目附图作为底图（不进笔画栈，撤销时整体保留）。 */
    async loadImage(source) {
        if (!source || !this.ctx) return;
        await new Promise((resolve) => {
            const image = new Image();
            image.onload = () => {
                const canvas = document.createElement('canvas');
                canvas.width = image.width;
                canvas.height = image.height;
                canvas.getContext('2d')?.drawImage(image, 0, 0);
                this.baseline = { canvas };
                this.strokes = [];
                this.repaint();
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
