import { apiFetch } from './api.js';
import { showToast, escapeHtml, formatDate } from './ui.js';
import { openTreeSelectFormModal } from './tree_select_form_modal.js';
import { enhancePromptPoolInput, recordPromptForInput } from './prompt_pool.js';
import { refreshProcessMaterialActionList, setActionButtonBusy, setProcessMaterialModalFormBusy } from './process_material_action_state.js';
import { openProcessMaterialModal as openModal, openProcessMaterialConfirm } from './process_material_modal.js';
import {
    PROCESS_DOCUMENT_IMPORT_ACCEPT,
    PROCESS_DOCUMENT_IMPORT_FORMAT_HINT,
} from './process_material_import_policy.js';
import { setupProcessMaterialImportPicker, setProcessMaterialImportBusyState } from './process_material_file_picker.js';
import { renderProcessImportSummary } from './process_material_import_summary.js';
import { bindProcessMaterialExportDownloadActions } from './process_material_editor_preview.js';
import {
    buildProcessMaterialOfferingTree,
    formatProcessMaterialOfferingOptionLabel,
    getProcessMaterialClassDisplayName,
} from './process_material_offering_tree.js';
import {
    collectTagCounts,
    compareDate,
    compareNumber,
    compareText,
    hasMatchingSelectedTag,
    normalizeFacetValue,
    normalizeSearchText,
    renderActiveFilterPills,
    renderFacetOptions,
    renderTagButtons,
    uniqueFacetValues,
} from './process_material_filters.js';

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
const state = {
    plans: [],
    offerings: [],
    scopeOptions: [],
    search: '',
    filters: {
        scope: '',
        school: '',
        college: '',
        course: '',
        className: '',
    },
    selectedTags: new Set(),
    sort: 'updated_desc',
    polling: null,
};

const STATUS_META = {
    draft: { label: '草稿', tone: 'is-draft' },
    ready: { label: '可用', tone: 'is-ready' },
    generating: { label: '生成中', tone: 'is-busy' },
    parsing: { label: '解析中', tone: 'is-busy' },
    failed: { label: '失败', tone: 'is-failed' },
};

const SOURCE_LABEL = { blank: '表单新建', classroom: '按课堂生成', import: '导入解析', exam_reverse: '试卷反推' };

const root = document.querySelector('[data-ap-root]');

function statusMeta(status) {
    return STATUS_META[status] || STATUS_META.draft;
}

function isBusy(plan) {
    return plan.status === 'generating' || plan.status === 'parsing';
}

function showListRefreshWarning(err) {
    showToast(err?.message || '操作已完成，但列表刷新失败，请手动刷新页面确认最新状态。', 'warning');
}

