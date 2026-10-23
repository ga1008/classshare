/**
 * Platform-wide material renderer registry.
 *
 * Every place that shows a material inline (library explorer, classroom
 * material focus, session panels) asks this module to render a serialized
 * item into a container. Dispatch is driven by the server contract written
 * by `material_preview_service.py` (`preview_mode` + `preview_*` URLs); a
 * renderer never guesses from extensions.
 *
 *   renderMaterialPreview(container, item, { fetchContent, onError })
 *   registerMaterialRenderer({ id, match(item), render(container, item, ctx) })
 *
 * Built-ins (ordered): markdown, text/code, image, frame (html/lessondoc),
 * document (pdf/office page images), media (audio/video), folder, download.
 */
import { apiFetch } from './api.js';
import { escapeHtml, formatSize } from './ui.js';
import { renderFilePreview } from './file_preview.js';
import { getMaterialKind, kindIcon } from './materials_kinds.js';

const registry = [];

export function registerMaterialRenderer(renderer, { prepend = false } = {}) {
    if (!renderer || typeof renderer.match !== 'function' || typeof renderer.render !== 'function') {
        throw new TypeError('material renderer needs match() and render()');
    }
    if (prepend) registry.unshift(renderer);
    else registry.push(renderer);
    return () => {
        const index = registry.indexOf(renderer);
        if (index >= 0) registry.splice(index, 1);
    };
}

export function resolveMaterialRenderer(item) {
    return registry.find((renderer) => {
        try {
            return renderer.match(item);
        } catch {
            return false;
        }
    }) || null;
}

function previewMode(item) {
    return String(item?.preview_mode || item?.kind_preview_mode || '');
}

function emptyState(message, actions = '') {
    return `<div class="material-renderer__empty">${escapeHtml(message)}${actions ? `<div class="material-renderer__empty-actions">${actions}</div>` : ''}</div>`;
}

function downloadActions(item) {
    const parts = [];
    if (item?.node_type === 'file' && item.download_allowed !== false) {
        parts.push(`<a data-lq-component="button" class="lq-btn lq-btn--sm lq-btn--glass" href="/materials/download/${Number(item.id)}">下载文件</a>`);
    }
    if (item?.preview_url) {
        parts.push(`<a data-lq-component="button" class="lq-btn lq-btn--sm lq-btn--glass" href="${escapeHtml(item.preview_url)}" target="_blank" rel="noopener">新标签打开</a>`);
    }
    return parts.join('');
}

async function loadContent(item, ctx) {
    if (typeof ctx.fetchContent === 'function') return ctx.fetchContent(item);
    if (!item.preview_content_url) return null;
    return apiFetch(item.preview_content_url, { silent: true });
}

/* ---------- built-in renderers ---------- */

registerMaterialRenderer({
    id: 'markdown',
    match: (item) => item?.node_type === 'file' && previewMode(item) === 'markdown',
    async render(container, item, ctx = {}) {
        container.innerHTML = '<div class="material-renderer__status">正在读取文档…</div>';
        const data = await loadContent(item, ctx);
        if (ctx.isCurrent && !ctx.isCurrent()) return;
        if (!data) {
            container.innerHTML = emptyState('当前账号不能读取该文档源码，可在新标签中打开查看。', downloadActions(item));
            return;
        }
        const host = document.createElement('div');
        host.className = 'material-renderer__markdown';
        container.replaceChildren(host);
        await renderFilePreview({
            file: { ...item, is_markdown: true, content: String(data.content || '') },
            contentEl: host,
            resolveLinkTarget: ctx.resolveLinkTarget || null,
            resolveImageTarget: ctx.resolveImageTarget || null,
        });
        ctx.onContentLoaded?.(data);
    },
});

registerMaterialRenderer({
    id: 'text',
    match: (item) => item?.node_type === 'file' && previewMode(item) === 'text',
    async render(container, item, ctx = {}) {
        container.innerHTML = '<div class="material-renderer__status">正在读取文本…</div>';
        const data = await loadContent(item, ctx);
        if (ctx.isCurrent && !ctx.isCurrent()) return;
        if (!data) {
            container.innerHTML = emptyState('当前账号不能读取该文本源码。', downloadActions(item));
            return;
        }
        const text = String(data.content || '');
        const lines = text.split('\n');
        const kind = getMaterialKind(item);
        container.innerHTML = `
            <div class="material-renderer__code" data-kind="${escapeHtml(kind.kind)}">
                <div class="material-renderer__code-head"><span>${escapeHtml(kind.label)}</span><span>${lines.length} 行 · ${escapeHtml(formatSize(new Blob([text]).size))}</span></div>
                <pre class="material-renderer__pre"><code>${lines.map((line, index) => `<span class="material-renderer__line"><span class="material-renderer__ln">${index + 1}</span>${escapeHtml(line)}</span>`).join('\n')}</code></pre>
            </div>
        `;
        ctx.onContentLoaded?.(data);
    },
});

