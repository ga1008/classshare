import { apiFetch } from './api.js';
import { closeModal, escapeHtml, formatDate, formatSize, getFileIcon, openModal, renderMarkdown, showToast } from './ui.js';
import { enhancePromptPoolInputs, recordPromptForInput } from './prompt_pool.js';
import { openProcessMaterialConfirm } from './process_material_modal.js';
import { bindProcessMaterialExportDownloadActions } from './process_material_editor_preview.js';
import {
    getLearningDocumentUrl,
    getMaterialPreviewUrl,
    getMaterialPrimaryAction,
    getMaterialTypeLabel,
    getRenderLabel,
    getRenderUrl,
    getRepositoryVisualMeta,
    hasLearningDocument,
    isGitRepository,
    isRenderable,
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

const DOCUMENT_TYPE_LABELS = {
    assessment_plan: '课程考核计划表',
    grading_rubric: '课程考核评分细则',
    exam_paper: '课程考核试卷',
    ordinary_grade_record: '学生平时成绩记录表',
    exam_grade_record: '考核登分表',
};

const CLASSROOM_GENERATION_HINTS = {
    ordinary_grade_record: '选择课堂后在当前页面确认 3 份平时作业和 1 份测评；系统会读取真实提交、评分与考勤数据生成 Excel。',
    exam_grade_record: '进入课堂后请选择已绑定试卷且有大题分值的考试；系统会读取考试成绩生成 Excel。',
};

const SEARCH_DEBOUNCE_MS = 280;
const AI_IMPORT_POLL_INTERVAL_MS = 3500;
const AI_IMPORT_ACTIVE_STATUSES = new Set(['queued', 'running']);
const AI_IMPORT_TERMINAL_STATUSES = new Set(['completed', 'failed', 'ai_failed', 'quality_failed', 'unsupported']);
const AI_IMPORT_DISMISSED_TASK_LIMIT = 80;
const AI_GENERATE_MAX_ATTACHMENTS = 10;
const AI_GENERATE_SEARCH_DEBOUNCE_MS = 260;

function normalizeKeyword(value) {
    return String(value || '')
        .replace(/\s+/g, ' ')
        .trim()
        .slice(0, 100);
}

function compactStatusText(value, fallback = '', maxLength = 280) {
    const text = String(value || fallback || '')
        .replace(/\s+/g, ' ')
        .trim();
    return maxLength > 0 ? text.slice(0, maxLength) : text;
}

async function recordMaterialPromptBestEffort(input, prompt) {
    try {
        await recordPromptForInput(input, prompt);
    } catch (_) {
        /* prompt pool recording is best effort */
    }
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
    return ['all', 'owned', 'shared', 'private', 'department', 'college', 'school', 'public'].includes(scope) ? scope : 'all';
}

function normalizeDocumentTypeFilter(value) {
    const normalized = String(value || '').trim().toLowerCase();
    if (!normalized || normalized.length > 80) return '';
    const allowed = 'abcdefghijklmnopqrstuvwxyz0123456789_:-';
    return [...normalized].every((char) => allowed.includes(char)) ? normalized : '';
}

function getDocumentTypeLabel(value) {
    const documentType = normalizeDocumentTypeFilter(value);
    return DOCUMENT_TYPE_LABELS[documentType] || documentType;
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
    const initial = window.MATERIALS_MANAGE_CONFIG?.initialLibraryFilter || {};
    const sortBy = normalizeSortBy(params.get('sort_by'));
    const initialKeyword = normalizeKeyword(params.get('keyword'));
    return {
        parentId: parsePositiveInt(params.get('parent_id')),
        keyword: shouldIgnoreInitialKeyword(initialKeyword) ? '' : initialKeyword,
        documentType: normalizeDocumentTypeFilter(
            params.get('document_type')
            || params.get('type')
            || initial.document_type
            || initial.type
        ),
        scopeLevel: normalizeScopeFilter(params.get('scope_level')),
        school: normalizeKeyword(params.get('school')),
        department: normalizeKeyword(params.get('department')),
        college: normalizeKeyword(params.get('college')),
        course: normalizeKeyword(params.get('course')),
        className: normalizeKeyword(params.get('class_name')),
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
    materialWorkspace: {
        root: null,
        stats: null,
        expandedIds: new Set(),
        selectedId: null,
        treeLoading: false,
        treeRequestId: 0,
        content: {
            materialId: null,
            text: '',
            originalText: '',
            encoding: 'utf-8',
            loading: false,
            dirty: false,
            error: '',
            requestId: 0,
        },
    },
    selectedIds: new Set(),
    currentFolder: null,
    currentBreadcrumbs: [],
    filters: {
        keyword: initialLibraryState.keyword,
        documentType: initialLibraryState.documentType,
        scopeLevel: initialLibraryState.scopeLevel,
        school: initialLibraryState.school,
        department: initialLibraryState.department,
        college: initialLibraryState.college,
        course: initialLibraryState.course,
        className: initialLibraryState.className,
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
        dismissedTaskStateKeys: new Map(),
        knownTaskStates: new Map(),
        pollTimer: 0,
        loadRequestId: 0,
    },
    aiGenerate: {
        busy: false,
        blockedReason: '',
        sourceBlockReason: '',
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
        materialName: '',
    },
    createNode: {
        type: 'folder',
        busy: false,
        parentId: null,
        fromWorkspace: false,
    },
    move: {
        materialId: null,
        materialName: '',
        busy: false,
    },
    bind: {
        materialId: null,
        busy: false,
        offerings: [],
        selected: new Set(),
    },
    aiExpand: {
        busy: false,
    },
    ordinaryGradeGenerate: {
        offering: null,
        candidates: [],
        attendanceFreshness: {},
        selections: [null, null, null, null],
        activeStep: 0,
        loading: false,
        busy: false,
        error: '',
    },
    recentGeneratedMaterialId: null,
    recentGeneratedHighlightArmed: false,
    // 本地 AI 处理中的提示卡（优化 / 润色 / 续写），key → {label, message, tone}
    aiPending: new Map(),
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

const config = window.MATERIALS_MANAGE_CONFIG || {
    offerings: [],
    canAssign: false,
    userId: '',
    aiImportDismissStorageKey: '',
    materialAiImportRegistry: [],
    initialAiGenerate: {},
    initialAiImport: {},
    initialLibraryFilter: {},
};

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
    aiImportShortcutBtn: document.getElementById('materials-ai-import-shortcut-btn'),
    classroomGenerateOpenBtn: document.getElementById('materials-classroom-generate-open-btn'),
    classroomGenerateModal: document.getElementById('materials-classroom-generate-modal'),
    classroomGenerateTitle: document.getElementById('materials-classroom-generate-title'),
    classroomGenerateSubtitle: document.getElementById('materials-classroom-generate-subtitle'),
    classroomGenerateStatus: document.getElementById('materials-classroom-generate-status'),
    classroomGenerateList: document.getElementById('materials-classroom-generate-list'),
    classroomGenerateSemesterFilter: document.getElementById('materials-classroom-semester-filter'),
    classroomGenerateSearch: document.getElementById('materials-classroom-search'),
    classroomGenerateCount: document.getElementById('materials-classroom-picker-count'),
    classroomGeneratePickerStage: document.getElementById('materials-classroom-picker-stage'),
    classroomGenerateBackBtn: document.getElementById('materials-classroom-generate-back-btn'),
    classroomGenerateSubmitBtn: document.getElementById('materials-classroom-generate-submit-btn'),
    ordinaryGradeWizard: document.getElementById('materials-ordinary-grade-wizard'),
    ordinaryGradeClassroomName: document.getElementById('materials-ordinary-grade-classroom-name'),
    ordinaryAttendanceFreshness: document.getElementById('materials-ordinary-attendance-freshness'),
    ordinaryGradeStepCards: Array.from(document.querySelectorAll('[data-materials-ordinary-step-index]')),
    ordinaryGradeSelectionDetails: Array.from(document.querySelectorAll('[data-materials-ordinary-selection-detail]')),
    ordinaryGradeProgressSteps: Array.from(document.querySelectorAll('[data-materials-ordinary-progress-step]')),
    ordinaryGradePicker: document.getElementById('materials-ordinary-grade-picker'),
    ordinaryGradePickerClose: document.getElementById('materials-ordinary-grade-picker-close'),
    ordinaryGradePickerKicker: document.getElementById('materials-ordinary-grade-picker-kicker'),
    ordinaryGradePickerTitle: document.getElementById('materials-ordinary-grade-picker-title'),
    ordinaryGradePickerSearch: document.getElementById('materials-ordinary-grade-picker-search'),
    ordinaryGradePickerList: document.getElementById('materials-ordinary-grade-picker-list'),
    ordinaryScoreFloorEnabled: document.getElementById('materials-ordinary-score-floor-enabled'),
    ordinaryScoreFloorInput: document.getElementById('materials-ordinary-score-floor'),
    ordinaryScoreFloorSummary: document.getElementById('materials-ordinary-score-floor-summary'),
    ordinaryGradePrompt: document.getElementById('materials-ordinary-grade-prompt'),
    ordinaryGradeStatus: document.getElementById('materials-ordinary-grade-status'),
    processClassroomGenerateBtn: document.querySelector('[data-process-classroom-generate]'),
    processAiImportBtn: document.querySelector('[data-process-ai-import]'),
    folderBtn: document.getElementById('materials-upload-folder-btn'),
    fileInput: document.getElementById('materials-file-input'),
    folderInput: document.getElementById('materials-folder-input'),
    aiImportModal: document.getElementById('materials-ai-import-modal'),
    aiImportGroup: document.getElementById('materials-ai-import-group'),
    aiImportType: document.getElementById('materials-ai-import-type'),
    aiImportFileInput: document.getElementById('materials-ai-import-file-input'),
    aiImportChooseFileBtn: document.getElementById('materials-ai-import-choose-file-btn'),
    aiImportFileName: document.getElementById('materials-ai-import-file-name'),
    aiImportFormatHint: document.getElementById('materials-ai-import-format-hint'),
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
    aiRewriteStrictnessField: document.getElementById('materials-ai-rewrite-strictness-field'),
    aiRewriteStrictness: document.getElementById('materials-ai-rewrite-strictness'),
    aiRewriteOfferingField: document.getElementById('materials-ai-rewrite-offering-field'),
    aiRewriteOffering: document.getElementById('materials-ai-rewrite-offering'),
    createMenu: document.getElementById('materials-create-menu'),
    createMenuBtn: document.getElementById('materials-create-menu-btn'),
    createDropdown: document.getElementById('materials-create-dropdown'),
    createFolderBtn: document.getElementById('materials-create-folder-btn'),
    createFileBtn: document.getElementById('materials-create-file-btn'),
    createNodeTitle: document.getElementById('materials-create-node-title'),
    createNodeSubtitle: document.getElementById('materials-create-node-subtitle'),
    createNodeLocation: document.getElementById('materials-create-node-location'),
    createNodeName: document.getElementById('materials-create-node-name'),
    createNodeNameLabel: document.getElementById('materials-create-node-name-label'),
    createNodeHint: document.getElementById('materials-create-node-hint'),
    createNodeStatus: document.getElementById('materials-create-node-status'),
    createNodeSubmitBtn: document.getElementById('materials-create-node-submit-btn'),
    moveName: document.getElementById('materials-move-name'),
    movePath: document.getElementById('materials-move-path'),
    moveTarget: document.getElementById('materials-move-target'),
    moveStatus: document.getElementById('materials-move-status'),
    moveSubmitBtn: document.getElementById('materials-move-submit-btn'),
    bindName: document.getElementById('materials-bind-name'),
    bindOffering: document.getElementById('materials-bind-offering'),
    bindTargets: document.getElementById('materials-bind-targets'),
    bindCount: document.getElementById('materials-bind-count'),
    bindStatus: document.getElementById('materials-bind-status'),
    bindSaveBtn: document.getElementById('materials-bind-save-btn'),
    aiExpandBtn: document.getElementById('materials-ai-expand-btn'),
    aiExpandFolder: document.getElementById('materials-ai-expand-folder'),
    aiExpandFolderPath: document.getElementById('materials-ai-expand-folder-path'),
    aiExpandPrompt: document.getElementById('materials-ai-expand-prompt'),
    aiExpandStatus: document.getElementById('materials-ai-expand-status'),
    aiExpandSubmitBtn: document.getElementById('materials-ai-expand-submit-btn'),
    searchInput: document.getElementById('materials-search-input'),
    searchClearBtn: document.getElementById('materials-search-clear-btn'),
    scopeFilter: document.getElementById('materials-scope-filter'),
    schoolFilter: document.getElementById('materials-school-filter'),
    departmentFilter: document.getElementById('materials-department-filter'),
    collegeFilter: document.getElementById('materials-college-filter'),
    courseFilter: document.getElementById('materials-course-filter'),
    classFilter: document.getElementById('materials-class-filter'),
    sortBy: document.getElementById('materials-sort-by'),
    sortOrder: document.getElementById('materials-sort-order'),
    scopeName: document.getElementById('materials-scope-name'),
    scopePath: document.getElementById('materials-scope-path'),
    scopeDescription: document.getElementById('materials-scope-description'),
    resultCount: document.getElementById('materials-result-count'),
    sortSummary: document.getElementById('materials-sort-summary'),
    searchSummary: document.getElementById('materials-search-summary'),
    documentTypeSummary: document.getElementById('materials-document-type-summary'),
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

const DEFAULT_AI_IMPORT_ACCEPT = refs.aiImportFileInput?.getAttribute('accept') || '';

if (refs.detail && refs.detailModalBody && refs.detail.parentElement !== refs.detailModalBody) {
    refs.detailModalBody.appendChild(refs.detail);
}

function getInitialAiGeneratePreset() {
    const params = new URLSearchParams(window.location.search);
    const initial = config.initialAiGenerate && typeof config.initialAiGenerate === 'object'
        ? config.initialAiGenerate
        : {};
    const openValue = String(params.get('open') || params.get('action') || '').trim();
    const shouldOpen = Boolean(initial.open)
        || openValue === 'ai-generate'
        || openValue === 'generate'
        || params.get('ai_generate') === '1';
    const documentGroup = normalizeKeyword(
        params.get('document_group')
        || params.get('group')
        || initial.document_group
        || initial.group
    );
    const documentType = normalizeKeyword(
        params.get('document_type')
        || params.get('type')
        || initial.document_type
        || initial.type
    );
    if (!shouldOpen && !documentGroup && !documentType) {
        return null;
    }
    return {
        open: shouldOpen,
        document_group: documentGroup,
        document_type: documentType,
        prompt: normalizeKeyword(params.get('prompt') || initial.prompt || ''),
        status: normalizeKeyword(initial.status || ''),
        blocked: Boolean(initial.blocked),
    };
}

function getInitialAiImportPreset() {
    const params = new URLSearchParams(window.location.search);
    const initial = config.initialAiImport && typeof config.initialAiImport === 'object'
        ? config.initialAiImport
        : {};
    const openValue = String(params.get('open') || params.get('action') || '').trim();
    const shouldOpen = Boolean(initial.open)
        || openValue === 'ai-import'
        || openValue === 'import'
        || params.get('ai_import') === '1';
    const documentGroup = normalizeKeyword(
        params.get('import_document_group')
        || params.get('document_group')
        || params.get('group')
        || initial.document_group
        || initial.group
    );
    const documentType = normalizeKeyword(
        params.get('import_document_type')
        || params.get('document_type')
        || params.get('type')
        || initial.document_type
        || initial.type
    );
    if (!shouldOpen && !documentGroup && !documentType && !initial.status) {
        return null;
    }
    return {
        open: shouldOpen,
        document_group: documentGroup,
        document_type: documentType,
        status: normalizeKeyword(initial.status || ''),
    };
}

function getProcessGeneratePolicy() {
    const initial = config.initialAiGenerate && typeof config.initialAiGenerate === 'object'
        ? config.initialAiGenerate
        : {};
    if (!initial.blocked) return null;
    const documentType = normalizeDocumentTypeFilter(initial.document_type || initial.type);
    return {
        document_group: normalizeKeyword(initial.document_group || initial.group || 'final_material'),
        document_type: documentType,
        label: getDocumentTypeLabel(documentType) || '过程材料',
        status: compactStatusText(initial.status, '该表需要从真实课堂数据生成，不能在材料库中泛化生成。'),
    };
}

function classroomWorkspaceGenerateUrl(offering, documentType) {
    const id = Number(offering?.id || 0);
    if (!id) return '#';
    const params = new URLSearchParams();
    params.set('open_final_material', '1');
    if (documentType) params.set('final_material_type', documentType);
    return `/classroom/${id}?${params.toString()}#materials-panel`;
}

function offeringLabel(offering) {
    return [offering?.course_name, offering?.class_name].filter(Boolean).join(' · ') || `课堂 ${offering?.id || ''}`;
}

function offeringMeta(offering) {
    return [offering?.semester, offering?.school_name, offering?.college].filter(Boolean).join(' / ');
}

function offeringSemesterKey(offering) {
    const semesterId = Number(offering?.semester_id || 0);
    if (semesterId > 0) return `id:${semesterId}`;
    const label = normalizeKeyword(offering?.semester || '');
    return label ? `label:${label}` : 'unset';
}

function compactFuzzyText(value) {
    return String(value || '')
        .normalize('NFKC')
        .toLocaleLowerCase('zh-CN')
        .replace(/[\s·•—–_\-/（）()【】[\]，,。.：:；;]+/g, '');
}

function fuzzyTextMatches(haystack, query) {
    const source = compactFuzzyText(haystack);
    const needle = compactFuzzyText(query);
    if (!needle) return true;
    if (source.includes(needle)) return true;
    let cursor = 0;
    for (const char of source) {
        if (char === needle[cursor]) cursor += 1;
        if (cursor >= needle.length) return true;
    }
    return false;
}

function resetOrdinaryGradeGeneration() {
    state.ordinaryGradeGenerate.offering = null;
    state.ordinaryGradeGenerate.candidates = [];
    state.ordinaryGradeGenerate.attendanceFreshness = {};
    state.ordinaryGradeGenerate.selections = [null, null, null, null];
    state.ordinaryGradeGenerate.activeStep = 0;
    state.ordinaryGradeGenerate.loading = false;
    state.ordinaryGradeGenerate.busy = false;
    state.ordinaryGradeGenerate.error = '';
    if (refs.ordinaryGradePicker) refs.ordinaryGradePicker.hidden = true;
    if (refs.ordinaryGradePickerSearch) refs.ordinaryGradePickerSearch.value = '';
    if (refs.ordinaryGradePrompt) refs.ordinaryGradePrompt.value = '';
    if (refs.ordinaryScoreFloorEnabled) refs.ordinaryScoreFloorEnabled.checked = true;
    if (refs.ordinaryScoreFloorInput) refs.ordinaryScoreFloorInput.value = '60';
    if (refs.ordinaryGradeStatus) {
        refs.ordinaryGradeStatus.hidden = true;
        refs.ordinaryGradeStatus.textContent = '';
        refs.ordinaryGradeStatus.className = 'classroom-final-material-status';
        delete refs.ordinaryGradeStatus.dataset.statusKind;
    }
}

function ordinaryGradeCandidateBuckets() {
    const candidates = state.ordinaryGradeGenerate.candidates || [];
    return {
        homework: candidates.filter((item) => item?.kind !== 'exam'),
        assessment: candidates.filter((item) => item?.kind === 'exam'),
    };
}

function ordinaryGradeStepCandidates(stepIndex) {
    const buckets = ordinaryGradeCandidateBuckets();
    return Number(stepIndex) === 3 ? buckets.assessment : buckets.homework;
}

function ordinaryGradeCandidateById(candidateId) {
    return (state.ordinaryGradeGenerate.candidates || [])
        .find((item) => Number(item?.id || 0) === Number(candidateId || 0)) || null;
}

function ordinaryGradeAttendanceLabel() {
    const freshness = state.ordinaryGradeGenerate.attendanceFreshness || {};
    if (freshness.is_fresh) {
        return `考勤已同步于 ${freshness.last_synced_at_display || '刚刚'}，生成时使用 30 分钟缓存`;
    }
    if (freshness.last_synced_at_display) {
        return `考勤上次同步于 ${freshness.last_synced_at_display}，生成前将自动刷新`;
    }
    return '考勤尚未同步，生成前将自动连接智慧课堂刷新';
}

function ordinaryGradeMinimumScore() {
    const raw = String(refs.ordinaryScoreFloorInput?.value ?? '').trim();
    return raw === '' ? Number.NaN : Number(raw);
}

function getManageOrdinaryGradeReadiness() {
    const generation = state.ordinaryGradeGenerate;
    if (!generation.offering) return { ready: false, message: '请先选择课堂。' };
    if (generation.loading) return { ready: false, message: '正在读取当前课堂的作业和测评...' };
    if (generation.error) return { ready: false, message: generation.error };
    const buckets = ordinaryGradeCandidateBuckets();
    if (buckets.homework.length < 3 || buckets.assessment.length < 1) {
        return {
            ready: false,
            message: `当前课堂需要 3 份作业和 1 份测评，目前识别到 ${buckets.homework.length} 份作业、${buckets.assessment.length} 份测评。可先在课堂或试卷管理页调整“平时成绩用途”。`,
        };
    }
    const selectedIds = generation.selections.map((value) => Number(value || 0));
    if (selectedIds.some((value) => value <= 0)) {
        return { ready: false, message: '请依次选择 3 份平时作业和 1 份测评。' };
    }
    if (new Set(selectedIds).size !== 4) {
        return { ready: false, message: '三次作业和一次测评不能重复。' };
    }
    if (refs.ordinaryScoreFloorEnabled?.checked) {
        const score = ordinaryGradeMinimumScore();
        if (!Number.isFinite(score) || score < 0 || score > 100) {
            return { ready: false, message: '最低平时分必须在 0 到 100 之间。' };
        }
    }
    return { ready: true, message: '' };
}

function setManageOrdinaryGradeStatus(message = '', tone = '') {
    if (!refs.ordinaryGradeStatus) return;
    refs.ordinaryGradeStatus.hidden = !message;
    refs.ordinaryGradeStatus.textContent = message;
    refs.ordinaryGradeStatus.className = 'classroom-final-material-status';
    if (tone) {
        refs.ordinaryGradeStatus.dataset.statusKind = tone;
    } else {
        delete refs.ordinaryGradeStatus.dataset.statusKind;
    }
}

function renderManageOrdinaryGradePicker() {
    if (!refs.ordinaryGradePicker || refs.ordinaryGradePicker.hidden) return;
    const generation = state.ordinaryGradeGenerate;
    const stepIndex = Number(generation.activeStep || 0);
    const keyword = refs.ordinaryGradePickerSearch?.value || '';
    const selectedIds = generation.selections.map((value) => Number(value || 0));
    const usedByOtherStep = new Map();
    selectedIds.forEach((candidateId, index) => {
        if (candidateId > 0 && index !== stepIndex) usedByOtherStep.set(candidateId, index);
    });
    const items = ordinaryGradeStepCandidates(stepIndex).filter((item) => fuzzyTextMatches([
        item?.title,
        item?.kind === 'exam' ? '考试 测评 测验' : '作业',
        item?.status,
        item?.graded_count,
        item?.submission_count,
        item?.average_score,
    ].filter((value) => value !== null && value !== undefined).join(' '), keyword));
    if (refs.ordinaryGradePickerKicker) refs.ordinaryGradePickerKicker.textContent = `第 ${stepIndex + 1} 步`;
    if (refs.ordinaryGradePickerTitle) {
        refs.ordinaryGradePickerTitle.textContent = stepIndex === 3 ? '选择课堂测评' : `选择平时作业 ${stepIndex + 1}`;
    }
    if (!refs.ordinaryGradePickerList) return;
    if (!items.length) {
        refs.ordinaryGradePickerList.innerHTML = `
            <div class="ordinary-grade-picker__empty">
                <strong>没有匹配的${stepIndex === 3 ? '测评' : '作业'}</strong>
                <span>清空关键词重试，或在课堂任务卡片、教师试卷管理页调整“平时成绩用途”。</span>
            </div>
        `;
        return;
    }
    refs.ordinaryGradePickerList.innerHTML = items.map((item) => {
        const candidateId = Number(item?.id || 0);
        const usedStep = usedByOtherStep.get(candidateId);
        const isCurrent = selectedIds[stepIndex] === candidateId;
        const disabled = usedStep !== undefined;
        const average = item?.average_score === null || item?.average_score === undefined
            ? '暂无均分'
            : `均分 ${item.average_score}`;
        const source = item?.ordinary_grade_kind_source === 'manual' ? '手动指定' : '自动识别';
        return `
            <button
                type="button"
                class="ordinary-grade-candidate${isCurrent ? ' is-selected' : ''}${disabled ? ' is-disabled' : ''}"
                data-materials-ordinary-candidate-id="${escapeHtml(String(candidateId))}"
                ${disabled ? 'disabled' : ''}
            >
                <span class="ordinary-grade-candidate__main">
                    <strong>${escapeHtml(item?.title || `作业 ${candidateId}`)}</strong>
                    <small>${escapeHtml(item?.kind === 'exam' ? '测评 / 考试' : '平时作业')} · ${escapeHtml(source)} · 已评分 ${escapeHtml(String(item?.graded_count || 0))}/${escapeHtml(String(item?.submission_count || 0))} · ${escapeHtml(average)}</small>
                </span>
                <span class="ordinary-grade-candidate__usage">${escapeHtml(disabled ? `已用于第 ${usedStep + 1} 步` : (isCurrent ? '当前已选择' : '选择此来源'))}</span>
            </button>
        `;
    }).join('');
}

function renderManageOrdinaryGradeWizard() {
    const generation = state.ordinaryGradeGenerate;
    const selectedIds = generation.selections.map((value) => Number(value || 0));
    if (refs.ordinaryGradeClassroomName) {
        refs.ordinaryGradeClassroomName.textContent = offeringLabel(generation.offering);
    }
    if (refs.ordinaryAttendanceFreshness) {
        refs.ordinaryAttendanceFreshness.textContent = ordinaryGradeAttendanceLabel();
        refs.ordinaryAttendanceFreshness.classList.toggle('is-fresh', Boolean(generation.attendanceFreshness?.is_fresh));
    }
    refs.ordinaryGradeStepCards.forEach((card, stepIndex) => {
        const candidate = ordinaryGradeCandidateById(selectedIds[stepIndex]);
        const detail = refs.ordinaryGradeSelectionDetails[stepIndex];
        card.classList.toggle('is-selected', Boolean(candidate));
        card.classList.toggle('is-active', !refs.ordinaryGradePicker?.hidden && generation.activeStep === stepIndex);
        if (detail) {
            detail.textContent = candidate
                ? `${candidate.title} · 已评分 ${candidate.graded_count || 0}/${candidate.submission_count || 0}${candidate.average_score === null || candidate.average_score === undefined ? '' : ` · 均分 ${candidate.average_score}`}`
                : (stepIndex === 3 ? '尚未选择，将只显示测评、测试或考试' : '尚未选择，点击查看当前课堂作业');
        }
        const action = card.querySelector('.ordinary-grade-step-card__action');
        if (action) action.textContent = candidate ? '更换' : '选择';
    });
    refs.ordinaryGradeProgressSteps.forEach((item, stepIndex) => {
        const complete = stepIndex < 4 ? selectedIds[stepIndex] > 0 : selectedIds.every((value) => value > 0);
        item.classList.toggle('is-complete', complete);
        item.classList.toggle(
            'is-active',
            stepIndex < 4
                ? (!refs.ordinaryGradePicker?.hidden && generation.activeStep === stepIndex)
                : selectedIds.every((value) => value > 0),
        );
    });
    const floorEnabled = Boolean(refs.ordinaryScoreFloorEnabled?.checked);
    const floorScore = ordinaryGradeMinimumScore();
    if (refs.ordinaryScoreFloorInput) refs.ordinaryScoreFloorInput.disabled = !floorEnabled;
    if (refs.ordinaryScoreFloorSummary) {
        refs.ordinaryScoreFloorSummary.textContent = !floorEnabled
            ? '已关闭最低分保护：所有学生均按真实出勤、作业和测评成绩计算。'
            : Number.isFinite(floorScore) && floorScore >= 0 && floorScore <= 100
                ? `出勤率达到 70% 的学生，若公式平时分低于 ${floorScore} 分，系统只上调所选作业和测评；出勤率保持真实。`
                : '请输入 0 到 100 之间的最低平时分。';
    }
    const readiness = getManageOrdinaryGradeReadiness();
    if (!generation.busy) setManageOrdinaryGradeStatus(readiness.ready ? '' : readiness.message, readiness.ready ? '' : 'blocking');
    if (refs.classroomGenerateSubmitBtn) {
        refs.classroomGenerateSubmitBtn.disabled = generation.busy || !readiness.ready;
        refs.classroomGenerateSubmitBtn.textContent = generation.busy
            ? '正在生成...'
            : (readiness.ready ? '生成并保存' : '请先补齐来源');
        refs.classroomGenerateSubmitBtn.title = readiness.message || '';
    }
    refs.classroomGenerateModal?.querySelectorAll('[data-dismiss="modal"], .modal-close').forEach((button) => {
        button.disabled = generation.busy;
    });
    if (refs.classroomGenerateBackBtn) refs.classroomGenerateBackBtn.disabled = generation.busy;
    renderManageOrdinaryGradePicker();
}

function openManageOrdinaryGradePicker(stepIndex) {
    state.ordinaryGradeGenerate.activeStep = Math.max(0, Math.min(3, Number(stepIndex || 0)));
    if (refs.ordinaryGradePicker) refs.ordinaryGradePicker.hidden = false;
    if (refs.ordinaryGradePickerSearch) refs.ordinaryGradePickerSearch.value = '';
    renderManageOrdinaryGradeWizard();
    window.setTimeout(() => refs.ordinaryGradePickerSearch?.focus(), 60);
}

function selectManageOrdinaryGradeCandidate(candidateId) {
    const generation = state.ordinaryGradeGenerate;
    const stepIndex = Number(generation.activeStep || 0);
    const numericId = Number(candidateId || 0);
    if (numericId <= 0) return;
    if (generation.selections.some((value, index) => index !== stepIndex && Number(value || 0) === numericId)) {
        showToast('这份来源已用于其他步骤，请选择另一份作业或测评。', 'warning');
        return;
    }
    generation.selections[stepIndex] = numericId;
    const nextIncomplete = generation.selections.findIndex((value, index) => index > stepIndex && !value);
    if (nextIncomplete >= 0) {
        openManageOrdinaryGradePicker(nextIncomplete);
        return;
    }
    if (refs.ordinaryGradePicker) refs.ordinaryGradePicker.hidden = true;
    renderManageOrdinaryGradeWizard();
    if (stepIndex === 3) {
        refs.ordinaryGradePrompt?.focus();
        refs.ordinaryGradePrompt?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
}

async function openManageOrdinaryGradeWizard(offering) {
    const offeringId = Number(offering?.id || 0);
    if (!offeringId) return;
    resetOrdinaryGradeGeneration();
    state.ordinaryGradeGenerate.offering = offering;
    state.ordinaryGradeGenerate.loading = true;
    if (refs.classroomGeneratePickerStage) refs.classroomGeneratePickerStage.hidden = true;
    if (refs.ordinaryGradeWizard) refs.ordinaryGradeWizard.hidden = false;
    if (refs.classroomGenerateBackBtn) refs.classroomGenerateBackBtn.hidden = false;
    if (refs.classroomGenerateSubmitBtn) refs.classroomGenerateSubmitBtn.hidden = false;
    if (refs.classroomGenerateStatus) refs.classroomGenerateStatus.hidden = true;
    refs.classroomGenerateModal?.querySelector('.materials-classroom-generate-dialog')?.classList.add('is-wizard');
    refs.classroomGenerateTitle.textContent = '生成学生平时成绩记录表';
    refs.classroomGenerateSubtitle.textContent = `${offeringLabel(offering)} · 生成完成后仍停留在当前页面`;
    renderManageOrdinaryGradeWizard();
    try {
        const data = await apiFetch(`/api/classrooms/${offeringId}/ordinary-grade-record/candidates`, { silent: true });
        state.ordinaryGradeGenerate.candidates = Array.isArray(data.items) ? data.items : [];
        state.ordinaryGradeGenerate.attendanceFreshness = data.attendance_sync || {};
        state.ordinaryGradeGenerate.error = '';
        const buckets = ordinaryGradeCandidateBuckets();
        if (buckets.homework.length >= 3 && buckets.assessment.length >= 1) {
            openManageOrdinaryGradePicker(0);
        }
    } catch (error) {
        state.ordinaryGradeGenerate.error = error.message || '读取作业和测评失败，请稍后重试。';
    } finally {
        state.ordinaryGradeGenerate.loading = false;
        renderManageOrdinaryGradeWizard();
    }
}

function returnToClassroomGeneratePicker() {
    if (state.ordinaryGradeGenerate.busy) return;
    resetOrdinaryGradeGeneration();
    if (refs.classroomGeneratePickerStage) refs.classroomGeneratePickerStage.hidden = false;
    if (refs.ordinaryGradeWizard) refs.ordinaryGradeWizard.hidden = true;
    if (refs.classroomGenerateBackBtn) refs.classroomGenerateBackBtn.hidden = true;
    if (refs.classroomGenerateSubmitBtn) refs.classroomGenerateSubmitBtn.hidden = true;
    if (refs.classroomGenerateStatus) refs.classroomGenerateStatus.hidden = false;
    refs.classroomGenerateModal?.querySelector('.materials-classroom-generate-dialog')?.classList.remove('is-wizard');
    renderClassroomGenerateOptions(getProcessGeneratePolicy());
}

async function revealRecentlyGeneratedMaterial(materialId) {
    const normalizedId = Number(materialId || 0);
    if (!normalizedId) return;
    state.recentGeneratedMaterialId = normalizedId;
    state.activeMaterialId = normalizedId;
    await loadLibrary(state.currentParentId, false);
    if (!state.items.some((item) => Number(item.id) === normalizedId)) {
        state.filters.keyword = '';
        state.filters.scopeLevel = 'all';
        state.filters.school = '';
        state.filters.department = '';
        state.filters.college = '';
        state.filters.course = '';
        state.filters.className = '';
        await loadLibrary(state.currentParentId, false);
    }
    window.requestAnimationFrame(() => {
        const row = refs.listBody?.querySelector(`[data-id="${normalizedId}"]`);
        row?.scrollIntoView({ behavior: 'smooth', block: 'center' });
        window.setTimeout(() => {
            state.recentGeneratedHighlightArmed = true;
        }, 350);
    });
}

function clearRecentGeneratedHighlight() {
    if (!state.recentGeneratedHighlightArmed || !state.recentGeneratedMaterialId) return;
    state.recentGeneratedMaterialId = null;
    state.recentGeneratedHighlightArmed = false;
    renderList();
}

async function submitManageOrdinaryGradeGeneration() {
    const generation = state.ordinaryGradeGenerate;
    const readiness = getManageOrdinaryGradeReadiness();
    if (!readiness.ready || generation.busy) {
        setManageOrdinaryGradeStatus(readiness.message || '请先补齐来源。', 'blocking');
        return;
    }
    const offeringId = Number(generation.offering?.id || 0);
    const selectedIds = generation.selections.map((value) => Number(value || 0));
    const prompt = refs.ordinaryGradePrompt?.value || '';
    generation.busy = true;
    renderManageOrdinaryGradeWizard();
    setManageOrdinaryGradeStatus('正在核对考勤缓存、计算成绩并生成 Excel，请勿重复提交...', 'progress');
    try {
        const data = await apiFetch(`/api/classrooms/${offeringId}/final-materials/generate`, {
            method: 'POST',
            body: {
                document_type: 'ordinary_grade_record',
                prompt,
                parent_id: state.currentParentId,
                homework_assignment_ids: selectedIds.slice(0, 3),
                assessment_assignment_id: selectedIds[3],
                minimum_ordinary_score_enabled: Boolean(refs.ordinaryScoreFloorEnabled?.checked),
                minimum_ordinary_score: Number.isFinite(ordinaryGradeMinimumScore()) ? ordinaryGradeMinimumScore() : 60,
            },
        });
        await recordMaterialPromptBestEffort(refs.ordinaryGradePrompt, prompt);
        const materialId = Number(data?.task?.package_material_id || 0);
        closeModal('materials-classroom-generate-modal');
        resetOrdinaryGradeGeneration();
        showToast(data.message || '平时成绩记录表已生成', 'success', 5200);
        await revealRecentlyGeneratedMaterial(materialId);
    } catch (error) {
        setManageOrdinaryGradeStatus(error.message || '生成失败，请核对课堂来源后重试。', 'error');
        showToast(error.message || '生成失败，请稍后重试。', 'error');
    } finally {
        generation.busy = false;
        renderManageOrdinaryGradeWizard();
    }
}

function populateClassroomSemesterFilter() {
    if (!refs.classroomGenerateSemesterFilter) return;
    const offerings = Array.isArray(config.offerings) ? config.offerings : [];
    const previous = refs.classroomGenerateSemesterFilter.value;
    const semesters = [];
    const seen = new Set();
    offerings.forEach((offering) => {
        const key = offeringSemesterKey(offering);
        if (seen.has(key)) return;
        seen.add(key);
        semesters.push({
            key,
            label: offering?.semester || '未设置学期',
            start: offering?.semester_start_date || '',
        });
    });
    semesters.sort((left, right) => String(right.start).localeCompare(String(left.start), 'zh-CN'));
    refs.classroomGenerateSemesterFilter.innerHTML = [
        '<option value="">全部学期</option>',
        ...semesters.map((item) => (
            `<option value="${escapeHtml(item.key)}">${escapeHtml(item.label)}</option>`
        )),
    ].join('');
    if (previous && seen.has(previous)) refs.classroomGenerateSemesterFilter.value = previous;
}

function renderClassroomGenerateOptions(policy) {
    if (!refs.classroomGenerateList || !refs.classroomGenerateStatus) return;
    const offerings = Array.isArray(config.offerings) ? config.offerings : [];
    const semesterKey = refs.classroomGenerateSemesterFilter?.value || '';
    const keyword = refs.classroomGenerateSearch?.value || '';
    const filteredOfferings = offerings.filter((offering) => {
        if (semesterKey && offeringSemesterKey(offering) !== semesterKey) return false;
        const searchText = [
            offeringLabel(offering),
            offeringMeta(offering),
            offering?.semester_start_date,
            offering?.semester_end_date,
        ].filter(Boolean).join(' ');
        return fuzzyTextMatches(searchText, keyword);
    });
    refs.classroomGenerateTitle.textContent = `从课堂生成${policy?.label || '过程材料'}`;
    refs.classroomGenerateSubtitle.textContent = CLASSROOM_GENERATION_HINTS[policy?.document_type]
        || '进入课堂后在课程材料区生成，会自动带入真实课堂上下文。';
    refs.classroomGenerateStatus.innerHTML = `
        <div class="text-muted text-sm">生成规则</div>
        <strong>${escapeHtml(policy?.status || '该表需要从真实课堂数据生成。')}</strong>
    `;
    if (!offerings.length) {
        refs.classroomGenerateList.innerHTML = '<div class="materials-empty">暂无可用课堂，请先在“开设课堂”中创建或同步教学班级。</div>';
        if (refs.classroomGenerateCount) refs.classroomGenerateCount.textContent = '0 个课堂';
        return;
    }
    if (refs.classroomGenerateCount) {
        refs.classroomGenerateCount.textContent = `显示 ${filteredOfferings.length} / ${offerings.length} 个课堂`;
    }
    if (!filteredOfferings.length) {
        refs.classroomGenerateList.innerHTML = `
            <div class="materials-empty materials-classroom-picker-empty">
                <strong>没有匹配的课堂</strong>
                <span>可以更换学期，或用课程名、班级名的部分文字重新搜索。</span>
            </div>
        `;
        return;
    }
    refs.classroomGenerateList.innerHTML = filteredOfferings.map((offering) => {
        const label = offeringLabel(offering);
        const meta = offeringMeta(offering) || '选择课堂后继续配置';
        const isOrdinaryGrade = policy?.document_type === 'ordinary_grade_record';
        const homeworkCount = Number(offering?.ordinary_homework_count || 0);
        const assessmentCount = Number(offering?.ordinary_assessment_count || 0);
        const sourceReady = Boolean(offering?.ordinary_grade_ready);
        const sourceStatus = sourceReady
            ? `来源齐全 · ${homeworkCount} 份作业 / ${assessmentCount} 份测评`
            : `当前 ${homeworkCount} 份作业 / ${assessmentCount} 份测评 · 可进入检查`;
        const content = `
                <div>
                    <strong>${escapeHtml(label)}</strong>
                    <small>${escapeHtml(meta)}</small>
                    ${isOrdinaryGrade ? `<small class="materials-classroom-source-status">${escapeHtml(sourceStatus)}</small>` : ''}
                </div>
                <span>${escapeHtml(isOrdinaryGrade && !sourceReady ? '检查来源' : '开始生成')}</span>
        `;
        if (isOrdinaryGrade) {
            return `
                <button type="button" class="materials-modal-option ${sourceReady ? 'is-source-ready' : 'is-source-missing'}" data-materials-ordinary-offering-id="${escapeHtml(String(offering.id))}">
                    ${content}
                </button>
            `;
        }
        const href = classroomWorkspaceGenerateUrl(offering, policy?.document_type || '');
        return `<a class="materials-modal-option" href="${escapeHtml(href)}">${content}</a>`;
    }).join('');
}

function openClassroomGenerateModal() {
    const policy = getProcessGeneratePolicy();
    if (!policy) {
        showToast('当前页面无需从课堂数据生成。', 'info');
        return;
    }
    resetOrdinaryGradeGeneration();
    if (refs.classroomGeneratePickerStage) refs.classroomGeneratePickerStage.hidden = false;
    if (refs.ordinaryGradeWizard) refs.ordinaryGradeWizard.hidden = true;
    if (refs.classroomGenerateBackBtn) refs.classroomGenerateBackBtn.hidden = true;
    if (refs.classroomGenerateSubmitBtn) refs.classroomGenerateSubmitBtn.hidden = true;
    if (refs.classroomGenerateStatus) refs.classroomGenerateStatus.hidden = false;
    refs.classroomGenerateModal?.querySelector('.materials-classroom-generate-dialog')?.classList.remove('is-wizard');
    populateClassroomSemesterFilter();
    renderClassroomGenerateOptions(policy);
    openModal('materials-classroom-generate-modal');
    window.setTimeout(() => refs.classroomGenerateSearch?.focus(), 80);
}

function applyProcessGenerateBlockIfNeeded() {
    const policy = getProcessGeneratePolicy();
    if (!policy) return false;
    state.aiGenerate.blockedReason = policy.status;
    if (refs.aiGenerateGroup) refs.aiGenerateGroup.value = policy.document_group || 'final_material';
    updateAiGenerateTypeOptions();
    setAiGenerateStatus(`${policy.label}必须从真实课堂数据生成，或上传学校模板 Excel 解析。`, 'warning');
    updateAiGenerateSubmitState();
    return true;
}

function applyAiGeneratePreset(preset) {
    if (!preset || !refs.aiGenerateGroup || !refs.aiGenerateType) return;
    state.aiGenerate.blockedReason = '';
    const desiredGroup = String(preset.document_group || preset.group || '').trim();
    const desiredType = String(preset.document_type || preset.type || '').trim();
    const groupOption = desiredGroup
        ? Array.from(refs.aiGenerateGroup.options || []).find((option) => option.value === desiredGroup)
        : null;
    if (groupOption) {
        refs.aiGenerateGroup.value = desiredGroup;
    }
    updateAiGenerateTypeOptions();
    let typeApplied = false;
    if (desiredType) {
        const typeOption = Array.from(refs.aiGenerateType.options || []).find((option) => (
            option.value === desiredType
            && !option.disabled
            && !option.hidden
            && (option.dataset.group || 'teaching_material') === (refs.aiGenerateGroup.value || 'teaching_material')
        ));
        if (typeOption) {
            refs.aiGenerateType.value = desiredType;
            updateAiGeneratePromptPlaceholder();
            typeApplied = true;
        }
    }
    const prompt = String(preset.prompt || '').trim();
    if (prompt && refs.aiGeneratePrompt) {
        refs.aiGeneratePrompt.value = prompt;
    }
    if (applyProcessGenerateBlockIfNeeded()) {
        return;
    }
    if (preset.blocked && desiredType && !typeApplied) {
        state.aiGenerate.blockedReason = preset.status || '当前材料类型不能在材料库中泛化生成。';
        setAiGenerateStatus(state.aiGenerate.blockedReason, 'warning');
        updateAiGenerateSubmitState();
    } else if (preset.status) {
        setAiGenerateStatus(preset.status, 'info');
    }
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
    renderMaterialFacetOptions(refs.collegeFilter, state.facets?.colleges || [], state.filters.college, '全部学院');
    renderMaterialFacetOptions(refs.courseFilter, state.facets?.courses || [], state.filters.course, '全部课程');
    renderMaterialFacetOptions(refs.classFilter, state.facets?.classes || [], state.filters.className, '全部班级');
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

    const documentTypeLabel = overview.document_type_label || getDocumentTypeLabel(state.filters.documentType);
    if (documentTypeLabel && refs.documentTypeSummary) {
        refs.documentTypeSummary.hidden = false;
        refs.documentTypeSummary.textContent = `类型：${documentTypeLabel}`;
    } else if (refs.documentTypeSummary) {
        refs.documentTypeSummary.hidden = true;
        refs.documentTypeSummary.textContent = '';
    }

    const filterLabels = [];
    if (state.filters.school) filterLabels.push(`学校：${state.filters.school}`);
    if (state.filters.department) filterLabels.push(`系部：${state.filters.department}`);
    if (state.filters.college) filterLabels.push(`学院：${state.filters.college}`);
    if (state.filters.course) filterLabels.push(`课程：${state.filters.course}`);
    if (state.filters.className) filterLabels.push(`班级：${state.filters.className}`);

    if (overview.search_active || filterLabels.length) {
        refs.searchSummary.hidden = false;
        refs.searchSummary.textContent = [
            overview.search_active ? `搜索：${overview.search_keyword || state.filters.keyword}` : '',
            ...filterLabels,
        ].filter(Boolean).join(' · ');
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
    if (refs.aiExpandBtn) {
        refs.aiExpandBtn.hidden = !(state.currentFolder && state.currentFolder.can_manage !== false);
    }
}

function renderNavigationState() {
    if (refs.backBtn) refs.backBtn.disabled = state.history.length === 0;
    if (refs.upBtn) refs.upBtn.disabled = state.currentBreadcrumbs.length === 0;
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

function renderAiPendingCards() {
    if (!state.aiPending.size) return '';
    return Array.from(state.aiPending.entries()).map(([key, task]) => {
        const tone = task.tone || 'info';
        const active = tone === 'info';
        const dismissAction = active
            ? ''
            : `<button type="button" class="btn btn-ghost btn-sm" data-ai-pending-dismiss="${escapeHtml(key)}">关闭</button>`;
        return `
            <section class="materials-ai-task-card is-${tone}" data-ai-pending-key="${escapeHtml(key)}">
                <div class="materials-ai-task-indicator" aria-hidden="true">${active ? '<span></span>' : ''}</div>
                <div class="materials-ai-task-main">
                    <div class="materials-ai-task-head">
                        <span class="materials-ai-task-status">${escapeHtml(task.statusLabel || (active ? '处理中' : '已结束'))}</span>
                        <strong>${escapeHtml(task.label || 'AI 正在处理材料')}</strong>
                    </div>
                    <p>${escapeHtml(task.message || 'AI 正在深度思考，完成后会自动刷新列表。')}</p>
                </div>
                <div class="materials-ai-task-actions">${dismissAction}</div>
            </section>
        `;
    }).join('');
}

function addAiPendingTask(key, label, message) {
    state.aiPending.set(key, { label, message, tone: 'info', statusLabel: '处理中' });
    renderList();
}

function finishAiPendingTask(key, { success, label, message }) {
    if (!state.aiPending.has(key)) return;
    if (success) {
        state.aiPending.delete(key);
    } else {
        state.aiPending.set(key, { label, message, tone: 'danger', statusLabel: '失败' });
    }
    renderList();
}

function renderList() {
    const aiTaskCards = renderAiPendingCards() + renderAiImportTaskCards();
    if (!state.items.length && !aiTaskCards) {
        const documentTypeLabel = getDocumentTypeLabel(state.filters.documentType);
        const emptyText = state.filters.keyword
            ? `未找到与“${escapeHtml(state.filters.keyword)}”匹配的材料，请尝试简化关键词或清空搜索。`
            : (documentTypeLabel ? `当前范围暂无“${escapeHtml(documentTypeLabel)}”资源。` : '当前目录暂无材料。');
        refs.listBody.innerHTML = `<div class="materials-empty">${emptyText}</div>`;
        updateSelectionBar();
        return;
    }

    const rowsHtml = state.items.map((item) => {
        const visualMeta = getVisualMeta(item);
        const activeClass = Number(item.id) === Number(state.activeMaterialId) ? 'is-active' : '';
        const selectedClass = state.selectedIds.has(Number(item.id)) ? 'is-selected' : '';
        const generatedClass = Number(item.id) === Number(state.recentGeneratedMaterialId) ? 'is-generated-highlight' : '';
        const generatedBadge = generatedClass ? '<span class="materials-generated-badge">刚刚生成</span>' : '';
        const primaryAction = getMaterialPrimaryAction(item);
        const primaryActionHtml = item.node_type === 'folder'
            ? ''
            : `<button type="button" class="btn btn-ghost btn-sm" data-action="${primaryAction.action}">${primaryAction.label}</button>`;
        const aiStatus = item.can_ai_parse ? `<span class="materials-meta-item">AI ${escapeHtml(item.ai_parse_status || 'idle')}</span>` : '';
        const optimizingBadge = item.ai_optimize_status === 'running'
            ? '<span class="materials-meta-item" style="color:#0d9488;">AI 优化中…</span>'
            : '';
        const readmeStatus = hasLearningDocument(item) ? '<span class="materials-meta-item">README</span>' : '';
        const scopeBadge = item.scope_label ? `<span class="materials-meta-item">${escapeHtml(item.scope_label)}</span>` : '';
        const sharedBadge = item.can_manage === false ? '<span class="materials-meta-item">共享材料</span>' : '';
        const documentAction = hasLearningDocument(item)
            ? '<button type="button" class="btn btn-outline btn-sm" data-action="view-doc">文档</button>'
            : '';
        const renderAction = isRenderable(item)
            ? `<button type="button" class="btn btn-outline btn-sm materials-render-btn" data-action="render">${escapeHtml(getRenderLabel(item))}</button>`
            : '';
        const repositoryAction = isGitRepository(item) && item.can_manage !== false
            ? '<button type="button" class="btn btn-outline btn-sm" data-action="repository">仓库</button>'
            : '';
        const repositoryBadge = visualMeta.badge
            ? `<span class="materials-repo-badge" style="--repo-color:${visualMeta.color};">${escapeHtml(visualMeta.badge)}</span>`
            : '';
        const assignedCourses = Array.isArray(item.assigned_course_names) ? item.assigned_course_names.filter(Boolean) : [];
        const assignedClasses = Array.isArray(item.assigned_class_names) ? item.assigned_class_names.filter(Boolean) : [];
        const assignmentMeta = [
            assignedCourses.length
                ? `<span class="materials-meta-item">课程 ${escapeHtml(assignedCourses.slice(0, 2).join(' / '))}${assignedCourses.length > 2 ? ` +${assignedCourses.length - 2}` : ''}</span>`
                : '',
            assignedClasses.length
                ? `<span class="materials-meta-item">班级 ${escapeHtml(assignedClasses.slice(0, 2).join(' / '))}${assignedClasses.length > 2 ? ` +${assignedClasses.length - 2}` : ''}</span>`
                : '',
        ].join('');

        return `
            <div class="materials-row materials-manage-row ${activeClass} ${selectedClass} ${generatedClass}" data-id="${item.id}">
                <div class="materials-row-check">
                    <input type="checkbox" data-role="select-item" data-id="${item.id}" ${state.selectedIds.has(Number(item.id)) ? 'checked' : ''}>
                </div>
                <div class="materials-row-main">
                    <div class="materials-name-cell">
                        <div class="materials-type-icon" style="background:${visualMeta.color}16;color:${visualMeta.color};">${escapeHtml(visualMeta.label)}</div>
                        <div class="materials-name-copy">
                            <strong title="${escapeHtml(item.name)}">${highlightText(item.name, state.filters.keyword)}</strong>
                            <div class="materials-name-badges">${generatedBadge}${repositoryBadge}</div>
                            <span title="${escapeHtml(item.material_path || '')}">${highlightText(item.material_path || '', state.filters.keyword)}</span>
                        </div>
                    </div>
                    <div class="materials-row-meta">
                        <span class="materials-type-pill">${escapeHtml(getMaterialTypeLabel(item))}</span>
                        <span class="materials-meta-item">${escapeHtml(getMetaText(item))}</span>
                        ${item.assignment_count ? `<span class="materials-meta-item">已分配 ${escapeHtml(String(item.assignment_count))} 次</span>` : ''}
                        ${assignmentMeta}
                        ${scopeBadge}
                        ${sharedBadge}
                        ${aiStatus}
                        ${optimizingBadge}
                        ${readmeStatus}
                    </div>
                </div>
                <div class="materials-row-time">
                    <span><strong>创建</strong>${escapeHtml(formatDateLabel(item.created_at))}</span>
                    <span><strong>更新</strong>${escapeHtml(formatDateLabel(item.updated_at || item.created_at))}</span>
                </div>
                <div class="materials-row-actions">
                    <button type="button" class="btn btn-ghost btn-sm" data-resource-attributes data-resource-type="material" data-resource-id="${item.id}">属性</button>
                    ${primaryActionHtml}
                    ${renderAction}
                    ${documentAction}
                    ${repositoryAction}
                    ${item.node_type === 'file' ? '<button type="button" class="btn btn-ghost btn-sm" data-action="download">下载</button>' : ''}
                    <button type="button" class="btn btn-ghost btn-sm" data-action="details">查看</button>
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

function resetMaterialWorkspace() {
    state.materialWorkspace.root = null;
    state.materialWorkspace.stats = null;
    state.materialWorkspace.expandedIds.clear();
    state.materialWorkspace.selectedId = null;
    state.materialWorkspace.treeLoading = false;
    resetWorkspaceContent();
}

function resetWorkspaceContent() {
    const nextRequestId = (state.materialWorkspace.content?.requestId || 0) + 1;
    state.materialWorkspace.content = {
        materialId: null,
        text: '',
        originalText: '',
        encoding: 'utf-8',
        loading: false,
        dirty: false,
        error: '',
        requestId: nextRequestId,
    };
}

function resetWorkspaceContentForDetail(detail) {
    const nextRequestId = (state.materialWorkspace.content?.requestId || 0) + 1;
    state.materialWorkspace.content = {
        materialId: detail?.editable ? Number(detail.id) : null,
        text: '',
        originalText: '',
        encoding: 'utf-8',
        loading: Boolean(detail?.editable),
        dirty: false,
        error: '',
        requestId: nextRequestId,
    };
}

function findTreeNode(materialId, node = state.materialWorkspace.root) {
    if (!node || !materialId) return null;
    if (Number(node.id) === Number(materialId)) return node;
    for (const child of node.children || []) {
        const found = findTreeNode(materialId, child);
        if (found) return found;
    }
    return null;
}

function findTreePath(materialId, node = state.materialWorkspace.root, path = []) {
    if (!node || !materialId) return [];
    const nextPath = [...path, node];
    if (Number(node.id) === Number(materialId)) return nextPath;
    for (const child of node.children || []) {
        const found = findTreePath(materialId, child, nextPath);
        if (found.length) return found;
    }
    return [];
}

function syncWorkspaceSelection(materialId) {
    const selectedId = Number(materialId || 0) || null;
    state.materialWorkspace.selectedId = selectedId;
    if (!selectedId) return;
    const path = findTreePath(selectedId);
    path.forEach((node) => {
        if (node.node_type === 'folder') {
            state.materialWorkspace.expandedIds.add(Number(node.id));
        }
    });
}

function renderWorkspaceStats() {
    const stats = state.materialWorkspace.stats || {};
    return `
        <div class="materials-workspace-stats" aria-label="当前材料包统计">
            <span><strong>${escapeHtml(String(stats.folder_count ?? 0))}</strong>文件夹</span>
            <span><strong>${escapeHtml(String(stats.file_count ?? 0))}</strong>文件</span>
            <span><strong>${escapeHtml(formatSize(stats.total_size || 0))}</strong>总大小</span>
            <span><strong>${escapeHtml(formatDateLabel(stats.latest_updated_at))}</strong>最近更新</span>
        </div>
    `;
}

function renderWorkspaceTopbar(detail) {
    const previewUrl = getMaterialPreviewUrl(detail);
    const optimizedUrl = detail.has_optimized_version ? `/materials/view/${detail.id}?variant=optimized` : '';
    const exportUrl = detail.ai_import_record?.export_url || '';
    const exportPdfUrl = detail.ai_import_record?.export_pdf_url || '';
    const renderPreviewUrl = detail.ai_import_record?.render_preview_url || '';
    const exportLabel = ['ordinary_grade_record', 'exam_grade_record'].includes(detail.ai_import_record?.document_type) ? '导出Excel' : '导出Word';
    const exportDownloadLabel = ['ordinary_grade_record', 'exam_grade_record'].includes(detail.ai_import_record?.document_type) ? 'Excel' : 'Word';
    const canManage = detail.can_manage !== false;
    const isFolder = detail.node_type === 'folder';
    const isBindable = Boolean(detail.is_markdown) || isRenderable(detail);

    return `
        <section class="materials-workspace-top">
            <div class="materials-workspace-actions">
                ${isFolder && canManage ? '<button type="button" class="btn btn-outline btn-sm" data-detail-action="create-folder">新建文件夹</button>' : ''}
                ${isFolder && canManage ? '<button type="button" class="btn btn-outline btn-sm" data-detail-action="create-file">新建文档</button>' : ''}
                ${previewUrl ? `<a href="${escapeHtml(previewUrl)}" class="btn btn-primary btn-sm" target="_blank" rel="noopener">全屏预览</a>` : ''}
                ${isRenderable(detail) ? `<a href="${escapeHtml(getRenderUrl(detail))}" class="btn btn-primary btn-sm" target="_blank" rel="noopener">${escapeHtml(getRenderLabel(detail))}</a>` : ''}
                ${optimizedUrl ? `<a href="${escapeHtml(optimizedUrl)}" class="btn btn-outline btn-sm" target="_blank" rel="noopener">查看优化稿</a>` : ''}
                ${renderPreviewUrl ? `<a href="${escapeHtml(renderPreviewUrl)}" class="btn btn-outline btn-sm" target="_blank" rel="noopener">渲染预览</a>` : ''}
                ${exportUrl ? `<button type="button" class="btn btn-outline btn-sm" data-process-export-url="${escapeHtml(exportUrl)}" data-process-export-label="${escapeHtml(exportDownloadLabel)}">${escapeHtml(exportLabel)}</button>` : ''}
                ${exportPdfUrl ? `<button type="button" class="btn btn-outline btn-sm" data-process-export-url="${escapeHtml(exportPdfUrl)}" data-process-export-label="PDF">导出PDF</button>` : ''}
                ${detail.node_type === 'file' ? `<a href="/materials/download/${detail.id}" class="btn btn-outline btn-sm">下载</a>` : ''}
                ${isGitRepository(detail) && canManage ? '<button type="button" class="btn btn-outline btn-sm" data-detail-action="repository">仓库</button>' : ''}
                <button type="button" class="btn btn-outline btn-sm" data-detail-action="assign" ${config.canAssign ? '' : 'disabled'}>分配课堂</button>
                ${isBindable && canManage && config.canAssign ? '<button type="button" class="btn btn-outline btn-sm" data-detail-action="bind">绑定课次 / 首页</button>' : ''}
                ${canManage ? '<button type="button" class="btn btn-outline btn-sm" data-detail-action="move">移动</button>' : ''}
                <button type="button" class="btn btn-outline btn-sm" data-detail-action="ai-parse" ${canManage && detail.can_ai_parse ? '' : 'disabled'}>AI 解析</button>
                <button type="button" class="btn btn-outline btn-sm" data-detail-action="ai-optimize" ${canManage && detail.can_ai_optimize ? '' : 'disabled'}>AI 优化</button>
                <button type="button" class="btn btn-outline btn-sm" data-detail-action="ai-polish" ${canManage && detail.can_ai_optimize ? '' : 'disabled'}>AI 润色</button>
                <button type="button" class="btn btn-outline btn-sm" data-detail-action="ai-regenerate" ${canManage && detail.can_ai_regenerate ? '' : 'disabled'}>AI 重生成</button>
                ${canManage ? '<button type="button" class="btn btn-danger btn-sm" data-detail-action="delete">删除</button>' : ''}
            </div>
            ${renderWorkspaceStats()}
        </section>
    `;
}

function renderTreeNode(node, depth = 0) {
    const hasChildren = Array.isArray(node.children) && node.children.length > 0;
    const expanded = state.materialWorkspace.expandedIds.has(Number(node.id));
    const selected = Number(node.id) === Number(state.materialWorkspace.selectedId || state.activeDetail?.id);
    const visualMeta = getVisualMeta(node);
    const childHtml = hasChildren && expanded
        ? `<div class="materials-tree-children">${node.children.map((child) => renderTreeNode(child, depth + 1)).join('')}</div>`
        : '';
    return `
        <div class="materials-tree-node" style="--tree-depth:${depth}">
            <div class="materials-tree-row ${selected ? 'is-selected' : ''}" data-node-id="${node.id}">
                <button type="button" class="materials-tree-toggle" data-tree-toggle="${node.id}" ${hasChildren ? '' : 'disabled'} aria-label="${expanded ? '收起' : '展开'}">
                    ${hasChildren ? (expanded ? '-' : '+') : ''}
                </button>
                <button type="button" class="materials-tree-select" data-tree-select="${node.id}" title="${escapeHtml(node.material_path || node.name || '')}">
                    <span class="materials-tree-icon" style="background:${visualMeta.color}16;color:${visualMeta.color};">${escapeHtml(visualMeta.label)}</span>
                    <span class="materials-tree-copy">
                        <strong>${escapeHtml(node.name || '未命名')}</strong>
                        <small>${node.node_type === 'folder' ? `${node.child_count || 0} 项` : formatSize(node.file_size || 0)}</small>
                    </span>
                </button>
            </div>
            ${childHtml}
        </div>
    `;
}

function renderWorkspaceTree() {
    if (state.materialWorkspace.treeLoading && !state.materialWorkspace.root) {
        return '<div class="materials-workspace-loading">正在加载目录树...</div>';
    }
    if (!state.materialWorkspace.root) {
        return '<div class="materials-workspace-empty">暂无目录结构。</div>';
    }
    return `<div class="materials-tree">${renderTreeNode(state.materialWorkspace.root)}</div>`;
}

function renderFolderChildCards(detail) {
    const node = findTreeNode(detail.id);
    const children = node?.children || [];
    if (!children.length) {
        return '<div class="materials-workspace-empty">这个文件夹下暂时没有下一层材料。</div>';
    }
    return `
        <div class="materials-folder-child-grid">
            ${children.map((child) => {
                const visualMeta = getVisualMeta(child);
                return `
                    <button type="button" class="materials-folder-child" data-tree-select="${child.id}">
                        <span class="materials-type-icon" style="background:${visualMeta.color}16;color:${visualMeta.color};">${escapeHtml(visualMeta.label)}</span>
                        <span>
                            <strong>${escapeHtml(child.name || '未命名')}</strong>
                            <small>${escapeHtml(child.node_type === 'folder' ? `${child.child_count || 0} 项` : formatSize(child.file_size || 0))}</small>
                        </span>
                    </button>
                `;
            }).join('')}
        </div>
    `;
}

function renderFolderPreview(detail) {
    const aiSummary = detail.ai_parse_result?.summary || '文件夹用于组织课程材料。选择左侧目录树或下方项目，可继续查看下一层内容。';
    return `
        <section class="materials-workspace-preview-card">
            <div class="materials-section-header">
                <h3>文件夹概览</h3>
                <span class="materials-type-pill">${escapeHtml(getMetaText(detail))}</span>
            </div>
            <div class="materials-folder-summary">
                <div>
                    <strong>${escapeHtml(detail.name || '未命名文件夹')}</strong>
                    <p>${escapeHtml(aiSummary)}</p>
                </div>
                ${hasLearningDocument(detail) ? `<a href="${escapeHtml(getLearningDocumentUrl(detail))}" class="btn btn-outline btn-sm" target="_blank" rel="noopener">查看 README</a>` : ''}
            </div>
            <div class="materials-folder-child-title">下一层内容</div>
            ${renderFolderChildCards(detail)}
        </section>
        ${renderRepositorySummary(detail)}
    `;
}

function renderEditableFilePreview(detail) {
    const content = state.materialWorkspace.content;
    const matches = Number(content.materialId) === Number(detail.id);
    const text = matches ? content.text : '';
    const status = content.error
        ? `<div class="materials-workspace-status is-error">${escapeHtml(content.error)}</div>`
        : (content.loading ? '<div class="materials-workspace-status">正在读取文件内容...</div>' : '');
    const markdownPreview = detail.preview_type === 'markdown' && !content.loading && !content.error
        ? `
            <div class="materials-workspace-rendered">
                <div class="materials-workspace-pane-title">渲染预览</div>
                <div id="materials-workspace-rendered-preview" data-material-rendered-preview></div>
            </div>
        `
        : '';
    return `
        <section class="materials-workspace-preview-card materials-workspace-preview-card--editor">
            <div class="materials-workspace-editor-head">
                <div>
                    <h3>文件内容</h3>
                    <p>${escapeHtml(detail.preview_type === 'markdown' ? 'Markdown 源码与渲染预览同步展示。' : '文本材料可直接在这里编辑保存。')}</p>
                </div>
                <button type="button" class="btn btn-primary btn-sm" data-detail-action="save-content" ${content.dirty && !content.loading ? '' : 'disabled'}>保存内容</button>
            </div>
            ${status}
            <div class="materials-workspace-editor-grid ${markdownPreview ? '' : 'materials-workspace-editor-grid--single'}">
                <label class="materials-workspace-source">
                    <span class="materials-workspace-pane-title">源码</span>
                    <textarea data-material-content-editor spellcheck="false" ${content.loading ? 'disabled' : ''}>${escapeHtml(text)}</textarea>
                </label>
                ${markdownPreview}
            </div>
        </section>
    `;
}

function renderFilePreview(detail) {
    if (detail.editable) {
        return renderEditableFilePreview(detail);
    }
    const previewUrl = getMaterialPreviewUrl(detail);
    if (previewUrl) {
        return `
            <section class="materials-workspace-preview-card materials-workspace-preview-card--frame">
                <div class="materials-section-header">
                    <h3>文件预览</h3>
                    <a href="${escapeHtml(previewUrl)}" target="_blank" rel="noopener">全屏查看</a>
                </div>
                <iframe class="materials-workspace-frame" src="${escapeHtml(previewUrl)}" title="${escapeHtml(detail.name || '材料预览')}"></iframe>
            </section>
        `;
    }
    return `
        <section class="materials-workspace-preview-card">
            <div class="materials-workspace-empty">
                当前文件暂不支持内嵌预览，可下载后查看。
                <div class="mt-3"><a class="btn btn-outline btn-sm" href="/materials/download/${detail.id}">下载文件</a></div>
            </div>
        </section>
    `;
}

function renderWorkspacePreview(detail) {
    return detail.node_type === 'folder' ? renderFolderPreview(detail) : renderFilePreview(detail);
}

function renderScopePropertyControl(detail) {
    const canManage = detail.can_manage !== false;
    const isScopeRoot = detail.parent_id === null || detail.parent_id === undefined;
    const scopeLevel = detail.scope_level || 'private';
    const scopeOptions = [
        ['private', '私有'],
        ['department', '本系部公开'],
        ['college', '本院级公开'],
        ['school', '全校公开'],
        ['public', '完全公开'],
    ];
    if (canManage && isScopeRoot) {
        return `
            <label class="materials-property-field">
                <span>开放范围</span>
                <select class="form-control" data-property-scope>
                    ${scopeOptions.map(([value, label]) => `<option value="${value}" ${scopeLevel === value ? 'selected' : ''}>${label}</option>`).join('')}
                </select>
            </label>
        `;
    }
    return `
        <div class="materials-property-readonly">
            <span>开放范围</span>
            <strong>${escapeHtml(detail.scope_label || '私有')}${isScopeRoot ? '' : ' · 随最外层'}</strong>
        </div>
    `;
}

function renderAiImportDetailSummary(detail) {
    const record = detail?.ai_import_record || null;
    const summary = record?.summary || null;
    if (!record || !summary) return '';
    const fieldItems = Array.isArray(summary.field_items) ? summary.field_items : [];
    const warnings = Array.isArray(summary.warnings) ? summary.warnings : [];
    const exportFormats = Array.isArray(summary.export_formats) ? summary.export_formats.join(' / ') : '';
    const warningCount = Number(summary.warning_count || warnings.length || 0);
    const warningTone = warningCount > 0 ? 'is-warning' : 'is-ok';
    const renderPreviewUrl = record.render_preview_url || '';
    const exportUrl = record.export_url || '';
    const exportPdfUrl = record.export_pdf_url || '';
    const exportLabel = ['ordinary_grade_record', 'exam_grade_record'].includes(record.document_type) ? '导出 Excel' : '导出 Word';
    const exportDownloadLabel = ['ordinary_grade_record', 'exam_grade_record'].includes(record.document_type) ? 'Excel' : 'Word';
    const sourceLabel = summary.source_file_name
        ? `${summary.parse_mode_label || record.parse_mode || '导入解析'} · ${summary.source_file_name}`
        : (summary.parse_mode_label || record.parse_mode || '导入解析');
    const fieldsHtml = fieldItems.length
        ? fieldItems.map((item) => `
            <div>
                <span>${escapeHtml(item.label || item.key || '字段')}</span>
                <strong title="${escapeHtml(item.value || '')}">${escapeHtml(item.value || '-')}</strong>
            </div>
        `).join('')
        : '<p class="materials-ai-import-summary-empty">暂无可展示的关键字段。</p>';
    const warningsHtml = warningCount > 0
        ? `
            <ul class="materials-ai-import-summary-warnings">
                ${warnings.map((item) => `<li>${escapeHtml(item)}</li>`).join('')}
                ${summary.has_more_warnings ? '<li>还有更多警告，请打开渲染预览或导出文档核对。</li>' : ''}
            </ul>
        `
        : '<p class="materials-ai-import-summary-empty">未记录解析警告。</p>';
    return `
        <section class="materials-ai-import-summary">
            <div class="materials-ai-import-summary-head">
                <div>
                    <span>过程材料解析结果</span>
                    <strong>${escapeHtml(summary.document_type_label || record.document_type_label || '过程材料')}</strong>
                </div>
                <em class="${warningTone}">${warningCount > 0 ? `${escapeHtml(String(warningCount))} 条警告` : '可导出'}</em>
            </div>
            <div class="materials-ai-import-summary-meta">
                <div><span>来源</span><strong>${escapeHtml(sourceLabel)}</strong></div>
                <div><span>格式</span><strong>${escapeHtml(exportFormats || '按材料类型')}</strong></div>
                <div><span>质量</span><strong>${escapeHtml(summary.content_quality_label || '未校验')}</strong></div>
                <div><span>完成</span><strong>${escapeHtml(formatDateLabel(summary.updated_at || record.completed_at || record.updated_at))}</strong></div>
            </div>
            <div class="materials-ai-import-summary-fields">
                ${fieldsHtml}
            </div>
            <details class="materials-ai-import-summary-detail" ${warningCount > 0 ? 'open' : ''}>
                <summary>解析警告与核对点</summary>
                ${warningsHtml}
            </details>
            <div class="materials-ai-import-summary-actions">
                ${renderPreviewUrl ? `<a href="${escapeHtml(renderPreviewUrl)}" class="btn btn-outline btn-sm" target="_blank" rel="noopener">渲染预览</a>` : ''}
                ${exportUrl ? `<button type="button" class="btn btn-outline btn-sm" data-process-export-url="${escapeHtml(exportUrl)}" data-process-export-label="${escapeHtml(exportDownloadLabel)}">${escapeHtml(exportLabel)}</button>` : ''}
                ${exportPdfUrl ? `<button type="button" class="btn btn-outline btn-sm" data-process-export-url="${escapeHtml(exportPdfUrl)}" data-process-export-label="PDF">导出 PDF</button>` : ''}
            </div>
        </section>
    `;
}

function renderWorkspaceProperties(detail) {
    const canManage = detail.can_manage !== false;
    const assignments = Array.isArray(detail.assignments) ? detail.assignments.length : 0;
    const aiSummary = detail.ai_parse_result?.summary || '';
    const keywords = detail.ai_parse_result?.keywords?.length ? detail.ai_parse_result.keywords.join('、') : '';
    return `
        <aside class="materials-workspace-properties">
            <div class="materials-section-header">
                <h3>属性</h3>
                ${canManage ? '<button type="button" class="btn btn-primary btn-sm" data-detail-action="save-properties">保存</button>' : ''}
            </div>
            ${renderAiImportDetailSummary(detail)}
            <label class="materials-property-field">
                <span>名称</span>
                <input type="text" class="form-control" data-property-name value="${escapeHtml(detail.name || '')}" maxlength="120" ${canManage ? '' : 'disabled'}>
            </label>
            ${renderScopePropertyControl(detail)}
            <div class="materials-property-readonly">
                <span>路径</span>
                <strong title="${escapeHtml(detail.material_path || '')}">${escapeHtml(detail.material_path || '/')}</strong>
            </div>
            <div class="materials-property-grid">
                <div><span>类型</span><strong>${escapeHtml(getMaterialTypeLabel(detail))}</strong></div>
                <div><span>大小/子项</span><strong>${escapeHtml(getMetaText(detail))}</strong></div>
                <div><span>分配课堂</span><strong>${escapeHtml(String(assignments))}</strong></div>
                <div><span>AI解析</span><strong>${escapeHtml(detail.ai_parse_status || 'idle')}</strong></div>
            </div>
            <div class="materials-property-readonly">
                <span>创建时间</span>
                <strong>${escapeHtml(formatDateLabel(detail.created_at))}</strong>
            </div>
            <div class="materials-property-readonly">
                <span>更新时间</span>
                <strong>${escapeHtml(formatDateLabel(detail.updated_at || detail.created_at))}</strong>
            </div>
            <div class="materials-property-readonly">
                <span>AI 摘要</span>
                <p>${escapeHtml(aiSummary || '暂无 AI 摘要。')}</p>
                ${keywords ? `<small>关键词：${escapeHtml(keywords)}</small>` : ''}
            </div>
            <details class="materials-property-more">
                <summary>解析目录</summary>
                ${renderOutline(detail.ai_parse_result?.outline)}
            </details>
            <details class="materials-property-more">
                <summary>课堂分配</summary>
                ${renderAssignments(detail.assignments || [])}
            </details>
        </aside>
    `;
}

function renderWorkspaceRenderedContent(detail) {
    if (!detail?.editable || detail.preview_type !== 'markdown') return;
    const content = state.materialWorkspace.content;
    if (Number(content.materialId) !== Number(detail.id) || content.loading || content.error) return;
    renderMarkdown('materials-workspace-rendered-preview', content.text || '');
}

function renderDetail(detail) {
    updateDetailModalHeader(detail);
    if (!refs.detail) return;

    if (!detail) {
        const loading = state.materialWorkspace.treeLoading ? '正在加载材料工作区...' : '选择一项材料后，这里会显示目录树、内容预览与属性信息。';
        refs.detail.innerHTML = `<div class="materials-empty">${escapeHtml(loading)}</div>`;
        return;
    }

    syncWorkspaceSelection(detail.id);
    const repositoryMeta = getRepositoryVisualMeta(detail);
    const repositoryBadge = repositoryMeta
        ? `<span class="materials-repo-badge" style="--repo-color:${repositoryMeta.color};">${escapeHtml(repositoryMeta.badge)}</span>`
        : '';
    refs.detail.innerHTML = `
        <div class="materials-workspace-shell">
            ${renderWorkspaceTopbar(detail)}
            <div class="materials-workspace-layout">
                <aside class="materials-workspace-tree-panel">
                    <div class="materials-workspace-panel-head">
                        <span>目录树</span>
                        ${state.materialWorkspace.treeLoading ? '<small>刷新中...</small>' : ''}
                    </div>
                    ${renderWorkspaceTree()}
                </aside>
                <main class="materials-workspace-preview">
                    <div class="materials-workspace-titlebar">
                        <div>
                            <div class="materials-detail-badges">
                                <span class="materials-type-pill">${escapeHtml(getMaterialTypeLabel(detail))}</span>
                                ${repositoryBadge}
                                ${hasLearningDocument(detail) ? '<span class="materials-meta-item">README</span>' : ''}
                            </div>
                            <h3 title="${escapeHtml(detail.name || '')}">${escapeHtml(detail.name || '未命名材料')}</h3>
                            <p title="${escapeHtml(detail.material_path || '')}">${escapeHtml(detail.material_path || '/')}</p>
                        </div>
                    </div>
                    ${renderWorkspacePreview(detail)}
                </main>
                ${renderWorkspaceProperties(detail)}
            </div>
        </div>
    `;
    renderWorkspaceRenderedContent(detail);
}

async function loadMaterialTree(materialId) {
    const requestId = ++state.materialWorkspace.treeRequestId;
    state.materialWorkspace.treeLoading = true;
    renderDetail(state.activeDetail);
    try {
        const data = await apiFetch(`/api/materials/${materialId}/tree`, { silent: true });
        if (requestId !== state.materialWorkspace.treeRequestId) {
            return state.materialWorkspace.root;
        }
        state.materialWorkspace.root = data.tree || null;
        state.materialWorkspace.stats = data.stats || null;
        if (state.materialWorkspace.root) {
            state.materialWorkspace.expandedIds.add(Number(state.materialWorkspace.root.id));
        }
        syncWorkspaceSelection(state.materialWorkspace.selectedId || data.selected_id || materialId);
        return state.materialWorkspace.root;
    } finally {
        if (requestId === state.materialWorkspace.treeRequestId) {
            state.materialWorkspace.treeLoading = false;
            renderDetail(state.activeDetail);
        }
    }
}

async function loadWorkspaceContent(detail) {
    if (!detail?.editable) return;
    const materialId = Number(detail.id);
    const requestId = state.materialWorkspace.content.requestId;
    try {
        const data = await apiFetch(`/api/materials/${materialId}/content`, { silent: true });
        if (
            requestId !== state.materialWorkspace.content.requestId
            || Number(state.activeDetail?.id) !== materialId
        ) {
            return;
        }
        const text = String(data.content || '');
        state.materialWorkspace.content = {
            materialId,
            text,
            originalText: text,
            encoding: data.encoding || 'utf-8',
            loading: false,
            dirty: false,
            error: '',
            requestId,
        };
    } catch (error) {
        if (requestId === state.materialWorkspace.content.requestId) {
            state.materialWorkspace.content.loading = false;
            state.materialWorkspace.content.error = error.message || '文件内容读取失败';
        }
    } finally {
        if (requestId === state.materialWorkspace.content.requestId) {
            renderDetail(state.activeDetail);
        }
    }
}

async function loadMaterialDetail(materialId) {
    const requestId = ++state.detailRequestId;
    state.activeMaterialId = Number(materialId);
    state.materialWorkspace.selectedId = Number(materialId);
    renderList();
    const detail = await apiFetch(`/api/materials/${materialId}`, { silent: true }).then((data) => data.material);
    if (requestId !== state.detailRequestId) {
        return state.activeDetail;
    }
    state.activeDetail = detail;
    resetWorkspaceContentForDetail(detail);
    renderDetail(state.activeDetail);
    loadWorkspaceContent(detail).catch(() => {});
    return state.activeDetail;
}

async function openMaterialDetail(materialId) {
    resetMaterialWorkspace();
    state.activeMaterialId = Number(materialId);
    state.materialWorkspace.selectedId = Number(materialId);
    renderList();
    renderDetail(null);
    openDetailModal();
    await Promise.all([
        loadMaterialTree(materialId),
        loadMaterialDetail(materialId),
    ]);
}

async function selectWorkspaceNode(materialId) {
    const normalizedId = Number(materialId);
    if (!normalizedId) return;
    syncWorkspaceSelection(normalizedId);
    renderDetail(state.activeDetail);
    await loadMaterialDetail(normalizedId);
}

function buildLibraryQuery(parentId) {
    const params = new URLSearchParams();
    if (parentId) {
        params.set('parent_id', String(parentId));
    }
    if (state.filters.keyword) {
        params.set('keyword', state.filters.keyword);
    }
    if (state.filters.documentType) {
        params.set('document_type', state.filters.documentType);
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
    if (state.filters.college) {
        params.set('college', state.filters.college);
    }
    if (state.filters.course) {
        params.set('course', state.filters.course);
    }
    if (state.filters.className) {
        params.set('class_name', state.filters.className);
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
    state.filters.documentType = normalizeDocumentTypeFilter(data.filters?.document_type ?? state.filters.documentType);
    state.filters.scopeLevel = normalizeScopeFilter(data.filters?.scope_level ?? state.filters.scopeLevel);
    state.filters.school = normalizeKeyword(data.filters?.school ?? state.filters.school);
    state.filters.department = normalizeKeyword(data.filters?.department ?? state.filters.department);
    state.filters.college = normalizeKeyword(data.filters?.college ?? state.filters.college);
    state.filters.course = normalizeKeyword(data.filters?.course ?? state.filters.course);
    state.filters.className = normalizeKeyword(data.filters?.class_name ?? state.filters.className);
    state.filters.sortBy = normalizeSortBy(data.filters?.sort_by ?? state.filters.sortBy);
    state.filters.sortOrder = normalizeSortOrder(data.filters?.sort_order ?? state.filters.sortOrder, state.filters.sortBy);
    state.overview = data.overview || null;
    state.facets = data.facets || null;
    state.stats = data.stats || null;

    const activeStillVisible = state.items.some((item) => Number(item.id) === Number(previousActiveId));
    const activeStillInWorkspace = Boolean(previousActiveId && findTreeNode(previousActiveId));
    state.activeMaterialId = activeStillVisible || activeStillInWorkspace ? previousActiveId : null;
    if (!activeStillVisible && !activeStillInWorkspace) {
        state.detailRequestId += 1;
        state.activeDetail = null;
        resetMaterialWorkspace();
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

function renderMaterial(materialId) {
    const item = getCurrentItem(materialId);
    const renderUrl = getRenderUrl(item);
    if (!renderUrl) {
        showToast('当前材料不支持直接渲染', 'warning');
        return;
    }
    window.open(renderUrl, '_blank', 'noopener');
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

function normalizeAiImportExtension(value) {
    const extension = String(value || '').trim().toLowerCase();
    if (!extension) return '';
    if (extension.startsWith('.')) return extension;
    if (extension.includes('/')) return '';
    return `.${extension}`;
}

function normalizeAiImportExtensions(values) {
    const source = Array.isArray(values) ? values : String(values || '').split(',');
    const normalized = [];
    source.forEach((item) => {
        const extension = normalizeAiImportExtension(item);
        if (extension && !normalized.includes(extension)) normalized.push(extension);
    });
    return normalized;
}

function getSelectedAiImportTypeMeta() {
    const group = getSelectedAiImportGroup();
    const types = Array.isArray(group?.types) ? group.types : [];
    const selectedType = refs.aiImportType?.value || types[0]?.key || '';
    return types.find((docType) => docType.key === selectedType) || types[0] || null;
}

function getAiImportAcceptedExtensions(typeMeta = getSelectedAiImportTypeMeta()) {
    const configured = normalizeAiImportExtensions(typeMeta?.accepted_extensions || []);
    if (configured.length) return configured;
    return normalizeAiImportExtensions(typeMeta?.accept || DEFAULT_AI_IMPORT_ACCEPT);
}

function getAiImportAcceptAttribute(typeMeta = getSelectedAiImportTypeMeta()) {
    const configured = String(typeMeta?.accept || '').trim();
    if (configured) return configured;
    const extensions = getAiImportAcceptedExtensions(typeMeta);
    return extensions.length ? extensions.join(',') : DEFAULT_AI_IMPORT_ACCEPT;
}

function getAiImportFileExtension(file) {
    const name = String(file?.name || '').trim();
    const dotIndex = name.lastIndexOf('.');
    return dotIndex >= 0 ? name.slice(dotIndex).toLowerCase() : '';
}

function isAiImportFileAccepted(file, typeMeta = getSelectedAiImportTypeMeta()) {
    if (!file) return true;
    const extensions = getAiImportAcceptedExtensions(typeMeta);
    if (!extensions.length) return true;
    return extensions.includes(getAiImportFileExtension(file));
}

function formatAiImportExtensionList(typeMeta = getSelectedAiImportTypeMeta()) {
    return String(typeMeta?.accepted_format_label || '').trim()
        || getAiImportAcceptedExtensions(typeMeta).join('、')
        || '当前支持格式';
}

function getAiImportFormatMismatchMessage(file, typeMeta = getSelectedAiImportTypeMeta()) {
    const label = String(typeMeta?.label || '该材料').trim();
    const fileName = String(file?.name || '当前文件').trim();
    return `${label}仅支持${formatAiImportExtensionList(typeMeta)}文件，请重新选择。当前文件：${fileName}`;
}

function clearAiImportSelectedFile() {
    state.aiImport.file = null;
    if (refs.aiImportFileInput) refs.aiImportFileInput.value = '';
    updateAiImportFileLabel();
}

function updateAiImportFormatGuide({ preserveStatus = true } = {}) {
    const typeMeta = getSelectedAiImportTypeMeta();
    if (refs.aiImportFileInput) {
        refs.aiImportFileInput.setAttribute('accept', getAiImportAcceptAttribute(typeMeta));
    }
    if (refs.aiImportFormatHint) {
        const hint = String(typeMeta?.format_hint || '').trim();
        const accepted = formatAiImportExtensionList(typeMeta);
        refs.aiImportFormatHint.textContent = hint || `支持${accepted}文件。`;
    }
    if (state.aiImport.file && !isAiImportFileAccepted(state.aiImport.file, typeMeta)) {
        const message = getAiImportFormatMismatchMessage(state.aiImport.file, typeMeta);
        clearAiImportSelectedFile();
        setAiImportStatus(message, 'warning');
        return;
    }
    if (!preserveStatus) setAiImportStatus('', 'info');
}

function renderAiImportTypes(options = {}) {
    const group = getSelectedAiImportGroup();
    const types = Array.isArray(group?.types) ? group.types : [];
    if (!refs.aiImportType) return;
    refs.aiImportType.innerHTML = types.map((docType) => (
        `<option value="${escapeHtml(docType.key)}">${escapeHtml(docType.label)}</option>`
    )).join('');
    updateAiImportFormatGuide({ preserveStatus: options.preserveStatus !== false });
}

function applyAiImportPreset(preset) {
    if (!preset || !refs.aiImportGroup || !refs.aiImportType) return;
    const desiredGroup = String(preset.document_group || preset.group || '').trim();
    const desiredType = String(preset.document_type || preset.type || '').trim();
    if (desiredGroup) {
        const groupOption = Array.from(refs.aiImportGroup.options || [])
            .find((option) => option.value === desiredGroup);
        if (groupOption) {
            refs.aiImportGroup.value = desiredGroup;
            renderAiImportTypes();
        }
    }
    if (desiredType) {
        const typeOption = Array.from(refs.aiImportType.options || [])
            .find((option) => option.value === desiredType);
        if (typeOption) refs.aiImportType.value = desiredType;
    }
    updateAiImportFormatGuide({ preserveStatus: true });
    if (preset.status) setAiImportStatus(preset.status, 'info');
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

function setModalDismissDisabled(modal, disabled) {
    modal?.querySelectorAll('[data-dismiss="modal"]').forEach((button) => {
        if ('disabled' in button) button.disabled = disabled;
        button.setAttribute('aria-disabled', disabled ? 'true' : 'false');
    });
    modal?.classList.toggle('is-busy', Boolean(disabled));
}

function bindBusyModalCloseGuard(modal, isBusy, setStatus, message) {
    modal?.addEventListener('click', (event) => {
        const target = event.target instanceof Element ? event.target : event.target?.parentElement;
        const isCloseAttempt = event.target === modal || Boolean(target?.closest('[data-dismiss="modal"]'));
        if (!isCloseAttempt || !isBusy()) return;
        event.preventDefault();
        event.stopPropagation();
        event.stopImmediatePropagation();
        setStatus(message, 'warning');
    }, true);
}

function bindAiWorkModalCloseGuards() {
    bindBusyModalCloseGuard(
        refs.aiImportModal,
        () => state.aiImport.busy,
        setAiImportStatus,
        '文件正在上传并加入解析队列，请等待完成。',
    );
    bindBusyModalCloseGuard(
        refs.aiGenerateModal,
        () => state.aiGenerate.busy,
        setAiGenerateStatus,
        'AI 正在生成材料，请等待完成。',
    );
    bindBusyModalCloseGuard(
        document.getElementById('materials-ai-expand-modal'),
        () => state.aiExpand.busy,
        (message, type) => setModalStatus(refs.aiExpandStatus, message, type),
        'AI 正在续写材料，请等待任务提交完成。',
    );
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
    setModalDismissDisabled(refs.aiImportModal, busy);
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

function isAiGenerationTask(task) {
    const parseMode = String(task?.parse_mode || '').trim().toLowerCase();
    const extractionMethod = String(task?.extraction_method || '').trim().toLowerCase();
    return ['ai_generated', 'local_fallback'].includes(parseMode) || extractionMethod === 'exam_reverse';
}

function getAiImportTaskStateKey(task) {
    return `${task.id}:${task.parse_status}:${task.updated_at || ''}`;
}

function getAiImportDismissStorageKey() {
    const configured = String(config.aiImportDismissStorageKey || '').trim();
    if (configured) return configured;
    const userKey = String(config.userId || 'anonymous').replace(/[^a-zA-Z0-9_-]/g, '_') || 'anonymous';
    return `lanshare:materials:ai-import:dismissed:${userKey}`;
}

function readAiImportDismissalEntries() {
    try {
        const raw = window.sessionStorage?.getItem(getAiImportDismissStorageKey());
        if (!raw) return [];
        const parsed = JSON.parse(raw);
        if (!Array.isArray(parsed)) return [];
        return parsed
            .map((entry) => ({
                id: Number(entry?.id || 0),
                stateKey: String(entry?.stateKey || ''),
            }))
            .filter((entry) => entry.id > 0 && entry.stateKey)
            .slice(-AI_IMPORT_DISMISSED_TASK_LIMIT);
    } catch (_) {
        return [];
    }
}

function persistAiImportDismissals() {
    try {
        const entries = Array.from(state.aiImport.dismissedTaskStateKeys.entries())
            .slice(-AI_IMPORT_DISMISSED_TASK_LIMIT)
            .map(([id, stateKey]) => ({ id, stateKey }));
        window.sessionStorage?.setItem(getAiImportDismissStorageKey(), JSON.stringify(entries));
    } catch (_) {
        // Session storage is an enhancement; task cards still work without it.
    }
}

function hydrateAiImportDismissals() {
    state.aiImport.dismissedTaskIds.clear();
    state.aiImport.dismissedTaskStateKeys.clear();
    readAiImportDismissalEntries().forEach((entry) => {
        state.aiImport.dismissedTaskIds.add(entry.id);
        state.aiImport.dismissedTaskStateKeys.set(entry.id, entry.stateKey);
    });
}

function rememberAiImportTaskDismissal(task) {
    if (!task || !isAiImportTaskTerminal(task)) return;
    const taskId = Number(task.id || 0);
    if (!taskId) return;
    const stateKey = getAiImportTaskStateKey(task);
    state.aiImport.dismissedTaskIds.add(taskId);
    state.aiImport.dismissedTaskStateKeys.delete(taskId);
    state.aiImport.dismissedTaskStateKeys.set(taskId, stateKey);
    while (state.aiImport.dismissedTaskStateKeys.size > AI_IMPORT_DISMISSED_TASK_LIMIT) {
        const oldestId = state.aiImport.dismissedTaskStateKeys.keys().next().value;
        state.aiImport.dismissedTaskStateKeys.delete(oldestId);
        state.aiImport.dismissedTaskIds.delete(oldestId);
    }
    persistAiImportDismissals();
}

function clearMismatchedAiImportDismissal(task) {
    const taskId = Number(task?.id || 0);
    if (!taskId || !state.aiImport.dismissedTaskStateKeys.has(taskId)) return;
    if (state.aiImport.dismissedTaskStateKeys.get(taskId) === getAiImportTaskStateKey(task)) return;
    state.aiImport.dismissedTaskIds.delete(taskId);
    state.aiImport.dismissedTaskStateKeys.delete(taskId);
    persistAiImportDismissals();
}

function isAiImportTaskDismissed(task) {
    if (!task || !isAiImportTaskTerminal(task)) return false;
    const taskId = Number(task.id || 0);
    const dismissedStateKey = state.aiImport.dismissedTaskStateKeys.get(taskId);
    return Boolean(dismissedStateKey && dismissedStateKey === getAiImportTaskStateKey(task));
}

function isAiImportTaskActive(task) {
    return AI_IMPORT_ACTIVE_STATUSES.has(task?.parse_status);
}

function isAiImportTaskTerminal(task) {
    return AI_IMPORT_TERMINAL_STATUSES.has(task?.parse_status);
}

function isAiImportTaskVisible(task) {
    const currentParentId = state.currentParentId ? Number(state.currentParentId) : null;
    const documentType = normalizeDocumentTypeFilter(state.filters.documentType);
    if (documentType && normalizeDocumentTypeFilter(task?.document_type) !== documentType) {
        return false;
    }
    return (task?.parent_material_id || null) === currentParentId;
}

function upsertAiImportTask(rawTask) {
    const task = normalizeAiImportTask(rawTask);
    if (!task) return null;
    clearMismatchedAiImportDismissal(task);
    if (isAiImportTaskDismissed(task)) {
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
    const task = state.aiImport.tasks.get(normalizedId);
    if (task) {
        rememberAiImportTaskDismissal(task);
    } else {
        state.aiImport.dismissedTaskIds.add(normalizedId);
    }
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
    const action = isAiGenerationTask(task) ? '生成' : '解析';
    if (task.parse_status === 'queued') return `AI 正在等待${action}《${fileName}》`;
    if (task.parse_status === 'running') return `AI 正在${action}《${fileName}》`;
    if (task.parse_status === 'completed') return `AI 已完成《${fileName}》${action}`;
    if (task.parse_status === 'quality_failed') return `《${fileName}》疑似乱码`;
    if (task.parse_status === 'unsupported') return `《${fileName}》暂不支持解析`;
    if (task.parse_status === 'ai_failed') return isAiGenerationTask(task) ? `AI 未能生成《${fileName}》` : `AI 未能识别《${fileName}》`;
    return `《${fileName}》${action}失败`;
}

function getAiImportTaskMessage(task) {
    if (task?.message) return task.message;
    const isGeneration = isAiGenerationTask(task);
    if (task?.parse_status === 'queued') return isGeneration
        ? '任务已进入后台生成队列，会按顺序处理并在完成后刷新材料列表。'
        : '任务已进入后台队列，会按顺序调用 AI，避免影响平台其他 AI 功能。';
    if (task?.parse_status === 'running') return isGeneration
        ? '系统正在根据来源内容生成结构化材料，完成后会自动刷新材料列表。'
        : '系统正在抽取正文、校验乱码并调用 AI 识别，完成后会自动刷新材料列表。';
    if (task?.parse_status === 'completed') return '已生成可阅读正文和结构化 JSON，后续可按同类模板导出。';
    if (task?.parse_status === 'quality_failed') return '系统检测到解析结果质量不足，已阻止保存无效内容。';
    if (task?.parse_status === 'unsupported') return '请先转换为 docx、xlsx 或 PDF 后重试。';
    if (task?.parse_status === 'ai_failed') return 'AI 服务未返回可用结果，请稍后重试。';
    return isGeneration ? '生成未完成，请稍后重试。' : '解析未完成，请稍后重试。';
}

function renderAiImportTaskCards() {
    const tasks = getVisibleAiImportTasks();
    if (!tasks.length) return '';
    return tasks.map((task) => {
        const tone = getAiImportTaskTone(task);
        const active = isAiImportTaskActive(task);
        const completed = task.parse_status === 'completed';
        const exportLabel = ['ordinary_grade_record', 'exam_grade_record'].includes(task.document_type) ? '导出 Excel' : '导出 Word';
        const exportDownloadLabel = ['ordinary_grade_record', 'exam_grade_record'].includes(task.document_type) ? 'Excel' : 'Word';
        const qualityMeta = task.content_quality_label
            ? `<span>质量 ${escapeHtml(task.content_quality_label)}</span>`
            : '';
        const renderPreviewAction = completed && task.render_preview_url
            ? `<a href="${escapeHtml(task.render_preview_url)}" class="btn btn-outline btn-sm" target="_blank" rel="noopener">渲染预览</a>`
            : '';
        const exportAction = completed && task.export_url
            ? `<button type="button" class="btn btn-outline btn-sm" data-process-export-url="${escapeHtml(task.export_url)}" data-process-export-label="${escapeHtml(exportDownloadLabel)}">${escapeHtml(exportLabel)}</button>`
            : '';
        const exportPdfAction = completed && task.export_pdf_url
            ? `<button type="button" class="btn btn-outline btn-sm" data-process-export-url="${escapeHtml(task.export_pdf_url)}" data-process-export-label="PDF">导出 PDF</button>`
            : '';
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
                        ${qualityMeta}
                        ${task.updated_at ? `<span>更新 ${escapeHtml(formatDateLabel(task.updated_at))}</span>` : ''}
                    </div>
                </div>
                <div class="materials-ai-task-actions">
                    ${renderPreviewAction}
                    ${exportAction}
                    ${exportPdfAction}
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
                    showToast(`《${nextTask.source_file_name}》AI ${isAiGenerationTask(nextTask) ? '生成' : '解析'}完成`, 'success', 4200);
                } else {
                    const toastType = ['quality_failed', 'unsupported'].includes(nextTask.parse_status) ? 'warning' : 'error';
                    showToast(nextTask.message || `《${nextTask.source_file_name}》${isAiGenerationTask(nextTask) ? '生成' : '解析'}未完成`, toastType, 5200);
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

function openAiImportModal(preset = getInitialAiImportPreset()) {
    if (!getAiImportRegistry().length) {
        showToast('材料解析类型暂未加载', 'error');
        return;
    }
    state.aiImport.file = null;
    if (refs.aiImportFileInput) refs.aiImportFileInput.value = '';
    renderAiImportGroups();
    applyAiImportPreset(preset);
    updateAiImportFileLabel();
    if (!preset?.status) setAiImportStatus('', 'info');
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
    if (!isAiImportFileAccepted(state.aiImport.file)) {
        setAiImportStatus(getAiImportFormatMismatchMessage(state.aiImport.file), 'warning');
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
    refs.aiGenerateStatus.className = `materials-ai-import-status materials-ai-generate-status materials-ai-import-status--${type}`;
    refs.aiGenerateStatus.textContent = normalizedMessage;
}

function updateAiGenerateSubmitState() {
    if (!refs.aiGenerateSubmitBtn) return;
    const blockedReason = state.aiGenerate.blockedReason || state.aiGenerate.sourceBlockReason || '';
    const blocked = Boolean(blockedReason);
    const disabled = state.aiGenerate.busy || blocked;
    refs.aiGenerateSubmitBtn.disabled = disabled;
    refs.aiGenerateSubmitBtn.setAttribute('aria-disabled', disabled ? 'true' : 'false');
    refs.aiGenerateSubmitBtn.title = blocked ? blockedReason : '';
    refs.aiGenerateSubmitBtn.textContent = state.aiGenerate.busy ? '深度思考中...' : '生成并保存';
}

function setAiGenerateBusy(busy) {
    state.aiGenerate.busy = busy;
    updateAiGenerateSubmitState();
    [
        refs.aiGenerateGroup,
        refs.aiGenerateType,
        refs.aiGeneratePrompt,
        refs.aiGenerateUploadBtn,
        refs.aiGenerateMaterialQuery,
        refs.aiGenerateAssignmentQuery,
    ].forEach((element) => {
        if (element) element.disabled = busy;
    });
    renderAiGenerateModal();
    setModalDismissDisabled(refs.aiGenerateModal, busy);
}

function setAiExpandBusy(busy) {
    state.aiExpand.busy = busy;
    if (refs.aiExpandSubmitBtn) {
        refs.aiExpandSubmitBtn.disabled = busy;
        refs.aiExpandSubmitBtn.textContent = busy ? '提交中...' : '开始续写';
    }
    if (refs.aiExpandPrompt) refs.aiExpandPrompt.disabled = busy;
    setModalDismissDisabled(document.getElementById('materials-ai-expand-modal'), busy);
}

function resetAiGenerateState() {
    state.aiGenerate.blockedReason = '';
    state.aiGenerate.sourceBlockReason = '';
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

function getAiGenerateTypeKey() {
    return refs.aiGenerateType?.value || 'teaching_document';
}

function getAiGenerateGroupKey() {
    return refs.aiGenerateGroup?.value || 'teaching_material';
}

function getAiGenerateCandidateDocumentType(item) {
    return String(
        item?.ai_generation_document_type
        || item?.document_type
        || item?.ai_import_record?.document_type
        || ''
    ).trim();
}

function textContainsAny(value, patterns) {
    const text = String(value || '').toLowerCase();
    return patterns.some((pattern) => text.includes(pattern));
}

function materialLooksLikeAssessmentPlan(item) {
    if (getAiGenerateCandidateDocumentType(item) === 'assessment_plan') return true;
    return textContainsAny(
        [item?.ai_generation_document_type_label, item?.name, item?.material_path].filter(Boolean).join(' '),
        ['考核计划表', 'assessment plan', 'assessment_plan']
    );
}

function materialLooksLikeExamPaper(item) {
    if (getAiGenerateCandidateDocumentType(item) === 'exam_paper') return true;
    const text = [item?.ai_generation_document_type_label, item?.name, item?.material_path].filter(Boolean).join(' ');
    return textContainsAny(text, ['课程考核试卷', '考核试卷', 'exam paper', 'exam_paper']);
}

function getAiGenerateSourceState() {
    const selectedMaterials = Array.from(state.aiGenerate.selectedMaterials.values());
    const selectedAssignments = Array.from(state.aiGenerate.selectedAssignments.values());
    const uploadCount = state.aiGenerate.files.length;
    const attachmentCount = getAiGenerateAttachmentCount();
    return {
        attachmentCount,
        uploadCount,
        hasAssessmentPlanMaterial: selectedMaterials.some(materialLooksLikeAssessmentPlan),
        hasExamPaperMaterial: selectedMaterials.some(materialLooksLikeExamPaper),
        hasQuestionAssignment: selectedAssignments.some((item) => Number(item?.question_count || 0) > 0),
    };
}

function refreshAiGenerateSourceGuidance() {
    state.aiGenerate.sourceBlockReason = '';
    if (state.aiGenerate.blockedReason) {
        updateAiGenerateSubmitState();
        return;
    }
    if (getAiGenerateGroupKey() !== 'final_material') {
        setAiGenerateStatus('', 'info');
        updateAiGenerateSubmitState();
        return;
    }
    const type = getAiGenerateTypeKey();
    const source = getAiGenerateSourceState();
    if (type === 'exam_paper') {
        if (source.attachmentCount <= 0) {
            state.aiGenerate.sourceBlockReason = '生成课程考核试卷前，请先关联考核计划表、课程材料或上传参考附件。';
            setAiGenerateStatus(state.aiGenerate.sourceBlockReason, 'warning');
        } else if (source.hasAssessmentPlanMaterial) {
            setAiGenerateStatus('已关联考核计划表，生成试卷时会优先继承考核项目、分值分布和考试约束。', 'success');
        } else {
            setAiGenerateStatus('未识别到考核计划表。可以继续生成，但请在生成后重点核对分值分布、考试形式和命题信息。', 'warning');
        }
    } else if (type === 'grading_rubric') {
        const hasConcreteSource = source.hasExamPaperMaterial || source.hasQuestionAssignment || source.uploadCount > 0;
        if (!hasConcreteSource) {
            state.aiGenerate.sourceBlockReason = '生成评分细则前，请先关联课程考核试卷、带题目的作业，或上传试卷文件。';
            setAiGenerateStatus(state.aiGenerate.sourceBlockReason, 'warning');
        } else if (source.hasExamPaperMaterial || source.hasQuestionAssignment) {
            setAiGenerateStatus('已关联具体试卷或题目来源，评分细则会按题目逐项生成给分点。', 'success');
        } else {
            setAiGenerateStatus('将从上传附件中识别试卷题目；若附件不是具体试卷，系统会拒绝生成评分细则。', 'info');
        }
    } else {
        setAiGenerateStatus('', 'info');
    }
    updateAiGenerateSubmitState();
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
            meta: [item.ai_generation_document_type_label, item.material_path || '站内材料'].filter(Boolean).join(' · '),
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
    const removeDisabledAttr = state.aiGenerate.busy ? ' disabled aria-disabled="true"' : '';
    refs.aiGenerateSelected.innerHTML = selected.map((item) => `
        <span class="materials-ai-generate-chip" title="${escapeHtml(item.meta)}">
            <strong>${escapeHtml(item.kind === 'file' ? '上传' : (item.kind === 'assignment' ? '作业' : '材料'))}</strong>
            <span>${escapeHtml(item.title)}</span>
            <button type="button" data-ai-generate-remove="${escapeHtml(item.kind)}" data-id="${escapeHtml(String(item.id))}" aria-label="移除 ${escapeHtml(item.title)}"${removeDisabledAttr}>&times;</button>
        </span>
    `).join('');
}

function renderAiGenerateUploadList() {
    if (!refs.aiGenerateUploadList) return;
    if (!state.aiGenerate.files.length) {
        refs.aiGenerateUploadList.innerHTML = '<div class="materials-ai-generate-empty">未选择新文件。</div>';
        return;
    }
    const removeDisabledAttr = state.aiGenerate.busy ? ' disabled aria-disabled="true"' : '';
    refs.aiGenerateUploadList.innerHTML = state.aiGenerate.files.map((entry) => `
        <div class="materials-ai-generate-candidate is-selected">
            <div>
                <strong title="${escapeHtml(entry.file.name)}">${escapeHtml(entry.file.name)}</strong>
                <span>${escapeHtml(formatSize(entry.file.size || 0))}</span>
            </div>
            <button type="button" class="btn btn-ghost btn-sm" data-ai-generate-remove="file" data-id="${escapeHtml(entry.id)}"${removeDisabledAttr}>移除</button>
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
    const locked = state.aiGenerate.busy;
    listEl.innerHTML = items.map((item) => {
        const selected = selectedMap.has(Number(item.id));
        const title = isMaterial ? item.name : item.title;
        const subtitle = isMaterial
            ? (item.material_path || getMaterialTypeLabel(item))
            : ([item.course_name, item.class_name].filter(Boolean).join(' / ') || item.question_excerpt || '作业题目');
        const materialTypeLabel = item.ai_generation_document_type_label || getMaterialTypeLabel(item);
        const meta = isMaterial
            ? [materialTypeLabel, item.node_type === 'folder' ? `${item.child_count || 0} 项` : formatSize(item.file_size || 0)].filter(Boolean).join(' · ')
            : [`${item.question_count || 0} 题`, item.status || ''].filter(Boolean).join(' · ');
        return `
            <button type="button"
                class="materials-ai-generate-candidate ${selected ? 'is-selected' : ''}"
                data-ai-generate-add="${escapeHtml(kind)}"
                data-id="${escapeHtml(String(item.id))}"
                ${selected || reachedLimit || locked ? 'disabled' : ''}
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
    if (state.aiGenerate.busy) return;
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
    refreshAiGenerateSourceGuidance();
}

function removeAiGenerateAttachment(kind, idValue) {
    if (state.aiGenerate.busy) return;
    if (kind === 'file') {
        state.aiGenerate.files = state.aiGenerate.files.filter((entry) => entry.id !== idValue);
    } else if (kind === 'material') {
        state.aiGenerate.selectedMaterials.delete(Number(idValue));
    } else if (kind === 'assignment') {
        state.aiGenerate.selectedAssignments.delete(Number(idValue));
    }
    renderAiGenerateModal();
    refreshAiGenerateSourceGuidance();
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
    if (state.aiGenerate.busy) return;
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
    refreshAiGenerateSourceGuidance();
}

function openAiGenerateModal(preset = null) {
    resetAiGenerateState();
    applyAiGeneratePreset(preset);
    setAiGenerateBusy(false);
    renderAiGenerateModal();
    refreshAiGenerateSourceGuidance();
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
    refreshAiGenerateSourceGuidance();
    if (state.aiGenerate.blockedReason) {
        showToast(state.aiGenerate.blockedReason, 'warning');
        return;
    }
    if (state.aiGenerate.sourceBlockReason) {
        showToast(state.aiGenerate.sourceBlockReason, 'warning');
        return;
    }
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
        await recordMaterialPromptBestEffort(refs.aiGeneratePrompt, prompt);
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

const AI_REWRITE_MODE_COPY = {
    regenerate: {
        title: 'AI重新生成材料',
        subtitle: '写下希望调整的方向；留空则基于原材料重新组织并生成新材料。',
        pendingLabel: 'AI 正在重新生成',
    },
    optimize: {
        title: 'AI优化排版',
        subtitle: '整理为排版规整的 Markdown 文档。改动限制决定允许对原文改动多少，默认“一般”。',
        pendingLabel: 'AI 正在优化排版',
    },
    polish: {
        title: 'AI深度润色',
        subtitle: '按材料意思深度重写并优化排版；可选择目标课堂，让内容贴合课程、班级与专业目标。',
        pendingLabel: 'AI 正在深度润色',
    },
};

function renderAiRewriteOfferingOptions() {
    if (!refs.aiRewriteOffering) return;
    const offerings = Array.isArray(config.offerings) ? config.offerings : [];
    refs.aiRewriteOffering.innerHTML = [
        '<option value="">不关联课堂，按通用教学标准润色</option>',
        ...offerings.map((offering) => `
            <option value="${escapeHtml(String(offering.id))}">
                ${escapeHtml(`${offering.course_name} / ${offering.class_name}${offering.semester ? ` · ${offering.semester}` : ''}`)}
            </option>
        `),
    ].join('');
}

function openAiRewriteModal(mode = 'regenerate') {
    if (!state.activeDetail) return;
    const normalizedMode = ['regenerate', 'optimize', 'polish'].includes(mode) ? mode : 'optimize';
    state.aiRewrite.mode = normalizedMode;
    state.aiRewrite.materialId = state.activeDetail.id;
    state.aiRewrite.materialName = state.activeDetail.name || '';
    const copy = AI_REWRITE_MODE_COPY[normalizedMode];
    if (refs.aiRewriteTitle) refs.aiRewriteTitle.textContent = copy.title;
    if (refs.aiRewriteSubtitle) refs.aiRewriteSubtitle.textContent = copy.subtitle;
    if (refs.aiRewriteStrictnessField) refs.aiRewriteStrictnessField.hidden = normalizedMode !== 'optimize';
    if (refs.aiRewriteStrictness) refs.aiRewriteStrictness.value = 'balanced';
    if (refs.aiRewriteOfferingField) refs.aiRewriteOfferingField.hidden = normalizedMode !== 'polish';
    if (normalizedMode === 'polish') {
        renderAiRewriteOfferingOptions();
    }
    if (refs.aiRewritePrompt) refs.aiRewritePrompt.value = '';
    setAiRewriteStatus('', 'info');
    setAiRewriteBusy(false);
    openModal('materials-ai-rewrite-modal');
    window.setTimeout(() => refs.aiRewritePrompt?.focus(), 50);
}

function submitAiRewrite() {
    if (state.aiRewrite.busy || !state.aiRewrite.materialId) return Promise.resolve();
    const materialId = state.aiRewrite.materialId;
    const materialName = state.aiRewrite.materialName || '材料';
    const mode = state.aiRewrite.mode || 'regenerate';
    const prompt = refs.aiRewritePrompt?.value || '';
    const strictness = refs.aiRewriteStrictness?.value || 'balanced';
    const offeringValue = mode === 'polish' ? (refs.aiRewriteOffering?.value || '') : '';
    const copy = AI_REWRITE_MODE_COPY[mode] || AI_REWRITE_MODE_COPY.optimize;

    // 立即回到列表页并展示“处理中”提示卡；请求在后台完成后扭转提示并刷新。
    closeModal('materials-ai-rewrite-modal');
    closeDetailModal();
    const pendingKey = `rewrite:${mode}:${materialId}:${Date.now()}`;
    addAiPendingTask(
        pendingKey,
        `${copy.pendingLabel}《${materialName}》`,
        'AI 正在深度思考处理材料，期间可继续其他操作，完成后会自动刷新列表。',
    );

    return apiFetch(`/api/materials/${materialId}/ai-rewrite`, {
        method: 'POST',
        body: {
            mode,
            prompt,
            strictness,
            class_offering_id: offeringValue ? Number(offeringValue) : null,
        },
        silent: true,
    }).then(async (result) => {
        finishAiPendingTask(pendingKey, { success: true });
        await recordMaterialPromptBestEffort(refs.aiRewritePrompt, prompt);
        showToast(result.message || 'AI 处理完成', 'success', 5200);
        await loadLibrary(state.currentParentId, false);
        if (result.viewer_url) {
            window.open(result.viewer_url, '_blank', 'noopener');
        }
    }).catch((error) => {
        finishAiPendingTask(pendingKey, {
            success: false,
            label: `《${materialName}》AI 处理失败`,
            message: error.message || 'AI 处理失败，请稍后重试。',
        });
        loadLibrary(state.currentParentId, false).catch(() => {});
    });
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

async function updateActiveMaterialScope(scopeLevel) {
    if (!state.activeDetail || state.activeDetail.can_manage === false) return;
    const normalizedScope = ['private', 'department', 'college', 'school', 'public'].includes(scopeLevel) ? scopeLevel : 'private';
    const result = await apiFetch(`/api/materials/${state.activeDetail.id}/scope`, {
        method: 'PATCH',
        body: { scope_level: normalizedScope },
    });
    showToast(result.message || '材料开放范围已更新', 'success');
    await loadLibrary(state.currentParentId);
    await loadMaterialDetail(state.activeDetail.id);
}

async function refreshActiveWorkspace(materialId, { refreshTree = true } = {}) {
    const targetId = Number(materialId || state.activeDetail?.id || 0);
    if (!targetId) return;
    if (refreshTree) {
        await loadMaterialTree(targetId);
    }
    await loadMaterialDetail(targetId);
    await loadLibrary(state.currentParentId, false);
}

async function saveActiveMaterialProperties() {
    if (!state.activeDetail || state.activeDetail.can_manage === false) return;
    const nameInput = refs.detail?.querySelector('[data-property-name]');
    const scopeSelect = refs.detail?.querySelector('[data-property-scope]');
    const payload = {};
    const nextName = String(nameInput?.value || '').trim();
    if (nextName && nextName !== String(state.activeDetail.name || '')) {
        payload.name = nextName;
    }
    if (scopeSelect && scopeSelect.value !== String(state.activeDetail.scope_level || 'private')) {
        payload.scope_level = scopeSelect.value;
    }
    if (!Object.keys(payload).length) {
        showToast('属性没有变化', 'info');
        return;
    }
    const result = await apiFetch(`/api/materials/${state.activeDetail.id}/attributes`, {
        method: 'PATCH',
        body: payload,
    });
    showToast(result.message || '材料属性已保存', 'success');
    await refreshActiveWorkspace(state.activeDetail.id);
}

async function saveActiveMaterialContent() {
    if (!state.activeDetail || state.activeDetail.can_manage === false || !state.activeDetail.editable) return;
    const editor = refs.detail?.querySelector('[data-material-content-editor]');
    if (!editor) return;
    const materialId = Number(state.activeDetail.id);
    const result = await apiFetch(`/api/materials/${materialId}/content`, {
        method: 'PUT',
        body: {
            content: String(editor.value || ''),
            encoding: state.materialWorkspace.content.encoding || 'utf-8',
        },
    });
    showToast(result.message || '材料内容已保存', 'success');
    await refreshActiveWorkspace(materialId);
}

async function deleteActiveMaterial() {
    if (!state.activeDetail) return;
    const confirmed = await openProcessMaterialConfirm({
        title: '删除材料',
        message: `确定删除材料“${state.activeDetail.name || '未命名材料'}”吗？`,
        detail: '删除后无法恢复，关联的过程材料预览、导出入口和课堂分配也会一并失效。',
        confirmText: '删除',
        tone: 'danger',
    });
    if (!confirmed) return;
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

function setModalStatus(statusEl, message = '', type = 'info') {
    if (!statusEl) return;
    const normalizedMessage = String(message || '').trim();
    statusEl.hidden = !normalizedMessage;
    statusEl.className = `materials-ai-import-status materials-ai-import-status--${type}`;
    statusEl.textContent = normalizedMessage;
}

async function loadFolderOptions(select, { excludeSubtreeOf = null, selectedId = null, rootLabel = '材料库根目录' } = {}) {
    if (!select) return;
    select.innerHTML = '<option value="">正在加载目录...</option>';
    const params = new URLSearchParams();
    if (excludeSubtreeOf) params.set('exclude_subtree_of', String(excludeSubtreeOf));
    const result = await apiFetch(`/api/materials/folder-options${params.toString() ? `?${params.toString()}` : ''}`, {
        method: 'GET',
        silent: true,
    });
    const folders = result.folders || [];
    const optionHtml = folders.map((folder) => {
        const indent = '&nbsp;&nbsp;'.repeat(Math.min(Number(folder.depth) || 0, 8));
        return `<option value="${escapeHtml(String(folder.id))}" title="${escapeHtml(folder.material_path)}">${indent}${escapeHtml(folder.name)}</option>`;
    }).join('');
    select.innerHTML = `<option value="">${escapeHtml(rootLabel)}</option>${optionHtml}`;
    const desired = selectedId ? String(selectedId) : '';
    select.value = folders.some((folder) => String(folder.id) === desired) ? desired : '';
}

function setCreateMenuOpen(open) {
    const dropdown = refs.createDropdown || document.getElementById('materials-create-dropdown');
    const button = refs.createMenuBtn || document.getElementById('materials-create-menu-btn');
    if (!dropdown || !button) return;
    dropdown.hidden = !open;
    button.setAttribute('aria-expanded', open ? 'true' : 'false');
}

function openCreateNodeModal(type, parentId = state.currentParentId, { fromWorkspace = false } = {}) {
    state.createNode.type = type === 'file' ? 'file' : 'folder';
    state.createNode.busy = false;
    state.createNode.parentId = parentId ?? null;
    state.createNode.fromWorkspace = Boolean(fromWorkspace);
    const isFile = state.createNode.type === 'file';
    if (refs.createNodeTitle) refs.createNodeTitle.textContent = isFile ? '新建文档' : '新建文件夹';
    if (refs.createNodeSubtitle) {
        refs.createNodeSubtitle.textContent = isFile
            ? '创建一个 Markdown 材料文档，创建后可直接在线编辑。'
            : '空文件夹可继续在里面新建子文件夹和材料文档。';
    }
    if (refs.createNodeNameLabel) refs.createNodeNameLabel.textContent = isFile ? '文档名称' : '文件夹名称';
    if (refs.createNodeName) {
        refs.createNodeName.value = '';
        refs.createNodeName.placeholder = isFile ? '例如：第4次课讲义（自动加 .md）' : '例如：第4次课';
    }
    if (refs.createNodeHint) {
        refs.createNodeHint.textContent = isFile
            ? '文档会保存为 Markdown（.md）格式；开放范围随所在最外层文件夹。'
            : '开放范围随所在最外层文件夹；顶层文件夹默认私有，可在属性中调整。';
    }
    if (refs.createNodeSubmitBtn) {
        refs.createNodeSubmitBtn.disabled = false;
        refs.createNodeSubmitBtn.textContent = '创建';
    }
    setModalStatus(refs.createNodeStatus, '', 'info');
    openModal('materials-create-node-modal');
    loadFolderOptions(refs.createNodeLocation, { selectedId: state.createNode.parentId }).catch((error) => {
        setModalStatus(refs.createNodeStatus, error.message || '目录加载失败', 'error');
    });
    window.setTimeout(() => refs.createNodeName?.focus(), 50);
}

async function submitCreateNode() {
    if (state.createNode.busy) return;
    const name = String(refs.createNodeName?.value || '').trim();
    if (!name) {
        showToast('请填写名称', 'warning');
        refs.createNodeName?.focus();
        return;
    }
    const isFile = state.createNode.type === 'file';
    const locationValue = refs.createNodeLocation?.value || '';
    const parentId = locationValue ? Number(locationValue) : null;
    state.createNode.busy = true;
    if (refs.createNodeSubmitBtn) {
        refs.createNodeSubmitBtn.disabled = true;
        refs.createNodeSubmitBtn.textContent = '创建中...';
    }
    try {
        const endpoint = isFile ? '/api/materials/files' : '/api/materials/folders';
        const result = await apiFetch(endpoint, {
            method: 'POST',
            body: { name, parent_id: parentId },
        });
        closeModal('materials-create-node-modal');
        showToast(result.message || '创建成功', 'success');
        if (state.createNode.fromWorkspace && result.material?.id) {
            await loadLibrary(state.currentParentId, false);
            await loadMaterialTree(result.material.id);
            await loadMaterialDetail(result.material.id);
        } else if (isFile && result.viewer_url) {
            await loadLibrary(parentId ?? state.currentParentId, false);
            window.open(result.viewer_url, '_blank', 'noopener');
        } else {
            await loadLibrary(parentId ?? state.currentParentId, false);
        }
    } catch (error) {
        setModalStatus(refs.createNodeStatus, error.message || '创建失败', 'error');
    } finally {
        state.createNode.busy = false;
        if (refs.createNodeSubmitBtn) {
            refs.createNodeSubmitBtn.disabled = false;
            refs.createNodeSubmitBtn.textContent = '创建';
        }
    }
}

function openMoveModal() {
    if (!state.activeDetail || state.activeDetail.can_manage === false) return;
    state.move.materialId = Number(state.activeDetail.id);
    state.move.materialName = state.activeDetail.name || '';
    state.move.busy = false;
    if (refs.moveName) refs.moveName.textContent = state.activeDetail.name || '-';
    if (refs.movePath) refs.movePath.textContent = state.activeDetail.material_path || '-';
    if (refs.moveSubmitBtn) {
        refs.moveSubmitBtn.disabled = false;
        refs.moveSubmitBtn.textContent = '移动';
    }
    setModalStatus(refs.moveStatus, '', 'info');
    openModal('materials-move-modal');
    const excludeId = state.activeDetail.node_type === 'folder' ? state.move.materialId : null;
    loadFolderOptions(refs.moveTarget, {
        excludeSubtreeOf: excludeId,
        selectedId: state.activeDetail.parent_id || null,
    }).catch((error) => {
        setModalStatus(refs.moveStatus, error.message || '目录加载失败', 'error');
    });
}

async function submitMove() {
    if (state.move.busy || !state.move.materialId) return;
    const targetValue = refs.moveTarget?.value || '';
    state.move.busy = true;
    if (refs.moveSubmitBtn) {
        refs.moveSubmitBtn.disabled = true;
        refs.moveSubmitBtn.textContent = '移动中...';
    }
    try {
        const result = await apiFetch(`/api/materials/${state.move.materialId}/move`, {
            method: 'POST',
            body: { target_parent_id: targetValue ? Number(targetValue) : null },
        });
        closeModal('materials-move-modal');
        showToast(result.message || '移动完成', 'success');
        closeDetailModal();
        await loadLibrary(state.currentParentId, false);
    } catch (error) {
        setModalStatus(refs.moveStatus, error.message || '移动失败', 'error');
    } finally {
        state.move.busy = false;
        if (refs.moveSubmitBtn) {
            refs.moveSubmitBtn.disabled = false;
            refs.moveSubmitBtn.textContent = '移动';
        }
    }
}

function getBindTargetKey(offeringId, sessionId) {
    return `${Number(offeringId)}:${Number(sessionId) || 0}`;
}

function updateBindCount() {
    if (refs.bindCount) refs.bindCount.textContent = `已选 ${state.bind.selected.size} 处`;
}

function renderBindTargets() {
    if (!refs.bindTargets) return;
    const offeringId = Number(refs.bindOffering?.value || 0);
    const offering = state.bind.offerings.find((item) => Number(item.id) === offeringId);
    if (!offering) {
        refs.bindTargets.innerHTML = '<div class="materials-empty">请选择课堂后勾选首页或课次。</div>';
        updateBindCount();
        return;
    }
    const homeKey = getBindTargetKey(offering.id, 0);
    const sessionRows = (offering.sessions || []).map((session) => {
        const key = getBindTargetKey(offering.id, session.id);
        return `
            <label class="materials-modal-option">
                <input type="checkbox" data-bind-target="${escapeHtml(key)}" ${state.bind.selected.has(key) ? 'checked' : ''}>
                <div>
                    <strong>第 ${escapeHtml(String(session.order_index || ''))} 次课</strong>
                    <div class="text-muted text-sm">${escapeHtml(session.title || '未命名课次')}</div>
                </div>
            </label>
        `;
    }).join('');
    refs.bindTargets.innerHTML = `
        <label class="materials-modal-option">
            <input type="checkbox" data-bind-target="${escapeHtml(homeKey)}" ${state.bind.selected.has(homeKey) ? 'checked' : ''}>
            <div>
                <strong>课堂材料区首页</strong>
                <div class="text-muted text-sm">学生打开课堂材料区时的首页按钮</div>
            </div>
        </label>
        ${sessionRows || '<div class="materials-empty">该课堂暂无课次安排，只能绑定首页。</div>'}
    `;
    updateBindCount();
}

async function openBindModal() {
    if (!state.activeDetail) return;
    state.bind.materialId = Number(state.activeDetail.id);
    state.bind.busy = false;
    if (refs.bindName) refs.bindName.textContent = state.activeDetail.name || '-';
    setModalStatus(refs.bindStatus, '', 'info');
    if (refs.bindSaveBtn) {
        refs.bindSaveBtn.disabled = false;
        refs.bindSaveBtn.textContent = '保存绑定';
    }
    openModal('materials-bind-modal');
    if (refs.bindTargets) refs.bindTargets.innerHTML = '<div class="materials-empty">正在加载课堂与课次...</div>';
    try {
        const result = await apiFetch(`/api/materials/${state.bind.materialId}/learning-bindings`, {
            method: 'GET',
            silent: true,
        });
        if (!result.bindable) {
            setModalStatus(refs.bindStatus, '当前材料不是 Markdown 或可渲染 HTML，暂不能绑定课次/首页。', 'warning');
        }
        state.bind.offerings = result.offerings || [];
        state.bind.selected = new Set();
        state.bind.offerings.forEach((offering) => {
            if (offering.home_bound) state.bind.selected.add(getBindTargetKey(offering.id, 0));
            (offering.bound_session_ids || []).forEach((sessionId) => {
                state.bind.selected.add(getBindTargetKey(offering.id, sessionId));
            });
        });
        if (refs.bindOffering) {
            refs.bindOffering.innerHTML = state.bind.offerings.length
                ? state.bind.offerings.map((offering) => `
                    <option value="${escapeHtml(String(offering.id))}">
                        ${escapeHtml(`${offering.course_name} / ${offering.class_name}${offering.semester ? ` · ${offering.semester}` : ''}`)}
                    </option>
                `).join('')
                : '<option value="">暂无课堂，请先创建课堂</option>';
        }
        renderBindTargets();
    } catch (error) {
        setModalStatus(refs.bindStatus, error.message || '加载绑定信息失败', 'error');
    }
}

async function submitBindTargets() {
    if (state.bind.busy || !state.bind.materialId) return;
    state.bind.busy = true;
    if (refs.bindSaveBtn) {
        refs.bindSaveBtn.disabled = true;
        refs.bindSaveBtn.textContent = '保存中...';
    }
    try {
        const targets = Array.from(state.bind.selected).map((key) => {
            const [offeringId, sessionId] = key.split(':');
            return { class_offering_id: Number(offeringId), session_id: Number(sessionId) || 0 };
        });
        const result = await apiFetch(`/api/materials/${state.bind.materialId}/learning-bindings`, {
            method: 'PUT',
            body: { targets },
        });
        closeModal('materials-bind-modal');
        showToast(result.message || '绑定已保存', 'success');
    } catch (error) {
        setModalStatus(refs.bindStatus, error.message || '保存绑定失败', 'error');
    } finally {
        state.bind.busy = false;
        if (refs.bindSaveBtn) {
            refs.bindSaveBtn.disabled = false;
            refs.bindSaveBtn.textContent = '保存绑定';
        }
    }
}

function openAiExpandModal() {
    if (state.aiExpand.busy) {
        showToast('AI 正在续写材料，请等待当前任务提交完成。', 'warning');
        return;
    }
    if (!state.currentFolder) {
        showToast('请先进入一个文件夹，AI 会基于该文件夹的材料续写', 'warning');
        return;
    }
    if (refs.aiExpandFolder) refs.aiExpandFolder.textContent = state.currentFolder.name || '-';
    if (refs.aiExpandFolderPath) refs.aiExpandFolderPath.textContent = state.currentFolder.material_path || '-';
    if (refs.aiExpandPrompt) refs.aiExpandPrompt.value = '';
    setAiExpandBusy(false);
    setModalStatus(refs.aiExpandStatus, '', 'info');
    openModal('materials-ai-expand-modal');
    window.setTimeout(() => refs.aiExpandPrompt?.focus(), 50);
}

function submitAiExpand() {
    if (state.aiExpand.busy || !state.currentFolder) return Promise.resolve();
    const folderId = Number(state.currentFolder.id);
    const folderName = state.currentFolder.name || '当前文件夹';
    const prompt = refs.aiExpandPrompt?.value || '';

    setAiExpandBusy(true);
    closeModal('materials-ai-expand-modal');
    const pendingKey = `expand:${folderId}:${Date.now()}`;
    addAiPendingTask(
        pendingKey,
        `AI 正在为《${folderName}》续写下一份材料`,
        'AI 正在阅读文件夹内已有材料并深度思考续写，完成后新材料会自动出现在列表。',
    );

    return apiFetch('/api/materials/ai-expand', {
        method: 'POST',
        body: { parent_id: folderId, prompt },
        silent: true,
    }).then(async (result) => {
        finishAiPendingTask(pendingKey, { success: true });
        await recordMaterialPromptBestEffort(refs.aiExpandPrompt, prompt);
        showToast(result.message || 'AI 续写完成', 'success', 5200);
        await loadLibrary(state.currentParentId, false);
        if (result.viewer_url) {
            window.open(result.viewer_url, '_blank', 'noopener');
        }
    }).catch((error) => {
        finishAiPendingTask(pendingKey, {
            success: false,
            label: `《${folderName}》AI 续写失败`,
            message: error.message || 'AI 续写失败，请稍后重试。',
        });
    }).finally(() => {
        setAiExpandBusy(false);
    });
}

function bindEvents() {
    bindAiWorkModalCloseGuards();
    bindProcessMaterialExportDownloadActions(document, showToast, { saved: false });

    document.addEventListener('click', (event) => {
        const aiExpandSubmit = event.target.closest('#materials-ai-expand-submit-btn');
        if (aiExpandSubmit) {
            event.preventDefault();
            event.stopPropagation();
            if (aiExpandSubmit.disabled) return;
            submitAiExpand().catch((error) => {
                setModalStatus(refs.aiExpandStatus, error.message || 'AI 续写失败', 'error');
            });
            return;
        }

        const aiImportSubmit = event.target.closest('#materials-ai-import-submit-btn');
        if (aiImportSubmit) {
            event.preventDefault();
            event.stopPropagation();
            if (aiImportSubmit.disabled) return;
            submitAiImport().catch((error) => {
                setAiImportStatus(error.message || 'AI 解析导入失败', 'error');
            });
            return;
        }

        const aiGenerateSubmit = event.target.closest('#materials-ai-generate-submit-btn');
        if (aiGenerateSubmit) {
            event.preventDefault();
            event.stopPropagation();
            if (aiGenerateSubmit.disabled) return;
            submitAiGenerate().catch((error) => {
                setAiGenerateStatus(error.message || 'AI 材料生成失败', 'error');
            });
            return;
        }

        const aiRewriteSubmit = event.target.closest('#materials-ai-rewrite-submit-btn');
        if (aiRewriteSubmit) {
            event.preventDefault();
            event.stopPropagation();
            if (aiRewriteSubmit.disabled) return;
            submitAiRewrite().catch((error) => {
                showToast(error.message || 'AI 材料处理失败', 'error');
            });
            return;
        }

        const createTrigger = event.target.closest('#materials-create-menu-btn');
        if (createTrigger) {
            event.preventDefault();
            event.stopPropagation();
            setCreateMenuOpen((document.getElementById('materials-create-dropdown'))?.hidden !== false);
            return;
        }

        const createFolder = event.target.closest('#materials-create-folder-btn');
        if (createFolder) {
            event.preventDefault();
            event.stopPropagation();
            setCreateMenuOpen(false);
            openCreateNodeModal('folder');
            return;
        }

        const createFile = event.target.closest('#materials-create-file-btn');
        if (createFile) {
            event.preventDefault();
            event.stopPropagation();
            setCreateMenuOpen(false);
            openCreateNodeModal('file');
            return;
        }

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
            setCreateMenuOpen(false);
            openAiImportModal(getProcessGeneratePolicy() ? initialAiImportPreset : undefined);
            return;
        }

        const aiImportShortcut = event.target.closest('#materials-ai-import-shortcut-btn, [data-process-ai-import]');
        if (aiImportShortcut) {
            event.preventDefault();
            event.stopPropagation();
            setUploadMenuOpen(false);
            setCreateMenuOpen(false);
            openAiImportModal(initialAiImportPreset);
            return;
        }

        const classroomGenerate = event.target.closest('#materials-classroom-generate-open-btn, [data-process-classroom-generate]');
        if (classroomGenerate) {
            event.preventDefault();
            event.stopPropagation();
            setUploadMenuOpen(false);
            setCreateMenuOpen(false);
            openClassroomGenerateModal();
            return;
        }
    }, true);

    refs.refreshBtn?.addEventListener('click', () => {
        loadLibrary(state.currentParentId, false).catch((error) => {
            showToast(error.message || '刷新材料失败', 'error');
        });
    });

    refs.classroomGenerateSemesterFilter?.addEventListener('change', () => {
        renderClassroomGenerateOptions(getProcessGeneratePolicy());
    });
    refs.classroomGenerateSearch?.addEventListener('input', () => {
        renderClassroomGenerateOptions(getProcessGeneratePolicy());
    });
    refs.classroomGenerateList?.addEventListener('click', (event) => {
        const button = event.target.closest('[data-materials-ordinary-offering-id]');
        if (!button) return;
        const offeringId = Number(button.dataset.materialsOrdinaryOfferingId || 0);
        const offering = (config.offerings || []).find((item) => Number(item?.id || 0) === offeringId);
        if (offering) {
            openManageOrdinaryGradeWizard(offering).catch((error) => {
                state.ordinaryGradeGenerate.error = error.message || '读取课堂来源失败。';
                renderManageOrdinaryGradeWizard();
            });
        }
    });
    refs.ordinaryGradeStepCards.forEach((card) => {
        card.addEventListener('click', () => {
            openManageOrdinaryGradePicker(Number(card.dataset.materialsOrdinaryStepIndex || 0));
        });
    });
    refs.ordinaryGradePickerClose?.addEventListener('click', () => {
        if (refs.ordinaryGradePicker) refs.ordinaryGradePicker.hidden = true;
        renderManageOrdinaryGradeWizard();
    });
    refs.ordinaryGradePickerSearch?.addEventListener('input', renderManageOrdinaryGradePicker);
    refs.ordinaryGradePickerList?.addEventListener('click', (event) => {
        const button = event.target.closest('[data-materials-ordinary-candidate-id]');
        if (button && !button.disabled) {
            selectManageOrdinaryGradeCandidate(Number(button.dataset.materialsOrdinaryCandidateId || 0));
        }
    });
    refs.ordinaryScoreFloorEnabled?.addEventListener('change', renderManageOrdinaryGradeWizard);
    refs.ordinaryScoreFloorInput?.addEventListener('input', renderManageOrdinaryGradeWizard);
    refs.ordinaryGradePrompt?.addEventListener('input', renderManageOrdinaryGradeWizard);
    refs.classroomGenerateBackBtn?.addEventListener('click', returnToClassroomGeneratePicker);
    refs.classroomGenerateSubmitBtn?.addEventListener('click', () => {
        submitManageOrdinaryGradeGeneration().catch((error) => {
            setManageOrdinaryGradeStatus(error.message || '生成失败，请稍后重试。', 'error');
        });
    });
    document.addEventListener('pointerdown', clearRecentGeneratedHighlight, true);
    document.addEventListener('keydown', clearRecentGeneratedHighlight, true);

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
        setCreateMenuOpen(false);
        openAiImportModal(getProcessGeneratePolicy() ? initialAiImportPreset : undefined);
    });
    refs.aiImportShortcutBtn?.addEventListener('click', () => {
        setUploadMenuOpen(false);
        setCreateMenuOpen(false);
        openAiImportModal(initialAiImportPreset);
    });
    refs.processAiImportBtn?.addEventListener('click', () => {
        setUploadMenuOpen(false);
        setCreateMenuOpen(false);
        openAiImportModal(initialAiImportPreset);
    });
    refs.classroomGenerateOpenBtn?.addEventListener('click', () => {
        setUploadMenuOpen(false);
        setCreateMenuOpen(false);
        openClassroomGenerateModal();
    });
    refs.processClassroomGenerateBtn?.addEventListener('click', () => {
        setUploadMenuOpen(false);
        setCreateMenuOpen(false);
        openClassroomGenerateModal();
    });
    refs.aiGenerateOpenBtn?.addEventListener('click', () => {
        openAiGenerateModal(initialAiGeneratePreset);
    });
    refs.folderBtn?.addEventListener('click', () => refs.folderInput?.click());

    refs.createMenuBtn?.addEventListener('click', (event) => {
        event.stopPropagation();
        setCreateMenuOpen(refs.createDropdown?.hidden !== false);
    });
    refs.createFolderBtn?.addEventListener('click', () => {
        setCreateMenuOpen(false);
        openCreateNodeModal('folder');
    });
    refs.createFileBtn?.addEventListener('click', () => {
        setCreateMenuOpen(false);
        openCreateNodeModal('file');
    });
    refs.createNodeSubmitBtn?.addEventListener('click', () => {
        submitCreateNode().catch((error) => {
            showToast(error.message || '创建失败', 'error');
        });
    });
    refs.createNodeName?.addEventListener('keydown', (event) => {
        if (event.key !== 'Enter') return;
        event.preventDefault();
        submitCreateNode().catch((error) => {
            showToast(error.message || '创建失败', 'error');
        });
    });

    refs.moveSubmitBtn?.addEventListener('click', () => {
        submitMove().catch((error) => {
            showToast(error.message || '移动失败', 'error');
        });
    });

    refs.bindOffering?.addEventListener('change', () => {
        renderBindTargets();
    });
    refs.bindTargets?.addEventListener('change', (event) => {
        const checkbox = event.target.closest('[data-bind-target]');
        if (!checkbox) return;
        const key = checkbox.dataset.bindTarget;
        if (checkbox.checked) {
            state.bind.selected.add(key);
        } else {
            state.bind.selected.delete(key);
        }
        updateBindCount();
    });
    refs.bindSaveBtn?.addEventListener('click', () => {
        submitBindTargets().catch((error) => {
            showToast(error.message || '保存绑定失败', 'error');
        });
    });

    refs.aiExpandBtn?.addEventListener('click', () => {
        openAiExpandModal();
    });

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

    refs.collegeFilter?.addEventListener('change', () => {
        state.filters.college = normalizeKeyword(refs.collegeFilter.value);
        reloadForLibraryFilter('筛选材料失败');
    });

    refs.courseFilter?.addEventListener('change', () => {
        state.filters.course = normalizeKeyword(refs.courseFilter.value);
        reloadForLibraryFilter('筛选材料失败');
    });

    refs.classFilter?.addEventListener('change', () => {
        state.filters.className = normalizeKeyword(refs.classFilter.value);
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
        const treeToggle = event.target.closest('[data-tree-toggle]');
        if (treeToggle) {
            event.preventDefault();
            const nodeId = Number(treeToggle.dataset.treeToggle || 0);
            if (!nodeId || treeToggle.disabled) return;
            if (state.materialWorkspace.expandedIds.has(nodeId)) {
                state.materialWorkspace.expandedIds.delete(nodeId);
            } else {
                state.materialWorkspace.expandedIds.add(nodeId);
            }
            renderDetail(state.activeDetail);
            return;
        }

        const treeSelect = event.target.closest('[data-tree-select]');
        if (treeSelect) {
            event.preventDefault();
            selectWorkspaceNode(Number(treeSelect.dataset.treeSelect || 0)).catch((error) => {
                showToast(error.message || '加载材料失败', 'error');
            });
            return;
        }

        const action = event.target.closest('[data-detail-action]')?.dataset.detailAction;
        if (!action || !state.activeDetail) return;

        if (action === 'create-folder' || action === 'create-file') {
            const targetFolderId = state.activeDetail.node_type === 'folder'
                ? Number(state.activeDetail.id)
                : (state.activeDetail.parent_id ? Number(state.activeDetail.parent_id) : null);
            openCreateNodeModal(action === 'create-file' ? 'file' : 'folder', targetFolderId, { fromWorkspace: true });
            return;
        }
        if (action === 'save-properties') {
            saveActiveMaterialProperties().catch((error) => {
                showToast(error.message || '保存属性失败', 'error');
            });
            return;
        }
        if (action === 'save-content') {
            saveActiveMaterialContent().catch((error) => {
                showToast(error.message || '保存内容失败', 'error');
            });
            return;
        }
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
            openAiRewriteModal('optimize');
            return;
        }
        if (action === 'ai-polish') {
            openAiRewriteModal('polish');
            return;
        }
        if (action === 'ai-regenerate') {
            openAiRewriteModal('regenerate');
            return;
        }
        if (action === 'move') {
            openMoveModal();
            return;
        }
        if (action === 'bind') {
            openBindModal().catch((error) => {
                showToast(error.message || '加载绑定信息失败', 'error');
            });
            return;
        }
        if (action === 'delete') {
            deleteActiveMaterial().catch((error) => {
                showToast(error.message || '删除材料失败', 'error');
            });
        }
    });

    refs.aiImportGroup?.addEventListener('change', () => {
        renderAiImportTypes({ preserveStatus: false });
    });

    refs.aiImportType?.addEventListener('change', () => {
        updateAiImportFormatGuide({ preserveStatus: false });
    });

    refs.aiImportChooseFileBtn?.addEventListener('click', () => {
        if (!state.aiImport.busy) {
            refs.aiImportFileInput?.click();
        }
    });

    refs.aiImportFileInput?.addEventListener('change', () => {
        const selectedFile = refs.aiImportFileInput.files?.[0] || null;
        if (selectedFile && !isAiImportFileAccepted(selectedFile)) {
            state.aiImport.file = null;
            refs.aiImportFileInput.value = '';
            updateAiImportFileLabel();
            setAiImportStatus(getAiImportFormatMismatchMessage(selectedFile), 'warning');
            return;
        }
        state.aiImport.file = selectedFile;
        updateAiImportFileLabel();
        setAiImportStatus('', 'info');
    });

    refs.aiGenerateUploadBtn?.addEventListener('click', () => {
        if (!state.aiGenerate.busy) refs.aiGenerateFileInput?.click();
    });

    refs.aiGenerateFileInput?.addEventListener('change', () => {
        if (state.aiGenerate.busy) {
            refs.aiGenerateFileInput.value = '';
            return;
        }
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
        state.aiGenerate.blockedReason = '';
        state.aiGenerate.sourceBlockReason = '';
        setAiGenerateStatus('', 'info');
        updateAiGenerateTypeOptions();
        if (!applyProcessGenerateBlockIfNeeded()) {
            refreshAiGenerateSourceGuidance();
        }
    });

    refs.aiGenerateType?.addEventListener('change', () => {
        state.aiGenerate.blockedReason = '';
        state.aiGenerate.sourceBlockReason = '';
        setAiGenerateStatus('', 'info');
        updateAiGeneratePromptPlaceholder();
        if (!applyProcessGenerateBlockIfNeeded()) {
            refreshAiGenerateSourceGuidance();
        }
    });

    refs.detail?.addEventListener('input', (event) => {
        const editor = event.target.closest('[data-material-content-editor]');
        if (!editor || !state.activeDetail?.editable) return;
        const content = state.materialWorkspace.content;
        if (Number(content.materialId) !== Number(state.activeDetail.id)) return;
        content.text = String(editor.value || '');
        content.dirty = content.text !== content.originalText;
        const saveButton = refs.detail.querySelector('[data-detail-action="save-content"]');
        if (saveButton) {
            saveButton.disabled = !content.dirty || content.loading;
        }
        if (state.activeDetail.preview_type === 'markdown') {
            renderMarkdown('materials-workspace-rendered-preview', content.text || '');
        }
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
        const pendingDismissButton = event.target.closest('[data-ai-pending-dismiss]');
        if (pendingDismissButton) {
            event.preventDefault();
            event.stopPropagation();
            state.aiPending.delete(pendingDismissButton.dataset.aiPendingDismiss);
            renderList();
            return;
        }

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
        if (action === 'render') {
            renderMaterial(materialId);
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

        openMaterialDetail(materialId).catch((error) => {
            showToast(error.message || '加载详情失败', 'error');
        });
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
            setCreateMenuOpen(false);
        }
        if (event.key === 'Escape' && isDetailModalOpen()) {
            closeDetailModal();
        }
    });

    document.addEventListener('click', (event) => {
        if (refs.uploadMenu && !refs.uploadMenu.contains(event.target)) {
            setUploadMenuOpen(false);
        }
        if (refs.createMenu && !refs.createMenu.contains(event.target)) {
            setCreateMenuOpen(false);
        }
    });
}

const initialAiGeneratePreset = getInitialAiGeneratePreset();
const initialAiImportPreset = getInitialAiImportPreset();

hydrateAiImportDismissals();
bindEvents();
enhancePromptPoolInputs(document);
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

if (initialAiGeneratePreset?.open) {
    window.setTimeout(() => openAiGenerateModal(initialAiGeneratePreset), 0);
}

if (initialAiImportPreset?.open) {
    window.setTimeout(() => openAiImportModal(initialAiImportPreset), 0);
}
