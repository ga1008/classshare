import { apiFetch } from '/static/js/api.js';
import { closeModal, openModal, showMessage } from '/static/js/ui.js';
import { initLearningMaterialSelector } from '/static/js/learning_material_selector.js';

const pageData = window.COURSE_PAGE_DATA || {};
const courses = Array.isArray(pageData.courses) ? pageData.courses : [];
const courseMap = new Map(courses.map((item) => [Number(item.id), item]));
const learningMaterialSelector = initLearningMaterialSelector();

const elements = {
    openButtons: [
        document.getElementById('openCourseCreateBtn'),
        document.getElementById('heroCourseCreateBtn'),
        document.getElementById('toolbarCourseCreateBtn'),
    ].filter(Boolean),
    academicSyncButtons: [
        document.getElementById('heroCourseAcademicSyncBtn'),
        document.getElementById('toolbarCourseAcademicSyncBtn'),
        document.getElementById('courseModalAcademicSyncBtn'),
    ].filter(Boolean),
    modal: document.getElementById('courseModal'),
    modalTitle: document.getElementById('courseModalTitle'),
    courseIdInput: document.getElementById('courseIdInput'),
    nameInput: document.getElementById('courseNameInput'),
    departmentInput: document.getElementById('courseDepartmentInput'),
    sectNameInput: document.getElementById('courseSectNameInput'),
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
    groupModeButtons: Array.from(document.querySelectorAll('[data-course-group-mode]')),
    groupModeLabel: document.getElementById('courseGroupModeLabel'),
    courseCardGrid: document.getElementById('courseCardGrid'),
    resultSummary: document.getElementById('courseResultSummary'),
};

let currentGroupMode = 'none';
let courseGroupSectionSerial = 0;
let courseGroupResizeFrame = 0;
const storedCollapsedCourseGroups = readJsonStorage('manage:courses:collapsed-groups', []);
const collapsedCourseGroups = new Set(Array.isArray(storedCollapsedCourseGroups) ? storedCollapsedCourseGroups : []);

const groupModeConfig = {
    none: {
        label: '默认网格',
    },
    department: {
        label: '按系别分类',
        eyebrow: '系别',
        keyAttr: 'departmentGroup',
        labelAttr: 'departmentLabel',
        metaAttr: 'departmentMeta',
        orderAttr: 'departmentOrder',
        fallbackKey: 'department:__unset__',
        fallbackLabel: '未指定系别',
    },
    semester: {
        label: '按创建学期分类',
        eyebrow: '创建学期',
        keyAttr: 'semesterGroup',
        labelAttr: 'semesterLabel',
        metaAttr: 'semesterMeta',
        orderAttr: 'semesterOrder',
        fallbackKey: 'semester:__unknown__',
        fallbackLabel: '创建时间未知',
    },
};

function getCourseCards() {
    return Array.from(document.querySelectorAll('.course-card[data-course-id]'));
}

function isCardActionTarget(target) {
    return Boolean(target?.closest?.('a, button, input, select, textarea, label, [data-action]'));
}

function readJsonStorage(key, fallback) {
    try {
        const rawValue = window.localStorage?.getItem(key);
        if (!rawValue) return fallback;
        const parsed = JSON.parse(rawValue);
        return parsed ?? fallback;
    } catch (error) {
        return fallback;
    }
}

function writeJsonStorage(key, value) {
    try {
        window.localStorage?.setItem(key, JSON.stringify(value));
    } catch (error) {
        // Storage is a convenience; the page should still work in private modes.
    }
}

function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, (char) => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;',
    }[char]));
}

