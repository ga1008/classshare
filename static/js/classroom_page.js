import { apiFetch } from '/static/js/api.js';
import { initLearningMaterialSelector } from '/static/js/learning_material_selector.js';
import { showToast } from '/static/js/ui.js';

const learningMaterialSelector = initLearningMaterialSelector();

function initCoursePopover() {
    const popover = document.getElementById('course-info-popover');
    if (!popover) return;

    const overlay = document.getElementById('course-popover-overlay');
    const closeBtn = document.getElementById('course-popover-close');
    const expandBtn = document.getElementById('hero-desc-expand-btn');
    const transitionMs = 280;

    const openPopover = () => {
        popover.hidden = false;
        popover.setAttribute('aria-hidden', 'false');
        document.body.classList.add('has-course-popover');
        window.requestAnimationFrame(() => {
            popover.classList.add('popover-open');
        });
    };

    const closePopover = () => {
        popover.classList.remove('popover-open');
        document.body.classList.remove('has-course-popover');
        window.setTimeout(() => {
            if (!popover.classList.contains('popover-open')) {
                popover.hidden = true;
                popover.setAttribute('aria-hidden', 'true');
            }
        }, transitionMs);
    };

    expandBtn?.addEventListener('click', (event) => {
        event.stopPropagation();
        openPopover();
    });

    overlay?.addEventListener('click', closePopover);
    closeBtn?.addEventListener('click', closePopover);

    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && popover.classList.contains('popover-open')) {
            closePopover();
        }
    });
}

