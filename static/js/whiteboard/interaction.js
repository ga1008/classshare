/**
 * 舞台交互 mixin：草稿层绘制、像素/整笔橡皮、指针事件（画笔/形状/平移/橡皮）。
 * 挂到 TeacherWhiteboard.prototype；依赖主类提供的 viewport / canvas / 面板与撤销方法。
 */
import { distance, hitTestElement } from './geometry.js';
import { popoverManager } from './popover.js';
import { drawElement } from './renderer.js';
import { makeId, nowIso } from './state.js';

export const interactionMixin = {
    drawScreenSegment(from, to, color, size) {
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
    },

    drawScreenDot(point, color, size) {
        const ctx = this.draftCtx;
        this.setScreenTransform(ctx);
        ctx.save();
        ctx.fillStyle = color;
        ctx.beginPath();
        ctx.arc(point.x, point.y, Math.max(size / 2, 1), 0, Math.PI * 2);
        ctx.fill();
        ctx.restore();
    },

    scheduleDraftShapeRender() {
        if (this.draftFrame !== null) return;
        this.draftFrame = window.requestAnimationFrame(() => {
            this.draftFrame = null;
            if (!this.activeShape) return;
            this.clearDraftCanvas();
            this.setScreenTransform(this.draftCtx);
            this.draftCtx.save();
            this.draftCtx.translate(this.viewport.x, this.viewport.y);
            this.draftCtx.scale(this.viewport.scale, this.viewport.scale);
            drawElement(this.draftCtx, this.activeShape, { draft: true });
            this.draftCtx.restore();
        });
    },

    /** 像素橡皮实时预览：直接在主画布上 destination-out（草稿层无法预览挖空）。 */
    drawEraserSegmentLive(points) {
        const ctx = this.ctx;
        this.setScreenTransform(ctx);
        ctx.save();
        ctx.translate(this.viewport.x, this.viewport.y);
        ctx.scale(this.viewport.scale, this.viewport.scale);
        drawElement(ctx, { ...this.activeEraser, points });
        ctx.restore();
    },

    getStagePoint(event) {
        const rect = this.stageEl.getBoundingClientRect();
        return { x: event.clientX - rect.left, y: event.clientY - rect.top };
    },

    screenToWorld(point) {
        return { x: (point.x - this.viewport.x) / this.viewport.scale, y: (point.y - this.viewport.y) / this.viewport.scale };
    },

    worldToScreen(point) {
        return { x: point.x * this.viewport.scale + this.viewport.x, y: point.y * this.viewport.scale + this.viewport.y };
    },

    updateEraserCursor(screenPoint) {
        if (!this.eraserCursorEl) return;
        if (this.currentTool !== 'eraser') { this.hideEraserCursor(); return; }
        const size = this.settings.eraserSize;
        this.eraserCursorEl.hidden = false;
        this.eraserCursorEl.style.width = `${size}px`;
        this.eraserCursorEl.style.height = `${size}px`;
        this.eraserCursorEl.style.transform = `translate(${screenPoint.x - size / 2}px, ${screenPoint.y - size / 2}px)`;
        this.eraserCursorEl.dataset.mode = this.settings.eraserMode;
    },

    hideEraserCursor() {
        if (this.eraserCursorEl) this.eraserCursorEl.hidden = true;
    },

    eraseStrokesAt(worldPoint) {
        const radius = this.settings.eraserSize / 2 / this.viewport.scale;
        const elements = this.activeBoard.elements;
        const survivors = elements.filter((element) => !hitTestElement(element, worldPoint, radius, this.measureWidth));
        if (survivors.length === elements.length) return;
        if (!this.eraseSession.pushed) {
            this.pushUndoSnapshot();
            this.eraseSession.pushed = true;
        }
        this.activeBoard.elements = survivors;
        this.scheduleRender(true);
    },

    handleStagePointerDown(event) {
        if (!this.isOpen || event.button !== 0 || event.target.closest('.teacher-whiteboard-text-editor')) return;
        popoverManager.closeAll('stage');
        this.resizeCanvases();
        this.commitTextEditor();
        const screenPoint = this.getStagePoint(event);
        const worldPoint = this.screenToWorld(screenPoint);
        this.activePointer = event.pointerId;
        const tool = this.currentTool;

        if (tool === 'hand') {
            this.activePan = { startX: event.clientX, startY: event.clientY, viewportX: this.viewport.x, viewportY: this.viewport.y };
            this.rootEl?.classList.add('is-panning');
        } else if (tool === 'brush') {
            this.activeStroke = {
                id: makeId('stroke'), type: 'stroke', color: this.settings.brushColor,
                size: this.settings.brushSize / this.viewport.scale, points: [worldPoint], createdAt: nowIso(),
            };
            this.clearDraftCanvas();
            this.drawScreenDot(screenPoint, this.settings.brushColor, this.settings.brushSize);
        } else if (tool === 'eraser') {
            if (this.settings.eraserMode === 'stroke') {
                this.eraseSession = { pushed: false };
                this.eraseStrokesAt(worldPoint);
            } else {
                this.activeEraser = {
                    id: makeId('eraser'), type: 'eraser', size: this.settings.eraserSize / this.viewport.scale,
                    hardness: this.settings.eraserHardness, points: [worldPoint], createdAt: nowIso(),
                };
                this.drawEraserSegmentLive([worldPoint]);
            }
        } else if (tool === 'shape') {
            this.activeShape = {
                id: makeId('shape'), type: 'shape', shape: this.settings.shapeType, color: this.settings.brushColor,
                size: this.settings.brushSize / this.viewport.scale,
                x1: worldPoint.x, y1: worldPoint.y, x2: worldPoint.x, y2: worldPoint.y, createdAt: nowIso(),
            };
            this.clearDraftCanvas();
        } else if (tool === 'text') {
            this.activePointer = null;
            this.openTextEditor(worldPoint);
            return;
        }
        try { this.stageEl.setPointerCapture(event.pointerId); } catch { /* optional */ }
        event.preventDefault();
    },

    handleStagePointerMove(event) {
        if (this.currentTool === 'eraser') this.updateEraserCursor(this.getStagePoint(event));
        if (!this.activePointer || this.activePointer !== event.pointerId) return;
        if (this.activePan) {
            this.viewport.x = this.activePan.viewportX + (event.clientX - this.activePan.startX);
            this.viewport.y = this.activePan.viewportY + (event.clientY - this.activePan.startY);
            this.updateGridPosition();
            this.scheduleRender(true);
            event.preventDefault();
            return;
        }
        const coalesced = typeof event.getCoalescedEvents === 'function' ? event.getCoalescedEvents() : [event];
        if (this.activeStroke) {
            for (const pointerEvent of coalesced) this.addStrokePoint(this.getStagePoint(pointerEvent));
            event.preventDefault();
            return;
        }
        if (this.activeEraser) {
            for (const pointerEvent of coalesced) this.addEraserPoint(this.getStagePoint(pointerEvent));
            event.preventDefault();
            return;
        }
        if (this.eraseSession) {
            for (const pointerEvent of coalesced) this.eraseStrokesAt(this.screenToWorld(this.getStagePoint(pointerEvent)));
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
    },

    handleStagePointerUp(event) {
        if (!this.activePointer || this.activePointer !== event.pointerId) return;
        this.handleStagePointerMove(event);
        this.finishDrawing(event);
    },

    handleStagePointerCancel(event) {
        if (this.activePointer !== event.pointerId) return;
        this.finishPointerState(event);
        this.clearDraftCanvas();
        this.scheduleRender(true);
    },

    addStrokePoint(screenPoint) {
        const points = this.activeStroke.points;
        const lastScreen = this.worldToScreen(points[points.length - 1]);
        if (distance(lastScreen, screenPoint) < 0.8) return;
        points.push(this.screenToWorld(screenPoint));
        this.drawScreenSegment(lastScreen, screenPoint, this.activeStroke.color, this.settings.brushSize);
    },

    addEraserPoint(screenPoint) {
        const points = this.activeEraser.points;
        const last = points[points.length - 1];
        if (distance(this.worldToScreen(last), screenPoint) < 1) return;
        const next = this.screenToWorld(screenPoint);
        points.push(next);
        this.drawEraserSegmentLive([last, next]);
    },

    finishDrawing(event) {
        if (this.activePan) {
            this.activeBoard.viewport = { ...this.viewport };
            this.scheduleSave();
        }
        if (this.activeStroke?.points.length) {
            this.pushUndoSnapshot();
            this.activeBoard.elements.push(this.activeStroke);
            this.clearDraftCanvas();
            this.scheduleRender(true);
            this.markDirty();
        }
        if (this.activeEraser) {
            this.pushUndoSnapshot();
            this.activeBoard.elements.push(this.activeEraser);
            this.scheduleRender(true);
            this.markDirty();
        }
        if (this.eraseSession?.pushed) this.markDirty();
        if (this.activeShape) {
            const start = this.worldToScreen({ x: this.activeShape.x1, y: this.activeShape.y1 });
            const end = this.worldToScreen({ x: this.activeShape.x2, y: this.activeShape.y2 });
            if (distance(start, end) > 5) {
                this.pushUndoSnapshot();
                this.activeBoard.elements.push(this.activeShape);
                this.markDirty();
            }
            this.clearDraftCanvas();
            this.scheduleRender(true);
        }
        this.finishPointerState(event);
    },

    finishPointerState(event = null) {
        if (this.stageEl && event) {
            try {
                if (this.stageEl.hasPointerCapture?.(event.pointerId)) this.stageEl.releasePointerCapture(event.pointerId);
            } catch { /* ignore */ }
        }
        this.rootEl?.classList.remove('is-panning');
        this.activePointer = null;
        this.activeStroke = null;
        this.activeShape = null;
        this.activePan = null;
        this.activeEraser = null;
        this.eraseSession = null;
    },
};
