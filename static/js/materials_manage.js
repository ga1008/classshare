import { apiFetch } from './api.js';
import { closeModal, formatDate, formatSize, getFileIcon, openModal, showToast, escapeHtml } from './ui.js';

const state = {
    currentParentId: null,
    history: [],
    items: [],
    activeMaterialId: null,
    selectedIds: new Set(),
    activeDetail: null,
    currentFolder: null,
    currentBreadcrumbs: [],
};

const config = window.MATERIALS_MANAGE_CONFIG || { offerings: [], canAssign: false };

const refs = {
    listBody: document.getElementById('materials-list-body'),
    breadcrumbs: document.getElementById('materials-breadcrumbs'),
    detail: document.getElementById('materials-detail'),
    backBtn: document.getElementById('materials-back-btn'),
    upBtn: document.getElementById('materials-up-btn'),
    refreshBtn: document.getElementById('materials-refresh-btn'),
    fileBtn: document.getElementById('materials-upload-file-btn'),
    folderBtn: document.getElementById('materials-upload-folder-btn'),
    fileInput: document.getElementById('materials-file-input'),
    folderInput: document.getElementById('materials-folder-input'),
    selectAll: document.getElementById('materials-select-all'),
    selectionBar: document.getElementById('materials-selection-bar'),
    selectionCount: document.getElementById('materials-selection-count'),
    selectionDownloadBtn: document.getElementById('materials-selection-download-btn'),
    selectionClearBtn: document.getElementById('materials-selection-clear-btn'),
    assignName: document.getElementById('materials-assign-name'),
    assignOptions: document.getElementById('materials-assign-options'),
    assignSaveBtn: document.getElementById('materials-assign-save-btn'),
    rootCount: document.getElementById('materials-root-count'),
};

function getTypeLabel(item) {
    if (item.node_type === 'folder') return '文件夹';
    if (item.preview_type === 'markdown') return 'Markdown';
    if (item.preview_type === 'image') return '图片';
    if (item.file_ext) return item.file_ext.toUpperCase();
    return '文件';
}

function getMetaText(item) {
    if (item.node_type === 'folder') {
        return `${item.child_count || 0} 个子项`;
    }
    return formatSize(item.file_size || 0);
}

function updateSelectionBar() {
    const count = state.selectedIds.size;
    refs.selectionBar.hidden = count === 0;
    refs.selectionCount.textContent = String(count);
    refs.selectAll.checked = state.items.length > 0 && state.items.every((item) => state.selectedIds.has(item.id));
}

function renderBreadcrumbs(breadcrumbs) {
    if (!breadcrumbs || breadcrumbs.length === 0) {
        refs.breadcrumbs.innerHTML = '<span class="text-muted">材料库根目录</span>';
        return;
    }

    refs.breadcrumbs.innerHTML = breadcrumbs.map((crumb, index) => `
        ${index > 0 ? '<span class="separator">/</span>' : ''}
        <button type="button" data-crumb-id="${crumb.id}">${escapeHtml(crumb.name)}</button>
    `).join('');
}

function renderList() {
    if (!state.items.length) {
        refs.listBody.innerHTML = '<div class="materials-empty">当前目录暂无材料。</div>';
        updateSelectionBar();
        return;
    }

    refs.listBody.innerHTML = state.items.map((item) => {
        const icon = item.node_type === 'folder' ? { color: '#0ea5e9', label: 'DIR' } : getFileIcon(item.name || 'file');
        const activeClass = item.id === state.activeMaterialId ? 'is-active' : '';
        const selectedClass = state.selectedIds.has(item.id) ? 'is-selected' : '';
        const primaryAction = item.node_type === 'folder'
            ? '<button type="button" class="btn btn-ghost btn-sm" data-action="open">打开</button>'
            : `<button type="button" class="btn btn-ghost btn-sm" data-action="${item.preview_supported ? 'preview' : 'download'}">${item.preview_supported ? '预览' : '下载'}</button>`;

        return `
            <div class="materials-row ${activeClass} ${selectedClass}" data-id="${item.id}">
                <div>
                    <input type="checkbox" data-role="select-item" data-id="${item.id}" ${state.selectedIds.has(item.id) ? 'checked' : ''}>
                </div>
                <div class="materials-name-cell">
                    <div class="materials-type-icon" style="background:${icon.color}16;color:${icon.color};">${icon.label}</div>
                    <div class="materials-name-copy">
                        <strong title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</strong>
                        <span title="${escapeHtml(item.material_path || '')}">${escapeHtml(item.material_path || '')}</span>
                    </div>
                </div>
                <div>${escapeHtml(getTypeLabel(item))}</div>
                <div>${escapeHtml(formatDate(item.updated_at || item.created_at || ''))}</div>
                <div class="materials-row-actions">
                    ${primaryAction}
                    ${item.node_type === 'file' ? '<button type="button" class="btn btn-ghost btn-sm" data-action="download">下载</button>' : ''}
                    <button type="button" class="btn btn-ghost btn-sm" data-action="details">详情</button>
                </div>
            </div>
        `;
    }).join('');

    updateSelectionBar();
}

