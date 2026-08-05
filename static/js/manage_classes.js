import { apiFetch } from '/static/js/api.js';
import { showMessage } from '/static/js/ui.js';
import { initAcademicSyncDialog } from '/static/js/academic_sync_dialog.js';

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
    customClassButtons: [
        document.getElementById('customClassCreateTopBtn'),
        document.getElementById('heroCustomClassCreateBtn'),
    ].filter(Boolean),
    templateButtons: [
        document.getElementById('classTemplateDownloadBtn'),
        document.getElementById('heroTemplateDownloadBtn'),
    ].filter(Boolean),
    academicSyncButtons: [
        document.getElementById('syncAcademicRosterBtn'),
        document.getElementById('syncAcademicRosterTopBtn'),
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
    drawerAdd: document.getElementById('classStudentAddBtn'),
    drawerExport: document.getElementById('classStudentExportBtn'),
    addModal: document.getElementById('classStudentAddModal'),
    addModalPanel: document.querySelector('.class-student-modal'),
    addModalClose: document.getElementById('classStudentAddClose'),
    addModalCancel: document.getElementById('classStudentAddCancel'),
    addForm: document.getElementById('classStudentAddForm'),
    addClassId: document.getElementById('classStudentAddClassId'),
    addTitle: document.getElementById('classStudentAddTitle'),
    addMeta: document.getElementById('classStudentAddMeta'),
    addName: document.getElementById('classStudentAddName'),
    customModal: document.getElementById('classCustomCreateModal'),
    customModalPanel: document.querySelector('.class-custom-modal'),
    customModalClose: document.getElementById('classCustomCreateClose'),
    customModalCancel: document.getElementById('classCustomCreateCancel'),
    customForm: document.getElementById('classCustomCreateForm'),
    customName: document.getElementById('classCustomNameInput'),
    customCreateAndAddStudent: document.getElementById('classCustomCreateAndAddStudent'),
};

