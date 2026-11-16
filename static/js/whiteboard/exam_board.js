import { adoptDomainControl } from '../lq/domain-controls.js';
import { bindTooltip } from '../lq/tooltips.js';
import { getLayerSystem } from '../lq/layer.js';
/**
 * 作业/考试答题附图画板。底图和矢量笔画共同参与真实擦除，提交仍导出白底 PNG。
 * 复用 teacher-whiteboard-* 类名；exam_take.html 有覆盖样式，勿改类名。
 */
import { showToast } from '../ui.js';
import { ICONS, LEGACY_DEFAULT_COLOR, MAX_DPR } from './constants.js';
import { clamp } from './state.js';

const DEFAULT_SETTINGS = { brushColor: LEGACY_DEFAULT_COLOR, brushSize: 5, eraserSize: 24 };
const SIZE_LIMITS = { brush: [1, 32], eraser: [4, 120] };

function setControlDisabled(control, disabled) {
    if (!control) return;
    control.disabled = disabled;
    if (disabled) {
        control.setAttribute('aria-disabled', 'true');
        control.setAttribute('data-lq-disabled', 'true');
    } else {
        control.removeAttribute('aria-disabled');
        control.removeAttribute('data-lq-disabled');
    }
}

class ExamDrawingWhiteboard {
    constructor(options = {}) {
        this.rootId = options.rootId || 'exam-drawing-whiteboard-root';
        this.rootEl = null;
        this.stageEl = null;
        this.canvasEl = null;
        this.ctx = null;
        this.eraserCursorEl = null;
        this.controls = {};
        this.tooltipBindings = [];
        this.layerRegistration = null;
        this.dpr = 1;
        this.canvasWidth = 0;
        this.canvasHeight = 0;
        this.isOpen = false;
        this.isDrawing = false;
        this.activePointerId = null;
        this.openGeneration = 0;
        this.isLoading = false;
        this.loadFailed = false;
        this.hideTimer = null;
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
            eraserSize: DEFAULT_SETTINGS.eraserSize,
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
                    <canvas id="${this.rootId}-canvas" tabindex="0" aria-label="绘图画布"></canvas>
                </div>
                <div class="twb-eraser-cursor" id="${this.rootId}-eraser-cursor" aria-hidden="true" hidden></div>
            </div>
            <div data-lq-component="toolbar" data-lq-material="raised" class="lq-domain-toolbar lq-domain-raised teacher-whiteboard-toolbar exam-drawing-toolbar" role="toolbar" aria-label="答题绘图板工具">
                <div class="teacher-whiteboard-group is-board exam-drawing-title">
                    <strong id="${this.rootId}-title">题目附图</strong>
                    <span id="${this.rootId}-subtitle"></span>
                </div>
                <div class="teacher-whiteboard-group is-tools" aria-label="工具">
                    <button type="button" class="teacher-whiteboard-btn is-active" data-exam-drawing-tool="brush" aria-label="画笔" aria-pressed="true">${ICONS.pen}</button>
                    <button type="button" class="teacher-whiteboard-btn" data-exam-drawing-tool="eraser" aria-label="橡皮擦" aria-pressed="false">${ICONS.eraser}</button>
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
        // Explicit creation boundary; listeners and drawing state stay owned here.
        for (const button of root.querySelectorAll('button')) {
            adoptDomainControl(button, { variant: button.dataset.examDrawingAction === 'save' ? 'prominent' : 'glass' });
            if (button.querySelector('svg')) button.classList.add('lq-btn--icon');
        }
        for (const input of root.querySelectorAll('input')) {
            adoptDomainControl(input);
            if (input.type === 'color') input.dataset.lqVisual = 'color';
        }
        document.body.append(root);
    }

