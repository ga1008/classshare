import { apiFetch } from './api.js';
import { showToast } from './ui.js';

const material = window.MATERIAL_VIEWER || {};

const contentEl = document.getElementById('viewer-content');
const tocEl = document.getElementById('viewer-toc');
const tocCountEl = document.getElementById('viewer-toc-count');
const lightboxEl = document.getElementById('viewer-image-lightbox');
const lightboxStageEl = document.getElementById('viewer-image-lightbox-stage');
const lightboxImgEl = document.getElementById('viewer-image-lightbox-img');
const lightboxTitleEl = document.getElementById('viewer-image-lightbox-title');
const lightboxScaleEl = document.getElementById('viewer-image-lightbox-scale');
const zoomOutBtn = document.getElementById('viewer-image-zoom-out-btn');
const zoomInBtn = document.getElementById('viewer-image-zoom-in-btn');
const fullscreenBtn = document.getElementById('viewer-image-fullscreen-btn');
const closeBtn = document.getElementById('viewer-image-close-btn');
const editSourceBtn = document.getElementById('viewer-edit-source-btn');
const editorBackdropEl = document.getElementById('viewer-source-editor');
const editorEncodingEl = document.getElementById('viewer-editor-encoding');
const editorTextareaEl = document.getElementById('viewer-editor-textarea');
const editorSaveBtn = document.getElementById('viewer-editor-save-btn');
const editorCancelBtn = document.getElementById('viewer-editor-cancel-btn');

const LIGHTBOX_ZOOM_FACTOR = 1.2;
const LIGHTBOX_EPSILON = 0.01;

const lightboxState = {
    scale: 1,
    fitScale: 1,
    maxScale: 4,
    src: '',
    title: '',
    naturalWidth: 0,
    naturalHeight: 0,
    stageWidth: 0,
    stageHeight: 0,
    left: 0,
    top: 0,
    loaded: false,
    clickZoomEnabled: false,
    dragging: false,
    dragMoved: false,
    dragPointerId: null,
    dragStartX: 0,
    dragStartY: 0,
    dragLeft: 0,
    dragTop: 0,
    suppressClick: false,
};

let lightboxSyncFrame = null;
let stageResizeObserver = null;
let editorLoadingPromise = null;
let editorLoaded = false;
let editorEncoding = material.content_encoding || 'utf-8';

function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

function buildBlockedDownloadButton() {
    const title = escapeHtml(material.download_blocked_reason || '当前材料已限制下载');
    return `
        <button type="button" class="btn btn-danger resource-download-blocked-btn" data-download-blocked="true" title="${title}" aria-label="${title}">
            <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <circle cx="12" cy="12" r="9"></circle>
                <path d="M5 5l14 14"></path>
            </svg>
            已限制下载
        </button>
    `;
}

function buildMaterialDownloadAction(label = '\u4e0b\u8f7d\u539f\u6587\u4ef6') {
    if (material.download_allowed === false) {
        return buildBlockedDownloadButton();
    }
    return `<a href="${material.download_url}" class="btn btn-primary">${label}</a>`;
}

function parseMarkdownHtml(value) {
    const runtime = window.MarkdownRuntime;
    if (runtime && typeof runtime.parse === 'function') {
        return runtime.parse(value, {
            fallbackMode: 'pre',
            silent: true,
        });
    }

    return `<pre>${escapeHtml(String(value ?? ''))}</pre>`;
}

function isExternalLink(href) {
    return /^(?:[a-z]+:)?\/\//i.test(href) || href.startsWith('mailto:');
}

