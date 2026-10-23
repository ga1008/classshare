/**
 * Platform-wide material editor registry.
 *
 * Editors are mounted into a container for an *editable* material (the
 * server marks `editable` and exposes `preview_content_url`). Each editor
 * owns its own DOM and reports changes through callbacks; persistence (the
 * PUT with revision guards) stays with the host so one save path serves
 * every surface.
 *
 *   const editor = await mountMaterialEditor(container, item, {
 *       content, onDirty(isDirty), onSave(), onCancel(isDirty)
 *   });
 *   editor.getValue(); editor.setValue(text); editor.isDirty(); editor.destroy();
 *
 * Built-ins: markdown (source + live preview), html (source + live frame),
 * text/code (plain source).
 */
import { escapeHtml } from './ui.js';
import { renderFilePreview } from './file_preview.js';
import { getMaterialKind } from './materials_kinds.js';

const registry = [];
const PREVIEW_DEBOUNCE_MS = 220;

export function registerMaterialEditor(editor, { prepend = false } = {}) {
    if (!editor || typeof editor.match !== 'function' || typeof editor.mount !== 'function') {
        throw new TypeError('material editor needs match() and mount()');
    }
    if (prepend) registry.unshift(editor);
    else registry.push(editor);
    return () => {
        const index = registry.indexOf(editor);
        if (index >= 0) registry.splice(index, 1);
    };
}

export function resolveMaterialEditor(item) {
    if (!item?.editable) return null;
    return registry.find((editor) => {
        try {
            return editor.match(item);
        } catch {
            return false;
        }
    }) || null;
}

export function canEditMaterial(item) {
    return Boolean(resolveMaterialEditor(item));
}

function previewMode(item) {
    return String(item?.preview_mode || item?.kind_preview_mode || '');
}

function extensionOf(item) {
    const ext = String(item?.file_ext || '').toLowerCase().replace(/^\./, '');
    if (ext) return ext;
    const name = String(item?.name || '');
    return name.includes('.') ? name.split('.').pop().toLowerCase() : '';
}

function insertAtCursor(textarea, before, after = '', placeholder = '') {
    const start = textarea.selectionStart ?? textarea.value.length;
    const end = textarea.selectionEnd ?? start;
    const hadSelection = end > start;
    const selected = textarea.value.slice(start, end) || placeholder;
    textarea.setRangeText(`${before}${selected}${after}`, start, end, 'end');
    if (!hadSelection && placeholder) {
        textarea.setSelectionRange(start + before.length, start + before.length + placeholder.length);
    }
    textarea.dispatchEvent(new Event('input', { bubbles: true }));
    textarea.focus();
}

const MARKDOWN_TOOLS = [
    { key: 'h2', label: 'H2', title: '二级标题', apply: (ta) => insertAtCursor(ta, '\n## ', '', '标题') },
    { key: 'h3', label: 'H3', title: '三级标题', apply: (ta) => insertAtCursor(ta, '\n### ', '', '标题') },
    { key: 'bold', label: 'B', title: '加粗', apply: (ta) => insertAtCursor(ta, '**', '**', '加粗文字') },
    { key: 'italic', label: 'I', title: '斜体', apply: (ta) => insertAtCursor(ta, '*', '*', '斜体文字') },
    { key: 'code', label: '</>', title: '行内代码', apply: (ta) => insertAtCursor(ta, '`', '`', 'code') },
    { key: 'block', label: '```', title: '代码块', apply: (ta) => insertAtCursor(ta, '\n```\n', '\n```\n', '代码') },
    { key: 'list', label: '•', title: '列表', apply: (ta) => insertAtCursor(ta, '\n- ', '', '条目') },
    { key: 'quote', label: '❝', title: '引用', apply: (ta) => insertAtCursor(ta, '\n> ', '', '引用内容') },
    { key: 'link', label: '链接', title: '链接', apply: (ta) => insertAtCursor(ta, '[', '](https://)', '链接文字') },
    { key: 'table', label: '表格', title: '表格', apply: (ta) => insertAtCursor(ta, '\n| 列1 | 列2 |\n| --- | --- |\n| ', ' |  |\n', '内容') },
];

