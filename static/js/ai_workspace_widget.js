const CONFIG = window.AI_WORKSPACE_WIDGET_CONFIG || {};
const TASK_REFRESH_MS = 5000;
const TASK_EVENT_POLL_MS = 2500;
const COMPOSER_HEARTBEAT_MS = 10000;
const AGENT_ATTACHMENT_MAX_FILES = 5;
const AGENT_ATTACHMENT_MAX_FILE_BYTES = 10 * 1024 * 1024;
const AGENT_ATTACHMENT_MAX_TOTAL_BYTES = 20 * 1024 * 1024;
const AGENT_ATTACHMENT_ALLOWED_EXTENSIONS = new Set([
    '.txt', '.md', '.markdown', '.csv', '.json', '.xml', '.yaml', '.yml',
    '.py', '.js', '.ts', '.html', '.htm', '.css', '.sql', '.log',
    '.docx', '.doc', '.pdf', '.pptx', '.ppt', '.xlsx', '.xls',
    '.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp',
]);
const AGENT_ATTACHMENT_ALLOWED_TYPES_LABEL = 'txt/md/csv/json/docx/pdf/xlsx/pptx/图片';

let chatComponent = null;
let taskBootstrapLoaded = false;
let taskBootstrapPromise = null;
let agentRuntimeConfigured = null;
let runtimeWarningShown = false;
let taskPollTimer = null;
let selectedTaskId = null;
let composerHeartbeatTimer = null;
let composerTouchTimer = null;
let lastComposerTouchAt = 0;
let composerActive = false;
let agentMode = false;
let agentSubmitting = false;
let lastTaskPayload = { tasks: [], counts: {}, queue_state: {} };
let workflowCatalog = [];
let taskTypesCatalog = [];
let selectedAgentWorkflowKey = '';
let taskEventPollTimer = null;
let taskEventPollBusy = false;
const agentTaskMessages = new Map();
const taskLastEventIds = new Map();
const taskEventStreams = new Map();
let taskEventStreamDisabled = false;
let agentSubscriptionPayload = { subscriptions: [], recent_tasks: [] };
let agentSubscriptionBusy = false;
const taskTerminalNotificationRefreshIds = new Set();
const agentQuestionRefreshes = new Map();
const agentQuestionExpiryTimers = new Map();
const taskDetailRequestVersions = new Map();

function $(selector, root = document) {
    return root.querySelector(selector);
}

function $all(selector, root = document) {
    return Array.from(root.querySelectorAll(selector));
}

function notify(message, type = 'info') {
    const notifier = window.showMessage || window.showToast || window.UI?.showToast || window.UI?.showMessage;
    if (typeof notifier === 'function') {
        notifier(message, type);
    } else {
        console[type === 'error' ? 'error' : 'log'](message);
    }
}

function showRuntimeUnavailableWarning() {
    if (agentRuntimeConfigured !== false || runtimeWarningShown) {
        return;
    }
    runtimeWarningShown = true;
    notify('Agent 运行时未配置，任务会先进入队列等待独立服务。', 'warning');
}

function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function normalizeWorkspaceMarkdown(value) {
    if (typeof window.normalizeAIChatMarkdown === 'function') {
        return window.normalizeAIChatMarkdown(value);
    }
    return String(value ?? '').replace(/\r\n?/g, '\n').trim();
}

function renderWorkspaceMarkdown(value, fallback = '') {
    const normalized = normalizeWorkspaceMarkdown(value);
    if (!normalized) {
        return fallback;
    }
    if (typeof window.safeMarkedParse === 'function') {
        return window.safeMarkedParse(normalized, fallback);
    }
    return escapeHtml(normalized).replace(/\n/g, '<br>');
}

function clampText(value, maxLength) {
    const text = String(value ?? '').replace(/\s+/g, ' ').trim();
    return text.length > maxLength ? `${text.slice(0, maxLength).trim()}...` : text;
}

function getSelectedText() {
    const selected = String(window.getSelection?.() || '').trim();
    return selected ? clampText(selected, 800) : '';
}

function visibleHeadingTexts() {
    return $all('h1, h2, h3')
        .filter((item) => item.offsetParent !== null)
        .map((item) => clampText(item.textContent, 80))
        .filter(Boolean)
        .slice(0, 12);
}

function activeNavText() {
    const active = $('.active, [aria-current="page"], [data-workspace-nav].active, [data-classroom-message-tab].is-active');
    return active ? clampText(active.textContent, 80) : '';
}

function collectSelectedSessionContext() {
    const session = window.LANSHARE_SELECTED_CLASSROOM_SESSION || null;
    if (!session || typeof session !== 'object') {
        return {};
    }
    return {
        id: session.id || null,
        orderIndex: session.orderIndex || session.order_index || null,
        title: clampText(session.title || '', 160),
        content: clampText(session.content || '', 1800),
        sessionDate: session.sessionDate || session.session_date || '',
        sectionCount: session.sectionCount || session.section_count || 1,
        learningMaterialId: session.learningMaterialId || session.learning_material_id || null,
        learningMaterialName: session.learningMaterialName || session.learning_material_name || '',
        learningMaterialPath: session.learningMaterialPath || session.learning_material_path || '',
    };
}

function collectClassroomContext() {
    const appConfig = window.APP_CONFIG || {};
    if (!Object.keys(appConfig).length) {
        return {};
    }
    const selectedSession = collectSelectedSessionContext();
    return {
        classOfferingId: appConfig.classOfferingId || CONFIG.classOfferingId || null,
        courseId: appConfig.courseId || null,
        userRole: appConfig.userInfo?.role || CONFIG.userRole || '',
        courseName: appConfig.classroom?.course_name || appConfig.classroom?.courseName || '',
        className: appConfig.classroom?.class_name || appConfig.classroom?.className || '',
        currentSection: activeNavText(),
        teachingPlan: clampText(appConfig.teachingPlan || appConfig.classroom?.teaching_plan || '', 1200),
        learningProgress: appConfig.learningProgress?.summary || appConfig.learningOverview || null,
        selectedSession: Object.keys(selectedSession).length ? selectedSession : null,
    };
}

function collectMaterialContext() {
    const material = window.MATERIAL_VIEWER || {};
    const materialContext = window.MATERIAL_VIEWER_CONTEXT || {};
    if (!Object.keys(material).length && !Object.keys(materialContext).length) {
        return {};
    }
    return {
        materialId: materialContext.materialId || material.id || CONFIG.materialId || null,
        materialName: materialContext.materialName || material.name || '',
        materialPath: material.material_path || '',
        classOfferingId: materialContext.classOfferingId || CONFIG.classOfferingId || null,
        sessionId: materialContext.sessionId || null,
        headings: $all('#viewer-toc button, #viewer-content h1, #viewer-content h2, #viewer-content h3')
            .map((item) => clampText(item.textContent, 90))
            .filter(Boolean)
            .slice(0, 16),
        aiSummary: clampText(material.ai_parse_result?.summary || '', 1000),
    };
}

function collectAssignmentContext() {
    const assignmentTitle = $('[data-assignment-title], .assignment-title, h1')?.textContent || '';
    const statusText = $('.status-badge, [data-assignment-status]')?.textContent || '';
    if (!CONFIG.assignmentId) {
        return {};
    }
    return {
        assignmentId: CONFIG.assignmentId || null,
        classOfferingId: CONFIG.classOfferingId || null,
        title: clampText(assignmentTitle, 140),
        status: clampText(statusText, 80),
        visibleStats: $all('.stat-card, .assignment-stat, [data-submission-stat]')
            .map((item) => clampText(item.textContent, 120))
            .filter(Boolean)
            .slice(0, 10),
    };
}

function collectManageContext() {
    const manageRoot = $('.manage-main, .manage-content');
    if (!manageRoot) {
        return {};
    }
    return {
        pageTitle: clampText($('.manage-topbar-page strong, .manage-header-title, h1')?.textContent || document.title, 120),
        activePage: clampText($('.manage-nav-item.active, .manage-topbar-page strong')?.textContent || '', 120),
        visibleSections: visibleHeadingTexts(),
    };
}

function collectDashboardContext() {
    const dashboardRoot = $('[data-dashboard-root], .dashboard-grid, .dashboard-main');
    if (!dashboardRoot) {
        return {};
    }
    return {
        pageTitle: clampText(document.title, 120),
        activeCourseCards: $all('[data-classroom-card], .classroom-card, .course-card')
            .map((item) => clampText(item.textContent, 140))
            .filter(Boolean)
            .slice(0, 8),
    };
}

function collectPageContext() {
    const context = {
        page: {
            title: clampText(document.title, 140),
            path: window.location.pathname,
            search: window.location.search,
            headings: visibleHeadingTexts(),
            activeArea: activeNavText(),
            selectedText: getSelectedText(),
        },
        user: {
            role: CONFIG.userRole || '',
            name: CONFIG.userName || '',
        },
        classOfferingId: CONFIG.classOfferingId || null,
        assignmentId: CONFIG.assignmentId || null,
        materialId: CONFIG.materialId || null,
        sessionId: collectSelectedSessionContext().id || window.MATERIAL_VIEWER_CONTEXT?.sessionId || null,
        sessionOrderIndex: collectSelectedSessionContext().orderIndex || null,
        classroomContext: collectClassroomContext(),
        materialContext: collectMaterialContext(),
        assignmentContext: collectAssignmentContext(),
        manageContext: collectManageContext(),
        dashboardContext: collectDashboardContext(),
    };
    if (selectedAgentWorkflowKey) {
        const selectedWorkflow = workflowCatalog.find((item) => item.key === selectedAgentWorkflowKey);
        context.agentWorkflowKey = selectedAgentWorkflowKey;
        if (selectedWorkflow) {
            context.agentWorkflow = {
                key: selectedWorkflow.key,
                name: selectedWorkflow.name,
                taskType: selectedWorkflow.task_type || '',
            };
        }
    }

    Object.keys(context).forEach((key) => {
        const value = context[key];
        if (value && typeof value === 'object' && !Array.isArray(value) && !Object.keys(value).length) {
            delete context[key];
        }
    });
    return context;
}

function formatContextForPrompt(context = collectPageContext()) {
    const lines = [
        '【当前页面背景】',
        `页面：${context.page?.title || document.title}`,
        `路径：${context.page?.path || window.location.pathname}`,
    ];
    if (context.page?.activeArea) {
        lines.push(`当前区域：${context.page.activeArea}`);
    }
    if (context.page?.selectedText) {
        lines.push(`用户选中文本：${context.page.selectedText}`);
    }
    if (context.classroomContext?.courseName || context.classroomContext?.className) {
        lines.push(`课堂：${context.classroomContext.courseName || ''} ${context.classroomContext.className || ''}`.trim());
    }
    if (context.classroomContext?.selectedSession?.title) {
        const selected = context.classroomContext.selectedSession;
        lines.push(`当前课时：第 ${selected.orderIndex || ''} 次课 ${selected.title}`.trim());
        if (selected.learningMaterialName) {
            lines.push(`当前课时文档：${selected.learningMaterialName} ${selected.learningMaterialPath || ''}`.trim());
        }
    }
    if (context.materialContext?.materialName) {
        lines.push(`材料：${context.materialContext.materialName} ${context.materialContext.materialPath || ''}`.trim());
    }
    if (context.assignmentContext?.title) {
        lines.push(`作业/考试：${context.assignmentContext.title}`);
    }
    if (context.manageContext?.pageTitle) {
        lines.push(`管理页面：${context.manageContext.pageTitle}`);
    }
    const headings = context.page?.headings || [];
    if (headings.length) {
        lines.push(`页面重点：${headings.join(' / ')}`);
    }
    const serverUseful = JSON.stringify({
        classOfferingId: context.classOfferingId,
        assignmentId: context.assignmentId,
        materialId: context.materialId,
        classroomContext: context.classroomContext,
        materialContext: context.materialContext,
        assignmentContext: context.assignmentContext,
        manageContext: context.manageContext,
    });
    lines.push(`结构化线索：${serverUseful}`);
    return lines.join('\n').slice(0, 12000);
}

function refreshContextPreview() {
    const context = collectPageContext();
    const pieces = [
        context.materialContext?.materialName,
        context.assignmentContext?.title,
        context.classroomContext?.courseName,
        context.manageContext?.pageTitle,
        context.page?.title,
    ].filter(Boolean);
    const label = clampText(pieces[0] || '当前页面', 90);
    ['#ai-agent-context-title', '#agent-task-context-title'].forEach((selector) => {
        const node = $(selector);
        if (node) {
            node.textContent = label;
        }
    });
    const subtitle = $('#ai-workspace-subtitle');
    if (subtitle) {
        subtitle.textContent = agentMode ? `Agent 任务 · 全平台队列 · ${label}` : `普通对话 · ${label}`;
    }
}

function workflowByKey(key) {
    return workflowCatalog.find((item) => item.key === key) || null;
}

function recommendedWorkflowKeys(context = collectPageContext()) {
    if (context.assignmentId || context.assignmentContext?.title) {
        return ['assignment_exam_workflow', 'submission_grading_feedback', 'student_support', 'classroom_preparation'];
    }
    if (context.materialId || context.materialContext?.materialName) {
        return ['lesson_document_generation', 'material_operations', 'classroom_preparation', 'blog_and_reflection'];
    }
    if (context.classOfferingId || context.classroomContext?.courseName) {
        return ['classroom_preparation', 'lesson_document_generation', 'assignment_exam_workflow', 'discussion_collaboration'];
    }
    if (context.manageContext?.pageTitle) {
        return ['course_roster_setup', 'operations_admin', 'gongwen_lookup', 'material_operations'];
    }
    return ['classroom_preparation', 'assignment_exam_workflow', 'gongwen_lookup', 'blog_and_reflection'];
}

function recommendedAgentStarters(context = collectPageContext()) {
    const seen = new Set();
    const preferred = recommendedWorkflowKeys(context)
        .map((key) => workflowByKey(key))
        .filter(Boolean);
    const fallback = workflowCatalog.filter(Boolean);
    return [...preferred, ...fallback]
        .filter((item) => {
            if (!item?.key || seen.has(item.key)) {
                return false;
            }
            seen.add(item.key);
            return true;
        })
        .slice(0, 4);
}

function currentActiveOwnAgentTask() {
    const tasks = Array.isArray(lastTaskPayload.tasks) ? lastTaskPayload.tasks : [];
    const activeTasks = tasks.filter((task) => task?.is_owner && task?.is_active);
    if (!activeTasks.length) {
        return null;
    }
    return activeTasks.find((task) => Number(task.id) === Number(selectedTaskId)) || activeTasks[0];
}

function currentSelectedOwnAgentTask({ terminalOnly = false } = {}) {
    const tasks = Array.isArray(lastTaskPayload.tasks) ? lastTaskPayload.tasks : [];
    const task = tasks.find((item) => item?.is_owner && Number(item.id) === Number(selectedTaskId));
    if (!task) {
        return null;
    }
    if (terminalOnly && !task.is_terminal) {
        return null;
    }
    return task;
}

function currentAgentComposerTargetTask() {
    return currentActiveOwnAgentTask() || currentSelectedOwnAgentTask({ terminalOnly: true });
}

function hasCurrentAgentTaskContext() {
    return Boolean(currentActiveOwnAgentTask() || currentSelectedOwnAgentTask() || visibleAgentTaskIds().length);
}

function workflowStarterCopy(item = {}) {
    const steps = Array.isArray(item.steps) ? item.steps : [];
    return steps[0] || item.agent_capability || '生成清单、草案和确认项。';
}

function renderAgentStarters() {
    const panel = $('#ai-agent-starters');
    if (!panel) {
        return;
    }
    const surface = currentChatSurface();
    const hasInput = Boolean(surface.textarea?.value.trim());
    const hasTaskContext = hasCurrentAgentTaskContext();
    const starters = recommendedAgentStarters();
    if (!agentMode || hasTaskContext || hasInput || !starters.length) {
        panel.hidden = true;
        panel.innerHTML = '';
        return;
    }
    panel.hidden = false;
    const contextLabel = $('#ai-agent-context-title')?.textContent || '当前页面';
    panel.innerHTML = `
        <div class="ai-agent-starters__head">
            <strong>当前页面推荐</strong>
            <small>${escapeHtml(contextLabel)}</small>
        </div>
        <div class="ai-agent-starters__grid">
            ${starters.map((item) => `
                <button type="button" class="ai-agent-starter ${item.key === selectedAgentWorkflowKey ? 'is-selected' : ''}" data-agent-starter="${escapeHtml(item.key)}">
                    <strong>${escapeHtml(item.name || '教学事务')}</strong>
                    <small>${escapeHtml(workflowStarterCopy(item))}</small>
                </button>
            `).join('')}
        </div>
    `;
}

function applyAgentStarter(button) {
    const key = button.dataset.agentStarter || '';
    const workflow = workflowByKey(key);
    if (!workflow) {
        notify('这个 Agent 工作流暂不可用。', 'warning');
        return;
    }
    selectedAgentWorkflowKey = workflow.key || '';
    const surface = currentChatSurface();
    if (surface.textarea) {
        surface.textarea.value = workflow.starter_prompt || workflowStarterCopy(workflow);
        resetTextareaHeight(surface.textarea);
        surface.textarea.focus();
    }
    if (surface.sendBtn) {
        surface.sendBtn.disabled = false;
    }
    refreshContextPreview();
    renderAgentStarters();
    notify('已载入 Agent 任务骨架。', 'success');
}

function openWorkspaceModal() {
    if (chatComponent && typeof chatComponent.openChat === 'function') {
        chatComponent.openChat();
        return true;
    }
    const modal = $('#ai-chat-modal');
    const fab = $('#ai-chat-fab');
    const container = $('.ai-chat-container', modal || document);
    if (!modal) {
        return false;
    }
    refreshContextPreview();
    modal.style.display = 'block';
    modal.setAttribute('aria-hidden', 'false');
    if (fab) {
        fab.style.display = 'none';
    }
    container?.classList.remove('fullscreen');
    document.body.classList.remove('ai-chat-fullscreen-active');
    window.setTimeout(ensureWorkspaceWindowVisible, 0);
    window.dispatchEvent(new CustomEvent('ai-workspace:opened', { detail: collectPageContext() }));
    return true;
}

