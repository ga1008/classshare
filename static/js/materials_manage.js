import { apiFetch } from './api.js';
import { closeModal, formatDate, formatSize, getFileIcon, openModal, showToast, escapeHtml } from './ui.js';
import {
    getLearningDocumentUrl,
    getMaterialPreviewUrl,
    getMaterialPrimaryAction,
    getMaterialTypeLabel,
    getRepositoryVisualMeta,
    hasLearningDocument,
    isGitRepository,
} from './materials_common.js';

const state = {
    currentParentId: null,
    history: [],
    items: [],
    activeMaterialId: null,
    selectedIds: new Set(),
    activeDetail: null,
    currentFolder: null,
    currentBreadcrumbs: [],
    repository: {
        materialId: null,
        detail: null,
        busy: false,
        pendingAction: null,
        lastStatus: 'idle',
        lastOutput: '暂无输出',
        lastSyncSummary: '等待执行',
    },
};

const config = window.MATERIALS_MANAGE_CONFIG || { offerings: [], canAssign: false };

const refs = {
    listBody: document.getElementById('materials-list-body'),
    breadcrumbs: document.getElementById('materials-breadcrumbs'),
    detail: document.getElementById('materials-detail'),
    backBtn: document.getElementById('materials-back-btn'),
    upBtn: document.getElementById('materials-up-btn'),
    refreshBtn: document.getElementById('materials-refresh-btn'),
    repositoryBtn: document.getElementById('materials-repository-btn'),
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
    repositoryName: document.getElementById('materials-repository-name'),
    repositoryPath: document.getElementById('materials-repository-path'),
    repositoryProvider: document.getElementById('materials-repository-provider'),
    repositoryRemoteName: document.getElementById('materials-repository-remote-name'),
    repositoryBranch: document.getElementById('materials-repository-branch'),
    repositoryProtocol: document.getElementById('materials-repository-protocol'),
    repositoryCredentialState: document.getElementById('materials-repository-credential-state'),
    repositoryCredentialUser: document.getElementById('materials-repository-credential-user'),
    repositoryStatus: document.getElementById('materials-repository-status'),
    repositorySyncSummary: document.getElementById('materials-repository-sync-summary'),
    repositoryCommandPreview: document.getElementById('materials-repository-command-preview'),
    repositoryCommandInput: document.getElementById('materials-repository-command-input'),
    repositoryOutput: document.getElementById('materials-repository-output'),
    repositoryUpdateBtn: document.getElementById('materials-repository-update-btn'),
    repositoryPushBtn: document.getElementById('materials-repository-push-btn'),
    repositoryAuthBtn: document.getElementById('materials-repository-auth-btn'),
    repositoryCommandRunBtn: document.getElementById('materials-repository-command-run-btn'),
    repositoryCredentialRemote: document.getElementById('materials-repository-credential-remote'),
    repositoryCredentialHost: document.getElementById('materials-repository-credential-host'),
    repositoryCredentialUsername: document.getElementById('materials-repository-credential-username'),
    repositoryCredentialSecret: document.getElementById('materials-repository-credential-secret'),
    repositoryCredentialAuthMode: document.getElementById('materials-repository-credential-auth-mode'),
    repositoryCredentialHint: document.getElementById('materials-repository-credential-hint'),
    repositoryCredentialSaveBtn: document.getElementById('materials-repository-credential-save-btn'),
};