function normalizeLearningMaterial(data = {}) {
    const materialId = Number(
        data.learning_material?.id
        || data.learning_material_id
        || 0,
    );
    if (!materialId) {
        return null;
    }
    return {
        id: materialId,
        parent_id: data.learning_material?.parent_id ?? data.learning_material_parent_id ?? null,
        name: data.learning_material?.name || data.learning_material_name || '',
        material_path: data.learning_material?.material_path || data.learning_material_path || '',
        preview_type: 'markdown',
        node_type: 'file',
        viewer_url: data.learning_material?.viewer_url || data.learning_material_viewer_url || `/materials/view/${materialId}`,
    };
}

function setLessonMaterial(row, material) {
    if (!row) return;
    const normalized = normalizeLearningMaterial(material || {});
    const materialBox = row.querySelector('[data-role="lesson-material"]');
    const materialInput = row.querySelector('[data-field="learning_material_id"]');
    const summary = row.querySelector('[data-role="material-summary"]');
    const path = row.querySelector('[data-role="material-path"]');
    const previewBtn = row.querySelector('[data-action="preview-material"]');
    const clearBtn = row.querySelector('[data-action="clear-material"]');

    if (!normalized) {
        if (materialInput) materialInput.value = '';
        if (summary) summary.textContent = '未绑定课堂文档';
        if (path) path.textContent = '可选择课程材料库中的 Markdown 文档，后续开设课堂会自动继承。';
        if (previewBtn) previewBtn.disabled = true;
        if (clearBtn) clearBtn.disabled = true;
        materialBox?.classList.add('is-empty');
        row.dataset.learningMaterialViewerUrl = '';
        return;
    }

    if (materialInput) materialInput.value = String(normalized.id);
    if (summary) summary.textContent = normalized.name || '已绑定课堂文档';
    if (path) path.textContent = normalized.material_path || '';
    if (previewBtn) previewBtn.disabled = false;
    if (clearBtn) clearBtn.disabled = false;
    materialBox?.classList.remove('is-empty');
    row.dataset.learningMaterialViewerUrl = normalized.viewer_url || '';
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
    setLessonMaterial(row, data);
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
        learning_material_id: Number(row.querySelector('[data-field="learning_material_id"]')?.value || 0) || null,
    }));
}

function resetForm() {
    if (elements.modalTitle) elements.modalTitle.textContent = '新增课程';
    if (elements.courseIdInput) elements.courseIdInput.value = '';
    if (elements.nameInput) elements.nameInput.value = '';
    if (elements.departmentInput) elements.departmentInput.value = '';
    if (elements.sectNameInput) elements.sectNameInput.value = '';
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
    if (elements.departmentInput) elements.departmentInput.value = course.department || '';
    if (elements.sectNameInput) elements.sectNameInput.value = course.sect_name || '';
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
    const groupLabel = groupModeConfig[currentGroupMode]?.label || groupModeConfig.none.label;

    elements.resultSummary.innerHTML = `
        <span>当前展示 <strong>${visibleCount}</strong> / ${total} 门课程。</span>
        <span>筛选条件：${filterLabelMap[filterValue] || '全部课程'}${keyword ? `，关键词“${escapeHtml(keyword)}”` : ''} · ${escapeHtml(groupLabel)}</span>
    `;
}

function resetCourseGrid(cards) {
    if (!elements.courseCardGrid) return;
    const sections = Array.from(elements.courseCardGrid.querySelectorAll('.course-group-section'));
    cards.forEach((card) => elements.courseCardGrid.appendChild(card));
    sections.forEach((section) => section.remove());
    elements.courseCardGrid.classList.toggle('is-grouped', currentGroupMode !== 'none');
}