    cacheDom() {
        this.rootEl = document.getElementById(this.rootId);
        this.stageEl = document.getElementById(`${this.rootId}-stage`);
        this.canvasEl = document.getElementById(`${this.rootId}-canvas`);
        this.eraserCursorEl = document.getElementById(`${this.rootId}-eraser-cursor`);
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
            save: this.rootEl?.querySelector('[data-exam-drawing-action="save"]'),
            clear: this.rootEl?.querySelector('[data-exam-drawing-action="clear"]'),
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
            const tool = this.settings.tool;
            this.settings[tool === 'eraser' ? 'eraserSize' : 'brushSize'] = clamp(Number(this.controls.brushSize.value), ...SIZE_LIMITS[tool]);
            this.updateRangeLabel();
        });
        this.bindTooltips();
        this.layerRegistration = getLayerSystem(document).registerExternal({
            mode: 'ordered', root: () => this.rootEl, owner: () => this.rootEl,
            trigger: () => this.returnFocusEl, isOpen: () => this.isOpen,
            isPresent: () => Boolean(this.rootEl && !this.rootEl.hidden),
            dismissTop: () => this.close(null),
        });
        this.rootEl?.addEventListener('click', (event) => this.handleToolbarClick(event));
        this.stageEl?.addEventListener('pointerdown', (event) => this.handlePointerDown(event));
        this.stageEl?.addEventListener('pointermove', (event) => this.handlePointerMove(event));
        this.stageEl?.addEventListener('pointerup', (event) => this.handlePointerUp(event));
        this.stageEl?.addEventListener('pointercancel', (event) => this.handlePointerUp(event));
        this.canvasEl?.addEventListener('lostpointercapture', (event) => this.handlePointerUp(event));
        this.stageEl?.addEventListener('pointerleave', () => this.hideEraserCursor());
    }

    bindTooltips() {
        if (this.tooltipBindings.length) return;
        for (const button of this.rootEl?.querySelectorAll('button[aria-label]') || []) {
            if (button.querySelector('svg') && !button.textContent.trim()) {
                this.tooltipBindings.push(bindTooltip(button, { text: button.getAttribute('aria-label') },
                    { hoverDelay: 1000, observeRemoval: false }));
            }
        }
    }

    disposeTooltips() {
        // An ordered external owner's close destroys its child tooltip layers.
        // Release bindings too, then recreate them on the next visible session.
        // This never accumulates handlers on the cached toolbar DOM.
        this.tooltipBindings.forEach(binding => binding.destroy());
        this.tooltipBindings = [];
    }

    open(context = {}) {
        this.init();
        if (this.isOpen) this.close(null);
        const generation = ++this.openGeneration;
        window.clearTimeout(this.hideTimer);
        this.returnFocusEl = document.activeElement;
        this.context = { ...context };
        this.isOpen = true;
        this.isLoading = true;
        this.loadFailed = false;
        this.rootEl.hidden = false;
        this.rootEl.setAttribute('aria-hidden', 'false');
        this.rootEl.classList.add('is-open');
        this.layerRegistration?.refresh();
        this.bindTooltips();
        this.setTitle();
        this.updateHistoryButtons();
        window.addEventListener('resize', this.boundResize);
        document.addEventListener('keydown', this.boundKeydown);
        window.requestAnimationFrame(async () => {
            if (!this.isOpen || generation !== this.openGeneration) return;
            this.resizeCanvas({ preserve: false });
            this.designWidth = this.canvasWidth;
            this.designHeight = this.canvasHeight;
            this.clearCanvas({ silent: true });
            if (context.dataUrl || context.imageUrl) {
                const loaded = await this.loadImage(context.dataUrl || context.imageUrl, generation);
                if (!this.isOpen || generation !== this.openGeneration) return;
                this.loadFailed = !loaded;
                if (!loaded) showToast('原附图加载失败，请关闭后重试；原图仍保留。', 'warning');
            }
            this.isLoading = false;
            this.history = [];
            this.redoStack = [];
            this.updateHistoryButtons();
            this.canvasEl?.focus({ preventScroll: true });
        });

        return new Promise((resolve) => {
            this.resolveOpen = resolve;
        });
    }

    close(result = null) {
        if (!this.isOpen) return;
        this.finishStroke();
        this.isOpen = false;
        this.openGeneration += 1;
        this.hideEraserCursor();
        this.disposeTooltips();
        this.rootEl.classList.remove('is-open');
        this.rootEl.setAttribute('aria-hidden', 'true');
        this.layerRegistration?.refresh();
        window.removeEventListener('resize', this.boundResize);
        document.removeEventListener('keydown', this.boundKeydown);
        const resolver = this.resolveOpen;
        this.resolveOpen = null;
        this.hideTimer = window.setTimeout(() => {
            if (!this.isOpen && this.rootEl) {
                this.rootEl.hidden = true;
                this.layerRegistration?.refresh();
            }
        }, 180);
        if (this.returnFocusEl?.isConnected) this.returnFocusEl.focus({ preventScroll: true });
        if (resolver) resolver(result);
    }

    destroy() {
        this.close(null);
        this.openGeneration += 1;
        window.clearTimeout(this.hideTimer);
        this.disposeTooltips();
        this.layerRegistration?.destroy();
        this.layerRegistration = null;
        this.rootEl?.remove();
        this.rootEl = this.stageEl = this.canvasEl = this.ctx = this.eraserCursorEl = null;
        this.controls = {};
        this.baseline = null;
        this.strokes = this.history = this.redoStack = [];
        this.returnFocusEl = null;
        if (window.examDrawingWhiteboard === this) delete window.examDrawingWhiteboard;
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
        return Boolean(this.baseline) || this.strokes.some(stroke => stroke.tool !== 'eraser')
            || Boolean(this.activeStroke && this.activeStroke.tool !== 'eraser');
    }

    resizeCanvas({ preserve = true } = {}) {
        if (!this.canvasEl || !this.stageEl || !this.ctx) return;
        this.canvasRect = null;
        this.hideEraserCursor();
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
        if (!this.isOpen || this.isLoading || this.loadFailed || this.isDrawing || event.isPrimary === false || event.button !== 0) return;
        event.preventDefault();
        this.canvasRect = null;
        this.canvasEl.setPointerCapture?.(event.pointerId);
        this.pushHistory();
        this.isDrawing = true;
        this.activePointerId = event.pointerId;
        const point = this.getPoint(event);
        this.activeStroke = {
            tool: this.settings.tool,
            color: this.settings.brushColor,
            size: this.settings.tool === 'eraser' ? this.settings.eraserSize : this.settings.brushSize,
            points: [point],
        };
        this.strokeSegment(this.activeStroke, point, point);
        this.updateEraserCursor(event);
    }

    handlePointerMove(event) {
        if (!this.isOpen) return;
        if (!this.isDrawing || event.pointerId === this.activePointerId) this.updateEraserCursor(event);
        if (!this.isDrawing || !this.activeStroke || event.pointerId !== this.activePointerId) return;
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
            if (from.x === to.x && from.y === to.y) {
                this.ctx.arc(from.x, from.y, this.ctx.lineWidth / 2, 0, Math.PI * 2);
                this.ctx.fill();
                return;
            }
            this.ctx.moveTo(from.x, from.y);
            this.ctx.lineTo(to.x, to.y);
            this.ctx.stroke();
        });
    }

    handlePointerUp(event) {
        if (!this.isDrawing || event.pointerId !== this.activePointerId) return;
        event.preventDefault();
        // Preserve the final touch/mouse coordinate even without a last move.
        if (event.type === 'pointerup' && Number.isFinite(event.clientX) && Number.isFinite(event.clientY)) this.handlePointerMove(event);
        this.finishStroke();
        if (event.pointerType === 'touch' || event.type !== 'pointerup') this.hideEraserCursor();
    }

    finishStroke() {
        const pointerId = this.activePointerId;
        this.isDrawing = false;
        this.activePointerId = null;
        if (this.activeStroke) {
            this.strokes.push(this.activeStroke);
            this.activeStroke = null;
        }
        if (pointerId !== null && this.canvasEl?.hasPointerCapture?.(pointerId)) this.canvasEl.releasePointerCapture(pointerId);
        this.updateHistoryButtons();
    }

    hideEraserCursor() {
        if (this.eraserCursorEl) this.eraserCursorEl.hidden = true;
    }

    updateEraserCursor(event) {
        if (!this.eraserCursorEl || this.settings.tool !== 'eraser' || (event.pointerType === 'touch' && !this.isDrawing)) {
            this.hideEraserCursor();
            return;
        }
        if (!this.canvasRect) this.canvasRect = this.canvasEl.getBoundingClientRect();
        const size = this.settings.eraserSize * this.viewTransform().scale;
        this.eraserCursorEl.hidden = false;
        this.eraserCursorEl.style.width = `${size}px`;
        this.eraserCursorEl.style.height = `${size}px`;
        this.eraserCursorEl.style.transform = `translate(${event.clientX - this.canvasRect.left - size / 2}px, ${event.clientY - this.canvasRect.top - size / 2}px)`;
    }

    /** 把底图与全部笔画重放一遍。只在撤销/重做/清空/载图/改尺寸时调用，不在绘制热路径上。 */
    repaint() {
        if (!this.ctx) return;
        this.ctx.clearRect(0, 0, this.canvasWidth, this.canvasHeight);
        this.withView(() => {
            this.drawBaseline();
            for (const stroke of this.strokes) this.drawStrokePath(stroke);
            if (this.activeStroke) this.drawStrokePath(this.activeStroke);
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
        if (points.length === 1 || points.every(point => point.x === points[0].x && point.y === points[0].y)) {
            this.ctx.arc(points[0].x, points[0].y, this.ctx.lineWidth / 2, 0, Math.PI * 2);
            this.ctx.fill();
        } else {
            for (let index = 1; index < points.length; index += 1) this.ctx.lineTo(points[index].x, points[index].y);
            this.ctx.stroke();
        }
        this.ctx.closePath();
    }

    applyStrokeStyle(stroke = null) {
        const tool = stroke ? stroke.tool : this.settings.tool;
        const color = stroke ? stroke.color : this.settings.brushColor;
        const size = stroke ? stroke.size : this.settings.brushSize;
        if (tool === 'eraser') {
            this.ctx.globalCompositeOperation = 'destination-out';
            this.ctx.strokeStyle = 'rgba(0,0,0,1)';
            this.ctx.fillStyle = 'rgba(0,0,0,1)';
            this.ctx.lineWidth = size;
            return;
        }
        this.ctx.globalCompositeOperation = 'source-over';
        this.ctx.strokeStyle = color;
        this.ctx.fillStyle = color;
        this.ctx.lineWidth = size;
    }

    handleToolbarClick(event) {
        const toolButton = event.target.closest('[data-exam-drawing-tool]');
        if (toolButton) {
            this.tooltipBindings.forEach(binding => { void binding.hide('activate'); });
            this.setTool(toolButton.dataset.examDrawingTool);
            return;
        }

        const actionButton = event.target.closest('[data-exam-drawing-action]');
        if (!actionButton || actionButton.disabled) return;
        this.tooltipBindings.forEach(binding => { void binding.hide('activate'); });
        const action = actionButton.dataset.examDrawingAction;
        if (action === 'undo') this.undo();
        if (action === 'redo') this.redo();
        if (action === 'clear') this.clearCanvas();
        if (action === 'cancel') this.close(null);
        if (action === 'save') this.saveDrawing();
    }

    handleKeydown(event) {
        if (!this.isOpen || event.defaultPrevented) return;
        if (event.key === 'Escape') {
            event.preventDefault();
            this.close(null);
            return;
        }
        if (event.target?.closest?.('input,textarea,select,[contenteditable="true"]')) return;
        if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z') {
            event.preventDefault();
            event.shiftKey ? this.redo() : this.undo();
        }
        if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'y') {
            event.preventDefault();
            this.redo();
        }
        if (!event.ctrlKey && !event.metaKey && !event.altKey && ['b', 'e'].includes(event.key.toLowerCase())) {
            event.preventDefault();
            this.setTool(event.key.toLowerCase() === 'e' ? 'eraser' : 'brush');
        }
    }

    setTool(tool) {
        if (!(tool in SIZE_LIMITS)) return;
        this.finishStroke();
        this.settings.tool = tool;
        if (this.rootEl) this.rootEl.dataset.tool = tool;
        this.hideEraserCursor();
        this.updateToolButtons();
        this.updateRangeLabel();
    }

    updateRangeLabel() {
        const tool = this.settings.tool;
        const size = tool === 'eraser' ? this.settings.eraserSize : this.settings.brushSize;
        const label = tool === 'eraser' ? '橡皮大小' : '笔触粗细';
        if (this.controls.brushSize) {
            this.controls.brushSize.min = String(SIZE_LIMITS[tool][0]);
            this.controls.brushSize.max = String(SIZE_LIMITS[tool][1]);
            this.controls.brushSize.value = String(size);
            this.controls.brushSize.setAttribute('aria-label', label);
            this.controls.brushSize.closest('label')?.setAttribute('title', label);
        }
        if (this.controls.brushSizeValue) this.controls.brushSizeValue.textContent = `${size}px`;
    }

    updateToolButtons() {
        this.rootEl?.querySelectorAll('[data-exam-drawing-tool]').forEach((button) => {
            const selected = button.dataset.examDrawingTool === this.settings.tool;
            button.classList.toggle('is-active', selected);
            button.setAttribute('aria-pressed', String(selected));
        });
    }

    updateHistoryButtons() {
        const unavailable = this.isLoading || this.loadFailed;
        setControlDisabled(this.controls.undo, unavailable || !this.history.length);
        setControlDisabled(this.controls.redo, unavailable || !this.redoStack.length);
        setControlDisabled(this.controls.save, unavailable);
        setControlDisabled(this.controls.clear, unavailable);
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
        this.finishStroke();
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
        if (this.isLoading || this.loadFailed) return;
        this.finishStroke();
        if (!this.history.length) return;
        this.redoStack.push(this.currentDocument());
        this.restoreDocument(this.history.pop());
        this.updateHistoryButtons();
    }

    redo() {
        if (this.isLoading || this.loadFailed) return;
        this.finishStroke();
        if (!this.redoStack.length) return;
        this.history.push(this.currentDocument());
        this.restoreDocument(this.redoStack.pop());
        this.updateHistoryButtons();
    }

    clearCanvas({ silent = false } = {}) {
        if (!this.ctx) return;
        if (!silent && (this.isLoading || this.loadFailed)) return;
        this.finishStroke();
        if (!silent) this.pushHistory();
        this.baseline = null;
        this.strokes = [];
        this.activeStroke = null;
        this.repaint();
        this.updateHistoryButtons();
    }

    /** 载入题目附图作为底图（不进笔画栈，撤销时整体保留）。 */
    async loadImage(source, generation = this.openGeneration) {
        if (!source || !this.ctx) return false;
        return new Promise((resolve) => {
            const image = new Image();
            image.onload = () => {
                if (generation !== this.openGeneration || !this.ctx) { resolve(false); return; }
                const canvas = document.createElement('canvas');
                canvas.width = image.width;
                canvas.height = image.height;
                canvas.getContext('2d')?.drawImage(image, 0, 0);
                this.baseline = { canvas };
                this.strokes = [];
                this.repaint();
                resolve(true);
            };
            image.onerror = () => resolve(false);
            image.src = source;
        });
    }

    exportDataUrl() {
        const exportCanvas = document.createElement('canvas');
        exportCanvas.width = Math.round(this.canvasWidth * this.dpr);
        exportCanvas.height = Math.round(this.canvasHeight * this.dpr);
        const exportCtx = exportCanvas.getContext('2d');
        // Preserve the existing white-paper attachment contract. Erasure itself
        // happens on the transparent editing canvas, including loaded PNG ink.
        exportCtx.fillStyle = '#ffffff';
        exportCtx.fillRect(0, 0, exportCanvas.width, exportCanvas.height);
        exportCtx.drawImage(this.canvasEl, 0, 0, exportCanvas.width, exportCanvas.height);
        return exportCanvas.toDataURL('image/png');
    }

    saveDrawing() {
        if (this.isLoading || this.loadFailed) return;
        this.finishStroke();
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
