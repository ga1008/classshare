/** Current-tab capture and local annotation. No upload or message side effects. */
import { drawShape, drawStroke } from './whiteboard/renderer.js';
import { MAX_CANVAS_PIXELS } from './whiteboard/constants.js';
import { getLayerSystem } from './lq/layer.js';

const SESSION = Symbol.for('lanshare.ai-workspace-capture');
const MIN_SELECTION = 8;
const DEFAULT_COLOR = '#ef4444';
let styleReady;

function ensureStyle() {
    if (styleReady) return styleReady;
    styleReady = new Promise((resolve, reject) => {
        const link = document.createElement('link');
        link.rel = 'stylesheet';
        link.href = new URL('../css/ai_workspace_capture.css', import.meta.url).href;
        link.dataset.aiCaptureStyle = '';
        const timer = setTimeout(() => failed(), 10000);
        const failed = () => { clearTimeout(timer); link.remove(); styleReady = null; reject(new Error('截图工具加载失败，请重试。')); };
        link.onload = () => { clearTimeout(timer); resolve(); };
        link.onerror = failed;
        document.head.append(link);
    });
    return styleReady;
}

const aborted = () => new DOMException('截图已取消', 'AbortError');
const stopStream = stream => stream?.getTracks().forEach(track => track.stop());
const clamp = (value, maximum) => Math.max(0, Math.min(maximum, value));

/** CSS viewport coordinates -> bounded backing pixels (also handles reverse drags). */
export function capturePixelRect(start, end, viewport, bitmap) {
    const x1 = clamp(Math.min(start.x, end.x), viewport.width);
    const y1 = clamp(Math.min(start.y, end.y), viewport.height);
    const x2 = clamp(Math.max(start.x, end.x), viewport.width);
    const y2 = clamp(Math.max(start.y, end.y), viewport.height);
    const x = Math.floor(x1 * bitmap.width / viewport.width), y = Math.floor(y1 * bitmap.height / viewport.height);
    return { x, y, width: Math.max(0, Math.ceil(x2 * bitmap.width / viewport.width) - x),
        height: Math.max(0, Math.ceil(y2 * bitmap.height / viewport.height) - y) };
}

export function drawCaptureMark(context, mark) {
    if (mark.type === 'stroke') return drawStroke(context, mark);
    if (mark.shape !== 'arrow') return drawShape(context, mark);
    const length = Math.hypot(mark.x2 - mark.x1, mark.y2 - mark.y1);
    if (length < .5) return;
    const angle = Math.atan2(mark.y2 - mark.y1, mark.x2 - mark.x1);
    const head = Math.min(length * .45, Math.max(12, mark.size * 3));
    context.save(); context.strokeStyle = mark.color; context.lineWidth = mark.size;
    context.lineCap = context.lineJoin = 'round'; context.beginPath();
    context.moveTo(mark.x1, mark.y1); context.lineTo(mark.x2, mark.y2);
    for (const turn of [-Math.PI / 6, Math.PI / 6]) {
        context.moveTo(mark.x2, mark.y2);
        context.lineTo(mark.x2 - head * Math.cos(angle + turn), mark.y2 - head * Math.sin(angle + turn));
    }
    context.stroke(); context.restore();
}

function canvas(width, height) {
    const node = document.createElement('canvas');
    node.width = width; node.height = height;
    if (!node.getContext('2d')) throw new Error('当前浏览器无法使用截图画布，请上传截图。');
    return node;
}

/**
 * Must be called directly from a user click (getDisplayMedia needs activation).
 * hideAssistant is invoked synchronously before the chooser; it may return a
 * promise for its exit animation. restoreAssistant always runs after a hide.
 * Resolves with a PNG File for the caller's ordinary attachment flow, or null.
 */