function getMetaText(item) {
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

function renderRepositoryToolbar() {
    if (!refs.repositoryBtn) return;
    const canOpenRepository = Boolean(state.currentFolder && isGitRepository(state.currentFolder));
    refs.repositoryBtn.hidden = !canOpenRepository;
}

function renderList() {
    if (!state.items.length) {
        refs.listBody.innerHTML = '<div class="materials-empty">当前目录暂无材料。</div>';
        updateSelectionBar();
        return;
    }

    refs.listBody.innerHTML = state.items.map((item) => {
        const visualMeta = getVisualMeta(item);
        const activeClass = item.id === state.activeMaterialId ? 'is-active' : '';
        const selectedClass = state.selectedIds.has(item.id) ? 'is-selected' : '';
        const primaryAction = getMaterialPrimaryAction(item);
        const documentAction = hasLearningDocument(item)
            ? '<button type="button" class="btn btn-outline btn-sm" data-action="view-doc">查看</button>'
            : '';
        const repositoryAction = isGitRepository(item)
            ? '<button type="button" class="btn btn-outline btn-sm" data-action="repository">仓库</button>'
            : '';
        const repositoryBadge = visualMeta.badge
            ? `<span class="materials-repo-badge" style="--repo-color:${visualMeta.color};">${escapeHtml(visualMeta.badge)}</span>`
            : '';

        return `
            <div class="materials-row ${activeClass} ${selectedClass}" data-id="${item.id}">
                <div>
                    <input type="checkbox" data-role="select-item" data-id="${item.id}" ${state.selectedIds.has(item.id) ? 'checked' : ''}>
                </div>
                <div class="materials-name-cell">
                    <div class="materials-type-icon" style="background:${visualMeta.color}16;color:${visualMeta.color};">${visualMeta.label}</div>
                    <div class="materials-name-copy">
                        <strong title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</strong>
                        <div class="materials-name-badges">${repositoryBadge}</div>
                        <span title="${escapeHtml(item.material_path || '')}">${escapeHtml(item.material_path || '')}</span>
                    </div>
                </div>
                <div>${escapeHtml(getMaterialTypeLabel(item))}</div>
                <div>${escapeHtml(formatDate(item.updated_at || item.created_at || ''))}</div>
                <div class="materials-row-actions">
                    <button type="button" class="btn btn-ghost btn-sm" data-action="${primaryAction.action}">${primaryAction.label}</button>
                    ${documentAction}
                    ${repositoryAction}
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

function renderRepositorySummary(detail) {
    if (!isGitRepository(detail)) return '';
    const repositoryMeta = getRepositoryVisualMeta(detail) || { badge: 'Git', color: '#f97316' };
    const remoteUrl = detail.git_remote_url || '未识别远程地址';
    const branchLabel = detail.git_default_branch || detail.git_head_branch || '未识别分支';
    return `
        <div class="materials-section">
            <div class="materials-section-header">
                <h3>Git 仓库</h3>
                <span class="materials-repo-badge" style="--repo-color:${repositoryMeta.color};">${escapeHtml(repositoryMeta.badge)}</span>
            </div>
            <div class="materials-repo-detail-grid">
                <div class="materials-repo-detail-item">
                    <strong>远程地址</strong>
                    <span title="${escapeHtml(remoteUrl)}">${escapeHtml(remoteUrl)}</span>
                </div>
                <div class="materials-repo-detail-item">
                    <strong>默认分支</strong>
                    <span>${escapeHtml(branchLabel)}</span>
                </div>
                <div class="materials-repo-detail-item">
                    <strong>远程名称</strong>
                    <span>${escapeHtml(detail.git_remote_name || 'origin')}</span>
                </div>
                <div class="materials-repo-detail-item">
                    <strong>协议</strong>
                    <span>${escapeHtml(detail.git_remote_protocol || '未识别')}</span>
                </div>
            </div>
        </div>
    `;
}

function renderDetail(detail) {
    if (!detail) {
        refs.detail.innerHTML = '<div class="materials-empty">选择一个材料后，这里会显示详情、AI 摘要和课堂分配状态。</div>';
        return;
    }

    const previewUrl = getMaterialPreviewUrl(detail);
    const optimizedUrl = detail.has_optimized_version ? `/materials/view/${detail.id}?variant=optimized` : '';
    const aiSummary = detail.ai_parse_result?.summary || '尚未执行 AI 解析。';
    const previewLabel = detail.node_type === 'folder' && detail.document_readme_id
        ? '查看文档'
        : (detail.editable ? '预览 / 编辑' : '全屏预览');

    refs.detail.innerHTML = `
        <div>
            <div class="text-muted text-sm">${escapeHtml(getMaterialTypeLabel(detail))}</div>
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
            ${previewUrl ? `<a href="${previewUrl}" class="btn btn-primary" target="_blank" rel="noopener">${previewLabel}</a>` : ''}
            ${optimizedUrl ? `<a href="${optimizedUrl}" class="btn btn-outline" target="_blank" rel="noopener">查看优化稿</a>` : ''}
            ${detail.node_type === 'file' ? `<a href="/materials/download/${detail.id}" class="btn btn-outline">下载</a>` : ''}
            ${isGitRepository(detail) ? '<button type="button" class="btn btn-outline" id="materials-repository-open-btn">仓库</button>' : ''}
            <button type="button" class="btn btn-outline" id="materials-assign-open-btn">分配课堂</button>
            <button type="button" class="btn btn-outline" id="materials-ai-parse-btn" ${detail.can_ai_parse ? '' : 'disabled'}>AI 解析</button>
            <button type="button" class="btn btn-outline" id="materials-ai-optimize-btn" ${detail.can_ai_optimize ? '' : 'disabled'}>AI 优化</button>
            <button type="button" class="btn btn-danger" id="materials-delete-btn">删除</button>
        </div>
        ${renderRepositorySummary(detail)}
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

    document.getElementById('materials-repository-open-btn')?.addEventListener('click', () => openRepositoryModal(detail.id));
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
    renderRepositoryToolbar();
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

function viewLearningDocument(materialId) {
    const item = getCurrentItem(materialId);
    const viewerUrl = getLearningDocumentUrl(item);
    if (!viewerUrl) {
        showToast('当前目录没有可查看的 README.md', 'warning');
        return;
    }
    window.open(viewerUrl, '_blank', 'noopener');
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
            if (window.handleAuthFailureResponse) {
                await window.handleAuthFailureResponse(response, errorData);
            }
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

function formatRepositoryCommandPreview(detail) {
    if (!detail) return '-';
    const updateCommand = detail.commands?.update || '-';
    const pushCommand = detail.commands?.commit_push || '-';
    return `更新：${updateCommand}\n提交 + 推送：${pushCommand}`;
}

function formatRepositorySyncSummary(syncSummary) {
    if (!syncSummary) return '等待执行';
    return `新增 ${syncSummary.inserted || 0} / 更新 ${syncSummary.updated || 0} / 删除 ${syncSummary.deleted || 0} / 未变化 ${syncSummary.unchanged || 0}`;
}

function setRepositoryBusy(busy, statusText = '') {
    state.repository.busy = busy;
    if (statusText) {
        refs.repositoryStatus.textContent = statusText;
    }
    const detail = state.repository.detail;
    refs.repositoryUpdateBtn.disabled = busy || !detail || !detail.can_update;
    refs.repositoryPushBtn.disabled = busy || !detail || !detail.can_commit_push;
    refs.repositoryCommandRunBtn.disabled = busy || !detail;
    refs.repositoryAuthBtn.disabled = busy || !detail || !detail.credential_supported;
    refs.repositoryCredentialSaveBtn.disabled = busy;
}

function renderRepositoryModal() {
    const detail = state.repository.detail;
    if (!detail) return;

    refs.repositoryName.textContent = detail.name || '-';
    refs.repositoryPath.textContent = detail.material_path || '-';
    refs.repositoryProvider.textContent = detail.provider || 'Git';
    refs.repositoryRemoteName.textContent = detail.remote_url || '未识别远程地址';
    refs.repositoryBranch.textContent = detail.default_branch || detail.head_branch || '未识别分支';
    refs.repositoryProtocol.textContent = detail.remote_protocol || '未识别协议';
    refs.repositoryCredentialState.textContent = detail.credential_saved ? '已保存' : '未保存';
    refs.repositoryCredentialUser.textContent = detail.credential_username || '未填写';
    refs.repositoryCommandPreview.textContent = formatRepositoryCommandPreview(detail);
    refs.repositoryOutput.textContent = state.repository.lastOutput || '暂无输出';
    refs.repositoryStatus.textContent = state.repository.lastStatus === 'idle' ? '就绪' : state.repository.lastStatus;
    refs.repositorySyncSummary.textContent = state.repository.lastSyncSummary || '等待执行';
    refs.repositoryCommandInput.placeholder = '例如：git status -sb';
    setRepositoryBusy(state.repository.busy, refs.repositoryStatus.textContent);
}

async function refreshRepositoryState() {
    if (!state.repository.materialId) return;
    const data = await apiFetch(`/api/materials/${state.repository.materialId}/repository`, { silent: true });
    state.repository.detail = data.repository;
    renderRepositoryModal();
}

async function openRepositoryModal(materialId) {
    const data = await apiFetch(`/api/materials/${materialId}/repository`, { silent: true });
    state.repository.materialId = materialId;
    state.repository.detail = data.repository;
    state.repository.pendingAction = null;
    state.repository.lastStatus = '就绪';
    state.repository.lastOutput = '暂无输出';
    state.repository.lastSyncSummary = '等待执行';
    renderRepositoryModal();
    openModal('materials-repository-modal');
}

function openRepositoryCredentialModal() {
    const detail = state.repository.detail;
    if (!detail) return;
    refs.repositoryCredentialRemote.textContent = detail.remote_url || '未识别远程地址';
    refs.repositoryCredentialHost.textContent = detail.remote_host || detail.remote_protocol || '-';
    refs.repositoryCredentialUsername.value = detail.credential_username || '';
    refs.repositoryCredentialSecret.value = '';
    refs.repositoryCredentialAuthMode.value = 'password';
    refs.repositoryCredentialHint.textContent = detail.credential_supported
        ? '仅支持 HTTP / HTTPS 远程仓库的表单凭据。'
        : '当前远程仓库不是 HTTP / HTTPS，不能使用表单凭据，请优先配置 SSH Key。';
    openModal('materials-repository-credential-modal');
}

async function refreshRepositoryAffectedViews() {
    const currentParentId = state.currentParentId;
    try {
        await loadLibrary(currentParentId, false);
    } catch {
        await loadLibrary(null, false);
    }

    if (state.activeMaterialId) {
        try {
            await loadMaterialDetail(state.activeMaterialId);
        } catch {
            renderDetail(null);
        }
    }
}