function buildShell(container, item, { title, hint, tools = [], withPreview = true, previewLabel = '预览' }) {
    const kind = getMaterialKind(item);
    container.innerHTML = `
        <div class="material-editor" data-kind="${escapeHtml(kind.kind)}" data-split="${withPreview ? 'true' : 'false'}">
            <div class="material-editor__bar">
                <div class="material-editor__title">
                    <strong>${escapeHtml(title)}</strong>
                    <small>${escapeHtml(hint)}</small>
                </div>
                <div class="material-editor__tools" role="toolbar" aria-label="编辑工具">
                    ${tools.map((tool) => `<button data-lq-component="button" type="button" class="lq-btn lq-btn--sm lq-btn--ghost material-editor__tool" data-editor-tool="${escapeHtml(tool.key)}" title="${escapeHtml(tool.title)}">${escapeHtml(tool.label)}</button>`).join('')}
                    ${withPreview ? `<button data-lq-component="button" type="button" class="lq-btn lq-btn--sm lq-btn--glass material-editor__toggle" data-editor-toggle-preview aria-pressed="true">${escapeHtml(previewLabel)}</button>` : ''}
                </div>
                <div class="material-editor__actions">
                    <span class="material-editor__state" data-editor-state>未修改</span>
                    <button data-lq-component="button" type="button" class="lq-btn lq-btn--sm lq-btn--ghost" data-editor-cancel>放弃</button>
                    <button data-lq-component="button" type="button" class="lq-btn lq-btn--sm lq-btn--prominent" data-editor-save disabled>保存</button>
                </div>
            </div>
            <div class="material-editor__panes">
                <label class="material-editor__source">
                    <span class="sr-only">源码</span>
                    <textarea data-lq-component="textarea" class="lq-textarea material-editor__textarea" data-editor-textarea spellcheck="false" wrap="off"></textarea>
                </label>
                ${withPreview ? '<div class="material-editor__preview" data-editor-preview></div>' : ''}
            </div>
        </div>
    `;
    return {
        root: container.querySelector('.material-editor'),
        textarea: container.querySelector('[data-editor-textarea]'),
        preview: container.querySelector('[data-editor-preview]'),
        state: container.querySelector('[data-editor-state]'),
        saveBtn: container.querySelector('[data-editor-save]'),
        cancelBtn: container.querySelector('[data-editor-cancel]'),
        toggleBtn: container.querySelector('[data-editor-toggle-preview]'),
    };
}

function wireCommon(shell, item, options, { renderPreview, tools = [] }) {
    let original = String(options.content ?? '');
    shell.textarea.value = original;
    let dirty = false;
    let timer = 0;
    let destroyed = false;
    const applyDirty = (next) => {
        dirty = next;
        shell.state.textContent = next ? '有未保存修改' : '未修改';
        shell.state.classList.toggle('is-dirty', next);
        shell.saveBtn.disabled = !next || Boolean(options.readOnly);
        options.onDirty?.(next);
    };
    const setDirty = (next) => {
        if (dirty !== next) applyDirty(next);
    };
    const schedulePreview = () => {
        if (!renderPreview || !shell.preview || shell.preview.hidden) return;
        window.clearTimeout(timer);
        timer = window.setTimeout(() => {
            if (!destroyed) renderPreview(shell.textarea.value);
        }, PREVIEW_DEBOUNCE_MS);
    };
    shell.textarea.addEventListener('input', () => {
        setDirty(shell.textarea.value !== original);
        schedulePreview();
    });
    shell.textarea.addEventListener('keydown', (event) => {
        if (event.key === 'Tab') {
            event.preventDefault();
            insertAtCursor(shell.textarea, '    ');
            return;
        }
        if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
            event.preventDefault();
            if (!shell.saveBtn.disabled) shell.saveBtn.click();
        }
    });
    shell.root.addEventListener('click', (event) => {
        const toolBtn = event.target.closest('[data-editor-tool]');
        if (toolBtn) {
            event.preventDefault();
            tools.find((tool) => tool.key === toolBtn.dataset.editorTool)?.apply(shell.textarea);
            return;
        }
        if (event.target.closest('[data-editor-toggle-preview]') && shell.preview) {
            const pressed = shell.toggleBtn.getAttribute('aria-pressed') === 'true';
            shell.toggleBtn.setAttribute('aria-pressed', String(!pressed));
            shell.preview.hidden = pressed;
            shell.root.dataset.split = String(!pressed);
            if (!pressed) renderPreview?.(shell.textarea.value);
            return;
        }
        if (event.target.closest('[data-editor-save]')) {
            event.preventDefault();
            options.onSave?.();
            return;
        }
        if (event.target.closest('[data-editor-cancel]')) {
            event.preventDefault();
            options.onCancel?.(dirty);
        }
    });
    if (options.readOnly) {
        shell.textarea.readOnly = true;
        shell.saveBtn.hidden = true;
        shell.cancelBtn.textContent = '关闭';
    }
    renderPreview?.(original);
    return {
        getValue: () => shell.textarea.value,
        setValue: (text) => {
            shell.textarea.value = String(text ?? '');
            setDirty(shell.textarea.value !== original);
            schedulePreview();
        },
        markSaved: (text) => {
            original = String(text ?? shell.textarea.value);
            shell.textarea.value = original;
            applyDirty(false);
        },
        isDirty: () => dirty,
        focus: () => shell.textarea.focus(),
        setBusy: (busy) => {
            shell.saveBtn.disabled = busy || !dirty;
            shell.saveBtn.textContent = busy ? '保存中…' : '保存';
            shell.textarea.disabled = Boolean(busy);
        },
        destroy: () => {
            destroyed = true;
            window.clearTimeout(timer);
        },
    };
}

