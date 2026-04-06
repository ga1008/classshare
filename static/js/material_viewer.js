const material = window.MATERIAL_VIEWER || {};

const contentEl = document.getElementById('viewer-content');
const tocEl = document.getElementById('viewer-toc');
const tocCountEl = document.getElementById('viewer-toc-count');

function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

function isExternalLink(href) {
    return /^(?:[a-z]+:)?\/\//i.test(href) || href.startsWith('mailto:');
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

function buildToc() {
    const headings = Array.from(contentEl.querySelectorAll('h1, h2, h3, h4, h5, h6'));
    if (!headings.length) {
        tocEl.innerHTML = '<div class="materials-viewer-empty">当前文档没有可显示的标题目录。</div>';
        tocCountEl.textContent = '0 节';
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
    tocCountEl.textContent = `${items.length} 节`;

    tocEl.addEventListener('click', (event) => {
        const button = event.target.closest('[data-anchor]');
        if (!button) return;
        const target = document.getElementById(button.dataset.anchor);
        if (!target) return;
        target.scrollIntoView({ behavior: 'smooth', block: 'start' });
        tocEl.querySelectorAll('button').forEach((el) => el.classList.remove('is-active'));
        button.classList.add('is-active');
    });
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

        if (target.preview_type === 'markdown') {
            anchor.href = `/materials/view/${target.id}${resolved.hash}`;
        } else if (target.preview_type === 'image') {
            anchor.href = `/materials/raw/${target.id}`;
            anchor.target = '_blank';
            anchor.rel = 'noopener';
        } else {
            anchor.href = `/materials/download/${target.id}`;
        }
    });

    contentEl.querySelectorAll('img[src]').forEach((image) => {
        const src = image.getAttribute('src') || '';
        if (!src || src.startsWith('data:') || isExternalLink(src)) return;
        const resolved = resolveRelativeTarget(material.material_path, src);
        if (!resolved) return;
        const target = pathMap.get(resolved.path);
        if (!target) return;
        image.src = `/materials/raw/${target.id}`;
        image.loading = 'lazy';
    });
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
        contentEl.innerHTML = '<div class="materials-viewer-empty">当前材料内容为空。</div>';
        return;
    }

    if (typeof marked !== 'undefined' && marked.parse) {
        contentEl.innerHTML = `<article class="md-content">${marked.parse(markdown)}</article>`;
    } else {
        contentEl.innerHTML = `<article class="md-content"><pre>${escapeHtml(markdown)}</pre></article>`;
    }

    rewriteLinksAndImages();
    buildToc();
    await renderMermaidBlocks();
}

function renderImage() {
    contentEl.innerHTML = `
        <div class="materials-image-preview">
            <img src="${material.raw_url}" alt="${escapeHtml(material.name)}">
        </div>
    `;
    tocEl.innerHTML = '<div class="materials-viewer-empty">图片材料没有标题目录。</div>';
    tocCountEl.textContent = '0 节';
}

function renderFallback() {
    contentEl.innerHTML = `
        <div class="materials-file-fallback">
            <h2 style="margin-top:0;">当前类型暂不支持在线预览</h2>
            <p class="text-muted">已完成材料库建模、上传、分配与下载链路。当前优先实现 Markdown 在线渲染，其他文档类型可继续扩展。</p>
            <a href="${material.download_url}" class="btn btn-primary">下载原文件</a>
        </div>
    `;
    tocEl.innerHTML = '<div class="materials-viewer-empty">此类型材料暂无目录。</div>';
    tocCountEl.textContent = '0 节';
}

async function init() {
    if (!contentEl) return;

    if (material.is_markdown) {
        await renderMarkdown();
        return;
    }

    if (material.is_image) {
        renderImage();
        return;
    }

    renderFallback();
}

init().catch((error) => {
    console.error(error);
    if (contentEl) {
        contentEl.innerHTML = `<div class="materials-viewer-empty">渲染失败：${escapeHtml(error.message || '未知错误')}</div>`;
    }
});
