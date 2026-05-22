const CONFIG = window.AI_WORKSPACE_WIDGET_CONFIG || {};
const TASK_REFRESH_MS = 6000;

let chatComponent = null;
let taskBootstrapLoaded = false;
let taskPollTimer = null;
let selectedTaskId = null;
let lastTaskPayload = { tasks: [], counts: {} };
let workflowCatalog = [];

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
    const title = $('#agent-task-context-title');
    if (!title) {
        return;
    }
    const context = collectPageContext();
    const pieces = [
        context.materialContext?.materialName,
        context.assignmentContext?.title,
        context.classroomContext?.courseName,
        context.manageContext?.pageTitle,
        context.page?.title,
    ].filter(Boolean);
    title.textContent = clampText(pieces[0] || '当前页面', 90);
}

function workflowHintForTaskType(taskType) {
    const map = {
        course_material_digest: ['material_operations'],
        lesson_document: ['lesson_document_generation'],
        assignment_blueprint: ['assignment_exam_workflow'],
        blog_draft: ['blog_and_reflection'],
        student_notification: ['student_support', 'submission_grading_feedback'],
        general_teaching_task: ['classroom_preparation', 'submission_grading_feedback'],
    };
    const keys = map[taskType] || [];
    const matched = workflowCatalog.find((item) => keys.includes(item.key)) || workflowCatalog[0];
    if (!matched) {
        return '';
    }
    return `${matched.agent_capability || ''} ${matched.guardrail ? `安全边界：${matched.guardrail}` : ''}`.trim();
}

function updateTaskTypeHint() {
    const select = $('#agent-task-type');
    const hint = $('#agent-task-type-hint');
    if (!select || !hint) {
        return;
    }
    hint.textContent = workflowHintForTaskType(select.value);
}

