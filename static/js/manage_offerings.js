import { apiFetch } from '/static/js/api.js';
import { showMessage } from '/static/js/ui.js';

const config = window.OFFERINGS_PAGE_DATA || {};
const courseMap = new Map((config.courses || []).map((item) => [Number(item.id), item]));
const offeringMap = new Map((config.offerings || []).map((item) => [Number(item.id), item]));

const elements = {
    form: document.getElementById('offeringSaveForm'),
    editorState: document.getElementById('offeringEditorState'),
    editorStateText: document.getElementById('offeringEditorStateText'),
    cancelEditBtn: document.getElementById('offeringCancelEditBtn'),
    offeringIdInput: document.getElementById('offeringIdInput'),
    semesterSelect: document.getElementById('offeringSemesterSelect'),
    classSelect: document.getElementById('offeringClassSelect'),
    courseSelect: document.getElementById('offeringCourseSelect'),
    textbookSelect: document.getElementById('offeringTextbookSelect'),
    scheduleSourceSelect: document.getElementById('offeringScheduleSourceSelect'),
    academicPanel: document.getElementById('offeringAcademicPanel'),
    academicClassSelect: document.getElementById('offeringAcademicClassSelect'),
    academicSummary: document.getElementById('offeringAcademicSummary'),
    academicHelp: document.getElementById('offeringAcademicHelp'),
    firstClassDateInput: document.getElementById('offeringFirstClassDateInput'),
    fixedSchedulePanel: document.getElementById('fixedSchedulePanel'),
    weeklyScheduleContainer: document.getElementById('weeklyScheduleContainer'),
    weeklyScheduleMenu: document.getElementById('weeklyScheduleMenu'),
    weeklyScheduleTemplate: document.getElementById('weeklyScheduleRowTemplate'),
    addWeeklyScheduleBtn: document.getElementById('addWeeklyScheduleBtn'),
    previewMeta: document.getElementById('offeringPreviewMeta'),
    previewWarnings: document.getElementById('offeringPreviewWarnings'),
    previewList: document.getElementById('offeringPreviewList'),
    previewBtn: document.getElementById('offeringPreviewBtn'),
    saveBtn: document.getElementById('offeringSaveBtn'),
    offeringList: document.getElementById('offeringList'),
    courseSummary: document.getElementById('offeringCourseSummary'),
};

let previewDebounceTimer = null;
let activeScheduleIndex = 0;

const weekdayLabels = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];

function getScheduleRows() {
    return Array.from(elements.weeklyScheduleContainer?.querySelectorAll('[data-schedule-row]') || []);
}

function scheduleRowState(row) {
    const weekday = Number(row?.querySelector('[data-field="weekday"]')?.value ?? -1);
    const sections = Number(row?.querySelector('[data-field="section_count"]')?.value || 0);
    if (weekday >= 0 && weekday <= 6 && sections > 0) return 'complete';
    if (weekday >= 0 || sections > 0) return 'partial';
    return 'empty';
}

function scheduleRowLabel(row, index) {
    const weekday = Number(row?.querySelector('[data-field="weekday"]')?.value ?? index % 7);
    return weekdayLabels[weekday] || `安排 ${index + 1}`;
}

function scheduleRowMeta(row) {
    const sections = Number(row?.querySelector('[data-field="section_count"]')?.value || 0);
    const state = scheduleRowState(row);
    const status = state === 'complete' ? '已完整' : (state === 'partial' ? '待补齐' : '未填写');
    return `${sections || 0} 小节 · ${status}`;
}

function syncScheduleWorkbench() {
    const rows = getScheduleRows();
    if (!rows.length) {
        if (elements.weeklyScheduleMenu) elements.weeklyScheduleMenu.innerHTML = '';
        return;
    }
    activeScheduleIndex = Math.max(0, Math.min(activeScheduleIndex, rows.length - 1));

    rows.forEach((row, index) => {
        const isActive = index === activeScheduleIndex;
        const heading = row.querySelector('[data-schedule-heading]');
        const status = row.querySelector('[data-schedule-status]');
        row.hidden = !isActive;
        row.classList.toggle('is-active', isActive);
        row.setAttribute('aria-hidden', isActive ? 'false' : 'true');
        row.dataset.scheduleIndex = String(index);
        if (heading) heading.textContent = `第 ${index + 1} 个上课日 · ${scheduleRowLabel(row, index)}`;
        if (status) status.textContent = scheduleRowMeta(row);
    });

    if (!elements.weeklyScheduleMenu) return;
    elements.weeklyScheduleMenu.innerHTML = rows.map((row, index) => {
        const state = scheduleRowState(row);
        const isActive = index === activeScheduleIndex;
        return `
            <button type="button"
                    class="offering-schedule-tab is-${state}${isActive ? ' is-active' : ''}"
                    data-action="select-schedule"
                    data-schedule-index="${index}"
                    aria-current="${isActive ? 'true' : 'false'}">
                <span class="offering-schedule-tab-dot" aria-hidden="true"></span>
                <span class="offering-schedule-tab-copy">
                    <strong>${scheduleRowLabel(row, index)}</strong>
                    <span>${scheduleRowMeta(row)}</span>
                </span>
                <span class="offering-schedule-tab-index">${index + 1}</span>
            </button>
        `;
    }).join('');
}