function renderOutline(outline = []) {
    if (!Array.isArray(outline) || outline.length === 0) {
        return '<div class="materials-viewer-empty">暂无解析目录。</div>';
    }
    return `
        <div class="materials-outline-list">
            ${outline.map((item) => `
                <div class="materials-outline-item materials-outline-level-${Math.min(Number(item.level) || 1, 4)}">
                    ${escapeHtml(item.title || '')}
                </div>
            `).join('')}
        </div>
    `;
}

function renderAssignments(assignments = []) {
    if (!assignments.length) {
        return '<div class="materials-viewer-empty">尚未分配到课堂。</div>';
    }
    return `
        <div class="materials-assignment-list">
            ${assignments.map((assignment) => `
                <div class="materials-assignment-item">
                    <strong>${escapeHtml(assignment.course_name)} / ${escapeHtml(assignment.class_name)}</strong>
                    <div class="text-muted text-sm">${escapeHtml(assignment.semester || '未填写学期')}</div>
                </div>
            `).join('')}
        </div>
    `;
}

function renderDetail(detail) {
    if (!detail) {
        refs.detail.innerHTML = '<div class="materials-empty">选择一个材料后，这里会显示详情、AI 摘要和课堂分配状态。</div>';
        return;
    }

    const previewUrl = detail.node_type === 'file' && detail.preview_supported ? `/materials/view/${detail.id}` : '';
    const optimizedUrl = detail.has_optimized_version ? `/materials/view/${detail.id}?variant=optimized` : '';
    const aiSummary = detail.ai_parse_result?.summary || '尚未执行 AI 解析。';

    refs.detail.innerHTML = `
        <div>
            <div class="text-muted text-sm">${escapeHtml(getTypeLabel(detail))}</div>
            <h3 title="${escapeHtml(detail.name)}">${escapeHtml(detail.name)}</h3>
            <div class="text-muted text-sm">${escapeHtml(detail.material_path || '')}</div>
        </div>
        <div class="materials-detail-meta">
            <div class="meta-chip">
                <strong>大小 / 子项</strong>
                <span>${escapeHtml(getMetaText(detail))}</span>
            </div>
            <div class="meta-chip">
                <strong>更新时间</strong>
                <span>${escapeHtml(formatDate(detail.updated_at || detail.created_at || ''))}</span>
            </div>
            <div class="meta-chip">
                <strong>AI 解析状态</strong>
                <span>${escapeHtml(detail.ai_parse_status || 'idle')}</span>
            </div>
            <div class="meta-chip">
                <strong>AI 优化状态</strong>
                <span>${escapeHtml(detail.ai_optimize_status || 'idle')}</span>
            </div>
        </div>
        <div class="materials-detail-actions">
            ${previewUrl ? `<a href="${previewUrl}" class="btn btn-primary" target="_blank" rel="noopener">全屏预览</a>` : ''}
            ${optimizedUrl ? `<a href="${optimizedUrl}" class="btn btn-outline" target="_blank" rel="noopener">查看优化稿</a>` : ''}
            ${detail.node_type === 'file' ? `<a href="/materials/download/${detail.id}" class="btn btn-outline">下载</a>` : ''}
            <button type="button" class="btn btn-outline" id="materials-assign-open-btn">分配课堂</button>
            <button type="button" class="btn btn-outline" id="materials-ai-parse-btn" ${detail.can_ai_parse ? '' : 'disabled'}>AI 解析</button>
            <button type="button" class="btn btn-outline" id="materials-ai-optimize-btn" ${detail.can_ai_optimize ? '' : 'disabled'}>AI 优化</button>
            <button type="button" class="btn btn-danger" id="materials-delete-btn">删除</button>
        </div>
        <div class="materials-section">
            <div class="materials-section-header">
                <h3>AI 摘要</h3>
            </div>
            <div class="text-muted text-sm">${escapeHtml(aiSummary)}</div>
            ${detail.ai_parse_result?.keywords?.length ? `<div class="text-muted text-sm mt-2">关键词：${escapeHtml(detail.ai_parse_result.keywords.join('、'))}</div>` : ''}
        </div>
        <div class="materials-section">
            <div class="materials-section-header">
                <h3>解析目录</h3>
            </div>
            ${renderOutline(detail.ai_parse_result?.outline)}
        </div>
        <div class="materials-section">
            <div class="materials-section-header">
                <h3>已分配课堂</h3>
            </div>
            ${renderAssignments(detail.assignments || [])}
        </div>
    `;

    document.getElementById('materials-assign-open-btn')?.addEventListener('click', openAssignModal);
    document.getElementById('materials-ai-parse-btn')?.addEventListener('click', runAiParse);
    document.getElementById('materials-ai-optimize-btn')?.addEventListener('click', runAiOptimize);
    document.getElementById('materials-delete-btn')?.addEventListener('click', deleteActiveMaterial);
}

