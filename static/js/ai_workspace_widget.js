const CONFIG = window.AI_WORKSPACE_WIDGET_CONFIG || {};
const TASK_REFRESH_MS = 5000;
const TASK_EVENT_POLL_MS = 2500;
const COMPOSER_HEARTBEAT_MS = 10000;
const AGENT_ATTACHMENT_MAX_FILES = 5;
const AGENT_ATTACHMENT_MAX_FILE_BYTES = 10 * 1024 * 1024;
const AGENT_ATTACHMENT_MAX_TOTAL_BYTES = 20 * 1024 * 1024;

let chatComponent = null;
let taskBootstrapLoaded = false;
let taskBootstrapPromise = null;
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
let taskEventPollTimer = null;
let taskEventPollBusy = false;
const agentTaskMessages = new Map();
const taskLastEventIds = new Map();
const taskEventStreams = new Map();
let taskEventStreamDisabled = false;
let agentSubscriptionPayload = { subscriptions: [], recent_tasks: [] };
let agentSubscriptionBusy = false;

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
        tooltip = `${running.teacher_name || '某位老师'}的${running.public_summary || running.task_type_label || 'Agent 任务'}正在运行（${runningLabel}）`;
        modebarStatus = queued > 0 ? `${runningLabel} · 排队 ${queued}` : runningLabel;
    } else if (state === 'yellow') {
        const composer = queueState.composer || {};
        if (queued > 0) {
            tooltip = `已有 ${queued} 个 Agent 任务在等待全平台队列`;
            modebarStatus = `排队 ${queued}`;
        } else {
            tooltip = `${composer.teacher_name || '某位老师'}正在编写新任务`;
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

function resultSummaryText(task) {
    if (task.result_summary) return task.result_summary;
    if (task.error_message) return task.error_message;
    if (task.status === 'completed') return '任务已结束，但运行时没有返回明确的业务结论。建议查看下方执行记录，确认是否产生了可用内容。';
    if (task.status === 'failed') return '任务失败，但未返回具体错误。建议稍后重试，或把任务要求描述得更具体。';
    return task.status_label || task.status || '处理中';
}

function safeLocalHref(value) {
    const text = String(value || '').trim();
    if (!text) return '';
    if (text.startsWith('/')) return text;
    return '';
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

function renderRuntimeDetail(detail = {}) {
    if (detail.platform_action) {
        return renderPlatformResult(detail);
    }
    const textOutputs = Array.isArray(detail.text_outputs) ? detail.text_outputs.slice(0, 4) : [];
    const recoveredArtifacts = Array.isArray(detail.recovered_artifacts) ? detail.recovered_artifacts.slice(0, 8) : [];
    const artifacts = Array.isArray(detail.artifacts) ? detail.artifacts.slice(0, 6) : [];
    const regularArtifacts = artifacts.filter((item) => !(item && item.recovered && item.download_url));
    const toolCalls = Array.isArray(detail.tool_calls) ? detail.tool_calls.slice(-6) : [];
    if (!textOutputs.length && !recoveredArtifacts.length && !regularArtifacts.length && !toolCalls.length) {
        return '';
    }
    return `
        <div class="ai-task-detail__block is-runtime-detail">
            <h4>运行时细节</h4>
            ${textOutputs.length ? `
                <div class="ai-task-runtime-section">
                    <strong>关键输出</strong>
                    ${textOutputs.map((item) => `<div class="ai-task-runtime-output md-content">${renderWorkspaceMarkdown(item.text || item)}</div>`).join('')}
                </div>
            ` : ''}
            ${recoveredArtifacts.length ? `
                <div class="ai-task-runtime-section is-recovered">
                    <strong>已挽救的中间产物</strong>
                    <ul>${recoveredArtifacts.map((item) => {
                        const href = safeLocalHref(item.download_url || '');
                        const label = item.path || item.name || '中间产物';
                        const meta = item.size ? ` · ${formatAgentFileSize(item.size)}` : '';
                        return `<li>${href ? `<a href="${escapeHtml(href)}" target="_blank" rel="noopener">${escapeHtml(label)}</a>` : escapeHtml(label)}<small>${escapeHtml(meta)}</small></li>`;
                    }).join('')}</ul>
                </div>
            ` : ''}
            ${regularArtifacts.length ? `
                <div class="ai-task-runtime-section">
                    <strong>产物</strong>
                    <ul>${regularArtifacts.map((item) => `<li>${escapeHtml(item.path || item.name || item.id || JSON.stringify(item))}</li>`).join('')}</ul>
                </div>
            ` : ''}
            ${toolCalls.length ? `
                <div class="ai-task-runtime-section">
                    <strong>工具调用</strong>
                    <ul>${toolCalls.map((item) => `<li>${escapeHtml(item.name || item.tool || item.type || JSON.stringify(item).slice(0, 160))}</li>`).join('')}</ul>
                </div>
            ` : ''}
        </div>
    `;
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
    return '';
}

function renderTaskEventHtml(event) {
    return `
        <div class="ai-task-event" data-agent-event-id="${escapeHtml(event.id || 0)}">
            <span>${escapeHtml(formatDateTime(event.created_at))}</span>
            <strong>${escapeHtml(event.message || event.event_type || '')}</strong>
            ${renderEventDetail(event)}
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
        if (executed) {
            const url = safeLocalHref(executed.url || '');
            return `
                <div class="ai-task-action is-done">
                    <span class="ai-task-action__done">✓ ${escapeHtml(executed.label || proposal.label || '已执行')}</span>
                    ${url ? `<a class="btn btn-outline btn-sm" href="${escapeHtml(url)}" target="_blank" rel="noopener">去查看</a>` : ''}
                </div>
            `;
        }
        if (proposal.execution_mode === 'manual_link') {
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
        return `
            <div class="ai-task-action" data-agent-action-block="${escapeHtml(task.id)}:${index}">
                <div class="ai-task-action__head">
                    <strong>${escapeHtml(proposal.label || '动作')}</strong>
                    <small>${escapeHtml(proposal.summary || '')}</small>
                </div>
                <button type="button" class="btn btn-primary btn-sm" data-agent-action-open="${escapeHtml(task.id)}" data-action-index="${index}">${escapeHtml(proposal.label || '执行')}</button>
                <div class="ai-task-action__confirm" hidden>
                    ${titleValue !== '' ? `
                        <label>标题<input type="text" data-agent-action-title value="${escapeHtml(titleValue)}" maxlength="120"></label>
                    ` : ''}
                    <p class="ai-task-action__note">确认后将以你的身份创建草稿（不会直接面向学生）。</p>
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
    if (!task.is_owner || (!task.is_terminal && !task.is_active)) {
        return '';
    }
    const isActive = Boolean(task.is_active && !task.is_terminal);
    const placeholder = isActive
        ? '补充说明会记录到当前任务；若运行时无法实时吸收，完成后可继续追问...'
        : '对这个结果继续提要求，Agent 会带着上文优先处理...';
    const buttonLabel = isActive ? '补充说明' : '追问';
    const retryButtons = (task.status === 'failed' || task.status === 'canceled') ? `
        <div class="ai-task-retry-row">
            <button type="button" class="btn btn-outline btn-sm" data-agent-retry="${escapeHtml(task.id)}">原样重试</button>
            <button type="button" class="btn btn-outline btn-sm" data-agent-retry-edit="${escapeHtml(task.id)}">修改后重试</button>
        </div>
    ` : '';
    return `
        ${retryButtons}
        <div class="ai-task-followup">
            <textarea data-agent-followup-input="${escapeHtml(task.id)}" rows="1" maxlength="4000" placeholder="${escapeHtml(placeholder)}"></textarea>
            <button type="button" class="btn btn-primary btn-sm" data-agent-followup="${escapeHtml(task.id)}">${buttonLabel}</button>
        </div>
    `;
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
        const ownerLabel = task.is_owner ? '我的任务' : `${escapeHtml(task.teacher_name || '某位老师')}`;
        const runningText = task.status === 'running' ? ` · 已运行 ${formatElapsed(task.elapsed_seconds)}` : '';
        const queueText = task.status === 'queued' && task.queue_position ? ` · 队列第 ${task.queue_position}` : '';
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
                        <small>${ownerLabel} · ${escapeHtml(task.status_label || task.status)}${runningText}${queueText}</small>
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
    list.innerHTML = `
        <div class="ai-agent-subscription-rows">
            ${subscriptions.map((item) => `
                <label class="ai-agent-subscription-row ${item.enabled ? 'is-enabled' : ''}">
                    <input type="checkbox" data-agent-sub-toggle="${escapeHtml(item.key)}" ${item.enabled ? 'checked' : ''}>
                    <span class="ai-agent-subscription-row__copy">
                        <strong>${escapeHtml(item.label || item.key)}</strong>
                        <small>${escapeHtml(item.enabled ? `下次 ${item.next_run_at || formatAgentSubscriptionHour(item.hour)}` : item.description || '')}</small>
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

function buildAgentTaskDetailHtml(task) {
    if (!task) {
        return '<div class="ai-task-detail__empty">选择一个任务查看状态。自己的任务会显示详情和执行记录。</div>';
    }
    const runtime = task.runtime_status ? `<span>运行时：${escapeHtml(task.runtime_status)}</span>` : '';
    const elapsed = task.elapsed_seconds ? `<span>已运行：${formatElapsed(task.elapsed_seconds)}</span>` : '';
    const cancelButton = task.is_owner && task.is_active
        ? `<button type="button" class="btn btn-outline btn-sm" data-agent-cancel="${escapeHtml(task.id)}">取消任务</button>`
        : '';
    const deleteButton = task.is_owner && task.is_terminal
        ? `<button type="button" class="btn btn-outline btn-sm ai-task-delete-btn" data-agent-delete="${escapeHtml(task.id)}">删除记录</button>`
        : '';
    const detailPayload = task.result_detail || {};
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
        <div class="ai-task-detail__block">
            <h4>任务要求</h4>
            <p>${escapeHtml(task.private_instruction || '无')}</p>
            ${attachments.length ? `<small class="ai-task-attachment-list">附件：${attachments.map((item) => escapeHtml(item.name || '')).join('、')}</small>` : ''}
        </div>
        ${task.is_terminal ? `
        <div class="ai-task-detail__block ${terminalTone(task.status)}">
            <h4>${terminalTitle(task.status)}</h4>
            <p>${escapeHtml(resultSummaryText(task))}</p>
        </div>` : ''}
        ${task.error_message ? `
        <div class="ai-task-detail__block is-error">
            <h4>异常信息</h4>
            <p>${escapeHtml(task.error_message)}</p>
        </div>` : ''}
        ${renderProposedActions(task)}
        ${renderRuntimeDetail(detailPayload)}
        <div class="ai-task-events" data-agent-events="${escapeHtml(task.id)}">
            ${(task.events || []).map(renderTaskEventHtml).join('') || '<div class="ai-task-detail__empty">暂无执行记录。</div>'}
        </div>
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
                    <span class="ai-task-status ${statusClass(task.status)}">${escapeHtml(task.status_label || task.status)}</span>
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
    bubble.innerHTML = `<article class="ai-agent-task-card">${buildAgentTaskDetailHtml(task)}</article>`;
    if (!bubble.parentNode) {
        msgDiv.appendChild(bubble);
    }
    const events = Array.isArray(task.events) ? task.events : [];
    const lastEventId = events.reduce((maxId, event) => Math.max(maxId, Number(event.id || 0)), 0);
    if (lastEventId > 0) {
        taskLastEventIds.set(Number(task.id), lastEventId);
    }
    // 轮询刷新只做粘性跟随（force=false），不打断用户向上浏览输出内容。
    currentChatSurface().scrollToBottom(autoScroll || isNew);
}

function appendTaskEventsToCard(taskId, events = []) {
    const id = Number(taskId || 0);
    const container = $(`[data-agent-events="${id}"]`);
    if (!id || !container || !Array.isArray(events) || !events.length) {
        return;
    }
    const empty = container.querySelector('.ai-task-detail__empty');
    if (empty) {
        empty.remove();
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
    container.insertAdjacentHTML('beforeend', freshEvents.map(renderTaskEventHtml).join(''));
    const lastId = freshEvents.reduce((maxId, event) => Math.max(maxId, Number(event.id || 0)), taskLastEventIds.get(id) || 0);
    taskLastEventIds.set(id, lastId);
}

async function refreshTerminalTaskCard(taskId) {
    if (Number(selectedTaskId) === Number(taskId)) {
        await loadTaskDetail(taskId);
        await refreshTasks({ silent: true });
    }
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
    if (payload.is_terminal) {
        closeTaskEventStream(id);
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
            appendTaskEventsToCard(taskId, data.events || []);
            if (data.last_event_id) {
                taskLastEventIds.set(Number(taskId), Number(data.last_event_id));
            }
            if (data.is_terminal && (data.events || []).length && Number(selectedTaskId) === Number(taskId)) {
                await refreshTerminalTaskCard(taskId);
            }
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
    const data = await apiJson(`/api/agent-tasks/${taskId}`);
    renderTaskDetail(data.task, { autoScroll });
    return data.task;
}

function removeAgentTaskMessage(taskId) {
    const id = Number(taskId || 0);
    const node = agentTaskMessages.get(id);
    if (node?.isConnected) {
        node.remove();
    }
    agentTaskMessages.delete(id);
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
    const data = await apiJson(`/api/agent-tasks/${taskId}`);
    const proposal = (((data.task || {}).result_detail || {}).proposed_actions || [])[actionIndex] || {};
    const params = proposal.params || {};
    const copyText = params.content_md || params.body_md || params.requirements_md || proposal.summary || '';
    const copied = await copyTextToClipboard(copyText).catch(() => false);
    notify(copied ? '已复制草稿内容，正在打开消息中心。' : '正在打开消息中心，请手动复制草稿内容。', copied ? 'success' : 'warning');
    window.open('/messages', '_blank', 'noopener');
}

async function executeAgentAction(button) {
    const taskId = Number(button.dataset.agentActionConfirm || 0);
    const actionIndex = Number(button.dataset.actionIndex || 0);
    const block = button.closest('[data-agent-action-block]');
    const params = {};
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

async function continueAgentTask(button) {
    const taskId = Number(button.dataset.agentFollowup || 0);
    const card = button.closest('.ai-agent-task-card') || document;
    const input = card.querySelector(`[data-agent-followup-input="${taskId}"]`);
    const instruction = input?.value.trim() || '';
    if (instruction.length < 2) {
        notify('请补充要继续提的要求。', 'warning');
        return;
    }
    button.disabled = true;
    try {
        const data = await apiJson(`/api/agent-tasks/${taskId}/follow-up`, {
            method: 'POST',
            body: JSON.stringify({ instruction }),
        });
        if (input) {
            input.value = '';
        }
        if (data.supplemented) {
            renderTaskDetail(data.task, { autoScroll: true });
            notify('补充说明已记录。', 'success');
        } else {
            selectedTaskId = data.task?.id || selectedTaskId;
            renderTaskDetail(data.task, { autoScroll: true });
            notify('追问任务已加入队列。', 'success');
        }
        await refreshTasks({ silent: true });
    } finally {
        button.disabled = false;
    }
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
        }
    } catch (error) {
        if (!silent) {
            notify(error.message || '任务中心加载失败', 'error');
        }
    }
}

async function loadBootstrap() {
    if (!CONFIG.taskCenterEnabled || taskBootstrapLoaded) {
        return;
    }
    if (taskBootstrapPromise) {
        return taskBootstrapPromise;
    }
    taskBootstrapPromise = (async () => {
        const data = await apiJson('/api/agent-tasks/bootstrap');
        taskBootstrapLoaded = true;
        workflowCatalog = Array.isArray(data.workflow_catalog) ? data.workflow_catalog : [];
        taskTypesCatalog = Array.isArray(data.task_types) ? data.task_types : [];
        setQueueState(data.queue_state || {}, data.counts || {});
        renderTaskList(data.tasks || []);
        loadAgentSubscriptions({ silent: true });
        if (!data.runtime_configured && !runtimeWarningShown) {
            runtimeWarningShown = true;
            notify('Agent 运行时未配置，任务会先进入队列等待独立服务。', 'warning');
        }
    })();
    try {
        await taskBootstrapPromise;
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

function setAgentMode(enabled, { persist = true } = {}) {
    if (!CONFIG.taskCenterEnabled) {
        return;
    }
    agentMode = Boolean(enabled);
    const surface = currentChatSurface();
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
    if (surface.textarea) {
        surface.textarea.placeholder = agentMode
            ? '描述要让 Agent 执行的教学业务任务...'
            : '把当前页面作为上下文提问...';
    }
    if (surface.attachBtn) {
        surface.attachBtn.disabled = false;
        surface.attachBtn.title = agentMode ? '上传附件给 Agent 任务' : '上传附件';
        surface.attachBtn.setAttribute('aria-label', surface.attachBtn.title);
    }
    if (surface.deepThinkBtn) {
        surface.deepThinkBtn.disabled = false;
    }
    if (surface.sendBtn) {
        if (agentMode) {
            surface.sendBtn.disabled = Boolean(agentSubmitting);
        } else if (chatComponent?.updateSendButtonState) {
            chatComponent.updateSendButtonState();
        } else {
            surface.sendBtn.disabled = !surface.textarea?.value.trim();
        }
        surface.sendBtn.title = agentMode ? '加入 Agent 队列' : '发送';
        surface.sendBtn.setAttribute('aria-label', agentMode ? '加入 Agent 队列' : '发送');
    }
    refreshContextPreview();
    setQueueState(lastTaskPayload.queue_state || {}, lastTaskPayload.counts || {});
    if (persist) {
        try {
            window.localStorage.setItem('lanshare.aiWorkspace.agentMode', agentMode ? '1' : '0');
        } catch {
            // Ignore storage restrictions.
        }
    }
    if (agentMode) {
        loadBootstrap().then(() => refreshTasks({ silent: true })).catch((error) => notify(error.message || 'Agent 加载失败', 'error'));
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
    setAgentMode(true);
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

function validateAgentAttachments(files = []) {
    const normalized = Array.from(files || []);
    if (normalized.length > AGENT_ATTACHMENT_MAX_FILES) {
        return `单个 Agent 任务最多携带 ${AGENT_ATTACHMENT_MAX_FILES} 个附件。`;
    }
    let total = 0;
    for (const file of normalized) {
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

async function submitAgentTaskFromChat() {
    if (!CONFIG.taskCenterEnabled || agentSubmitting) {
        return;
    }
    const surface = currentChatSurface();
    const textarea = surface.textarea;
    const instruction = textarea?.value.trim() || '';
    const pendingFiles = Array.from(chatComponent?.pendingFiles || []);
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
    const taskType = inferAgentTaskType(instruction, context);
    surface.renderMessage('user', instruction, agentAttachmentPreviews(pendingFiles));
    if (textarea) {
        textarea.value = '';
        resetTextareaHeight(textarea);
    }
    await updateComposerPresence(false);
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
        chatComponent?.clearPendingFiles?.();
        selectedTaskId = data.task?.id || null;
        if (data.task) {
            renderAgentTaskMessage(data.task, { autoScroll: true });
        }
        const noHistoryInput = $('#ai-agent-no-history');
        if (noHistoryInput) {
            noHistoryInput.checked = false;
        }
        await refreshTasks({ silent: true });
        notify('Agent 任务已加入全平台队列。', 'success');
    } catch (error) {
        surface.renderMessage('assistant', `Agent 任务提交失败：${error.message || '未知错误'}`);
        notify(error.message || 'Agent 任务提交失败', 'error');
    } finally {
        agentSubmitting = false;
        if (surface.sendBtn) {
            surface.sendBtn.disabled = false;
        }
        textarea?.focus();
    }
}

function bindTaskCenter() {
    if (!CONFIG.taskCenterEnabled) {
        return;
    }
    window.addEventListener('lanshare:agent-handoff', (event) => {
        prefillAgentTaskFromChat(event.detail?.instruction || '');
    });
    $('#ai-agent-mode-toggle')?.addEventListener('click', () => setAgentMode(!agentMode));
    $all('[data-ai-mode-select]').forEach((button) => {
        button.addEventListener('click', () => {
            setAgentMode(button.dataset.aiModeSelect === 'agent');
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
    $('#ai-chat-messages-box')?.addEventListener('click', async (event) => {
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
        const followUpButton = event.target.closest('[data-agent-followup]');
        if (followUpButton) {
            try {
                await continueAgentTask(followUpButton);
            } catch (error) {
                notify(error.message || '提交失败', 'error');
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
    $('#ai-chat-messages-box')?.addEventListener('keydown', (event) => {
        const input = event.target.closest('[data-agent-followup-input]');
        if (!input || event.key !== 'Enter' || event.shiftKey) {
            return;
        }
        event.preventDefault();
        const button = input.closest('.ai-task-followup')?.querySelector('[data-agent-followup]');
        button?.click();
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
    setAgentMode(preferredAgentMode, { persist: false });
    if (!preferredAgentMode) {
        loadBootstrap().then(() => refreshTasks({ silent: true })).catch((error) => notify(error.message || 'Agent 加载失败', 'error'));
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
        $('#ai-chat-textarea')?.setAttribute('placeholder', CONFIG.taskCenterEnabled ? '描述要让 Agent 执行的教学业务任务...' : '当前页面未绑定具体课堂。');
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
    initOpenContextHooks();
    bindTaskCenter();
    refreshContextPreview();
    handleAgentTaskDeepLink().catch((error) => notify(error.message || 'Agent 任务链接打开失败', 'error'));
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initAIWorkspaceWidget, { once: true });
} else {
    initAIWorkspaceWidget();
}
