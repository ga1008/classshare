import { apiFetch } from './api.js';
import { enhancePromptPoolInput, recordPromptForInput } from './prompt_pool.js';

const root = document.querySelector('[data-afm-root]');
if (!root) throw new Error('Academic final-material root not found.');

const type = root.dataset.documentType;
const typeLabel = root.dataset.documentLabel;
const isGrade = type === 'academic_grade_register';
const $ = (selector, scope = document) => scope.querySelector(selector);
const $$ = (selector, scope = document) => Array.from(scope.querySelectorAll(selector));

const state = {
    items: [],
    candidates: [],
    signatures: [],
    currentBatchId: '',
    currentRecord: null,
    currentPreviewUrl: '',
    pendingClassOfferingId: 0,
    pendingExamCandidates: [],
};

const els = {
    grid: $('[data-afm-grid]'),
    empty: $('[data-afm-empty]'),
    loading: $('[data-afm-loading]'),
    summary: $('[data-afm-summary]'),
    search: $('[data-afm-search]'),
    syncDialog: $('[data-afm-sync-dialog]'),
    syncForm: $('[data-afm-sync-form]'),
    courseSearch: $('[data-afm-course-search]'),
    courseList: $('[data-afm-course-list]'),
    force: $('[data-afm-force]'),
    syncSubmit: $('[data-afm-sync-submit]'),
    syncHint: $('[data-afm-sync-hint]'),
    editorDialog: $('[data-afm-editor-dialog]'),
    editorForm: $('[data-afm-editor-form]'),
    editorTitle: $('[data-afm-editor-title]'),
    editorSubtitle: $('[data-afm-editor-subtitle]'),
    identity: $('[data-afm-identity]'),
    gradeFields: $('[data-afm-grade-fields]'),
    analysisFields: $('[data-afm-analysis-fields]'),
    teacherSignature: $('[data-afm-teacher-signature]'),
    departmentSignature: $('[data-afm-department-signature]'),
    deanSignature: $('[data-afm-dean-signature]'),
    analysisText: $('[data-afm-analysis-text]'),
    regeneratePrompt: $('[data-afm-regenerate-prompt]'),
    regenerate: $('[data-afm-regenerate]'),
    previewCurrent: $('[data-afm-preview-current]'),
    previewDialog: $('[data-afm-preview-dialog]'),
    previewFrame: $('[data-afm-preview-frame]'),
    previewTitle: $('[data-afm-preview-title]'),
};

function escapeHtml(value) {
    return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#039;');
}

function message(text, kind = 'success') {
    if (typeof window.showMessage === 'function') {
        window.showMessage(text, kind);
    } else {
        window.alert(text);
    }
}

function setBusy(button, busy, busyText = '处理中…') {
    if (!button) return;
    if (busy) {
        button.dataset.label = button.textContent;
        button.disabled = true;
        button.innerHTML = `<span class="afm-spinner" style="width:16px;height:16px;border-width:2px"></span>${escapeHtml(busyText)}`;
    } else {
        button.disabled = false;
        button.textContent = button.dataset.label || button.textContent;
    }
}

function statusLabel(status) {
    return {
        completed: '已就绪',
        processing: '正在入库',
        running: '正在同步',
        validation_failed: '校验未通过',
        grades_missing: '成绩未提交',
        needs_attention: '需要处理',
        needs_confirmation: '待确认课程',
        failed: '同步失败',
        not_synced: '尚未同步',
    }[status] || status || '尚未同步';
}

function formatTime(value) {
    if (!value) return '尚未同步';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return date.toLocaleString('zh-CN', { hour12: false });
}

function filteredItems() {
    const query = String(els.search?.value || '').trim().toLocaleLowerCase();
    if (!query) return state.items;
    return state.items.filter((item) => [
        item.course_name,
        item.teaching_class_name,
        item.academic_year,
        item.academic_term,
    ].join(' ').toLocaleLowerCase().includes(query));
}

