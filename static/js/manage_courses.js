import { apiFetch } from '/static/js/api.js';
import { closeModal, openModal, showMessage } from '/static/js/ui.js';

const pageData = window.COURSE_PAGE_DATA || {};
const courses = Array.isArray(pageData.courses) ? pageData.courses : [];
const courseMap = new Map(courses.map((item) => [Number(item.id), item]));

const elements = {
    openButtons: [
        document.getElementById('openCourseCreateBtn'),
        document.getElementById('heroCourseCreateBtn'),
        document.getElementById('toolbarCourseCreateBtn'),
    ].filter(Boolean),
    modal: document.getElementById('courseModal'),
    modalTitle: document.getElementById('courseModalTitle'),
    courseIdInput: document.getElementById('courseIdInput'),
    nameInput: document.getElementById('courseNameInput'),
    creditsInput: document.getElementById('courseCreditsInput'),
    descriptionInput: document.getElementById('courseDescriptionInput'),
    totalHoursInput: document.getElementById('courseTotalHoursInput'),
    aiTextbookSelect: document.getElementById('courseAiTextbookSelect'),
    aiSectionCountInput: document.getElementById('courseAiSectionCountInput'),
    aiSessionCount: document.getElementById('courseAiSessionCount'),
    aiMetaHint: document.getElementById('courseAiMetaHint'),
    aiGenerateBtn: document.getElementById('courseAiGenerateBtn'),
    addLessonBtn: document.getElementById('courseAddLessonBtn'),
    saveBtn: document.getElementById('courseSaveBtn'),
    lessonsContainer: document.getElementById('courseLessonsContainer'),
    lessonCounter: document.getElementById('courseLessonCounter'),
    lessonTemplate: document.getElementById('courseLessonRowTemplate'),
    searchInput: document.getElementById('courseSearchInput'),
    filterSelect: document.getElementById('courseFilterSelect'),
    courseCardGrid: document.getElementById('courseCardGrid'),
    resultSummary: document.getElementById('courseResultSummary'),
};

function getCourseCards() {
    return Array.from(document.querySelectorAll('.course-card[data-course-id]'));
}

function createLessonRow(data = {}) {
    if (!elements.lessonTemplate || !elements.lessonsContainer) {
        return null;
    }
    const fragment = elements.lessonTemplate.content.cloneNode(true);
    const row = fragment.querySelector('[data-lesson-row]');
    if (!row) {
        return null;
    }

    row.querySelector('[data-field="title"]').value = data.title || '';
    row.querySelector('[data-field="content"]').value = data.content || '';
    row.querySelector('[data-field="section_count"]').value = String(data.section_count || 2);
    elements.lessonsContainer.appendChild(fragment);
    return row;
}

function renumberLessonRows() {
    const rows = Array.from(elements.lessonsContainer?.querySelectorAll('[data-lesson-row]') || []);
    rows.forEach((row, index) => {
        const label = index + 1;
        const indexNode = row.querySelector('[data-lesson-index]');
        const headingNode = row.querySelector('[data-lesson-heading]');
        if (indexNode) indexNode.textContent = String(label);
        if (headingNode) headingNode.textContent = `第 ${label} 次课`;
    });
    if (elements.lessonCounter) {
        elements.lessonCounter.textContent = `${rows.length} 条课堂设置`;
    }
}

function ensureOneLessonRow() {
    const rows = elements.lessonsContainer?.querySelectorAll('[data-lesson-row]') || [];
    if (!rows.length) {
        createLessonRow();
    }
    renumberLessonRows();
}

function collectLessons() {
    return Array.from(elements.lessonsContainer?.querySelectorAll('[data-lesson-row]') || []).map((row) => ({
        title: row.querySelector('[data-field="title"]')?.value || '',
        content: row.querySelector('[data-field="content"]')?.value || '',
        section_count: Number(row.querySelector('[data-field="section_count"]')?.value || 0),
    }));
}

function resetForm() {
    if (elements.modalTitle) elements.modalTitle.textContent = '新增课程';
    if (elements.courseIdInput) elements.courseIdInput.value = '';
    if (elements.nameInput) elements.nameInput.value = '';
    if (elements.creditsInput) elements.creditsInput.value = '0';
    if (elements.descriptionInput) elements.descriptionInput.value = '';
    if (elements.totalHoursInput) elements.totalHoursInput.value = '0';
    if (elements.aiTextbookSelect) elements.aiTextbookSelect.value = '';
    if (elements.aiSectionCountInput) elements.aiSectionCountInput.value = '2';
    if (elements.lessonsContainer) elements.lessonsContainer.innerHTML = '';
    ensureOneLessonRow();
    updateAiMeta();
}

