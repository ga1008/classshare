import { apiFetch } from '/static/js/api.js';
import { escapeHtml, showMessage } from '/static/js/ui.js';

function parseJsonScript(id, fallback) {
    const el = document.getElementById(id);
    if (!el) return fallback;
    try {
        return JSON.parse(el.textContent || '');
    } catch {
        return fallback;
    }
}

const boot = parseJsonScript('classrooms-page-data', {});

const state = {
    places: Array.isArray(boot.places) ? boot.places : [],
    dashboard: boot.dashboard && typeof boot.dashboard === 'object' ? boot.dashboard : {},
    semesters: Array.isArray(boot.semesters) ? boot.semesters : [],
    selectedSections: new Set([2, 3]),
    selectedWeek: 1,
    selectedWeekday: 1,
    freeOptions: (boot.dashboard && boot.dashboard.options) || {},
    freeQueryTerm: null,
};

const refs = {
    syncButtons: [
        document.getElementById('syncClassroomsBtn'),
        document.getElementById('syncClassroomsTopBtn'),
    ].filter(Boolean),
    focusButtons: [
        document.getElementById('focusFreeRoomBtn'),
        document.getElementById('focusFreeRoomTopBtn'),
    ].filter(Boolean),
    syncStatus: document.getElementById('classroomSyncStatus'),
    reloadButton: document.getElementById('classroomReloadBtn'),
    searchInput: document.getElementById('classroomSearchInput'),
    campusFilter: document.getElementById('classroomCampusFilter'),
    buildingFilter: document.getElementById('classroomBuildingFilter'),
    typeFilter: document.getElementById('classroomTypeFilter'),
    availabilityFilter: document.getElementById('classroomAvailabilityFilter'),
    resetButton: document.getElementById('classroomFilterResetBtn'),
    typeChips: document.getElementById('classroomQuickTypeChips'),
    placeList: document.getElementById('classroomPlaceList'),
    placeEmpty: document.getElementById('classroomPlaceEmpty'),
    resultCount: document.getElementById('classroomResultCount'),
    statActive: document.getElementById('classroomStatActive'),
    statSchedulable: document.getElementById('classroomStatSchedulable'),
    statBorrowable: document.getElementById('classroomStatBorrowable'),
    statExam: document.getElementById('classroomStatExam'),
    statSync: document.getElementById('classroomStatSync'),
    statStale: document.getElementById('classroomStatStale'),
    freePanel: document.getElementById('freeRoomQueryPanel'),
    freeForm: document.getElementById('freeRoomForm'),
    freeSubmit: document.getElementById('freeRoomSubmitBtn'),
    freeSemester: document.getElementById('freeRoomSemesterSelect'),
    freeCampus: document.getElementById('freeRoomCampusSelect'),
    freeBuilding: document.getElementById('freeRoomBuildingSelect'),
    freeType: document.getElementById('freeRoomTypeSelect'),
    freeName: document.getElementById('freeRoomNameInput'),
    freeWeekRow: document.getElementById('freeRoomWeekRow'),
    freeWeekdayRow: document.getElementById('freeRoomWeekdayRow'),
    freeSectionRow: document.getElementById('freeRoomSectionRow'),
    freeResultList: document.getElementById('freeRoomResultList'),
    freeResultEmpty: document.getElementById('freeRoomResultEmpty'),
    freeResultSummary: document.getElementById('freeRoomResultSummary'),
    freeResultTerm: document.getElementById('freeRoomResultTerm'),
};

function normalize(value) {
    return String(value || '').trim().toLowerCase();
}

function numberValue(value) {
    return Number(value || 0) || 0;
}

function setBusy(buttons, busy, busyText = '处理中') {
    buttons.forEach((button) => {
        if (!button) return;
        if (busy) {
            button.dataset.originalText = button.textContent;
            button.textContent = busyText;
            button.disabled = true;
        } else {
            button.textContent = button.dataset.originalText || button.textContent;
            button.disabled = false;
        }
    });
}

function debounce(fn, delay = 260) {
    let timer = null;
    return (...args) => {
        window.clearTimeout(timer);
        timer = window.setTimeout(() => fn(...args), delay);
    };
}

