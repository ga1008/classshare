import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { ExamDrawingWhiteboard } from './exam_board.js';
import { bindTooltip } from '../lq/tooltips.js';
import { getLayerSystem } from '../lq/layer.js';
import { showToast } from '../ui.js';

vi.mock('../ui.js', () => ({ showToast: vi.fn() }));
vi.mock('../lq/tooltips.js', () => ({ bindTooltip: vi.fn(() => ({ hide: vi.fn(), destroy: vi.fn() })) }));
vi.mock('../lq/layer.js', () => ({ getLayerSystem: vi.fn(() => ({ registerExternal: vi.fn(() => ({ refresh: vi.fn(), destroy: vi.fn() })) })) }));

function control(attrs = {}) {
    const values = new Map(Object.entries(attrs));
    return {
        dataset: {}, style: {}, listeners: new Map(), textContent: '', isConnected: true,
        classList: { add: vi.fn(), remove: vi.fn(), toggle: vi.fn() },
        getAttribute: key => values.get(key) ?? null,
        setAttribute: (key, value) => values.set(key, String(value)),
        removeAttribute: key => values.delete(key),
        addEventListener(name, fn) { this.listeners.set(name, fn); },
        focus: vi.fn(), remove: vi.fn(), querySelector: () => ({}), closest: () => null,
    };
}

function context() {
    const calls = [], stack = [];
    return {
        calls, globalCompositeOperation: 'source-over', strokeStyle: '#000', fillStyle: '#000', lineWidth: 1,
        save() { stack.push({ globalCompositeOperation: this.globalCompositeOperation, strokeStyle: this.strokeStyle,
            fillStyle: this.fillStyle, lineWidth: this.lineWidth }); },
        restore() { Object.assign(this, stack.pop()); },
        translate: vi.fn(), scale: vi.fn(), setTransform: vi.fn(),
        clearRect: (...args) => calls.push({ name: 'clearRect', args }),
        beginPath: vi.fn(), closePath: vi.fn(), moveTo: vi.fn(), lineTo: vi.fn(), arc: vi.fn(),
        stroke() { calls.push({ name: 'stroke', operation: this.globalCompositeOperation, size: this.lineWidth, color: this.strokeStyle }); },
        fill() { calls.push({ name: 'fill', operation: this.globalCompositeOperation, size: this.lineWidth, color: this.fillStyle }); },
        fillRect(...args) { calls.push({ name: 'fillRect', color: this.fillStyle, args }); },
        drawImage(...args) { calls.push({ name: 'drawImage', operation: this.globalCompositeOperation, args }); },
    };
}

function canvas() {
    const ctx = context(), captured = new Set();
    return Object.assign(control(), {
        width: 800, height: 600, ctx,
        getContext: () => ctx, getBoundingClientRect: () => ({ left: 0, top: 0, width: 800, height: 600 }),
        setPointerCapture: vi.fn(id => captured.add(id)), hasPointerCapture: id => captured.has(id),
        releasePointerCapture: vi.fn(id => captured.delete(id)), toDataURL: vi.fn(() => 'data:image/png;base64,synthetic'),
    });
}

function host() {
    const app = new ExamDrawingWhiteboard();
    const brush = control({ 'aria-label': '画笔' }), eraser = control({ 'aria-label': '橡皮擦' });
    brush.dataset.examDrawingTool = 'brush'; eraser.dataset.examDrawingTool = 'eraser';
    const actions = ['撤销', '重做', '清空', '关闭'].map(name => control({ 'aria-label': name }));
    app.rootEl = control();
    app.rootEl.querySelectorAll = selector => selector.includes('data-exam-drawing-tool') ? [brush, eraser] : [brush, eraser, ...actions];
    app.stageEl = control();
    app.stageEl.getBoundingClientRect = () => ({ width: 800, height: 600 });
    app.canvasEl = canvas(); app.ctx = app.canvasEl.ctx;
    app.eraserCursorEl = control(); app.eraserCursorEl.hidden = true;
    app.controls = Object.fromEntries(['brushColor', 'brushSize', 'brushSizeValue', 'undo', 'redo', 'save', 'clear'].map(key => [key, control()]));
    app.canvasWidth = app.designWidth = 800; app.canvasHeight = app.designHeight = 600;
    app.isOpen = true;
    app.toolButtons = { brush, eraser };
    return app;
}