/* ---------- built-in editors ---------- */

registerMaterialEditor({
    id: 'markdown',
    match: (item) => previewMode(item) === 'markdown' || extensionOf(item) === 'md',
    async mount(container, item, options = {}) {
        const shell = buildShell(container, item, {
            title: 'Markdown 编辑',
            hint: '左侧源码，右侧实时渲染；Ctrl+S 保存。',
            tools: MARKDOWN_TOOLS,
        });
        const renderPreview = async (text) => {
            if (!shell.preview) return;
            await renderFilePreview({
                file: { ...item, is_markdown: true, content: String(text || '') },
                contentEl: shell.preview,
                resolveLinkTarget: options.resolveLinkTarget || null,
                resolveImageTarget: options.resolveImageTarget || null,
            });
        };
        return wireCommon(shell, item, options, { renderPreview, tools: MARKDOWN_TOOLS });
    },
});

registerMaterialEditor({
    id: 'html',
    match: (item) => ['html', 'htm', 'xhtml'].includes(extensionOf(item)),
    async mount(container, item, options = {}) {
        const shell = buildShell(container, item, {
            title: 'HTML 编辑',
            hint: '右侧为沙箱实时渲染（不执行脚本）；保存后渲染页按材料库版本刷新。',
            previewLabel: '实时预览',
        });
        const renderPreview = (text) => {
            if (!shell.preview) return;
            let frame = shell.preview.querySelector('iframe');
            if (!frame) {
                frame = document.createElement('iframe');
                frame.className = 'material-editor__frame';
                frame.setAttribute('sandbox', '');
                frame.title = 'HTML 实时预览';
                shell.preview.replaceChildren(frame);
            }
            frame.srcdoc = String(text || '');
        };
        return wireCommon(shell, item, options, { renderPreview });
    },
});

registerMaterialEditor({
    id: 'text',
    match: (item) => previewMode(item) === 'text' || Boolean(item?.is_text),
    async mount(container, item, options = {}) {
        const kind = getMaterialKind(item);
        const shell = buildShell(container, item, {
            title: `${kind.label}编辑`,
            hint: '纯文本编辑；Tab 插入四个空格，Ctrl+S 保存。',
            withPreview: false,
        });
        return wireCommon(shell, item, options, {});
    },
});

/** Mount the first matching editor; returns the editor handle or null. */
export async function mountMaterialEditor(container, item, options = {}) {
    const editor = resolveMaterialEditor(item);
    if (!editor || !container) return null;
    container.dataset.materialEditor = editor.id || '';
    return editor.mount(container, item, options);
}

export function listMaterialEditors() {
    return registry.map((editor) => editor.id || 'anonymous');
}
