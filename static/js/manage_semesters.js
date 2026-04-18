import { apiFetch } from '/static/js/api.js';
import { showMessage } from '/static/js/ui.js';

const config = window.SEMESTER_MANAGE_DATA || {};
const todayIso = String(config.todayIso || '');
const holidayLookup = config.holidayLookup || {};

const dayLabels = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];
const monthFormatter = new Intl.DateTimeFormat('zh-CN', { month: 'numeric' });
const dateFormatter = new Intl.DateTimeFormat('zh-CN', { month: 'numeric', day: 'numeric', weekday: 'short' });

const state = {
    semesters: Array.isArray(config.semesters) ? config.semesters.map(normalizeSemester) : [],
    activeSemesterId: null,
    search: '',
};

const elements = {
    list: document.getElementById('semesterList'),
    listEmpty: document.getElementById('semesterListEmpty'),
    searchInput: document.getElementById('semesterSearchInput'),
    clearSearchBtn: document.getElementById('semesterClearSearchBtn'),
    summaryText: document.getElementById('semesterSummaryText'),
    adviceText: document.getElementById('semesterAdviceText'),
    calendarSelect: document.getElementById('semesterCalendarSelect'),
    calendarBoard: document.getElementById('semesterCalendarBoard'),
    calendarScroll: document.getElementById('semesterCalendarScroll'),
    calendarEmpty: document.getElementById('semesterCalendarEmpty'),
    scrollStartBtn: document.getElementById('scrollCalendarStartBtn'),
    scrollTodayBtn: document.getElementById('scrollCalendarTodayBtn'),
    openCreateBtns: [
        document.getElementById('openSemesterCreateBtn'),
        document.getElementById('heroSemesterCreateBtn'),
    ].filter(Boolean),
    modalBackdrop: document.getElementById('semesterModalBackdrop'),
    modalTitle: document.getElementById('semesterModalTitle'),
    modalCloseBtn: document.getElementById('semesterModalCloseBtn'),
    modalCancelBtn: document.getElementById('semesterModalCancelBtn'),
    form: document.getElementById('semesterForm'),
    semesterIdInput: document.getElementById('semesterIdInput'),
    nameInput: document.getElementById('semesterNameInput'),
    startInput: document.getElementById('semesterStartInput'),
    endInput: document.getElementById('semesterEndInput'),
    weekCountValue: document.getElementById('semesterWeekCountValue'),
    weekCountHint: document.getElementById('semesterWeekCountHint'),
    submitBtn: document.getElementById('semesterSubmitBtn'),
};

let dragState = null;

function normalizeSemester(item) {
    const weekCount = Number(item.week_count || 0);
    return {
        ...item,
        id: Number(item.id),
        week_count: Number.isFinite(weekCount) ? weekCount : 0,
        searchText: [
            item.name,
            item.start_date,
            item.end_date,
            item.display_range,
            weekCount ? `${weekCount}周` : '',
        ].filter(Boolean).join(' ').toLowerCase(),
    };
}

function getSemesterById(semesterId) {
    return state.semesters.find((item) => item.id === Number(semesterId)) || null;
}

function getActiveSemester() {
    return getSemesterById(state.activeSemesterId);
}

function parseIsoDate(isoDate) {
    const normalized = String(isoDate || '').trim();
    if (!normalized) return null;
    const parts = normalized.split('-').map((part) => Number(part));
    if (parts.length < 3 || parts.some((part) => !Number.isFinite(part))) return null;
    return new Date(parts[0], parts[1] - 1, parts[2]);
}

function formatIsoDate(date) {
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, '0');
    const day = String(date.getDate()).padStart(2, '0');
    return `${year}-${month}-${day}`;
}

function addDays(date, amount) {
    const next = new Date(date.getTime());
    next.setDate(next.getDate() + amount);
    return next;
}

function getMonday(date) {
    const weekday = date.getDay() || 7;
    return addDays(date, 1 - weekday);
}

function getSunday(date) {
    const weekday = date.getDay() || 7;
    return addDays(date, 7 - weekday);
}

function computeWeekCount(startDate, endDate) {
    if (!startDate || !endDate || endDate < startDate) return 0;
    const calendarStart = getMonday(startDate);
    const calendarEnd = getSunday(endDate);
    return Math.floor((calendarEnd - calendarStart) / (1000 * 60 * 60 * 24 * 7)) + 1;
}

