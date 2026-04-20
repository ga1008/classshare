const config = window.MANAGE_WORKFLOW_DATA || {};
const steps = Array.isArray(config.steps) ? config.steps : [];
const prepResources = Array.isArray(config.prep_resources) ? config.prep_resources : [];
const counts = config.counts || {};
const stageViews = config.stage_views || {};

const stepMap = new Map(steps.map((item) => [item.id, item]));
const prepMap = new Map(prepResources.map((item) => [item.id, item]));
const stageOrder = steps.map((item) => item.id);

const statusLabels = {
    complete: '已完成',
    in_progress: '进行中',
    pending: '待开始',
};

const elements = {
    recommendedLabel: document.getElementById('workflowRecommendedLabel'),
    recommendedHelp: document.getElementById('workflowRecommendedHelp'),
    stageButtons: Array.from(document.querySelectorAll('[data-stage-id]')),
    prepTabs: document.getElementById('workflowPrepTabs'),
    prepButtons: Array.from(document.querySelectorAll('[data-prep-id]')),
    stageEyebrow: document.getElementById('workflowStageEyebrow'),
    stageTitle: document.getElementById('workflowStageTitle'),
    stageDescription: document.getElementById('workflowStageDescription'),
    stageSummaryText: document.getElementById('workflowStageSummaryText'),
    stageAdviceText: document.getElementById('workflowStageAdviceText'),
    checklist: document.getElementById('workflowChecklist'),
    prevBtn: document.getElementById('workflowPrevBtn'),
    nextBtn: document.getElementById('workflowNextBtn'),
    frameTitle: document.getElementById('workflowFrameTitle'),
    frameSubtitle: document.getElementById('workflowFrameSubtitle'),
    openPageLink: document.getElementById('workflowOpenPageLink'),
    frame: document.getElementById('workflowContentFrame'),
    frameLoading: document.getElementById('workflowFrameLoading'),
};

const state = {
    stage: resolveInitialStage(),
    prep: resolveInitialPrep(),
    frameSrc: '',
};

function resolveInitialStage() {
    const params = new URLSearchParams(window.location.search);
    const fromQuery = String(params.get('stage') || '').trim();
    if (stepMap.has(fromQuery)) {
        return fromQuery;
    }
    return String(config.recommended_stage || stageOrder[0] || 'preparation');
}

function resolveInitialPrep() {
    const params = new URLSearchParams(window.location.search);
    const fromQuery = String(params.get('prep') || '').trim();
    if (prepMap.has(fromQuery)) {
        return fromQuery;
    }
    return String(config.recommended_prep || prepResources[0]?.id || 'classes');
}

function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function getCurrentStep() {
    return stepMap.get(state.stage) || steps[0] || null;
}

function getCurrentPrepResource() {
    return prepMap.get(state.prep) || prepResources[0] || null;
}

function syncUrl() {
    const params = new URLSearchParams(window.location.search);
    params.set('stage', state.stage);
    if (state.stage === 'preparation') {
        params.set('prep', state.prep);
    } else {
        params.delete('prep');
    }
    const query = params.toString();
    const nextUrl = query ? `${window.location.pathname}?${query}` : window.location.pathname;
    window.history.replaceState({}, '', nextUrl);
}

function getCurrentFrameTarget() {
    const step = getCurrentStep();
    if (!step) {
        return null;
    }

    if (step.id === 'preparation') {
        const resource = getCurrentPrepResource();
        if (!resource) {
            return null;
        }
        return {
            title: `${resource.title}准备`,
            subtitle: '下方直接复用当前模块中的新建表单与已创建列表。',
            href: resource.href,
            embedUrl: resource.embed_url,
        };
    }

    const view = stageViews[step.id];
    if (!view) {
        return null;
    }
    return {
        title: step.title,
        subtitle: '下方直接复用当前阶段页面中的表单、列表与已有业务逻辑。',
        href: view.href,
        embedUrl: view.embed_url,
    };
}

