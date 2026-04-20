import { apiFetch } from '/static/js/api.js';
import { closeModal, escapeHtml, getFileIcon, openModal, showToast } from '/static/js/ui.js';

const SELECTOR_MODAL_ID = 'learningMaterialSelectorModal';
const SEARCH_DEBOUNCE_MS = 180;

const state = {
    initialized: false,
    currentParentId: null,
    history: [],
    breadcrumbs: [],
    items: [],
    keyword: '',
    selectedMaterial: null,
    options: {},
    resolve: null,
    cache: new Map(),
    searchTimer: 0,
};

function refs() {
    return {
        modal: document.getElementById(SELECTOR_MODAL_ID),
        title: document.getElementById('learningMaterialSelectorTitle'),
        subtitle: document.getElementById('learningMaterialSelectorSubtitle'),
        breadcrumbs: document.getElementById('learningMaterialSelectorBreadcrumbs'),
        current: document.getElementById('learningMaterialSelectorCurrent'),
        search: document.getElementById('learningMaterialSelectorSearch'),
        backBtn: document.getElementById('learningMaterialSelectorBackBtn'),
        upBtn: document.getElementById('learningMaterialSelectorUpBtn'),
        refreshBtn: document.getElementById('learningMaterialSelectorRefreshBtn'),
        list: document.getElementById('learningMaterialSelectorList'),
        selected: document.getElementById('learningMaterialSelectorSelected'),
        selectedName: document.getElementById('learningMaterialSelectorSelectedName'),
        selectedPath: document.getElementById('learningMaterialSelectorSelectedPath'),
        footerNote: document.getElementById('learningMaterialSelectorFooterNote'),
        clearBtn: document.getElementById('learningMaterialSelectorClearBtn'),
        confirmBtn: document.getElementById('learningMaterialSelectorConfirmBtn'),
        cancelBtn: document.getElementById('learningMaterialSelectorCancelBtn'),
        closeBtn: document.getElementById('learningMaterialSelectorCloseBtn'),
    };
}

function buildCacheKey(parentId, keyword) {
    return `${parentId ?? 'root'}::${String(keyword || '').trim().toLowerCase()}`;
}

function normalizeMaterial(material) {
    if (!material || !material.id) return null;
    return {
        id: Number(material.id),
        parent_id: material.parent_id == null ? null : Number(material.parent_id),
        name: String(material.name || '').trim(),
        material_path: String(material.material_path || '').trim(),
        preview_type: String(material.preview_type || 'markdown').trim(),
        node_type: 'file',
        viewer_url: String(
            material.viewer_url
            || material.viewerUrl
            || material.learning_material_viewer_url
            || `/materials/view/${Number(material.id)}`,
        ),
    };
}

function normalizeItem(item) {
    if (!item || !item.id) return null;
    if (item.node_type === 'folder') {
        return {
            id: Number(item.id),
            parent_id: item.parent_id == null ? null : Number(item.parent_id),
            name: String(item.name || '').trim(),
            material_path: String(item.material_path || '').trim(),
            node_type: 'folder',
            preview_type: String(item.preview_type || '').trim(),
        };
    }
    if (item.node_type === 'file' && item.preview_type === 'markdown') {
        return {
            id: Number(item.id),
            parent_id: item.parent_id == null ? null : Number(item.parent_id),
            name: String(item.name || '').trim(),
            material_path: String(item.material_path || '').trim(),
            node_type: 'file',
            preview_type: 'markdown',
            viewer_url: `/materials/view/${Number(item.id)}`,
        };
    }
    return null;
}

function getVisibleItems() {
    return (state.items || []).map((item) => normalizeItem(item)).filter(Boolean);
}

function getScopeName() {
    if (!state.breadcrumbs.length) {
        return '材料库根目录';
    }
    return String(state.breadcrumbs[state.breadcrumbs.length - 1].name || '').trim() || '当前目录';
}

