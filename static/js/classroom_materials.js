import { apiFetch } from './api.js';
import { escapeHtml, formatSize, getFileIcon, showToast } from './ui.js';
import {
    getLearningDocumentUrl,
    getMaterialPrimaryAction,
    getMaterialTypeLabel,
    getRepositoryVisualMeta,
    hasLearningDocument,
    isGitRepository,
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
        finalMaterialAssessmentMode: document.getElementById('classroom-final-material-assessment-mode'),
        finalMaterialAssessmentMethod: document.getElementById('classroom-final-material-assessment-method'),
        finalMaterialPrompt: document.getElementById('classroom-final-material-prompt'),
        finalMaterialSubmitBtn: document.getElementById('classroom-final-material-submit-btn'),
        finalMaterialStatus: document.getElementById('classroom-final-material-status'),
    };
}

function isTeacher() {
    return config?.canGenerateFinalMaterials || config?.userRole === 'teacher' || config?.userInfo?.role === 'teacher';
}

function openModal(modal) {
    if (!modal) return;
    modal.style.display = 'flex';
    modal.setAttribute('aria-hidden', 'false');
    modal.classList.add('show');
    document.body.classList.add('modal-open');
}

function closeModal(modal) {
    if (!modal) return;
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
                    <input type="checkbox" data-role="select-item" data-id="${item.id}" ${state.selectedIds.has(item.id) ? 'checked' : ''}>
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
    return '<p class="text-muted text-sm">这份材料暂未绑定期末材料模板。</p>';
}

function renderDetailContent(material, preview = null) {
    const aiRecord = material.ai_import_record || null;
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
            <textarea class="classroom-material-ai-prompt" data-role="final-material-optimize-prompt" rows="4" placeholder="例如：补齐审核人、考试时间，细化评分细则，保持总分100分。"></textarea>
            <div class="classroom-material-inline-actions">
                <button type="button" class="btn btn-primary btn-sm" data-action="optimize-final-material">AI优化并保存</button>
            </div>
        </section>
    ` : '';

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
        ${aiBlock}
        ${optimizeBlock}
    `;
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
        dom.detailLoading.hidden = true;
        dom.detailContent.hidden = false;
        if (dom.detailOpenBtn) dom.detailOpenBtn.textContent = material.node_type === 'folder' ? '打开文件夹' : '打开';
        if (dom.detailDownloadBtn) dom.detailDownloadBtn.disabled = material.node_type !== 'file' || material.download_allowed === false;
        if (dom.detailExportBtn) dom.detailExportBtn.disabled = !state.detailExportUrl;
        if (dom.detailExportPdfBtn) dom.detailExportPdfBtn.disabled = !state.detailExportPdfUrl;
    } catch (error) {
        dom.detailLoading.hidden = true;
        dom.detailContent.hidden = false;
        dom.detailContent.innerHTML = `<div class="materials-empty">加载材料详情失败：${escapeHtml(error.message || '未知错误')}</div>`;
    }
}

function updateFinalMaterialTemplateOptions() {
    const dom = refs();
    const selectedType = dom.finalMaterialType?.value || '';
    const isAssessmentPlan = selectedType === 'assessment_plan';
    const isGradingRubric = selectedType === 'grading_rubric';
    const isExamPaper = selectedType === 'exam_paper';
    if (dom.examPaperOptions) {
        dom.examPaperOptions.hidden = !isExamPaper;
    }
    if (dom.assessmentPlanOptions) {
        dom.assessmentPlanOptions.hidden = !isAssessmentPlan;
    }
    if (dom.gradingRubricOptions) {
        dom.gradingRubricOptions.hidden = !isGradingRubric;
    }
    if (isAssessmentPlan && dom.finalMaterialAssessmentMethod && !dom.finalMaterialAssessmentMethod.value.trim()) {
        dom.finalMaterialAssessmentMethod.value = dom.finalMaterialAssessmentMode?.value === 'written' ? '闭卷笔试' : '机试';
    }
    if (dom.finalMaterialPrompt) {
        if (isGradingRubric) {
            dom.finalMaterialPrompt.placeholder = '例如：评分时突出脚本可执行性、截图编号一致性和例外情况；每个任务写清楚可给一半分的情形。';
        } else if (isAssessmentPlan) {
            dom.finalMaterialPrompt.placeholder = '例如：按机试方式拆分 Linux 服务部署、数据库授权、脚本备份等考核技能，分值合计100。';
        } else {
            dom.finalMaterialPrompt.placeholder = '例如：根据本课堂最新考核计划表，围绕 Linux 服务部署、数据库授权、脚本备份设计机试任务，写清截图编号、提交物和考试时长。';
        }
    }
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
    const statusEl = dom.finalMaterialStatus;
    dom.finalMaterialSubmitBtn.disabled = true;
    if (statusEl) {
        statusEl.hidden = false;
        statusEl.textContent = '正在生成并保存材料...';
    }
    try {
        const data = await apiFetch(`/api/classrooms/${config.classOfferingId}/final-materials/generate`, {
            method: 'POST',
            body: {
                document_type: documentType,
                prompt,
                parent_id: state.currentParentId,
                assessment_mode: documentType === 'assessment_plan' ? (dom.finalMaterialAssessmentMode?.value || '') : '',
                assessment_method: documentType === 'assessment_plan' ? (dom.finalMaterialAssessmentMethod?.value || '') : '',
            },
        });
        showToast(data.message || '期末材料已生成', 'success');
        closeModal(dom.finalMaterialModal);
        await loadMaterials(state.currentParentId, false);
    } catch (error) {
        if (statusEl) statusEl.textContent = error.message || '生成失败';
        showToast(error.message || '生成失败', 'error');
    } finally {
        dom.finalMaterialSubmitBtn.disabled = false;
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

export function init(appConfig) {
    config = appConfig;
    const dom = refs();
    if (!dom.list) return;

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
        if (!dom.finalMaterialModal) return;
        if (dom.finalMaterialStatus) {
            dom.finalMaterialStatus.hidden = true;
            dom.finalMaterialStatus.textContent = '';
        }
        updateFinalMaterialTemplateOptions();
        openModal(dom.finalMaterialModal);
    });

    dom.finalMaterialType?.addEventListener('change', updateFinalMaterialTemplateOptions);
    dom.finalMaterialAssessmentMode?.addEventListener('change', updateAssessmentMethodDefault);

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
        window.location.href = state.detailExportUrl;
    });

    dom.detailExportPdfBtn?.addEventListener('click', () => {
        if (!state.detailExportPdfUrl) {
            showToast('这份材料暂时没有可导出的 PDF 模板', 'warning');
            return;
        }
        window.location.href = state.detailExportPdfUrl;
    });

    dom.detailContent?.addEventListener('click', async (event) => {
        const button = event.target.closest('[data-action="optimize-final-material"]');
        if (!button || !state.detailItem) return;
        const prompt = dom.detailContent.querySelector('[data-role="final-material-optimize-prompt"]')?.value || '';
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

    loadMaterials().catch((error) => {
        console.error(error);
        dom.list.innerHTML = `<div class="materials-empty">加载材料失败：${escapeHtml(error.message || '未知错误')}</div>`;
    });
}

export async function refresh() {
    if (!config) return;
    await loadMaterials(state.currentParentId);
}
