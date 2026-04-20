const MS_PER_DAY = 1000 * 60 * 60 * 24;
const dayLabels = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];
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
    };
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

function buildSemesterModel(semester, holidayLookup, todayIso, modelCache) {
    if (!semester) {
        return null;
    }

    const cacheKey = [
        semester.id,
        semester.start_date,
        semester.end_date,
        semester.week_count,
        todayIso,
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
    const calendarStart = getMonday(startDate);
    const calendarEnd = getSunday(endDate);
    const weeks = [];
    let holidayCount = 0;
    let workdayCount = 0;
    let currentWeekNumber = null;

    for (let cursor = new Date(calendarStart); cursor <= calendarEnd; cursor = addDays(cursor, 7)) {
        const weekStart = new Date(cursor);
        const weekEnd = addDays(weekStart, 6);
        const isCurrentWeek = Boolean(
            semester.is_current
            && todayDate
            && todayDate >= weekStart
            && todayDate <= weekEnd,
        );
        if (isCurrentWeek) {
            currentWeekNumber = weeks.length + 1;
        }

        const days = [];
        for (let dayIndex = 0; dayIndex < 7; dayIndex += 1) {
            const currentDate = addDays(weekStart, dayIndex);
            const isoDate = formatIsoDate(currentDate);
            const holidayInfo = holidayLookup[isoDate] || null;
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
                label: inSemester ? dateFormatter.format(currentDate) : `衔接日 · ${dateFormatter.format(currentDate)}`,
                holidayInfo,
                inSemester,
                isWeekend,
                isHoliday,
                isWorkday,
                isToday: isoDate === todayIso,
                isCurrentWeek,
            });
        }

        weeks.push({ start: weekStart, end: weekEnd, isCurrentWeek, days });
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

export function initSemesterCalendar(root, config = {}, options = {}) {
    if (!root) {
        return null;
    }

    const normalizedConfig = normalizeCalendarConfig(config);
    const holidayLookup = normalizedConfig.holidayLookup;
    const todayIso = normalizedConfig.todayIso;
    const defaultSemesterId = Number(normalizedConfig.defaultSemesterId || options.initialSemesterId || 0) || null;
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
    };

    const onChange = typeof options.onChange === 'function' ? options.onChange : null;
    const onMessage = typeof options.onMessage === 'function' ? options.onMessage : null;
    let dragState = null;

    function getSemesterById(semesterId) {
        return state.semesters.find((item) => item.id === Number(semesterId)) || null;
    }

    function getActiveSemester() {
        return getSemesterById(state.activeSemesterId);
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

    function renderCalendar() {
        if (!elements.board || !elements.empty) {
            return;
        }

        const semester = getActiveSemester();
        const model = buildSemesterModel(semester, holidayLookup, todayIso, modelCache);

        if (!semester || !model) {
            elements.board.innerHTML = '';
            elements.board.style.gridTemplateColumns = '';
            elements.board.style.gridTemplateRows = '';
            elements.empty.hidden = false;
            renderOverview(null, null);
            return;
        }

        renderOverview(semester, model);
        elements.empty.hidden = true;

        const board = elements.board;
        board.innerHTML = '';
        board.style.gridTemplateColumns = `160px repeat(${model.weeks.length}, minmax(96px, 1fr))`;
        board.style.gridTemplateRows = '52px 52px repeat(7, minmax(64px, auto))';

        const fragment = document.createDocumentFragment();
        createCell(fragment, 'semester-header-cell semester-sticky-cell', '月份', 1, 1);
        model.monthGroups.forEach((group) => {
            createCell(fragment, 'semester-header-cell month', group.label, 1, group.start + 2, group.span);
        });

        createCell(fragment, 'semester-header-cell semester-sticky-cell', '周次', 2, 1);
        model.weeks.forEach((week, index) => {
            const classes = ['semester-header-cell'];
            if (week.isCurrentWeek) {
                classes.push('is-current-week');
            }
            const weekCell = createCell(fragment, classes.join(' '), `第 ${index + 1} 周`, 2, index + 2);
            const label = document.createElement('span');
            label.className = 'semester-week-label';
            label.textContent = `${monthFormatter.format(week.start)}${week.start.getDate()}日`;
            weekCell.appendChild(label);
        });

        for (let dayIndex = 0; dayIndex < 7; dayIndex += 1) {
            createCell(fragment, 'semester-weekday-cell semester-sticky-cell', dayLabels[dayIndex], dayIndex + 3, 1);

            model.weeks.forEach((week, weekIndex) => {
                const day = week.days[dayIndex];
                const cellClasses = ['semester-day-cell'];
                if (day.isCurrentWeek) cellClasses.push('is-current-week');
                if (day.isWeekend) cellClasses.push('is-weekend');
                if (day.isHoliday) cellClasses.push('is-holiday');
                if (day.isWorkday) cellClasses.push('is-workday');
                if (day.isToday) cellClasses.push('is-today');
                if (!day.inSemester) cellClasses.push('is-outside');

                const cell = createCell(fragment, cellClasses.join(' '), '', dayIndex + 3, weekIndex + 2);
                cell.dataset.date = day.isoDate;

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
    }

    function setActiveSemester(semesterId, { emit = true } = {}) {
        const semester = getSemesterById(semesterId);
        state.activeSemesterId = semester ? semester.id : (state.semesters[0]?.id ?? null);
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

        const todayCell = elements.board?.querySelector(`[data-date="${todayIso}"]`);
        if (!todayCell || !elements.scroll) {
            if (onMessage) {
                onMessage('今天不在当前学期网格范围内', 'info');
            }
            return;
        }

        const left = todayCell.offsetLeft - 180;
        elements.scroll.scrollTo({ left: Math.max(left, 0), behavior: 'smooth' });
    }

    function bindDragScroll() {
        if (!elements.scroll) {
            return;
        }

        elements.scroll.addEventListener('pointerdown', (event) => {
            if (event.pointerType === 'mouse' && event.button !== 0) {
                return;
            }
            dragState = {
                pointerId: event.pointerId,
                startX: event.clientX,
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
            elements.scroll.scrollLeft = dragState.startScrollLeft - delta;
        });

        const releaseDrag = (event) => {
            if (!dragState) {
                return;
            }
            if (event?.pointerId && dragState.pointerId && event.pointerId !== dragState.pointerId) {
                return;
            }
            elements.scroll.classList.remove('is-dragging');
            dragState = null;
        };

        elements.scroll.addEventListener('pointerup', releaseDrag);
        elements.scroll.addEventListener('pointercancel', releaseDrag);
        elements.scroll.addEventListener('pointerleave', releaseDrag);
    }

    function setSemesters(nextSemesters, { preserveSelection = true } = {}) {
        const previousActiveId = preserveSelection ? state.activeSemesterId : null;
        state.semesters = Array.isArray(nextSemesters) ? nextSemesters.map(normalizeSemester) : [];
        const fallbackSemesterId = defaultSemesterId ?? state.semesters[0]?.id ?? null;
        const nextActiveSemester = getSemesterById(previousActiveId) || getSemesterById(fallbackSemesterId);
        state.activeSemesterId = nextActiveSemester?.id ?? state.semesters[0]?.id ?? null;
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
        elements.scroll?.scrollTo({ left: 0, behavior: 'smooth' });
    });
    elements.scrollTodayBtn?.addEventListener('click', scrollToToday);
    bindDragScroll();

    const initialSemester = getSemesterById(defaultSemesterId) || state.semesters[0] || null;
    state.activeSemesterId = initialSemester?.id ?? null;
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
