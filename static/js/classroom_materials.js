import { apiFetch } from './api.js';
import { escapeHtml, formatSize, getFileIcon, showToast } from './ui.js';
import { enhancePromptPoolInput, enhancePromptPoolInputs, recordPromptForInput } from './prompt_pool.js';
import {
    bindProcessMaterialExportDownloadActions,
    startProcessMaterialExportDownloadFromTrigger,
} from './process_material_editor_preview.js';
import {
    getLearningDocumentUrl,
    getMaterialPrimaryAction,
    getMaterialTypeLabel,
    getRenderLabel,
    getRenderUrl,
    getRepositoryVisualMeta,
    hasLearningDocument,
    isGitRepository,
    isRenderable,
} from './materials_common.js';

let config = null;

const state = {
    currentParentId: null,
    breadcrumbs: [],
    history: [],
    items: [],
    selectedIds: new Set(),
    detailItem: null,
    detailPreview: null,
    detailExportUrl: '',
    detailExportPdfUrl: '',
    ordinaryGradeCandidates: [],
    ordinaryGradeCandidatesLoaded: false,
    ordinaryGradeCandidatesLoading: false,
    ordinaryGradeCandidatesReloadPending: false,
    ordinaryGradeCandidatesError: '',
    ordinaryGradeAttendanceFreshness: {},
    ordinaryGradeActiveStep: 0,
    examGradeCandidates: [],
    examGradeCandidatesLoaded: false,
    examGradeCandidatesLoading: false,
    examGradeCandidatesError: '',
    finalMaterialPrerequisites: {},
    finalMaterialPrerequisitesLoaded: false,
    finalMaterialPrerequisitesLoading: false,
    finalMaterialPrerequisitesError: '',
    finalMaterialBusy: false,
};

function withClassroomLearningContext(urlText) {
    const raw = String(urlText || '').trim();
    if (!raw) return '';
    try {
        const url = new URL(raw, window.location.origin);
        if (config?.classOfferingId) {
            url.searchParams.set('class_offering_id', String(config.classOfferingId));
        }
        return url.pathname + url.search + url.hash;
    } catch {
        return raw;
    }
}

function refs() {
    return {
        list: document.getElementById('classroom-materials-list'),
        breadcrumbs: document.getElementById('classroom-materials-breadcrumbs'),
        backBtn: document.getElementById('classroom-materials-back-btn'),
        upBtn: document.getElementById('classroom-materials-up-btn'),
        refreshBtn: document.getElementById('classroom-materials-refresh-btn'),
        generateBtn: document.getElementById('classroom-final-material-generate-btn'),
        selectionBar: document.getElementById('classroom-materials-selection'),
        selectionCount: document.getElementById('classroom-materials-selection-count'),
        selectAll: document.getElementById('classroom-materials-select-all'),
        selectionDownloadBtn: document.getElementById('classroom-materials-download-btn'),
        detailModal: document.getElementById('classroom-material-detail-modal'),
        detailTitle: document.getElementById('classroom-material-detail-title'),
        detailKicker: document.getElementById('classroom-material-detail-kicker'),
        detailPath: document.getElementById('classroom-material-detail-path'),
        detailLoading: document.getElementById('classroom-material-detail-loading'),
        detailContent: document.getElementById('classroom-material-detail-content'),
        detailOpenBtn: document.getElementById('classroom-material-detail-open-btn'),
        detailDownloadBtn: document.getElementById('classroom-material-detail-download-btn'),
        detailExportBtn: document.getElementById('classroom-material-detail-export-btn'),
        detailExportPdfBtn: document.getElementById('classroom-material-detail-export-pdf-btn'),
        finalMaterialModal: document.getElementById('classroom-final-material-modal'),
        finalMaterialType: document.getElementById('classroom-final-material-type'),
        examPaperOptions: document.getElementById('classroom-exam-paper-options'),
        assessmentPlanOptions: document.getElementById('classroom-assessment-plan-options'),
        gradingRubricOptions: document.getElementById('classroom-grading-rubric-options'),
        ordinaryGradeOptions: document.getElementById('classroom-ordinary-grade-record-options'),
        ordinaryGradeStatus: document.getElementById('classroom-ordinary-grade-record-status'),
        ordinaryAttendanceFreshness: document.getElementById('classroom-ordinary-attendance-freshness'),
        ordinaryGradePicker: document.getElementById('classroom-ordinary-grade-picker'),
        ordinaryGradePickerKicker: document.getElementById('classroom-ordinary-grade-picker-kicker'),
        ordinaryGradePickerTitle: document.getElementById('classroom-ordinary-grade-picker-title'),
        ordinaryGradePickerSearch: document.getElementById('classroom-ordinary-grade-picker-search'),
        ordinaryGradePickerList: document.getElementById('classroom-ordinary-grade-picker-list'),
        ordinaryScoreFloorEnabled: document.getElementById('classroom-ordinary-score-floor-enabled'),
        ordinaryScoreFloorInput: document.getElementById('classroom-ordinary-score-floor'),
        ordinaryScoreFloorSummary: document.getElementById('classroom-ordinary-score-floor-summary'),
        ordinaryGradeStepCards: Array.from(document.querySelectorAll('[data-ordinary-grade-step-index]')),
        ordinaryGradeSelectionDetails: Array.from(document.querySelectorAll('[data-ordinary-grade-selection-detail]')),
        ordinaryGradeProgressSteps: Array.from(document.querySelectorAll('[data-ordinary-progress-step]')),
        examGradeOptions: document.getElementById('classroom-exam-grade-record-options'),
        examGradeSelect: document.getElementById('classroom-exam-grade-record-assignment'),
        examGradeStatus: document.getElementById('classroom-exam-grade-record-status'),
        ordinaryHomeworkSelects: [
            document.getElementById('classroom-ordinary-homework-1'),
            document.getElementById('classroom-ordinary-homework-2'),
            document.getElementById('classroom-ordinary-homework-3'),
        ],
        ordinaryAssessmentSelect: document.getElementById('classroom-ordinary-assessment'),
        finalMaterialAssessmentMode: document.getElementById('classroom-final-material-assessment-mode'),
        finalMaterialAssessmentMethod: document.getElementById('classroom-final-material-assessment-method'),
        finalMaterialPrompt: document.getElementById('classroom-final-material-prompt'),
        finalMaterialPromptGroup: document.getElementById('classroom-final-material-prompt-group'),
        finalMaterialPromptStep: document.getElementById('classroom-final-material-prompt-step'),
        finalMaterialPromptLabel: document.getElementById('classroom-final-material-prompt-label'),
        finalMaterialSubmitBtn: document.getElementById('classroom-final-material-submit-btn'),
        finalMaterialStatus: document.getElementById('classroom-final-material-status'),
    };
}

function isTeacher() {
    return config?.canGenerateFinalMaterials || config?.userRole === 'teacher' || config?.userInfo?.role === 'teacher';
}

function openModal(modal) {
    if (!modal) return;
    if (modal.id === 'classroom-material-detail-modal' && document.body.classList.contains('classroom-workspace-v2')) {
        document.dispatchEvent(new CustomEvent('classroom:workspace-panel', { detail: { panel: 'material-detail' } }));
        return;
    }
    modal.style.display = 'flex';
    modal.setAttribute('aria-hidden', 'false');
    modal.classList.add('show');
    document.body.classList.add('modal-open');
}

function closeModal(modal) {
    if (!modal) return;
    if (modal.id === 'classroom-material-detail-modal' && document.body.classList.contains('classroom-workspace-v2')) {
        document.dispatchEvent(new CustomEvent('classroom:workspace-panel', { detail: { back: true } }));
        return;
    }
    modal.style.display = 'none';
    modal.setAttribute('aria-hidden', 'true');
    modal.classList.remove('show');
    document.body.classList.remove('modal-open');
}

function getMetaText(item) {
    if (!item) return '--';
    if (item.node_type === 'folder') {
        return `${item.child_count || 0} 个子项`;
    }
    return formatSize(item.file_size || 0);
}

function getVisualMeta(item) {
    const repositoryMeta = getRepositoryVisualMeta(item);
    if (repositoryMeta) {
        return {
            color: repositoryMeta.color,
            label: repositoryMeta.icon,
            badge: repositoryMeta.badge,
        };
    }
    if (item.node_type === 'folder') {
        return { color: '#0ea5e9', label: 'DIR', badge: '' };
    }
    const fileMeta = getFileIcon(item.name || 'file');
    return { color: fileMeta.color, label: fileMeta.label, badge: '' };
}

function getDownloadAction(item) {
    if (item.node_type !== 'file') {
        return '';
    }

    if (item.download_allowed !== false) {
        return '<button type="button" class="btn btn-ghost btn-sm" data-action="download">下载</button>';
    }

    const title = escapeHtml(item.download_blocked_reason || '已限制下载');
    return `
        <button
            type="button"
            class="btn btn-ghost btn-sm resource-download-blocked"
            data-action="download-blocked"
            title="${title}"
            aria-label="${title}"
        >
            <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <circle cx="12" cy="12" r="9"></circle>
                <path d="M5 5l14 14"></path>
            </svg>
        </button>
    `;
}

function updateSelectionBar() {
    const el = refs().selectionBar;
    if (!el) return;
    const count = state.selectedIds.size;
    el.hidden = count === 0;
    refs().selectionCount.textContent = String(count);
    const selectAll = refs().selectAll;
    if (selectAll) {
        selectAll.disabled = state.items.length === 0;
        selectAll.checked = state.items.length > 0 && count === state.items.length;
        selectAll.indeterminate = count > 0 && count < state.items.length;
    }
}

function renderBreadcrumbs() {
    const container = refs().breadcrumbs;
    if (!container) return;
    if (!state.breadcrumbs.length) {
        container.innerHTML = '<span class="text-muted">已分配材料</span>';
        return;
    }
    container.innerHTML = state.breadcrumbs.map((crumb, index) => `
        ${index > 0 ? '<span class="separator">/</span>' : ''}
        <button type="button" data-crumb-id="${crumb.id}">${escapeHtml(crumb.name)}</button>
    `).join('');
}