function initWorkspaceNav() {
    const navLinks = Array.from(document.querySelectorAll('[data-workspace-nav]'));
    if (!navLinks.length) return;

    const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const resolveBehavior = (behavior) => (prefersReducedMotion ? 'auto' : behavior);
    const navItems = navLinks
        .map((link) => {
            const href = link.getAttribute('href') || '';
            const targetId = href.startsWith('#') ? href.slice(1) : '';
            const section = targetId ? document.getElementById(targetId) : null;
            return section ? { link, targetId, section } : null;
        })
        .filter(Boolean);

    if (!navItems.length) return;

    const spotlightDurationMs = prefersReducedMotion ? 720 : 1900;
    const manualNavigationGuardMs = prefersReducedMotion ? 180 : 960;
    const spotlightTimers = new WeakMap();
    let activeTargetId = '';
    let viewportSyncFrame = 0;
    let manualSyncTimer = 0;
    let manualNavigationUntil = 0;

    const setActiveLink = (targetId) => {
        activeTargetId = targetId || activeTargetId;
        navItems.forEach((item) => {
            const isActive = item.targetId === targetId;
            item.link.classList.toggle('is-active', isActive);
            if (isActive) {
                item.link.setAttribute('aria-current', 'location');
            } else {
                item.link.removeAttribute('aria-current');
            }
        });
    };

    const spotlightSection = (section) => {
        if (!section) return;

        const existingTimer = spotlightTimers.get(section);
        if (existingTimer) {
            window.clearTimeout(existingTimer);
        }

        section.classList.remove('is-nav-spotlight');
        void section.offsetWidth;
        section.classList.add('is-nav-spotlight');

        const timer = window.setTimeout(() => {
            section.classList.remove('is-nav-spotlight');
            spotlightTimers.delete(section);
        }, spotlightDurationMs);
        spotlightTimers.set(section, timer);
    };

    const getScrollTopForSection = (section) => {
        const rect = section.getBoundingClientRect();
        const scrollMarginTop = Number.parseFloat(window.getComputedStyle(section).scrollMarginTop) || 0;
        return Math.max(window.scrollY + rect.top - scrollMarginTop, 0);
    };

    const focusSection = (targetId, options = {}) => {
        const item = navItems.find((candidate) => candidate.targetId === targetId);
        if (!item) return;

        manualNavigationUntil = Date.now() + manualNavigationGuardMs;
        setActiveLink(item.targetId);

        const nextTop = getScrollTopForSection(item.section);
        const currentTop = window.scrollY || window.pageYOffset || 0;
        if (Math.abs(nextTop - currentTop) > 4) {
            window.scrollTo({
                top: nextTop,
                behavior: resolveBehavior(options.behavior || 'smooth'),
            });
        }

        spotlightSection(item.section);

        if (options.updateHash !== false && window.history && typeof window.history.replaceState === 'function') {
            const nextHash = `#${item.targetId}`;
            if (window.location.hash !== nextHash) {
                window.history.replaceState(null, '', nextHash);
            }
        }

        window.clearTimeout(manualSyncTimer);
        manualSyncTimer = window.setTimeout(() => {
            manualSyncTimer = 0;
            if (Date.now() >= manualNavigationUntil) {
                syncActiveLinkFromViewport();
            }
        }, manualNavigationGuardMs + 40);
    };

    const syncActiveLinkFromViewport = () => {
        if (Date.now() < manualNavigationUntil) return;

        const viewportAnchor = Math.min(window.innerHeight * 0.28, 220);
        let bestItem = navItems[0];
        let bestScore = Number.POSITIVE_INFINITY;

        navItems.forEach((item) => {
            const rect = item.section.getBoundingClientRect();
            const anchorInsideSection = rect.top <= viewportAnchor && rect.bottom >= viewportAnchor;
            const score = anchorInsideSection
                ? Math.abs(rect.top - viewportAnchor) - 10000
                : Math.abs(rect.top - viewportAnchor);

            if (score < bestScore) {
                bestScore = score;
                bestItem = item;
            }
        });

        if (bestItem && bestItem.targetId !== activeTargetId) {
            setActiveLink(bestItem.targetId);
        }
    };

    const scheduleViewportSync = () => {
        if (viewportSyncFrame) return;
        viewportSyncFrame = window.requestAnimationFrame(() => {
            viewportSyncFrame = 0;
            syncActiveLinkFromViewport();
        });
    };

    navItems.forEach((item) => {
        item.link.addEventListener('click', (event) => {
            event.preventDefault();
            focusSection(item.targetId, {
                behavior: 'smooth',
                updateHash: true,
            });
        });
    });

    window.addEventListener('scroll', scheduleViewportSync, { passive: true });
    window.addEventListener('resize', scheduleViewportSync);

    const initialHash = String(window.location.hash || '').replace(/^#/, '').trim();
    if (initialHash && navItems.some((item) => item.targetId === initialHash)) {
        window.requestAnimationFrame(() => {
            focusSection(initialHash, {
                behavior: 'auto',
                updateHash: false,
            });
        });
        return;
    }

    syncActiveLinkFromViewport();
}

function initTeachingTimeline() {
    const widget = document.getElementById('teaching-plan-widget');
    const scrollEl = document.getElementById('teachingTimelineScroll');
    const sessions = Array.isArray(window.APP_CONFIG?.teachingPlan?.sessions)
        ? window.APP_CONFIG.teachingPlan.sessions
        : [];
    if (!widget || !scrollEl || !sessions.length) return;

    const userInfo = window.APP_CONFIG?.userInfo || {};
    const isTeacher = String(userInfo.role || '').trim() === 'teacher';
    const detailKicker = document.getElementById('teachingTimelineDetailKicker');
    const detailTitle = document.getElementById('teachingTimelineDetailTitle');
    const detailStatus = document.getElementById('teachingTimelineDetailStatus');
    const detailSummary = document.getElementById('teachingTimelineDetailSummary');
    const detailMeta = document.getElementById('teachingTimelineDetailMeta');
    const materialPanel = document.getElementById('teachingTimelineMaterialPanel');
    const materialName = document.getElementById('teachingTimelineMaterialName');
    const materialPath = document.getElementById('teachingTimelineMaterialPath');
    const selectMaterialBtn = document.getElementById('teachingTimelineSelectMaterialBtn');
    const clearMaterialBtn = document.getElementById('teachingTimelineClearMaterialBtn');
    const openMaterialBtn = document.getElementById('teachingTimelineOpenMaterialBtn');
    const sessionButtons = Array.from(scrollEl.querySelectorAll('[data-session-order]'));
    const sessionMap = new Map(
        sessions.map((session) => [String(session.order_index), session]),
    );
    const buttonMap = new Map(
        sessionButtons.map((button) => [String(button.getAttribute('data-session-order') || ''), button]),
    );
    const detailSummaryCache = new Map();

    const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const resolveBehavior = (behavior) => (prefersReducedMotion ? 'auto' : behavior);
    let selectedOrder = String(
        sessions.find((session) => session.is_anchor)?.order_index
        ?? sessions[0]?.order_index
        ?? '',
    );

    let pointerId = null;
    let startX = 0;
    let startScrollLeft = 0;
    let dragDistance = 0;
    let snapTimer = 0;
    let ignoreClickUntil = 0;

    const getSessionByOrder = (sessionOrder) => sessionMap.get(String(sessionOrder || '').trim());

    const updateSessionButtonMaterialState = (session) => {
        if (!session) return;
        const button = buttonMap.get(String(session.order_index));
        if (!button) return;
        const indicator = button.querySelector('[data-role="session-material-indicator"]');
        const hasMaterial = Boolean(session.learning_material_id);
        if (indicator) {
            indicator.hidden = !hasMaterial;
        }
        button.dataset.hasMaterial = hasMaterial ? 'true' : 'false';
    };

    const renderDetailMeta = (session) => {
        if (!detailMeta) return;
        detailMeta.textContent = '';

        const metaItems = [];
        if (session.detail_meta) {
            metaItems.push({ text: session.detail_meta, warning: false });
        }
        if (session.detail_hint) {
            metaItems.push({ text: session.detail_hint, warning: true });
        }

        metaItems.forEach((item) => {
            const chip = document.createElement('span');
            chip.textContent = item.text;
            if (item.warning) {
                chip.classList.add('is-warning');
            }
            detailMeta.appendChild(chip);
        });
    };

    const renderDetailSummary = (session) => {
        if (!detailSummary) return;

        const cacheKey = String(session.order_index ?? '');
        if (detailSummaryCache.has(cacheKey)) {
            detailSummary.classList.add('md-content');
            detailSummary.innerHTML = detailSummaryCache.get(cacheKey) || '';
            return;
        }

        const markdownSource = String(
            session.detail_content
            || session.detail_summary
            || session.content_preview
            || '',
        ).trim();
        const emptyHtml = '<p class="text-muted">暂无课堂内容。</p>';
        const runtime = window.MarkdownRuntime;

        detailSummary.classList.add('md-content');
        if (runtime && typeof runtime.renderIntoElement === 'function') {
            runtime.renderIntoElement(detailSummary, markdownSource, {
                emptyHtml,
                fallbackMode: 'lines',
                silent: true,
            });
            detailSummaryCache.set(cacheKey, detailSummary.innerHTML);
            return;
        }

        if (!markdownSource) {
            detailSummary.innerHTML = emptyHtml;
            detailSummaryCache.set(cacheKey, detailSummary.innerHTML);
            return;
        }

        detailSummary.innerHTML = String(markdownSource)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/\n/g, '<br>');
        detailSummaryCache.set(cacheKey, detailSummary.innerHTML);
    };

    const renderMaterialPanel = (session) => {
        if (!materialPanel || !materialName || !materialPath) return;
        const hasMaterial = Boolean(session.learning_material_id && session.learning_material_viewer_url);
        materialPanel.classList.toggle('is-empty', !hasMaterial);

        if (hasMaterial) {
            materialName.textContent = session.learning_material_name || '已绑定课堂文档';
            materialPath.textContent = session.learning_material_path || '';
        } else if (isTeacher) {
            materialName.textContent = '尚未绑定课堂文档';
            materialPath.textContent = '可为本次课绑定一份 Markdown 材料，师生可从这里直接进入文档页面。';
        } else {
            materialName.textContent = '教师尚未配置学习文档';
            materialPath.textContent = '当前节点还没有可打开的课堂文档。';
        }

        if (openMaterialBtn) {
            openMaterialBtn.disabled = !hasMaterial;
        }
        if (clearMaterialBtn) {
            clearMaterialBtn.hidden = !hasMaterial;
        }
    };

    const focusSession = (sessionOrder, behavior = 'smooth') => {
        const sessionNode = scrollEl.querySelector(`[data-session-order="${sessionOrder}"]`);
        if (!sessionNode) return;
        sessionNode.scrollIntoView({
            behavior: resolveBehavior(behavior),
            inline: 'center',
            block: 'nearest',
        });
    };

    const syncSelectedState = (activeOrder) => {
        const activeOrderText = String(activeOrder);
        sessionButtons.forEach((button) => {
            const isSelected = button.getAttribute('data-session-order') === activeOrderText;
            button.classList.toggle('is-selected', isSelected);
            button.setAttribute('aria-pressed', isSelected ? 'true' : 'false');
        });
    };

    const setActiveSession = (sessionOrder, options = {}) => {
        const key = String(sessionOrder || '').trim();
        const session = getSessionByOrder(key);
        if (!session) return;

        const previousOrder = selectedOrder;
        selectedOrder = key;
        syncSelectedState(key);
        if (detailKicker) detailKicker.textContent = session.session_number_label || '';
        if (detailTitle) detailTitle.textContent = session.detail_title || session.title || '';
        if (detailStatus) {
            detailStatus.textContent = session.session_status_label || '';
            detailStatus.className = `teaching-timeline-detail-status is-${session.progress_state || 'upcoming'}`;
        }
        renderDetailSummary(session);
        renderDetailMeta(session);
        renderMaterialPanel(session);

        if (options.center !== false && (options.forceCenter || previousOrder !== key)) {
            focusSession(key, options.behavior || 'smooth');
        }
    };

    const applySessionPatch = (patch) => {
        if (!patch) return;
        const session = getSessionByOrder(patch.order_index);
        if (!session) return;
        Object.assign(session, patch, {
            has_learning_material: Boolean(patch.learning_material_id),
        });
        updateSessionButtonMaterialState(session);
        if (String(session.order_index) === selectedOrder) {
            renderMaterialPanel(session);
        }
    };

    const persistSessionMaterial = async (learningMaterialId) => {
        const session = getSessionByOrder(selectedOrder);
        if (!session?.id) return;
        const result = await apiFetch(
            `/api/classrooms/${window.APP_CONFIG.classOfferingId}/sessions/${session.id}/learning-material`,
            {
                method: 'PUT',
                body: { learning_material_id: learningMaterialId },
                silent: true,
            },
        );
        applySessionPatch(result.session);
        if (window.materialsApp && typeof window.materialsApp.refresh === 'function') {
            window.materialsApp.refresh().catch(() => {});
        }
        showToast(result.message || '课堂材料已更新', 'success');
    };

    const getNearestSessionOrder = () => {
        const viewportCenter = scrollEl.scrollLeft + (scrollEl.clientWidth / 2);
        let nearestOrder = selectedOrder;
        let nearestDistance = Number.POSITIVE_INFINITY;

        sessionButtons.forEach((button) => {
            const order = button.getAttribute('data-session-order');
            const buttonCenter = button.offsetLeft + (button.offsetWidth / 2);
            const distance = Math.abs(buttonCenter - viewportCenter);
            if (distance < nearestDistance) {
                nearestDistance = distance;
                nearestOrder = order || nearestOrder;
            }
        });

        return nearestOrder;
    };

    const scheduleSnapToNearest = () => {
        window.clearTimeout(snapTimer);
        snapTimer = window.setTimeout(() => {
            if (!sessionButtons.length) return;
            setActiveSession(getNearestSessionOrder(), {
                center: true,
                behavior: 'smooth',
            });
        }, 110);
    };

    scrollEl.addEventListener('pointerdown', (event) => {
        if (!event.isPrimary || event.button !== 0) return;
        pointerId = event.pointerId;
        startX = event.clientX;
        startScrollLeft = scrollEl.scrollLeft;
        dragDistance = 0;
        scrollEl.classList.add('is-dragging');
        scrollEl.setPointerCapture(event.pointerId);
    });

    scrollEl.addEventListener('pointermove', (event) => {
        if (pointerId !== event.pointerId) return;
        event.preventDefault();
        const deltaX = event.clientX - startX;
        dragDistance = Math.max(dragDistance, Math.abs(deltaX));
        scrollEl.scrollLeft = startScrollLeft - deltaX;
    });

    const releaseDrag = (event) => {
        if (pointerId !== event.pointerId) return;
        const didDrag = dragDistance > 6;
        pointerId = null;
        dragDistance = 0;
        scrollEl.classList.remove('is-dragging');
        if (scrollEl.hasPointerCapture(event.pointerId)) {
            scrollEl.releasePointerCapture(event.pointerId);
        }
        if (didDrag) {
            ignoreClickUntil = Date.now() + 180;
            scheduleSnapToNearest();
        }
    };

    scrollEl.addEventListener('pointerup', releaseDrag);
    scrollEl.addEventListener('pointercancel', releaseDrag);
    scrollEl.addEventListener('pointerleave', (event) => {
        if (pointerId === event.pointerId && event.buttons === 0) {
            releaseDrag(event);
        }
    });

    sessionButtons.forEach((button) => {
        button.addEventListener('click', () => {
            if (Date.now() < ignoreClickUntil) return;
            setActiveSession(button.getAttribute('data-session-order'), {
                center: true,
                behavior: 'smooth',
            });
        });
        button.addEventListener('keydown', (event) => {
            if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') {
                return;
            }
            event.preventDefault();
            const currentIndex = sessions.findIndex((session) => String(session.order_index) === selectedOrder);
            if (currentIndex === -1) return;
            const nextIndex = event.key === 'ArrowRight'
                ? Math.min(currentIndex + 1, sessions.length - 1)
                : Math.max(currentIndex - 1, 0);
            const nextOrder = sessions[nextIndex]?.order_index;
            if (nextOrder != null) {
                setActiveSession(nextOrder, { center: true, behavior: 'smooth' });
                sessionButtons[nextIndex]?.focus();
            }
        });
    });

    scrollEl.addEventListener('scroll', () => {
        if (pointerId !== null) return;
        scheduleSnapToNearest();
    }, { passive: true });

    sessionButtons.forEach((button) => {
        const session = getSessionByOrder(button.getAttribute('data-session-order'));
        updateSessionButtonMaterialState(session);
    });

    openMaterialBtn?.addEventListener('click', () => {
        const session = getSessionByOrder(selectedOrder);
        const viewerUrl = session?.learning_material_viewer_url || '';
        if (!viewerUrl) {
            showToast(isTeacher ? '当前次课还没有绑定文档' : '教师尚未配置学习文档', 'warning');
            return;
        }
        window.open(viewerUrl, '_blank', 'noopener');
    });

    selectMaterialBtn?.addEventListener('click', async () => {
        const session = getSessionByOrder(selectedOrder);
        if (!session) return;
        try {
            const selectedMaterial = await learningMaterialSelector.open({
                title: '选择课堂材料',
                subtitle: '为当前时间轴节点绑定一个 Markdown 文档，课堂内“学习文档”按钮会直接跳转到该页面。',
                confirmLabel: '绑定到本次课',
                initialMaterial: session.learning_material,
            });
            if (!selectedMaterial || Number(selectedMaterial.id) === Number(session.learning_material_id || 0)) {
                return;
            }
            await persistSessionMaterial(Number(selectedMaterial.id));
        } catch (error) {
            showToast(error.message || '更新课堂材料失败', 'error');
        }
    });

    clearMaterialBtn?.addEventListener('click', async () => {
        const session = getSessionByOrder(selectedOrder);
        if (!session?.learning_material_id) {
            showToast('当前次课还没有绑定文档', 'warning');
            return;
        }
        const confirmed = window.confirm('确定移除本次课的学习文档吗？');
        if (!confirmed) return;
        try {
            await persistSessionMaterial(null);
        } catch (error) {
            showToast(error.message || '移除课堂材料失败', 'error');
        }
    });

    window.requestAnimationFrame(() => {
        setActiveSession(selectedOrder, {
            center: true,
            behavior: 'auto',
            forceCenter: true,
        });
    });
}

