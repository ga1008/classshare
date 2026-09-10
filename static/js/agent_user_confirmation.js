import { escapeHtml } from './ui.js';
import { openProcessMaterialModal } from './process_material_modal.js';
import { renderTeachingReview, renderTeachingConfirmationFields, readTeachingConfirmationInputs } from './teaching_lifecycle_review.js';

const score = (value) => value == null ? '缺分' : String(value);
const forms = {
    publish_classroom_grades: {
        title: '核对并公布课堂成绩', submit: '确认并公布本版成绩', pending: '正在公布本版成绩…',
        introduction: '核对本次课堂、学期、名单和成绩。确认后，学生只能看到自己的公布分数。',
        acknowledgement: '我已核对以上课堂、学期、名单及分数，确认向学生公布本版成绩。',
        noteLimit: 2000, canExecute: (value) => value.can_publish === true,
        needsNote: (value) => !!value.warnings?.length,
        ready: () => true, inputs: () => ({}),
        render(value, params) {
            const rows = value.students || [];
            return `<h4>${escapeHtml(value.course_name || '')} · ${escapeHtml(value.semester_name || '')}</h4>
                <p>课堂 #${Number(params.class_offering_id)} · 待公布第 ${Number(value.expected_version) + 1} 版 · ${rows.length} 位学生 · 百分制</p>
                <p>${escapeHtml(value.formula?.text || '沿用来源成绩材料的核定公式')}</p>
                <p>表格可横向滚动，请核对每一列成绩。</p>
                <div class="grade-publication__table" tabindex="0" role="region" aria-label="本次待公布成绩">
                    <table><thead><tr><th scope="col">学号</th><th scope="col">姓名</th><th scope="col">平时成绩</th><th scope="col">期末成绩</th><th scope="col">课程总评</th></tr></thead>
                    <tbody>${rows.map((item) => `<tr><td>${escapeHtml(item.student_number || '')}</td><td>${escapeHtml(item.student_name || '')}</td><td>${escapeHtml(score(item.ordinary_score))}</td><td>${escapeHtml(score(item.final_exam_score))}</td><td><strong>${escapeHtml(score(item.overall_score))}</strong></td></tr>`).join('')}</tbody></table>
                </div>`;
        },
    },
    review_signature_request: {
        title: '核对签章申请并审批', submit: '提交本次审批决定', pending: '正在提交审批决定…',
        introduction: '请打开申请时的文档，核对材料、签章和用途，再选择本次审批决定。',
        acknowledgement: '我已阅读申请时的文档并核对上述用途，确认以本人身份提交所选决定。',
        noteLimit: 300, canExecute: (value) => value.can_execute === true,
        needsNote: (value, content) => !!value.warnings?.length || content.querySelector('[data-agent-business-decision]')?.value === 'reject',
        ready: (value, content) => {
            const decision = content.querySelector('[data-agent-business-decision]')?.value;
            return decision === 'reject' || (decision === 'approve' && value.approve_allowed === true);
        },
        inputs: (content) => ({ decision: content.querySelector('[data-agent-business-decision]')?.value || '' }),
        render(value, params) {
            // This URL is constructed from the reviewed ID, never from model text.
            const documentUrl = `/api/signatures/requests/${Number(params.request_id)}/preview`;
            return `<h4>${escapeHtml(value.material_title || '')}</h4>
                <p>申请 #${Number(params.request_id)} · 申请人：${escapeHtml(value.requester_name || '')}</p>
                <p>签章：${escapeHtml(value.signature_name || '')}<br>用途：${escapeHtml(value.point_label || '')}</p>
                <p>申请说明：${escapeHtml(value.request_note || '未填写')}</p>
                <p><a class="lp-btn lp-btn--ghost" href="${documentUrl}" target="_blank" rel="noopener noreferrer">打开申请时的文档</a></p>
                <p>${escapeHtml(value.scope_notice || '')}</p>
                <label class="materials-property-field"><span>本次审批决定</span>
                    <select class="form-control" data-agent-business-decision><option value="">请选择决定</option>
                        <option value="approve" ${value.approve_allowed ? '' : 'disabled'}>批准本次签章申请</option>
                        <option value="reject">拒绝本次签章申请</option></select></label>`;
        },
    },
};
for (const [action, title, submit] of [
    ['delete_empty_class', '核对并删除空班级', '确认删除此空班级'],
    ['delete_unreferenced_course', '核对并删除未引用课程', '确认删除此课程'],
    ['merge_class_offerings', '核对并合并本人课堂', '确认合并课堂'],
]) {
    forms[action] = {
        title, submit, pending: '正在提交本次操作…',
        introduction: '请核对当前关联内容及处理方式，逐项确认提示，并手工输入名称。',
        acknowledgement: '我已核对以上影响和处理方式，确认以本人身份执行本次操作。',
        noteLimit: 2000, needsNote: () => true,
        canExecute: (value) => value.can_execute === true,
        ready: (value, content) => readTeachingConfirmationInputs(content).confirmation_text.trim() === String(value.expected_confirmation_text || '').trim(),
        inputs: (content) => readTeachingConfirmationInputs(content),
        render: (value) => renderTeachingReview(value) + renderTeachingConfirmationFields(value),
    };
}

