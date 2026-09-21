/**
 * LanShare shared image lightbox — "liquid glass" edition.
 *
 * One module for every place that previews images (assignment attachments,
 * private messages, learning documents, AI chat, blog…).  Visual language is
 * ported from the 概念池 learning-document page: white glass panels with a
 * deep blur, soft drifting colour blobs behind, pill buttons.
 *
 * Usage (programmatic):
 *   import { openImageLightbox } from '/static/js/ls_image_lightbox.js';
 *   openImageLightbox({
 *     items: [{ src, previewSrc?, originalSrc?, title?, meta?, downloadUrl? }],
 *     index: 0,
 *     groupLabel: '第 2 题附件',
 *   });
 *
 * Usage (declarative):
 *   <img data-ls-lightbox data-ls-lightbox-src="…" data-ls-lightbox-group="q2" data-ls-lightbox-title="…">
 *   Elements sharing a group form the prev/next set in DOM order.
 *   Call bindImageLightboxDelegation() once (done automatically on import).
 *
 * Interactions: wheel = zoom around cursor, drag = pan, double-click = fit/1:1,
 * two-finger pinch, swipe at fit view = prev/next, ←/→, +/−, 0 = fit, Esc.
 */

import { getLayerSystem } from './lq/layer.js';

const ZOOM_FACTOR = 1.18;
const EPSILON = 0.001;
const SWIPE_THRESHOLD = 70;

let root = null;
let refs = null;
let layerHandle = null;
const delegations = new Map();

const state = {
    items: [],
    index: 0,
    groupLabel: '',
    scale: 1,
    fitScale: 1,
    minScale: 0.25,
    maxScale: 8,
    left: 0,
    top: 0,
    naturalWidth: 0,
    naturalHeight: 0,
    stageWidth: 0,
    stageHeight: 0,
    loaded: false,
    dragging: false,
    pointers: new Map(),
    pinchStartDistance: 0,
    pinchStartScale: 1,
    dragStart: null,
    movedDuringDrag: false,
    pointerStartedOnImage: false,
    returnFocus: null,
    loadToken: 0,
    open: false,
};

function clamp(value, min, max) {
    return Math.min(Math.max(value, min), max);
}

function escapeText(value) {
    return String(value ?? '');
}

