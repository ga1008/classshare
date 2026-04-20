const config = window.MANAGE_WORKFLOW_DATA || {};
const steps = Array.isArray(config.steps) ? config.steps : [];
const prepResources = Array.isArray(config.prep_resources) ? config.prep_resources : [];
const counts = config.counts || {};
const stageViews = config.stage_views || {};
const prefersReducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;

const stepMap = new Map(steps.map((item) => [item.id, item]));
const prepMap = new Map(prepResources.map((item) => [item.id, item]));
const stageOrder = steps.map((item) => item.id);
const animationTimers = new WeakMap();

const elements = {
    recommendedLabel: document.getElementById('workflowRecommendedLabel'),
    recommendedHelp: document.getElementById('workflowRecommendedHelp'),
    stageRail: document.querySelector('.workflow-stage-rail'),
    stageTrack: document.getElementById('workflowStageTrack'),
    stageButtons: Array.from(document.querySelectorAll('[data-stage-id]')),
    stageArrows: Array.from(document.querySelectorAll('[data-arrow-after-stage]')),
    prepTabs: document.getElementById('workflowPrepTabs'),
    prepButtons: Array.from(document.querySelectorAll('[data-prep-id]')),
    focusCard: document.querySelector('.workflow-focus-card'),
    frameShell: document.querySelector('.workflow-frame-shell'),
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

const dragState = {
    active: false,
    dragging: false,
    suppressClick: false,
    pointerId: null,
    startX: 0,
    startScrollLeft: 0,
    lastX: 0,
    lastTime: 0,
    velocity: 0,
    targetScrollLeft: 0,
    metricsFrame: 0,
    scrollFrame: 0,
    momentumFrame: 0,
    momentumLastTime: 0,
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

function countOf(key) {
    return Number(counts[key] || 0);
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
            subtitle: '下方直接处理当前基础资源的表单和已创建列表。',
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
        subtitle: '下方直接复用当前阶段已有的表单和列表。',
        href: view.href,
        embedUrl: view.embed_url,
    };
}

function buildStageSummary(step) {
    if (!step) {
        return '';
    }

    if (step.id === 'preparation') {
        const readyCount = prepResources.filter((item) => item.ready).length;
        return `已准备 ${readyCount}/${prepResources.length} 项基础资源，优先补齐班级、课程和教材后再继续。`;
    }

    if (step.id === 'semester') {
        return countOf('semesters') > 0
            ? `当前共有 ${countOf('semesters')} 个学期，其中 ${countOf('current_semesters')} 个覆盖今天的日期。`
            : '当前还没有学期，建议先创建本学期，再继续开设课堂。';
    }

    if (step.id === 'offerings') {
        return countOf('offerings') > 0
            ? `当前已开设 ${countOf('offerings')} 个课堂，可以继续补充或调整已有课堂。`
            : '这里会把学期、班级、课程和教材组合成具体课堂。';
    }

    return countOf('ai_configs') > 0
        ? `当前已有 ${countOf('ai_configs')} 个课堂完成 AI 助手配置。`
        : '当前还没有课堂级 AI 配置，请先选择一个已开设的课堂。';
}

function buildStageAdvice(step) {
    if (!step) {
        return '';
    }

    if (step.id === 'preparation') {
        const missing = prepResources.filter((item) => !item.ready).map((item) => item.title);
        if (!missing.length) {
            return '基础资源已经齐备，建议直接进入“确认学期”。';
        }
        return `还未准备：${missing.join('、')}。先补齐这些资源，后续开设课堂会更顺畅。`;
    }

    if (step.id === 'semester') {
        return countOf('semesters') > 0
            ? '优先检查当前学期的起止日期和周次设置是否准确。'
            : '创建学期时建议直接按真实教学周期填写日期范围。';
    }

    if (step.id === 'offerings') {
        return countOf('semesters') === 0
            ? '建议先回到“确认学期”，至少创建一个可用学期。'
            : '开设课堂时优先绑定课程模板和教材，后续 AI 上下文会更稳定。';
    }

    return countOf('offerings') === 0
        ? '先创建至少一个课堂，课堂级 AI 助手才能建立自己的上下文。'
        : '建议优先为当前正在使用的课堂完成 AI 配置。';
}

function buildChecklistItems(step) {
    if (!step) {
        return [];
    }

    if (step.id === 'preparation') {
        return prepResources.map((item) => ({
            title: item.title,
            ready: Boolean(item.ready),
            status: item.status_label,
            description: `当前 ${item.count} 项，可跨学期复用。`,
        }));
    }

    if (step.id === 'semester') {
        const basicReady = prepResources.filter((item) => item.ready).length;
        return [
            {
                title: '基础资源准备',
                ready: basicReady >= 3,
                status: `${basicReady}/${prepResources.length}`,
                description: '建议至少先准备班级、课程和教材。',
            },
            {
                title: '学期数量',
                ready: countOf('semesters') > 0,
                status: `${countOf('semesters')} 个`,
                description: '学期决定课堂时间范围和周次设置。',
            },
            {
                title: '当前学期识别',
                ready: countOf('current_semesters') > 0,
                status: `${countOf('current_semesters')} 个`,
                description: '覆盖今天日期的学期更适合作为默认学期。',
            },
        ];
    }

    if (step.id === 'offerings') {
        return [
            {
                title: '班级',
                ready: countOf('classes') > 0,
                status: `${countOf('classes')} 个`,
                description: '每个课堂都需要绑定班级。',
            },
            {
                title: '课程与教材',
                ready: countOf('courses') > 0 && countOf('textbooks') > 0,
                status: `${countOf('courses')} 门 / ${countOf('textbooks')} 本`,
                description: '课程模板和教材都会进入课堂上下文。',
            },
            {
                title: '学期',
                ready: countOf('semesters') > 0,
                status: `${countOf('semesters')} 个`,
                description: '绑定学期后，排课预览和时间信息才会准确。',
            },
            {
                title: '已开课堂',
                ready: countOf('offerings') > 0,
                status: `${countOf('offerings')} 个`,
                description: '已创建的课堂可以继续编辑，或直接进入使用。',
            },
        ];
    }

    return [
        {
            title: '已开课堂',
            ready: countOf('offerings') > 0,
            status: `${countOf('offerings')} 个`,
            description: '只有已开设的课堂才能继续配置课堂级 AI 助手。',
        },
        {
            title: '教材准备',
            ready: countOf('textbooks') > 0,
            status: `${countOf('textbooks')} 本`,
            description: '教材越完整，AI 上下文越稳定。',
        },
        {
            title: '已完成配置',
            ready: countOf('ai_configs') > 0,
            status: `${countOf('ai_configs')} 个`,
            description: '建议优先覆盖正在使用的课堂。',
        },
    ];
}

function resolveStageBadgeText(step) {
    if (!step) {
        return '已有 0 份';
    }

    const fromSnapshot = String(step.badge_text || '').trim();
    if (fromSnapshot) {
        return fromSnapshot;
    }

    const count = Number(step.badge_count ?? step.count ?? 0);
    return `已有 ${count} 份`;
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
    const currentIndex = stageOrder.indexOf(state.stage);

    elements.stageButtons.forEach((button) => {
        const stageId = button.dataset.stageId;
        const step = stepMap.get(stageId);
        const badge = button.querySelector('.workflow-status-badge');
        const isActive = stageId === state.stage;
        const badgeCount = Number(step?.badge_count ?? step?.count ?? 0);

        button.classList.toggle('is-active', isActive);
        button.classList.toggle('has-content', badgeCount > 0);
        button.setAttribute('aria-current', isActive ? 'step' : 'false');
        button.setAttribute('aria-selected', isActive ? 'true' : 'false');
        button.setAttribute('tabindex', isActive ? '0' : '-1');

        if (badge) {
            badge.textContent = resolveStageBadgeText(step);
            badge.classList.remove('is-complete', 'is-in_progress', 'is-pending', 'has-count', 'is-empty', 'is-active-stage');
            badge.classList.add(badgeCount > 0 ? 'has-count' : 'is-empty');
            if (isActive) {
                badge.classList.add('is-active-stage');
            }
        }
    });

    elements.stageArrows.forEach((arrow) => {
        const arrowIndex = stageOrder.indexOf(arrow.dataset.arrowAfterStage);
        arrow.classList.toggle('is-traversed', currentIndex > arrowIndex);
        arrow.classList.toggle('is-current', currentIndex === arrowIndex);
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
            ? '当前就是推荐阶段，可以直接在下方继续处理。'
            : `系统建议优先处理“${recommendedStep.title}”。`;
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

function restartAnimation(node, className, duration = 480) {
    if (!node || prefersReducedMotion) {
        return;
    }

    const activeTimer = animationTimers.get(node);
    if (activeTimer) {
        window.clearTimeout(activeTimer);
    }

    node.classList.remove(className);
    void node.offsetWidth;
    node.classList.add(className);

    const timer = window.setTimeout(() => {
        node.classList.remove(className);
        animationTimers.delete(node);
    }, duration);
    animationTimers.set(node, timer);
}

function ensureActiveStepVisible({ animate = false } = {}) {
    const activeButton = elements.stageButtons.find((button) => button.dataset.stageId === state.stage);
    if (!activeButton) {
        return;
    }

    activeButton.scrollIntoView({
        behavior: animate && !prefersReducedMotion ? 'smooth' : 'auto',
        block: 'nearest',
        inline: 'center',
    });
}

function animateCurrentStep() {
    const activeButton = elements.stageButtons.find((button) => button.dataset.stageId === state.stage);
    restartAnimation(activeButton, 'is-activating', 420);
    ensureActiveStepVisible({ animate: true });
}

function playPanelTransitions() {
    restartAnimation(elements.focusCard, 'is-switching', 420);
    restartAnimation(elements.frameShell, 'is-switching', 460);
}

function clampRailScroll(value) {
    const rail = elements.stageRail;
    if (!rail) {
        return 0;
    }
    const maxScrollLeft = Math.max(rail.scrollWidth - rail.clientWidth, 0);
    return Math.min(Math.max(value, 0), maxScrollLeft);
}

function updateStageRailMetrics() {
    const rail = elements.stageRail;
    if (!rail) {
        return;
    }

    const maxScrollLeft = Math.max(rail.scrollWidth - rail.clientWidth, 0);
    const hasOverflow = maxScrollLeft > 4;
    rail.classList.toggle('is-scrollable', hasOverflow);
    rail.classList.toggle('is-at-start', rail.scrollLeft <= 2);
    rail.classList.toggle('is-at-end', rail.scrollLeft >= maxScrollLeft - 2);
}

function scheduleStageRailMetrics() {
    if (dragState.metricsFrame) {
        return;
    }
    dragState.metricsFrame = window.requestAnimationFrame(() => {
        dragState.metricsFrame = 0;
        updateStageRailMetrics();
    });
}

function cancelRailScrollFrame() {
    if (!dragState.scrollFrame) {
        return;
    }
    window.cancelAnimationFrame(dragState.scrollFrame);
    dragState.scrollFrame = 0;
}

function scheduleRailScroll(nextScrollLeft) {
    const rail = elements.stageRail;
    if (!rail) {
        return;
    }

    dragState.targetScrollLeft = clampRailScroll(nextScrollLeft);
    if (dragState.scrollFrame) {
        return;
    }

    dragState.scrollFrame = window.requestAnimationFrame(() => {
        dragState.scrollFrame = 0;
        rail.scrollLeft = dragState.targetScrollLeft;
        updateStageRailMetrics();
    });
}

function cancelRailMomentum() {
    if (!dragState.momentumFrame) {
        return;
    }
    window.cancelAnimationFrame(dragState.momentumFrame);
    dragState.momentumFrame = 0;
    elements.stageRail?.classList.remove('is-gliding');
}

function startRailMomentum(initialVelocity) {
    const rail = elements.stageRail;
    if (!rail || prefersReducedMotion || Math.abs(initialVelocity) < 0.02) {
        return;
    }

    cancelRailMomentum();
    dragState.velocity = initialVelocity;
    dragState.momentumLastTime = performance.now();
    rail.classList.add('is-gliding');

    const step = (timestamp) => {
        const deltaTime = Math.min(32, Math.max(8, timestamp - dragState.momentumLastTime || 16));
        dragState.momentumLastTime = timestamp;

        const maxScrollLeft = Math.max(rail.scrollWidth - rail.clientWidth, 0);
        if (maxScrollLeft <= 0) {
            cancelRailMomentum();
            updateStageRailMetrics();
            return;
        }

        const nextScrollLeft = clampRailScroll(rail.scrollLeft + dragState.velocity * deltaTime);
        const hitEdge = nextScrollLeft <= 0 || nextScrollLeft >= maxScrollLeft;
        rail.scrollLeft = nextScrollLeft;
        updateStageRailMetrics();

        dragState.velocity *= hitEdge ? 0.72 : Math.pow(0.94, deltaTime / 16);
        if (Math.abs(dragState.velocity) < 0.02) {
            cancelRailMomentum();
            updateStageRailMetrics();
            return;
        }

        dragState.momentumFrame = window.requestAnimationFrame(step);
    };

    dragState.momentumFrame = window.requestAnimationFrame(step);
}

function resetDragState() {
    dragState.active = false;
    dragState.dragging = false;
    dragState.pointerId = null;
    dragState.startX = 0;
    dragState.startScrollLeft = 0;
    dragState.lastX = 0;
    dragState.lastTime = 0;
    dragState.velocity = 0;
    elements.stageRail?.classList.remove('is-dragging');
}

function temporarilySuppressClick() {
    dragState.suppressClick = true;
    window.setTimeout(() => {
        dragState.suppressClick = false;
    }, 180);
}

function bindStageRailDrag() {
    const rail = elements.stageRail;
    if (!rail) {
        return;
    }

    rail.addEventListener('dragstart', (event) => {
        event.preventDefault();
    });

    rail.addEventListener('scroll', scheduleStageRailMetrics, { passive: true });

    rail.addEventListener('pointerdown', (event) => {
        if (event.button !== 0 || rail.scrollWidth <= rail.clientWidth) {
            return;
        }

        cancelRailMomentum();
        cancelRailScrollFrame();

        dragState.active = true;
        dragState.dragging = false;
        dragState.pointerId = event.pointerId;
        dragState.startX = event.clientX;
        dragState.startScrollLeft = rail.scrollLeft;
        dragState.lastX = event.clientX;
        dragState.lastTime = performance.now();
        dragState.velocity = 0;
    });

    rail.addEventListener('pointermove', (event) => {
        if (!dragState.active || dragState.pointerId !== event.pointerId) {
            return;
        }

        const deltaX = event.clientX - dragState.startX;
        if (!dragState.dragging && Math.abs(deltaX) > 8) {
            dragState.dragging = true;
            rail.classList.add('is-dragging');
            if (typeof rail.setPointerCapture === 'function') {
                try {
                    rail.setPointerCapture(event.pointerId);
                } catch {
                    // ignore pointer capture failures
                }
            }
        }

        if (!dragState.dragging) {
            return;
        }

        event.preventDefault();
        scheduleRailScroll(dragState.startScrollLeft - deltaX);

        const now = performance.now();
        const deltaTime = Math.max(now - dragState.lastTime, 1);
        const instantVelocity = (dragState.lastX - event.clientX) / deltaTime;
        dragState.velocity = (dragState.velocity * 0.72) + (instantVelocity * 0.28);
        dragState.lastX = event.clientX;
        dragState.lastTime = now;
    });

    const finishDrag = (event) => {
        if (!dragState.active || dragState.pointerId !== event.pointerId) {
            return;
        }

        const wasDragging = dragState.dragging;
        const releaseVelocity = dragState.velocity;

        if (typeof rail.hasPointerCapture === 'function' && rail.hasPointerCapture(event.pointerId)) {
            try {
                rail.releasePointerCapture(event.pointerId);
            } catch {
                // ignore pointer release failures
            }
        }

        resetDragState();

        if (!wasDragging) {
            return;
        }

        temporarilySuppressClick();
        startRailMomentum(releaseVelocity);
    };

    rail.addEventListener('pointerup', finishDrag);
    rail.addEventListener('pointercancel', finishDrag);
    rail.addEventListener('lostpointercapture', (event) => {
        if (!dragState.active || dragState.pointerId !== event.pointerId) {
            return;
        }
        resetDragState();
        updateStageRailMetrics();
    });

    window.addEventListener('resize', scheduleStageRailMetrics);
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
    elements.frame.style.height = '920px';
    elements.frame.src = target.embedUrl;
}

function render({ animateStep = false, animatePanels = false } = {}) {
    const step = getCurrentStep();
    renderRecommended();
    renderStageRail();
    renderPrepTabs();
    renderStageCopy(step);
    renderChecklist(step);
    renderStepNavigation();

    if (animatePanels) {
        playPanelTransitions();
    }
    if (animateStep) {
        animateCurrentStep();
    } else {
        ensureActiveStepVisible();
    }

    loadFrame();
    syncUrl();
    scheduleStageRailMetrics();
}

function switchStage(nextStage, { animate = true } = {}) {
    if (!stepMap.has(nextStage) || nextStage === state.stage) {
        return;
    }

    state.stage = nextStage;
    render({
        animateStep: animate,
        animatePanels: animate,
    });
}

function switchPrep(nextPrep, { animate = true } = {}) {
    if (!prepMap.has(nextPrep)) {
        return;
    }

    const stageChanged = state.stage !== 'preparation';
    const prepChanged = state.prep !== nextPrep;
    if (!stageChanged && !prepChanged) {
        return;
    }

    state.prep = nextPrep;
    state.stage = 'preparation';
    render({
        animateStep: animate,
        animatePanels: animate,
    });
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
        button.addEventListener('click', (event) => {
            if (dragState.suppressClick) {
                dragState.suppressClick = false;
                event.preventDefault();
                event.stopPropagation();
                return;
            }
            switchStage(button.dataset.stageId);
        });
    });

    elements.stageTrack?.addEventListener('keydown', (event) => {
        const currentIndex = stageOrder.indexOf(state.stage);
        let nextIndex = currentIndex;

        if (event.key === 'ArrowRight') {
            nextIndex = Math.min(stageOrder.length - 1, currentIndex + 1);
        } else if (event.key === 'ArrowLeft') {
            nextIndex = Math.max(0, currentIndex - 1);
        } else if (event.key === 'Home') {
            nextIndex = 0;
        } else if (event.key === 'End') {
            nextIndex = stageOrder.length - 1;
        } else {
            return;
        }

        if (nextIndex === -1 || nextIndex === currentIndex) {
            return;
        }

        event.preventDefault();
        switchStage(stageOrder[nextIndex]);
        const nextButton = elements.stageButtons.find((button) => button.dataset.stageId === stageOrder[nextIndex]);
        nextButton?.focus();
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

bindStageRailDrag();
bindEvents();
render();
