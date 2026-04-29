import { apiFetch } from '/static/js/api.js';
import { showMessage } from '/static/js/ui.js';
import {
    computeSemesterWeekCount,
    initSemesterCalendar,
    parseIsoDate,
} from '/static/js/semester_calendar.js';

const config = window.SEMESTER_MANAGE_DATA || {};
const semesterCalendarConfig = config.semesterCalendar || {};
const todayIso = String(semesterCalendarConfig.todayIso || semesterCalendarConfig.today_iso || '');

const state = {
    semesters: Array.isArray(semesterCalendarConfig.semesters)
        ? semesterCalendarConfig.semesters.map(normalizeSemester)
        : [],
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
    calendarRoot: document.querySelector('[data-semester-calendar-root]'),
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

let semesterCalendar = null;

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
    return semesterCalendar?.getActiveSemester() || getSemesterById(state.activeSemesterId);
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

function getCurrentWeekText(semester) {
    if (!semester?.is_current) {
        return '当前不在学期范围内';
    }
    const today = parseIsoDate(todayIso);
    const startDate = parseIsoDate(semester.start_date);
    if (!today || !startDate) {
        return '当前不在学期范围内';
    }
    const currentWeek = computeSemesterWeekCount(startDate, today);
    return `今天位于第 ${Math.max(currentWeek, 1)} 周`;
}

function renderSemesterList() {
    if (!elements.list) {
        return;
    }

    const query = state.search.trim().toLowerCase();
    const items = query
        ? state.semesters.filter((item) => item.searchText.includes(query))
        : state.semesters;

    elements.list.innerHTML = items.map((semester) => `
        <div
            class="academic-list-item${semester.id === state.activeSemesterId ? ' is-active' : ''}"
            data-semester-id="${semester.id}"
            aria-current="${semester.id === state.activeSemesterId ? 'true' : 'false'}"
        >
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
        if (elements.summaryText) {
            elements.summaryText.textContent = '请选择一个学期。';
        }
        if (elements.adviceText) {
            elements.adviceText.textContent = '请先新增一个学期，开设课堂时可直接绑定。';
        }
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

function setActiveSemester(semesterId) {
    const semester = getSemesterById(semesterId);
    state.activeSemesterId = semester ? semester.id : (state.semesters[0]?.id ?? null);

    if (semesterCalendar) {
        semesterCalendar.setActiveSemester(state.activeSemesterId);
        return;
    }

    renderSemesterList();
    renderSummary();
}

function openModal(mode, semester = null) {
    if (!elements.modalBackdrop || !elements.form) {
        return;
    }

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
    if (!elements.modalBackdrop) {
        return;
    }
    elements.modalBackdrop.classList.remove('is-open');
}

function updateWeekPreview() {
    const startDate = parseIsoDate(elements.startInput?.value);
    const endDate = parseIsoDate(elements.endInput?.value);
    const weekCount = computeSemesterWeekCount(startDate, endDate);

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
    if (!semester) {
        return;
    }

    const confirmed = window.confirm(`确定删除学期“${semester.name}”吗？\n如果已经有课堂绑定到这个学期，需要先调整课堂绑定。`);
    if (!confirmed) {
        return;
    }

    const result = await apiFetch(`/api/manage/semesters/${semester.id}`, { method: 'DELETE' });
    showMessage(result.message || '学期已删除', 'success');
    window.location.reload();
}

async function handleSubmit(event) {
    event.preventDefault();
    if (!elements.form || !elements.submitBtn) {
        return;
    }

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
        if (event.target === elements.modalBackdrop) {
            closeModal();
        }
    });

    elements.searchInput?.addEventListener('input', (event) => {
        state.search = String(event.target.value || '');
        renderSemesterList();
    });

    elements.clearSearchBtn?.addEventListener('click', () => {
        state.search = '';
        if (elements.searchInput) {
            elements.searchInput.value = '';
        }
        renderSemesterList();
    });

    elements.list?.addEventListener('click', async (event) => {
        const actionButton = event.target.closest('[data-action]');
        if (!actionButton) {
            return;
        }

        const semesterId = Number(actionButton.dataset.semesterId || 0);
        if (!semesterId) {
            return;
        }

        if (actionButton.dataset.action === 'focus') {
            setActiveSemester(semesterId);
            return;
        }
        if (actionButton.dataset.action === 'edit') {
            const semester = getSemesterById(semesterId);
            if (semester) {
                openModal('edit', semester);
            }
            return;
        }
        if (actionButton.dataset.action === 'delete') {
            await handleDeleteSemester(semesterId);
        }
    });

    elements.startInput?.addEventListener('change', updateWeekPreview);
    elements.endInput?.addEventListener('change', updateWeekPreview);
    elements.nameInput?.addEventListener('input', () => {
        const value = String(elements.nameInput.value || '').trim();
        elements.nameInput.dataset.touched = value ? 'true' : 'false';
    });
    elements.form?.addEventListener('submit', handleSubmit);
}

function initDefaultState() {
    renderSemesterList();

    if (state.semesters.length === 0) {
        renderSummary();
        return;
    }

    const currentSemester = state.semesters.find((item) => item.is_current);
    state.activeSemesterId = currentSemester?.id ?? state.semesters[0].id;

    if (semesterCalendar) {
        semesterCalendar.setActiveSemester(state.activeSemesterId);
        return;
    }

    renderSummary();
}

function handleQueryOpen() {
    const searchParams = new URLSearchParams(window.location.search);
    if (searchParams.get('open') === 'new') {
        openModal('create');
    }
}

semesterCalendar = initSemesterCalendar(elements.calendarRoot, semesterCalendarConfig, {
    onChange: (semester) => {
        state.activeSemesterId = semester?.id ?? null;
        renderSemesterList();
        renderSummary();
    },
    onMessage: (message, tone) => showMessage(message, tone || 'info'),
});

initEvents();
initDefaultState();
handleQueryOpen();
