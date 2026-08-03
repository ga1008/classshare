import { formatDate, showMessage } from '/static/js/ui.js';
import { initSemesterCalendar } from '/static/js/semester_calendar.js?v=ux-empty-collapse-20260803';
import { createScheduleDeck } from '/static/js/course_schedule_deck.js?v=deck3d-20260707';

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

// Short labels for the agenda event types shown on each session row.
const AGENDA_KIND_LABELS = {
    class: '上课',
    invigilation: '监考',
    exam: '考试',
    assignment: '作业',
    todo: '待办',
};

function readAgendaEvents(root) {
    const node = root.querySelector('[data-dashboard-agenda-events]');
    if (!node) {
        return [];
    }
    try {
        const parsed = JSON.parse(node.textContent || '[]');
        return Array.isArray(parsed) ? parsed.filter((item) => item && item.date_full_label) : [];
    } catch (error) {
        return [];
    }
}

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
    const agendaEvents = readAgendaEvents(root);
    const filterButtons = Array.from(root.querySelectorAll('[data-filter-value]'));
    const groupModeButtons = Array.from(root.querySelectorAll('[data-group-mode]'));
    const searchForm = root.querySelector('[data-dashboard-search-form]');
    const semesterSelect = root.querySelector('[data-semester-filter]');
    const filterField = root.querySelector('[data-dashboard-filter-field]');
    const searchInput = root.querySelector('[data-dashboard-search]');
    const visibleCount = root.querySelector('[data-visible-count]');
    const resultsSummary = root.querySelector('[data-results-summary]');
    const offeringList = root.querySelector('[data-offering-list]');
    const emptySearch = root.querySelector('[data-empty-search]');
    const resetButton = root.querySelector('[data-reset-search]');
    const semesterCalendarRoot = root.querySelector('[data-semester-calendar-root]');
    const emptySearchChips = root.querySelector('[data-empty-search-chips]');
    const emptySearchSuggestions = root.querySelector('[data-empty-search-suggestions]');
    const dashboardRole = root.dataset.dashboardRole || 'teacher';
    const storagePrefix = `dashboard:${dashboardRole}`;

    const cardState = new Map();
    const collator = new Intl.Collator('zh-Hans-CN', { numeric: true, sensitivity: 'base' });
    const recentActivityDays = toNumber(root.dataset.recentActivityDays) || 14;
    const groupModeLabels = {
        department: '系别班级',
        course: '课程',
        timeline: '时间轴',
        schedule3d: '3D课表',
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
    const savedGroupMode = readStorageValue(`${storagePrefix}:group-mode`);
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

    const collapsedGroups = new Set(readJsonStorage(`${storagePrefix}:collapsed-groups`, []));

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
            semesterKey: String(card.dataset.semesterKey || ''),
            semesterLabel: String(card.dataset.semesterLabel || '未设学期'),
            visible: !card.hidden,
        });
    });

    // 学年学期筛选：默认定位今天所在学期；用户显式选择后记忆。
    const currentSemesterKey = String(root.dataset.currentSemesterKey || '');
    const semesterOptionValues = new Set(
        Array.from(semesterSelect?.options || []).map((option) => option.value),
    );
    let activeSemesterKey = '';
    if (semesterSelect) {
        const savedSemesterKey = readStorageValue(`${storagePrefix}:semester-key`);
        if (savedSemesterKey !== null && savedSemesterKey !== undefined && semesterOptionValues.has(savedSemesterKey)) {
            activeSemesterKey = savedSemesterKey;
        } else if (currentSemesterKey && semesterOptionValues.has(currentSemesterKey)) {
            activeSemesterKey = currentSemesterKey;
        }
        semesterSelect.value = activeSemesterKey;
    }

    function semesterKeyToTerm(key) {
        // 规范学期 key = identity.code，如 "2025-2026-2"（学年区间 + 学期号）。
        const matched = /^(\d{4}-\d{4})-([12])$/.exec(String(key || ''));
        return matched ? { year: matched[1], term: matched[2] } : null;
    }

    function activeSemesterLabel() {
        const option = semesterSelect?.selectedOptions?.[0];
        return option ? option.textContent.replace(/（\d+）\s*$/, '').trim() : '';
    }

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

    function setupCockpitPulseCollapse() {
        const pulse = root.querySelector('[data-cockpit-pulse]');
        const toggle = root.querySelector('[data-cockpit-pulse-toggle]');
        const body = root.querySelector('[data-cockpit-pulse-body]');
        if (!pulse || !toggle || !body) {
            return;
        }
        const storageKey = 'lanshare:cockpit-pulse-collapsed';
        const narrowQuery = window.matchMedia('(max-width: 720px)');
        const applyCollapsed = (collapsed) => {
            const effectiveCollapsed = narrowQuery.matches && collapsed;
            pulse.classList.toggle('is-collapsed', effectiveCollapsed);
            body.hidden = effectiveCollapsed;
            toggle.setAttribute('aria-expanded', String(!effectiveCollapsed));
            toggle.textContent = effectiveCollapsed
                ? `展开 ${body.querySelectorAll('.student-cockpit-pulse').length} 门课程`
                : '收起';
        };
        let collapsed = readStorageValue(storageKey);
        let isCollapsed = collapsed === null || collapsed === '' ? true : collapsed !== 'false';
        applyCollapsed(isCollapsed);
        toggle.addEventListener('click', () => {
            isCollapsed = !isCollapsed;
            writeStorageValue(storageKey, String(isCollapsed));
            applyCollapsed(isCollapsed);
        });
        if (typeof narrowQuery.addEventListener === 'function') {
            narrowQuery.addEventListener('change', () => applyCollapsed(isCollapsed));
        }
    }

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
        if (activeSemesterKey) {
            fragments.push(`学期：${activeSemesterLabel() || activeSemesterKey}`);
        }
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

    const clearSearchCriterion = (kind) => {
        if (kind === 'filter') {
            activeFilter = 'all';
        }
        if (kind === 'keyword' && searchInput) {
            searchInput.value = '';
        }
        applyFilters();
    };

    const getSuggestionScore = (state, keyword) => {
        const compactKeyword = compactText(keyword);
        if (!compactKeyword) {
            return 0;
        }
        const pairs = [];
        for (let index = 0; index < compactKeyword.length - 1; index += 1) {
            pairs.push(compactKeyword.slice(index, index + 2));
        }
        const tokens = pairs.length ? pairs : Array.from(new Set(compactKeyword.split('')));
        return tokens.reduce((score, token) => score + (state.searchCompact.includes(token) ? token.length : 0), 0);
    };

    const renderEmptySearchHelp = (keyword) => {
        if (!emptySearch) {
            return;
        }
        if (emptySearchChips) {
            emptySearchChips.replaceChildren();
            const chips = [];
            if (activeFilter !== 'all') {
                chips.push({
                    kind: 'filter',
                    label: `筛选：${filterLabels.get(activeFilter) || activeFilter}`,
                });
            }
            if (keyword) {
                chips.push({
                    kind: 'keyword',
                    label: `关键词：${keyword}`,
                });
            }
            chips.forEach((chip) => {
                const button = document.createElement('button');
                button.type = 'button';
                button.className = 'dashboard-empty-search__chip';
                button.dataset.emptySearchChip = chip.kind;
                button.textContent = `${chip.label} ×`;
                emptySearchChips.appendChild(button);
            });
        }

        if (!emptySearchSuggestions) {
            return;
        }
        emptySearchSuggestions.replaceChildren();
        const suggestions = cards
            .map((card) => ({ card, state: cardState.get(card) }))
            .filter((item) => item.state)
            .map((item) => ({
                ...item,
                score: getSuggestionScore(item.state, keyword),
            }))
            .filter((item) => item.score > 0)
            .sort((a, b) => b.score - a.score || compareCardsByActivity(a.card, b.card))
            .slice(0, 3);
        if (!suggestions.length) {
            return;
        }
        const title = document.createElement('strong');
        title.textContent = '你可能想找';
        const list = document.createElement('div');
        list.className = 'dashboard-empty-search__suggestion-list';
        suggestions.forEach(({ card, state }) => {
            const href = card.querySelector('.dashboard-offering-card__enter')?.getAttribute('href') || '#';
            const link = document.createElement('a');
            link.href = href;
            link.className = 'dashboard-empty-search__suggestion';
            const name = document.createElement('span');
            name.textContent = state.courseName || card.dataset.courseName || '课堂';
            const meta = document.createElement('small');
            meta.textContent = [state.className, card.dataset.teacherName].filter(Boolean).join(' · ') || '进入课堂查看';
            link.append(name, meta);
            list.appendChild(link);
        });
        emptySearchSuggestions.append(title, list);
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
            const matchesSemester = !activeSemesterKey
                || (state?.semesterKey || '') === activeSemesterKey;
            const visible = Boolean(matchesKeyword && matchesSemester && matchesFilter(card));
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
        // 3D课表模式不依赖课堂卡片的可见性：始终显示课表面板，不显示空态。
        const isSchedule3d = groupModeButtons.length && activeGroupMode === 'schedule3d';
        if (offeringList) {
            offeringList.hidden = isSchedule3d ? false : count === 0;
            renderOfferingList(visibleCards);
        }
        if (emptySearch) {
            emptySearch.hidden = isSchedule3d || count !== 0;
            if (!emptySearch.hidden) {
                renderEmptySearchHelp(keyword);
            }
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
            writeStorageValue(`${storagePrefix}:group-mode`, activeGroupMode);
            applyFilters();
        });
    });

    searchForm?.addEventListener('submit', (event) => {
        event.preventDefault();
        applyFilters();
    });

    semesterSelect?.addEventListener('change', () => {
        activeSemesterKey = semesterSelect.value || '';
        writeStorageValue(`${storagePrefix}:semester-key`, activeSemesterKey);
        // 3D课表模式：顶部学期切换直接驱动课表数据重载。
        if (activeGroupMode === 'schedule3d') {
            const term = semesterKeyToTerm(activeSemesterKey);
            loadScheduleOverview(term ? { year: term.year, term: term.term } : {});
        }
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
            searchInput.blur();
        }
    });

    document.addEventListener('keydown', (event) => {
        if (!searchInput || event.defaultPrevented || event.key !== '/') {
            return;
        }
        if (window.matchMedia('(max-width: 720px), (pointer: coarse)').matches) {
            return;
        }
        if (isNativeInteractiveElement(event.target)) {
            return;
        }
        event.preventDefault();
        searchInput.focus({ preventScroll: true });
        searchInput.select();
    });

    emptySearch?.addEventListener('click', (event) => {
        const button = event.target.closest('[data-empty-search-chip]');
        if (!button) {
            return;
        }
        clearSearchCriterion(button.dataset.emptySearchChip || '');
    });

    setupCockpitPulseCollapse();

    resetButton?.addEventListener('click', () => {
        activeFilter = 'all';
        if (searchInput) {
            searchInput.value = '';
        }
        applyFilters();
    });

    /* ---------------- 3D课表归纳方式 ----------------
     * 复用可移植的 course_schedule_deck 模块，仅提供学年学期切换，
     * 不显示课时统计页的课程/班级筛选与归集信息。面板与 deck 实例
     * 跨 applyFilters 重渲染持久存在，DOM 节点被移动而非重建。
     *
     * 这些 let 声明必须排在下面的 applyFilters({syncUrl:false}) 首次调用之前：
     * 若上次访问时归纳方式记忆为 schedule3d，首次调用就会同步触发
     * renderOfferingList → getScheduleDeckPanel，在声明语句执行前引用
     * 会命中暂时性死区（TDZ）抛 ReferenceError，导致整个模块脚本中断
     * 执行——3D课表面板、归纳方式按钮、搜索等全部失效（一度表现为
     * "从新建课堂页面返回后 3D 课表一片空白"）。 */
    let scheduleDeckPanel = null;
    let scheduleDeck = null;
    let scheduleDeckStatus = null;
    let scheduleDeckLoaded = false;
    let scheduleDeckLoading = false;

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

    function getScheduleDeckPanel() {
        if (!scheduleDeckPanel) {
            scheduleDeckPanel = document.createElement('div');
            scheduleDeckPanel.className = 'dashboard-schedule3d';
            scheduleDeckStatus = document.createElement('div');
            scheduleDeckStatus.className = 'dashboard-schedule3d__status';
            scheduleDeckStatus.hidden = true;
            scheduleDeckStatus.setAttribute('aria-live', 'polite');
            scheduleDeckStatus.addEventListener('click', (event) => {
                if (event.target.closest('[data-schedule3d-retry]')) {
                    loadScheduleOverview();
                }
            });
            const mount = document.createElement('div');
            scheduleDeckPanel.append(scheduleDeckStatus, mount);
            scheduleDeck = createScheduleDeck(mount, {
                title: '3D 课表',
                description: '滚轮或方向键切换周次，点击卡片放大；点击课程进入课堂。',
                showTermSelect: true,
                onTermChange: (year, term) => loadScheduleOverview({ year, term }),
                emptyHtml: () => '<strong>暂无课表数据</strong><p>请先到 <a href="/manage/teaching/course-schedule">课时统计</a> 同步智慧课堂课程表。</p>',
            });
        }
        if (!scheduleDeckLoaded && !scheduleDeckLoading) {
            const term = semesterKeyToTerm(activeSemesterKey);
            loadScheduleOverview(term ? { year: term.year, term: term.term } : {});
        }
        return scheduleDeckPanel;
    }

    function setScheduleDeckStatus(html) {
        if (!scheduleDeckStatus) {
            return;
        }
        scheduleDeckStatus.hidden = !html;
        scheduleDeckStatus.innerHTML = html || '';
    }

    async function loadScheduleOverview({ year = '', term = '' } = {}) {
        if (!scheduleDeck || scheduleDeckLoading) {
            return;
        }
        scheduleDeckLoading = true;
        setScheduleDeckStatus('正在加载课表…');
        try {
            const params = new URLSearchParams({ year, term });
            const response = await fetch(`/api/manage/teaching/course-schedule/overview?${params.toString()}`, {
                credentials: 'same-origin',
            });
            if (!response.ok) {
                throw new Error(`课表加载失败（${response.status}）`);
            }
            const data = await response.json();
            scheduleDeckLoaded = true;
            setScheduleDeckStatus('');
            scheduleDeck.setOverview(data.overview, { keepWeek: false });
        } catch (error) {
            const message = error instanceof Error ? error.message : '课表加载失败。';
            setScheduleDeckStatus(
                `${normalizeText(message) ? message.replace(/[<>&"]/g, '') : '课表加载失败。'} `
                + '<button type="button" class="dashboard-schedule3d__retry" data-schedule3d-retry>重试</button>',
            );
            showMessage(message, 'error');
        } finally {
            scheduleDeckLoading = false;
        }
    }

    function renderOfferingList(visibleCards) {
        if (!offeringList) {
            return;
        }

        offeringList.replaceChildren();
        offeringList.className = 'dashboard-offering-grid';
        offeringList.removeAttribute('aria-label');

        if (groupModeButtons.length && activeGroupMode === 'schedule3d') {
            offeringList.classList.add('is-schedule3d');
            offeringList.appendChild(getScheduleDeckPanel());
            return;
        }

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
        const kind = item.kind || 'class';
        const session = document.createElement('a');
        session.className = `dashboard-agenda-session is-${item.status || 'upcoming'}`;
        session.dataset.kind = kind;
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
        const titleRow = document.createElement('span');
        titleRow.className = 'dashboard-agenda-session__titlerow';
        const chip = document.createElement('span');
        chip.className = `dashboard-agenda-session__kind kind-${kind}`;
        chip.textContent = AGENDA_KIND_LABELS[kind] || '日程';
        const title = document.createElement('strong');
        title.textContent = item.title || item.course_name || '课堂安排';
        titleRow.append(chip, title);
        const meta = document.createElement('span');
        meta.className = 'dashboard-agenda-session__meta';
        meta.textContent = item.subtitle
            || [item.course_name, item.class_name, item.week_label, item.section_label].filter(Boolean).join(' · ');
        body.append(titleRow, meta);

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
        const cardItems = visibleCards.flatMap((card) => {
            const state = cardState.get(card);
            return (state?.timelineItems || []).map((item) => ({ ...item, kind: item.kind || 'class', card }));
        });
        const timelineItems = [...cardItems, ...agendaEvents].sort(compareTimelineItems);

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
        summaryTitle.textContent = hasToday ? '今天有安排' : '日程已按时间排好';
        const summaryNote = document.createElement('span');
        summaryNote.textContent = `已结束 ${pastSessionCount} 项 · 今后 ${aheadSessionCount} 项`;
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
                    ? `收起已结束的 ${pastSessionCount} 项`
                    : `查看已结束的 ${pastSessionCount} 项（${pastDays.length} 天）`;
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
            writeJsonStorage(`${storagePrefix}:collapsed-groups`, Array.from(collapsedGroups));
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

// ── 个性化欢迎语（AI 每日一句，就绪后滚动替换默认问候） ──────────────
(function initPersonalGreeting() {
    const node = document.querySelector('[data-personal-greeting]');
    if (!node) return;

    const RETRY_DELAY_MS = 25000;
    let retried = false;

    function rollReplace(text) {
        if (!text || text === node.textContent) return;
        const reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches;
        if (reducedMotion) {
            node.textContent = text;
            return;
        }
        node.classList.add('personal-greeting-roll-out');
        window.setTimeout(() => {
            node.textContent = text;
            node.classList.remove('personal-greeting-roll-out');
            node.classList.add('personal-greeting-roll-in');
            window.setTimeout(() => node.classList.remove('personal-greeting-roll-in'), 700);
        }, 380);
    }

    async function fetchGreeting() {
        try {
            const response = await fetch('/api/learning/personal-greeting', {
                credentials: 'same-origin',
                headers: { Accept: 'application/json' },
            });
            if (!response.ok) return;
            const payload = await response.json();
            const greeting = payload?.greeting;
            if (greeting?.status === 'ready' && greeting.text) {
                rollReplace(greeting.text);
            } else if (greeting?.status === 'pending' && !retried) {
                // 后台 AI 正在排队生成；稍后再看一眼，仍没好就保持默认文案。
                retried = true;
                window.setTimeout(fetchGreeting, RETRY_DELAY_MS);
            }
        } catch (error) {
            // 欢迎语是点缀，失败静默。
        }
    }

    const idle = window.requestIdleCallback || ((fn) => window.setTimeout(fn, 800));
    idle(fetchGreeting);
})();

// ── 教师课堂教学评价：低频同步 + 本地详情浮窗 ─────────────────────
(function initAcademicEvaluationExperience() {
    const syncConfig = window.DASHBOARD_ACADEMIC_EVALUATION_SYNC || {};
    const panel = document.querySelector('[data-academic-evaluation-sync-panel]');
    const syncButton = document.querySelector('[data-academic-evaluation-sync-action]');
    const syncTitle = document.querySelector('[data-academic-evaluation-sync-title]');
    const syncStatus = document.querySelector('[data-academic-evaluation-sync-status]');
    const syncTriggerLabel = document.querySelector('[data-academic-evaluation-sync-trigger-label]');
    const modal = document.querySelector('[data-academic-evaluation-modal]');
    const dialog = modal?.querySelector('.academic-evaluation-modal__dialog');
    const modalBody = modal?.querySelector('[data-academic-evaluation-modal-body]');
    const modalTitle = modal?.querySelector('[data-academic-evaluation-modal-title]');
    const modalSubtitle = modal?.querySelector('[data-academic-evaluation-modal-subtitle]');
    if (!panel && !modal) return;

    let syncing = false;
    let activeOfferingId = '';
    let returnFocus = null;
    let abortController = null;

    const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    })[char]);
    const finite = (value, fallback = 0) => {
        const number = Number(value);
        return Number.isFinite(number) ? number : fallback;
    };
    const scoreText = (value) => Number.isFinite(Number(value)) ? Number(value).toFixed(1) : '--';
    const sentiment = (value) => ['positive', 'improvement', 'neutral'].includes(value) ? value : 'neutral';

    function keywordHtml(keyword, withCount = false) {
        const count = Math.max(0, finite(keyword?.count));
        return `<span class="evaluation-keyword evaluation-keyword--${sentiment(keyword?.sentiment)}">${escapeHtml(keyword?.label || '')}${withCount && count ? `<small>×${count}</small>` : ''}</span>`;
    }

    function heatmapHtml(items, tone) {
        if (!Array.isArray(items) || !items.length) {
            const empty = tone === 'improvement' ? '暂未形成高频缺点' : '暂无可归纳的高频词';
            return `<span class="academic-evaluation-heatmap__empty">${empty}</span>`;
        }
        const maxCount = Math.max(...items.map((item) => Math.max(1, finite(item?.count, 1))));
        return items.map((item) => {
            const count = Math.max(1, finite(item?.count, 1));
            const level = Math.max(1, Math.min(5, Math.ceil((count / maxCount) * 5)));
            const confidence = Math.round(Math.max(0, Math.min(1, finite(item?.confidence, .7))) * 100);
            return `<span class="academic-evaluation-heatword is-${tone} heat-${level}" title="出现 ${count} 次 · 置信度 ${confidence}%">
                ${escapeHtml(item?.label || '')}<small>${count}</small>
            </span>`;
        }).join('');
    }

    function cardSummaryHtml(overview) {
        const keywords = Array.isArray(overview?.keywords) ? overview.keywords.slice(0, 3) : [];
        const keywordContent = keywords.length
            ? `<div class="dashboard-evaluation-card__keywords" aria-label="学生评语高频词">${keywords.map((item) => keywordHtml(item)).join('')}</div>`
            : `<span class="dashboard-evaluation-card__pending">${overview?.ai_keyword_status === 'running' ? '正在提炼评语高频词…' : '暂无可提炼的文字评语'}</span>`;
        const responseRate = overview?.response_rate == null ? '' : `<span>有效率 ${escapeHtml(overview.response_rate)}%</span>`;
        const score = Math.max(0, Math.min(100, finite(overview?.score)));
        return `
            <section class="dashboard-evaluation-card" data-academic-evaluation-summary>
                <div class="dashboard-evaluation-card__score" style="--evaluation-score:${score}">
                    <span>总体评价</span><strong>${escapeHtml(overview?.score_display || scoreText(score))}</strong><small>/ 100</small>
                </div>
                <div class="dashboard-evaluation-card__insight">
                    <div class="dashboard-evaluation-card__meta">
                        <span>${Math.max(0, finite(overview?.valid_response_count))} 份有效评价</span>${responseRate}<span>${escapeHtml(overview?.freshness_label || '刚刚更新')}</span>
                    </div>
                    ${keywordContent}
                </div>
                <button type="button" class="dashboard-evaluation-card__detail" data-academic-evaluation-open="${escapeHtml(overview?.offering_id || '')}">
                    查看详情
                    <svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m9 18 6-6-6-6"/></svg>
                </button>
            </section>`;
    }

    function updateOfferingCards(overviews) {
        Object.entries(overviews || {}).forEach(([offeringId, overview]) => {
            const card = document.querySelector(`[data-offering-card][data-offering-id="${CSS.escape(offeringId)}"]`);
            if (!card || !overview?.available) return;
            const enriched = { ...overview, offering_id: offeringId };
            const current = card.querySelector('[data-academic-evaluation-summary]');
            const metrics = card.querySelector('.dashboard-offering-card__metrics');
            if (current) {
                current.outerHTML = cardSummaryHtml(enriched);
            } else if (metrics) {
                metrics.insertAdjacentHTML('beforebegin', cardSummaryHtml(enriched));
            }
        });
    }

    function setSyncUi(state, message = '') {
        if (!panel) return;
        panel.classList.toggle('is-syncing', state === 'running');
        if (syncButton) {
            syncButton.disabled = state === 'running';
            const label = syncButton.querySelector('span');
            if (label) label.textContent = state === 'running' ? '同步中…' : '手动同步';
        }
        if (syncTriggerLabel) syncTriggerLabel.textContent = state === 'running' ? '同步中' : '评价';
        panel.querySelector('.dashboard-evaluation-menu__state')?.classList.toggle('is-ready', state === 'ready');
        if (syncTitle) {
            syncTitle.textContent = state === 'running'
                ? '正在同步教学评价'
                : state === 'failed' ? '本次同步未完成，已保留原数据' : '评价数据已对齐课堂';
        }
        if (syncStatus && message) syncStatus.textContent = message;
    }

    async function synchronize(force = false, automatic = false) {
        if (syncing || !syncConfig.endpoint) return;
        syncing = true;
        setSyncUi('running', automatic ? '页面继续可用；请求将严格串行，完成后自动更新卡片' : '正在安全读取已发布评价，请稍候…');
        try {
            const response = await fetch(syncConfig.endpoint, {
                method: 'POST',
                credentials: 'same-origin',
                headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
                body: JSON.stringify({ force }),
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(payload.detail || payload.message || `同步失败（${response.status}）`);
            updateOfferingCards(payload.offerings);
            const failed = payload.status === 'failed';
            setSyncUi(failed ? 'failed' : 'ready', payload.message || (failed ? '同步未完成' : '教学评价已更新'));
            if (!automatic || failed) showMessage(payload.message || (failed ? '教学评价同步未完成' : '教学评价已更新'), failed ? 'error' : 'success');
        } catch (error) {
            setSyncUi('failed', `${error.message || '网络异常'}；课堂仍显示上次同步结果`);
            if (!automatic) showMessage(error.message || '教学评价同步失败', 'error');
        } finally {
            syncing = false;
            if (syncButton) syncButton.disabled = false;
        }
    }

    function loadingHtml() {
        return `<div class="academic-evaluation-modal__loading"><span class="academic-evaluation-modal__loader" aria-hidden="true"></span><strong>正在整理课堂评价…</strong><small>评价洞察、量化指标与有效评语即将呈现</small></div>`;
    }

    function metricScore(metric) {
        return Math.max(0, Math.min(100, finite(metric?.satisfaction_score ?? metric?.mean_score)));
    }

    function metricShortName(metric, index) {
        const fullName = String(metric?.metric_name || '').trim();
        const firstClause = fullName.split(/[，,。；;：:]/)[0]
            .replace(/^(课程|课堂|教师|学生)/, '')
            .replace(/^(能否|是否|能够)/, '')
            .trim();
        if (!firstClause) return `指标 ${index + 1}`;
        return firstClause.length > 9 ? `${firstClause.slice(0, 9)}…` : firstClause;
    }

    function sortedMetrics(metrics, mode = 'desc') {
        return metrics.map((metric, index) => ({ metric, index })).sort((left, right) => {
            if (mode === 'source') return left.index - right.index;
            const difference = metricScore(right.metric) - metricScore(left.metric);
            return mode === 'asc' ? -difference || left.index - right.index : difference || left.index - right.index;
        });
    }

    function metricHtml(metric, index, rank) {
        const satisfaction = Math.max(0, Math.min(100, finite(metric?.satisfaction_score ?? metric?.mean_score)));
        const fullName = metric?.metric_name || '评价指标';
        return `<article class="academic-evaluation-metric" tabindex="0" title="${escapeHtml(fullName)}" aria-label="${escapeHtml(fullName)}，得分 ${scoreText(satisfaction)}">
            <span class="academic-evaluation-metric__rank">${rank + 1}</span>
            <strong>${escapeHtml(metricShortName(metric, index))}</strong>
            <em>${scoreText(satisfaction)}</em>
            <div class="academic-evaluation-metric__track"><span style="--metric-progress:${satisfaction}%"></span></div>
            <span class="academic-evaluation-metric__tooltip" role="tooltip">${escapeHtml(fullName)}</span>
        </article>`;
    }

    function metricGridHtml(metrics, mode = 'desc') {
        return sortedMetrics(metrics, mode)
            .map(({ metric, index }, rank) => metricHtml(metric, index, rank))
            .join('');
    }

    function commentsHtml(comments) {
        if (!Array.isArray(comments) || !comments.length) return '<div class="academic-evaluation-muted">这一评价维度暂无有效文字评语</div>';
        return `<div class="academic-evaluation-comments">${comments.map((comment, index) => `
            <article class="academic-evaluation-comment"${index >= 8 ? ' data-comment-extra hidden' : ''}>
                <span class="academic-evaluation-comment__index">${index + 1}</span>
                <p>${escapeHtml(comment?.text || '')}</p>
            </article>`).join('')}</div>
            ${comments.length > 8 ? `<button type="button" class="academic-evaluation-comments__toggle" data-comment-toggle aria-expanded="false">展开其余 ${comments.length - 8} 条</button>` : ''}`;
    }

    function sourcePanelHtml(source, index) {
        const metrics = Array.isArray(source?.metrics) ? source.metrics : [];
        const comments = Array.isArray(source?.comments) ? source.comments : [];
        return `<div class="academic-evaluation-source-panel${index === 0 ? ' is-active' : ''}" data-evaluation-source-panel="${index}">
            <div class="academic-evaluation-source-meta">
                <span>课程得分 ${scoreText(source?.course_score)}</span>
                <span>有效评价 ${Math.max(0, finite(source?.valid_response_count))} / ${Math.max(0, finite(source?.response_count))}</span>
                ${source?.campus_name ? `<span>${escapeHtml(source.campus_name)}</span>` : ''}
                ${source?.institution_rank ? `<span>学校排名 ${escapeHtml(source.institution_rank)}</span>` : ''}
            </div>
            <div class="academic-evaluation-section__header academic-evaluation-section__header--metrics">
                <div><h3>分项指标</h3><span>${metrics.length} 项 · 悬停查看完整说明</span></div>
                <div class="academic-evaluation-sort" role="group" aria-label="分项指标排序">
                    <button type="button" class="is-active" data-metric-sort="desc" aria-pressed="true">高到低</button>
                    <button type="button" data-metric-sort="asc" aria-pressed="false">低到高</button>
                    <button type="button" data-metric-sort="source" aria-pressed="false">原顺序</button>
                </div>
            </div>
            ${metrics.length ? `<div class="academic-evaluation-metrics" data-metric-grid>${metricGridHtml(metrics)}</div>` : '<div class="academic-evaluation-muted">暂无分项评分数据</div>'}
            <div class="academic-evaluation-section__header"><h3>匿名学生评语</h3><span>保留 ${comments.length} 条${source?.filtered_comment_count ? ` · 已过滤 ${Math.max(0, finite(source.filtered_comment_count))} 条低信息文本` : ''}</span></div>
            ${commentsHtml(comments)}
        </div>`;
    }

    function renderDetail(payload) {
        if (!payload?.available) {
            modalBody.innerHTML = `<div class="academic-evaluation-modal__empty"><strong>暂未匹配到这门课堂的已发布评价</strong><small>${escapeHtml(payload?.message || '完成低频同步后再来看看')}</small></div>`;
            return;
        }
        const overall = payload.overall || {};
        const sources = Array.isArray(payload.sources) ? payload.sources : [];
        const keywords = Array.isArray(payload.keywords) ? payload.keywords : [];
        const strengths = Array.isArray(payload.strength_keywords)
            ? payload.strength_keywords
            : keywords.filter((item) => item?.sentiment !== 'improvement');
        const improvements = Array.isArray(payload.improvement_keywords)
            ? payload.improvement_keywords
            : keywords.filter((item) => item?.sentiment === 'improvement');
        const summaries = Array.isArray(payload.ai_summaries) ? payload.ai_summaries.filter(Boolean) : [];
        const score = Math.max(0, Math.min(100, finite(overall.score)));
        modalTitle.textContent = payload.offering?.course_name || '教学评价详情';
        modalSubtitle.textContent = `${payload.offering?.class_name || '课堂'} · ${payload.offering?.semester_name || '当前学期'} · ${overall.freshness_label || '本地同步数据'}`;
        modalBody.innerHTML = `
            <div class="academic-evaluation-overview">
                <section class="academic-evaluation-score-panel">
                    <div class="academic-evaluation-score-panel__number" style="--evaluation-score:${score}">${escapeHtml(overall.score_display || scoreText(score))}</div>
                    <div class="academic-evaluation-score-panel__copy"><span>Overall score</span><strong>总体评价 / 100</strong><small>${Math.max(0, finite(overall.valid_response_count))} 份有效评价 · ${Math.max(0, finite(overall.comment_count))} 条有效评语</small></div>
                </section>
                <section class="academic-evaluation-insight-panel">
                    <h3>评价洞察</h3>
                    <p>${escapeHtml(summaries[0] || '匿名评语仅用于聚合教学反馈，不推断或识别学生身份。')}</p>
                    <div class="academic-evaluation-heatmaps">
                        <section class="academic-evaluation-heatmap is-strength"><h4>优势热力图</h4><div>${heatmapHtml(strengths, 'strength')}</div></section>
                        <section class="academic-evaluation-heatmap is-improvement"><h4>缺点热力图</h4><div>${heatmapHtml(improvements, 'improvement')}</div></section>
                    </div>
                </section>
            </div>
            <section class="academic-evaluation-section">
                <div class="academic-evaluation-section__header"><h3>评价明细</h3><span>${sources.length} 组课时类型</span></div>
                <div class="academic-evaluation-source-tabs" role="tablist" aria-label="评价课时类型">
                    ${sources.map((source, index) => `<button type="button" role="tab" aria-selected="${index === 0}" class="academic-evaluation-source-tab${index === 0 ? ' is-active' : ''}" data-evaluation-source-tab="${index}">${escapeHtml(source?.hour_type_name || `评价 ${index + 1}`)}</button>`).join('')}
                </div>
                ${sources.map(sourcePanelHtml).join('')}
            </section>
            <p class="academic-evaluation-frequency">${escapeHtml(payload.frequency_note || '')} 本页详情始终读取本地镜像。</p>`;
        modalBody.querySelectorAll('[data-evaluation-source-tab]').forEach((tab) => {
            tab.addEventListener('click', () => {
                const index = tab.dataset.evaluationSourceTab;
                modalBody.querySelectorAll('[data-evaluation-source-tab]').forEach((item) => {
                    const active = item === tab;
                    item.classList.toggle('is-active', active);
                    item.setAttribute('aria-selected', String(active));
                });
                modalBody.querySelectorAll('[data-evaluation-source-panel]').forEach((item) => item.classList.toggle('is-active', item.dataset.evaluationSourcePanel === index));
            });
        });
        modalBody.querySelectorAll('[data-metric-sort]').forEach((button) => {
            button.addEventListener('click', () => {
                const sourcePanel = button.closest('[data-evaluation-source-panel]');
                const sourceIndex = Number(sourcePanel?.dataset.evaluationSourcePanel);
                const metrics = Array.isArray(sources[sourceIndex]?.metrics) ? sources[sourceIndex].metrics : [];
                const grid = sourcePanel?.querySelector('[data-metric-grid]');
                if (!grid) return;
                grid.innerHTML = metricGridHtml(metrics, button.dataset.metricSort || 'desc');
                sourcePanel.querySelectorAll('[data-metric-sort]').forEach((item) => {
                    const active = item === button;
                    item.classList.toggle('is-active', active);
                    item.setAttribute('aria-pressed', String(active));
                });
            });
        });
        modalBody.querySelectorAll('[data-comment-toggle]').forEach((button) => {
            button.addEventListener('click', () => {
                const expanded = button.getAttribute('aria-expanded') === 'true';
                button.closest('[data-evaluation-source-panel]')?.querySelectorAll('[data-comment-extra]')
                    .forEach((item) => { item.hidden = expanded; });
                button.setAttribute('aria-expanded', String(!expanded));
                const hiddenCount = button.closest('[data-evaluation-source-panel]')?.querySelectorAll('[data-comment-extra]').length || 0;
                button.textContent = expanded ? `展开其余 ${hiddenCount} 条` : '收起评语';
            });
        });
    }

    async function openDetail(offeringId, trigger) {
        if (!modal || !modalBody || !offeringId) return;
        activeOfferingId = String(offeringId);
        returnFocus = trigger || document.activeElement;
        abortController?.abort();
        abortController = new AbortController();
        modal.hidden = false;
        document.body.classList.add('academic-evaluation-modal-open');
        modalBody.innerHTML = loadingHtml();
        const card = document.querySelector(`[data-offering-card][data-offering-id="${CSS.escape(activeOfferingId)}"]`);
        if (modalTitle) modalTitle.textContent = card?.dataset.courseName || '教学评价详情';
        if (modalSubtitle) modalSubtitle.textContent = '读取本地同步结果，不会再次访问教务系统';
        window.requestAnimationFrame(() => dialog?.focus());
        try {
            const response = await fetch(`/api/academic-evaluations/classrooms/${encodeURIComponent(activeOfferingId)}`, {
                credentials: 'same-origin',
                headers: { Accept: 'application/json' },
                signal: abortController.signal,
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(payload.detail || '评价详情读取失败');
            if (!modal.hidden && activeOfferingId === String(offeringId)) renderDetail(payload);
        } catch (error) {
            if (error.name === 'AbortError') return;
            modalBody.innerHTML = `<div class="academic-evaluation-modal__error"><strong>评价详情暂时无法读取</strong><small>${escapeHtml(error.message || '请稍后再试')}</small></div>`;
        }
    }

    function closeDetail() {
        if (!modal || modal.hidden) return;
        abortController?.abort();
        modal.hidden = true;
        document.body.classList.remove('academic-evaluation-modal-open');
        activeOfferingId = '';
        if (returnFocus instanceof HTMLElement) returnFocus.focus();
    }

    document.addEventListener('click', (event) => {
        if (panel?.open && !panel.contains(event.target)) panel.removeAttribute('open');
        const trigger = event.target.closest('[data-academic-evaluation-open]');
        if (trigger) {
            event.preventDefault();
            event.stopPropagation();
            openDetail(trigger.dataset.academicEvaluationOpen, trigger);
            return;
        }
        if (event.target.closest('[data-academic-evaluation-close]')) closeDetail();
    });
    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && panel?.open) panel.removeAttribute('open');
        if (!modal || modal.hidden) return;
        if (event.key === 'Escape') {
            event.preventDefault();
            closeDetail();
            return;
        }
        if (event.key !== 'Tab' || !dialog) return;
        const focusable = Array.from(dialog.querySelectorAll('button:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])'));
        if (!focusable.length) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    });

    syncButton?.addEventListener('click', () => synchronize(true, false));
    if (syncConfig.should_auto_sync && syncConfig.has_credential) {
        const idle = window.requestIdleCallback || ((callback) => window.setTimeout(callback, 1400));
        idle(() => synchronize(false, true));
    }
})();

