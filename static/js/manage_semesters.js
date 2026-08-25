import { apiFetch } from '/static/js/api.js';
import { showMessage } from '/static/js/ui.js';
import {
    computeSemesterWeekCount,
    initSemesterCalendar,
    parseIsoDate,
} from '/static/js/semester_calendar.js?v=semester-band-20260825';

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
    syncCurrentBtn: document.getElementById('semesterSyncCurrentBtn'),
    submitBtn: document.getElementById('semesterSubmitBtn'),
};

let semesterCalendar = null;
const activeSyncPolls = new Map();

function normalizeSemester(item) {
    const weekCount = Number(item.week_count || 0);
    const isOwned = item.is_owned !== false;
    const canManage = item.can_manage !== false && isOwned;
    return {
        ...item,
        id: Number(item.id),
        week_count: Number.isFinite(weekCount) ? weekCount : 0,
        is_owned: isOwned,
        can_manage: canManage,
        is_shared_semester: item.is_shared_semester === true || !isOwned,
        school_name: String(item.school_name || ''),
        organization_label: String(item.organization_label || item.school_name || ''),
        calendar_sync_status: String(item.calendar_sync_status || 'pending'),
        calendar_sync_active: item.calendar_sync_active === false
            ? false
            : ['pending', 'running'].includes(String(item.calendar_sync_status || 'pending')),
        calendar_sync_message: String(item.calendar_sync_message || ''),
        calendar_sync_at: String(item.calendar_sync_at || ''),
        temporal_status: String(item.temporal_status || ''),
        temporal_status_label: String(item.temporal_status_label || ''),
        calendar_holiday_count: Number(item.calendar_holiday_count || 0),
        calendar_workday_count: Number(item.calendar_workday_count || 0),
        searchText: [
            item.name,
            item.start_date,
            item.end_date,
            item.display_range,
            item.school_name,
            item.organization_label,
            weekCount ? `${weekCount}周` : '',
            item.calendar_sync_status,
            item.calendar_sync_message,
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
    const today = parseIsoDate(todayIso);
    const startDate = parseIsoDate(semester.start_date);
    const endDate = parseIsoDate(semester.end_date);
    if (!today || !startDate || !endDate) {
        return '学期日期尚未完整确认';
    }
    if (today < startDate) {
        const days = Math.ceil((startDate - today) / 86400000);
        return `距离开学还有 ${days} 天`;
    }
    if (today > endDate) {
        return `该学期已于 ${semester.end_date} 结束`;
    }
    const currentWeek = computeSemesterWeekCount(startDate, today);
    return `今天位于第 ${Math.max(currentWeek, 1)} 周`;
}

function temporalMeta(semester) {
    const status = String(semester?.temporal_status || 'unknown');
    const map = {
        current: { label: '进行中', className: 'is-success' },
        future: { label: '未开始', className: 'is-accent' },
        past: { label: '已结束', className: 'is-muted' },
        unknown: { label: '日期待确认', className: 'is-muted' },
    };
    const result = map[status] || map.unknown;
    return { ...result, label: semester?.temporal_status_label || result.label };
}

function calendarSyncMeta(semester) {
    const status = String(semester?.calendar_sync_status || 'pending');
    const map = {
        synced: { label: '教务校历已对齐', className: 'is-success' },
        generated: { label: '系统校历已生成', className: 'is-accent' },
        partial: { label: '校历部分同步', className: 'is-accent' },
        running: { label: '校历同步中', className: 'is-accent' },
        pending: { label: '校历待同步', className: 'is-muted' },
        failed: { label: '校历同步失败', className: 'is-accent' },
    };
    return map[status] || map.pending;
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
        ${(() => {
            const sync = calendarSyncMeta(semester);
            const canManage = semester.can_manage !== false;
            const temporal = temporalMeta(semester);
            const isSyncing = semester.calendar_sync_active === true;
            return `
        <div
            class="academic-list-item academic-list-item-selectable${semester.id === state.activeSemesterId ? ' is-active' : ''}"
            data-semester-id="${semester.id}"
            role="button"
            tabindex="0"
            aria-current="${semester.id === state.activeSemesterId ? 'true' : 'false'}"
            aria-label="切换当前焦点到 ${escapeHtml(semester.name || '该学期')}"
            aria-busy="${isSyncing ? 'true' : 'false'}"
        >
            <div class="academic-list-main">
                <strong>${escapeHtml(semester.name || '未命名学期')}</strong>
                <p>${escapeHtml(semester.start_date || '--')} 至 ${escapeHtml(semester.end_date || '--')} · ${semester.week_count || 0} 周</p>
                <div class="academic-badge-row">
                    <span class="academic-badge ${temporal.className}">${escapeHtml(temporal.label)}</span>
                    <span class="academic-badge">开学首周自动计为第 1 周</span>
                    <span class="academic-badge ${sync.className}">${sync.label}</span>
                    ${semester.is_shared_semester ? '<span class="academic-badge is-accent">同校共享</span>' : ''}
                    ${semester.organization_label ? `<span class="academic-badge">${escapeHtml(semester.organization_label)}</span>` : ''}
                </div>
            </div>
            <div class="academic-list-side">
                ${config.embeddedMode ? '' : `<button type="button" class="btn btn-ghost btn-sm" data-action="focus" data-semester-id="${semester.id}">查看日历</button>`}
                ${canManage ? `<button type="button" class="btn btn-outline btn-sm${isSyncing ? ' is-loading' : ''}" data-action="sync-calendar" data-semester-id="${semester.id}" ${isSyncing ? 'disabled aria-disabled="true"' : ''}>${isSyncing ? '<span class="semester-button-spinner" aria-hidden="true"></span><span>正在同步</span>' : '同步校历'}</button>` : ''}
                ${canManage ? `<button type="button" class="btn btn-outline btn-sm" data-action="edit" data-semester-id="${semester.id}" ${isSyncing ? 'disabled aria-disabled="true" title="校历同步完成后可编辑"' : ''}>编辑</button>` : ''}
                ${canManage ? `<button type="button" class="btn btn-danger btn-sm" data-action="delete" data-semester-id="${semester.id}" ${isSyncing ? 'disabled aria-disabled="true" title="校历同步完成后可删除"' : ''}>删除</button>` : ''}
            </div>
        </div>
            `;
        })()}
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
        `当前状态：${temporalMeta(semester).label}`,
        `校历同步：${calendarSyncMeta(semester).label}${semester.calendar_sync_at ? `（${semester.calendar_sync_at}）` : ''}`,
        `节假日/补课：${semester.calendar_holiday_count || 0} 个假期，${semester.calendar_workday_count || 0} 个调休补课日`,
        getCurrentWeekText(semester),
    ];
    if (semester.calendar_sync_message) {
        summaryLines.push(`同步说明：${semester.calendar_sync_message}`);
    }
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

function setActiveSemester(semesterId, { scrollCalendar = false } = {}) {
    const semester = getSemesterById(semesterId);
    state.activeSemesterId = semester ? semester.id : (state.semesters[0]?.id ?? null);

    if (semesterCalendar) {
        semesterCalendar.setActiveSemester(state.activeSemesterId);
    } else {
        renderSemesterList();
        renderSummary();
    }
    if (scrollCalendar && elements.calendarRoot) {
        window.requestAnimationFrame(() => {
            elements.calendarRoot.scrollIntoView({ behavior: 'smooth', block: 'start' });
        });
    }
}

function openModal(mode, semester = null) {
    if (!elements.modalBackdrop || !elements.form) {
        return;
    }
    if (mode === 'edit' && semester?.can_manage === false) {
        showMessage('同校共享学期可以直接复用，只有创建者可以编辑。', 'warning');
        return;
    }
    if (mode === 'edit' && semester?.calendar_sync_active) {
        showMessage('校历正在同步，请等待完成后再编辑学期。', 'info');
        return;
    }

    const defaults = config.defaults || {};
    elements.modalTitle.textContent = mode === 'edit' ? '编辑学期' : '新增学期';
    elements.submitBtn.textContent = mode === 'edit' ? '保存修改' : '保存学期';
    if (elements.syncCurrentBtn) {
        elements.syncCurrentBtn.hidden = mode !== 'create';
        elements.syncCurrentBtn.disabled = false;
        elements.syncCurrentBtn.textContent = '从教务系统同步';
    }
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
    document.body.classList.add('has-academic-modal');
    window.requestAnimationFrame(() => elements.nameInput?.focus());
}

function closeModal() {
    if (!elements.modalBackdrop) {
        return;
    }
    elements.modalBackdrop.classList.remove('is-open');
    document.body.classList.remove('has-academic-modal');
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
    if (semester.can_manage === false) {
        showMessage('同校共享学期可以直接复用，只有创建者可以删除。', 'warning');
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

function updateSemesterSyncState(semesterId, payload = {}) {
    const semester = getSemesterById(semesterId);
    if (!semester) return;
    const status = String(payload.calendar_sync_status || semester.calendar_sync_status || 'pending');
    semester.calendar_sync_status = status;
    semester.calendar_sync_active = payload.calendar_sync_active === undefined
        ? ['pending', 'running'].includes(status)
        : payload.calendar_sync_active === true;
    if (payload.calendar_sync_message !== undefined) {
        semester.calendar_sync_message = String(payload.calendar_sync_message || '');
    }
    if (payload.calendar_sync_at !== undefined) {
        semester.calendar_sync_at = String(payload.calendar_sync_at || '');
    }
    renderSemesterList();
    renderSummary();
}

function wait(milliseconds) {
    return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

async function pollSemesterSync(semesterId, { reloadOnComplete = true } = {}) {
    if (activeSyncPolls.has(semesterId)) {
        return activeSyncPolls.get(semesterId);
    }
    const task = (async () => {
        for (let attempt = 0; attempt < 100; attempt += 1) {
            await wait(attempt === 0 ? 500 : 1500);
            const result = await apiFetch(`/api/manage/semesters/${semesterId}/calendar/status`);
            updateSemesterSyncState(semesterId, result);
            if (result.calendar_sync_active !== true) {
                if (reloadOnComplete) {
                    const nextUrl = new URL(window.location.href);
                    nextUrl.searchParams.set('semester_id', String(semesterId));
                    window.location.assign(nextUrl.toString());
                }
                return result;
            }
        }
        showMessage('校历仍在后台同步，可稍后刷新查看结果。', 'info');
        return null;
    })().catch((error) => {
        showMessage(error.message || '读取校历同步进度失败，可稍后刷新重试。', 'warning');
        return null;
    }).finally(() => {
        activeSyncPolls.delete(semesterId);
    });
    activeSyncPolls.set(semesterId, task);
    return task;
}

async function handleSyncCalendar(semesterId, button = null) {
    const semester = getSemesterById(semesterId);
    if (!semester) {
        return;
    }
    if (semester.can_manage === false) {
        showMessage('同校共享学期已可复用，校历同步由创建者维护。', 'warning');
        return;
    }
    if (semester.calendar_sync_active) return;
    updateSemesterSyncState(semesterId, {
        calendar_sync_status: 'pending',
        calendar_sync_active: true,
        calendar_sync_message: '校历同步已排队，正在连接教务系统。',
    });
    try {
        const result = await apiFetch(`/api/manage/semesters/${semester.id}/calendar/sync`, { method: 'POST' });
        showMessage(result.message || '校历同步已开始', 'success');
        await pollSemesterSync(semester.id);
    } catch (error) {
        showMessage(error.message || '校历同步启动失败', 'error');
        updateSemesterSyncState(semesterId, {
            calendar_sync_status: 'failed',
            calendar_sync_active: false,
            calendar_sync_message: error.message || '校历同步启动失败',
        });
    }
}

async function handleSyncCurrentSemester(button = null) {
    const originalText = button?.textContent || '';
    if (button) {
        button.disabled = true;
        button.classList.add('is-loading');
        button.innerHTML = '<span class="semester-button-spinner" aria-hidden="true"></span><span>正在同步</span>';
    }
    if (elements.submitBtn) {
        elements.submitBtn.disabled = true;
    }
    try {
        const result = await apiFetch('/api/manage/semesters/calendar/sync-current', { method: 'POST' });
        showMessage(result.message || '已从教务系统同步本学期', 'success');
        closeModal();
        const semesterId = Number(result.semester_id || 0);
        if (semesterId) {
            window.location.href = `${window.location.pathname}?semester_id=${semesterId}`;
        } else {
            window.location.reload();
        }
    } catch (error) {
        showMessage(error.message || '从教务系统同步失败', 'error');
        if (button) {
            button.disabled = false;
            button.textContent = originalText;
            button.classList.remove('is-loading');
        }
        if (elements.submitBtn) {
            elements.submitBtn.disabled = false;
        }
    }
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
            const row = event.target.closest('[data-semester-id]');
            const rowSemesterId = Number(row?.dataset.semesterId || 0);
            if (rowSemesterId) setActiveSemester(rowSemesterId);
            return;
        }

        const semesterId = Number(actionButton.dataset.semesterId || 0);
        if (!semesterId) {
            return;
        }

        if (actionButton.dataset.action === 'focus') {
            setActiveSemester(semesterId, { scrollCalendar: true });
            return;
        }
        if (actionButton.dataset.action === 'edit') {
            const semester = getSemesterById(semesterId);
            if (semester) {
                openModal('edit', semester);
            }
            return;
        }
        if (actionButton.dataset.action === 'sync-calendar') {
            await handleSyncCalendar(semesterId, actionButton);
            return;
        }
        if (actionButton.dataset.action === 'delete') {
            await handleDeleteSemester(semesterId);
        }
    });
    elements.list?.addEventListener('keydown', (event) => {
        if (event.key !== 'Enter' && event.key !== ' ') return;
        if (event.target.closest('[data-action]')) return;
        const row = event.target.closest('.academic-list-item[data-semester-id]');
        const semesterId = Number(row?.dataset.semesterId || 0);
        if (!semesterId) return;
        event.preventDefault();
        setActiveSemester(semesterId);
    });

    elements.startInput?.addEventListener('change', updateWeekPreview);
    elements.endInput?.addEventListener('change', updateWeekPreview);
    elements.nameInput?.addEventListener('input', () => {
        const value = String(elements.nameInput.value || '').trim();
        elements.nameInput.dataset.touched = value ? 'true' : 'false';
    });
    elements.form?.addEventListener('submit', handleSubmit);
    elements.syncCurrentBtn?.addEventListener('click', () => handleSyncCurrentSemester(elements.syncCurrentBtn));
    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && elements.modalBackdrop?.classList.contains('is-open')) {
            closeModal();
        }
    });
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
    const semesterId = Number(searchParams.get('semester_id') || 0);
    if (semesterId && getSemesterById(semesterId)) {
        setActiveSemester(semesterId);
    }
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

if (elements.modalBackdrop && elements.modalBackdrop.parentElement !== document.body) {
    document.body.appendChild(elements.modalBackdrop);
}
initEvents();
initDefaultState();
handleQueryOpen();
state.semesters
    .filter((semester) => semester.calendar_sync_active)
    .forEach((semester) => pollSemesterSync(semester.id));
