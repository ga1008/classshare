import { apiFetch } from './api.js';
import { escapeHtml, formatSize, getFileIcon, showToast } from './ui.js';
import { getLearningDocumentUrl, getMaterialPrimaryAction, getMaterialTypeLabel, hasLearningDocument } from './materials_common.js';

let config = null;

const state = {
    currentParentId: null,
    breadcrumbs: [],
    history: [],
    items: [],
    selectedIds: new Set(),
};

function refs() {
    return {
        list: document.getElementById('classroom-materials-list'),
        breadcrumbs: document.getElementById('classroom-materials-breadcrumbs'),
        backBtn: document.getElementById('classroom-materials-back-btn'),
        upBtn: document.getElementById('classroom-materials-up-btn'),
        refreshBtn: document.getElementById('classroom-materials-refresh-btn'),
        selectionBar: document.getElementById('classroom-materials-selection'),
        selectionCount: document.getElementById('classroom-materials-selection-count'),
        selectionDownloadBtn: document.getElementById('classroom-materials-download-btn'),
    };
}

function updateSelectionBar() {
    const el = refs().selectionBar;
    if (!el) return;
    const count = state.selectedIds.size;
    el.hidden = count === 0;
    refs().selectionCount.textContent = String(count);
}

function renderBreadcrumbs() {
    const container = refs().breadcrumbs;
    if (!container) return;
    if (!state.breadcrumbs.length) {
        container.innerHTML = '<span class="text-muted">已分配材料</span>';
        return;
    }
    container.innerHTML = state.breadcrumbs.map((crumb, index) => `
        ${index > 0 ? '<span class="separator">/</span>' : ''}
        <button type="button" data-crumb-id="${crumb.id}">${escapeHtml(crumb.name)}</button>
    `).join('');
}

function renderList() {
    const container = refs().list;
    if (!container) return;
    if (!state.items.length) {
        container.innerHTML = '<div class="materials-empty">当前课堂还没有分配课程材料。</div>';
        updateSelectionBar();
        return;
    }

    container.innerHTML = state.items.map((item) => {
        const icon = item.node_type === 'folder' ? { color: '#0ea5e9', label: 'DIR' } : getFileIcon(item.name || 'file');
        const primaryAction = getMaterialPrimaryAction(item);
        const documentAction = hasLearningDocument(item)
            ? '<button type="button" class="btn btn-outline btn-sm" data-action="view-doc">查看</button>'
            : '';
        return `
            <div class="materials-row" data-id="${item.id}">
                <div>
                    <input type="checkbox" data-role="select-item" data-id="${item.id}" ${state.selectedIds.has(item.id) ? 'checked' : ''}>
                </div>
                <div class="materials-name-cell">
                    <div class="materials-type-icon" style="background:${icon.color}16;color:${icon.color};">${icon.label}</div>
                    <div class="materials-name-copy">
                        <strong>${escapeHtml(item.name)}</strong>
                        <span>${escapeHtml(item.material_path || '')}</span>
                    </div>
                </div>
                <div>${escapeHtml(getMaterialTypeLabel(item))}</div>
                <div>${escapeHtml(item.node_type === 'folder' ? `${item.child_count || 0} 个子项` : formatSize(item.file_size || 0))}</div>
                <div class="materials-row-actions">
                    <button type="button" class="btn btn-ghost btn-sm" data-action="${primaryAction.action}">
                        ${primaryAction.label}
                    </button>
                    ${documentAction}
                    ${item.node_type === 'file' ? '<button type="button" class="btn btn-ghost btn-sm" data-action="download">下载</button>' : ''}
                </div>
            </div>
        `;
    }).join('');

    updateSelectionBar();
}

