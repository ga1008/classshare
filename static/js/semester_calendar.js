import { apiFetch } from '/static/js/api.js';

const MS_PER_DAY = 1000 * 60 * 60 * 24;
const dayLabels = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];
const compactDayLabels = ['一', '二', '三', '四', '五', '六', '日'];
const monthFormatter = new Intl.DateTimeFormat('zh-CN', { month: 'numeric' });
const dateFormatter = new Intl.DateTimeFormat('zh-CN', {
    month: 'numeric',
    day: 'numeric',
    weekday: 'short',
});

function normalizeCalendarConfig(config = {}) {
    return {
        semesters: Array.isArray(config.semesters) ? config.semesters : [],
        holidayLookup: config.holidayLookup || config.holiday_lookup || {},
        todayIso: String(config.todayIso || config.today_iso || ''),
        defaultSemesterId: config.defaultSemesterId ?? config.default_semester_id ?? null,
        showTodos: Boolean(config.showTodos || config.show_todos),
    };
}

export function parseIsoDate(isoDate) {
    const normalized = String(isoDate || '').trim();
    if (!normalized) {
        return null;
    }
    const parts = normalized.split('-').map((part) => Number(part));
    if (parts.length < 3 || parts.some((part) => !Number.isFinite(part))) {
        return null;
    }
    return new Date(parts[0], parts[1] - 1, parts[2]);
}

export function formatIsoDate(date) {
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, '0');
    const day = String(date.getDate()).padStart(2, '0');
    return `${year}-${month}-${day}`;
}

function formatMonthTitle(date) {
    return `${date.getFullYear()}年${date.getMonth() + 1}月`;
}

function compareIsoDate(left, right) {
    return String(left || '').localeCompare(String(right || ''));
}

export function addDays(date, amount) {
    const next = new Date(date.getTime());
    next.setDate(next.getDate() + amount);
    return next;
}

export function getMonday(date) {
    const weekday = date.getDay() || 7;
    return addDays(date, 1 - weekday);
}

export function getSunday(date) {
    const weekday = date.getDay() || 7;
    return addDays(date, 7 - weekday);
}

export function computeSemesterWeekCount(startDate, endDate) {
    if (!startDate || !endDate || endDate < startDate) {
        return 0;
    }
    const calendarStart = getMonday(startDate);
    const calendarEnd = getSunday(endDate);
    return Math.floor((calendarEnd - calendarStart) / (MS_PER_DAY * 7)) + 1;
}

function normalizeSemester(item) {
    const numericId = Number(item?.id);
    const weekCount = Number(item?.week_count || 0);
    return {
        ...item,
        id: Number.isFinite(numericId) ? numericId : item?.id,
        week_count: Number.isFinite(weekCount) ? weekCount : 0,
        is_current: Boolean(item?.is_current),
        calendar_days: Array.isArray(item?.calendar_days) ? item.calendar_days : [],
        calendar_sync_status: String(item?.calendar_sync_status || 'pending'),
        calendar_sync_at: String(item?.calendar_sync_at || ''),
        calendar_sync_message: String(item?.calendar_sync_message || ''),
        todo_overview: item?.todo_overview || { items: [], weeks: [], summary: {}, role_policy: {} },
        todo_create_options: Array.isArray(item?.todo_create_options) ? item.todo_create_options : [],
    };
}

function buildSemesterDayLookup(semester) {
    const lookup = {};
    (Array.isArray(semester?.calendar_days) ? semester.calendar_days : []).forEach((day) => {
        const isoDate = String(day?.date || '').trim();
        if (!isoDate) return;
        const kind = String(day?.kind || day?.day_type || '').trim();
        if (!kind) return;
        lookup[isoDate] = {
            kind,
            label: day?.label || (kind === 'workday' ? '调休上课' : '节假日'),
            scope: day?.scope || day?.source || 'semester',
            source: day?.source || '',
            source_url: day?.source_url || '',
            confidence: day?.confidence ?? null,
        };
    });
    return lookup;
}

function computeMonthGroups(weeks) {
    const labels = weeks.map((weekStart) => {
        const monthNames = new Set();
        for (let index = 0; index < 7; index += 1) {
            monthNames.add(monthFormatter.format(addDays(weekStart, index)));
        }
        return Array.from(monthNames).join(' / ');
    });

    const groups = [];
    labels.forEach((label, index) => {
        const lastGroup = groups[groups.length - 1];
        if (lastGroup && lastGroup.label === label) {
            lastGroup.span += 1;
            return;
        }
        groups.push({ label, start: index, span: 1 });
    });
    return groups;
}

function getStatusCopy(semester, startDate, endDate, todayDate) {
    if (semester?.is_current) {
        return '进行中';
    }
    if (todayDate && startDate && todayDate < startDate) {
        return '未开始';
    }
    if (todayDate && endDate && todayDate > endDate) {
        return '已结束';
    }
    return '待确认';
}

function buildSemesterModel(semester, holidayLookup, todayIso, modelCache, extraPaddingWeekCount = 1) {
    if (!semester) {
        return null;
    }

    const paddingWeekCount = Math.max(1, Math.min(16, Math.ceil(Number(extraPaddingWeekCount) || 1)));
    const cacheKey = [
        semester.id,
        semester.start_date,
        semester.end_date,
        semester.week_count,
        semester.calendar_sync_at,
        semester.calendar_days?.length || 0,
        todayIso,
        paddingWeekCount,
    ].join(':');
    if (modelCache.has(cacheKey)) {
        return modelCache.get(cacheKey);
    }

    const startDate = parseIsoDate(semester.start_date);
    const endDate = parseIsoDate(semester.end_date);
    if (!startDate || !endDate) {
        return null;
    }

    const todayDate = parseIsoDate(todayIso);
    const semesterDayLookup = buildSemesterDayLookup(semester);
    const semesterCalendarStart = getMonday(startDate);
    const semesterCalendarEnd = getSunday(endDate);
    const calendarStart = addDays(semesterCalendarStart, -7 * paddingWeekCount);
    const calendarEnd = addDays(semesterCalendarEnd, 7 * paddingWeekCount);
    const weeks = [];
    let holidayCount = 0;
    let workdayCount = 0;
    let currentWeekNumber = null;
    let semesterWeekNumber = 0;

    for (let cursor = new Date(calendarStart); cursor <= calendarEnd; cursor = addDays(cursor, 7)) {
        const weekStart = new Date(cursor);
        const weekEnd = addDays(weekStart, 6);
        const isPaddingWeek = weekEnd < startDate || weekStart > endDate;
        const currentSemesterWeekNumber = isPaddingWeek ? null : (semesterWeekNumber += 1);
        const isCurrentWeek = Boolean(
            semester.is_current
            && !isPaddingWeek
            && todayDate
            && todayDate >= weekStart
            && todayDate <= weekEnd,
        );
        if (isCurrentWeek) {
            currentWeekNumber = currentSemesterWeekNumber;
        }

        const days = [];
        for (let dayIndex = 0; dayIndex < 7; dayIndex += 1) {
            const currentDate = addDays(weekStart, dayIndex);
            const isoDate = formatIsoDate(currentDate);
            const holidayInfo = semesterDayLookup[isoDate] || holidayLookup[isoDate] || null;
            const inSemester = currentDate >= startDate && currentDate <= endDate;
            const isWeekend = currentDate.getDay() === 0 || currentDate.getDay() === 6;
            const isHoliday = holidayInfo?.kind === 'holiday';
            const isWorkday = holidayInfo?.kind === 'workday';

            if (inSemester && isHoliday) {
                holidayCount += 1;
            }
            if (inSemester && isWorkday) {
                workdayCount += 1;
            }

            days.push({
                date: currentDate,
                isoDate,
                label: inSemester ? dateFormatter.format(currentDate) : dateFormatter.format(currentDate),
                holidayInfo,
                inSemester,
                isPaddingWeek,
                isWeekend,
                isHoliday,
                isWorkday,
                isToday: isoDate === todayIso,
                isCurrentWeek,
            });
        }

        weeks.push({
            start: weekStart,
            end: weekEnd,
            isCurrentWeek,
            isPaddingWeek,
            semesterWeekNumber: currentSemesterWeekNumber,
            days,
        });
    }

    const model = {
        startDate,
        endDate,
        todayDate,
        weeks,
        monthGroups: computeMonthGroups(weeks.map((item) => item.start)),
        holidayCount,
        workdayCount,
        currentWeekNumber,
        statusCopy: getStatusCopy(semester, startDate, endDate, todayDate),
    };
    modelCache.set(cacheKey, model);
    return model;
}

function createCell(fragment, className, text, row, column, columnSpan = 1) {
    const cell = document.createElement('div');
    cell.className = className;
    if (text != null) {
        cell.textContent = text;
    }
    cell.style.gridRow = String(row);
    cell.style.gridColumn = `${column} / span ${columnSpan}`;
    fragment.appendChild(cell);
    return cell;
}

