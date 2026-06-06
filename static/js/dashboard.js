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

function toNumber(value) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : 0;
}

// Selector for interactive descendants that should keep their own click
// behaviour instead of triggering whole-card navigation.
const INTERACTIVE_CARD_CHILD = 'a, button, input, select, textarea, label, [role="button"], [data-timeline-axis], [contenteditable="true"]';

/**
 * Make each offering card fully clickable: a click (or Enter/Space when the
 * card is focused) anywhere outside an interactive child navigates to the
 * classroom, reusing the existing "进入课堂" link as the source of truth.
 * @param {HTMLElement[]} cards
 */
function setupOfferingCardNavigation(cards) {
    cards.forEach((card) => {
        const enterLink = card.querySelector('.dashboard-offering-card__enter, a[href^="/classroom/"]');
        const href = enterLink && enterLink.getAttribute('href');
        if (!href) {
            return;
        }
        card.addEventListener('click', (event) => {
            if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
                return;
            }
            if (event.target.closest(INTERACTIVE_CARD_CHILD)) {
                return;
            }
            const selection = window.getSelection();
            if (selection && selection.toString().trim()) {
                return;
            }
            window.location.assign(href);
        });
    });
}

if (root) {
    const cards = Array.from(root.querySelectorAll('[data-offering-card]'));
    setupOfferingCardNavigation(cards);
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
    const recentActivityDays = toNumber(root.dataset.recentActivityDays) || 14;
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
    let timelinePastExpanded = false;
    let groupSectionSerial = 0;
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
            activityScore: toNumber(card.dataset.activityScore),
            recentUserCount: toNumber(card.dataset.recentUserCount),
            recentLoginCount: toNumber(card.dataset.recentLoginCount),
            lastActivitySort: toNumber(card.dataset.lastActivitySort),
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
                activityLabel: buildGroupActivityLabel(departmentGroup),
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
                    activityLabel: buildGroupActivityLabel(classGroup),
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
                activityLabel: buildGroupActivityLabel(courseGroup),
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

    function groupTimelineDays(items) {
        const buckets = new Map();
        items.forEach((item) => {
            const key = item.date_full_label || String(item.starts_at || '').slice(0, 10);
            if (!key) {
                return;
            }
            if (!buckets.has(key)) {
                buckets.set(key, {
                    key,
                    dateFull: key,
                    dateLabel: item.date_label || '',
                    weekdayLabel: item.weekday_label || '',
                    yearLabel: item.year_label || getYearLabel(item.starts_at),
                    relativeLabel: item.relative_label || '',
                    items: [],
                });
            }
            buckets.get(key).items.push(item);
        });
        const days = Array.from(buckets.values());
        days.forEach((day) => day.items.sort(compareTimelineSession));
        days.sort((a, b) => compareText(a.dateFull, b.dateFull));
        return days;
    }

    function timelineDayStatus(day) {
        const time = new Date(`${day.dateFull}T00:00:00`).getTime();
        if (!Number.isFinite(time)) {
            return 'future';
        }
        const now = new Date();
        const today = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
        if (time < today) {
            return 'past';
        }
        if (time === today) {
            return 'current';
        }
        return 'future';
    }

    function buildTimelineSession(item) {
        const session = document.createElement('a');
        session.className = `dashboard-agenda-session is-${item.status || 'upcoming'}`;
        session.href = item.href || '#';
        const time = document.createElement('span');
        time.className = 'dashboard-agenda-session__time';
        time.textContent = item.hour_label || '时间待定';
        if (item.time_hint) {
            time.title = item.time_hint;
            session.classList.add('has-hint');
        }
        const body = document.createElement('span');
        body.className = 'dashboard-agenda-session__body';
        const title = document.createElement('strong');
        title.textContent = item.title || item.course_name || '课堂安排';
        const meta = document.createElement('span');
        meta.className = 'dashboard-agenda-session__meta';
        meta.textContent = [item.course_name, item.class_name, item.week_label, item.section_label]
            .filter(Boolean).join(' · ');
        body.append(title, meta);
        const go = document.createElement('span');
        go.className = 'dashboard-agenda-session__go';
        go.setAttribute('aria-hidden', 'true');
        go.textContent = '进入';
        session.append(time, body, go);
        return session;
    }

    function buildTimelineDay(day) {
        const status = timelineDayStatus(day);
        const section = document.createElement('section');
        section.className = `dashboard-agenda-day is-${status}`;
        if (status === 'current') {
            section.dataset.timelineToday = 'true';
        }

        const marker = document.createElement('span');
        marker.className = 'dashboard-agenda-day__marker';
        marker.setAttribute('aria-hidden', 'true');

        const header = document.createElement('div');
        header.className = 'dashboard-agenda-day__header';
        const heading = document.createElement('h3');
        const dateStrong = document.createElement('strong');
        dateStrong.textContent = `${day.dateLabel} ${day.weekdayLabel}`.trim();
        heading.appendChild(dateStrong);
        const rel = (day.relativeLabel || '').trim();
        if (rel) {
            const relSpan = document.createElement('span');
            relSpan.className = `dashboard-agenda-day__rel is-${status}`;
            relSpan.textContent = rel;
            heading.appendChild(relSpan);
        }
        const sub = document.createElement('p');
        sub.textContent = [day.yearLabel, `${day.items.length} 节课`].filter(Boolean).join(' · ');
        header.append(heading, sub);

        const list = document.createElement('div');
        list.className = 'dashboard-agenda-day__sessions';
        day.items.forEach((item) => list.appendChild(buildTimelineSession(item)));

        const body = document.createElement('div');
        body.className = 'dashboard-agenda-day__body';
        body.append(header, list);

        section.append(marker, body);
        return section;
    }

    function renderTimelineGroups(visibleCards) {
        const timelineItems = visibleCards.flatMap((card) => {
            const state = cardState.get(card);
            return (state?.timelineItems || []).map((item) => ({ ...item, card }));
        }).sort(compareTimelineItems);

        if (!timelineItems.length) {
            const emptyShell = document.createElement('div');
            emptyShell.className = 'dashboard-agenda dashboard-agenda--empty';
            const empty = document.createElement('div');
            empty.className = 'dashboard-timeline-empty';
            const title = document.createElement('strong');
            title.textContent = '当前筛选范围内，还没有可归纳的课次。';
            const copy = document.createElement('p');
            copy.textContent = '可以切换搜索或标签筛选，或在课堂管理里补齐首次上课日期、每周安排与课堂时间轴。';
            empty.append(title, copy);
            emptyShell.appendChild(empty);
            offeringList.appendChild(emptyShell);
            return;
        }

        const days = groupTimelineDays(timelineItems);
        const pastDays = days.filter((day) => timelineDayStatus(day) === 'past');
        const aheadDays = days.filter((day) => timelineDayStatus(day) !== 'past');
        const pastSessionCount = pastDays.reduce((sum, day) => sum + day.items.length, 0);
        const aheadSessionCount = aheadDays.reduce((sum, day) => sum + day.items.length, 0);
        const hasToday = aheadDays.some((day) => timelineDayStatus(day) === 'current');

        const shell = document.createElement('div');
        shell.className = 'dashboard-agenda';

        const head = document.createElement('div');
        head.className = 'dashboard-agenda__head';
        const summary = document.createElement('div');
        summary.className = 'dashboard-agenda__summary';
        const summaryTitle = document.createElement('strong');
        summaryTitle.textContent = hasToday ? '今天有课' : '已按上课时间排好';
        const summaryNote = document.createElement('span');
        summaryNote.textContent = `已结束 ${pastSessionCount} 节 · 今后 ${aheadSessionCount} 节`;
        summary.append(summaryTitle, summaryNote);
        head.appendChild(summary);
        const todayBtn = document.createElement('button');
        todayBtn.type = 'button';
        todayBtn.className = 'dashboard-agenda__today-btn';
        todayBtn.textContent = '回到今天';
        head.appendChild(todayBtn);
        shell.appendChild(head);

        const track = document.createElement('div');
        track.className = 'dashboard-agenda__track';

        if (pastDays.length) {
            const pastWrap = document.createElement('div');
            pastWrap.className = 'dashboard-agenda__past';
            const toggle = document.createElement('button');
            toggle.type = 'button';
            toggle.className = 'dashboard-agenda__past-toggle';
            const pastBody = document.createElement('div');
            pastBody.className = 'dashboard-agenda__past-body';
            pastDays.forEach((day) => pastBody.appendChild(buildTimelineDay(day)));

            const syncPast = () => {
                toggle.setAttribute('aria-expanded', String(timelinePastExpanded));
                toggle.textContent = timelinePastExpanded
                    ? `收起已结束的 ${pastSessionCount} 节课`
                    : `查看已结束的 ${pastSessionCount} 节课（${pastDays.length} 天）`;
                pastBody.hidden = !timelinePastExpanded;
                pastWrap.classList.toggle('is-open', timelinePastExpanded);
            };
            toggle.addEventListener('click', () => {
                timelinePastExpanded = !timelinePastExpanded;
                syncPast();
            });
            syncPast();

            pastWrap.append(toggle, pastBody);
            track.appendChild(pastWrap);
        }

        if (!hasToday) {
            const divider = document.createElement('div');
            divider.className = 'dashboard-agenda__divider';
            divider.dataset.timelineToday = 'true';
            const dot = document.createElement('span');
            dot.className = 'dashboard-agenda__divider-dot';
            dot.setAttribute('aria-hidden', 'true');
            const label = document.createElement('span');
            label.textContent = '今天 · 暂无课程安排';
            divider.append(dot, label);
            track.appendChild(divider);
        }

        aheadDays.forEach((day) => track.appendChild(buildTimelineDay(day)));

        shell.appendChild(track);
        offeringList.appendChild(shell);

        const scrollToToday = () => {
            const anchor = track.querySelector('[data-timeline-today="true"]');
            if (anchor && typeof anchor.scrollIntoView === 'function') {
                anchor.scrollIntoView({ block: 'center', behavior: 'smooth' });
                anchor.classList.add('is-pinged');
                window.setTimeout(() => anchor.classList.remove('is-pinged'), 1200);
            }
        };
        todayBtn.addEventListener('click', scrollToToday);
    }

    function createGroupSection({ key, title, subtitle, activityLabel, count, level, tone }) {
        const isCollapsed = collapsedGroups.has(key);
        const bodyId = `dashboard-group-body-${++groupSectionSerial}`;
        const section = document.createElement('section');
        section.className = `dashboard-group-section dashboard-group-section--level-${level} dashboard-group-section--${tone}`;
        section.dataset.groupSection = '';

        const header = document.createElement('div');
        header.className = 'dashboard-group-header';
        header.tabIndex = 0;
        header.setAttribute('role', 'button');
        header.setAttribute('aria-controls', bodyId);
        const copy = document.createElement('div');
        copy.className = 'dashboard-group-header__copy';
        const heading = document.createElement('h3');
        heading.textContent = title || '未分类';
        const note = document.createElement('p');
        note.textContent = subtitle || `${count} 个课堂`;
        copy.append(heading, note);

        const actions = document.createElement('div');
        actions.className = 'dashboard-group-header__actions';
        if (activityLabel) {
            const activityPill = document.createElement('span');
            activityPill.className = 'dashboard-group-activity';
            activityPill.textContent = activityLabel;
            actions.appendChild(activityPill);
        }
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
        body.id = bodyId;
        const bodyInner = document.createElement('div');
        bodyInner.className = 'dashboard-group-body__inner';
        body.appendChild(bodyInner);

        const setCollapsed = (nextCollapsed, { persist = true } = {}) => {
            const isCurrentlyCollapsed = section.classList.contains('is-collapsed');
            const shouldAnimate = persist && isCurrentlyCollapsed !== nextCollapsed && body.isConnected;
            if (shouldAnimate) {
                animateGroupBody(section, body, nextCollapsed);
            } else {
                section.classList.toggle('is-collapsed', nextCollapsed);
                body.classList.remove('is-animating');
                body.style.height = nextCollapsed ? '0px' : 'auto';
            }
            body.setAttribute('aria-hidden', String(nextCollapsed));
            header.setAttribute('aria-expanded', String(!nextCollapsed));
            header.setAttribute('aria-label', `${title || '当前分组'}，${nextCollapsed ? '已收缩，点击展开' : '已展开，点击收缩'}`);
            toggle.setAttribute('aria-expanded', String(!nextCollapsed));
            toggle.setAttribute('aria-label', `${nextCollapsed ? '展开' : '折叠'}${title || '当前分组'}`);
            if ('inert' in bodyInner) {
                bodyInner.inert = nextCollapsed;
            }
            if (!persist) {
                return;
            }
            if (nextCollapsed) {
                collapsedGroups.add(key);
            } else {
                collapsedGroups.delete(key);
            }
            writeJsonStorage('dashboard:teacher-collapsed-groups', Array.from(collapsedGroups));
        };

        const toggleCollapsed = () => {
            setCollapsed(!section.classList.contains('is-collapsed'));
        };

        toggle.addEventListener('click', (event) => {
            event.stopPropagation();
            toggleCollapsed();
        });

        header.addEventListener('click', (event) => {
            if (isNativeInteractiveElement(event.target)) {
                return;
            }
            event.stopPropagation();
            toggleCollapsed();
        });

        header.addEventListener('keydown', (event) => {
            if (event.key !== 'Enter' && event.key !== ' ') {
                return;
            }
            event.preventDefault();
            toggleCollapsed();
        });

        section.addEventListener('click', (event) => {
            if (!section.classList.contains('is-collapsed') || isNativeInteractiveElement(event.target)) {
                return;
            }
            setCollapsed(false);
        });

        setCollapsed(isCollapsed, { persist: false });
        section.append(header, body);
        return { section, body: bodyInner };
    }

    function buildGroupActivityLabel(group) {
        const activeUsers = Math.round(group.maxRecentUserCount || 0);
        if (activeUsers > 0) {
            return `近${recentActivityDays}天活跃 ${activeUsers} 人`;
        }
        const logins = Math.round(group.maxRecentLoginCount || 0);
        if (logins > 0) {
            return `近${recentActivityDays}天登录 ${logins} 次`;
        }
        return '';
    }

    function isNativeInteractiveElement(target) {
        return Boolean(target?.closest?.('a, button, input, select, textarea, label, summary, [contenteditable="true"]'));
    }

    function animateGroupBody(section, body, nextCollapsed) {
        if (body._dashboardGroupAnimationCleanup) {
            body._dashboardGroupAnimationCleanup();
        }

        const startHeight = body.getBoundingClientRect().height;
        body.classList.add('is-animating');
        body.style.height = `${startHeight}px`;
        body.offsetHeight;
        section.classList.toggle('is-collapsed', nextCollapsed);

        const targetHeight = nextCollapsed ? 0 : body.scrollHeight;
        let done = false;
        let targetApplied = false;
        const cleanup = (event) => {
            if (!targetApplied) {
                return;
            }
            if (event?.type === 'transitionend' && (event.target !== body || event.propertyName !== 'height')) {
                return;
            }
            if (done) {
                return;
            }
            done = true;
            body.removeEventListener('transitionend', cleanup);
            body.classList.remove('is-animating');
            body.style.height = nextCollapsed ? '0px' : 'auto';
            body._dashboardGroupAnimationCleanup = null;
        };
        body._dashboardGroupAnimationCleanup = cleanup;
        body.addEventListener('transitionend', cleanup);

        window.requestAnimationFrame(() => {
            targetApplied = true;
            body.style.height = `${targetHeight}px`;
            window.setTimeout(() => cleanup(), 280);
        });
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
            const activityCompared = compareCardsByActivity(a, b);
            if (activityCompared !== 0) {
                return activityCompared;
            }
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
                buckets.set(rawKey, {
                    key: rawKey,
                    label: label || '未分类',
                    items: [],
                    maxActivityScore: 0,
                    totalActivityScore: 0,
                    maxRecentUserCount: 0,
                    totalRecentUserCount: 0,
                    maxRecentLoginCount: 0,
                    totalRecentLoginCount: 0,
                    maxLastActivitySort: 0,
                });
            }
            const bucket = buckets.get(rawKey);
            bucket.items.push(card);
            addCardActivityToGroup(bucket, card);
        });
        return Array.from(buckets.values()).sort(compareGroupsByActivity);
    }

    function addCardActivityToGroup(group, card) {
        const state = cardState.get(card) || {};
        group.maxActivityScore = Math.max(group.maxActivityScore, state.activityScore || 0);
        group.totalActivityScore += state.activityScore || 0;
        group.maxRecentUserCount = Math.max(group.maxRecentUserCount, state.recentUserCount || 0);
        group.totalRecentUserCount += state.recentUserCount || 0;
        group.maxRecentLoginCount = Math.max(group.maxRecentLoginCount, state.recentLoginCount || 0);
        group.totalRecentLoginCount += state.recentLoginCount || 0;
        group.maxLastActivitySort = Math.max(group.maxLastActivitySort, state.lastActivitySort || 0);
    }

    function compareGroupsByActivity(a, b) {
        const fields = [
            'maxRecentUserCount',
            'totalRecentUserCount',
            'maxRecentLoginCount',
            'totalRecentLoginCount',
            'maxActivityScore',
            'totalActivityScore',
            'maxLastActivitySort',
        ];
        for (const field of fields) {
            const difference = (b[field] || 0) - (a[field] || 0);
            if (difference !== 0) {
                return difference;
            }
        }
        return compareText(a.label, b.label);
    }

    function compareCardsByActivity(a, b) {
        const stateA = cardState.get(a) || {};
        const stateB = cardState.get(b) || {};
        const fields = ['recentUserCount', 'recentLoginCount', 'activityScore', 'lastActivitySort'];
        for (const field of fields) {
            const difference = (stateB[field] || 0) - (stateA[field] || 0);
            if (difference !== 0) {
                return difference;
            }
        }
        return 0;
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
                    yearLabel: item.year_label || getYearLabel(item.starts_at),
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

    function getYearLabel(value) {
        const dateValue = new Date(value || '');
        if (!Number.isFinite(dateValue.getTime())) {
            return '';
        }
        return `${dateValue.getFullYear()}年`;
    }

    function compareTimelineItems(a, b) {
        return compareText(a.starts_at, b.starts_at)
            || compareTimelineSession(a, b)
            || compareText(a.course_name, b.course_name)
            || compareText(a.class_name, b.class_name)
            || compareText(a.title, b.title);
    }

    function compareTimelineSession(a, b) {
        if (a.card && b.card) {
            const activityCompared = compareCardsByActivity(a.card, b.card);
            if (activityCompared !== 0) {
                return activityCompared;
            }
        }
        return compareText(a.course_name, b.course_name)
            || compareText(a.class_name, b.class_name)
            || compareText(a.title, b.title);
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