function readAgentTaskDeepLinkId() {
    try {
        const params = new URLSearchParams(window.location.search);
        const raw = params.get('agent_task') || params.get('agentTask');
        const taskId = Number(raw || 0);
        return Number.isInteger(taskId) && taskId > 0 ? taskId : 0;
    } catch {
        return 0;
    }
}

function shouldOpenAgentSubscriptionsFromDeepLink() {
    try {
        const params = new URLSearchParams(window.location.search);
        return ['agent_subscriptions', 'agentSubscriptions'].some((key) => {
            const raw = params.get(key);
            return raw === '1' || raw === 'true' || raw === 'open';
        });
    } catch {
        return false;
    }
}

function clearAgentTaskDeepLink() {
    if (!window.history?.replaceState) {
        return;
    }
    try {
        const url = new URL(window.location.href);
        url.searchParams.delete('agent_task');
        url.searchParams.delete('agentTask');
        window.history.replaceState(window.history.state, '', `${url.pathname}${url.search}${url.hash}`);
    } catch {
        // Deep link cleanup is cosmetic; loading the task matters more.
    }
}

function clearAgentSubscriptionDeepLink() {
    if (!window.history?.replaceState) {
        return;
    }
    try {
        const url = new URL(window.location.href);
        url.searchParams.delete('agent_subscriptions');
        url.searchParams.delete('agentSubscriptions');
        window.history.replaceState(window.history.state, '', `${url.pathname}${url.search}${url.hash}`);
    } catch {
        // Deep link cleanup is cosmetic; loading the subscription panel matters more.
    }
}

async function handleAgentTaskDeepLink() {
    const taskId = readAgentTaskDeepLinkId();
    if (!taskId || !CONFIG.taskCenterEnabled) {
        return;
    }
    if (!openWorkspaceModal()) {
        return;
    }
    setAgentMode(true, { persist: false });
    setAgentHistoryOpen(true);
    selectedTaskId = taskId;
    try {
        await loadBootstrap();
        const task = await loadTaskDetail(taskId, { autoScroll: true });
        await refreshTasks({ silent: true });
        if (task?.is_owner) {
            notify('已打开通知对应的 Agent 任务。', 'success');
        }
    } catch (error) {
        notify(error.message || '无法打开通知对应的 Agent 任务', 'error');
    } finally {
        clearAgentTaskDeepLink();
    }
}

async function handleAgentSubscriptionDeepLink() {
    if (!shouldOpenAgentSubscriptionsFromDeepLink() || !CONFIG.taskCenterEnabled) {
        return;
    }
    if (!openWorkspaceModal()) {
        return;
    }
    setAgentMode(true, { persist: false });
    setAgentHistoryOpen(true);
    const panel = $('#ai-agent-subscriptions-panel');
    if (panel) {
        panel.open = true;
        panel.scrollIntoView({ block: 'nearest' });
    }
    try {
        await loadBootstrap();
        await loadAgentSubscriptions({ silent: false });
        notify('已打开 Agent 定时任务。', 'success');
    } catch (error) {
        notify(error.message || '无法打开 Agent 定时任务', 'error');
    } finally {
        clearAgentSubscriptionDeepLink();
    }
}

function topbarBottomOffset() {
    const candidates = [
        '.app-topbar',
        'header.navbar',
        '.main-topbar',
        '.teacher-topbar',
        '.global-topbar',
    ];
    for (const selector of candidates) {
        const node = $(selector);
        if (!node) {
            continue;
        }
        const rect = node.getBoundingClientRect();
        if (rect.width > 200 && rect.height > 20 && rect.bottom > 0 && rect.bottom < window.innerHeight * 0.35) {
            return Math.ceil(rect.bottom);
        }
    }
    return 0;
}

function ensureWorkspaceWindowVisible() {
    const container = $('.ai-workspace-container');
    if (!container || container.classList.contains('fullscreen')) {
        return;
    }
    const isCompactViewport = window.innerWidth <= 768;
    const margin = isCompactViewport ? 10 : 16;
    const minTop = Math.max(margin, topbarBottomOffset() + 8);
    const maxHeight = Math.max(360, window.innerHeight - minTop - margin);
    const rect = container.getBoundingClientRect();
    const availableWidth = Math.max(260, window.innerWidth - margin * 2);
    const preferredWidth = Math.min(Math.max(Math.round(window.innerWidth * 0.52), 560), 860, availableWidth);
    const preferredHeight = Math.min(Math.max(Math.round(window.innerHeight * 0.76), 560), 760, maxHeight);
    const hasManualRect = Boolean(chatComponent?.lastWindowRect);
    const width = isCompactViewport
        ? availableWidth
        : Math.min(hasManualRect ? (rect.width || preferredWidth) : preferredWidth, Math.max(300, availableWidth));
    const height = Math.min(hasManualRect ? (rect.height || preferredHeight) : preferredHeight, maxHeight);
    const currentTop = Number.isFinite(rect.top) ? rect.top : minTop;
    const shouldSnapNearTop = currentTop < minTop || currentTop > minTop + 80;
    const top = shouldSnapNearTop
        ? minTop
        : Math.min(Math.max(currentTop, minTop), Math.max(minTop, window.innerHeight - height - margin));
    const left = isCompactViewport ? margin : Math.min(Math.max(rect.left, margin), Math.max(margin, window.innerWidth - width - margin));
    container.style.width = `${Math.round(width)}px`;
    container.style.height = `${Math.round(height)}px`;
    container.style.top = `${Math.round(top)}px`;
    container.style.left = `${Math.round(left)}px`;
    container.style.right = 'auto';
    container.style.bottom = 'auto';
}