function optionHtml(item, fallbackName = '全部') {
    const id = String(item?.id ?? '');
    const name = String(item?.name || item?.label || id || fallbackName);
    return `<option value="${escapeHtml(id)}">${escapeHtml(name)}</option>`;
}

function updateSelectOptions(select, items, { keepValue = true, includeEmpty = false, emptyLabel = '全部' } = {}) {
    if (!select) return;
    const previous = select.value;
    const normalizedItems = Array.isArray(items) ? items : [];
    const html = [
        includeEmpty ? optionHtml({ id: '', name: emptyLabel }) : '',
        ...normalizedItems.map((item) => optionHtml(item, emptyLabel)),
    ].join('');
    select.innerHTML = html || optionHtml({ id: '', name: emptyLabel });
    if (keepValue && [...select.options].some((option) => option.value === previous)) {
        select.value = previous;
    }
}

function selectedSemester() {
    const id = numberValue(refs.freeSemester?.value);
    return state.semesters.find((item) => Number(item.id) === id) || null;
}

function defaultWeekForSemester(semester) {
    const maxWeek = Math.max(1, Math.min(numberValue(semester?.week_count) || 20, 30));
    const startDate = semester?.start_date ? new Date(`${semester.start_date}T00:00:00`) : null;
    if (!startDate || Number.isNaN(startDate.getTime())) {
        return Math.min(state.selectedWeek || 1, maxWeek);
    }
    const today = new Date();
    const diffDays = Math.floor((today - startDate) / 86400000);
    if (diffDays < 0) return 1;
    return Math.max(1, Math.min(Math.floor(diffDays / 7) + 1, maxWeek));
}

function renderWeeks() {
    const semester = selectedSemester();
    const option = refs.freeSemester?.selectedOptions?.[0];
    const weekCount = Math.max(1, Math.min(numberValue(semester?.week_count || option?.dataset.weekCount) || 20, 30));
    state.selectedWeek = Math.max(1, Math.min(state.selectedWeek || defaultWeekForSemester(semester), weekCount));
    if (!refs.freeWeekRow) return;
    refs.freeWeekRow.innerHTML = Array.from({ length: weekCount }, (_, index) => {
        const week = index + 1;
        return `<button type="button" class="classroom-toggle-chip${week === state.selectedWeek ? ' is-active' : ''}" data-week="${week}">${week}</button>`;
    }).join('');
}

function renderSections(sections = []) {
    const normalized = Array.isArray(sections) && sections.length
        ? sections
        : Array.from({ length: 11 }, (_, index) => ({ id: String(index + 1), name: `第 ${index + 1} 节`, time: '' }));
    if (!refs.freeSectionRow) return;
    refs.freeSectionRow.innerHTML = normalized.map((item) => {
        const id = String(item.id || item.section || item.name || '').replace(/[^\d]/g, '') || String(item.id || '');
        const number = numberValue(id);
        const active = state.selectedSections.has(number);
        const title = item.time ? ` title="${escapeHtml(item.time)}"` : '';
        return `<button type="button" class="classroom-toggle-chip${active ? ' is-active' : ''}" data-section="${number}"${title}>${escapeHtml(item.name || `第 ${number} 节`)}</button>`;
    }).join('');
}

function refreshToggleState(container, selector, activeValue) {
    container?.querySelectorAll(selector).forEach((button) => {
        button.classList.toggle('is-active', Number(button.dataset.week || button.dataset.weekday) === activeValue);
    });
}

function updateDashboard(dashboard = {}) {
    state.dashboard = dashboard;
    const setText = (el, value) => {
        if (el) el.textContent = String(value);
    };
    setText(refs.statActive, numberValue(dashboard.active_count));
    setText(refs.statSchedulable, numberValue(dashboard.schedulable_count));
    setText(refs.statBorrowable, numberValue(dashboard.borrowable_count));
    setText(refs.statExam, numberValue(dashboard.exam_count));
    setText(refs.statSync, dashboard.last_synced_at ? String(dashboard.last_synced_at).slice(0, 10) : '-');
    setText(refs.statStale, `${numberValue(dashboard.stale_count)} 条待复核`);
    if (dashboard.options) {
        state.freeOptions = dashboard.options;
    }
}

function placeSearchText(place) {
    return normalize([
        place.room_code,
        place.room_name,
        place.room_full_name,
        place.display_name,
        place.campus_name,
        place.building_name,
        place.room_type_name,
        place.organization_name,
        place.manager_name,
    ].filter(Boolean).join(' '));
}

