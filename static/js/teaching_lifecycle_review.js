import { apiFetch } from './api.js';
import { escapeHtml } from './ui.js';
import { openProcessMaterialModal } from './process_material_modal.js';

const effects = {
    block: '先处理关联', delete: '随资源删除', detach: '保留记录，解除关联',
    repoint: '迁入主课堂', repoint_guarded: '保留并迁入', dedup_skip: '迁入，重复项留档',
    keep_target: '保留主课堂配置，来源留档', assignment_coexist: '作业并存',
    session_structure: '映射课次后留档', grade_publications: '保留公布历史',
    keep_billing_scope: '保留原计费范围', scope_review: '先核对读者范围',
};

// Pure domain content: the caller owns warning checkboxes and declaration UI.
export function renderTeachingReview(review) {
    const impacts = review.impact_sections || [];
    const blockers = review.blockers || [];
    return `<h4>${escapeHtml(review.title || '')}</h4><p>${escapeHtml(review.summary || '')}</p>
        ${impacts.length ? `<div class="grade-publication__table" tabindex="0" role="region" aria-label="本次操作影响">
            <table><thead><tr><th scope="col">关联内容</th><th scope="col">数量</th><th scope="col">处理方式</th></tr></thead>
            <tbody>${impacts.map((item) => `<tr><td>${escapeHtml(item.label || '')}</td><td>${Number(item.count || 0)}</td><td>${escapeHtml(effects[item.effect] || item.effect || '')}</td></tr>`).join('')}</tbody></table></div>` : '<p>未发现关联的业务记录。</p>'}
        ${blockers.length ? `<div class="grade-publication__issues" role="alert"><strong>请先处理以下问题</strong><ul>${blockers.map((message) => `<li>${escapeHtml(message)}</li>`).join('')}</ul></div>` : ''}`;
}

export function renderTeachingConfirmationFields(review) {
    return `<label class="materials-property-field"><span>输入完整名称“${escapeHtml(review.expected_confirmation_text || '')}”确认</span>
        <input class="form-control" type="text" maxlength="500" autocomplete="off" data-teaching-confirmation-text></label>`;
}

export function readTeachingConfirmationInputs(container) {
    return { confirmation_text: container.querySelector('[data-teaching-confirmation-text]')?.value || '' };
}

export function openTeachingDeleteConfirmation({ kind, resourceId }) {
    if (!['class', 'course'].includes(kind) || !Number.isSafeInteger(resourceId) || resourceId <= 0) throw new Error('教学资源编号无效');
    if (document.querySelector('[data-teaching-delete-review]')) return Promise.resolve(null);
    if (!document.querySelector('[data-grade-publication-style]')) {
        const style = document.createElement('link'); style.rel = 'stylesheet';
        style.href = '/static/css/grade_publication.css'; style.dataset.gradePublicationStyle = '';
        document.head.appendChild(style);
    }
    const endpoint = `/api/manage/${kind === 'class' ? 'classes' : 'courses'}/${resourceId}`;
    let current = null, submitting = false, loading = false, closed = false, outcome = null, resolveClosed;
    const done = new Promise((resolve) => { resolveClosed = resolve; });
    const modal = openProcessMaterialModal(`核对并删除${kind === 'class' ? '班级' : '课程'}`, `
        <div class="grade-publication" data-teaching-delete-review>
            <p role="status" aria-live="polite" data-teaching-delete-message>正在读取当前引用…</p>
            <div data-teaching-delete-content></div></div>`, {
        wide: true, canClose: () => !submitting,
        onClose: () => { closed = true; resolveClosed(outcome); },
        footerHtml: '<button type="button" class="lp-btn lp-btn--ghost" data-pm-close>取消</button>'
            + '<button type="button" class="lp-btn lp-btn--ghost" data-teaching-delete-refresh>重新读取影响</button>'
            + '<button type="button" class="lp-btn lp-btn--primary" data-teaching-delete-submit disabled>确认删除</button>',
    });
    const { overlay } = modal;
    const content = overlay.querySelector('[data-teaching-delete-content]');
    const feedback = overlay.querySelector('[data-teaching-delete-message]');
    const submit = overlay.querySelector('[data-teaching-delete-submit]');
    const refresh = overlay.querySelector('[data-teaching-delete-refresh]');
    function update() {
        const name = readTeachingConfirmationInputs(content).confirmation_text.trim();
        submit.disabled = submitting || loading || !current?.can_execute
            || name !== String(current?.expected_confirmation_text || '').trim()
            || !content.querySelector('[data-teaching-delete-note]')?.value.trim()
            || [...content.querySelectorAll('[data-teaching-delete-warning]')].some((input) => !input.checked);
        refresh.disabled = loading || submitting;
        content.querySelectorAll('input,textarea').forEach((input) => { input.disabled = submitting; });
        overlay.querySelectorAll('[data-pm-close],.lp-modal__close').forEach((button) => { button.disabled = submitting; });
    }
    async function reload() {
        if (loading || submitting || closed) return;
        const name = readTeachingConfirmationInputs(content).confirmation_text;
        const note = content.querySelector('[data-teaching-delete-note]')?.value || '';
        loading = true; update(); feedback.textContent = '正在读取当前引用…';
        try {
            const data = await apiFetch(`${endpoint}/delete-impact`, { silent: true });
            if (closed) return;
            current = data.review;
            content.innerHTML = `${renderTeachingReview(current)}
                ${(current.warnings || []).length ? `<fieldset class="grade-publication__checks"><legend>请逐项确认</legend>${current.warnings.map((warning) => `<label><input type="checkbox" data-teaching-delete-warning value="${escapeHtml(warning.code)}"><span>${escapeHtml(warning.message)}</span></label>`).join('')}</fieldset>` : ''}
                ${renderTeachingConfirmationFields(current)}
                <label class="materials-property-field"><span>核对说明（必填）</span><textarea class="form-control" rows="2" maxlength="2000" data-teaching-delete-note></textarea></label>`;
            content.querySelector('[data-teaching-confirmation-text]').value = name;
            content.querySelector('[data-teaching-delete-note]').value = note;
            feedback.textContent = current.can_execute ? '请核对当前影响后确认。重新读取后需重新勾选警告。' : '关联问题处理完成后，可重新读取影响。';
        } catch (error) {
            if (!closed) { current = null; feedback.textContent = error.message || '读取失败，可重试；填写内容已保留。'; }
        } finally { loading = false; if (!closed) update(); }
    }
    content.addEventListener('input', update); content.addEventListener('change', update);
    refresh.addEventListener('click', reload);
    submit.addEventListener('click', async () => {
        if (submit.disabled || submitting || !current) return;
        const body = { expected_review_hash: current.review_hash,
            ...readTeachingConfirmationInputs(content),
            accepted_warning_codes: [...content.querySelectorAll('[data-teaching-delete-warning]:checked')].map((input) => input.value),
            confirmation_note: content.querySelector('[data-teaching-delete-note]').value };
        submitting = true; update(); feedback.textContent = '正在提交本次删除…';
        try {
            outcome = await apiFetch(endpoint, { method: 'DELETE', body, silent: true });
            submitting = false; modal.close();
        } catch (error) {
            current = null;
            feedback.textContent = `${error.message || '未收到确定结果'}。请重新读取资源状态后核对，填写内容已保留。`;
        } finally { submitting = false; if (!closed) update(); }
    });
    reload();
    return done;
}