function renderCards() {
    const items = filteredItems();
    els.loading.hidden = true;
    els.grid.hidden = !items.length;
    els.empty.hidden = Boolean(items.length) || Boolean(state.items.length);
    const isDocumentComplete = (item) => Boolean(
        isGrade ? item.edit_state?.grade_complete : item.edit_state?.analysis_complete
    );
    const ready = state.items.filter((item) => (
        item.sync_status === 'completed' && item.record_id && isDocumentComplete(item)
    )).length;
    els.summary.textContent = state.items.length
        ? `共 ${state.items.length} 门课堂 · ${ready} 份已可预览下载`
        : '暂无同步记录';
    els.grid.innerHTML = items.map((item) => {
        const readyItem = item.sync_status === 'completed' && item.record_id;
        const documentComplete = readyItem && isDocumentComplete(item);
        const action = readyItem
            ? `
                <button type="button" class="afm-btn afm-btn--primary" data-afm-edit="${escapeHtml(item.id)}">${documentComplete ? '查看与调整' : '补全与签名'}</button>
                <button type="button" class="afm-btn afm-btn--ghost" data-afm-preview="${escapeHtml(item.preview_url)}" data-title="${escapeHtml(item.course_name)}">预览</button>
                ${documentComplete
                    ? `<a class="afm-btn afm-btn--ghost" href="${escapeHtml(item.export_url)}">下载 Word</a>`
                    : '<button type="button" class="afm-btn afm-btn--ghost" disabled title="请先补齐必填项和签名">补全后下载</button>'}
              `
            : `<button type="button" class="afm-btn afm-btn--primary" data-afm-resync="${item.class_offering_id}">处理并同步</button>`;
        return `
            <article class="afm-card">
                <div class="afm-card__top">
                    <div class="afm-card__doc">
                        <div class="afm-card__icon">${isGrade ? '绩' : '析'}</div>
                        <div>
                            <h3 title="${escapeHtml(item.course_name)}">${escapeHtml(item.course_name || '未命名课程')}</h3>
                            <p class="afm-card__class">${escapeHtml(item.teaching_class_name || '待确认教学班')}</p>
                        </div>
                    </div>
                    <span class="afm-status afm-status--${escapeHtml(item.sync_status)}">${escapeHtml(
                        readyItem ? (documentComplete ? '可提交' : '待补全') : statusLabel(item.sync_status)
                    )}</span>
                </div>
                <div class="afm-card__meta">
                    <div><span>学年学期</span><strong>${escapeHtml([item.academic_year, item.academic_term].filter(Boolean).join(' ') || '同步后识别')}</strong></div>
                    <div><span>最近同步</span><strong>${escapeHtml(formatTime(item.synced_at))}</strong></div>
                    <div><span>成绩状态</span><strong>${escapeHtml(item.grade_entry_status || '待教务确认')}</strong></div>
                    <div><span>双表校验</span><strong>${item.validation_status === 'passed' ? '✓ 已通过' : escapeHtml(item.validation_status || '待校验')}</strong></div>
                </div>
                ${item.last_error ? `<div class="afm-card__error">${escapeHtml(item.last_error)}</div>` : ''}
                <div class="afm-card__actions">${action}</div>
            </article>`;
    }).join('');
}

async function loadItems() {
    els.loading.hidden = false;
    try {
        const data = await apiFetch(`/api/academic-final-materials?document_type=${encodeURIComponent(type)}`);
        state.items = Array.isArray(data.items) ? data.items : [];
        renderCards();
    } catch (error) {
        els.loading.hidden = true;
        els.empty.hidden = false;
        message(error.message || '读取期末材料失败。', 'error');
    }
}

function candidateStateLabel(candidate) {
    if (candidate.sync_status === 'completed') return '已同步';
    if (candidate.sync_status === 'grades_missing') return '成绩未提交';
    if (candidate.sync_status === 'failed') return '上次失败';
    return '可同步';
}