function createRoot() {
    if (root) return root;
    root = document.createElement('div');
    root.className = 'ls-lightbox lq-lightbox';
    root.hidden = true;
    root.setAttribute('role', 'dialog');
    root.setAttribute('aria-modal', 'true');
    root.setAttribute('aria-label', '图片预览');
    root.innerHTML = `
        <div class="ls-lightbox__bg lq-lightbox__bg lq-scrim" aria-hidden="true">
            <span class="ls-lightbox__blob ls-lightbox__blob--1 lq-lightbox__blob lq-lightbox__blob--1"></span>
            <span class="ls-lightbox__blob ls-lightbox__blob--2 lq-lightbox__blob lq-lightbox__blob--2"></span>
            <span class="ls-lightbox__blob ls-lightbox__blob--3 lq-lightbox__blob lq-lightbox__blob--3"></span>
        </div>
        <div class="ls-lightbox__shell lq-lightbox__shell">
            <header class="ls-lightbox__bar ls-glass lq-lightbox__bar lq-glass lq-glass--clear">
                <div class="ls-lightbox__copy lq-lightbox__copy">
                    <strong class="ls-lightbox__title lq-lightbox__title" data-ref="title">图片预览</strong>
                    <span class="ls-lightbox__meta lq-lightbox__meta" data-ref="meta"></span>
                </div>
                <div class="ls-lightbox__actions lq-lightbox__actions">
                    <span class="ls-lightbox__counter lq-lightbox__counter" data-ref="counter"></span>
                    <button type="button" class="ls-glass-pill ls-lightbox__pill lq-lightbox__pill" data-act="zoom-out" aria-label="缩小" title="缩小（−）">−</button>
                    <span class="ls-lightbox__scale lq-lightbox__scale" data-ref="scale">100%</span>
                    <button type="button" class="ls-glass-pill ls-lightbox__pill lq-lightbox__pill" data-act="zoom-in" aria-label="放大" title="放大（+）">+</button>
                    <button type="button" class="ls-glass-pill ls-lightbox__pill lq-lightbox__pill" data-act="fit" title="适应窗口（0）">适应</button>
                    <a class="ls-glass-pill ls-lightbox__pill lq-lightbox__pill" data-act="original" target="_blank" rel="noopener noreferrer" title="在新标签页打开原图">原图</a>
                    <button type="button" class="ls-glass-pill ls-lightbox__pill ls-lightbox__close lq-lightbox__pill lq-lightbox__close" data-act="close" aria-label="关闭预览" title="关闭（Esc）">×</button>
                </div>
            </header>
            <div class="ls-lightbox__stage lq-lightbox__stage" data-ref="stage">
                <img class="ls-lightbox__img lq-lightbox__img" data-ref="img" alt="" draggable="false">
                <div class="ls-lightbox__spinner lq-lightbox__spinner" data-ref="spinner" hidden></div>
                <div class="ls-lightbox__error lq-lightbox__error" data-ref="error" hidden>图片加载失败</div>
            </div>
            <button type="button" class="ls-lightbox__nav ls-lightbox__nav--prev ls-glass lq-lightbox__nav lq-lightbox__nav--prev" data-act="prev" aria-label="上一张" title="上一张（←）">‹</button>
            <button type="button" class="ls-lightbox__nav ls-lightbox__nav--next ls-glass lq-lightbox__nav lq-lightbox__nav--next" data-act="next" aria-label="下一张" title="下一张（→）">›</button>
            <div class="ls-lightbox__hint ls-glass-pill lq-lightbox__hint" aria-hidden="true">滚轮缩放 · 拖拽移动 · ← → 切换 · Esc 关闭</div>
        </div>
    `;
    document.body.appendChild(root);
    refs = {};
    root.querySelectorAll('[data-ref]').forEach((el) => { refs[el.dataset.ref] = el; });
    refs.prev = root.querySelector('[data-act="prev"]');
    refs.next = root.querySelector('[data-act="next"]');
    refs.original = root.querySelector('[data-act="original"]');
    refs.zoomIn = root.querySelector('[data-act="zoom-in"]');
    refs.zoomOut = root.querySelector('[data-act="zoom-out"]');
    refs.close = root.querySelector('[data-act="close"]');
    bindRootEvents();
    return root;
}

function currentItem() {
    return state.items[state.index] || null;
}

function renderedWidth(scale = state.scale) { return state.naturalWidth * scale; }
function renderedHeight(scale = state.scale) { return state.naturalHeight * scale; }

function clampOffsets(left, top, scale = state.scale) {
    const width = renderedWidth(scale);
    const height = renderedHeight(scale);
    const nextLeft = width <= state.stageWidth + EPSILON
        ? (state.stageWidth - width) / 2
        : clamp(left, state.stageWidth - width, 0);
    const nextTop = height <= state.stageHeight + EPSILON
        ? (state.stageHeight - height) / 2
        : clamp(top, state.stageHeight - height, 0);
    return { left: nextLeft, top: nextTop };
}

function isPannable() {
    return renderedWidth() > state.stageWidth + EPSILON || renderedHeight() > state.stageHeight + EPSILON;
}

function isAtFit() {
    return Math.abs(state.scale - state.fitScale) < EPSILON;
}

function measureStage() {
    if (!refs?.stage) return;
    const rect = refs.stage.getBoundingClientRect();
    state.stageWidth = Math.max(rect.width, 1);
    state.stageHeight = Math.max(rect.height, 1);
}

function computeFitScale() {
    if (!state.naturalWidth || !state.naturalHeight) return 1;
    const fit = Math.min(state.stageWidth / state.naturalWidth, state.stageHeight / state.naturalHeight, 1);
    return fit > 0 ? fit : 1;
}