function resolveCopyTokens(overrides = {}) {
    const userInfo = window.APP_CONFIG?.userInfo || {};
    const classroom = window.APP_CONFIG?.classroom || {};
    const displayName = String(
        overrides.displayName
        || overrides.display_name
        || document.getElementById('chat-display-name')?.textContent
        || '',
    ).trim();
    const userName = String(userInfo.name || '').trim();
    const aliasOrName = displayName && displayName !== '分配中...' ? displayName : userName;

    return {
        name: userName,
        class_name: String(classroom.class_name || '').trim(),
        course_name: String(classroom.course_name || '').trim(),
        alias_or_name: aliasOrName,
    };
}

function applyCopyTokens(template, tokens) {
    return Object.entries(tokens).reduce((current, [key, value]) => {
        return current.split(`{{${key}}}`).join(String(value || ''));
    }, String(template || ''));
}

function personalizeClassroomCopy(overrides = {}) {
    const tokens = resolveCopyTokens(overrides);
    document.querySelectorAll('[data-copy-template]').forEach((node) => {
        const template = node.getAttribute('data-copy-template');
        if (!template) {
            return;
        }
        node.textContent = applyCopyTokens(template, tokens);
    });
}

export function initClassroomPage() {
    initCoursePopover();
    initWorkspaceNav();
    initTeachingTimeline();
    personalizeClassroomCopy();
    document.addEventListener('classroom:alias-change', (event) => {
        personalizeClassroomCopy(event.detail || {});
    });
}