async function apiJson(url, options = {}) {
    const { headers = {}, ...restOptions } = options;
    const isFormData = typeof FormData !== 'undefined' && restOptions.body instanceof FormData;
    const response = await fetch(url, {
        credentials: 'same-origin',
        ...restOptions,
        headers: {
            Accept: 'application/json',
            ...(restOptions.body && !isFormData ? { 'Content-Type': 'application/json' } : {}),
            ...headers,
        },
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
        if (window.handleAuthFailureResponse) {
            await window.handleAuthFailureResponse(response, data);
        }
        throw new Error(data.detail || data.message || `请求失败：${response.status}`);
    }
    return data;
}

function setQueueState(queueState = {}, counts = {}) {
    const queued = Number(queueState.queued_count ?? counts.queued ?? 0);
    const runningCount = Math.max(0, Number(queueState.running_count ?? (queueState.is_running ? 1 : 0)));
    const globalConcurrency = Math.max(1, Number(queueState.global_concurrency ?? 1));
    const runningLabel = globalConcurrency > 1
        ? `运行 ${Math.min(runningCount, globalConcurrency)}/${globalConcurrency}`
        : '运行中';
    ['#ai-agent-queue-count', '#ai-agent-fab-queue-badge'].forEach((selector) => {
        const node = $(selector);
        if (node) {
            node.textContent = String(queued);
            node.toggleAttribute('data-empty', queued <= 0);
        }
    });

    const state = queueState.is_running ? 'red' : ((queueState.is_composing || queued > 0) ? 'yellow' : 'green');
    let tooltip = 'Agent 队列空闲';
    let modebarStatus = queued > 0 ? `排队 ${queued}` : '队列空闲';
    if (state === 'red') {
        const running = queueState.running || {};
        tooltip = `${running.teacher_name || '某位用户'}的${running.public_summary || running.task_type_label || 'Agent 任务'}正在运行（${runningLabel}）`;
        modebarStatus = queued > 0 ? `${runningLabel} · 排队 ${queued}` : runningLabel;
    } else if (state === 'yellow') {
        const composer = queueState.composer || {};
        if (queued > 0) {
            tooltip = `已有 ${queued} 个 Agent 任务在等待全平台队列`;
            modebarStatus = `排队 ${queued}`;
        } else {
            tooltip = `${composer.teacher_name || '某位用户'}正在编写新任务`;
            modebarStatus = '有人正在编辑';
        }
    }
    ['#ai-agent-traffic-light', '#ai-agent-fab-light', '#ai-agent-modebar-light'].forEach((selector) => {
        const node = $(selector);
        if (!node) {
            return;
        }
        node.classList.remove('is-green', 'is-yellow', 'is-red');
        node.classList.add(`is-${state}`);
        node.title = tooltip;
    });
    const statusNode = $('#ai-agent-modebar-status');
    if (statusNode) {
        statusNode.textContent = modebarStatus;
    }
    const agentModeOption = $('[data-ai-mode-select="agent"]');
    if (agentModeOption) {
        agentModeOption.classList.remove('is-green', 'is-yellow', 'is-red');
        agentModeOption.classList.add(`is-${state}`);
        agentModeOption.title = tooltip;
    }
}

function inferAgentTaskType(instruction, context = collectPageContext()) {
    const text = `${instruction || ''} ${context.page?.title || ''} ${context.page?.activeArea || ''}`.toLowerCase();
    if (/公文|红头|文号|校发|院发|教学发|(学校|学院).{0,6}(规定|通知|文件|要求)|规章|办法|细则/.test(text)) {
        return 'gongwen_lookup';
    }
    if (/学习文档|导学|下一节课|下次课|第\s*\d+\s*(课|次)|lesson|document/.test(text)) {
        return 'lesson_document';
    }
    if (/作业|考试|试卷|题目|出题|课堂作业|测验|exam|quiz|assignment/.test(text)) {
        return 'assignment_blueprint';
    }
    if (/博客|博文|blog|反思|发布文章/.test(text)) {
        return 'blog_draft';
    }
    if (/通知|提醒|低分|未交|学生|私信|message|notice/.test(text)) {
        return 'student_notification';
    }
    if (/(材料|课件|资料|教材|素材|文件|material|resource)/.test(text) && /(整理|收集|归档|汇总|重命名|移动|删除|material|resource)/.test(text)) {
        return 'course_material_digest';
    }
    return 'general_teaching_task';
}

function currentChatSurface() {
    const messagesBox = chatComponent?.messagesBox || $('#ai-chat-messages-box');
    return {
        messagesBox,
        textarea: chatComponent?.textarea || $('#ai-chat-textarea'),
        sendBtn: chatComponent?.sendBtn || $('#ai-chat-btn-send'),
        attachBtn: chatComponent?.attachBtn || $('#ai-chat-btn-attach'),
        deepThinkBtn: chatComponent?.deepThinkBtn || $('#ai-deep-think-btn'),
        scrollToBottom: (force = false) => {
            if (chatComponent?.scrollToBottom) {
                chatComponent.scrollToBottom(force);
                return;
            }
            if (!messagesBox) {
                return;
            }
            // 无 chatComponent 时的粘性兜底：用户已上翻就不要拽回底部。
            const nearBottom = messagesBox.scrollHeight - messagesBox.scrollTop - messagesBox.clientHeight <= 80;
            if (force || nearBottom) {
                messagesBox.scrollTop = messagesBox.scrollHeight;
            }
        },
        renderMessage: (role, content, attachments = []) => {
            if (chatComponent?.renderMessage) {
                chatComponent.renderMessage(role, content, attachments);
                return;
            }
            if (!messagesBox) {
                return;
            }
            const msgDiv = document.createElement('div');
            msgDiv.className = `ai-chat-message ${role}`;
            const bubble = document.createElement('div');
            bubble.className = 'bubble';
            const p = document.createElement('p');
            p.textContent = content || '';
            bubble.appendChild(p);
            msgDiv.appendChild(bubble);
            messagesBox.appendChild(msgDiv);
            messagesBox.scrollTop = messagesBox.scrollHeight;
        },
    };
}

function resetTextareaHeight(textarea) {
    if (!textarea) {
        return;
    }
    textarea.style.height = 'auto';
    textarea.style.height = textarea.value ? `${textarea.scrollHeight}px` : 'auto';
}

function formatElapsed(seconds) {
    const total = Math.max(0, Number(seconds || 0));
    const minutes = Math.floor(total / 60);
    const rest = total % 60;
    if (minutes >= 60) {
        const hours = Math.floor(minutes / 60);
        return `${hours}小时${minutes % 60}分`;
    }
    return `${minutes}分${rest}秒`;
}

function formatDateTime(value) {
    const raw = String(value || '').trim();
    if (!raw) return '';
    const normalized = raw.includes('T') ? raw : raw.replace(' ', 'T');
    const date = new Date(normalized);
    if (Number.isNaN(date.getTime())) {
        return raw.replace('T', ' ').replace(/\.\d+(\+\d{2}:\d{2}|Z)?$/, '').replace(/\+\d{2}:\d{2}$/, '');
    }
    const pad = (num) => String(num).padStart(2, '0');
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function statusClass(status) {
    return `is-${String(status || 'queued').replace(/[^a-z0-9_-]/gi, '')}`;
}

function terminalTone(status) {
    if (status === 'completed') return 'is-result';
    if (status === 'failed') return 'is-error';
    if (status === 'canceled') return 'is-warning';
    return '';
}

function terminalTitle(status) {
    if (status === 'completed') return '最终结论：成功';
    if (status === 'failed') return '最终结论：失败';
    if (status === 'canceled') return '最终结论：已取消';
    return '当前状态';
}

function taskCompletionPresentation(task) {
    const detail = task.result_detail || {};
    const pendingDomain = (detail.completion_blockers || []).some((item) => item.code === 'domain_job_pending')
        || (detail.platform_operations || []).some((item) => ['queued', 'running'].includes(item.domain_result?.status));
    const pendingRequests = (detail.platform_requests || []).some((item) => item.status !== 'observed_http_result');
    if (task.is_terminal && task.status !== 'canceled') {
        if (pendingDomain) return { label: '业务结果待跟进', title: '已提交，等待业务结果', tone: 'is-warning', pendingDomain: true };
        if (detail.completion_kind === 'partial') return { label: '部分完成', title: '已保留结果，仍有待完成事项', tone: 'is-warning', pendingRequests };
        if (detail.completion_kind === 'authority_changed') return { label: '权限变更已提交', title: '已按新权限停止当前任务', tone: 'is-result' };
        if (detail.completion_kind === 'observed_http_result') return { label: '已收到平台响应', title: '平台已响应，业务结果以记录为准', tone: 'is-result' };
        if (task.status === 'completed' && detail.business_outcome_verified === true) return { label: '业务结果已核验', title: '平台操作已完成并核验', tone: 'is-result' };
        if (task.status === 'completed' && detail.completion_kind === 'deliverable') return { label: '内容已交付', title: '已交付内容', tone: 'is-result' };
    }
    return { label: task.status_label || task.status, title: terminalTitle(task.status), tone: terminalTone(task.status), pendingDomain, pendingRequests };
}

function renderAgentOperationReceipts(detail = {}, taskId = null) {
    const operations = Array.isArray(detail.platform_operations) ? detail.platform_operations.slice(0, 100) : [];
    const requests = Array.isArray(detail.platform_requests) ? detail.platform_requests.slice(0, 100) : [];
    const blockers = Array.isArray(detail.completion_blockers) ? detail.completion_blockers : [];
    if (!operations.length && !requests.length && !blockers.length) return '';
    const blockerLabels = { domain_job_pending: '已提交的领域任务尚在排队或运行，请跟进原任务。', domain_job_failed: '领域任务未成功完成，请查看原任务的原因。',
        domain_job_identity_mismatch: '领域任务的归属发生变化，结果尚未核验。', domain_material_binding_missing: '生成材料或课时绑定尚未核验。',
        domain_material_integrity_failed: '生成文件完整性尚未核验。', domain_result_unverified: '业务结果尚未核验。',
        unprocessed_supplements: '仍有补充说明未处理。', unanswered_questions: '仍有未回答的问题。', unexecuted_proposals: '仍有建议动作未执行。',
        platform_request_uncertain: '平台请求结果尚不确定，请核对原业务记录后再决定下一步。', platform_request_submitted: '平台已接收请求，还需跟进后续处理结果。',
        platform_request_admitted: '平台请求已登记，尚无执行回执。', platform_request_executing: '平台请求尚无确定响应，请先核对。', request_identity_mismatch: '请求归属尚未核验。' };
    return `<div class="ai-task-detail__block ai-agent-operation-receipts"><h4>平台操作回执</h4>
        ${operations.map((item) => {
            const result = item.result || {};
            const domain = item.domain_result;
            const status = domain?.status || item.completion_status || item.status;
            const labels = { queued: '已提交 · 排队中', running: '已提交 · 处理中', committed: '已提交', completed: domain?.binding_verified ? '成品及课时绑定已核验' : '已完成', failed: '领域任务失败', canceled: '已取消', unverified: '结果待核验' };
            const href = safeLocalHref(result.url);
            return `<div class="ai-agent-operation-receipt"><div><strong>${escapeHtml(domain ? '课时文档生成' : result.label || item.action || '平台操作')}</strong><span>${escapeHtml(labels[status] || '回执待核验')}</span></div>
                ${domain?.generation_task_id ? `<small>生成任务 #${escapeHtml(domain.generation_task_id)}${domain.generated_material_path ? ` · ${escapeHtml(domain.generated_material_path)}` : ''}</small>` : ''}
                ${href ? `<a href="${escapeHtml(href)}" target="_blank" rel="noopener">${domain && ['queued', 'running'].includes(status) ? '前往课堂查看当前状态' : '查看业务记录'}</a>` : ''}</div>`;
        }).join('')}
        ${requests.map((item) => {
            const labels = { observed_http_result: '已收到平台响应 · 业务结果未单独核验', submitted: '平台已接收 · 后续结果待跟进', uncertain: '结果不确定 · 请先核对', admitted: '请求已登记', executing: '请求执行中 · 等待回执' };
            const names = { 'http.blog.bookmark.toggle': '博客收藏', 'http.blog.follow.set': '博客关注设置', 'http.blog.follows.list': '读取博客关注',
                'http.messages.read': '消息已读设置', 'http.messages.private.open': '打开私信', 'http.messages.blocks.list': '读取私信屏蔽',
                'http.messages.blocks.add': '屏蔽私信联系人', 'http.messages.blocks.remove': '解除私信屏蔽',
                'http.polls.snapshot': '课堂投票', 'http.polls.detail': '投票详情', 'http.polls.vote': '提交投票' };
            const status = Number(item.observation?.http_status);
            return `<div class="ai-agent-operation-receipt ai-agent-request-receipt"><div><strong>${escapeHtml(names[item.capability_key] || '平台业务请求')}</strong><span>${escapeHtml(labels[item.status] || '请求回执待核对')}</span></div>
                <small>请求编号 ${escapeHtml(item.request_id || item.operation_id || '')}${Number.isInteger(status) && status >= 100 && status <= 599 ? ` · HTTP ${status}` : ''}</small>
                ${taskId && item.request_id ? `<button type="button" class="btn btn-outline btn-sm" data-agent-reconcile-open="${escapeHtml(item.request_id)}" data-task-id="${escapeHtml(taskId)}">查看请求与核对</button>` : ''}</div>`;
        }).join('')}
        ${requests.length ? '<small>以上保留平台响应事实；不确定或仍在处理的请求需先核对原记录，避免重复执行。</small>' : ''}
        ${requests.length && taskId ? `<button type="button" class="btn btn-outline btn-sm" data-agent-request-list="${escapeHtml(taskId)}">全部平台请求</button>` : ''}
        ${blockers.length ? `<ul class="ai-agent-completion-blockers">${Array.from(new Set(blockers.map((item) => blockerLabels[item.code] || '仍有操作需要核对回执。'))).map((message) => `<li>${escapeHtml(message)}</li>`).join('')}</ul>` : ''}
        ${operations.some((item) => item.domain_result) ? '<small>以上为 Agent 结束时核对的状态；已提交的生成任务会继续处理，可前往课堂查看最新结果。</small>' : ''}
    </div>`;
}

async function openAgentRequestList(button) {
    let dialog = document.querySelector('[data-agent-request-list-dialog]');
    if (!dialog) {
        dialog = document.createElement('dialog');
        dialog.className = 'ai-agent-reconciliation-dialog';
        dialog.dataset.agentRequestListDialog = 'true';
        dialog.setAttribute('aria-labelledby', 'agent-request-list-title');
        dialog.innerHTML = '<header><h3 id="agent-request-list-title">平台请求记录</h3><button type="button" data-request-list-close aria-label="关闭请求记录">×</button></header><p>逐条查看原请求与平台响应，需要时再保存独立核对声明。</p><div data-request-list-items></div><p data-request-list-feedback role="status" aria-live="polite"></p><footer><button type="button" class="btn btn-outline" data-request-list-close>关闭</button><button type="button" class="btn btn-outline" data-request-list-more>加载更多</button></footer>';
        dialog.addEventListener('keydown', (event) => { if (event.key === 'Escape') event.stopPropagation(); });
        dialog.addEventListener('click', (event) => {
            if (event.target.closest('[data-request-list-close]')) dialog.close();
            if (event.target.closest('[data-request-list-more]')) loadAgentRequestPage(dialog).catch(() => {});
            const detailButton = event.target.closest('[data-agent-reconcile-open]');
            if (detailButton) openAgentReconciliation(detailButton).catch(() => {});
        });
        dialog.addEventListener('close', () => {
            dialog.agentListVersion = (dialog.agentListVersion || 0) + 1;
            const opener = dialog.agentOpener?.isConnected ? dialog.agentOpener : Array.from(document.querySelectorAll('[data-agent-request-list]')).find((item) => item.dataset.agentRequestList === dialog.dataset.taskId);
            opener?.focus({ preventScroll: true });
        });
        document.body.appendChild(dialog);
    }
    if (dialog.open) return;
    dialog.agentOpener = button;
    dialog.dataset.taskId = button.dataset.agentRequestList;
    dialog.agentListVersion = (dialog.agentListVersion || 0) + 1;
    dialog.agentRequestIds = new Set();
    dialog.agentNextOffset = 0;
    dialog.dataset.loading = 'false';
    dialog.querySelector('[data-request-list-items]').replaceChildren();
    dialog.querySelector('[data-request-list-more]').hidden = false;
    dialog.showModal();
    await loadAgentRequestPage(dialog);
}

async function loadAgentRequestPage(dialog) {
    if (dialog.dataset.loading === 'true' || dialog.agentNextOffset === null) return;
    const version = dialog.agentListVersion;
    const more = dialog.querySelector('[data-request-list-more]');
    const feedback = dialog.querySelector('[data-request-list-feedback]');
    dialog.dataset.loading = 'true'; more.disabled = true;
    feedback.textContent = '正在读取请求记录…';
    try {
        const data = await apiJson(`/api/agent-tasks/${encodeURIComponent(dialog.dataset.taskId)}/platform-requests?limit=20&offset=${dialog.agentNextOffset}`);
        if (!dialog.open || dialog.agentListVersion !== version) return;
        const container = dialog.querySelector('[data-request-list-items]');
        for (const item of (Array.isArray(data.requests) ? data.requests : [])) {
            const id = String(item.id || '');
            if (!id || dialog.agentRequestIds.has(id)) continue;
            dialog.agentRequestIds.add(id);
            const row = document.createElement('div'); row.className = 'ai-agent-operation-receipt'; row.dataset.requestListItem = id;
            const title = document.createElement('strong'); title.textContent = String(item.summary || item.capability_key || '平台请求').slice(0, 500);
            const status = document.createElement('small');
            const labels = { observed_http_result: '已收到平台响应', uncertain: '结果不确定', submitted: '后续结果待跟进', executing: '执行中', admitted: '已登记' };
            const declaration = item.reconciliation?.resolution;
            status.textContent = (labels[item.observation?.status] || '回执待核对') + (declaration === 'occurred' ? ' · 人工声明已生效' : declaration === 'not_occurred' ? ' · 人工声明未生效' : '');
            const detail = document.createElement('button'); detail.type = 'button'; detail.className = 'btn btn-outline btn-sm';
            detail.dataset.agentReconcileOpen = id; detail.dataset.taskId = dialog.dataset.taskId; detail.textContent = '查看详情与核对';
            row.append(title, status, detail); container.appendChild(row);
        }
        dialog.agentNextOffset = data.has_more && Number.isInteger(data.next_offset) && data.next_offset > dialog.agentNextOffset ? data.next_offset : null;
        more.hidden = dialog.agentNextOffset === null;
        feedback.textContent = dialog.agentRequestIds.size ? `已显示 ${dialog.agentRequestIds.size} 条请求。` : '没有平台请求记录。';
    } catch (error) {
        if (dialog.agentListVersion === version) feedback.textContent = error.message || '请求记录读取失败，请重试。';
    } finally {
        if (dialog.agentListVersion === version) { dialog.dataset.loading = 'false'; more.disabled = false; }
    }
}

function agentReconciliationDialog() {
    let dialog = document.querySelector('[data-agent-reconciliation-dialog]');
    if (dialog) return dialog;
    dialog = document.createElement('dialog');
    dialog.className = 'ai-agent-reconciliation-dialog';
    dialog.dataset.agentReconciliationDialog = 'true';
    dialog.setAttribute('aria-labelledby', 'agent-reconciliation-title');
    dialog.innerHTML = `<form novalidate><header><h3 id="agent-reconciliation-title">核对平台请求</h3><button type="button" data-reconcile-close aria-label="关闭核对">×</button></header>
        <p data-reconcile-summary></p><div class="ai-agent-reconciliation-facts"><strong>平台响应事实</strong><p data-reconcile-observation></p>
        <details><summary>查看原请求参数</summary><pre data-reconcile-parameters></pre></details></div>
        <div data-reconcile-statement hidden><strong>已保存的人工核对声明</strong><p data-reconcile-statement-text></p></div>
        <fieldset data-reconcile-fields><legend>你的核对结论</legend><p>请查看正常业务记录后选择。人工声明会单独保留，不会替代平台响应或自动执行操作。</p>
        <label><input type="radio" name="reconciliation-resolution" value="occurred"> 已确认该操作生效</label>
        <label><input type="radio" name="reconciliation-resolution" value="not_occurred"> 已确认该操作未生效</label>
        <label class="ai-agent-reconciliation-note">核对说明<textarea name="note" maxlength="1000" rows="4" placeholder="例如：已打开投票详情，确认当前没有该选项记录。"></textarea></label></fieldset>
        <p data-reconcile-block></p><p data-reconcile-feedback role="status" aria-live="polite"></p>
        <footer><button type="button" class="btn btn-outline" data-reconcile-close>关闭</button><button type="submit" class="btn btn-primary" data-reconcile-submit>保存核对声明</button></footer></form>`;
    dialog.addEventListener('click', (event) => { if (event.target.closest('[data-reconcile-close]') && dialog.dataset.submitting !== 'true') dialog.close(); });
    dialog.addEventListener('keydown', (event) => { if (event.key === 'Escape') event.stopPropagation(); });
    dialog.addEventListener('cancel', (event) => { if (dialog.dataset.submitting === 'true') event.preventDefault(); });
    dialog.addEventListener('close', () => {
        const target = dialog.agentOpener?.isConnected ? dialog.agentOpener
            : Array.from(document.querySelectorAll('[data-agent-reconcile-open]')).find((button) => button.dataset.agentReconcileOpen === dialog.dataset.requestId);
        target?.focus({ preventScroll: true });
    });
    dialog.querySelector('form').addEventListener('submit', (event) => { event.preventDefault(); submitAgentReconciliation(dialog).catch(() => {}); });
    document.body.appendChild(dialog);
    return dialog;
}

function renderAgentReconciliation(dialog, view) {
    dialog.agentRequestView = view;
    dialog.querySelector('[data-reconcile-summary]').textContent = view.summary || `平台请求 ${view.id}`;
    const observation = view.observation || {};
    const labels = { observed_http_result: '已收到平台响应', uncertain: '结果不确定', submitted: '已接收，后续结果待跟进', admitted: '已登记', executing: '执行中' };
    const http = Number(observation.http_status);
    dialog.querySelector('[data-reconcile-observation]').textContent = `${labels[observation.status] || '尚无确定响应'}${Number.isInteger(http) && http >= 100 && http <= 599 ? ` · HTTP ${http}` : ''}。业务结果未单独核验。`;
    dialog.querySelector('[data-reconcile-parameters]').textContent = JSON.stringify(view.request?.parameters || {}, null, 2).slice(0, 20000);
    const statement = view.reconciliation || {};
    const resolved = ['occurred', 'not_occurred'].includes(statement.resolution);
    dialog.querySelector('[data-reconcile-statement]').hidden = !resolved;
    dialog.querySelector('[data-reconcile-statement-text]').textContent = resolved ? `${statement.resolution === 'occurred' ? '已确认生效' : '已确认未生效'} · ${statement.note || ''}${statement.at ? ` · ${statement.at}` : ''}${statement.late_http_observation ? '。声明后又收到平台响应，请结合两份记录核对。' : ''}` : '';
    dialog.querySelector('[data-reconcile-block]').textContent = view.block_reason || '';
    const finished = Boolean(view.host_execution_finished_at);
    const canOccurred = !resolved && finished && view.can_reconcile_occurred === true;
    const canNotOccurred = !resolved && finished && view.can_reconcile_not_occurred === true;
    dialog.querySelector('[value="occurred"]').disabled = !canOccurred;
    dialog.querySelector('[value="not_occurred"]').disabled = !canNotOccurred;
    dialog.querySelector('[data-reconcile-fields]').hidden = resolved;
    dialog.querySelector('[data-reconcile-submit]').disabled = !(canOccurred || canNotOccurred);
    dialog.querySelector('[data-reconcile-submit]').hidden = resolved;
    if (!finished && !resolved && !view.block_reason) dialog.querySelector('[data-reconcile-block]').textContent = '执行是否结束尚未获得证明，仍需执行恢复核查。';
}

async function openAgentReconciliation(button) {
    const dialog = agentReconciliationDialog();
    if (dialog.open || dialog.dataset.submitting === 'true') return;
    const loadVersion = (dialog.agentLoadVersion || 0) + 1;
    dialog.agentLoadVersion = loadVersion;
    dialog.agentRequestView = null;
    dialog.agentOpener = button;
    dialog.dataset.taskId = button.dataset.taskId;
    dialog.dataset.requestId = button.dataset.agentReconcileOpen;
    dialog.querySelector('form').reset();
    dialog.querySelector('[data-reconcile-fields]').disabled = true;
    dialog.querySelector('[data-reconcile-fields]').hidden = false;
    dialog.querySelector('[data-reconcile-submit]').disabled = true;
    dialog.querySelector('[data-reconcile-submit]').hidden = false;
    dialog.querySelector('[data-reconcile-feedback]').textContent = '正在读取原请求…';
    dialog.querySelector('[data-reconcile-summary]').textContent = '';
    dialog.querySelector('[data-reconcile-observation]').textContent = '';
    dialog.querySelector('[data-reconcile-statement]').hidden = true;
    dialog.querySelector('[data-reconcile-block]').textContent = '';
    dialog.querySelector('[data-reconcile-parameters]').textContent = '';
    dialog.showModal();
    try {
        const data = await apiJson(`/api/agent-tasks/${encodeURIComponent(dialog.dataset.taskId)}/platform-requests/${encodeURIComponent(dialog.dataset.requestId)}`);
        if (!dialog.open || dialog.agentLoadVersion !== loadVersion) return;
        renderAgentReconciliation(dialog, data.request);
        dialog.querySelector('[data-reconcile-feedback]').textContent = '';
        dialog.querySelector('[data-reconcile-fields]').disabled = false;
    } catch (error) {
        if (!dialog.open || dialog.agentLoadVersion !== loadVersion) return;
        dialog.querySelector('[data-reconcile-feedback]').textContent = error.message || '请求读取失败，请关闭后重新打开。';
    }
}

async function submitAgentReconciliation(dialog) {
    if (dialog.dataset.submitting === 'true') return;
    const feedback = dialog.querySelector('[data-reconcile-feedback]');
    const selected = dialog.querySelector('[name="reconciliation-resolution"]:checked');
    const note = dialog.querySelector('[name="note"]').value.trim();
    if (!selected || selected.disabled || !note) { feedback.textContent = '请选择允许的核对结论，并填写核对说明。'; return; }
    dialog.dataset.submitting = 'true';
    dialog.querySelector('[data-reconcile-fields]').disabled = true;
    dialog.querySelectorAll('button').forEach((button) => { button.disabled = true; });
    feedback.textContent = '正在保存核对声明…';
    const url = `/api/agent-tasks/${encodeURIComponent(dialog.dataset.taskId)}/platform-requests/${encodeURIComponent(dialog.dataset.requestId)}`;
    try {
        const data = await apiJson(`${url}/reconcile`, { method: 'POST', body: JSON.stringify({ resolution: selected.value, note, expected_revision: dialog.agentRequestView.revision }) });
        renderAgentReconciliation(dialog, data.request);
        feedback.textContent = '核对声明已保存。平台原响应保持不变，没有自动执行新操作。';
        await loadTaskDetail(Number(dialog.dataset.taskId)).catch(() => {});
    } catch (error) {
        feedback.textContent = error.message || '保存失败，核对说明已保留，请重试。';
        try { const latest = await apiJson(url); renderAgentReconciliation(dialog, latest.request); } catch { /* retain the original form and draft */ }
    } finally {
        dialog.dataset.submitting = 'false';
        dialog.querySelector('[data-reconcile-fields]').disabled = false;
        dialog.querySelectorAll('[data-reconcile-close]').forEach((button) => { button.disabled = false; });
        if (dialog.agentRequestView) renderAgentReconciliation(dialog, dialog.agentRequestView);
    }
}

function resultSummaryText(task) {
    if (task.result_summary) return task.result_summary;
    if (task.error_message) {
        if (/exceeded\s+\d+\s+seconds/i.test(task.error_message)) {
            return '任务运行超时，未能在平台时间上限内完整完成。可先查看已保留的部分结果；如果没有可用产物，请缩小任务范围后重试。';
        }
        return task.error_message;
    }
    if (task.status === 'completed') return '任务已结束，但运行时没有返回明确的业务结论。建议查看下方执行记录，确认是否产生了可用内容。';
    if (task.status === 'failed') return '任务失败，但未返回具体错误。建议稍后重试，或把任务要求描述得更具体。';
    return task.status_label || task.status || '处理中';
}

function safeLocalHref(value) {
    const text = String(value || '').trim();
    if (!text.startsWith('/') || text.startsWith('//') || /[\\\u0000-\u0020]/.test(text)) return '';
    return text;
}

function renderPlatformResult(detail = {}) {
    if (!detail.platform_action) {
        return '';
    }
    if (detail.platform_action !== 'lesson_document_generation') {
        return renderBusinessResult(detail);
    }
    const viewerUrl = safeLocalHref(detail.generated_material_viewer_url);
    const path = detail.generated_material_path || '';
    const order = detail.session_order_index || detail.target?.order_index || '';
    const title = detail.session_title || detail.target?.title || '';
    const generationTask = detail.generation_task || {};
    return `
        <div class="ai-task-detail__block is-business-result">
            <h4>业务产物</h4>
            <dl class="ai-task-result-grid">
                <div><dt>目标课时</dt><dd>第 ${escapeHtml(order)} 次课 ${escapeHtml(title)}</dd></div>
                <div><dt>生成文档</dt><dd>${escapeHtml(path || '未返回路径')}</dd></div>
                <div><dt>生成任务</dt><dd>#${escapeHtml(generationTask.id || '-')} · ${escapeHtml(generationTask.status_label || generationTask.status || '-')}</dd></div>
            </dl>
            ${viewerUrl ? `<a class="btn btn-outline btn-sm ai-task-result-link" href="${escapeHtml(viewerUrl)}" target="_blank" rel="noopener">打开生成文档</a>` : ''}
        </div>
    `;
}

function renderDetailList(items = []) {
    const normalized = Array.isArray(items) ? items.filter(Boolean).slice(0, 30) : [];
    if (!normalized.length) {
        return '';
    }
    return `
        <div class="ai-task-business-list">
            ${normalized.map((item) => {
                if (typeof item === 'string') {
                    return `<div class="ai-task-business-item"><strong>${escapeHtml(item)}</strong></div>`;
                }
                const href = safeLocalHref(item.url || item.href || '');
                return `
                    <div class="ai-task-business-item">
                        <strong>${href ? `<a href="${escapeHtml(href)}" target="_blank" rel="noopener">${escapeHtml(item.title || item.label || '查看')}</a>` : escapeHtml(item.title || item.label || '条目')}</strong>
                        ${item.meta ? `<span>${escapeHtml(item.meta)}</span>` : ''}
                        ${item.note ? `<small>${escapeHtml(item.note)}</small>` : ''}
                    </div>
                `;
            }).join('')}
        </div>
    `;
}

function renderBusinessResult(detail = {}) {
    const metrics = Array.isArray(detail.metrics) ? detail.metrics.slice(0, 8) : [];
    const markdown = String(detail.markdown || detail.summary || '').trim();
    const links = Array.isArray(detail.links) ? detail.links.filter(Boolean).slice(0, 4) : [];
    const nextActions = Array.isArray(detail.next_actions) ? detail.next_actions.slice(0, 8) : [];
    const safety = Array.isArray(detail.safety) ? detail.safety.slice(0, 6) : [];
    return `
        <div class="ai-task-detail__block is-business-result">
            <h4>${escapeHtml(detail.display_title || '业务产物')}</h4>
            ${detail.context_label ? `<p class="ai-task-business-context">${escapeHtml(detail.context_label)}</p>` : ''}
            ${metrics.length ? `
                <dl class="ai-task-result-grid">
                    ${metrics.map((item) => `
                        <div><dt>${escapeHtml(item.label || '')}</dt><dd>${escapeHtml(item.value ?? '-')}</dd></div>
                    `).join('')}
                </dl>
            ` : ''}
            ${markdown ? `<div class="ai-task-business-markdown md-content">${renderWorkspaceMarkdown(markdown)}</div>` : ''}
            ${renderDetailList(detail.items || [])}
            ${nextActions.length ? `
                <div class="ai-task-runtime-section">
                    <strong>教师下一步</strong>
                    <ul>${nextActions.map((item) => `<li>${escapeHtml(item)}</li>`).join('')}</ul>
                </div>
            ` : ''}
            ${safety.length ? `
                <div class="ai-task-runtime-section">
                    <strong>安全边界</strong>
                    <ul>${safety.map((item) => `<li>${escapeHtml(item)}</li>`).join('')}</ul>
                </div>
            ` : ''}
            ${links.length ? `
                <div class="ai-task-result-actions">
                    ${links.map((item) => {
                        const href = safeLocalHref(item.url || item.href || '');
                        return href ? `<a class="btn btn-outline btn-sm ai-task-result-link" href="${escapeHtml(href)}" target="_blank" rel="noopener">${escapeHtml(item.label || '打开')}</a>` : '';
                    }).join('')}
                </div>
            ` : ''}
        </div>
    `;
}

function renderDeliverable(detail = {}) {
    const markdown = typeof detail.deliverable_markdown === 'string' ? detail.deliverable_markdown.trim() : '';
    if (!markdown) {
        return '';
    }
    return `
        <div class="ai-task-detail__block is-deliverable">
            <h4>最终结果</h4>
            <div class="ai-task-deliverable md-content">${renderWorkspaceMarkdown(markdown)}</div>
        </div>
    `;
}

function renderAgentArtifact(item, fallbackLabel = '产物') {
    const artifact = item && typeof item === 'object' ? item : { name: String(item || '') };
    const href = safeLocalHref(artifact.download_url || '');
    const label = artifact.path || artifact.name || artifact.id || fallbackLabel;
    const meta = artifact.size ? ` · ${formatAgentFileSize(artifact.size)}` : '';
    return `<li>${href ? `<a href="${escapeHtml(href)}" target="_blank" rel="noopener">${escapeHtml(label)}</a>` : escapeHtml(label)}<small>${escapeHtml(meta)}</small></li>`;
}

function renderRuntimeDetail(detail = {}) {
    if (detail.platform_action) {
        return renderPlatformResult(detail);
    }
    const hasDeliverable = typeof detail.deliverable_markdown === 'string' && detail.deliverable_markdown.trim() !== '';
    const textOutputs = Array.isArray(detail.text_outputs) ? detail.text_outputs.slice(0, 4) : [];
    const recoveredArtifacts = Array.isArray(detail.recovered_artifacts) ? detail.recovered_artifacts.slice(0, 8) : [];
    const artifacts = Array.isArray(detail.artifacts) ? detail.artifacts.slice(0, 6) : [];
    const regularArtifacts = artifacts.filter((item) => !(item && item.recovered && item.download_url));
    const toolCalls = Array.isArray(detail.tool_calls) ? detail.tool_calls.slice(-6) : [];
    const nextActions = Array.isArray(detail.next_actions) ? detail.next_actions.slice(0, 6) : [];
    if (!textOutputs.length && !recoveredArtifacts.length && !regularArtifacts.length && !toolCalls.length && !nextActions.length) {
        return '';
    }
    return `
        <div class="ai-task-detail__block is-runtime-detail">
            <h4>${detail.partial_result_available ? '已保留的部分结果' : '执行信息'}</h4>
            ${textOutputs.length ? (hasDeliverable ? `
                <details class="ai-task-runtime-process">
                    <summary>执行过程（点击展开）</summary>
                    ${textOutputs.map((item) => `<div class="ai-task-runtime-output md-content">${renderWorkspaceMarkdown(item.text || item)}</div>`).join('')}
                </details>
            ` : `
                <div class="ai-task-runtime-section">
                    <strong>关键输出</strong>
                    ${textOutputs.map((item) => `<div class="ai-task-runtime-output md-content">${renderWorkspaceMarkdown(item.text || item)}</div>`).join('')}
                </div>
            `) : ''}
            ${recoveredArtifacts.length ? `
                <div class="ai-task-runtime-section is-recovered">
                    <strong>已挽救的中间产物</strong>
                    <ul>${recoveredArtifacts.map((item) => renderAgentArtifact(item, '中间产物')).join('')}</ul>
                </div>
            ` : ''}
            ${regularArtifacts.length ? `
                <div class="ai-task-runtime-section">
                    <strong>产物</strong>
                    <ul>${regularArtifacts.map((item) => renderAgentArtifact(item)).join('')}</ul>
                </div>
            ` : ''}
            ${toolCalls.length ? `
                <div class="ai-task-runtime-section">
                    <strong>工具调用</strong>
                    <ul>${toolCalls.map((item) => `<li>${escapeHtml(item.name || item.tool || item.type || JSON.stringify(item).slice(0, 160))}</li>`).join('')}</ul>
                </div>
            ` : ''}
            ${nextActions.length ? `
                <div class="ai-task-runtime-section">
                    <strong>建议下一步</strong>
                    <ul>${nextActions.map((item) => `<li>${escapeHtml(item)}</li>`).join('')}</ul>
                </div>
            ` : ''}
        </div>
    `;
}

function protocolArtifactLabel(text) {
    const match = String(text || '').match(/(?:path|file|filename)["']?\s*[:=]\s*["'\\]*([^"'\\,\s]+)/i);
    if (!match) {
        return '任务产物';
    }
    const value = match[1].replace(/\\\//g, '/').replace(/\\/g, '/');
    return value.split('/').filter(Boolean).pop() || value || '任务产物';
}

function userFacingTaskEvent(event) {
    if (event?.event_type === 'supplements_delivered') return { ...event, message: '补充说明已送达，Agent 正在当前任务中继续处理。' };
    const message = String(event?.message || event?.event_type || '').trim();
    const lower = message.toLowerCase();
    const protocol = /^\s*#?\d+\s+(item\.(delta|started|completed)|response\.[\w.-]+|turn\.[\w.-]+)\s*:/i.test(message);
    if (!protocol) {
        return { ...event, message };
    }
    if (lower.includes('item.delta') || lower.includes('agent_reasoning')) {
        return null;
    }
    if (lower.includes('file_change')) {
        const label = protocolArtifactLabel(message);
        return {
            ...event,
            message: lower.includes('item.completed') ? `已写入任务产物：${label}` : `正在写入任务产物：${label}`,
            detail: {},
        };
    }
    if (message.includes('/api/agent-bridge/web')) {
        return { ...event, message: '正在联网检索资料', detail: {} };
    }
    if (message.includes('/api/agent-bridge/query')) {
        return { ...event, message: '正在查询平台数据库', detail: {} };
    }
    return null;
}

function renderEventDetail(event) {
    const detail = event.detail || {};
    if (detail.generated_material_path) {
        return `<small>生成文档：${escapeHtml(detail.generated_material_path)}</small>`;
    }
    if (detail.error) {
        return `<small>原因：${escapeHtml(detail.error)}</small>`;
    }
    if (detail.generation_task_id) {
        return `<small>生成任务 #${escapeHtml(detail.generation_task_id)}</small>`;
    }
    if (detail.supplement) {
        const encoded = encodeURIComponent(String(detail.supplement || ''));
        return `
            <small class="ai-task-event__supplement">补充说明：${escapeHtml(detail.supplement)}</small>
            ${detail.runtime_injection === 'next_turn' ? '<small>当前轮次结束后继续处理。</small>' : ''}
            ${detail.follow_up_available && detail.runtime_injection !== 'next_turn' ? `
                <button type="button" class="btn btn-outline btn-sm ai-task-event__followup" data-agent-supplement-followup="${escapeHtml(encoded)}">作为追问继续</button>
            ` : ''}
        `;
    }
    return '';
}

function renderTaskEventHtml(event) {
    const displayEvent = userFacingTaskEvent(event);
    if (!displayEvent) {
        return '';
    }
    return `
        <div class="ai-task-event" data-agent-event-id="${escapeHtml(displayEvent.id || 0)}">
            <span>${escapeHtml(formatDateTime(displayEvent.created_at))}</span>
            <strong>${escapeHtml(displayEvent.message || displayEvent.event_type || '')}</strong>
            ${renderEventDetail(displayEvent)}
        </div>
    `;
}

function renderProposedActions(task) {
    const proposals = Array.isArray((task.result_detail || {}).proposed_actions)
        ? task.result_detail.proposed_actions
        : [];
    if (!proposals.length || !task.is_owner) {
        return '';
    }
    const items = proposals.map((proposal, index) => {
        const executed = proposal.executed || null;
        const params = proposal.params || {};
        const needsRecipients = ['send_student_notification', 'send_private_message'].includes(proposal.action);
        if (executed) {
            const url = safeLocalHref(executed.url || '');
            return `
                <div class="ai-task-action is-done">
                    <span class="ai-task-action__done">✓ ${escapeHtml(executed.label || proposal.label || '已执行')}</span>
                    ${url ? `<a class="btn btn-outline btn-sm" href="${escapeHtml(url)}" target="_blank" rel="noopener">去查看</a>` : ''}
                </div>
            `;
        }
        if (proposal.execution_mode === 'manual_link' && !needsRecipients) {
            return `
                <div class="ai-task-action">
                    <div class="ai-task-action__head">
                        <strong>${escapeHtml(proposal.label || '动作')}</strong>
                        <small>${escapeHtml(proposal.summary || '')}</small>
                    </div>
                    <button type="button" class="btn btn-outline btn-sm" data-agent-action-manual="${escapeHtml(task.id)}" data-action-index="${index}">复制内容并打开消息中心</button>
                </div>
            `;
        }
        const titleValue = params.title || '';
        const confirmationNote = proposal.confirmation_note || '确认后将以你的身份执行该动作。';
        return `
            <div class="ai-task-action" data-agent-action-block="${escapeHtml(task.id)}:${index}">
                <div class="ai-task-action__head">
                    <strong>${escapeHtml(proposal.label || '动作')}</strong>
                    <small>${escapeHtml(proposal.summary || '')}</small>
                </div>
                <button type="button" class="btn btn-primary btn-sm" data-agent-action-open="${escapeHtml(task.id)}" data-action-index="${index}">${escapeHtml(proposal.label || '执行')}</button>
                <div class="ai-task-action__confirm" hidden>
                    ${needsRecipients ? `<label>选择收件人<select data-agent-action-recipients data-recipient-action="${escapeHtml(proposal.action)}" data-class-offering-id="${Number(params.class_offering_id) || ''}" data-initial-recipients="${escapeHtml(JSON.stringify(params.recipient_identities || (params.contact_identity ? [params.contact_identity] : [])))}" ${proposal.action === 'send_student_notification' ? 'multiple size="5"' : ''} aria-label="选择当前可联系的收件人"><option value="">正在读取可见联系人…</option></select></label><small data-agent-recipient-status>请核对姓名与课堂后再确认；多选时最多选择 30 人。</small>` : ''}
                    ${titleValue !== '' ? `
                        <label>标题<input type="text" data-agent-action-title value="${escapeHtml(titleValue)}" maxlength="120"></label>
                    ` : ''}
                    <p class="ai-task-action__note">${escapeHtml(confirmationNote)}</p>
                    <div class="ai-task-action__buttons">
                        <button type="button" class="btn btn-primary btn-sm" data-agent-action-confirm="${escapeHtml(task.id)}" data-action-index="${index}">确认执行</button>
                        <button type="button" class="btn btn-outline btn-sm" data-agent-action-cancel>取消</button>
                    </div>
                </div>
            </div>
        `;
    }).join('');
    return `
        <div class="ai-task-detail__block is-proposed-actions">
            <h4>可一键落地的动作</h4>
            ${items}
        </div>
    `;
}

function renderFollowUpBox(task) {
    if (!task.is_owner || !task.is_terminal) {
        return '';
    }
    if (taskCompletionPresentation(task).pendingDomain) return `<div class="ai-task-detail__block"><p>请跟进已提交的生成任务，避免重复创建。</p><button type="button" class="btn btn-outline btn-sm" data-agent-domain-followup="${escapeHtml(task.id)}">跟进已有生成任务</button></div>`;
    if (taskCompletionPresentation(task).pendingRequests) return `<div class="ai-task-detail__block"><p>请先核对已有平台请求的回执和业务记录。</p><button type="button" class="btn btn-outline btn-sm" data-agent-request-followup="${escapeHtml(task.id)}">核对已有平台请求</button></div>`;
    const retryButtons = (task.status === 'failed' || task.status === 'canceled') ? `
        <div class="ai-task-retry-row">
            <button type="button" class="btn btn-outline btn-sm" data-agent-retry="${escapeHtml(task.id)}">原样重试</button>
            <button type="button" class="btn btn-outline btn-sm" data-agent-retry-edit="${escapeHtml(task.id)}">修改后重试</button>
        </div>
    ` : '';
    return retryButtons;
}

function renderTaskList(tasks = []) {
    const list = $('#agent-task-list');
    if (!list) {
        return;
    }
    if (!tasks.length) {
        list.innerHTML = '<div class="ai-task-list__empty">暂无任务。提交后会进入全平台队列。</div>';
        return;
    }
    list.innerHTML = tasks.map((task, index) => {
        const ownerLabel = task.is_owner ? '我的任务' : `${escapeHtml(task.teacher_name || '某位用户')}`;
        const runningText = task.status === 'running' ? ` · 已运行 ${formatElapsed(task.elapsed_seconds)}` : '';
        const queuePieces = [];
        if (task.status === 'queued' && task.queue_position) {
            queuePieces.push(`队列第 ${task.queue_position}`);
        }
        if (task.status === 'queued' && task.estimated_wait_label) {
            queuePieces.push(task.estimated_wait_label);
        }
        const queueText = queuePieces.length ? ` · ${queuePieces.join(' · ')}` : '';
        const isSelected = Number(task.id) === Number(selectedTaskId);
        const deleteButton = task.is_owner && task.is_terminal
            ? `<button type="button" class="ai-task-item__delete" data-agent-delete="${escapeHtml(task.id)}" title="删除这条历史" aria-label="删除这条历史"><svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"></path><path d="M8 6V4h8v2"></path><path d="M10 11v6"></path><path d="M14 11v6"></path><path d="M5 6l1 15h12l1-15"></path></svg></button>`
            : '';
        return `
            <div class="ai-task-item-row ${isSelected ? 'is-selected' : ''}">
                <button type="button" class="ai-task-item ${statusClass(task.status)} ${isSelected ? 'is-selected' : ''}" data-agent-task-id="${escapeHtml(task.id)}">
                    <span class="ai-task-item__order">${index + 1}</span>
                    <span class="ai-task-item__body">
                        <strong>${escapeHtml(task.title || task.public_summary || '教学任务')}</strong>
                        <small>${ownerLabel} · ${escapeHtml(taskCompletionPresentation(task).label)}${runningText}${queueText}</small>
                    </span>
                </button>
                ${deleteButton}
            </div>
        `;
    }).join('');
}

function formatAgentSubscriptionHour(hour) {
    const value = Math.max(0, Math.min(Number(hour || 0), 23));
    return `${String(value).padStart(2, '0')}:00`;
}

function renderAgentSubscriptions(payload = agentSubscriptionPayload) {
    const list = $('#agent-subscription-list');
    if (!list) {
        return;
    }
    const subscriptions = Array.isArray(payload.subscriptions) ? payload.subscriptions : [];
    if (!subscriptions.length) {
        list.innerHTML = '<div class="ai-agent-subscription-empty">暂无可用订阅。</div>';
        return;
    }
    const hourOptions = Array.from({ length: 24 }, (_, hour) => `<option value="${hour}">${formatAgentSubscriptionHour(hour)}</option>`).join('');
    const recentTasks = Array.isArray(payload.recent_tasks) ? payload.recent_tasks.slice(0, 3) : [];
    const subscriptionStatusLine = (item) => {
        const next = item.enabled ? `下次 ${item.next_run_at || formatAgentSubscriptionHour(item.hour)}` : (item.description || '');
        const last = item.enabled && item.last_run_message ? ` · 上次：${item.last_run_message}` : '';
        return `${next}${last}`;
    };
    const subscriptionAttentionHtml = (item) => item.attention_message
        ? `<small class="ai-agent-subscription-row__attention">${escapeHtml(item.attention_message)}</small>`
        : '';
    list.innerHTML = `
        <div class="ai-agent-subscription-rows">
            ${subscriptions.map((item) => `
                <label class="ai-agent-subscription-row ${item.enabled ? 'is-enabled' : ''} ${item.attention_message ? 'has-attention' : ''}">
                    <input type="checkbox" data-agent-sub-toggle="${escapeHtml(item.key)}" ${item.enabled ? 'checked' : ''}>
                    <span class="ai-agent-subscription-row__copy">
                        <strong>${escapeHtml(item.label || item.key)}</strong>
                        <small>${escapeHtml(subscriptionStatusLine(item))}</small>
                        ${subscriptionAttentionHtml(item)}
                    </span>
                    <select data-agent-sub-hour="${escapeHtml(item.key)}" aria-label="${escapeHtml(item.label || item.key)}时间">
                        ${hourOptions}
                    </select>
                </label>
            `).join('')}
        </div>
        ${recentTasks.length ? `
            <div class="ai-agent-subscription-recent">
                ${recentTasks.map((task) => `
                    <button type="button" data-agent-task-id="${escapeHtml(task.id)}">
                        <strong>${escapeHtml(task.title || '定时任务产出')}</strong>
                        <small>${escapeHtml(task.status || '')} · ${escapeHtml(formatDateTime(task.created_at))}</small>
                    </button>
                `).join('')}
            </div>
        ` : ''}
    `;
    subscriptions.forEach((item) => {
        const select = Array.from(list.querySelectorAll('[data-agent-sub-hour]'))
            .find((node) => node.dataset.agentSubHour === String(item.key));
        if (select) {
            select.value = String(Math.max(0, Math.min(Number(item.hour ?? 0), 23)));
        }
    });
}

async function loadAgentSubscriptions({ silent = true } = {}) {
    if (!CONFIG.taskCenterEnabled) {
        return;
    }
    try {
        const data = await apiJson('/api/agent-tasks/subscriptions');
        agentSubscriptionPayload = {
            subscriptions: Array.isArray(data.subscriptions) ? data.subscriptions : [],
            recent_tasks: Array.isArray(data.recent_tasks) ? data.recent_tasks : [],
        };
        renderAgentSubscriptions(agentSubscriptionPayload);
    } catch (error) {
        if (!silent) {
            notify(error.message || '定时任务加载失败', 'error');
        }
    }
}

async function setAgentSubscription(templateKey, { enabled, hour }) {
    if (agentSubscriptionBusy) {
        return;
    }
    agentSubscriptionBusy = true;
    try {
        const data = await apiJson('/api/agent-tasks/subscriptions', {
            method: 'POST',
            body: JSON.stringify({
                template_key: templateKey,
                enabled: Boolean(enabled),
                hour: Number(hour),
            }),
        });
        agentSubscriptionPayload = {
            subscriptions: Array.isArray(data.subscriptions) ? data.subscriptions : [],
            recent_tasks: Array.isArray(data.recent_tasks) ? data.recent_tasks : [],
        };
        renderAgentSubscriptions(agentSubscriptionPayload);
        notify(Boolean(enabled) ? '定时任务已更新。' : '定时任务已关闭。', 'success');
    } finally {
        agentSubscriptionBusy = false;
    }
}

function renderTaskEventsPanel(task) {
    const events = Array.isArray(task.events) ? task.events : [];
    const visibleHtml = events.map(renderTaskEventHtml).join('');
    const countText = events.length ? ` · ${events.length} 条` : '';
    const openAttr = task.is_active ? ' open' : '';
    return `
        <details class="ai-task-events-panel"${openAttr}>
            <summary>
                <span>执行记录${escapeHtml(countText)}</span>
                <small>${task.is_active ? '实时更新' : '点击查看过程细节'}</small>
            </summary>
            <div class="ai-task-events" data-agent-events="${escapeHtml(task.id)}">
                ${visibleHtml || '<div class="ai-task-detail__empty">暂无可展示的执行记录。</div>'}
            </div>
        </details>
    `;
}

function renderAgentQuestions(task) {
    const requests = Array.isArray(task.questions) ? task.questions : [];
    return `<section class="ai-agent-questions" data-agent-questions>${requests.map((request) => {
        const status = request.status === 'pending' && Number(request.expires_at) * 1000 <= Date.now() ? 'expired' : request.status;
        const pending = status === 'pending';
        const label = { pending: '需要你的回答', answered: '已回答 · Agent 将继续任务', canceled: '本次提问已取消', expired: '本次提问已过期' }[status] || '本次提问已结束';
        const signature = JSON.stringify([status, request.expires_at, request.questions, request.answers]);
        const answers = new Map((request.answers || []).map((answer) => [answer.id, answer]));
        return `<details class="ai-agent-question-request" data-agent-question-request="${escapeHtml(request.id)}" data-question-signature="${escapeHtml(signature)}" ${pending ? 'open' : ''}>
            <summary><span class="ai-agent-question-mark" aria-hidden="true">?</span><strong>${escapeHtml(label)}</strong><span class="ai-agent-question-chevron" aria-hidden="true">⌄</span></summary>
            <form data-agent-question-form data-task-id="${escapeHtml(task.id)}" data-question-id="${escapeHtml(request.id)}" data-expires-at="${escapeHtml(request.expires_at)}" data-question-state="${escapeHtml(status)}">
                ${(request.questions || []).map((question, index) => {
                    const answer = answers.get(question.id);
                    return `<fieldset data-question-item="${escapeHtml(question.id)}" data-multi-select="${question.multiSelect ? 'true' : 'false'}" ${!pending ? 'disabled' : ''}>
                        <legend>${question.header ? `<small>${escapeHtml(question.header)}</small>` : ''}${escapeHtml(question.question)}</legend>
                        ${question.detail ? `<p class="ai-agent-question-detail">${escapeHtml(question.detail)}</p>` : ''}
                        ${pending ? `<div class="ai-agent-question-options">${(question.options || []).map((option) => `<label class="ai-agent-question-option"><input type="${question.multiSelect ? 'checkbox' : 'radio'}" name="agent-question-${escapeHtml(request.id)}-${index}" value="${escapeHtml(option.label)}"><span><strong>${escapeHtml(option.label)}</strong>${option.description ? `<small>${escapeHtml(option.description)}</small>` : ''}</span></label>`).join('')}</div>
                        <label class="ai-agent-question-custom">${question.options?.length ? (question.multiSelect ? '补充说明（可选）' : '或填写自己的答案') : '你的回答'}<textarea data-question-custom maxlength="4000" rows="2" placeholder="写下你的想法…"></textarea></label>` : `<p class="ai-agent-question-answer">${status === 'answered' ? escapeHtml([...(answer?.selected || []), answer?.custom].filter(Boolean).join('；')) : escapeHtml(status === 'expired' ? '等待时间已结束，未提交的回答不会发送。' : '任务已结束等待，无需继续回答。')}</p>`}
                    </fieldset>`;
                }).join('')}
                ${pending ? `<div class="ai-agent-question-footer"><small>回答后继续当前任务${request.expires_at ? ` · ${escapeHtml(new Date(Number(request.expires_at) * 1000).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }))} 前有效` : ''}</small><button type="submit" class="btn btn-primary btn-sm" data-question-submit>提交回答</button></div>` : ''}
                <p class="ai-agent-question-feedback" data-question-feedback role="status" aria-live="polite"></p>
            </form></details>`;
    }).join('')}</section>`;
}