async function loadMaterialDetail(materialId) {
    state.activeDetail = await apiFetch(`/api/materials/${materialId}`, { silent: true }).then((data) => data.material);
    renderDetail(state.activeDetail);
}

async function loadLibrary(parentId = null, trackHistory = false) {
    const query = parentId ? `?parent_id=${parentId}` : '';
    const data = await apiFetch(`/api/materials/library${query}`, { silent: true });

    if (trackHistory) {
        state.history.push(state.currentParentId);
    }

    state.currentParentId = parentId;
    state.items = data.items || [];
    state.selectedIds.clear();
    state.activeMaterialId = null;
    state.activeDetail = null;
    state.currentFolder = data.current_folder || null;
    state.currentBreadcrumbs = data.breadcrumbs || [];

    if (refs.rootCount && parentId === null) {
        refs.rootCount.textContent = String(state.items.length);
    }

    renderBreadcrumbs(state.currentBreadcrumbs);
    renderList();
    renderDetail(null);
}

function getCurrentItem(materialId) {
    return state.items.find((item) => item.id === materialId) || state.activeDetail;
}

function openFolder(materialId, trackHistory = true) {
    loadLibrary(materialId, trackHistory).catch((error) => showToast(error.message || '打开文件夹失败', 'error'));
}

function previewMaterial(materialId) {
    window.open(`/materials/view/${materialId}`, '_blank', 'noopener');
}