function emptyTodoOverview() {
    return {
        items: [],
        weeks: [],
        summary: {},
        role_policy: {},
        active_week_key: '',
    };
}

function normalizeTodoOverview(semester) {
    const overview = semester?.todo_overview || emptyTodoOverview();
    return {
        ...emptyTodoOverview(),
        ...overview,
        items: Array.isArray(overview.items) ? overview.items : [],
        weeks: Array.isArray(overview.weeks) ? overview.weeks : [],
        summary: overview.summary || {},
        role_policy: overview.role_policy || {},
    };
}

function sourceLabel(todo) {
    const labels = {
        lesson: '课堂',
        assignment: '作业',
        stage_exam: '试炼',
        manual: '我的待办',
        academic_exam: '考试',
        academic_course_exam: '考试',
        academic_invigilation: '监考',
    };
    return labels[todo?.source_type] || todo?.subtitle || '待办';
}

function sourceTone(todo) {
    return String(todo?.tone || todo?.source_type || 'neutral').replace(/[^a-z0-9_-]/gi, '') || 'neutral';
}

function manualTodoId(todo) {
    return Number(todo?.source_id || String(todo?.id || '').split(':').pop() || 0);
}

function isAcademicCalendarEvent(todo) {
    return ['academic_exam', 'academic_course_exam', 'academic_invigilation'].includes(String(todo?.source_type || ''));
}

function compactCourseName(value) {
    return String(value || '')
        .replace(/^教务(?:考试|监考)[：:]\s*/, '')
        .replace(/考试安排$/, '')
        .trim();
}

function shortLocation(value) {
    const text = String(value || '').trim();
    const roomMatch = text.match(/([A-Za-z]\d{3,4}[A-Za-z]?|[A-Za-z]-?\d{3,4}[A-Za-z]?|\d{3,4})$/);
    if (roomMatch) return roomMatch[1].replace('-', '');
    const embedded = text.match(/([A-Za-z]\d{3,4}[A-Za-z]?)/);
    if (embedded) return embedded[1];
    return text;
}

function academicEventParts(todo) {
    const label = String(todo?.source_type || '') === 'academic_invigilation' ? '监考' : '考试';
    const metadata = todo?.metadata || {};
    const course = compactCourseName(
        todo?.course_name
        || metadata.course_name
        || todo?.title
        || label,
    );
    const location = shortLocation(todo?.location || metadata.location || '');
    return {
        label,
        course: course || label,
        location: location || '',
    };
}

function findTodoById(semester, todoId) {
    const overview = normalizeTodoOverview(semester);
    return overview.items.find((item) => String(item.id) === String(todoId)) || null;
}

function getWeekTodos(semester, weekStart) {
    const weekKey = formatIsoDate(weekStart);
    const overview = normalizeTodoOverview(semester);
    const week = overview.weeks.find((item) => String(item.key) === weekKey);
    return Array.isArray(week?.todos) ? week.todos : [];
}

function getWeekKey(week) {
    return week?.start ? formatIsoDate(week.start) : '';
}

function clampNumber(value, min, max) {
    return Math.max(min, Math.min(max, value));
}

function summarizeTodoTitles(todos, limit = 3) {
    const titles = (Array.isArray(todos) ? todos : [])
        .map((todo) => String(todo?.title || '').trim())
        .filter(Boolean)
        .slice(0, limit);
    return titles.length ? titles.join(' / ') : '本周暂无待办';
}

function recalcTodoOverview(overview) {
    const items = Array.isArray(overview.items) ? overview.items : [];
    const weeks = Array.isArray(overview.weeks) ? overview.weeks : [];
    weeks.forEach((week) => {
        const todos = Array.isArray(week.todos) ? week.todos : [];
        week.todo_count = todos.length;
        week.open_count = todos.filter((item) => !item.is_completed).length;
    });
    overview.summary = {
        total_count: items.length,
        open_count: items.filter((item) => !item.is_completed).length,
        manual_count: items.filter((item) => item.source_type === 'manual').length,
        due_soon_count: items.filter((item) => String(item.relative_due_label || '').includes('后截止')).length,
        no_deadline_count: items.filter((item) => item.no_deadline).length,
    };
}

function enrichTodoFromOption(todo, option) {
    return {
        ...todo,
        class_offering_id: Number(option?.class_offering_id || todo?.class_offering_id || 0),
        course_name: option?.course_name || todo?.course_name || '',
        class_name: option?.class_name || todo?.class_name || '',
        offering_label: option?.label || todo?.offering_label || [option?.course_name, option?.class_name].filter(Boolean).join(' · '),
    };
}

function mergeClassTodoOverview(semester, classOfferingId, nextOverview, option) {
    const overview = normalizeTodoOverview(semester);
    const targetId = Number(classOfferingId || 0);
    const enrichedItems = (Array.isArray(nextOverview?.items) ? nextOverview.items : [])
        .map((item) => enrichTodoFromOption(item, option));

    const weekMap = new Map();
    overview.weeks.forEach((week) => {
        const todos = (Array.isArray(week.todos) ? week.todos : [])
            .filter((todo) => Number(todo.class_offering_id || 0) !== targetId);
        weekMap.set(String(week.key), { ...week, todos });
    });

    (Array.isArray(nextOverview?.weeks) ? nextOverview.weeks : []).forEach((week) => {
        const key = String(week.key || '');
        if (!key) return;
        const existing = weekMap.get(key) || {
            key,
            week_index: week.week_index,
            label: week.label || '',
            range_label: week.range_label || '',
            todos: [],
            is_current: Boolean(week.is_current),
        };
        existing.todos = [
            ...(existing.todos || []),
            ...(Array.isArray(week.todos) ? week.todos : []).map((todo) => enrichTodoFromOption(todo, option)),
        ];
        existing.is_current = Boolean(existing.is_current || week.is_current);
        weekMap.set(key, existing);
    });

    overview.items = [
        ...overview.items.filter((todo) => Number(todo.class_offering_id || 0) !== targetId),
        ...enrichedItems,
    ].sort((a, b) => String(a.effective_end_at || a.effective_start_at || '').localeCompare(String(b.effective_end_at || b.effective_start_at || '')));
    overview.weeks = Array.from(weekMap.values())
        .filter((week) => (week.todos || []).length > 0)
        .sort((a, b) => String(a.key || '').localeCompare(String(b.key || '')));
    recalcTodoOverview(overview);
    semester.todo_overview = overview;
    return overview;
}

