import { apiFetch } from './api.js';
import { closeModal, escapeHtml, formatDate, formatSize, getFileIcon, openModal, showToast } from './ui.js';
import {
    getLearningDocumentUrl,
    getMaterialPreviewUrl,
    getMaterialPrimaryAction,
    getMaterialTypeLabel,
    getRepositoryVisualMeta,
    hasLearningDocument,
    isGitRepository,
} from './materials_common.js';

const SORT_FIELD_LABELS = {
    name: '名称',
    created_at: '创建时间',
    updated_at: '更新时间',
};

const DEFAULT_SORT_ORDERS = {
    name: 'asc',
    created_at: 'desc',
    updated_at: 'desc',
};

const SEARCH_DEBOUNCE_MS = 280;

function normalizeKeyword(value) {
    return String(value || '')
        .replace(/\s+/g, ' ')
        .trim()
        .slice(0, 100);
}

function normalizeSortBy(value) {
    const sortBy = String(value || 'name').trim().toLowerCase();
    return Object.prototype.hasOwnProperty.call(SORT_FIELD_LABELS, sortBy) ? sortBy : 'name';
}

function normalizeSortOrder(value, sortBy = 'name') {
    const fallback = DEFAULT_SORT_ORDERS[sortBy] || 'asc';
    return String(value || fallback).trim().toLowerCase() === 'desc' ? 'desc' : 'asc';
}

function parsePositiveInt(value) {
    const parsed = Number(value);
    return Number.isInteger(parsed) && parsed > 0 ? parsed : null;
}

function normalizeIdentityHint(value) {
    return normalizeKeyword(value).toLowerCase();
}

function shouldIgnoreInitialKeyword(keyword) {
    const normalizedKeyword = normalizeIdentityHint(keyword);
    if (!normalizedKeyword) {
        return false;
    }

    const hints = Array.isArray(window.MATERIALS_MANAGE_CONFIG?.userIdentityHints)
        ? window.MATERIALS_MANAGE_CONFIG.userIdentityHints
        : [];
    return hints.some((hint) => normalizeIdentityHint(hint) === normalizedKeyword);
}

function getInitialLibraryState() {
    const params = new URLSearchParams(window.location.search);
    const sortBy = normalizeSortBy(params.get('sort_by'));
    const initialKeyword = normalizeKeyword(params.get('keyword'));
    return {
        parentId: parsePositiveInt(params.get('parent_id')),
        keyword: shouldIgnoreInitialKeyword(initialKeyword) ? '' : initialKeyword,
        sortBy,
        sortOrder: normalizeSortOrder(params.get('sort_order'), sortBy),
    };
}