function renderBreadcrumbs() {
    const dom = refs();
    if (!dom.breadcrumbs) return;

    if (!state.breadcrumbs.length) {
        dom.breadcrumbs.innerHTML = '<span>材料库</span>';
        return;
    }

    const parts = ['<button type="button" data-crumb-root="true">材料库</button>'];
    state.breadcrumbs.forEach((crumb) => {
        parts.push('<span class="separator">/</span>');
        parts.push(`<button type="button" data-crumb-id="${Number(crumb.id)}">${escapeHtml(crumb.name || '')}</button>`);
    });
    dom.breadcrumbs.innerHTML = parts.join('');
}

function renderCurrentHint() {
    const dom = refs();
    if (!dom.current) return;

    if (state.keyword) {
        dom.current.textContent = `当前在“${getScopeName()}”内搜索，共 ${getVisibleItems().length} 项结果。`;
        return;
    }

    dom.current.textContent = `当前位置：${getScopeName()}。单击文件选中，双击文件夹继续进入。`;
}

function renderSelectedMaterial() {
    const dom = refs();
    const material = state.selectedMaterial;
    const hasMaterial = Boolean(material && material.id);
    const allowClear = Boolean(state.options.allowClear);

    dom.selected?.classList.toggle('is-empty', !hasMaterial);
    if (dom.clearBtn) {
        dom.clearBtn.hidden = !allowClear;
        dom.clearBtn.disabled = !allowClear || !hasMaterial;
        dom.clearBtn.textContent = state.options.clearLabel || '清空当前文档';
    }
    if (dom.confirmBtn) {
        dom.confirmBtn.disabled = !hasMaterial;
    }

    if (!hasMaterial) {
        if (dom.selectedName) dom.selectedName.textContent = '尚未选择文档';
        if (dom.selectedPath) dom.selectedPath.textContent = '单击列表中的 Markdown 文件后确认';
        return;
    }

    if (dom.selectedName) dom.selectedName.textContent = material.name || '已选择文档';
    if (dom.selectedPath) dom.selectedPath.textContent = material.material_path || '';
}

function buildFolderIcon() {
    return { color: '#0ea5e9', label: 'DIR' };
}

function renderLoadingState() {
    const dom = refs();
    if (!dom.list) return;
    dom.list.innerHTML = '<div class="learning-material-selector-empty">正在加载材料...</div>';
    if (dom.current) {
        dom.current.textContent = state.keyword ? '正在搜索材料...' : '正在读取材料目录...';
    }
}

function renderList() {
    const dom = refs();
    if (!dom.list) return;

    const items = getVisibleItems();
    if (!items.length) {
        const emptyText = state.keyword
            ? '当前搜索条件下没有匹配的 Markdown 文档或文件夹。'
            : '当前目录下没有可选的 Markdown 文档，可以继续进入子文件夹查找。';
        dom.list.innerHTML = `<div class="learning-material-selector-empty">${escapeHtml(emptyText)}</div>`;
        renderCurrentHint();
        return;
    }

    dom.list.innerHTML = items.map((item) => {
        const isSelected = Number(state.selectedMaterial?.id || 0) === Number(item.id);
        const iconMeta = item.node_type === 'folder' ? buildFolderIcon() : getFileIcon(item.name || 'md');
        const typeLabel = item.node_type === 'folder' ? '文件夹' : 'Markdown';
        const hintText = item.node_type === 'folder' ? '进入子目录继续查找' : '可绑定为课堂学习文档';
        const action = item.node_type === 'folder'
            ? '<button type="button" class="btn btn-ghost btn-sm" data-action="open">进入</button>'
            : '<button type="button" class="btn btn-primary btn-sm" data-action="select">选择</button>';

        return `
            <div class="learning-material-selector-row${isSelected ? ' is-selected' : ''}" data-id="${item.id}" data-node-type="${item.node_type}">
                <div class="learning-material-selector-row-main">
                    <div class="learning-material-selector-icon" style="background:${iconMeta.color}16;color:${iconMeta.color};">
                        ${escapeHtml(iconMeta.label)}
                    </div>
                    <div class="learning-material-selector-name">
                        <strong>${escapeHtml(item.name || '')}</strong>
                        <div class="learning-material-selector-meta-line">
                            <span class="learning-material-selector-type">${escapeHtml(typeLabel)}</span>
                            <span class="learning-material-selector-hint">${escapeHtml(hintText)}</span>
                        </div>
                        <span class="learning-material-selector-path" title="${escapeHtml(item.material_path || '')}">
                            ${escapeHtml(item.material_path || '')}
                        </span>
                    </div>
                </div>
                <div class="learning-material-selector-actions">
                    ${action}
                </div>
            </div>
        `;
    }).join('');

    renderCurrentHint();
}