// Keep unchanged question nodes attached: polling must not blur inputs or interrupt IME composition.
function preserveAgentQuestionNodes(article, nextArticle) {
    const existing = article.querySelector('[data-agent-questions]');
    const incoming = nextArticle.querySelector('[data-agent-questions]');
    if (!existing || !incoming) {
        article.replaceChildren(...nextArticle.childNodes);
        return;
    }
    const known = new Map(Array.from(existing.children).map((node) => [node.dataset.agentQuestionRequest, node]));
    const keep = new Set();
    for (const next of Array.from(incoming.children)) {
        const old = known.get(next.dataset.agentQuestionRequest);
        if (old && old.dataset.questionSignature === next.dataset.questionSignature) {
            keep.add(old);
        } else {
            const hadFocus = old?.contains(document.activeElement);
            if (old) old.replaceWith(next);
            else existing.appendChild(next);
            keep.add(next);
            if (hadFocus) next.querySelector('summary')?.focus({ preventScroll: true });
        }
    }
    Array.from(existing.children).forEach((node) => { if (!keep.has(node)) node.remove(); });
    Array.from(article.childNodes).forEach((node) => { if (node !== existing) node.remove(); });
    let passedQuestions = false;
    for (const node of Array.from(nextArticle.childNodes)) {
        if (node === incoming) { passedQuestions = true; continue; }
        if (passedQuestions) article.appendChild(node);
        else article.insertBefore(node, existing);
    }
}