function renderList() {
    const container = refs().list;
    if (!container) return;
    if (!state.items.length) {
        container.innerHTML = '<div class="materials-empty">当前课堂还没有分配课程材料。</div>';
        updateSelectionBar();
        return;
    }

    container.innerHTML = state.items.map((item) => {
        const visualMeta = getVisualMeta(item);
        const primaryAction = getMaterialPrimaryAction(item);
        const documentAction = hasLearningDocument(item)
            ? '<button type="button" class="btn btn-outline btn-sm" data-action="view-doc">文档</button>'
            : '';
        const renderAction = isRenderable(item)
            ? `<button type="button" class="btn btn-outline btn-sm materials-render-btn" data-action="render">${escapeHtml(getRenderLabel(item))}</button>`
            : '';
        const repositoryBadge = isGitRepository(item)
            ? `<span class="materials-repo-badge" style="--repo-color:${visualMeta.color};">${escapeHtml(visualMeta.badge)}</span>`
            : '';

        return `
            <div
                class="materials-row"
                data-id="${item.id}"
                data-material-node-type="${escapeHtml(item.node_type || '')}"
                data-material-name="${escapeHtml(item.name || '')}"
                data-material-path="${escapeHtml(item.material_path || '')}"
                data-material-preview-supported="${item.preview_supported ? 'true' : 'false'}"
                data-material-download-allowed="${item.download_allowed === false ? 'false' : 'true'}"
                data-material-has-document="${hasLearningDocument(item) ? 'true' : 'false'}"
                data-material-primary-action="${escapeHtml(primaryAction.action || '')}"
            >
                <div>
                    <input type="checkbox" data-role="select-item" data-id="${item.id}" aria-label="${escapeHtml(`选择材料：${item.name || '未命名材料'}`)}" ${state.selectedIds.has(item.id) ? 'checked' : ''}>
                </div>
                <div class="materials-name-cell">
                    <div class="materials-type-icon" style="background:${visualMeta.color}16;color:${visualMeta.color};">${escapeHtml(visualMeta.label)}</div>
                    <div class="materials-name-copy">
                        <strong>${escapeHtml(item.name)}</strong>
                        <div class="materials-name-badges">${repositoryBadge}</div>
                        <span>${escapeHtml(item.material_path || '')}</span>
                    </div>
                </div>
                <div>${escapeHtml(getMaterialTypeLabel(item))}</div>
                <div>${escapeHtml(getMetaText(item))}</div>
                <div class="materials-row-actions">
                    <button type="button" class="btn btn-ghost btn-sm" data-action="${primaryAction.action}">
                        ${primaryAction.label}
                    </button>
                    ${renderAction}
                    ${documentAction}
                    ${getDownloadAction(item)}
                </div>
            </div>
        `;
    }).join('');

    updateSelectionBar();
}

function compactValue(value) {
    if (value === null || value === undefined || value === '') return '未填写';
    if (Array.isArray(value)) return value.map((item) => compactValue(item)).join('、');
    if (typeof value === 'object') return JSON.stringify(value);
    return String(value);
}

function compactDateTime(value) {
    const text = String(value || '').trim();
    if (!text) return '';
    return text.replace('T', ' ').slice(0, 16);
}

function getOpenUrl(item) {
    if (!item) return '';
    if (item.node_type === 'folder') return '';
    if (item.preview_supported) return withClassroomLearningContext(`/materials/view/${item.id}`);
    if (item.download_allowed !== false) return `/materials/download/${item.id}`;
    return '';
}

function renderFields(fields = {}) {
    const labels = {
        course_name: '课程',
        class_name: '班级',
        teacher_name: '教师',
        examiner_name: '命题教师',
        reviewer_name: '审核人',
        leader_name: '主管领导',
        academic_year: '学年',
        semester: '学期',
        assessment_type: '考核类型',
        assessment_mode_label: '笔试/非笔试',
        assessment_method: '考核形式',
        education_level: '学历层次',
        paper_type: '试卷类型',
        exam_flags: '考试标记',
        source_assessment_plan_title: '来源考核计划表',
        source_exam_paper_title: '来源试卷',
        source_homework_titles: '平时作业来源',
        source_assessment_title: '测评来源',
        class_size: '班级人数',
        exam_duration: '考试时间',
        total_score: '总分',
        date: '日期',
    };
    const entries = Object.entries(labels)
        .map(([key, label]) => [label, fields[key]])
        .filter(([, value]) => value !== undefined && value !== null && value !== '');
    if (!entries.length) {
        return '<p class="text-muted text-sm">暂未识别到可替换字段。</p>';
    }
    return `<div class="classroom-material-field-grid">${entries.map(([label, value]) => `
        <div class="classroom-material-field">
            <span>${escapeHtml(label)}</span>
            <strong>${escapeHtml(compactValue(value))}</strong>
        </div>
    `).join('')}</div>`;
}

function renderStructuredSummary(preview) {
    const structured = preview?.structured || {};
    const type = preview?.document_type || '';
    if (type === 'assessment_plan') {
        const items = Array.isArray(structured.assessment_items) ? structured.assessment_items : [];
        return `
            <div class="classroom-material-preview-list">
                ${items.map((item) => `
                    <div>
                        <strong>${escapeHtml(compactValue(item.assessment_form || item.form || '考核'))}</strong>
                        <span>${escapeHtml(compactValue(item.content || item.assessment_content || ''))}</span>
                        <em>${escapeHtml(compactValue(item.score || ''))}分</em>
                    </div>
                `).join('') || '<p class="text-muted text-sm">暂无考核项目。</p>'}
            </div>
        `;
    }
    if (type === 'grading_rubric') {
        const items = Array.isArray(structured.rubric_items) ? structured.rubric_items : [];
        return `
            <div class="classroom-material-preview-list">
                ${items.slice(0, 8).map((item) => `
                    <div>
                        <strong>${escapeHtml(compactValue(item.title || '评分项'))}</strong>
                        <span>${escapeHtml(compactValue((item.criteria || []).map((criterion) => criterion.text || criterion).join('；')).slice(0, 140))}</span>
                        <em>${escapeHtml(compactValue(item.score || ''))}分</em>
                    </div>
                `).join('') || '<p class="text-muted text-sm">暂无评分细则摘要。</p>'}
            </div>
        `;
    }
    if (type === 'exam_paper') {
        const sections = Array.isArray(structured.paper_sections) ? structured.paper_sections : [];
        return `
            <div class="classroom-material-preview-list">
                ${sections.map((section) => `
                    <div>
                        <strong>${escapeHtml(compactValue(section.title || '试题'))}</strong>
                        <span>${escapeHtml(compactValue(section.content || '').slice(0, 160))}</span>
                        <em>${escapeHtml(compactValue(section.score || ''))}分</em>
                    </div>
                `).join('') || '<p class="text-muted text-sm">暂无试卷题目摘要。</p>'}
            </div>
        `;
    }
    if (type === 'ordinary_grade_record') {
        const students = Array.isArray(structured.students) ? structured.students : [];
        const source = structured.source_assignments || {};
        const homework = Array.isArray(source.homework_assignments) ? source.homework_assignments : [];
        const assessment = source.assessment_assignment || {};
        return `
            <div class="classroom-material-preview-list">
                <div>
                    <strong>学生</strong>
                    <span>${escapeHtml(compactValue(students.length))} 人，按每 25 人一版生成 Excel。</span>
                    <em>A4</em>
                </div>
                <div>
                    <strong>作业</strong>
                    <span>${escapeHtml(homework.map((item) => item.title || '').filter(Boolean).join('；') || '未绑定')}</span>
                    <em>3次</em>
                </div>
                <div>
                    <strong>测评</strong>
                    <span>${escapeHtml(assessment.title || '未绑定')}</span>
                    <em>1次</em>
                </div>
            </div>
        `;
    }
    if (type === 'exam_grade_record') {
        const students = Array.isArray(structured.students) ? structured.students : [];
        const sections = Array.isArray(structured.sections) ? structured.sections : [];
        const source = structured.source_exam || {};
        return `
            <div class="classroom-material-preview-list">
                <div>
                    <strong>学生</strong>
                    <span>${escapeHtml(compactValue(students.length))} 人，按考试最终分生成 Excel。</span>
                    <em>A4</em>
                </div>
                <div>
                    <strong>大题</strong>
                    <span>${escapeHtml(sections.map((item) => `${item.label || ''}${item.full_score ? `(${item.full_score}分)` : ''}`).filter(Boolean).join('；') || '未识别')}</span>
                    <em>${escapeHtml(compactValue(sections.length))}列</em>
                </div>
                <div>
                    <strong>考试</strong>
                    <span>${escapeHtml(source.assignment_title || source.exam_paper_title || '未绑定')}</span>
                    <em>${escapeHtml(compactValue(source.total_score || ''))}分</em>
                </div>
            </div>
        `;
    }
    return '<p class="text-muted text-sm">这份材料暂未绑定期末材料模板。</p>';
}

