const config = window.MANAGE_WORKFLOW_DATA || {};
const steps = Array.isArray(config.steps) ? config.steps : [];
const counts = config.counts || {};
const stageViews = config.stage_views || {};
const prefersReducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;

const stepMap = new Map(steps.map((item) => [item.id, item]));
const stageOrder = steps.map((item) => item.id);
const animationTimers = new WeakMap();

const elements = {
    recommendedLabel: document.getElementById('workflowRecommendedLabel'),
    recommendedHelp: document.getElementById('workflowRecommendedHelp'),
    stageRail: document.querySelector('.workflow-stage-rail'),
    stageTrack: document.getElementById('workflowStageTrack'),
    stageButtons: Array.from(document.querySelectorAll('[data-stage-id]')),
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
    railPrevBtn: document.getElementById('workflowRailPrevBtn'),
    railNextBtn: document.getElementById('workflowRailNextBtn'),
    frameTitle: document.getElementById('workflowFrameTitle'),
    frameSubtitle: document.getElementById('workflowFrameSubtitle'),
    openPageLink: document.getElementById('workflowOpenPageLink'),
    frame: document.getElementById('workflowContentFrame'),
    frameLoading: document.getElementById('workflowFrameLoading'),
};

const state = {
    stage: resolveInitialStage(),
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
    return String(config.recommended_stage || stageOrder[0] || 'semester');
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

function syncUrl() {
    const params = new URLSearchParams(window.location.search);
    params.set('stage', state.stage);
    params.delete('prep');

    const query = params.toString();
    const nextUrl = query ? `${window.location.pathname}?${query}` : window.location.pathname;
    window.history.replaceState({}, '', nextUrl);
}

function getCurrentFrameTarget() {
    const step = getCurrentStep();
    if (!step) {
        return null;
    }

    const view = stageViews[step.id];
    if (!view) {
        return null;
    }

    return {
        title: step.title,
        subtitle: step.summary || '下方直接复用当前阶段已有的表单和列表。',
        href: view.href,
        embedUrl: view.embed_url,
    };
}

function buildStageSummary(step) {
    if (!step) {
        return '';
    }
    return step.summary || '';
}

function buildStageAdvice(step) {
    if (!step) {
        return '';
    }
    return step.advice || '';
}

function buildChecklistItems(step) {
    if (!step) {
        return [];
    }
    if (Array.isArray(step.checklist) && step.checklist.length) {
        return step.checklist;
    }

    return [
        {
            title: step.title || '当前步骤',
            ready: step.status === 'complete' || step.status === 'optional',
            status: step.status_label || resolveStageBadgeText(step),
            description: step.description || '',
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
    elements.stageButtons.forEach((button) => {
        const stageId = button.dataset.stageId;
        const step = stepMap.get(stageId);
        const badge = button.querySelector('.workflow-status-badge');
        const isActive = stageId === state.stage;
        const badgeCount = Number(step?.badge_count ?? step?.count ?? 0);

        button.classList.toggle('is-active', isActive);
        button.classList.toggle('has-content', badgeCount > 0);
        button.classList.remove('is-complete', 'is-in_progress', 'is-pending', 'is-optional');
        if (step?.status) {
            button.classList.add(`is-${step.status}`);
        }
        button.setAttribute('aria-current', isActive ? 'step' : 'false');
        button.setAttribute('aria-selected', isActive ? 'true' : 'false');
        button.setAttribute('tabindex', isActive ? '0' : '-1');

        if (badge) {
            badge.textContent = resolveStageBadgeText(step);
            badge.classList.remove('is-complete', 'is-in_progress', 'is-pending', 'is-optional', 'has-count', 'is-empty', 'is-active-stage');
            badge.classList.add(badgeCount > 0 ? 'has-count' : 'is-empty');
            if (step?.status) {
                badge.classList.add(`is-${step.status}`);
            }
            if (isActive) {
                badge.classList.add('is-active-stage');
            }
        }
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
    const isAtStart = currentIndex <= 0;
    const isAtEnd = currentIndex === -1 || currentIndex >= stageOrder.length - 1;

    [elements.prevBtn, elements.railPrevBtn].forEach((button) => {
        if (button) {
            button.disabled = isAtStart;
        }
    });
    [elements.nextBtn, elements.railNextBtn].forEach((button) => {
        if (button) {
            button.disabled = isAtEnd;
        }
    });
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

function switchStageByOffset(offset) {
    const currentIndex = stageOrder.indexOf(state.stage);
    const nextIndex = currentIndex + offset;
    if (currentIndex === -1 || nextIndex < 0 || nextIndex >= stageOrder.length) {
        return;
    }
    switchStage(stageOrder[nextIndex]);
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

    elements.prevBtn?.addEventListener('click', () => switchStageByOffset(-1));

    elements.nextBtn?.addEventListener('click', () => switchStageByOffset(1));
    elements.railPrevBtn?.addEventListener('click', () => switchStageByOffset(-1));
    elements.railNextBtn?.addEventListener('click', () => switchStageByOffset(1));

    elements.frame?.addEventListener('load', () => {
        setLoading(false);
    });

    window.addEventListener('message', handleEmbedMessage);
    window.addEventListener('popstate', () => {
        state.stage = resolveInitialStage();
        render();
    });
}

bindStageRailDrag();
bindEvents();
render();