function closeExpiredAgentQuestions(taskId) {
    const id = Number(taskId);
    clearTimeout(agentQuestionExpiryTimers.get(id));
    agentQuestionExpiryTimers.delete(id);
    const node = agentTaskMessages.get(id);
    let nextExpiry = Infinity;
    node?.querySelectorAll('[data-agent-question-form][data-question-state="pending"]').forEach((form) => {
        const remaining = Number(form.dataset.expiresAt) * 1000 - Date.now();
        if (remaining <= 0) {
            form.dataset.questionState = 'expired';
            form.querySelectorAll('fieldset, button').forEach((control) => { control.disabled = true; });
            form.querySelector('[data-question-feedback]').textContent = '本次提问已过期，未提交的回答不会发送。';
            form.closest('details').querySelector('summary strong').textContent = '本次提问已过期';
        } else nextExpiry = Math.min(nextExpiry, remaining);
    });
    if (Number.isFinite(nextExpiry)) agentQuestionExpiryTimers.set(id, setTimeout(() => closeExpiredAgentQuestions(id), Math.min(nextExpiry + 25, 2147483647)));
}

async function refreshAgentQuestionTask(taskId) {
    const id = Number(taskId);
    if (agentQuestionRefreshes.has(id)) return agentQuestionRefreshes.get(id);
    const pending = loadTaskDetail(id).finally(() => agentQuestionRefreshes.delete(id));
    agentQuestionRefreshes.set(id, pending);
    return pending;
}

async function submitAgentQuestionForm(form) {
    if (form.dataset.submitting === 'true' || form.dataset.questionState !== 'pending') return;
    closeExpiredAgentQuestions(form.dataset.taskId);
    if (form.dataset.questionState !== 'pending') return;
    const feedback = form.querySelector('[data-question-feedback]');
    const answers = Array.from(form.querySelectorAll('[data-question-item]')).map((field) => ({
        id: field.dataset.questionItem,
        selected: Array.from(field.querySelectorAll('input:checked')).map((input) => input.value),
        custom: field.querySelector('[data-question-custom]')?.value.trim() || '',
    }));
    const unanswered = answers.findIndex((answer) => !answer.selected.length && !answer.custom);
    if (unanswered >= 0) {
        feedback.textContent = '请回答每个问题：选择选项或填写自己的答案。';
        form.querySelectorAll('[data-question-item]')[unanswered].querySelector('input, textarea')?.focus();
        return;
    }
    form.dataset.submitting = 'true';
    form.querySelectorAll('fieldset, button').forEach((control) => { control.disabled = true; });
    feedback.textContent = '正在提交你的回答…';
    delete feedback.dataset.error;
    let accepted = false;
    try {
        await apiJson(`/api/agent-tasks/${encodeURIComponent(form.dataset.taskId)}/questions/${encodeURIComponent(form.dataset.questionId)}/answer`, { method: 'POST', body: JSON.stringify({ answers }) });
        accepted = true;
        form.dataset.questionState = 'answered';
        feedback.textContent = '回答已提交，Agent 正在继续当前任务。';
        form.closest('details').querySelector('summary strong').textContent = '已回答 · Agent 将继续任务';
    } catch (error) {
        feedback.textContent = error.message || '提交失败，答案已保留，请重试。';
        feedback.dataset.error = 'true';
    } finally {
        form.dataset.submitting = 'false';
        if (!accepted && form.dataset.questionState === 'pending') form.querySelectorAll('fieldset, button').forEach((control) => { control.disabled = false; });
        // Reconcile cancellation, expiry and a response lost after a committed answer.
        await refreshAgentQuestionTask(form.dataset.taskId).catch(() => {});
    }
}

function bindAgentQuestionInteractions(messagesBox) {
    if (!messagesBox || messagesBox.dataset.agentQuestionsBound) return;
    messagesBox.dataset.agentQuestionsBound = 'true';
    messagesBox.addEventListener('click', (event) => {
        const listButton = event.target.closest('[data-agent-request-list]');
        if (listButton) { openAgentRequestList(listButton).catch(() => {}); return; }
        const button = event.target.closest('[data-agent-reconcile-open]');
        if (button) openAgentReconciliation(button).catch(() => {});
    });
    messagesBox.addEventListener('submit', (event) => {
        const form = event.target.closest('[data-agent-question-form]');
        if (!form) return;
        event.preventDefault();
        submitAgentQuestionForm(form).catch(() => {});
    });
    messagesBox.addEventListener('input', (event) => {
        const field = event.target.closest('[data-question-item]');
        const form = field?.closest('[data-agent-question-form]');
        if (form?.dataset.questionState === 'pending' && form.dataset.submitting !== 'true') {
            const feedback = form.querySelector('[data-question-feedback]');
            feedback.textContent = '';
            delete feedback.dataset.error;
        }
        if (!field || field.dataset.multiSelect === 'true') return;
        if (event.target.matches('textarea') && event.target.value.trim()) field.querySelectorAll('input').forEach((input) => { input.checked = false; });
        if (event.target.matches('input')) field.querySelector('textarea').value = '';
    });
    messagesBox.addEventListener('keydown', (event) => {
        const request = event.target.closest('[data-agent-question-request]');
        if (event.key !== 'Escape' || !request?.open) return;
        event.preventDefault();
        event.stopPropagation();
        request.open = false;
        request.querySelector('summary')?.focus({ preventScroll: true });
    });
}

function buildAgentTaskDetailHtml(task) {
    if (!task) {
        return '<div class="ai-task-detail__empty">选择一个任务查看状态。自己的任务会显示详情和执行记录。</div>';
    }
    const runtime = task.runtime_status && task.runtime_status !== 'waiting_input' ? `<span>运行时：${escapeHtml(task.runtime_status)}</span>` : '';
    const elapsed = task.elapsed_seconds ? `<span>已运行：${formatElapsed(task.elapsed_seconds)}</span>` : '';
    const cancelButton = task.is_owner && task.is_active
        ? `<button type="button" class="btn btn-outline btn-sm" data-agent-cancel="${escapeHtml(task.id)}">取消任务</button>`
        : '';
    const deleteButton = task.is_owner && task.is_terminal
        ? `<button type="button" class="btn btn-outline btn-sm ai-task-delete-btn" data-agent-delete="${escapeHtml(task.id)}">删除记录</button>`
        : '';
    const detailPayload = task.result_detail || {};
    const completion = taskCompletionPresentation(task);
    const attachments = Array.isArray(task.attachments) ? task.attachments : [];
    const waitHint = task.status === 'queued' && task.estimated_wait_label
        ? `<div class="ai-task-wait-hint">${escapeHtml(task.estimated_wait_label)} · 完成后会在消息中心通知你</div>`
        : '';
    const originHint = task.parent_task_id
        ? `<div class="ai-task-origin-hint">↳ 来自任务 #${escapeHtml(task.parent_task_id)} 的${escapeHtml(task.origin_label || '后续')}</div>`
        : (task.origin_label ? `<div class="ai-task-origin-hint">${escapeHtml(task.origin_label)}</div>` : '');
    const ownerBody = task.is_owner ? `
        ${waitHint}
        ${originHint}
        ${renderAgentQuestions(task)}
        <div class="ai-task-detail__block">
            <h4>任务要求</h4>
            <p>${escapeHtml(task.private_instruction || '无')}</p>
            ${attachments.length ? `<small class="ai-task-attachment-list">附件：${attachments.map((item) => escapeHtml(item.name || '')).join('、')}</small>` : ''}
        </div>
        ${task.is_terminal ? `
        <div class="ai-task-detail__block ${completion.tone}">
            <h4>${escapeHtml(completion.title)}</h4>
            <p>${escapeHtml(resultSummaryText(task))}</p>
        </div>` : ''}
        ${task.is_terminal ? renderDeliverable(detailPayload) : ''}
        ${renderAgentOperationReceipts(detailPayload, task.is_owner ? task.id : null)}
        ${task.error_message ? `
        <div class="ai-task-detail__block is-error">
            <h4>异常信息</h4>
            <p>${escapeHtml(task.error_message)}</p>
        </div>` : ''}
        ${renderProposedActions(task)}
        ${renderRuntimeDetail(detailPayload)}
        ${renderTaskEventsPanel(task)}
        ${task.is_active ? '<div class="ai-task-live-indicator">执行过程实时更新中…</div>' : ''}
        ${renderFollowUpBox(task)}
    ` : `
        <div class="ai-task-detail__block">
            <h4>隐私保护</h4>
            <p>这是其他老师的任务。这里只显示公开队列状态，不展示任务细节、上下文或结果。</p>
        </div>
    `;
    return `
        <header class="ai-task-detail__header">
            <div>
                <div class="ai-agent-card__label-row">
                    <span class="ai-agent-card__badge">Agent</span>
                    <span class="ai-task-status ${statusClass(completion.tone === 'is-warning' ? 'canceled' : task.status)}">${escapeHtml(completion.label)}</span>
                </div>
                <h3>${escapeHtml(task.title || task.public_summary || '教学任务')}</h3>
                <div class="ai-task-detail__meta">
                    <span>${escapeHtml(task.teacher_name || '')}</span>
                    <span>${escapeHtml(task.task_type_label || '')}</span>
                    ${elapsed}
                    ${runtime}
                </div>
            </div>
            <div class="ai-task-detail__actions">${cancelButton}${deleteButton}</div>
        </header>
        ${ownerBody}
    `;
}

function renderTaskDetail(task, options = {}) {
    return renderAgentTaskMessage(task, options);
}

function getAgentTaskMessageNode(taskId) {
    const id = Number(taskId || 0);
    if (!id) {
        return null;
    }
    const surface = currentChatSurface();
    if (!surface.messagesBox) {
        return null;
    }
    const existing = agentTaskMessages.get(id);
    if (existing?.isConnected) {
        return existing;
    }
    const msgDiv = document.createElement('div');
    msgDiv.className = 'ai-chat-message assistant agent-task-message';
    msgDiv.dataset.agentTaskMessageId = String(id);
    const bubble = document.createElement('div');
    bubble.className = 'bubble';
    msgDiv.appendChild(bubble);
    surface.messagesBox.appendChild(msgDiv);
    agentTaskMessages.set(id, msgDiv);
    return msgDiv;
}