function applyTransform() {
    if (!refs?.img || !state.loaded) return;
    const offsets = clampOffsets(state.left, state.top, state.scale);
    state.left = offsets.left;
    state.top = offsets.top;
    refs.img.style.width = `${state.naturalWidth}px`;
    refs.img.style.height = `${state.naturalHeight}px`;
    refs.img.style.transform = `translate3d(${state.left}px, ${state.top}px, 0) scale(${state.scale})`;
    refs.scale.textContent = `${Math.round(state.scale * 100)}%`;
    refs.stage.classList.toggle('is-pannable', isPannable());
    refs.stage.classList.toggle('is-zoomed', !isAtFit());
    refs.stage.classList.toggle('is-dragging', state.dragging);
    refs.zoomOut.disabled = state.scale <= state.minScale + EPSILON;
    refs.zoomIn.disabled = state.scale >= state.maxScale - EPSILON;
}

function fitToStage() {
    measureStage();
    state.fitScale = computeFitScale();
    state.minScale = Math.min(state.fitScale, 1) * 0.5;
    state.maxScale = Math.max(state.fitScale * 8, 4);
    state.scale = state.fitScale;
    const centered = clampOffsets(0, 0, state.scale);
    state.left = centered.left;
    state.top = centered.top;
    applyTransform();
}

function zoomTo(nextScale, anchor) {
    if (!state.loaded) return;
    const target = clamp(nextScale, state.minScale, state.maxScale);
    if (Math.abs(target - state.scale) < EPSILON) return;
    const rect = refs.stage.getBoundingClientRect();
    const anchorX = anchor ? anchor.clientX - rect.left : state.stageWidth / 2;
    const anchorY = anchor ? anchor.clientY - rect.top : state.stageHeight / 2;
    const ratio = target / state.scale;
    state.left = anchorX - (anchorX - state.left) * ratio;
    state.top = anchorY - (anchorY - state.top) * ratio;
    state.scale = target;
    applyTransform();
}

function zoomBy(factor, anchor) {
    zoomTo(state.scale * factor, anchor);
}

function toggleActualSize(anchor) {
    if (!isAtFit()) {
        fitToStage();
        return;
    }
    zoomTo(Math.max(1, state.fitScale * 2), anchor);
}

function updateChrome() {
    const item = currentItem();
    const total = state.items.length;
    refs.title.textContent = escapeText(item?.title || '图片预览');
    refs.meta.textContent = [state.groupLabel, item?.meta].filter(Boolean).join(' · ');
    refs.counter.textContent = total > 1 ? `${state.index + 1} / ${total}` : '';
    refs.counter.hidden = total <= 1;
    refs.prev.hidden = total <= 1;
    refs.next.hidden = total <= 1;
    refs.prev.disabled = state.index <= 0;
    refs.next.disabled = state.index >= total - 1;
    const originalUrl = item?.originalSrc || item?.downloadUrl || item?.src || '';
    refs.original.href = originalUrl || '#';
    refs.original.hidden = !originalUrl;
}

function preloadNeighbours() {
    [state.index - 1, state.index + 1].forEach((idx) => {
        const item = state.items[idx];
        if (!item) return;
        const preload = new Image();
        preload.decoding = 'async';
        preload.src = item.previewSrc || item.src;
    });
}

function loadCurrent() {
    const item = currentItem();
    if (!item || !refs) return;
    const token = ++state.loadToken;
    state.loaded = false;
    refs.error.hidden = true;
    refs.spinner.hidden = false;
    refs.img.classList.remove('is-ready');
    refs.img.alt = escapeText(item.title || '图片');
    refs.img.style.transform = '';
    updateChrome();

    const source = item.previewSrc || item.src;
    const probe = new Image();
    probe.decoding = 'async';
    probe.onload = () => {
        if (token !== state.loadToken) return;
        state.naturalWidth = probe.naturalWidth || probe.width || 1;
        state.naturalHeight = probe.naturalHeight || probe.height || 1;
        refs.img.src = source;
        state.loaded = true;
        refs.spinner.hidden = true;
        refs.img.classList.add('is-ready');
        fitToStage();
        if (!item.meta && state.naturalWidth && state.naturalHeight) {
            refs.meta.textContent = [state.groupLabel, `${state.naturalWidth}×${state.naturalHeight}`].filter(Boolean).join(' · ');
        }
        preloadNeighbours();
    };
    probe.onerror = () => {
        if (token !== state.loadToken) return;
        // Fall back to the original when the compressed variant fails.
        if (item.previewSrc && source === item.previewSrc && item.src && item.src !== item.previewSrc) {
            const fallbackItem = { ...item, previewSrc: '' };
            state.items[state.index] = fallbackItem;
            loadCurrent();
            return;
        }
        refs.spinner.hidden = true;
        refs.error.hidden = false;
    };
    probe.src = source;
}