function isImageUrl(url) {
    if (!url) return false;
    if (url.includes('/materials/raw/')) return true;
    return /\.(png|jpe?g|gif|svg|webp|bmp|ico)(?:[?#].*)?$/i.test(url);
}

function resolveRelativeTarget(basePath, rawHref) {
    if (!rawHref || rawHref.startsWith('#') || isExternalLink(rawHref)) {
        return null;
    }

    const [rawPathPart, rawHash = ''] = rawHref.split('#');
    let pathPart = rawPathPart;
    try {
        pathPart = decodeURIComponent(rawPathPart);
    } catch {
        pathPart = rawPathPart;
    }
    pathPart = pathPart.replace(/\\/g, '/');
    const baseSegments = String(basePath || '').split('/').slice(0, -1);
    const resolvedSegments = pathPart.startsWith('/') ? [] : baseSegments;

    for (const segment of pathPart.split('/')) {
        if (!segment || segment === '.') continue;
        if (segment === '..') {
            resolvedSegments.pop();
            continue;
        }
        resolvedSegments.push(segment);
    }

    return {
        path: resolvedSegments.join('/'),
        hash: rawHash ? `#${rawHash}` : '',
    };
}

function slugify(text) {
    return String(text || '')
        .toLowerCase()
        .replace(/[\s\W-]+/g, '-')
        .replace(/^-+|-+$/g, '') || 'section';
}

function clamp(value, min, max) {
    return Math.min(max, Math.max(min, value));
}

function nearlyEqual(a, b, epsilon = LIGHTBOX_EPSILON) {
    return Math.abs(a - b) <= epsilon;
}

function updateFullscreenButton() {
    if (!fullscreenBtn) return;
    fullscreenBtn.textContent = document.fullscreenElement ? '\u9000\u51fa\u5168\u5c4f' : '\u5168\u5c4f';
}

function getImageTitle(source, fallbackTitle = '\u56fe\u7247\u9884\u89c8') {
    if (!source) return fallbackTitle;
    try {
        const parsedUrl = new URL(source, window.location.origin);
        const fileName = decodeURIComponent(parsedUrl.pathname.split('/').pop() || '');
        return fileName || fallbackTitle;
    } catch {
        return fallbackTitle;
    }
}

function getStageMetrics() {
    if (!lightboxStageEl) {
        return { width: 0, height: 0 };
    }

    return {
        width: lightboxStageEl.clientWidth,
        height: lightboxStageEl.clientHeight,
    };
}

function getRenderedWidth(scale = lightboxState.scale) {
    return lightboxState.naturalWidth * scale;
}

function getRenderedHeight(scale = lightboxState.scale) {
    return lightboxState.naturalHeight * scale;
}

function getCenteredOffsets(scale = lightboxState.scale) {
    return {
        left: (lightboxState.stageWidth - getRenderedWidth(scale)) / 2,
        top: (lightboxState.stageHeight - getRenderedHeight(scale)) / 2,
    };
}

function clampLightboxOffsets(left, top, scale = lightboxState.scale) {
    const renderedWidth = getRenderedWidth(scale);
    const renderedHeight = getRenderedHeight(scale);

    if (renderedWidth <= lightboxState.stageWidth + LIGHTBOX_EPSILON) {
        left = (lightboxState.stageWidth - renderedWidth) / 2;
    } else {
        left = clamp(left, lightboxState.stageWidth - renderedWidth, 0);
    }

    if (renderedHeight <= lightboxState.stageHeight + LIGHTBOX_EPSILON) {
        top = (lightboxState.stageHeight - renderedHeight) / 2;
    } else {
        top = clamp(top, lightboxState.stageHeight - renderedHeight, 0);
    }

    return { left, top };
}

function isStageReady() {
    return Boolean(
        lightboxState.loaded
        && lightboxState.naturalWidth
        && lightboxState.naturalHeight
        && lightboxState.stageWidth
        && lightboxState.stageHeight
    );
}

function isPannable(scale = lightboxState.scale) {
    return (
        getRenderedWidth(scale) > lightboxState.stageWidth + LIGHTBOX_EPSILON
        || getRenderedHeight(scale) > lightboxState.stageHeight + LIGHTBOX_EPSILON
    );
}

function isAtFitView() {
    if (!isStageReady()) return true;
    const centered = getCenteredOffsets(lightboxState.fitScale);
    return (
        nearlyEqual(lightboxState.scale, lightboxState.fitScale)
        && nearlyEqual(lightboxState.left, centered.left, 0.5)
        && nearlyEqual(lightboxState.top, centered.top, 0.5)
    );
}

function updateLightboxScaleLabel() {
    if (!lightboxScaleEl) return;
    lightboxScaleEl.textContent = `${Math.round(lightboxState.scale * 100)}%`;
}

function updateLightboxButtons() {
    if (zoomOutBtn) {
        zoomOutBtn.disabled = !isStageReady() || lightboxState.scale <= lightboxState.fitScale + LIGHTBOX_EPSILON;
    }
    if (zoomInBtn) {
        zoomInBtn.disabled = !isStageReady() || lightboxState.scale >= lightboxState.maxScale - LIGHTBOX_EPSILON;
    }
}

function updateLightboxStageState() {
    if (!lightboxStageEl) return;

    lightboxStageEl.classList.toggle('is-pannable', isPannable());
    lightboxStageEl.classList.toggle('is-zoomable', lightboxState.clickZoomEnabled && isAtFitView());
    lightboxStageEl.classList.toggle('is-zoomed', lightboxState.clickZoomEnabled && !isAtFitView());
    lightboxStageEl.classList.toggle('is-dragging', lightboxState.dragging);
}

function renderLightboxImage() {
    if (!lightboxImgEl || !isStageReady()) {
        updateLightboxScaleLabel();
        updateLightboxButtons();
        updateLightboxStageState();
        return;
    }

    const clampedOffsets = clampLightboxOffsets(lightboxState.left, lightboxState.top, lightboxState.scale);
    lightboxState.left = clampedOffsets.left;
    lightboxState.top = clampedOffsets.top;

    lightboxImgEl.style.width = `${lightboxState.naturalWidth}px`;
    lightboxImgEl.style.height = `${lightboxState.naturalHeight}px`;
    lightboxImgEl.style.transform = `translate3d(${lightboxState.left}px, ${lightboxState.top}px, 0) scale(${lightboxState.scale})`;

    updateLightboxScaleLabel();
    updateLightboxButtons();
    updateLightboxStageState();
}

function resetLightboxView() {
    if (!isStageReady()) return;
    const centered = getCenteredOffsets(lightboxState.fitScale);
    lightboxState.scale = lightboxState.fitScale;
    lightboxState.left = centered.left;
    lightboxState.top = centered.top;
    renderLightboxImage();
}

function setLightboxView(nextScale, options = {}) {
    if (!isStageReady()) return;

    const targetScale = clamp(nextScale, lightboxState.fitScale, lightboxState.maxScale);
    let nextLeft = options.left;
    let nextTop = options.top;

    if (typeof options.clientX === 'number' && typeof options.clientY === 'number') {
        const stageRect = lightboxStageEl.getBoundingClientRect();
        const focalX = clamp(options.clientX - stageRect.left, 0, lightboxState.stageWidth);
        const focalY = clamp(options.clientY - stageRect.top, 0, lightboxState.stageHeight);
        const naturalX = (focalX - lightboxState.left) / lightboxState.scale;
        const naturalY = (focalY - lightboxState.top) / lightboxState.scale;
        nextLeft = focalX - (naturalX * targetScale);
        nextTop = focalY - (naturalY * targetScale);
    } else if (typeof nextLeft !== 'number' || typeof nextTop !== 'number') {
        const centerNaturalX = (lightboxState.stageWidth / 2 - lightboxState.left) / lightboxState.scale;
        const centerNaturalY = (lightboxState.stageHeight / 2 - lightboxState.top) / lightboxState.scale;
        nextLeft = lightboxState.stageWidth / 2 - (centerNaturalX * targetScale);
        nextTop = lightboxState.stageHeight / 2 - (centerNaturalY * targetScale);
    }

    lightboxState.scale = targetScale;
    lightboxState.left = nextLeft;
    lightboxState.top = nextTop;
    renderLightboxImage();
}

function zoomLightboxByFactor(factor, options = {}) {
    setLightboxView(lightboxState.scale * factor, options);
}

function zoomLightboxToActualSize(event) {
    if (!lightboxState.clickZoomEnabled || !isStageReady() || !lightboxImgEl) return;

    if (!isAtFitView()) {
        resetLightboxView();
        return;
    }

    const imageRect = lightboxImgEl.getBoundingClientRect();
    if (!imageRect.width || !imageRect.height) return;

    const relativeX = clamp((event.clientX - imageRect.left) / imageRect.width, 0, 1);
    const relativeY = clamp((event.clientY - imageRect.top) / imageRect.height, 0, 1);
    const targetScale = clamp(1, lightboxState.fitScale, lightboxState.maxScale);

    setLightboxView(targetScale, {
        left: lightboxState.stageWidth / 2 - (relativeX * lightboxState.naturalWidth * targetScale),
        top: lightboxState.stageHeight / 2 - (relativeY * lightboxState.naturalHeight * targetScale),
    });
}

function clearLightboxDragState() {
    lightboxState.dragging = false;
    lightboxState.dragMoved = false;
    lightboxState.dragPointerId = null;
    updateLightboxStageState();
}

function finishLightboxDrag(event) {
    if (lightboxState.dragPointerId !== null && event?.pointerId !== lightboxState.dragPointerId) {
        return;
    }

    if (lightboxState.dragMoved) {
        lightboxState.suppressClick = true;
    }

    try {
        if (event && lightboxImgEl?.hasPointerCapture?.(event.pointerId)) {
            lightboxImgEl.releasePointerCapture(event.pointerId);
        }
    } catch {
        // Ignore pointer capture release errors.
    }

    clearLightboxDragState();
}

function handleLightboxPointerDown(event) {
    if (event.button !== 0 || !isPannable() || !lightboxImgEl) return;

    lightboxState.dragging = true;
    lightboxState.dragMoved = false;
    lightboxState.dragPointerId = event.pointerId;
    lightboxState.dragStartX = event.clientX;
    lightboxState.dragStartY = event.clientY;
    lightboxState.dragLeft = lightboxState.left;
    lightboxState.dragTop = lightboxState.top;

    try {
        lightboxImgEl.setPointerCapture(event.pointerId);
    } catch {
        // Pointer capture is optional.
    }

    updateLightboxStageState();
    event.preventDefault();
}

function handleLightboxPointerMove(event) {
    if (!lightboxState.dragging || event.pointerId !== lightboxState.dragPointerId) return;

    const deltaX = event.clientX - lightboxState.dragStartX;
    const deltaY = event.clientY - lightboxState.dragStartY;

    if (!lightboxState.dragMoved && Math.hypot(deltaX, deltaY) > 4) {
        lightboxState.dragMoved = true;
    }

    const clampedOffsets = clampLightboxOffsets(
        lightboxState.dragLeft + deltaX,
        lightboxState.dragTop + deltaY,
        lightboxState.scale
    );
    lightboxState.left = clampedOffsets.left;
    lightboxState.top = clampedOffsets.top;
    renderLightboxImage();
    event.preventDefault();
}

function handleLightboxPointerUp(event) {
    finishLightboxDrag(event);
}

function computeFitScale(stageWidth, stageHeight) {
    if (!stageWidth || !stageHeight || !lightboxState.naturalWidth || !lightboxState.naturalHeight) {
        return 1;
    }

    const scale = Math.min(stageWidth / lightboxState.naturalWidth, stageHeight / lightboxState.naturalHeight);
    return Number.isFinite(scale) && scale > 0 ? scale : 1;
}

function syncLightboxLayout() {
    if (lightboxEl?.hidden || !lightboxState.loaded) return;

    const previousStageWidth = lightboxState.stageWidth;
    const previousStageHeight = lightboxState.stageHeight;
    const previousScale = lightboxState.scale;
    const previousFitScale = lightboxState.fitScale;
    const previousLeft = lightboxState.left;
    const previousTop = lightboxState.top;

    const metrics = getStageMetrics();
    lightboxState.stageWidth = metrics.width;
    lightboxState.stageHeight = metrics.height;

    if (!lightboxState.stageWidth || !lightboxState.stageHeight) return;

    lightboxState.fitScale = computeFitScale(lightboxState.stageWidth, lightboxState.stageHeight);
    lightboxState.maxScale = Math.max(4, lightboxState.fitScale * 2, 1);
    lightboxState.clickZoomEnabled = lightboxState.fitScale < 1 - LIGHTBOX_EPSILON;

    if (!previousStageWidth || !previousStageHeight || nearlyEqual(previousScale, previousFitScale)) {
        resetLightboxView();
        return;
    }

    const targetScale = clamp(previousScale, lightboxState.fitScale, lightboxState.maxScale);
    const centerNaturalX = (previousStageWidth / 2 - previousLeft) / previousScale;
    const centerNaturalY = (previousStageHeight / 2 - previousTop) / previousScale;

    lightboxState.scale = targetScale;
    lightboxState.left = lightboxState.stageWidth / 2 - (centerNaturalX * targetScale);
    lightboxState.top = lightboxState.stageHeight / 2 - (centerNaturalY * targetScale);
    renderLightboxImage();
}

function queueLightboxLayoutSync() {
    if (lightboxSyncFrame !== null) return;

    lightboxSyncFrame = window.requestAnimationFrame(() => {
        lightboxSyncFrame = null;
        syncLightboxLayout();
    });
}

function handleLightboxImageLoaded(expectedSource) {
    if (!lightboxImgEl || lightboxState.src !== expectedSource) return;

    lightboxState.loaded = true;
    lightboxState.naturalWidth = lightboxImgEl.naturalWidth || 1;
    lightboxState.naturalHeight = lightboxImgEl.naturalHeight || 1;
    queueLightboxLayoutSync();
}

function openImageLightbox(source, title = '\u56fe\u7247\u9884\u89c8') {
    if (!lightboxEl || !lightboxImgEl || !source) return;

    lightboxState.src = source;
    lightboxState.title = title || getImageTitle(source);
    lightboxState.loaded = false;
    lightboxState.naturalWidth = 0;
    lightboxState.naturalHeight = 0;
    lightboxState.stageWidth = 0;
    lightboxState.stageHeight = 0;
    lightboxState.scale = 1;
    lightboxState.fitScale = 1;
    lightboxState.maxScale = 4;
    lightboxState.left = 0;
    lightboxState.top = 0;
    lightboxState.clickZoomEnabled = false;
    lightboxState.suppressClick = false;
    clearLightboxDragState();

    if (lightboxTitleEl) {
        lightboxTitleEl.textContent = lightboxState.title;
    }

    lightboxImgEl.alt = lightboxState.title;
    lightboxImgEl.draggable = false;
    lightboxImgEl.style.transform = '';
    lightboxImgEl.style.width = '';
    lightboxImgEl.style.height = '';

    lightboxEl.hidden = false;
    lightboxEl.setAttribute('aria-hidden', 'false');
    document.body.style.overflow = 'hidden';
    updateLightboxScaleLabel();
    updateLightboxButtons();
    updateLightboxStageState();
    updateFullscreenButton();

    const onLoad = () => {
        if (lightboxState.loaded && lightboxState.src === source) return;
        handleLightboxImageLoaded(source);
    };

    lightboxImgEl.addEventListener('load', onLoad, { once: true });
    lightboxImgEl.src = source;

    if (lightboxImgEl.complete && lightboxImgEl.naturalWidth) {
        window.requestAnimationFrame(onLoad);
    }

    closeBtn?.focus();
}

function closeImageLightbox() {
    if (!lightboxEl || !lightboxImgEl) return;

    if (lightboxSyncFrame !== null) {
        window.cancelAnimationFrame(lightboxSyncFrame);
        lightboxSyncFrame = null;
    }

    if (document.fullscreenElement) {
        document.exitFullscreen().catch(() => {});
    }

    clearLightboxDragState();
    lightboxEl.hidden = true;
    lightboxEl.setAttribute('aria-hidden', 'true');
    lightboxImgEl.removeAttribute('src');
    lightboxImgEl.style.transform = '';
    lightboxImgEl.style.width = '';
    lightboxImgEl.style.height = '';
    document.body.style.overflow = '';

    lightboxState.src = '';
    lightboxState.title = '';
    lightboxState.loaded = false;
    lightboxState.naturalWidth = 0;
    lightboxState.naturalHeight = 0;
    lightboxState.stageWidth = 0;
    lightboxState.stageHeight = 0;
    lightboxState.scale = 1;
    lightboxState.fitScale = 1;
    lightboxState.maxScale = 4;
    lightboxState.left = 0;
    lightboxState.top = 0;
    lightboxState.clickZoomEnabled = false;
    lightboxState.suppressClick = false;

    updateLightboxScaleLabel();
    updateLightboxButtons();
    updateLightboxStageState();
}

async function toggleImageFullscreen() {
    if (!lightboxEl) return;
    try {
        if (document.fullscreenElement) {
            await document.exitFullscreen();
        } else {
            await lightboxEl.requestFullscreen();
        }
    } catch (error) {
        console.error('Fullscreen toggle failed:', error);
    } finally {
        updateFullscreenButton();
        queueLightboxLayoutSync();
    }
}

function buildToc() {
    const headings = Array.from(contentEl.querySelectorAll('h1, h2, h3, h4, h5, h6'));
    if (!headings.length) {
        tocEl.innerHTML = '<div class="materials-viewer-empty">\u5f53\u524d\u6587\u6863\u6ca1\u6709\u53ef\u663e\u793a\u7684\u6807\u9898\u76ee\u5f55\u3002</div>';
        tocCountEl.textContent = '0 \u8282';
        tocEl.onclick = null;
        return;
    }

    const slugCount = new Map();
    const items = headings.map((heading) => {
        const level = Number(heading.tagName.slice(1));
        const baseSlug = slugify(heading.textContent);
        const currentCount = slugCount.get(baseSlug) || 0;
        slugCount.set(baseSlug, currentCount + 1);
        const id = currentCount ? `${baseSlug}-${currentCount + 1}` : baseSlug;
        heading.id = heading.id || id;
        return {
            id: heading.id,
            level,
            title: heading.textContent.trim(),
        };
    });

    tocEl.innerHTML = items.map((item) => `
        <button type="button" data-anchor="${item.id}" style="padding-left:${(item.level - 1) * 14 + 10}px;">
            ${escapeHtml(item.title)}
        </button>
    `).join('');
    tocCountEl.textContent = `${items.length} \u8282`;

    tocEl.onclick = (event) => {
        const button = event.target.closest('[data-anchor]');
        if (!button) return;
        const target = document.getElementById(button.dataset.anchor);
        if (!target) return;
        target.scrollIntoView({ behavior: 'smooth', block: 'start' });
        tocEl.querySelectorAll('button').forEach((el) => el.classList.remove('is-active'));
        button.classList.add('is-active');
    };
}

function rewriteLinksAndImages() {
    const pathMap = new Map((material.path_index || []).map((item) => [item.material_path, item]));

    contentEl.querySelectorAll('a[href]').forEach((anchor) => {
        const href = anchor.getAttribute('href') || '';
        if (!href || href.startsWith('#')) return;
        if (isExternalLink(href)) {
            anchor.target = '_blank';
            anchor.rel = 'noopener noreferrer';
            return;
        }

        const resolved = resolveRelativeTarget(material.material_path, href);
        if (!resolved) return;
        const target = pathMap.get(resolved.path);
        if (!target) return;

        if (target.preview_type === 'markdown' || target.preview_type === 'text') {
            anchor.href = `/materials/view/${target.id}${resolved.hash}`;
        } else if (target.preview_type === 'image') {
            anchor.href = `/materials/raw/${target.id}`;
            anchor.dataset.lightboxImage = 'true';
            anchor.dataset.lightboxTitle = target.name || '';
        } else {
            anchor.href = `/materials/download/${target.id}`;
        }
    });

    contentEl.querySelectorAll('img[src]').forEach((image) => {
        const src = image.getAttribute('src') || '';
        if (!src || src.startsWith('data:')) return;
        if (!isExternalLink(src)) {
            const resolved = resolveRelativeTarget(material.material_path, src);
            if (!resolved) return;
            const target = pathMap.get(resolved.path);
            if (!target) return;
            image.src = `/materials/raw/${target.id}`;
            image.dataset.lightboxTitle = target.name || image.alt || '';
        } else {
            image.dataset.lightboxTitle = image.alt || getImageTitle(src);
        }
        image.loading = 'lazy';
        image.dataset.lightboxImage = 'true';
    });
}

async function copyTextToClipboard(text) {
    if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
        return;
    }

    const helper = document.createElement('textarea');
    helper.value = text;
    helper.setAttribute('readonly', 'true');
    helper.style.position = 'fixed';
    helper.style.opacity = '0';
    helper.style.pointerEvents = 'none';
    document.body.appendChild(helper);
    helper.focus();
    helper.select();

    try {
        document.execCommand('copy');
    } finally {
        helper.remove();
    }
}

function decorateCodeBlocks() {
    const blocks = Array.from(contentEl.querySelectorAll('.md-content pre'));

    blocks.forEach((pre) => {
        if (pre.parentElement?.classList.contains('materials-code-block')) {
            return;
        }

        const wrapper = document.createElement('div');
        wrapper.className = 'materials-code-block';

        const copyButton = document.createElement('button');
        copyButton.type = 'button';
        copyButton.className = 'materials-code-copy-btn';
        copyButton.textContent = '\u590d\u5236';
        copyButton.setAttribute('aria-label', '\u590d\u5236\u4ee3\u7801');

        copyButton.addEventListener('click', async () => {
            const codeText = pre.querySelector('code')?.textContent ?? pre.textContent ?? '';
            if (!codeText.trim()) {
                return;
            }

            copyButton.disabled = true;

            try {
                await copyTextToClipboard(codeText);
                copyButton.classList.add('is-copied');
                copyButton.textContent = '\u221a';
                window.clearTimeout(Number(copyButton.dataset.resetTimer || '0'));
                copyButton.dataset.resetTimer = String(window.setTimeout(() => {
                    copyButton.classList.remove('is-copied');
                    copyButton.textContent = '\u590d\u5236';
                    copyButton.disabled = false;
                    copyButton.dataset.resetTimer = '';
                }, 1600));
            } catch (error) {
                console.error('Copy code block failed:', error);
                copyButton.disabled = false;
            }
        });

        pre.parentNode?.insertBefore(wrapper, pre);
        wrapper.append(copyButton, pre);
    });
}

function bindImageLightbox() {
    if (!contentEl || !lightboxEl || !lightboxStageEl || !lightboxImgEl) return;

    contentEl.addEventListener('click', (event) => {
        const anchor = event.target.closest('a[href]');
        if (anchor && isImageUrl(anchor.href)) {
            event.preventDefault();
            const linkedImage = anchor.querySelector('img');
            const title = anchor.dataset.lightboxTitle
                || linkedImage?.dataset.lightboxTitle
                || linkedImage?.alt
                || anchor.textContent.trim()
                || getImageTitle(anchor.href);
            openImageLightbox(anchor.href, title);
            return;
        }

        const image = event.target.closest('img[data-lightbox-image="true"]');
        if (!image) return;
        event.preventDefault();
        const parentAnchor = image.closest('a[href]');
        const source = parentAnchor && isImageUrl(parentAnchor.href) ? parentAnchor.href : (image.currentSrc || image.src);
        const title = image.dataset.lightboxTitle || image.alt || getImageTitle(source);
        openImageLightbox(source, title);
    });

    zoomOutBtn?.addEventListener('click', () => zoomLightboxByFactor(1 / LIGHTBOX_ZOOM_FACTOR));
    zoomInBtn?.addEventListener('click', () => zoomLightboxByFactor(LIGHTBOX_ZOOM_FACTOR));
    fullscreenBtn?.addEventListener('click', () => toggleImageFullscreen());
    closeBtn?.addEventListener('click', () => closeImageLightbox());

    lightboxEl.addEventListener('click', (event) => {
        if (event.target === lightboxEl) {
            closeImageLightbox();
        }
    });

    lightboxStageEl.addEventListener('click', (event) => {
        if (lightboxState.suppressClick) {
            lightboxState.suppressClick = false;
            event.preventDefault();
            return;
        }

        if (event.target === lightboxStageEl && lightboxState.clickZoomEnabled && !isAtFitView()) {
            event.preventDefault();
            resetLightboxView();
        }
    });

    lightboxImgEl.addEventListener('click', (event) => {
        if (lightboxEl.hidden) return;

        if (lightboxState.suppressClick) {
            lightboxState.suppressClick = false;
            event.preventDefault();
            return;
        }

        if (!lightboxState.clickZoomEnabled) return;

        event.preventDefault();
        zoomLightboxToActualSize(event);
    });

    lightboxImgEl.addEventListener('pointerdown', handleLightboxPointerDown);
    lightboxImgEl.addEventListener('pointermove', handleLightboxPointerMove);
    lightboxImgEl.addEventListener('pointerup', handleLightboxPointerUp);
    lightboxImgEl.addEventListener('pointercancel', handleLightboxPointerUp);
    lightboxImgEl.addEventListener('dragstart', (event) => event.preventDefault());

    lightboxStageEl.addEventListener('wheel', (event) => {
        if (lightboxEl.hidden || !lightboxState.loaded) return;
        event.preventDefault();
        const factor = event.deltaY > 0 ? (1 / LIGHTBOX_ZOOM_FACTOR) : LIGHTBOX_ZOOM_FACTOR;
        zoomLightboxByFactor(factor, { clientX: event.clientX, clientY: event.clientY });
    }, { passive: false });

    document.addEventListener('keydown', (event) => {
        if (lightboxEl.hidden) return;

        if (event.key === 'Escape') {
            closeImageLightbox();
            return;
        }

        if (event.key === '+' || event.key === '=' || event.key === 'Add') {
            event.preventDefault();
            zoomLightboxByFactor(LIGHTBOX_ZOOM_FACTOR);
            return;
        }

        if (event.key === '-' || event.key === '_' || event.key === 'Subtract') {
            event.preventDefault();
            zoomLightboxByFactor(1 / LIGHTBOX_ZOOM_FACTOR);
            return;
        }

        if (event.key === '0') {
            event.preventDefault();
            resetLightboxView();
        }
    });

    document.addEventListener('fullscreenchange', () => {
        updateFullscreenButton();
        queueLightboxLayoutSync();
    });

    window.addEventListener('resize', () => queueLightboxLayoutSync());

    if (window.ResizeObserver && !stageResizeObserver) {
        stageResizeObserver = new ResizeObserver(() => queueLightboxLayoutSync());
        stageResizeObserver.observe(lightboxStageEl);
    }
}

async function renderMermaidBlocks() {
    const mermaidBlocks = Array.from(contentEl.querySelectorAll('pre code.language-mermaid, pre code.lang-mermaid'));
    if (!mermaidBlocks.length || typeof mermaid === 'undefined') return;

    mermaid.initialize({
        startOnLoad: false,
        theme: 'neutral',
        securityLevel: 'loose',
    });

    mermaidBlocks.forEach((codeBlock, index) => {
        const wrapper = document.createElement('div');
        wrapper.className = 'materials-mermaid mermaid';
        wrapper.id = `mermaid-diagram-${index}`;
        wrapper.textContent = codeBlock.textContent || '';
        codeBlock.closest('pre')?.replaceWith(wrapper);
    });

    await mermaid.run({
        nodes: Array.from(contentEl.querySelectorAll('.mermaid')),
    });
}

async function renderMarkdown() {
    const markdown = String(material.content || '').trim();
    if (!markdown) {
        contentEl.innerHTML = '<div class="materials-viewer-empty">\u5f53\u524d\u6750\u6599\u5185\u5bb9\u4e3a\u7a7a\u3002</div>';
        tocEl.innerHTML = '<div class="materials-viewer-empty">\u5f53\u524d\u6587\u6863\u6ca1\u6709\u53ef\u663e\u793a\u7684\u6807\u9898\u76ee\u5f55\u3002</div>';
        tocCountEl.textContent = '0 \u8282';
        tocEl.onclick = null;
        return;
    }

    contentEl.innerHTML = `<article class="md-content">${parseMarkdownHtml(markdown)}</article>`;

    rewriteLinksAndImages();
    buildToc();
    await renderMermaidBlocks();
    decorateCodeBlocks();
}

function renderText() {
    const text = String(material.content || '');
    if (!text) {
        contentEl.innerHTML = '<div class="materials-viewer-empty">\u5f53\u524d\u6750\u6599\u5185\u5bb9\u4e3a\u7a7a\u3002</div>';
    } else {
        contentEl.innerHTML = `
            <article class="materials-text-preview">
                <pre>${escapeHtml(text)}</pre>
            </article>
        `;
    }
    tocEl.innerHTML = '<div class="materials-viewer-empty">\u7eaf\u6587\u672c\u6750\u6599\u4e0d\u751f\u6210\u6807\u9898\u76ee\u5f55\u3002</div>';
    tocCountEl.textContent = '0 \u8282';
    tocEl.onclick = null;
}

function renderImage() {
    contentEl.innerHTML = `
        <div class="materials-image-preview">
            <img src="${material.raw_url}" alt="${escapeHtml(material.name)}" data-lightbox-image="true" data-lightbox-title="${escapeHtml(material.name)}">
        </div>
    `;
    tocEl.innerHTML = '<div class="materials-viewer-empty">\u56fe\u7247\u6750\u6599\u6ca1\u6709\u6807\u9898\u76ee\u5f55\u3002</div>';
    tocCountEl.textContent = '0 \u8282';
    tocEl.onclick = null;
}

function renderFallback() {
    contentEl.innerHTML = `
        <div class="materials-file-fallback">
            <h2 style="margin-top:0;">\u5f53\u524d\u7c7b\u578b\u6682\u4e0d\u652f\u6301\u5728\u7ebf\u9884\u89c8</h2>
            <p class="text-muted">\u6750\u6599\u5e93\u5df2\u652f\u6301 Markdown\u3001\u6587\u672c\u4e0e\u56fe\u7247\u5728\u7ebf\u9884\u89c8\uff0c\u5176\u4ed6\u6587\u6863\u7c7b\u578b\u53ef\u7ee7\u7eed\u6269\u5c55\u3002</p>
            ${buildMaterialDownloadAction('\u4e0b\u8f7d\u539f\u6587\u4ef6')}
        </div>
    `;
    tocEl.innerHTML = '<div class="materials-viewer-empty">\u6b64\u7c7b\u578b\u6750\u6599\u6682\u65e0\u76ee\u5f55\u3002</div>';
    tocCountEl.textContent = '0 \u8282';
    tocEl.onclick = null;
}

function decorateViewerDownloadActions() {
    if (!material.download_url || material.download_allowed !== false) {
        return;
    }

    document.querySelectorAll('.materials-viewer-actions a').forEach((anchor) => {
        if (anchor.getAttribute('href') !== material.download_url) {
            return;
        }
        anchor.outerHTML = buildBlockedDownloadButton();
    });
}

function bindBlockedDownloadTips() {
    document.querySelectorAll('[data-download-blocked="true"]').forEach((button) => {
        if (button.dataset.blockedDownloadBound === 'true') {
            return;
        }
        button.dataset.blockedDownloadBound = 'true';
        button.addEventListener('click', () => {
            showToast(material.download_blocked_reason || '当前材料已限制下载', 'warning');
        });
    });
}

function updateEditorEncodingLabel() {
    if (!editorEncodingEl) return;
    editorEncodingEl.textContent = `编码 ${editorEncoding}`;
}

function openSourceEditor() {
    if (!editorBackdropEl) return;
    editorBackdropEl.hidden = false;
    editorBackdropEl.setAttribute('aria-hidden', 'false');
    document.body.style.overflow = 'hidden';
    window.requestAnimationFrame(() => editorTextareaEl?.focus());
}

function closeSourceEditor() {
    if (!editorBackdropEl) return;
    editorBackdropEl.hidden = true;
    editorBackdropEl.setAttribute('aria-hidden', 'true');
    document.body.style.overflow = '';
}

async function ensureSourceEditorContent() {
    if (!material.content_url || !editorTextareaEl) return;
    if (editorLoaded) {
        updateEditorEncodingLabel();
        return;
    }
    if (editorLoadingPromise) {
        await editorLoadingPromise;
        return;
    }

    editorLoadingPromise = apiFetch(material.content_url, { silent: true })
        .then((data) => {
            editorTextareaEl.value = String(data.content || '');
            editorEncoding = data.encoding || editorEncoding;
            editorLoaded = true;
            updateEditorEncodingLabel();
        })
        .finally(() => {
            editorLoadingPromise = null;
        });

    await editorLoadingPromise;
}

async function handleOpenSourceEditor() {
    if (!editorBackdropEl) return;
    openSourceEditor();
    try {
        await ensureSourceEditorContent();
    } catch (error) {
        closeSourceEditor();
        showToast(error.message || '加载源码失败', 'error');
    }
}

async function handleSaveSourceEditor() {
    if (!editorSaveBtn || !editorTextareaEl || !material.content_url) return;

    editorSaveBtn.disabled = true;
    const originalLabel = editorSaveBtn.textContent;
    editorSaveBtn.textContent = '保存中...';

    try {
        const result = await apiFetch(material.content_url, {
            method: 'PUT',
            silent: true,
            body: {
                content: editorTextareaEl.value,
                encoding: editorEncoding,
            },
        });
        if (result.unchanged) {
            closeSourceEditor();
            showToast(result.message || '源码没有变化', 'info');
            return;
        }

        sessionStorage.setItem('material-viewer-toast', result.message || '材料源码已保存');
        window.location.href = result.material?.viewer_url || material.viewer_url || window.location.href;
    } catch (error) {
        showToast(error.message || '保存源码失败', 'error');
    } finally {
        editorSaveBtn.disabled = false;
        editorSaveBtn.textContent = originalLabel;
    }
}

function bindSourceEditor() {
    if (!editSourceBtn || !editorBackdropEl) return;

    editSourceBtn.addEventListener('click', () => {
        handleOpenSourceEditor().catch((error) => {
            showToast(error.message || '加载源码失败', 'error');
        });
    });
    editorCancelBtn?.addEventListener('click', () => closeSourceEditor());
    editorSaveBtn?.addEventListener('click', () => {
        handleSaveSourceEditor().catch((error) => {
            showToast(error.message || '保存源码失败', 'error');
        });
    });
    editorBackdropEl.addEventListener('click', (event) => {
        if (event.target === editorBackdropEl) {
            closeSourceEditor();
        }
    });
    document.addEventListener('keydown', (event) => {
        if (!editorBackdropEl.hidden && event.key === 'Escape') {
            event.preventDefault();
            closeSourceEditor();
        }
    });
}

async function init() {
    if (!contentEl) return;
    bindImageLightbox();
    bindSourceEditor();
    decorateViewerDownloadActions();
    bindBlockedDownloadTips();

    const pendingToast = sessionStorage.getItem('material-viewer-toast');
    if (pendingToast) {
        sessionStorage.removeItem('material-viewer-toast');
        showToast(pendingToast, 'success');
    }

    if (material.is_markdown) {
        await renderMarkdown();
        bindBlockedDownloadTips();
        return;
    }

    if (material.preview_type === 'text' || (material.is_text && !material.is_markdown)) {
        renderText();
        bindBlockedDownloadTips();
        return;
    }

    if (material.is_image) {
        renderImage();
        bindBlockedDownloadTips();
        return;
    }

    renderFallback();
    bindBlockedDownloadTips();
}

init().catch((error) => {
    console.error(error);
    if (contentEl) {
        contentEl.innerHTML = `<div class="materials-viewer-empty">\u6e32\u67d3\u5931\u8d25\uff1a${escapeHtml(error.message || '\u672a\u77e5\u9519\u8bef')}</div>`;
    }
});