function setActiveSchedule(index, { focus = false } = {}) {
    const rows = getScheduleRows();
    if (!rows.length) return;
    activeScheduleIndex = Math.max(0, Math.min(Number(index) || 0, rows.length - 1));
    syncScheduleWorkbench();
    if (focus) {
        rows[activeScheduleIndex]?.querySelector('[data-field="weekday"]')?.focus({ preventScroll: true });
    }
}

function createScheduleRow(data = {}) {
    if (!elements.weeklyScheduleTemplate || !elements.weeklyScheduleContainer) {
        return null;
    }
    const fragment = elements.weeklyScheduleTemplate.content.cloneNode(true);
    const row = fragment.querySelector('[data-schedule-row]');
    if (!row) return null;

    row.querySelector('[data-field="weekday"]').value = String(data.weekday ?? 0);
    row.querySelector('[data-field="section_count"]').value = String(data.section_count || 2);
    elements.weeklyScheduleContainer.appendChild(fragment);
    return row;
}

function ensureOneScheduleRow() {
    const rows = getScheduleRows();
    if (!rows.length) {
        activeScheduleIndex = 0;
        createScheduleRow();
    }
    syncScheduleWorkbench();
}

function collectWeeklySchedule() {
    return getScheduleRows().map((row) => ({
        weekday: Number(row.querySelector('[data-field="weekday"]')?.value || 0),
        section_count: Number(row.querySelector('[data-field="section_count"]')?.value || 0),
    }));
}

function getSelectedCourse() {
    return courseMap.get(Number(elements.courseSelect?.value || 0)) || null;
}

function getSelectedSemesterId() {
    return Number(elements.semesterSelect?.value || 0);
}

function getAcademicClassesForSelectedCourse() {
    const course = getSelectedCourse();
    if (!course || !Array.isArray(course.academic_occurrence_classes)) return [];
    const semesterId = getSelectedSemesterId();
    return course.academic_occurrence_classes.filter((item) => {
        if (!semesterId) return true;
        return Number(item.semester_id || 0) === semesterId;
    });
}

function selectedScheduleSource() {
    return elements.scheduleSourceSelect?.value || 'fixed_cycle';
}