function go(delta) {
    const next = state.index + delta;
    if (next < 0 || next >= state.items.length) return;
    state.index = next;
    loadCurrent();
}

/* ---------- pointer handling (drag / pinch / swipe) ---------- */

function pointerDistance() {
    const points = Array.from(state.pointers.values());
    if (points.length < 2) return 0;
    const [a, b] = points;
    return Math.hypot(a.x - b.x, a.y - b.y);
}

function pointerCenter() {
    const points = Array.from(state.pointers.values());
    if (!points.length) return null;
    const sum = points.reduce((acc, p) => ({ x: acc.x + p.x, y: acc.y + p.y }), { x: 0, y: 0 });
    return { clientX: sum.x / points.length, clientY: sum.y / points.length };
}

function onPointerDown(event) {
    if (!state.loaded) return;
    if (event.button !== undefined && event.button !== 0) return;
    event.preventDefault();
    // Pointer capture retargets the later click/dblclick to the stage. Keep
    // the original hit so an image tap is not mistaken for empty backdrop.
    if (state.pointers.size === 0) state.pointerStartedOnImage = event.target === refs.img;
    state.pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
    try { refs.stage.setPointerCapture(event.pointerId); } catch { /* ignore */ }
    if (state.pointers.size === 2) {
        state.pinchStartDistance = pointerDistance();
        state.pinchStartScale = state.scale;
        state.dragStart = null;
        return;
    }
    state.dragging = true;
    state.movedDuringDrag = false;
    state.dragStart = { x: event.clientX, y: event.clientY, left: state.left, top: state.top };
    applyTransform();
}

function onPointerMove(event) {
    if (!state.pointers.has(event.pointerId)) return;
    state.pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
    if (state.pointers.size >= 2 && state.pinchStartDistance > 0) {
        const distance = pointerDistance();
        if (distance > 0) {
            const nextScale = state.pinchStartScale * (distance / state.pinchStartDistance);
            zoomTo(nextScale, pointerCenter());
        }
        return;
    }
    if (!state.dragging || !state.dragStart) return;
    const dx = event.clientX - state.dragStart.x;
    const dy = event.clientY - state.dragStart.y;
    if (Math.abs(dx) > 3 || Math.abs(dy) > 3) state.movedDuringDrag = true;
    if (isPannable()) {
        state.left = state.dragStart.left + dx;
        state.top = state.dragStart.top + dy;
        applyTransform();
    }
}

function onPointerUp(event) {
    const start = state.dragStart;
    state.pointers.delete(event.pointerId);
    try { refs.stage.releasePointerCapture(event.pointerId); } catch { /* ignore */ }
    if (state.pointers.size < 2) {
        state.pinchStartDistance = 0;
    }
    if (state.pointers.size > 0) return;
    const wasDragging = state.dragging;
    state.dragging = false;
    state.dragStart = null;
    applyTransform();
    if (event.type === 'pointercancel') {
        state.movedDuringDrag = true;
        return;
    }
    if (!wasDragging || !start) return;
    // Swipe between images when the picture is not zoomed in.
    if (!isPannable() && event.pointerType !== 'mouse') {
        const dx = event.clientX - start.x;
        const dy = event.clientY - start.y;
        if (Math.abs(dx) > SWIPE_THRESHOLD && Math.abs(dx) > Math.abs(dy) * 1.5) {
            go(dx < 0 ? 1 : -1);
        }
    }
}

function onWheel(event) {
    if (!state.loaded) return;
    event.preventDefault();
    const factor = event.deltaY > 0 ? 1 / ZOOM_FACTOR : ZOOM_FACTOR;
    zoomBy(factor, { clientX: event.clientX, clientY: event.clientY });
}