function escapeRegex(value) {
    return String(value || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function highlightText(text, keyword) {
    const source = String(text || '');
    const normalizedKeyword = normalizeKeyword(keyword);
    if (!normalizedKeyword) {
        return escapeHtml(source);
    }

    const matcher = new RegExp(`(${escapeRegex(normalizedKeyword)})`, 'ig');
    return source
        .split(matcher)
        .map((segment, index) => (index % 2 === 1 ? `<mark>${escapeHtml(segment)}</mark>` : escapeHtml(segment)))
        .join('');
}

function formatDateLabel(value) {
    return formatDate(value || '') || '暂无';
}

function getSortSummary(sortBy, sortOrder) {
    return `按${SORT_FIELD_LABELS[sortBy] || '名称'}${sortOrder === 'desc' ? '降序' : '升序'}`;
}

const initialLibraryState = getInitialLibraryState();

const state = {
    currentParentId: initialLibraryState.parentId,
    history: [],
    items: [],
    activeMaterialId: null,
    activeDetail: null,
    detailRequestId: 0,
    selectedIds: new Set(),
    currentFolder: null,
    currentBreadcrumbs: [],
    filters: {
        keyword: initialLibraryState.keyword,
        sortBy: initialLibraryState.sortBy,
        sortOrder: initialLibraryState.sortOrder,
    },
    overview: null,
    stats: null,
    searchTimer: null,
    _aiAssignBusy: false,
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
    detailModal: document.getElementById('materials-detail-modal'),
    detailModalBody: document.getElementById('materials-detail-modal-body'),
    detailModalCloseBtn: document.getElementById('materials-detail-modal-close-btn'),
    detailModalLabel: document.getElementById('materials-detail-modal-label'),
    detailModalTitle: document.getElementById('materials-detail-modal-title'),
    detailModalPath: document.getElementById('materials-detail-modal-path'),
    backBtn: document.getElementById('materials-back-btn'),
    upBtn: document.getElementById('materials-up-btn'),
    refreshBtn: document.getElementById('materials-refresh-btn'),
    repositoryBtn: document.getElementById('materials-repository-btn'),
    fileBtn: document.getElementById('materials-upload-file-btn'),
    folderBtn: document.getElementById('materials-upload-folder-btn'),
    fileInput: document.getElementById('materials-file-input'),
    folderInput: document.getElementById('materials-folder-input'),
    searchInput: document.getElementById('materials-search-input'),
    searchClearBtn: document.getElementById('materials-search-clear-btn'),
    sortBy: document.getElementById('materials-sort-by'),
    sortOrder: document.getElementById('materials-sort-order'),
    scopeName: document.getElementById('materials-scope-name'),
    scopePath: document.getElementById('materials-scope-path'),
    scopeDescription: document.getElementById('materials-scope-description'),
    resultCount: document.getElementById('materials-result-count'),
    sortSummary: document.getElementById('materials-sort-summary'),
    searchSummary: document.getElementById('materials-search-summary'),
    selectAll: document.getElementById('materials-select-all'),
    selectionBar: document.getElementById('materials-selection-bar'),
    selectionCount: document.getElementById('materials-selection-count'),
    selectionDownloadBtn: document.getElementById('materials-selection-download-btn'),
    selectionClearBtn: document.getElementById('materials-selection-clear-btn'),
    assignName: document.getElementById('materials-assign-name'),
    assignOptions: document.getElementById('materials-assign-options'),
    assignSaveBtn: document.getElementById('materials-assign-save-btn'),
    assignAiBtn: document.getElementById('materials-assign-ai-btn'),
    aiAssignResult: document.getElementById('materials-ai-assign-result'),
    aiAssignSummary: document.getElementById('materials-ai-assign-summary'),
    aiAssignList: document.getElementById('materials-ai-assign-list'),
    rootCount: document.getElementById('materials-root-count'),
    totalCount: document.getElementById('materials-total-count'),
    folderFileSummary: document.getElementById('materials-folder-file-summary'),
    assignmentCount: document.getElementById('materials-assignment-count'),
    classroomCount: document.getElementById('materials-classroom-count'),
    totalSize: document.getElementById('materials-total-size'),
    latestUpdated: document.getElementById('materials-latest-updated'),
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

if (refs.detail && refs.detailModalBody && refs.detail.parentElement !== refs.detailModalBody) {
    refs.detailModalBody.appendChild(refs.detail);
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

function updateFilterControls() {
    refs.searchInput.value = state.filters.keyword;
    refs.searchClearBtn.hidden = !state.filters.keyword;
    refs.sortBy.value = state.filters.sortBy;
    refs.sortOrder.value = state.filters.sortOrder;
}

function renderStats() {
    if (!state.stats) return;
    refs.rootCount.textContent = String(state.stats.root_count || 0);
    refs.totalCount.textContent = String(state.stats.total_count || 0);
    refs.folderFileSummary.textContent = `文件夹 ${state.stats.folder_count || 0} / 文件 ${state.stats.file_count || 0}`;
    refs.assignmentCount.textContent = String(state.stats.assigned_material_count || 0);
    refs.classroomCount.textContent = `覆盖 ${state.stats.classroom_count || 0} 个课堂`;
    refs.totalSize.textContent = formatSize(state.stats.total_size || 0);
    refs.latestUpdated.textContent = formatDateLabel(state.stats.latest_updated_at);
}

function renderLibraryOverview() {
    const overview = state.overview || {
        scope_name: '材料库根目录',
        scope_path: '/',
        description: '当前目录显示 0 项',
        result_count: 0,
        search_active: false,
        sort_by: state.filters.sortBy,
        sort_order: state.filters.sortOrder,
    };

    refs.scopeName.textContent = overview.scope_name || '材料库根目录';
    refs.scopePath.textContent = overview.scope_path || '/';
    refs.scopeDescription.textContent = overview.description || '当前目录显示 0 项';
    refs.resultCount.textContent = `${overview.result_count || 0} 项`;
    refs.sortSummary.textContent = getSortSummary(overview.sort_by || state.filters.sortBy, overview.sort_order || state.filters.sortOrder);

    if (overview.search_active) {
        refs.searchSummary.hidden = false;
        refs.searchSummary.textContent = `搜索：${overview.search_keyword || state.filters.keyword}`;
    } else {
        refs.searchSummary.hidden = true;
        refs.searchSummary.textContent = '';
    }
}

function updateSelectionBar() {
    const count = state.selectedIds.size;
    refs.selectionBar.hidden = count === 0;
    refs.selectionCount.textContent = String(count);
    refs.selectAll.checked = state.items.length > 0 && state.items.every((item) => state.selectedIds.has(Number(item.id)));
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
    refs.repositoryBtn.hidden = !(state.currentFolder && isGitRepository(state.currentFolder));
}

function renderNavigationState() {
    refs.backBtn.disabled = state.history.length === 0;
    refs.upBtn.disabled = state.currentBreadcrumbs.length === 0;
}

function updateDetailModalHeader(detail) {
    refs.detailModalLabel.textContent = detail ? getMaterialTypeLabel(detail) : '材料详情';
    refs.detailModalTitle.textContent = detail?.name || '课程材料详情';
    refs.detailModalPath.textContent = detail?.material_path || '/';
}

function isDetailModalOpen() {
    return Boolean(refs.detailModal && refs.detailModal.style.display !== 'none');
}

function openDetailModal() {
    if (!refs.detailModal) return;
    refs.detailModal.setAttribute('aria-hidden', 'false');
    openModal('materials-detail-modal');
}

function closeDetailModal() {
    if (!refs.detailModal) return;
    refs.detailModal.setAttribute('aria-hidden', 'true');
    closeModal('materials-detail-modal');
}

function renderList() {
    if (!state.items.length) {
        const emptyText = state.filters.keyword
            ? `未找到与“${escapeHtml(state.filters.keyword)}”匹配的材料，请尝试简化关键词或清空搜索。`
            : '当前目录暂无材料。';
        refs.listBody.innerHTML = `<div class="materials-empty">${emptyText}</div>`;
        updateSelectionBar();
        return;
    }

    refs.listBody.innerHTML = state.items.map((item) => {
        const visualMeta = getVisualMeta(item);
        const activeClass = Number(item.id) === Number(state.activeMaterialId) ? 'is-active' : '';
        const selectedClass = state.selectedIds.has(Number(item.id)) ? 'is-selected' : '';
        const primaryAction = getMaterialPrimaryAction(item);
        const aiStatus = item.can_ai_parse ? `<span class="materials-meta-item">AI ${escapeHtml(item.ai_parse_status || 'idle')}</span>` : '';
        const readmeStatus = hasLearningDocument(item) ? '<span class="materials-meta-item">README</span>' : '';
        const documentAction = hasLearningDocument(item)
            ? '<button type="button" class="btn btn-outline btn-sm" data-action="view-doc">文档</button>'
            : '';
        const repositoryAction = isGitRepository(item)
            ? '<button type="button" class="btn btn-outline btn-sm" data-action="repository">仓库</button>'
            : '';
        const repositoryBadge = visualMeta.badge
            ? `<span class="materials-repo-badge" style="--repo-color:${visualMeta.color};">${escapeHtml(visualMeta.badge)}</span>`
            : '';

        return `
            <div class="materials-row materials-manage-row ${activeClass} ${selectedClass}" data-id="${item.id}">
                <div class="materials-row-check">
                    <input type="checkbox" data-role="select-item" data-id="${item.id}" ${state.selectedIds.has(Number(item.id)) ? 'checked' : ''}>
                </div>
                <div class="materials-row-main">
                    <div class="materials-name-cell">
                        <div class="materials-type-icon" style="background:${visualMeta.color}16;color:${visualMeta.color};">${escapeHtml(visualMeta.label)}</div>
                        <div class="materials-name-copy">
                            <strong title="${escapeHtml(item.name)}">${highlightText(item.name, state.filters.keyword)}</strong>
                            <div class="materials-name-badges">${repositoryBadge}</div>
                            <span title="${escapeHtml(item.material_path || '')}">${highlightText(item.material_path || '', state.filters.keyword)}</span>
                        </div>
                    </div>
                    <div class="materials-row-meta">
                        <span class="materials-type-pill">${escapeHtml(getMaterialTypeLabel(item))}</span>
                        <span class="materials-meta-item">${escapeHtml(getMetaText(item))}</span>
                        ${item.assignment_count ? `<span class="materials-meta-item">已分配 ${escapeHtml(String(item.assignment_count))} 次</span>` : ''}
                        ${aiStatus}
                        ${readmeStatus}
                    </div>
                </div>
                <div class="materials-row-time">
                    <span><strong>创建</strong>${escapeHtml(formatDateLabel(item.created_at))}</span>
                    <span><strong>更新</strong>${escapeHtml(formatDateLabel(item.updated_at || item.created_at))}</span>
                </div>
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
    updateDetailModalHeader(detail);

    if (!detail) {
        refs.detail.innerHTML = '<div class="materials-empty">选择一项材料后，这里会显示详情、AI 摘要与课堂分配状态。</div>';
        return;
    }

    const previewUrl = getMaterialPreviewUrl(detail);
    const optimizedUrl = detail.has_optimized_version ? `/materials/view/${detail.id}?variant=optimized` : '';
    const aiSummary = detail.ai_parse_result?.summary || '尚未执行 AI 解析。';
    const assignmentCount = Array.isArray(detail.assignments) ? detail.assignments.length : 0;
    const repositoryMeta = getRepositoryVisualMeta(detail);
    const previewLabel = detail.node_type === 'folder' && detail.document_readme_id
        ? '查看文档'
        : (detail.editable ? '预览 / 编辑' : '全屏预览');
    const repositoryBadge = repositoryMeta
        ? `<span class="materials-repo-badge" style="--repo-color:${repositoryMeta.color};">${escapeHtml(repositoryMeta.badge)}</span>`
        : '';
    const keywords = detail.ai_parse_result?.keywords?.length
        ? `<div class="text-muted text-sm mt-2">关键词：${escapeHtml(detail.ai_parse_result.keywords.join('、'))}</div>`
        : '';
    const teachingValue = detail.ai_parse_result?.teaching_value
        ? `
            <div class="materials-detail-note">
                <strong>教学价值</strong>
                <div>${escapeHtml(detail.ai_parse_result.teaching_value)}</div>
            </div>
        `
        : '';
    const cautions = detail.ai_parse_result?.cautions
        ? `
            <div class="materials-detail-note">
                <strong>使用提醒</strong>
                <div>${escapeHtml(detail.ai_parse_result.cautions)}</div>
            </div>
        `
        : '';

    refs.detail.innerHTML = `
        <div class="materials-detail-shell">
            <section class="materials-detail-hero">
                <div class="materials-detail-hero-main">
                    <div class="materials-detail-badges">
                        <span class="materials-type-pill">${escapeHtml(getMaterialTypeLabel(detail))}</span>
                        ${repositoryBadge}
                        ${hasLearningDocument(detail) ? '<span class="materials-meta-item">README</span>' : ''}
                    </div>
                    <h3 title="${escapeHtml(detail.name)}">${escapeHtml(detail.name)}</h3>
                    <div class="text-muted text-sm">${escapeHtml(detail.material_path || '')}</div>
                    <div class="materials-detail-actions">
                        ${previewUrl ? `<a href="${previewUrl}" class="btn btn-primary" target="_blank" rel="noopener">${previewLabel}</a>` : ''}
                        ${optimizedUrl ? `<a href="${optimizedUrl}" class="btn btn-outline" target="_blank" rel="noopener">查看优化稿</a>` : ''}
                        ${detail.node_type === 'file' ? `<a href="/materials/download/${detail.id}" class="btn btn-outline">下载</a>` : ''}
                        ${isGitRepository(detail) ? '<button type="button" class="btn btn-outline" data-detail-action="repository">仓库</button>' : ''}
                        <button type="button" class="btn btn-outline" data-detail-action="assign" ${config.canAssign ? '' : 'disabled'}>分配课堂</button>
                        <button type="button" class="btn btn-outline" data-detail-action="ai-parse" ${detail.can_ai_parse ? '' : 'disabled'}>AI 解析</button>
                        <button type="button" class="btn btn-outline" data-detail-action="ai-optimize" ${detail.can_ai_optimize ? '' : 'disabled'}>AI 优化</button>
                        <button type="button" class="btn btn-danger" data-detail-action="delete">删除</button>
                    </div>
                </div>
                <div class="materials-detail-meta">
                    <div class="meta-chip">
                        <strong>大小 / 子项</strong>
                        <span>${escapeHtml(getMetaText(detail))}</span>
                    </div>
                    <div class="meta-chip">
                        <strong>创建时间</strong>
                        <span>${escapeHtml(formatDateLabel(detail.created_at))}</span>
                    </div>
                    <div class="meta-chip">
                        <strong>更新时间</strong>
                        <span>${escapeHtml(formatDateLabel(detail.updated_at || detail.created_at))}</span>
                    </div>
                    <div class="meta-chip">
                        <strong>已分配课堂</strong>
                        <span>${escapeHtml(String(assignmentCount))}</span>
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
            </section>
            ${renderRepositorySummary(detail)}
            <div class="materials-detail-section-grid">
                <div class="materials-section">
                    <div class="materials-section-header">
                        <h3>AI 摘要</h3>
                    </div>
                    <div class="text-muted text-sm">${escapeHtml(aiSummary)}</div>
                    ${keywords}
                    ${teachingValue}
                    ${cautions}
                </div>
                <div class="materials-section">
                    <div class="materials-section-header">
                        <h3>已分配课堂</h3>
                    </div>
                    ${renderAssignments(detail.assignments || [])}
                </div>
                <div class="materials-section materials-section--wide">
                    <div class="materials-section-header">
                        <h3>解析目录</h3>
                    </div>
                    ${renderOutline(detail.ai_parse_result?.outline)}
                </div>
            </div>
        </div>
    `;
}

async function loadMaterialDetail(materialId) {
    const requestId = ++state.detailRequestId;
    state.activeMaterialId = Number(materialId);
    renderList();
    const detail = await apiFetch(`/api/materials/${materialId}`, { silent: true }).then((data) => data.material);
    if (requestId !== state.detailRequestId) {
        return state.activeDetail;
    }
    state.activeDetail = detail;
    renderDetail(state.activeDetail);
    return state.activeDetail;
}

async function openMaterialDetail(materialId) {
    await loadMaterialDetail(materialId);
    openDetailModal();
}

function buildLibraryQuery(parentId) {
    const params = new URLSearchParams();
    if (parentId) {
        params.set('parent_id', String(parentId));
    }
    if (state.filters.keyword) {
        params.set('keyword', state.filters.keyword);
    }
    params.set('sort_by', state.filters.sortBy);
    params.set('sort_order', state.filters.sortOrder);
    return params.toString();
}

function syncLibraryUrl() {
    const query = buildLibraryQuery(state.currentParentId);
    const url = `${window.location.pathname}${query ? `?${query}` : ''}`;
    window.history.replaceState({}, '', url);
}

async function loadLibrary(parentId = null, trackHistory = false) {
    const targetParentId = parentId ?? null;
    const query = buildLibraryQuery(targetParentId);
    const data = await apiFetch(`/api/materials/library${query ? `?${query}` : ''}`, { silent: true });

    if (trackHistory && state.currentParentId !== targetParentId) {
        state.history.push(state.currentParentId);
    }

    const previousActiveId = state.activeMaterialId;
    state.currentParentId = targetParentId;
    state.items = data.items || [];
    state.selectedIds.clear();
    state.currentFolder = data.current_folder || null;
    state.currentBreadcrumbs = data.breadcrumbs || [];
    state.filters.keyword = normalizeKeyword(data.filters?.keyword ?? state.filters.keyword);
    state.filters.sortBy = normalizeSortBy(data.filters?.sort_by ?? state.filters.sortBy);
    state.filters.sortOrder = normalizeSortOrder(data.filters?.sort_order ?? state.filters.sortOrder, state.filters.sortBy);
    state.overview = data.overview || null;
    state.stats = data.stats || null;

    const activeStillVisible = state.items.some((item) => Number(item.id) === Number(previousActiveId));
    state.activeMaterialId = activeStillVisible ? previousActiveId : null;
    if (!activeStillVisible) {
        state.detailRequestId += 1;
        state.activeDetail = null;
        if (isDetailModalOpen()) {
            closeDetailModal();
        }
    }

    updateFilterControls();
    renderStats();
    renderLibraryOverview();
    renderBreadcrumbs(state.currentBreadcrumbs);
    renderNavigationState();
    renderRepositoryToolbar();
    renderList();
    renderDetail(state.activeDetail);
    syncLibraryUrl();
}

function getCurrentItem(materialId) {
    return state.items.find((item) => Number(item.id) === Number(materialId)) || state.activeDetail;
}

function openFolder(materialId, trackHistory = true) {
    loadLibrary(materialId, trackHistory).catch((error) => {
        showToast(error.message || '打开文件夹失败', 'error');
    });
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

function triggerSearch() {
    clearTimeout(state.searchTimer);
    state.searchTimer = window.setTimeout(() => {
        loadLibrary(state.currentParentId, false).catch((error) => {
            showToast(error.message || '搜索材料失败', 'error');
        });
    }, SEARCH_DEBOUNCE_MS);
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
    const normalizedId = Number(materialId);
    if (checked) {
        state.selectedIds.add(normalizedId);
    } else {
        state.selectedIds.delete(normalizedId);
    }
    renderList();
}

function getSelectedMaterialIds() {
    return Array.from(state.selectedIds);
}

async function openAssignModal() {
    if (!state.activeDetail || !config.canAssign) return;
    refs.assignName.textContent = state.activeDetail.name;

    refs.assignOptions?.querySelectorAll('input[type="checkbox"]').forEach((checkbox) => {
        checkbox.checked = (state.activeDetail.assignments || []).some(
            (item) => Number(item.class_offering_id) === Number(checkbox.value),
        );
    });

    // 重置 AI 分配状态
    setAiAssignBusy(false);
    if (refs.aiAssignResult) refs.aiAssignResult.hidden = true;
    if (refs.aiAssignList) refs.aiAssignList.innerHTML = '';
    if (refs.aiAssignSummary) refs.aiAssignSummary.textContent = '';
    updateAiButtonState();

    openModal('materials-assign-modal');
}

function updateAiButtonState() {
    if (!refs.assignAiBtn) return;
    const checkedCount = refs.assignOptions?.querySelectorAll('input[type="checkbox"]:checked').length || 0;
    refs.assignAiBtn.disabled = checkedCount === 0 || state._aiAssignBusy === true;
}

function setAiAssignBusy(busy) {
    state._aiAssignBusy = busy;
    const btn = refs.assignAiBtn;
    if (!btn) return;
    const contentEl = btn.querySelector('.materials-ai-btn-content');
    const loadingEl = btn.querySelector('.materials-ai-btn-loading');
    if (contentEl) contentEl.hidden = busy;
    if (loadingEl) loadingEl.hidden = !busy;
    btn.classList.toggle('materials-ai-btn--loading', busy);
    updateAiButtonState();
}

function renderAiAssignResult(assignments) {
    if (!refs.aiAssignResult || !refs.aiAssignList || !refs.aiAssignSummary) return;
    if (!assignments || !assignments.length) {
        refs.aiAssignSummary.textContent = '未找到匹配结果';
        refs.aiAssignList.innerHTML = '<div class="text-muted text-sm" style="padding:8px 0;">AI 未能将文档匹配到课次，请手动分配。</div>';
        refs.aiAssignResult.hidden = false;
        return;
    }

    refs.aiAssignSummary.textContent = `成功绑定 ${assignments.length} 个文档到课次`;
    refs.aiAssignList.innerHTML = assignments.map((item) => {
        const confidence = String(item.confidence || 'medium').toLowerCase();
        const confidenceLabel = confidence === 'high' ? '高' : (confidence === 'low' ? '低' : '中');
        const pathFull = item.material_path || '';
        const pathShort = pathFull ? pathFull.split('/').slice(-2).join('/') : '';
        const orderIdx = item.order_index || 0;
        const sessionTitle = item.session_title || '';
        return `
            <div class="materials-ai-assign-item">
                <span class="materials-ai-assign-path" title="${escapeHtml(pathFull)}">${escapeHtml(pathShort)}</span>
                <span class="materials-ai-assign-arrow">&rarr;</span>
                <span class="materials-ai-assign-session">
                    <strong>第${escapeHtml(String(orderIdx))}课</strong>
                    ${sessionTitle ? `<span class="materials-ai-assign-session-title">${escapeHtml(sessionTitle)}</span>` : ''}
                </span>
                <span class="materials-ai-confidence materials-ai-confidence--${escapeHtml(confidence)}">${escapeHtml(confidenceLabel)}</span>
            </div>
        `;
    }).join('');
    refs.aiAssignResult.hidden = false;
}

async function runAiAssign() {
    if (!state.activeDetail) {
        console.warn('[AI Assign] state.activeDetail is null, cannot proceed');
        showToast('请先选择一个材料', 'warning');
        return;
    }
    const materialId = state.activeDetail.id;
    const selectedOfferingIds = Array.from(
        refs.assignOptions?.querySelectorAll('input[type="checkbox"]:checked') || [],
    ).map((checkbox) => Number(checkbox.value));
    if (!selectedOfferingIds.length) {
        showToast('请先选择至少一个课堂', 'warning');
        return;
    }

    console.log(`[AI Assign] Starting for material ${materialId}, offerings:`, selectedOfferingIds);
    setAiAssignBusy(true);
    try {
        const result = await apiFetch(`/api/materials/${materialId}/ai-assign-sessions`, {
            method: 'POST',
            body: { class_offering_ids: selectedOfferingIds },
        });
        console.log('[AI Assign] API response:', result);
        showToast(result.message || 'AI 分配完成', 'success');
        renderAiAssignResult(result.assignments || []);
        // 刷新详情和列表以反映绑定变化
        await loadLibrary(state.currentParentId);
        if (state.activeDetail) {
            await loadMaterialDetail(state.activeDetail.id);
        }
    } catch (error) {
        // apiFetch 已自动展示错误 toast，此处仅做日志记录
        console.error('[AI Assign] Failed:', error);
    } finally {
        setAiAssignBusy(false);
    }
}

async function saveAssignments() {
    if (!state.activeDetail) return;
    const materialId = state.activeDetail.id;
    const selectedOfferingIds = Array.from(
        refs.assignOptions.querySelectorAll('input[type="checkbox"]:checked'),
    ).map((checkbox) => Number(checkbox.value));

    const result = await apiFetch(`/api/materials/${materialId}/assign`, {
        method: 'POST',
        body: { class_offering_ids: selectedOfferingIds },
    });
    showToast(result.message || '课堂分配已更新', 'success');
    closeModal('materials-assign-modal');
    await loadLibrary(state.currentParentId);
    await loadMaterialDetail(materialId);
}

async function runAiParse() {
    if (!state.activeDetail) return;
    const materialId = state.activeDetail.id;
    const result = await apiFetch(`/api/materials/${materialId}/ai-parse`, { method: 'POST' });
    showToast(result.message || 'AI 解析完成', 'success');
    await loadLibrary(state.currentParentId);
    await loadMaterialDetail(materialId);
}

async function runAiOptimize() {
    if (!state.activeDetail) return;
    const materialId = state.activeDetail.id;
    const result = await apiFetch(`/api/materials/${materialId}/ai-optimize`, { method: 'POST' });
    showToast(result.message || 'AI 优化完成', 'success');
    await loadLibrary(state.currentParentId);
    await loadMaterialDetail(materialId);
    if (result.viewer_url) {
        window.open(result.viewer_url, '_blank', 'noopener');
    }
}

async function deleteActiveMaterial() {
    if (!state.activeDetail) return;
    if (!window.confirm(`确定删除材料“${state.activeDetail.name}”吗？`)) return;
    const result = await apiFetch(`/api/materials/${state.activeDetail.id}`, { method: 'DELETE' });
    showToast(result.message || '材料已删除', 'success');
    state.detailRequestId += 1;
    state.activeMaterialId = null;
    state.activeDetail = null;
    renderDetail(null);
    closeDetailModal();
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
    refs.repositoryCredentialSaveBtn.disabled = busy || !detail || !detail.credential_supported;
    refs.repositoryCommandInput.disabled = busy || !detail;
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
        : '当前远程仓库不是 HTTP / HTTPS，请优先配置 SSH Key。';
    openModal('materials-repository-credential-modal');
}

async function refreshRepositoryAffectedViews() {
    const currentParentId = state.currentParentId;
    const activeMaterialId = state.activeMaterialId;
    try {
        await loadLibrary(currentParentId, false);
    } catch {
        await loadLibrary(null, false);
    }

    if (activeMaterialId) {
        try {
            await loadMaterialDetail(activeMaterialId);
        } catch {
            state.activeMaterialId = null;
            state.activeDetail = null;
            renderList();
            renderDetail(null);
        }
    }
}

async function executeRepositoryAction(action, command = '') {
    if (!state.repository.materialId || !state.repository.detail) return;
    if (action === 'custom' && !String(command || '').trim()) {
        showToast('请输入 Git 命令', 'warning');
        refs.repositoryCommandInput.focus();
        return;
    }

    const busyText = action === 'update'
        ? '更新中'
        : (action === 'commit_push' ? '提交并推送中' : '执行命令中');
    setRepositoryBusy(true, busyText);

    try {
        const result = await apiFetch(`/api/materials/${state.repository.materialId}/repository/command`, {
            method: 'POST',
            body: { action, command },
            silent: true,
        });

        state.repository.detail = result.repository || state.repository.detail;
        state.repository.lastStatus = result.status === 'success'
            ? '执行成功'
            : (result.status === 'auth_required' ? '需要登录' : '执行失败');
        state.repository.lastOutput = result.combined_output || '暂无输出';
        state.repository.lastSyncSummary = formatRepositorySyncSummary(result.sync_summary);
        renderRepositoryModal();

        await refreshRepositoryAffectedViews();

        if (result.status === 'auth_required') {
            state.repository.pendingAction = { action, command };
            showToast(result.message || '远程仓库需要认证后才能继续', 'warning');
            if (result.credential_supported) {
                openRepositoryCredentialModal();
            }
            return;
        }

        state.repository.pendingAction = null;
        showToast(
            result.message || (result.status === 'success' ? '仓库操作完成' : '仓库操作失败'),
            result.status === 'success' ? 'success' : 'error',
        );
    } catch (error) {
        state.repository.lastStatus = '执行失败';
        state.repository.lastOutput = error.message || '暂无输出';
        renderRepositoryModal();
        showToast(error.message || '仓库操作失败', 'error');
    } finally {
        setRepositoryBusy(false, state.repository.lastStatus);
    }
}

async function saveRepositoryCredential() {
    const detail = state.repository.detail;
    if (!detail) return;
    if (!detail.credential_supported) {
        showToast('当前仓库不支持表单凭据，请改用 SSH Key', 'warning');
        return;
    }

    const username = refs.repositoryCredentialUsername.value.trim();
    const secret = refs.repositoryCredentialSecret.value.trim();
    const authMode = refs.repositoryCredentialAuthMode.value;
    if (!secret) {
        showToast('请输入密码或访问令牌', 'warning');
        refs.repositoryCredentialSecret.focus();
        return;
    }

    setRepositoryBusy(true, '保存凭据中');

    try {
        const result = await apiFetch(`/api/materials/${state.repository.materialId}/repository/credentials`, {
            method: 'POST',
            body: {
                username,
                secret,
                auth_mode: authMode,
            },
            silent: true,
        });

        closeModal('materials-repository-credential-modal');
        await refreshRepositoryState();
        showToast(result.message || '仓库凭据已保存', 'success');

        const pendingAction = state.repository.pendingAction;
        if (pendingAction) {
            state.repository.pendingAction = null;
            await executeRepositoryAction(pendingAction.action, pendingAction.command);
            return;
        }

        state.repository.lastStatus = '凭据已保存';
        renderRepositoryModal();
    } catch (error) {
        showToast(error.message || '保存凭据失败', 'error');
    } finally {
        setRepositoryBusy(false, state.repository.lastStatus);
    }
}

function bindEvents() {
    refs.refreshBtn?.addEventListener('click', () => {
        loadLibrary(state.currentParentId, false).catch((error) => {
            showToast(error.message || '刷新材料失败', 'error');
        });
    });

    refs.backBtn?.addEventListener('click', () => {
        const previousParentId = state.history.pop();
        loadLibrary(previousParentId ?? null, false).catch((error) => {
            showToast(error.message || '返回失败', 'error');
        });
    });

    refs.upBtn?.addEventListener('click', () => {
        const parentCrumb = state.currentBreadcrumbs.length >= 2
            ? state.currentBreadcrumbs[state.currentBreadcrumbs.length - 2]
            : null;
        loadLibrary(parentCrumb ? Number(parentCrumb.id) : null, true).catch((error) => {
            showToast(error.message || '返回上一级失败', 'error');
        });
    });

    refs.repositoryBtn?.addEventListener('click', () => {
        if (!state.currentFolder) return;
        openRepositoryModal(state.currentFolder.id).catch((error) => {
            showToast(error.message || '加载仓库信息失败', 'error');
        });
    });

    refs.fileBtn?.addEventListener('click', () => refs.fileInput?.click());
    refs.folderBtn?.addEventListener('click', () => refs.folderInput?.click());

    refs.fileInput?.addEventListener('change', async () => {
        try {
            await uploadFiles(refs.fileInput.files);
        } catch (error) {
            showToast(error.message || '文件上传失败', 'error');
        } finally {
            refs.fileInput.value = '';
        }
    });

    refs.folderInput?.addEventListener('change', async () => {
        try {
            await uploadFiles(refs.folderInput.files);
        } catch (error) {
            showToast(error.message || '文件夹上传失败', 'error');
        } finally {
            refs.folderInput.value = '';
        }
    });

    refs.searchInput?.addEventListener('input', (event) => {
        state.filters.keyword = normalizeKeyword(event.target.value);
        refs.searchClearBtn.hidden = !state.filters.keyword;
        triggerSearch();
    });

    refs.searchInput?.addEventListener('keydown', (event) => {
        if (event.key !== 'Enter') return;
        clearTimeout(state.searchTimer);
        state.filters.keyword = normalizeKeyword(refs.searchInput.value);
        loadLibrary(state.currentParentId, false).catch((error) => {
            showToast(error.message || '搜索材料失败', 'error');
        });
    });

    refs.searchClearBtn?.addEventListener('click', () => {
        clearTimeout(state.searchTimer);
        state.filters.keyword = '';
        updateFilterControls();
        loadLibrary(state.currentParentId, false).catch((error) => {
            showToast(error.message || '刷新搜索失败', 'error');
        });
    });

    refs.sortBy?.addEventListener('change', () => {
        state.filters.sortBy = normalizeSortBy(refs.sortBy.value);
        state.filters.sortOrder = normalizeSortOrder(DEFAULT_SORT_ORDERS[state.filters.sortBy], state.filters.sortBy);
        updateFilterControls();
        loadLibrary(state.currentParentId, false).catch((error) => {
            showToast(error.message || '排序材料失败', 'error');
        });
    });

    refs.sortOrder?.addEventListener('change', () => {
        state.filters.sortOrder = normalizeSortOrder(refs.sortOrder.value, state.filters.sortBy);
        loadLibrary(state.currentParentId, false).catch((error) => {
            showToast(error.message || '排序材料失败', 'error');
        });
    });

    refs.selectAll?.addEventListener('change', () => {
        if (refs.selectAll.checked) {
            state.items.forEach((item) => state.selectedIds.add(Number(item.id)));
        } else {
            state.selectedIds.clear();
        }
        renderList();
    });

    refs.selectionDownloadBtn?.addEventListener('click', async () => {
        try {
            await downloadByIds(getSelectedMaterialIds());
        } catch (error) {
            showToast(error.message || '下载失败', 'error');
        }
    });

    refs.selectionClearBtn?.addEventListener('click', () => {
        state.selectedIds.clear();
        renderList();
    });

    refs.assignSaveBtn?.addEventListener('click', () => {
        saveAssignments().catch((error) => {
            showToast(error.message || '保存课堂分配失败', 'error');
        });
    });

    refs.assignAiBtn?.addEventListener('click', () => {
        runAiAssign().catch((error) => {
            showToast(error.message || 'AI 分配失败', 'error');
        });
    });

    refs.assignOptions?.addEventListener('input', () => {
        updateAiButtonState();
    });

    refs.assignOptions?.addEventListener('click', (event) => {
        if (event.target.type === 'checkbox' || event.target.closest('label.materials-modal-option')) {
            requestAnimationFrame(() => updateAiButtonState());
        }
    });

    refs.detailModalCloseBtn?.addEventListener('click', () => {
        closeDetailModal();
    });

    refs.detailModal?.addEventListener('click', (event) => {
        if (event.target === refs.detailModal) {
            closeDetailModal();
        }
    });

    refs.detail?.addEventListener('click', (event) => {
        const action = event.target.closest('[data-detail-action]')?.dataset.detailAction;
        if (!action || !state.activeDetail) return;

        if (action === 'repository') {
            openRepositoryModal(state.activeDetail.id).catch((error) => {
                showToast(error.message || '加载仓库信息失败', 'error');
            });
            return;
        }
        if (action === 'assign') {
            openAssignModal().catch((error) => {
                showToast(error.message || '加载课堂分配失败', 'error');
            });
            return;
        }
        if (action === 'ai-parse') {
            runAiParse().catch((error) => {
                showToast(error.message || 'AI 解析失败', 'error');
            });
            return;
        }
        if (action === 'ai-optimize') {
            runAiOptimize().catch((error) => {
                showToast(error.message || 'AI 优化失败', 'error');
            });
            return;
        }
        if (action === 'delete') {
            deleteActiveMaterial().catch((error) => {
                showToast(error.message || '删除材料失败', 'error');
            });
        }
    });

    refs.breadcrumbs?.addEventListener('click', (event) => {
        const button = event.target.closest('[data-crumb-id]');
        if (!button) return;
        loadLibrary(Number(button.dataset.crumbId), true).catch((error) => {
            showToast(error.message || '打开目录失败', 'error');
        });
    });

    refs.listBody?.addEventListener('click', (event) => {
        const row = event.target.closest('.materials-row');
        if (!row) return;

        const materialId = Number(row.dataset.id);
        const item = state.items.find((entry) => Number(entry.id) === materialId);
        if (!item) return;

        const checkbox = event.target.closest('[data-role="select-item"]');
        if (checkbox) {
            toggleSelection(materialId, checkbox.checked);
            return;
        }

        const action = event.target.closest('[data-action]')?.dataset.action;
        if (action === 'open') {
            openFolder(materialId, true);
            return;
        }
        if (action === 'preview') {
            previewMaterial(materialId);
            return;
        }
        if (action === 'view-doc') {
            viewLearningDocument(materialId);
            return;
        }
        if (action === 'download') {
            downloadByIds([materialId]).catch((error) => {
                showToast(error.message || '下载失败', 'error');
            });
            return;
        }
        if (action === 'details') {
            openMaterialDetail(materialId).catch((error) => {
                showToast(error.message || '加载详情失败', 'error');
            });
            return;
        }
        if (action === 'repository') {
            state.activeMaterialId = materialId;
            renderList();
            openRepositoryModal(materialId).catch((error) => {
                showToast(error.message || '加载仓库信息失败', 'error');
            });
            return;
        }

        openMaterialDetail(materialId).catch((error) => {
            showToast(error.message || '加载详情失败', 'error');
        });
    });

    refs.listBody?.addEventListener('dblclick', (event) => {
        const row = event.target.closest('.materials-row');
        if (!row) return;

        const materialId = Number(row.dataset.id);
        const item = state.items.find((entry) => Number(entry.id) === materialId);
        if (!item) return;

        if (item.node_type === 'folder') {
            openFolder(materialId, true);
        } else if (item.preview_supported) {
            previewMaterial(materialId);
        }
    });

    refs.repositoryUpdateBtn?.addEventListener('click', () => {
        executeRepositoryAction('update').catch((error) => {
            showToast(error.message || '仓库更新失败', 'error');
        });
    });

    refs.repositoryPushBtn?.addEventListener('click', () => {
        executeRepositoryAction('commit_push').catch((error) => {
            showToast(error.message || '提交并推送失败', 'error');
        });
    });

    refs.repositoryAuthBtn?.addEventListener('click', () => {
        openRepositoryCredentialModal();
    });

    refs.repositoryCommandRunBtn?.addEventListener('click', () => {
        executeRepositoryAction('custom', refs.repositoryCommandInput?.value || '').catch((error) => {
            showToast(error.message || 'Git 命令执行失败', 'error');
        });
    });

    refs.repositoryCommandInput?.addEventListener('keydown', (event) => {
        if (event.key !== 'Enter') return;
        event.preventDefault();
        executeRepositoryAction('custom', refs.repositoryCommandInput.value || '').catch((error) => {
            showToast(error.message || 'Git 命令执行失败', 'error');
        });
    });

    refs.repositoryCredentialSaveBtn?.addEventListener('click', () => {
        saveRepositoryCredential().catch((error) => {
            showToast(error.message || '保存凭据失败', 'error');
        });
    });

    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && isDetailModalOpen()) {
            closeDetailModal();
        }
    });
}

bindEvents();
updateFilterControls();

loadLibrary(state.currentParentId, false).catch(async (error) => {
    if (state.currentParentId) {
        try {
            state.currentParentId = null;
            await loadLibrary(null, false);
            return;
        } catch {
            // fallback to original error below
        }
    }
    console.error(error);
    refs.listBody.innerHTML = `<div class="materials-empty">加载材料失败：${escapeHtml(error.message || '未知错误')}</div>`;
});