// ===== UX overhaul 2026-08 · 阶段 9 移动端分区手风琴 =====
// 窄屏（<640px）把辅助分区默认收起为「标题一行」，点击标题展开/收起；
// 展开偏好记忆在 localStorage，桌面端完全不介入。
(() => {
    const MOBILE_QUERY = window.matchMedia('(max-width: 639px)');
    const STORAGE_KEY = 'lanshare:dashboard-mobile-expanded';
    const sections = Array.from(document.querySelectorAll('[data-mobile-collapse]'));
    if (!sections.length) return;

    const readExpanded = () => {
        try {
            return new Set(JSON.parse(window.localStorage.getItem(STORAGE_KEY) || '[]'));
        } catch {
            return new Set();
        }
    };
    const writeExpanded = (expanded) => {
        try {
            window.localStorage.setItem(STORAGE_KEY, JSON.stringify(Array.from(expanded)));
        } catch {
            /* 存储不可用时静默降级为不记忆 */
        }
    };

    const expanded = readExpanded();

    const apply = () => {
        sections.forEach((section) => {
            const key = section.dataset.mobileCollapse || '';
            const shouldCollapse = MOBILE_QUERY.matches && !expanded.has(key);
            section.classList.toggle('is-mobile-collapsed', shouldCollapse);
        });
    };

    // 事件委托：岛屿（如快捷入口）会整体重渲染 header 节点，逐节点绑定会丢失监听
    document.addEventListener('click', (event) => {
        if (!MOBILE_QUERY.matches) return;
        const header = event.target.closest('.dashboard-panel__header, .semester-calendar-panel__header');
        if (!header) return;
        const section = header.closest('[data-mobile-collapse]');
        if (!section) return;
        if (event.target.closest('button, a, select, input')) return;
        const key = section.dataset.mobileCollapse || '';
        if (expanded.has(key)) {
            expanded.delete(key);
        } else {
            expanded.add(key);
        }
        writeExpanded(expanded);
        apply();
    });

    if (typeof MOBILE_QUERY.addEventListener === 'function') {
        MOBILE_QUERY.addEventListener('change', apply);
    }
    apply();
})();