async function downloadByIds(materialIds) {
    if (!materialIds.length) return;

    const singleItem = materialIds.length === 1 ? getCurrentItem(materialIds[0]) : null;
    if (singleItem && singleItem.node_type === 'file') {
        window.location.href = `/materials/download/${singleItem.id}`;
        return;
    }

    const response = await fetch('/api/materials/download', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ material_ids: materialIds }),
        credentials: 'same-origin',
    });

    if (!response.ok) {
        let message = '下载失败';
        try {
            const errorData = await response.json();
            message = errorData.detail || errorData.message || message;
        } catch {
            // no-op
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

async function uploadFiles(fileList) {
    if (!fileList || !fileList.length) return;

    const formData = new FormData();
    const manifest = [];
    Array.from(fileList).forEach((file) => {
        formData.append('files', file, file.name);
        manifest.push({
            relative_path: file.webkitRelativePath || file.name,
            content_type: file.type || '',
        });
    });
    formData.append('manifest', JSON.stringify(manifest));
    if (state.currentParentId) {
        formData.append('parent_id', String(state.currentParentId));
    }

    const result = await apiFetch('/api/materials/upload', {
        method: 'POST',
        body: formData,
    });
    showToast(result.message || '材料上传成功', 'success');
    await loadLibrary(state.currentParentId);
}

function toggleSelection(materialId, checked) {
    if (checked) state.selectedIds.add(materialId);
    else state.selectedIds.delete(materialId);
    renderList();
}

function getSelectedMaterialIds() {
    return Array.from(state.selectedIds);
}

async function openAssignModal() {
    if (!state.activeDetail) return;
    refs.assignName.textContent = state.activeDetail.name;

    refs.assignOptions?.querySelectorAll('input[type="checkbox"]').forEach((checkbox) => {
        checkbox.checked = (state.activeDetail.assignments || []).some((item) => Number(item.class_offering_id) === Number(checkbox.value));
    });
    openModal('materials-assign-modal');
}

async function saveAssignments() {
    if (!state.activeDetail) return;
    const ids = Array.from(refs.assignOptions.querySelectorAll('input[type="checkbox"]:checked')).map((checkbox) => Number(checkbox.value));
    const result = await apiFetch(`/api/materials/${state.activeDetail.id}/assign`, {
        method: 'POST',
        body: { class_offering_ids: ids },
    });
    showToast(result.message || '课堂分配已更新', 'success');
    closeModal('materials-assign-modal');
    await loadMaterialDetail(state.activeDetail.id);
}

async function runAiParse() {
    if (!state.activeDetail) return;
    const result = await apiFetch(`/api/materials/${state.activeDetail.id}/ai-parse`, { method: 'POST' });
    showToast(result.message || 'AI 解析完成', 'success');
    await loadMaterialDetail(state.activeDetail.id);
}

async function runAiOptimize() {
    if (!state.activeDetail) return;
    const result = await apiFetch(`/api/materials/${state.activeDetail.id}/ai-optimize`, { method: 'POST' });
    showToast(result.message || 'AI 优化完成', 'success');
    await loadMaterialDetail(state.activeDetail.id);
    if (result.viewer_url) {
        window.open(result.viewer_url, '_blank', 'noopener');
    }
}

async function deleteActiveMaterial() {
    if (!state.activeDetail) return;
    if (!window.confirm(`确定删除材料 "${state.activeDetail.name}" 吗？`)) return;
    const result = await apiFetch(`/api/materials/${state.activeDetail.id}`, { method: 'DELETE' });
    showToast(result.message || '材料已删除', 'success');
    await loadLibrary(state.currentParentId);
}

refs.listBody.addEventListener('click', async (event) => {
    const row = event.target.closest('.materials-row');
    if (!row) return;
    const materialId = Number(row.dataset.id);

    if (event.target.matches('[data-role="select-item"]')) {
        toggleSelection(materialId, event.target.checked);
        return;
    }

    const action = event.target.dataset.action;
    if (action === 'open') {
        openFolder(materialId);
        return;
    }
    if (action === 'preview') {
        previewMaterial(materialId);
        return;
    }
    if (action === 'download') {
        window.location.href = `/materials/download/${materialId}`;
        return;
    }

    state.activeMaterialId = materialId;
    renderList();
    try {
        await loadMaterialDetail(materialId);
    } catch (error) {
        showToast(error.message || '加载材料详情失败', 'error');
    }
});

refs.listBody.addEventListener('dblclick', (event) => {
    const row = event.target.closest('.materials-row');
    if (!row) return;
    const materialId = Number(row.dataset.id);
    const item = state.items.find((entry) => entry.id === materialId);
    if (!item) return;
    if (item.node_type === 'folder') openFolder(materialId);
    else if (item.preview_supported) previewMaterial(materialId);
    else window.location.href = `/materials/download/${materialId}`;
});

refs.breadcrumbs.addEventListener('click', (event) => {
    const button = event.target.closest('[data-crumb-id]');
    if (!button) return;
    openFolder(Number(button.dataset.crumbId), true);
});

refs.selectAll.addEventListener('change', (event) => {
    if (event.target.checked) {
        state.items.forEach((item) => state.selectedIds.add(item.id));
    } else {
        state.selectedIds.clear();
    }
    renderList();
});

refs.refreshBtn.addEventListener('click', () => loadLibrary(state.currentParentId));
refs.backBtn.addEventListener('click', () => {
    const previousParentId = state.history.pop();
    loadLibrary(previousParentId ?? null, false);
});
refs.upBtn.addEventListener('click', () => {
    if (!state.currentParentId || !state.currentBreadcrumbs.length) {
        loadLibrary(null, false);
        return;
    }
    const parentCrumb = state.currentBreadcrumbs.length >= 2 ? state.currentBreadcrumbs[state.currentBreadcrumbs.length - 2] : null;
    loadLibrary(parentCrumb ? Number(parentCrumb.id) : null, true);
});
refs.fileBtn.addEventListener('click', () => refs.fileInput.click());
refs.folderBtn.addEventListener('click', () => refs.folderInput.click());
refs.fileInput.addEventListener('change', async (event) => {
    try {
        await uploadFiles(event.target.files);
    } finally {
        event.target.value = '';
    }
});
refs.folderInput.addEventListener('change', async (event) => {
    try {
        await uploadFiles(event.target.files);
    } finally {
        event.target.value = '';
    }
});
refs.selectionDownloadBtn.addEventListener('click', async () => {
    try {
        await downloadByIds(getSelectedMaterialIds());
    } catch (error) {
        showToast(error.message || '下载失败', 'error');
    }
});
refs.selectionClearBtn.addEventListener('click', () => {
    state.selectedIds.clear();
    renderList();
});
refs.assignSaveBtn?.addEventListener('click', async () => {
    try {
        await saveAssignments();
    } catch (error) {
        showToast(error.message || '保存分配失败', 'error');
    }
});

loadLibrary().catch((error) => {
    console.error(error);
    refs.listBody.innerHTML = `<div class="materials-empty">加载材料库失败：${escapeHtml(error.message || '未知错误')}</div>`;
});
