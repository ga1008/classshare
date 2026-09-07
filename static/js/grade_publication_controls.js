import { apiFetch } from './api.js';
import { escapeHtml, showToast } from './ui.js';
import { openProcessMaterialModal } from './process_material_modal.js';

const score = (value) => value == null ? '缺分' : String(value);
const publicationLabels = { active: '当前公布', superseded: '已被新版本替代', withdrawn: '已撤回' };

export function openGradePublicationModal(detail) {
    const materialId = Number(detail?.ai_import_record?.id);
    const offerings = [...new Map((detail?.assignments || []).map((item) => [Number(item.class_offering_id), item])).values()];
    if ((!materialId && !offerings.length) || detail?.can_manage === false) return;
    if (!document.querySelector('[data-grade-publication-style]')) {
        const link = document.createElement('link');
        link.rel = 'stylesheet';
        link.href = '/static/css/grade_publication.css';
        link.dataset.gradePublicationStyle = '';
        document.head.appendChild(link);
    }
    let busy = false;
    let revision = 0;
    let preview = null;
    let current = null;
    let offeringId = offerings.length === 1 ? Number(offerings[0].class_offering_id) : 0;
    const body = `
        <div class="grade-publication">
            <p>${materialId ? `从「${escapeHtml(detail.name || '当前成绩材料')}」公布课程成绩。` : '查看课堂成绩的公布状态与历史记录。重新公布请进入已分配的正式成绩材料。'}学生在成绩查看页仅能看到本人的公布分数。</p>
            <label class="materials-property-field"><span>公布到课堂</span>
                <select class="form-control" data-gp-offering>
                    <option value="">请选择已分配的课堂</option>
                    ${offerings.map((item) => `<option value="${Number(item.class_offering_id)}" ${Number(item.class_offering_id) === offeringId ? 'selected' : ''}>${escapeHtml([item.course_name, item.class_name, item.semester].filter(Boolean).join(' · '))}</option>`).join('')}
                </select>
            </label>
            ${!offerings.length ? '<p>请先将材料分配到相应课堂，再预览公布。</p>' : ''}
            <p data-gp-message role="status" aria-live="polite"></p>
            <div data-gp-content></div>
        </div>`;
    const modal = openProcessMaterialModal(materialId ? '公布课程成绩' : '课堂成绩公布', body, {
        wide: true,
        canClose: () => !busy,
        onClose: () => { revision += 1; },
        footerHtml: '<button type="button" class="lp-btn lp-btn--ghost" data-pm-close>关闭</button>' + (materialId ? '<button type="button" class="lp-btn lp-btn--primary" data-gp-publish disabled>确认并公布本版成绩</button>' : ''),
    });
    const { overlay } = modal;
    const select = overlay.querySelector('[data-gp-offering]');
    const content = overlay.querySelector('[data-gp-content]');
    const message = overlay.querySelector('[data-gp-message]');
    const publish = overlay.querySelector('[data-gp-publish]');
    const endpoint = () => `/api/classrooms/${offeringId}/grade-publication`;
    function eligible() {
        const warnings = [...content.querySelectorAll('[data-gp-warning]')];
        return !busy && preview?.can_publish && content.querySelector('[data-gp-confirm]')?.checked
            && warnings.every((item) => item.checked)
            && (!warnings.length || content.querySelector('[data-gp-note]')?.value.trim());
    }
    function refreshButtons() {
        select.disabled = busy;
        if (publish) publish.disabled = !eligible();
        overlay.querySelectorAll('[data-pm-close],.lp-modal__close').forEach((button) => { button.disabled = busy; });
        const withdraw = content.querySelector('[data-gp-withdraw]');
        if (withdraw) withdraw.disabled = busy || !content.querySelector('[data-gp-reason]')?.value.trim();
    }
    function render(status) {
        const warnings = preview?.warnings || [];
        const blockers = preview?.blocking_reasons || [];
        const rows = preview?.students || [];
        content.innerHTML = `
            <section class="grade-publication__status">
                <strong>${current ? `当前公布第 ${Number(current.version)} 版` : '本课堂尚无正在公布的成绩'}</strong>
                ${current?.source_stale ? '<p>来源已变化，学生仍看到原公布分数。请核对并更新材料，再重新公布。</p>' : ''}
                ${current ? `<details><summary>撤回当前公布</summary><p>撤回后学生暂时无法查看这版正式成绩，历史记录仍保留。</p><label class="materials-property-field"><span>撤回原因</span><textarea class="form-control" data-gp-reason maxlength="2000" rows="2"></textarea></label><button type="button" class="lp-btn lp-btn--danger" data-gp-withdraw disabled>撤回第 ${Number(current.version)} 版</button></details>` : ''}
            </section>
            ${preview ? `<section>
                <h4>${escapeHtml(preview.course_name)} · ${escapeHtml(preview.semester_name)}</h4>
                <p>待公布第 ${Number(preview.expected_version) + 1} 版 · ${rows.length} 位学生 · 百分制</p>
                <p>${escapeHtml(preview.formula?.text || '沿用来源成绩材料的核定公式')}</p>
                ${blockers.length ? `<div class="grade-publication__issues" role="alert"><strong>请先处理以下问题</strong><ul>${blockers.map((item) => `<li>${escapeHtml(item.message)}</li>`).join('')}</ul></div>` : ''}
                <div class="grade-publication__table" tabindex="0" role="region" aria-label="待公布成绩预览">
                    <table><thead><tr><th scope="col">学号</th><th scope="col">姓名</th><th scope="col">平时成绩</th><th scope="col">期末成绩</th><th scope="col">课程总评</th></tr></thead><tbody>
                        ${rows.map((item) => `<tr><td>${escapeHtml(item.student_number)}</td><td>${escapeHtml(item.student_name)}</td><td>${escapeHtml(score(item.ordinary_score))}</td><td>${escapeHtml(score(item.final_exam_score))}</td><td><strong>${escapeHtml(score(item.overall_score))}</strong></td></tr>`).join('')}
                    </tbody></table>
                </div>
                ${warnings.length ? `<fieldset class="grade-publication__checks"><legend>逐项核对来源提示</legend>${warnings.map((item) => `<label><input type="checkbox" data-gp-warning value="${escapeHtml(item.code)}"><span>${escapeHtml(item.message)}</span></label>`).join('')}</fieldset><label class="materials-property-field"><span>核对说明（必填）</span><textarea class="form-control" data-gp-note maxlength="2000" rows="2"></textarea></label>` : ''}
                <label class="grade-publication__confirm"><input type="checkbox" data-gp-confirm ${!preview.can_publish ? 'disabled' : ''}><span>我已核对课堂、学期、名单及分数，确认向学生公布以上成绩。</span></label>
            </section>` : ''}
            ${(status.history || []).length ? `<details><summary>公布历史（${status.history.length} 条）</summary><ul>${status.history.map((item) => `<li>第 ${Number(item.version)} 版 · ${escapeHtml(publicationLabels[item.status] || item.status)} · ${escapeHtml(item.published_at || '')}${item.withdrawal_reason ? ` · 撤回原因：${escapeHtml(item.withdrawal_reason)}` : ''}</li>`).join('')}</ul></details>` : ''}`;
        refreshButtons();
    }
    async function load(successMessage = '') {
        const requestRevision = ++revision;
        preview = null;
        current = null;
        content.innerHTML = '';
        message.textContent = offeringId ? '正在读取课堂成绩公布状态…' : '请选择课堂。';
        refreshButtons();
        if (!offeringId) return;
        const results = await Promise.allSettled([
            apiFetch(endpoint(), { silent: true }),
            ...(materialId ? [apiFetch(`${endpoint()}/preview?material_id=${materialId}`, { silent: true })] : []),
        ]);
        if (requestRevision !== revision || !overlay.isConnected) return;
        const status = results[0].status === 'fulfilled' ? results[0].value : { history: [] };
        current = status.current || null;
        preview = results[1]?.status === 'fulfilled' ? results[1].value.preview : null;
        // A failed status read must never enable a publish action with incomplete context.
        if (results[0].status === 'rejected') preview = null;
        const error = results.find((item) => item.status === 'rejected');
        message.textContent = error ? (error.reason.message || '加载失败，请重新选择课堂后重试。') : successMessage;
        render(status);
    }
    select.addEventListener('change', () => { offeringId = Number(select.value); void load(); });
    content.addEventListener('input', refreshButtons);
    content.addEventListener('change', refreshButtons);
    publish?.addEventListener('click', async () => {
        if (!eligible()) return;
        const payload = {
            material_id: materialId, expected_source_hash: preview.source_hash,
            expected_version: preview.expected_version, confirmed: true,
            accepted_warning_codes: [...content.querySelectorAll('[data-gp-warning]:checked')].map((item) => item.value),
            confirmation_note: content.querySelector('[data-gp-note]')?.value.trim() || '',
        };
        busy = true;
        refreshButtons();
        message.textContent = '正在公布，请稍候…';
        try {
            const result = await apiFetch(`${endpoint()}/publish`, { method: 'POST', body: payload, silent: true });
            showToast(`已公布第 ${result.version} 版成绩`, 'success');
            await load(`已公布第 ${result.version} 版，${result.student_count} 位学生可查看本人成绩。`);
        } catch (error) {
            // Do not automatically retry mutations, especially after a network timeout.
            await load(`${error.message || '公布结果暂不明确'}。请核对当前公布版本后再操作。`);
        } finally { busy = false; refreshButtons(); }
    });
    content.addEventListener('click', async (event) => {
        if (!event.target.closest('[data-gp-withdraw]') || busy || !current) return;
        const reason = content.querySelector('[data-gp-reason]')?.value.trim();
        if (!reason) return;
        busy = true;
        refreshButtons();
        try {
            await apiFetch(`${endpoint()}/withdraw`, { method: 'POST', body: { publication_id: current.publication_id, reason }, silent: true });
            await load('已撤回当前公布版本，历史记录已保留。');
        } catch (error) {
            await load(`${error.message || '撤回结果暂不明确'}。请核对当前状态后再操作。`);
        } finally { busy = false; refreshButtons(); }
    });
    void load();
}

export function openClassroomGradePublicationModal(offering) {
    openGradePublicationModal({ can_manage: true, assignments: [offering] });
}

document.addEventListener('click', (event) => {
    const button = event.target.closest('[data-classroom-grade-publication]');
    const offeringId = Number(button?.dataset.offeringId);
    if (!offeringId) return;
    openClassroomGradePublicationModal({ class_offering_id: offeringId, course_name: button.dataset.offeringLabel || '当前课堂' });
});