function badge(text, tone = '') {
    if (!text) return '';
    return `<span class="classroom-badge${tone ? ` ${tone}` : ''}">${escapeHtml(text)}</span>`;
}

function renderPlaceCard(place) {
    const meta = [
        place.campus_name,
        place.building_name,
        place.room_type_name,
        place.seat_count ? `${place.seat_count} 座` : '',
        place.organization_name,
    ].filter(Boolean);
    const flags = [
        place.is_schedulable ? badge('可排课', 'is-ok') : '',
        place.is_borrowable ? badge('可借用', 'is-live') : '',
        place.is_exam_schedulable ? badge('考试可用', 'is-ok') : '',
        place.sync_status === 'stale' ? badge('待复核', 'is-warn') : '',
    ].filter(Boolean).join('');
    return `
        <article class="classroom-place-card${place.sync_status === 'stale' ? ' is-stale' : ''}">
            <div class="classroom-place-main">
                <div class="classroom-place-title">
                    <h4 title="${escapeHtml(place.display_name || place.room_name || '')}">${escapeHtml(place.display_name || place.room_name || '未命名场地')}</h4>
                    ${place.room_code ? `<code>${escapeHtml(place.room_code)}</code>` : ''}
                </div>
                <div class="classroom-place-meta">
                    ${meta.map((item) => `<span>${escapeHtml(item)}</span>`).join('')}
                </div>
            </div>
            <div class="classroom-place-flags">${flags || badge('教务场地')}</div>
        </article>
    `;
}

function renderPlaces() {
    if (!refs.placeList) return;
    const query = normalize(refs.searchInput?.value);
    const filtered = state.places.filter((place) => !query || placeSearchText(place).includes(query));
    refs.placeList.innerHTML = filtered.map(renderPlaceCard).join('');
    if (refs.resultCount) refs.resultCount.textContent = String(filtered.length);
    if (refs.placeEmpty) refs.placeEmpty.hidden = filtered.length > 0;
}

function currentListParams() {
    const params = new URLSearchParams();
    params.set('limit', '600');
    const q = String(refs.searchInput?.value || '').trim();
    const campus = refs.campusFilter?.value || '';
    const building = refs.buildingFilter?.value || '';
    const type = refs.typeFilter?.value || '';
    const availability = refs.availabilityFilter?.value || '';
    if (q) params.set('q', q);
    if (campus) params.set('campus_id', campus);
    if (building) params.set('building_id', building);
    if (type) params.set('room_type_id', type);
    if (availability) params.set('availability', availability);
    return params;
}

async function reloadPlaces({ silent = false } = {}) {
    if (!silent) setBusy([refs.reloadButton], true, '刷新中');
    try {
        const result = await apiFetch(`/api/manage/classrooms/teaching-places?${currentListParams().toString()}`);
        state.places = Array.isArray(result.items) ? result.items : [];
        if (result.dashboard) updateDashboard(result.dashboard);
        renderPlaces();
    } catch (error) {
        showMessage(error.message || '刷新教学场地失败。', 'error');
    } finally {
        if (!silent) setBusy([refs.reloadButton], false);
    }
}

const debouncedReloadPlaces = debounce(() => reloadPlaces({ silent: true }), 260);

function setActiveRoomTypeChip(value) {
    refs.typeChips?.querySelectorAll('[data-room-type]').forEach((chip) => {
        chip.classList.toggle('is-active', chip.dataset.roomType === value);
    });
}

async function syncClassrooms() {
    setBusy(refs.syncButtons, true, '同步中');
    if (refs.syncStatus) {
        refs.syncStatus.textContent = '正在连接教务系统并同步教学场地...';
        refs.syncStatus.classList.add('is-visible');
    }
    try {
        const result = await apiFetch('/api/manage/classrooms/sync-academic', { method: 'POST' });
        if (refs.syncStatus) {
            refs.syncStatus.textContent = `${result.message || '教学场地同步完成。'} 新增 ${numberValue(result.created_count)}，更新 ${numberValue(result.updated_count)}，待复核 ${numberValue(result.stale_count)}。`;
        }
        showMessage(result.message || '教学场地同步完成。', 'success');
        await reloadPlaces({ silent: true });
        await loadFreeOptions({ silent: true });
    } catch (error) {
        if (refs.syncStatus) {
            refs.syncStatus.textContent = error.message || '教学场地同步失败。';
        }
        showMessage(error.message || '教学场地同步失败。', 'error');
    } finally {
        setBusy(refs.syncButtons, false);
    }
}