export async function capturePageImage({ hideAssistant, restoreAssistant, notify, signal } = {}) {
    const tell = message => { try { notify?.(message, 'error'); } catch { /* UI notification must not retain capture resources. */ } };
    if (document[SESSION]) { tell('截图工具已经打开。'); return null; }
    const media = navigator.mediaDevices;
    if (!media?.getDisplayMedia || !media.setCaptureHandleConfig || !globalThis.MediaStreamTrack?.prototype.getCaptureHandle) {
        tell('当前浏览器不支持安全识别网页截图。请使用桌面版 Chrome / Edge，或直接上传截图。'); return null;
    }
    if (signal?.aborted) return null;
    let closed = false, hidden = false, stream, video, overlay, external, captureConfigured = false;
    let abortResolve;
    const cancellation = new Promise(resolve => { abortResolve = resolve; });
    const controller = new AbortController();
    const ownedCanvases = [];
    const session = { cancel: () => { if (!closed) { closed = true; abortResolve(); stopStream(stream); } } };
    document[SESSION] = session;
    const race = promise => Promise.race([promise, cancellation.then(() => { throw aborted(); })]);
    const priorFocus = document.activeElement;
    let undoFromKey = () => {};
    const width = window.innerWidth, height = window.innerHeight;
    const makeCanvas = (w, h) => { const value = canvas(w, h); ownedCanvases.push(value); return value; };
    try {
        const readyStyle = ensureStyle();
        // Observe the load rejection immediately while the browser chooser is open.
        readyStyle.catch(() => {});
        const handle = crypto.randomUUID();
        media.setCaptureHandleConfig({ handle, exposeOrigin: true, permittedOrigins: [location.origin] });
        captureConfigured = true;
        hidden = true;
        const assistantHidden = Promise.resolve(hideAssistant?.());
        assistantHidden.catch(() => {});
        // No await before this call: preserve the trusted button activation.
        const request = media.getDisplayMedia({ video: { displaySurface: 'browser', frameRate: 10 }, audio: false,
            preferCurrentTab: true, selfBrowserSurface: 'include', surfaceSwitching: 'exclude', monitorTypeSurfaces: 'exclude' });
        request.then(value => { if (closed) stopStream(value); }, () => {});

        overlay = document.createElement('dialog');
        overlay.className = 'ai-workspace-capture';
        overlay.dataset.phase = 'capture';
        overlay.setAttribute('aria-label', '网页截图与标注');
        // Essential transparent freeze while CSS loads; no UI is painted into the captured frame.
        overlay.style.cssText = 'position:fixed;inset:0;width:100vw;height:100dvh;max-width:none;max-height:none;margin:0;padding:0;border:0;background:transparent;outline:0';
        document.body.append(overlay); overlay.showModal();
        external = getLayerSystem(document).registerExternal({ mode: 'ordered', root: () => overlay,
            trigger: () => priorFocus, isOpen: () => !closed, isPresent: () => !closed,
            dismissTop: () => session.cancel() });
        overlay.addEventListener('cancel', event => { event.preventDefault(); session.cancel(); }, { signal: controller.signal });
        overlay.addEventListener('close', () => session.cancel(), { signal: controller.signal });
        window.addEventListener('keydown', event => {
            if (event.key === 'Escape') { event.preventDefault(); session.cancel(); }
            else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z') { event.preventDefault(); undoFromKey(); }
            else if (event.key !== 'Tab' && !event.target.closest?.('[data-capture-toolbar]')) event.preventDefault();
            // Stop page-level shortcuts while retaining native toolbar/input keys.
            event.stopImmediatePropagation();
        }, { capture: true, signal: controller.signal });
        signal?.addEventListener('abort', session.cancel, { once: true, signal: controller.signal });
        window.addEventListener('pagehide', session.cancel, { signal: controller.signal });
        // A native dialog isolates focus/pointers, but wheel events over its
        // non-scrolling toolbar can otherwise still scroll the document.
        const freezeScroll = event => event.preventDefault();
        document.addEventListener('wheel', freezeScroll, { capture: true, passive: false, signal: controller.signal });
        document.addEventListener('touchmove', freezeScroll, { capture: true, passive: false, signal: controller.signal });
        stream = await race(request);
        const track = stream.getVideoTracks()[0];
        const validateSource = () => {
            const identity = track?.getCaptureHandle?.();
            if (track?.getSettings().displaySurface !== 'browser' || identity?.handle !== handle || identity?.origin !== location.origin) {
                throw new Error('请选择浏览器中的“当前标签页”。窗口、整个屏幕或其他标签页不会被截图。');
            }
        };
        validateSource();
        track.addEventListener('ended', session.cancel, { once: true, signal: controller.signal });
        await race(Promise.all([assistantHidden, readyStyle]));
        await race(new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
        video = document.createElement('video'); video.muted = true; video.playsInline = true; video.srcObject = stream;
        // Wait for a decoded frame after the assistant's exit has completed.
        const frameReady = new Promise((resolve, reject) => {
            const timer = setTimeout(() => reject(new Error('网页截图未能就绪，请重试。')), 10000);
            const done = () => { clearTimeout(timer); resolve(); };
            if (video.requestVideoFrameCallback) video.requestVideoFrameCallback(done);
            else if (video.readyState >= 2) requestAnimationFrame(done);
            else video.addEventListener('loadeddata', done, { once: true, signal: controller.signal });
            controller.signal.addEventListener('abort', () => clearTimeout(timer), { once: true });
        });
        await race(Promise.all([frameReady, video.play()]));
        // Recheck immediately before reading pixels: the captured tab may have
        // navigated or changed while its first video frame was being decoded.
        validateSource();
        if (!video.videoWidth || !video.videoHeight) throw new Error('网页截图为空，请重试。');
        if (window.innerWidth !== width || window.innerHeight !== height) throw new Error('窗口大小已变化，请重新截图。');
        const factor = Math.min(1, 4096 / Math.max(video.videoWidth, video.videoHeight), Math.sqrt(MAX_CANVAS_PIXELS / (video.videoWidth * video.videoHeight)));
        const snapshot = makeCanvas(Math.max(1, Math.round(video.videoWidth * factor)), Math.max(1, Math.round(video.videoHeight * factor)));
        snapshot.getContext('2d').drawImage(video, 0, 0, snapshot.width, snapshot.height);
        // This is a one-frame operation. Sharing ends before crop/annotation begins.
        stopStream(stream); stream = null; video.pause(); video.srcObject = null;

        let complete;
        const result = new Promise(resolve => { complete = resolve; });
        let phase = 'select', crop = null, ink = null, draft = null, selecting = null, color = DEFAULT_COLOR, tool = 'rectangle';
        let pointerId = null, drawingFrame = 0, exporting = false;
        const marks = [];
        const screen = makeCanvas(snapshot.width, snapshot.height);
        screen.className = 'ai-capture-canvas'; screen.setAttribute('aria-label', '拖动框选网页内容');
        overlay.append(screen);
        const toolbar = document.createElement('div'); toolbar.className = 'ai-capture-toolbar'; toolbar.dataset.captureToolbar = '';
        toolbar.innerHTML = `<div class="ai-capture-tools" data-capture-select><span role="status" data-capture-hint>拖动框选网页内容</span><button type="button" data-capture-all>选择整个可见区域</button></div>
            <div class="ai-capture-tools" data-capture-edit hidden><div class="ai-capture-tools" role="group" aria-label="标注工具"><button type="button" data-capture-tool="rectangle" aria-pressed="true">矩形</button><button type="button" data-capture-tool="pen" aria-pressed="false">自由笔</button><button type="button" data-capture-tool="arrow" aria-pressed="false">箭头</button></div>
            <label class="ai-capture-color">颜色<input type="color" aria-label="标注颜色" value="${color}"></label><label class="ai-capture-width">粗细<input type="range" aria-label="标注粗细" min="1" max="10" step="1" value="5"><output>5 px</output></label>
            <button type="button" data-capture-undo disabled>撤销</button><button type="button" data-capture-insert>插入附件</button></div><button type="button" data-capture-cancel>取消</button>`;
        overlay.append(toolbar);
        const selectTools = toolbar.querySelector('[data-capture-select]'), editTools = toolbar.querySelector('[data-capture-edit]');
        const hint = toolbar.querySelector('[data-capture-hint]'), undo = toolbar.querySelector('[data-capture-undo]');
        const widthInput = toolbar.querySelector('input[type="range"]');
        const context = screen.getContext('2d');
        const repaint = () => {
            drawingFrame = 0;
            context.clearRect(0, 0, screen.width, screen.height);
            context.drawImage(crop || snapshot, 0, 0);
            if (phase === 'select') {
                context.fillStyle = 'rgba(10,15,25,.38)'; context.fillRect(0, 0, screen.width, screen.height);
                if (selecting) {
                    const rect = capturePixelRect(selecting.start, selecting.end, { width, height }, snapshot);
                    if (rect.width && rect.height) context.drawImage(snapshot, rect.x, rect.y, rect.width, rect.height, rect.x, rect.y, rect.width, rect.height);
                    context.strokeStyle = '#ffffff'; context.lineWidth = 2 * snapshot.width / width;
                    context.strokeRect(rect.x, rect.y, rect.width, rect.height);
                }
            } else { context.drawImage(ink, 0, 0); if (draft) drawCaptureMark(context, draft); }
        };
        const schedulePaint = () => { if (!drawingFrame) drawingFrame = requestAnimationFrame(repaint); };
        const finishSelection = (start, end) => {
            if (Math.abs(end.x - start.x) < MIN_SELECTION || Math.abs(end.y - start.y) < MIN_SELECTION) {
                selecting = null; hint.textContent = '区域太小，请至少选择 8 × 8 像素'; repaint(); return;
            }
            const rect = capturePixelRect(start, end, { width, height }, snapshot);
            crop = makeCanvas(rect.width, rect.height); ink = makeCanvas(rect.width, rect.height);
            crop.getContext('2d').drawImage(snapshot, rect.x, rect.y, rect.width, rect.height, 0, 0, rect.width, rect.height);
            screen.width = rect.width; screen.height = rect.height;
            phase = 'edit'; overlay.dataset.phase = phase; selectTools.hidden = true; editTools.hidden = false;
            screen.setAttribute('aria-label', '截图标注画布'); repaint();
            toolbar.querySelector('[data-capture-tool]').focus({ preventScroll: true });
        };
        const point = event => {
            const bounds = screen.getBoundingClientRect();
            return phase === 'select' ? { x: clamp((event.clientX - bounds.left) * width / bounds.width, width), y: clamp((event.clientY - bounds.top) * height / bounds.height, height) }
                : { x: clamp((event.clientX - bounds.left) * screen.width / bounds.width, screen.width), y: clamp((event.clientY - bounds.top) * screen.height / bounds.height, screen.height) };
        };
        const undoMark = () => {
            if (exporting || !marks.length) return;
            marks.pop(); const ctx = ink.getContext('2d'); ctx.clearRect(0, 0, ink.width, ink.height);
            marks.forEach(mark => drawCaptureMark(ctx, mark)); undo.disabled = !marks.length; repaint();
        };
        undoFromKey = undoMark;
        screen.addEventListener('pointerdown', event => {
            if (exporting || pointerId !== null || event.button !== 0) return;
            event.preventDefault(); pointerId = event.pointerId; screen.setPointerCapture(pointerId);
            const p = point(event);
            if (phase === 'select') selecting = { start: p, end: p };
            else {
                const size = Number(widthInput.value) * screen.width / screen.getBoundingClientRect().width;
                draft = tool === 'pen' ? { type: 'stroke', points: [p], color, size } : { type: 'shape', shape: tool, x1: p.x, y1: p.y, x2: p.x, y2: p.y, color, size };
            }
            schedulePaint();
        }, { signal: controller.signal });
        const move = event => {
            if (event.pointerId !== pointerId) return;
            const p = point(event);
            if (phase === 'select') selecting.end = p;
            else if (draft?.type === 'stroke') {
                const tail = draft.points.at(-1);
                if (draft.points.length < 8000 && Math.hypot(p.x - tail.x, p.y - tail.y) > .4) draft.points.push(p);
            } else if (draft) { draft.x2 = p.x; draft.y2 = p.y; }
            schedulePaint();
        };
        screen.addEventListener('pointermove', move, { signal: controller.signal });
        screen.addEventListener('pointerup', event => {
            if (event.pointerId !== pointerId) return;
            move(event); screen.releasePointerCapture(pointerId); pointerId = null;
            if (phase === 'select') finishSelection(selecting.start, selecting.end);
            else if (draft) {
                if (marks.length >= 100) { tell('最多添加 100 个标注，可先撤销部分标注。'); }
                else { marks.push(draft); drawCaptureMark(ink.getContext('2d'), draft); undo.disabled = false; }
                draft = null; repaint();
            }
        }, { signal: controller.signal });
        screen.addEventListener('pointercancel', () => { pointerId = null; draft = null; selecting = null; repaint(); }, { signal: controller.signal });
        toolbar.addEventListener('click', async event => {
            const button = event.target.closest('button'); if (!button) return;
            if (button.hasAttribute('data-capture-cancel')) { session.cancel(); return; }
            if (exporting) return;
            if (button.hasAttribute('data-capture-all')) { finishSelection({ x: 0, y: 0 }, { x: width, y: height }); return; }
            if (button.dataset.captureTool) {
                tool = button.dataset.captureTool;
                toolbar.querySelectorAll('[data-capture-tool]').forEach(item => item.setAttribute('aria-pressed', String(item === button)));
            }
            if (button.hasAttribute('data-capture-undo')) undoMark();
            if (button.hasAttribute('data-capture-insert')) {
                exporting = true; button.disabled = true; button.textContent = '正在生成…';
                try {
                    draft = null; repaint();
                    const blob = await race(new Promise((resolve, reject) => screen.toBlob(value => value ? resolve(value) : reject(new Error('截图生成失败，请重试。')), 'image/png')));
                    complete(new File([blob], `网页截图-${new Date().toISOString().replace(/[:.]/g, '-')}.png`, { type: 'image/png', lastModified: Date.now() }));
                } catch (error) {
                    if (error.name !== 'AbortError') tell(error.message);
                    exporting = false; button.disabled = false; button.textContent = '插入附件';
                }
            }
        }, { signal: controller.signal });
        toolbar.querySelector('input[type="color"]').addEventListener('input', event => { color = event.target.value; }, { signal: controller.signal });
        widthInput.addEventListener('input', () => { toolbar.querySelector('output').textContent = `${widthInput.value} px`; }, { signal: controller.signal });
        window.addEventListener('resize', session.cancel, { once: true, signal: controller.signal });
        controller.signal.addEventListener('abort', () => cancelAnimationFrame(drawingFrame), { once: true });
        overlay.dataset.phase = phase; repaint(); toolbar.querySelector('[data-capture-all]').focus({ preventScroll: true });
        return await race(result);
    } catch (error) {
        if (!['AbortError', 'NotAllowedError'].includes(error?.name)) tell(error?.message || '网页截图失败，请重试或上传截图。');
        return null;
    } finally {
        closed = true; abortResolve(); controller.abort(); stopStream(stream);
        if (video) { video.pause(); video.srcObject = null; }
        if (captureConfigured) { try { media.setCaptureHandleConfig({ handle: '', permittedOrigins: [] }); } catch { /* Browser may be unloading. */ } }
        if (overlay?.open) overlay.close();
        overlay?.remove(); external?.destroy();
        ownedCanvases.forEach(node => { node.width = node.height = 1; });
        if (document[SESSION] === session) delete document[SESSION];
        if (hidden) { try { await restoreAssistant?.(); } catch { tell('助手窗口未能恢复，请重新打开助手。'); } }
    }
}