async function loadMaterials(parentId = null, trackHistory = false) {
    const query = parentId ? `?parent_id=${parentId}` : '';
    const data = await apiFetch(`/api/classrooms/${config.classOfferingId}/materials${query}`, { silent: true });
    if (trackHistory) {
        state.history.push(state.currentParentId);
    }
    state.currentParentId = parentId;
    state.breadcrumbs = data.breadcrumbs || [];
    state.items = data.items || [];
    state.selectedIds.clear();
    renderBreadcrumbs();
    renderList();
}

async function downloadSelected(ids) {
    if (!ids.length) return;
    const response = await fetch('/api/materials/download', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ material_ids: ids }),
        credentials: 'same-origin',
    });

    if (!response.ok) {
        let message = '下载失败';
        try {
            const errorData = await response.json();
            if (window.handleAuthFailureResponse) {
                await window.handleAuthFailureResponse(response, errorData);
            }
            message = errorData.detail || errorData.message || message;
        } catch {
            // ignore
        }
        throw new Error(message);
    }

    const blob = await response.blob();
    const disposition = response.headers.get('content-disposition') || '';
    const match = disposition.match(/filename="?([^"]+)"?/i);
    const fileName = match ? decodeURIComponent(match[1]) : 'course-materials.zip';
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = fileName;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
}

export function init(appConfig) {
    config = appConfig;
    const dom = refs();
    if (!dom.list) return;

    dom.refreshBtn?.addEventListener('click', () => loadMaterials(state.currentParentId));
    dom.backBtn?.addEventListener('click', () => {
        const previousParentId = state.history.pop();
        loadMaterials(previousParentId ?? null, false);
    });
    dom.upBtn?.addEventListener('click', () => {
        const parentCrumb = state.breadcrumbs.length >= 2 ? state.breadcrumbs[state.breadcrumbs.length - 2] : null;
        loadMaterials(parentCrumb ? Number(parentCrumb.id) : null, true);
    });
    dom.selectionDownloadBtn?.addEventListener('click', async () => {
        try {
            await downloadSelected(Array.from(state.selectedIds));
        } catch (error) {
            showToast(error.message || '下载失败', 'error');
        }
    });
    dom.breadcrumbs?.addEventListener('click', (event) => {
        const button = event.target.closest('[data-crumb-id]');
        if (!button) return;
        loadMaterials(Number(button.dataset.crumbId), true);
    });
    dom.list.addEventListener('click', (event) => {
        const row = event.target.closest('.materials-row');
        if (!row) return;
        const materialId = Number(row.dataset.id);
        const item = state.items.find((entry) => entry.id === materialId);
        if (!item) return;

        if (event.target.matches('[data-role="select-item"]')) {
            if (event.target.checked) state.selectedIds.add(materialId);
            else state.selectedIds.delete(materialId);
            updateSelectionBar();
            return;
        }

        const action = event.target.dataset.action;
        if (action === 'open') {
            loadMaterials(materialId, true);
        } else if (action === 'preview') {
            window.open(`/materials/view/${materialId}`, '_blank', 'noopener');
        } else if (action === 'view-doc') {
            const viewerUrl = getLearningDocumentUrl(item);
            if (!viewerUrl) {
                showToast('当前目录没有可查看的 README.md', 'warning');
                return;
            }
            window.open(viewerUrl, '_blank', 'noopener');
        } else if (action === 'download') {
            window.location.href = `/materials/download/${materialId}`;
        }
    });
    dom.list.addEventListener('dblclick', (event) => {
        const row = event.target.closest('.materials-row');
        if (!row) return;
        const materialId = Number(row.dataset.id);
        const item = state.items.find((entry) => entry.id === materialId);
        if (!item) return;
        if (item.node_type === 'folder') loadMaterials(materialId, true);
        else if (item.preview_supported) window.open(`/materials/view/${materialId}`, '_blank', 'noopener');
    });

    loadMaterials().catch((error) => {
        console.error(error);
        dom.list.innerHTML = `<div class="materials-empty">加载材料失败：${escapeHtml(error.message || '未知错误')}</div>`;
    });
}

export async function refresh() {
    if (!config) return;
    await loadMaterials(state.currentParentId);
}