function populateForm(course) {
    if (!course) {
        resetForm();
        return;
    }
    if (elements.modalTitle) elements.modalTitle.textContent = '编辑课程';
    if (elements.courseIdInput) elements.courseIdInput.value = String(course.id || '');
    if (elements.nameInput) elements.nameInput.value = course.name || '';
    if (elements.creditsInput) elements.creditsInput.value = String(course.credits ?? 0);
    if (elements.descriptionInput) elements.descriptionInput.value = course.description || '';
    if (elements.totalHoursInput) elements.totalHoursInput.value = String(course.total_hours || 0);
    if (elements.lessonsContainer) elements.lessonsContainer.innerHTML = '';

    if (Array.isArray(course.lessons) && course.lessons.length) {
        course.lessons.forEach((lesson) => createLessonRow(lesson));
    } else {
        createLessonRow();
    }
    renumberLessonRows();
    updateAiMeta();
}

function updateAiMeta() {
    const totalHours = Number(elements.totalHoursInput?.value || 0);
    const sectionCount = Number(elements.aiSectionCountInput?.value || 0);
    let sessionCount = 0;
    let hint = '请先填写课程总学时，并保证能被每次课小节数整除。';

    if (totalHours > 0 && sectionCount > 0) {
        if (totalHours % sectionCount === 0) {
            sessionCount = totalHours / sectionCount;
            hint = `将按 ${sessionCount} 次课生成课堂标题和上课内容，生成后仍可继续修改。`;
        } else {
            hint = '当前学时不能被每次课小节数整除，请调整后再生成。';
        }
    }

    if (elements.aiSessionCount) elements.aiSessionCount.textContent = String(sessionCount);
    if (elements.aiMetaHint) elements.aiMetaHint.textContent = hint;
}

function updateVisibleSummary() {
    if (!elements.resultSummary) return;
    const cards = getCourseCards();
    const visibleCards = cards.filter((card) => card.style.display !== 'none');
    const total = cards.length;
    const visibleCount = visibleCards.length;
    const keyword = String(elements.searchInput?.value || '').trim();
    const filterValue = elements.filterSelect?.value || 'all';

    const filterLabelMap = {
        all: '全部课程',
        active: '已开设课堂',
        complete: '结构完整',
        pending: '待完善',
        idle: '未开课',
    };

    elements.resultSummary.innerHTML = `
        <span>当前展示 <strong>${visibleCount}</strong> / ${total} 门课程。</span>
        <span>筛选条件：${filterLabelMap[filterValue] || '全部课程'}${keyword ? `，关键词“${keyword}”` : ''}</span>
    `;
}

function renderFilterEmptyState() {
    if (!elements.courseCardGrid) return;
    let emptyState = document.getElementById('courseFilterEmptyState');
    if (!getCourseCards().length && document.getElementById('courseEmptyState')) {
        emptyState?.remove();
        return;
    }
    const hasVisibleCard = getCourseCards().some((card) => card.style.display !== 'none');

    if (hasVisibleCard) {
        emptyState?.remove();
        return;
    }

    if (!emptyState) {
        emptyState = document.createElement('div');
        emptyState.id = 'courseFilterEmptyState';
        emptyState.className = 'academic-empty';
        elements.courseCardGrid.appendChild(emptyState);
    }

    emptyState.innerHTML = `
        <strong>没有匹配的课程</strong>
        试试调整关键词或筛选条件，或者直接新增一门新的课程模板。
    `;
}

function applyFilters() {
    const keyword = String(elements.searchInput?.value || '').trim().toLowerCase();
    const filterValue = elements.filterSelect?.value || 'all';

    getCourseCards().forEach((card) => {
        const searchText = String(card.dataset.search || '');
        const filterState = String(card.dataset.filterState || 'idle');
        const coverageState = String(card.dataset.coverageState || 'empty');

        let matchesFilter = true;
        if (filterValue === 'active') matchesFilter = filterState === 'active';
        if (filterValue === 'idle') matchesFilter = filterState === 'idle';
        if (filterValue === 'complete') matchesFilter = coverageState === 'complete';
        if (filterValue === 'pending') matchesFilter = coverageState !== 'complete';

        const matchesKeyword = !keyword || searchText.includes(keyword);
        card.style.display = matchesFilter && matchesKeyword ? '' : 'none';
    });

    renderFilterEmptyState();
    updateVisibleSummary();
}

function openCreateModal() {
    resetForm();
    openModal('courseModal');
}