function renderAgentTaskMessage(task, { autoScroll = false } = {}) {
    if (!task?.id) {
        return;
    }
    const isNew = !agentTaskMessages.get(Number(task.id))?.isConnected;
    const msgDiv = getAgentTaskMessageNode(task.id);
    if (!msgDiv) {
        return;
    }
    const bubble = msgDiv.querySelector('.bubble') || document.createElement('div');
    bubble.className = 'bubble';
    const nextArticle = document.createElement('article');
    nextArticle.className = 'ai-agent-task-card';
    nextArticle.innerHTML = buildAgentTaskDetailHtml(task);
    const article = bubble.querySelector('.ai-agent-task-card');
    if (article) preserveAgentQuestionNodes(article, nextArticle);
    else bubble.appendChild(nextArticle);
    if (!bubble.parentNode) {
        msgDiv.appendChild(bubble);
    }
    closeExpiredAgentQuestions(task.id);
    const events = Array.isArray(task.events) ? task.events : [];
    const lastEventId = events.reduce((maxId, event) => Math.max(maxId, Number(event.id || 0)), 0);
    if (lastEventId > 0) {
        taskLastEventIds.set(Number(task.id), lastEventId);
    }
    refreshAgentComposerChrome();
    renderAgentStarters();
    // 轮询刷新只做粘性跟随（force=false），不打断用户向上浏览输出内容。
    currentChatSurface().scrollToBottom(autoScroll || isNew);
}

function appendTaskEventsToCard(taskId, events = []) {
    const id = Number(taskId || 0);
    const container = $(`[data-agent-events="${id}"]`);
    if (!id || !container || !Array.isArray(events) || !events.length) {
        return;
    }
    const knownIds = new Set(
        Array.from(container.querySelectorAll('[data-agent-event-id]'))
            .map((node) => Number(node.dataset.agentEventId || 0))
            .filter(Boolean)
    );
    const freshEvents = events.filter((event) => !knownIds.has(Number(event.id || 0)));
    if (!freshEvents.length) {
        return;
    }
    const lastId = freshEvents.reduce((maxId, event) => Math.max(maxId, Number(event.id || 0)), taskLastEventIds.get(id) || 0);
    taskLastEventIds.set(id, lastId);
    const freshHtml = freshEvents.map(renderTaskEventHtml).filter(Boolean).join('');
    if (!freshHtml) {
        return;
    }
    const empty = container.querySelector('.ai-task-detail__empty');
    if (empty) {
        empty.remove();
    }
    container.insertAdjacentHTML('beforeend', freshHtml);
}

async function refreshTerminalTaskCard(taskId) {
    if (Number(selectedTaskId) === Number(taskId)) {
        await loadTaskDetail(taskId);
        await refreshTasks({ silent: true });
    }
}

function refreshAgentTaskFinishNotification(taskId) {
    const id = Number(taskId || 0);
    if (!id || taskTerminalNotificationRefreshIds.has(id)) {
        return;
    }
    taskTerminalNotificationRefreshIds.add(id);
    const refreshBell = window.refreshMessageCenterBell;
    if (typeof refreshBell === 'function') {
        Promise.resolve(refreshBell({ allowPopup: true })).catch(() => {});
        return;
    }
    window.dispatchEvent(new CustomEvent('message-center:refresh-requested', {
        detail: { source: 'agent-task', taskId: id, allowPopup: true },
    }));
}

function closeTaskEventStream(taskId) {
    const id = Number(taskId || 0);
    const source = taskEventStreams.get(id);
    if (source) {
        source.close();
        taskEventStreams.delete(id);
    }
}

function handleTaskEventPayload(taskId, payload = {}) {
    const id = Number(taskId || payload.task_id || 0);
    if (!id || payload.status === 'error') {
        return;
    }
    appendTaskEventsToCard(id, payload.events || []);
    if (payload.last_event_id) {
        taskLastEventIds.set(id, Number(payload.last_event_id));
    }
    if ((payload.events || []).some((event) => ['question_requested', 'question_answered', 'question_closed'].includes(event.event_type || event.kind))) {
        refreshAgentQuestionTask(id).catch(() => {});
    }
    if (payload.is_terminal) {
        closeTaskEventStream(id);
        refreshAgentTaskFinishNotification(id);
        refreshTerminalTaskCard(id).catch(() => {});
    }
}

function startTaskEventStream(taskId) {
    const id = Number(taskId || 0);
    if (!id || taskEventStreamDisabled || typeof window.EventSource !== 'function') {
        return false;
    }
    if (taskEventStreams.has(id)) {
        return true;
    }
    const after = Number(taskLastEventIds.get(id) || 0);
    let source;
    try {
        source = new EventSource(`/api/agent-tasks/${id}/stream?after=${after}`);
    } catch {
        taskEventStreamDisabled = true;
        return false;
    }
    source.onmessage = (event) => {
        try {
            handleTaskEventPayload(id, JSON.parse(event.data || '{}'));
        } catch {
            // Malformed stream chunks are ignored; fallback polling remains available.
        }
    };
    source.onerror = () => {
        closeTaskEventStream(id);
        taskEventStreamDisabled = true;
    };
    taskEventStreams.set(id, source);
    return true;
}

function syncTaskEventStreams() {
    if (taskEventStreamDisabled || typeof window.EventSource !== 'function') {
        return false;
    }
    const modal = $('#ai-chat-modal');
    if (modal?.style.display !== 'block') {
        Array.from(taskEventStreams.keys()).forEach(closeTaskEventStream);
        return true;
    }
    const visibleIds = new Set(visibleAgentTaskIds());
    Array.from(taskEventStreams.keys()).forEach((taskId) => {
        if (!visibleIds.has(Number(taskId))) {
            closeTaskEventStream(taskId);
        }
    });
    visibleIds.forEach((taskId) => startTaskEventStream(taskId));
    return true;
}

function visibleAgentTaskIds() {
    return Array.from(document.querySelectorAll('[data-agent-events]'))
        .map((node) => Number(node.dataset.agentEvents || 0))
        .filter(Boolean);
}

async function pollTaskEventsOnce() {
    if (taskEventPollBusy || !CONFIG.taskCenterEnabled) {
        return;
    }
    if (syncTaskEventStreams()) {
        return;
    }
    const modal = $('#ai-chat-modal');
    if (modal?.style.display !== 'block') {
        return;
    }
    const taskIds = Array.from(new Set(visibleAgentTaskIds()));
    if (!taskIds.length) {
        return;
    }
    taskEventPollBusy = true;
    try {
        for (const taskId of taskIds) {
            const after = Number(taskLastEventIds.get(Number(taskId)) || 0);
            const data = await apiJson(`/api/agent-tasks/${taskId}/events?after=${after}`);
            handleTaskEventPayload(taskId, data);
        }
    } catch {
        // The 5s full task refresh is the fallback; keep this channel quiet.
    } finally {
        taskEventPollBusy = false;
    }
}

function startTaskEventPolling() {
    if (!CONFIG.taskCenterEnabled || taskEventPollTimer) {
        return;
    }
    taskEventPollTimer = window.setInterval(() => {
        pollTaskEventsOnce();
    }, TASK_EVENT_POLL_MS);
}

async function loadTaskDetail(taskId, { autoScroll = false } = {}) {
    const id = Number(taskId);
    const version = (taskDetailRequestVersions.get(id) || 0) + 1;
    taskDetailRequestVersions.set(id, version);
    const data = await apiJson(`/api/agent-tasks/${taskId}`);
    if (taskDetailRequestVersions.get(id) === version) renderTaskDetail(data.task, { autoScroll });
    return data.task;
}

function removeAgentTaskMessage(taskId) {
    const id = Number(taskId || 0);
    const node = agentTaskMessages.get(id);
    if (node?.isConnected) {
        node.remove();
    }
    agentTaskMessages.delete(id);
    clearTimeout(agentQuestionExpiryTimers.get(id));
    agentQuestionExpiryTimers.delete(id);
}

async function deleteAgentTask(taskId) {
    const id = Number(taskId || 0);
    if (!id) {
        return;
    }
    if (!window.confirm('确定从历史记录中删除这条 Agent 任务吗？')) {
        return;
    }
    const data = await apiJson(`/api/agent-tasks/${id}`, { method: 'DELETE' });
    const deletedIds = Array.isArray(data.task_ids) && data.task_ids.length
        ? data.task_ids.map((item) => Number(item || 0)).filter(Boolean)
        : [id];
    deletedIds.forEach(removeAgentTaskMessage);
    if (deletedIds.some((item) => Number(selectedTaskId) === Number(item))) {
        selectedTaskId = null;
    }
    lastTaskPayload = data;
    setQueueState(data.queue_state || {}, data.counts || {});
    renderTaskList(data.tasks || []);
    refreshAgentComposerChrome();
    renderAgentStarters();
    notify('任务历史已删除。', 'success');
}

async function clearAgentTaskHistory() {
    if (!window.confirm('确定删除你所有已结束的 Agent 任务历史吗？正在排队或执行中的任务不会删除。')) {
        return;
    }
    const data = await apiJson('/api/agent-tasks/history', { method: 'DELETE' });
    (data.task_ids || []).forEach(removeAgentTaskMessage);
    if ((data.task_ids || []).some((id) => Number(id) === Number(selectedTaskId))) {
        selectedTaskId = null;
    }
    lastTaskPayload = data;
    setQueueState(data.queue_state || {}, data.counts || {});
    renderTaskList(data.tasks || []);
    refreshAgentComposerChrome();
    renderAgentStarters();
    notify(data.deleted_count ? `已删除 ${data.deleted_count} 条任务历史。` : '没有可删除的已结束任务。', 'success');
}

async function copyTextToClipboard(text) {
    const value = String(text || '').trim();
    if (!value) {
        return false;
    }
    if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(value);
        return true;
    }
    const textarea = document.createElement('textarea');
    textarea.value = value;
    textarea.setAttribute('readonly', 'readonly');
    textarea.style.position = 'fixed';
    textarea.style.left = '-9999px';
    document.body.appendChild(textarea);
    textarea.select();
    const copied = document.execCommand?.('copy') || false;
    textarea.remove();
    return copied;
}

async function openManualAgentAction(button) {
    const taskId = Number(button.dataset.agentActionManual || 0);
    const actionIndex = Number(button.dataset.actionIndex || 0);
    if (!taskId) {
        return;
    }
    const preview = await apiJson(`/api/agent-tasks/${taskId}/actions/${actionIndex}/preview`, {
        method: 'POST',
        body: JSON.stringify({ params: {} }),
    });
    const data = await apiJson(`/api/agent-tasks/${taskId}/actions/${actionIndex}/execute`, {
        method: 'POST',
        body: JSON.stringify({
            params: {},
            confirmation_token: preview.confirmation_token,
        }),
    });
    const copyText = data.result?.copy_text || data.result?.summary || '';
    const copied = await copyTextToClipboard(copyText).catch(() => false);
    notify(copied ? '已复制草稿内容，正在打开消息中心。' : '正在打开消息中心，请手动复制草稿内容。', copied ? 'success' : 'warning');
    if (data.task) {
        renderTaskDetail(data.task, { autoScroll: true });
        await refreshTasks({ silent: true });
    }
    window.open(safeLocalHref(data.result?.url) || '/messages', '_blank', 'noopener');
}

async function executeAgentAction(button) {
    const taskId = Number(button.dataset.agentActionConfirm || 0);
    const actionIndex = Number(button.dataset.actionIndex || 0);
    const block = button.closest('[data-agent-action-block]');
    const params = {};
    const recipients = block?.querySelector('[data-agent-action-recipients]');
    if (recipients) {
        const selected = [...recipients.selectedOptions].map((option) => option.value).filter(Boolean);
        if (!selected.length || selected.length > 30 || recipients.dataset.loaded !== 'true') {
            throw new Error('请先选择 1 至 30 位可联系的收件人。');
        }
        if (recipients.dataset.recipientAction === 'send_student_notification') {
            params.recipient_identities = selected;
        } else {
            params.contact_identity = selected[0];
        }
    }
    const titleInput = block?.querySelector('[data-agent-action-title]');
    if (titleInput && titleInput.value.trim()) {
        params.title = titleInput.value.trim();
    }
    button.disabled = true;
    try {
        const preview = await apiJson(`/api/agent-tasks/${taskId}/actions/${actionIndex}/preview`, {
            method: 'POST',
            body: JSON.stringify({ params }),
        });
        if (preview.execution_mode === 'user_confirmation') {
            const { openAgentUserConfirmation } = await import('./agent_user_confirmation.js');
            await openAgentUserConfirmation({ taskId, actionIndex, preview, apiJson,
                onComplete: async (data) => {
                    if (data.task) renderTaskDetail(data.task, { autoScroll: true });
                    await refreshTasks({ silent: true }).catch(() => {});
                    notify(data.result?.label || '业务操作已完成。', 'success');
                },
                onClose: () => { if (button.isConnected) button.focus({ preventScroll: true }); },
            });
            return;
        }
        if (preview.execution_mode === 'secure_input' || preview.secure_fields?.length) {
            await confirmSecureAgentAction({ button, taskId, actionIndex, params, preview });
            return;
        }
        const data = await apiJson(`/api/agent-tasks/${taskId}/actions/${actionIndex}/execute`, {
            method: 'POST',
            body: JSON.stringify({
                params,
                confirmation_token: preview.confirmation_token,
            }),
        });
        renderTaskDetail(data.task, { autoScroll: true });
        await refreshTasks({ silent: true });
        notify(data.result?.label ? `已执行：${data.result.label}` : '动作已执行。', 'success');
    } finally {
        button.disabled = false;
    }
}

function confirmSecureAgentAction({ button, taskId, actionIndex, params, preview }) {
    if (document.querySelector('[data-agent-secure-dialog]')) return Promise.resolve();
    // Secret values exist only in these input elements and the final HTTPS body.
    // Keep this dialog outside the task card so polling cannot replace its inputs.
    const fields = Array.isArray(preview.secure_fields) ? preview.secure_fields : [];
    if (fields.length !== 1 || fields[0].name !== 'password' || fields[0].type !== 'password') {
        throw new Error('当前安全输入表单不可用，请刷新任务后重试。');
    }
    const dialog = document.createElement('dialog');
    dialog.className = 'ai-agent-reconciliation-dialog ai-agent-secure-dialog';
    dialog.dataset.agentSecureDialog = 'true';
    dialog.setAttribute('aria-labelledby', 'agent-secure-title');
    dialog.innerHTML = `<form><header><h3 id="agent-secure-title"></h3><button type="button" data-secure-close aria-label="关闭安全操作">×</button></header>
        <p data-secure-summary></p><div class="ai-agent-reconciliation-facts"><strong>确认操作对象与内容</strong><pre data-secure-parameters></pre></div>
        <fieldset><label class="ai-agent-reconciliation-note"><span data-secure-label></span><input type="password" name="password" autocomplete="new-password" autocapitalize="off" spellcheck="false" required></label>
        <p>口令仅用于这次账户操作，不会提供给 Agent 或保存在任务记录中。</p></fieldset>
        <p data-secure-feedback role="status" aria-live="polite"></p><footer><button type="button" class="btn btn-outline" data-secure-close>取消</button><button type="submit" class="btn btn-primary">确认执行</button></footer></form>`;
    dialog.querySelector('h3').textContent = preview.label || '安全账户操作';
    dialog.querySelector('[data-secure-summary]').textContent = preview.summary || '请核对信息并输入口令。';
    const publicParams = preview.params || params;
    const labels = { teacher_id: '教师编号', name: '姓名', username: '登录名', email: '邮箱', school_code: '学校代码', school_name: '学校', college: '学院', college_name: '学院', department: '部门', department_name: '部门', is_super_admin: '设为超级管理员' };
    dialog.querySelector('[data-secure-parameters]').textContent = Object.entries(publicParams)
        .filter(([key]) => !key.startsWith('expected_'))
        .map(([key, value]) => `${preview.fields?.[key]?.label || labels[key] || key}：${typeof value === 'boolean' ? (value ? '是' : '否') : typeof value === 'object' ? JSON.stringify(value) : String(value ?? '')}`).join('\n');
    dialog.querySelector('[data-secure-label]').textContent = fields[0].label || '口令';
    const input = dialog.querySelector('input');
    input.minLength = Math.max(8, Number(fields[0].min_length) || 8);
    input.maxLength = Math.min(128, Number(fields[0].max_length) || 128);
    const feedback = dialog.querySelector('[data-secure-feedback]');
    let submitting = false;
    let settled = false;
    let firstAttempt = true;
    document.body.appendChild(dialog);
    return new Promise((resolve) => {
        const clearInput = () => { input.value = ''; };
        const finish = () => {
            if (settled) return;
            settled = true;
            clearInput();
            window.removeEventListener('pagehide', clearInput);
            dialog.remove();
            const target = button.isConnected ? button : document.querySelector(`[data-agent-action-confirm="${taskId}"][data-action-index="${actionIndex}"]`);
            if (target) { target.disabled = false; target.focus({ preventScroll: true }); }
            resolve();
        };
        window.addEventListener('pagehide', clearInput);
        dialog.addEventListener('close', finish);
        dialog.addEventListener('click', (event) => { if (event.target.closest('[data-secure-close]') && !submitting) dialog.close(); });
        dialog.addEventListener('keydown', (event) => { if (event.key === 'Escape') event.stopPropagation(); });
        dialog.addEventListener('cancel', (event) => { if (submitting) event.preventDefault(); });
        dialog.querySelector('form').addEventListener('submit', async (event) => {
            event.preventDefault();
            if (submitting || settled) return;
            if (input.value.length < input.minLength || input.value.length > input.maxLength) {
                feedback.textContent = `请输入 ${input.minLength} 至 ${input.maxLength} 个字符的口令。`;
                input.focus();
                return;
            }
            submitting = true;
            dialog.querySelector('fieldset').disabled = true;
            dialog.querySelectorAll('button').forEach((node) => { node.disabled = true; });
            feedback.textContent = '正在提交安全操作…';
            try {
                const currentPreview = firstAttempt ? preview : await apiJson(`/api/agent-tasks/${taskId}/actions/${actionIndex}/preview`, { method: 'POST', body: JSON.stringify({ params }) });
                firstAttempt = false;
                if (currentPreview.action !== preview.action || JSON.stringify(currentPreview.params || params) !== JSON.stringify(publicParams)) {
                    feedback.textContent = '操作内容已经变化，请关闭窗口并重新核对。';
                    return;
                }
                const data = await apiJson(`/api/agent-tasks/${taskId}/actions/${actionIndex}/execute`, {
                    method: 'POST',
                    body: JSON.stringify({ params, confirmation_token: currentPreview.confirmation_token, secure_inputs: { password: input.value } }),
                });
                clearInput();
                dialog.close();
                if (data.task) renderTaskDetail(data.task, { autoScroll: true });
                if (data.result?.requires_relogin) {
                    notify('账户操作已完成，当前会话已结束，请重新登录。', 'warning');
                } else {
                    await refreshTasks({ silent: true }).catch(() => {});
                    notify(data.result?.label ? `已执行：${data.result.label}` : '账户操作已完成。', 'success');
                }
            } catch (error) {
                const latest = await apiJson(`/api/agent-tasks/${taskId}`).catch(() => null);
                const executed = latest?.task?.result_detail?.proposed_actions?.[actionIndex]?.executed;
                if (executed) {
                    clearInput();
                    dialog.close();
                    renderTaskDetail(latest.task, { autoScroll: true });
                    notify(executed.label || '已确认该账户操作完成。', 'success');
                    return;
                }
                // Never reflect an accidentally echoed secret from an error body.
                feedback.textContent = String(error.message || '安全操作未完成，请重试。').split(input.value || '\u0000').join('••••');
            } finally {
                submitting = false;
                dialog.querySelector('fieldset').disabled = false;
                dialog.querySelectorAll('button').forEach((node) => { node.disabled = false; });
            }
        });
        dialog.showModal();
        input.focus();
    });
}