function buildStageSummary(step) {
    if (!step) {
        return '';
    }

    if (step.id === 'preparation') {
        return `当前已准备 ${prepResources.filter((item) => item.ready).length}/${prepResources.length} 项基础资源。建议优先补齐班级、课程和教材，再继续进入学期与课堂流程。`;
    }
    if (step.id === 'semester') {
        return counts.semesters > 0
            ? `当前已维护 ${counts.semesters} 个学期，其中 ${counts.current_semesters} 个覆盖今天的日期。新建课堂时会优先使用这里定义的时间范围。`
            : '当前还没有学期。建议先创建本学期，再继续开设课堂，避免排课时间和周次规则不一致。';
    }
    if (step.id === 'offerings') {
        return counts.offerings > 0
            ? `当前已开设 ${counts.offerings} 个课堂。可以继续补充课堂，也可以返回前序资源页面完善课程、教材和班级。`
            : '当前还没有课堂。这里会将学期、班级、课程和教材组合为具体课堂，并生成课堂排期。';
    }
    return counts.ai_configs > 0
        ? `当前已有 ${counts.ai_configs} 个课堂完成 AI 配置。建议持续检查提示词、知识依据和教材绑定是否与课堂保持一致。`
        : '当前还没有课堂级 AI 配置。请先选择一个已经开设的课堂，再绑定教材并补全提示词与知识依据。';
}

function buildStageAdvice(step) {
    if (!step) {
        return '';
    }

    if (step.id === 'preparation') {
        const missing = prepResources.filter((item) => !item.ready).map((item) => item.title);
        if (!missing.length) {
            return '基础资源已经齐备。建议下一步直接确认学期，再进入课堂创建流程。';
        }
        return `还未准备：${missing.join('、')}。这些资源彼此独立、可跨学期复用，先整理好后续会减少重复录入。`;
    }
    if (step.id === 'semester') {
        return counts.semesters > 0
            ? '若本学期已经在用，优先检查日期范围和周次是否准确；如果即将进入新学期，建议现在就提前建好草稿。'
            : '创建学期时建议直接按实际起止日期填写，这样课堂时间轴、考试安排和课堂周次会保持一致。';
    }
    if (step.id === 'offerings') {
        return counts.semesters === 0
            ? '在开设课堂前，建议先回到“确认学期”创建至少一个学期。'
            : '开设课堂时优先绑定课程模板和教材，后续课堂 AI 会直接复用这里的上下文。';
    }
    return counts.offerings === 0
        ? '先创建至少一个课堂，课堂级 AI 助教才能建立自己的上下文。'
        : '建议为正在使用的课堂逐个完成 AI 配置，避免不同班级共用同一套提示词造成语境错位。';
}