function renderClassroomAiImportSummary(material, preview = null) {
    const record = material?.ai_import_record || null;
    const summary = record?.summary || null;
    if (!record || !summary) return '';
    const fieldItems = Array.isArray(summary.field_items) ? summary.field_items : [];
    const previewWarnings = Array.isArray(preview?.warnings) ? preview.warnings : [];
    const summaryWarnings = Array.isArray(summary.warnings) ? summary.warnings : [];
    const warnings = summaryWarnings.length ? summaryWarnings : previewWarnings.slice(0, 3);
    const warningCount = Number(summary.warning_count || previewWarnings.length || warnings.length || 0);
    const qualityStatus = String(summary.content_quality_status || '').toLowerCase();
    const warningTone = warningCount > 0 || ['failed', 'suspect', 'empty', 'too_short'].includes(qualityStatus) ? 'is-warning' : 'is-ok';
    const exportFormats = Array.isArray(summary.export_formats) && summary.export_formats.length
        ? summary.export_formats.join(' / ')
        : (isExcelFinalMaterial(material, preview) ? 'Excel' : (record.export_pdf_url ? 'Word / PDF' : 'Word'));
    const sourceLabel = [summary.parse_mode_label || record.parse_mode || '导入解析', summary.source_file_name || '']
        .filter(Boolean)
        .join(' · ');
    const updatedAt = compactDateTime(summary.updated_at || record.completed_at || record.updated_at);
    const renderPreviewUrl = preview?.render_preview_url || record.render_preview_url || '';
    const exportUrl = preview?.export_url || record.export_url || '';
    const exportPdfUrl = preview?.export_pdf_url || record.export_pdf_url || '';
    const exportLabel = isExcelFinalMaterial(material, preview) ? '导出 Excel' : '导出 Word';
    const exportDownloadLabel = isExcelFinalMaterial(material, preview) ? 'Excel' : 'Word';
    const fieldsHtml = fieldItems.length
        ? fieldItems.map((item) => `
            <div>
                <span>${escapeHtml(item.label || item.key || '字段')}</span>
                <strong title="${escapeHtml(item.value || '')}">${escapeHtml(item.value || '-')}</strong>
            </div>
        `).join('')
        : '<p class="materials-ai-import-summary-empty">暂无可展示的关键字段。</p>';
    const warningsHtml = warningCount > 0
        ? `
            <ul class="materials-ai-import-summary-warnings">
                ${warnings.map((item) => `<li>${escapeHtml(item)}</li>`).join('')}
                ${summary.has_more_warnings || warningCount > warnings.length ? '<li>还有更多警告，请打开渲染预览或导出文档核对。</li>' : ''}
            </ul>
        `
        : '<p class="materials-ai-import-summary-empty">未记录解析警告。</p>';

    return `
        <section class="materials-ai-import-summary classroom-material-ai-import-summary">
            <div class="materials-ai-import-summary-head">
                <div>
                    <span>过程材料解析结果</span>
                    <strong>${escapeHtml(summary.document_type_label || record.document_type_label || '过程材料')}</strong>
                </div>
                <em class="${warningTone}">${warningCount > 0 ? `${escapeHtml(String(warningCount))} 条警告` : '可导出'}</em>
            </div>
            <div class="materials-ai-import-summary-meta">
                <div><span>来源</span><strong>${escapeHtml(sourceLabel)}</strong></div>
                <div><span>格式</span><strong>${escapeHtml(exportFormats)}</strong></div>
                <div><span>质量</span><strong>${escapeHtml(summary.content_quality_label || '未校验')}</strong></div>
                <div><span>完成</span><strong>${escapeHtml(updatedAt || '--')}</strong></div>
            </div>
            <div class="materials-ai-import-summary-fields">
                ${fieldsHtml}
            </div>
            <details class="materials-ai-import-summary-detail" ${warningCount > 0 ? 'open' : ''}>
                <summary>解析警告与核对点</summary>
                ${warningsHtml}
            </details>
            <div class="materials-ai-import-summary-actions">
                ${renderPreviewUrl ? `<a href="${escapeHtml(renderPreviewUrl)}" class="btn btn-outline btn-sm" target="_blank" rel="noopener">渲染预览</a>` : ''}
                ${exportUrl ? `<button type="button" class="btn btn-outline btn-sm" data-process-export-url="${escapeHtml(exportUrl)}" data-process-export-label="${escapeHtml(exportDownloadLabel)}">${escapeHtml(exportLabel)}</button>` : ''}
                ${exportPdfUrl ? `<button type="button" class="btn btn-outline btn-sm" data-process-export-url="${escapeHtml(exportPdfUrl)}" data-process-export-label="PDF">导出 PDF</button>` : ''}
            </div>
        </section>
    `;
}

function renderDetailContent(material, preview = null) {
    const aiRecord = material.ai_import_record || null;
    const renderPreviewUrl = preview?.render_preview_url || aiRecord?.render_preview_url || '';
    const renderPreviewAction = renderPreviewUrl
        ? `<a class="btn btn-outline btn-sm" href="${escapeHtml(renderPreviewUrl)}" target="_blank" rel="noopener">渲染预览</a>`
        : '';
    const metaRows = [
        ['类型', getMaterialTypeLabel(material)],
        ['大小', getMetaText(material)],
        ['更新时间', material.updated_at || '--'],
        ['材料路径', material.material_path || '--'],
    ];
    const aiBlock = preview ? `
        <section class="classroom-material-detail-section">
            <div class="classroom-material-detail-section-head">
                <span>导出预览</span>
                <strong>${escapeHtml(preview.document_type_label || '期末材料')}</strong>
                ${renderPreviewAction}
            </div>
            ${renderFields(preview.fields || {})}
            ${renderStructuredSummary(preview)}
            <details class="classroom-material-markdown-preview">
                <summary>查看解析正文</summary>
                <pre>${escapeHtml(preview.content_markdown || '暂无正文')}</pre>
            </details>
        </section>
    ` : `
        <section class="classroom-material-detail-section">
            <div class="classroom-material-empty-hint">
                <strong>暂无期末材料导出数据</strong>
                <span>${isTeacher() ? '可在管理中心执行 AI 导入解析，或在本课堂顶部直接 AI 生成期末材料。' : '教师尚未为这份材料生成导出数据。'}</span>
            </div>
        </section>
    `;
    const optimizeBlock = isTeacher() && aiRecord ? `
        <section class="classroom-material-detail-section">
            <div class="classroom-material-detail-section-head">
                <span>AI优化</span>
                <strong>字段、内容与导出结构</strong>
            </div>
            <textarea class="classroom-material-ai-prompt" data-role="final-material-optimize-prompt" data-prompt-pool-key="classroom.final_material_optimize" rows="4" placeholder="例如：补齐审核人、考试时间，细化评分细则，保持总分100分。"></textarea>
            <div class="classroom-material-inline-actions">
                <button type="button" class="btn btn-primary btn-sm" data-action="optimize-final-material">AI优化并保存</button>
            </div>
        </section>
    ` : '';
    const aiSummaryBlock = renderClassroomAiImportSummary(material, preview);

    return `
        <section class="classroom-material-detail-section">
            <div class="classroom-material-meta-grid">
                ${metaRows.map(([label, value]) => `
                    <div>
                        <span>${escapeHtml(label)}</span>
                        <strong>${escapeHtml(compactValue(value))}</strong>
                    </div>
                `).join('')}
            </div>
        </section>
        ${aiSummaryBlock}
        ${aiBlock}
        ${optimizeBlock}
    `;
}

function isExcelFinalMaterial(material = null, preview = null) {
    const type = preview?.document_type || material?.ai_import_record?.document_type || '';
    return type === 'ordinary_grade_record'
        || type === 'exam_grade_record'
        || type === 'final_grade_transcript';
}

function setDetailExportButtons(material = null, preview = null) {
    const dom = refs();
    if (dom.detailExportBtn) {
        dom.detailExportBtn.textContent = isExcelFinalMaterial(material, preview) ? '导出Excel' : '导出Word';
        dom.detailExportBtn.dataset.processExportLabel = isExcelFinalMaterial(material, preview) ? 'Excel' : 'Word';
        if (state.detailExportUrl) {
            dom.detailExportBtn.dataset.processExportUrl = state.detailExportUrl;
        } else {
            delete dom.detailExportBtn.dataset.processExportUrl;
        }
    }
    if (dom.detailExportPdfBtn) {
        dom.detailExportPdfBtn.dataset.processExportLabel = 'PDF';
        if (state.detailExportPdfUrl) {
            dom.detailExportPdfBtn.dataset.processExportUrl = state.detailExportPdfUrl;
        } else {
            delete dom.detailExportPdfBtn.dataset.processExportUrl;
        }
    }
}

async function openMaterialDetail(materialId) {
    const dom = refs();
    const item = state.items.find((entry) => Number(entry.id) === Number(materialId));
    state.detailItem = item || null;
    state.detailPreview = null;
    state.detailExportUrl = item?.ai_import_record?.export_url || '';
    state.detailExportPdfUrl = item?.ai_import_record?.export_pdf_url || '';

    if (!isTeacher()) {
        const action = getMaterialPrimaryAction(item || {});
        if (action.action === 'open') {
            await loadMaterials(materialId, true);
        } else if (item?.preview_supported) {
            window.open(withClassroomLearningContext(`/materials/view/${materialId}`), '_blank', 'noopener');
        }
        return;
    }

    dom.detailTitle.textContent = item?.name || '材料详情';
    dom.detailKicker.textContent = item?.node_type === 'folder' ? '材料文件夹' : '课程材料';
    dom.detailPath.textContent = item?.material_path || '';
    dom.detailLoading.hidden = false;
    dom.detailContent.hidden = true;
    dom.detailContent.innerHTML = '';
    if (dom.detailExportBtn) dom.detailExportBtn.disabled = true;
    if (dom.detailExportPdfBtn) dom.detailExportPdfBtn.disabled = true;
    setDetailExportButtons(item, null);
    if (dom.detailOpenBtn) dom.detailOpenBtn.textContent = item?.node_type === 'folder' ? '打开文件夹' : '打开';
    if (dom.detailDownloadBtn) dom.detailDownloadBtn.disabled = !item || item.node_type !== 'file' || item.download_allowed === false;
    openModal(dom.detailModal);

    try {
        const detail = await apiFetch(`/api/materials/${materialId}`, { silent: true });
        const material = detail.material || item || {};
        state.detailItem = material;
        state.detailExportUrl = material.ai_import_record?.export_url || '';
        state.detailExportPdfUrl = material.ai_import_record?.export_pdf_url || '';
        let preview = null;
        if (material.ai_import_record?.preview_url) {
            try {
                const previewData = await apiFetch(material.ai_import_record.preview_url, { silent: true });
                preview = previewData.preview || null;
                state.detailPreview = preview;
                state.detailExportUrl = preview?.export_url || state.detailExportUrl;
                state.detailExportPdfUrl = preview?.export_pdf_url || state.detailExportPdfUrl;
            } catch (error) {
                console.warn('final material preview failed', error);
            }
        }
        dom.detailTitle.textContent = material.name || item?.name || '材料详情';
        dom.detailKicker.textContent = material.ai_import_record?.document_type_label || (material.node_type === 'folder' ? '材料文件夹' : '课程材料');
        dom.detailPath.textContent = material.material_path || '';
        dom.detailContent.innerHTML = renderDetailContent(material, preview);
        enhancePromptPoolInputs(dom.detailContent);
        dom.detailLoading.hidden = true;
        dom.detailContent.hidden = false;
        if (dom.detailOpenBtn) dom.detailOpenBtn.textContent = material.node_type === 'folder' ? '打开文件夹' : '打开';
        if (dom.detailDownloadBtn) dom.detailDownloadBtn.disabled = material.node_type !== 'file' || material.download_allowed === false;
        if (dom.detailExportBtn) dom.detailExportBtn.disabled = !state.detailExportUrl;
        if (dom.detailExportPdfBtn) dom.detailExportPdfBtn.disabled = !state.detailExportPdfUrl;
        setDetailExportButtons(material, preview);
    } catch (error) {
        dom.detailLoading.hidden = true;
        dom.detailContent.hidden = false;
        dom.detailContent.innerHTML = `<div class="materials-empty">加载材料详情失败：${escapeHtml(error.message || '未知错误')}</div>`;
    }
}

