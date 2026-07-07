/**
 * Reusable menu + option-form modal (带菜单的选项表单浮窗).
 *
 * Use this for flows where the user first chooses an item through a collapsible
 * tree, then reviews base information and edits a small set of options before
 * confirming. The first consumer is 教师评学表按班级生成.
 *
 * Tree node shape:
 *   branch: { label, badge?, children: [...], expanded? }
 *   leaf:   { label, badge?, leaf: true, data: <caller payload> }
 *
 * onSelect(data, node) -> {
 *   title?: string,
 *   baseInfo: [{ label, value }],
 *   fields: [{ key, label, value?, placeholder?, type?('text'|'select'), options?, hint? }],
 *   note?: string
 * }
 *
 * onConfirm({ node, data, fieldValues, prompt }) -> boolean | Promise<boolean>
 *   Return false to keep the modal open; any other value closes it.
 */

import { escapeHtml } from './ui.js';
import { enhancePromptPoolInput, recordPromptForInput, isPromptShareEnabled } from './prompt_pool.js';

function safeText(value, fallback = '') {
    const text = value == null ? '' : String(value);
    return text || fallback;
}

function htmlAttr(value) {
    return escapeHtml(safeText(value));
}

export function openTreeSelectFormModal(config) {
    const {
        title = '选择',
        subtitle = '',
        tree = [],
        treeTitle = '选择对象',
        treeHint = '',
        onSelect,
        onConfirm,
        confirmLabel = '确定',
        promptLabel = '',
        promptPlaceholder = '',
        promptPoolKey = '',
        hint = '',
        hintHtml = '',
        emptyText = '暂无可选项',
        placeholderTitle = '请选择对象',
        placeholderText = '请从左侧逐级展开并选择一项',
        fieldsTitle = '可配置选项',
        baseInfoTitle = '基础信息',
        autoExpandDepth = 2,
        levelLabels = [],
    } = config || {};

    const overlay = document.createElement('div');
    overlay.className = 'lp-modal-overlay tsf-overlay';
    overlay.innerHTML = `
        <div class="tsf-modal" role="dialog" aria-modal="true" aria-labelledby="tsf-modal-title">
            <header class="tsf-modal__head">
                <div>
                    <h3 id="tsf-modal-title">${escapeHtml(title)}</h3>
                    ${subtitle ? `<p>${escapeHtml(subtitle)}</p>` : ''}
                </div>
                <button type="button" class="tsf-close" data-tsf-close aria-label="关闭">×</button>
            </header>
            <div class="tsf-modal__body">
                <aside class="tsf-tree">
                    <div class="tsf-tree__head">
                        <strong>${escapeHtml(treeTitle)}</strong>
                        ${treeHint ? `<small>${escapeHtml(treeHint)}</small>` : ''}
                    </div>
                    <div class="tsf-tree__list" data-tsf-tree></div>
                </aside>
                <section class="tsf-panel" data-tsf-panel></section>
            </div>
        </div>`;
    document.body.appendChild(overlay);

    const treeEl = overlay.querySelector('[data-tsf-tree]');
    const panelEl = overlay.querySelector('[data-tsf-panel]');
    const closeBtn = overlay.querySelector('[data-tsf-close]');

    const expanded = new Set();
    const state = {
        selectedPath: null,
        selectedNode: null,
        fieldValues: {},
        prompt: '',
        token: 0,
    };

    const close = () => {
        overlay.remove();
        document.removeEventListener('keydown', onKey);
    };

    function onKey(e) {
        if (e.key === 'Escape') close();
    }

    overlay.addEventListener('click', (e) => {
        if (e.target === overlay || e.target.closest('[data-tsf-close]')) close();
    });
    document.addEventListener('keydown', onKey);
    closeBtn?.focus({ preventScroll: true });

    // Seed default expansion: first branch at each level, plus explicit expanded nodes.
    (function seedExpansion(nodes, prefix, depth) {
        if (!nodes || !nodes.length || depth <= 0) return;
        nodes.forEach((node, index) => {
            const path = prefix ? `${prefix}.${index}` : String(index);
            if (!node.leaf && (node.expanded || index === 0)) {
                expanded.add(path);
                seedExpansion(node.children, path, depth - 1);
            }
        });
    })(tree, '', autoExpandDepth);

    function nodeByPath(path) {
        let list = tree;
        let node = null;
        for (const part of String(path || '').split('.')) {
            node = (list || [])[Number(part)];
            if (!node) return null;
            list = node.children;
        }
        return node;
    }

    function pathLabels(path) {
        const labels = [];
        let list = tree;
        for (const part of String(path || '').split('.')) {
            const node = (list || [])[Number(part)];
            if (!node) break;
            labels.push(node.label || '');
            list = node.children;
        }
        return labels.filter(Boolean);
    }

    function renderTreeNodes(nodes, prefix, level = 1) {
        if (!nodes || !nodes.length) return '';
        return nodes.map((node, index) => {
            const path = prefix ? `${prefix}.${index}` : String(index);
            const badge = node.badge ? `<span class="tsf-node__badge">${escapeHtml(node.badge)}</span>` : '';
            const label = escapeHtml(node.label || '');
            const levelClass = ` tsf-level-${Math.min(level, 3)}`;
            const kindLabel = levelLabels[level - 1]
                ? `<span class="tsf-node__kind">${escapeHtml(levelLabels[level - 1])}</span>`
                : '';
            const mainLabel = `<span class="tsf-node__main">${kindLabel}<span class="tsf-node__label">${label}</span></span>`;
            if (node.leaf) {
                const active = state.selectedPath === path ? ' is-active' : '';
                return `
                    <button type="button" class="tsf-leaf${levelClass}${active}" data-tsf-leaf="${path}" style="--tsf-indent:${(level - 1) * 14}px">
                        <span class="tsf-leaf__dot"></span>
                        ${mainLabel}
                        ${badge}
                    </button>`;
            }
            const isOpen = expanded.has(path);
            const childHtml = isOpen ? renderTreeNodes(node.children, path, level + 1) : '';
            return `
                <div class="tsf-node${levelClass}${isOpen ? ' is-open' : ''}" data-tsf-node="${path}" style="--tsf-indent:${(level - 1) * 14}px">
                    <button type="button" class="tsf-node__head${levelClass}" data-tsf-toggle="${path}" aria-expanded="${isOpen ? 'true' : 'false'}">
                        <span class="tsf-node__chevron">${isOpen ? '▾' : '▸'}</span>
                        ${mainLabel}
                        ${badge}
                    </button>
                    <div class="tsf-node__children">${childHtml}</div>
                </div>`;
        }).join('');
    }

    function renderTree() {
        treeEl.innerHTML = tree.length
            ? renderTreeNodes(tree, '')
            : `<div class="tsf-tree__empty">${escapeHtml(emptyText)}</div>`;
    }

    function renderPlaceholder() {
        panelEl.innerHTML = `
            <div class="tsf-panel__placeholder">
                <span class="tsf-panel__placeholder-mark">01</span>
                <strong>${escapeHtml(placeholderTitle)}</strong>
                <p>${escapeHtml(placeholderText)}</p>
            </div>`;
    }

    function renderLoading() {
        panelEl.innerHTML = `
            <div class="tsf-panel__placeholder tsf-panel__placeholder--loading">
                <span class="tsf-spinner"></span>
                <strong>正在加载</strong>
                <p>正在读取所选项信息…</p>
            </div>`;
    }

    function fieldControl(field) {
        const key = safeText(field.key);
        const value = state.fieldValues[key] ?? '';
        if (field.type === 'select') {
            const opts = (field.options || [])
                .map((option) => {
                    const optionValue = typeof option === 'string' ? option : option.value;
                    const optionLabel = typeof option === 'string' ? option : (option.label ?? option.value);
                    const selected = String(optionValue ?? '') === String(value ?? '') ? ' selected' : '';
                    return `<option value="${htmlAttr(optionValue)}"${selected}>${escapeHtml(optionLabel ?? '')}</option>`;
                })
                .join('');
            return `<select data-tsf-field="${htmlAttr(key)}"><option value="">未填写</option>${opts}</select>`;
        }
        return `<input data-tsf-field="${htmlAttr(key)}" value="${htmlAttr(value)}" placeholder="${htmlAttr(field.placeholder || '')}">`;
    }

    function renderPanel(descriptor) {
        const selectedLabels = pathLabels(state.selectedPath);
        const baseInfo = Array.isArray(descriptor.baseInfo) ? descriptor.baseInfo : [];
        const fields = Array.isArray(descriptor.fields) ? descriptor.fields : [];
        state.fieldValues = {};
        fields.forEach((field) => {
            if (field?.key) state.fieldValues[field.key] = field.value ?? '';
        });

        const crumbs = selectedLabels.length
            ? `<div class="tsf-crumbs">${selectedLabels.map((label) => `<span>${escapeHtml(label)}</span>`).join('')}</div>`
            : '';
        const baseHtml = baseInfo.length
            ? `<div class="tsf-base">${baseInfo.map((row) => `
                <div class="tsf-base__row">
                    <span class="tsf-base__label">${escapeHtml(row.label)}</span>
                    <span class="tsf-base__value">${escapeHtml(row.value || '—')}</span>
                </div>`).join('')}</div>`
            : '';
        const noteHtml = descriptor.note ? `<p class="tsf-panel__note">${escapeHtml(descriptor.note)}</p>` : '';
        const fieldsHtml = fields.length
            ? `<div class="tsf-fields">${fields.map((field) => `
                <label class="tsf-field">
                    <span class="tsf-field__label">${escapeHtml(field.label)}</span>
                    ${fieldControl(field)}
                    ${field.hint ? `<small class="tsf-field__hint">${escapeHtml(field.hint)}</small>` : ''}
                </label>`).join('')}</div>`
            : '';
        const promptHtml = (promptLabel || promptPlaceholder)
            ? `<label class="tsf-field tsf-field--full">
                    <span class="tsf-field__label">${escapeHtml(promptLabel || '补充说明')}</span>
                    <textarea data-tsf-prompt rows="4"${promptPoolKey ? ` data-prompt-pool-key="${htmlAttr(promptPoolKey)}"` : ''} placeholder="${htmlAttr(promptPlaceholder)}">${escapeHtml(state.prompt)}</textarea>
               </label>`
            : '';
        const hintContent = hintHtml || (hint ? escapeHtml(hint) : '');
        const hintBlock = hintContent ? `<p class="tsf-panel__hint">${hintContent}</p>` : '';

        panelEl.innerHTML = `
            <div class="tsf-panel__scroll">
                ${crumbs}
                <div class="tsf-panel__section">
                    <h4 class="tsf-panel__title">${escapeHtml(descriptor.title || baseInfoTitle)}</h4>
                    ${baseHtml}
                    ${noteHtml}
                </div>
                ${fields.length ? `
                    <div class="tsf-panel__section">
                        <h4 class="tsf-panel__title">${escapeHtml(fieldsTitle)}</h4>
                        ${fieldsHtml}
                    </div>` : ''}
                <div class="tsf-panel__section">
                    ${promptHtml}
                    ${hintBlock}
                </div>
            </div>
            <div class="tsf-panel__foot">
                <button type="button" class="tsf-btn tsf-btn--primary" data-tsf-confirm>${escapeHtml(confirmLabel)}</button>
            </div>`;
        const promptInput = panelEl.querySelector('[data-tsf-prompt][data-prompt-pool-key]');
        if (promptInput) enhancePromptPoolInput(promptInput);
    }

    async function selectLeaf(path, node) {
        if (!node) return;
        state.selectedPath = path;
        state.selectedNode = node;
        renderTree();
        const token = ++state.token;
        renderLoading();
        let descriptor = {};
        try {
            descriptor = (onSelect ? await onSelect(node.data, node) : {}) || {};
        } catch (_) {
            descriptor = { baseInfo: [], fields: [], note: '加载所选项信息失败，可直接生成或重试。' };
        }
        if (token !== state.token) return;
        renderPanel(descriptor);
    }

    async function handleConfirm(btn) {
        if (!state.selectedNode || btn.disabled) return;
        btn.disabled = true;
        const original = btn.textContent;
        btn.textContent = '处理中…';
        try {
            const promptInput = panelEl.querySelector('[data-tsf-prompt]');
            const ok = onConfirm ? await onConfirm({
                node: state.selectedNode,
                data: state.selectedNode.data,
                fieldValues: { ...state.fieldValues },
                prompt: state.prompt,
                sharePrompt: isPromptShareEnabled(promptInput),
            }) : true;
            if (ok !== false) {
                await recordPromptForInput(promptInput, state.prompt);
                close();
                return;
            }
        } catch (_) {
            // onConfirm should surface its own toast. Keep the modal open.
        }
        btn.disabled = false;
        btn.textContent = original;
    }

    treeEl.addEventListener('click', (e) => {
        const toggle = e.target.closest('[data-tsf-toggle]');
        if (toggle) {
            const path = toggle.dataset.tsfToggle;
            if (expanded.has(path)) expanded.delete(path); else expanded.add(path);
            renderTree();
            return;
        }
        const leaf = e.target.closest('[data-tsf-leaf]');
        if (leaf) selectLeaf(leaf.dataset.tsfLeaf, nodeByPath(leaf.dataset.tsfLeaf));
    });

    panelEl.addEventListener('input', (e) => {
        const field = e.target.closest('[data-tsf-field]');
        if (field) {
            state.fieldValues[field.dataset.tsfField] = field.value;
            return;
        }
        const prompt = e.target.closest('[data-tsf-prompt]');
        if (prompt) state.prompt = prompt.value;
    });
    panelEl.addEventListener('change', (e) => {
        const field = e.target.closest('[data-tsf-field]');
        if (field) state.fieldValues[field.dataset.tsfField] = field.value;
    });
    panelEl.addEventListener('click', (e) => {
        const confirmBtn = e.target.closest('[data-tsf-confirm]');
        if (confirmBtn) handleConfirm(confirmBtn);
    });

    renderTree();
    renderPlaceholder();

    return { overlay, close };
}
