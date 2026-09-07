import { apiFetch } from './api.js';
import { escapeHtml } from './ui.js';
import { openProcessMaterialModal } from './process_material_modal.js';

const score = value => value == null || value === '' ? '缺分' : String(value);

export async function submitGradeMaterialWithPreflight(url, options) {
    const body = options.body || {};
    const refresh = url.endsWith('/final-material/refresh');
    if (!refresh && !['ordinary_grade_record', 'exam_grade_record'].includes(body.document_type)) {
        return apiFetch(url, options);
    }
    const confirmation = await confirmGradeMaterialPreflight({
        url: refresh ? `${url}/preflight` : url.replace(/\/generate$/, '/preflight'), body,
    });
    if (!confirmation) return null;
    return apiFetch(url, { ...options, body: { ...body, ...confirmation } });
}

export async function confirmGradeMaterialPreflight({ url, body = {} }) {
    const data = await apiFetch(url, { method: 'POST', body });
    const preview = data.preflight;
    if (!preview?.source_hash) throw new Error('来源预检未返回完整结果，请重试');
    if (!document.querySelector('[data-grade-material-preflight-style]')) {
        const link = document.createElement('link');
        link.rel = 'stylesheet';
        link.href = '/static/css/grade_material_preflight.css';
        link.dataset.gradeMaterialPreflightStyle = '';
        document.head.appendChild(link);
    }
    return new Promise(resolve => {
        let settled = false;
        const warnings = preview.warnings || [];
        const students = preview.students || [];
        const studentNumbers = new Map(students.map(item => [Number(item.student_id), item.student_number]));
        const bodyHtml = `<div class="grade-material-preflight">
            <p class="grade-material-preflight__intro">${escapeHtml([preview.course_name, preview.class_name].filter(Boolean).join(' · '))} · ${students.length} 位学生 · 满分 ${escapeHtml(score(preview.full_score))}</p>
            <p>${escapeHtml(preview.formula)}</p>
            <p class="grade-material-preflight__notice">${escapeHtml(preview.missing_score_policy)} 本次仅${preview.is_refresh ? '更新' : '生成'}材料，学生课程成绩需要另行确认公布。</p>
            <div class="grade-material-preflight__table" tabindex="0" role="region" aria-label="本次材料成绩预览">
                <table><thead><tr><th>学号</th><th>姓名</th>${preview.is_refresh ? '<th>原材料</th>' : ''}<th>本次材料</th></tr></thead><tbody>
                ${students.map(item => `<tr><td>${escapeHtml(item.student_number)}</td><td>${escapeHtml(item.student_name)}</td>${preview.is_refresh ? `<td>${escapeHtml(score(item.previous_score))}</td>` : ''}<td><strong>${escapeHtml(score(item.score))}</strong></td></tr>`).join('')}
                </tbody></table>
            </div>
            <details><summary>查看任务来源与有效成绩快照（${(preview.source_snapshots || []).length} 条）</summary>
                <div class="grade-material-preflight__table" tabindex="0"><table><thead><tr><th>任务</th><th>学号</th><th>有效分</th><th>状态</th><th>评分版本</th></tr></thead><tbody>
                ${(preview.source_snapshots || []).map(item => `<tr><td>${escapeHtml(item.assignment_title || String(item.assignment_id))}</td><td>${escapeHtml(studentNumbers.get(Number(item.student_id)) || '')}</td><td>${escapeHtml(score(item.effective_score))}</td><td>${escapeHtml(item.grade_display_state === 'group_pending' ? '小组未揭晓' : item.review_required ? '待复核' : ({ graded: '已评分', grading: '重批中', grading_review: '待复核', unsubmitted: '未提交', submitted: '待评分' }[item.status] || item.status))}</td><td>${escapeHtml(item.grade_revision_id == null ? '历史成绩' : String(item.grade_revision_id))}</td></tr>`).join('')}
                </tbody></table></div>
            </details>
            ${warnings.length ? `<fieldset class="grade-material-preflight__checks"><legend>逐项核对后确认</legend>${warnings.map(item => `<label><input type="checkbox" data-grade-warning value="${escapeHtml(item.code)}"><span>${escapeHtml(item.message)}${item.details?.length ? `<details><summary>查看明细（${item.details.length} 条）</summary><ul>${item.details.map(text => `<li>${escapeHtml(text)}</li>`).join('')}</ul></details>` : ''}</span></label>`).join('')}</fieldset>` : '<p>本次来源未发现缺分或待复核警告。</p>'}
            <label class="grade-material-preflight__note">核对说明（可选）<textarea class="form-control" rows="2" maxlength="2000" data-grade-note></textarea></label>
            <label class="grade-material-preflight__confirm"><input type="checkbox" data-grade-confirm><span>我已核对本次来源、分制与预览分数，确认${preview.is_refresh ? '更新这份材料' : '生成材料'}。</span></label>
        </div>`;
        const modal = openProcessMaterialModal(preview.is_refresh ? '核对并更新成绩材料' : '核对成绩来源', bodyHtml, {
            wide: true,
            onClose: () => { if (!settled) { settled = true; resolve(null); } },
            footerHtml: `<button type="button" class="lp-btn lp-btn--ghost" data-pm-close>返回核对</button><button type="button" class="lp-btn lp-btn--primary" data-grade-continue disabled>确认并${preview.is_refresh ? '更新' : '生成'}</button>`,
        });
        const button = modal.overlay.querySelector('[data-grade-continue]');
        const checks = [...modal.overlay.querySelectorAll('[data-grade-warning]')];
        const confirmed = modal.overlay.querySelector('[data-grade-confirm]');
        const update = () => { button.disabled = !confirmed.checked || checks.some(item => !item.checked); };
        modal.overlay.addEventListener('change', update);
        button.addEventListener('click', () => {
            if (button.disabled || settled) return;
            settled = true;
            resolve({ expected_preflight_hash: preview.source_hash, preflight_confirmed: true,
                accepted_preflight_warning_codes: checks.map(item => item.value),
                preflight_confirmation_note: modal.overlay.querySelector('[data-grade-note]').value.trim() });
            modal.close();
        });
    });
}
