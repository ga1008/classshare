import { apiFetch } from '/static/js/api.js';
import { initLearningMaterialSelector } from '/static/js/learning_material_selector.js';
import { initSessionMaterialAiAssistant } from '/static/js/session_material_ai_assistant.js';
import { showToast } from '/static/js/ui.js';

const learningMaterialSelector = initLearningMaterialSelector();

function buildLearningViewerUrl(viewerUrl, session = null) {
    const urlText = String(viewerUrl || '').trim();
    if (!urlText) return '';
    try {
        const url = new URL(urlText, window.location.origin);
        const classOfferingId = window.APP_CONFIG?.classOfferingId;
        if (classOfferingId) {
            url.searchParams.set('class_offering_id', String(classOfferingId));
        }
        if (session?.id && !session?.is_home_entry && session?.entry_type !== 'home') {
            url.searchParams.set('session_id', String(session.id));
        }
        return url.pathname + url.search + url.hash;
    } catch {
        return urlText;
    }
}

function initCoursePopover() {
    const popover = document.getElementById('course-info-popover');
    if (!popover) return;

    const overlay = document.getElementById('course-popover-overlay');
    const closeBtn = document.getElementById('course-popover-close');
    const titleEl = document.getElementById('course-popover-title');
    const kickerEl = document.getElementById('course-popover-kicker');
    const triggerButtons = Array.from(document.querySelectorAll('[data-course-popover-target]'));
    const panels = Array.from(popover.querySelectorAll('[data-course-popover-panel]'));
    const popoverCard = popover.querySelector('.course-popover-card');
    const transitionMs = 280;
    let activeTrigger = null;
    let closeTimer = 0;

    const getFocusableElements = () => Array.from(
        popover.querySelectorAll('a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])'),
    ).filter((element) => element.offsetParent !== null || element === document.activeElement);

    const selectPanel = (targetName) => {
        const resolvedTarget = panels.some((panel) => panel.dataset.coursePopoverPanel === targetName)
            ? targetName
            : 'stats';

        panels.forEach((panel) => {
            panel.hidden = panel.dataset.coursePopoverPanel !== resolvedTarget;
        });

        return resolvedTarget;
    };

    const openPopover = (targetName = 'stats', triggerButton = null) => {
        window.clearTimeout(closeTimer);
        const activePanel = selectPanel(targetName);
        activeTrigger = triggerButton || document.activeElement;
        popover.hidden = false;
        popover.setAttribute('aria-hidden', 'false');
        triggerButtons.forEach((button) => {
            const isActiveTrigger = button.dataset.coursePopoverTarget === activePanel && button === triggerButton;
            button.setAttribute('aria-expanded', String(isActiveTrigger));
        });
        if (triggerButton) {
            if (titleEl) {
                titleEl.textContent = triggerButton.dataset.popoverTitle || titleEl.textContent;
            }
            if (kickerEl) {
                kickerEl.textContent = triggerButton.dataset.popoverKicker || kickerEl.textContent;
            }
        }
        document.body.classList.add('has-course-popover');
        window.requestAnimationFrame(() => {
            popover.classList.add('popover-open');
            (closeBtn || popoverCard)?.focus({ preventScroll: true });
        });
    };

    const closePopover = () => {
        popover.classList.remove('popover-open');
        document.body.classList.remove('has-course-popover');
        triggerButtons.forEach((button) => button.setAttribute('aria-expanded', 'false'));
        closeTimer = window.setTimeout(() => {
            if (!popover.classList.contains('popover-open')) {
                popover.hidden = true;
                popover.setAttribute('aria-hidden', 'true');
                activeTrigger?.focus?.({ preventScroll: true });
                activeTrigger = null;
            }
        }, transitionMs);
    };

    triggerButtons.forEach((button) => {
        button.addEventListener('click', (event) => {
            event.stopPropagation();
            openPopover(button.dataset.coursePopoverTarget || 'stats', event.currentTarget);
        });
    });

    overlay?.addEventListener('click', closePopover);
    closeBtn?.addEventListener('click', closePopover);

    document.addEventListener('keydown', (event) => {
        if (!popover.classList.contains('popover-open')) return;

        if (event.key === 'Escape') {
            closePopover();
            return;
        }

        if (event.key !== 'Tab') return;

        const focusableElements = getFocusableElements();
        if (!focusableElements.length) {
            event.preventDefault();
            popoverCard?.focus({ preventScroll: true });
            return;
        }

        const firstFocusable = focusableElements[0];
        const lastFocusable = focusableElements[focusableElements.length - 1];
        if (event.shiftKey && document.activeElement === firstFocusable) {
            event.preventDefault();
            lastFocusable.focus({ preventScroll: true });
        } else if (!event.shiftKey && document.activeElement === lastFocusable) {
            event.preventDefault();
            firstFocusable.focus({ preventScroll: true });
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

function initTeachingTimelineLegacy() {
    const widget = document.getElementById('teaching-plan-widget');
    const scrollEl = document.getElementById('teachingTimelineScroll');
    const teachingPlan = window.APP_CONFIG?.teachingPlan || {};
    const lessonSessions = Array.isArray(teachingPlan.sessions) ? teachingPlan.sessions : [];
    const sessions = Array.isArray(teachingPlan.timeline_entries)
        ? teachingPlan.timeline_entries
        : lessonSessions;
    if (!widget || !scrollEl || !sessions.length) return;

    if (!Array.isArray(teachingPlan.timeline_entries)) {
        teachingPlan.timeline_entries = sessions;
    }
    if (!Array.isArray(teachingPlan.sessions)) {
        teachingPlan.sessions = sessions.filter((session) => !session?.is_home_entry);
    }

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
    const openMaterialHint = document.getElementById('teachingTimelineOpenMaterialHint');
    const openMaterialLabel = document.getElementById('teachingTimelineOpenMaterialLabel');
    const selectHomeMaterialBtn = document.getElementById('teachingTimelineSelectHomeMaterialBtn');
    const selectMaterialBtn = document.getElementById('teachingTimelineSelectMaterialBtn');
    const aiMaterialBtn = document.getElementById('teachingTimelineAiMaterialBtn');
    const clearMaterialBtn = document.getElementById('teachingTimelineClearMaterialBtn');
    const openHomeMaterialBtn = document.getElementById('teachingTimelineOpenHomeMaterialBtn');
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
    const getHomeMaterial = () => teachingPlan.home_material || null;
    const hasHomeMaterial = () => Boolean(getHomeMaterial()?.id && getHomeMaterial()?.viewer_url);
    const isHomeEntry = (session) => Boolean(session?.is_home_entry || session?.entry_type === 'home');
    const getSessionViewerUrl = (session) => String(
        isHomeEntry(session)
            ? session?.home_learning_material_viewer_url || session?.learning_material_viewer_url || ''
            : session?.learning_material_viewer_url || '',
    ).trim();
    const getSessionMaterialReady = (session) => (
        isHomeEntry(session)
            ? Boolean(session?.home_learning_material_id && session?.home_learning_material_viewer_url)
            : Boolean(session?.learning_material_id && session?.learning_material_viewer_url)
    );
    const scheduleProjectionSync = () => {};

    const updateSessionButtonMaterialState = (session) => {
        if (!session) return;
        const button = buttonMap.get(String(session.order_index));
        if (!button) return;
        const orderLabel = button.querySelector('.teaching-timeline-segment-order');
        const titleLabel = button.querySelector('.teaching-timeline-segment-title');
        const metaLabel = button.querySelector('.teaching-timeline-segment-meta');
        const indicator = button.querySelector('[data-role="session-material-indicator"]');
        const hasMaterial = getSessionMaterialReady(session);
        if (orderLabel) orderLabel.textContent = session.session_number_label || '';
        if (titleLabel) titleLabel.textContent = session.segment_title || session.detail_title || session.title || '';
        if (metaLabel) {
            metaLabel.dataset.weekdayLabel = session.timeline_weekday_label || '';
            metaLabel.dataset.relativeDateLabel = session.timeline_relative_date_label || '';
        }
        if (indicator) {
            indicator.textContent = isHomeEntry(session) ? '首页文档' : '学习文档';
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
        const homeEntry = isHomeEntry(session);
        const hasMaterial = getSessionMaterialReady(session);
        const homeReady = hasHomeMaterial();
        materialPanel.classList.toggle('is-empty', !hasMaterial);
        materialPanel.dataset.materialReady = hasMaterial ? 'true' : 'false';
        materialPanel.dataset.entryType = homeEntry ? 'home' : 'lesson';

        if (openMaterialLabel) {
            openMaterialLabel.textContent = homeEntry ? '首页' : '学习文档';
        }
        if (openHomeMaterialBtn) {
            openHomeMaterialBtn.hidden = !homeReady || homeEntry;
            openHomeMaterialBtn.disabled = !homeReady || homeEntry;
            openHomeMaterialBtn.dataset.materialReady = homeReady ? 'true' : 'false';
        }

        if (homeEntry && hasMaterial) {
            materialName.textContent = session.home_learning_material_name || session.learning_material_name || '课程学习首页';
            materialPath.textContent = session.home_learning_material_path || session.learning_material_path || '';
            if (openMaterialHint) {
                openMaterialHint.textContent = '打开课程目录与简介';
            }
        } else if (homeEntry && isTeacher) {
            materialName.textContent = '尚未配置课程首页';
            materialPath.textContent = '可绑定课程首页 Markdown，用于目录、简介和后续文档导航。';
            if (openMaterialHint) {
                openMaterialHint.textContent = '先设置课程首页';
            }
        } else if (homeEntry) {
            materialName.textContent = '教师尚未配置课程首页';
            materialPath.textContent = '当前课堂还没有可打开的课程首页。';
            if (openMaterialHint) {
                openMaterialHint.textContent = '等待教师配置课程首页';
            }
        } else if (hasMaterial) {
            materialName.textContent = session.learning_material_name || '已绑定课堂文档';
            materialPath.textContent = session.learning_material_path || '';
            if (openMaterialHint) {
                openMaterialHint.textContent = '进入本次课学习入口';
            }
        } else if (isTeacher) {
            materialName.textContent = '尚未绑定课堂文档';
            materialPath.textContent = '可为本次课绑定一份 Markdown 材料，师生可从这里直接进入文档页面。';
            if (openMaterialHint) {
                openMaterialHint.textContent = '先为本次课绑定文档';
            }
        } else {
            materialName.textContent = '教师尚未配置学习文档';
            materialPath.textContent = '当前节点还没有可打开的课堂文档。';
            if (openMaterialHint) {
                openMaterialHint.textContent = '等待教师配置学习文档';
            }
        }

        if (openMaterialBtn) {
            openMaterialBtn.disabled = !hasMaterial;
            openMaterialBtn.dataset.materialReady = hasMaterial ? 'true' : 'false';
        }
        if (clearMaterialBtn) {
            clearMaterialBtn.hidden = !hasMaterial || homeEntry;
        }
    };

    const syncTeacherActionState = (session) => {
        const homeEntry = isHomeEntry(session);
        if (selectMaterialBtn) {
            selectMaterialBtn.hidden = homeEntry;
            selectMaterialBtn.disabled = homeEntry;
        }
        if (aiMaterialBtn) {
            aiMaterialBtn.hidden = homeEntry;
            aiMaterialBtn.disabled = homeEntry || Boolean(session?.material_generation_task?.is_active);
        }
        if (selectHomeMaterialBtn) {
            selectHomeMaterialBtn.textContent = hasHomeMaterial() ? '更换首页' : '设置首页';
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

    const applyHomeMaterialPatch = (result = {}) => {
        teachingPlan.home_material = result.home_material || null;
        teachingPlan.has_home_material = Boolean(result.home_material);
        if (result.home_entry) {
            const homeEntry = getSessionByOrder('home');
            if (homeEntry) {
                Object.assign(homeEntry, result.home_entry);
                updateSessionButtonMaterialState(homeEntry);
                if (String(homeEntry.order_index) === selectedOrder) {
                    if (detailKicker) detailKicker.textContent = homeEntry.session_number_label || '';
                    if (detailTitle) detailTitle.textContent = homeEntry.detail_title || homeEntry.title || '';
                    if (detailStatus) {
                        detailStatus.textContent = homeEntry.session_status_label || '';
                        detailStatus.className = `teaching-timeline-detail-status is-${homeEntry.progress_state || 'home'}`;
                    }
                    renderDetailSummary(homeEntry);
                    renderDetailMeta(homeEntry);
                    renderMaterialPanel(homeEntry);
                }
            }
        }
        const currentSession = getSessionByOrder(selectedOrder);
        syncTeacherActionState(currentSession);
        renderMaterialPanel(currentSession);
        scheduleProjectionSync();
    };

    const persistHomeMaterial = async (learningMaterialId) => {
        const result = await apiFetch(
            `/api/classrooms/${window.APP_CONFIG.classOfferingId}/learning-home-material`,
            {
                method: 'PUT',
                body: { learning_material_id: learningMaterialId },
                silent: true,
            },
        );
        applyHomeMaterialPatch(result);
        if (window.materialsApp && typeof window.materialsApp.refresh === 'function') {
            window.materialsApp.refresh().catch(() => {});
        }
        showToast(result.message || '课程首页已更新', 'success');
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
        const viewerUrl = getSessionViewerUrl(session);
        if (!viewerUrl) {
            if (isHomeEntry(session)) {
                showToast(isTeacher ? '课程首页尚未配置' : '教师尚未配置课程首页', 'warning');
            } else {
                showToast(isTeacher ? '当前次课还没有绑定文档' : '教师尚未配置学习文档', 'warning');
            }
            return;
        }
        window.open(buildLearningViewerUrl(viewerUrl, session), '_blank', 'noopener');
    });

    openHomeMaterialBtn?.addEventListener('click', () => {
        const homeMaterial = getHomeMaterial();
        const viewerUrl = String(homeMaterial?.viewer_url || '').trim();
        if (!viewerUrl) {
            showToast(isTeacher ? '课程首页尚未配置' : '教师尚未配置课程首页', 'warning');
            return;
        }
        window.open(buildLearningViewerUrl(viewerUrl, { is_home_entry: true }), '_blank', 'noopener');
    });

    selectHomeMaterialBtn?.addEventListener('click', async () => {
        try {
            const currentHomeMaterial = getHomeMaterial();
            const selectedMaterial = await learningMaterialSelector.open({
                title: '选择课程首页',
                subtitle: '首页用于课程目录、简介和后续学习文档导航，会显示在时间轴第一课之前。',
                confirmLabel: currentHomeMaterial ? '更换首页' : '设置为首页',
                allowClear: Boolean(currentHomeMaterial),
                clearLabel: '移除课程首页',
                footerNote: currentHomeMaterial
                    ? '选择新的 Markdown 文档可替换首页，也可以移除当前首页入口。'
                    : '仅支持绑定 Markdown 文档。建议选择根目录下的 README、index 或课程目录文档。',
                initialMaterial: currentHomeMaterial,
            });
            if (!selectedMaterial) {
                return;
            }
            if (selectedMaterial.clear) {
                await persistHomeMaterial(null);
                return;
            }
            if (Number(selectedMaterial.id) === Number(currentHomeMaterial?.id || 0)) {
                return;
            }
            await persistHomeMaterial(Number(selectedMaterial.id));
        } catch (error) {
            showToast(error.message || '更新课程首页失败', 'error');
        }
    });

    selectMaterialBtn?.addEventListener('click', async () => {
        const session = getSessionByOrder(selectedOrder);
        if (!session || isHomeEntry(session)) return;
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

function initTeachingTimeline() {
    const widget = document.getElementById('teaching-plan-widget');
    const stageEl = document.getElementById('teachingTimelineStage');
    const scrollEl = document.getElementById('teachingTimelineScroll');
    const prevTimelineBtn = document.getElementById('teachingTimelinePrevBtn');
    const nextTimelineBtn = document.getElementById('teachingTimelineNextBtn');
    const teachingPlan = window.APP_CONFIG?.teachingPlan || {};
    const lessonSessions = Array.isArray(teachingPlan.sessions) ? teachingPlan.sessions : [];
    const sessions = Array.isArray(teachingPlan.timeline_entries)
        ? teachingPlan.timeline_entries
        : lessonSessions;
    if (!widget || !scrollEl || !sessions.length) return;

    if (!Array.isArray(teachingPlan.timeline_entries)) {
        teachingPlan.timeline_entries = sessions;
    }
    if (!Array.isArray(teachingPlan.sessions)) {
        teachingPlan.sessions = sessions.filter((session) => !session?.is_home_entry);
    }

    const userInfo = window.APP_CONFIG?.userInfo || {};
    const isTeacher = String(userInfo.role || '').trim() === 'teacher';
    const detailCard = document.getElementById('teachingTimelineDetail');
    const detailKicker = document.getElementById('teachingTimelineDetailKicker');
    const detailTitle = document.getElementById('teachingTimelineDetailTitle');
    const detailStatus = document.getElementById('teachingTimelineDetailStatus');
    const detailSummary = document.getElementById('teachingTimelineDetailSummary');
    const detailMeta = document.getElementById('teachingTimelineDetailMeta');
    const materialPanel = document.getElementById('teachingTimelineMaterialPanel');
    const materialName = document.getElementById('teachingTimelineMaterialName');
    const materialPath = document.getElementById('teachingTimelineMaterialPath');
    const openMaterialHint = document.getElementById('teachingTimelineOpenMaterialHint');
    const openMaterialLabel = document.getElementById('teachingTimelineOpenMaterialLabel');
    const selectHomeMaterialBtn = document.getElementById('teachingTimelineSelectHomeMaterialBtn');
    const selectMaterialBtn = document.getElementById('teachingTimelineSelectMaterialBtn');
    const aiMaterialBtn = document.getElementById('teachingTimelineAiMaterialBtn');
    const openHomeMaterialBtn = document.getElementById('teachingTimelineOpenHomeMaterialBtn');
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
    let lastPointerTime = 0;
    let scrollVelocity = 0;
    let dragDistance = 0;
    let dragTargetScrollLeft = 0;
    let lastDragTargetScrollLeft = 0;
    let snapTimer = 0;
    let motionFrame = 0;
    let motionMode = 'idle';
    let suppressSnapUntil = 0;
    let ignoreClickUntil = 0;
    let projectionFrame = 0;
    let cardMotionFrame = 0;
    let detailTransitionTimer = 0;
    let sessionMaterialAssistant = null;

    const getSessionByOrder = (sessionOrder) => sessionMap.get(String(sessionOrder || '').trim());
    const getHomeMaterial = () => teachingPlan.home_material || null;
    const hasHomeMaterial = () => Boolean(getHomeMaterial()?.id && getHomeMaterial()?.viewer_url);
    const isHomeEntry = (session) => Boolean(session?.is_home_entry || session?.entry_type === 'home');
    const getSessionViewerUrl = (session) => String(
        isHomeEntry(session)
            ? session?.home_learning_material_viewer_url || session?.learning_material_viewer_url || ''
            : session?.learning_material_viewer_url || '',
    ).trim();
    const getSessionMaterialReady = (session) => (
        isHomeEntry(session)
            ? Boolean(session?.home_learning_material_id && session?.home_learning_material_viewer_url)
            : Boolean(session?.learning_material_id && session?.learning_material_viewer_url)
    );
    const getMaxScrollLeft = () => Math.max(0, scrollEl.scrollWidth - scrollEl.clientWidth);
    const clampScrollLeft = (value) => Math.max(0, Math.min(getMaxScrollLeft(), Number(value) || 0));
    const setTimelineScrollLeft = (value) => {
        scrollEl.scrollLeft = clampScrollLeft(value);
        scheduleProjectionSync();
        scheduleCardMotionSync();
    };
    const getSessionCenterScrollLeft = (sessionOrder) => {
        const button = buttonMap.get(String(sessionOrder || '').trim());
        if (!button) return scrollEl.scrollLeft;
        return clampScrollLeft(button.offsetLeft + (button.offsetWidth / 2) - (scrollEl.clientWidth / 2));
    };
    const getSelectedIndex = () => sessions.findIndex((session) => String(session.order_index) === selectedOrder);
    const stopTimelineMotion = () => {
        window.clearTimeout(snapTimer);
        snapTimer = 0;
        if (motionFrame) {
            window.cancelAnimationFrame(motionFrame);
            motionFrame = 0;
        }
        motionMode = 'idle';
        scrollEl.classList.remove('is-settling');
    };

    const updateSessionButtonMaterialState = (session) => {
        if (!session) return;
        const button = buttonMap.get(String(session.order_index));
        if (!button) return;
        const orderLabel = button.querySelector('.teaching-timeline-segment-order');
        const titleLabel = button.querySelector('.teaching-timeline-segment-title');
        const metaLabel = button.querySelector('.teaching-timeline-segment-meta');
        const indicator = button.querySelector('[data-role="session-material-indicator"]');
        const hasMaterial = getSessionMaterialReady(session);
        if (orderLabel) orderLabel.textContent = session.session_number_label || '';
        if (titleLabel) titleLabel.textContent = session.segment_title || session.detail_title || session.title || '';
        if (metaLabel) {
            metaLabel.dataset.weekdayLabel = session.timeline_weekday_label || '';
            metaLabel.dataset.relativeDateLabel = session.timeline_relative_date_label || '';
        }
        if (indicator) {
            indicator.textContent = isHomeEntry(session) ? '首页文档' : '学习文档';
            indicator.hidden = !hasMaterial;
        }
        button.dataset.hasMaterial = hasMaterial ? 'true' : 'false';
    };

    const syncProjection = () => {
        if (!stageEl || !detailCard) return;
        const activeButton = buttonMap.get(selectedOrder);
        if (!activeButton) return;

        const stageRect = stageEl.getBoundingClientRect();
        const buttonRect = activeButton.getBoundingClientRect();
        const detailRect = detailCard.getBoundingClientRect();
        const beamLeft = Math.max(
            28,
            Math.min(stageRect.width - 28, buttonRect.left - stageRect.left + (buttonRect.width / 2)),
        );
        const beamTop = Math.max(0, buttonRect.bottom - stageRect.top + 10);
        const beamHeight = Math.max(0, detailRect.top - buttonRect.bottom - 18);
        const detailLocalLeft = Math.max(
            42,
            Math.min(detailRect.width - 42, beamLeft - (detailRect.left - stageRect.left)),
        );

        stageEl.style.setProperty('--timeline-projector-left', `${beamLeft.toFixed(2)}px`);
        stageEl.style.setProperty('--timeline-projector-top', `${beamTop.toFixed(2)}px`);
        stageEl.style.setProperty('--timeline-projector-height', `${beamHeight.toFixed(2)}px`);
        detailCard.style.setProperty('--timeline-projector-local-left', `${detailLocalLeft.toFixed(2)}px`);
    };

    const scheduleProjectionSync = () => {
        if (projectionFrame) return;
        projectionFrame = window.requestAnimationFrame(() => {
            projectionFrame = 0;
            syncProjection();
        });
    };

    const syncCardMotion = () => {
        if (!sessionButtons.length) return;
        const viewportCenter = scrollEl.scrollLeft + (scrollEl.clientWidth / 2);
        const baseWidth = sessionButtons[0]?.offsetWidth || 180;
        const influenceWidth = Math.max(150, baseWidth * 0.86);

        sessionButtons.forEach((button) => {
            const buttonCenter = button.offsetLeft + (button.offsetWidth / 2);
            const distance = Math.abs(buttonCenter - viewportCenter);
            const rawFocus = Math.max(0, 1 - (distance / influenceWidth));
            const focus = rawFocus * rawFocus * (3 - (2 * rawFocus));
            button.style.setProperty('--timeline-card-focus', focus.toFixed(3));
            button.style.setProperty('--timeline-card-lift', `${(-2.4 * focus).toFixed(2)}px`);
            button.style.setProperty('--timeline-card-scale', (1 + (0.022 * focus)).toFixed(4));
        });
    };

    const scheduleCardMotionSync = () => {
        if (cardMotionFrame) return;
        cardMotionFrame = window.requestAnimationFrame(() => {
            cardMotionFrame = 0;
            syncCardMotion();
        });
    };

    const playDetailTransition = () => {
        if (!detailCard || prefersReducedMotion) return;
        window.clearTimeout(detailTransitionTimer);
        detailCard.classList.remove('is-switching');
        detailCard.offsetWidth;
        detailCard.classList.add('is-switching');
        detailTransitionTimer = window.setTimeout(() => {
            detailCard.classList.remove('is-switching');
        }, 280);
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
        const homeEntry = isHomeEntry(session);
        const hasMaterial = getSessionMaterialReady(session);
        const homeReady = hasHomeMaterial();
        materialPanel.classList.toggle('is-empty', !hasMaterial);
        materialPanel.dataset.materialReady = hasMaterial ? 'true' : 'false';
        materialPanel.dataset.entryType = homeEntry ? 'home' : 'lesson';

        if (openMaterialLabel) {
            openMaterialLabel.textContent = homeEntry ? '首页' : '学习文档';
        }
        if (openHomeMaterialBtn) {
            openHomeMaterialBtn.hidden = !homeReady || homeEntry;
            openHomeMaterialBtn.disabled = !homeReady || homeEntry;
            openHomeMaterialBtn.dataset.materialReady = homeReady ? 'true' : 'false';
        }

        if (homeEntry && hasMaterial) {
            materialName.textContent = session.home_learning_material_name || session.learning_material_name || '课程学习首页';
            materialPath.textContent = session.home_learning_material_path || session.learning_material_path || '';
            if (openMaterialHint) {
                openMaterialHint.textContent = '打开课程目录与简介';
            }
        } else if (homeEntry && isTeacher) {
            materialName.textContent = '尚未配置课程首页';
            materialPath.textContent = '可绑定课程首页 Markdown，用于目录、简介和后续文档导航。';
            if (openMaterialHint) {
                openMaterialHint.textContent = '先设置课程首页';
            }
        } else if (homeEntry) {
            materialName.textContent = '教师尚未配置课程首页';
            materialPath.textContent = '当前课堂还没有可打开的课程首页。';
            if (openMaterialHint) {
                openMaterialHint.textContent = '等待教师配置课程首页';
            }
        } else if (hasMaterial) {
            materialName.textContent = session.learning_material_name || '已绑定课堂文档';
            materialPath.textContent = session.learning_material_path || '';
            if (openMaterialHint) {
                openMaterialHint.textContent = '进入本次课学习入口';
            }
        } else if (isTeacher) {
            materialName.textContent = '尚未绑定课堂文档';
            materialPath.textContent = '可为本次课绑定一份 Markdown 材料，师生可从这里直接进入文档页面。';
            if (openMaterialHint) {
                openMaterialHint.textContent = '先为本次课绑定文档';
            }
        } else {
            materialName.textContent = '教师尚未配置学习文档';
            materialPath.textContent = '当前节点还没有可打开的课堂文档。';
            if (openMaterialHint) {
                openMaterialHint.textContent = '等待教师配置学习文档';
            }
        }

        if (openMaterialBtn) {
            openMaterialBtn.disabled = !hasMaterial;
            openMaterialBtn.dataset.materialReady = hasMaterial ? 'true' : 'false';
        }
    };

    const syncTeacherActionState = (session) => {
        const homeEntry = isHomeEntry(session);
        if (selectMaterialBtn) {
            selectMaterialBtn.hidden = homeEntry;
            selectMaterialBtn.disabled = homeEntry;
        }
        if (aiMaterialBtn) {
            aiMaterialBtn.hidden = homeEntry;
            aiMaterialBtn.disabled = homeEntry || Boolean(session?.material_generation_task?.is_active);
        }
        if (selectHomeMaterialBtn) {
            selectHomeMaterialBtn.textContent = hasHomeMaterial() ? '更换首页' : '设置首页';
        }
    };

    const animateToScrollLeft = (targetScrollLeft, options = {}) => {
        const target = clampScrollLeft(targetScrollLeft);
        if (prefersReducedMotion || options.behavior === 'auto') {
            stopTimelineMotion();
            suppressSnapUntil = performance.now() + 220;
            setTimelineScrollLeft(target);
            options.onComplete?.();
            return;
        }

        stopTimelineMotion();
        motionMode = 'snap';
        scrollEl.classList.add('is-settling');
        let velocity = 0;
        let lastTimestamp = 0;
        const stiffness = Number(options.stiffness || 0.28);
        const damping = Number(options.damping || 0.64);

        const step = (timestamp) => {
            if (!lastTimestamp) lastTimestamp = timestamp;
            const dtScale = Math.min(2.1, Math.max(0.55, (timestamp - lastTimestamp) / 16.67));
            lastTimestamp = timestamp;

            const current = scrollEl.scrollLeft;
            const distance = target - current;
            velocity = (velocity + distance * stiffness * dtScale) * Math.pow(damping, dtScale);
            setTimelineScrollLeft(current + velocity * dtScale);

            if (Math.abs(distance) < 0.45 && Math.abs(velocity) < 0.16) {
                setTimelineScrollLeft(target);
                motionFrame = 0;
                motionMode = 'idle';
                scrollEl.classList.remove('is-settling');
                options.onComplete?.();
                return;
            }
            motionFrame = window.requestAnimationFrame(step);
        };

        motionFrame = window.requestAnimationFrame(step);
    };

    const focusSession = (sessionOrder, behavior = 'smooth') => {
        animateToScrollLeft(getSessionCenterScrollLeft(sessionOrder), {
            behavior: resolveBehavior(behavior),
            stiffness: behavior === 'gear' ? 0.34 : 0.24,
            damping: behavior === 'gear' ? 0.58 : 0.66,
            onComplete: scheduleProjectionSync,
        });
    };

    const updateTimelineControls = () => {
        const currentIndex = getSelectedIndex();
        if (prevTimelineBtn) prevTimelineBtn.disabled = currentIndex <= 0;
        if (nextTimelineBtn) nextTimelineBtn.disabled = currentIndex < 0 || currentIndex >= sessions.length - 1;
    };

    const syncSelectedState = (activeOrder) => {
        const activeOrderText = String(activeOrder);
        sessionButtons.forEach((button) => {
            const isSelected = button.getAttribute('data-session-order') === activeOrderText;
            button.classList.toggle('is-selected', isSelected);
            button.setAttribute('aria-pressed', isSelected ? 'true' : 'false');
        });
        updateTimelineControls();
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
        if (previousOrder && previousOrder !== key) {
            playDetailTransition();
        }
        renderDetailSummary(session);
        renderDetailMeta(session);
        renderMaterialPanel(session);
        syncTeacherActionState(session);
        if (isHomeEntry(session)) {
            sessionMaterialAssistant?.syncSelectedSession(null);
        } else {
            sessionMaterialAssistant?.syncSelectedSession(session);
        }

        if (options.center !== false && (options.forceCenter || previousOrder !== key)) {
            focusSession(key, options.behavior || 'smooth');
        }
        scheduleProjectionSync();
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
            sessionMaterialAssistant?.syncSelectedSession(session);
        }
        scheduleProjectionSync();
    };

    if (isTeacher) {
        sessionMaterialAssistant = initSessionMaterialAiAssistant({
            classOfferingId: window.APP_CONFIG.classOfferingId,
            getSessions: () => teachingPlan.sessions || [],
            getCurrentSession: () => {
                const session = getSessionByOrder(selectedOrder);
                return isHomeEntry(session) ? null : session;
            },
            onSessionPatch: applySessionPatch,
        });
    }

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

    const applyHomeMaterialPatch = (result = {}) => {
        teachingPlan.home_material = result.home_material || null;
        teachingPlan.has_home_material = Boolean(result.home_material);
        if (result.home_entry) {
            const homeEntry = getSessionByOrder('home');
            if (homeEntry) {
                Object.assign(homeEntry, result.home_entry);
                updateSessionButtonMaterialState(homeEntry);
                if (String(homeEntry.order_index) === selectedOrder) {
                    if (detailKicker) detailKicker.textContent = homeEntry.session_number_label || '';
                    if (detailTitle) detailTitle.textContent = homeEntry.detail_title || homeEntry.title || '';
                    if (detailStatus) {
                        detailStatus.textContent = homeEntry.session_status_label || '';
                        detailStatus.className = `teaching-timeline-detail-status is-${homeEntry.progress_state || 'home'}`;
                    }
                    renderDetailSummary(homeEntry);
                    renderDetailMeta(homeEntry);
                    renderMaterialPanel(homeEntry);
                }
            }
        }
        const currentSession = getSessionByOrder(selectedOrder);
        syncTeacherActionState(currentSession);
        renderMaterialPanel(currentSession);
        sessionMaterialAssistant?.syncSelectedSession(isHomeEntry(currentSession) ? null : currentSession);
        scheduleProjectionSync();
        scheduleCardMotionSync();
    };

    const persistHomeMaterial = async (learningMaterialId) => {
        const result = await apiFetch(
            `/api/classrooms/${window.APP_CONFIG.classOfferingId}/learning-home-material`,
            {
                method: 'PUT',
                body: { learning_material_id: learningMaterialId },
                silent: true,
            },
        );
        applyHomeMaterialPatch(result);
        if (window.materialsApp && typeof window.materialsApp.refresh === 'function') {
            window.materialsApp.refresh().catch(() => {});
        }
        showToast(result.message || '课程首页已更新', 'success');
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

    const snapToNearest = (behavior = 'gear') => {
        if (!sessionButtons.length) return;
        setActiveSession(getNearestSessionOrder(), {
            center: true,
            behavior,
            forceCenter: true,
        });
    };

    const scheduleSnapToNearest = (delay = 140) => {
        window.clearTimeout(snapTimer);
        snapTimer = window.setTimeout(() => {
            snapToNearest();
        }, delay);
    };

    const startInertia = (initialVelocity) => {
        stopTimelineMotion();
        const maxScrollLeft = getMaxScrollLeft();
        const hasRoomToMove = maxScrollLeft > 0;
        if (prefersReducedMotion || !hasRoomToMove || Math.abs(initialVelocity) < 0.08) {
            snapToNearest();
            return;
        }

        motionMode = 'inertia';
        scrollEl.classList.add('is-settling');
        let velocity = Math.max(-4.2, Math.min(4.2, initialVelocity));
        let lastTimestamp = 0;

        const step = (timestamp) => {
            if (!lastTimestamp) lastTimestamp = timestamp;
            const dtScale = Math.min(2.2, Math.max(0.45, (timestamp - lastTimestamp) / 16.67));
            lastTimestamp = timestamp;

            const current = scrollEl.scrollLeft;
            const next = clampScrollLeft(current + velocity * dtScale);
            const hitEdge = next <= 0 || next >= maxScrollLeft;
            setTimelineScrollLeft(next);

            velocity *= Math.pow(hitEdge ? 0.68 : 0.885, dtScale);
            if (Math.abs(velocity) < 0.16 || Math.abs(next - current) < 0.08) {
                motionFrame = 0;
                motionMode = 'idle';
                scrollEl.classList.remove('is-settling');
                snapToNearest();
                return;
            }

            motionFrame = window.requestAnimationFrame(step);
        };

        motionFrame = window.requestAnimationFrame(step);
    };

    const startDragFollow = () => {
        if (motionMode !== 'drag' || motionFrame) return;

        const step = () => {
            if (motionMode !== 'drag') {
                motionFrame = 0;
                return;
            }

            const current = scrollEl.scrollLeft;
            const distance = dragTargetScrollLeft - current;
            if (Math.abs(distance) < 0.35) {
                setTimelineScrollLeft(dragTargetScrollLeft);
                motionFrame = 0;
                return;
            } else {
                setTimelineScrollLeft(current + (distance * 0.72));
            }

            motionFrame = window.requestAnimationFrame(step);
        };

        motionFrame = window.requestAnimationFrame(step);
    };

    scrollEl.addEventListener('pointerdown', (event) => {
        if (!event.isPrimary || event.button !== 0) return;
        stopTimelineMotion();
        pointerId = event.pointerId;
        startX = event.clientX;
        startScrollLeft = scrollEl.scrollLeft;
        dragTargetScrollLeft = startScrollLeft;
        lastDragTargetScrollLeft = startScrollLeft;
        lastPointerTime = event.timeStamp || performance.now();
        scrollVelocity = 0;
        dragDistance = 0;
        motionMode = 'drag';
        scrollEl.classList.add('is-dragging');
        scrollEl.setPointerCapture(event.pointerId);
    });

    scrollEl.addEventListener('pointermove', (event) => {
        if (pointerId !== event.pointerId) return;
        event.preventDefault();
        const deltaX = event.clientX - startX;
        const timestamp = event.timeStamp || performance.now();
        const elapsed = Math.max(8, timestamp - lastPointerTime);
        const nextScrollLeft = clampScrollLeft(startScrollLeft - deltaX);
        dragDistance = Math.max(dragDistance, Math.abs(deltaX));
        scrollVelocity = (scrollVelocity * 0.62) + (((nextScrollLeft - lastDragTargetScrollLeft) / elapsed) * 16.67 * 0.38);
        lastDragTargetScrollLeft = nextScrollLeft;
        dragTargetScrollLeft = nextScrollLeft;
        lastPointerTime = timestamp;
        startDragFollow();
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
            startInertia(scrollVelocity + ((dragTargetScrollLeft - scrollEl.scrollLeft) * 0.18));
        } else {
            stopTimelineMotion();
        }
        scrollVelocity = 0;
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
                behavior: 'gear',
                forceCenter: true,
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
                setActiveSession(nextOrder, { center: true, behavior: 'gear' });
                sessionButtons[nextIndex]?.focus();
            }
        });
    });

    const shiftActiveSession = (direction) => {
        const currentIndex = getSelectedIndex();
        if (currentIndex === -1) return;
        const nextIndex = Math.max(0, Math.min(sessions.length - 1, currentIndex + direction));
        if (nextIndex === currentIndex) return;
        const nextOrder = sessions[nextIndex]?.order_index;
        if (nextOrder == null) return;
        setActiveSession(nextOrder, {
            center: true,
            behavior: 'gear',
            forceCenter: true,
        });
        sessionButtons[nextIndex]?.focus({ preventScroll: true });
    };

    prevTimelineBtn?.addEventListener('click', () => shiftActiveSession(-1));
    nextTimelineBtn?.addEventListener('click', () => shiftActiveSession(1));

    scrollEl.addEventListener('scroll', () => {
        scheduleProjectionSync();
        scheduleCardMotionSync();
        if (pointerId !== null || motionMode !== 'idle' || performance.now() < suppressSnapUntil) return;
        scheduleSnapToNearest(180);
    }, { passive: true });

    window.addEventListener('resize', () => {
        scheduleProjectionSync();
        scheduleCardMotionSync();
        if (motionMode === 'idle') {
            window.clearTimeout(snapTimer);
            snapTimer = window.setTimeout(() => {
                focusSession(selectedOrder, 'auto');
            }, 120);
        }
    });

    sessionButtons.forEach((button) => {
        const session = getSessionByOrder(button.getAttribute('data-session-order'));
        updateSessionButtonMaterialState(session);
    });

    openMaterialBtn?.addEventListener('click', () => {
        const session = getSessionByOrder(selectedOrder);
        const viewerUrl = getSessionViewerUrl(session);
        if (!viewerUrl) {
            if (isHomeEntry(session)) {
                showToast(isTeacher ? '课程首页尚未配置' : '教师尚未配置课程首页', 'warning');
            } else {
                showToast(isTeacher ? '当前次课还没有绑定文档' : '教师尚未配置学习文档', 'warning');
            }
            return;
        }
        window.open(buildLearningViewerUrl(viewerUrl, session), '_blank', 'noopener');
    });

    openHomeMaterialBtn?.addEventListener('click', () => {
        const homeMaterial = getHomeMaterial();
        const viewerUrl = String(homeMaterial?.viewer_url || '').trim();
        if (!viewerUrl) {
            showToast(isTeacher ? '课程首页尚未配置' : '教师尚未配置课程首页', 'warning');
            return;
        }
        window.open(buildLearningViewerUrl(viewerUrl, { is_home_entry: true }), '_blank', 'noopener');
    });

    selectHomeMaterialBtn?.addEventListener('click', async () => {
        try {
            const currentHomeMaterial = getHomeMaterial();
            const selectedMaterial = await learningMaterialSelector.open({
                title: '选择课程首页',
                subtitle: '首页用于课程目录、简介和后续学习文档导航，会显示在时间轴第一课之前。',
                confirmLabel: currentHomeMaterial ? '更换首页' : '设置为首页',
                allowClear: Boolean(currentHomeMaterial),
                clearLabel: '移除课程首页',
                footerNote: currentHomeMaterial
                    ? '选择新的 Markdown 文档可替换首页，也可以移除当前首页入口。'
                    : '仅支持绑定 Markdown 文档。建议选择根目录下的 README、index 或课程目录文档。',
                initialMaterial: currentHomeMaterial,
            });
            if (!selectedMaterial) {
                return;
            }
            if (selectedMaterial.clear) {
                await persistHomeMaterial(null);
                return;
            }
            if (Number(selectedMaterial.id) === Number(currentHomeMaterial?.id || 0)) {
                return;
            }
            await persistHomeMaterial(Number(selectedMaterial.id));
        } catch (error) {
            showToast(error.message || '更新课程首页失败', 'error');
        }
    });

    selectMaterialBtn?.addEventListener('click', async () => {
        const session = getSessionByOrder(selectedOrder);
        if (!session || isHomeEntry(session)) return;
        try {
            const selectedMaterial = await learningMaterialSelector.open({
                title: '选择课堂材料',
                subtitle: '为当前时间轴节点绑定一个 Markdown 文档，课堂内“学习文档”按钮会直接跳转到该页面。',
                confirmLabel: '绑定到本次课',
                allowClear: Boolean(session.learning_material_id),
                clearLabel: '解绑当前文档',
                footerNote: session.learning_material_id
                    ? '单击文件选中，双击文件夹继续进入；如需解绑当前文档，可直接点“解绑当前文档”。'
                    : '仅支持绑定 Markdown 文档。单击文件选中，双击文件夹继续进入。',
                initialMaterial: session.learning_material,
            });
            if (!selectedMaterial) {
                return;
            }
            if (selectedMaterial.clear) {
                await persistSessionMaterial(null);
                return;
            }
            if (Number(selectedMaterial.id) === Number(session.learning_material_id || 0)) {
                return;
            }
            await persistSessionMaterial(Number(selectedMaterial.id));
        } catch (error) {
            showToast(error.message || '更新课堂材料失败', 'error');
        }
    });

    aiMaterialBtn?.addEventListener('click', () => {
        sessionMaterialAssistant?.openForCurrentSession();
    });

    window.requestAnimationFrame(() => {
        setActiveSession(selectedOrder, {
            center: true,
            behavior: 'auto',
            forceCenter: true,
        });
        const selectedSession = getSessionByOrder(selectedOrder);
        sessionMaterialAssistant?.syncSelectedSession(isHomeEntry(selectedSession) ? null : selectedSession);
        sessionMaterialAssistant?.startPolling();
        window.requestAnimationFrame(scheduleProjectionSync);
    });
}

const TODO_TONE_LABELS = {
    lesson: '课程',
    assignment: '作业',
    exam: '考试',
    stage: '试炼',
    manual: '自定义',
    neutral: '待办',
};

function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function parseDateKey(value) {
    const text = String(value || '').slice(0, 10);
    const match = text.match(/^(\d{4})-(\d{2})-(\d{2})$/);
    if (!match) return null;
    return new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
}

function formatDateKey(date) {
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, '0');
    const day = String(date.getDate()).padStart(2, '0');
    return `${year}-${month}-${day}`;
}

function addDays(date, days) {
    const next = new Date(date.getFullYear(), date.getMonth(), date.getDate());
    next.setDate(next.getDate() + days);
    return next;
}

function eachDateKey(startKey, endKey) {
    const start = parseDateKey(startKey);
    const end = parseDateKey(endKey || startKey);
    if (!start || !end) return [];
    const from = start <= end ? start : end;
    const to = start <= end ? end : start;
    const keys = [];
    for (let cursor = from; cursor <= to; cursor = addDays(cursor, 1)) {
        keys.push(formatDateKey(cursor));
    }
    return keys;
}

function monthTitle(date) {
    return `${date.getFullYear()}年${date.getMonth() + 1}月`;
}

function monthDayLabel(dateKey) {
    const date = parseDateKey(dateKey);
    return date ? `${date.getMonth() + 1}月${date.getDate()}日` : '';
}

function composeDateTime(dateKey, timeValue, fallbackTime) {
    if (!dateKey) return null;
    const timeText = String(timeValue || fallbackTime || '').trim();
    const resolvedTime = /^\d{2}:\d{2}$/.test(timeText) ? timeText : '00:00';
    return `${dateKey}T${resolvedTime}`;
}

function initSemesterTodoBoard(config = window.APP_CONFIG || {}) {
    const panel = document.getElementById('semesterTodoPanel');
    const weeksEl = document.getElementById('semesterTodoWeeks');
    const scrollEl = document.getElementById('semesterTodoScroll');
    const summaryEl = document.getElementById('semesterTodoSummary');
    if (!panel || !weeksEl || !scrollEl) return;

    const addBtn = document.getElementById('semesterTodoAddBtn');
    const modal = document.getElementById('semesterTodoModal');
    const modalClose = document.getElementById('semesterTodoModalClose');
    const modalCancel = document.getElementById('semesterTodoModalCancel');
    const form = document.getElementById('semesterTodoForm');
    const pickerTitle = document.getElementById('semesterTodoPickerTitle');
    const pickerGrid = document.getElementById('semesterTodoPickerGrid');
    const pickerResult = document.getElementById('semesterTodoDateResult');
    const roleTabs = Array.from(document.querySelectorAll('[data-date-role]'));
    const classOfferingId = config.classOfferingId;
    let overview = config.todoOverview || { weeks: [], summary: {}, role_policy: {} };
    let activeTodoId = '';
    let pickerMonth = new Date();
    let selectedStartDate = '';
    let selectedDueDate = '';
    let selectedRole = 'due';

    const sourceLabel = (todo) => TODO_TONE_LABELS[todo?.tone] || TODO_TONE_LABELS[todo?.source_type] || '待办';
    const manualTodoId = (todo) => Number(todo?.source_id || String(todo?.id || '').split(':').pop() || 0);
    const cssEscapeValue = (value) => (
        window.CSS && typeof window.CSS.escape === 'function'
            ? window.CSS.escape(String(value))
            : String(value).replace(/["\\]/g, '\\$&')
    );

    const scrollToWeek = (weekKey, behavior = 'smooth') => {
        if (!weekKey) return;
        const target = weeksEl.querySelector(`[data-week-key="${cssEscapeValue(weekKey)}"]`);
        if (!target) return;
        const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        scrollEl.scrollTo({
            top: Math.max(0, target.offsetTop - 12),
            behavior: prefersReducedMotion ? 'auto' : behavior,
        });
        target.classList.remove('is-week-focus');
        void target.offsetWidth;
        target.classList.add('is-week-focus');
        window.setTimeout(() => target.classList.remove('is-week-focus'), 1500);
    };

    const renderSummary = () => {
        if (!summaryEl) return;
        const summary = overview.summary || {};
        summaryEl.innerHTML = `
            <span><strong>${Number(summary.open_count || 0)}</strong> 未完成</span>
            <span><strong>${Number(summary.due_soon_count || 0)}</strong> 7天内截止</span>
            <span><strong>${Number(summary.no_deadline_count || 0)}</strong> 无截止</span>
        `;
    };

    const renderTodoListItem = (todo) => {
        const tone = escapeHtml(todo.tone || 'neutral');
        const completedClass = todo.is_completed ? ' is-completed' : '';
        const checkbox = todo.can_complete
            ? `<button type="button" class="semester-todo-check${todo.is_completed ? ' is-checked' : ''}" data-todo-complete="${manualTodoId(todo)}" aria-label="${todo.is_completed ? '标记为未完成' : '标记为已完成'}"></button>`
            : `<span class="semester-todo-source-dot" aria-hidden="true"></span>`;
        const link = todo.link_url
            ? `<a class="semester-todo-open" href="${escapeHtml(todo.link_url)}" aria-label="打开${escapeHtml(todo.title)}">打开</a>`
            : '';
        const remove = todo.can_complete
            ? `<button type="button" class="semester-todo-delete" data-todo-delete="${manualTodoId(todo)}" aria-label="删除待办">删除</button>`
            : '';
        return `
            <li class="semester-todo-item is-${tone}${completedClass}" data-todo-id="${escapeHtml(todo.id)}">
                ${checkbox}
                <button type="button" class="semester-todo-name" data-todo-focus="${escapeHtml(todo.id)}">
                    <span>${escapeHtml(todo.title)}</span>
                    <small>${escapeHtml(todo.duration_label || todo.deadline_label || '')}</small>
                </button>
                <span class="semester-todo-status">${escapeHtml(todo.status_label || sourceLabel(todo))}</span>
                ${link}
                ${remove}
            </li>
        `;
    };

    const renderGanttRow = (todo) => {
        const left = Number(todo.bar_left || 0).toFixed(3);
        const width = Math.max(7, Number(todo.bar_width || 0)).toFixed(3);
        const tone = escapeHtml(todo.tone || 'neutral');
        const completedClass = todo.is_completed ? ' is-completed' : '';
        const timeChip = todo.due_time_label && !todo.no_deadline
            ? `<span class="semester-gantt-time">${escapeHtml(todo.due_time_label)}</span>`
            : '';
        return `
            <button type="button" class="semester-gantt-row is-${tone}${completedClass}" data-todo-focus="${escapeHtml(todo.id)}">
                <span class="semester-gantt-lane" aria-hidden="true">
                    <span class="semester-gantt-bar" style="left:${left}%;width:${width}%"></span>
                </span>
                <span class="semester-gantt-title">${escapeHtml(todo.title)}</span>
                ${timeChip}
            </button>
        `;
    };

    const renderWeek = (week) => {
        const currentClass = week.is_current ? ' is-current' : '';
        const days = (week.days || []).map((day) => `
            <button type="button"
                class="semester-day-cell${day.is_today ? ' is-today' : ''}${day.is_weekend ? ' is-weekend' : ''}"
                data-calendar-date="${escapeHtml(day.date)}"
                aria-label="${escapeHtml(day.month_day_label)} ${escapeHtml(day.weekday_label)}">
                <span>${escapeHtml(day.weekday_label)}</span>
                <strong>${escapeHtml(day.day_number)}</strong>
            </button>
        `).join('');
        const todos = Array.isArray(week.todos) ? week.todos : [];
        const gantt = todos.length
            ? todos.map(renderGanttRow).join('')
            : '<div class="semester-week-empty">本周暂无待办。</div>';
        const list = todos.length
            ? `<ul class="semester-todo-list">${todos.map(renderTodoListItem).join('')}</ul>`
            : '';
        return `
            <article class="semester-week-card${currentClass}" data-week-key="${escapeHtml(week.key)}">
                <div class="semester-week-head">
                    <div>
                        <strong>${escapeHtml(week.label)}</strong>
                        <span>${escapeHtml(week.range_label)}</span>
                    </div>
                    <small>${Number(week.open_count || 0)} 项待处理</small>
                </div>
                <div class="semester-week-calendar">${days}</div>
                <div class="semester-week-todos">
                    <div class="semester-gantt">${gantt}</div>
                    ${list}
                </div>
            </article>
        `;
    };

    const renderOverview = (nextOverview = overview) => {
        overview = nextOverview || { weeks: [], summary: {}, role_policy: {} };
        config.todoOverview = overview;
        renderSummary();
        const weeks = Array.isArray(overview.weeks) ? overview.weeks : [];
        if (!weeks.length) {
            weeksEl.innerHTML = '<div class="semester-week-empty is-large">还没有可展示的教学日历待办。</div>';
            return;
        }
        weeksEl.innerHTML = weeks.map(renderWeek).join('');
        window.requestAnimationFrame(() => {
            scrollToWeek(overview.active_week_key, 'auto');
            if (activeTodoId) {
                highlightTodo(activeTodoId, { scroll: false });
            }
        });
    };

    const findTodo = (todoId) => {
        const items = Array.isArray(overview.items) ? overview.items : [];
        return items.find((item) => String(item.id) === String(todoId));
    };

    const highlightTodo = (todoId, options = {}) => {
        const todo = findTodo(todoId);
        if (!todo) return;
        activeTodoId = String(todoId);
        const startDate = todo.effective_start_date;
        const endDate = todo.no_deadline ? startDate : (todo.effective_end_date || startDate);
        const dateKeys = eachDateKey(startDate, endDate);
        const dateSet = new Set(dateKeys);

        panel.querySelectorAll('[data-todo-id], [data-todo-focus]').forEach((node) => {
            const nodeTodoId = node.getAttribute('data-todo-id') || node.getAttribute('data-todo-focus');
            node.classList.toggle('is-active', String(nodeTodoId) === String(todoId));
        });
        panel.querySelectorAll('[data-calendar-date]').forEach((node) => {
            const key = node.getAttribute('data-calendar-date');
            const active = dateSet.has(key);
            node.classList.toggle('is-highlighted', active);
            node.classList.toggle('is-range-start', active && key === startDate);
            node.classList.toggle('is-range-end', active && key === endDate);
        });
        document.querySelectorAll('.teaching-timeline-segment[data-session-date]').forEach((node) => {
            const key = node.getAttribute('data-session-date');
            node.classList.toggle('is-todo-highlighted', dateSet.has(key));
        });

        if (options.scroll !== false) {
            const week = (overview.weeks || []).find((candidate) => {
                const weekStart = candidate.key;
                const weekDate = parseDateKey(weekStart);
                if (!weekDate) return false;
                const weekEnd = formatDateKey(addDays(weekDate, 6));
                return dateKeys.some((key) => key >= weekStart && key <= weekEnd);
            });
            if (week) scrollToWeek(week.key);
            const timelineMatch = document.querySelector(`.teaching-timeline-segment[data-session-date="${cssEscapeValue(startDate)}"]`);
            timelineMatch?.scrollIntoView({ behavior: 'smooth', inline: 'center', block: 'nearest' });
        }
    };

    const refreshFromApi = async () => {
        const result = await apiFetch(`/api/classrooms/${classOfferingId}/todos`, { silent: true });
        renderOverview(result.todo_overview);
    };

    const patchManualTodo = async (todoId, body) => {
        const result = await apiFetch(`/api/classrooms/${classOfferingId}/todos/${todoId}`, {
            method: 'PATCH',
            body,
            silent: true,
        });
        renderOverview(result.todo_overview);
        showToast(result.message || '待办已更新', 'success');
    };

    const deleteManualTodo = async (todoId) => {
        const confirmed = window.confirm('确定删除这条待办吗？');
        if (!confirmed) return;
        const result = await apiFetch(`/api/classrooms/${classOfferingId}/todos/${todoId}`, {
            method: 'DELETE',
            silent: true,
        });
        activeTodoId = '';
        renderOverview(result.todo_overview);
        showToast(result.message || '待办已删除', 'success');
    };

    const updatePickerResult = () => {
        if (!pickerResult) return;
        const dueTime = form?.elements?.due_time?.value || '23:59';
        const startTime = form?.elements?.start_time?.value || '00:00';
        const startText = selectedStartDate ? `${monthDayLabel(selectedStartDate)} ${startTime}` : '未选择';
        const dueText = selectedDueDate ? `${monthDayLabel(selectedDueDate)} ${dueTime}` : '无截止日期';
        pickerResult.textContent = `开始：${startText}；截止：${dueText}。无开始日期时，将使用创建日期。`;
    };

    const setDateRole = (role) => {
        selectedRole = role === 'start' ? 'start' : 'due';
        roleTabs.forEach((tab) => tab.classList.toggle('is-active', tab.dataset.dateRole === selectedRole));
    };

    const renderPicker = () => {
        if (!pickerGrid || !pickerTitle) return;
        pickerTitle.textContent = monthTitle(pickerMonth);
        const firstOfMonth = new Date(pickerMonth.getFullYear(), pickerMonth.getMonth(), 1);
        const startOffset = (firstOfMonth.getDay() + 6) % 7;
        const gridStart = addDays(firstOfMonth, -startOffset);
        const todayKey = formatDateKey(new Date());
        const cells = [];
        for (let index = 0; index < 42; index += 1) {
            const day = addDays(gridStart, index);
            const key = formatDateKey(day);
            const inMonth = day.getMonth() === pickerMonth.getMonth();
            const inRange = selectedStartDate && selectedDueDate
                ? key >= selectedStartDate && key <= selectedDueDate
                : false;
            cells.push(`
                <button type="button"
                    class="semester-picker-day${inMonth ? '' : ' is-outside'}${key === todayKey ? ' is-today' : ''}${key === selectedStartDate ? ' is-start' : ''}${key === selectedDueDate ? ' is-due' : ''}${inRange ? ' is-in-range' : ''}"
                    data-picker-date="${key}">
                    <span>${day.getDate()}</span>
                </button>
            `);
        }
        pickerGrid.innerHTML = cells.join('');
        updatePickerResult();
    };

    const openModal = () => {
        if (!modal || !form) return;
        form.reset();
        selectedStartDate = '';
        selectedDueDate = '';
        setDateRole('due');
        pickerMonth = new Date();
        renderPicker();
        modal.hidden = false;
        modal.setAttribute('aria-hidden', 'false');
        document.body.classList.add('has-semester-todo-modal');
        window.requestAnimationFrame(() => {
            modal.classList.add('is-open');
            form.elements.title?.focus();
        });
    };

    const closeModal = () => {
        if (!modal) return;
        modal.classList.remove('is-open');
        modal.setAttribute('aria-hidden', 'true');
        window.setTimeout(() => {
            modal.hidden = true;
            document.body.classList.remove('has-semester-todo-modal');
        }, 180);
    };

    weeksEl.addEventListener('click', async (event) => {
        const completeBtn = event.target.closest('[data-todo-complete]');
        if (completeBtn) {
            event.preventDefault();
            event.stopPropagation();
            const todoId = completeBtn.dataset.todoComplete;
            await patchManualTodo(todoId, { completed: !completeBtn.classList.contains('is-checked') });
            return;
        }

        const deleteBtn = event.target.closest('[data-todo-delete]');
        if (deleteBtn) {
            event.preventDefault();
            event.stopPropagation();
            await deleteManualTodo(deleteBtn.dataset.todoDelete);
            return;
        }

        if (event.target.closest('.semester-todo-open')) return;

        const focusNode = event.target.closest('[data-todo-focus], [data-todo-id]');
        if (focusNode) {
            const todoId = focusNode.getAttribute('data-todo-focus') || focusNode.getAttribute('data-todo-id');
            highlightTodo(todoId);
        }
    });

    roleTabs.forEach((tab) => {
        tab.addEventListener('click', () => setDateRole(tab.dataset.dateRole));
    });

    document.querySelectorAll('[data-date-nav]').forEach((button) => {
        button.addEventListener('click', () => {
            pickerMonth = new Date(
                pickerMonth.getFullYear(),
                pickerMonth.getMonth() + (button.dataset.dateNav === 'next' ? 1 : -1),
                1,
            );
            renderPicker();
        });
    });

    pickerGrid?.addEventListener('click', (event) => {
        const dayButton = event.target.closest('[data-picker-date]');
        if (!dayButton) return;
        const key = dayButton.dataset.pickerDate;
        if (selectedRole === 'start') {
            selectedStartDate = key;
            if (selectedDueDate && selectedDueDate < selectedStartDate) {
                selectedDueDate = selectedStartDate;
            }
            setDateRole('due');
        } else {
            selectedDueDate = key;
            if (selectedStartDate && selectedDueDate < selectedStartDate) {
                const previousStart = selectedStartDate;
                selectedStartDate = selectedDueDate;
                selectedDueDate = previousStart;
            }
        }
        renderPicker();
    });

    form?.addEventListener('input', (event) => {
        if (event.target?.name === 'due_time' || event.target?.name === 'start_time') {
            updatePickerResult();
        }
    });

    form?.addEventListener('submit', async (event) => {
        event.preventDefault();
        const formData = new FormData(form);
        const body = {
            title: String(formData.get('title') || '').trim(),
            notes: String(formData.get('notes') || '').trim(),
            start_at: selectedStartDate ? composeDateTime(selectedStartDate, formData.get('start_time'), '00:00') : null,
            due_at: selectedDueDate ? composeDateTime(selectedDueDate, formData.get('due_time'), '23:59') : null,
        };
        const submitBtn = form.querySelector('button[type="submit"]');
        if (submitBtn) submitBtn.disabled = true;
        try {
            const result = await apiFetch(`/api/classrooms/${classOfferingId}/todos`, {
                method: 'POST',
                body,
                silent: true,
            });
            closeModal();
            activeTodoId = result.id ? `manual:${result.id}` : '';
            renderOverview(result.todo_overview);
            if (activeTodoId) {
                window.requestAnimationFrame(() => highlightTodo(activeTodoId));
            }
            showToast(result.message || '待办已添加', 'success');
        } catch (error) {
            showToast(error.message || '新增待办失败', 'error');
        } finally {
            if (submitBtn) submitBtn.disabled = false;
        }
    });

    addBtn?.addEventListener('click', openModal);
    modalClose?.addEventListener('click', closeModal);
    modalCancel?.addEventListener('click', closeModal);
    modal?.addEventListener('click', (event) => {
        if (event.target === modal) closeModal();
    });
    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && modal && !modal.hidden) closeModal();
    });

    renderOverview(overview);
    if (!overview?.weeks?.length) {
        refreshFromApi().catch(() => {});
    }
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
    initSemesterTodoBoard(window.APP_CONFIG || {});
    personalizeClassroomCopy();
    document.addEventListener('classroom:alias-change', (event) => {
        personalizeClassroomCopy(event.detail || {});
    });
}