function freeOptionParams() {
    const params = new URLSearchParams();
    const semesterId = refs.freeSemester?.value || '';
    if (semesterId) params.set('semester_id', semesterId);
    const campus = refs.freeCampus?.value || '1';
    if (campus) params.set('xqh_id', campus);
    return params;
}

async function loadFreeOptions({ silent = false } = {}) {
    if (refs.freeSubmit) refs.freeSubmit.disabled = true;
    try {
        const result = await apiFetch(`/api/manage/classrooms/free-options?${freeOptionParams().toString()}`);
        state.freeQueryTerm = result.term || null;
        const options = result.options || {};
        updateSelectOptions(refs.freeBuilding, options.buildings || [], { keepValue: true });
        updateSelectOptions(refs.freeType, options.room_types || [], { keepValue: true });
        renderSections(options.sections || []);
        if (refs.freeResultTerm) {
            refs.freeResultTerm.textContent = result.semester_name || (result.term ? `${result.term.xnm}-${result.term.xqm}` : '教务实时');
        }
    } catch (error) {
        if (!silent) showMessage(error.message || '读取教务系统教室选项失败。', 'error');
        renderSections();
    } finally {
        if (refs.freeSubmit) refs.freeSubmit.disabled = false;
    }
}

function selectedSections() {
    return [...state.selectedSections].sort((a, b) => a - b);
}

function freeQueryPayload() {
    const semesterId = refs.freeSemester?.value || '';
    const payload = {
        xqh_id: refs.freeCampus?.value || '1',
        lh: refs.freeBuilding?.value || '',
        cdlb_id: refs.freeType?.value || '',
        cdmc: String(refs.freeName?.value || '').trim(),
        weeks: [state.selectedWeek],
        weekday: [state.selectedWeekday],
        sections: selectedSections(),
        page_size: 100,
    };
    if (semesterId) payload.semester_id = Number(semesterId);
    return payload;
}

function renderFreeCard(place) {
    const meta = [
        place.campus_name,
        place.building_name,
        place.room_type_name,
        place.seat_count ? `${place.seat_count} 座` : '',
        place.organization_name,
    ].filter(Boolean);
    return `
        <article class="classroom-free-card">
            <div class="classroom-free-main">
                <div class="classroom-free-title">
                    <h4 title="${escapeHtml(place.display_name || place.room_name || '')}">${escapeHtml(place.display_name || place.room_name || '空闲场地')}</h4>
                    ${place.room_code ? `<code>${escapeHtml(place.room_code)}</code>` : ''}
                </div>
                <div class="classroom-free-meta">
                    ${meta.map((item) => `<span>${escapeHtml(item)}</span>`).join('')}
                </div>
            </div>
            <div class="classroom-place-flags">${badge('空闲', 'is-live')}${place.is_borrowable ? badge('可借用', 'is-ok') : ''}</div>
        </article>
    `;
}

function describeFreeQuery(result) {
    const term = result.semester_name || (result.term ? `${result.term.xnm}-${result.term.xqm}` : '');
    const sectionText = selectedSections().join('、');
    const weekday = `周${['', '一', '二', '三', '四', '五', '六', '日'][state.selectedWeekday] || state.selectedWeekday}`;
    return [term, `第 ${state.selectedWeek} 周`, weekday, `第 ${sectionText} 节`]
        .filter(Boolean)
        .join(' · ');
}

