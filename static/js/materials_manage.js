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
const AI_IMPORT_POLL_INTERVAL_MS = 3500;
const AI_IMPORT_ACTIVE_STATUSES = new Set(['queued', 'running']);
const AI_IMPORT_TERMINAL_STATUSES = new Set(['completed', 'failed', 'ai_failed', 'quality_failed', 'unsupported']);
const AI_GENERATE_MAX_ATTACHMENTS = 10;
const AI_GENERATE_SEARCH_DEBOUNCE_MS = 260;

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

function normalizeScopeFilter(value) {
    const scope = String(value || 'all').trim().toLowerCase();
    return ['all', 'owned', 'shared', 'private', 'department', 'school'].includes(scope) ? scope : 'all';
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
        scopeLevel: normalizeScopeFilter(params.get('scope_level')),
        school: normalizeKeyword(params.get('school')),
        department: normalizeKeyword(params.get('department')),
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
        scopeLevel: initialLibraryState.scopeLevel,
        school: initialLibraryState.school,
        department: initialLibraryState.department,
        sortBy: initialLibraryState.sortBy,
        sortOrder: initialLibraryState.sortOrder,
    },
    facets: null,
    overview: null,
    stats: null,
    searchTimer: null,
    _aiAssignBusy: false,
    aiImport: {
        busy: false,
        file: null,
        tasks: new Map(),
        dismissedTaskIds: new Set(),
        knownTaskStates: new Map(),
        pollTimer: 0,
        loadRequestId: 0,
    },
    aiGenerate: {
        busy: false,
        files: [],
        selectedMaterials: new Map(),
        selectedAssignments: new Map(),
        materialCandidates: [],
        assignmentCandidates: [],
        materialSearchTimer: 0,
        assignmentSearchTimer: 0,
        materialRequestId: 0,
        assignmentRequestId: 0,
    },
    aiRewrite: {
        busy: false,
        mode: 'regenerate',
        materialId: null,
    },
    repository: {
        materialId: null,
        detail: null,
        busy: false,
        autoBindBusy: false,
        autoBindCandidates: [],
        autoBindResult: null,
        pendingAction: null,
        lastStatus: 'idle',
        lastOutput: '暂无输出',
        lastSyncSummary: '等待执行',
    },
};

const config = window.MATERIALS_MANAGE_CONFIG || { offerings: [], canAssign: false, materialAiImportRegistry: [] };

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
    uploadMenu: document.getElementById('materials-upload-menu'),
    uploadMenuBtn: document.getElementById('materials-upload-menu-btn'),
    uploadDropdown: document.getElementById('materials-upload-dropdown'),
    directUploadBtn: document.getElementById('materials-upload-direct-btn'),
    aiImportOpenBtn: document.getElementById('materials-ai-import-open-btn'),
    folderBtn: document.getElementById('materials-upload-folder-btn'),
    fileInput: document.getElementById('materials-file-input'),
    folderInput: document.getElementById('materials-folder-input'),
    aiImportModal: document.getElementById('materials-ai-import-modal'),
    aiImportGroup: document.getElementById('materials-ai-import-group'),
    aiImportType: document.getElementById('materials-ai-import-type'),
    aiImportFileInput: document.getElementById('materials-ai-import-file-input'),
    aiImportChooseFileBtn: document.getElementById('materials-ai-import-choose-file-btn'),
    aiImportFileName: document.getElementById('materials-ai-import-file-name'),
    aiImportStatus: document.getElementById('materials-ai-import-status'),
    aiImportSubmitBtn: document.getElementById('materials-ai-import-submit-btn'),
    aiGenerateOpenBtn: document.getElementById('materials-ai-generate-open-btn'),
    aiGenerateModal: document.getElementById('materials-ai-generate-modal'),
    aiGenerateGroup: document.getElementById('materials-ai-generate-group'),
    aiGenerateType: document.getElementById('materials-ai-generate-type'),
    aiGeneratePrompt: document.getElementById('materials-ai-generate-prompt'),
    aiGenerateFileInput: document.getElementById('materials-ai-generate-file-input'),
    aiGenerateUploadBtn: document.getElementById('materials-ai-generate-upload-btn'),
    aiGenerateUploadList: document.getElementById('materials-ai-generate-upload-list'),
    aiGenerateMaterialQuery: document.getElementById('materials-ai-generate-material-query'),
    aiGenerateMaterialList: document.getElementById('materials-ai-generate-material-list'),
    aiGenerateAssignmentQuery: document.getElementById('materials-ai-generate-assignment-query'),
    aiGenerateAssignmentList: document.getElementById('materials-ai-generate-assignment-list'),
    aiGenerateSelected: document.getElementById('materials-ai-generate-selected'),
    aiGenerateCount: document.getElementById('materials-ai-generate-count'),
    aiGenerateStatus: document.getElementById('materials-ai-generate-status'),
    aiGenerateSubmitBtn: document.getElementById('materials-ai-generate-submit-btn'),
    aiRewriteModal: document.getElementById('materials-ai-rewrite-modal'),
    aiRewriteTitle: document.getElementById('materials-ai-rewrite-title'),
    aiRewriteSubtitle: document.getElementById('materials-ai-rewrite-subtitle'),
    aiRewritePrompt: document.getElementById('materials-ai-rewrite-prompt'),
    aiRewriteStatus: document.getElementById('materials-ai-rewrite-status'),
    aiRewriteSubmitBtn: document.getElementById('materials-ai-rewrite-submit-btn'),
    searchInput: document.getElementById('materials-search-input'),
    searchClearBtn: document.getElementById('materials-search-clear-btn'),
    scopeFilter: document.getElementById('materials-scope-filter'),
    schoolFilter: document.getElementById('materials-school-filter'),
    departmentFilter: document.getElementById('materials-department-filter'),
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
    repositoryAutoBindPanel: document.getElementById('materials-repository-autobind-panel'),
    repositoryAutoBindSummary: document.getElementById('materials-repository-autobind-summary'),
    repositoryAutoBindList: document.getElementById('materials-repository-autobind-list'),
    repositoryAutoBindRunBtn: document.getElementById('materials-repository-autobind-run-btn'),
    repositoryAutoBindDismissBtn: document.getElementById('materials-repository-autobind-dismiss-btn'),
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
    if (refs.scopeFilter) refs.scopeFilter.value = state.filters.scopeLevel;
    renderMaterialFacetOptions(refs.schoolFilter, state.facets?.schools || [], state.filters.school, '全部学校');
    renderMaterialFacetOptions(refs.departmentFilter, state.facets?.departments || [], state.filters.department, '全部系部');
    refs.sortBy.value = state.filters.sortBy;
    refs.sortOrder.value = state.filters.sortOrder;
}