function updateScheduleMode({ preserveSelection = true } = {}) {
    const academicClasses = getAcademicClassesForSelectedCourse();
    const hasAcademicSchedule = academicClasses.length > 0;
    const previousAcademicClass = preserveSelection ? (elements.academicClassSelect?.value || '') : '';

    if (elements.academicPanel) {
        elements.academicPanel.classList.toggle('is-visible', hasAcademicSchedule);
    }
    if (elements.scheduleSourceSelect) {
        const currentValue = elements.scheduleSourceSelect.value;
        if (!hasAcademicSchedule && currentValue === 'academic_sync') {
            elements.scheduleSourceSelect.value = 'fixed_cycle';
        } else if (hasAcademicSchedule && !preserveSelection) {
            elements.scheduleSourceSelect.value = 'academic_sync';
        } else if (hasAcademicSchedule && !currentValue) {
            elements.scheduleSourceSelect.value = 'academic_sync';
        }
        const academicOption = elements.scheduleSourceSelect.querySelector('option[value="academic_sync"]');
        if (academicOption) academicOption.disabled = !hasAcademicSchedule;
    }

    if (elements.academicClassSelect) {
        elements.academicClassSelect.innerHTML = '<option value="">自动匹配或请选择教学班</option>';
        academicClasses.forEach((item) => {
            const option = document.createElement('option');
            option.value = item.teaching_class_name || '';
            option.textContent = [
                item.teaching_class_name || '未命名教学班',
                `${item.session_count || 0} 次`,
                item.first_session_date && item.last_session_date ? `${item.first_session_date} 至 ${item.last_session_date}` : '',
            ].filter(Boolean).join(' · ');
            elements.academicClassSelect.appendChild(option);
        });
        if (previousAcademicClass && academicClasses.some((item) => item.teaching_class_name === previousAcademicClass)) {
            elements.academicClassSelect.value = previousAcademicClass;
        } else if (academicClasses.length === 1) {
            elements.academicClassSelect.value = academicClasses[0].teaching_class_name || '';
        }
    }

    const useAcademic = selectedScheduleSource() === 'academic_sync' && hasAcademicSchedule;
    if (elements.fixedSchedulePanel) {
        elements.fixedSchedulePanel.classList.toggle('is-muted', useAcademic);
    }
    if (elements.firstClassDateInput) {
        elements.firstClassDateInput.required = !useAcademic;
        elements.firstClassDateInput.closest('.form-group')?.classList.toggle('is-muted', useAcademic);
    }
    if (elements.academicSummary) {
        const totalSessions = academicClasses.reduce((sum, item) => sum + Number(item.session_count || 0), 0);
        const nonPeriodicCount = academicClasses.reduce((sum, item) => sum + Number(item.non_periodic_count || 0), 0);
        elements.academicSummary.innerHTML = hasAcademicSchedule
            ? `
                <span class="academic-badge is-success">${totalSessions} 次真实课次</span>
                ${nonPeriodicCount ? `<span class="academic-badge is-accent">${nonPeriodicCount} 次非周期</span>` : ''}
            `
            : '<span class="academic-badge is-muted">暂无教务课次</span>';
    }
    if (elements.academicHelp) {
        elements.academicHelp.textContent = hasAcademicSchedule
            ? '保存后课堂时间轴会按教务系统每一周的真实日期、节次和地点生成；若存在多个教学班，请先确认当前平台班级对应哪一个。'
            : '该课程当前没有可用的教务真实课次，请先同步教务课表或使用固定周循环。';
    }
}

function renderCourseSummary() {
    if (!elements.courseSummary) return;
    const course = getSelectedCourse();

    if (!course) {
        elements.courseSummary.innerHTML = `
            <div class="academic-empty">
                <strong>还没有选中课程</strong>
                选择课程后，可在这里快速确认课程模板是否足够完整，避免开课后再返工调整。
            </div>
        `;
        return;
    }

    const lessons = Array.isArray(course.lessons) ? course.lessons : [];
    const academicClasses = getAcademicClassesForSelectedCourse();
    const academicSessionCount = academicClasses.reduce((sum, item) => sum + Number(item.session_count || 0), 0);
    const academicClassNames = academicClasses.map((item) => item.teaching_class_name || item.class_composition).filter(Boolean);
    const lessonListHtml = lessons.length
        ? lessons.slice(0, 4).map((lesson) => `
            <div class="offering-course-lesson-item">
                <strong>${lesson.title || '未命名课堂'}</strong>
                <span>${lesson.content_preview || lesson.content || '暂无内容摘要'}</span>
            </div>
        `).join('')
        : `
            <div class="academic-empty">
                <strong>该课程还没有课堂设置</strong>
                请先回到课程管理页补充课堂模板，否则无法生成课堂时间轴。
            </div>
        `;

    elements.courseSummary.innerHTML = `
        <div>
            <h4>${course.name}</h4>
            <p class="academic-card-subtitle">${course.description_preview || '暂未填写课程简介。'}</p>
        </div>
        <div class="academic-meta-list">
            <div class="academic-meta-row">
                <span class="academic-meta-label">课程总学时</span>
                <span class="academic-meta-value">${course.total_hours || 0} 学时</span>
            </div>
            <div class="academic-meta-row">
                <span class="academic-meta-label">课堂设置</span>
                <span class="academic-meta-value">${course.lesson_count || 0} 次课</span>
            </div>
            <div class="academic-meta-row">
                <span class="academic-meta-label">合计小节</span>
                <span class="academic-meta-value">${course.planned_section_count || 0} 小节</span>
            </div>
            <div class="academic-meta-row">
                <span class="academic-meta-label">结构状态</span>
                <span class="academic-meta-value">${course.coverage_label || '待完善'}</span>
            </div>
            <div class="academic-meta-row">
                <span class="academic-meta-label">教务真实课次</span>
                <span class="academic-meta-value">${academicSessionCount ? `${academicSessionCount} 次 · ${academicClassNames.slice(0, 2).join(' / ') || '已同步'}` : '未同步或非当前学期'}</span>
            </div>
        </div>
        <div class="offering-course-lesson-list">${lessonListHtml}</div>
    `;
}

