import { formatDate, showMessage } from '/static/js/ui.js';
import { initSemesterCalendar } from '/static/js/semester_calendar.js?v=dashboard-todo-axis-20260507';

const root = document.querySelector('[data-dashboard-root]');

function normalizeText(value) {
    return String(value || '')
        .toLowerCase()
        .replace(/\s+/g, ' ')
        .trim();
}

function compactText(value) {
    return normalizeText(value).replace(/\s+/g, '');
}

if (root) {
    const cards = Array.from(root.querySelectorAll('[data-offering-card]'));
    const filterButtons = Array.from(root.querySelectorAll('[data-filter-value]'));
    const groupModeButtons = Array.from(root.querySelectorAll('[data-group-mode]'));
    const searchForm = root.querySelector('[data-dashboard-search-form]');
    const filterField = root.querySelector('[data-dashboard-filter-field]');
    const searchInput = root.querySelector('[data-dashboard-search]');
    const visibleCount = root.querySelector('[data-visible-count]');
    const resultsSummary = root.querySelector('[data-results-summary]');
    const offeringList = root.querySelector('[data-offering-list]');
    const emptySearch = root.querySelector('[data-empty-search]');
    const resetButton = root.querySelector('[data-reset-search]');
    const semesterCalendarRoot = root.querySelector('[data-semester-calendar-root]');

    const cardState = new Map();
    const collator = new Intl.Collator('zh-Hans-CN', { numeric: true, sensitivity: 'base' });
    const groupModeLabels = {
        department: '系别班级',
        course: '课程',
        timeline: '时间轴',
        flat: '列表',
    };
    const allowedGroupModes = new Set(groupModeButtons.map((button) => button.dataset.groupMode || 'department'));
    const filterLabels = new Map(
        filterButtons.map((button) => [
            button.dataset.filterValue || 'all',
            button.dataset.filterLabel || button.textContent.trim(),
        ]),
    );
    const allowedFilters = new Set(filterButtons.map((button) => button.dataset.filterValue || 'all'));
    const initialFilter = root.dataset.initialFilter || 'all';
    const savedGroupMode = readStorageValue('dashboard:teacher-group-mode');
    const initialGroupMode = root.dataset.initialGroupMode || 'flat';
    let activeFilter = allowedFilters.has(initialFilter)
        ? initialFilter
        : filterButtons.find((button) => button.classList.contains('is-active'))?.dataset.filterValue || 'all';
    let activeGroupMode = groupModeButtons.length
        ? (allowedGroupModes.has(savedGroupMode) ? savedGroupMode : initialGroupMode)
        : 'flat';
    if (groupModeButtons.length && !allowedGroupModes.has(activeGroupMode)) {
        activeGroupMode = 'department';
    }
    let activeTimelineKey = '';
    let isComposing = false;
    let searchTimerId = 0;

    const collapsedGroups = new Set(readJsonStorage('dashboard:teacher-collapsed-groups', []));

    cards.forEach((card) => {
        const searchText = String(card.dataset.searchText || '');
        cardState.set(card, {
            searchNormalized: normalizeText(searchText),
            searchCompact: compactText(searchText),
            department: normalizeGroupLabel(card.dataset.department, '未分类'),
            className: normalizeGroupLabel(card.dataset.className, '未命名班级'),
            classId: String(card.dataset.classId || ''),
            courseName: normalizeGroupLabel(card.dataset.courseName, '未命名课程'),
            courseId: String(card.dataset.courseId || ''),
            timelineItems: parseTimelineItems(card.dataset.timelineItems),
            visible: !card.hidden,
        });
    });

    const formatDateNodes = () => {
        root.querySelectorAll('[data-datetime]').forEach((node) => {
            const value = node.getAttribute('data-datetime');
            if (!value) {
                return;
            }
            node.textContent = formatDate(value);
        });
    };

    const revealElements = () => {
        const targets = root.querySelectorAll('.dashboard-reveal');
        targets.forEach((element, index) => {
            element.style.setProperty('--reveal-index', String(Math.min(index, 8)));
        });
        root.classList.add('is-reveal-ready');
        const showTargets = () => {
            targets.forEach((element) => element.classList.add('is-visible'));
        };
        window.requestAnimationFrame(showTargets);
    };

    const updateFilterUi = () => {
        filterButtons.forEach((button) => {
            const isActive = (button.dataset.filterValue || 'all') === activeFilter;
            button.classList.toggle('is-active', isActive);
            button.setAttribute('aria-selected', String(isActive));
        });
    };

    const updateGroupModeUi = () => {
        groupModeButtons.forEach((button) => {
            const isActive = (button.dataset.groupMode || '') === activeGroupMode;
            button.classList.toggle('is-active', isActive);
            button.setAttribute('aria-pressed', String(isActive));
        });
    };

    const syncSearchForm = () => {
        if (filterField) {
            filterField.value = activeFilter || 'all';
        }
    };

    const buildResultsSummary = (keyword) => {
        const fragments = [];
        if (activeFilter !== 'all') {
            fragments.push(`筛选：${filterLabels.get(activeFilter) || activeFilter}`);
        }
        if (keyword) {
            fragments.push(`关键词：${keyword}`);
        }
        if (groupModeButtons.length) {
            fragments.push(`归纳：${groupModeLabels[activeGroupMode] || activeGroupMode}`);
        }
        return fragments.length ? fragments.join(' · ') : '显示全部课堂';
    };

    const syncUrlState = (keyword) => {
        const url = new URL(window.location.href);
        if (activeFilter && activeFilter !== 'all') {
            url.searchParams.set('filter', activeFilter);
        } else {
            url.searchParams.delete('filter');
        }
        if (keyword) {
            url.searchParams.set('q', keyword);
        } else {
            url.searchParams.delete('q');
        }
        const nextUrl = `${url.pathname}${url.search}${url.hash}`;
        const currentUrl = `${window.location.pathname}${window.location.search}${window.location.hash}`;
        if (nextUrl !== currentUrl) {
            window.history.replaceState({}, '', nextUrl);
        }
    };

    const matchesFilter = (card) => {
        if (activeFilter === 'attention') {
            return card.dataset.attention === 'true';
        }
        if (activeFilter === 'recent') {
            return card.dataset.recent === 'true';
        }
        if (activeFilter === 'progress') {
            return card.dataset.progress === 'true';
        }
        return true;
    };

    const applyFilters = ({ syncUrl = true } = {}) => {
        const keyword = String(searchInput?.value || '')
            .replace(/\s+/g, ' ')
            .trim();
        const normalizedKeyword = normalizeText(keyword);
        const compactKeyword = normalizedKeyword.replace(/\s+/g, '');
        let count = 0;
        const visibleCards = [];

        cards.forEach((card) => {
            const state = cardState.get(card);
            const normalizedSearch = state?.searchNormalized || '';
            const compactSearch = state?.searchCompact || normalizedSearch.replace(/\s+/g, '');
            const matchesKeyword = !normalizedKeyword
                || normalizedSearch.includes(normalizedKeyword)
                || (compactKeyword && compactSearch.includes(compactKeyword));
            const visible = Boolean(matchesKeyword && matchesFilter(card));
            if (state) {
                state.visible = visible;
            }
            card.hidden = !visible;
            card.setAttribute('aria-hidden', visible ? 'false' : 'true');
            if (visible) {
                count += 1;
                visibleCards.push(card);
            }
        });

        if (visibleCount) {
            visibleCount.textContent = String(count);
        }
        if (resultsSummary) {
            resultsSummary.textContent = buildResultsSummary(keyword);
        }
        if (offeringList) {
            offeringList.hidden = count === 0;
            renderOfferingList(visibleCards);
        }
        if (emptySearch) {
            emptySearch.hidden = count !== 0;
        }
        if (resetButton) {
            resetButton.hidden = !(keyword || activeFilter !== 'all');
        }

        updateFilterUi();
        updateGroupModeUi();
        syncSearchForm();
        if (syncUrl) {
            syncUrlState(keyword);
        }
    };

    const scheduleApplyFilters = () => {
        window.clearTimeout(searchTimerId);
        searchTimerId = window.setTimeout(() => {
            applyFilters();
        }, 100);
    };

    filterButtons.forEach((button) => {
        button.addEventListener('click', (event) => {
            event.preventDefault();
            activeFilter = button.dataset.filterValue || 'all';
            applyFilters();
        });
    });

    groupModeButtons.forEach((button) => {
        button.addEventListener('click', () => {
            const nextMode = button.dataset.groupMode || 'department';
            if (!allowedGroupModes.has(nextMode) || nextMode === activeGroupMode) {
                return;
            }
            activeGroupMode = nextMode;
            activeTimelineKey = '';
            writeStorageValue('dashboard:teacher-group-mode', activeGroupMode);
            applyFilters();
        });
    });

    searchForm?.addEventListener('submit', (event) => {
        event.preventDefault();
        applyFilters();
    });

    searchInput?.addEventListener('compositionstart', () => {
        isComposing = true;
    });

    searchInput?.addEventListener('compositionend', () => {
        isComposing = false;
        applyFilters();
    });

    searchInput?.addEventListener('input', () => {
        if (isComposing) {
            return;
        }
        scheduleApplyFilters();
    });

    searchInput?.addEventListener('search', () => {
        applyFilters();
    });

    searchInput?.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
            searchInput.value = '';
            applyFilters();
        }
    });

    resetButton?.addEventListener('click', () => {
        activeFilter = 'all';
        if (searchInput) {
            searchInput.value = '';
        }
        applyFilters();
    });

    syncSearchForm();
    updateFilterUi();
    updateGroupModeUi();
    applyFilters({ syncUrl: false });
    formatDateNodes();
    revealElements();

    initSemesterCalendar(semesterCalendarRoot, window.DASHBOARD_SEMESTER_CALENDAR || {}, {
        showTodos: true,
        onMessage: (message, tone) => showMessage(message, tone || 'info'),
    });

    function renderOfferingList(visibleCards) {
        if (!offeringList) {
            return;
        }

        offeringList.replaceChildren();
        offeringList.className = 'dashboard-offering-grid';
        offeringList.removeAttribute('aria-label');

        if (!visibleCards.length) {
            return;
        }

        if (!groupModeButtons.length || activeGroupMode === 'flat') {
            appendCards(offeringList, sortCards(visibleCards, ['department', 'className', 'courseName']));
            return;
        }

        offeringList.classList.add('is-grouped');
        if (activeGroupMode === 'course') {
            offeringList.classList.add('is-course-grouped');
            renderCourseGroups(visibleCards);
            return;
        }
        if (activeGroupMode === 'timeline') {
            offeringList.classList.add('is-timeline');
            renderTimelineGroups(visibleCards);
            return;
        }
        renderDepartmentGroups(visibleCards);
    }

    function renderDepartmentGroups(visibleCards) {
        const board = document.createElement('div');
        board.className = 'dashboard-group-board';
        const departmentGroups = groupCards(visibleCards, (card) => cardState.get(card)?.department || '未分类');

        departmentGroups.forEach((departmentGroup) => {
            const classGroups = groupCards(departmentGroup.items, (card) => {
                const state = cardState.get(card);
                return `${state?.classId || ''}|${state?.className || '未命名班级'}`;
            });
            const departmentShell = createGroupSection({
                key: `department:${departmentGroup.key}`,
                title: departmentGroup.label,
                subtitle: `${classGroups.length} 个班级 · ${departmentGroup.items.length} 个课堂`,
                count: departmentGroup.items.length,
                level: 1,
                tone: 'department',
            });
            const classBoard = document.createElement('div');
            classBoard.className = 'dashboard-subgroup-board';

            classGroups.forEach((classGroup) => {
                const className = cardState.get(classGroup.items[0])?.className || classGroup.label;
                const classShell = createGroupSection({
                    key: `department:${departmentGroup.key}:class:${classGroup.key}`,
                    title: className,
                    subtitle: summarizeUnique(classGroup.items, 'courseName', '门课程'),
                    count: classGroup.items.length,
                    level: 2,
                    tone: 'class',
                });
                const grid = createCardGrid();
                appendCards(grid, sortCards(classGroup.items, ['courseName']));
                classShell.body.appendChild(grid);
                classBoard.appendChild(classShell.section);
            });

            departmentShell.body.appendChild(classBoard);
            board.appendChild(departmentShell.section);
        });

        offeringList.appendChild(board);
    }

    function renderCourseGroups(visibleCards) {
        const board = document.createElement('div');
        board.className = 'dashboard-group-board dashboard-course-board';
        const courseGroups = groupCards(visibleCards, (card) => {
            const state = cardState.get(card);
            return `${state?.courseId || ''}|${state?.courseName || '未命名课程'}`;
        });

        courseGroups.forEach((courseGroup) => {
            const courseName = cardState.get(courseGroup.items[0])?.courseName || courseGroup.label;
            const courseShell = createGroupSection({
                key: `course:${courseGroup.key}`,
                title: courseName,
                subtitle: `${summarizeUnique(courseGroup.items, 'department', '个系别')} · ${summarizeUnique(courseGroup.items, 'className', '个班级')}`,
                count: courseGroup.items.length,
                level: 1,
                tone: 'course',
            });
            const grid = createCardGrid();
            appendCards(grid, sortCards(courseGroup.items, ['department', 'className']));
            courseShell.body.appendChild(grid);
            board.appendChild(courseShell.section);
        });

        offeringList.appendChild(board);
    }

    function renderTimelineGroups(visibleCards) {
        const timelineItems = visibleCards.flatMap((card) => {
            const state = cardState.get(card);
            return (state?.timelineItems || []).map((item) => ({ ...item, card }));
        }).sort(compareTimelineItems);

        const shell = document.createElement('div');
        shell.className = 'dashboard-timeline-shell';

        if (!timelineItems.length) {
            const empty = document.createElement('div');
            empty.className = 'dashboard-timeline-empty';
            const title = document.createElement('strong');
            title.textContent = '当前筛选范围内，近四天没有排课。';
            const copy = document.createElement('p');
            copy.textContent = '可以切换搜索或标签筛选，或在课堂管理里补齐首次上课日期和课堂时间轴。';
            empty.append(title, copy);
            shell.appendChild(empty);
            offeringList.appendChild(shell);
            return;
        }

        const groups = groupTimelineItems(timelineItems);
        if (!groups.some((group) => group.key === activeTimelineKey)) {
            activeTimelineKey = chooseTimelineKey(groups);
        }

        const axis = document.createElement('div');
        axis.className = 'dashboard-timeline-axis';
        axis.setAttribute('aria-label', '近四天课堂时间轴');
        axis.setAttribute('role', 'listbox');

        const focus = document.createElement('div');
        focus.className = 'dashboard-timeline-focus';

        groups.forEach((group) => {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'dashboard-timeline-tick';
            button.dataset.timelineKey = group.key;
            button.setAttribute('role', 'option');
            const line = document.createElement('span');
            line.className = 'dashboard-timeline-tick__line';
            const copy = document.createElement('span');
            copy.className = 'dashboard-timeline-tick__copy';
            const dateLabel = document.createElement('strong');
            dateLabel.textContent = `${group.relativeLabel} · ${group.dateLabel}`;
            const hourLabel = document.createElement('span');
            hourLabel.textContent = `${group.hourLabel} ${group.weekdayLabel}`;
            const count = document.createElement('em');
            count.textContent = `${group.items.length} 次课`;
            copy.append(dateLabel, hourLabel, count);
            button.append(line, copy);
            axis.appendChild(button);
        });

        const selectTimelineKey = (key, { scrollIntoView = false } = {}) => {
            const group = groups.find((item) => item.key === key) || groups[0];
            if (!group) {
                return;
            }
            activeTimelineKey = group.key;
            axis.querySelectorAll('[data-timeline-key]').forEach((button) => {
                const isActive = button.dataset.timelineKey === group.key;
                button.classList.toggle('is-active', isActive);
                button.setAttribute('aria-selected', String(isActive));
                if (isActive && scrollIntoView) {
                    button.scrollIntoView({ block: 'center', behavior: 'smooth' });
                }
            });
            renderTimelineFocus(focus, group);
        };

        axis.addEventListener('click', (event) => {
            const button = event.target.closest('[data-timeline-key]');
            if (!button) {
                return;
            }
            if (axis.dataset.dragged === 'true') {
                delete axis.dataset.dragged;
                return;
            }
            selectTimelineKey(button.dataset.timelineKey, { scrollIntoView: true });
        });
        bindTimelineDrag(axis, groups, selectTimelineKey);

        shell.append(axis, focus);
        offeringList.appendChild(shell);
        selectTimelineKey(activeTimelineKey, { scrollIntoView: true });
    }

    function renderTimelineFocus(container, group) {
        container.replaceChildren();
        const header = document.createElement('div');
        header.className = 'dashboard-timeline-focus__header';
        const copy = document.createElement('div');
        const title = document.createElement('h3');
        title.textContent = `${group.relativeLabel} ${group.hourLabel}`;
        const subtitle = document.createElement('p');
        subtitle.textContent = `${group.dateFullLabel} · ${group.weekdayLabel} · ${group.items.length} 次课`;
        copy.append(title, subtitle);
        const badge = document.createElement('span');
        badge.className = 'dashboard-timeline-focus__badge';
        badge.textContent = group.items.some((item) => item.status === 'current') ? '今天' : '时间焦点';
        header.append(copy, badge);

        const sessionList = document.createElement('div');
        sessionList.className = 'dashboard-timeline-session-list';
        group.items.forEach((item) => {
            const session = document.createElement('a');
            session.className = `dashboard-timeline-session is-${item.status || 'upcoming'}`;
            session.href = item.href || '#';
            const main = document.createElement('div');
            const title = document.createElement('strong');
            title.textContent = item.title || item.course_name || '课堂安排';
            const meta = document.createElement('span');
            meta.textContent = [
                item.course_name,
                item.class_name,
                item.section_label,
                item.week_label,
            ].filter(Boolean).join(' · ');
            main.append(title, meta);
            const time = document.createElement('em');
            time.textContent = item.time_hint ? `${item.hour_label} · 默认` : item.hour_label;
            if (item.time_hint) {
                time.title = item.time_hint;
            }
            session.append(main, time);
            sessionList.appendChild(session);
        });

        const grid = createCardGrid();
        const uniqueCards = uniqueCardsFromTimeline(group.items);
        appendCards(grid, sortCards(uniqueCards, ['courseName', 'className']));
        container.append(header, sessionList, grid);
    }

    function createGroupSection({ key, title, subtitle, count, level, tone }) {
        const isCollapsed = collapsedGroups.has(key);
        const section = document.createElement('section');
        section.className = `dashboard-group-section dashboard-group-section--level-${level} dashboard-group-section--${tone}`;
        section.classList.toggle('is-collapsed', isCollapsed);

        const header = document.createElement('div');
        header.className = 'dashboard-group-header';
        const copy = document.createElement('div');
        copy.className = 'dashboard-group-header__copy';
        const heading = document.createElement('h3');
        heading.textContent = title || '未分类';
        const note = document.createElement('p');
        note.textContent = subtitle || `${count} 个课堂`;
        copy.append(heading, note);

        const actions = document.createElement('div');
        actions.className = 'dashboard-group-header__actions';
        const pill = document.createElement('span');
        pill.className = 'dashboard-group-count';
        pill.textContent = `${count} 个`;
        const toggle = document.createElement('button');
        toggle.type = 'button';
        toggle.className = 'dashboard-group-toggle';
        toggle.setAttribute('aria-label', `${isCollapsed ? '展开' : '折叠'}${title || '当前分组'}`);
        toggle.setAttribute('aria-expanded', String(!isCollapsed));
        const icon = document.createElement('span');
        icon.className = 'dashboard-group-toggle__icon';
        icon.setAttribute('aria-hidden', 'true');
        toggle.appendChild(icon);
        actions.append(pill, toggle);
        header.append(copy, actions);

        const body = document.createElement('div');
        body.className = 'dashboard-group-body';
        body.hidden = isCollapsed;
        toggle.addEventListener('click', () => {
            const nextCollapsed = !section.classList.contains('is-collapsed');
            section.classList.toggle('is-collapsed', nextCollapsed);
            body.hidden = nextCollapsed;
            toggle.setAttribute('aria-expanded', String(!nextCollapsed));
            toggle.setAttribute('aria-label', `${nextCollapsed ? '展开' : '折叠'}${title || '当前分组'}`);
            if (nextCollapsed) {
                collapsedGroups.add(key);
            } else {
                collapsedGroups.delete(key);
            }
            writeJsonStorage('dashboard:teacher-collapsed-groups', Array.from(collapsedGroups));
        });

        section.append(header, body);
        return { section, body };
    }

    function createCardGrid() {
        const grid = document.createElement('div');
        grid.className = 'dashboard-group-card-grid';
        return grid;
    }

    function appendCards(target, cardList) {
        cardList.forEach((card) => {
            card.hidden = false;
            card.setAttribute('aria-hidden', 'false');
            target.appendChild(card);
        });
    }

    function sortCards(cardList, fields) {
        return [...cardList].sort((a, b) => {
            const stateA = cardState.get(a) || {};
            const stateB = cardState.get(b) || {};
            for (const field of fields) {
                const compared = compareText(stateA[field], stateB[field]);
                if (compared !== 0) {
                    return compared;
                }
            }
            return compareText(a.dataset.courseId, b.dataset.courseId) || compareText(a.dataset.classId, b.dataset.classId);
        });
    }

    function groupCards(cardList, getKey) {
        const buckets = new Map();
        cardList.forEach((card) => {
            const rawKey = String(getKey(card) || '未分类');
            const label = rawKey.includes('|') ? rawKey.split('|').pop() : rawKey;
            if (!buckets.has(rawKey)) {
                buckets.set(rawKey, { key: rawKey, label: label || '未分类', items: [] });
            }
            buckets.get(rawKey).items.push(card);
        });
        return Array.from(buckets.values()).sort((a, b) => compareText(a.label, b.label));
    }

    function summarizeUnique(cardList, field, suffix) {
        const values = new Set(
            cardList
                .map((card) => cardState.get(card)?.[field])
                .filter(Boolean),
        );
        return `${values.size} ${suffix}`;
    }

    function groupTimelineItems(items) {
        const buckets = new Map();
        items.forEach((item) => {
            const key = item.timeline_key || item.starts_at || '';
            if (!key) {
                return;
            }
            if (!buckets.has(key)) {
                buckets.set(key, {
                    key,
                    startsAt: item.starts_at || '',
                    dateLabel: item.date_label || '',
                    dateFullLabel: item.date_full_label || '',
                    hourLabel: item.hour_label || '',
                    weekdayLabel: item.weekday_label || '',
                    relativeLabel: item.relative_label || '',
                    items: [],
                });
            }
            buckets.get(key).items.push(item);
        });
        return Array.from(buckets.values()).sort((a, b) => compareText(a.startsAt, b.startsAt));
    }

    function chooseTimelineKey(groups) {
        const now = new Date();
        const futureGroup = groups.find((group) => new Date(group.startsAt).getTime() >= now.getTime());
        if (futureGroup) {
            return futureGroup.key;
        }
        return groups[groups.length - 1]?.key || '';
    }

    function compareTimelineItems(a, b) {
        return compareText(a.starts_at, b.starts_at)
            || compareText(a.course_name, b.course_name)
            || compareText(a.class_name, b.class_name)
            || compareText(a.title, b.title);
    }

    function uniqueCardsFromTimeline(items) {
        const seen = new Set();
        const unique = [];
        items.forEach((item) => {
            if (!item.card || seen.has(item.card)) {
                return;
            }
            seen.add(item.card);
            unique.push(item.card);
        });
        return unique;
    }

    function bindTimelineDrag(axis, groups, selectTimelineKey) {
        let pointerId = null;
        let startY = 0;
        let startScrollTop = 0;
        let moved = false;
        let scrollTimer = 0;

        const selectClosestToCenter = () => {
            const buttons = Array.from(axis.querySelectorAll('[data-timeline-key]'));
            if (!buttons.length) {
                return;
            }
            const axisRect = axis.getBoundingClientRect();
            const centerY = axisRect.top + axisRect.height / 2;
            let closestButton = buttons[0];
            let closestDistance = Number.POSITIVE_INFINITY;
            buttons.forEach((button) => {
                const rect = button.getBoundingClientRect();
                const buttonCenter = rect.top + rect.height / 2;
                const distance = Math.abs(buttonCenter - centerY);
                if (distance < closestDistance) {
                    closestDistance = distance;
                    closestButton = button;
                }
            });
            selectTimelineKey(closestButton.dataset.timelineKey || groups[0]?.key || '', { scrollIntoView: false });
        };

        axis.addEventListener('pointerdown', (event) => {
            pointerId = event.pointerId;
            startY = event.clientY;
            startScrollTop = axis.scrollTop;
            moved = false;
            axis.classList.add('is-dragging');
            axis.setPointerCapture?.(event.pointerId);
        });

        axis.addEventListener('pointermove', (event) => {
            if (pointerId !== event.pointerId) {
                return;
            }
            const deltaY = startY - event.clientY;
            if (Math.abs(deltaY) > 3) {
                moved = true;
            }
            axis.scrollTop = startScrollTop + deltaY;
            if (moved) {
                event.preventDefault();
                selectClosestToCenter();
            }
        });

        const stopDragging = (event) => {
            if (pointerId !== event.pointerId) {
                return;
            }
            pointerId = null;
            axis.classList.remove('is-dragging');
            axis.releasePointerCapture?.(event.pointerId);
            axis.dataset.dragged = moved ? 'true' : 'false';
            if (moved) {
                selectClosestToCenter();
            }
        };

        axis.addEventListener('pointerup', stopDragging);
        axis.addEventListener('pointercancel', stopDragging);
        axis.addEventListener('scroll', () => {
            window.clearTimeout(scrollTimer);
            scrollTimer = window.setTimeout(selectClosestToCenter, 80);
        }, { passive: true });
    }

    function normalizeGroupLabel(value, fallback) {
        return String(value || '').replace(/\s+/g, ' ').trim() || fallback;
    }

    function parseTimelineItems(rawValue) {
        if (!rawValue) {
            return [];
        }
        try {
            const parsed = JSON.parse(rawValue);
            return Array.isArray(parsed) ? parsed : [];
        } catch (error) {
            return [];
        }
    }

    function compareText(a, b) {
        return collator.compare(String(a || ''), String(b || ''));
    }

    function readStorageValue(key) {
        try {
            return window.localStorage.getItem(key) || '';
        } catch (error) {
            return '';
        }
    }

    function writeStorageValue(key, value) {
        try {
            window.localStorage.setItem(key, value);
        } catch (error) {
            // Storage can be disabled in hardened browsers; the UI still works without persistence.
        }
    }

    function readJsonStorage(key, fallback) {
        try {
            const raw = window.localStorage.getItem(key);
            if (!raw) {
                return fallback;
            }
            const parsed = JSON.parse(raw);
            return Array.isArray(parsed) ? parsed : fallback;
        } catch (error) {
            return fallback;
        }
    }

    function writeJsonStorage(key, value) {
        try {
            window.localStorage.setItem(key, JSON.stringify(value));
        } catch (error) {
            // Best-effort preference storage.
        }
    }
}