function renderCourseGroups() {
    if (!elements.courseCardGrid) return;
    const cards = getCourseCards();
    document.getElementById('courseFilterEmptyState')?.remove();
    if (!cards.length) {
        elements.courseCardGrid.classList.remove('is-grouped');
        return;
    }

    const config = groupModeConfig[currentGroupMode] || groupModeConfig.none;
    resetCourseGrid(cards);

    if (currentGroupMode === 'none') {
        return;
    }

    const groups = new Map();
    cards.forEach((card) => {
        const key = card.dataset[config.keyAttr] || config.fallbackKey;
        const label = card.dataset[config.labelAttr] || config.fallbackLabel;
        const meta = card.dataset[config.metaAttr] || '';
        const order = Number(card.dataset[config.orderAttr] || 0);
        if (!groups.has(key)) {
            groups.set(key, {
                key,
                label,
                meta,
                order,
                cards: [],
                visibleCount: 0,
            });
        }
        const group = groups.get(key);
        group.cards.push(card);
        if (card.style.display !== 'none') {
            group.visibleCount += 1;
        }
    });

    Array.from(groups.values())
        .sort((left, right) => (
            left.order - right.order
            || left.label.localeCompare(right.label, 'zh-Hans-CN')
        ))
        .forEach((group) => {
            const collapseKey = `${currentGroupMode}:${group.key}`;
            const isCollapsed = collapsedCourseGroups.has(collapseKey);
            const bodyId = `course-group-body-${++courseGroupSectionSerial}`;
            const headingId = `course-group-heading-${courseGroupSectionSerial}`;
            const section = document.createElement('section');
            section.className = `course-group-section${isCollapsed ? ' is-collapsed' : ''}`;
            section.dataset.courseGroupKey = group.key;
            section.hidden = group.visibleCount === 0;
            section.innerHTML = `
                <button type="button"
                        class="course-group-header"
                        data-action="toggle-course-group"
                        data-course-group-collapse-key="${escapeHtml(collapseKey)}"
                        aria-expanded="${isCollapsed ? 'false' : 'true'}"
                        aria-controls="${bodyId}"
                        aria-labelledby="${headingId}">
                    <div class="course-group-heading">
                        <span>${escapeHtml(config.eyebrow)}</span>
                        <h4 id="${headingId}">${escapeHtml(group.label)}</h4>
                        ${group.meta ? `<p>${escapeHtml(group.meta)}</p>` : ''}
                    </div>
                    <span class="course-group-header-actions">
                        <strong class="course-group-count">${group.visibleCount} 门课程</strong>
                        <span class="course-group-toggle" aria-hidden="true">
                            <span class="course-group-toggle-icon"></span>
                        </span>
                    </span>
                </button>
                <div class="course-group-body" id="${bodyId}" aria-hidden="${isCollapsed ? 'true' : 'false'}">
                    <div class="course-group-body-inner">
                        <div class="course-group-card-grid"></div>
                    </div>
                </div>
            `;
            const groupGrid = section.querySelector('.course-group-card-grid');
            const bodyInner = section.querySelector('.course-group-body-inner');
            if (bodyInner && 'inert' in bodyInner) {
                bodyInner.inert = isCollapsed;
            }
            group.cards.forEach((card) => groupGrid.appendChild(card));
            elements.courseCardGrid.appendChild(section);
            refreshCourseGroupHeight(section);
        });
}

function refreshCourseGroupHeight(section) {
    const body = section?.querySelector('.course-group-body');
    if (!body) return;
    body.style.maxHeight = section.classList.contains('is-collapsed') ? '0px' : `${body.scrollHeight}px`;
}

function refreshCourseGroupHeights() {
    elements.courseCardGrid?.querySelectorAll('.course-group-section').forEach((section) => {
        refreshCourseGroupHeight(section);
    });
}