function renderCandidates() {
    const query = String(els.courseSearch.value || '').trim().toLocaleLowerCase();
    const items = state.candidates.filter((item) => !query || [
        item.course_name,
        item.class_name,
        item.teaching_class_name,
        item.semester,
    ].join(' ').toLocaleLowerCase().includes(query));
    els.courseList.innerHTML = items.length ? items.map((item) => `
        <label class="afm-course-option">
            <input type="radio" name="afm-course" value="${item.class_offering_id}">
            <span>
                <strong>${escapeHtml(item.course_name || '未命名课程')}</strong>
                <small>${escapeHtml(item.teaching_class_name || item.class_name || '未命名班级')} · ${escapeHtml(item.semester || '未设置学期')}</small>
            </span>
            <em>${escapeHtml(candidateStateLabel(item))}</em>
        </label>
    `).join('') : '<div class="afm-empty" style="min-height:150px"><strong>没有匹配的课堂</strong><p>可先在课堂管理中创建或同步课程。</p></div>';
}

function renderExamCourseCandidates() {
    const items = state.pendingExamCandidates;
    els.courseList.innerHTML = items.length ? items.map((item) => `
        <label class="afm-course-option">
            <input type="radio" name="afm-exam-course" value="${escapeHtml(item.exam_course_key)}">
            <span>
                <strong>${escapeHtml(item.course_name || '未命名教务课程')}</strong>
                <small>${escapeHtml(item.teaching_class_name || item.class_composition || '未命名教学班')}
                    ${item.declared_student_count ? ` · ${escapeHtml(item.declared_student_count)} 人` : ''}
                </small>
            </span>
            <em>匹配度 ${escapeHtml(item.score ?? 0)}</em>
        </label>
    `).join('') : '<div class="afm-empty" style="min-height:150px"><strong>没有可确认的教务课程</strong><p>请检查课堂学期与教务课程信息。</p></div>';
}

async function openSync(preselect = '') {
    state.pendingClassOfferingId = 0;
    state.pendingExamCandidates = [];
    els.courseSearch.hidden = false;
    els.syncSubmit.textContent = '确认并同步两份表';
    els.syncDialog.showModal();
    els.courseList.innerHTML = '<div class="afm-loading" style="min-height:160px"><span class="afm-spinner"></span><p>读取课堂列表…</p></div>';
    try {
        const data = await apiFetch('/api/academic-final-materials/candidates');
        state.candidates = Array.isArray(data.items) ? data.items : [];
        renderCandidates();
        if (preselect) {
            const input = $(`input[name="afm-course"][value="${CSS.escape(String(preselect))}"]`, els.courseList);
            if (input) {
                input.checked = true;
                input.dispatchEvent(new Event('change', { bubbles: true }));
                input.closest('label')?.scrollIntoView({ block: 'center' });
            }
        }
    } catch (error) {
        els.courseList.innerHTML = `<div class="afm-empty" style="min-height:160px"><strong>课堂读取失败</strong><p>${escapeHtml(error.message || '')}</p></div>`;
    }
}

async function submitSync(event) {
    event.preventDefault();
    const selected = $('input[name="afm-course"]:checked', els.syncForm);
    const selectedExamCourse = $('input[name="afm-exam-course"]:checked', els.syncForm);
    const classOfferingId = state.pendingClassOfferingId || Number(selected?.value || 0);
    if (!classOfferingId || (state.pendingClassOfferingId && !selectedExamCourse)) return;
    setBusy(els.syncSubmit, true, '同步并校验中…');
    els.syncHint.textContent = '正在登录教务系统并顺序下载两份 Word，请勿重复提交。';
    try {
        const data = await apiFetch('/api/academic-final-materials/sync', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                class_offering_id: classOfferingId,
                exam_course_key: selectedExamCourse?.value || '',
                force: Boolean(els.force.checked),
            }),
        });
        els.syncDialog.close();
        message(data.message || '双表同步完成。');
        await loadItems();
    } catch (error) {
        if (error?.data?.status === 'needs_confirmation' && Array.isArray(error.data.candidates)) {
            state.pendingClassOfferingId = classOfferingId;
            state.pendingExamCandidates = error.data.candidates;
            els.courseSearch.hidden = true;
            renderExamCourseCandidates();
            els.syncSubmit.dataset.label = '确认教务课程并继续';
            els.syncHint.textContent = '发现多个相近的教务教学班，请确认本课堂对应项。';
            return;
        }
        els.syncHint.textContent = error.message || '同步失败，请按提示处理后重试。';
        message(error.message || '同步失败。', 'error');
        await loadItems();
    } finally {
        setBusy(els.syncSubmit, false);
        els.syncSubmit.disabled = Boolean(
            state.pendingClassOfferingId
            && !$('input[name="afm-exam-course"]:checked', els.syncForm)
        );
    }
}