function onKeydown(event) {
    if (!state.open || getLayerSystem(document).top() !== layerHandle || event.defaultPrevented || event.isComposing || event.keyCode === 229) return;
    switch (event.key) {
        case 'ArrowLeft':
            event.preventDefault();
            go(-1);
            break;
        case 'ArrowRight':
            event.preventDefault();
            go(1);
            break;
        case '+':
        case '=':
        case 'Add':
            event.preventDefault();
            zoomBy(ZOOM_FACTOR);
            break;
        case '-':
        case '_':
        case 'Subtract':
            event.preventDefault();
            zoomBy(1 / ZOOM_FACTOR);
            break;
        case '0':
            event.preventDefault();
            fitToStage();
            break;
        default:
            break;
    }
}

function bindRootEvents() {
    root.addEventListener('click', (event) => {
        const actor = event.target.closest('[data-act]');
        if (actor) {
            const action = actor.dataset.act;
            if (action === 'close') closeImageLightbox();
            else if (action === 'prev') go(-1);
            else if (action === 'next') go(1);
            else if (action === 'zoom-in') zoomBy(ZOOM_FACTOR);
            else if (action === 'zoom-out') zoomBy(1 / ZOOM_FACTOR);
            else if (action === 'fit') fitToStage();
            return;
        }
        // Click on the backdrop (outside the image) closes.
        if (event.target === refs.stage && !state.pointerStartedOnImage && !state.movedDuringDrag) {
            closeImageLightbox();
        }
    });
    refs.stage.addEventListener('pointerdown', onPointerDown);
    refs.stage.addEventListener('pointermove', onPointerMove);
    refs.stage.addEventListener('pointerup', onPointerUp);
    refs.stage.addEventListener('pointercancel', onPointerUp);
    refs.stage.addEventListener('wheel', onWheel, { passive: false });
    refs.stage.addEventListener('dblclick', (event) => {
        if (event.target !== refs.img && !(event.target === refs.stage && state.pointerStartedOnImage)) return;
        event.preventDefault();
        toggleActualSize({ clientX: event.clientX, clientY: event.clientY });
    });
    refs.img.addEventListener('click', (event) => {
        if (state.movedDuringDrag) {
            state.movedDuringDrag = false;
            event.preventDefault();
        }
    });
}

function onResize() {
    if (!state.open || !state.loaded) return;
    const wasAtFit = isAtFit();
    measureStage(); state.fitScale = computeFitScale();
    if (wasAtFit) fitToStage(); else applyTransform();
}

function beginClose() {
    state.open = false;
    root?.classList.remove('is-visible');
    document.removeEventListener('keydown', onKeydown, true);
    window.removeEventListener('resize', onResize);
    state.loadToken += 1; state.loaded = false;
    for (const pointerId of state.pointers.keys()) { try { refs?.stage.releasePointerCapture(pointerId); } catch { /* already released */ } }
    state.pointers.clear(); state.dragging = false; state.dragStart = null; state.pinchStartDistance = 0;
    state.pointerStartedOnImage = false;
}

function finishClose(_reason, handle) {
    if (handle !== layerHandle) return;
    beginClose();
    root.hidden = true; root.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('ls-lightbox-open');
    refs.img.removeAttribute('src'); refs.img.classList.remove('is-ready');
    state.returnFocus = null; layerHandle = null;
}

/* ---------- public API ---------- */

function normalizeItem(item) {
    if (!item) return null;
    if (typeof item === 'string') return { src: item, previewSrc: '', originalSrc: item, title: '' };
    const src = String(item.src || item.originalSrc || item.previewSrc || '');
    if (!src) return null;
    return {
        src,
        previewSrc: String(item.previewSrc || ''),
        originalSrc: String(item.originalSrc || item.downloadUrl || ''),
        downloadUrl: String(item.downloadUrl || ''),
        title: String(item.title || ''),
        meta: String(item.meta || ''),
    };
}