function pointer(type, x = 20, y = 30, pointerId = 1, extra = {}) {
    return { type, pointerId, pointerType: 'mouse', isPrimary: true, button: 0, clientX: x, clientY: y,
        preventDefault: vi.fn(), ...extra };
}

function stroke(app, tool = 'brush') {
    app.setTool(tool);
    app.handlePointerDown(pointer('pointerdown'));
    app.handlePointerMove(pointer('pointermove', 100, 50));
    app.handlePointerUp(pointer('pointerup', 120, 60));
}

let frames, images, created;
beforeEach(() => {
    vi.useFakeTimers(); vi.clearAllMocks();
    frames = []; images = []; created = [];
    vi.stubGlobal('window', { devicePixelRatio: 1, innerWidth: 800, innerHeight: 600, setTimeout, clearTimeout,
        addEventListener: vi.fn(), removeEventListener: vi.fn(), requestAnimationFrame: callback => frames.push(callback) });
    vi.stubGlobal('document', { activeElement: control(), addEventListener: vi.fn(), removeEventListener: vi.fn(),
        createElement: () => { const item = canvas(); created.push(item); return item; } });
    vi.stubGlobal('Image', class {
        constructor() { this.width = 200; this.height = 100; images.push(this); }
    });
});
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

describe('student drawing: real erasure and document history', () => {
    test('brush and eraser retain independent sizes and selected accessibility state', () => {
        const app = host(); app.bindEvents();
        app.controls.brushSize.value = '7'; app.controls.brushSize.listeners.get('input')();
        app.setTool('eraser');
        expect(app.controls.brushSize.value).toBe('24');
        expect(app.controls.brushSize.getAttribute('aria-label')).toBe('橡皮大小');
        expect(app.toolButtons.eraser.getAttribute('aria-pressed')).toBe('true');
        app.controls.brushSize.value = '70'; app.controls.brushSize.listeners.get('input')();
        app.setTool('brush');
        expect(app.controls.brushSize.value).toBe('7');
        expect(app.toolButtons.eraser.getAttribute('aria-pressed')).toBe('false');
        app.setTool('eraser');
        expect(app.controls.brushSize.value).toBe('70');
        expect(app.controls.brushSize.max).toBe('120');
    });

    test('erasure composites after the unchanged PNG baseline; undo and redo replay it', () => {
        const app = host(), baseline = { canvas: { width: 200, height: 100 } };
        app.baseline = baseline;
        stroke(app, 'eraser');
        expect(app.strokes[0]).toMatchObject({ tool: 'eraser', size: 24 });
        expect(app.ctx.calls.filter(call => call.name === 'stroke').at(-1).operation).toBe('destination-out');
        app.undo();
        expect(app.baseline).toBe(baseline); expect(app.strokes).toEqual([]);
        expect(app.ctx.calls.at(-1)).toMatchObject({ name: 'drawImage', operation: 'source-over' });
        app.redo();
        expect(app.baseline).toBe(baseline); expect(app.strokes).toHaveLength(1);
        expect(app.ctx.calls.at(-1)).toMatchObject({ name: 'stroke', operation: 'destination-out', size: 24 });
        expect(app.ctx.globalCompositeOperation).toBe('source-over');
        stroke(app, 'brush');
        expect(app.ctx.calls.at(-1).operation).toBe('source-over');
    });

    test('a stationary eraser tap has area, but erasing a blank board does not create answer content', () => {
        const app = host(); app.setTool('eraser');
        app.handlePointerDown(pointer('pointerdown'));
        app.handlePointerUp(pointer('pointerup'));
        expect(app.ctx.arc).toHaveBeenCalledWith(20, 30, 12, 0, Math.PI * 2);
        expect(app.ctx.calls.at(-1)).toMatchObject({ name: 'fill', operation: 'destination-out' });
        expect(app.hasContent).toBe(false);
        app.saveDrawing();
        expect(showToast).toHaveBeenCalled(); expect(app.isOpen).toBe(true);
    });

    test('secondary fingers cannot replace or finish the owning touch stroke; cancellation releases it', () => {
        const app = host(); app.setTool('eraser');
        app.handlePointerDown(pointer('pointerdown', 20, 30, 1, { pointerType: 'touch' }));
        app.handlePointerDown(pointer('pointerdown', 200, 300, 2, { pointerType: 'touch', isPrimary: false }));
        app.handlePointerMove(pointer('pointermove', 400, 300, 2));
        app.handlePointerUp(pointer('pointerup', 400, 300, 2));
        expect(app.isDrawing).toBe(true); expect(app.activeStroke.points).toHaveLength(1);
        app.handlePointerMove(pointer('pointermove', 40, 50, 1, { pointerType: 'touch' }));
        app.handlePointerUp(pointer('pointercancel', 0, 0, 1, { pointerType: 'touch' }));
        expect(app.strokes[0].points.at(-1)).toEqual({ x: 40, y: 50 });
        expect(app.activePointerId).toBeNull(); expect(app.eraserCursorEl.hidden).toBe(true);
        expect(app.canvasEl.releasePointerCapture).toHaveBeenCalledExactlyOnceWith(1);
    });

    test('resizing during a stroke repaints the active erasure with the same design coordinates', () => {
        const app = host(); app.baseline = { canvas: { width: 800, height: 600 } }; app.setTool('eraser');
        app.handlePointerDown(pointer('pointerdown'));
        app.handlePointerMove(pointer('pointermove', 80, 90));
        const points = [...app.activeStroke.points];
        app.stageEl.getBoundingClientRect = () => ({ width: 400, height: 300 });
        app.resizeCanvas();
        expect(app.activeStroke.points).toEqual(points);
        expect(app.ctx.calls.at(-1)).toMatchObject({ name: 'stroke', operation: 'destination-out' });
        expect(app.viewTransform().scale).toBe(0.5);
        app.handlePointerUp(pointer('pointerup', 50, 60));
        expect(app.strokes[0].points.at(-1)).toEqual({ x: 100, y: 120 });
    });

    test('undo during an active stroke releases capture and redo restores the completed stroke', () => {
        const app = host();
        app.handlePointerDown(pointer('pointerdown'));
        app.handlePointerMove(pointer('pointermove', 80, 90));
        app.undo();
        expect(app.isDrawing).toBe(false); expect(app.strokes).toEqual([]);
        app.redo();
        expect(app.strokes[0].points.at(-1)).toEqual({ x: 80, y: 90 });
        stroke(app);
        expect(app.redoStack).toEqual([]);
    });

    test('clear is undoable with the original PNG and erasure intact', () => {
        const app = host(), baseline = { canvas: { width: 800, height: 600 } };
        app.baseline = baseline; stroke(app, 'eraser');
        const erased = app.currentDocument();
        app.clearCanvas(); expect(app.hasContent).toBe(false);
        app.undo(); expect(app.currentDocument()).toEqual(erased); expect(app.baseline).toBe(baseline);
        app.redo(); expect(app.hasContent).toBe(false);
    });

    test('drawing and undo never encode PNG; save finishes a live stroke and keeps white-paper export', () => {
        const app = host(), result = vi.fn(); app.resolveOpen = result;
        app.handlePointerDown(pointer('pointerdown'));
        app.handlePointerMove(pointer('pointermove', 80, 90));
        expect(created).toEqual([]);
        app.saveDrawing();
        expect(app.strokes).toHaveLength(1);
        expect(created).toHaveLength(1);
        expect(created[0].ctx.calls.map(call => call.name)).toEqual(['fillRect', 'drawImage']);
        expect(created[0].ctx.calls[0].color).toBe('#ffffff');
        expect(created[0].ctx.calls[1].args[0]).toBe(app.canvasEl);
        expect(result).toHaveBeenCalledWith(expect.objectContaining({ dataUrl: 'data:image/png;base64,synthetic' }));
    });

    test('the eraser ring uses rendered size and becomes hidden on tool switch', () => {
        const app = host(); app.canvasWidth = 400; app.canvasHeight = 300;
        app.setTool('eraser'); app.handlePointerMove(pointer('pointermove', 60, 70));
        expect(app.eraserCursorEl.style).toMatchObject({ width: '12px', height: '12px', transform: 'translate(54px, 64px)' });
        app.setTool('brush'); expect(app.eraserCursorEl.hidden).toBe(true);
    });
});

