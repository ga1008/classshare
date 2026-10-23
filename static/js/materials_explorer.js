/**
 * Material library explorer: a Windows-11-style file browser inside a modal.
 *
 *   const explorer = createMaterialsExplorer({
 *       mount,            // element that receives the explorer DOM
 *       modalId,          // legacy modal id to open/close (ui.js openModal)
 *       onDetailChange(detail),          // host mirrors the active detail
 *       onNavigate(detail),              // host updates modal header
 *       saveContent(detail, payload),    // host persists editor text (returns API result)
 *       renderActions(detail) -> html,   // host-provided action buttons (data-detail-action)
 *       runAction(action, detail),       // host executes a context-menu action
 *       onGitResult(detail, result),
 *   });
 *   explorer.open(materialId); explorer.refresh(); explorer.close();
 *
 * Layout: toolbar (back / forward / up / address bar / search / view toggle /
 * actions), navigation tree, content pane (folder list or rendered file via
 * material_renderers / material_editors), details pane (properties, Git).
 * Keyboard: ↑↓ move, Enter open, Backspace up, F2 rename, Delete, Alt+←/→.
 */
import { apiFetch } from './api.js';
import { closeModal, escapeHtml, formatDate, formatSize, openModal, showToast } from './ui.js';
import { getMaterialKind, kindIcon } from './materials_kinds.js';
import { renderMaterialPreview } from './material_renderers.js';
import { canEditMaterial, mountMaterialEditor } from './material_editors.js';
import { describeLastSync, renderBindingPlan, renderSyncProgress, renderSyncReport, startRepositoryUpdate } from './materials_sync_report.js';

const VIEW_STORAGE_KEY = 'lanshare:materials:explorer:view';
const PANE_STORAGE_KEY = 'lanshare:materials:explorer:details';

function readStorage(key, fallback) {
    try {
        return window.localStorage.getItem(key) || fallback;
    } catch {
        return fallback;
    }
}

function writeStorage(key, value) {
    try {
        window.localStorage.setItem(key, value);
    } catch {
        /* storage may be unavailable */
    }
}

function nodeMeta(node) {
    if (!node) return '';
    if (node.node_type === 'folder') return `${node.child_count ?? node.children?.length ?? 0} 项`;
    return formatSize(node.file_size || 0);
}