function setSelectedMaterial(material) {
    state.selectedMaterial = normalizeMaterial(material);
    renderSelectedMaterial();
    renderList();
}

async function loadLibrary(parentId = null, { trackHistory = false, force = false } = {}) {
    const keyword = String(state.keyword || '').trim();
    const cacheKey = buildCacheKey(parentId, keyword);

    if (!force && state.cache.has(cacheKey)) {
        const cached = state.cache.get(cacheKey);
        const previousParentId = state.currentParentId;
        if (trackHistory && previousParentId !== parentId) {
            state.history.push(previousParentId);
        }
        state.currentParentId = parentId;
        state.breadcrumbs = cached.breadcrumbs || [];
        state.items = cached.items || [];
        renderBreadcrumbs();
        renderList();
        return cached;
    }

    renderLoadingState();

    const params = new URLSearchParams();
    if (parentId != null) params.set('parent_id', String(parentId));
    if (keyword) params.set('keyword', keyword);
    const query = params.toString();
    const data = await apiFetch(`/api/materials/library${query ? `?${query}` : ''}`, { silent: true });

    if (trackHistory && state.currentParentId !== parentId) {
        state.history.push(state.currentParentId);
    }
    state.currentParentId = parentId;
    state.breadcrumbs = data.breadcrumbs || [];
    state.items = data.items || [];
    state.cache.set(cacheKey, data);
    renderBreadcrumbs();
    renderList();
    return data;
}

function resetState(options = {}) {
    const dom = refs();

    state.currentParentId = null;
    state.history = [];
    state.breadcrumbs = [];
    state.items = [];
    state.keyword = '';
    state.options = options;

    if (dom.search) {
        dom.search.value = '';
    }

    setSelectedMaterial(options.initialMaterial || null);

    if (dom.title) {
        dom.title.textContent = options.title || '选择 Markdown 材料';
    }
    if (dom.subtitle) {
        dom.subtitle.textContent = options.subtitle || '按目录进入材料库，选择任意层级下的 Markdown 文档。';
    }
    if (dom.confirmBtn) {
        dom.confirmBtn.textContent = options.confirmLabel || '使用该文档';
    }
    if (dom.footerNote) {
        dom.footerNote.textContent = options.footerNote || '仅支持绑定 Markdown 文档。单击文件选中，双击文件夹继续进入。';
    }
}

function settle(result) {
    const resolve = state.resolve;
    state.resolve = null;
    closeModal(SELECTOR_MODAL_ID);
    if (typeof resolve === 'function') {
        resolve(result);
    }
}

function handleCancel() {
    settle(undefined);
}

function handleClear() {
    if (!state.options.allowClear) return;
    settle({ clear: true });
}

function handleConfirm() {
    if (!state.selectedMaterial) {
        showToast('请先选择一个 Markdown 文档。', 'warning');
        return;
    }
    settle(state.selectedMaterial);
}

async function openFolder(folderId, { trackHistory = true } = {}) {
    try {
        await loadLibrary(folderId, { trackHistory });
    } catch (error) {
        showToast(error.message || '打开目录失败', 'error');
    }
}