function formatScoreValue(value) {
    const number = Number(value || 0);
    if (!Number.isFinite(number)) return '0';
    return Number.isInteger(number) ? String(number) : number.toFixed(1).replace(/\.0$/, '');
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------
function renderSummary() {
    const summaryEl = root.querySelector('[data-ap-summary]');
    const total = state.plans.length;
    const mine = state.plans.filter((p) => p.is_owned).length;
    const busy = state.plans.filter(isBusy).length;
    const shared = state.plans.filter((p) => !p.is_owned).length;
    const items = [['全部', total], ['我的', mine], ['进行中', busy], ['他人公开', shared]];
    summaryEl.innerHTML = items
        .map(([label, value]) => `<span class="manage-lp__summary-item"><strong>${value}</strong><small>${label}</small></span>`)
        .join('');
}

function facetText(value) {
    return normalizeSearchText(value);
}

function renderFilterControls() {
    renderFacetOptions(
        root.querySelector('[data-ap-filter-school]'),
        uniqueFacetValues(state.plans, (item) => item.school || item.school_name),
        state.filters.school,
        '全部学校'
    );
    renderFacetOptions(
        root.querySelector('[data-ap-filter-college]'),
        uniqueFacetValues(state.plans, (item) => item.college),
        state.filters.college,
        '全部学院'
    );
    renderFacetOptions(
        root.querySelector('[data-ap-filter-course]'),
        uniqueFacetValues(state.plans, (item) => item.course_name),
        state.filters.course,
        '全部课程'
    );
    renderFacetOptions(
        root.querySelector('[data-ap-filter-class]'),
        uniqueFacetValues(state.plans, (item) => item.class_name),
        state.filters.className,
        '全部班级'
    );
}

function hasAnyFilter() {
    return Boolean(
        state.search.trim()
        || state.filters.scope
        || state.filters.school
        || state.filters.college
        || state.filters.course
        || state.filters.className
        || state.selectedTags.size
        || state.sort !== 'updated_desc'
    );
}

function renderFilterState() {
    renderFilterControls();
    renderTagButtons({
        container: root.querySelector('[data-ap-tags]'),
        tags: collectTagCounts(state.plans),
        selectedTags: state.selectedTags,
        dataAttr: 'data-ap-tag-filter',
    });
    renderActiveFilterPills({
        container: root.querySelector('[data-ap-active-filters]'),
        entries: [
            { label: '搜索', value: state.search },
            { label: '范围', value: root.querySelector('[data-ap-filter-scope]')?.selectedOptions?.[0]?.textContent || '' },
            { label: '学校', value: state.filters.school },
            { label: '学院', value: state.filters.college },
            { label: '课程', value: state.filters.course },
            { label: '班级', value: state.filters.className },
            { label: '标签', value: [...state.selectedTags].join(' / ') },
            { label: '排序', value: state.sort === 'updated_desc' ? '' : root.querySelector('[data-ap-sort]')?.selectedOptions?.[0]?.textContent || '' },
        ].filter((entry) => entry.label !== '范围' || state.filters.scope),
    });
    const clearBtn = root.querySelector('[data-ap-clear-filters]');
    if (clearBtn) clearBtn.hidden = !hasAnyFilter();
}

function matchesFilters(plan) {
    const q = state.search.trim().toLowerCase();
    if (q) {
        const hay = [
            plan.title,
            plan.course_name,
            plan.class_name,
            plan.college,
            plan.school,
            plan.school_name,
            plan.semester_label,
            plan.assessment_type,
            (plan.tags || []).join(' '),
        ]
            .join(' ').toLowerCase();
        if (!hay.includes(q)) return false;
    }
    const f = state.filters.scope;
    if (f === 'mine' && !plan.is_owned) return false;
    else if (f === 'shared' && plan.is_owned) return false;
    else if (f && !['mine', 'shared'].includes(f) && plan.scope_level !== f) return false;
    if (state.filters.school && facetText(plan.school || plan.school_name) !== facetText(state.filters.school)) return false;
    if (state.filters.college && facetText(plan.college) !== facetText(state.filters.college)) return false;
    if (state.filters.course && facetText(plan.course_name) !== facetText(state.filters.course)) return false;
    if (state.filters.className && facetText(plan.class_name) !== facetText(state.filters.className)) return false;
    return hasMatchingSelectedTag(plan.tags, state.selectedTags);
}

function sortPlans(plans) {
    const copy = [...plans];
    switch (state.sort) {
        case 'updated_asc': return copy.sort((a, b) => compareDate(a.updated_at, b.updated_at, 'asc'));
        case 'score_desc': return copy.sort((a, b) => compareNumber(a.score_total, b.score_total, 'desc') || compareDate(a.updated_at, b.updated_at, 'desc'));
        case 'score_asc': return copy.sort((a, b) => compareNumber(a.score_total, b.score_total, 'asc') || compareDate(a.updated_at, b.updated_at, 'desc'));
        case 'title_asc': return copy.sort((a, b) => compareText(a.title, b.title, 'asc'));
        case 'title_desc': return copy.sort((a, b) => compareText(a.title, b.title, 'desc'));
        default: return copy.sort((a, b) => compareDate(a.updated_at, b.updated_at, 'desc'));
    }
}

function progressText(plan) {
    const p = plan.ai_gen_progress || {};
    const label = p.current_label ? escapeHtml(p.current_label) : '';
    if (label) return label;
    if (plan.status === 'parsing') return 'AI 正在解析导入文件…';
    if (plan.source_type === 'classroom') return 'AI 正在根据课堂资料生成考核计划表…';
    if (plan.source_type === 'exam_reverse') return 'AI 正在根据试卷反推考核计划表…';
    return 'AI 正在生成考核计划表…';
}

function scoreBadge(plan) {
    const total = plan.score_total || 0;
    const ok = plan.score_balanced;
    const tone = ok ? 'is-ready' : 'is-failed';
    const text = ok ? `分值 ${total}` : `分值 ${total}≠100`;
    return `<span class="lp-status ${tone}" title="考核项分值合计">${text}</span>`;
}

function renderFailedActions(plan) {
    if (!plan.can_manage) {
        return `<small>来源教师需处理该失败记录</small>`;
    }
    return `<button type="button" class="lp-btn lp-btn--danger" data-action="delete" data-id="${plan.id}">删除</button>`;
}

function renderCard(plan) {
    const meta = statusMeta(plan.status);
    const tags = (plan.tags || []).map((t) => `<span class="lp-tag">${escapeHtml(t)}</span>`).join('');
    const sourceBadge = `<span class="lp-source">${SOURCE_LABEL[plan.source_type] || '考核计划表'}</span>`;
    const scopeBadge = `<span class="lp-scope">${escapeHtml(plan.scope_label || '私有')}</span>`;
    const importSummary = renderProcessImportSummary(plan);

    if (isBusy(plan)) {
        const p = plan.ai_gen_progress || {};
        const pct = p.total ? Math.round((Number(p.done || 0) / Number(p.total)) * 100) : 35;
        return `
        <article class="lp-card lp-card--busy ${meta.tone}" data-ap-card="${plan.id}">
            <div class="lp-card__top"><span class="lp-status ${meta.tone}">${meta.label}</span>${sourceBadge}</div>
            <strong class="lp-card__title">${escapeHtml(plan.title)}</strong>
            <div class="lp-progress"><div class="lp-progress__bar" style="width:${pct}%"></div></div>
            <p class="lp-card__busy">${progressText(plan)}</p>
            ${importSummary}
            <div class="lp-card__foot">
                <small>请稍候，可能需要几分钟…</small>
                <button type="button" class="lp-btn lp-btn--ghost" data-action="delete" data-id="${plan.id}">取消并删除</button>
            </div>
        </article>`;
    }

    if (plan.status === 'failed') {
        return `
        <article class="lp-card lp-card--failed" data-ap-card="${plan.id}">
            <div class="lp-card__top"><span class="lp-status is-failed">失败</span>${sourceBadge}</div>
            <strong class="lp-card__title">${escapeHtml(plan.title)}</strong>
            <p class="lp-card__error">${escapeHtml(plan.ai_gen_error || '生成/解析失败')}</p>
            ${importSummary}
            <div class="lp-card__foot">
                ${renderFailedActions(plan)}
            </div>
        </article>`;
    }

    const owner = plan.is_owned ? '' : `<span class="lp-owner">来自 ${escapeHtml(plan.owner_teacher_name || '其他老师')}</span>`;
    const sig = [];
    if (plan.examiner_signature) sig.push('命题签名');
    if (plan.reviewer_signature) sig.push('系主任签名');
    const sigBadge = sig.length ? `<span class="lp-tag">${sig.join(' · ')}</span>` : '';
    const manageActions = plan.can_manage ? `
        <button type="button" class="lp-btn" data-action="edit" data-id="${plan.id}">编辑</button>
        <button type="button" class="lp-btn" data-action="preview" data-id="${plan.id}">预览/导出</button>
        <button type="button" class="lp-btn lp-btn--ghost" data-action="attributes" data-id="${plan.id}">属性</button>
        <button type="button" class="lp-btn lp-btn--ghost" data-action="tags" data-id="${plan.id}">标签</button>
        <button type="button" class="lp-btn lp-btn--danger" data-action="delete" data-id="${plan.id}">删除</button>
    ` : `
        <button type="button" class="lp-btn" data-action="preview" data-id="${plan.id}">预览/导出</button>
        <button type="button" class="lp-btn lp-btn--primary" data-action="inherit" data-id="${plan.id}">一键继承</button>
    `;

    return `
        <article class="lp-card" data-ap-card="${plan.id}">
            <div class="lp-card__top"><span class="lp-status ${meta.tone}">${meta.label}</span>${sourceBadge}${scopeBadge}${scoreBadge(plan)}</div>
            <strong class="lp-card__title">${escapeHtml(plan.title)}</strong>
            <div class="lp-card__meta">
                ${plan.course_name ? `<span>${escapeHtml(plan.course_name)}</span>` : ''}
                ${plan.class_name ? `<span>${escapeHtml(plan.class_name)}</span>` : ''}
                ${plan.assessment_type ? `<span>${escapeHtml(plan.assessment_type)}</span>` : ''}
                <span>${plan.item_count || 0} 考核项</span>
            </div>
            ${importSummary}
            ${(tags || sigBadge) ? `<div class="lp-card__tags">${tags}${sigBadge}</div>` : ''}
            <div class="lp-card__foot">
                <small>${owner || ('更新于 ' + escapeHtml(formatDate(plan.updated_at)))}</small>
            </div>
            <div class="lp-card__actions">${manageActions}</div>
        </article>`;
}

function render() {
    renderSummary();
    renderFilterState();
    const grid = root.querySelector('[data-ap-grid]');
    const loading = root.querySelector('[data-ap-loading]');
    const empty = root.querySelector('[data-ap-empty]');
    loading.hidden = true;
    const visible = sortPlans(state.plans.filter(matchesFilters));
    if (!state.plans.length) {
        grid.innerHTML = '';
        grid.hidden = true;
        empty.hidden = false;
        return;
    }
    empty.hidden = true;
    grid.hidden = false;
    grid.innerHTML = visible.length
        ? visible.map(renderCard).join('')
        : `<div class="manage-lp__empty" style="grid-column:1/-1">没有符合筛选条件的考核计划表。</div>`;
}

function clearFilters() {
    state.search = '';
    state.filters = { scope: '', school: '', college: '', course: '', className: '' };
    state.selectedTags.clear();
    state.sort = 'updated_desc';
    const search = root.querySelector('[data-ap-search]');
    if (search) search.value = '';
    const scope = root.querySelector('[data-ap-filter-scope]');
    if (scope) scope.value = '';
    const sort = root.querySelector('[data-ap-sort]');
    if (sort) sort.value = state.sort;
    render();
}

// ---------------------------------------------------------------------------
// Data
// ---------------------------------------------------------------------------
async function loadPlans() {
    try {
        const data = await apiFetch('/api/assessment-plans');
        state.plans = data.assessment_plans || [];
        render();
        managePolling();
    } catch (err) {
        showToast(err.message || '加载考核计划表失败', 'error');
    }
}

function managePolling() {
    const hasBusy = state.plans.some(isBusy);
    if (hasBusy && !state.polling) {
        state.polling = setInterval(loadPlans, 4000);
    } else if (!hasBusy && state.polling) {
        clearInterval(state.polling);
        state.polling = null;
    }
}

// ---------------------------------------------------------------------------
// Modals
// ---------------------------------------------------------------------------
function offeringOptionsHtml(includeBlank) {
    const blank = includeBlank ? ['<option value="">不绑定课堂（手动填写）</option>'] : [];
    return blank
        .concat(state.offerings.map((o) => {
            const label = formatProcessMaterialOfferingOptionLabel(o);
            return `<option value="${o.id}">${escapeHtml(label)}</option>`;
        }))
        .join('');
}

function openCreateBlankModal() {
    const body = `
        <form data-ap-form-blank class="lp-form">
            <label>标题<input name="title" placeholder="如：服务器配置与管理 考核计划表"></label>
            <label>绑定课堂（自动带入课程/班级/考核类型，可选）
                <select name="class_offering_id">${offeringOptionsHtml(true)}</select>
            </label>
            <p class="lp-form__hint">创建后进入编辑器，用完整表单填写基础信息和考核项目（分值合计须为 100）。</p>
        </form>`;
    const footer = `<button type="button" class="lp-btn lp-btn--primary" data-ap-submit>创建并编辑</button>`;
    let createBusy = false;
    const setCreateBusy = (overlay, busy) => {
        createBusy = Boolean(busy);
        setProcessMaterialModalFormBusy(overlay, createBusy);
    };
    openModal('空白新建考核计划表', body, {
        footerHtml: footer,
        onMount: (overlay, close) => {
            const submit = overlay.querySelector('[data-ap-submit]');
            submit.addEventListener('click', async () => {
                if (createBusy || submit.disabled) return;
                const fd = new FormData(overlay.querySelector('[data-ap-form-blank]'));
                const payload = {
                    title: (fd.get('title') || '').trim(),
                    class_offering_id: fd.get('class_offering_id') || null,
                };
                setActionButtonBusy(submit, true, '正在创建…');
                setCreateBusy(overlay, true);
                try {
                    const res = await apiFetch('/api/assessment-plans', { method: 'POST', body: payload });
                    close({ force: true });
                    window.location.href = `/assessment-plan/${res.id}/edit`;
                } catch (err) {
                    showToast(err.message || '创建失败', 'error');
                    setCreateBusy(overlay, false);
                    setActionButtonBusy(submit, false);
                }
            });
        },
        canClose: () => !createBusy,
    });
}

async function offeringPanelDescriptor(offering) {
    let fields = {};
    try {
        const res = await apiFetch(`/api/assessment-plans/classroom/${offering.id}/prefill`);
        fields = res.fields || {};
    } catch (_) { fields = {}; }
    const semesterText = [fields.academic_year, fields.semester].filter(Boolean).join(' ')
        || offering.semester_label || '—';
    const courseName = fields.course_name || offering.course_name || '';
    const className = fields.class_name || getProcessMaterialClassDisplayName(offering);
    return {
        title: '生成配置',
        baseInfo: [
            { label: '学年学期', value: semesterText },
            { label: '课程名称', value: courseName },
            { label: '授课班级', value: className },
            { label: '课堂编号', value: offering.id },
        ],
        note: '基础信息由所选课堂自动带入；下方字段会写入考核计划表，可按学校纸质表要求微调。',
        fields: [
            { key: 'school', label: '学校', value: fields.school || '广西外国语学院' },
            { key: 'academic_year', label: '学年', value: fields.academic_year || '', placeholder: '如：2025-2026' },
            {
                key: 'semester',
                label: '学期',
                type: 'select',
                value: fields.semester || '',
                options: ['第一学期', '第二学期', '第三学期'],
            },
            { key: 'course_name', label: '课程名称', value: courseName, placeholder: '如：服务器配置与管理' },
            { key: 'class_name', label: '专业年级班级', value: className, placeholder: '如：软工2401班' },
            {
                key: 'assessment_type',
                label: '考核类型',
                type: 'select',
                value: fields.assessment_type || '',
                options: ['考试', '考查'],
            },
            { key: 'assessment_method', label: '考核形式', value: fields.assessment_method || '', placeholder: '如：机试 / 闭卷笔试 / 项目实操' },
            { key: 'examiner_name', label: '命题教师', value: fields.examiner_name || fields.teacher_name || '' },
            { key: 'reviewer_name', label: '系主任审核签字', value: fields.reviewer_name || '', placeholder: '可留空，线下签字' },
            { key: 'date', label: '命题日期', value: fields.date || '', placeholder: '如：2026年06月20日' },
        ],
    };
}

function openGenerateModal() {
    if (!state.offerings.length) {
        showToast('你还没有可用课堂，请先在「开设课堂」创建。', 'error');
        return;
    }
    openTreeSelectFormModal({
        title: '按课堂生成考核计划表',
        subtitle: '先定位学年学期，再定位课程和班级',
        tree: buildProcessMaterialOfferingTree(state.offerings),
        treeTitle: '学年学期 / 课程 / 班级',
        treeHint: '按最新学期排序',
        levelLabels: ['学期', '课程', '班级'],
        placeholderTitle: '请选择课堂',
        placeholderText: '请在左侧选择「学年学期 → 课程 → 班级」，选中班级后在此配置并生成。',
        emptyText: '你还没有可用课堂。',
        promptLabel: '给 AI 的补充要求（可选）',
        promptPlaceholder: '如：以机试为主，重点考核 Linux 与数据库部署，分值合计 100。',
        promptPoolKey: 'assessment_plan.generate_from_classroom',
        confirmLabel: '确定并生成',
        hintHtml: '系统将整合课堂内容、绑定文档、教材与教务考核形式，<strong>用思考型 AI</strong>生成考核计划表；生成后仍可在编辑器中调整考核项，导出前分值合计须为 100。',
        onSelect: (offering) => offeringPanelDescriptor(offering),
        onConfirm: async ({ data, fieldValues, prompt }) => {
            try {
                await apiFetch('/api/assessment-plans/generate', {
                    method: 'POST',
                    body: {
                        class_offering_id: Number(data.id),
                        prompt: (prompt || '').trim(),
                        fields: fieldValues || {},
                    },
                });
                showToast('已开始生成，列表中将显示进度。', 'success');
                loadPlans();
                return true;
            } catch (err) {
                showToast(err.message || '启动生成失败', 'error');
                return false;
            }
        },
    });
}

function renderImportRetryNote(id) {
    if (!id) return '';
    const plan = state.plans.find((item) => String(item.id) === String(id));
    const sourceTitle = plan?.import_summary?.source_file_title || plan?.title || '这条失败记录';
    return `
        <div class="lp-import-retry-note" role="note">
            <strong>重新上传模式</strong>
            <span>本次会新建一条解析任务，不会覆盖「${escapeHtml(sourceTitle)}」；新任务成功后，可返回列表删除旧失败记录。</span>
        </div>`;
}

function openImportModal({ retryingFailedId = null } = {}) {
    const retryNote = renderImportRetryNote(retryingFailedId);
    const body = `
        <div class="lp-import">
            ${retryNote}
            <div class="lp-dropzone" data-ap-dropzone>
                <p>拖拽文件到此处，或<button type="button" class="lp-link" data-ap-pick>点击选择文件</button></p>
                <small>${escapeHtml(PROCESS_DOCUMENT_IMPORT_FORMAT_HINT)} 单文件 ≤ 30MB，最多 8 个。docx 中的签名图片会自动入签名库（去重）。</small>
                <input type="file" data-ap-file multiple hidden
                       accept="${PROCESS_DOCUMENT_IMPORT_ACCEPT}">
            </div>
            <div class="lp-import-policy" data-ap-import-policy>
                <strong>解析后校验</strong>
                <span>系统会检查考核项分值合计，未达到 100 分会提示补齐并阻止导出。</span>
            </div>
            <ul class="lp-filelist" data-ap-filelist></ul>
            <div class="lp-import-selection" id="ap-import-selection-status" data-ap-import-selection>
                <span id="ap-import-selection-message" class="lp-import-selection__message" data-selection-message role="status" aria-live="polite">尚未选择文件，请先选择要导入解析的文件。</span>
            </div>
            <label class="lp-form__full">给 AI 的额外提示（可选）
                <textarea data-ap-extra data-prompt-pool-key="assessment_plan.import" rows="3" placeholder="如：这是《服务器配置与管理》机试考核计划表，请忠实还原考核项与分值。"></textarea>
            </label>
            <p class="lp-form__hint">点击导入后将调用<strong>思考 + 多模态 AI</strong>解析，并自动归集签名图片。窗口会关闭并在列表中以占位卡显示「解析中」。</p>
        </div>`;
    const footer = `<button type="button" class="lp-btn lp-btn--primary" data-ap-submit aria-describedby="ap-import-selection-message">开始导入解析</button>`;
    let importBusy = false;
    const setImportBusy = (overlay, busy) => {
        importBusy = Boolean(busy);
        setProcessMaterialImportBusyState(overlay, importBusy);
    };
    openModal(retryingFailedId ? '重新上传考核计划表文件' : '导入考核计划表文件', body, {
        footerHtml: footer,
        onMount: (overlay, close) => {
            const promptInput = overlay.querySelector('[data-ap-extra]');
            enhancePromptPoolInput(promptInput);
            const submit = overlay.querySelector('[data-ap-submit]');
            const filePicker = setupProcessMaterialImportPicker({
                overlay,
                inputSelector: '[data-ap-file]',
                pickSelector: '[data-ap-pick]',
                listSelector: '[data-ap-filelist]',
                dropzoneSelector: '[data-ap-dropzone]',
                selectionSelector: '[data-ap-import-selection]',
                submitSelector: '[data-ap-submit]',
                showToast,
            });
            submit.addEventListener('click', async () => {
                if (submit.disabled) return;
                if (!filePicker.hasFiles()) {
                    showToast('请先选择文件', 'error');
                    filePicker.updateSubmitState();
                    return;
                }
                const fd = new FormData();
                filePicker.getFiles().forEach((f) => fd.append('files', f));
                fd.append('extra_prompt', overlay.querySelector('[data-ap-extra]').value || '');
                setImportBusy(overlay, true);
                setActionButtonBusy(submit, true, '正在解析…');
                filePicker.updateSubmitState();
                try {
                    await apiFetch('/api/assessment-plans/import', { method: 'POST', body: fd });
                    try { await recordPromptForInput(promptInput); } catch (_) { /* prompt pool recording is best effort */ }
                    close({ force: true });
                    showToast(
                        retryingFailedId
                            ? '已重新上传并开始解析，原失败记录仍保留，可稍后删除。'
                            : '已开始解析，列表中将显示进度。',
                        'success',
                    );
                    loadPlans();
                } catch (err) {
                    showToast(err.message || '导入失败', 'error');
                    setActionButtonBusy(submit, false);
                    setImportBusy(overlay, false);
                    filePicker.updateSubmitState();
                }
            });
        },
        canClose: () => !importBusy,
    });
}

async function openAttributesModal(id) {
    let data;
    try { data = await apiFetch(`/api/assessment-plans/${id}/attributes`); }
    catch (err) { showToast(err.message || '读取属性失败', 'error'); return; }
    const scopeOptions = (data.scope_options || state.scopeOptions)
        .map((o) => `<option value="${o.value}"${o.value === data.scope_level ? ' selected' : ''}>${escapeHtml(o.label)}</option>`)
        .join('');
    const body = `
        <form data-ap-form-attr class="lp-form">
            <label>标题<input name="title" value="${escapeHtml(data.title || '')}"></label>
            <label>公开范围
                <select name="scope_level">${scopeOptions}</select>
            </label>
            <p class="lp-form__hint">默认私有；可设为本系部 / 本院级 / 全校公开，公开后其他老师可一键继承。</p>
        </form>`;
    const footer = `<button type="button" class="lp-btn lp-btn--primary" data-ap-submit>保存</button>`;
    let modalBusy = false;
    const setModalBusy = (overlay, busy) => {
        modalBusy = Boolean(busy);
        setProcessMaterialModalFormBusy(overlay, modalBusy);
    };
    openModal('考核计划表属性', body, {
        footerHtml: footer,
        onMount: (overlay, close) => {
            const submit = overlay.querySelector('[data-ap-submit]');
            submit.addEventListener('click', async () => {
                if (modalBusy || submit.disabled) return;
                const fd = new FormData(overlay.querySelector('[data-ap-form-attr]'));
                setActionButtonBusy(submit, true, '正在保存…');
                setModalBusy(overlay, true);
                try {
                    await apiFetch(`/api/assessment-plans/${id}/attributes`, {
                        method: 'PATCH',
                        body: { title: (fd.get('title') || '').trim(), scope_level: fd.get('scope_level') },
                    });
                    close({ force: true });
                    showToast('已保存', 'success');
                    loadPlans();
                } catch (err) {
                    showToast(err.message || '保存失败', 'error');
                    setModalBusy(overlay, false);
                    setActionButtonBusy(submit, false);
                }
            });
        },
        canClose: () => !modalBusy,
    });
}

async function openTagsModal(id) {
    const plan = state.plans.find((p) => p.id === id);
    const current = (plan?.tags || []).join('、');
    const body = `
        <form data-ap-form-tags class="lp-form">
            <label>标签（用、或逗号分隔）
                <input name="tags" value="${escapeHtml(current)}" placeholder="如：机试、2025秋、专升本">
            </label>
        </form>`;
    const footer = `<button type="button" class="lp-btn lp-btn--primary" data-ap-submit>保存标签</button>`;
    let modalBusy = false;
    const setModalBusy = (overlay, busy) => {
        modalBusy = Boolean(busy);
        setProcessMaterialModalFormBusy(overlay, modalBusy);
    };
    openModal('设置标签', body, {
        footerHtml: footer,
        onMount: (overlay, close) => {
            const submit = overlay.querySelector('[data-ap-submit]');
            submit.addEventListener('click', async () => {
                if (modalBusy || submit.disabled) return;
                const raw = new FormData(overlay.querySelector('[data-ap-form-tags]')).get('tags') || '';
                const tags = raw.split(/[、,，\s]+/).map((t) => t.trim()).filter(Boolean);
                setActionButtonBusy(submit, true, '正在保存…');
                setModalBusy(overlay, true);
                try {
                    await apiFetch(`/api/assessment-plans/${id}/tags`, { method: 'PUT', body: { tags } });
                    close({ force: true });
                    showToast('标签已更新', 'success');
                    loadPlans();
                } catch (err) {
                    showToast(err.message || '保存失败', 'error');
                    setModalBusy(overlay, false);
                    setActionButtonBusy(submit, false);
                }
            });
        },
        canClose: () => !modalBusy,
    });
}

function openPreviewModal(id) {
    const plan = state.plans.find((p) => p.id === id);
    const title = plan ? plan.title : '考核计划表预览';
    const exportActions = renderAssessmentPreviewExportActions(plan, id);
    const body = `
        <div class="lp-preview">
            <div class="lp-preview__bar">
                ${exportActions}
            </div>
            <iframe class="lp-preview__frame" src="/assessment-plan/${id}/preview" title="考核计划表预览"></iframe>
        </div>`;
    openModal(`${title} · 渲染预览`, body, {
        wide: true,
        onMount: (overlay) => bindProcessMaterialExportDownloadActions(overlay, showToast, { saved: false }),
    });
}

function renderAssessmentPreviewExportActions(plan, id) {
    if (plan?.score_balanced === false) {
        const reason = `考核项分值合计为 ${formatScoreValue(plan.score_total)}，调整到 100 后才能导出。`;
        return `
            <span>导出：</span>
            <button type="button" class="lp-btn lp-btn--disabled" disabled title="${escapeHtml(reason)}">Word (.docx)</button>
            <button type="button" class="lp-btn lp-btn--disabled" disabled title="${escapeHtml(reason)}">PDF (.pdf)</button>
            <span class="lp-preview__notice">${escapeHtml(reason)}</span>
            <a class="lp-btn lp-btn--ghost" href="/assessment-plan/${id}/preview" target="_blank" rel="noopener">在新标签页打开</a>`;
    }
    return `
        <span>导出：</span>
        <button type="button" class="lp-btn" data-process-export-url="/api/assessment-plans/${id}/export?fmt=docx" data-process-export-label="Word">Word (.docx)</button>
        <button type="button" class="lp-btn" data-process-export-url="/api/assessment-plans/${id}/export?fmt=pdf" data-process-export-label="PDF">PDF (.pdf)</button>
        <a class="lp-btn lp-btn--ghost" href="/assessment-plan/${id}/preview" target="_blank" rel="noopener">在新标签页打开</a>`;
}

async function inheritPlan(id, trigger) {
    if (trigger?.disabled) return;
    const confirmed = await openProcessMaterialConfirm({
        title: '继承公开考核计划表',
        message: '将这份公开考核计划表继承为你的私有副本？',
        detail: '继承后命题信息会替换为你的信息，内容可继续调整。',
        confirmText: '确认继承',
    });
    if (!confirmed) return;
    setActionButtonBusy(trigger, true, '正在继承…');
    try {
        const res = await apiFetch(`/api/assessment-plans/${id}/inherit`, { method: 'POST' });
        showToast('已继承到你的库', 'success');
        window.location.href = `/assessment-plan/${res.id}/edit`;
    } catch (err) {
        showToast(err.message || '继承失败', 'error');
        setActionButtonBusy(trigger, false);
    }
}

async function retryPlan(id, trigger) {
    if (trigger?.disabled) return;
    setActionButtonBusy(trigger, true, '正在重试…');
    try {
        await apiFetch(`/api/assessment-plans/${id}/retry`, { method: 'POST' });
        showToast('已重新开始', 'success');
        await refreshProcessMaterialActionList(trigger, loadPlans, showListRefreshWarning);
    } catch (err) {
        showToast(err.message || '重试失败', 'error');
        setActionButtonBusy(trigger, false);
    }
}

async function deletePlan(id, trigger) {
    if (trigger?.disabled) return;
    const confirmed = await openProcessMaterialConfirm({
        title: '删除考核计划表',
        message: '确定删除该考核计划表？',
        detail: '删除后无法恢复，已生成的预览和导出入口也会一并失效。',
        confirmText: '删除',
        tone: 'danger',
    });
    if (!confirmed) return;
    setActionButtonBusy(trigger, true, '正在删除…');
    try {
        await apiFetch(`/api/assessment-plans/${id}`, { method: 'DELETE' });
        showToast('已删除', 'success');
        await refreshProcessMaterialActionList(trigger, loadPlans, showListRefreshWarning);
    } catch (err) {
        showToast(err.message || '删除失败', 'error');
        setActionButtonBusy(trigger, false);
    }
}

// ---------------------------------------------------------------------------
// Events
// ---------------------------------------------------------------------------
function bindEvents() {
    document.addEventListener('click', (e) => {
        if (e.target.closest('[data-ap-create-blank]')) { e.preventDefault(); openCreateBlankModal(); return; }
        if (e.target.closest('[data-ap-generate-open]')) { e.preventDefault(); openGenerateModal(); return; }
        if (e.target.closest('[data-ap-import-open]')) { e.preventDefault(); openImportModal(); }
    });

    const search = root.querySelector('[data-ap-search]');
    search.addEventListener('input', () => { state.search = search.value; render(); });
    root.querySelector('[data-ap-filter-scope]').addEventListener('change', (e) => { state.filters.scope = e.target.value; render(); });
    root.querySelector('[data-ap-filter-school]').addEventListener('change', (e) => { state.filters.school = normalizeFacetValue(e.target.value); render(); });
    root.querySelector('[data-ap-filter-college]').addEventListener('change', (e) => { state.filters.college = normalizeFacetValue(e.target.value); render(); });
    root.querySelector('[data-ap-filter-course]').addEventListener('change', (e) => { state.filters.course = normalizeFacetValue(e.target.value); render(); });
    root.querySelector('[data-ap-filter-class]').addEventListener('change', (e) => { state.filters.className = normalizeFacetValue(e.target.value); render(); });
    root.querySelector('[data-ap-sort]').addEventListener('change', (e) => { state.sort = e.target.value; render(); });
    root.querySelector('[data-ap-clear-filters]').addEventListener('click', clearFilters);
    root.querySelector('[data-ap-tags]').addEventListener('click', (e) => {
        const btn = e.target.closest('[data-ap-tag-filter]');
        if (!btn) return;
        const tag = normalizeFacetValue(btn.dataset.apTagFilter);
        if (!tag) return;
        if (state.selectedTags.has(tag)) state.selectedTags.delete(tag);
        else state.selectedTags.add(tag);
        render();
    });

    root.querySelector('[data-ap-grid]').addEventListener('click', (e) => {
        const btn = e.target.closest('[data-action]');
        if (!btn) return;
        const id = btn.dataset.id;
        switch (btn.dataset.action) {
            case 'edit': window.location.href = `/assessment-plan/${id}/edit`; break;
            case 'preview': openPreviewModal(id); break;
            case 'attributes': openAttributesModal(id); break;
            case 'tags': openTagsModal(id); break;
            case 'delete': deletePlan(id, btn); break;
            case 'retry': retryPlan(id, btn); break;
            case 'import-again': openImportModal({ retryingFailedId: id }); break;
            case 'inherit': inheritPlan(id, btn); break;
        }
    });
}

function boot() {
    if (!root) return;
    try {
        const bootEl = document.getElementById('assessment-plan-boot');
        const data = bootEl ? JSON.parse(bootEl.textContent) : {};
        state.offerings = data.offerings || [];
        state.scopeOptions = data.scope_options || [];
    } catch (_) { /* ignore boot parse errors */ }
    bindEvents();
    loadPlans();
}

boot();