export function initSemesterCalendar(root, config = {}, options = {}) {
    if (!root) {
        return null;
    }

    const normalizedConfig = normalizeCalendarConfig(config);
    const holidayLookup = normalizedConfig.holidayLookup;
    const todayIso = normalizedConfig.todayIso;
    const defaultSemesterId = Number(normalizedConfig.defaultSemesterId || options.initialSemesterId || 0) || null;
    const showTodos = Boolean(
        options.showTodos
        || normalizedConfig.showTodos
        || root.dataset.semesterCalendarTodos === 'true',
    );
    const modelCache = new Map();
    const state = {
        semesters: normalizedConfig.semesters.map(normalizeSemester),
        activeSemesterId: null,
    };

    const elements = {
        select: root.querySelector('[data-semester-calendar-select]'),
        board: root.querySelector('[data-semester-calendar-board]'),
        scroll: root.querySelector('[data-semester-calendar-scroll]'),
        empty: root.querySelector('[data-semester-calendar-empty]'),
        overview: root.querySelector('[data-semester-calendar-overview]'),
        period: root.querySelector('[data-semester-calendar-period]'),
        periodNote: root.querySelector('[data-semester-calendar-period-note]'),
        weekRange: root.querySelector('[data-semester-calendar-week-range]'),
        progress: root.querySelector('[data-semester-calendar-progress]'),
        status: root.querySelector('[data-semester-calendar-status]'),
        holidaySummary: root.querySelector('[data-semester-calendar-holiday-summary]'),
        holidayNote: root.querySelector('[data-semester-calendar-holiday-note]'),
        scrollStartBtn: root.querySelector('[data-semester-calendar-scroll-start]'),
        scrollTodayBtn: root.querySelector('[data-semester-calendar-scroll-today]'),
        todoAddBtn: root.querySelector('[data-semester-todo-add]'),
        todoSummary: root.querySelector('[data-semester-todo-summary]'),
        todoDetail: root.querySelector('[data-semester-todo-detail]'),
    };

    const onChange = typeof options.onChange === 'function' ? options.onChange : null;
    const onMessage = typeof options.onMessage === 'function' ? options.onMessage : null;
    const prefersReducedMotion = Boolean(
        window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches,
    );
    let dragState = null;
    let activeTodoId = '';
    let activeWeekKey = '';
    let pendingScrollWeekKey = '';
    let snapTimer = 0;
    let ignoreClickUntil = 0;
    let suppressSnapUntil = 0;
    let todoModal = null;
    let todoPickerState = null;
    let resizeTimer = 0;

    function getSemesterById(semesterId) {
        return state.semesters.find((item) => item.id === Number(semesterId)) || null;
    }

    function getCalendarPaddingWeeks() {
        const viewportWidth = elements.scroll?.clientWidth || window.innerWidth || 1024;
        const centerSpaceNeeded = Math.max(0, (viewportWidth / 2) - 176);
        return Math.max(2, Math.min(14, Math.ceil(centerSpaceNeeded / 76) + 2));
    }

    function getSemesterModel(semester) {
        return buildSemesterModel(
            semester,
            holidayLookup,
            todayIso,
            modelCache,
            getCalendarPaddingWeeks(),
        );
    }

    function getActiveSemester() {
        return getSemesterById(state.activeSemesterId);
    }

    function getTodoWeekKey(todo) {
        const startDate = parseIsoDate(todo?.effective_start_date || todo?.effective_end_date);
        return startDate ? formatIsoDate(getMonday(startDate)) : '';
    }

    function getRealWeeks(model) {
        return (model?.weeks || []).filter((week) => !week.isPaddingWeek);
    }

    function getRealWeekByKey(model, weekKey) {
        const key = String(weekKey || '').trim();
        return getRealWeeks(model).find((week) => getWeekKey(week) === key) || null;
    }

    function getSemesterActiveWeekKey(semester) {
        const overview = normalizeTodoOverview(semester);
        return String(overview.active_week_key || '').trim();
    }

    function resolveActiveWeekKey(semester, model, preferredWeekKey = activeWeekKey) {
        const weeks = getRealWeeks(model);
        if (!weeks.length) return '';
        const preferred = String(preferredWeekKey || '').trim();
        if (preferred && getRealWeekByKey(model, preferred)) return preferred;
        const overviewKey = getSemesterActiveWeekKey(semester);
        if (overviewKey && getRealWeekByKey(model, overviewKey)) return overviewKey;
        const currentWeek = weeks.find((week) => week.isCurrentWeek);
        if (currentWeek) return getWeekKey(currentWeek);
        const firstTodoWeek = normalizeTodoOverview(semester).weeks
            .map((week) => String(week.key || '').trim())
            .find((weekKey) => weekKey && getRealWeekByKey(model, weekKey));
        return firstTodoWeek || getWeekKey(weeks[0]);
    }

    function scheduleWeekScroll(weekKey) {
        pendingScrollWeekKey = String(weekKey || '').trim();
        if (pendingScrollWeekKey) {
            activeWeekKey = pendingScrollWeekKey;
        }
    }

    function scrollToWeekKey(weekKey, behavior = 'smooth') {
        if (!weekKey || !elements.scroll || !elements.board) {
            return;
        }
        const target = elements.board.querySelector(`[data-week-key="${weekKey}"]`);
        if (!target) {
            return;
        }
        const maxScrollLeft = Math.max(0, elements.scroll.scrollWidth - elements.scroll.clientWidth);
        const scrollRect = elements.scroll.getBoundingClientRect();
        const targetRect = target.getBoundingClientRect();
        const delta = (targetRect.left + (targetRect.width / 2)) - (scrollRect.left + (scrollRect.width / 2));
        const left = clampNumber(elements.scroll.scrollLeft + delta, 0, maxScrollLeft);
        suppressSnapUntil = performance.now() + 240;
        elements.scroll.scrollTo({
            left,
            behavior: prefersReducedMotion ? 'auto' : behavior,
        });
    }

    function getWeekColumns(model) {
        return (model?.weeks || [])
            .map((week) => {
                if (week.isPaddingWeek) return '76px';
                return getWeekKey(week) === activeWeekKey ? '112px' : '88px';
            })
            .join(' ');
    }

    function applyActiveWeekVisuals(semester, model) {
        if (!elements.board || !model) return;
        elements.board.style.gridTemplateColumns = `132px ${getWeekColumns(model)}`;
        elements.board.querySelectorAll('[data-week-key]').forEach((node) => {
            node.classList.toggle('is-active-week', node.dataset.weekKey === activeWeekKey);
        });
        renderWeekTodoStage(semester, model);
        syncActiveTodoVisuals(semester);
    }

    function setActiveWeekKey(weekKey, options = {}) {
        const semester = getActiveSemester();
        const model = getSemesterModel(semester);
        const resolved = resolveActiveWeekKey(semester, model, weekKey);
        if (!resolved) return;
        activeWeekKey = resolved;
        if (activeTodoId) {
            const todo = findTodoById(semester, activeTodoId);
            if (!todo || getTodoWeekKey(todo) !== activeWeekKey) {
                activeTodoId = '';
            }
        }
        applyActiveWeekVisuals(semester, model);
        if (options.center !== false) {
            window.requestAnimationFrame(() => scrollToWeekKey(resolved, options.behavior || 'smooth'));
        }
    }

    function getNearestWeekKey() {
        if (!elements.scroll || !elements.board) return activeWeekKey;
        const weekHeaders = Array.from(elements.board.querySelectorAll('.semester-header-cell[data-week-key]'));
        if (!weekHeaders.length) return activeWeekKey;
        const scrollRect = elements.scroll.getBoundingClientRect();
        const viewportCenter = scrollRect.left + (scrollRect.width / 2);
        let nearest = weekHeaders[0];
        let nearestDistance = Number.POSITIVE_INFINITY;
        weekHeaders.forEach((header) => {
            const rect = header.getBoundingClientRect();
            const center = rect.left + (rect.width / 2);
            const distance = Math.abs(center - viewportCenter);
            if (distance < nearestDistance) {
                nearest = header;
                nearestDistance = distance;
            }
        });
        return nearest.dataset.weekKey || activeWeekKey;
    }

    function scheduleSnapToNearest(delay = 140) {
        if (!showTodos) return;
        window.clearTimeout(snapTimer);
        snapTimer = window.setTimeout(() => {
            if (dragState || performance.now() < suppressSnapUntil) return;
            setActiveWeekKey(getNearestWeekKey(), { behavior: 'smooth', center: true });
        }, delay);
    }

    function renderSelect() {
        if (!elements.select) {
            return;
        }

        elements.select.innerHTML = '';
        if (state.semesters.length === 0) {
            const option = document.createElement('option');
            option.value = '';
            option.textContent = '暂无学期';
            elements.select.appendChild(option);
            elements.select.disabled = true;
            return;
        }

        const fragment = document.createDocumentFragment();
        state.semesters.forEach((semester) => {
            const option = document.createElement('option');
            option.value = String(semester.id ?? '');
            option.textContent = `${semester.name || '未命名学期'} · ${semester.start_date || '--'}`;
            fragment.appendChild(option);
        });
        elements.select.appendChild(fragment);
        elements.select.disabled = false;

        if (state.activeSemesterId != null) {
            elements.select.value = String(state.activeSemesterId);
        }
    }

    function renderOverview(semester, model) {
        if (!elements.overview) {
            return;
        }

        if (!semester || !model) {
            elements.overview.hidden = true;
            return;
        }

        elements.overview.hidden = false;
        if (elements.period) {
            elements.period.textContent = `${semester.start_date || '--'} 至 ${semester.end_date || '--'}`;
        }
        if (elements.periodNote) {
            elements.periodNote.textContent = semester.name || '未命名学期';
        }
        if (elements.weekRange) {
            elements.weekRange.textContent = `第 1 周至第 ${semester.week_count || 0} 周`;
        }

        let progressText = model.statusCopy;
        let statusText = '当前日期不在本学期范围内';
        if (semester.is_current && model.currentWeekNumber) {
            progressText = `第 ${model.currentWeekNumber} 周`;
            statusText = '当前日期位于本学期范围内';
        } else if (model.todayDate && model.startDate && model.todayDate < model.startDate) {
            const days = Math.ceil((model.startDate - model.todayDate) / MS_PER_DAY);
            statusText = `距离开学还有 ${Math.max(days, 0)} 天`;
        } else if (model.todayDate && model.endDate && model.todayDate > model.endDate) {
            const days = Math.ceil((model.todayDate - model.endDate) / MS_PER_DAY);
            statusText = `距离结课已过去 ${Math.max(days, 0)} 天`;
        }

        if (elements.progress) {
            elements.progress.textContent = progressText;
        }
        if (elements.status) {
            elements.status.textContent = statusText;
        }
        if (elements.holidaySummary) {
            elements.holidaySummary.textContent = `${model.holidayCount} 天 / ${model.workdayCount} 天`;
        }
        if (elements.holidayNote) {
            elements.holidayNote.textContent = `法定节假日 ${model.holidayCount} 天，调休上课 ${model.workdayCount} 天`;
        }
    }

    function renderTodoToolbar(semester) {
        if (!showTodos) {
            return;
        }
        const overview = normalizeTodoOverview(semester);
        const summary = overview.summary || {};
        const totalCount = Number(summary.total_count || 0);
        const openCount = Number(summary.open_count || 0);
        const canCreate = Boolean(overview.role_policy?.can_create_manual && semester?.todo_create_options?.length);

        if (elements.todoAddBtn) {
            elements.todoAddBtn.hidden = !canCreate;
        }
        if (elements.todoSummary) {
            elements.todoSummary.hidden = totalCount <= 0 && !canCreate;
            elements.todoSummary.textContent = totalCount > 0
                ? `待办 ${totalCount} 项，未完成 ${openCount} 项`
                : '可在当前学期添加个人待办';
        }
    }

    function formatShortDate(date) {
        return `${date.getMonth() + 1}/${date.getDate()}`;
    }

    function getWeekDisplayLabel(week) {
        return week?.semesterWeekNumber ? `第 ${week.semesterWeekNumber} 周` : '衔接周';
    }

    function createWeekDateAxis(week, { compact = false } = {}) {
        const axis = document.createElement('div');
        axis.className = `semester-week-date-axis${compact ? ' is-compact' : ''}`;
        if (!week) {
            for (let index = 0; index < 7; index += 1) {
                const cell = document.createElement('span');
                cell.className = 'semester-week-date-axis__cell';
                cell.textContent = '--';
                axis.appendChild(cell);
            }
            return axis;
        }
        for (let index = 0; index < 7; index += 1) {
            const date = addDays(week.start, index);
            const cell = document.createElement('span');
            cell.className = 'semester-week-date-axis__cell';
            const day = document.createElement('em');
            day.textContent = compactDayLabels[index];
            const value = document.createElement('strong');
            value.textContent = formatShortDate(date);
            cell.append(day, value);
            axis.appendChild(cell);
        }
        return axis;
    }

    function parseTodoDateTime(value, fallbackDate, fallbackClock = '00:00') {
        const raw = String(value || '').trim();
        if (raw) {
            const normalized = raw.includes('T') ? raw : raw.replace(' ', 'T');
            const parsed = new Date(normalized);
            if (!Number.isNaN(parsed.getTime())) {
                return parsed;
            }
        }
        const datePart = String(fallbackDate || '').trim().slice(0, 10);
        if (!datePart) return null;
        const clockPart = String(fallbackClock || '00:00').trim().slice(0, 5) || '00:00';
        const fallback = new Date(`${datePart}T${clockPart}`);
        return Number.isNaN(fallback.getTime()) ? null : fallback;
    }

    function getTodoMinutePosition(todo, week) {
        const weekStartAt = new Date(week.start);
        weekStartAt.setHours(0, 0, 0, 0);
        const weekEndAt = addDays(weekStartAt, 7);
        const startAt = parseTodoDateTime(
            todo.effective_start_at,
            todo.effective_start_date || todo.effective_end_date,
            '00:00',
        ) || new Date(weekStartAt);
        const endAt = parseTodoDateTime(
            todo.effective_end_at || todo.due_at,
            todo.effective_end_date || todo.effective_start_date,
            todo.no_deadline ? '00:00' : '23:59',
        ) || new Date(startAt);
        const startTime = startAt.getTime();
        const endTime = Math.max(startTime, endAt.getTime());
        const weekStartTime = weekStartAt.getTime();
        const weekEndTime = weekEndAt.getTime();
        const totalMinutes = (weekEndTime - weekStartTime) / 60000;
        const clampedStart = clampNumber(startTime, weekStartTime, weekEndTime);
        const clampedEnd = clampNumber(endTime, weekStartTime, weekEndTime);
        const left = ((clampedStart - weekStartTime) / 60000 / totalMinutes) * 100;
        const width = ((Math.max(clampedEnd, clampedStart) - clampedStart) / 60000 / totalMinutes) * 100;
        return {
            left: clampNumber(left, 0, 100),
            width: clampNumber(Math.max(width, 0.72), 0.72, 100),
        };
    }

    function createWeekPreviewCard(semester, week, position) {
        const card = document.createElement('button');
        card.type = 'button';
        card.className = `semester-week-todo-peek is-${position}`;
        if (!week) {
            card.disabled = true;
            card.appendChild(createWeekDateAxis(null, { compact: true }));
            const empty = document.createElement('strong');
            empty.textContent = '无相邻周';
            card.appendChild(empty);
            return card;
        }
        const weekKey = getWeekKey(week);
        const todos = getWeekTodos(semester, week.start);
        card.dataset.weekKey = weekKey;
        card.appendChild(createWeekDateAxis(week, { compact: true }));
        const label = document.createElement('span');
        label.textContent = getWeekDisplayLabel(week);
        const summary = document.createElement('strong');
        summary.textContent = summarizeTodoTitles(todos);
        const count = document.createElement('small');
        count.textContent = todos.length ? `${todos.length} 项待办` : '暂无待办';
        card.append(label, summary, count);
        return card;
    }

    function createTodoItemCard(todo, week) {
        const row = document.createElement('article');
        const isActive = String(todo.id || '') === String(activeTodoId || '');
        row.className = `semester-week-todo-item-card is-${sourceTone(todo)}${todo.is_completed ? ' is-completed' : ''}${isActive ? ' is-active' : ''}${todo.link_url ? ' has-link' : ''}`;
        row.dataset.semesterTodoId = String(todo.id || '');
        if (todo.link_url) {
            row.dataset.todoHref = String(todo.link_url);
        }
        row.tabIndex = 0;
        row.setAttribute('role', todo.link_url ? 'link' : 'button');
        row.title = [todo.title || '待办', todo.duration_label, todo.offering_label].filter(Boolean).join(' / ');

        const copy = document.createElement('div');
        copy.className = 'semester-week-todo-item-card__copy';
        const eyebrow = document.createElement('span');
        eyebrow.className = 'semester-week-todo-item-card__eyebrow';
        eyebrow.textContent = [sourceLabel(todo), todo.offering_label || todo.course_name || '课堂']
            .filter(Boolean)
            .join(' / ');
        const title = document.createElement('strong');
        title.textContent = todo.title || '待办事项';
        copy.append(eyebrow, title);
        if (todo.notes) {
            const notes = document.createElement('p');
            notes.textContent = todo.notes;
            copy.appendChild(notes);
        }

        const meta = document.createElement('div');
        meta.className = 'semester-week-todo-item-card__meta';
        [todo.duration_label, todo.status_label || todo.relative_due_label, todo.deadline_label]
            .filter(Boolean)
            .forEach((text) => {
                const chip = document.createElement('span');
                chip.textContent = text;
                meta.appendChild(chip);
            });

        const progress = document.createElement('div');
        progress.className = 'semester-week-todo-progress';
        const progressLabel = document.createElement('small');
        progressLabel.textContent = todo.no_deadline
            ? '无截止日期'
            : (todo.due_time_label ? `${todo.due_time_label} 截止` : (todo.deadline_label || '截止时间待定'));
        const track = document.createElement('span');
        track.className = 'semester-week-todo-progress__track';
        const bar = document.createElement('span');
        bar.className = 'semester-week-todo-progress__bar';
        const position = getTodoMinutePosition(todo, week);
        bar.style.setProperty('--todo-left', `${position.left.toFixed(3)}%`);
        bar.style.setProperty('--todo-width', `${position.width.toFixed(3)}%`);
        track.appendChild(bar);
        progress.append(progressLabel, track);

        const actions = document.createElement('div');
        actions.className = 'semester-week-todo-item-card__actions';
        if (todo.can_complete) {
            const completeBtn = document.createElement('button');
            completeBtn.type = 'button';
            completeBtn.className = 'btn btn-outline btn-sm';
            completeBtn.dataset.semesterTodoComplete = String(manualTodoId(todo));
            completeBtn.dataset.semesterTodoId = String(todo.id || '');
            completeBtn.textContent = todo.is_completed ? '标记未完成' : '完成';
            actions.appendChild(completeBtn);

            const deleteBtn = document.createElement('button');
            deleteBtn.type = 'button';
            deleteBtn.className = 'btn btn-ghost btn-sm text-danger';
            deleteBtn.dataset.semesterTodoDelete = String(manualTodoId(todo));
            deleteBtn.dataset.semesterTodoId = String(todo.id || '');
            deleteBtn.textContent = '删除';
            actions.appendChild(deleteBtn);
        }

        row.append(copy, meta, progress);
        if (actions.childElementCount) {
            row.appendChild(actions);
        }
        return row;
    }

    function renderWeekTodoStage(semester, model) {
        if (!showTodos || !elements.todoDetail) return;
        if (!semester || !model || !(model.weeks || []).length) {
            elements.todoDetail.hidden = true;
            elements.todoDetail.innerHTML = '';
            return;
        }

        activeWeekKey = resolveActiveWeekKey(semester, model);
        const weeks = getRealWeeks(model);
        const activeIndex = Math.max(0, weeks.findIndex((week) => getWeekKey(week) === activeWeekKey));
        const activeWeek = weeks[activeIndex] || weeks[0];
        const activeTodos = getWeekTodos(semester, activeWeek.start);

        elements.todoDetail.hidden = false;
        elements.todoDetail.innerHTML = '';
        elements.todoDetail.className = 'semester-calendar-week-todo-stage';

        const prevBtn = document.createElement('button');
        prevBtn.type = 'button';
        prevBtn.className = 'semester-week-todos__arrow is-prev';
        prevBtn.dataset.weekShift = '-1';
        prevBtn.disabled = activeIndex <= 0;
        prevBtn.setAttribute('aria-label', '切换到上一周待办');
        prevBtn.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m15 18-6-6 6-6"/></svg>';

        const nextBtn = document.createElement('button');
        nextBtn.type = 'button';
        nextBtn.className = 'semester-week-todos__arrow is-next';
        nextBtn.dataset.weekShift = '1';
        nextBtn.disabled = activeIndex >= weeks.length - 1;
        nextBtn.setAttribute('aria-label', '切换到下一周待办');
        nextBtn.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m9 18 6-6-6-6"/></svg>';

        const rail = document.createElement('div');
        rail.className = 'semester-week-todos__rail';
        rail.appendChild(createWeekPreviewCard(semester, weeks[activeIndex - 1], 'prev'));

        const card = document.createElement('article');
        card.className = 'semester-week-todo-card is-active';
        card.dataset.weekKey = activeWeekKey;

        const head = document.createElement('div');
        head.className = 'semester-week-todo-card__head';
        const headCopy = document.createElement('div');
        const kicker = document.createElement('span');
        kicker.textContent = getWeekDisplayLabel(activeWeek);
        const title = document.createElement('strong');
        title.textContent = activeWeek.range_label || `${formatIsoDate(activeWeek.start)} 至 ${formatIsoDate(activeWeek.end)}`;
        headCopy.append(kicker, title);
        const count = document.createElement('small');
        const openCount = activeTodos.filter((todo) => !todo.is_completed).length;
        count.textContent = activeTodos.length
            ? `${activeTodos.length} 项 / ${openCount} 项未完成`
            : '暂无待办';
        head.append(headCopy, count);

        const list = document.createElement('div');
        list.className = 'semester-week-todo-list';
        if (activeTodos.length) {
            activeTodos.forEach((todo) => list.appendChild(createTodoItemCard(todo, activeWeek)));
        } else {
            const empty = document.createElement('div');
            empty.className = 'semester-week-gantt__empty';
            empty.textContent = '本周暂时没有待办事项。';
            list.appendChild(empty);
        }

        card.append(head, createWeekDateAxis(activeWeek), list);
        rail.appendChild(card);
        rail.appendChild(createWeekPreviewCard(semester, weeks[activeIndex + 1], 'next'));

        elements.todoDetail.append(prevBtn, rail, nextBtn);
    }

    function renderTodoDetail(semester, todoId) {
        if (!showTodos) return;
        if (!elements.todoDetail) return;
        const todo = todoId ? findTodoById(semester, todoId) : null;
        if (!todo) {
            elements.todoDetail.hidden = true;
            elements.todoDetail.innerHTML = '';
            return;
        }

        elements.todoDetail.hidden = false;
        elements.todoDetail.innerHTML = '';
        elements.todoDetail.className = `semester-calendar-todo-detail is-${sourceTone(todo)}`;

        const copy = document.createElement('div');
        copy.className = 'semester-calendar-todo-detail__copy';

        const eyebrow = document.createElement('span');
        eyebrow.textContent = `${sourceLabel(todo)} · ${todo.offering_label || '课堂'}`;
        copy.appendChild(eyebrow);

        const title = document.createElement('strong');
        title.textContent = todo.title || '待办事项';
        copy.appendChild(title);

        const meta = document.createElement('small');
        meta.textContent = [todo.duration_label, todo.status_label || todo.relative_due_label]
            .filter(Boolean)
            .join(' · ');
        copy.appendChild(meta);

        const actions = document.createElement('div');
        actions.className = 'semester-calendar-todo-detail__actions';

        if (todo.link_url) {
            const link = document.createElement('a');
            link.className = 'btn btn-outline btn-sm';
            link.href = todo.link_url;
            link.textContent = '打开';
            actions.appendChild(link);
        }

        if (todo.can_complete) {
            const completeBtn = document.createElement('button');
            completeBtn.type = 'button';
            completeBtn.className = 'btn btn-outline btn-sm';
            completeBtn.dataset.semesterTodoComplete = String(manualTodoId(todo));
            completeBtn.textContent = todo.is_completed ? '标记未完成' : '完成';
            actions.appendChild(completeBtn);

            const deleteBtn = document.createElement('button');
            deleteBtn.type = 'button';
            deleteBtn.className = 'btn btn-ghost btn-sm text-danger';
            deleteBtn.dataset.semesterTodoDelete = String(manualTodoId(todo));
            deleteBtn.textContent = '删除';
            actions.appendChild(deleteBtn);
        }

        elements.todoDetail.append(copy, actions);
    }

    function createTodoBar(todo) {
        const button = document.createElement('button');
        button.type = 'button';
        const isActive = String(todo.id || '') === String(activeTodoId || '');
        const compactEvent = isAcademicCalendarEvent(todo);
        button.className = `semester-calendar-todo-bar is-${sourceTone(todo)}${compactEvent ? ' is-compact-academic' : ''}${todo.is_completed ? ' is-completed' : ''}${isActive ? ' is-active' : ''}`;
        button.dataset.semesterTodoId = String(todo.id || '');
        button.style.setProperty('--todo-left', `${Number(todo.bar_left || 0).toFixed(3)}%`);
        button.style.setProperty('--todo-width', `${Math.max(8, Number(todo.bar_width || 0)).toFixed(3)}%`);
        button.title = [
            todo.title || '待办',
            todo.duration_label,
            todo.location || todo?.metadata?.location,
            todo.offering_label,
            todo.notes,
        ].filter(Boolean).join(' · ');

        const label = document.createElement('span');
        label.className = compactEvent
            ? 'semester-calendar-todo-bar__label is-compact-event'
            : 'semester-calendar-todo-bar__label';
        if (compactEvent) {
            const parts = academicEventParts(todo);
            const eventType = document.createElement('span');
            eventType.className = 'semester-calendar-todo-bar__event-type';
            eventType.textContent = parts.label;
            const course = document.createElement('span');
            course.className = 'semester-calendar-todo-bar__event-course';
            course.textContent = parts.course;
            label.append(eventType, course);
            if (parts.location) {
                const location = document.createElement('span');
                location.className = 'semester-calendar-todo-bar__event-location';
                location.textContent = parts.location;
                label.appendChild(location);
            }
        } else {
            label.textContent = todo.title || '待办';
        }
        button.appendChild(label);

        if (!compactEvent) {
            const meta = document.createElement('small');
            meta.textContent = todo.no_deadline
                ? '无截止'
                : (todo.due_time_label ? `${todo.due_time_label}截止` : sourceLabel(todo));
            button.appendChild(meta);
        }
        return button;
    }

    function renderTodoWeekCell(fragment, semester, week, row, column) {
        const todos = getWeekTodos(semester, week.start);
        const classes = ['semester-todo-cell'];
        if (week.isCurrentWeek) classes.push('is-current-week');
        if (todos.length > 0) classes.push('has-todos');
        const cell = createCell(fragment, classes.join(' '), '', row, column);
        cell.dataset.weekKey = formatIsoDate(week.start);

        if (!todos.length) {
            const empty = document.createElement('span');
            empty.className = 'semester-todo-cell__empty';
            empty.textContent = '本周无待办';
            cell.appendChild(empty);
            return;
        }

        const lane = document.createElement('div');
        lane.className = 'semester-calendar-gantt-lane';
        todos.forEach((todo) => {
            lane.appendChild(createTodoBar(todo));
        });
        cell.appendChild(lane);

        if (todos.length > 5) {
            const more = document.createElement('span');
            more.className = 'semester-todo-cell__more';
            more.textContent = `共 ${todos.length} 项，可滚动`;
            cell.appendChild(more);
        }
    }

    function syncActiveTodoVisuals(semester) {
        if (!showTodos || !elements.board) return;
        const todo = activeTodoId ? findTodoById(semester, activeTodoId) : null;
        elements.board.querySelectorAll('.semester-day-cell').forEach((cell) => {
            cell.classList.remove('is-todo-highlight', 'is-todo-range-start', 'is-todo-range-end', 'is-todo-range-mid');
        });
        root.querySelectorAll('[data-semester-todo-id]').forEach((bar) => {
            bar.classList.toggle('is-active', String(bar.dataset.semesterTodoId || '') === String(activeTodoId || ''));
        });
        if (!todo) return;

        const startDate = todo.effective_start_date || todo.effective_end_date;
        const endDate = todo.effective_end_date || startDate;
        if (!startDate || !endDate) return;
        elements.board.querySelectorAll('.semester-day-cell[data-date]').forEach((cell) => {
            const date = cell.dataset.date || '';
            if (compareIsoDate(date, startDate) < 0 || compareIsoDate(date, endDate) > 0) {
                return;
            }
            cell.classList.add('is-todo-highlight');
            if (date === startDate) {
                cell.classList.add('is-todo-range-start');
            }
            if (date === endDate) {
                cell.classList.add('is-todo-range-end');
            }
            if (date !== startDate && date !== endDate) {
                cell.classList.add('is-todo-range-mid');
            }
        });
    }

    function getTodoOption(classOfferingId) {
        const semester = getActiveSemester();
        return (semester?.todo_create_options || []).find(
            (item) => Number(item.class_offering_id || 0) === Number(classOfferingId || 0),
        ) || null;
    }

    function ensureTodoModal() {
        if (todoModal) return todoModal;
        const modal = document.createElement('div');
        modal.className = 'semester-todo-modal-shell';
        modal.hidden = true;
        modal.innerHTML = `
            <div class="semester-todo-modal-backdrop" data-semester-todo-modal-close></div>
            <div class="semester-todo-modal-card" role="dialog" aria-modal="true" aria-labelledby="semesterDashboardTodoTitle">
                <div class="semester-todo-modal-head">
                    <div>
                        <span>我的待办</span>
                        <h3 id="semesterDashboardTodoTitle">新增待办事项</h3>
                    </div>
                    <button type="button" class="modal-close" data-semester-todo-modal-close aria-label="关闭">×</button>
                </div>
                <form class="semester-todo-modal-form">
                    <label class="form-group">
                        <span>所属课堂</span>
                        <select name="class_offering_id" class="form-control" required></select>
                    </label>
                    <label class="form-group">
                        <span>待办名称</span>
                        <input type="text" name="title" maxlength="120" required placeholder="例如：完成第二章实验报告">
                    </label>
                    <label class="form-group">
                        <span>备注</span>
                        <textarea name="notes" maxlength="1200" rows="3" placeholder="可以写下任务要求、材料位置或提醒自己的话"></textarea>
                    </label>
                    <input type="hidden" name="start_date">
                    <input type="hidden" name="due_date">
                    <div class="semester-todo-picker" data-semester-todo-picker>
                        <div class="semester-todo-picker__roles" role="tablist" aria-label="选择日期类型">
                            <button type="button" class="is-active" data-picker-role="due">截止日</button>
                            <button type="button" data-picker-role="start">开始日</button>
                        </div>
                        <div class="semester-todo-picker__head">
                            <button type="button" class="btn btn-ghost btn-sm btn-icon" data-picker-nav="prev" aria-label="上个月">‹</button>
                            <strong data-picker-title></strong>
                            <button type="button" class="btn btn-ghost btn-sm btn-icon" data-picker-nav="next" aria-label="下个月">›</button>
                        </div>
                        <div class="semester-todo-picker__weekdays" aria-hidden="true">
                            ${compactDayLabels.map((label) => `<span>周${label}</span>`).join('')}
                        </div>
                        <div class="semester-todo-picker__grid" data-picker-grid></div>
                        <div class="semester-todo-picker__result" data-picker-result>未选择日期时，将使用创建日期作为开始日期。</div>
                    </div>
                    <div class="semester-todo-modal-grid">
                        <label class="form-group">
                            <span>开始时间</span>
                            <input type="time" name="start_time" value="00:00" step="60">
                        </label>
                        <label class="form-group">
                            <span>截止时间（精确到分钟）</span>
                            <input type="time" name="due_time" value="23:59" step="60">
                        </label>
                    </div>
                    <div class="modal-actions">
                        <button type="button" class="btn btn-ghost" data-semester-todo-modal-close>取消</button>
                        <button type="submit" class="btn btn-primary">保存待办</button>
                    </div>
                </form>
            </div>
        `;
        document.body.appendChild(modal);
        modal.addEventListener('click', (event) => {
            if (event.target.closest('[data-semester-todo-modal-close]')) {
                closeTodoModal();
            }
        });
        modal.querySelector('[data-semester-todo-picker]')?.addEventListener('click', handlePickerClick);
        modal.querySelector('form')?.addEventListener('submit', handleCreateTodo);
        todoModal = modal;
        return todoModal;
    }

    function getPickerElements() {
        const modal = ensureTodoModal();
        return {
            form: modal.querySelector('form'),
            title: modal.querySelector('[data-picker-title]'),
            grid: modal.querySelector('[data-picker-grid]'),
            result: modal.querySelector('[data-picker-result]'),
            roleButtons: Array.from(modal.querySelectorAll('[data-picker-role]')),
        };
    }

    function getPickerAnchorDate() {
        const semester = getActiveSemester();
        return parseIsoDate(semester?.todo_overview?.active_week_key)
            || parseIsoDate(semester?.start_date)
            || parseIsoDate(todayIso)
            || new Date();
    }

    function resetTodoPicker(form) {
        const anchor = getPickerAnchorDate();
        todoPickerState = {
            role: 'due',
            monthDate: new Date(anchor.getFullYear(), anchor.getMonth(), 1),
        };
        if (form?.elements?.start_date) form.elements.start_date.value = '';
        if (form?.elements?.due_date) form.elements.due_date.value = '';
        renderTodoPicker();
    }

    function updatePickerResult(form, elements) {
        if (!elements.result) return;
        const startDate = form?.elements?.start_date?.value || '';
        const dueDate = form?.elements?.due_date?.value || '';
        const startText = startDate || '创建日期';
        const dueText = dueDate || '无截止日';
        elements.result.textContent = `开始：${startText}；截止：${dueText}`;
    }

    function renderTodoPicker() {
        if (!todoPickerState || !todoModal) return;
        const elements = getPickerElements();
        const { form, title, grid, roleButtons } = elements;
        if (!form || !grid) return;

        const startDate = form.elements.start_date?.value || '';
        const dueDate = form.elements.due_date?.value || '';
        roleButtons.forEach((button) => {
            const isActive = button.dataset.pickerRole === todoPickerState.role;
            button.classList.toggle('is-active', isActive);
            button.setAttribute('aria-selected', String(isActive));
        });
        if (title) {
            title.textContent = formatMonthTitle(todoPickerState.monthDate);
        }

        grid.innerHTML = '';
        const firstOfMonth = new Date(
            todoPickerState.monthDate.getFullYear(),
            todoPickerState.monthDate.getMonth(),
            1,
        );
        const gridStart = getMonday(firstOfMonth);
        const todayDate = parseIsoDate(todayIso);
        for (let index = 0; index < 42; index += 1) {
            const current = addDays(gridStart, index);
            const isoDate = formatIsoDate(current);
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'semester-todo-picker__day';
            button.dataset.date = isoDate;
            button.textContent = String(current.getDate());
            if (current.getMonth() !== todoPickerState.monthDate.getMonth()) {
                button.classList.add('is-outside');
            }
            if (todayDate && isoDate === todayIso) {
                button.classList.add('is-today');
            }
            if (isoDate === startDate) {
                button.classList.add('is-start');
            }
            if (isoDate === dueDate) {
                button.classList.add('is-due');
            }
            if (startDate && dueDate && compareIsoDate(isoDate, startDate) >= 0 && compareIsoDate(isoDate, dueDate) <= 0) {
                button.classList.add('is-range');
            }
            grid.appendChild(button);
        }
        updatePickerResult(form, elements);
    }

    function handlePickerClick(event) {
        if (!todoPickerState) return;
        const roleButton = event.target.closest('[data-picker-role]');
        if (roleButton) {
            todoPickerState.role = roleButton.dataset.pickerRole || 'due';
            renderTodoPicker();
            return;
        }

        const navButton = event.target.closest('[data-picker-nav]');
        if (navButton) {
            const direction = navButton.dataset.pickerNav === 'prev' ? -1 : 1;
            todoPickerState.monthDate = new Date(
                todoPickerState.monthDate.getFullYear(),
                todoPickerState.monthDate.getMonth() + direction,
                1,
            );
            renderTodoPicker();
            return;
        }

        const dayButton = event.target.closest('[data-date]');
        if (!dayButton) return;
        const form = todoModal?.querySelector('form');
        const selectedDate = dayButton.dataset.date || '';
        if (!form || !selectedDate) return;
        if (todoPickerState.role === 'start') {
            form.elements.start_date.value = selectedDate;
            if (form.elements.due_date.value && compareIsoDate(form.elements.due_date.value, selectedDate) < 0) {
                form.elements.due_date.value = '';
            }
        } else {
            form.elements.due_date.value = selectedDate;
            if (form.elements.start_date.value && compareIsoDate(selectedDate, form.elements.start_date.value) < 0) {
                form.elements.start_date.value = selectedDate;
            }
        }
        renderTodoPicker();
    }

    function closeTodoModal() {
        if (!todoModal) return;
        todoModal.classList.remove('is-open');
        window.setTimeout(() => {
            if (todoModal) todoModal.hidden = true;
            document.body.classList.remove('has-semester-todo-modal');
        }, 160);
    }

    function openTodoModal() {
        const semester = getActiveSemester();
        const optionsList = semester?.todo_create_options || [];
        if (!optionsList.length) {
            onMessage?.('当前学期没有可添加待办的课堂', 'info');
            return;
        }
        const modal = ensureTodoModal();
        const form = modal.querySelector('form');
        const select = form?.elements?.class_offering_id;
        if (select) {
            select.innerHTML = '';
            optionsList.forEach((item) => {
                const option = document.createElement('option');
                option.value = String(item.class_offering_id);
                option.textContent = item.label || `${item.course_name || ''} ${item.class_name || ''}`;
                select.appendChild(option);
            });
        }
        form?.reset();
        if (select && optionsList.length === 1) {
            select.value = String(optionsList[0].class_offering_id);
        }
        if (form?.elements?.start_time) form.elements.start_time.value = '00:00';
        if (form?.elements?.due_time) form.elements.due_time.value = '23:59';
        resetTodoPicker(form);
        modal.hidden = false;
        document.body.classList.add('has-semester-todo-modal');
        window.requestAnimationFrame(() => {
            modal.classList.add('is-open');
            form?.elements?.title?.focus();
        });
    }

    async function refreshClassTodos(classOfferingId, nextOverview, nextTodoId = '') {
        const semester = getActiveSemester();
        if (!semester) return;
        const option = getTodoOption(classOfferingId);
        mergeClassTodoOverview(semester, classOfferingId, nextOverview, option);
        activeTodoId = nextTodoId || activeTodoId;
        if (activeTodoId) {
            scheduleWeekScroll(getTodoWeekKey(findTodoById(semester, activeTodoId)));
        }
        renderCalendar();
    }

    async function handleCreateTodo(event) {
        event.preventDefault();
        const form = event.currentTarget;
        const submitBtn = form.querySelector('button[type="submit"]');
        const classOfferingId = Number(form.elements.class_offering_id?.value || 0);
        if (!classOfferingId) {
            onMessage?.('请选择课堂', 'error');
            return;
        }
        const dateTime = (dateValue, timeValue, fallbackTime) => (
            dateValue ? `${dateValue}T${timeValue || fallbackTime}` : null
        );
        const body = {
            title: form.elements.title?.value || '',
            notes: form.elements.notes?.value || '',
            start_at: dateTime(form.elements.start_date?.value, form.elements.start_time?.value, '00:00'),
            due_at: dateTime(form.elements.due_date?.value, form.elements.due_time?.value, '23:59'),
        };
        if (submitBtn) submitBtn.disabled = true;
        try {
            const result = await apiFetch(`/api/classrooms/${classOfferingId}/todos`, {
                method: 'POST',
                body,
                silent: true,
            });
            closeTodoModal();
            await refreshClassTodos(classOfferingId, result.todo_overview, result.id ? `manual:${result.id}` : '');
            onMessage?.(result.message || '待办已添加', 'success');
        } catch (error) {
            onMessage?.(error.message || '新增待办失败', 'error');
        } finally {
            if (submitBtn) submitBtn.disabled = false;
        }
    }

    async function patchManualTodo(todo, body) {
        const classOfferingId = Number(todo?.class_offering_id || 0);
        const todoId = manualTodoId(todo);
        if (!classOfferingId || !todoId) return;
        const result = await apiFetch(`/api/classrooms/${classOfferingId}/todos/${todoId}`, {
            method: 'PATCH',
            body,
            silent: true,
        });
        await refreshClassTodos(classOfferingId, result.todo_overview, `manual:${todoId}`);
        onMessage?.(result.message || '待办已更新', 'success');
    }

    async function deleteManualTodo(todo) {
        const confirmed = window.confirm('确定删除这条待办吗？');
        if (!confirmed) return;
        const classOfferingId = Number(todo?.class_offering_id || 0);
        const todoId = manualTodoId(todo);
        if (!classOfferingId || !todoId) return;
        const result = await apiFetch(`/api/classrooms/${classOfferingId}/todos/${todoId}`, {
            method: 'DELETE',
            silent: true,
        });
        activeTodoId = '';
        await refreshClassTodos(classOfferingId, result.todo_overview, '');
        onMessage?.(result.message || '待办已删除', 'success');
    }

    function renderCalendar() {
        if (!elements.board || !elements.empty) {
            return;
        }

        const semester = getActiveSemester();
        const model = getSemesterModel(semester);

        if (!semester || !model) {
            elements.board.innerHTML = '';
            elements.board.style.gridTemplateColumns = '';
            elements.board.style.gridTemplateRows = '';
            elements.empty.hidden = false;
            renderOverview(null, null);
            renderTodoToolbar(null);
            renderWeekTodoStage(null, null);
            return;
        }

        renderOverview(semester, model);
        renderTodoToolbar(semester);
        elements.empty.hidden = true;

        const board = elements.board;
        board.innerHTML = '';
        activeWeekKey = resolveActiveWeekKey(semester, model);
        board.style.gridTemplateColumns = `132px ${getWeekColumns(model)}`;
        board.style.gridTemplateRows = '52px 52px repeat(7, minmax(58px, auto))';

        const fragment = document.createDocumentFragment();
        createCell(fragment, 'semester-header-cell semester-sticky-cell', '月份', 1, 1);
        model.monthGroups.forEach((group) => {
            createCell(fragment, 'semester-header-cell month', group.label, 1, group.start + 2, group.span);
        });

        createCell(fragment, 'semester-header-cell semester-sticky-cell', '周次', 2, 1);
        model.weeks.forEach((week, index) => {
            const weekKey = getWeekKey(week);
            const classes = ['semester-header-cell'];
            if (week.isPaddingWeek) {
                classes.push('is-padding-week');
            }
            if (week.isCurrentWeek) {
                classes.push('is-current-week');
            }
            if (weekKey === activeWeekKey) {
                classes.push('is-active-week');
            }
            const weekCell = createCell(
                fragment,
                classes.join(' '),
                week.isPaddingWeek ? '' : getWeekDisplayLabel(week),
                2,
                index + 2,
            );
            if (!week.isPaddingWeek) {
                weekCell.dataset.weekKey = weekKey;
                weekCell.tabIndex = 0;
                weekCell.setAttribute('role', 'button');
            } else {
                weekCell.setAttribute('aria-hidden', 'true');
            }
        });

        for (let dayIndex = 0; dayIndex < 7; dayIndex += 1) {
            createCell(fragment, 'semester-weekday-cell semester-sticky-cell', dayLabels[dayIndex], dayIndex + 3, 1);

            model.weeks.forEach((week, weekIndex) => {
                const day = week.days[dayIndex];
                const weekKey = getWeekKey(week);
                const cellClasses = ['semester-day-cell'];
                if (week.isPaddingWeek) cellClasses.push('is-padding-week');
                if (day.isCurrentWeek) cellClasses.push('is-current-week');
                if (weekKey === activeWeekKey) cellClasses.push('is-active-week');
                if (day.isWeekend) cellClasses.push('is-weekend');
                if (day.isHoliday) cellClasses.push('is-holiday');
                if (day.isWorkday) cellClasses.push('is-workday');
                if (day.isToday) cellClasses.push('is-today');
                if (!day.inSemester) cellClasses.push('is-outside');

                const cell = createCell(fragment, cellClasses.join(' '), '', dayIndex + 3, weekIndex + 2);
                cell.dataset.date = day.isoDate;
                if (!week.isPaddingWeek) {
                    cell.dataset.weekKey = weekKey;
                }

                const number = document.createElement('div');
                number.className = 'date-number';
                number.textContent = String(day.date.getDate());
                cell.appendChild(number);

                const meta = document.createElement('div');
                meta.className = 'date-meta';
                meta.textContent = day.label;
                cell.appendChild(meta);

                if (day.holidayInfo?.label) {
                    const tag = document.createElement('div');
                    tag.className = `semester-mini-tag ${day.holidayInfo.kind === 'workday' ? 'workday' : 'holiday'}`;
                    tag.textContent = day.holidayInfo.label;
                    cell.appendChild(tag);
                } else if (day.isWeekend) {
                    const tag = document.createElement('div');
                    tag.className = 'semester-mini-tag';
                    tag.textContent = '周末';
                    cell.appendChild(tag);
                }
            });
        }

        board.appendChild(fragment);
        renderWeekTodoStage(semester, model);
        syncActiveTodoVisuals(semester);
        if (pendingScrollWeekKey) {
            const weekKey = pendingScrollWeekKey;
            pendingScrollWeekKey = '';
            window.requestAnimationFrame(() => scrollToWeekKey(weekKey, 'smooth'));
        }
    }

    function setActiveSemester(semesterId, { emit = true } = {}) {
        const semester = getSemesterById(semesterId);
        state.activeSemesterId = semester ? semester.id : (state.semesters[0]?.id ?? null);
        activeTodoId = '';
        scheduleWeekScroll(getSemesterActiveWeekKey(getActiveSemester()));
        renderSelect();
        renderCalendar();
        if (emit && onChange) {
            onChange(getActiveSemester());
        }
    }

    function scrollToToday() {
        const semester = getActiveSemester();
        if (!semester?.is_current) {
            if (onMessage) {
                onMessage('今天不在当前选择学期范围内', 'info');
            }
            return;
        }

        const todayDate = parseIsoDate(todayIso);
        const todayWeekKey = todayDate ? formatIsoDate(getMonday(todayDate)) : '';
        const todayCell = elements.board?.querySelector(`[data-date="${todayIso}"]`);
        if (!todayCell || !elements.scroll) {
            if (onMessage) {
                onMessage('今天不在当前学期网格范围内', 'info');
            }
            return;
        }

        setActiveWeekKey(todayWeekKey, { center: true, behavior: 'smooth' });
    }

    function bindDragScroll() {
        if (!elements.scroll) {
            return;
        }

        elements.scroll.addEventListener('pointerdown', (event) => {
            if (event.pointerType === 'mouse' && event.button !== 0) {
                return;
            }
            window.clearTimeout(snapTimer);
            dragState = {
                pointerId: event.pointerId,
                startX: event.clientX,
                lastX: event.clientX,
                moved: false,
                startScrollLeft: elements.scroll.scrollLeft,
            };
            elements.scroll.classList.add('is-dragging');
            elements.scroll.setPointerCapture?.(event.pointerId);
        });

        elements.scroll.addEventListener('pointermove', (event) => {
            if (!dragState) {
                return;
            }
            const delta = event.clientX - dragState.startX;
            dragState.lastX = event.clientX;
            dragState.moved = dragState.moved || Math.abs(delta) > 6;
            elements.scroll.scrollLeft = dragState.startScrollLeft - delta;
        });

        const releaseDrag = (event) => {
            if (!dragState) {
                return;
            }
            if (event?.pointerId && dragState.pointerId && event.pointerId !== dragState.pointerId) {
                return;
            }
            const didDrag = Boolean(dragState.moved);
            elements.scroll.classList.remove('is-dragging');
            dragState = null;
            if (didDrag) {
                ignoreClickUntil = Date.now() + 180;
                scheduleSnapToNearest(60);
            }
        };

        elements.scroll.addEventListener('pointerup', releaseDrag);
        elements.scroll.addEventListener('pointercancel', releaseDrag);
        elements.scroll.addEventListener('pointerleave', releaseDrag);
        elements.scroll.addEventListener('scroll', () => {
            if (dragState || performance.now() < suppressSnapUntil) return;
            scheduleSnapToNearest(170);
        }, { passive: true });
    }

    function setSemesters(nextSemesters, { preserveSelection = true } = {}) {
        const previousActiveId = preserveSelection ? state.activeSemesterId : null;
        state.semesters = Array.isArray(nextSemesters) ? nextSemesters.map(normalizeSemester) : [];
        const fallbackSemesterId = defaultSemesterId ?? state.semesters[0]?.id ?? null;
        const nextActiveSemester = getSemesterById(previousActiveId) || getSemesterById(fallbackSemesterId);
        state.activeSemesterId = nextActiveSemester?.id ?? state.semesters[0]?.id ?? null;
        activeTodoId = '';
        scheduleWeekScroll(getSemesterActiveWeekKey(getActiveSemester()));
        renderSelect();
        renderCalendar();
        if (onChange) {
            onChange(getActiveSemester());
        }
    }

    elements.select?.addEventListener('change', (event) => {
        setActiveSemester(Number(event.target.value || 0));
    });
    elements.scrollStartBtn?.addEventListener('click', () => {
        const semester = getActiveSemester();
        const model = getSemesterModel(semester);
        const firstWeekKey = getWeekKey(getRealWeeks(model)[0]);
        if (firstWeekKey) {
            setActiveWeekKey(firstWeekKey, { center: true, behavior: 'smooth' });
        } else {
            elements.scroll?.scrollTo({ left: 0, behavior: 'smooth' });
        }
    });
    elements.scrollTodayBtn?.addEventListener('click', scrollToToday);
    elements.todoAddBtn?.addEventListener('click', openTodoModal);
    elements.board?.addEventListener('click', (event) => {
        if (Date.now() < ignoreClickUntil) return;
        const weekNode = event.target.closest('[data-week-key]');
        if (!weekNode) return;
        setActiveWeekKey(weekNode.dataset.weekKey || '', { center: true, behavior: 'smooth' });
    });
    elements.board?.addEventListener('keydown', (event) => {
        if (event.key !== 'Enter' && event.key !== ' ') return;
        const weekNode = event.target.closest('[data-week-key]');
        if (!weekNode) return;
        event.preventDefault();
        setActiveWeekKey(weekNode.dataset.weekKey || '', { center: true, behavior: 'smooth' });
    });
    elements.todoDetail?.addEventListener('click', async (event) => {
        const semester = getActiveSemester();
        const model = getSemesterModel(semester);
        const shiftButton = event.target.closest('[data-week-shift]');
        if (shiftButton) {
            const weeks = getRealWeeks(model);
            const currentIndex = weeks.findIndex((week) => getWeekKey(week) === activeWeekKey);
            const nextIndex = clampNumber(currentIndex + Number(shiftButton.dataset.weekShift || 0), 0, weeks.length - 1);
            const nextWeekKey = getWeekKey(weeks[nextIndex]);
            if (nextWeekKey) {
                activeTodoId = '';
                setActiveWeekKey(nextWeekKey, { center: true, behavior: 'smooth' });
            }
            return;
        }

        const completeBtn = event.target.closest('[data-semester-todo-complete]');
        if (completeBtn) {
            const todoNode = completeBtn.closest('[data-semester-todo-id]');
            const todo = findTodoById(semester, todoNode?.dataset.semesterTodoId || activeTodoId);
            if (!todo) return;
            completeBtn.disabled = true;
            try {
                await patchManualTodo(todo, { completed: !todo.is_completed });
            } catch (error) {
                onMessage?.(error.message || '待办更新失败', 'error');
            } finally {
                completeBtn.disabled = false;
            }
            return;
        }
        const deleteBtn = event.target.closest('[data-semester-todo-delete]');
        if (deleteBtn) {
            const todoNode = deleteBtn.closest('[data-semester-todo-id]');
            const todo = findTodoById(semester, todoNode?.dataset.semesterTodoId || activeTodoId);
            if (!todo) return;
            deleteBtn.disabled = true;
            try {
                await deleteManualTodo(todo);
            } catch (error) {
                onMessage?.(error.message || '待办删除失败', 'error');
            } finally {
                deleteBtn.disabled = false;
            }
            return;
        }

        const weekNode = event.target.closest('[data-week-key]');
        if (weekNode && !weekNode.classList.contains('is-active')) {
            activeTodoId = '';
            setActiveWeekKey(weekNode.dataset.weekKey || '', { center: true, behavior: 'smooth' });
            return;
        }

        const todoButton = event.target.closest('[data-semester-todo-id]');
        if (todoButton) {
            activeTodoId = todoButton.dataset.semesterTodoId || '';
            syncActiveTodoVisuals(semester);
            const href = todoButton.dataset.todoHref || findTodoById(semester, activeTodoId)?.link_url || '';
            if (href) {
                window.location.href = href;
                return;
            }
            renderWeekTodoStage(semester, model);
            return;
        }
    });
    elements.todoDetail?.addEventListener('keydown', (event) => {
        if (event.key !== 'Enter' && event.key !== ' ') return;
        const todoNode = event.target.closest('[data-semester-todo-id]');
        if (!todoNode || event.target.closest('button, a, input, select, textarea')) return;
        event.preventDefault();
        todoNode.click();
    });
    window.addEventListener('resize', () => {
        window.clearTimeout(resizeTimer);
        resizeTimer = window.setTimeout(() => {
            const currentWeekKey = activeWeekKey;
            modelCache.clear();
            scheduleWeekScroll(currentWeekKey);
            renderCalendar();
        }, 140);
    }, { passive: true });
    bindDragScroll();

    const initialSemester = getSemesterById(defaultSemesterId) || state.semesters[0] || null;
    state.activeSemesterId = initialSemester?.id ?? null;
    scheduleWeekScroll(getSemesterActiveWeekKey(initialSemester));
    renderSelect();
    renderCalendar();
    if (onChange) {
        onChange(getActiveSemester());
    }

    return {
        getActiveSemester,
        getSemesters: () => [...state.semesters],
        setActiveSemester,
        setSemesters,
        render: renderCalendar,
        scrollToToday,
    };
}