function ordinaryCandidateLabel(item) {
    const title = item?.title || `作业 ${item?.id || ''}`;
    const stats = `${item?.graded_count || 0}/${item?.submission_count || 0}`;
    const kind = item?.kind === 'exam' ? '测验' : '作业';
    const source = item?.ordinary_grade_kind_source === 'manual' ? '，手动指定' : '';
    const average = item?.average_score === null || item?.average_score === undefined ? '' : `，均分 ${item.average_score}`;
    return `${title}（${kind}${source}，已评分 ${stats}${average}）`;
}

function isOrdinaryAssessmentCandidate(item) {
    return item?.kind === 'exam';
}

function ordinaryGradeCandidateBuckets() {
    const candidates = Array.isArray(state.ordinaryGradeCandidates) ? state.ordinaryGradeCandidates : [];
    const assessment = candidates.filter(isOrdinaryAssessmentCandidate);
    const homework = candidates.filter((item) => !isOrdinaryAssessmentCandidate(item));
    return { homework, assessment };
}

function ordinaryOptionsHtml(items, placeholder = '请选择') {
    return [
        `<option value="">${escapeHtml(placeholder)}</option>`,
        ...items.map((item) => `<option value="${escapeHtml(String(item.id))}">${escapeHtml(ordinaryCandidateLabel(item))}</option>`),
    ].join('');
}

function ordinaryFuzzyText(value) {
    return String(value || '')
        .normalize('NFKC')
        .toLocaleLowerCase('zh-CN')
        .replace(/[\s·•—–_\-/（）()【】[\]，,。.：:；;]+/g, '');
}

function ordinaryFuzzyMatches(item, keyword) {
    const needle = ordinaryFuzzyText(keyword);
    if (!needle) return true;
    const source = ordinaryFuzzyText([
        item?.title,
        item?.kind === 'exam' ? '考试 测评' : '作业',
        item?.status,
        item?.graded_count,
        item?.submission_count,
        item?.average_score,
    ].filter((value) => value !== null && value !== undefined).join(' '));
    if (source.includes(needle)) return true;
    let cursor = 0;
    for (const char of source) {
        if (char === needle[cursor]) cursor += 1;
        if (cursor >= needle.length) return true;
    }
    return false;
}

function ordinaryGradeStepSelect(dom, stepIndex) {
    if (stepIndex >= 0 && stepIndex <= 2) return dom.ordinaryHomeworkSelects?.[stepIndex] || null;
    return stepIndex === 3 ? dom.ordinaryAssessmentSelect : null;
}

function ordinaryGradeSelectedIds(dom) {
    return [0, 1, 2, 3].map((stepIndex) => Number(ordinaryGradeStepSelect(dom, stepIndex)?.value || 0));
}

function ordinaryGradeCandidateById(candidateId) {
    return state.ordinaryGradeCandidates.find((item) => Number(item?.id || 0) === Number(candidateId || 0)) || null;
}

function ordinaryGradeStepCandidates(stepIndex) {
    const buckets = ordinaryGradeCandidateBuckets();
    return stepIndex === 3 ? buckets.assessment : buckets.homework;
}

function ordinaryGradeAttendanceLabel() {
    const freshness = state.ordinaryGradeAttendanceFreshness || {};
    if (freshness.is_fresh) {
        return `考勤已同步于 ${freshness.last_synced_at_display || '刚刚'}，生成时使用 30 分钟缓存`;
    }
    if (freshness.last_synced_at_display) {
        return `考勤上次同步于 ${freshness.last_synced_at_display}，生成前将自动刷新`;
    }
    return '考勤尚未同步，生成前将自动连接智慧课堂刷新';
}

function renderOrdinaryGradePicker() {
    const dom = refs();
    if (!dom.ordinaryGradePicker || dom.ordinaryGradePicker.hidden) return;
    const stepIndex = Number(state.ordinaryGradeActiveStep || 0);
    const keyword = dom.ordinaryGradePickerSearch?.value || '';
    const selectedIds = ordinaryGradeSelectedIds(dom);
    const usedByOtherStep = new Map();
    selectedIds.forEach((candidateId, index) => {
        if (candidateId > 0 && index !== stepIndex) usedByOtherStep.set(candidateId, index);
    });
    const items = ordinaryGradeStepCandidates(stepIndex).filter((item) => ordinaryFuzzyMatches(item, keyword));
    if (dom.ordinaryGradePickerKicker) dom.ordinaryGradePickerKicker.textContent = `第 ${stepIndex + 1} 步`;
    if (dom.ordinaryGradePickerTitle) {
        dom.ordinaryGradePickerTitle.textContent = stepIndex === 3 ? '选择课堂测评' : `选择平时作业 ${stepIndex + 1}`;
    }
    if (!dom.ordinaryGradePickerList) return;
    if (!items.length) {
        dom.ordinaryGradePickerList.innerHTML = `
            <div class="ordinary-grade-picker__empty">
                <strong>没有匹配的${stepIndex === 3 ? '测评' : '作业'}</strong>
                <span>可以清空关键词重试，或在课堂任务卡片中调整“平时成绩用途”。</span>
            </div>
        `;
        return;
    }
    dom.ordinaryGradePickerList.innerHTML = items.map((item) => {
        const candidateId = Number(item?.id || 0);
        const usedStep = usedByOtherStep.get(candidateId);
        const isCurrent = selectedIds[stepIndex] === candidateId;
        const disabled = usedStep !== undefined;
        const average = item?.average_score === null || item?.average_score === undefined
            ? '暂无均分'
            : `均分 ${item.average_score}`;
        const usage = disabled
            ? `已用于第 ${usedStep + 1} 步`
            : (isCurrent ? '当前已选择' : '选择此来源');
        return `
            <button
                type="button"
                class="ordinary-grade-candidate${isCurrent ? ' is-selected' : ''}${disabled ? ' is-disabled' : ''}"
                data-ordinary-grade-candidate-id="${escapeHtml(String(candidateId))}"
                ${disabled ? 'disabled' : ''}
            >
                <span class="ordinary-grade-candidate__main">
                    <strong>${escapeHtml(item?.title || `作业 ${candidateId}`)}</strong>
                    <small>
                        ${escapeHtml(item?.kind === 'exam' ? '测评 / 考试' : '平时作业')}
                        ${item?.ordinary_grade_kind_source === 'manual' ? ' · 手动指定' : ' · 自动识别'}
                        · 已评分 ${escapeHtml(String(item?.graded_count || 0))}/${escapeHtml(String(item?.submission_count || 0))}
                        · ${escapeHtml(average)}
                    </small>
                </span>
                <span class="ordinary-grade-candidate__usage">${escapeHtml(usage)}</span>
            </button>
        `;
    }).join('');
}

function renderOrdinaryGradeWizard() {
    const dom = refs();
    const selectedIds = ordinaryGradeSelectedIds(dom);
    syncOrdinaryGradeScoreFloorControls(dom);
    if (dom.ordinaryAttendanceFreshness) {
        dom.ordinaryAttendanceFreshness.textContent = ordinaryGradeAttendanceLabel();
        dom.ordinaryAttendanceFreshness.classList.toggle('is-fresh', Boolean(state.ordinaryGradeAttendanceFreshness?.is_fresh));
    }
    dom.ordinaryGradeStepCards?.forEach((card, stepIndex) => {
        const candidate = ordinaryGradeCandidateById(selectedIds[stepIndex]);
        const detail = dom.ordinaryGradeSelectionDetails?.[stepIndex];
        const action = card.querySelector('.ordinary-grade-step-card__action');
        card.classList.toggle('is-selected', Boolean(candidate));
        card.classList.toggle('is-active', !dom.ordinaryGradePicker?.hidden && state.ordinaryGradeActiveStep === stepIndex);
        if (detail) {
            detail.textContent = candidate
                ? `${candidate.title} · 已评分 ${candidate.graded_count || 0}/${candidate.submission_count || 0}${candidate.average_score === null || candidate.average_score === undefined ? '' : ` · 均分 ${candidate.average_score}`}`
                : (stepIndex === 3 ? '尚未选择，将只显示测评、测试或考试' : '尚未选择，点击查看当前课堂作业');
        }
        if (action) action.textContent = candidate ? '更换' : '选择';
    });
    dom.ordinaryGradeProgressSteps?.forEach((item, stepIndex) => {
        const complete = stepIndex < 4
            ? selectedIds[stepIndex] > 0
            : Boolean(dom.finalMaterialPrompt?.value?.trim());
        item.classList.toggle('is-complete', complete);
        item.classList.toggle(
            'is-active',
            stepIndex < 4
                ? (!dom.ordinaryGradePicker?.hidden && state.ordinaryGradeActiveStep === stepIndex)
                : selectedIds.every((value) => value > 0),
        );
    });
    renderOrdinaryGradePicker();
}


