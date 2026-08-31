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
    extraClassList: document.getElementById('offeringExtraClassList'),
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
            const displayName = item.class_display_name || item.display_teaching_class_name || item.teaching_class_name || '';
            option.value = item.teaching_class_name || '';
            option.textContent = [
                item.teaching_class_name || '未命名教学班',
                `${item.session_count || 0} 次`,
                item.first_session_date && item.last_session_date ? `${item.first_session_date} 至 ${item.last_session_date}` : '',
            ].filter(Boolean).join(' · ');
            option.textContent = option.textContent.replace(item.teaching_class_name || '', displayName || item.teaching_class_name || '');
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
                选择课程后在这里核对课程模板完整度。
            </div>
        `;
        return;
    }

    const lessons = Array.isArray(course.lessons) ? course.lessons : [];
    const academicClasses = getAcademicClassesForSelectedCourse();
    const academicSessionCount = academicClasses.reduce((sum, item) => sum + Number(item.session_count || 0), 0);
    const academicClassNames = academicClasses.map((item) => item.teaching_class_name || item.class_composition).filter(Boolean);
    academicClassNames.splice(
        0,
        academicClassNames.length,
        ...academicClasses
            .map((item) => item.class_display_name || item.display_teaching_class_name || item.class_composition || item.teaching_class_name)
            .filter(Boolean),
    );
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
    const previewAcademicClassName = preview.academic_teaching_class_display_name
        || previewResponse.academic_teaching_class_display_name
        || preview.academic_teaching_class_name
        || '';
    if (previewAcademicClassName) preview.academic_teaching_class_name = previewAcademicClassName;

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

function extraClassCheckboxes() {
    return Array.from(elements.extraClassList?.querySelectorAll('input[type="checkbox"]') || []);
}

function selectedClassIds() {
    const primary = elements.classSelect?.value || '';
    const ids = primary ? [primary] : [];
    extraClassCheckboxes().forEach((box) => {
        if (box.checked && box.value && !ids.includes(box.value)) ids.push(box.value);
    });
    return ids;
}

let lastCombinedAutofillCourseId = 0;

/** 教务同步课程带有教学班组成（合班）时，选课后自动匹配主班级并勾选其余班级。 */
function applyCombinedClassAutofill() {
    if (elements.offeringIdInput?.value) return; // 编辑既有课堂时不自动改动
    const courseId = Number(elements.courseSelect?.value || 0);
    if (!courseId || courseId === lastCombinedAutofillCourseId) return;
    const course = courseMap.get(courseId);
    const names = (course?.academic_metadata?.combined_admin_classes || [])
        .map((value) => String(value || '').trim())
        .filter(Boolean);
    if (names.length < 2) return;
    lastCombinedAutofillCourseId = courseId;

    if (!elements.classSelect?.value) {
        selectOptionByText(elements.classSelect, names);
    }
    const primaryValue = elements.classSelect?.value || '';
    let matchedCount = 0;
    names.forEach((candidate) => {
        const box = extraClassCheckboxes().find((item) => {
            const label = (item.closest('.offering-extra-class-item')?.textContent || '').trim();
            return label && (label === candidate || label.includes(candidate) || candidate.includes(label));
        });
        if (box && box.value !== primaryValue && !box.checked) {
            box.checked = true;
            matchedCount += 1;
        }
    });
    syncPrimaryClassState();
    if (matchedCount) {
        showMessage(`该课程为合班课（教学班组成：${names.join('、')}），已自动勾选 ${matchedCount} 个合班班级，可调整后保存。`, 'info');
        schedulePreviewRefresh();
    }
}

/** 主班级不允许在合班勾选组里重复出现：置灰并取消勾选。 */
function syncPrimaryClassState() {
    const primary = elements.classSelect?.value || '';
    extraClassCheckboxes().forEach((box) => {
        const isPrimary = Boolean(primary) && box.value === primary;
        if (isPrimary) box.checked = false;
        box.closest('.offering-extra-class-item')?.classList.toggle('is-primary-class', isPrimary);
    });
}

function collectFormPayload() {
    return {
        offering_id: elements.offeringIdInput?.value || '',
        semester_id: elements.semesterSelect?.value || '',
        class_id: elements.classSelect?.value || '',
        class_ids: selectedClassIds(),
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
    extraClassCheckboxes().forEach((box) => { box.checked = false; });
    syncPrimaryClassState();
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
    const linkedClassIds = (Array.isArray(offering.class_ids) ? offering.class_ids : [])
        .map((value) => String(value));
    extraClassCheckboxes().forEach((box) => {
        box.checked = linkedClassIds.includes(box.value) && box.value !== String(offering.class_id || '');
    });
    syncPrimaryClassState();
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

const SEMESTER_NAME_PATTERN = /(\d{4})\s*[-–—~]\s*(\d{4}).*?([一二12])\s*学期/;
const TERM_DIGITS = { '一': '1', '1': '1', '二': '2', '2': '2' };

function selectOptionByText(select, targets) {
    if (!select) return false;
    const candidates = (Array.isArray(targets) ? targets : [targets])
        .map((value) => String(value || '').trim())
        .filter(Boolean);
    if (!candidates.length) return false;
    const options = Array.from(select.options).filter((option) => option.value);
    // 先找完全相等，再找互相包含（避免"软件工程2302班"与选项"软工2302班"漏配）。
    for (const target of candidates) {
        const exact = options.find((option) => (option.textContent || '').trim() === target);
        if (exact) { select.value = exact.value; return true; }
    }
    for (const target of candidates) {
        const fuzzy = options.find((option) => {
            const text = (option.textContent || '').trim();
            return text && (text.includes(target) || target.includes(text));
        });
        if (fuzzy) { select.value = fuzzy.value; return true; }
    }
    return false;
}

/** 智慧课堂课表"点击创建课堂"深链的自动预填。 */
function applySmartSchedulePrefill(params) {
    const courseName = (params.get('course') || '').trim();
    const rawClassName = (params.get('class_name') || '').trim();
    const year = (params.get('year') || '').trim();
    const term = (params.get('term') || '').trim();
    const missing = [];

    if (year && term && elements.semesterSelect) {
        const target = Array.from(elements.semesterSelect.options).find((option) => {
            const matched = SEMESTER_NAME_PATTERN.exec(option.textContent || '');
            if (!matched) return false;
            return `${matched[1]}-${matched[2]}` === year && (TERM_DIGITS[matched[3]] || '') === term;
        });
        if (target) {
            elements.semesterSelect.value = target.value;
        } else {
            missing.push(`学期（${year} 第${term}学期，请先在学期管理中创建）`);
        }
    }
    // 教学班组成可能是逗号分隔的多个行政班：第一个匹配到的作主班级，其余自动勾选为合班班级。
    const classCandidates = rawClassName.split(/[,，、]/).map((part) => part.trim()).filter(Boolean);
    if (classCandidates.length && !selectOptionByText(elements.classSelect, classCandidates)) {
        missing.push(`班级（${classCandidates[0]}，请先在班级管理中创建）`);
    }
    if (classCandidates.length > 1) {
        const primaryValue = elements.classSelect?.value || '';
        const unmatchedExtras = [];
        classCandidates.forEach((candidate) => {
            const box = extraClassCheckboxes().find((item) => {
                const label = (item.closest('.offering-extra-class-item')?.textContent || '').trim();
                return label && (label === candidate || label.includes(candidate) || candidate.includes(label));
            });
            if (box && box.value !== primaryValue) {
                box.checked = true;
            } else if (!box) {
                unmatchedExtras.push(candidate);
            }
        });
        syncPrimaryClassState();
        const matchedExtraCount = extraClassCheckboxes().filter((item) => item.checked).length;
        if (matchedExtraCount) {
            showMessage(`检测到教学班由多个行政班合成，已自动勾选 ${matchedExtraCount} 个合班班级，请核对。`, 'info');
        }
        if (unmatchedExtras.length) {
            missing.push(`合班班级（${unmatchedExtras.join('、')}，请先在班级管理中创建后再勾选）`);
        }
    }
    if (courseName && !selectOptionByText(elements.courseSelect, courseName)) {
        missing.push(`课程（${courseName}，请先在课程管理中创建）`);
    }
    elements.courseSelect?.dispatchEvent(new Event('change'));
    elements.form?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    if (missing.length) {
        showMessage(`已带入智慧课堂课表信息；以下项未匹配到，请补充：${missing.join('；')}`, 'info');
    } else {
        showMessage('已从智慧课堂课表带入开课信息，请核对后保存。', 'success');
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
    if (params.get('prefill') === 'smart_schedule') {
        applySmartSchedulePrefill(params);
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
            if (node === elements.courseSelect) {
                applyCombinedClassAutofill();
            }
            if (node === elements.classSelect) {
                syncPrimaryClassState();
            }
            schedulePreviewRefresh();
        });
    });

    elements.extraClassList?.addEventListener('change', () => {
        syncPrimaryClassState();
        schedulePreviewRefresh();
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

// ---------------------------------------------------------------------------
// 双开课堂合并向导（P4.0）：检测卡 → 预检 → 输入班级名确认执行
// ---------------------------------------------------------------------------
const mergeSection = document.getElementById('offeringMergeSection');
const mergeContainer = document.getElementById('offeringMergeCandidates');

function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, (ch) => (
        { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]
    ));
}

function renderMergeCandidates(candidates) {
    if (!mergeSection || !mergeContainer || !candidates.length) return;
    mergeSection.hidden = false;
    mergeContainer.innerHTML = candidates.map((group, index) => {
        const badge = group.academic_confirmed_combined
            ? '<span class="academic-badge is-accent">教务确认合班</span>'
            : '<span class="academic-badge">请自行确认确为合班</span>';
        const rows = group.offerings.map((o) => `
            <label class="offering-merge-option">
                <input type="radio" name="mergeTarget-${index}" value="${o.offering_id}" ${o.offering_id === group.recommended_target_id ? 'checked' : ''}>
                <span><strong>${escapeHtml(o.class_name)}</strong>（课堂 #${o.offering_id} · ${o.student_count} 人 · ${o.assignment_count} 作业 · ${o.session_count} 课次）</span>
            </label>`).join('');
        return `
        <article class="offering-merge-group" data-merge-group data-index="${index}">
            <div class="offering-merge-group__head">
                <strong>${escapeHtml(group.course_name)}</strong>
                <span>${escapeHtml(group.semester || '')} · ${group.offerings.length} 个课堂</span>
                ${badge}
            </div>
            <p class="offering-merge-hint">选择保留为主课堂的一项（其余课堂的数据将迁入主课堂）：</p>
            ${rows}
            <div class="offering-merge-actions">
                <button type="button" class="btn btn-secondary btn-sm" data-merge-preview>预检合并</button>
            </div>
            <div class="offering-merge-preview" data-merge-preview-result hidden></div>
        </article>`;
    }).join('');
}

function mergeGroupState(groupEl, candidates) {
    const index = Number(groupEl.dataset.index || 0);
    const group = candidates[index];
    const targetId = Number(groupEl.querySelector(`input[name="mergeTarget-${index}"]:checked`)?.value || 0);
    const sourceIds = group.offerings.map((o) => o.offering_id).filter((id) => id !== targetId);
    const targetName = group.offerings.find((o) => o.offering_id === targetId)?.class_name || '';
    return { group, targetId, sourceIds, targetName };
}

async function handleMergePreview(groupEl, candidates) {
    const { targetId, sourceIds, targetName } = mergeGroupState(groupEl, candidates);
    const resultEl = groupEl.querySelector('[data-merge-preview-result]');
    try {
        const data = await apiFetch('/api/manage/class_offerings/merge/preview', {
            method: 'POST',
            body: { target_offering_id: targetId, source_offering_ids: sourceIds },
        });
        const preview = data.preview;
        const tableRows = preview.tables.map((t) => `
            <tr><td>${escapeHtml(t.table)}</td><td>${escapeHtml(t.strategy)}</td><td>${t.source_rows}</td></tr>`).join('');
        const warnings = preview.warnings.map((w) => `<li>${escapeHtml(w)}</li>`).join('');
        const blockers = preview.blockers.map((b) => `<li class="text-danger">${escapeHtml(b)}</li>`).join('');
        resultEl.hidden = false;
        resultEl.innerHTML = `
            <p>共 ${preview.total_source_rows} 行数据将迁入主课堂「${escapeHtml(preview.target.class_name)}」。</p>
            ${blockers ? `<ul>${blockers}</ul><p class="text-danger">存在阻断项，无法执行。</p>` : ''}
            ${warnings ? `<ul>${warnings}</ul>` : ''}
            <details><summary>各表迁移明细（${preview.tables.length} 张表）</summary>
                <table class="offering-merge-table"><thead><tr><th>表</th><th>策略</th><th>迁移行数</th></tr></thead>
                <tbody>${tableRows}</tbody></table>
            </details>
            ${preview.can_execute ? `
            <div class="offering-merge-confirm">
                <input type="text" class="form-control" data-merge-confirm-input
                       placeholder="输入主课堂班级名「${escapeHtml(targetName)}」确认">
                <label class="offering-merge-ack"><input type="checkbox" data-merge-ack> 我已知晓该操作不可逆（已生成数据快照兜底）</label>
                <button type="button" class="btn btn-sm text-danger" data-merge-execute>确认合并（不可逆）</button>
            </div>` : ''}
        `;
    } catch (error) {
        showMessage(error.message || '合并预检失败', 'error');
    }
}

async function handleMergeExecute(groupEl, candidates) {
    const { targetId, sourceIds } = mergeGroupState(groupEl, candidates);
    const confirmInput = groupEl.querySelector('[data-merge-confirm-input]');
    const ack = groupEl.querySelector('[data-merge-ack]');
    if (!ack?.checked) {
        showMessage('请先勾选"我已知晓该操作不可逆"。', 'error');
        return;
    }
    const button = groupEl.querySelector('[data-merge-execute]');
    button.disabled = true;
    button.textContent = '合并中...';
    try {
        const result = await apiFetch('/api/manage/class_offerings/merge/execute', {
            method: 'POST',
            body: {
                target_offering_id: targetId,
                source_offering_ids: sourceIds,
                confirm_class_name: confirmInput?.value || '',
            },
        });
        showMessage(result.message || '合并完成', 'success');
        window.setTimeout(() => window.location.reload(), 1200);
    } catch (error) {
        showMessage(error.message || '合并失败，已整体回滚', 'error');
        button.disabled = false;
        button.textContent = '确认合并（不可逆）';
    }
}

async function initMergeWizard() {
    if (!mergeSection || !mergeContainer) return;
    let candidates = [];
    try {
        const data = await apiFetch('/api/manage/class_offerings/merge/candidates', { silent: true });
        candidates = data.candidates || [];
    } catch (error) {
        return; // 检测失败静默：不影响开课主流程
    }
    if (!candidates.length) return;
    renderMergeCandidates(candidates);
    mergeContainer.addEventListener('click', (event) => {
        const groupEl = event.target.closest('[data-merge-group]');
        if (!groupEl) return;
        if (event.target.closest('[data-merge-preview]')) {
            handleMergePreview(groupEl, candidates);
        } else if (event.target.closest('[data-merge-execute]')) {
            handleMergeExecute(groupEl, candidates);
        }
    });
}

async function initBootstrapPanel() {
    const container = document.getElementById('offeringBootstrapSection');
    const semesterId = Number(elements.semesterSelect?.value || config.defaultSemesterId || 0);
    if (!container || !semesterId) return;
    try {
        const { mountOfferingBootstrap } = await import('/static/js/offering_bootstrap.js?v=20260831-obs');
        await mountOfferingBootstrap(container, { semesterId, variant: 'page' });
    } catch (error) {
        // 检测失败静默：不影响开课主流程
    }
}

bindEvents();
resetForm();
applyQueryDefaults();
renderCourseSummary();
schedulePreviewRefresh();
initMergeWizard();
initBootstrapPanel();