function renderPreviewPlaceholder(message) {
    if (elements.previewMeta) {
        elements.previewMeta.innerHTML = `
            <div class="offering-preview-meta-row">
                <span>预览状态</span>
                <strong>${message}</strong>
            </div>
        `;
    }
    if (elements.previewWarnings) {
        elements.previewWarnings.innerHTML = '';
    }
    if (elements.previewList) {
        elements.previewList.innerHTML = `
            <div class="academic-empty">
                <strong>预览还未生成</strong>
                ${message}
            </div>
        `;
    }
}

function renderPreview(previewResponse) {
    const preview = previewResponse?.preview || {};
    const sessions = Array.isArray(preview.sessions) ? preview.sessions : [];
    const warnings = Array.isArray(preview.warnings) ? preview.warnings : [];

    if (elements.previewMeta) {
        elements.previewMeta.innerHTML = `
            <div class="offering-preview-meta-row">
                <span>课堂名称</span>
                <strong>${previewResponse.course_name || '--'} / ${previewResponse.class_name || '--'}</strong>
            </div>
            <div class="offering-preview-meta-row">
                <span>课程模板</span>
                <strong>${previewResponse.course_lesson_count || 0} 次课 · ${previewResponse.planned_section_count || 0} 小节</strong>
            </div>
            <div class="offering-preview-meta-row">
                <span>排课来源</span>
                <strong>${preview.schedule_source_label || (previewResponse.schedule_source === 'academic_sync' ? '教务实际排课' : '固定周循环')}${preview.academic_teaching_class_name ? ` · ${preview.academic_teaching_class_name}` : ''}</strong>
            </div>
            <div class="offering-preview-meta-row">
                <span>时间轴摘要</span>
                <strong>${preview.schedule_info || '暂未生成'}</strong>
            </div>
            <div class="offering-preview-meta-row">
                <span>生成结果</span>
                <strong>${preview.session_count || 0} 次课已映射到具体日期</strong>
            </div>
        `;
    }

    if (elements.previewWarnings) {
        elements.previewWarnings.innerHTML = warnings.length
            ? warnings.map((item) => `<div class="offering-warning-item">${item}</div>`).join('')
            : '';
    }

    if (elements.previewList) {
        elements.previewList.innerHTML = sessions.length
            ? sessions.map((session) => `
                <article class="offering-session-item">
                    <div class="offering-session-top">
                        <strong>${session.title || '未命名课堂'}</strong>
                        <span class="offering-session-date">${session.week_label || '未计算周次'} · ${session.date_label || ''}</span>
                    </div>
                    <p>${session.content_preview || session.content || '暂无课堂内容'}</p>
                    <div class="academic-badge-row">
                        <span class="academic-badge">${session.section_count || 0} 小节</span>
                        ${session.is_academic_schedule ? `<span class="academic-badge is-success">教务实际排课</span>` : ''}
                        ${session.academic_section_text ? `<span class="academic-badge">节次 ${session.academic_section_text}</span>` : ''}
                        ${session.academic_location ? `<span class="academic-badge">${session.academic_location}</span>` : ''}
                        ${session.is_non_periodic ? `<span class="academic-badge is-accent">非周期课次</span>` : ''}
                        ${session.is_section_match ? '' : `<span class="academic-badge is-accent">与排课节数不一致</span>`}
                    </div>
                </article>
            `).join('')
            : `
                <div class="academic-empty">
                    <strong>当前没有可映射的课堂内容</strong>
                    可能是课程模板还未补齐，或排课日期超出了学期范围。
                </div>
            `;
    }
}

function collectFormPayload() {
    return {
        offering_id: elements.offeringIdInput?.value || '',
        semester_id: elements.semesterSelect?.value || '',
        class_id: elements.classSelect?.value || '',
        course_id: elements.courseSelect?.value || '',
        textbook_id: elements.textbookSelect?.value || '',
        schedule_source: selectedScheduleSource(),
        academic_teaching_class_name: elements.academicClassSelect?.value || '',
        first_class_date: elements.firstClassDateInput?.value || '',
        weekly_schedule: collectWeeklySchedule(),
    };
}