function syncOrdinaryGradeScoreFloorControls(dom = refs()) {
    const enabled = Boolean(dom.ordinaryScoreFloorEnabled?.checked);
    if (dom.ordinaryScoreFloorInput) {
        dom.ordinaryScoreFloorInput.disabled = !enabled;
    }
    const score = ordinaryGradeMinimumScore(dom);
    const validScore = Number.isFinite(score) && score >= 0 && score <= 100;
    if (dom.ordinaryScoreFloorSummary) {
        dom.ordinaryScoreFloorSummary.textContent = !enabled
            ? '已关闭最低分保护：所有学生均按真实出勤、作业和测评成绩计算。'
            : validScore
                ? `出勤率达到 70% 的学生，若公式平时分低于 ${score} 分，系统只上调所选作业和测评；出勤率保持真实。`
                : '请输入 0 到 100 之间的最低平时分，填写正确后才能生成。';
    }
}

function ordinaryGradeMinimumScore(dom = refs()) {
    const raw = String(dom.ordinaryScoreFloorInput?.value ?? '').trim();
    return raw === '' ? Number.NaN : Number(raw);
}

function openOrdinaryGradePicker(stepIndex) {
    const dom = refs();
    if (!dom.ordinaryGradePicker) return;
    state.ordinaryGradeActiveStep = Math.max(0, Math.min(3, Number(stepIndex || 0)));
    dom.ordinaryGradePicker.hidden = false;
    if (dom.ordinaryGradePickerSearch) dom.ordinaryGradePickerSearch.value = '';
    renderOrdinaryGradeWizard();
    window.setTimeout(() => dom.ordinaryGradePickerSearch?.focus(), 60);
}

function closeOrdinaryGradePicker() {
    const dom = refs();
    if (dom.ordinaryGradePicker) dom.ordinaryGradePicker.hidden = true;
    renderOrdinaryGradeWizard();
}

