import { escapeHtml } from './ui.js';

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

function slugify(text) {
    return String(text || '')
        .toLowerCase()
        .replace(/[\s\W-]+/g, '-')
        .replace(/^-+|-+$/g, '') || 'section';
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

function setTocEmpty(tocEl, tocCountEl, message) {
    if (!tocEl) return;
    tocEl.innerHTML = `<div class="materials-viewer-empty">${escapeHtml(message)}</div>`;
    if (tocCountEl) {
        tocCountEl.textContent = '0 节';
    }
    tocEl.onclick = null;
}

export function buildPreviewToc({ contentEl, tocEl = null, tocCountEl = null }) {
    if (!contentEl || !tocEl) return;

    const headings = Array.from(contentEl.querySelectorAll('h1, h2, h3, h4, h5, h6'));
    if (!headings.length) {
        setTocEmpty(tocEl, tocCountEl, '当前文档没有可显示的标题目录。');
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
    if (tocCountEl) {
        tocCountEl.textContent = `${items.length} 节`;
    }

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

export function decoratePreviewCodeBlocks(contentEl) {
    if (!contentEl) return;

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
        copyButton.textContent = '复制';
        copyButton.setAttribute('aria-label', '复制代码');

        copyButton.addEventListener('click', async () => {
            const codeText = pre.querySelector('code')?.textContent ?? pre.textContent ?? '';
            if (!codeText.trim()) {
                return;
            }

            copyButton.disabled = true;
            try {
                await copyTextToClipboard(codeText);
                copyButton.classList.add('is-copied');
                copyButton.textContent = '√';
                window.clearTimeout(Number(copyButton.dataset.resetTimer || '0'));
                copyButton.dataset.resetTimer = String(window.setTimeout(() => {
                    copyButton.classList.remove('is-copied');
                    copyButton.textContent = '复制';
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

export async function renderPreviewMermaid(contentEl) {
    if (!contentEl) return;
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

export function rewritePreviewLinks(contentEl, file, resolveLinkTarget = null, resolveImageTarget = null) {
    if (!contentEl) return;

    contentEl.querySelectorAll('a[href]').forEach((anchor) => {
        const href = anchor.getAttribute('href') || '';
        if (!href || href.startsWith('#')) return;
        if (isExternalLink(href)) {
            anchor.target = '_blank';
            anchor.rel = 'noopener noreferrer';
            return;
        }
        if (typeof resolveLinkTarget !== 'function') {
            return;
        }

        const resolved = resolveLinkTarget({ anchor, href, file });
        if (!resolved || !resolved.href) return;

        anchor.href = resolved.href;
        if (resolved.external) {
            anchor.target = '_blank';
            anchor.rel = 'noopener noreferrer';
        }
        if (resolved.lightboxImage) {
            anchor.dataset.lightboxImage = 'true';
            anchor.dataset.lightboxTitle = resolved.title || '';
        }
    });

    contentEl.querySelectorAll('img[src]').forEach((image) => {
        const src = image.getAttribute('src') || '';
        if (!src || src.startsWith('data:')) return;

        if (!isExternalLink(src) && typeof resolveImageTarget === 'function') {
            const resolved = resolveImageTarget({ image, src, file });
            if (resolved?.src) {
                image.src = resolved.src;
                image.dataset.lightboxTitle = resolved.title || image.alt || '';
            }
        }

        image.loading = 'lazy';
        image.dataset.lightboxImage = 'true';
    });
}

export async function renderFilePreview({
    file,
    contentEl,
    tocEl = null,
    tocCountEl = null,
    resolveLinkTarget = null,
    resolveImageTarget = null,
    buildFallbackActionHtml = null,
}) {
    if (!contentEl) return;

    const currentFile = file || {};
    if (currentFile.is_markdown) {
        const markdown = String(currentFile.content || '').trim();
        if (!markdown) {
            contentEl.innerHTML = '<div class="materials-viewer-empty">当前材料内容为空。</div>';
            setTocEmpty(tocEl, tocCountEl, '当前文档没有可显示的标题目录。');
            return;
        }

        contentEl.innerHTML = `<article class="md-content">${parseMarkdownHtml(markdown)}</article>`;
        rewritePreviewLinks(contentEl, currentFile, resolveLinkTarget, resolveImageTarget);
        buildPreviewToc({ contentEl, tocEl, tocCountEl });
        await renderPreviewMermaid(contentEl);
        decoratePreviewCodeBlocks(contentEl);
        return;
    }

    if (currentFile.preview_type === 'text' || (currentFile.is_text && !currentFile.is_markdown)) {
        const text = String(currentFile.content || '');
        if (!text) {
            contentEl.innerHTML = '<div class="materials-viewer-empty">当前材料内容为空。</div>';
        } else {
            contentEl.innerHTML = `
                <article class="materials-text-preview">
                    <pre>${escapeHtml(text)}</pre>
                </article>
            `;
        }
        setTocEmpty(tocEl, tocCountEl, '纯文本材料不生成标题目录。');
        return;
    }

    if (currentFile.is_image) {
        contentEl.innerHTML = `
            <div class="materials-image-preview">
                <img src="${escapeHtml(currentFile.raw_url || '')}" alt="${escapeHtml(currentFile.name || currentFile.display_name || 'image')}" data-lightbox-image="true" data-lightbox-title="${escapeHtml(currentFile.name || currentFile.display_name || '')}">
            </div>
        `;
        setTocEmpty(tocEl, tocCountEl, '图片材料没有标题目录。');
        return;
    }

    const actionHtml = typeof buildFallbackActionHtml === 'function'
        ? buildFallbackActionHtml(currentFile) || ''
        : '';
    contentEl.innerHTML = `
        <div class="materials-file-fallback">
            <h2 style="margin-top:0;">当前类型暂不支持在线预览</h2>
            <p class="text-muted">已支持 Markdown、文本与图片在线预览，其他类型仍可下载查看。</p>
            ${actionHtml}
        </div>
    `;
    setTocEmpty(tocEl, tocCountEl, '此类型材料暂无目录。');
}
