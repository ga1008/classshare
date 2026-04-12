import { apiFetch } from './api.js';
import { escapeHtml, formatSize, getFileIcon, showToast } from './ui.js';
import {
    getLearningDocumentUrl,
    getMaterialPrimaryAction,
    getMaterialTypeLabel,
    getRepositoryVisualMeta,
    hasLearningDocument,
    isGitRepository,
} from './materials_common.js';

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

function getMetaText(item) {
    if (!item) return '--';
    if (item.node_type === 'folder') {
        return `${item.child_count || 0} 个子项`;
    }
    return formatSize(item.file_size || 0);
}

function getVisualMeta(item) {
    const repositoryMeta = getRepositoryVisualMeta(item);
    if (repositoryMeta) {
        return {
            color: repositoryMeta.color,
            label: repositoryMeta.icon,
            badge: repositoryMeta.badge,
        };
    }
    if (item.node_type === 'folder') {
        return { color: '#0ea5e9', label: 'DIR', badge: '' };
    }
    const fileMeta = getFileIcon(item.name || 'file');
    return { color: fileMeta.color, label: fileMeta.label, badge: '' };
}

function getDownloadAction(item) {
    if (item.node_type !== 'file') {
        return '';
    }

    if (item.download_allowed !== false) {
        return '<button type="button" class="btn btn-ghost btn-sm" data-action="download">下载</button>';
    }

    const title = escapeHtml(item.download_blocked_reason || '已限制下载');
    return `
        <button
            type="button"
            class="btn btn-ghost btn-sm resource-download-blocked"
            data-action="download-blocked"
            title="${title}"
            aria-label="${title}"
        >
            <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <circle cx="12" cy="12" r="9"></circle>
                <path d="M5 5l14 14"></path>
            </svg>
        </button>
    `;
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
        const visualMeta = getVisualMeta(item);
        const primaryAction = getMaterialPrimaryAction(item);
        const documentAction = hasLearningDocument(item)
            ? '<button type="button" class="btn btn-outline btn-sm" data-action="view-doc">文档</button>'
            : '';
        const repositoryBadge = isGitRepository(item)
            ? `<span class="materials-repo-badge" style="--repo-color:${visualMeta.color};">${escapeHtml(visualMeta.badge)}</span>`
            : '';

        return `
            <div class="materials-row" data-id="${item.id}">
                <div>
                    <input type="checkbox" data-role="select-item" data-id="${item.id}" ${state.selectedIds.has(item.id) ? 'checked' : ''}>
                </div>
                <div class="materials-name-cell">
                    <div class="materials-type-icon" style="background:${visualMeta.color}16;color:${visualMeta.color};">${escapeHtml(visualMeta.label)}</div>
                    <div class="materials-name-copy">
                        <strong>${escapeHtml(item.name)}</strong>
                        <div class="materials-name-badges">${repositoryBadge}</div>
                        <span>${escapeHtml(item.material_path || '')}</span>
                    </div>
                </div>
                <div>${escapeHtml(getMaterialTypeLabel(item))}</div>
                <div>${escapeHtml(getMetaText(item))}</div>
                <div class="materials-row-actions">
                    <button type="button" class="btn btn-ghost btn-sm" data-action="${primaryAction.action}">
                        ${primaryAction.label}
                    </button>
                    ${documentAction}
                    ${getDownloadAction(item)}
                </div>
            </div>
        `;
    }).join('');

    updateSelectionBar();
}

async function loadMaterials(parentId = null, trackHistory = false) {
    const query = parentId ? `?parent_id=${parentId}` : '';
    const data = await apiFetch(`/api/classrooms/${config.classOfferingId}/materials${query}`, { silent: true });
    if (trackHistory && state.currentParentId !== parentId) {
        state.history.push(state.currentParentId);
    }
    state.currentParentId = parentId;
    state.breadcrumbs = data.breadcrumbs || [];
    state.items = data.items || [];
    state.selectedIds.clear();
    renderBreadcrumbs();
    renderList();
}

function getBlockedSelectedItems(ids) {
    return ids
        .map((id) => state.items.find((item) => Number(item.id) === Number(id)))
        .filter((item) => item && item.node_type === 'file' && item.download_allowed === false);
}

async function downloadSelected(ids) {
    if (!ids.length) return;
    const blockedItems = getBlockedSelectedItems(ids);
    if (blockedItems.length) {
        throw new Error(blockedItems[0].download_blocked_reason || '所选材料中包含已限制下载的文件');
    }
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

    dom.refreshBtn?.addEventListener('click', () => {
        loadMaterials(state.currentParentId).catch((error) => {
            showToast(error.message || '刷新材料失败', 'error');
        });
    });

    dom.backBtn?.addEventListener('click', () => {
        const previousParentId = state.history.pop();
        loadMaterials(previousParentId ?? null, false).catch((error) => {
            showToast(error.message || '返回失败', 'error');
        });
    });

    dom.upBtn?.addEventListener('click', () => {
        const parentCrumb = state.breadcrumbs.length >= 2 ? state.breadcrumbs[state.breadcrumbs.length - 2] : null;
        loadMaterials(parentCrumb ? Number(parentCrumb.id) : null, true).catch((error) => {
            showToast(error.message || '返回上一级失败', 'error');
        });
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
        loadMaterials(Number(button.dataset.crumbId), true).catch((error) => {
            showToast(error.message || '打开目录失败', 'error');
        });
    });

    dom.list.addEventListener('click', (event) => {
        const row = event.target.closest('.materials-row');
        if (!row) return;
        const materialId = Number(row.dataset.id);
        const item = state.items.find((entry) => Number(entry.id) === materialId);
        if (!item) return;

        const checkbox = event.target.closest('[data-role="select-item"]');
        if (checkbox) {
            if (checkbox.checked) state.selectedIds.add(materialId);
            else state.selectedIds.delete(materialId);
            updateSelectionBar();
            return;
        }

        const action = event.target.closest('[data-action]')?.dataset.action;
        if (action === 'open') {
            loadMaterials(materialId, true).catch((error) => {
                showToast(error.message || '打开目录失败', 'error');
            });
        } else if (action === 'preview') {
            window.open(`/materials/view/${materialId}`, '_blank', 'noopener');
        } else if (action === 'view-doc') {
            const viewerUrl = getLearningDocumentUrl(item);
            if (!viewerUrl) {
                showToast('当前目录没有可查看的 README.md', 'warning');
                return;
            }
            window.open(viewerUrl, '_blank', 'noopener');
        } else if (action === 'download-blocked') {
            showToast(item.download_blocked_reason || '当前材料已限制下载', 'warning');
        } else if (action === 'download') {
            window.location.href = `/materials/download/${materialId}`;
        }
    });

    dom.list.addEventListener('dblclick', (event) => {
        const row = event.target.closest('.materials-row');
        if (!row) return;
        const materialId = Number(row.dataset.id);
        const item = state.items.find((entry) => Number(entry.id) === materialId);
        if (!item) return;
        if (item.node_type === 'folder') {
            loadMaterials(materialId, true).catch((error) => {
                showToast(error.message || '打开目录失败', 'error');
            });
        } else if (item.preview_supported) {
            window.open(`/materials/view/${materialId}`, '_blank', 'noopener');
        }
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