function inferSemesterName(dateValue) {
    const currentDate = parseIsoDate(dateValue) || parseIsoDate(todayIso) || new Date();
    const month = currentDate.getMonth() + 1;
    let startYear = currentDate.getFullYear();
    let termLabel = '第一学期';
    if (month >= 8) {
        startYear = currentDate.getFullYear();
        termLabel = '第一学期';
    } else if (month <= 1) {
        startYear = currentDate.getFullYear() - 1;
        termLabel = '第一学期';
    } else {
        startYear = currentDate.getFullYear() - 1;
        termLabel = '第二学期';
    }
    return `${startYear}-${startYear + 1}${termLabel}`;
}

function formatDateLabel(value) {
    const date = parseIsoDate(value);
    return date ? dateFormatter.format(date) : '未设置';
}

function getCurrentWeekText(semester) {
    if (!semester?.is_current) return '当前不在学期范围内';
    const today = parseIsoDate(todayIso);
    const startDate = parseIsoDate(semester.start_date);
    if (!today || !startDate) return '当前不在学期范围内';
    const currentWeek = computeWeekCount(startDate, today);
    return `今天位于第 ${Math.max(currentWeek, 1)} 周`;
}

function renderSemesterList() {
    if (!elements.list) return;

    const query = state.search.trim().toLowerCase();
    const items = query
        ? state.semesters.filter((item) => item.searchText.includes(query))
        : state.semesters;

    elements.list.innerHTML = items.map((semester) => `
        <div class="academic-list-item" data-semester-id="${semester.id}">
            <div class="academic-list-main">
                <strong>${escapeHtml(semester.name || '未命名学期')}</strong>
                <p>${escapeHtml(semester.start_date || '--')} 至 ${escapeHtml(semester.end_date || '--')} · ${semester.week_count || 0} 周</p>
                <div class="academic-badge-row">
                    ${semester.is_current ? '<span class="academic-badge is-success">当前学期</span>' : '<span class="academic-badge is-muted">历史或未来学期</span>'}
                    <span class="academic-badge">开学首周自动计为第 1 周</span>
                </div>
            </div>
            <div class="academic-list-side">
                <button type="button" class="btn btn-ghost btn-sm" data-action="focus" data-semester-id="${semester.id}">查看日历</button>
                <button type="button" class="btn btn-outline btn-sm" data-action="edit" data-semester-id="${semester.id}">编辑</button>
                <button type="button" class="btn btn-danger btn-sm" data-action="delete" data-semester-id="${semester.id}">删除</button>
            </div>
        </div>
    `).join('');

    if (elements.listEmpty) {
        elements.listEmpty.hidden = items.length > 0;
    }
}

function renderSummary() {
    const semester = getActiveSemester();
    if (!semester) {
        if (elements.summaryText) elements.summaryText.textContent = '请选择一个学期。';
        if (elements.adviceText) elements.adviceText.textContent = '请先新增一个学期，开设课堂时可直接绑定。';
        return;
    }

    const summaryLines = [
        `学期名称：${semester.name || '未命名学期'}`,
        `起止日期：${semester.start_date || '--'} 至 ${semester.end_date || '--'}`,
        `自动周数：第 1 周至第 ${semester.week_count || 0} 周`,
        `当前状态：${semester.is_current ? '进行中' : '非进行中'}`,
        getCurrentWeekText(semester),
    ];
    if (elements.summaryText) {
        elements.summaryText.textContent = summaryLines.join('\n');
    }

    const adviceLines = [
        '1. 建议先创建学期，再开设课堂，确保课堂时间信息完整。',
        '2. 日历会标注法定假期和调休安排，可用于排课和考试安排参考。',
        '3. 当前正在使用的学期会在开课时自动优先选中。',
    ];
    if (semester.is_current) {
        adviceLines.unshift('当前日期位于该学期内，开课时会自动优先选中。');
    }
    if (elements.adviceText) {
        elements.adviceText.textContent = adviceLines.join('\n');
    }
}

