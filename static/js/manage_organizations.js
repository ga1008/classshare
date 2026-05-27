import { apiFetch } from './api.js';
import { escapeHtml, showMessage } from './ui.js';

const state = {
    payload: window.__organizationPayload || { schools: [], summary: {} },
    selectedSchoolCode: '',
    selectedCollegeId: null,
};

const els = {};
const byId = (id) => document.getElementById(id);

const debounce = (fn, delay = 220) => {
    let timer = null;
    return (...args) => {
        window.clearTimeout(timer);
        timer = window.setTimeout(() => fn(...args), delay);
    };
};

function cacheElements() {
    [
        'org-search-input',
        'org-include-inactive-input',
        'org-refresh-btn',
        'org-tree',
        'org-stat-schools',
        'org-stat-colleges',
        'org-stat-departments',
        'org-stat-active',
        'org-school-form',
        'org-current-note',
        'org-current-school-form',
        'org-deactivate-school-btn',
        'org-college-form',
        'org-department-form',
    ].forEach((id) => {
        els[id] = byId(id);
    });
}

function currentSchool() {
    return (state.payload.schools || []).find((item) => item.school_code === state.selectedSchoolCode) || null;
}

function currentCollege() {
    const school = currentSchool();
    if (!school) return null;
    return (school.colleges || []).find((item) => Number(item.id) === Number(state.selectedCollegeId)) || null;
}

async function loadTree({ keepSelection = true } = {}) {
    const params = new URLSearchParams();
    const query = els['org-search-input']?.value?.trim();
    if (query) params.set('q', query);
    if (els['org-include-inactive-input']?.checked) params.set('include_inactive', '1');
    try {
        const payload = await apiFetch(`/api/manage/system/organizations/tree?${params.toString()}`, { method: 'GET' });
        state.payload = { schools: payload.schools || [], summary: payload.summary || {} };
        if (!keepSelection || !state.payload.schools.some((item) => item.school_code === state.selectedSchoolCode)) {
            state.selectedSchoolCode = state.payload.schools[0]?.school_code || '';
            state.selectedCollegeId = state.payload.schools[0]?.colleges?.[0]?.id || null;
        }
        if (keepSelection && state.selectedCollegeId) {
            const school = currentSchool();
            if (!school?.colleges?.some((item) => Number(item.id) === Number(state.selectedCollegeId))) {
                state.selectedCollegeId = school?.colleges?.[0]?.id || null;
            }
        }
        render();
    } catch {
        if (els['org-tree']) {
            els['org-tree'].innerHTML = '<div class="org-empty">组织目录加载失败，请稍后重试。</div>';
        }
    }
}

function updateStats() {
    const summary = state.payload.summary || {};
    const pairs = [
        ['org-stat-schools', summary.school_count ?? 0],
        ['org-stat-colleges', summary.college_count ?? 0],
        ['org-stat-departments', summary.department_count ?? 0],
        ['org-stat-active', summary.active_school_count ?? 0],
    ];
    pairs.forEach(([id, value]) => {
        if (els[id]) els[id].textContent = String(value);
    });
}

function countLabel(item) {
    const count = Number(item?.reference_count || 0);
    return count > 0 ? `${count} 个引用` : '暂无引用';
}

function render() {
    updateStats();
    renderTree();
    renderCurrentForms();
}

function renderTree() {
    const container = els['org-tree'];
    if (!container) return;
    const schools = state.payload.schools || [];
    if (!schools.length) {
        container.innerHTML = '<div class="org-empty">暂无组织目录，先新增一个学校。</div>';
        return;
    }
    container.innerHTML = schools.map(renderSchool).join('');
}

function renderSchool(school) {
    const activeClass = school.school_code === state.selectedSchoolCode ? ' is-active' : '';
    const inactiveChip = school.is_active ? '' : '<span class="org-chip is-off">已停用</span>';
    const collegesHtml = (school.colleges || []).map(renderCollege).join('');
    return `
        <article class="org-school-card${activeClass}" data-school-code="${escapeHtml(school.school_code)}">
            <div class="org-school-head">
                <div class="org-school-title">
                    <strong>${escapeHtml(school.school_name)}</strong>
                    <span>${escapeHtml(school.school_code)}</span>
                </div>
                <div class="org-actions">
                    <button type="button" class="btn btn-ghost btn-sm" data-select-school="${escapeHtml(school.school_code)}">选择</button>
                </div>
            </div>
            <div class="org-chip-line">
                <span class="org-chip">${escapeHtml(countLabel(school))}</span>
                <span class="org-chip is-muted">${(school.colleges || []).length} 个学院</span>
                ${inactiveChip}
            </div>
            <div class="org-college-list">
                ${collegesHtml || '<div class="org-empty">这个学校还没有学院。</div>'}
            </div>
        </article>
    `;
}