function renderMaterialFacetOptions(select, options, selectedValue, emptyLabel) {
    if (!select) return;
    const current = normalizeKeyword(selectedValue);
    const uniqueOptions = [...new Set((options || []).map(item => normalizeKeyword(item)).filter(Boolean))];
    const optionHtml = uniqueOptions
        .map(item => `<option value="${escapeHtml(item)}" ${item === current ? 'selected' : ''}>${escapeHtml(item)}</option>`)
        .join('');
    select.innerHTML = `<option value="">${escapeHtml(emptyLabel)}</option>${optionHtml}`;
    select.value = uniqueOptions.includes(current) ? current : '';
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
    const aiTaskCards = renderAiImportTaskCards();
    if (!state.items.length && !aiTaskCards) {
        const emptyText = state.filters.keyword
            ? `未找到与“${escapeHtml(state.filters.keyword)}”匹配的材料，请尝试简化关键词或清空搜索。`
            : '当前目录暂无材料。';
        refs.listBody.innerHTML = `<div class="materials-empty">${emptyText}</div>`;
        updateSelectionBar();
        return;
    }

    const rowsHtml = state.items.map((item) => {
        const visualMeta = getVisualMeta(item);
        const activeClass = Number(item.id) === Number(state.activeMaterialId) ? 'is-active' : '';
        const selectedClass = state.selectedIds.has(Number(item.id)) ? 'is-selected' : '';
        const primaryAction = getMaterialPrimaryAction(item);
        const aiStatus = item.can_ai_parse ? `<span class="materials-meta-item">AI ${escapeHtml(item.ai_parse_status || 'idle')}</span>` : '';
        const readmeStatus = hasLearningDocument(item) ? '<span class="materials-meta-item">README</span>' : '';
        const scopeBadge = item.scope_label ? `<span class="materials-meta-item">${escapeHtml(item.scope_label)}</span>` : '';
        const sharedBadge = item.can_manage === false ? '<span class="materials-meta-item">共享材料</span>' : '';
        const documentAction = hasLearningDocument(item)
            ? '<button type="button" class="btn btn-outline btn-sm" data-action="view-doc">文档</button>'
            : '';
        const repositoryAction = isGitRepository(item) && item.can_manage !== false
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
                        ${scopeBadge}
                        ${sharedBadge}
                        ${aiStatus}
                        ${readmeStatus}
                    </div>
                </div>
                <div class="materials-row-time">
                    <span><strong>创建</strong>${escapeHtml(formatDateLabel(item.created_at))}</span>
                    <span><strong>更新</strong>${escapeHtml(formatDateLabel(item.updated_at || item.created_at))}</span>
                </div>
                <div class="materials-row-actions">
                    <button type="button" class="btn btn-ghost btn-sm" data-resource-attributes data-resource-type="material" data-resource-id="${item.id}">属性</button>
                    <button type="button" class="btn btn-ghost btn-sm" data-action="${primaryAction.action}">${primaryAction.label}</button>
                    ${documentAction}
                    ${repositoryAction}
                    ${item.node_type === 'file' ? '<button type="button" class="btn btn-ghost btn-sm" data-action="download">下载</button>' : ''}
                    <button type="button" class="btn btn-ghost btn-sm" data-action="details">详情</button>
                </div>
            </div>
        `;
    }).join('');
    const emptyHtml = !state.items.length
        ? '<div class="materials-empty">材料正在生成中，完成后会自动刷新到列表。</div>'
        : '';
    refs.listBody.innerHTML = `${aiTaskCards}${emptyHtml}${rowsHtml}`;

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
    const exportUrl = detail.ai_import_record?.export_url || '';
    const exportPdfUrl = detail.ai_import_record?.export_pdf_url || '';
    const aiSummary = detail.ai_parse_result?.summary || '尚未执行 AI 解析。';
    const assignmentCount = Array.isArray(detail.assignments) ? detail.assignments.length : 0;
    const canManage = detail.can_manage !== false;
    const scopeLevel = detail.scope_level || 'private';
    const scopeOptions = [
        ['private', '私有'],
        ['department', '本系部可见'],
        ['school', '本校可见'],
    ];
    const scopeControl = canManage
        ? `<select class="form-control" data-material-scope-select aria-label="材料开放范围">
            ${scopeOptions.map(([value, label]) => `<option value="${value}" ${scopeLevel === value ? 'selected' : ''}>${label}</option>`).join('')}
          </select>`
        : `<span>${escapeHtml(detail.scope_label || '私有')}</span>`;
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
                        <button type="button" class="btn btn-outline" data-resource-attributes data-resource-type="material" data-resource-id="${detail.id}">属性</button>
                        ${previewUrl ? `<a href="${previewUrl}" class="btn btn-primary" target="_blank" rel="noopener">${previewLabel}</a>` : ''}
                        ${optimizedUrl ? `<a href="${optimizedUrl}" class="btn btn-outline" target="_blank" rel="noopener">查看优化稿</a>` : ''}
                        ${exportUrl ? `<a href="${exportUrl}" class="btn btn-outline">导出Word</a>` : ''}
                        ${exportPdfUrl ? `<a href="${exportPdfUrl}" class="btn btn-outline">导出PDF</a>` : ''}
                        ${detail.node_type === 'file' ? `<a href="/materials/download/${detail.id}" class="btn btn-outline">下载</a>` : ''}
                        ${isGitRepository(detail) && canManage ? '<button type="button" class="btn btn-outline" data-detail-action="repository">仓库</button>' : ''}
                        <button type="button" class="btn btn-outline" data-detail-action="assign" ${config.canAssign ? '' : 'disabled'}>分配课堂</button>
                        <button type="button" class="btn btn-outline" data-detail-action="ai-parse" ${canManage && detail.can_ai_parse ? '' : 'disabled'}>AI 解析</button>
                        <button type="button" class="btn btn-outline" data-detail-action="ai-optimize" ${canManage && detail.can_ai_optimize ? '' : 'disabled'}>AI 优化</button>
                        <button type="button" class="btn btn-outline" data-detail-action="ai-regenerate" ${canManage && detail.can_ai_regenerate ? '' : 'disabled'}>AI 重新生成</button>
                        ${canManage ? '<button type="button" class="btn btn-danger" data-detail-action="delete">删除</button>' : ''}
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
                        <strong>开放范围</strong>
                        ${scopeControl}
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
    if (state.filters.scopeLevel && state.filters.scopeLevel !== 'all') {
        params.set('scope_level', state.filters.scopeLevel);
    }
    if (state.filters.school) {
        params.set('school', state.filters.school);
    }
    if (state.filters.department) {
        params.set('department', state.filters.department);
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
    state.filters.scopeLevel = normalizeScopeFilter(data.filters?.scope_level ?? state.filters.scopeLevel);
    state.filters.school = normalizeKeyword(data.filters?.school ?? state.filters.school);
    state.filters.department = normalizeKeyword(data.filters?.department ?? state.filters.department);
    state.filters.sortBy = normalizeSortBy(data.filters?.sort_by ?? state.filters.sortBy);
    state.filters.sortOrder = normalizeSortOrder(data.filters?.sort_order ?? state.filters.sortOrder, state.filters.sortBy);
    state.overview = data.overview || null;
    state.facets = data.facets || null;
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
    refreshAiImportTasksForCurrentFolder().catch(() => {});
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

function getAiImportRegistry() {
    return Array.isArray(config.materialAiImportRegistry) ? config.materialAiImportRegistry : [];
}

function setUploadMenuOpen(open) {
    if (!refs.uploadDropdown || !refs.uploadMenuBtn) return;
    refs.uploadDropdown.hidden = !open;
    refs.uploadMenuBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
}

function renderAiImportGroups() {
    const registry = getAiImportRegistry();
    if (!refs.aiImportGroup) return;
    refs.aiImportGroup.innerHTML = registry.map((group) => (
        `<option value="${escapeHtml(group.key)}">${escapeHtml(group.label)}</option>`
    )).join('');
    renderAiImportTypes();
}

function getSelectedAiImportGroup() {
    const registry = getAiImportRegistry();
    const selectedKey = refs.aiImportGroup?.value || registry[0]?.key || '';
    return registry.find((group) => group.key === selectedKey) || registry[0] || null;
}

function renderAiImportTypes() {
    const group = getSelectedAiImportGroup();
    const types = Array.isArray(group?.types) ? group.types : [];
    if (!refs.aiImportType) return;
    refs.aiImportType.innerHTML = types.map((docType) => (
        `<option value="${escapeHtml(docType.key)}">${escapeHtml(docType.label)}</option>`
    )).join('');
}

function updateAiImportFileLabel() {
    if (!refs.aiImportFileName) return;
    refs.aiImportFileName.textContent = state.aiImport.file ? state.aiImport.file.name : '未选择文件';
}

function setAiImportStatus(message = '', type = 'info') {
    if (!refs.aiImportStatus) return;
    const normalizedMessage = String(message || '').trim();
    refs.aiImportStatus.hidden = !normalizedMessage;
    refs.aiImportStatus.className = `materials-ai-import-status materials-ai-import-status--${type}`;
    refs.aiImportStatus.textContent = normalizedMessage;
}

function setAiImportBusy(busy) {
    state.aiImport.busy = busy;
    if (refs.aiImportSubmitBtn) {
        refs.aiImportSubmitBtn.disabled = busy;
        refs.aiImportSubmitBtn.textContent = busy ? '解析中...' : '开始解析';
    }
    if (refs.aiImportChooseFileBtn) refs.aiImportChooseFileBtn.disabled = busy;
    if (refs.aiImportGroup) refs.aiImportGroup.disabled = busy;
    if (refs.aiImportType) refs.aiImportType.disabled = busy;
}

function normalizeAiImportTask(rawTask) {
    if (!rawTask || !rawTask.id) return null;
    const status = String(rawTask.parse_status || rawTask.status || 'queued').trim().toLowerCase();
    return {
        ...rawTask,
        id: Number(rawTask.id),
        parent_material_id: rawTask.parent_material_id ? Number(rawTask.parent_material_id) : null,
        package_material_id: rawTask.package_material_id ? Number(rawTask.package_material_id) : null,
        source_material_id: rawTask.source_material_id ? Number(rawTask.source_material_id) : null,
        parsed_material_id: rawTask.parsed_material_id ? Number(rawTask.parsed_material_id) : null,
        source_file_name: String(rawTask.source_file_name || '材料文件'),
        document_type_label: String(rawTask.document_type_label || rawTask.document_type || '材料'),
        parse_status: status,
        status,
        status_label: String(rawTask.status_label || ''),
        message: String(rawTask.message || rawTask.error_message || ''),
        updated_at: String(rawTask.updated_at || ''),
    };
}

function getAiImportTaskStateKey(task) {
    return `${task.id}:${task.parse_status}:${task.updated_at || ''}`;
}

function isAiImportTaskActive(task) {
    return AI_IMPORT_ACTIVE_STATUSES.has(task?.parse_status);
}

function isAiImportTaskTerminal(task) {
    return AI_IMPORT_TERMINAL_STATUSES.has(task?.parse_status);
}

function isAiImportTaskVisible(task) {
    const currentParentId = state.currentParentId ? Number(state.currentParentId) : null;
    return (task?.parent_material_id || null) === currentParentId;
}

function upsertAiImportTask(rawTask) {
    const task = normalizeAiImportTask(rawTask);
    if (!task) return null;
    if (state.aiImport.dismissedTaskIds.has(task.id) && isAiImportTaskTerminal(task)) {
        return task;
    }
    state.aiImport.tasks.set(task.id, task);
    if (!state.aiImport.knownTaskStates.has(task.id)) {
        state.aiImport.knownTaskStates.set(task.id, getAiImportTaskStateKey(task));
    }
    return task;
}

function removeAiImportTask(taskId) {
    const normalizedId = Number(taskId);
    state.aiImport.dismissedTaskIds.add(normalizedId);
    state.aiImport.tasks.delete(normalizedId);
    state.aiImport.knownTaskStates.delete(normalizedId);
    renderList();
    startAiImportPolling();
}

function getVisibleAiImportTasks() {
    return Array.from(state.aiImport.tasks.values())
        .filter(isAiImportTaskVisible)
        .sort((left, right) => {
            const leftActive = isAiImportTaskActive(left) ? 0 : 1;
            const rightActive = isAiImportTaskActive(right) ? 0 : 1;
            if (leftActive !== rightActive) return leftActive - rightActive;
            return Number(right.id || 0) - Number(left.id || 0);
        });
}

function getAiImportTaskTone(task) {
    if (!task) return 'info';
    if (task.parse_status === 'completed') return 'success';
    if (task.parse_status === 'quality_failed' || task.parse_status === 'unsupported') return 'warning';
    if (['failed', 'ai_failed'].includes(task.parse_status)) return 'danger';
    return 'info';
}

function getAiImportTaskTitle(task) {
    const fileName = task?.source_file_name || '材料文件';
    if (task.parse_status === 'queued') return `AI 正在等待解析《${fileName}》`;
    if (task.parse_status === 'running') return `AI 正在解析《${fileName}》`;
    if (task.parse_status === 'completed') return `AI 已完成《${fileName}》解析`;
    if (task.parse_status === 'quality_failed') return `《${fileName}》疑似乱码`;
    if (task.parse_status === 'unsupported') return `《${fileName}》暂不支持解析`;
    if (task.parse_status === 'ai_failed') return `AI 未能识别《${fileName}》`;
    return `《${fileName}》解析失败`;
}

function getAiImportTaskMessage(task) {
    if (task?.message) return task.message;
    if (task?.parse_status === 'queued') return '任务已进入后台队列，会按顺序调用 AI，避免影响平台其他 AI 功能。';
    if (task?.parse_status === 'running') return '系统正在抽取正文、校验乱码并调用 AI 识别，完成后会自动刷新材料列表。';
    if (task?.parse_status === 'completed') return '已生成可阅读正文和结构化 JSON，后续可按同类模板导出。';
    if (task?.parse_status === 'quality_failed') return '系统检测到解析结果质量不足，已阻止保存无效内容。';
    if (task?.parse_status === 'unsupported') return '请先转换为 docx、xlsx 或 PDF 后重试。';
    if (task?.parse_status === 'ai_failed') return 'AI 服务未返回可用结果，请稍后重试。';
    return '解析未完成，请稍后重试。';
}

function renderAiImportTaskCards() {
    const tasks = getVisibleAiImportTasks();
    if (!tasks.length) return '';
    return tasks.map((task) => {
        const tone = getAiImportTaskTone(task);
        const active = isAiImportTaskActive(task);
        const completed = task.parse_status === 'completed';
        const packageAction = completed && task.package_material_id
            ? `<button type="button" class="btn btn-primary btn-sm" data-ai-import-action="open-package" data-ai-import-task-id="${task.id}">打开材料包</button>`
            : '';
        const viewAction = completed && task.parsed_material_id
            ? `<button type="button" class="btn btn-outline btn-sm" data-ai-import-action="view-doc" data-ai-import-task-id="${task.id}">查看正文</button>`
            : '';
        const dismissAction = isAiImportTaskTerminal(task)
            ? `<button type="button" class="btn btn-ghost btn-sm" data-ai-import-action="dismiss" data-ai-import-task-id="${task.id}">关闭</button>`
            : '';
        const queueText = task.queue_position && task.parse_status === 'queued'
            ? `<span>队列第 ${escapeHtml(String(task.queue_position))} 位</span>`
            : '';

        return `
            <section class="materials-ai-task-card is-${tone}" data-ai-import-task-id="${task.id}">
                <div class="materials-ai-task-indicator" aria-hidden="true">${active ? '<span></span>' : ''}</div>
                <div class="materials-ai-task-main">
                    <div class="materials-ai-task-head">
                        <span class="materials-ai-task-status">${escapeHtml(task.status_label || '处理中')}</span>
                        <strong>${escapeHtml(getAiImportTaskTitle(task))}</strong>
                    </div>
                    <p>${escapeHtml(getAiImportTaskMessage(task))}</p>
                    <div class="materials-ai-task-meta">
                        <span>${escapeHtml(task.document_type_label || '材料')}</span>
                        ${queueText}
                        ${task.updated_at ? `<span>更新 ${escapeHtml(formatDateLabel(task.updated_at))}</span>` : ''}
                    </div>
                </div>
                <div class="materials-ai-task-actions">
                    ${packageAction}
                    ${viewAction}
                    ${dismissAction}
                </div>
            </section>
        `;
    }).join('');
}

function hasActiveAiImportTasks() {
    return Array.from(state.aiImport.tasks.values()).some(isAiImportTaskActive);
}

function buildAiImportActiveTasksUrl() {
    const params = new URLSearchParams();
    if (state.currentParentId) {
        params.set('parent_id', String(state.currentParentId));
    }
    return `/api/materials/ai-import-records/active${params.toString() ? `?${params.toString()}` : ''}`;
}

async function refreshAiImportTasksForCurrentFolder() {
    const requestId = ++state.aiImport.loadRequestId;
    try {
        const result = await apiFetch(buildAiImportActiveTasksUrl(), { method: 'GET', silent: true });
        if (requestId !== state.aiImport.loadRequestId) return;
        (result.tasks || []).forEach((task) => upsertAiImportTask(task));
        renderList();
        startAiImportPolling();
    } catch (_error) {
        startAiImportPolling();
    }
}

async function pollAiImportTasks() {
    window.clearTimeout(state.aiImport.pollTimer);
    state.aiImport.pollTimer = 0;

    const activeTasks = Array.from(state.aiImport.tasks.values()).filter(isAiImportTaskActive);
    if (!activeTasks.length) return;

    let shouldRefreshLibrary = false;
    await Promise.all(activeTasks.map(async (task) => {
        try {
            const result = await apiFetch(`/api/materials/ai-import-records/${task.id}/status`, {
                method: 'GET',
                silent: true,
            });
            const nextTask = upsertAiImportTask(result.task);
            if (!nextTask) return;

            const previousStateKey = state.aiImport.knownTaskStates.get(nextTask.id);
            const nextStateKey = getAiImportTaskStateKey(nextTask);
            state.aiImport.knownTaskStates.set(nextTask.id, nextStateKey);

            if (previousStateKey !== nextStateKey && isAiImportTaskTerminal(nextTask)) {
                if (nextTask.parse_status === 'completed') {
                    showToast(`《${nextTask.source_file_name}》AI 解析完成`, 'success', 4200);
                } else {
                    const toastType = ['quality_failed', 'unsupported'].includes(nextTask.parse_status) ? 'warning' : 'error';
                    showToast(nextTask.message || `《${nextTask.source_file_name}》解析未完成`, toastType, 5200);
                }
                if (isAiImportTaskVisible(nextTask)) {
                    shouldRefreshLibrary = true;
                }
            }
        } catch (_error) {
            // 单个状态轮询失败不打断其他任务；下一轮继续尝试。
        }
    }));

    if (shouldRefreshLibrary) {
        await loadLibrary(state.currentParentId, false);
    } else {
        renderList();
    }
    startAiImportPolling();
}

function startAiImportPolling() {
    window.clearTimeout(state.aiImport.pollTimer);
    if (!hasActiveAiImportTasks()) {
        state.aiImport.pollTimer = 0;
        return;
    }
    state.aiImport.pollTimer = window.setTimeout(() => {
        pollAiImportTasks().catch(() => {
            startAiImportPolling();
        });
    }, AI_IMPORT_POLL_INTERVAL_MS);
}

function openAiImportModal() {
    if (!getAiImportRegistry().length) {
        showToast('材料解析类型暂未加载', 'error');
        return;
    }
    state.aiImport.file = null;
    if (refs.aiImportFileInput) refs.aiImportFileInput.value = '';
    renderAiImportGroups();
    updateAiImportFileLabel();
    setAiImportStatus('', 'info');
    setAiImportBusy(false);
    openModal('materials-ai-import-modal');
}

async function submitAiImport() {
    if (state.aiImport.busy) return;
    const groupKey = refs.aiImportGroup?.value || '';
    const typeKey = refs.aiImportType?.value || '';
    if (!groupKey || !typeKey) {
        showToast('请选择材料类型', 'warning');
        return;
    }
    if (!state.aiImport.file) {
        showToast('请选择要解析的文件', 'warning');
        refs.aiImportFileInput?.click();
        return;
    }

    const formData = new FormData();
    formData.append('file', state.aiImport.file, state.aiImport.file.name);
    formData.append('document_group', groupKey);
    formData.append('document_type', typeKey);
    if (state.currentParentId) {
        formData.append('parent_id', String(state.currentParentId));
    }

    setAiImportBusy(true);
    setAiImportStatus('正在上传并加入后台解析队列...', 'info');
    try {
        const result = await apiFetch('/api/materials/ai-import', {
            method: 'POST',
            body: formData,
        });
        const task = upsertAiImportTask(result.task || { id: result.import_record_id, source_file_name: state.aiImport.file.name, parse_status: 'queued' });
        closeModal('materials-ai-import-modal');
        state.aiImport.file = null;
        if (refs.aiImportFileInput) refs.aiImportFileInput.value = '';
        updateAiImportFileLabel();
        renderList();
        startAiImportPolling();
        showToast(result.message || `《${task?.source_file_name || '材料文件'}》已加入 AI 解析队列`, 'success', 4200);
    } catch (error) {
        setAiImportStatus(error.message || 'AI 解析导入失败', 'error');
        throw error;
    } finally {
        setAiImportBusy(false);
    }
}

function getAiGenerateAttachmentCount() {
    return state.aiGenerate.files.length
        + state.aiGenerate.selectedMaterials.size
        + state.aiGenerate.selectedAssignments.size;
}

function canAddAiGenerateAttachment() {
    return getAiGenerateAttachmentCount() < AI_GENERATE_MAX_ATTACHMENTS;
}

function setAiGenerateStatus(message = '', type = 'info') {
    if (!refs.aiGenerateStatus) return;
    const normalizedMessage = String(message || '').trim();
    refs.aiGenerateStatus.hidden = !normalizedMessage;
    refs.aiGenerateStatus.className = `materials-ai-import-status materials-ai-import-status--${type}`;
    refs.aiGenerateStatus.textContent = normalizedMessage;
}

function setAiGenerateBusy(busy) {
    state.aiGenerate.busy = busy;
    if (refs.aiGenerateSubmitBtn) {
        refs.aiGenerateSubmitBtn.disabled = busy;
        refs.aiGenerateSubmitBtn.textContent = busy ? '深度思考中...' : '生成并保存';
    }
    [
        refs.aiGeneratePrompt,
        refs.aiGenerateUploadBtn,
        refs.aiGenerateMaterialQuery,
        refs.aiGenerateAssignmentQuery,
    ].forEach((element) => {
        if (element) element.disabled = busy;
    });
}

function resetAiGenerateState() {
    state.aiGenerate.files = [];
    state.aiGenerate.selectedMaterials = new Map();
    state.aiGenerate.selectedAssignments = new Map();
    state.aiGenerate.materialCandidates = [];
    state.aiGenerate.assignmentCandidates = [];
    if (refs.aiGenerateFileInput) refs.aiGenerateFileInput.value = '';
    if (refs.aiGenerateGroup) refs.aiGenerateGroup.value = 'teaching_material';
    updateAiGenerateTypeOptions();
    if (refs.aiGeneratePrompt) refs.aiGeneratePrompt.value = '';
    if (refs.aiGenerateMaterialQuery) refs.aiGenerateMaterialQuery.value = '';
    if (refs.aiGenerateAssignmentQuery) refs.aiGenerateAssignmentQuery.value = '';
    setAiGenerateStatus('', 'info');
}

function updateAiGenerateTypeOptions() {
    if (!refs.aiGenerateGroup || !refs.aiGenerateType) return;
    const group = refs.aiGenerateGroup.value || 'teaching_material';
    let firstVisible = '';
    Array.from(refs.aiGenerateType.options || []).forEach((option) => {
        const visible = (option.dataset.group || 'teaching_material') === group;
        option.hidden = !visible;
        option.disabled = !visible;
        if (visible && !firstVisible) {
            firstVisible = option.value;
        }
    });
    const selected = refs.aiGenerateType.selectedOptions?.[0];
    if (!selected || selected.hidden || selected.disabled) {
        refs.aiGenerateType.value = firstVisible;
    }
    updateAiGeneratePromptPlaceholder();
}

function updateAiGeneratePromptPlaceholder() {
    if (!refs.aiGeneratePrompt || !refs.aiGenerateType) return;
    const type = refs.aiGenerateType.value || 'teaching_document';
    if (type === 'grading_rubric') {
        refs.aiGeneratePrompt.placeholder = '例如：根据关联试卷逐题生成评分细则，写清每题给分点、扣分项、例外情况和截图要求。';
    } else if (type === 'assessment_plan') {
        refs.aiGeneratePrompt.placeholder = '例如：按机试/项目实操拆分考核技能与分值，补齐课程、班级、命题教师等字段。';
    } else if (type === 'exam_paper') {
        refs.aiGeneratePrompt.placeholder = '例如：优先关联考核计划表，再围绕课程核心能力生成期末机试试卷，包含任务、截图编号、提交要求和考试时长，分值严格继承计划表。';
    } else {
        refs.aiGeneratePrompt.placeholder = '例如：根据这些作业题目生成一份期末复习提纲，包含知识点、易错点和课堂练习安排。';
    }
}

function renderAiGenerateSelected() {
    const count = getAiGenerateAttachmentCount();
    if (refs.aiGenerateCount) {
        refs.aiGenerateCount.textContent = `${count} / ${AI_GENERATE_MAX_ATTACHMENTS}`;
    }
    if (!refs.aiGenerateSelected) return;
    const selected = [
        ...state.aiGenerate.files.map((entry) => ({
            kind: 'file',
            id: entry.id,
            title: entry.file.name,
            meta: formatSize(entry.file.size || 0),
        })),
        ...Array.from(state.aiGenerate.selectedMaterials.values()).map((item) => ({
            kind: 'material',
            id: item.id,
            title: item.name,
            meta: item.material_path || '站内材料',
        })),
        ...Array.from(state.aiGenerate.selectedAssignments.values()).map((item) => ({
            kind: 'assignment',
            id: item.id,
            title: item.title,
            meta: [item.course_name, item.class_name].filter(Boolean).join(' / ') || '已生成作业',
        })),
    ];
    if (!selected.length) {
        refs.aiGenerateSelected.innerHTML = '<div class="materials-empty materials-empty--compact">还没有关联附件。</div>';
        return;
    }
    refs.aiGenerateSelected.innerHTML = selected.map((item) => `
        <span class="materials-ai-generate-chip" title="${escapeHtml(item.meta)}">
            <strong>${escapeHtml(item.kind === 'file' ? '上传' : (item.kind === 'assignment' ? '作业' : '材料'))}</strong>
            <span>${escapeHtml(item.title)}</span>
            <button type="button" data-ai-generate-remove="${escapeHtml(item.kind)}" data-id="${escapeHtml(String(item.id))}" aria-label="移除 ${escapeHtml(item.title)}">&times;</button>
        </span>
    `).join('');
}

function renderAiGenerateUploadList() {
    if (!refs.aiGenerateUploadList) return;
    if (!state.aiGenerate.files.length) {
        refs.aiGenerateUploadList.innerHTML = '<div class="materials-ai-generate-empty">未选择新文件。</div>';
        return;
    }
    refs.aiGenerateUploadList.innerHTML = state.aiGenerate.files.map((entry) => `
        <div class="materials-ai-generate-candidate is-selected">
            <div>
                <strong title="${escapeHtml(entry.file.name)}">${escapeHtml(entry.file.name)}</strong>
                <span>${escapeHtml(formatSize(entry.file.size || 0))}</span>
            </div>
            <button type="button" class="btn btn-ghost btn-sm" data-ai-generate-remove="file" data-id="${escapeHtml(entry.id)}">移除</button>
        </div>
    `).join('');
}

function renderAiGenerateCandidateList(kind) {
    const isMaterial = kind === 'material';
    const listEl = isMaterial ? refs.aiGenerateMaterialList : refs.aiGenerateAssignmentList;
    if (!listEl) return;
    const items = isMaterial ? state.aiGenerate.materialCandidates : state.aiGenerate.assignmentCandidates;
    const selectedMap = isMaterial ? state.aiGenerate.selectedMaterials : state.aiGenerate.selectedAssignments;
    if (!items.length) {
        listEl.innerHTML = `<div class="materials-ai-generate-empty">暂无可选${isMaterial ? '材料' : '作业'}。</div>`;
        return;
    }
    const reachedLimit = !canAddAiGenerateAttachment();
    listEl.innerHTML = items.map((item) => {
        const selected = selectedMap.has(Number(item.id));
        const title = isMaterial ? item.name : item.title;
        const subtitle = isMaterial
            ? (item.material_path || getMaterialTypeLabel(item))
            : ([item.course_name, item.class_name].filter(Boolean).join(' / ') || item.question_excerpt || '作业题目');
        const meta = isMaterial
            ? [getMaterialTypeLabel(item), item.node_type === 'folder' ? `${item.child_count || 0} 项` : formatSize(item.file_size || 0)].filter(Boolean).join(' · ')
            : [`${item.question_count || 0} 题`, item.status || ''].filter(Boolean).join(' · ');
        return `
            <button type="button"
                class="materials-ai-generate-candidate ${selected ? 'is-selected' : ''}"
                data-ai-generate-add="${escapeHtml(kind)}"
                data-id="${escapeHtml(String(item.id))}"
                ${selected || reachedLimit ? 'disabled' : ''}
            >
                <div>
                    <strong title="${escapeHtml(title)}">${escapeHtml(title)}</strong>
                    <span title="${escapeHtml(subtitle)}">${escapeHtml(subtitle)}</span>
                </div>
                <em>${escapeHtml(selected ? '已选' : meta)}</em>
            </button>
        `;
    }).join('');
}

function renderAiGenerateModal() {
    renderAiGenerateSelected();
    renderAiGenerateUploadList();
    renderAiGenerateCandidateList('material');
    renderAiGenerateCandidateList('assignment');
}

function addAiGenerateFiles(fileList) {
    if (!fileList || !fileList.length) return;
    const files = Array.from(fileList);
    for (const file of files) {
        if (!canAddAiGenerateAttachment()) {
            showToast(`关联附件最多支持 ${AI_GENERATE_MAX_ATTACHMENTS} 份`, 'warning');
            break;
        }
        const duplicate = state.aiGenerate.files.some((entry) => (
            entry.file.name === file.name && entry.file.size === file.size && entry.file.lastModified === file.lastModified
        ));
        if (duplicate) continue;
        state.aiGenerate.files.push({
            id: `${Date.now()}-${Math.random().toString(16).slice(2)}`,
            file,
        });
    }
    renderAiGenerateModal();
}

function removeAiGenerateAttachment(kind, idValue) {
    if (kind === 'file') {
        state.aiGenerate.files = state.aiGenerate.files.filter((entry) => entry.id !== idValue);
    } else if (kind === 'material') {
        state.aiGenerate.selectedMaterials.delete(Number(idValue));
    } else if (kind === 'assignment') {
        state.aiGenerate.selectedAssignments.delete(Number(idValue));
    }
    renderAiGenerateModal();
}

async function loadAiGenerateCandidates(kind, query = '') {
    const isMaterial = kind === 'material';
    const requestIdKey = isMaterial ? 'materialRequestId' : 'assignmentRequestId';
    const requestId = ++state.aiGenerate[requestIdKey];
    const params = new URLSearchParams();
    if (query) params.set('query', query);
    params.set('limit', '32');
    const endpoint = isMaterial ? '/api/materials/ai-generation/candidates' : '/api/materials/ai-generation/assignments';
    const result = await apiFetch(`${endpoint}?${params.toString()}`, { method: 'GET', silent: true });
    if (requestId !== state.aiGenerate[requestIdKey]) return;
    if (isMaterial) {
        state.aiGenerate.materialCandidates = result.items || [];
    } else {
        state.aiGenerate.assignmentCandidates = result.items || [];
    }
    renderAiGenerateCandidateList(kind);
}

function triggerAiGenerateCandidateSearch(kind) {
    const isMaterial = kind === 'material';
    const timerKey = isMaterial ? 'materialSearchTimer' : 'assignmentSearchTimer';
    const queryEl = isMaterial ? refs.aiGenerateMaterialQuery : refs.aiGenerateAssignmentQuery;
    window.clearTimeout(state.aiGenerate[timerKey]);
    state.aiGenerate[timerKey] = window.setTimeout(() => {
        loadAiGenerateCandidates(kind, normalizeKeyword(queryEl?.value || '')).catch((error) => {
            showToast(error.message || `加载${isMaterial ? '材料' : '作业'}候选失败`, 'error');
        });
    }, AI_GENERATE_SEARCH_DEBOUNCE_MS);
}

function selectAiGenerateCandidate(kind, idValue) {
    if (!canAddAiGenerateAttachment()) {
        showToast(`关联附件最多支持 ${AI_GENERATE_MAX_ATTACHMENTS} 份`, 'warning');
        return;
    }
    const id = Number(idValue);
    if (kind === 'material') {
        const item = state.aiGenerate.materialCandidates.find((entry) => Number(entry.id) === id);
        if (item) state.aiGenerate.selectedMaterials.set(id, item);
    } else if (kind === 'assignment') {
        const item = state.aiGenerate.assignmentCandidates.find((entry) => Number(entry.id) === id);
        if (item) state.aiGenerate.selectedAssignments.set(id, item);
    }
    renderAiGenerateModal();
}

function openAiGenerateModal() {
    resetAiGenerateState();
    setAiGenerateBusy(false);
    renderAiGenerateModal();
    openModal('materials-ai-generate-modal');
    Promise.all([
        loadAiGenerateCandidates('material', ''),
        loadAiGenerateCandidates('assignment', ''),
    ]).catch((error) => {
        setAiGenerateStatus(error.message || '候选上下文加载失败', 'error');
    });
    window.setTimeout(() => refs.aiGeneratePrompt?.focus(), 50);
}

async function submitAiGenerate() {
    if (state.aiGenerate.busy) return;
    const count = getAiGenerateAttachmentCount();
    const prompt = refs.aiGeneratePrompt?.value?.trim() || '';
    if (!prompt && count <= 0) {
        showToast('请填写提示语，或至少关联一份附件', 'warning');
        refs.aiGeneratePrompt?.focus();
        return;
    }
    const formData = new FormData();
    formData.append('prompt', prompt);
    formData.append('document_group', refs.aiGenerateGroup?.value || 'teaching_material');
    formData.append('document_type', refs.aiGenerateType?.value || 'teaching_document');
    formData.append('existing_material_ids', JSON.stringify(Array.from(state.aiGenerate.selectedMaterials.keys())));
    formData.append('assignment_ids', JSON.stringify(Array.from(state.aiGenerate.selectedAssignments.keys())));
    state.aiGenerate.files.forEach((entry) => {
        formData.append('new_files', entry.file, entry.file.name);
    });
    if (state.currentParentId) {
        formData.append('parent_id', String(state.currentParentId));
    }

    setAiGenerateBusy(true);
    setAiGenerateStatus('AI 正在深度整理提示与关联附件，完成后会保存成新材料...', 'info');
    try {
        const result = await apiFetch('/api/materials/ai-generate', {
            method: 'POST',
            body: formData,
        });
        closeModal('materials-ai-generate-modal');
        showToast(result.message || 'AI 材料已生成', 'success', 5200);
        await loadLibrary(state.currentParentId, false);
        if (result.material?.id) {
            await loadMaterialDetail(result.material.id);
            openDetailModal();
        }
        if (result.viewer_url) {
            window.open(result.viewer_url, '_blank', 'noopener');
        }
    } catch (error) {
        setAiGenerateStatus(error.message || 'AI 材料生成失败', 'error');
        throw error;
    } finally {
        setAiGenerateBusy(false);
    }
}

function setAiRewriteStatus(message = '', type = 'info') {
    if (!refs.aiRewriteStatus) return;
    const normalizedMessage = String(message || '').trim();
    refs.aiRewriteStatus.hidden = !normalizedMessage;
    refs.aiRewriteStatus.className = `materials-ai-import-status materials-ai-import-status--${type}`;
    refs.aiRewriteStatus.textContent = normalizedMessage;
}

function setAiRewriteBusy(busy) {
    state.aiRewrite.busy = busy;
    if (refs.aiRewriteSubmitBtn) {
        refs.aiRewriteSubmitBtn.disabled = busy;
        refs.aiRewriteSubmitBtn.textContent = busy ? '处理中...' : '开始处理';
    }
    if (refs.aiRewritePrompt) refs.aiRewritePrompt.disabled = busy;
}

function openAiRewriteModal(mode = 'regenerate') {
    if (!state.activeDetail) return;
    state.aiRewrite.mode = mode;
    state.aiRewrite.materialId = state.activeDetail.id;
    if (refs.aiRewriteTitle) {
        refs.aiRewriteTitle.textContent = mode === 'regenerate' ? 'AI重新生成材料' : 'AI优化材料';
    }
    if (refs.aiRewriteSubtitle) {
        refs.aiRewriteSubtitle.textContent = mode === 'regenerate'
            ? '写下希望调整的方向；留空则基于原材料重新组织并生成新材料。'
            : '留空则保留关键信息并优化表达、层级和格式。';
    }
    if (refs.aiRewritePrompt) refs.aiRewritePrompt.value = '';
    setAiRewriteStatus('', 'info');
    setAiRewriteBusy(false);
    openModal('materials-ai-rewrite-modal');
    window.setTimeout(() => refs.aiRewritePrompt?.focus(), 50);
}

async function submitAiRewrite() {
    if (state.aiRewrite.busy || !state.aiRewrite.materialId) return;
    const materialId = state.aiRewrite.materialId;
    const mode = state.aiRewrite.mode || 'regenerate';
    const prompt = refs.aiRewritePrompt?.value || '';
    setAiRewriteBusy(true);
    setAiRewriteStatus(mode === 'regenerate' ? 'AI 正在重新生成材料...' : 'AI 正在优化材料...', 'info');
    try {
        const result = await apiFetch(`/api/materials/${materialId}/ai-rewrite`, {
            method: 'POST',
            body: { mode, prompt },
        });
        closeModal('materials-ai-rewrite-modal');
        showToast(result.message || 'AI 处理完成', 'success', 5200);
        await loadLibrary(state.currentParentId, false);
        const nextMaterialId = result.material?.id || materialId;
        await loadMaterialDetail(nextMaterialId);
        openDetailModal();
        if (result.viewer_url) {
            window.open(result.viewer_url, '_blank', 'noopener');
        }
    } catch (error) {
        setAiRewriteStatus(error.message || 'AI 处理失败', 'error');
        throw error;
    } finally {
        setAiRewriteBusy(false);
    }
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

    const homeCount = assignments.filter((item) => item.target_type === 'home').length;
    const lessonCount = assignments.length - homeCount;
    refs.aiAssignSummary.textContent = homeCount
        ? `成功识别 ${homeCount} 个首页文档，并绑定 ${lessonCount} 个课次文档`
        : `成功绑定 ${lessonCount} 个文档到课次`;
    refs.aiAssignList.innerHTML = assignments.map((item) => {
        const confidence = String(item.confidence || 'medium').toLowerCase();
        const confidenceLabel = confidence === 'high' ? '高' : (confidence === 'low' ? '低' : '中');
        const pathFull = item.material_path || '';
        const pathShort = pathFull ? pathFull.split('/').slice(-2).join('/') : '';
        const orderIdx = item.order_index || 0;
        const sessionTitle = item.session_title || '';
        const isHome = item.target_type === 'home';
        return `
            <div class="materials-ai-assign-item">
                <span class="materials-ai-assign-path" title="${escapeHtml(pathFull)}">${escapeHtml(pathShort)}</span>
                <span class="materials-ai-assign-arrow">&rarr;</span>
                <span class="materials-ai-assign-session">
                    <strong>${isHome ? '首页' : `第${escapeHtml(String(orderIdx))}课`}</strong>
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
    await loadMaterialDetail(result.material?.id || materialId);
    if (result.viewer_url) {
        window.open(result.viewer_url, '_blank', 'noopener');
    }
}

async function updateActiveMaterialScope(scopeLevel) {
    if (!state.activeDetail || state.activeDetail.can_manage === false) return;
    const normalizedScope = ['private', 'department', 'school'].includes(scopeLevel) ? scopeLevel : 'private';
    const result = await apiFetch(`/api/materials/${state.activeDetail.id}/scope`, {
        method: 'PATCH',
        body: { scope_level: normalizedScope },
    });
    showToast(result.message || '材料开放范围已更新', 'success');
    await loadLibrary(state.currentParentId);
    await loadMaterialDetail(state.activeDetail.id);
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

function getReadmeCandidateId(candidate) {
    return Number(candidate?.material_id || candidate?.id || 0);
}

function getReadmeCandidatePath(candidate) {
    return String(candidate?.relative_path || candidate?.material_path || candidate?.name || 'README.md');
}

function renderRepositoryAutoBindAssignments(assignments = []) {
    if (!assignments.length) {
        return '<div class="text-muted text-sm materials-repo-autobind-result">AI 没有返回可绑定结果。</div>';
    }

    return `
        <div class="materials-ai-assign-list-scroll materials-repo-autobind-result">
            ${assignments.map((item) => {
                const confidence = String(item.confidence || 'medium').toLowerCase();
                const confidenceLabel = confidence === 'high' ? '高' : (confidence === 'low' ? '低' : '中');
                const isHome = item.target_type === 'home';
                const pathFull = item.material_path || '';
                const pathShort = pathFull ? pathFull.split('/').slice(-2).join('/') : 'README.md';
                const classroom = [item.course_name, item.class_name].filter(Boolean).join(' / ');
                return `
                    <div class="materials-ai-assign-item">
                        <span class="materials-ai-assign-path" title="${escapeHtml(pathFull)}">${escapeHtml(pathShort)}</span>
                        <span class="materials-ai-assign-arrow">&rarr;</span>
                        <span class="materials-ai-assign-session">
                            <strong>${isHome ? '首页' : `第${escapeHtml(String(item.order_index || ''))}次课`}</strong>
                            ${classroom ? `<span class="materials-ai-assign-session-title">${escapeHtml(classroom)}</span>` : ''}
                        </span>
                        <span class="materials-ai-confidence materials-ai-confidence--${escapeHtml(confidence)}">${escapeHtml(confidenceLabel)}</span>
                    </div>
                `;
            }).join('')}
        </div>
    `;
}

function renderRepositoryAutoBindPanel() {
    if (!refs.repositoryAutoBindPanel || !refs.repositoryAutoBindList || !refs.repositoryAutoBindSummary) return;
    const candidates = Array.isArray(state.repository.autoBindCandidates)
        ? state.repository.autoBindCandidates.filter((item) => getReadmeCandidateId(item) > 0)
        : [];
    const result = state.repository.autoBindResult;

    if (!candidates.length && !result) {
        refs.repositoryAutoBindPanel.hidden = true;
        return;
    }

    refs.repositoryAutoBindPanel.hidden = false;
    if (result) {
        refs.repositoryAutoBindSummary.textContent = result.message || '自动绑定已完成';
        refs.repositoryAutoBindList.innerHTML = renderRepositoryAutoBindAssignments(result.assignments || []);
    } else {
        refs.repositoryAutoBindSummary.textContent = `发现 ${candidates.length} 个 README`;
        refs.repositoryAutoBindList.innerHTML = candidates.map((candidate) => {
            const status = candidate.change_status === 'inserted' ? '新增' : '更新';
            const path = getReadmeCandidatePath(candidate);
            return `
                <div class="materials-repo-autobind-item">
                    <span class="materials-type-pill">${escapeHtml(status)}</span>
                    <strong title="${escapeHtml(path)}">${escapeHtml(path)}</strong>
                    <span class="text-muted text-sm">README.md</span>
                </div>
            `;
        }).join('');
    }

    if (refs.repositoryAutoBindRunBtn) {
        refs.repositoryAutoBindRunBtn.disabled = state.repository.busy
            || state.repository.autoBindBusy
            || !candidates.length
            || Boolean(result);
        refs.repositoryAutoBindRunBtn.textContent = state.repository.autoBindBusy ? 'AI 识别中...' : 'AI 识别并绑定';
    }
    if (refs.repositoryAutoBindDismissBtn) {
        refs.repositoryAutoBindDismissBtn.disabled = state.repository.autoBindBusy;
        refs.repositoryAutoBindDismissBtn.hidden = Boolean(result);
    }
}

function setRepositoryAutoBindBusy(busy) {
    state.repository.autoBindBusy = busy;
    renderRepositoryAutoBindPanel();
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
    if (refs.repositoryAutoBindRunBtn) {
        refs.repositoryAutoBindRunBtn.disabled = busy
            || state.repository.autoBindBusy
            || !(state.repository.autoBindCandidates || []).length
            || Boolean(state.repository.autoBindResult);
    }
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
    renderRepositoryAutoBindPanel();
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
    state.repository.autoBindBusy = false;
    state.repository.autoBindCandidates = [];
    state.repository.autoBindResult = null;
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
        state.repository.autoBindResult = null;
        state.repository.autoBindCandidates = (
            action === 'update' && result.status === 'success' && Array.isArray(result.readme_candidates)
        )
            ? result.readme_candidates
            : [];
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
        if (state.repository.autoBindCandidates.length) {
            showToast(`发现 ${state.repository.autoBindCandidates.length} 个 README，可确认后自动绑定到已分配课堂`, 'info', 5200);
            renderRepositoryAutoBindPanel();
        }
    } catch (error) {
        state.repository.lastStatus = '执行失败';
        state.repository.lastOutput = error.message || '暂无输出';
        renderRepositoryModal();
        showToast(error.message || '仓库操作失败', 'error');
    } finally {
        setRepositoryBusy(false, state.repository.lastStatus);
    }
}

async function runRepositoryAutoBind() {
    if (!state.repository.materialId) return;
    const candidateIds = (state.repository.autoBindCandidates || [])
        .map(getReadmeCandidateId)
        .filter((id) => id > 0);
    if (!candidateIds.length) {
        showToast('没有可自动绑定的 README 候选', 'warning');
        return;
    }

    setRepositoryAutoBindBusy(true);
    try {
        const result = await apiFetch(`/api/materials/${state.repository.materialId}/repository/auto-bind-readmes`, {
            method: 'POST',
            body: { candidate_material_ids: candidateIds },
            silent: true,
        });
        state.repository.autoBindResult = result;
        state.repository.autoBindCandidates = [];
        renderRepositoryAutoBindPanel();
        showToast(result.message || 'README 自动绑定完成', 'success', 5200);
        await refreshRepositoryAffectedViews();
        await refreshRepositoryState();
        renderRepositoryAutoBindPanel();
    } catch (error) {
        showToast(error.message || 'README 自动绑定失败，请稍后重试或手动绑定', 'error');
    } finally {
        setRepositoryAutoBindBusy(false);
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
    document.addEventListener('click', (event) => {
        const uploadTrigger = event.target.closest('#materials-upload-menu-btn');
        if (uploadTrigger) {
            event.preventDefault();
            event.stopPropagation();
            setUploadMenuOpen(refs.uploadDropdown?.hidden !== false);
            return;
        }

        const directUpload = event.target.closest('#materials-upload-direct-btn');
        if (directUpload) {
            event.preventDefault();
            event.stopPropagation();
            setUploadMenuOpen(false);
            refs.fileInput?.click();
            return;
        }

        const aiImportOpen = event.target.closest('#materials-ai-import-open-btn');
        if (aiImportOpen) {
            event.preventDefault();
            event.stopPropagation();
            setUploadMenuOpen(false);
            openAiImportModal();
        }
    }, true);

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

    refs.uploadMenuBtn?.addEventListener('click', (event) => {
        event.stopPropagation();
        setUploadMenuOpen(refs.uploadDropdown?.hidden !== false);
    });
    refs.directUploadBtn?.addEventListener('click', () => {
        setUploadMenuOpen(false);
        refs.fileInput?.click();
    });
    refs.aiImportOpenBtn?.addEventListener('click', () => {
        setUploadMenuOpen(false);
        openAiImportModal();
    });
    refs.aiGenerateOpenBtn?.addEventListener('click', () => {
        openAiGenerateModal();
    });
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

    const reloadForLibraryFilter = (message) => {
        loadLibrary(state.currentParentId, false).catch((error) => {
            showToast(error.message || message, 'error');
        });
    };

    refs.scopeFilter?.addEventListener('change', () => {
        state.filters.scopeLevel = normalizeScopeFilter(refs.scopeFilter.value);
        reloadForLibraryFilter('筛选材料失败');
    });

    refs.schoolFilter?.addEventListener('change', () => {
        state.filters.school = normalizeKeyword(refs.schoolFilter.value);
        reloadForLibraryFilter('筛选材料失败');
    });

    refs.departmentFilter?.addEventListener('change', () => {
        state.filters.department = normalizeKeyword(refs.departmentFilter.value);
        reloadForLibraryFilter('筛选材料失败');
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
        if (action === 'ai-regenerate') {
            openAiRewriteModal('regenerate');
            return;
        }
        if (action === 'delete') {
            deleteActiveMaterial().catch((error) => {
                showToast(error.message || '删除材料失败', 'error');
            });
        }
    });

    refs.aiImportGroup?.addEventListener('change', () => {
        renderAiImportTypes();
    });

    refs.aiImportChooseFileBtn?.addEventListener('click', () => {
        if (!state.aiImport.busy) {
            refs.aiImportFileInput?.click();
        }
    });

    refs.aiImportFileInput?.addEventListener('change', () => {
        state.aiImport.file = refs.aiImportFileInput.files?.[0] || null;
        updateAiImportFileLabel();
        setAiImportStatus('', 'info');
    });

    refs.aiImportSubmitBtn?.addEventListener('click', () => {
        submitAiImport().catch((error) => {
            showToast(error.message || 'AI 解析导入失败', 'error');
        });
    });

    refs.aiGenerateUploadBtn?.addEventListener('click', () => {
        if (!state.aiGenerate.busy) refs.aiGenerateFileInput?.click();
    });

    refs.aiGenerateFileInput?.addEventListener('change', () => {
        addAiGenerateFiles(refs.aiGenerateFileInput.files);
        refs.aiGenerateFileInput.value = '';
    });

    refs.aiGenerateSelected?.addEventListener('click', (event) => {
        const button = event.target.closest('[data-ai-generate-remove]');
        if (!button) return;
        removeAiGenerateAttachment(button.dataset.aiGenerateRemove, button.dataset.id);
    });

    refs.aiGenerateUploadList?.addEventListener('click', (event) => {
        const button = event.target.closest('[data-ai-generate-remove]');
        if (!button) return;
        removeAiGenerateAttachment(button.dataset.aiGenerateRemove, button.dataset.id);
    });

    refs.aiGenerateMaterialList?.addEventListener('click', (event) => {
        const button = event.target.closest('[data-ai-generate-add="material"]');
        if (!button) return;
        selectAiGenerateCandidate('material', button.dataset.id);
    });

    refs.aiGenerateAssignmentList?.addEventListener('click', (event) => {
        const button = event.target.closest('[data-ai-generate-add="assignment"]');
        if (!button) return;
        selectAiGenerateCandidate('assignment', button.dataset.id);
    });

    refs.aiGenerateMaterialQuery?.addEventListener('input', () => triggerAiGenerateCandidateSearch('material'));
    refs.aiGenerateAssignmentQuery?.addEventListener('input', () => triggerAiGenerateCandidateSearch('assignment'));

    refs.aiGenerateGroup?.addEventListener('change', () => {
        updateAiGenerateTypeOptions();
    });

    refs.aiGenerateType?.addEventListener('change', () => {
        updateAiGeneratePromptPlaceholder();
    });

    refs.aiGenerateSubmitBtn?.addEventListener('click', () => {
        submitAiGenerate().catch((error) => {
            showToast(error.message || 'AI 材料生成失败', 'error');
        });
    });

    refs.aiRewriteSubmitBtn?.addEventListener('click', () => {
        submitAiRewrite().catch((error) => {
            showToast(error.message || 'AI 材料处理失败', 'error');
        });
    });

    refs.detail?.addEventListener('change', (event) => {
        const select = event.target.closest('[data-material-scope-select]');
        if (!select) return;
        updateActiveMaterialScope(select.value).catch((error) => {
            showToast(error.message || '开放范围更新失败', 'error');
            loadMaterialDetail(state.activeDetail?.id).catch(() => {});
        });
    });

    refs.breadcrumbs?.addEventListener('click', (event) => {
        const button = event.target.closest('[data-crumb-id]');
        if (!button) return;
        loadLibrary(Number(button.dataset.crumbId), true).catch((error) => {
            showToast(error.message || '打开目录失败', 'error');
        });
    });

    refs.listBody?.addEventListener('click', (event) => {
        const taskActionButton = event.target.closest('[data-ai-import-action]');
        if (taskActionButton) {
            event.preventDefault();
            event.stopPropagation();
            const taskId = Number(taskActionButton.dataset.aiImportTaskId || 0);
            const task = state.aiImport.tasks.get(taskId);
            const action = taskActionButton.dataset.aiImportAction;
            if (action === 'dismiss') {
                removeAiImportTask(taskId);
                return;
            }
            if (action === 'open-package' && task?.package_material_id) {
                openMaterialDetail(task.package_material_id).catch((error) => {
                    showToast(error.message || '加载材料包失败', 'error');
                });
                return;
            }
            if (action === 'view-doc' && task?.parsed_material_id) {
                window.open(`/materials/view/${task.parsed_material_id}`, '_blank', 'noopener');
            }
            return;
        }

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

    refs.repositoryAutoBindRunBtn?.addEventListener('click', () => {
        runRepositoryAutoBind().catch((error) => {
            showToast(error.message || 'README 自动绑定失败', 'error');
        });
    });

    refs.repositoryAutoBindDismissBtn?.addEventListener('click', () => {
        state.repository.autoBindCandidates = [];
        state.repository.autoBindResult = null;
        renderRepositoryAutoBindPanel();
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
        if (event.key === 'Escape') {
            setUploadMenuOpen(false);
        }
        if (event.key === 'Escape' && isDetailModalOpen()) {
            closeDetailModal();
        }
    });

    document.addEventListener('click', (event) => {
        if (refs.uploadMenu && !refs.uploadMenu.contains(event.target)) {
            setUploadMenuOpen(false);
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