let activeDrawerClass = null;
let activeDrawerTrigger = null;
let activeAddTrigger = null;
let activeCustomTrigger = null;
let reloadAfterAddModalClose = false;

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
            student.phone,
            student.academic_class_name,
            student.academic_college,
            student.academic_major,
            student.academic_school_status,
        ].filter(Boolean).join(' '))
        .join(' ');
    return normalize([
        classItem.name,
        classItem.department,
        classItem.department_label,
        classItem.school_name,
        classItem.college,
        classItem.organization_label,
        classItem.class_kind,
        classItem.class_kind_label,
        classItem.academic_class_name,
        classItem.academic_college,
        classItem.academic_major,
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
    const suspendedCount = numberValue(card.dataset.suspendedStudentCount);
    const academicCount = numberValue(card.dataset.academicSyncedStudentCount);
    const classKind = normalize(card.dataset.classKind);
    if (health === 'missing-email') return missingCount > 0;
    if (health === 'complete-email') return missingCount === 0;
    if (health === 'has-suspended') return suspendedCount > 0;
    if (health === 'custom-class') return classKind === 'custom';
    if (health === 'bound') return offeringCount > 0;
    if (health === 'unbound') return offeringCount === 0;
    if (health === 'academic-synced') return academicCount > 0;
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
        ['班级', '系别', '姓名', '学号', '状态', '昵称', '邮箱', '手机号', '教务班级', '学院', '年级', '专业', '学籍状态', '最近教务同步'],
        ...(classItem.students || []).map((student) => [
            classItem.name || '',
            classItem.department_label || classItem.department || '',
            student.name || '',
            student.student_id_number || '',
            student.enrollment_status_label || (student.enrollment_status === 'suspended' ? '休学' : '在读'),
            student.nickname || '',
            student.email || '',
            student.phone || '',
            student.academic_class_name || '',
            student.academic_college || '',
            student.academic_grade || '',
            student.academic_major || '',
            student.academic_school_status || '',
            student.academic_sync_at || '',
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
        student.enrollment_status_label,
        student.enrollment_status === 'suspended' ? '休学' : '在读',
        student.email,
        student.phone,
        student.academic_student_id,
        student.academic_class_name,
        student.academic_college,
        student.academic_grade,
        student.academic_major,
        student.academic_school_status,
    ].filter(Boolean).join(' '));
}

function studentStatusLabel(student) {
    return student.enrollment_status_label || (student.enrollment_status === 'suspended' ? '休学' : '在读');
}

function renderStudentRows(classItem) {
    const students = Array.isArray(classItem?.students) ? classItem.students : [];
    if (!elements.drawerList) return;
    elements.drawerList.innerHTML = students.map((student) => {
        const name = student.name || student.display_name || '学生';
        const hasEmail = Boolean(student.has_email || normalize(student.email));
        const isSuspended = student.enrollment_status === 'suspended';
        const isAcademicSynced = Boolean(student.is_academic_synced || student.academic_sync_at || student.academic_source === 'gxufl_jwxt');
        const statusText = studentStatusLabel(student);
        const statusActionText = isSuspended ? '恢复在读' : '设为休学';
        const nextStatus = isSuspended ? 'active' : 'suspended';
        const metaParts = [
            student.student_id_number || '未填学号',
            student.email || '',
            student.phone || '',
            student.academic_class_name || '',
            student.academic_major || '',
        ].filter(Boolean);
        return `
            <article class="class-student-row" data-student-row data-search-text="${escapeHtml(studentSearchText(student))}">
                <span class="class-student-row__avatar${isSuspended ? ' is-muted' : ''}">${escapeHtml(name.slice(0, 1))}</span>
                <span class="class-student-row__main">
                    <strong>${escapeHtml(name)}</strong>
                    <small>${escapeHtml(metaParts.join(' · '))}</small>
                </span>
                <span class="class-student-row__badges">
                    <span class="class-student-row__status${isSuspended ? ' is-muted' : ''}">${escapeHtml(statusText)}</span>
                    <span class="class-student-row__status${hasEmail ? '' : ' is-warning'}">${hasEmail ? '邮箱已填' : '缺邮箱'}</span>
                    ${isAcademicSynced ? '<span class="class-student-row__status is-academic">教务同步</span>' : ''}
                    ${student.academic_school_status ? `<span class="class-student-row__status is-muted">${escapeHtml(student.academic_school_status)}</span>` : ''}
                </span>
                <span class="class-student-row__actions">
                    <a class="btn btn-outline btn-sm" href="/manage/students/${Number(student.id)}">详情</a>
                    <button type="button" class="btn btn-outline btn-sm" data-student-action="status" data-student-id="${Number(student.id)}" data-next-status="${nextStatus}" data-student-name="${escapeHtml(name)}">${statusActionText}</button>
                    <button type="button" class="btn btn-ghost btn-sm text-danger" data-student-action="delete" data-student-id="${Number(student.id)}" data-student-name="${escapeHtml(name)}">删除</button>
                </span>
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
        const suspended = Number(classItem.suspended_student_count || 0);
        elements.drawerMeta.textContent = `${Number(classItem.student_count || 0)} 名在读学生 · ${suspended ? `${suspended} 人休学 · ` : ''}${missing ? `${missing} 人缺邮箱` : '邮箱覆盖完整'}`;
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

function openAddStudentModal(classItem, trigger = null, options = {}) {
    if (!elements.addModal || !classItem) return;
    activeAddTrigger = trigger;
    reloadAfterAddModalClose = Boolean(options.reloadOnClose);
    if (elements.addClassId) elements.addClassId.value = String(classItem.id || '');
    if (elements.addTitle) elements.addTitle.textContent = `加入 ${classItem.name || '班级'}`;
    if (elements.addMeta) {
        const department = classItem.department_label || classItem.department || '未分类';
        elements.addMeta.textContent = `${department} · 当前 ${Number(classItem.student_count || 0)} 名在读学生`;
    }
    elements.addForm?.reset();
    if (elements.addClassId) elements.addClassId.value = String(classItem.id || '');
    elements.addModal.hidden = false;
    elements.addModal.setAttribute('aria-hidden', 'false');
    document.body.classList.add('has-class-student-modal');
    window.requestAnimationFrame(() => {
        elements.addModal.classList.add('is-open');
        elements.addName?.focus({ preventScroll: true });
    });
}

function closeAddStudentModal() {
    if (!elements.addModal) return;
    const shouldReload = reloadAfterAddModalClose;
    reloadAfterAddModalClose = false;
    elements.addModal.classList.remove('is-open');
    elements.addModal.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('has-class-student-modal');
    window.setTimeout(() => {
        if (!elements.addModal.classList.contains('is-open')) {
            elements.addModal.hidden = true;
            if (shouldReload) {
                window.location.reload();
                return;
            }
            activeAddTrigger?.focus?.({ preventScroll: true });
            activeAddTrigger = null;
        }
    }, 160);
}

function openCustomClassModal(trigger = null) {
    if (!elements.customModal) return;
    activeCustomTrigger = trigger;
    elements.customForm?.reset();
    if (elements.customCreateAndAddStudent) {
        elements.customCreateAndAddStudent.checked = true;
    }
    elements.customModal.hidden = false;
    elements.customModal.setAttribute('aria-hidden', 'false');
    document.body.classList.add('has-class-student-modal');
    window.requestAnimationFrame(() => {
        elements.customModal.classList.add('is-open');
        elements.customName?.focus({ preventScroll: true });
    });
}

function closeCustomClassModal() {
    if (!elements.customModal) return;
    elements.customModal.classList.remove('is-open');
    elements.customModal.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('has-class-student-modal');
    window.setTimeout(() => {
        if (!elements.customModal.classList.contains('is-open')) {
            elements.customModal.hidden = true;
            activeCustomTrigger?.focus?.({ preventScroll: true });
            activeCustomTrigger = null;
        }
    }, 160);
}

function normalizeCreatedClass(classItem) {
    const item = classItem || {};
    return {
        id: Number(item.id || 0),
        name: item.name || '自定义班级',
        department: item.department || '',
        department_label: item.department_label || item.department || '未分类',
        school_name: item.school_name || '',
        college: item.college || '',
        major: item.major || '',
        organization_label: [item.school_name, item.college].filter(Boolean).join(' / '),
        class_kind: item.class_kind || 'custom',
        class_kind_label: item.class_kind_label || '自定义班级',
        is_custom_class: true,
        student_count: Number(item.student_count || 0),
        suspended_student_count: Number(item.suspended_student_count || 0),
        missing_email_count: Number(item.missing_email_count || 0),
        academic_synced_student_count: Number(item.academic_synced_student_count || 0),
        offering_count: Number(item.offering_count || 0),
        students: Array.isArray(item.students) ? item.students : [],
        active_students: Array.isArray(item.active_students) ? item.active_students : [],
        can_manage: true,
        can_view_content: true,
    };
}

async function submitCustomClass(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const submitButton = form.querySelector('button[type="submit"]');
    const originalText = submitButton?.innerHTML;
    if (submitButton) {
        submitButton.disabled = true;
        submitButton.innerHTML = '<span class="spinner" style="width:14px;height:14px;border-width:2px;"></span> 创建中...';
    }
    try {
        const formData = new FormData(form);
        const result = await apiFetch('/api/manage/classes/custom', {
            method: 'POST',
            body: formData,
            silent: true,
        });
        const createdClass = normalizeCreatedClass(result.class);
        showMessage(result.message || '自定义班级已创建', 'success');
        const shouldAddStudent = Boolean(elements.customCreateAndAddStudent?.checked);
        closeCustomClassModal();
        if (shouldAddStudent && createdClass.id) {
            window.setTimeout(() => openAddStudentModal(createdClass, null, { reloadOnClose: true }), 180);
        } else {
            window.setTimeout(() => window.location.reload(), 650);
        }
    } catch (error) {
        showMessage(error.message || '创建自定义班级失败', 'error');
    } finally {
        if (submitButton) {
            submitButton.disabled = false;
            submitButton.innerHTML = originalText;
        }
    }
}

async function submitAddStudent(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const classId = Number(elements.addClassId?.value || 0);
    if (!classId) {
        showMessage('未找到目标班级', 'error');
        return;
    }
    const submitButton = form.querySelector('button[type="submit"]');
    const originalText = submitButton?.innerHTML;
    if (submitButton) {
        submitButton.disabled = true;
        submitButton.innerHTML = '<span class="spinner" style="width:14px;height:14px;border-width:2px;"></span> 提交中...';
    }
    try {
        const formData = new FormData(form);
        formData.delete('class_id');
        const result = await apiFetch(`/api/manage/classes/${classId}/students`, {
            method: 'POST',
            body: formData,
            silent: true,
        });
        showMessage(result.message || '学生已加入班级', 'success');
        reloadAfterAddModalClose = false;
        window.setTimeout(() => window.location.reload(), 650);
    } catch (error) {
        showMessage(error.message || '新增学生失败', 'error');
    } finally {
        if (submitButton) {
            submitButton.disabled = false;
            submitButton.innerHTML = originalText;
        }
    }
}

async function updateStudentStatus(button) {
    const studentId = Number(button.dataset.studentId || 0);
    const nextStatus = button.dataset.nextStatus || 'active';
    const studentName = button.dataset.studentName || '该学生';
    if (!studentId) return;
    const confirmed = window.confirm(
        nextStatus === 'suspended'
            ? `确定将“${studentName}”设置为休学吗？\n休学后会保留数据，但不再纳入课堂任务、统计和通知范围。`
            : `确定将“${studentName}”恢复为在读吗？\n恢复后会重新纳入课堂任务、统计和通知范围。`
    );
    if (!confirmed) return;
    const formData = new FormData();
    formData.set('enrollment_status', nextStatus);
    try {
        const result = await apiFetch(`/api/manage/students/${studentId}/status`, {
            method: 'POST',
            body: formData,
            silent: true,
        });
        showMessage(result.message || '学生状态已更新', 'success');
        window.setTimeout(() => window.location.reload(), 650);
    } catch (error) {
        showMessage(error.message || '更新学生状态失败', 'error');
    }
}

async function deleteStudent(button) {
    const studentId = Number(button.dataset.studentId || 0);
    const studentName = button.dataset.studentName || '该学生';
    if (!studentId) return;
    const confirmed = window.confirm(
        `确定删除“${studentName}”吗？\n这会移除该学生账号及其关联课堂数据；如果只是暂时不参与学习，请改用休学。`
    );
    if (!confirmed) return;
    try {
        const result = await apiFetch(`/api/manage/students/${studentId}`, {
            method: 'DELETE',
            silent: true,
        });
        showMessage(result.message || '学生已删除', 'success');
        window.setTimeout(() => window.location.reload(), 650);
    } catch (error) {
        showMessage(error.message || '删除学生失败', 'error');
    }
}

function bindEvents() {
    elements.form?.addEventListener('submit', (event) => {
        window.handleFormSubmit(event);
    });

    elements.createButtons.forEach((button) => {
        button.addEventListener('click', focusCreateForm);
    });

    elements.customClassButtons.forEach((button) => {
        button.addEventListener('click', () => openCustomClassModal(button));
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
        if (actionButton.dataset.action === 'add-student') {
            openAddStudentModal(classItem, actionButton);
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
    elements.drawerAdd?.addEventListener('click', () => openAddStudentModal(activeDrawerClass, elements.drawerAdd));
    elements.drawerExport?.addEventListener('click', () => exportClassRoster(activeDrawerClass));
    elements.drawerList?.addEventListener('click', (event) => {
        const button = event.target.closest('[data-student-action]');
        if (!button) return;
        if (button.dataset.studentAction === 'status') {
            updateStudentStatus(button);
            return;
        }
        if (button.dataset.studentAction === 'delete') {
            deleteStudent(button);
        }
    });
    elements.addForm?.addEventListener('submit', submitAddStudent);
    elements.addModalClose?.addEventListener('click', closeAddStudentModal);
    elements.addModalCancel?.addEventListener('click', closeAddStudentModal);
    elements.addModal?.addEventListener('click', (event) => {
        if (event.target === elements.addModal) closeAddStudentModal();
    });
    elements.customForm?.addEventListener('submit', submitCustomClass);
    elements.customModalClose?.addEventListener('click', closeCustomClassModal);
    elements.customModalCancel?.addEventListener('click', closeCustomClassModal);
    elements.customModal?.addEventListener('click', (event) => {
        if (event.target === elements.customModal) closeCustomClassModal();
    });
    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && elements.addModal && !elements.addModal.hidden) {
            closeAddStudentModal();
            return;
        }
        if (event.key === 'Escape' && elements.customModal && !elements.customModal.hidden) {
            closeCustomClassModal();
            return;
        }
        if (event.key === 'Escape' && elements.drawer && !elements.drawer.hidden) {
            closeStudentDrawer();
        }
    });
}

bindEvents();
initAcademicSyncDialog({
    buttons: elements.academicSyncButtons,
    endpoint: '/api/manage/classes/sync-current-academic',
    semesters: data.academicSyncSemesters,
    defaultSemesterId: data.academicSyncDefaultSemesterId,
});
applyFilters();