function renderCollege(college) {
    const activeClass = Number(college.id) === Number(state.selectedCollegeId) ? ' is-active' : '';
    const inactiveChip = college.is_active ? '' : '<span class="org-chip is-off">已停用</span>';
    const departmentsHtml = (college.departments || []).map(renderDepartment).join('');
    return `
        <div class="org-unit-card${activeClass}" data-college-id="${college.id}">
            <div class="org-unit-head">
                <div class="org-unit-title">
                    <strong>${escapeHtml(college.college_name)}</strong>
                    <span>${escapeHtml(countLabel(college))}</span>
                </div>
                <div class="org-actions">
                    <button type="button" class="btn btn-ghost btn-sm" data-select-college="${college.id}" data-school-code="${escapeHtml(college.school_code)}">选择</button>
                    <button type="button" class="btn btn-outline btn-sm" data-rename-college="${college.id}">改名</button>
                    <button type="button" class="btn btn-danger btn-sm" data-delete-college="${college.id}">停用</button>
                </div>
            </div>
            <div class="org-chip-line">${inactiveChip}</div>
            <div class="org-dept-list">
                ${departmentsHtml || '<div class="org-empty">这个学院还没有系部。</div>'}
            </div>
        </div>
    `;
}

function renderDepartment(department) {
    const inactiveChip = department.is_active ? '' : '<span class="org-chip is-off">已停用</span>';
    return `
        <div class="org-unit-card" data-department-id="${department.id}">
            <div class="org-unit-head">
                <div class="org-unit-title">
                    <strong>${escapeHtml(department.department_name)}</strong>
                    <span>${escapeHtml(countLabel(department))}</span>
                </div>
                <div class="org-actions">
                    <button type="button" class="btn btn-outline btn-sm" data-rename-department="${department.id}">改名</button>
                    <button type="button" class="btn btn-danger btn-sm" data-delete-department="${department.id}">停用</button>
                </div>
            </div>
            <div class="org-chip-line">${inactiveChip}</div>
        </div>
    `;
}

function renderCurrentForms() {
    const school = currentSchool();
    const college = currentCollege();
    if (els['org-current-school-form']) {
        els['org-current-school-form'].hidden = !school;
        if (school) {
            const form = els['org-current-school-form'];
            form.school_name.value = school.school_name || '';
            form.display_order.value = school.display_order || 0;
            form.is_active.value = school.is_active ? '1' : '0';
        }
    }
    if (els['org-college-form']) {
        els['org-college-form'].hidden = !school;
    }
    if (els['org-department-form']) {
        els['org-department-form'].hidden = !college;
    }
    if (els['org-current-note']) {
        els['org-current-note'].textContent = school
            ? `当前学校：${school.school_name}。${college ? `当前学院：${college.college_name}。` : '请选择学院后维护系部。'}`
            : '请选择左侧学校后维护学院与系部。';
    }
}

async function submitSchool(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const body = {
        school_code: form.school_code.value.trim(),
        school_name: form.school_name.value.trim(),
        display_order: Number(form.display_order.value || 0),
    };
    await apiFetch('/api/manage/system/organizations/schools', { method: 'POST', body });
    showMessage('学校已保存', 'success');
    form.reset();
    await loadTree({ keepSelection: false });
}

async function updateCurrentSchool(event) {
    event.preventDefault();
    const school = currentSchool();
    if (!school) return;
    const form = event.currentTarget;
    await apiFetch(`/api/manage/system/organizations/schools/${school.id}`, {
        method: 'PATCH',
        body: {
            school_name: form.school_name.value.trim(),
            display_order: Number(form.display_order.value || 0),
            is_active: form.is_active.value,
        },
    });
    showMessage('学校已更新', 'success');
    await loadTree({ keepSelection: true });
}

async function submitCollege(event) {
    event.preventDefault();
    const school = currentSchool();
    if (!school) return;
    const form = event.currentTarget;
    await apiFetch('/api/manage/system/organizations/colleges', {
        method: 'POST',
        body: {
            school_code: school.school_code,
            college_name: form.college_name.value.trim(),
            display_order: Number(form.display_order.value || 0),
        },
    });
    showMessage('学院已保存', 'success');
    form.reset();
    await loadTree({ keepSelection: true });
}

async function submitDepartment(event) {
    event.preventDefault();
    const school = currentSchool();
    const college = currentCollege();
    if (!school || !college) return;
    const form = event.currentTarget;
    await apiFetch('/api/manage/system/organizations/departments', {
        method: 'POST',
        body: {
            school_code: school.school_code,
            college_name: college.college_name,
            department_name: form.department_name.value.trim(),
            display_order: Number(form.display_order.value || 0),
        },
    });
    showMessage('系部已保存', 'success');
    form.reset();
    await loadTree({ keepSelection: true });
}

function findCollege(id) {
    for (const school of state.payload.schools || []) {
        const college = (school.colleges || []).find((item) => Number(item.id) === Number(id));
        if (college) return college;
    }
    return null;
}