async function fetchPreview({ silent = true } = {}) {
    const payload = collectFormPayload();
    const needsFixedDate = payload.schedule_source !== 'academic_sync';
    if (!payload.semester_id || !payload.class_id || !payload.course_id || !payload.textbook_id || (needsFixedDate && !payload.first_class_date)) {
        renderPreviewPlaceholder(needsFixedDate
            ? '先完整选择课程、班级、学期、教材和第一次上课日期。'
            : '先完整选择课程、班级、学期和教材；若存在多个教务教学班，请选择对应教学班。');
        return null;
    }

    try {
        const result = await apiFetch('/api/manage/class_offerings/preview', {
            method: 'POST',
            body: payload,
            silent: true,
        });
        renderPreview(result);
        return result;
    } catch (error) {
        renderPreviewPlaceholder(error.message || '预览生成失败，请检查表单配置。');
        if (!silent) {
            showMessage(error.message || '预览生成失败', 'error');
        }
        return null;
    }
}

function schedulePreviewRefresh() {
    window.clearTimeout(previewDebounceTimer);
    previewDebounceTimer = window.setTimeout(() => {
        fetchPreview({ silent: true });
    }, 260);
}

function toggleEditorState(isEditing, title = '') {
    if (!elements.editorState || !elements.editorStateText || !elements.saveBtn) return;
    elements.editorState.classList.toggle('is-visible', Boolean(isEditing));
    elements.editorStateText.textContent = title || '正在编辑课堂';
    elements.saveBtn.textContent = isEditing ? '更新课堂' : '开设课堂';
}

function resetForm() {
    if (elements.offeringIdInput) elements.offeringIdInput.value = '';
    if (elements.classSelect) elements.classSelect.value = '';
    if (elements.courseSelect) elements.courseSelect.value = '';
    if (elements.textbookSelect) elements.textbookSelect.value = '';
    if (elements.scheduleSourceSelect) elements.scheduleSourceSelect.value = 'academic_sync';
    if (elements.academicClassSelect) elements.academicClassSelect.value = '';
    if (elements.firstClassDateInput) elements.firstClassDateInput.value = '';
    if (elements.weeklyScheduleContainer) elements.weeklyScheduleContainer.innerHTML = '';
    activeScheduleIndex = 0;
    ensureOneScheduleRow();

    if (elements.semesterSelect && config.defaultSemesterId) {
        elements.semesterSelect.value = String(config.defaultSemesterId);
    }

    toggleEditorState(false);
    updateScheduleMode({ preserveSelection: false });
    renderCourseSummary();
    renderPreviewPlaceholder('先完整选择课程、班级、学期和教材，系统会优先使用教务真实课次。');
}

function populateForm(offering) {
    resetForm();
    if (!offering) return;

    if (elements.offeringIdInput) elements.offeringIdInput.value = String(offering.id || '');
    if (elements.semesterSelect) elements.semesterSelect.value = String(offering.semester_id || '');
    if (elements.classSelect) elements.classSelect.value = String(offering.class_id || '');
    if (elements.courseSelect) elements.courseSelect.value = String(offering.course_id || '');
    if (elements.textbookSelect) elements.textbookSelect.value = String(offering.textbook_id || '');
    if (elements.scheduleSourceSelect) elements.scheduleSourceSelect.value = offering.schedule_source || 'fixed_cycle';
    if (elements.firstClassDateInput) elements.firstClassDateInput.value = offering.first_class_date || '';

    if (elements.weeklyScheduleContainer) elements.weeklyScheduleContainer.innerHTML = '';
    activeScheduleIndex = 0;
    const weeklySchedule = Array.isArray(offering.weekly_schedule) && offering.weekly_schedule.length
        ? offering.weekly_schedule
        : [{ weekday: 0, section_count: 2 }];
    weeklySchedule.forEach((item) => createScheduleRow(item));
    updateScheduleMode({ preserveSelection: false });
    if (elements.academicClassSelect) {
        elements.academicClassSelect.value = offering.academic_teaching_class_name || '';
    }

    toggleEditorState(true, `正在编辑：${offering.course_name} / ${offering.class_name}`);
    renderCourseSummary();
    fetchPreview({ silent: true });
    window.scrollTo({ top: 0, behavior: 'smooth' });
}