function buildChecklistItems(step) {
    if (!step) {
        return [];
    }

    if (step.id === 'preparation') {
        return prepResources.map((item) => ({
            title: item.title,
            ready: item.ready,
            status: item.status_label,
            description: `${item.description} 当前已准备 ${item.count} 项。`,
        }));
    }

    if (step.id === 'semester') {
        const basicReady = prepResources.filter((item) => item.ready).length;
        return [
            {
                title: '基础资源准备',
                ready: basicReady >= 3,
                status: `${basicReady}/${prepResources.length}`,
                description: '至少建议先准备班级、课程和教材，再统一建立本学期。',
            },
            {
                title: '学期数量',
                ready: counts.semesters > 0,
                status: `${counts.semesters} 个`,
                description: '学期决定课堂时间范围、周次和后续排课依据。',
            },
            {
                title: '当前学期识别',
                ready: counts.current_semesters > 0,
                status: `${counts.current_semesters} 个`,
                description: '若今天落在某个学期范围内，开课时会更容易直接选中当前学期。',
            },
        ];
    }

    if (step.id === 'offerings') {
        return [
            {
                title: '班级',
                ready: counts.classes > 0,
                status: `${counts.classes} 个`,
                description: '课堂必须绑定一个班级。建议先导入学生名单，再集中开课。',
            },
            {
                title: '课程与教材',
                ready: counts.courses > 0 && counts.textbooks > 0,
                status: `${counts.courses} 门课程 / ${counts.textbooks} 本教材`,
                description: '课程模板决定课堂结构，教材会直接成为课堂和 AI 助教的参考依据。',
            },
            {
                title: '学期',
                ready: counts.semesters > 0,
                status: `${counts.semesters} 个`,
                description: '课堂在这里绑定学期后，排课预览和时间轴才能保持准确。',
            },
            {
                title: '已开课堂',
                ready: counts.offerings > 0,
                status: `${counts.offerings} 个`,
                description: '已经创建的课堂可以继续编辑，也能直接进入课堂或配置 AI。',
            },
        ];
    }

    return [
        {
            title: '已开课堂',
            ready: counts.offerings > 0,
            status: `${counts.offerings} 个`,
            description: '只有已经开设的课堂，才能拥有独立的课堂级 AI 助教配置。',
        },
        {
            title: '教材储备',
            ready: counts.textbooks > 0,
            status: `${counts.textbooks} 本`,
            description: '教材越完善，AI 自动生成的提示词和知识依据越稳定。',
        },
        {
            title: '已完成配置',
            ready: counts.ai_configs > 0,
            status: `${counts.ai_configs} 个`,
            description: '建议为常用课堂逐个完成 AI 配置，避免遗漏当前正在教学的班级。',
        },
    ];
}

function renderChecklist(step) {
    if (!elements.checklist) {
        return;
    }

    const items = buildChecklistItems(step);
    elements.checklist.innerHTML = items.map((item) => `
        <article class="workflow-check-item${item.ready ? ' is-ready' : ''}">
            <div class="workflow-check-item-top">
                <strong>${escapeHtml(item.title)}</strong>
                <span class="workflow-status-badge ${item.ready ? 'is-complete' : 'is-pending'}">${escapeHtml(item.status)}</span>
            </div>
            <p>${escapeHtml(item.description)}</p>
        </article>
    `).join('');
}

function renderStageRail() {
    elements.stageButtons.forEach((button) => {
        const stageId = button.dataset.stageId;
        const step = stepMap.get(stageId);
        const badge = button.querySelector('.workflow-status-badge');
        button.classList.toggle('is-active', stageId === state.stage);
        button.classList.toggle('is-complete', step?.status === 'complete');
        if (badge && step) {
            badge.textContent = statusLabels[step.status] || step.status;
            badge.classList.remove('is-complete', 'is-in_progress', 'is-pending');
            badge.classList.add(`is-${step.status}`);
        }
        button.setAttribute('aria-current', stageId === state.stage ? 'true' : 'false');
    });
}

function renderPrepTabs() {
    if (!elements.prepTabs) {
        return;
    }
    const showTabs = state.stage === 'preparation';
    elements.prepTabs.hidden = !showTabs;
    elements.prepButtons.forEach((button) => {
        button.classList.toggle('is-active', showTabs && button.dataset.prepId === state.prep);
    });
}

function renderStageCopy(step) {
    if (!step) {
        return;
    }
    if (elements.stageEyebrow) {
        elements.stageEyebrow.textContent = step.eyebrow || '流程阶段';
    }
    if (elements.stageTitle) {
        elements.stageTitle.textContent = step.title || '流程阶段';
    }
    if (elements.stageDescription) {
        elements.stageDescription.textContent = step.description || '';
    }
    if (elements.stageSummaryText) {
        elements.stageSummaryText.textContent = buildStageSummary(step);
    }
    if (elements.stageAdviceText) {
        elements.stageAdviceText.textContent = buildStageAdvice(step);
    }
}

function renderRecommended() {
    const recommendedStep = stepMap.get(String(config.recommended_stage || ''));
    if (!recommendedStep) {
        return;
    }
    if (elements.recommendedLabel) {
        elements.recommendedLabel.textContent = recommendedStep.title;
    }
    if (elements.recommendedHelp) {
        elements.recommendedHelp.textContent = state.stage === recommendedStep.id
            ? '当前就是系统推荐的下一步，可直接在下方继续处理。'
            : `系统建议优先处理“${recommendedStep.title}”，也可以按需切换到其他阶段。`;
    }
}