function findDepartment(id) {
    for (const school of state.payload.schools || []) {
        for (const college of school.colleges || []) {
            const department = (college.departments || []).find((item) => Number(item.id) === Number(id));
            if (department) return department;
        }
    }
    return null;
}

async function handleTreeClick(event) {
    const selectSchoolBtn = event.target.closest?.('[data-select-school]');
    const selectCollegeBtn = event.target.closest?.('[data-select-college]');
    const renameCollegeBtn = event.target.closest?.('[data-rename-college]');
    const renameDepartmentBtn = event.target.closest?.('[data-rename-department]');
    const deleteCollegeBtn = event.target.closest?.('[data-delete-college]');
    const deleteDepartmentBtn = event.target.closest?.('[data-delete-department]');
    if (selectSchoolBtn) {
        state.selectedSchoolCode = selectSchoolBtn.dataset.selectSchool;
        const school = currentSchool();
        state.selectedCollegeId = school?.colleges?.[0]?.id || null;
        render();
        return;
    }
    if (selectCollegeBtn) {
        state.selectedSchoolCode = selectCollegeBtn.dataset.schoolCode;
        state.selectedCollegeId = Number(selectCollegeBtn.dataset.selectCollege || 0);
        render();
        return;
    }
    if (renameCollegeBtn) {
        const college = findCollege(renameCollegeBtn.dataset.renameCollege);
        const nextName = window.prompt('新的学院名称', college?.college_name || '');
        if (!college || !nextName?.trim()) return;
        await apiFetch(`/api/manage/system/organizations/colleges/${college.id}`, {
            method: 'PATCH',
            body: {
                college_name: nextName.trim(),
                display_order: college.display_order || 0,
                is_active: college.is_active ? '1' : '0',
            },
        });
        showMessage('学院已更新', 'success');
        await loadTree({ keepSelection: true });
        return;
    }
    if (renameDepartmentBtn) {
        const department = findDepartment(renameDepartmentBtn.dataset.renameDepartment);
        const nextName = window.prompt('新的系部名称', department?.department_name || '');
        if (!department || !nextName?.trim()) return;
        await apiFetch(`/api/manage/system/organizations/departments/${department.id}`, {
            method: 'PATCH',
            body: {
                department_name: nextName.trim(),
                display_order: department.display_order || 0,
                is_active: department.is_active ? '1' : '0',
            },
        });
        showMessage('系部已更新', 'success');
        await loadTree({ keepSelection: true });
        return;
    }
    if (deleteCollegeBtn) {
        const college = findCollege(deleteCollegeBtn.dataset.deleteCollege);
        if (!college || !window.confirm(`停用学院“${college.college_name}”？历史资源会保留。`)) return;
        await apiFetch(`/api/manage/system/organizations/colleges/${college.id}`, { method: 'DELETE' });
        showMessage('学院已停用', 'success');
        await loadTree({ keepSelection: true });
        return;
    }
    if (deleteDepartmentBtn) {
        const department = findDepartment(deleteDepartmentBtn.dataset.deleteDepartment);
        if (!department || !window.confirm(`停用系部“${department.department_name}”？历史资源会保留。`)) return;
        await apiFetch(`/api/manage/system/organizations/departments/${department.id}`, { method: 'DELETE' });
        showMessage('系部已停用', 'success');
        await loadTree({ keepSelection: true });
    }
}

async function deactivateCurrentSchool() {
    const school = currentSchool();
    if (!school || !window.confirm(`停用学校“${school.school_name}”？历史资源会保留，但新建资源将不再默认选择它。`)) {
        return;
    }
    await apiFetch(`/api/manage/system/organizations/schools/${school.id}`, { method: 'DELETE' });
    showMessage('学校已停用', 'success');
    await loadTree({ keepSelection: true });
}

function bindEvents() {
    const reloadDebounced = debounce(() => loadTree({ keepSelection: false }));
    els['org-search-input']?.addEventListener('input', reloadDebounced);
    els['org-include-inactive-input']?.addEventListener('change', () => loadTree({ keepSelection: false }));
    els['org-refresh-btn']?.addEventListener('click', () => loadTree({ keepSelection: true }));
    els['org-tree']?.addEventListener('click', handleTreeClick);
    els['org-school-form']?.addEventListener('submit', submitSchool);
    els['org-current-school-form']?.addEventListener('submit', updateCurrentSchool);
    els['org-college-form']?.addEventListener('submit', submitCollege);
    els['org-department-form']?.addEventListener('submit', submitDepartment);
    els['org-deactivate-school-btn']?.addEventListener('click', deactivateCurrentSchool);
}

document.addEventListener('DOMContentLoaded', () => {
    cacheElements();
    state.selectedSchoolCode = state.payload.schools?.[0]?.school_code || '';
    state.selectedCollegeId = state.payload.schools?.[0]?.colleges?.[0]?.id || null;
    bindEvents();
    render();
});