function setCourseGroupCollapsed(section, header, nextCollapsed, { persist = true } = {}) {
    const body = section?.querySelector('.course-group-body');
    const bodyInner = section?.querySelector('.course-group-body-inner');
    const collapseKey = header?.dataset.courseGroupCollapseKey || '';
    if (!section || !header || !body || !collapseKey) return;

    if (nextCollapsed) {
        body.style.maxHeight = `${body.scrollHeight}px`;
        void body.offsetHeight;
        section.classList.add('is-collapsed');
        body.style.maxHeight = '0px';
    } else {
        section.classList.remove('is-collapsed');
        body.style.maxHeight = `${body.scrollHeight}px`;
    }

    header.setAttribute('aria-expanded', String(!nextCollapsed));
    body.setAttribute('aria-hidden', String(nextCollapsed));
    if (bodyInner && 'inert' in bodyInner) {
        bodyInner.inert = nextCollapsed;
    }

    if (!persist) return;
    if (nextCollapsed) {
        collapsedCourseGroups.add(collapseKey);
    } else {
        collapsedCourseGroups.delete(collapseKey);
    }
    writeJsonStorage('manage:courses:collapsed-groups', Array.from(collapsedCourseGroups));
}

function toggleCourseGroup(header) {
    const section = header?.closest('.course-group-section');
    if (!section) return;
    setCourseGroupCollapsed(section, header, !section.classList.contains('is-collapsed'));
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

    renderCourseGroups();
    renderFilterEmptyState();
    updateVisibleSummary();
}