export function openAgentUserConfirmation({ taskId, actionIndex, preview, apiJson, onComplete, onClose }) {
    if (document.querySelector('[data-agent-business-confirmation]')) return Promise.resolve();
    const form = forms[preview.action];
    if (!form || !preview.confirmation_review) {
        throw new Error('当前业务核对表单不可用，请重新读取提案。');
    }
    if (!document.querySelector('[data-grade-publication-style]')) {
        const style = document.createElement('link');
        style.rel = 'stylesheet';
        style.href = '/static/css/grade_publication.css';
        style.dataset.gradePublicationStyle = '';
        document.head.appendChild(style);
    }
    let current = preview;
    let busy = false;
    let closed = false;
    let resolveClosed;
    const completion = new Promise((resolve) => { resolveClosed = resolve; });
    const modal = openProcessMaterialModal(form.title, `
        <div class="grade-publication" data-agent-business-confirmation>
            <p>${escapeHtml(form.introduction)}</p>
            <p data-agent-business-message role="status" aria-live="polite"></p>
            <div data-agent-business-review></div>
        </div>`, {
        wide: true,
        canClose: () => !busy,
        onClose: () => { closed = true; onClose?.(); resolveClosed(); },
        footerHtml: '<button type="button" class="lp-btn lp-btn--ghost" data-pm-close>取消</button>'
            + '<button type="button" class="lp-btn lp-btn--ghost" data-agent-business-refresh>重新读取快照</button>'
            + `<button type="button" class="lp-btn lp-btn--primary" data-agent-business-publish disabled>${escapeHtml(form.submit)}</button>`,
    });
    const { overlay } = modal;
    overlay.classList.add('agent-grade-confirmation');
    const content = overlay.querySelector('[data-agent-business-review]');
    const feedback = overlay.querySelector('[data-agent-business-message]');
    const publish = overlay.querySelector('[data-agent-business-publish]');
    const refresh = overlay.querySelector('[data-agent-business-refresh]');
    const endpoint = `/api/agent-tasks/${taskId}/actions/${actionIndex}`;
    const source = () => JSON.stringify(current.params);

    function eligible() {
        const warnings = [...content.querySelectorAll('[data-agent-business-warning]')];
        return !busy && form.canExecute(current.confirmation_review)
            && form.ready(current.confirmation_review, content)
            && content.querySelector('[data-agent-business-reviewed]')?.checked
            && warnings.every((input) => input.checked)
            && (!form.needsNote(current.confirmation_review, content) || content.querySelector('[data-agent-business-note]')?.value.trim());
    }
    function updateControls() {
        publish.disabled = !eligible();
        refresh.disabled = busy;
        overlay.querySelectorAll('[data-pm-close],.lp-modal__close').forEach((button) => { button.disabled = busy; });
        content.querySelectorAll('input,textarea,select').forEach((input) => { input.disabled = busy || !form.canExecute(current.confirmation_review); });
        const noteLabel = content.querySelector('[data-agent-business-note-label]');
        if (noteLabel) noteLabel.textContent = `核对说明${form.needsNote(current.confirmation_review, content) ? '（必填）' : '（可选）'}`;
    }
    function render({ note = '', accepted = [], reviewed = false, decision = '', confirmation_text = '' } = {}) {
        const value = current.confirmation_review;
        const warnings = value.warnings || [];
        const blockers = value.blocking_reasons || [];
        content.innerHTML = `
            ${form.render(value, current.params)}
            ${blockers.length ? `<div class="grade-publication__issues" role="alert"><strong>请先处理以下问题</strong><ul>${blockers.map((item) => `<li>${escapeHtml(item.message || '')}</li>`).join('')}</ul></div>` : ''}
            ${warnings.length ? `<fieldset class="grade-publication__checks"><legend>逐项核对来源提示</legend>${warnings.map((item) => `<label><input type="checkbox" data-agent-business-warning value="${escapeHtml(item.code)}"><span>${escapeHtml(item.message || '')}</span></label>`).join('')}</fieldset>` : ''}
            <label class="materials-property-field"><span data-agent-business-note-label>核对说明</span><textarea class="form-control" data-agent-business-note maxlength="${form.noteLimit}" rows="2"></textarea></label>
            <label class="grade-publication__confirm"><input type="checkbox" data-agent-business-reviewed><span>${escapeHtml(form.acknowledgement)}</span></label>`;
        content.querySelector('[data-agent-business-note]').value = note;
        content.querySelector('[data-agent-business-reviewed]').checked = reviewed;
        const decisionInput = content.querySelector('[data-agent-business-decision]');
        if (decisionInput) decisionInput.value = decision;
        const nameInput = content.querySelector('[data-teaching-confirmation-text]');
        if (nameInput) nameInput.value = confirmation_text;
        content.querySelectorAll('[data-agent-business-warning]').forEach((input) => { input.checked = accepted.includes(input.value); });
        updateControls();
    }
    function inputs() {
        return {
            ...form.inputs(content),
            accepted_warning_codes: [...content.querySelectorAll('[data-agent-business-warning]:checked')].map((input) => input.value),
            confirmation_note: content.querySelector('[data-agent-business-note]')?.value.trim() || '',
        };
    }
    async function acknowledge(data) {
        busy = false;
        modal.close();
        await onComplete(data);
    }
    async function completedReceipt() {
        const data = await apiJson(`/api/agent-tasks/${taskId}`).catch(() => null);
        const receipt = data?.task?.result_detail?.proposed_actions?.[actionIndex]?.executed;
        if (!receipt) return false;
        await acknowledge({ task: data.task, result: receipt });
        return true;
    }
    content.addEventListener('input', updateControls);
    content.addEventListener('change', updateControls);
    refresh.addEventListener('click', async () => {
        if (busy || closed) return;
        const oldSource = source();
        const saved = inputs();
        const reviewed = content.querySelector('[data-agent-business-reviewed]').checked;
        busy = true;
        updateControls();
        feedback.textContent = '正在重新读取业务快照…';
        try {
            if (await completedReceipt()) return;
            const next = await apiJson(`${endpoint}/preview`, { method: 'POST', body: JSON.stringify({ params: current.params }) });
            if (next.action !== current.action || !next.confirmation_review) throw new Error('操作类型已变化，请关闭并重新核对。');
            current = next;
            const same = source() === oldSource;
            render({ note: saved.confirmation_note, accepted: same ? saved.accepted_warning_codes : [], reviewed: same && reviewed,
                decision: same ? saved.decision : '', confirmation_text: same ? saved.confirmation_text : '' });
            feedback.textContent = same ? '快照未变化，已保留本次核对内容。' : '来源已更新，已保留核对说明；请重新核对并勾选确认。';
        } catch (error) {
            feedback.textContent = error.message || '读取失败，已保留本次核对内容。';
        } finally {
            busy = false;
            if (!closed) updateControls();
        }
    });
    publish.addEventListener('click', async () => {
        if (!eligible() || closed) return;
        const confirmation = inputs();
        busy = true;
        updateControls();
        feedback.textContent = form.pending;
        try {
            const data = await apiJson(`${endpoint}/execute`, {
                method: 'POST', body: JSON.stringify({ params: current.params,
                    confirmation_token: current.confirmation_token, confirmation_inputs: confirmation }),
            });
            await acknowledge(data);
        } catch (error) {
            if (await completedReceipt()) return;
            feedback.textContent = `${error.message || '尚未取得执行结果。'} 已保留核对内容；来源或版本变化时请重新读取快照。`;
        } finally {
            busy = false;
            if (!closed) updateControls();
        }
    });
    render();
    return completion;
}