function selectOrdinaryGradeCandidate(candidateId) {
    const dom = refs();
    const stepIndex = Number(state.ordinaryGradeActiveStep || 0);
    const select = ordinaryGradeStepSelect(dom, stepIndex);
    const numericId = Number(candidateId || 0);
    if (!select || numericId <= 0) return;
    const selectedIds = ordinaryGradeSelectedIds(dom);
    if (selectedIds.some((value, index) => index !== stepIndex && value === numericId)) {
        showToast('这份来源已用于其他步骤，请选择另一份作业或测评。', 'warning');
        return;
    }
    select.value = String(numericId);
    select.dispatchEvent(new Event('change', { bubbles: true }));
    const nextIncomplete = ordinaryGradeSelectedIds(dom).findIndex((value, index) => index > stepIndex && value <= 0);
    if (nextIncomplete >= 0) {
        openOrdinaryGradePicker(nextIncomplete);
        return;
    }
    closeOrdinaryGradePicker();
    if (stepIndex === 3) {
        dom.finalMaterialPrompt?.focus();
        dom.finalMaterialPromptGroup?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
}

function getOrdinaryGradeReadiness() {
    const dom = refs();
    if (state.ordinaryGradeCandidatesLoading) {
        return { ready: false, message: '正在读取当前课堂的作业和测评...' };
    }
    if (state.ordinaryGradeCandidatesError) {
        return { ready: false, message: state.ordinaryGradeCandidatesError };
    }
    if (!state.ordinaryGradeCandidatesLoaded) {
        return { ready: false, message: '请等待系统读取当前课堂的作业和测评。' };
    }
    const buckets = ordinaryGradeCandidateBuckets();
    if (buckets.homework.length < 3 || buckets.assessment.length < 1) {
        return {
            ready: false,
            message: `当前课堂还缺少可用来源：需要 3 份作业和 1 份测评，目前识别到 ${buckets.homework.length} 份作业、${buckets.assessment.length} 份测评。`,
        };
    }
    const homeworkIds = (dom.ordinaryHomeworkSelects || [])
        .map((select) => Number(select?.value || 0))
        .filter((value) => value > 0);
    const assessmentId = Number(dom.ordinaryAssessmentSelect?.value || 0);
    if (homeworkIds.length !== 3 || assessmentId <= 0) {
        return { ready: false, message: '请选择 3 份平时作业和 1 份测评。' };
    }
    if (new Set([...homeworkIds, assessmentId]).size !== 4) {
        return { ready: false, message: '三次作业和一次测评不能重复。' };
    }
    if (dom.ordinaryScoreFloorEnabled?.checked) {
        const score = ordinaryGradeMinimumScore(dom);
        if (!Number.isFinite(score) || score < 0 || score > 100) {
            return { ready: false, message: '最低平时分必须在 0 到 100 之间。' };
        }
    }
    return { ready: true, message: '' };
}

function refreshOrdinaryGradeAvailabilityStatus() {
    const dom = refs();
    const readiness = getOrdinaryGradeReadiness();
    if (dom.ordinaryGradeStatus) {
        dom.ordinaryGradeStatus.hidden = readiness.ready || !readiness.message;
        dom.ordinaryGradeStatus.textContent = readiness.message || '';
    }
    renderOrdinaryGradeWizard();
    updateFinalMaterialSubmitState();
    return readiness;
}

function populateOrdinaryGradeSelects() {
    const dom = refs();
    const buckets = ordinaryGradeCandidateBuckets();
    const homeworkOptions = ordinaryOptionsHtml(buckets.homework, buckets.homework.length ? '请选择作业' : '暂无可用作业');
    const assessmentOptions = ordinaryOptionsHtml(buckets.assessment, buckets.assessment.length ? '请选择测评' : '暂无可用测评');
    dom.ordinaryHomeworkSelects?.forEach((select, index) => {
        if (!select) return;
        const previous = select.value;
        select.innerHTML = homeworkOptions;
        select.disabled = buckets.homework.length === 0;
        if (previous && buckets.homework.some((item) => String(item.id) === String(previous))) {
            select.value = previous;
        }
    });
    if (dom.ordinaryAssessmentSelect) {
        const previous = dom.ordinaryAssessmentSelect.value;
        dom.ordinaryAssessmentSelect.innerHTML = assessmentOptions;
        dom.ordinaryAssessmentSelect.disabled = buckets.assessment.length === 0;
        if (previous && buckets.assessment.some((item) => String(item.id) === String(previous))) {
            dom.ordinaryAssessmentSelect.value = previous;
        }
    }
    renderOrdinaryGradeWizard();
}

async function loadOrdinaryGradeCandidates({ force = false } = {}) {
    const dom = refs();
    if (force && state.ordinaryGradeCandidatesLoading) {
        state.ordinaryGradeCandidatesReloadPending = true;
        return;
    }
    if (force) {
        state.ordinaryGradeCandidatesLoaded = false;
        state.ordinaryGradeCandidatesError = '';
    }
    if (state.ordinaryGradeCandidatesLoaded) {
        populateOrdinaryGradeSelects();
        refreshOrdinaryGradeAvailabilityStatus();
        return;
    }
    if (state.ordinaryGradeCandidatesLoading) {
        refreshOrdinaryGradeAvailabilityStatus();
        return;
    }
    state.ordinaryGradeCandidatesLoading = true;
    state.ordinaryGradeCandidatesError = '';
    if (dom.ordinaryGradeStatus) {
        dom.ordinaryGradeStatus.hidden = false;
        dom.ordinaryGradeStatus.textContent = '正在读取当前课堂的作业和测评...';
    }
    updateFinalMaterialSubmitState();
    try {
        const data = await apiFetch(`/api/classrooms/${config.classOfferingId}/ordinary-grade-record/candidates`, { silent: true });
        state.ordinaryGradeCandidates = Array.isArray(data.items) ? data.items : [];
        state.ordinaryGradeAttendanceFreshness = data.attendance_sync || {};
        state.ordinaryGradeCandidatesLoaded = true;
        state.ordinaryGradeCandidatesError = '';
        populateOrdinaryGradeSelects();
    } catch (error) {
        state.ordinaryGradeCandidatesError = error.message || '读取作业候选失败';
    } finally {
        state.ordinaryGradeCandidatesLoading = false;
        refreshOrdinaryGradeAvailabilityStatus();
        if (state.ordinaryGradeCandidatesReloadPending) {
            state.ordinaryGradeCandidatesReloadPending = false;
            loadOrdinaryGradeCandidates({ force: true });
        }
    }
}

window.addEventListener('lanshare:ordinary-grade-kind-updated', (event) => {
    const updatedOfferingId = Number(event?.detail?.class_offering_id || 0);
    if (updatedOfferingId > 0 && updatedOfferingId !== Number(config.classOfferingId || 0)) return;
    loadOrdinaryGradeCandidates({ force: true });
});

function collectOrdinaryGradeSelection() {
    const dom = refs();
    const homeworkIds = (dom.ordinaryHomeworkSelects || []).map((select) => Number(select?.value || 0)).filter((value) => value > 0);
    const assessmentId = Number(dom.ordinaryAssessmentSelect?.value || 0);
    const unique = new Set([...homeworkIds, assessmentId].filter((value) => value > 0));
    if (homeworkIds.length !== 3 || assessmentId <= 0) {
        throw new Error('请选择 3 份平时作业和 1 份测评。');
    }
    if (unique.size !== 4) {
        throw new Error('三次作业和一次测评不能重合。');
    }
    const minimumScoreEnabled = Boolean(dom.ordinaryScoreFloorEnabled?.checked);
    const minimumScore = ordinaryGradeMinimumScore(dom);
    if (minimumScoreEnabled && (!Number.isFinite(minimumScore) || minimumScore < 0 || minimumScore > 100)) {
        throw new Error('最低平时分必须在 0 到 100 之间。');
    }
    return {
        homeworkIds,
        assessmentId,
        minimumScoreEnabled,
        minimumScore: Number.isFinite(minimumScore) ? minimumScore : 60,
    };
}

function examGradeCandidateLabel(item) {
    const title = item?.title || `考试 ${item?.id || ''}`;
    const rosterCount = item?.roster_count || item?.submission_count || 0;
    const stats = `${item?.graded_count || 0}/${rosterCount}`;
    const sections = item?.section_count ? `，${item.section_count} 个大题` : '';
    const total = item?.total_score ? `，满分 ${item.total_score}` : '';
    const average = item?.average_score === null || item?.average_score === undefined ? '' : `，均分 ${item.average_score}`;
    const blocked = item?.eligible === false ? `，不可生成：${item?.blocking_reason || '来源不完整'}` : '';
    return `${title}（全班 ${rosterCount} 人同一张总表，已评分 ${stats}${sections}${total}${average}${blocked}）`;
}

function examGradeCandidateEligible(item) {
    if (!item) return false;
    if (typeof item.eligible === 'boolean') return item.eligible;
    return Number(item.section_count || 0) > 0
        && Number(item.total_score || 0) > 0
        && Number(item.graded_count || 0) > 0;
}

function populateExamGradeSelect() {
    const dom = refs();
    if (!dom.examGradeSelect) return;
    const previous = dom.examGradeSelect.value;
    const eligible = state.examGradeCandidates.filter(examGradeCandidateEligible);
    dom.examGradeSelect.innerHTML = [
        '<option value="">请选择考试</option>',
        ...state.examGradeCandidates.map((item) => `<option value="${escapeHtml(String(item.id))}"${examGradeCandidateEligible(item) ? '' : ' disabled'}>${escapeHtml(examGradeCandidateLabel(item))}</option>`),
    ].join('');
    if (previous && eligible.some((item) => String(item.id) === String(previous))) {
        dom.examGradeSelect.value = previous;
    } else if (eligible.length === 1) {
        dom.examGradeSelect.value = String(eligible[0].id);
    }
}

function getExamGradeReadiness() {
    const dom = refs();
    if (state.examGradeCandidatesLoading) {
        return { ready: false, message: '正在读取当前课堂的考试成绩...' };
    }
    if (state.examGradeCandidatesError) {
        return { ready: false, message: state.examGradeCandidatesError };
    }
    if (!state.examGradeCandidatesLoaded) {
        return { ready: false, message: '请等待系统读取当前课堂的考试成绩。' };
    }
    const eligible = state.examGradeCandidates.filter(examGradeCandidateEligible);
    if (!eligible.length) {
        return { ready: false, message: '当前课堂没有可生成的考试：需要绑定试卷、配置大题分值，并至少完成 1 名学生的评分。' };
    }
    const selected = state.examGradeCandidates.find((item) => Number(item?.id || 0) === Number(dom.examGradeSelect?.value || 0));
    if (!selected) {
        return { ready: false, message: '请选择一个用于生成考核登分表的考试。' };
    }
    if (!examGradeCandidateEligible(selected)) {
        return { ready: false, message: selected.blocking_reason || '所选考试尚不满足生成条件。' };
    }
    return { ready: true, message: '' };
}

function refreshExamGradeAvailabilityStatus() {
    const dom = refs();
    const readiness = getExamGradeReadiness();
    if (dom.examGradeStatus) {
        dom.examGradeStatus.hidden = readiness.ready || !readiness.message;
        dom.examGradeStatus.textContent = readiness.message || '';
    }
    updateFinalMaterialSubmitState();
    return readiness;
}

async function loadExamGradeCandidates() {
    const dom = refs();
    if (state.examGradeCandidatesLoaded) {
        populateExamGradeSelect();
        refreshExamGradeAvailabilityStatus();
        return;
    }
    if (state.examGradeCandidatesLoading) {
        refreshExamGradeAvailabilityStatus();
        return;
    }
    state.examGradeCandidatesLoading = true;
    state.examGradeCandidatesError = '';
    if (dom.examGradeStatus) {
        dom.examGradeStatus.hidden = false;
        dom.examGradeStatus.textContent = '正在读取当前课堂的考试成绩...';
    }
    updateFinalMaterialSubmitState();
    try {
        const data = await apiFetch(`/api/classrooms/${config.classOfferingId}/exam-grade-record/candidates`, { silent: true });
        state.examGradeCandidates = Array.isArray(data.items) ? data.items : [];
        state.examGradeCandidatesLoaded = true;
        state.examGradeCandidatesError = '';
        populateExamGradeSelect();
    } catch (error) {
        state.examGradeCandidatesError = error.message || '读取考试候选失败';
    } finally {
        state.examGradeCandidatesLoading = false;
        refreshExamGradeAvailabilityStatus();
    }
}

function collectExamGradeSelection() {
    const dom = refs();
    const examAssignmentId = Number(dom.examGradeSelect?.value || 0);
    if (examAssignmentId <= 0) {
        throw new Error('请选择一个用于生成考核登分表的考试。');
    }
    return { examAssignmentId };
}

function setFinalMaterialStatus(message = '', kind = '') {
    const statusEl = refs().finalMaterialStatus;
    if (!statusEl) return;
    const text = String(message || '').trim();
    statusEl.hidden = !text;
    statusEl.textContent = text;
    if (kind) {
        statusEl.dataset.statusKind = kind;
    } else {
        delete statusEl.dataset.statusKind;
    }
}

function needsFinalMaterialPrerequisites(documentType) {
    return documentType === 'exam_paper' || documentType === 'grading_rubric';
}

function getFinalMaterialSourceReadiness(documentType) {
    if (!needsFinalMaterialPrerequisites(documentType)) {
        return { ready: true, message: '', sourceMessage: '' };
    }
    if (state.finalMaterialPrerequisitesLoading) {
        return { ready: false, message: '正在确认当前课堂的前置材料...' };
    }
    if (state.finalMaterialPrerequisitesError) {
        return { ready: false, message: state.finalMaterialPrerequisitesError };
    }
    if (!state.finalMaterialPrerequisitesLoaded) {
        return { ready: false, message: '请等待系统确认当前课堂是否已有前置材料。' };
    }
    const prerequisite = state.finalMaterialPrerequisites?.[documentType] || {};
    if (!prerequisite.ready) {
        return {
            ready: false,
            message: prerequisite.message || (
                documentType === 'grading_rubric'
                    ? '请先在本课堂导入或生成课程考核试卷，再生成评分细则。'
                    : '请先在本课堂导入或生成课程考核计划表，再生成课程考核试卷。'
            ),
        };
    }
    const source = prerequisite.source_record || {};
    const sourceLabel = prerequisite.source_label || source.document_type_label || (
        documentType === 'grading_rubric' ? '课程考核试卷' : '课程考核计划表'
    );
    const title = compactValue(source.title || sourceLabel);
    const updatedAt = compactDateTime(source.updated_at);
    const action = documentType === 'grading_rubric'
        ? '生成评分细则时会按这份试卷逐题拆分给分点、扣分项和截图/提交要求。'
        : '生成试卷时会继承这份计划表的考核形式、分值分布和课程字段。';
    const updatedPart = updatedAt ? `（更新于 ${updatedAt}）` : '';
    return {
        ready: true,
        message: '',
        sourceMessage: `已关联${sourceLabel}：《${title}》${updatedPart}。${action}`,
    };
}

function refreshFinalMaterialBlockingStatus(blockingMessage = getFinalMaterialBlockingMessage()) {
    const statusEl = refs().finalMaterialStatus;
    if (!statusEl || state.finalMaterialBusy) return;
    if (blockingMessage) {
        setFinalMaterialStatus(blockingMessage, 'blocking');
        return;
    }
    const documentType = refs().finalMaterialType?.value || '';
    const readiness = getFinalMaterialSourceReadiness(documentType);
    if (readiness.ready && readiness.sourceMessage) {
        setFinalMaterialStatus(readiness.sourceMessage, 'success');
        return;
    }
    if (statusEl.dataset.statusKind === 'blocking' || statusEl.dataset.statusKind === 'success') {
        setFinalMaterialStatus('', '');
    }
}

async function loadFinalMaterialPrerequisites({ force = false } = {}) {
    if (state.finalMaterialPrerequisitesLoaded && !force) {
        updateFinalMaterialSubmitState();
        return;
    }
    if (state.finalMaterialPrerequisitesLoading) {
        updateFinalMaterialSubmitState();
        return;
    }
    state.finalMaterialPrerequisitesLoading = true;
    state.finalMaterialPrerequisitesError = '';
    updateFinalMaterialSubmitState();
    try {
        const data = await apiFetch(`/api/classrooms/${config.classOfferingId}/final-materials/prerequisites`, { silent: true });
        state.finalMaterialPrerequisites = data.prerequisites || {};
        state.finalMaterialPrerequisitesLoaded = true;
        state.finalMaterialPrerequisitesError = '';
    } catch (error) {
        state.finalMaterialPrerequisitesError = error.message || '读取期末材料前置来源失败';
    } finally {
        state.finalMaterialPrerequisitesLoading = false;
        updateFinalMaterialSubmitState();
    }
}

function updateFinalMaterialTemplateOptions() {
    const dom = refs();
    const selectedType = dom.finalMaterialType?.value || '';
    const isAssessmentPlan = selectedType === 'assessment_plan';
    const isGradingRubric = selectedType === 'grading_rubric';
    const isExamPaper = selectedType === 'exam_paper';
    const isOrdinary = selectedType === 'ordinary_grade_record';
    const isExamGrade = selectedType === 'exam_grade_record';
    if (dom.examPaperOptions) {
        dom.examPaperOptions.hidden = !isExamPaper;
    }
    if (dom.assessmentPlanOptions) {
        dom.assessmentPlanOptions.hidden = !isAssessmentPlan;
    }
    if (dom.gradingRubricOptions) {
        dom.gradingRubricOptions.hidden = !isGradingRubric;
    }
    if (dom.ordinaryGradeOptions) {
        dom.ordinaryGradeOptions.hidden = !isOrdinary;
    }
    if (dom.examGradeOptions) {
        dom.examGradeOptions.hidden = !isExamGrade;
    }
    if (dom.finalMaterialPromptGroup) {
        dom.finalMaterialPromptGroup.classList.toggle('is-ordinary-grade-step', isOrdinary);
    }
    if (dom.finalMaterialPromptStep) {
        dom.finalMaterialPromptStep.hidden = !isOrdinary;
    }
    if (dom.finalMaterialPromptLabel) {
        dom.finalMaterialPromptLabel.textContent = isOrdinary ? '额外的生成要求' : '生成要求';
    }
    if (isOrdinary) {
        loadOrdinaryGradeCandidates();
        renderOrdinaryGradeWizard();
    } else {
        closeOrdinaryGradePicker();
    }
    if (isExamGrade) {
        loadExamGradeCandidates();
    }
    if (isExamPaper || isGradingRubric) {
        loadFinalMaterialPrerequisites();
    }
    if (isAssessmentPlan && dom.finalMaterialAssessmentMethod && !dom.finalMaterialAssessmentMethod.value.trim()) {
        dom.finalMaterialAssessmentMethod.value = dom.finalMaterialAssessmentMode?.value === 'written' ? '闭卷笔试' : '机试';
    }
    if (dom.finalMaterialPrompt) {
        if (isGradingRubric) {
            dom.finalMaterialPrompt.placeholder = '例如：评分时突出脚本可执行性、截图编号一致性和例外情况；每个任务写清楚可给一半分的情形。';
        } else if (isAssessmentPlan) {
            dom.finalMaterialPrompt.placeholder = '例如：按机试方式拆分 Linux 服务部署、数据库授权、脚本备份等考核技能，分值合计100。';
        } else if (isOrdinary) {
            dom.finalMaterialPrompt.placeholder = '可选：例如补充本次归档说明、课程组统一口径或需要教师后续核对的事项。成绩保护策略请使用上方开关和最低分设置。';
        } else if (isExamGrade) {
            dom.finalMaterialPrompt.placeholder = '例如：按考试大题生成“一、二、三”列，迟交和小组互评扣分要整数分摊并核验总分。';
        } else {
            dom.finalMaterialPrompt.placeholder = '例如：根据本课堂最新考核计划表，围绕 Linux 服务部署、数据库授权、脚本备份设计机试任务，写清截图编号、提交物和考试时长。';
        }
    }
    updateFinalMaterialSubmitState();
}

function getFinalMaterialBlockingMessage() {
    const dom = refs();
    const documentType = dom.finalMaterialType?.value || '';
    if (documentType === 'ordinary_grade_record') {
        return getOrdinaryGradeReadiness().message;
    }
    if (documentType === 'exam_grade_record') {
        return getExamGradeReadiness().message;
    }
    if (needsFinalMaterialPrerequisites(documentType)) {
        return getFinalMaterialSourceReadiness(documentType).message;
    }
    return '';
}

function updateFinalMaterialSubmitState() {
    const dom = refs();
    if (!dom.finalMaterialSubmitBtn) return;
    const blockingMessage = getFinalMaterialBlockingMessage();
    const disabled = state.finalMaterialBusy || Boolean(blockingMessage);
    dom.finalMaterialSubmitBtn.disabled = disabled;
    dom.finalMaterialSubmitBtn.textContent = state.finalMaterialBusy
        ? '生成中...'
        : (blockingMessage ? '请先补齐来源' : '生成并保存');
    dom.finalMaterialSubmitBtn.title = blockingMessage || '';
    refreshFinalMaterialBlockingStatus(blockingMessage);
}

function updateAssessmentMethodDefault() {
    const dom = refs();
    if (!dom.finalMaterialAssessmentMethod) return;
    const current = dom.finalMaterialAssessmentMethod.value.trim();
    if (current && current !== '机试' && current !== '闭卷笔试') return;
    dom.finalMaterialAssessmentMethod.value = dom.finalMaterialAssessmentMode?.value === 'written' ? '闭卷笔试' : '机试';
}

async function submitFinalMaterialGeneration() {
    const dom = refs();
    if (!dom.finalMaterialSubmitBtn) return;
    const documentType = dom.finalMaterialType?.value || 'exam_paper';
    const prompt = dom.finalMaterialPrompt?.value || '';
    const blockingMessage = getFinalMaterialBlockingMessage();
    if (blockingMessage) {
        setFinalMaterialStatus(blockingMessage, 'blocking');
        showToast(blockingMessage, 'warning');
        updateFinalMaterialSubmitState();
        return;
    }
    let ordinarySelection = null;
    let examGradeSelection = null;
    if (documentType === 'ordinary_grade_record') {
        try {
            ordinarySelection = collectOrdinaryGradeSelection();
        } catch (error) {
            setFinalMaterialStatus(error.message || '请选择成绩来源', 'blocking');
            showToast(error.message || '请选择成绩来源', 'warning');
            return;
        }
    }
    if (documentType === 'exam_grade_record') {
        try {
            examGradeSelection = collectExamGradeSelection();
        } catch (error) {
            setFinalMaterialStatus(error.message || '请选择考试', 'blocking');
            showToast(error.message || '请选择考试', 'warning');
            return;
        }
    }
    state.finalMaterialBusy = true;
    updateFinalMaterialSubmitState();
    setFinalMaterialStatus(
        documentType === 'exam_grade_record'
            ? '正在按名单顺序生成包含全班学生的一张连续总表...'
            : '正在生成并保存材料...',
        'progress',
    );
    try {
        const data = await apiFetch(`/api/classrooms/${config.classOfferingId}/final-materials/generate`, {
            method: 'POST',
            body: {
                document_type: documentType,
                prompt,
                parent_id: state.currentParentId,
                assessment_mode: documentType === 'assessment_plan' ? (dom.finalMaterialAssessmentMode?.value || '') : '',
                assessment_method: documentType === 'assessment_plan' ? (dom.finalMaterialAssessmentMethod?.value || '') : '',
                homework_assignment_ids: ordinarySelection?.homeworkIds || [],
                assessment_assignment_id: ordinarySelection?.assessmentId || null,
                minimum_ordinary_score_enabled: ordinarySelection?.minimumScoreEnabled ?? true,
                minimum_ordinary_score: ordinarySelection?.minimumScore ?? 60,
                exam_assignment_id: examGradeSelection?.examAssignmentId || null,
            },
        });
        showToast(data.message || '期末材料已生成', 'success');
        await recordPromptForInput(dom.finalMaterialPrompt, prompt);
        closeModal(dom.finalMaterialModal);
        state.finalMaterialPrerequisitesLoaded = false;
        state.finalMaterialPrerequisites = {};
        await loadMaterials(state.currentParentId, false);
    } catch (error) {
        setFinalMaterialStatus(error.message || '生成失败', 'error');
        showToast(error.message || '生成失败', 'error');
    } finally {
        state.finalMaterialBusy = false;
        updateFinalMaterialSubmitState();
    }
}

async function loadMaterials(parentId = null, trackHistory = false) {
    const query = parentId ? `?parent_id=${parentId}` : '';
    const data = await apiFetch(`/api/classrooms/${config.classOfferingId}/materials${query}`, { silent: true });
    if (trackHistory && state.currentParentId !== parentId) {
        state.history.push(state.currentParentId);
    }
    state.currentParentId = parentId;
    state.breadcrumbs = data.breadcrumbs || [];
    state.items = data.items || [];
    state.selectedIds.clear();
    renderBreadcrumbs();
    renderList();
}

function getBlockedSelectedItems(ids) {
    return ids
        .map((id) => state.items.find((item) => Number(item.id) === Number(id)))
        .filter((item) => item && item.node_type === 'file' && item.download_allowed === false);
}

async function downloadSelected(ids) {
    if (!ids.length) return;
    const blockedItems = getBlockedSelectedItems(ids);
    if (blockedItems.length) {
        throw new Error(blockedItems[0].download_blocked_reason || '所选材料中包含已限制下载的文件');
    }
    const response = await fetch('/api/materials/download', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ material_ids: ids }),
        credentials: 'same-origin',
    });

    if (!response.ok) {
        let message = '下载失败';
        try {
            const errorData = await response.json();
            if (window.handleAuthFailureResponse) {
                await window.handleAuthFailureResponse(response, errorData);
            }
            message = errorData.detail || errorData.message || message;
        } catch {
            // ignore
        }
        throw new Error(message);
    }

    const blob = await response.blob();
    const disposition = response.headers.get('content-disposition') || '';
    const match = disposition.match(/filename="?([^"]+)"?/i);
    const fileName = match ? decodeURIComponent(match[1]) : 'course-materials.zip';
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = fileName;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
}