describe('student drawing: loading, cancellation and owned resources', () => {
    test('slow image from a closed question cannot overwrite a later open question', async () => {
        const app = host(); app.isOpen = false;
        const first = app.open({ imageUrl: '/old.png' }); const oldLoad = frames.shift()();
        expect(app.isLoading).toBe(true);
        app.handlePointerDown(pointer('pointerdown')); app.saveDrawing();
        expect(app.activeStroke).toBeNull(); expect(app.isOpen).toBe(true);
        const second = app.open({ imageUrl: '/new.png' }); const newLoad = frames.shift()();
        expect(await first).toBeNull();
        images[1].onload(); await newLoad;
        const baseline = app.baseline;
        images[0].onload(); await oldLoad;
        expect(app.baseline).toBe(baseline); expect(baseline.canvas.ctx.calls[0].args[0]).toBe(images[1]);
        expect(app.isLoading).toBe(false);
        app.close(); expect(await second).toBeNull();
    });

    test('failed old-image loading cannot silently save a blank replacement', async () => {
        const app = host(); app.isOpen = false;
        const result = app.open({ imageUrl: '/unavailable.png' }); const loading = frames.shift()();
        images[0].onerror(); await loading;
        app.handlePointerDown(pointer('pointerdown')); app.saveDrawing();
        expect(app.loadFailed).toBe(true); expect(app.controls.save.disabled).toBe(true);
        expect(app.activeStroke).toBeNull(); expect(app.isOpen).toBe(true);
        app.close(); expect(await result).toBeNull();
    });

    test('cancel before the initial frame makes it inert and preserves the next session', async () => {
        const app = host(); app.isOpen = false;
        const first = app.open({ imageUrl: '/old.png' }); app.close();
        const second = app.open();
        await frames.shift()(); expect(images).toEqual([]);
        await frames.shift()(); expect(app.isLoading).toBe(false);
        stroke(app); app.close();
        expect(await first).toBeNull(); expect(await second).toBeNull();
    });

    test('tooltip ownership is bounded by each visible session; close/reopen never reuses a dead binding', async () => {
        const app = host(); app.bindEvents(); app.isOpen = false;
        expect(bindTooltip).toHaveBeenCalledTimes(6);
        expect(bindTooltip.mock.calls.every(args => args[2].hoverDelay === 1000 && args[2].observeRemoval === false)).toBe(true);
        const bindings = [...app.tooltipBindings], root = app.rootEl, layer = app.layerRegistration;
        const first = app.open(); await frames.shift()(); app.close(); await first;
        expect(bindings.every(binding => binding.destroy.mock.calls.length === 1)).toBe(true);
        expect(app.tooltipBindings).toHaveLength(0);
        const second = app.open(); await frames.shift()();
        expect(bindTooltip).toHaveBeenCalledTimes(12);
        const secondBindings = [...app.tooltipBindings];
        app.destroy(); expect(await second).toBeNull();
        expect(secondBindings.every(binding => binding.destroy.mock.calls.length === 1)).toBe(true);
        expect(layer.destroy).toHaveBeenCalledOnce(); expect(layer.refresh).toHaveBeenCalled();
        expect(root.remove).toHaveBeenCalledOnce(); expect(vi.getTimerCount()).toBe(0);
        expect(window.removeEventListener).toHaveBeenCalledWith('resize', app.boundResize);
    });

    test('external layer owns ordering and keeps the closing drawing present until its hide finishes', async () => {
        const app = host(); app.isOpen = false; app.rootEl.hidden = true; app.bindEvents();
        const system = getLayerSystem.mock.results.at(-1).value;
        const adapter = system.registerExternal.mock.calls[0][0];
        expect(adapter.mode).toBe('ordered'); expect(adapter.isPresent()).toBe(false);
        const pending = app.open(); await frames.shift()();
        expect(adapter.isOpen()).toBe(true); expect(adapter.root()).toBe(app.rootEl);
        adapter.dismissTop('escape'); expect(await pending).toBeNull();
        expect(adapter.isOpen()).toBe(false); expect(adapter.isPresent()).toBe(true);
        vi.advanceTimersByTime(180); expect(adapter.isPresent()).toBe(false);
    });

    test('keyboard tool changes respect native fields and a tooltip-consumed Escape', () => {
        const app = host();
        app.handleKeydown({ key: 'e', preventDefault: vi.fn() }); expect(app.settings.tool).toBe('eraser');
        app.handleKeydown({ key: 'b', target: { closest: () => ({}) }, preventDefault: vi.fn() });
        expect(app.settings.tool).toBe('eraser');
        app.handleKeydown({ key: 'Escape', defaultPrevented: true }); expect(app.isOpen).toBe(true);
        app.handleKeydown({ key: 'b', preventDefault: vi.fn() }); expect(app.settings.tool).toBe('brush');
    });
});