async function apiJson(url, options = {}) {
    const response = await fetch(url, {
        credentials: 'same-origin',
        headers: {
            Accept: 'application/json',
            ...(options.body ? { 'Content-Type': 'application/json' } : {}),
            ...(options.headers || {}),
        },
        ...options,
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

function setCounts(counts = {}) {
    $all('[data-agent-count]').forEach((node) => {
        const key = node.dataset.agentCount;
        node.textContent = String(counts[key] ?? 0);
    });
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
            ${markdown ? `<div class="ai-task-business-markdown">${escapeHtml(markdown)}</div>` : ''}
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
    const artifacts = Array.isArray(detail.artifacts) ? detail.artifacts.slice(0, 6) : [];
    const toolCalls = Array.isArray(detail.tool_calls) ? detail.tool_calls.slice(-6) : [];
    if (!textOutputs.length && !artifacts.length && !toolCalls.length) {
        return '';
    }
    return `
        <div class="ai-task-detail__block is-runtime-detail">
            <h4>运行时细节</h4>
            ${textOutputs.length ? `
                <div class="ai-task-runtime-section">
                    <strong>关键输出</strong>
                    ${textOutputs.map((item) => `<p>${escapeHtml(item.text || item)}</p>`).join('')}
                </div>
            ` : ''}
            ${artifacts.length ? `
                <div class="ai-task-runtime-section">
                    <strong>产物</strong>
                    <ul>${artifacts.map((item) => `<li>${escapeHtml(item.path || item.name || item.id || JSON.stringify(item))}</li>`).join('')}</ul>
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
        return `
            <button type="button" class="ai-task-item ${statusClass(task.status)} ${isSelected ? 'is-selected' : ''}" data-agent-task-id="${escapeHtml(task.id)}">
                <span class="ai-task-item__order">${index + 1}</span>
                <span class="ai-task-item__body">
                    <strong>${escapeHtml(task.title || task.public_summary || '教学任务')}</strong>
                    <small>${ownerLabel} · ${escapeHtml(task.status_label || task.status)}${runningText}${queueText}</small>
                </span>
            </button>
        `;
    }).join('');
}

function renderTaskDetail(task) {
    const detail = $('#agent-task-detail');
    if (!detail) {
        return;
    }
    if (!task) {
        detail.innerHTML = '<div class="ai-task-detail__empty">选择一个任务查看状态。自己的任务会显示详情和执行记录。</div>';
        return;
    }
    const runtime = task.runtime_status ? `<span>运行时：${escapeHtml(task.runtime_status)}</span>` : '';
    const elapsed = task.elapsed_seconds ? `<span>已运行：${formatElapsed(task.elapsed_seconds)}</span>` : '';
    const cancelButton = task.is_owner && task.is_active
        ? `<button type="button" class="btn btn-outline btn-sm" data-agent-cancel="${escapeHtml(task.id)}">取消任务</button>`
        : '';
    const detailPayload = task.result_detail || {};
    const ownerBody = task.is_owner ? `
        <div class="ai-task-detail__block">
            <h4>任务要求</h4>
            <p>${escapeHtml(task.private_instruction || '无')}</p>
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
        ${renderRuntimeDetail(detailPayload)}
        <div class="ai-task-events">
            ${(task.events || []).map((event) => `
                <div class="ai-task-event">
                    <span>${escapeHtml(event.created_at || '')}</span>
                    <strong>${escapeHtml(event.message || event.event_type || '')}</strong>
                    ${renderEventDetail(event)}
                </div>
            `).join('') || '<div class="ai-task-detail__empty">暂无执行记录。</div>'}
        </div>
    ` : `
        <div class="ai-task-detail__block">
            <h4>隐私保护</h4>
            <p>这是其他老师的任务。这里只显示公开队列状态，不展示任务细节、上下文或结果。</p>
        </div>
    `;
    detail.innerHTML = `
        <header class="ai-task-detail__header">
            <div>
                <span class="ai-task-status ${statusClass(task.status)}">${escapeHtml(task.status_label || task.status)}</span>
                <h3>${escapeHtml(task.title || task.public_summary || '教学任务')}</h3>
                <div class="ai-task-detail__meta">
                    <span>${escapeHtml(task.teacher_name || '')}</span>
                    <span>${escapeHtml(task.task_type_label || '')}</span>
                    ${elapsed}
                    ${runtime}
                </div>
            </div>
            ${cancelButton}
        </header>
        ${ownerBody}
    `;
}

function focusTaskDetailIfCompact() {
    const detail = $('#agent-task-detail');
    if (!detail || !window.matchMedia('(max-width: 840px)').matches) {
        return;
    }
    const scroller = detail.closest('.ai-task-center');
    if (scroller) {
        const detailTop = detail.getBoundingClientRect().top - scroller.getBoundingClientRect().top + scroller.scrollTop - 8;
        scroller.scrollTo({ top: Math.max(0, detailTop), behavior: 'auto' });
        return;
    }
    detail.scrollIntoView({ block: 'start', inline: 'nearest' });
}

async function loadTaskDetail(taskId) {
    const data = await apiJson(`/api/agent-tasks/${taskId}`);
    renderTaskDetail(data.task);
    focusTaskDetailIfCompact();
}

async function refreshTasks({ silent = false } = {}) {
    if (!CONFIG.taskCenterEnabled) {
        return;
    }
    try {
        const data = await apiJson('/api/agent-tasks');
        lastTaskPayload = data;
        setCounts(data.counts || {});
        renderTaskList(data.tasks || []);
        if (selectedTaskId) {
            const selected = (data.tasks || []).find((task) => Number(task.id) === Number(selectedTaskId));
            if (selected?.is_owner) {
                await loadTaskDetail(selectedTaskId);
            } else {
                renderTaskDetail(selected || null);
            }
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
    const data = await apiJson('/api/agent-tasks/bootstrap');
    taskBootstrapLoaded = true;
    workflowCatalog = Array.isArray(data.workflow_catalog) ? data.workflow_catalog : [];
    const select = $('#agent-task-type');
    if (select) {
        select.innerHTML = (data.task_types || []).map((item) => (
            `<option value="${escapeHtml(item.value)}" data-placeholder="${escapeHtml(item.placeholder || '')}">${escapeHtml(item.label || item.value)}</option>`
        )).join('');
        const updatePlaceholder = () => {
            const option = select.options[select.selectedIndex];
            const textarea = $('#agent-task-instruction');
            if (textarea && option?.dataset.placeholder) {
                textarea.placeholder = option.dataset.placeholder;
            }
            updateTaskTypeHint();
        };
        select.addEventListener('change', updatePlaceholder);
        updatePlaceholder();
    }
    setCounts(data.counts || {});
    renderTaskList(data.tasks || []);
    if (!data.runtime_configured) {
        const status = $('#agent-task-form-status');
        if (status) {
            status.textContent = '运行时未配置，任务会等待独立 Agent 服务启动。';
        }
    }
}

function startTaskPolling() {
    if (!CONFIG.taskCenterEnabled || taskPollTimer) {
        return;
    }
    taskPollTimer = window.setInterval(() => {
        const modal = $('#ai-chat-modal');
        const taskPanelVisible = !$('[data-ai-workspace-panel="tasks"]')?.hidden;
        if (modal?.style.display === 'block' && taskPanelVisible) {
            refreshTasks({ silent: true });
        }
    }, TASK_REFRESH_MS);
}

function switchTab(tabName) {
    if (!CONFIG.taskCenterEnabled && tabName === 'tasks') {
        return;
    }
    $all('[data-ai-workspace-tab]').forEach((tab) => {
        const active = tab.dataset.aiWorkspaceTab === tabName;
        tab.classList.toggle('is-active', active);
        tab.setAttribute('aria-selected', active ? 'true' : 'false');
    });
    $all('[data-ai-workspace-panel]').forEach((panel) => {
        const active = panel.dataset.aiWorkspacePanel === tabName;
        panel.hidden = !active;
        panel.classList.toggle('is-active', active);
    });
    document.body.dataset.aiWorkspaceActiveTab = tabName;
    try {
        window.localStorage.setItem('lanshare.aiWorkspace.tab', tabName);
    } catch {
        // Ignore storage restrictions.
    }
    if (tabName === 'tasks') {
        refreshContextPreview();
        loadBootstrap()
            .then(() => refreshTasks({ silent: true }))
            .catch((error) => notify(error.message || '任务中心加载失败', 'error'));
        startTaskPolling();
    }
}

function bindTaskCenter() {
    if (!CONFIG.taskCenterEnabled) {
        return;
    }
    $all('[data-ai-workspace-tab]').forEach((tab) => {
        tab.addEventListener('click', () => switchTab(tab.dataset.aiWorkspaceTab));
    });
    $('#agent-task-list')?.addEventListener('click', async (event) => {
        const button = event.target.closest('[data-agent-task-id]');
        if (!button) {
            return;
        }
        selectedTaskId = Number(button.dataset.agentTaskId);
        renderTaskList(lastTaskPayload.tasks || []);
        try {
            await loadTaskDetail(selectedTaskId);
        } catch (error) {
            notify(error.message || '任务详情加载失败', 'error');
        }
    });
    $('#agent-task-detail')?.addEventListener('click', async (event) => {
        const button = event.target.closest('[data-agent-cancel]');
        if (!button) {
            return;
        }
        button.disabled = true;
        try {
            const data = await apiJson(`/api/agent-tasks/${button.dataset.agentCancel}/cancel`, { method: 'POST' });
            renderTaskDetail(data.task);
            await refreshTasks({ silent: true });
            notify('已提交取消请求', 'success');
        } catch (error) {
            notify(error.message || '取消失败', 'error');
        } finally {
            button.disabled = false;
        }
    });
    $('#agent-task-form')?.addEventListener('submit', async (event) => {
        event.preventDefault();
        const form = event.currentTarget;
        const submit = form.querySelector('button[type="submit"]');
        const status = $('#agent-task-form-status');
        const instruction = $('#agent-task-instruction')?.value.trim() || '';
        if (instruction.length < 6) {
            notify('请补充更明确的任务要求', 'warning');
            return;
        }
        if (submit) {
            submit.disabled = true;
        }
        if (status) {
            status.textContent = '正在加入队列...';
        }
        try {
            const payload = {
                task_type: $('#agent-task-type')?.value || 'general_teaching_task',
                title: $('#agent-task-title')?.value.trim() || '',
                instruction,
                page_context: collectPageContext(),
            };
            const data = await apiJson('/api/agent-tasks', {
                method: 'POST',
                body: JSON.stringify(payload),
            });
            selectedTaskId = data.task?.id || null;
            $('#agent-task-instruction').value = '';
            $('#agent-task-title').value = '';
            renderTaskDetail(data.task);
            focusTaskDetailIfCompact();
            await refreshTasks({ silent: true });
            notify('任务已加入全平台队列', 'success');
            if (status) {
                status.textContent = '已进入队列';
            }
        } catch (error) {
            if (status) {
                status.textContent = error.message || '提交失败';
            }
            notify(error.message || '任务提交失败', 'error');
        } finally {
            if (submit) {
                submit.disabled = false;
            }
        }
    });
}

function initChatComponent() {
    if (!CONFIG.classOfferingId || typeof window.AIChatComponent !== 'function') {
        return false;
    }
    try {
        chatComponent = new window.AIChatComponent({
            classOfferingId: CONFIG.classOfferingId,
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
        refreshContextPreview();
        modal.style.display = 'block';
        modal.setAttribute('aria-hidden', 'false');
        fab.style.display = 'none';
    };
    const close = () => {
        modal.style.display = 'none';
        modal.setAttribute('aria-hidden', 'true');
        fab.style.display = 'flex';
    };
    fab.addEventListener('click', open);
    $('#ai-chat-btn-close')?.addEventListener('click', close);
    $('#ai-chat-btn-fullscreen')?.addEventListener('click', () => {
        container.classList.toggle('fullscreen');
    });
    if (!CONFIG.classOfferingId) {
        $('#ai-chat-textarea')?.setAttribute('placeholder', '当前页面未绑定具体课堂，可切换到任务中心提交教学任务。');
        ['#ai-chat-btn-send', '#ai-chat-btn-attach', '#ai-deep-think-btn'].forEach((selector) => {
            const button = $(selector);
            if (button) {
                button.disabled = true;
            }
        });
    }
}

function initOpenContextHooks() {
    $('#ai-chat-fab')?.addEventListener('click', () => {
        refreshContextPreview();
        window.dispatchEvent(new CustomEvent('ai-workspace:opened', { detail: collectPageContext() }));
    }, { capture: true });
    window.addEventListener('ai-workspace:opened', refreshContextPreview);
}

document.addEventListener('DOMContentLoaded', () => {
    window.buildAIWorkspacePageContext = collectPageContext;
    window.formatAIWorkspaceContextForPrompt = formatContextForPrompt;

    const chatReady = initChatComponent();
    if (!chatReady) {
        initFallbackShell();
    }
    initOpenContextHooks();
    bindTaskCenter();
    refreshContextPreview();

    if (CONFIG.taskCenterEnabled) {
        let preferredTab = CONFIG.classOfferingId ? 'chat' : 'tasks';
        try {
            preferredTab = window.localStorage.getItem('lanshare.aiWorkspace.tab') || preferredTab;
        } catch {
            // Ignore storage restrictions.
        }
        switchTab(preferredTab === 'tasks' ? 'tasks' : 'chat');
    }
});