registerMaterialRenderer({
    id: 'image',
    match: (item) => item?.node_type === 'file' && previewMode(item) === 'image',
    async render(container, item) {
        const src = item.preview_image_url || `/materials/raw/${Number(item.id)}`;
        container.innerHTML = `
            <figure class="material-renderer__image">
                <img src="${escapeHtml(src)}" alt="${escapeHtml(item.name || '图片')}" loading="lazy" data-ls-lightbox data-ls-lightbox-src="${escapeHtml(src)}" data-ls-lightbox-title="${escapeHtml(item.name || '')}">
                <figcaption>${escapeHtml(item.name || '')}${item.file_size ? ` · ${escapeHtml(formatSize(item.file_size))}` : ''}</figcaption>
            </figure>
        `;
    },
});

registerMaterialRenderer({
    id: 'frame',
    match: (item) => previewMode(item) === 'frame' && Boolean(item?.preview_frame_url || item?.preview_url),
    async render(container, item) {
        const src = item.preview_frame_url || item.preview_url;
        container.innerHTML = `
            <div class="material-renderer__frame-wrap">
                <iframe class="material-renderer__frame" src="${escapeHtml(src)}" title="${escapeHtml(item.name || '渲染预览')}" loading="lazy"></iframe>
            </div>
        `;
    },
});

registerMaterialRenderer({
    id: 'document',
    match: (item) => item?.node_type === 'file' && previewMode(item) === 'document' && Boolean(item?.preview_frame_url),
    async render(container, item) {
        container.innerHTML = `
            <div class="material-renderer__frame-wrap material-renderer__frame-wrap--document">
                <iframe class="material-renderer__frame" src="${escapeHtml(item.preview_frame_url)}" title="${escapeHtml(item.name || '文档预览')}" loading="lazy"></iframe>
            </div>
        `;
    },
});

registerMaterialRenderer({
    id: 'media',
    match: (item) => item?.node_type === 'file' && previewMode(item) === 'media' && Boolean(item?.preview_media_url),
    async render(container, item) {
        const mime = String(item.mime_type || '');
        const isAudio = mime.startsWith('audio/') || /\.(mp3|wav|ogg|m4a|flac|aac)$/i.test(String(item.name || ''));
        const tag = isAudio ? 'audio' : 'video';
        container.innerHTML = `
            <div class="material-renderer__media">
                <${tag} controls preload="metadata" src="${escapeHtml(item.preview_media_url)}"${isAudio ? '' : ' playsinline'}></${tag}>
                <p>${escapeHtml(item.name || '')}${item.file_size ? ` · ${escapeHtml(formatSize(item.file_size))}` : ''}</p>
            </div>
        `;
    },
});

registerMaterialRenderer({
    id: 'folder',
    match: (item) => item?.node_type === 'folder',
    async render(container, item, ctx = {}) {
        if (typeof ctx.renderFolder === 'function') {
            await ctx.renderFolder(container, item);
            return;
        }
        const kind = getMaterialKind(item);
        container.innerHTML = `
            <div class="material-renderer__folder">
                <span class="material-renderer__folder-icon" style="--kind-color:${escapeHtml(kind.color)}">${kindIcon(kind.icon, 28)}</span>
                <strong>${escapeHtml(item.name || '文件夹')}</strong>
                <p>${escapeHtml(kind.label)} · ${escapeHtml(String(item.child_count ?? 0))} 项</p>
            </div>
        `;
    },
});

registerMaterialRenderer({
    id: 'download',
    match: () => true,
    async render(container, item) {
        const kind = getMaterialKind(item);
        container.innerHTML = emptyState(
            `${kind.label}暂不支持在线预览，可下载后查看。`,
            downloadActions(item),
        );
    },
});

/**
 * Render `item` into `container`. Resolves after the renderer finishes (frames
 * resolve immediately). Errors are surfaced inside the container and passed to
 * `ctx.onError` so callers never have to try/catch around a render.
 */
export async function renderMaterialPreview(container, item, ctx = {}) {
    if (!container) return null;
    const renderer = resolveMaterialRenderer(item);
    if (!renderer) {
        container.innerHTML = emptyState('没有可用的渲染器。');
        return null;
    }
    container.dataset.materialRenderer = renderer.id || '';
    try {
        await renderer.render(container, item, ctx);
    } catch (error) {
        if (ctx.isCurrent && !ctx.isCurrent()) return renderer;
        container.innerHTML = emptyState(error?.message || '预览失败。', downloadActions(item));
        ctx.onError?.(error);
    }
    return renderer;
}

export function listMaterialRenderers() {
    return registry.map((renderer) => renderer.id || 'anonymous');
}