function bindEvents() {
    const dom = refs();
    if (!dom.modal || state.initialized) return;

    state.initialized = true;

    dom.closeBtn?.addEventListener('click', handleCancel);
    dom.cancelBtn?.addEventListener('click', handleCancel);
    dom.clearBtn?.addEventListener('click', handleClear);
    dom.confirmBtn?.addEventListener('click', handleConfirm);

    dom.modal.addEventListener('click', (event) => {
        if (event.target === dom.modal) {
            handleCancel();
        }
    });

    dom.backBtn?.addEventListener('click', async () => {
        const previousParentId = state.history.pop();
        try {
            await loadLibrary(previousParentId ?? null);
        } catch (error) {
            showToast(error.message || '返回失败', 'error');
        }
    });

    dom.upBtn?.addEventListener('click', async () => {
        const parentCrumb = state.breadcrumbs.length >= 2 ? state.breadcrumbs[state.breadcrumbs.length - 2] : null;
        await openFolder(parentCrumb ? Number(parentCrumb.id) : null);
    });

    dom.refreshBtn?.addEventListener('click', async () => {
        state.cache.delete(buildCacheKey(state.currentParentId, state.keyword));
        try {
            await loadLibrary(state.currentParentId, { force: true });
        } catch (error) {
            showToast(error.message || '刷新材料失败', 'error');
        }
    });

    dom.search?.addEventListener('input', () => {
        window.clearTimeout(state.searchTimer);
        state.searchTimer = window.setTimeout(async () => {
            state.keyword = String(dom.search?.value || '').trim();
            try {
                await loadLibrary(state.currentParentId);
            } catch (error) {
                showToast(error.message || '搜索材料失败', 'error');
            }
        }, SEARCH_DEBOUNCE_MS);
    });

    dom.breadcrumbs?.addEventListener('click', async (event) => {
        const rootButton = event.target.closest('[data-crumb-root]');
        const crumbButton = event.target.closest('[data-crumb-id]');
        if (!rootButton && !crumbButton) return;

        try {
            await loadLibrary(rootButton ? null : Number(crumbButton.dataset.crumbId), { trackHistory: true });
        } catch (error) {
            showToast(error.message || '打开目录失败', 'error');
        }
    });

    dom.list?.addEventListener('click', async (event) => {
        const row = event.target.closest('[data-id]');
        if (!row) return;

        const materialId = Number(row.dataset.id || 0);
        const item = getVisibleItems().find((entry) => Number(entry.id) === materialId);
        if (!item) return;

        const action = event.target.closest('[data-action]')?.dataset.action;
        if (!action) {
            if (item.node_type === 'folder') {
                await openFolder(materialId);
                return;
            }
            setSelectedMaterial(item);
            return;
        }

        if (action === 'open') {
            await openFolder(materialId);
            return;
        }

        if (action === 'select') {
            setSelectedMaterial(item);
        }
    });

    dom.list?.addEventListener('dblclick', async (event) => {
        const row = event.target.closest('[data-id]');
        if (!row) return;

        const materialId = Number(row.dataset.id || 0);
        const item = getVisibleItems().find((entry) => Number(entry.id) === materialId);
        if (!item) return;

        if (item.node_type === 'folder') {
            await openFolder(materialId);
            return;
        }

        setSelectedMaterial(item);
    });
}

export function initLearningMaterialSelector() {
    bindEvents();
    return {
        async open(options = {}) {
            bindEvents();
            const dom = refs();
            if (!dom.modal) {
                throw new Error('材料选择器未加载');
            }

            if (typeof state.resolve === 'function') {
                state.resolve(undefined);
            }

            resetState(options);
            openModal(SELECTOR_MODAL_ID);

            try {
                await loadLibrary(null);
            } catch (error) {
                showToast(error.message || '加载材料失败', 'error');
            }

            return await new Promise((resolve) => {
                state.resolve = resolve;
            });
        },
        clearCache() {
            state.cache.clear();
        },
    };
}
