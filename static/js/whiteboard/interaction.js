/**
 * 舞台交互 mixin：草稿层绘制、像素/整笔橡皮、指针事件（画笔/形状/平移/橡皮）。
 * 挂到 TeacherWhiteboard.prototype；依赖主类提供的 viewport / canvas / 面板与撤销方法。
 *
 * 性能约定（2026-09 批次 A）：
 * - 指针事件只负责「取点入队」，所有绘制统一在一个 rAF 里合批完成；
 * - 舞台矩形由主类缓存（`getStageRect`），事件回调里不再触发强制同步布局；
 * - 整笔橡皮按帧处理整批点，并用包围盒做 AABB 快速排除，避免逐点全量遍历。
 */
import { INPUT } from './constants.js';
import {
    cachedElementBounds, cachedPaintBounds, distance, hitTestElement, pointOutsideBounds,
    simplifyStroke, unionBounds,
} from './geometry.js';
import { popoverManager } from './popover.js';
import { drawElement } from './renderer.js';
import { clamp, makeId, nowIso } from './state.js';

export const interactionMixin = {
    /** 采点最小间距（屏幕像素）：粗笔不需要细笔那么密的点。 */
    minPointDistance() {
        return clamp(
            this.settings.brushSize * INPUT.POINT_DISTANCE_BRUSH_RATIO,
            INPUT.MIN_POINT_DISTANCE,
            INPUT.MAX_POINT_DISTANCE,
        );
    },

    /** 抬笔入库前的抽稀容差（世界坐标）。屏幕上看不出差别，点数常能降到 1/3。 */
    commitTolerance() {
        const tolerance = this.profile?.simplifyTolerance || INPUT.COMMIT_SIMPLIFY_TOLERANCE;
        return tolerance / Math.max(this.viewport.scale, 0.01);
    },

    /** 一次性画完本帧新增的所有笔段（合批：一次 beginPath + 一次 stroke）。 */
    drawScreenPolyline(points, color, size) {
        if (points.length < 2) return;
        const ctx = this.draftCtx;
        this.setScreenTransform(ctx);
        ctx.save();
        ctx.strokeStyle = color;
        ctx.lineWidth = size;
        ctx.lineCap = 'round';
        ctx.lineJoin = 'round';
        ctx.beginPath();
        ctx.moveTo(points[0].x, points[0].y);
        for (let index = 1; index < points.length; index += 1) ctx.lineTo(points[index].x, points[index].y);
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

    renderDraftShape() {
        if (!this.activeShape) return;
        this.clearDraftCanvas();
        this.setScreenTransform(this.draftCtx);
        this.draftCtx.save();
        this.draftCtx.translate(this.viewport.x, this.viewport.y);
        this.draftCtx.scale(this.viewport.scale, this.viewport.scale);
        drawElement(this.draftCtx, this.activeShape, { draft: true });
        this.draftCtx.restore();
    },

    /** 像素橡皮实时预览：直接在主画布上 destination-out（草稿层无法预览挖空）。 */
    drawEraserSegmentLive(points) {
        if (points.length < 1) return;
        const ctx = this.ctx;
        this.setScreenTransform(ctx);
        ctx.save();
        ctx.translate(this.viewport.x, this.viewport.y);
        ctx.scale(this.viewport.scale, this.viewport.scale);
        drawElement(ctx, { ...this.activeEraser, points });
        ctx.restore();
    },

    getStagePoint(event) {
        const rect = this.getStageRect();
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

    /**
     * 整笔橡皮：一帧处理一批点，对每个元素只走一次循环。
     * 先用缓存的包围盒做 AABB 排除，绝大多数元素不必进入逐段测距。
     */
    eraseStrokesAtPoints(worldPoints) {
        if (!worldPoints.length || !this.activeBoard) return;
        const radius = this.settings.eraserSize / 2 / this.viewport.scale;
        const elements = this.activeBoard.elements;
        const survivors = [];
        let removedBounds = null;
        let removed = false;
        for (const element of elements) {
            const bounds = cachedElementBounds(element, this.measureWidth);
            let hit = false;
            for (const point of worldPoints) {
                if (pointOutsideBounds(bounds, point, radius)) continue;
                if (hitTestElement(element, point, radius, this.measureWidth)) { hit = true; break; }
            }
            if (hit) {
                removed = true;
                removedBounds = unionBounds(removedBounds, cachedPaintBounds(element, this.measureWidth));
            } else {
                survivors.push(element);
            }
        }
        if (!removed) return;
        if (!this.eraseSession.pushed) {
            this.pushUndoSnapshot();
            this.eraseSession.pushed = true;
        }
        this.activeBoard.elements = survivors;
        // 只有被删元素覆盖过的那块需要重画，不必让整张缓存失效。
        this.repaintCacheRegion(removedBounds);
        this.scheduleRender(true);
    },

    // --------------------------------------------------------------- 输入合批
    queueInputPoints(points) {
        if (!points.length) return;
        for (const point of points) this.inputQueue.push(point);
        this.scheduleInputFlush();
    },

    scheduleInputFlush() {
        if (this.inputFrame !== null) return;
        this.inputFrame = window.requestAnimationFrame(() => {
            this.inputFrame = null;
            this.flushInput();
        });
    },

    cancelInputFlush() {
        if (this.inputFrame === null) return;
        window.cancelAnimationFrame(this.inputFrame);
        this.inputFrame = null;
    },

    /** 把本帧攒下的指针输入一次性消化掉。所有画布写操作都只在这里发生。 */
    flushInput() {
        if (this.activePan) {
            const pan = this.activePan;
            this.viewport.x = pan.viewportX + (pan.lastX - pan.startX);
            this.viewport.y = pan.viewportY + (pan.lastY - pan.startY);
            this.updateGridPosition();
            this.scheduleRender(true);
            return;
        }
        const queue = this.inputQueue;
        if (!queue.length) return;
        this.inputQueue = [];

        if (this.activeStroke) { this.flushStrokePoints(queue); return; }
        if (this.activeEraser) { this.flushEraserPoints(queue); return; }
        if (this.eraseSession) {
            this.eraseStrokesAtPoints(queue.map((point) => this.screenToWorld(point)));
            return;
        }
        if (this.activeShape) {
            const point = this.screenToWorld(queue[queue.length - 1]);
            this.activeShape.x2 = point.x;
            this.activeShape.y2 = point.y;
            this.renderDraftShape();
        }
    },

    flushStrokePoints(screenPoints) {
        const minDistance = this.minPointDistance();
        const points = this.activeStroke.points;
        let lastScreen = this.worldToScreen(points[points.length - 1]);
        const segment = [lastScreen];
        for (const screenPoint of screenPoints) {
            if (distance(lastScreen, screenPoint) < minDistance) continue;
            points.push(this.screenToWorld(screenPoint));
            segment.push(screenPoint);
            lastScreen = screenPoint;
        }
        this.drawScreenPolyline(segment, this.activeStroke.color, this.settings.brushSize);
    },

    flushEraserPoints(screenPoints) {
        const points = this.activeEraser.points;
        let last = points[points.length - 1];
        const segment = [last];
        for (const screenPoint of screenPoints) {
            if (distance(this.worldToScreen(last), screenPoint) < INPUT.MIN_POINT_DISTANCE) continue;
            last = this.screenToWorld(screenPoint);
            points.push(last);
            segment.push(last);
        }
        if (segment.length > 1) this.drawEraserSegmentLive(segment);
    },

    // ------------------------------------------------------------- 指针事件
    handleStagePointerDown(event) {
        if (!this.isOpen || event.button !== 0 || event.target.closest('.teacher-whiteboard-text-editor')) return;
        popoverManager.closeAll('stage');
        this.invalidateStageRect();
        this.resizeCanvases();
        this.commitTextEditor();
        const screenPoint = this.getStagePoint(event);
        const worldPoint = this.screenToWorld(screenPoint);
        this.activePointer = event.pointerId;
        this.inputQueue = [];
        const tool = this.currentTool;

        if (tool === 'hand') {
            this.activePan = {
                startX: event.clientX, startY: event.clientY, lastX: event.clientX, lastY: event.clientY,
                viewportX: this.viewport.x, viewportY: this.viewport.y,
            };
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
                this.eraseStrokesAtPoints([worldPoint]);
            } else {
                this.activeEraser = {
                    id: makeId('eraser'), type: 'eraser', size: this.settings.eraserSize / this.viewport.scale,
                    // 流畅档强制硬边：软边要沿路径盖章，是橡皮里最贵的一条路径。
                    hardness: this.profile?.softEraser === false ? 1 : this.settings.eraserHardness,
                    points: [worldPoint], createdAt: nowIso(),
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
        this.setDrawingState(true);
        try { this.stageEl.setPointerCapture(event.pointerId); } catch { /* optional */ }
        event.preventDefault();
    },

    handleStagePointerMove(event) {
        if (this.currentTool === 'eraser') this.updateEraserCursor(this.getStagePoint(event));
        if (!this.activePointer || this.activePointer !== event.pointerId) return;
        if (this.activePan) {
            this.activePan.lastX = event.clientX;
            this.activePan.lastY = event.clientY;
            this.scheduleInputFlush();
            event.preventDefault();
            return;
        }
        if (!this.activeStroke && !this.activeEraser && !this.eraseSession && !this.activeShape) return;
        // 形状只关心最后一个点，没必要展开 coalesced。
        const useCoalesced = !this.activeShape && typeof event.getCoalescedEvents === 'function';
        const raw = useCoalesced ? event.getCoalescedEvents() : [event];
        const points = [];
        for (const pointerEvent of raw) points.push(this.getStagePoint(pointerEvent));
        this.queueInputPoints(points);
        event.preventDefault();
    },

    handleStagePointerUp(event) {
        if (!this.activePointer || this.activePointer !== event.pointerId) return;
        this.handleStagePointerMove(event);
        this.cancelInputFlush();
        this.flushInput();
        this.finishDrawing(event);
    },

    handleStagePointerCancel(event) {
        if (this.activePointer !== event.pointerId) return;
        this.finishPointerState(event);
        this.clearDraftCanvas();
        this.scheduleRender(true);
    },

    finishDrawing(event) {
        if (this.activePan) {
            this.activeBoard.viewport = { ...this.viewport };
            this.scheduleSave();
        }
        if (this.activeStroke?.points.length) {
            const stroke = this.activeStroke;
            if (stroke.points.length > 2) stroke.points = simplifyStroke(stroke.points, this.commitTolerance());
            this.pushUndoSnapshot();
            this.activeBoard.elements.push(stroke);
            this.commitToCache(stroke);
            this.clearDraftCanvas();
            this.scheduleRender(true);
            this.markDirty();
        }
        if (this.activeEraser) {
            const eraser = this.activeEraser;
            if (eraser.points.length > 2) eraser.points = simplifyStroke(eraser.points, this.commitTolerance());
            this.pushUndoSnapshot();
            this.activeBoard.elements.push(eraser);
            this.commitToCache(eraser);
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
                this.commitToCache(this.activeShape);
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
        this.cancelInputFlush();
        this.inputQueue = [];
        this.rootEl?.classList.remove('is-panning');
        this.activePointer = null;
        this.activeStroke = null;
        this.activeShape = null;
        this.activePan = null;
        this.activeEraser = null;
        this.eraseSession = null;
        this.setDrawingState(false);
    },
};