function setGroupMode(mode) {
    currentGroupMode = groupModeConfig[mode] ? mode : 'none';
    const label = groupModeConfig[currentGroupMode]?.label || groupModeConfig.none.label;
    elements.groupModeButtons.forEach((button) => {
        const isActive = button.dataset.courseGroupMode === currentGroupMode;
        button.classList.toggle('is-active', isActive);
        button.setAttribute('aria-pressed', isActive ? 'true' : 'false');
    });
    if (elements.groupModeLabel) {
        elements.groupModeLabel.textContent = label;
    }
    renderCourseGroups();
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
    if (course.can_manage === false) {
        showMessage('系内共享课程可以直接开课，只有创建者可以编辑课程内容。', 'warning');
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
        department: elements.departmentInput?.value || '',
        sect_name: elements.sectNameInput?.value || '',
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

async function handleAcademicCourseSync(triggerButton) {
    const buttons = elements.academicSyncButtons || [];
    const originalLabels = new Map(buttons.map((button) => [button, button.textContent]));
    buttons.forEach((button) => {
        button.disabled = true;
        button.textContent = '同步中...';
    });
    if (triggerButton) {
        triggerButton.textContent = '正在读取教务课表...';
    }

    try {
        const result = await apiFetch('/api/manage/courses/sync-current-academic', {
            method: 'POST',
            silent: true,
        });
        const followUp = Array.isArray(result.follow_up_items) && result.follow_up_items.length
            ? ` 后续请补充：${result.follow_up_items.slice(0, 3).join('、')}。`
            : '';
        const baseMessage = result.message
            || `已同步 ${result.course_count || 0} 门课程、${result.schedule_item_count || 0} 条课表安排。`;
        showMessage(
            `${baseMessage}${followUp}`,
            'success',
            5200,
        );
        setTimeout(() => {
            window.location.assign('/manage/courses?academic_course_sync=1');
        }, 900);
    } catch (error) {
        showMessage(error.message || '同步教务课程失败，请确认账号已验证且教务课表可访问。', 'error', 5200);
    } finally {
        buttons.forEach((button) => {
            button.disabled = false;
            button.textContent = originalLabels.get(button) || '从教务系统同步';
        });
    }
}

function bindEvents() {
    elements.openButtons.forEach((button) => button.addEventListener('click', openCreateModal));
    elements.academicSyncButtons.forEach((button) => button.addEventListener('click', () => handleAcademicCourseSync(button)));
    elements.addLessonBtn?.addEventListener('click', () => {
        createLessonRow();
        renumberLessonRows();
    });
    elements.saveBtn?.addEventListener('click', handleSaveCourse);
    elements.aiGenerateBtn?.addEventListener('click', handleAiGenerateLessons);
    elements.searchInput?.addEventListener('input', applyFilters);
    elements.filterSelect?.addEventListener('change', applyFilters);
    elements.groupModeButtons.forEach((button) => {
        button.addEventListener('click', () => setGroupMode(button.dataset.courseGroupMode || 'none'));
    });
    window.addEventListener('resize', () => {
        window.cancelAnimationFrame(courseGroupResizeFrame);
        courseGroupResizeFrame = window.requestAnimationFrame(refreshCourseGroupHeights);
    });
    elements.totalHoursInput?.addEventListener('input', updateAiMeta);
    elements.aiSectionCountInput?.addEventListener('input', updateAiMeta);

    elements.lessonsContainer?.addEventListener('click', (event) => {
        const button = event.target.closest('[data-action="remove-lesson"]');
        if (button) {
            const row = button.closest('[data-lesson-row]');
            row?.remove();
            ensureOneLessonRow();
            return;
        }

        const row = event.target.closest('[data-lesson-row]');
        if (!row) return;

        const pickButton = event.target.closest('[data-action="pick-material"]');
        if (pickButton) {
            const currentMaterial = normalizeLearningMaterial({
                learning_material_id: row.querySelector('[data-field="learning_material_id"]')?.value || '',
                learning_material_name: row.querySelector('[data-role="material-summary"]')?.textContent || '',
                learning_material_path: row.querySelector('[data-role="material-path"]')?.textContent || '',
                learning_material_viewer_url: row.dataset.learningMaterialViewerUrl || '',
            });
            learningMaterialSelector.open({
                title: '选择本次课的课程材料',
                subtitle: '浏览课程材料库中的文件夹结构，并为当前课堂节点绑定一个 Markdown 文档。',
                confirmLabel: '绑定到本次课',
                initialMaterial: currentMaterial,
            }).then((selectedMaterial) => {
                if (!selectedMaterial) return;
                setLessonMaterial(row, selectedMaterial);
            }).catch((error) => {
                showMessage(error.message || '加载材料选择器失败', 'error');
            });
            return;
        }

        const previewButton = event.target.closest('[data-action="preview-material"]');
        if (previewButton) {
            const viewerUrl = row.dataset.learningMaterialViewerUrl || '';
            if (!viewerUrl) {
                showMessage('当前课堂还没有绑定文档', 'warning');
                return;
            }
            window.open(viewerUrl, '_blank', 'noopener');
            return;
        }

        const clearButton = event.target.closest('[data-action="clear-material"]');
        if (clearButton) {
            setLessonMaterial(row, null);
        }
    });

    elements.courseCardGrid?.addEventListener('click', (event) => {
        const groupToggle = event.target.closest('[data-action="toggle-course-group"]');
        if (groupToggle) {
            toggleCourseGroup(groupToggle);
            return;
        }

        const editButton = event.target.closest('[data-action="edit-course"]');
        if (editButton) {
            openEditModal(editButton.dataset.courseId);
            return;
        }

        const deleteButton = event.target.closest('[data-action="delete-course"]');
        if (deleteButton) {
            handleDeleteCourse(deleteButton);
            return;
        }

        const card = event.target.closest('.course-card[data-course-id]');
        if (card && !isCardActionTarget(event.target)) {
            openEditModal(card.dataset.courseId);
        }
    });

    elements.courseCardGrid?.addEventListener('keydown', (event) => {
        if (!['Enter', ' '].includes(event.key)) return;
        const card = event.target.closest('.course-card[data-course-id]');
        if (!card || event.target !== card) return;
        event.preventDefault();
        openEditModal(card.dataset.courseId);
    });
}

function syncCardTabState() {
    getCourseCards().forEach((card) => {
        card.setAttribute('tabindex', '0');
        if (!card.getAttribute('aria-label')) {
            const title = card.querySelector('.course-card-title')?.textContent?.trim() || '课程';
            card.setAttribute('aria-label', `编辑课程 ${title}`);
        }
    });
}

bindEvents();
syncCardTabState();
ensureOneLessonRow();
updateAiMeta();
applyFilters();
