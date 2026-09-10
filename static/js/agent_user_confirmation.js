import { escapeHtml } from './ui.js';
import { openProcessMaterialModal } from './process_material_modal.js';

const score = (value) => value == null ? '缺分' : String(value);

export function openAgentUserConfirmation({ taskId, actionIndex, preview, apiJson, onComplete, onClose }) {
    if (document.querySelector('[data-agent-business-confirmation]')) return Promise.resolve();
    if (preview.action !== 'publish_classroom_grades' || !preview.confirmation_review) {
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
    const modal = openProcessMaterialModal('核对并公布课堂成绩', `
        <div class="grade-publication" data-agent-business-confirmation>
            <p>核对本次课堂、学期、名单和成绩。确认后，学生只能看到自己的公布分数。</p>
            <p data-agent-business-message role="status" aria-live="polite"></p>
            <div data-agent-business-review></div>
        </div>`, {
        wide: true,
        canClose: () => !busy,
        onClose: () => { closed = true; onClose?.(); resolveClosed(); },
        footerHtml: '<button type="button" class="lp-btn lp-btn--ghost" data-pm-close>取消</button>'
            + '<button type="button" class="lp-btn lp-btn--ghost" data-agent-business-refresh>重新读取快照</button>'
            + '<button type="button" class="lp-btn lp-btn--primary" data-agent-business-publish disabled>确认并公布本版成绩</button>',
    });
    const { overlay } = modal;
    overlay.classList.add('agent-grade-confirmation');
    const content = overlay.querySelector('[data-agent-business-review]');
    const feedback = overlay.querySelector('[data-agent-business-message]');
    const publish = overlay.querySelector('[data-agent-business-publish]');
    const refresh = overlay.querySelector('[data-agent-business-refresh]');
    const endpoint = `/api/agent-tasks/${taskId}/actions/${actionIndex}`;
    const source = () => `${current.params.expected_review_hash}:${current.params.expected_source_hash}:${current.params.expected_version}`;

    function eligible() {
        const warnings = [...content.querySelectorAll('[data-agent-business-warning]')];
        return !busy && current.confirmation_review.can_publish === true
            && content.querySelector('[data-agent-business-reviewed]')?.checked
            && warnings.every((input) => input.checked)
            && (!warnings.length || content.querySelector('[data-agent-business-note]')?.value.trim());
    }
    function updateControls() {
        publish.disabled = !eligible();
        refresh.disabled = busy;
        overlay.querySelectorAll('[data-pm-close],.lp-modal__close').forEach((button) => { button.disabled = busy; });
        content.querySelectorAll('input,textarea').forEach((input) => { input.disabled = busy || !current.confirmation_review.can_publish; });
    }
    function render({ note = '', accepted = [], reviewed = false } = {}) {
        const value = current.confirmation_review;
        const warnings = value.warnings || [];
        const blockers = value.blocking_reasons || [];
        const rows = value.students || [];
        content.innerHTML = `
            <h4>${escapeHtml(value.course_name || '')} · ${escapeHtml(value.semester_name || '')}</h4>
            <p>课堂 #${Number(current.params.class_offering_id)} · 待公布第 ${Number(value.expected_version) + 1} 版 · ${rows.length} 位学生 · 百分制</p>
            <p>${escapeHtml(value.formula?.text || '沿用来源成绩材料的核定公式')}</p>
            ${blockers.length ? `<div class="grade-publication__issues" role="alert"><strong>请先处理以下问题</strong><ul>${blockers.map((item) => `<li>${escapeHtml(item.message || '')}</li>`).join('')}</ul></div>` : ''}
            <div class="grade-publication__table" tabindex="0" role="region" aria-label="本次待公布成绩">
                <table><thead><tr><th scope="col">学号</th><th scope="col">姓名</th><th scope="col">平时成绩</th><th scope="col">期末成绩</th><th scope="col">课程总评</th></tr></thead>
                <tbody>${rows.map((item) => `<tr><td>${escapeHtml(item.student_number || '')}</td><td>${escapeHtml(item.student_name || '')}</td><td>${escapeHtml(score(item.ordinary_score))}</td><td>${escapeHtml(score(item.final_exam_score))}</td><td><strong>${escapeHtml(score(item.overall_score))}</strong></td></tr>`).join('')}</tbody></table>
            </div>
            ${warnings.length ? `<fieldset class="grade-publication__checks"><legend>逐项核对来源提示</legend>${warnings.map((item) => `<label><input type="checkbox" data-agent-business-warning value="${escapeHtml(item.code)}"><span>${escapeHtml(item.message || '')}</span></label>`).join('')}</fieldset>` : ''}
            <label class="materials-property-field"><span>核对说明${warnings.length ? '（必填）' : '（可选）'}</span><textarea class="form-control" data-agent-business-note maxlength="2000" rows="2"></textarea></label>
            <label class="grade-publication__confirm"><input type="checkbox" data-agent-business-reviewed><span>我已核对以上课堂、学期、名单及分数，确认向学生公布本版成绩。</span></label>`;
        content.querySelector('[data-agent-business-note]').value = note;
        content.querySelector('[data-agent-business-reviewed]').checked = reviewed;
        content.querySelectorAll('[data-agent-business-warning]').forEach((input) => { input.checked = accepted.includes(input.value); });
        updateControls();
    }
    function inputs() {
        return {
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
        feedback.textContent = '正在重新读取成绩快照…';
        try {
            if (await completedReceipt()) return;
            const next = await apiJson(`${endpoint}/preview`, { method: 'POST', body: JSON.stringify({ params: current.params }) });
            if (next.action !== current.action || !next.confirmation_review) throw new Error('操作类型已变化，请关闭并重新核对。');
            current = next;
            const same = source() === oldSource;
            render({ note: saved.confirmation_note, accepted: same ? saved.accepted_warning_codes : [], reviewed: same && reviewed });
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
        feedback.textContent = '正在公布本版成绩…';
        try {
            const data = await apiJson(`${endpoint}/execute`, {
                method: 'POST', body: JSON.stringify({ params: current.params,
                    confirmation_token: current.confirmation_token, confirmation_inputs: confirmation }),
            });
            await acknowledge(data);
        } catch (error) {
            if (await completedReceipt()) return;
            feedback.textContent = `${error.message || '尚未取得公布结果。'} 已保留核对内容；来源或版本变化时请重新读取快照。`;
        } finally {
            busy = false;
            if (!closed) updateControls();
        }
    });
    render();
    return completion;
}