function computeMonthGroups(weeks) {
    const labels = weeks.map((weekStart) => {
        const monthNames = new Set();
        for (let i = 0; i < 7; i += 1) {
            monthNames.add(monthFormatter.format(addDays(weekStart, i)));
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

function renderCalendar() {
    const semester = getActiveSemester();
    if (!elements.calendarBoard || !elements.calendarEmpty || !elements.calendarSelect) return;

    if (!semester) {
        elements.calendarBoard.innerHTML = '';
        elements.calendarBoard.style.gridTemplateColumns = '';
        elements.calendarEmpty.hidden = false;
        return;
    }

    const startDate = parseIsoDate(semester.start_date);
    const endDate = parseIsoDate(semester.end_date);
    if (!startDate || !endDate) {
        elements.calendarBoard.innerHTML = '';
        elements.calendarEmpty.hidden = false;
        return;
    }

    elements.calendarEmpty.hidden = true;
    const calendarStart = getMonday(startDate);
    const calendarEnd = getSunday(endDate);
    const weeks = [];
    for (let cursor = new Date(calendarStart); cursor <= calendarEnd; cursor = addDays(cursor, 7)) {
        weeks.push(new Date(cursor));
    }

    const board = elements.calendarBoard;
    board.innerHTML = '';
    board.style.gridTemplateColumns = `160px repeat(${weeks.length}, minmax(96px, 1fr))`;
    board.style.gridTemplateRows = '52px 52px repeat(7, minmax(64px, auto))';

    const createCell = (className, text, row, column, columnSpan = 1) => {
        const cell = document.createElement('div');
        cell.className = className;
        if (text != null) {
            cell.textContent = text;
        }
        cell.style.gridRow = String(row);
        cell.style.gridColumn = `${column} / span ${columnSpan}`;
        board.appendChild(cell);
        return cell;
    };

    createCell('semester-header-cell semester-sticky-cell', '月份', 1, 1);
    computeMonthGroups(weeks).forEach((group) => {
        createCell('semester-header-cell month', group.label, 1, group.start + 2, group.span);
    });

    createCell('semester-header-cell semester-sticky-cell', '周次', 2, 1);
    weeks.forEach((weekStart, index) => {
        const weekCell = createCell('semester-header-cell', `第 ${index + 1} 周`, 2, index + 2);
        const label = document.createElement('span');
        label.className = 'semester-week-label';
        label.textContent = `${monthFormatter.format(weekStart)}${weekStart.getDate()}日`;
        weekCell.appendChild(label);
    });

    for (let dayIndex = 0; dayIndex < 7; dayIndex += 1) {
        createCell('semester-weekday-cell semester-sticky-cell', dayLabels[dayIndex], dayIndex + 3, 1);
        weeks.forEach((weekStart, weekIndex) => {
            const currentDate = addDays(weekStart, dayIndex);
            const isoDate = formatIsoDate(currentDate);
            const holidayInfo = holidayLookup[isoDate];
            const inSemester = currentDate >= startDate && currentDate <= endDate;
            const isWeekend = currentDate.getDay() === 0 || currentDate.getDay() === 6;
            const isHoliday = holidayInfo?.kind === 'holiday';
            const isWorkday = holidayInfo?.kind === 'workday';
            const isToday = isoDate === todayIso;

            const cell = createCell('semester-day-cell', '', dayIndex + 3, weekIndex + 2);
            cell.dataset.date = isoDate;
            if (isWeekend) cell.classList.add('is-weekend');
            if (isHoliday) cell.classList.add('is-holiday');
            if (isWorkday) cell.classList.add('is-workday');
            if (isToday) cell.classList.add('is-today');
            if (!inSemester) cell.classList.add('is-outside');

            const number = document.createElement('div');
            number.className = 'date-number';
            number.textContent = String(currentDate.getDate());
            cell.appendChild(number);

            const meta = document.createElement('div');
            meta.className = 'date-meta';
            meta.textContent = inSemester ? dateFormatter.format(currentDate) : `衔接日 · ${dateFormatter.format(currentDate)}`;
            cell.appendChild(meta);

            if (holidayInfo?.label) {
                const tag = document.createElement('div');
                tag.className = `semester-mini-tag ${holidayInfo.kind === 'workday' ? 'workday' : 'holiday'}`;
                tag.textContent = holidayInfo.label;
                cell.appendChild(tag);
            } else if (isWeekend) {
                const tag = document.createElement('div');
                tag.className = 'semester-mini-tag';
                tag.textContent = '周末';
                cell.appendChild(tag);
            }
        });
    }
}

function renderCalendarSelect() {
    if (!elements.calendarSelect) return;
    elements.calendarSelect.innerHTML = state.semesters.map((semester) => `
        <option value="${semester.id}">${escapeHtml(semester.name || '未命名学期')} · ${escapeHtml(semester.start_date || '--')}</option>
    `).join('');

    if (state.activeSemesterId != null) {
        elements.calendarSelect.value = String(state.activeSemesterId);
    }
}

function setActiveSemester(semesterId) {
    const semester = getSemesterById(semesterId);
    state.activeSemesterId = semester ? semester.id : (state.semesters[0]?.id ?? null);
    renderCalendarSelect();
    renderSummary();
    renderCalendar();
}

function openModal(mode, semester = null) {
    if (!elements.modalBackdrop || !elements.form) return;
    const defaults = config.defaults || {};
    elements.modalTitle.textContent = mode === 'edit' ? '编辑学期' : '新增学期';
    elements.submitBtn.textContent = mode === 'edit' ? '保存修改' : '保存学期';
    elements.form.dataset.mode = mode;
    elements.form.dataset.autoName = mode === 'create' ? 'true' : 'false';
    elements.nameInput.dataset.touched = 'false';

    if (mode === 'edit' && semester) {
        elements.semesterIdInput.value = String(semester.id);
        elements.nameInput.value = semester.name || '';
        elements.startInput.value = semester.start_date || '';
        elements.endInput.value = semester.end_date || '';
        elements.form.dataset.autoName = 'false';
    } else {
        elements.semesterIdInput.value = '';
        elements.nameInput.value = defaults.name || '';
        elements.startInput.value = defaults.start_date || '';
        elements.endInput.value = defaults.end_date || '';
    }

    updateWeekPreview();
    elements.modalBackdrop.classList.add('is-open');
}

function closeModal() {
    if (!elements.modalBackdrop) return;
    elements.modalBackdrop.classList.remove('is-open');
}

function updateWeekPreview() {
    const startDate = parseIsoDate(elements.startInput?.value);
    const endDate = parseIsoDate(elements.endInput?.value);
    const weekCount = computeWeekCount(startDate, endDate);

    if (elements.weekCountValue) {
        elements.weekCountValue.textContent = weekCount > 0 ? `${weekCount} 周` : '0 周';
    }
    if (elements.weekCountHint) {
        if (!startDate || !endDate) {
            elements.weekCountHint.textContent = '选择开始和结束日期后自动计算。';
        } else if (endDate < startDate) {
            elements.weekCountHint.textContent = '结束日期不能早于开始日期。';
        } else {
            elements.weekCountHint.textContent = '自动按周一到周日补齐后计算周次，开学首周记为第 1 周。';
        }
    }

    if (elements.form?.dataset.autoName === 'true' && elements.nameInput && elements.nameInput.dataset.touched !== 'true') {
        elements.nameInput.value = inferSemesterName(elements.startInput.value || todayIso);
    }
}

async function handleDeleteSemester(semesterId) {
    const semester = getSemesterById(semesterId);
    if (!semester) return;
    const confirmed = window.confirm(`确定删除学期“${semester.name}”吗？\n如果已经有课堂绑定到这个学期，需要先调整课堂绑定。`);
    if (!confirmed) return;

    const result = await apiFetch(`/api/manage/semesters/${semester.id}`, { method: 'DELETE' });
    showMessage(result.message || '学期已删除', 'success');
    window.location.reload();
}

async function handleSubmit(event) {
    event.preventDefault();
    if (!elements.form || !elements.submitBtn) return;

    const startDate = parseIsoDate(elements.startInput.value);
    const endDate = parseIsoDate(elements.endInput.value);
    if (!startDate || !endDate) {
        showMessage('请完整填写学期开始和结束日期', 'warning');
        return;
    }
    if (endDate < startDate) {
        showMessage('学期结束日期不能早于开始日期', 'warning');
        return;
    }

    const formData = new FormData(elements.form);
    const originalText = elements.submitBtn.textContent;
    elements.submitBtn.disabled = true;
    elements.submitBtn.textContent = '正在保存...';

    try {
        const result = await apiFetch(elements.form.action, {
            method: 'POST',
            body: formData,
        });
        showMessage(result.message || '学期已保存', 'success');
        window.location.reload();
    } catch (error) {
        showMessage(error.message || '学期保存失败', 'error');
    } finally {
        elements.submitBtn.disabled = false;
        elements.submitBtn.textContent = originalText;
    }
}

function bindDragScroll() {
    if (!elements.calendarScroll) return;

    elements.calendarScroll.addEventListener('pointerdown', (event) => {
        if (event.pointerType === 'mouse' && event.button !== 0) return;
        dragState = {
            pointerId: event.pointerId,
            startX: event.clientX,
            startScrollLeft: elements.calendarScroll.scrollLeft,
        };
        elements.calendarScroll.classList.add('is-dragging');
        elements.calendarScroll.setPointerCapture?.(event.pointerId);
    });

    elements.calendarScroll.addEventListener('pointermove', (event) => {
        if (!dragState) return;
        const delta = event.clientX - dragState.startX;
        elements.calendarScroll.scrollLeft = dragState.startScrollLeft - delta;
    });

    const releaseDrag = (event) => {
        if (!dragState) return;
        if (event?.pointerId && dragState.pointerId && event.pointerId !== dragState.pointerId) return;
        elements.calendarScroll.classList.remove('is-dragging');
        dragState = null;
    };

    elements.calendarScroll.addEventListener('pointerup', releaseDrag);
    elements.calendarScroll.addEventListener('pointercancel', releaseDrag);
    elements.calendarScroll.addEventListener('pointerleave', releaseDrag);
}

function scrollCalendarToToday() {
    if (!elements.calendarScroll) return;
    const todayCell = elements.calendarBoard?.querySelector(`[data-date="${todayIso}"]`);
    if (!todayCell) {
        showMessage('今天不在当前学期网格范围内', 'info');
        return;
    }
    const left = todayCell.offsetLeft - 180;
    elements.calendarScroll.scrollTo({ left: Math.max(left, 0), behavior: 'smooth' });
}

function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function initEvents() {
    elements.openCreateBtns.forEach((button) => {
        button.addEventListener('click', () => openModal('create'));
    });

    elements.modalCloseBtn?.addEventListener('click', closeModal);
    elements.modalCancelBtn?.addEventListener('click', closeModal);
    elements.modalBackdrop?.addEventListener('click', (event) => {
        if (event.target === elements.modalBackdrop) closeModal();
    });

    elements.searchInput?.addEventListener('input', (event) => {
        state.search = String(event.target.value || '');
        renderSemesterList();
    });

    elements.clearSearchBtn?.addEventListener('click', () => {
        state.search = '';
        if (elements.searchInput) elements.searchInput.value = '';
        renderSemesterList();
    });

    elements.list?.addEventListener('click', async (event) => {
        const actionButton = event.target.closest('[data-action]');
        if (!actionButton) return;
        const semesterId = Number(actionButton.dataset.semesterId || 0);
        if (!semesterId) return;

        if (actionButton.dataset.action === 'focus') {
            setActiveSemester(semesterId);
            return;
        }
        if (actionButton.dataset.action === 'edit') {
            const semester = getSemesterById(semesterId);
            if (semester) openModal('edit', semester);
            return;
        }
        if (actionButton.dataset.action === 'delete') {
            await handleDeleteSemester(semesterId);
        }
    });

    elements.calendarSelect?.addEventListener('change', (event) => {
        setActiveSemester(Number(event.target.value || 0));
    });

    elements.scrollStartBtn?.addEventListener('click', () => {
        elements.calendarScroll?.scrollTo({ left: 0, behavior: 'smooth' });
    });

    elements.scrollTodayBtn?.addEventListener('click', scrollCalendarToToday);

    elements.startInput?.addEventListener('change', updateWeekPreview);
    elements.endInput?.addEventListener('change', updateWeekPreview);
    elements.nameInput?.addEventListener('input', () => {
        const value = String(elements.nameInput.value || '').trim();
        elements.nameInput.dataset.touched = value ? 'true' : 'false';
    });
    elements.form?.addEventListener('submit', handleSubmit);

    bindDragScroll();
}

function initDefaultState() {
    if (state.semesters.length === 0) {
        renderSemesterList();
        renderSummary();
        renderCalendar();
        return;
    }
    const currentSemester = state.semesters.find((item) => item.is_current);
    state.activeSemesterId = currentSemester?.id ?? state.semesters[0].id;
    renderSemesterList();
    renderCalendarSelect();
    renderSummary();
    renderCalendar();
}

function handleQueryOpen() {
    const searchParams = new URLSearchParams(window.location.search);
    if (searchParams.get('open') === 'new') {
        openModal('create');
    }
}

initEvents();
initDefaultState();
handleQueryOpen();