function signatureOptions(selectedId, { allowEmpty = true } = {}) {
    const usable = state.signatures.filter((item) => item.can_use && item.owner_role !== 'system');
    return [
        allowEmpty ? '<option value="">暂不填入</option>' : '',
        ...usable.map((item) => `<option value="${item.id}" ${Number(selectedId) === Number(item.id) ? 'selected' : ''}>${escapeHtml(item.subject_name || item.name)} · ${escapeHtml(item.scope_label || '')}</option>`),
    ].join('');
}

async function ensureSignatures() {
    if (state.signatures.length) return;
    const data = await apiFetch('/api/signatures?limit=500');
    state.signatures = Array.isArray(data.items) ? data.items : [];
}

function identityHtml(fields) {
    const entries = [
        ['课程', fields.course_name],
        ['班级', fields.class_name],
        ['教师', fields.teacher_name],
        ['学年学期', [fields.academic_year, fields.semester].filter(Boolean).join(' ')],
    ];
    return entries.map(([label, value]) => `<div><span>${label}</span><strong title="${escapeHtml(value || '')}">${escapeHtml(value || '—')}</strong></div>`).join('');
}

async function openEditor(batchId) {
    state.currentBatchId = batchId;
    els.editorDialog.showModal();
    els.editorTitle.textContent = `编辑${typeLabel}`;
    els.editorSubtitle.textContent = '正在读取结构化内容与签名库…';
    els.identity.innerHTML = '<div style="grid-column:1/-1">正在加载…</div>';
    try {
        const [detail] = await Promise.all([
            apiFetch(`/api/academic-final-materials/${encodeURIComponent(batchId)}`),
            ensureSignatures(),
        ]);
        const record = isGrade ? detail.grade : detail.analysis;
        if (!record?.id) throw new Error('该材料还未完成同步。');
        state.currentRecord = record;
        state.currentPreviewUrl = record.preview_url;
        const fields = record.fields || {};
        const structured = record.structured || {};
        els.editorSubtitle.textContent = `${fields.course_name || ''} · ${fields.class_name || ''}`;
        els.identity.innerHTML = identityHtml(fields);
        els.gradeFields.hidden = !isGrade;
        els.analysisFields.hidden = isGrade;
        if (isGrade) {
            els.teacherSignature.innerHTML = signatureOptions(fields.teacher_signature_id);
        } else {
            $$('[data-afm-field]', els.analysisFields).forEach((input) => {
                input.value = fields[input.dataset.afmField] || '';
            });
            els.analysisText.value = structured.analysis_text || fields.analysis_text || '';
            els.departmentSignature.innerHTML = signatureOptions(fields.department_signature_id);
            els.deanSignature.innerHTML = signatureOptions(fields.dean_signature_id);
            enhancePromptPoolInput(els.regeneratePrompt);
        }
    } catch (error) {
        els.editorDialog.close();
        message(error.message || '读取材料失败。', 'error');
    }
}

function editorPayload() {
    const payload = { document_type: type };
    if (isGrade) {
        payload.teacher_signature_id = els.teacherSignature.value ? Number(els.teacherSignature.value) : null;
    } else {
        $$('[data-afm-field]', els.analysisFields).forEach((input) => {
            payload[input.dataset.afmField] = input.value;
        });
        payload.analysis_text = els.analysisText.value.trim();
        payload.department_signature_id = els.departmentSignature.value ? Number(els.departmentSignature.value) : null;
        payload.dean_signature_id = els.deanSignature.value ? Number(els.deanSignature.value) : null;
    }
    return payload;
}