async function loadAgentActionRecipients(block) {
    const select = block?.querySelector('[data-agent-action-recipients]');
    if (!select || select.dataset.loaded === 'true' || select.dataset.loading === 'true') return;
    const confirm = block.querySelector('[data-agent-action-confirm]');
    const status = block.querySelector('[data-agent-recipient-status]');
    if (confirm) confirm.disabled = true;
    select.disabled = true;
    select.dataset.loading = 'true';
    try {
        const offeringId = Number(select.dataset.classOfferingId) || 0;
        const data = await apiJson(offeringId ? `/api/classrooms/${offeringId}/private/contacts` : '/api/message-center/private/contacts');
        const initial = new Set(JSON.parse(select.dataset.initialRecipients || '[]'));
        const contacts = [...new Map((data.contacts || []).filter((contact) =>
            contact.can_send && !contact.is_blocked && ['teacher', 'student'].includes(contact.role) &&
            (select.dataset.recipientAction !== 'send_student_notification' || contact.role === 'student')
        ).map((contact) => [contact.identity, contact])).values()];
        select.innerHTML = `${select.multiple ? '' : '<option value="">请选择联系人</option>'}${contacts.map((contact) => `<option value="${escapeHtml(contact.identity)}" ${initial.has(contact.identity) ? 'selected' : ''}>${escapeHtml(contact.display_name || '')} · ${escapeHtml(contact.subtitle || (contact.role === 'teacher' ? '教师' : '学生'))}</option>`).join('')}`;
        select.dataset.loaded = 'true';
        if (status) status.textContent = contacts.length ? '请核对收件人后确认。发送时平台会再次检查联系人权限。' : '当前没有可发送的联系人。';
        if (confirm) confirm.disabled = !contacts.length;
    } catch (error) {
        if (status) status.textContent = '联系人读取失败；请收起后重新打开以重试。';
        throw error;
    } finally {
        select.disabled = false;
        select.dataset.loading = 'false';
    }
}

function prefillSupplementFollowUp(button) {
    const encoded = button.dataset.agentSupplementFollowup || '';
    const text = decodeURIComponent(encoded || '').trim();
    if (!text) {
        return;
    }
    setAgentMode(true, { showRuntimeWarning: true });
    const input = currentChatSurface().textarea;
    if (!input) {
        notify('可在底部输入框继续补充。', 'info');
        return;
    }
    input.value = text;
    resetTextareaHeight(input);
    refreshAgentComposerChrome();
    renderAgentStarters();
    input.focus();
    notify('已把补充说明填到底部输入框。', 'success');
}

async function retryAgentTask(button, { edit = false } = {}) {
    const taskId = Number((edit ? button.dataset.agentRetryEdit : button.dataset.agentRetry) || 0);
    let instruction = '';
    if (edit) {
        const value = window.prompt('输入新的重试说明（留空将使用原任务要求）：', '');
        if (value === null) {
            return;
        }
        instruction = value.trim();
    }
    button.disabled = true;
    try {
        const data = await apiJson(`/api/agent-tasks/${taskId}/retry`, {
            method: 'POST',
            body: JSON.stringify({ instruction }),
        });
        selectedTaskId = data.task?.id || selectedTaskId;
        renderTaskDetail(data.task, { autoScroll: true });
        await refreshTasks({ silent: true });
        notify('重试任务已加入队列。', 'success');
    } finally {
        button.disabled = false;
    }
}

async function refreshTasks({ silent = false } = {}) {
    if (!CONFIG.taskCenterEnabled) {
        return;
    }
    try {
        const data = await apiJson('/api/agent-tasks');
        lastTaskPayload = data;
        setQueueState(data.queue_state || {}, data.counts || {});
        renderTaskList(data.tasks || []);
        refreshAgentComposerChrome();
        renderAgentStarters();
        if (selectedTaskId) {
            const selected = (data.tasks || []).find((task) => Number(task.id) === Number(selectedTaskId));
            if (selected?.is_owner) {
                await loadTaskDetail(selectedTaskId);
            } else {
                renderTaskDetail(selected || null);
            }
        }
        const activeOwnTask = (data.tasks || []).find((task) => task.is_owner && task.is_active);
        if (!selectedTaskId && activeOwnTask) {
            selectedTaskId = activeOwnTask.id;
            await loadTaskDetail(activeOwnTask.id, { autoScroll: true });
            refreshAgentComposerChrome();
            renderAgentStarters();
        }
    } catch (error) {
        if (!silent) {
            notify(error.message || '任务中心加载失败', 'error');
        }
    }
}

async function loadBootstrap({ showRuntimeWarning = false } = {}) {
    if (!CONFIG.taskCenterEnabled || taskBootstrapLoaded) {
        if (showRuntimeWarning) {
            showRuntimeUnavailableWarning();
        }
        return;
    }
    if (taskBootstrapPromise) {
        await taskBootstrapPromise;
        if (showRuntimeWarning) {
            showRuntimeUnavailableWarning();
        }
        return;
    }
    taskBootstrapPromise = (async () => {
        const data = await apiJson('/api/agent-tasks/bootstrap');
        taskBootstrapLoaded = true;
        agentRuntimeConfigured = Boolean(data.runtime_configured);
        workflowCatalog = Array.isArray(data.workflow_catalog) ? data.workflow_catalog : [];
        taskTypesCatalog = Array.isArray(data.task_types) ? data.task_types : [];
        setQueueState(data.queue_state || {}, data.counts || {});
        renderTaskList(data.tasks || []);
        refreshAgentComposerChrome();
        renderAgentStarters();
        loadAgentSubscriptions({ silent: true });
    })();
    try {
        await taskBootstrapPromise;
        if (showRuntimeWarning) {
            showRuntimeUnavailableWarning();
        }
    } finally {
        taskBootstrapPromise = null;
    }
}

function startTaskPolling() {
    if (!CONFIG.taskCenterEnabled || taskPollTimer) {
        return;
    }
    taskPollTimer = window.setInterval(() => {
        const modal = $('#ai-chat-modal');
        if (modal?.style.display === 'block') {
            refreshTasks({ silent: true });
        }
    }, TASK_REFRESH_MS);
}

function setAgentHistoryOpen(open) {
    const drawer = $('#ai-agent-history-drawer');
    const toggle = $('#ai-agent-history-toggle');
    if (!drawer) {
        return;
    }
    drawer.hidden = !open;
    drawer.classList.toggle('is-open', open);
    toggle?.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (open) {
        refreshTasks({ silent: true });
        loadAgentSubscriptions({ silent: true });
    }
}

function refreshAgentComposerChrome() {
    const surface = currentChatSurface();
    const targetTask = agentMode ? currentAgentComposerTargetTask() : null;
    const isActiveTarget = Boolean(targetTask?.is_active && !targetTask?.is_terminal);
    if (surface.textarea) {
        surface.textarea.placeholder = agentMode
            ? (isActiveTarget
                ? '给正在执行的 Agent 补充说明...'
                : (targetTask ? '对这个 Agent 结果继续提要求...' : '描述要让 Agent 执行的平台任务...'))
            : '把当前页面作为上下文提问...';
    }
    if (surface.attachBtn) {
        const attachmentDisabled = Boolean(agentMode && targetTask);
        surface.attachBtn.disabled = attachmentDisabled;
        surface.attachBtn.title = attachmentDisabled
            ? (isActiveTarget ? '任务运行中，补充说明暂不支持附件' : '追问当前结果暂不支持附件')
            : (agentMode ? '上传附件给 Agent 任务' : '上传附件');
        surface.attachBtn.setAttribute('aria-label', surface.attachBtn.title);
    }
    if (surface.deepThinkBtn) {
        surface.deepThinkBtn.disabled = Boolean(agentMode && targetTask);
    }
    if (surface.sendBtn) {
        if (agentMode) {
            surface.sendBtn.disabled = Boolean(agentSubmitting);
            const sendLabel = isActiveTarget ? '补充到当前 Agent 任务' : (targetTask ? '追问当前 Agent 结果' : '加入 Agent 队列');
            surface.sendBtn.title = sendLabel;
            surface.sendBtn.setAttribute('aria-label', sendLabel);
        } else if (chatComponent?.updateSendButtonState) {
            chatComponent.updateSendButtonState();
            surface.sendBtn.title = '发送';
            surface.sendBtn.setAttribute('aria-label', '发送');
        } else {
            surface.sendBtn.disabled = !surface.textarea?.value.trim();
            surface.sendBtn.title = '发送';
            surface.sendBtn.setAttribute('aria-label', '发送');
        }
    }
}

function setAgentMode(enabled, { persist = true, showRuntimeWarning = false } = {}) {
    if (!CONFIG.taskCenterEnabled) {
        return;
    }
    agentMode = Boolean(enabled);
    if (!agentMode) {
        selectedAgentWorkflowKey = '';
    }
    const container = $('.ai-workspace-container');
    const toggle = $('#ai-agent-mode-toggle');
    const memoryToggle = $('#ai-agent-memory-toggle');
    container?.classList.toggle('is-agent-mode', agentMode);
    if (memoryToggle) {
        memoryToggle.hidden = !agentMode;
    }
    document.body.dataset.aiAgentMode = agentMode ? 'agent' : 'chat';
    toggle?.classList.toggle('is-active', agentMode);
    toggle?.setAttribute('aria-pressed', agentMode ? 'true' : 'false');
    $all('[data-ai-mode-select]').forEach((button) => {
        const isActive = button.dataset.aiModeSelect === (agentMode ? 'agent' : 'chat');
        button.classList.toggle('is-active', isActive);
        button.setAttribute('aria-pressed', isActive ? 'true' : 'false');
    });
    if (toggle) {
        toggle.title = agentMode ? '切换为普通 AI 对话' : '切换为 Agent 任务';
    }
    refreshAgentComposerChrome();
    refreshContextPreview();
    renderAgentStarters();
    setQueueState(lastTaskPayload.queue_state || {}, lastTaskPayload.counts || {});
    if (persist) {
        try {
            window.localStorage.setItem('lanshare.aiWorkspace.agentMode', agentMode ? '1' : '0');
        } catch {
            // Ignore storage restrictions.
        }
    }
    if (agentMode) {
        loadBootstrap({ showRuntimeWarning }).then(() => refreshTasks({ silent: true })).catch((error) => notify(error.message || 'Agent 加载失败', 'error'));
        startTaskPolling();
    } else {
        updateComposerPresence(false).catch(() => {});
    }
}

function prefillAgentTaskFromChat(instruction) {
    const text = String(instruction || '').trim();
    if (!text) {
        return;
    }
    setAgentMode(true, { showRuntimeWarning: true });
    const surface = currentChatSurface();
    if (surface.textarea) {
        surface.textarea.value = text;
        resetTextareaHeight(surface.textarea);
        surface.textarea.focus();
    }
    chatComponent?.updateSendButtonState?.();
    notify('已切换到 Agent 模式，可补充说明后提交。', 'success');
}

async function updateComposerPresence(active) {
    if (!CONFIG.taskCenterEnabled) {
        return;
    }
    if (!active && !composerActive) {
        return;
    }
    composerActive = Boolean(active);
    try {
        const data = await apiJson('/api/agent-tasks/composer', {
            method: 'POST',
            body: JSON.stringify({
                active: composerActive,
                page_context: composerActive ? collectPageContext() : {},
            }),
        });
        setQueueState(data.queue_state || {});
    } catch {
        // Presence is advisory; do not interrupt typing.
    }
}

function scheduleComposerHeartbeat() {
    if (composerHeartbeatTimer) {
        window.clearInterval(composerHeartbeatTimer);
    }
    composerHeartbeatTimer = window.setInterval(() => {
        if (agentMode && document.activeElement === $('#ai-chat-textarea')) {
            updateComposerPresence(true).catch(() => {});
        }
    }, COMPOSER_HEARTBEAT_MS);
}

function touchComposerPresence() {
    if (!agentMode) {
        return;
    }
    const now = Date.now();
    if (now - lastComposerTouchAt > 3000) {
        lastComposerTouchAt = now;
        updateComposerPresence(true).catch(() => {});
        return;
    }
    if (composerTouchTimer) {
        window.clearTimeout(composerTouchTimer);
    }
    composerTouchTimer = window.setTimeout(() => {
        lastComposerTouchAt = Date.now();
        updateComposerPresence(true).catch(() => {});
    }, 900);
}

function formatAgentFileSize(bytes) {
    const value = Number(bytes || 0);
    if (value >= 1024 * 1024) {
        return `${(value / 1024 / 1024).toFixed(1)}MB`;
    }
    if (value >= 1024) {
        return `${Math.round(value / 1024)}KB`;
    }
    return `${value}B`;
}

function agentAttachmentExtension(fileName) {
    const text = String(fileName || '').trim().toLowerCase();
    const dotIndex = text.lastIndexOf('.');
    return dotIndex > -1 ? text.slice(dotIndex) : '';
}

function validateAgentAttachments(files = []) {
    const normalized = Array.from(files || []);
    if (normalized.length > AGENT_ATTACHMENT_MAX_FILES) {
        return `单个 Agent 任务最多携带 ${AGENT_ATTACHMENT_MAX_FILES} 个附件。`;
    }
    let total = 0;
    for (const file of normalized) {
        const extension = agentAttachmentExtension(file.name);
        if (!AGENT_ATTACHMENT_ALLOWED_EXTENSIONS.has(extension)) {
            return `附件 ${file.name || '未命名文件'} 类型暂不支持。支持：${AGENT_ATTACHMENT_ALLOWED_TYPES_LABEL}。`;
        }
        const size = Number(file.size || 0);
        total += size;
        if (size > AGENT_ATTACHMENT_MAX_FILE_BYTES) {
            return `附件 ${file.name || '未命名文件'} 超过 ${formatAgentFileSize(AGENT_ATTACHMENT_MAX_FILE_BYTES)} 上限。`;
        }
    }
    if (total > AGENT_ATTACHMENT_MAX_TOTAL_BYTES) {
        return `附件总大小超过 ${formatAgentFileSize(AGENT_ATTACHMENT_MAX_TOTAL_BYTES)} 上限。`;
    }
    return '';
}

function agentAttachmentPreviews(files = []) {
    return Array.from(files || []).map((file) => ({
        type: file.type?.startsWith('image/') ? 'image' : 'file',
        name: file.name,
        previewUrl: file.type?.startsWith('image/') ? URL.createObjectURL(file) : null,
    }));
}

async function submitActiveAgentSupplementFromComposer(targetTask, instruction, pendingFiles = []) {
    const taskId = Number(targetTask?.id || 0);
    const isActiveTarget = Boolean(targetTask?.is_active && !targetTask?.is_terminal);
    if (!taskId) {
        return false;
    }
    const surface = currentChatSurface();
    const textarea = surface.textarea;
    if (instruction.length < 2) {
        notify(isActiveTarget ? '请补充要追加给当前任务的说明。' : '请补充要继续追问的要求。', 'warning');
        return true;
    }
    if (pendingFiles.length) {
        notify(isActiveTarget ? '当前任务运行中，补充说明暂不支持附件；附件可在任务完成后作为追问提交。' : '追问当前结果暂不支持附件；需要附件时请新建 Agent 任务。', 'warning');
        return true;
    }
    agentSubmitting = true;
    refreshAgentComposerChrome();
    try {
        const data = await apiJson(`/api/agent-tasks/${taskId}/follow-up`, {
            method: 'POST',
            body: JSON.stringify({ instruction }),
        });
        if (textarea) {
            textarea.value = '';
            resetTextareaHeight(textarea);
        }
        selectedTaskId = data.task?.id || taskId;
        renderTaskDetail(data.task, { autoScroll: true });
        await refreshTasks({ silent: true });
        notify(data.supplemented ? '补充说明已记录到当前任务。' : '追问任务已加入队列。', 'success');
    } catch (error) {
        notify(error.message || '补充说明提交失败', 'error');
    } finally {
        agentSubmitting = false;
        refreshAgentComposerChrome();
        renderAgentStarters();
        textarea?.focus();
    }
    return true;
}

