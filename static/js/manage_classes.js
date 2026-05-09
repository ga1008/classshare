import { apiFetch } from '/static/js/api.js';
import { showMessage } from '/static/js/ui.js';

const data = window.MANAGE_CLASSES_DATA || {};
const classes = Array.isArray(data.classes) ? data.classes : [];
const classMap = new Map(classes.map((item) => [Number(item.id), item]));

const elements = {
    form: document.getElementById('classCreateForm'),
    classNameInput: document.getElementById('classNameInput'),
    createButtons: [
        document.getElementById('focusClassCreateBtn'),
        document.getElementById('heroClassCreateBtn'),
    ].filter(Boolean),
    templateButtons: [
        document.getElementById('classTemplateDownloadBtn'),
        document.getElementById('heroTemplateDownloadBtn'),
    ].filter(Boolean),
    classList: document.getElementById('classList'),
    cards: Array.from(document.querySelectorAll('[data-class-card]')),
    resultCount: document.getElementById('classResultCount'),
    filterEmpty: document.getElementById('classFilterEmpty'),
    searchInput: document.getElementById('classSearchInput'),
    departmentFilter: document.getElementById('classDepartmentFilter'),
    healthFilter: document.getElementById('classHealthFilter'),
    sortSelect: document.getElementById('classSortSelect'),
    resetButton: document.getElementById('classFilterResetBtn'),
    departmentChips: document.getElementById('classDepartmentChips'),
    drawer: document.getElementById('classStudentDrawer'),
    drawerPanel: document.querySelector('.class-student-drawer'),
    drawerClose: document.getElementById('classStudentDrawerClose'),
    drawerTitle: document.getElementById('classStudentDrawerTitle'),
    drawerKicker: document.getElementById('classStudentDrawerKicker'),
    drawerMeta: document.getElementById('classStudentDrawerMeta'),
    drawerList: document.getElementById('classStudentList'),
    drawerEmpty: document.getElementById('classStudentEmpty'),
    drawerSearch: document.getElementById('classStudentSearchInput'),
    drawerExport: document.getElementById('classStudentExportBtn'),
};

let activeDrawerClass = null;
let activeDrawerTrigger = null;

function normalize(value) {
    return String(value || '').trim().toLowerCase();
}