function renderStepNavigation() {
    const currentIndex = stageOrder.indexOf(state.stage);
    if (elements.prevBtn) {
        elements.prevBtn.disabled = currentIndex <= 0;
    }
    if (elements.nextBtn) {
        elements.nextBtn.disabled = currentIndex === -1 || currentIndex >= stageOrder.length - 1;
    }
}

function setLoading(isLoading) {
    if (!elements.frameLoading) {
        return;
    }
    elements.frameLoading.classList.toggle('is-visible', isLoading);
}

function updateFrameMeta(target) {
    if (!target) {
        return;
    }
    if (elements.frameTitle) {
        elements.frameTitle.textContent = target.title;
    }
    if (elements.frameSubtitle) {
        elements.frameSubtitle.textContent = target.subtitle;
    }
    if (elements.openPageLink) {
        elements.openPageLink.href = target.href;
    }
}

function loadFrame() {
    const target = getCurrentFrameTarget();
    if (!target || !elements.frame) {
        return;
    }

    updateFrameMeta(target);
    if (state.frameSrc === target.embedUrl) {
        return;
    }

    state.frameSrc = target.embedUrl;
    setLoading(true);
    elements.frame.style.height = '960px';
    elements.frame.src = target.embedUrl;
}

function render() {
    const step = getCurrentStep();
    renderRecommended();
    renderStageRail();
    renderPrepTabs();
    renderStageCopy(step);
    renderChecklist(step);
    renderStepNavigation();
    loadFrame();
    syncUrl();
}

function switchStage(nextStage) {
    if (!stepMap.has(nextStage)) {
        return;
    }
    state.stage = nextStage;
    if (state.stage !== 'preparation') {
        const firstIncompletePrep = prepResources.find((item) => !item.ready)?.id;
        if (firstIncompletePrep) {
            state.prep = firstIncompletePrep;
        }
    }
    render();
}

function switchPrep(nextPrep) {
    if (!prepMap.has(nextPrep)) {
        return;
    }
    state.prep = nextPrep;
    if (state.stage !== 'preparation') {
        state.stage = 'preparation';
    }
    render();
}

function handleEmbedMessage(event) {
    if (event.origin !== window.location.origin) {
        return;
    }
    const payload = event.data || {};
    if (payload.type !== 'manage-embed-height' || !elements.frame) {
        return;
    }
    if (elements.frame.contentWindow && event.source !== elements.frame.contentWindow) {
        return;
    }
    const nextHeight = Number(payload.height || 0);
    if (Number.isFinite(nextHeight) && nextHeight > 0) {
        elements.frame.style.height = `${Math.max(nextHeight, 720)}px`;
        setLoading(false);
    }
}

function bindEvents() {
    elements.stageButtons.forEach((button) => {
        button.addEventListener('click', () => {
            switchStage(button.dataset.stageId);
        });
    });

    elements.prepButtons.forEach((button) => {
        button.addEventListener('click', () => {
            switchPrep(button.dataset.prepId);
        });
    });

    elements.prevBtn?.addEventListener('click', () => {
        const currentIndex = stageOrder.indexOf(state.stage);
        if (currentIndex > 0) {
            switchStage(stageOrder[currentIndex - 1]);
        }
    });

    elements.nextBtn?.addEventListener('click', () => {
        const currentIndex = stageOrder.indexOf(state.stage);
        if (currentIndex >= 0 && currentIndex < stageOrder.length - 1) {
            switchStage(stageOrder[currentIndex + 1]);
        }
    });

    elements.frame?.addEventListener('load', () => {
        setLoading(false);
    });

    window.addEventListener('message', handleEmbedMessage);
    window.addEventListener('popstate', () => {
        state.stage = resolveInitialStage();
        state.prep = resolveInitialPrep();
        render();
    });
}

bindEvents();
render();