async function submitAgentTaskFromChat() {
    if (!CONFIG.taskCenterEnabled || agentSubmitting) {
        return;
    }
    const surface = currentChatSurface();
    const textarea = surface.textarea;
    const originalInput = textarea?.value || '';
    const instruction = originalInput.trim();
    const pendingFiles = Array.from(chatComponent?.pendingFiles || []);
    const targetTask = currentAgentComposerTargetTask();
    if (targetTask) {
        await submitActiveAgentSupplementFromComposer(targetTask, instruction, pendingFiles);
        return;
    }
    if (instruction.length < 6) {
        notify('请补充更明确的任务内容。', 'warning');
        return;
    }
    const attachmentError = validateAgentAttachments(pendingFiles);
    if (attachmentError) {
        notify(attachmentError, 'warning');
        return;
    }
    agentSubmitting = true;
    if (surface.sendBtn) {
        surface.sendBtn.disabled = true;
    }
    const context = collectPageContext();
    const selectedWorkflow = selectedAgentWorkflowKey ? workflowByKey(selectedAgentWorkflowKey) : null;
    const taskType = selectedWorkflow?.task_type || inferAgentTaskType(instruction, context);
    try {
        const payload = {
            task_type: taskType,
            instruction,
            page_context: context,
            chat_session_uuid: chatComponent?.currentSessionUUID || '',
            deep_thinking: Boolean(chatComponent?.isDeepThinking),
            no_history: Boolean($('#ai-agent-no-history')?.checked),
        };
        let requestBody = JSON.stringify(payload);
        if (pendingFiles.length) {
            const formData = new FormData();
            formData.append('payload', JSON.stringify(payload));
            pendingFiles.forEach((file) => formData.append('files', file));
            requestBody = formData;
        }
        const data = await apiJson('/api/agent-tasks', {
            method: 'POST',
            body: requestBody,
        });
        surface.renderMessage('user', instruction, agentAttachmentPreviews(pendingFiles));
        if (textarea && textarea.value === originalInput) {
            textarea.value = '';
            resetTextareaHeight(textarea);
        }
        if (chatComponent) {
            // Files added while the request was pending belong to the next draft.
            const remainingFiles = chatComponent.pendingFiles.filter((file) => !pendingFiles.includes(file));
            if (remainingFiles.length) {
                chatComponent.pendingFiles = remainingFiles;
                chatComponent.renderPreviews?.();
                chatComponent.updateSendButtonState?.();
            } else {
                chatComponent.clearPendingFiles?.();
            }
        }
        await updateComposerPresence(Boolean(textarea?.value.trim()));
        selectedAgentWorkflowKey = '';
        selectedTaskId = data.task?.id || null;
        if (data.task) {
            renderAgentTaskMessage(data.task, { autoScroll: true });
        }
        const noHistoryInput = $('#ai-agent-no-history');
        if (noHistoryInput) {
            noHistoryInput.checked = false;
        }
        // A refresh failure does not invalidate the task already accepted by the server.
        await refreshTasks({ silent: true }).catch(() => {});
        notify('Agent 任务已加入全平台队列。', 'success');
    } catch (error) {
        surface.renderMessage('assistant', `Agent 任务提交失败：${error.message || '未知错误'}`);
        notify(error.message || 'Agent 任务提交失败', 'error');
    } finally {
        agentSubmitting = false;
        if (surface.sendBtn) {
            surface.sendBtn.disabled = false;
        }
        renderAgentStarters();
        textarea?.focus();
    }
}

function bindTaskCenter() {
    if (!CONFIG.taskCenterEnabled) {
        return;
    }
    bindAgentQuestionInteractions($('#ai-chat-messages-box'));
    window.addEventListener('lanshare:agent-handoff', (event) => {
        selectedAgentWorkflowKey = '';
        prefillAgentTaskFromChat(event.detail?.instruction || '');
    });
    $('#ai-agent-mode-toggle')?.addEventListener('click', () => setAgentMode(!agentMode, { showRuntimeWarning: true }));
    $all('[data-ai-mode-select]').forEach((button) => {
        button.addEventListener('click', () => {
            setAgentMode(button.dataset.aiModeSelect === 'agent', { showRuntimeWarning: true });
        });
    });
    $('#ai-agent-history-toggle')?.addEventListener('click', () => {
        const drawer = $('#ai-agent-history-drawer');
        setAgentHistoryOpen(!drawer || drawer.hidden);
    });
    $('#ai-agent-history-close')?.addEventListener('click', () => setAgentHistoryOpen(false));
    $('#ai-agent-history-clear')?.addEventListener('click', async () => {
        try {
            await clearAgentTaskHistory();
        } catch (error) {
            notify(error.message || '删除任务历史失败', 'error');
        }
    });
    $('#agent-task-list')?.addEventListener('click', async (event) => {
        const deleteButton = event.target.closest('[data-agent-delete]');
        if (deleteButton) {
            deleteButton.disabled = true;
            try {
                await deleteAgentTask(deleteButton.dataset.agentDelete);
            } catch (error) {
                notify(error.message || '删除任务历史失败', 'error');
            } finally {
                deleteButton.disabled = false;
            }
            return;
        }
        const button = event.target.closest('[data-agent-task-id]');
        if (!button) {
            return;
        }
        selectedTaskId = Number(button.dataset.agentTaskId);
        renderTaskList(lastTaskPayload.tasks || []);
        try {
            await loadTaskDetail(selectedTaskId, { autoScroll: true });
        } catch (error) {
            notify(error.message || '任务详情加载失败', 'error');
        }
    });
    $('#agent-subscription-list')?.addEventListener('change', async (event) => {
        const toggle = event.target.closest('[data-agent-sub-toggle]');
        const select = event.target.closest('[data-agent-sub-hour]');
        const key = toggle?.dataset.agentSubToggle || select?.dataset.agentSubHour || '';
        if (!key) {
            return;
        }
        const row = event.target.closest('.ai-agent-subscription-row');
        const enabled = toggle ? toggle.checked : Boolean(row?.querySelector('[data-agent-sub-toggle]')?.checked);
        const hour = Number(row?.querySelector('[data-agent-sub-hour]')?.value ?? 0);
        if (!enabled && select) {
            return;
        }
        try {
            await setAgentSubscription(key, { enabled, hour });
        } catch (error) {
            notify(error.message || '定时任务更新失败', 'error');
            await loadAgentSubscriptions({ silent: true });
        }
    });
    $('#agent-subscription-list')?.addEventListener('click', async (event) => {
        const button = event.target.closest('[data-agent-task-id]');
        if (!button) {
            return;
        }
        selectedTaskId = Number(button.dataset.agentTaskId);
        renderTaskList(lastTaskPayload.tasks || []);
        try {
            await loadTaskDetail(selectedTaskId, { autoScroll: true });
        } catch (error) {
            notify(error.message || '任务详情加载失败', 'error');
        }
    });
    $('#ai-agent-starters')?.addEventListener('click', (event) => {
        const starter = event.target.closest('[data-agent-starter]');
        if (starter) {
            applyAgentStarter(starter);
        }
    });
    $('#ai-chat-messages-box')?.addEventListener('click', async (event) => {
        const requestFollowup = event.target.closest('[data-agent-request-followup]');
        if (requestFollowup) {
            selectedTaskId = Number(requestFollowup.dataset.agentRequestFollowup);
            prefillAgentTaskFromChat(`请核对任务 #${selectedTaskId} 中已有平台请求的回执和真实业务记录，先确认是否已生效或仍在处理，不要直接重复执行不确定的请求。`);
            return;
        }
        const domainFollowup = event.target.closest('[data-agent-domain-followup]');
        if (domainFollowup) {
            selectedTaskId = Number(domainFollowup.dataset.agentDomainFollowup);
            prefillAgentTaskFromChat(`请跟进任务 #${selectedTaskId} 中已提交的课时文档生成，核对原生成任务状态、成品和课时绑定。不要重新创建生成任务。`);
            return;
        }
        const deleteButton = event.target.closest('[data-agent-delete]');
        if (deleteButton) {
            deleteButton.disabled = true;
            try {
                await deleteAgentTask(deleteButton.dataset.agentDelete);
            } catch (error) {
                notify(error.message || '删除任务历史失败', 'error');
            } finally {
                deleteButton.disabled = false;
            }
            return;
        }
        const manualActionButton = event.target.closest('[data-agent-action-manual]');
        if (manualActionButton) {
            manualActionButton.disabled = true;
            try {
                await openManualAgentAction(manualActionButton);
            } catch (error) {
                notify(error.message || '手动处理入口打开失败', 'error');
            } finally {
                manualActionButton.disabled = false;
            }
            return;
        }
        const actionOpenButton = event.target.closest('[data-agent-action-open]');
        if (actionOpenButton) {
            const block = actionOpenButton.closest('[data-agent-action-block]');
            const panel = block?.querySelector('.ai-task-action__confirm');
            if (panel) {
                panel.hidden = false;
            }
            try {
                await loadAgentActionRecipients(block);
            } catch (error) {
                notify(error.message || '联系人读取失败', 'error');
            }
            return;
        }
        const actionCancelButton = event.target.closest('[data-agent-action-cancel]');
        if (actionCancelButton) {
            const panel = actionCancelButton.closest('.ai-task-action__confirm');
            if (panel) {
                panel.hidden = true;
            }
            return;
        }
        const actionConfirmButton = event.target.closest('[data-agent-action-confirm]');
        if (actionConfirmButton) {
            try {
                await executeAgentAction(actionConfirmButton);
            } catch (error) {
                notify(error.message || '动作执行失败', 'error');
            }
            return;
        }
        const supplementFollowUpButton = event.target.closest('[data-agent-supplement-followup]');
        if (supplementFollowUpButton) {
            try {
                prefillSupplementFollowUp(supplementFollowUpButton);
            } catch {
                notify('补充说明回填失败，请手动复制。', 'warning');
            }
            return;
        }
        const retryEditButton = event.target.closest('[data-agent-retry-edit]');
        if (retryEditButton) {
            try {
                await retryAgentTask(retryEditButton, { edit: true });
            } catch (error) {
                notify(error.message || '重试失败', 'error');
            }
            return;
        }
        const retryButton = event.target.closest('[data-agent-retry]');
        if (retryButton) {
            try {
                await retryAgentTask(retryButton);
            } catch (error) {
                notify(error.message || '重试失败', 'error');
            }
            return;
        }
        const cancelButton = event.target.closest('[data-agent-cancel]');
        if (cancelButton) {
            cancelButton.disabled = true;
            try {
                const data = await apiJson(`/api/agent-tasks/${cancelButton.dataset.agentCancel}/cancel`, { method: 'POST' });
                renderTaskDetail(data.task);
                await refreshTasks({ silent: true });
                notify('已提交取消请求', 'success');
            } catch (error) {
                notify(error.message || '取消失败', 'error');
            } finally {
                cancelButton.disabled = false;
            }
        }
    });
    const sendButton = $('#ai-chat-btn-send');
    const textarea = $('#ai-chat-textarea');
    sendButton?.addEventListener('click', (event) => {
        if (!agentMode) {
            return;
        }
        event.preventDefault();
        event.stopPropagation();
        event.stopImmediatePropagation();
        submitAgentTaskFromChat();
    }, true);
    textarea?.addEventListener('keypress', (event) => {
        if (!agentMode || event.key !== 'Enter' || event.shiftKey) {
            return;
        }
        event.preventDefault();
        event.stopPropagation();
        event.stopImmediatePropagation();
        submitAgentTaskFromChat();
    }, true);
    textarea?.addEventListener('focus', () => {
        if (agentMode) {
            touchComposerPresence();
        }
    });
    textarea?.addEventListener('blur', () => {
        updateComposerPresence(false).catch(() => {});
    });
    textarea?.addEventListener('input', () => {
        if (agentMode && !textarea.value.trim()) {
            selectedAgentWorkflowKey = '';
        }
        renderAgentStarters();
        if (agentMode) {
            touchComposerPresence();
        }
    });
    window.addEventListener('beforeunload', () => {
        if (!composerActive) {
            return;
        }
        navigator.sendBeacon?.('/api/agent-tasks/composer', new Blob([JSON.stringify({ active: false })], { type: 'application/json' }));
    });
    document.addEventListener('visibilitychange', () => {
        if (document.hidden) {
            updateComposerPresence(false).catch(() => {});
            Array.from(taskEventStreams.keys()).forEach(closeTaskEventStream);
        }
    });
    scheduleComposerHeartbeat();
    let preferredAgentMode = !CONFIG.classOfferingId;
    try {
        const saved = window.localStorage.getItem('lanshare.aiWorkspace.agentMode');
        if (saved === '1') preferredAgentMode = true;
        if (saved === '0') preferredAgentMode = false;
    } catch {
        // Ignore storage restrictions.
    }
    setAgentMode(preferredAgentMode, { persist: false, showRuntimeWarning: false });
    if (!preferredAgentMode) {
        loadBootstrap({ showRuntimeWarning: false }).then(() => refreshTasks({ silent: true })).catch((error) => notify(error.message || 'Agent 加载失败', 'error'));
    }
    startTaskPolling();
    startTaskEventPolling();
}

function initChatComponent() {
    if (typeof window.AIChatComponent !== 'function') {
        return false;
    }
    try {
        chatComponent = new window.AIChatComponent({
            classOfferingId: CONFIG.classOfferingId,
            contextOnly: !CONFIG.classOfferingId,
            getContextPromptExtra: () => formatContextForPrompt(collectPageContext()),
        });
        chatComponent.init();
        window.aiChat = chatComponent;
        return true;
    } catch (error) {
        console.error('Failed to init AI workspace chat', error);
        return false;
    }
}

function initFallbackShell() {
    const fab = $('#ai-chat-fab');
    const modal = $('#ai-chat-modal');
    const container = $('.ai-chat-container', modal || document);
    if (!fab || !modal || !container) {
        return;
    }
    const open = () => {
        openWorkspaceModal();
    };
    const close = () => {
        modal.style.display = 'none';
        modal.setAttribute('aria-hidden', 'true');
        fab.style.display = 'flex';
        container.classList.remove('fullscreen');
        document.body.classList.remove('ai-chat-fullscreen-active');
    };
    fab.addEventListener('click', open);
    $('#ai-chat-btn-close')?.addEventListener('click', close);
    $('#ai-chat-btn-fullscreen')?.addEventListener('click', () => {
        const button = $('#ai-chat-btn-fullscreen');
        const isFullscreen = container.classList.toggle('fullscreen');
        document.body.classList.toggle('ai-chat-fullscreen-active', isFullscreen);
        if (isFullscreen) {
            // 清掉浮窗模式留下的内联几何样式，否则全屏布局会被覆盖。
            ['width', 'height', 'top', 'bottom', 'left', 'right'].forEach((prop) => {
                container.style[prop] = '';
            });
        } else {
            window.setTimeout(ensureWorkspaceWindowVisible, 0);
        }
        if (button) {
            button.title = isFullscreen ? '退出全屏' : '全屏';
            button.setAttribute('aria-label', button.title);
            button.setAttribute('aria-pressed', isFullscreen ? 'true' : 'false');
        }
    });
    if (!CONFIG.classOfferingId) {
        $('#ai-chat-textarea')?.setAttribute('placeholder', CONFIG.taskCenterEnabled ? '描述要让 Agent 执行的平台任务...' : '当前页面未绑定具体课堂。');
        ['#ai-chat-btn-send', '#ai-chat-btn-attach', '#ai-deep-think-btn'].forEach((selector) => {
            const button = $(selector);
            if (button) {
                button.disabled = !(CONFIG.taskCenterEnabled && selector === '#ai-chat-btn-send');
            }
        });
    }
}

function initOpenContextHooks() {
    $('#ai-chat-fab')?.addEventListener('click', () => {
        refreshContextPreview();
        window.setTimeout(ensureWorkspaceWindowVisible, 0);
        window.dispatchEvent(new CustomEvent('ai-workspace:opened', { detail: collectPageContext() }));
    }, { capture: true });
    window.addEventListener('ai-workspace:opened', refreshContextPreview);
    window.addEventListener('ai-workspace:opened', () => window.setTimeout(ensureWorkspaceWindowVisible, 0));
}

let aiWorkspaceWidgetInitialized = false;

function initScopedModelessKeyboard() {
    if (!document.body.matches('.dw-page, .classroom-workspace-v2')) return;
    const modal = $('#ai-chat-modal');
    const container = $('.ai-chat-container', modal || document);
    const fab = $('#ai-chat-fab');
    const close = $('#ai-chat-btn-close');
    if (!modal || !container || !fab || !close) return;

    // This floating workspace deliberately permits work on the page behind it.
    container.setAttribute('aria-modal', 'false');
    modal.style.pointerEvents = 'none';
    modal.style.background = 'transparent';
    // Keep the workspace below these pages' real modal overlays (5090+).
    modal.style.zIndex = '5000';
    container.style.pointerEvents = 'auto';
    let returnTarget = fab;
    window.addEventListener('ai-workspace:opened', () => {
        const active = document.activeElement;
        if (active instanceof HTMLElement && active !== document.body && !modal.contains(active)) {
            returnTarget = active;
        }
    });
    close.addEventListener('click', () => {
        if (modal.getAttribute('aria-hidden') !== 'true') return;
        const target = returnTarget.isConnected && returnTarget.getClientRects().length ? returnTarget : fab;
        target.focus({ preventScroll: true });
    });
    modal.addEventListener('keydown', (event) => {
        if (event.key !== 'Escape' || event.defaultPrevented ||
            event.target.closest('[role="dialog"]') !== container) return;
        // A real modal opened above this tool owns Escape until it is dismissed.
        const blockingDialog = [...document.querySelectorAll('[role="dialog"][aria-modal="true"], dialog[open]')]
            .some((dialog) => dialog !== container && dialog.getClientRects().length &&
                getComputedStyle(dialog).visibility !== 'hidden');
        if (blockingDialog) return;
        event.preventDefault();
        event.stopPropagation();
        close.click();
    });
}

function initAIWorkspaceWidget() {
    if (aiWorkspaceWidgetInitialized) {
        return;
    }
    aiWorkspaceWidgetInitialized = true;
    window.buildAIWorkspacePageContext = collectPageContext;
    window.formatAIWorkspaceContextForPrompt = formatContextForPrompt;

    const chatReady = initChatComponent();
    if (!chatReady) {
        initFallbackShell();
    }
    initScopedModelessKeyboard();
    initOpenContextHooks();
    const deferredLauncher = $('#ai-chat-fab[data-ai-deferred]');
    if (deferredLauncher) {
        deferredLauncher.disabled = false;
        deferredLauncher.removeAttribute('aria-busy');
        deferredLauncher.removeAttribute('data-ai-deferred');
    }
    bindTaskCenter();
    refreshContextPreview();
    handleAgentSubscriptionDeepLink().catch((error) => notify(error.message || 'Agent 定时任务链接打开失败', 'error'));
    handleAgentTaskDeepLink().catch((error) => notify(error.message || 'Agent 任务链接打开失败', 'error'));
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initAIWorkspaceWidget, { once: true });
} else {
    initAIWorkspaceWidget();
}