export function openImageLightbox(options = {}) {
    const items = (Array.isArray(options.items) ? options.items : [options.items])
        .map(normalizeItem)
        .filter(Boolean);
    if (!items.length) return false;
    createRoot();
    const layer = getLayerSystem(document);
    if (!layerHandle || ['closed', 'destroyed'].includes(layerHandle.state)) {
        state.returnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
        const host = layer.getPortalHost({ trigger: state.returnFocus });
        if (root.parentElement !== host) host.appendChild(root);
    }
    state.items = items;
    state.index = clamp(Number(options.index) || 0, 0, items.length - 1);
    state.groupLabel = String(options.groupLabel || '');
    root.hidden = false;
    root.setAttribute('aria-hidden', 'false');
    window.getComputedStyle(root).opacity;
    root.classList.add('is-visible');
    state.open = true;
    document.addEventListener('keydown', onKeydown, true);
    window.addEventListener('resize', onResize);
    // Load immediately (rAF may be throttled in background tabs); only the
    // fade-in class waits for the next frame.
    loadCurrent();
    layerHandle = layer.open(root, { type: 'viewer', modality: 'modal', trigger: state.returnFocus,
        initialFocus: refs.close, returnFocus: state.returnFocus,
        closeOnOutside: false, onCloseRequested: beginClose, onClose: finishClose, onDestroy: finishClose,
    });
    return true;
}

export function closeImageLightbox() {
    if (!layerHandle) return;
    beginClose();
    return getLayerSystem(document).close(layerHandle, 'button');
}

export function isImageLightboxOpen() {
    return state.open;
}

function itemFromElement(el) {
    const data = el.dataset || {};
    const img = el.tagName === 'IMG' ? el : el.querySelector('img');
    const src = data.lsLightboxSrc || data.lsLightboxOriginal || img?.currentSrc || img?.src || el.getAttribute('href') || '';
    if (!src) return null;
    return {
        src,
        previewSrc: data.lsLightboxPreview || '',
        originalSrc: data.lsLightboxOriginal || '',
        downloadUrl: data.lsLightboxDownload || '',
        title: data.lsLightboxTitle || img?.alt || el.getAttribute('title') || '',
        meta: data.lsLightboxMeta || '',
    };
}

/**
 * Collect the navigation group for an element: all `[data-ls-lightbox]`
 * elements that share its `data-ls-lightbox-group` (DOM order).  Elements
 * without a group open alone.
 */
export function collectLightboxGroup(el) {
    const group = el?.dataset?.lsLightboxGroup || '';
    const scope = el.closest('[data-ls-lightbox-scope]') || document;
    const nodes = group
        ? Array.from(scope.querySelectorAll('[data-ls-lightbox]')).filter((node) => (node.dataset.lsLightboxGroup || '') === group)
        : [el];
    const items = [];
    let index = 0;
    nodes.forEach((node) => {
        const item = itemFromElement(node);
        if (!item) return;
        if (node === el) index = items.length;
        items.push(item);
    });
    const label = el.closest('[data-ls-lightbox-label]')?.dataset.lsLightboxLabel || '';
    return { items, index, groupLabel: label };
}

export function bindImageLightboxDelegation(scope = document) {
    if (delegations.has(scope)) return delegations.get(scope).dispose;
    const listener = (event) => {
        if (event.defaultPrevented) return;
        const trigger = event.target.closest?.('[data-ls-lightbox]');
        if (!trigger || trigger.hasAttribute('data-ls-lightbox-disabled')) return;
        const group = collectLightboxGroup(trigger);
        if (!group.items.length) return;
        event.preventDefault();
        openImageLightbox(group);
    };
    const dispose = () => { scope.removeEventListener('click', listener); delegations.delete(scope); };
    delegations.set(scope, { dispose }); scope.addEventListener('click', listener);
    return dispose;
}

export function destroyImageLightbox() {
    layerHandle?.destroy(); beginClose();
    root?.remove(); root = null; refs = null;
    for (const { dispose } of [...delegations.values()]) dispose();
}

bindImageLightboxDelegation(document);

const api = { open: openImageLightbox, close: closeImageLightbox, isOpen: isImageLightboxOpen, collectGroup: collectLightboxGroup, destroy: destroyImageLightbox };
if (typeof window !== 'undefined') {
    window.LsImageLightbox = api;
}
export default api;