function escapeHtml(value) {
    return String(value || '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
}

function numberValue(value) {
    return Number(value || 0) || 0;
}

function classSearchText(classItem) {
    const studentText = (classItem.students || [])
        .map((student) => [
            student.name,
            student.display_name,
            student.nickname,
            student.student_id_number,
            student.email,
        ].filter(Boolean).join(' '))
        .join(' ');
    return normalize([
        classItem.name,
        classItem.department,
        classItem.department_label,
        studentText,
    ].filter(Boolean).join(' '));
}

const classSearchIndex = new Map(classes.map((item) => [Number(item.id), classSearchText(item)]));

function focusCreateForm() {
    if (!elements.classNameInput) {
        return;
    }
    elements.classNameInput.scrollIntoView({ behavior: 'smooth', block: 'center' });
    window.setTimeout(() => {
        elements.classNameInput.focus();
    }, 180);
}

function setActiveDepartmentChip(value) {
    const normalizedValue = value || 'all';
    elements.departmentChips?.querySelectorAll('[data-department-chip]').forEach((chip) => {
        chip.classList.toggle('is-active', chip.dataset.departmentChip === normalizedValue);
    });
}

function compareCards(a, b) {
    const sortValue = elements.sortSelect?.value || 'department';
    const classA = classMap.get(numberValue(a.dataset.classId)) || {};
    const classB = classMap.get(numberValue(b.dataset.classId)) || {};
    if (sortValue === 'students-desc') {
        return numberValue(b.dataset.studentCount) - numberValue(a.dataset.studentCount)
            || String(classA.name || '').localeCompare(String(classB.name || ''), 'zh-Hans-CN');
    }
    if (sortValue === 'missing-desc') {
        return numberValue(b.dataset.missingEmailCount) - numberValue(a.dataset.missingEmailCount)
            || String(classA.name || '').localeCompare(String(classB.name || ''), 'zh-Hans-CN');
    }
    if (sortValue === 'recent-desc') {
        return String(b.dataset.latestTime || '').localeCompare(String(a.dataset.latestTime || ''))
            || String(classA.name || '').localeCompare(String(classB.name || ''), 'zh-Hans-CN');
    }
    return String(a.dataset.department || '').localeCompare(String(b.dataset.department || ''), 'zh-Hans-CN')
        || String(classA.name || '').localeCompare(String(classB.name || ''), 'zh-Hans-CN');
}

function cardPassesHealthFilter(card) {
    const health = elements.healthFilter?.value || 'all';
    const missingCount = numberValue(card.dataset.missingEmailCount);
    const offeringCount = numberValue(card.dataset.offeringCount);
    if (health === 'missing-email') return missingCount > 0;
    if (health === 'complete-email') return missingCount === 0;
    if (health === 'bound') return offeringCount > 0;
    if (health === 'unbound') return offeringCount === 0;
    return true;
}

function applyFilters() {
    const query = normalize(elements.searchInput?.value);
    const department = elements.departmentFilter?.value || 'all';
    let visibleCount = 0;

    const sortedCards = [...elements.cards].sort(compareCards);
    sortedCards.forEach((card) => {
        elements.classList?.appendChild(card);
        const classId = numberValue(card.dataset.classId);
        const matchesQuery = !query || (classSearchIndex.get(classId) || '').includes(query);
        const matchesDepartment = department === 'all' || card.dataset.department === department;
        const visible = matchesQuery && matchesDepartment && cardPassesHealthFilter(card);
        card.hidden = !visible;
        if (visible) visibleCount += 1;
    });

    if (elements.resultCount) {
        elements.resultCount.textContent = String(visibleCount);
    }
    if (elements.filterEmpty) {
        elements.filterEmpty.hidden = visibleCount > 0 || classes.length === 0;
    }
    setActiveDepartmentChip(department);
}

function resetFilters() {
    if (elements.searchInput) elements.searchInput.value = '';
    if (elements.departmentFilter) elements.departmentFilter.value = 'all';
    if (elements.healthFilter) elements.healthFilter.value = 'all';
    if (elements.sortSelect) elements.sortSelect.value = 'department';
    applyFilters();
}

function csvCell(value) {
    const text = String(value ?? '');
    if (/[",\n\r]/.test(text)) {
        return `"${text.replaceAll('"', '""')}"`;
    }
    return text;
}

function downloadCsv(fileName, rows) {
    const csvText = rows.map((row) => row.map(csvCell).join(',')).join('\n');
    const blob = new Blob(['\ufeff', csvText], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = fileName;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function downloadRosterTemplate() {
    downloadCsv('班级学生名单模板.csv', [
        ['姓名', '学号', '性别', '邮箱', '手机号'],
        ['张三', '20260001', '男', 'student@example.com', '13800000000'],
    ]);
}

function exportClassRoster(classItem) {
    if (!classItem) return;
    const rows = [
        ['班级', '系别', '姓名', '学号', '昵称', '邮箱', '手机号'],
        ...(classItem.students || []).map((student) => [
            classItem.name || '',
            classItem.department_label || classItem.department || '',
            student.name || '',
            student.student_id_number || '',
            student.nickname || '',
            student.email || '',
            student.phone || '',
        ]),
    ];
    downloadCsv(`${classItem.name || '班级'}-学生名单.csv`, rows);
}

async function handleDelete(button) {
    const classId = Number(button.dataset.classId || 0);
    const className = String(button.dataset.className || '').trim() || '当前班级';
    if (!classId) {
        return;
    }

    const confirmed = window.confirm(
        `确定删除班级“${className}”吗？\n这会同时删除该班级下的学生和与课堂的关联记录。`
    );
    if (!confirmed) {
        return;
    }

    try {
        const result = await apiFetch(`/api/manage/classes/${classId}`, {
            method: 'DELETE',
            silent: true,
        });
        showMessage(result.message || '班级已删除', 'success');
        window.location.reload();
    } catch (error) {
        showMessage(error.message || '删除班级失败', 'error');
    }
}

function studentSearchText(student) {
    return normalize([
        student.name,
        student.display_name,
        student.nickname,
        student.student_id_number,
        student.email,
        student.phone,
    ].filter(Boolean).join(' '));
}

function renderStudentRows(classItem) {
    const students = Array.isArray(classItem?.students) ? classItem.students : [];
    if (!elements.drawerList) return;
    elements.drawerList.innerHTML = students.map((student) => {
        const name = student.name || student.display_name || '学生';
        const hasEmail = Boolean(student.has_email || normalize(student.email));
        return `
            <article class="class-student-row" data-student-row data-search-text="${escapeHtml(studentSearchText(student))}">
                <span class="class-student-row__avatar">${escapeHtml(name.slice(0, 1))}</span>
                <span class="class-student-row__main">
                    <strong>${escapeHtml(name)}</strong>
                    <small>${escapeHtml(student.student_id_number || '未填学号')}${student.email ? ` · ${escapeHtml(student.email)}` : ''}</small>
                </span>
                <span class="class-student-row__status${hasEmail ? '' : ' is-warning'}">${hasEmail ? '邮箱已填' : '缺邮箱'}</span>
                <a class="btn btn-outline btn-sm" href="/manage/students/${Number(student.id)}">详情</a>
            </article>
        `;
    }).join('');
    if (elements.drawerEmpty) {
        elements.drawerEmpty.hidden = students.length > 0;
    }
}

function filterStudentRows() {
    const query = normalize(elements.drawerSearch?.value);
    const rows = Array.from(elements.drawerList?.querySelectorAll('[data-student-row]') || []);
    let visibleCount = 0;
    rows.forEach((row) => {
        const visible = !query || normalize(row.dataset.searchText).includes(query);
        row.hidden = !visible;
        if (visible) visibleCount += 1;
    });
    if (elements.drawerEmpty) {
        elements.drawerEmpty.hidden = visibleCount > 0;
    }
}

function openStudentDrawer(classItem, trigger = null) {
    if (!elements.drawer || !classItem) return;
    activeDrawerClass = classItem;
    activeDrawerTrigger = trigger;
    if (elements.drawerTitle) elements.drawerTitle.textContent = classItem.name || '班级学生';
    if (elements.drawerKicker) elements.drawerKicker.textContent = classItem.department_label || classItem.department || '未分类';
    if (elements.drawerMeta) {
        const missing = Number(classItem.missing_email_count || 0);
        elements.drawerMeta.textContent = `${Number(classItem.student_count || 0)} 名学生 · ${missing ? `${missing} 人缺邮箱` : '邮箱覆盖完整'}`;
    }
    if (elements.drawerSearch) elements.drawerSearch.value = '';
    renderStudentRows(classItem);
    elements.drawer.hidden = false;
    elements.drawer.setAttribute('aria-hidden', 'false');
    document.body.classList.add('has-class-student-drawer');
    window.requestAnimationFrame(() => {
        elements.drawer.classList.add('is-open');
        elements.drawerPanel?.focus({ preventScroll: true });
    });
}

function closeStudentDrawer() {
    if (!elements.drawer) return;
    elements.drawer.classList.remove('is-open');
    elements.drawer.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('has-class-student-drawer');
    window.setTimeout(() => {
        if (!elements.drawer.classList.contains('is-open')) {
            elements.drawer.hidden = true;
            activeDrawerTrigger?.focus?.({ preventScroll: true });
            activeDrawerTrigger = null;
            activeDrawerClass = null;
        }
    }, 180);
}

function bindEvents() {
    elements.form?.addEventListener('submit', (event) => {
        window.handleFormSubmit(event);
    });

    elements.createButtons.forEach((button) => {
        button.addEventListener('click', focusCreateForm);
    });

    elements.templateButtons.forEach((button) => {
        button.addEventListener('click', downloadRosterTemplate);
    });

    [elements.searchInput, elements.departmentFilter, elements.healthFilter, elements.sortSelect].forEach((input) => {
        input?.addEventListener('input', applyFilters);
        input?.addEventListener('change', applyFilters);
    });
    elements.resetButton?.addEventListener('click', resetFilters);
    elements.departmentChips?.addEventListener('click', (event) => {
        const chip = event.target.closest('[data-department-chip]');
        if (!chip || !elements.departmentFilter) return;
        elements.departmentFilter.value = chip.dataset.departmentChip || 'all';
        applyFilters();
    });

    elements.classList?.addEventListener('click', (event) => {
        const actionButton = event.target.closest('[data-action]');
        if (!actionButton) return;
        const classId = Number(actionButton.dataset.classId || 0);
        const classItem = classMap.get(classId);
        if (actionButton.dataset.action === 'delete-class') {
            handleDelete(actionButton);
            return;
        }
        if (actionButton.dataset.action === 'open-students') {
            openStudentDrawer(classItem, actionButton);
            return;
        }
        if (actionButton.dataset.action === 'export-class') {
            exportClassRoster(classItem);
        }
    });

    elements.drawerClose?.addEventListener('click', closeStudentDrawer);
    elements.drawer?.addEventListener('click', (event) => {
        if (event.target === elements.drawer) closeStudentDrawer();
    });
    elements.drawerSearch?.addEventListener('input', filterStudentRows);
    elements.drawerExport?.addEventListener('click', () => exportClassRoster(activeDrawerClass));
    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && elements.drawer && !elements.drawer.hidden) {
            closeStudentDrawer();
        }
    });
}

bindEvents();
applyFilters();