async function queryFreeRooms(event) {
    event?.preventDefault();
    if (!state.selectedWeek) {
        showMessage('请选择周次。', 'warning');
        return;
    }
    if (!state.selectedWeekday) {
        showMessage('请选择星期。', 'warning');
        return;
    }
    if (!state.selectedSections.size) {
        showMessage('请选择节次。', 'warning');
        return;
    }
    setBusy([refs.freeSubmit], true, '查询中');
    try {
        const result = await apiFetch('/api/manage/classrooms/free-query', {
            method: 'POST',
            body: freeQueryPayload(),
        });
        const items = Array.isArray(result.items) ? result.items : [];
        refs.freeResultList.innerHTML = items.map(renderFreeCard).join('');
        refs.freeResultEmpty.hidden = items.length > 0;
        if (refs.freeResultSummary) {
            refs.freeResultSummary.textContent = `${describeFreeQuery(result)}，共 ${numberValue(result.total_count)} 个可用场地`;
        }
        if (refs.freeResultTerm) {
            refs.freeResultTerm.textContent = result.semester_name || '教务实时';
        }
        if (!items.length) {
            refs.freeResultEmpty.innerHTML = '<strong>该时间段暂无空闲教室</strong>教务系统返回空结果，可调整校区、楼号、类别或节次。';
        }
    } catch (error) {
        showMessage(error.message || '空闲教室实时查询失败。', 'error');
    } finally {
        setBusy([refs.freeSubmit], false);
    }
}

function bindEvents() {
    refs.syncButtons.forEach((button) => button.addEventListener('click', syncClassrooms));
    refs.focusButtons.forEach((button) => {
        button.addEventListener('click', () => {
            refs.freePanel?.scrollIntoView({ behavior: 'smooth', block: 'start' });
            window.setTimeout(() => refs.freeSemester?.focus(), 180);
        });
    });
    refs.reloadButton?.addEventListener('click', () => reloadPlaces());
    refs.searchInput?.addEventListener('input', debouncedReloadPlaces);
    [refs.campusFilter, refs.buildingFilter, refs.typeFilter, refs.availabilityFilter].forEach((select) => {
        select?.addEventListener('change', () => {
            if (select === refs.typeFilter) setActiveRoomTypeChip(select.value || '');
            reloadPlaces({ silent: true });
        });
    });
    refs.resetButton?.addEventListener('click', () => {
        if (refs.searchInput) refs.searchInput.value = '';
        if (refs.campusFilter) refs.campusFilter.value = '';
        if (refs.buildingFilter) refs.buildingFilter.value = '';
        if (refs.typeFilter) refs.typeFilter.value = '';
        if (refs.availabilityFilter) refs.availabilityFilter.value = '';
        setActiveRoomTypeChip('');
        reloadPlaces({ silent: true });
    });
    refs.typeChips?.addEventListener('click', (event) => {
        const chip = event.target.closest('[data-room-type]');
        if (!chip) return;
        if (refs.typeFilter) refs.typeFilter.value = chip.dataset.roomType || '';
        setActiveRoomTypeChip(chip.dataset.roomType || '');
        reloadPlaces({ silent: true });
    });
    refs.freeSemester?.addEventListener('change', () => {
        state.selectedWeek = defaultWeekForSemester(selectedSemester());
        renderWeeks();
        loadFreeOptions();
    });
    refs.freeCampus?.addEventListener('change', () => loadFreeOptions());
    refs.freeWeekRow?.addEventListener('click', (event) => {
        const button = event.target.closest('[data-week]');
        if (!button) return;
        state.selectedWeek = numberValue(button.dataset.week);
        refreshToggleState(refs.freeWeekRow, '[data-week]', state.selectedWeek);
    });
    refs.freeWeekdayRow?.addEventListener('click', (event) => {
        const button = event.target.closest('[data-weekday]');
        if (!button) return;
        state.selectedWeekday = numberValue(button.dataset.weekday);
        refreshToggleState(refs.freeWeekdayRow, '[data-weekday]', state.selectedWeekday);
    });
    refs.freeSectionRow?.addEventListener('click', (event) => {
        const button = event.target.closest('[data-section]');
        if (!button) return;
        const value = numberValue(button.dataset.section);
        if (!value) return;
        if (state.selectedSections.has(value)) {
            state.selectedSections.delete(value);
        } else {
            state.selectedSections.add(value);
        }
        button.classList.toggle('is-active', state.selectedSections.has(value));
    });
    refs.freeForm?.addEventListener('submit', queryFreeRooms);
}

function init() {
    state.selectedWeek = defaultWeekForSemester(selectedSemester());
    renderWeeks();
    renderSections(state.freeOptions.sections || []);
    renderPlaces();
    updateDashboard(state.dashboard);
    bindEvents();
    loadFreeOptions({ silent: true });
}

init();