const FINAL_MATERIAL_TYPES = new Set([
    'exam_paper',
    'assessment_plan',
    'grading_rubric',
    'ordinary_grade_record',
    'exam_grade_record',
]);

function openFinalMaterialModal(dom, documentType = '') {
    if (!dom.finalMaterialModal) return;
    const normalizedType = String(documentType || '').trim();
    if (normalizedType && FINAL_MATERIAL_TYPES.has(normalizedType) && dom.finalMaterialType) {
        const option = Array.from(dom.finalMaterialType.options || []).find((item) => item.value === normalizedType);
        if (option) dom.finalMaterialType.value = normalizedType;
    }
    setFinalMaterialStatus('', '');
    if (needsFinalMaterialPrerequisites(dom.finalMaterialType?.value || '')) {
        loadFinalMaterialPrerequisites({ force: true });
    }
    updateFinalMaterialTemplateOptions();
    openModal(dom.finalMaterialModal);
    updateFinalMaterialSubmitState();
}

function openInitialFinalMaterialModalIfRequested(dom) {
    const params = new URLSearchParams(window.location.search || '');
    if (params.get('open_final_material') !== '1') return;
    const documentType = params.get('final_material_type') || '';
    window.setTimeout(() => openFinalMaterialModal(dom, documentType), 180);
}

