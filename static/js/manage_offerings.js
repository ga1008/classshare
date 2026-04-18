import { apiFetch } from '/static/js/api.js';
import { showMessage } from '/static/js/ui.js';

const config = window.OFFERINGS_PAGE_DATA || {};
const classMap = new Map((config.classes || []).map((item) => [Number(item.id), item]));
const courseMap = new Map((config.courses || []).map((item) => [Number(item.id), item]));
const semesterMap = new Map((config.semesters || []).map((item) => [Number(item.id), item]));
const textbookMap = new Map((config.textbooks || []).map((item) => [Number(item.id), item]));

const elements = {
    form: document.getElementById('offeringCreateForm'),
    submitBtn: document.getElementById('offeringSubmitBtn'),
    semesterSelect: document.getElementById('offeringSemesterSelect'),
    classSelect: document.getElementById('offeringClassSelect'),
    courseSelect: document.getElementById('offeringCourseSelect'),
    textbookSelect: document.getElementById('offeringTextbookSelect'),
    previewText: document.getElementById('offeringPreviewText'),
    offeringList: document.getElementById('offeringList'),
};

function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function getSelectedEntities() {
    const semester = semesterMap.get(Number(elements.semesterSelect?.value || 0)) || null;
    const classItem = classMap.get(Number(elements.classSelect?.value || 0)) || null;
    const course = courseMap.get(Number(elements.courseSelect?.value || 0)) || null;
    const textbook = textbookMap.get(Number(elements.textbookSelect?.value || 0)) || null;
    return { semester, classItem, course, textbook };
}

function updatePreview() {
    if (!elements.previewText) return;
    const missingResources = [];
    if (!semesterMap.size) missingResources.push('学期');
    if (!classMap.size) missingResources.push('班级');
    if (!courseMap.size) missingResources.push('课程');
    if (!textbookMap.size) missingResources.push('教材');
    if (missingResources.length > 0) {
        elements.previewText.textContent = `当前还缺少以下前置资源：${missingResources.join('、')}。请先去对应页面补齐，再回来开设课堂。`;
        return;
    }

    const { semester, classItem, course, textbook } = getSelectedEntities();

    if (!semester || !classItem || !course || !textbook) {
        elements.previewText.textContent = '请选择学期、班级、课程和教材。';
        return;
    }

    const lines = [
        `课堂名称：${course.name} / ${classItem.name}`,
        `学期：${semester.name}`,
        `时间范围：${semester.start_date} 至 ${semester.end_date}`,
        `教材：${textbook.title}`,
        `作者：${textbook.author_display || '未填写作者'}`,
    ];
    if (textbook.publisher) {
        lines.push(`出版社：${textbook.publisher}`);
    }
    if (textbook.publication_year) {
        lines.push(`出版年份：${textbook.publication_year}`);
    }
    elements.previewText.textContent = lines.join('\n');
}

function updateSubmitAvailability() {
    if (!elements.submitBtn) return;
    const missingResources = [];
    if (!semesterMap.size) missingResources.push('学期');
    if (!classMap.size) missingResources.push('班级');
    if (!courseMap.size) missingResources.push('课程');
    if (!textbookMap.size) missingResources.push('教材');

    elements.submitBtn.disabled = missingResources.length > 0;
}

async function handleSubmit(event) {
    event.preventDefault();
    if (!elements.form || !elements.submitBtn) return;

    const { semester, classItem, course, textbook } = getSelectedEntities();
    if (!semester || !classItem || !course || !textbook) {
        showMessage('请完整选择学期、班级、课程和教材', 'warning');
        return;
    }

    const formData = new FormData(elements.form);
    const originalText = elements.submitBtn.textContent;
    elements.submitBtn.disabled = true;
    elements.submitBtn.textContent = '正在开设...';

    try {
        const result = await apiFetch(elements.form.action, {
            method: 'POST',
            body: formData,
        });
        showMessage(result.message || '课堂已开设', 'success');
        window.location.reload();
    } catch (error) {
        showMessage(error.message || '开设课堂失败', 'error');
    } finally {
        elements.submitBtn.disabled = false;
        elements.submitBtn.textContent = originalText;
        updateSubmitAvailability();
    }
}

async function handleDelete(event) {
    const button = event.target.closest('[data-action="delete"]');
    if (!button) return;
    const offeringId = Number(button.dataset.offeringId || 0);
    const offeringName = button.dataset.offeringName || '当前课堂';
    if (!offeringId) return;

    const confirmed = window.confirm(`确定删除课堂“${offeringName}”吗？\n这会同时删除该课堂的 AI 配置和聊天记录。`);
    if (!confirmed) return;

    const result = await apiFetch(`/api/manage/class_offerings/${offeringId}`, { method: 'DELETE' });
    showMessage(result.message || '课堂已删除', 'success');
    const item = document.getElementById(`offering-item-${offeringId}`);
    if (item) {
        item.remove();
    }
    setTimeout(() => window.location.reload(), 400);
}

function applyInitialDefaults() {
    if (elements.semesterSelect && config.defaultSemesterId) {
        elements.semesterSelect.value = String(config.defaultSemesterId);
    } else if (elements.semesterSelect && !elements.semesterSelect.value && elements.semesterSelect.options.length > 1) {
        elements.semesterSelect.selectedIndex = 1;
    }

    if (elements.textbookSelect && !elements.textbookSelect.value && elements.textbookSelect.options.length > 1) {
        elements.textbookSelect.selectedIndex = 1;
    }
}

function initEvents() {
    [elements.semesterSelect, elements.classSelect, elements.courseSelect, elements.textbookSelect]
        .filter(Boolean)
        .forEach((select) => select.addEventListener('change', updatePreview));

    elements.form?.addEventListener('submit', handleSubmit);
    elements.offeringList?.addEventListener('click', handleDelete);
}

applyInitialDefaults();
updateSubmitAvailability();
updatePreview();
initEvents();