async function handleSave(event) {
    event.preventDefault();
    if (!elements.saveBtn) return;

    const originalText = elements.saveBtn.textContent;
    elements.saveBtn.disabled = true;
    elements.saveBtn.textContent = '保存中...';

    try {
        const result = await apiFetch('/api/manage/class_offerings/save', {
            method: 'POST',
            body: collectFormPayload(),
            silent: true,
        });
        showMessage(result.message || '课堂已保存', 'success');
        window.location.reload();
    } catch (error) {
        showMessage(error.message || '保存课堂失败', 'error');
    } finally {
        elements.saveBtn.disabled = false;
        elements.saveBtn.textContent = originalText;
        toggleEditorState(Boolean(elements.offeringIdInput?.value), elements.editorStateText?.textContent || '');
    }
}

async function handleDelete(button) {
    const offeringId = Number(button.dataset.offeringId || 0);
    const offeringName = button.dataset.offeringName || '当前课堂';
    if (!offeringId) return;

    const confirmed = window.confirm(`确定删除课堂“${offeringName}”吗？\n这会同时删除该课堂的时间轴快照、AI 配置和聊天记录。`);
    if (!confirmed) return;

    try {
        const result = await apiFetch(`/api/manage/class_offerings/${offeringId}`, { method: 'DELETE', silent: true });
        showMessage(result.message || '课堂已删除', 'success');
        window.location.reload();
    } catch (error) {
        showMessage(error.message || '删除课堂失败', 'error');
    }
}

function applyQueryDefaults() {
    const params = new URLSearchParams(window.location.search);
    const courseId = params.get('course_id');
    if (courseId && elements.courseSelect) {
        elements.courseSelect.value = courseId;
    }

    if (elements.semesterSelect && config.defaultSemesterId && !elements.semesterSelect.value) {
        elements.semesterSelect.value = String(config.defaultSemesterId);
    }
    updateScheduleMode({ preserveSelection: false });
}

function bindEvents() {
    elements.addWeeklyScheduleBtn?.addEventListener('click', () => {
        createScheduleRow();
        activeScheduleIndex = getScheduleRows().length - 1;
        syncScheduleWorkbench();
        setActiveSchedule(activeScheduleIndex, { focus: true });
        schedulePreviewRefresh();
    });

    elements.cancelEditBtn?.addEventListener('click', resetForm);
    elements.previewBtn?.addEventListener('click', () => fetchPreview({ silent: false }));
    elements.form?.addEventListener('submit', handleSave);

    [
        elements.semesterSelect,
        elements.classSelect,
        elements.courseSelect,
        elements.textbookSelect,
        elements.scheduleSourceSelect,
        elements.academicClassSelect,
        elements.firstClassDateInput,
    ].filter(Boolean).forEach((node) => {
        node.addEventListener('change', () => {
            if (node === elements.courseSelect || node === elements.semesterSelect || node === elements.scheduleSourceSelect) {
                updateScheduleMode({ preserveSelection: node !== elements.courseSelect });
                renderCourseSummary();
            }
            schedulePreviewRefresh();
        });
    });

    elements.weeklyScheduleContainer?.addEventListener('click', (event) => {
        const button = event.target.closest('[data-action="remove-schedule"]');
        if (!button) return;
        const row = button.closest('[data-schedule-row]');
        const removedIndex = Number(row?.dataset.scheduleIndex || 0);
        row?.remove();
        if (removedIndex < activeScheduleIndex) {
            activeScheduleIndex -= 1;
        } else if (removedIndex === activeScheduleIndex) {
            activeScheduleIndex = Math.max(0, removedIndex - 1);
        }
        ensureOneScheduleRow();
        schedulePreviewRefresh();
    });

    elements.weeklyScheduleMenu?.addEventListener('click', (event) => {
        const button = event.target.closest('[data-action="select-schedule"]');
        if (!button) return;
        setActiveSchedule(Number(button.dataset.scheduleIndex || 0));
    });

    elements.weeklyScheduleContainer?.addEventListener('input', syncScheduleWorkbench);
    elements.weeklyScheduleContainer?.addEventListener('change', () => {
        syncScheduleWorkbench();
        schedulePreviewRefresh();
    });
    elements.offeringList?.addEventListener('click', (event) => {
        const editButton = event.target.closest('[data-action="edit-offering"]');
        if (editButton) {
            populateForm(offeringMap.get(Number(editButton.dataset.offeringId || 0)));
            return;
        }

        const deleteButton = event.target.closest('[data-action="delete-offering"]');
        if (deleteButton) {
            handleDelete(deleteButton);
        }
    });
}

bindEvents();
resetForm();
applyQueryDefaults();
renderCourseSummary();
schedulePreviewRefresh();