export function createMaterialsExplorer(options = {}) {
    const mount = options.mount;
    if (!mount) throw new TypeError('materials explorer needs a mount element');
    const modalId = options.modalId || 'materials-detail-modal';

    const state = {
        rootId: null,
        root: null,
        nodes: new Map(),
        expanded: new Set(),
        selectedId: null,
        detail: null,
        history: [],
        future: [],
        filter: '',
        view: readStorage(VIEW_STORAGE_KEY, 'list'),
        detailsOpen: readStorage(PANE_STORAGE_KEY, 'open') !== 'closed',
        treeRequest: 0,
        detailRequest: 0,
        editing: false,
        editor: null,
        editorContent: null,
        contextMenu: null,
        git: { progress: null, result: null, plan: null, planLoading: false, running: false },
        stats: null,
    };

    const dom = {};

    /* ---------- data ---------- */

    function indexTree(node, parent = null) {
        if (!node) return;
        node.parent_id = parent ? Number(parent.id) : (node.parent_id ?? null);
        state.nodes.set(Number(node.id), node);
        for (const child of node.children || []) indexTree(child, node);
    }

    function pathTo(id) {
        const path = [];
        let node = state.nodes.get(Number(id));
        while (node) {
            path.unshift(node);
            node = node.parent_id ? state.nodes.get(Number(node.parent_id)) : null;
        }
        return path;
    }

    async function loadTree(materialId, { keepSelection = false } = {}) {
        const request = ++state.treeRequest;
        const data = await apiFetch(`/api/materials/${Number(materialId)}/tree`, { silent: true });
        if (request !== state.treeRequest) return null;
        state.nodes = new Map();
        state.root = data.tree || null;
        state.stats = data.stats || null;
        indexTree(state.root);
        state.rootId = state.root ? Number(state.root.id) : null;
        state.expanded = new Set([...state.expanded].filter((id) => state.nodes.has(id)));
        if (state.rootId) state.expanded.add(state.rootId);
        if (!keepSelection || !state.nodes.has(Number(state.selectedId))) {
            state.selectedId = Number(data.selected_id || materialId);
        }
        for (const node of pathTo(state.selectedId)) {
            if (node.node_type === 'folder') state.expanded.add(Number(node.id));
        }
        return state.root;
    }

    async function loadDetail(materialId) {
        const request = ++state.detailRequest;
        const data = await apiFetch(`/api/materials/${Number(materialId)}`, { silent: true });
        if (request !== state.detailRequest) return null;
        state.detail = data.material || null;
        options.onDetailChange?.(state.detail);
        options.onNavigate?.(state.detail);
        return state.detail;
    }

    /* ---------- rendering ---------- */

    function ensureShell() {
        if (dom.root && mount.contains(dom.root)) return;
        mount.innerHTML = `
            <div class="mx" data-materials-explorer>
                <div class="mx__toolbar" role="toolbar" aria-label="资源管理器工具栏">
                    <div class="mx__nav">
                        <button data-lq-component="button" type="button" class="lq-btn lq-btn--sm lq-btn--glass lq-btn--icon" data-mx="back" title="后退 (Alt+←)" aria-label="后退">${kindIcon('back')}</button>
                        <button data-lq-component="button" type="button" class="lq-btn lq-btn--sm lq-btn--glass lq-btn--icon" data-mx="forward" title="前进 (Alt+→)" aria-label="前进">${kindIcon('forward')}</button>
                        <button data-lq-component="button" type="button" class="lq-btn lq-btn--sm lq-btn--glass lq-btn--icon" data-mx="up" title="上一级 (Backspace)" aria-label="上一级">${kindIcon('up')}</button>
                        <button data-lq-component="button" type="button" class="lq-btn lq-btn--sm lq-btn--glass lq-btn--icon" data-mx="reload" title="刷新" aria-label="刷新">${kindIcon('refresh')}</button>
                    </div>
                    <nav class="mx__address" aria-label="当前位置" data-mx-address></nav>
                    <label class="mx__search lq-search">
                        <input data-lq-component="input" type="search" class="lq-input" placeholder="在当前文档库中筛选" data-mx-search autocomplete="off">
                    </label>
                    <div class="mx__view" role="group" aria-label="内容视图">
                        <button data-lq-component="button" type="button" class="lq-btn lq-btn--sm lq-btn--glass lq-btn--icon" data-mx="view-list" title="详细信息" aria-pressed="false">${kindIcon('list')}</button>
                        <button data-lq-component="button" type="button" class="lq-btn lq-btn--sm lq-btn--glass lq-btn--icon" data-mx="view-grid" title="大图标" aria-pressed="false">${kindIcon('grid')}</button>
                        <button data-lq-component="button" type="button" class="lq-btn lq-btn--sm lq-btn--glass lq-btn--icon" data-mx="toggle-details" title="详细信息窗格" aria-pressed="true">${kindIcon('info')}</button>
                    </div>
                    <div class="mx__actions" data-mx-actions></div>
                </div>
                <div class="mx__body" data-details="${state.detailsOpen ? 'open' : 'closed'}">
                    <aside class="mx__tree" aria-label="目录树">
                        <div class="mx__pane-head"><span>${kindIcon('tree')} 目录</span><small data-mx-tree-stats></small></div>
                        <div class="mx__tree-body" data-mx-tree role="tree"></div>
                    </aside>
                    <main class="mx__content" data-mx-content tabindex="0" aria-label="内容"></main>
                    <aside class="mx__details" aria-label="详细信息" data-mx-details></aside>
                </div>
                <div class="mx__statusbar" data-mx-status></div>
            </div>
        `;
        dom.root = mount.querySelector('[data-materials-explorer]');
        dom.address = mount.querySelector('[data-mx-address]');
        dom.search = mount.querySelector('[data-mx-search]');
        dom.actions = mount.querySelector('[data-mx-actions]');
        dom.body = mount.querySelector('.mx__body');
        dom.tree = mount.querySelector('[data-mx-tree]');
        dom.treeStats = mount.querySelector('[data-mx-tree-stats]');
        dom.content = mount.querySelector('[data-mx-content]');
        dom.details = mount.querySelector('[data-mx-details]');
        dom.status = mount.querySelector('[data-mx-status]');
        bindShell();
    }

    function renderAddress() {
        const path = pathTo(state.selectedId);
        dom.address.innerHTML = path.length
            ? path.map((node, index) => {
                const kind = getMaterialKind(node);
                const last = index === path.length - 1;
                return `${index ? `<span class="mx__crumb-sep" aria-hidden="true">${kindIcon('chevron', 14)}</span>` : ''}<button data-lq-component="button" type="button" class="lq-btn lq-btn--sm lq-btn--ghost mx__crumb${last ? ' is-current' : ''}" data-mx-go="${node.id}" ${last ? 'aria-current="location"' : ''} title="${escapeHtml(node.material_path || '')}">${index === 0 ? `<span class="mx__crumb-icon" style="--kind-color:${escapeHtml(kind.color)}">${kindIcon(kind.icon, 14)}</span>` : ''}${escapeHtml(node.name || '')}</button>`;
            }).join('')
            : '<span class="mx__crumb is-current">材料库</span>';
        mount.querySelector('[data-mx="back"]').disabled = !state.history.length;
        mount.querySelector('[data-mx="forward"]').disabled = !state.future.length;
        const current = state.nodes.get(Number(state.selectedId));
        mount.querySelector('[data-mx="up"]').disabled = !current?.parent_id;
        mount.querySelector('[data-mx="view-list"]').setAttribute('aria-pressed', String(state.view === 'list'));
        mount.querySelector('[data-mx="view-grid"]').setAttribute('aria-pressed', String(state.view === 'grid'));
        mount.querySelector('[data-mx="toggle-details"]').setAttribute('aria-pressed', String(state.detailsOpen));
        dom.body.dataset.details = state.detailsOpen ? 'open' : 'closed';
    }

    function matchesFilter(node) {
        if (!state.filter) return true;
        return String(node.name || '').toLowerCase().includes(state.filter.toLowerCase());
    }

    function subtreeMatches(node) {
        if (matchesFilter(node)) return true;
        return (node.children || []).some(subtreeMatches);
    }

    function renderTreeNode(node, depth) {
        if (state.filter && !subtreeMatches(node)) return '';
        const id = Number(node.id);
        const kind = getMaterialKind(node);
        const hasChildren = (node.children || []).length > 0;
        const expanded = state.expanded.has(id) || Boolean(state.filter);
        const selected = id === Number(state.selectedId);
        const inPath = pathTo(state.selectedId).some((item) => Number(item.id) === id);
        return `
            <div class="mx__tree-node" role="treeitem" aria-expanded="${hasChildren ? String(expanded) : 'false'}" aria-selected="${String(selected)}" data-mx-node="${id}" style="--depth:${depth}">
                <div class="mx__tree-row${selected ? ' is-selected' : ''}${inPath && !selected ? ' is-ancestor' : ''}" data-mx-row="${id}" tabindex="${selected ? 0 : -1}">
                    <button type="button" class="mx__tree-toggle" data-mx-toggle="${id}" aria-label="${expanded ? '收起' : '展开'}" ${hasChildren ? '' : 'disabled'}>${hasChildren ? kindIcon('chevron', 12) : ''}</button>
                    <span class="mx__kind" style="--kind-color:${escapeHtml(kind.color)}">${kindIcon(kind.icon, 15)}</span>
                    <span class="mx__tree-label" title="${escapeHtml(node.material_path || node.name || '')}">${escapeHtml(node.name || '未命名')}</span>
                </div>
                ${hasChildren && expanded ? `<div class="mx__tree-children" role="group">${node.children.map((child) => renderTreeNode(child, depth + 1)).join('')}</div>` : ''}
            </div>
        `;
    }

    function renderTree() {
        dom.tree.innerHTML = state.root ? renderTreeNode(state.root, 0) : '<div class="mx__empty">正在加载目录…</div>';
        const stats = state.stats || {};
        dom.treeStats.textContent = stats.file_count != null ? `${stats.folder_count || 0} 文件夹 · ${stats.file_count || 0} 文件` : '';
    }

    function sortedChildren(node) {
        return [...(node?.children || [])]
            .filter(matchesFilter)
            .sort((a, b) => (a.node_type === b.node_type ? String(a.name).localeCompare(String(b.name), 'zh-Hans-CN') : (a.node_type === 'folder' ? -1 : 1)));
    }

    function renderFolderContents(container, node) {
        const children = sortedChildren(node);
        if (!children.length) {
            container.innerHTML = `<div class="mx__empty">${state.filter ? '没有匹配的项目。' : '此文件夹为空。'}</div>`;
            return;
        }
        if (state.view === 'grid') {
            container.innerHTML = `
                <div class="mx__grid" role="listbox" aria-label="文件夹内容">
                    ${children.map((child) => {
                        const kind = getMaterialKind(child);
                        return `
                            <button type="button" class="mx__tile" role="option" aria-selected="false" data-mx-item="${child.id}" data-node-type="${escapeHtml(child.node_type)}" title="${escapeHtml(child.name || '')}">
                                <span class="mx__tile-icon" style="--kind-color:${escapeHtml(kind.color)}">${kindIcon(kind.icon, 30)}</span>
                                <span class="mx__tile-name">${escapeHtml(child.name || '')}</span>
                                <span class="mx__tile-meta">${escapeHtml(kind.label)} · ${escapeHtml(nodeMeta(child))}</span>
                            </button>`;
                    }).join('')}
                </div>`;
            return;
        }
        container.innerHTML = `
            <table class="mx__list" role="grid" aria-label="文件夹内容">
                <thead><tr><th scope="col">名称</th><th scope="col">类型</th><th scope="col">大小 / 项目</th><th scope="col">修改时间</th></tr></thead>
                <tbody>
                    ${children.map((child) => {
                        const kind = getMaterialKind(child);
                        return `
                            <tr class="mx__list-row" data-mx-item="${child.id}" data-node-type="${escapeHtml(child.node_type)}" tabindex="-1" aria-selected="false">
                                <td class="mx__list-name"><span class="mx__kind" style="--kind-color:${escapeHtml(kind.color)}">${kindIcon(kind.icon, 16)}</span><span title="${escapeHtml(child.material_path || '')}">${escapeHtml(child.name || '')}</span></td>
                                <td><span class="mx__kind-pill" style="--kind-color:${escapeHtml(kind.color)}">${escapeHtml(kind.label)}</span></td>
                                <td>${escapeHtml(nodeMeta(child))}</td>
                                <td>${escapeHtml(formatDate(child.updated_at || child.created_at || '') || '')}</td>
                            </tr>`;
                    }).join('')}
                </tbody>
            </table>`;
    }

    function renderContent() {
        const detail = state.detail;
        const node = state.nodes.get(Number(state.selectedId));
        if (!detail || Number(detail.id) !== Number(state.selectedId)) {
            dom.content.innerHTML = '<div class="mx__empty">正在加载…</div>';
            return;
        }
        destroyEditor();
        const kind = getMaterialKind(detail);
        const head = `
            <header class="mx__content-head">
                <span class="mx__kind mx__kind--lg" style="--kind-color:${escapeHtml(kind.color)}">${kindIcon(kind.icon, 20)}</span>
                <div class="mx__content-title">
                    <h3 title="${escapeHtml(detail.name || '')}">${escapeHtml(detail.name || '未命名')}</h3>
                    <p>${escapeHtml(kind.label)} · ${escapeHtml(nodeMeta(detail.node_type === 'folder' ? { ...detail, child_count: node?.children?.length ?? detail.child_count } : detail))}${detail.updated_at ? ` · 更新于 ${escapeHtml(formatDate(detail.updated_at) || '')}` : ''}</p>
                </div>
                <div class="mx__content-tools">
                    ${detail.preview_url ? `<a data-lq-component="button" class="lq-btn lq-btn--sm lq-btn--glass" href="${escapeHtml(detail.preview_url)}" target="_blank" rel="noopener">${kindIcon('external', 14)} 新标签打开</a>` : ''}
                    ${detail.node_type === 'file' && detail.download_allowed !== false ? `<a data-lq-component="button" class="lq-btn lq-btn--sm lq-btn--glass" href="/materials/download/${Number(detail.id)}">${kindIcon('download', 14)} 下载</a>` : ''}
                    ${canEditMaterial(detail) && detail.can_manage !== false ? `<button data-lq-component="button" type="button" class="lq-btn lq-btn--sm ${state.editing ? 'lq-btn--prominent' : 'lq-btn--glass'}" data-mx="edit" aria-pressed="${String(state.editing)}">${kindIcon('pencil', 14)} ${state.editing ? '正在编辑' : '编辑'}</button>` : ''}
                </div>
            </header>`;
        dom.content.innerHTML = `${head}<div class="mx__content-body" data-mx-body></div>`;
        const body = dom.content.querySelector('[data-mx-body]');
        if (state.editing && canEditMaterial(detail)) {
            mountEditor(body, detail);
            return;
        }
        const request = state.detailRequest;
        renderMaterialPreview(body, detail, {
            isCurrent: () => request === state.detailRequest,
            renderFolder: (container, folder) => renderFolderContents(container, state.nodes.get(Number(folder.id)) || folder),
            onContentLoaded: (data) => { state.editorContent = data; },
        });
    }

    function destroyEditor() {
        state.editor?.destroy?.();
        state.editor = null;
    }

    async function mountEditor(container, detail) {
        container.innerHTML = '<div class="material-renderer__status">正在读取源码…</div>';
        const request = state.detailRequest;
        let data = state.editorContent && Number(state.editorContent.material?.id) === Number(detail.id) ? state.editorContent : null;
        if (!data || data.content == null) {
            try {
                data = await apiFetch(detail.preview_content_url || `/api/materials/${Number(detail.id)}/content`, { silent: true });
            } catch (error) {
                container.innerHTML = `<div class="material-renderer__empty">${escapeHtml(error.message || '无法读取源码')}</div>`;
                return;
            }
        }
        if (request !== state.detailRequest) return;
        state.editorContent = data;
        const editor = await mountMaterialEditor(container, detail, {
            content: String(data.content || ''),
            onDirty: () => renderStatus(),
            onCancel: (dirty) => {
                if (dirty && !window.confirm('放弃未保存的修改？')) return;
                state.editing = false;
                renderContent();
            },
            onSave: async () => {
                if (!state.editor) return;
                state.editor.setBusy(true);
                try {
                    const result = await options.saveContent?.(detail, {
                        content: state.editor.getValue(),
                        encoding: data.encoding || 'utf-8',
                        revision: data.material?.revision || null,
                        sourceRevision: data.material?.source_revision || null,
                    });
                    if (result) {
                        state.editorContent = {
                            ...data,
                            content: result.content ?? state.editor.getValue(),
                            material: { ...(data.material || {}), revision: result.material?.revision || data.material?.revision, source_revision: result.material?.source_revision || data.material?.source_revision },
                        };
                        data = state.editorContent;
                        state.editor.markSaved(state.editorContent.content);
                    }
                } finally {
                    state.editor?.setBusy(false);
                }
            },
        });
        state.editor = editor;
        editor?.focus();
        renderStatus();
    }

    function renderDetails() {
        const detail = state.detail;
        if (!detail) {
            dom.details.innerHTML = '';
            return;
        }
        const kind = getMaterialKind(detail);
        const canManage = detail.can_manage !== false;
        const assignments = Array.isArray(detail.assignments) ? detail.assignments : [];
        const isRepo = Boolean(detail.is_git_repository);
        const summary = detail.ai_parse_result?.summary || detail.summary || '';
        const scopeOptions = [['private', '私有'], ['department', '本系部公开'], ['college', '本院级公开'], ['school', '全校公开'], ['public', '完全公开']];
        const isScopeRoot = detail.parent_id === null || detail.parent_id === undefined;
        dom.details.innerHTML = `
            <div class="mx__pane-head"><span>${kindIcon('info')} 详细信息</span>${canManage ? '<button data-lq-component="button" type="button" class="lq-btn lq-btn--sm lq-btn--prominent" data-detail-action="save-properties">保存</button>' : ''}</div>
            <div class="mx__details-body">
                <div class="mx__details-hero" style="--kind-color:${escapeHtml(kind.color)}">
                    <span class="mx__kind mx__kind--xl">${kindIcon(kind.icon, 34)}</span>
                    <span class="mx__kind-pill">${escapeHtml(kind.label)}</span>
                </div>
                <label class="mx__field">
                    <span>名称</span>
                    <input data-lq-component="input" type="text" class="lq-input" data-property-name value="${escapeHtml(detail.name || '')}" maxlength="120" ${canManage ? '' : 'disabled'}>
                </label>
                ${canManage && isScopeRoot ? `
                <label class="mx__field">
                    <span>开放范围</span>
                    <select data-lq-dropdown data-lq-component="select" class="lq-select" data-property-scope>
                        ${scopeOptions.map(([value, label]) => `<option value="${value}" ${detail.scope_level === value ? 'selected' : ''}>${label}</option>`).join('')}
                    </select>
                </label>` : `<div class="mx__field mx__field--readonly"><span>开放范围</span><strong>${escapeHtml(detail.scope_label || '私有')}${isScopeRoot ? '' : ' · 随最外层'}</strong></div>`}
                <dl class="mx__props">
                    <div><dt>路径</dt><dd title="${escapeHtml(detail.material_path || '')}">${escapeHtml(detail.material_path || '/')}</dd></div>
                    <div><dt>大小 / 子项</dt><dd>${escapeHtml(nodeMeta(detail))}</dd></div>
                    <div><dt>创建</dt><dd>${escapeHtml(formatDate(detail.created_at) || '—')}</dd></div>
                    <div><dt>更新</dt><dd>${escapeHtml(formatDate(detail.updated_at || detail.created_at) || '—')}</dd></div>
                    <div><dt>分配课堂</dt><dd>${assignments.length ? assignments.map((item) => `<span class="lq-chip lq-chip--sm">${escapeHtml(item.course_name || '')} / ${escapeHtml(item.class_name || '')}</span>`).join(' ') : '尚未分配'}</dd></div>
                    ${detail.lessondoc_pack ? `<div><dt>学习文档包</dt><dd>${escapeHtml(String(detail.lessondoc_pack.ready_count))} / ${escapeHtml(String(detail.lessondoc_pack.total_count))} 课就绪</dd></div>` : ''}
                    ${detail.ai_parse_status && detail.can_ai_parse ? `<div><dt>AI 解析</dt><dd>${escapeHtml(detail.ai_parse_status)}</dd></div>` : ''}
                </dl>
                ${summary ? `<div class="mx__summary"><span>AI 摘要</span><p>${escapeHtml(summary)}</p></div>` : ''}
                ${typeof options.renderExtraDetails === 'function' ? (options.renderExtraDetails(detail) || '') : ''}
                ${isRepo ? renderGitPanel(detail) : ''}
            </div>
        `;
        if (isRepo) {
            renderSyncProgress(dom.details.querySelector('[data-mx-git-progress]'), state.git.progress);
            renderSyncReport(dom.details.querySelector('[data-mx-git-report]'), state.git.result, { compact: false });
            if (state.git.plan) renderBindingPlan(dom.details.querySelector('[data-mx-git-plan]'), state.git.plan);
        }
    }

    function renderGitPanel(detail) {
        const last = describeLastSync(detail);
        return `
            <section class="mx__git" data-mx-git>
                <div class="mx__pane-head"><span>${kindIcon('git')} Git 仓库</span><span class="lq-chip lq-chip--sm lq-chip--status" data-lq-status data-lq-tone-level="${escapeHtml(last.tone)}" title="${escapeHtml(last.title)}">${escapeHtml(last.text)}</span></div>
                <dl class="mx__props">
                    <div><dt>远程</dt><dd title="${escapeHtml(detail.git_remote_url || '')}">${escapeHtml(detail.git_remote_url || '未识别')}</dd></div>
                    <div><dt>分支</dt><dd>${escapeHtml(detail.git_default_branch || detail.git_head_branch || '未识别')}${detail.git_head_commit ? ` <code>${escapeHtml(String(detail.git_head_commit).slice(0, 8))}</code>` : ''}</dd></div>
                </dl>
                <div class="mx__git-actions">
                    <button data-lq-component="button" type="button" class="lq-btn lq-btn--sm lq-btn--prominent" data-mx="git-update" ${state.git.running || detail.can_manage === false ? 'disabled' : ''}>${kindIcon('refresh', 14)} ${state.git.running ? '更新中…' : '一键更新'}</button>
                    <button data-lq-component="button" type="button" class="lq-btn lq-btn--sm lq-btn--glass" data-mx="git-plan" ${state.git.planLoading ? 'disabled' : ''}>${state.git.planLoading ? '读取中…' : '课次对应预览'}</button>
                    <button data-lq-component="button" type="button" class="lq-btn lq-btn--sm lq-btn--ghost" data-detail-action="repository">仓库控制台</button>
                </div>
                <div data-mx-git-progress hidden></div>
                <div data-mx-git-report></div>
                <div data-mx-git-plan></div>
            </section>
        `;
    }

    function renderActions() {
        dom.actions.innerHTML = typeof options.renderActions === 'function' && state.detail ? options.renderActions(state.detail) : '';
    }

    function renderStatus() {
        const node = state.nodes.get(Number(state.selectedId));
        const parts = [];
        if (node?.node_type === 'folder') parts.push(`${sortedChildren(node).length} 个项目`);
        if (state.detail?.material_path) parts.push(state.detail.material_path);
        if (state.editor?.isDirty()) parts.push('有未保存修改');
        dom.status.textContent = parts.join(' · ');
    }

    function renderAll() {
        renderAddress();
        renderTree();
        renderContent();
        renderDetails();
        renderActions();
        renderStatus();
    }

    /* ---------- navigation ---------- */

    async function select(materialId, { record = true } = {}) {
        const id = Number(materialId);
        if (!id || !state.nodes.has(id)) return;
        if (state.editor?.isDirty() && !window.confirm('当前文档有未保存修改，离开将丢失，是否继续？')) return;
        if (record && state.selectedId && state.selectedId !== id) {
            state.history.push(state.selectedId);
            state.future = [];
        }
        state.editing = false;
        state.editorContent = null;
        state.git = { ...state.git, result: null, plan: null, progress: state.git.running ? state.git.progress : null };
        state.selectedId = id;
        for (const node of pathTo(id)) if (node.node_type === 'folder') state.expanded.add(Number(node.id));
        renderAddress();
        renderTree();
        dom.content.innerHTML = '<div class="mx__empty">正在加载…</div>';
        try {
            await loadDetail(id);
        } catch (error) {
            dom.content.innerHTML = `<div class="mx__empty">${escapeHtml(error.message || '加载失败')}</div>`;
            return;
        }
        if (Number(state.selectedId) !== id) return;
        renderContent();
        renderDetails();
        renderActions();
        renderStatus();
        dom.tree.querySelector(`[data-mx-row="${id}"]`)?.scrollIntoView({ block: 'nearest' });
    }

    function goBack() {
        const previous = state.history.pop();
        if (!previous) return;
        state.future.push(state.selectedId);
        select(previous, { record: false });
    }

    function goForward() {
        const next = state.future.pop();
        if (!next) return;
        state.history.push(state.selectedId);
        select(next, { record: false });
    }

    function goUp() {
        const node = state.nodes.get(Number(state.selectedId));
        if (node?.parent_id) select(node.parent_id);
    }

    /* ---------- git ---------- */

    async function runGitUpdate() {
        const detail = state.detail;
        if (!detail?.is_git_repository || state.git.running) return;
        const id = Number(detail.id);
        state.git.running = true;
        state.git.result = null;
        renderDetails();
        const run = startRepositoryUpdate(id, {
            onProgress: (progress) => {
                if (Number(state.selectedId) !== id) return;
                state.git.progress = progress;
                renderSyncProgress(dom.details.querySelector('[data-mx-git-progress]'), progress);
            },
        });
        try {
            const result = await run.promise;
            state.git.result = result;
            state.git.progress = { status: result.status, phase: 'done', label: result.status === 'success' ? '完成' : (result.status === 'auth_required' ? '需要登录' : '失败'), percent: 100, message: result.message || '' };
            showToast([result.message, result.learning_bindings?.message].filter(Boolean).join('；'), result.status === 'success' ? 'success' : (result.status === 'auth_required' ? 'warning' : 'error'), 5200);
            options.onGitResult?.(detail, result);
            await refresh();
        } catch (error) {
            state.git.progress = { status: 'failed', phase: 'failed', label: '失败', percent: 100, message: error.message || '' };
            showToast(error.message || '仓库更新失败', 'error');
        } finally {
            state.git.running = false;
            if (Number(state.selectedId) === id) renderDetails();
        }
    }

    async function loadBindingPlan() {
        const detail = state.detail;
        if (!detail?.is_git_repository || state.git.planLoading) return;
        state.git.planLoading = true;
        renderDetails();
        try {
            state.git.plan = await apiFetch(`/api/materials/${Number(detail.id)}/repository/binding-plan`, { silent: true });
        } catch (error) {
            showToast(error.message || '读取课次对应预览失败', 'error');
        } finally {
            state.git.planLoading = false;
            renderDetails();
        }
    }

    /* ---------- context menu ---------- */

    function closeContextMenu() {
        state.contextMenu?.remove();
        state.contextMenu = null;
    }

    function openContextMenu(event, node) {
        closeContextMenu();
        const items = [{ action: 'open', label: node.node_type === 'folder' ? '打开' : '查看' }];
        if (node.preview_url) items.push({ action: 'external', label: '新标签打开' });
        if (node.node_type === 'file') items.push({ action: 'download', label: '下载' });
        if (node.editable && state.detail?.can_manage !== false) items.push({ action: 'edit', label: '编辑源码' });
        if (state.detail?.can_manage !== false) {
            items.push({ action: 'rename', label: '重命名' });
            if (node.node_type === 'folder') items.push({ action: 'new-folder', label: '新建文件夹' }, { action: 'new-file', label: '新建文档' });
            items.push({ action: 'move', label: '移动到…' });
            items.push({ action: 'delete', label: '删除', danger: true });
        }
        const menu = document.createElement('div');
        menu.className = 'mx__menu lq-glass';
        menu.setAttribute('role', 'menu');
        menu.innerHTML = items.map((item) => `<button type="button" class="mx__menu-item${item.danger ? ' is-danger' : ''}" role="menuitem" data-mx-menu="${item.action}" data-node="${node.id}">${escapeHtml(item.label)}</button>`).join('');
        document.body.appendChild(menu);
        const x = Math.min(event.clientX, window.innerWidth - menu.offsetWidth - 8);
        const y = Math.min(event.clientY, window.innerHeight - menu.offsetHeight - 8);
        menu.style.left = `${Math.max(8, x)}px`;
        menu.style.top = `${Math.max(8, y)}px`;
        state.contextMenu = menu;
        menu.querySelector('[role="menuitem"]')?.focus();
        menu.addEventListener('click', (clickEvent) => {
            const button = clickEvent.target.closest('[data-mx-menu]');
            if (!button) return;
            const action = button.dataset.mxMenu;
            const targetId = Number(button.dataset.node);
            closeContextMenu();
            runContextAction(action, targetId);
        });
    }

    async function runContextAction(action, targetId) {
        const node = state.nodes.get(targetId);
        if (!node) return;
        if (action === 'open') return select(targetId);
        if (action === 'external' && node.preview_url) return window.open(node.preview_url, '_blank', 'noopener');
        if (action === 'download') return window.open(`/materials/download/${targetId}`, '_blank', 'noopener');
        if (action === 'edit') {
            await select(targetId);
            state.editing = true;
            renderContent();
            return;
        }
        if (action === 'rename') {
            await select(targetId);
            const input = dom.details.querySelector('[data-property-name]');
            input?.focus();
            input?.select();
            return;
        }
        if (Number(state.selectedId) !== targetId) await select(targetId);
        const map = { 'new-folder': 'create-folder', 'new-file': 'create-file', move: 'move', delete: 'delete' };
        const hostAction = map[action];
        if (hostAction) options.runAction?.(hostAction, state.detail);
        return undefined;
    }

    /* ---------- events ---------- */

    function bindShell() {
        dom.root.addEventListener('click', (event) => {
            const control = event.target.closest('[data-mx]');
            if (control && dom.root.contains(control)) {
                const command = control.dataset.mx;
                if (command === 'back') goBack();
                else if (command === 'forward') goForward();
                else if (command === 'up') goUp();
                else if (command === 'reload') refresh();
                else if (command === 'view-list' || command === 'view-grid') {
                    state.view = command === 'view-grid' ? 'grid' : 'list';
                    writeStorage(VIEW_STORAGE_KEY, state.view);
                    renderAddress();
                    renderContent();
                } else if (command === 'toggle-details') {
                    state.detailsOpen = !state.detailsOpen;
                    writeStorage(PANE_STORAGE_KEY, state.detailsOpen ? 'open' : 'closed');
                    renderAddress();
                } else if (command === 'edit') {
                    if (state.editing && state.editor?.isDirty() && !window.confirm('放弃未保存的修改？')) return;
                    state.editing = !state.editing;
                    renderContent();
                } else if (command === 'git-update') runGitUpdate();
                else if (command === 'git-plan') loadBindingPlan();
                return;
            }
            const crumb = event.target.closest('[data-mx-go]');
            if (crumb) {
                select(Number(crumb.dataset.mxGo));
                return;
            }
            const toggle = event.target.closest('[data-mx-toggle]');
            if (toggle) {
                event.preventDefault();
                const id = Number(toggle.dataset.mxToggle);
                if (state.expanded.has(id)) state.expanded.delete(id);
                else state.expanded.add(id);
                renderTree();
                return;
            }
            const row = event.target.closest('[data-mx-row]');
            if (row) {
                select(Number(row.dataset.mxRow));
                return;
            }
            const item = event.target.closest('[data-mx-item]');
            if (item) {
                const id = Number(item.dataset.mxItem);
                if (event.detail >= 2 || item.dataset.nodeType === 'file' || item.classList.contains('mx__tile')) {
                    select(id);
                } else {
                    dom.content.querySelectorAll('[data-mx-item]').forEach((el) => el.setAttribute('aria-selected', String(el === item)));
                    item.focus?.();
                }
            }
        });
        dom.root.addEventListener('dblclick', (event) => {
            const item = event.target.closest('[data-mx-item]');
            if (item) select(Number(item.dataset.mxItem));
        });
        dom.root.addEventListener('contextmenu', (event) => {
            const target = event.target.closest('[data-mx-item], [data-mx-row]');
            if (!target) return;
            const node = state.nodes.get(Number(target.dataset.mxItem || target.dataset.mxRow));
            if (!node) return;
            event.preventDefault();
            openContextMenu(event, node);
        });
        dom.search.addEventListener('input', () => {
            state.filter = String(dom.search.value || '').trim();
            renderTree();
            if (state.detail?.node_type === 'folder' && !state.editing) renderContent();
            renderStatus();
        });
        dom.root.addEventListener('keydown', onKeydown);
        document.addEventListener('click', (event) => {
            if (state.contextMenu && !state.contextMenu.contains(event.target)) closeContextMenu();
        }, true);
        document.addEventListener('keydown', (event) => {
            if (event.key === 'Escape' && state.contextMenu) closeContextMenu();
        });
    }

    function onKeydown(event) {
        if (event.target.matches('input, textarea, select') || state.editing) return;
        if (event.altKey && event.key === 'ArrowLeft') { event.preventDefault(); goBack(); return; }
        if (event.altKey && event.key === 'ArrowRight') { event.preventDefault(); goForward(); return; }
        if (event.key === 'Backspace') { event.preventDefault(); goUp(); return; }
        if (event.key === 'F2') { event.preventDefault(); dom.details.querySelector('[data-property-name]')?.focus(); return; }
        if (event.key === 'Delete' && state.detail?.can_manage !== false) { event.preventDefault(); options.runAction?.('delete', state.detail); return; }
        const rows = [...dom.content.querySelectorAll('[data-mx-item]')];
        if (!rows.length) return;
        const currentIndex = rows.findIndex((row) => row.getAttribute('aria-selected') === 'true');
        const move = (index) => {
            const next = rows[Math.max(0, Math.min(rows.length - 1, index))];
            rows.forEach((row) => row.setAttribute('aria-selected', String(row === next)));
            next.focus?.();
            next.scrollIntoView({ block: 'nearest' });
        };
        if (event.key === 'ArrowDown') { event.preventDefault(); move(currentIndex + 1); }
        else if (event.key === 'ArrowUp') { event.preventDefault(); move(currentIndex - 1); }
        else if (event.key === 'Home') { event.preventDefault(); move(0); }
        else if (event.key === 'End') { event.preventDefault(); move(rows.length - 1); }
        else if (event.key === 'Enter' && currentIndex >= 0) { event.preventDefault(); select(Number(rows[currentIndex].dataset.mxItem)); }
    }

    /* ---------- public ---------- */

    async function open(materialId) {
        ensureShell();
        state.history = [];
        state.future = [];
        state.editing = false;
        state.git = { progress: null, result: null, plan: null, planLoading: false, running: false };
        state.selectedId = Number(materialId);
        dom.search.value = '';
        state.filter = '';
        dom.content.innerHTML = '<div class="mx__empty">正在加载材料…</div>';
        dom.tree.innerHTML = '<div class="mx__empty">正在加载目录…</div>';
        dom.details.innerHTML = '';
        openModal(modalId);
        await Promise.all([loadTree(materialId), loadDetail(materialId)]);
        renderAll();
        dom.content.focus({ preventScroll: true });
    }

    async function refresh({ keepEditor = false } = {}) {
        if (!state.rootId) return;
        const anchor = Number(state.selectedId);
        try {
            const root = await loadTree(state.nodes.has(anchor) ? anchor : state.rootId, { keepSelection: true });
            if (!root) return;
        } catch (error) {
            if (error?.status === 404) {
                close();
                return;
            }
            throw error;
        }
        if (!state.nodes.has(anchor)) {
            state.selectedId = Number(state.rootId);
        }
        if (!keepEditor) state.editing = false;
        await loadDetail(state.selectedId);
        renderAll();
    }

    function close() {
        destroyEditor();
        closeContextMenu();
        closeModal(modalId);
    }

    function presentGitResult(materialId, result) {
        if (Number(state.selectedId) !== Number(materialId) || !state.detail?.is_git_repository) return;
        state.git.result = result || null;
        state.git.progress = null;
        renderDetails();
    }

    return {
        open,
        refresh,
        close,
        select,
        presentGitResult,
        get activeId() { return state.selectedId; },
        get detail() { return state.detail; },
        isEditing: () => state.editing,
        hasUnsavedChanges: () => Boolean(state.editor?.isDirty()),
    };
}