async function saveEditor(event) {
    event.preventDefault();
    const button = $('[data-afm-save]');
    setBusy(button, true, '保存中…');
    try {
        const data = await apiFetch(`/api/academic-final-materials/${encodeURIComponent(state.currentBatchId)}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(editorPayload()),
        });
        els.editorDialog.close();
        message(data.message || '已保存。');
        await loadItems();
    } catch (error) {
        message(error.message || '保存失败。', 'error');
    } finally {
        setBusy(button, false);
    }
}

async function regenerateAnalysis() {
    setBusy(els.regenerate, true, '深度思考中…');
    try {
        const prompt = els.regeneratePrompt.value.trim();
        const data = await apiFetch(`/api/academic-final-materials/${encodeURIComponent(state.currentBatchId)}/regenerate-analysis`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ prompt }),
        });
        els.analysisText.value = data.analysis_text || '';
        await recordPromptForInput(els.regeneratePrompt);
        message(data.message || '已重新生成。');
    } catch (error) {
        message(error.message || '重新生成失败。', 'error');
    } finally {
        setBusy(els.regenerate, false);
    }
}

function openPreview(url, title = typeLabel) {
    if (!url) {
        message('请先同步并保存材料。', 'error');
        return;
    }
    els.previewTitle.textContent = `${title} · ${typeLabel}`;
    els.previewFrame.src = url;
    els.previewDialog.showModal();
}

function closeDialog(dialog) {
    if (dialog?.open) dialog.close();
}

$$('[data-afm-open-sync]').forEach((button) => button.addEventListener('click', () => openSync()));
$$('[data-afm-close-sync]').forEach((button) => button.addEventListener('click', () => closeDialog(els.syncDialog)));
$$('[data-afm-close-editor]').forEach((button) => button.addEventListener('click', () => closeDialog(els.editorDialog)));
$('[data-afm-close-preview]')?.addEventListener('click', () => {
    closeDialog(els.previewDialog);
    els.previewFrame.src = 'about:blank';
});
els.syncForm.addEventListener('submit', submitSync);
els.editorForm.addEventListener('submit', saveEditor);
els.search.addEventListener('input', renderCards);
els.courseSearch.addEventListener('input', renderCandidates);
els.courseList.addEventListener('change', (event) => {
    if (!event.target.matches('input[name="afm-course"], input[name="afm-exam-course"]')) return;
    els.syncSubmit.disabled = false;
    if (event.target.matches('input[name="afm-exam-course"]')) {
        els.syncHint.textContent = '确认后会重新登录教务系统，并仅对所选教学班成对下载两份 Word。';
        return;
    }
    const candidate = state.candidates.find((item) => Number(item.class_offering_id) === Number(event.target.value));
    els.syncHint.textContent = candidate?.sync_status === 'completed'
        ? '该课堂已有结果；未勾选强制同步时会复用 30 分钟缓存。'
        : '将同时同步成绩登记表与试卷分析表。';
});
els.grid.addEventListener('click', (event) => {
    const edit = event.target.closest('[data-afm-edit]');
    if (edit) return openEditor(edit.dataset.afmEdit);
    const preview = event.target.closest('[data-afm-preview]');
    if (preview) return openPreview(preview.dataset.afmPreview, preview.dataset.title);
    const resync = event.target.closest('[data-afm-resync]');
    if (resync) return openSync(resync.dataset.afmResync);
});
els.regenerate?.addEventListener('click', regenerateAnalysis);
els.previewCurrent?.addEventListener('click', () => openPreview(state.currentPreviewUrl, state.currentRecord?.fields?.course_name || typeLabel));
[els.syncDialog, els.editorDialog, els.previewDialog].forEach((dialog) => {
    dialog?.addEventListener('click', (event) => {
        if (event.target === dialog) closeDialog(dialog);
    });
});

loadItems();
