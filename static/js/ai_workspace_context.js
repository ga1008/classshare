// Page context for the AI window: what the user is looking at, shared by the
// chat (as a prompt prefix) and the Agent (as a task hint). Pure reads only;
// the server re-verifies every resource with the user's live permissions.

const CONFIG = window.AI_WORKSPACE_WIDGET_CONFIG || {};

const $ = (selector, root = document) => root.querySelector(selector);
const $all = (selector, root = document) => Array.from(root.querySelectorAll(selector));

export function clampText(value, maxLength) {
    const text = String(value ?? '').replace(/\s+/g, ' ').trim();
    return text.length > maxLength ? `${text.slice(0, maxLength).trim()}...` : text;
}

function getSelectedText() {
    const selected = String(window.getSelection?.() || '').trim();
    return selected ? clampText(selected, 800) : '';
}

function visibleHeadingTexts() {
    return $all('h1, h2, h3')
        .filter((item) => item.offsetParent !== null && !item.closest('#ai-chat-modal'))
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
    if (!session || typeof session !== 'object') return {};
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
    if (!Object.keys(appConfig).length) return {};
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
    if (!Object.keys(material).length && !Object.keys(materialContext).length) return {};
    return {
        materialId: materialContext.materialId || material.id || CONFIG.materialId || null,
        materialName: materialContext.materialName || material.name || '',
        materialPath: material.material_path || '',
        classOfferingId: materialContext.classOfferingId || CONFIG.classOfferingId || null,
        sessionId: materialContext.sessionId || null,
        headings: $all('#viewer-toc button, #viewer-content h1, #viewer-content h2, #viewer-content h3')
            .map((item) => clampText(item.textContent, 90)).filter(Boolean).slice(0, 16),
        aiSummary: clampText(material.ai_parse_result?.summary || '', 1000),
    };
}

function collectAssignmentContext() {
    if (!CONFIG.assignmentId) return {};
    return {
        assignmentId: CONFIG.assignmentId || null,
        classOfferingId: CONFIG.classOfferingId || null,
        title: clampText($('[data-assignment-title], .assignment-title, h1')?.textContent || '', 140),
        status: clampText($('.status-badge, [data-assignment-status]')?.textContent || '', 80),
        visibleStats: $all('.stat-card, .assignment-stat, [data-submission-stat]')
            .map((item) => clampText(item.textContent, 120)).filter(Boolean).slice(0, 10),
    };
}

function collectManageContext() {
    if (!$('.manage-main, .manage-content')) return {};
    return {
        pageTitle: clampText($('.manage-topbar-page strong, .manage-header-title, h1')?.textContent || document.title, 120),
        activePage: clampText($('.manage-nav-item.active, .manage-topbar-page strong')?.textContent || '', 120),
        visibleSections: visibleHeadingTexts(),
    };
}

function collectDashboardContext() {
    if (!$('[data-dashboard-root], .dashboard-grid, .dashboard-main')) return {};
    return {
        pageTitle: clampText(document.title, 120),
        activeCourseCards: $all('[data-classroom-card], .classroom-card, .course-card')
            .map((item) => clampText(item.textContent, 140)).filter(Boolean).slice(0, 8),
    };
}

export function collectPageContext(extra = {}) {
    const session = collectSelectedSessionContext();
    const context = {
        page: {
            title: clampText(document.title, 140),
            path: window.location.pathname,
            search: window.location.search,
            headings: visibleHeadingTexts(),
            activeArea: activeNavText(),
            selectedText: getSelectedText(),
        },
        user: { role: CONFIG.userRole || '', name: CONFIG.userName || '' },
        classOfferingId: CONFIG.classOfferingId || null,
        assignmentId: CONFIG.assignmentId || null,
        materialId: CONFIG.materialId || null,
        sessionId: session.id || window.MATERIAL_VIEWER_CONTEXT?.sessionId || null,
        sessionOrderIndex: session.orderIndex || null,
        classroomContext: collectClassroomContext(),
        materialContext: collectMaterialContext(),
        assignmentContext: collectAssignmentContext(),
        manageContext: collectManageContext(),
        dashboardContext: collectDashboardContext(),
        ...extra,
    };
    Object.keys(context).forEach((key) => {
        const value = context[key];
        if (value && typeof value === 'object' && !Array.isArray(value) && !Object.keys(value).length) delete context[key];
    });
    return context;
}

export function contextLabel(context = collectPageContext()) {
    const pieces = [
        context.materialContext?.materialName,
        context.assignmentContext?.title,
        context.classroomContext?.courseName,
        context.manageContext?.pageTitle,
        context.page?.title,
    ].filter(Boolean);
    return clampText(pieces[0] || '当前页面', 90);
}

export function formatContextForPrompt(context = collectPageContext()) {
    const lines = ['【当前页面背景】', `页面：${context.page?.title || document.title}`, `路径：${context.page?.path || window.location.pathname}`];
    if (context.page?.activeArea) lines.push(`当前区域：${context.page.activeArea}`);
    if (context.page?.selectedText) lines.push(`用户选中文本：${context.page.selectedText}`);
    if (context.classroomContext?.courseName || context.classroomContext?.className) {
        lines.push(`课堂：${context.classroomContext.courseName || ''} ${context.classroomContext.className || ''}`.trim());
    }
    const selected = context.classroomContext?.selectedSession;
    if (selected?.title) {
        lines.push(`当前课时：第 ${selected.orderIndex || ''} 次课 ${selected.title}`.trim());
        if (selected.learningMaterialName) lines.push(`当前课时文档：${selected.learningMaterialName} ${selected.learningMaterialPath || ''}`.trim());
    }
    if (context.materialContext?.materialName) lines.push(`材料：${context.materialContext.materialName} ${context.materialContext.materialPath || ''}`.trim());
    if (context.assignmentContext?.title) lines.push(`作业/考试：${context.assignmentContext.title}`);
    if (context.manageContext?.pageTitle) lines.push(`管理页面：${context.manageContext.pageTitle}`);
    const headings = context.page?.headings || [];
    if (headings.length) lines.push(`页面重点：${headings.join(' / ')}`);
    lines.push(`结构化线索：${JSON.stringify({
        classOfferingId: context.classOfferingId, assignmentId: context.assignmentId, materialId: context.materialId,
        classroomContext: context.classroomContext, materialContext: context.materialContext,
        assignmentContext: context.assignmentContext, manageContext: context.manageContext,
    })}`);
    return lines.join('\n').slice(0, 12000);
}