export function init(appConfig) {
    config = appConfig;
    const dom = refs();
    if (!dom.list) return;
    enhancePromptPoolInput(dom.finalMaterialPrompt);
    bindProcessMaterialExportDownloadActions(dom.detailContent, showToast, { saved: false });

    dom.refreshBtn?.addEventListener('click', () => {
        loadMaterials(state.currentParentId).catch((error) => {
            showToast(error.message || '刷新材料失败', 'error');
        });
    });

    dom.backBtn?.addEventListener('click', () => {
        const previousParentId = state.history.pop();
        loadMaterials(previousParentId ?? null, false).catch((error) => {
            showToast(error.message || '返回失败', 'error');
        });
    });

    dom.upBtn?.addEventListener('click', () => {
        const parentCrumb = state.breadcrumbs.length >= 2 ? state.breadcrumbs[state.breadcrumbs.length - 2] : null;
        loadMaterials(parentCrumb ? Number(parentCrumb.id) : null, true).catch((error) => {
            showToast(error.message || '返回上一级失败', 'error');
        });
    });

    dom.selectionDownloadBtn?.addEventListener('click', async () => {
        try {
            await downloadSelected(Array.from(state.selectedIds));
        } catch (error) {
            showToast(error.message || '下载失败', 'error');
        }
    });

    dom.generateBtn?.addEventListener('click', () => {
        openFinalMaterialModal(dom);
    });

    dom.finalMaterialType?.addEventListener('change', updateFinalMaterialTemplateOptions);
    dom.finalMaterialAssessmentMode?.addEventListener('change', updateAssessmentMethodDefault);
    dom.ordinaryHomeworkSelects?.forEach((select) => {
        select?.addEventListener('change', refreshOrdinaryGradeAvailabilityStatus);
    });
    dom.ordinaryAssessmentSelect?.addEventListener('change', refreshOrdinaryGradeAvailabilityStatus);
    dom.ordinaryScoreFloorEnabled?.addEventListener('change', refreshOrdinaryGradeAvailabilityStatus);
    dom.ordinaryScoreFloorInput?.addEventListener('input', refreshOrdinaryGradeAvailabilityStatus);
    dom.ordinaryGradeStepCards?.forEach((card) => {
        card.addEventListener('click', () => openOrdinaryGradePicker(Number(card.dataset.ordinaryGradeStepIndex || 0)));
    });
    dom.ordinaryGradePickerSearch?.addEventListener('input', renderOrdinaryGradePicker);
    dom.ordinaryGradePickerList?.addEventListener('click', (event) => {
        const trigger = event.target.closest('[data-ordinary-grade-candidate-id]');
        if (!trigger || trigger.disabled) return;
        selectOrdinaryGradeCandidate(Number(trigger.dataset.ordinaryGradeCandidateId || 0));
    });
    document.querySelector('[data-ordinary-grade-picker-close]')?.addEventListener('click', closeOrdinaryGradePicker);
    dom.finalMaterialPrompt?.addEventListener('input', renderOrdinaryGradeWizard);
    dom.examGradeSelect?.addEventListener('change', refreshExamGradeAvailabilityStatus);

    dom.finalMaterialSubmitBtn?.addEventListener('click', () => {
        submitFinalMaterialGeneration();
    });

    document.querySelectorAll('[data-classroom-final-material-close]').forEach((button) => {
        button.addEventListener('click', () => closeModal(dom.finalMaterialModal));
    });

    document.querySelectorAll('[data-classroom-material-modal-close]').forEach((button) => {
        button.addEventListener('click', () => closeModal(dom.detailModal));
    });

    dom.detailModal?.addEventListener('click', (event) => {
        if (event.target === dom.detailModal) closeModal(dom.detailModal);
    });

    dom.finalMaterialModal?.addEventListener('click', (event) => {
        if (event.target === dom.finalMaterialModal) closeModal(dom.finalMaterialModal);
    });

    dom.detailOpenBtn?.addEventListener('click', () => {
        const item = state.detailItem;
        if (!item) return;
        if (item.node_type === 'folder') {
            closeModal(dom.detailModal);
            loadMaterials(Number(item.id), true).catch((error) => {
                showToast(error.message || '打开目录失败', 'error');
            });
            return;
        }
        const url = getOpenUrl(item);
        if (url) window.open(url, '_blank', 'noopener');
    });

    dom.detailDownloadBtn?.addEventListener('click', () => {
        const item = state.detailItem;
        if (!item || item.node_type !== 'file') return;
        if (item.download_allowed === false) {
            showToast(item.download_blocked_reason || '当前材料已限制下载', 'warning');
            return;
        }
        window.location.href = `/materials/download/${item.id}`;
    });

    dom.detailExportBtn?.addEventListener('click', () => {
        if (!state.detailExportUrl) {
            showToast('这份材料暂时没有可导出的期末材料模板', 'warning');
            return;
        }
        startProcessMaterialExportDownloadFromTrigger(dom.detailExportBtn, showToast, { saved: false });
    });

    dom.detailExportPdfBtn?.addEventListener('click', () => {
        if (!state.detailExportPdfUrl) {
            showToast('这份材料暂时没有可导出的 PDF 模板', 'warning');
            return;
        }
        startProcessMaterialExportDownloadFromTrigger(dom.detailExportPdfBtn, showToast, { saved: false });
    });

    dom.detailContent?.addEventListener('click', async (event) => {
        const button = event.target.closest('[data-action="optimize-final-material"]');
        if (!button || !state.detailItem) return;
        const promptInput = dom.detailContent.querySelector('[data-role="final-material-optimize-prompt"]');
        const prompt = promptInput?.value || '';
        button.disabled = true;
        button.textContent = '优化中...';
        try {
            await apiFetch(`/api/materials/${state.detailItem.id}/ai-import/optimize`, {
                method: 'POST',
                body: {
                    prompt,
                    class_offering_id: config.classOfferingId,
                },
            });
            showToast('期末材料已优化', 'success');
            await recordPromptForInput(promptInput, prompt);
            await openMaterialDetail(state.detailItem.id);
            await loadMaterials(state.currentParentId, false);
        } catch (error) {
            showToast(error.message || 'AI 优化失败', 'error');
        } finally {
            button.disabled = false;
            button.textContent = 'AI优化并保存';
        }
    });

    dom.breadcrumbs?.addEventListener('click', (event) => {
        const button = event.target.closest('[data-crumb-id]');
        if (!button) return;
        loadMaterials(Number(button.dataset.crumbId), true).catch((error) => {
            showToast(error.message || '打开目录失败', 'error');
        });
    });

    dom.list.addEventListener('click', (event) => {
        const row = event.target.closest('.materials-row');
        if (!row) return;
        const materialId = Number(row.dataset.id);
        const item = state.items.find((entry) => Number(entry.id) === materialId);
        if (!item) return;

        const checkbox = event.target.closest('[data-role="select-item"]');
        if (checkbox) {
            if (checkbox.checked) state.selectedIds.add(materialId);
            else state.selectedIds.delete(materialId);
            updateSelectionBar();
            return;
        }

        const action = event.target.closest('[data-action]')?.dataset.action;
        if (!action) {
            openMaterialDetail(materialId).catch((error) => {
                showToast(error.message || '打开材料详情失败', 'error');
            });
            return;
        }

        if (action === 'open') {
            loadMaterials(materialId, true).catch((error) => {
                showToast(error.message || '打开目录失败', 'error');
            });
        } else if (action === 'preview') {
            window.open(withClassroomLearningContext(`/materials/view/${materialId}`), '_blank', 'noopener');
        } else if (action === 'render') {
            const renderUrl = getRenderUrl(item);
            if (!renderUrl) {
                showToast('当前材料不支持直接渲染', 'warning');
                return;
            }
            window.open(renderUrl, '_blank', 'noopener');
        } else if (action === 'view-doc') {
            const viewerUrl = getLearningDocumentUrl(item);
            if (!viewerUrl) {
                showToast('当前目录没有可查看的 README.md', 'warning');
                return;
            }
            window.open(withClassroomLearningContext(viewerUrl), '_blank', 'noopener');
        } else if (action === 'download-blocked') {
            showToast(item.download_blocked_reason || '当前材料已限制下载', 'warning');
        } else if (action === 'download') {
            window.location.href = `/materials/download/${materialId}`;
        }
    });

    dom.selectAll?.addEventListener('change', () => {
        state.selectedIds.clear();
        if (dom.selectAll.checked) state.items.forEach(item => state.selectedIds.add(Number(item.id)));
        dom.list.querySelectorAll('[data-role="select-item"]').forEach(checkbox => {
            checkbox.checked = state.selectedIds.has(Number(checkbox.dataset.id));
        });
        updateSelectionBar();
    });

    dom.list.addEventListener('dblclick', (event) => {
        const row = event.target.closest('.materials-row');
        if (!row) return;
        const materialId = Number(row.dataset.id);
        const item = state.items.find((entry) => Number(entry.id) === materialId);
        if (!item) return;
        if (item.node_type === 'folder') {
            loadMaterials(materialId, true).catch((error) => {
                showToast(error.message || '打开目录失败', 'error');
            });
        } else if (item.preview_supported) {
            window.open(withClassroomLearningContext(`/materials/view/${materialId}`), '_blank', 'noopener');
        }
    });

    let directoryLoaded = false;
    const loadDirectory = () => {
        if (directoryLoaded) return;
        directoryLoaded = true;
        loadMaterials().catch((error) => {
            directoryLoaded = false;
            dom.list.innerHTML = `<div class="materials-empty">加载材料失败：${escapeHtml(error.message || '未知错误')}。请点击刷新重试。</div>`;
        });
    };
    if (document.body.classList.contains('classroom-workspace-v2')) {
        document.addEventListener('classroom:workspace-surface-visible', event => {
            if (event.detail?.panel === 'materials') loadDirectory();
        });
        if (dom.list.closest('.cw-dialog')) loadDirectory();
    } else loadDirectory();
    openInitialFinalMaterialModalIfRequested(dom);
}

export async function refresh() {
    if (!config) return;
    document.dispatchEvent(new CustomEvent('classroom:materials-changed'));
    await loadMaterials(state.currentParentId);
}