function openEditModal(courseId) {
    const course = courseMap.get(Number(courseId));
    if (!course) {
        showMessage('未找到对应课程信息', 'warning');
        return;
    }
    populateForm(course);
    openModal('courseModal');
}

async function handleSaveCourse() {
    if (!elements.saveBtn) return;

    const payload = {
        course_id: elements.courseIdInput?.value || '',
        name: elements.nameInput?.value || '',
        description: elements.descriptionInput?.value || '',
        credits: Number(elements.creditsInput?.value || 0),
        total_hours: Number(elements.totalHoursInput?.value || 0),
        lessons: collectLessons(),
    };

    const originalText = elements.saveBtn.textContent;
    elements.saveBtn.disabled = true;
    elements.saveBtn.textContent = '保存中...';

    try {
        const result = await apiFetch('/api/manage/courses/save', {
            method: 'POST',
            body: payload,
            silent: true,
        });
        showMessage(result.message || '课程已保存', 'success');
        closeModal('courseModal');
        window.location.reload();
    } catch (error) {
        showMessage(error.message || '保存课程失败', 'error');
    } finally {
        elements.saveBtn.disabled = false;
        elements.saveBtn.textContent = originalText;
    }
}

async function handleDeleteCourse(button) {
    const courseId = Number(button.dataset.courseId || 0);
    const courseName = button.dataset.courseName || '当前课程';
    if (!courseId) return;

    const confirmed = window.confirm(`确定删除课程“${courseName}”吗？\n该操作会影响与课程相关的课堂绑定和课程资源。`);
    if (!confirmed) return;

    try {
        const result = await apiFetch(`/api/manage/courses/${courseId}`, { method: 'DELETE', silent: true });
        showMessage(result.message || '课程已删除', 'success');
        window.location.reload();
    } catch (error) {
        showMessage(error.message || '删除课程失败', 'error');
    }
}

async function handleAiGenerateLessons() {
    if (!elements.aiGenerateBtn) return;

    const payload = {
        name: elements.nameInput?.value || '',
        description: elements.descriptionInput?.value || '',
        textbook_id: elements.aiTextbookSelect?.value || '',
        total_hours: Number(elements.totalHoursInput?.value || 0),
        per_session_sections: Number(elements.aiSectionCountInput?.value || 0),
    };

    const originalText = elements.aiGenerateBtn.textContent;
    elements.aiGenerateBtn.disabled = true;
    elements.aiGenerateBtn.textContent = 'AI 生成中...';

    try {
        const result = await apiFetch('/api/manage/courses/ai-generate-lessons', {
            method: 'POST',
            body: payload,
            silent: true,
        });
        if (elements.lessonsContainer) {
            elements.lessonsContainer.innerHTML = '';
        }
        (result.lessons || []).forEach((lesson) => createLessonRow(lesson));
        ensureOneLessonRow();
        showMessage(result.message || 'AI 已生成课堂设置', 'success');
    } catch (error) {
        showMessage(error.message || 'AI 生成失败', 'error');
    } finally {
        elements.aiGenerateBtn.disabled = false;
        elements.aiGenerateBtn.textContent = originalText;
    }
}

function bindEvents() {
    elements.openButtons.forEach((button) => button.addEventListener('click', openCreateModal));
    elements.addLessonBtn?.addEventListener('click', () => {
        createLessonRow();
        renumberLessonRows();
    });
    elements.saveBtn?.addEventListener('click', handleSaveCourse);
    elements.aiGenerateBtn?.addEventListener('click', handleAiGenerateLessons);
    elements.searchInput?.addEventListener('input', applyFilters);
    elements.filterSelect?.addEventListener('change', applyFilters);
    elements.totalHoursInput?.addEventListener('input', updateAiMeta);
    elements.aiSectionCountInput?.addEventListener('input', updateAiMeta);

    elements.lessonsContainer?.addEventListener('click', (event) => {
        const button = event.target.closest('[data-action="remove-lesson"]');
        if (!button) return;
        const row = button.closest('[data-lesson-row]');
        row?.remove();
        ensureOneLessonRow();
    });

    elements.courseCardGrid?.addEventListener('click', (event) => {
        const editButton = event.target.closest('[data-action="edit-course"]');
        if (editButton) {
            openEditModal(editButton.dataset.courseId);
            return;
        }

        const deleteButton = event.target.closest('[data-action="delete-course"]');
        if (deleteButton) {
            handleDeleteCourse(deleteButton);
        }
    });
}

bindEvents();
ensureOneLessonRow();
updateAiMeta();
applyFilters();
