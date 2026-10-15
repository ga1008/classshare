/**
 * Generic approval workflow UI (通用审批流前端模板).
 *
 *   mountLauncher(root, opts)  – applicant side: a "?" trigger that opens a small
 *                                popover (hover 1 s / click) to raise a request.
 *   mountPanel(root, opts)     – reviewer side: a right-hand drawer listing requests
 *                                plus a large detail modal with approve / reject.
 *
 * New request types only need a detail renderer in DETAIL_RENDERERS (optional) and
 * a decision-form builder in DECISION_FORMS (optional). Everything else — API calls,
 * state badges, permissions, timeline — is shared.
 */
import { showToast, escapeHtml } from './ui.js';
import { adoptDomainControl } from './lq/domain-controls.js';
import { getLayerSystem } from './lq/layer.js';

const API = '/api/approvals';
const STATUS_TONE = { pending: 'warning', approved: 'success', rejected: 'danger', cancelled: 'muted', expired: 'muted' };
const SUBMISSION_STATUS_LABELS = { submitted: '已提交，未批改', grading: '批改中', grading_review: '待人工复核', grading_failed: 'AI 批改失败', graded: '已批改', unsubmitted: '未提交' };

// Called only at this module's render boundaries. Native values and the existing
// delegated data-apr actions keep their owner; LQ owns controls and layer motion.
function adoptControls(container) {
    for (const node of container.querySelectorAll('button,input,textarea,select')) {
        const choice = node.matches('.apr-item');
        adoptDomainControl(node, { ...(choice ? { kind: 'choice' } : {}),
            variant: node.classList.contains('btn-primary') ? 'prominent' : node.matches('.apr-drawer-close,.modal-close') ? 'ghost' : 'glass' });
        if (choice) node.dataset.lqShape = 'surface';
        // The drawer tab keeps its visible label; only label-less buttons are icon buttons.
        if (node.tagName === 'BUTTON' && node.querySelector('svg') && !node.matches('.apr-drawer-toggle')) node.classList.add('lq-btn--icon');
    }
    for (const field of container.querySelectorAll('.form-group')) field.classList.add('lq-field');
    for (const label of container.querySelectorAll('.form-label')) label.classList.add('lq-field__label');
}

function fmtDate(value) {
    if (!value) return '-';
    try {
        return new Date(String(value).replace(' ', 'T')).toLocaleString('zh-CN', {
            year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
        });
    } catch {
        return String(value);
    }
}

function toDatetimeLocalValue(value) {
    if (!value) return '';
    const date = value instanceof Date ? value : new Date(String(value).replace(' ', 'T'));
    if (Number.isNaN(date.getTime())) return '';
    const pad = (n) => String(n).padStart(2, '0');
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

async function api(path, options = {}) {
    const response = await fetch(`${API}${path}`, {
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        credentials: 'same-origin',
        ...options,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
        const detail = data.detail;
        throw new Error(typeof detail === 'string' ? detail : (detail?.message || data.message || '请求失败'));
    }
    return data;
}

function statusBadge(item) {
    return `<span class="apr-badge apr-badge--${STATUS_TONE[item.status] || 'muted'}">${escapeHtml(item.status_label || item.status)}</span>`;
}

/* ------------------------------------------------------------------ detail renderers */
const DETAIL_RENDERERS = {
    submission_withdraw(item) {
        const d = item.detail || {};
        const rows = [
            ['学生', d.student_name || item.applicant_name],
            ['作业', d.assignment_title],
            ['当前状态', d.already_returned ? '已开放重交' : (d.current_status === 'graded' ? `已批改，得分 ${d.current_score ?? '-'}` : d.current_status)],
            ['提交时间', fmtDate(d.submitted_at)],
            ['作业截止', d.assignment_effective_deadline ? fmtDate(d.assignment_effective_deadline) : '未设置'],
            ['默认重交截止', d.recommended_resubmission_due_at ? fmtDate(d.recommended_resubmission_due_at) : '-'],
        ];
        const table = rows.map(([k, v]) => `<div class="apr-kv"><span>${escapeHtml(k)}</span><strong>${escapeHtml(String(v ?? '-'))}</strong></div>`).join('');
        const frame = d.review_url
            ? `<details class="apr-review" open><summary data-lq-component="disclosure" class="lq-disclosure-trigger">学生答题与批改详情</summary>
                 <iframe class="apr-review-frame" src="${escapeHtml(d.review_url)}" title="答题与批改详情" loading="lazy"></iframe>
               </details>`
            : '';
        return `<div class="apr-kv-grid">${table}</div>${frame}`;
    },
    submission_correction(item) {
        const d = item.detail || {};
        const p = item.payload || {};
        const gradingNote = p.grading_interrupted
            ? '申请时 AI 正在批改，已自动暂停'
            : (p.score_at_apply !== null && p.score_at_apply !== undefined ? `申请时已出分 ${p.score_at_apply}` : '申请时尚未批改，批改已暂停');
        const rows = [
            ['学生', d.student_name || item.applicant_name],
            ['作业', d.assignment_title],
            ['申诉次数', `第 ${p.attempt_no || 1} / ${p.attempt_limit || 3} 次`],
            ['问题类型', p.problem_kind_label || '-'],
            ['批改情况', gradingNote],
            ['当前状态', d.already_returned ? '已退回修改' : (d.current_status === 'graded' ? `已批改，得分 ${d.current_score ?? '-'}` : (SUBMISSION_STATUS_LABELS[d.current_status] || d.current_status || '-'))],
            ['提交时间', fmtDate(d.submitted_at)],
            ['默认修改截止', d.recommended_resubmission_due_at ? fmtDate(d.recommended_resubmission_due_at) : '-'],
        ];
        const table = rows.map(([k, v]) => `<div class="apr-kv"><span>${escapeHtml(k)}</span><strong>${escapeHtml(String(v ?? '-'))}</strong></div>`).join('');
        const frame = d.review_url
            ? `<details class="apr-review" open><summary data-lq-component="disclosure" class="lq-disclosure-trigger">该生原始答题卡与批改情况</summary>
                 <iframe class="apr-review-frame" src="${escapeHtml(d.review_url)}" title="原始答题卡与批改情况" loading="lazy"></iframe>
               </details>`
            : '';
        return `<div class="apr-kv-grid">${table}</div>${frame}`;
    },
    grading_appeal(item) {
        const d = item.detail || {};
        const p = item.payload || {};
        const rows = [
            ['学生', d.student_name || item.applicant_name],
            ['作业', d.assignment_title],
            ['申诉次数', `第 ${p.attempt_no || 1} / ${p.attempt_limit || 3} 次`],
            ['申诉时成绩', p.score ?? '-'],
            ['当前成绩', d.current_score ?? '-'],
        ];
        const table = rows.map(([k, v]) => `<div class="apr-kv"><span>${escapeHtml(k)}</span><strong>${escapeHtml(String(v ?? '-'))}</strong></div>`).join('');
        const appealed = (d.appealed_questions || []).map((q) => `
            <li><strong>第 ${escapeHtml(String(q.question_no))} 题</strong>
                <span class="text-muted">（原得分 ${escapeHtml(String(q.current_score ?? '-'))}${q.max_score != null ? ` / ${escapeHtml(String(q.max_score))}` : ''}）</span>
                ${q.note ? `<div class="apr-timeline-note">${escapeHtml(q.note)}</div>` : ''}</li>`).join('');
        const cardPath = d.submission_id ? `/submission/${Number(d.submission_id)}` : '';
        const onCard = Boolean(cardPath) && window.location.pathname === cardPath;
        let frame = '';
        if (onCard) {
            frame = '<p class="text-muted appeal-hint">关闭此窗口即可对照本页答题卡；随时可从“申诉与申请”重新打开。</p>';
        } else if (cardPath) {
            frame = `<details class="apr-review"><summary data-lq-component="disclosure" class="lq-disclosure-trigger">该生答题卡与批改详情</summary>
                 <iframe class="apr-review-frame" src="${escapeHtml(cardPath)}" title="答题卡与批改详情" loading="lazy"></iframe>
               </details>`;
        }
        return `<div class="apr-kv-grid">${table}</div>
            ${appealed ? `<h5 class="appeal-subhead">学生申诉的题目</h5><ul class="appeal-question-notes">${appealed}</ul>` : ''}
            ${frame}`;
    },
    late_submission(item) {
        const d = item.detail || {};
        const p = item.payload || {};
        const rows = [
            ['学生', d.student_name || item.applicant_name],
            ['作业', d.assignment_title],
            ['申请次数', `第 ${p.attempt_no || 1} / ${p.attempt_limit || 4} 次（已获批 ${p.previous_grants || 0} / ${p.grant_limit || 2}）`],
            ['原截止', d.late_submission_until ? `${fmtDate(d.assignment_due_at)}（补交期至 ${fmtDate(d.late_submission_until)}）` : fmtDate(d.assignment_due_at)],
            ['已超时', p.missed_by_seconds != null ? formatSeconds(p.missed_by_seconds) : '-'],
            ['预计需要', p.expected_minutes ? `${p.expected_minutes} 分钟` : '未填写'],
            ['作业原扣分规则', d.assignment_policy_label || '未设置'],
        ];
        const table = rows.map(([k, v]) => `<div class="apr-kv"><span>${escapeHtml(k)}</span><strong>${escapeHtml(String(v ?? '-'))}</strong></div>`).join('');
        const risks = (d.risk_hints || []).map((x) => `<li>${escapeHtml(x)}</li>`).join('');
        return `<div class="apr-kv-grid">${table}</div>
            ${risks ? `<h5 class="appeal-subhead">请留意</h5><ul class="appeal-risk-list">${risks}</ul>` : ''}`;
    },
};

function formatSeconds(total) {
    const seconds = Math.max(0, Number(total) || 0);
    const days = Math.floor(seconds / 86400);
    const hours = Math.floor((seconds % 86400) / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    if (days) return `${days} 天 ${hours} 小时`;
    if (hours) return `${hours} 小时 ${minutes} 分钟`;
    return `${minutes} 分钟`;
}

function defaultDetail(item) {
    const entries = Object.entries(item.detail || {}).filter(([, v]) => v !== null && typeof v !== 'object');
    if (!entries.length) return '';
    return `<div class="apr-kv-grid">${entries.map(([k, v]) => `<div class="apr-kv"><span>${escapeHtml(k)}</span><strong>${escapeHtml(String(v))}</strong></div>`).join('')}</div>`;
}

/* ------------------------------------------------------------------ 批改有误 helpers */
const APPEAL_REGRADE_LIMIT = 2;

function appealRegradeState(d) {
    const regrade = d.ai_regrade || {};
    const candidate = d.ai_candidate && d.ai_candidate.status === 'candidate' ? d.ai_candidate : null;
    const attempts = Number(regrade.attempt || 0);
    const queued = regrade.status === 'queued';
    let text = 'AI 会结合前次批改与学生申诉，找出前次批改的问题并重新评分；结果只作建议，采纳前不影响学生成绩。';
    if (queued) text = `AI 正在以高档模型重改（第 ${attempts} 次），完成后会自动显示结果…`;
    else if (regrade.status === 'failed') text = `上次 AI 重改失败：${regrade.error || '未给出可用结果'}。原成绩未改变。`;
    else if (candidate) {
        text = `AI 复核建议总分 ${candidate.score ?? '-'}（原 ${d.current_score ?? '-'}）${candidate.changed ? '' : '，认为原批改基本正确'}。`;
    }
    return { candidate, attempts, queued, text };
}

function appealRegradeBox(state, hasQuestions) {
    const { candidate, attempts, queued, text } = state;
    const issues = (candidate?.prior_issues || []).map((x) => `<li>${escapeHtml(String(x))}</li>`).join('');
    return `
        <div class="appeal-ai-box">
            <p class="appeal-ai-status">${escapeHtml(text)}</p>
            ${candidate?.appeal_response ? `<p class="appeal-ai-response">${escapeHtml(candidate.appeal_response)}</p>` : ''}
            ${issues ? `<h5 class="appeal-subhead">AI 指出的前次批改问题</h5><ul class="appeal-ai-issues">${issues}</ul>` : ''}
            <div class="appeal-ai-actions">
                <button type="button" class="btn btn-outline btn-sm" data-apr-ai-regrade ${queued || attempts >= APPEAL_REGRADE_LIMIT ? 'disabled' : ''}>
                    ${attempts ? 'AI 再次重改' : 'AI 重改（高档模型）'}</button>
                ${candidate && hasQuestions ? '<button type="button" class="btn btn-outline btn-sm" data-apr-fill-ai>把 AI 建议填入逐题得分</button>' : ''}
                <small class="text-muted">每条申诉最多 ${APPEAL_REGRADE_LIMIT} 次</small>
            </div>
        </div>`;
}

function appealQuestionRow(q, appealed, suggestion) {
    const no = Number(q.question_no);
    const max = q.max_score != null ? Number(q.max_score) : null;
    return `
        <div class="appeal-q-row${appealed ? ' is-appealed' : ''}" data-appeal-q="${no}"
             data-question-id="${escapeHtml(q.question_id || `q${no}`)}" data-max-score="${max ?? ''}">
            <div class="appeal-q-head">
                <strong>第 ${no} 题</strong>
                ${appealed ? '<span class="apr-badge apr-badge--warning">学生申诉</span>' : ''}
                <span class="text-muted">原 ${escapeHtml(String(q.score ?? '-'))}${max != null ? ` / ${max}` : ''}</span>
                ${suggestion ? `<span class="appeal-q-ai">AI 建议 ${escapeHtml(String(suggestion.score ?? '-'))}</span>` : ''}
            </div>
            <label class="form-group appeal-q-score">
                <span class="form-label">得分</span>
                <input type="number" class="form-control" step="0.5" min="0" ${max != null ? `max="${max}"` : ''}
                       data-apr-field="q${no}_score" value="${escapeHtml(String(q.score ?? ''))}">
            </label>
            <label class="form-group appeal-q-comment">
                <span class="form-label">扣分点 / 评语</span>
                <textarea class="form-control" rows="2" maxlength="600" data-apr-field="q${no}_deduction">${escapeHtml(q.deduction_points || '')}</textarea>
            </label>
            <input type="hidden" data-apr-ai="q${no}_score" value="${escapeHtml(String(suggestion?.score ?? ''))}">
            <input type="hidden" data-apr-ai="q${no}_deduction" value="${escapeHtml(suggestion?.deduction_points || '')}">
        </div>`;
}

function collectAppealGrade(form, hasQuestions) {
    const value = (name) => form.querySelector(`[data-apr-field="${name}"]`)?.value?.trim() ?? '';
    const grade = { summary: value('summary') };
    const override = value('score_override');
    if (!hasQuestions) {
        grade.score = override === '' ? null : Number(override);
        return grade;
    }
    grade.questions = [...form.querySelectorAll('[data-appeal-q]')].map((row) => {
        const no = Number(row.dataset.appealQ);
        const score = value(`q${no}_score`);
        return {
            question_no: no,
            question_id: row.dataset.questionId,
            max_score: row.dataset.maxScore === '' ? null : Number(row.dataset.maxScore),
            score: score === '' ? null : Number(score),
            deduction_points: value(`q${no}_deduction`),
        };
    });
    if (override !== '') {
        grade.score_override = Number(override);
        grade.score_override_reason = value('score_override_reason');
    }
    return grade;
}

function bindAppealActions(body, ctx, state) {
    body.querySelector('[data-apr-ai-regrade]')?.addEventListener('click', async (event) => {
        const button = event.currentTarget;
        button.disabled = true;
        button.textContent = '正在发起…';
        try {
            await ctx.post('ai-regrade');
            showToast('已发起 AI 重改，结果出来后会显示在这里，也会通知你', 'success');
        } catch (error) {
            showToast(error.message, 'error');
        }
        ctx.refresh();
    });
    body.querySelector('[data-apr-fill-ai]')?.addEventListener('click', () => {
        for (const node of body.querySelectorAll('[data-apr-ai]')) {
            const target = body.querySelector(`[data-apr-field="${node.dataset.aprAi}"]`);
            if (target && node.value) target.value = node.value;
        }
        const mode = body.querySelector('[data-apr-field="mode"]');
        if (mode) mode.value = 'manual';
        ctx.captureDraft();
        showToast('已填入 AI 建议，可逐题微调后结案', 'info');
    });
    if (state.queued) ctx.poll(5000);
}

/* ------------------------------------------------------------------ 超时补交 helpers */
const LATE_UNIT_SECONDS = { day: 86400, hour: 3600, minute: 60, second: 1 };

function lateWindowFields(d) {
    const minutes = Number(d.default_window_minutes || 1440);
    const [value, unit] = minutes % 1440 === 0 ? [minutes / 1440, 'day'] : minutes % 60 === 0 ? [minutes / 60, 'hour'] : [minutes, 'minute'];
    const option = (v, label, selected) => `<option value="${v}"${selected ? ' selected' : ''}>${label}</option>`;
    return `
        <div class="apr-form-grid">
            <label class="form-group">
                <span class="form-label">补交通道</span>
                <select class="form-control" data-apr-field="window_mode">
                    ${option('duration', '从批准起给一段时长', true)}${option('until', '截止到指定时刻', false)}
                </select>
            </label>
            <label class="form-group" data-late-when="duration">
                <span class="form-label">时长</span>
                <span class="late-inline">
                    <input type="number" class="form-control" min="1" step="1" data-apr-field="duration_value" value="${value}">
                    <select class="form-control" data-apr-field="duration_unit">
                        ${option('minute', '分钟', unit === 'minute')}${option('hour', '小时', unit === 'hour')}${option('day', '天', unit === 'day')}
                    </select>
                </span>
            </label>
            <label class="form-group" data-late-when="until" hidden>
                <span class="form-label">截止时刻</span>
                <input type="datetime-local" class="form-control" data-apr-field="due_at">
            </label>
        </div>`;
}

function latePenaltyFields(d) {
    const inherit = d.assignment_policy
        ? `<option value="inherit" selected>沿用作业原规则（${escapeHtml(d.assignment_policy_label)}，迟交时长从原截止起算）</option>` : '';
    const unitOptions = Object.entries({ day: '天', hour: '小时', minute: '分钟', second: '秒' })
        .map(([v, label]) => `<option value="${v}"${v === 'hour' ? ' selected' : ''}>${label}</option>`).join('');
    return `
        <label class="form-group">
            <span class="form-label">扣分规则</span>
            <select class="form-control" data-apr-field="penalty_mode">
                ${inherit}<option value="custom"${inherit ? '' : ' selected'}>设置新规则</option><option value="none">不扣分</option>
            </select>
        </label>
        <div class="apr-form-grid" data-late-custom ${inherit ? 'hidden' : ''}>
            <label class="form-group"><span class="form-label">方式</span>
                <select class="form-control" data-apr-field="strategy">
                    <option value="gradient" selected>按时间梯度扣分</option><option value="fixed">一次性扣分</option>
                </select></label>
            <label class="form-group"><span class="form-label">每次扣分</span>
                <input type="number" class="form-control" min="0" max="100" step="0.5" data-apr-field="points" value="5"></label>
            <label class="form-group" data-late-gradient><span class="form-label">每隔</span>
                <span class="late-inline">
                    <input type="number" class="form-control" min="1" step="1" data-apr-field="interval_value" value="1">
                    <select class="form-control" data-apr-field="interval_unit">${unitOptions}</select>
                </span></label>
            <label class="form-group" data-late-gradient><span class="form-label">迟交时长起算</span>
                <select class="form-control" data-apr-field="lateness_base">
                    <option value="original_due" selected>从原截止时间（继续累计）</option><option value="window_open">从补交通道开启</option>
                </select></label>
            <label class="form-group"><span class="form-label">最多扣（分，可空）</span>
                <input type="number" class="form-control" min="0" max="100" step="0.5" data-apr-field="max_penalty_points"></label>
            <label class="form-group"><span class="form-label">补交最高得分（可空）</span>
                <input type="number" class="form-control" min="0" max="100" step="0.5" data-apr-field="score_cap"></label>
        </div>
        <p class="text-muted late-preview" data-late-preview></p>`;
}

function collectLatePolicy(form) {
    const value = (name) => form.querySelector(`[data-apr-field="${name}"]`)?.value?.trim() ?? '';
    const number = (name) => (value(name) === '' ? null : Number(value(name)));
    return {
        strategy: value('strategy') || 'gradient',
        points: number('points') ?? 0,
        interval_value: number('interval_value') ?? 1,
        interval_unit: value('interval_unit') || 'hour',
        lateness_base: value('lateness_base') || 'original_due',
        max_penalty_points: number('max_penalty_points'),
        score_cap: number('score_cap'),
    };
}

// Mirrors late_submission_policy.apply_late_policy_to_score for the preview.
function previewLateScore(raw, policy, lateSeconds) {
    if (!policy || policy.strategy === 'none') return raw;
    const interval = Math.max(1, (policy.interval_seconds ?? (policy.interval_value || 1) * (LATE_UNIT_SECONDS[policy.interval_unit] || 3600)));
    let penalty = policy.strategy === 'gradient' ? policy.points * Math.max(1, Math.ceil(Math.max(lateSeconds, 1) / interval)) : policy.points;
    if (policy.max_penalty_points != null) penalty = Math.min(penalty, policy.max_penalty_points);
    let score = Math.max(raw - penalty, Math.min(raw, policy.min_score || 0));
    if (policy.score_cap != null) score = Math.min(score, policy.score_cap);
    return Math.round(score * 100) / 100;
}

function bindLateForm(body, d) {
    const field = (name) => body.querySelector(`[data-apr-field="${name}"]`);
    const refresh = () => {
        const until = field('window_mode')?.value === 'until';
        body.querySelectorAll('[data-late-when]').forEach((node) => { node.hidden = node.dataset.lateWhen !== (until ? 'until' : 'duration'); });
        const mode = field('penalty_mode')?.value;
        const custom = body.querySelector('[data-late-custom]');
        if (custom) custom.hidden = mode !== 'custom';
        const gradient = field('strategy')?.value === 'gradient';
        body.querySelectorAll('[data-late-gradient]').forEach((node) => { node.hidden = !gradient; });
        const preview = body.querySelector('[data-late-preview]');
        if (!preview) return;
        const policy = mode === 'inherit' ? d.assignment_policy : mode === 'none' ? { strategy: 'none' } : collectLatePolicy(body);
        const windowSeconds = until
            ? Math.max(0, (Date.parse(field('due_at')?.value || '') - Date.now()) / 1000)
            : Number(field('duration_value')?.value || 0) * (LATE_UNIT_SECONDS[field('duration_unit')?.value] || 3600);
        // Same base as the server snapshot: lateness counts from the original due date.
        const dueMs = Date.parse(String(d.assignment_due_at || '').replace(' ', 'T'));
        const sinceDue = Number.isNaN(dueMs) ? windowSeconds : Math.max(0, (Date.now() - dueMs) / 1000) + windowSeconds;
        const late = policy?.lateness_base === 'window_open' ? windowSeconds : sinceDue;
        preview.textContent = Number.isFinite(late)
            ? `预览（按通道截止前一刻提交估算）：卷面 100 → ${previewLateScore(100, policy, late)} 分；卷面 80 → ${previewLateScore(80, policy, late)} 分。`
            : '';
    };
    // The decision section is rebuilt on every render, so its listeners die with it.
    const section = body.querySelector('[data-apr-decision]') || body;
    section.addEventListener('input', refresh);
    section.addEventListener('change', refresh);
    refresh();
}

/* ------------------------------------------------------------------ decision forms */
const DECISION_FORMS = {
    submission_withdraw(item) {
        const recommended = item.detail?.recommended_resubmission_due_at || '';
        return {
            html: `
                <div class="apr-form-grid">
                    <label class="form-group">
                        <span class="form-label">重交截止时间</span>
                        <input type="datetime-local" class="form-control" data-apr-field="resubmission_due_at" value="${escapeHtml(toDatetimeLocalValue(recommended))}">
                        <small class="text-muted">留空则按作业截止时间；作业已截止则从现在起 24 小时。两者取更晚的一个。</small>
                    </label>
                    <label class="form-group">
                        <span class="form-label">或延后（分钟）</span>
                        <input type="number" min="1" step="1" class="form-control" data-apr-field="extension_minutes" placeholder="例如 120">
                    </label>
                </div>`,
            collect(form) {
                const payload = {};
                const due = form.querySelector('[data-apr-field="resubmission_due_at"]')?.value?.trim();
                const minutes = form.querySelector('[data-apr-field="extension_minutes"]')?.value?.trim();
                if (due) payload.resubmission_due_at = due;
                if (minutes) payload.extension_minutes = Number(minutes);
                return payload;
            },
        };
    },
    submission_correction(item) {
        const base = DECISION_FORMS.submission_withdraw(item);
        return {
            html: `${base.html.replace('重交截止时间', '修改截止时间')}
                <div class="apr-form-grid">
                    <label class="form-group">
                        <span class="form-label">
                            <input type="checkbox" data-apr-field="prefill_draft" checked>
                            把原答案和附件保留为草稿（学生在原内容上修改）
                        </span>
                    </label>
                    <label class="form-group">
                        <span class="form-label">重新提交是否算迟交</span>
                        <select class="form-control" data-apr-field="late_mode">
                            <option value="inherit" selected>沿用原提交的迟交状态（推荐）</option>
                            <option value="recompute">按重新提交的时间重新判定</option>
                        </select>
                    </label>
                </div>
                <small class="text-muted">学生逾期未重新提交时，将自动以原提交为准（原成绩恢复或重新进入批改）。</small>`,
            collect(form) {
                return {
                    ...base.collect(form),
                    prefill_draft: Boolean(form.querySelector('[data-apr-field="prefill_draft"]')?.checked),
                    late_mode: form.querySelector('[data-apr-field="late_mode"]')?.value || 'inherit',
                };
            },
        };
    },
    grading_appeal(item) {
        const d = item.detail || {};
        const state = appealRegradeState(d);
        const questions = d.current_questions || [];
        const appealed = new Set((d.appealed_questions || []).map((q) => Number(q.question_no)));
        const suggested = new Map((state.candidate?.questions || []).map((q) => [Number(q.question_no), q]));
        const rows = questions.map((q) => appealQuestionRow(q, appealed.has(Number(q.question_no)), suggested.get(Number(q.question_no)))).join('');
        const adoptOption = state.candidate
            ? `<option value="adopt_ai">直接采纳 AI 复核结果（${escapeHtml(String(state.candidate.score ?? '-'))} 分）</option>` : '';
        return {
            html: `
                ${appealRegradeBox(state, questions.length > 0)}
                <label class="form-group">
                    <span class="form-label">结案方式</span>
                    <select class="form-control" data-apr-field="mode">
                        <option value="manual">按下方得分改分</option>${adoptOption}
                    </select>
                    <small class="text-muted">维持原分请点“${escapeHtml(item.reject_label || '维持原分')}”，并在审批意见中说明理由。</small>
                </label>
                ${rows ? `<div class="appeal-q-list">${rows}</div>` : ''}
                <div class="apr-form-grid">
                    <label class="form-group">
                        <span class="form-label">${rows ? '总分（留空按逐题合计）' : '新总分'}</span>
                        <input type="number" class="form-control" step="0.5" min="0" max="100" data-apr-field="score_override" placeholder="${rows ? '自动合计' : '0-100'}">
                    </label>
                    <label class="form-group">
                        <span class="form-label">总分调整原因</span>
                        <input type="text" class="form-control" maxlength="200" data-apr-field="score_override_reason" placeholder="仅在手动改总分时填写">
                    </label>
                </div>
                <label class="form-group">
                    <span class="form-label">复核总评（学生可见）</span>
                    <textarea class="form-control" rows="2" maxlength="1000" data-apr-field="summary" placeholder="留空则写“已按逐题评分复核”"></textarea>
                </label>`,
            collect(form) {
                const mode = form.querySelector('[data-apr-field="mode"]')?.value;
                if (mode === 'adopt_ai' && state.candidate) {
                    return { mode: 'adopt_ai', candidate_revision_id: state.candidate.revision_id };
                }
                return { mode: 'manual', grade: collectAppealGrade(form, questions.length > 0) };
            },
            bind(body, ctx) { bindAppealActions(body, ctx, state); },
        };
    },
    late_submission(item) {
        const d = item.detail || {};
        return {
            html: `${lateWindowFields(d)}${latePenaltyFields(d)}`,
            collect(form) {
                const value = (name) => form.querySelector(`[data-apr-field="${name}"]`)?.value?.trim() ?? '';
                const payload = {
                    window_mode: value('window_mode') || 'duration',
                    penalty_mode: value('penalty_mode') || 'custom',
                };
                if (payload.window_mode === 'until') payload.due_at = value('due_at');
                else Object.assign(payload, { duration_value: value('duration_value'), duration_unit: value('duration_unit') });
                if (payload.penalty_mode === 'custom') payload.penalty_policy = collectLatePolicy(form);
                return payload;
            },
            bind(body) { bindLateForm(body, d); },
        };
    },
};

/* ------------------------------------------------------------------ launcher (applicant) */
export function mountLauncher(root, options) {
    const {
        requestType, subjectId, currentRequest = null, hoverDelay = 1000,
        title = '分数有异议？', text = '如果你认为分数不合理，可以申请撤回本次提交并重新作答。教师审批通过后会开放重交窗口。',
        actionLabel = '申请撤回重做', placeholder = '请说明申请理由（必填），例如：截图未上传完整、作答内容被误判……',
        onSubmitted = null, disabledReason = '', beforeClose = null,
    } = options;
    let request = currentRequest;
    let timer = null;
    let open = false, busy = false, destroyed = false, editing = false, draft = '';
    let handle = null;
    const layer = getLayerSystem(root.ownerDocument);
    const abort = new AbortController();
    const listen = (node, event, callback) => node.addEventListener(event, callback, { signal: abort.signal });

    root.classList.add('apr-launcher');
    root.innerHTML = `
        <button type="button" class="apr-trigger" aria-haspopup="dialog" aria-expanded="false" aria-label="${escapeHtml(title)}">
            <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"></circle><path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.7.4-1 .9-1 1.7"></path><path d="M12 17h.01"></path></svg>
        </button>
        <div class="apr-popover" role="dialog" aria-label="${escapeHtml(title)}" hidden></div>`;
    const trigger = root.querySelector('.apr-trigger');
    const popover = root.querySelector('.apr-popover');
    popover.classList.add('lq-popover', 'lq-glass');
    popover.dataset.lqComponent = 'popover';
    popover.dataset.lqMaterial = 'raised';
    popover.remove();
    adoptControls(root);
    const captureDraft = () => { const input = popover.querySelector('textarea'); if (input) draft = input.value; };
    const reposition = () => { if (handle && open) handle.update({}); };

    function render() {
        captureDraft(); editing = false;
        let body = '';
        if (request && request.status === 'pending') {
            body = `<p class="apr-pop-state">${statusBadge(request)} 已于 ${escapeHtml(fmtDate(request.created_at))} 提交申请，等待教师处理。</p>
                    <p class="apr-pop-reason">${escapeHtml(request.reason || '')}</p>
                    <div class="apr-pop-actions"><button type="button" class="btn btn-outline btn-sm" data-apr-cancel>撤销申请</button></div>`;
        } else if (request && request.status === 'rejected') {
            body = `<p class="apr-pop-state">${statusBadge(request)} 教师意见：${escapeHtml(request.decision_note || '未填写')}</p>
                    <div class="apr-pop-actions"><button type="button" class="btn btn-primary btn-sm" data-apr-start>再次申请</button></div>`;
        } else if (request && request.status === 'approved') {
            const due = request.decision_payload?.resubmission_due_at;
            body = `<p class="apr-pop-state">${statusBadge(request)} 教师已同意撤回${due ? `，请在 ${escapeHtml(fmtDate(due))} 前重新提交` : ''}。</p>
                    <div class="apr-pop-actions"><button type="button" class="btn btn-primary btn-sm" data-apr-reload>刷新页面</button></div>`;
        } else if (disabledReason) {
            body = `<p class="apr-pop-text">${escapeHtml(disabledReason)}</p>`;
        } else {
            body = `<p class="apr-pop-text">${escapeHtml(text)}</p>
                    <div class="apr-pop-actions"><button type="button" class="btn btn-primary btn-sm" data-apr-start>${escapeHtml(actionLabel)}</button></div>`;
        }
        popover.innerHTML = `<div class="apr-pop-title">${escapeHtml(title)}</div>${body}`;
        adoptControls(popover); reposition();
    }

    function renderForm() {
        cancelHide(); editing = true;
        popover.innerHTML = `
            <div class="apr-pop-title">${escapeHtml(actionLabel)}</div>
            <textarea class="form-control apr-pop-textarea" rows="4" maxlength="1000" placeholder="${escapeHtml(placeholder)}"></textarea>
            <div class="apr-pop-actions">
                <button type="button" class="btn btn-outline btn-sm" data-apr-back>返回</button>
                <button type="button" class="btn btn-primary btn-sm" data-apr-submit>提交申请</button>
            </div>`;
        adoptControls(popover);
        popover.querySelector('textarea').value = draft;
        reposition(); popover.querySelector('textarea')?.focus();
    }

    function show() {
        if (destroyed || open) return;
        cancelHide();
        if (editing) renderForm(); else render();
        open = true;
        handle = layer.open(popover, { type: 'popover', modality: 'non-modal', trigger, anchor: trigger, owner: root,
            placement: 'bottom-start', initialFocus: false,
            beforeClose: reason => { captureDraft(); return busy ? false : beforeClose?.(reason); },
            onClose: () => { open = false; trigger.setAttribute('aria-expanded', 'false'); },
            onDestroy: () => { open = false; trigger.setAttribute('aria-expanded', 'false'); cancelTimer(); cancelHide(); },
        });
        trigger.setAttribute('aria-expanded', 'true');
    }
    function hide(reason = 'programmatic') {
        cancelTimer(); cancelHide();
        return open ? layer.close(handle, reason) : Promise.resolve(true);
    }
    function cancelTimer() { if (timer) { clearTimeout(timer); timer = null; } }

    listen(trigger, 'mouseenter', () => { cancelTimer(); timer = setTimeout(show, hoverDelay); });
    listen(trigger, 'mouseleave', cancelTimer);
    listen(trigger, 'click', () => { cancelTimer(); open ? hide() : show(); });
    let leaveTimer = null;
    const scheduleHide = () => {
        if (leaveTimer) clearTimeout(leaveTimer);
        leaveTimer = setTimeout(() => { if (open && !popover.querySelector('textarea')) hide(); }, 250);
    };
    const cancelHide = () => { if (leaveTimer) { clearTimeout(leaveTimer); leaveTimer = null; } };
    listen(root, 'mouseleave', () => { cancelTimer(); scheduleHide(); });
    listen(popover, 'mouseenter', cancelHide);
    listen(popover, 'mouseleave', scheduleHide);
    listen(trigger, 'mouseenter', cancelHide);
    listen(popover, 'input', event => { if (event.target.matches('textarea')) draft = event.target.value; });

    listen(popover, 'click', async (event) => {
        const target = event.target.closest('button');
        if (!target || busy || destroyed) return;
        if (target.hasAttribute('data-apr-start')) { renderForm(); return; }
        if (target.hasAttribute('data-apr-back')) { render(); return; }
        if (target.hasAttribute('data-apr-reload')) { window.location.reload(); return; }
        if (target.hasAttribute('data-apr-cancel')) {
            busy = true; target.disabled = true;
            try {
                const data = await api(`/${request.id}/cancel`, { method: 'POST', body: JSON.stringify({}) });
                if (destroyed) return;
                request = data.request;
                showToast('已撤销申请', 'info');
                render();
            } catch (error) {
                if (!destroyed) { showToast(error.message, 'error'); target.disabled = false; }
            } finally { busy = false; }
            return;
        }
        if (target.hasAttribute('data-apr-submit')) {
            const reason = popover.querySelector('textarea')?.value.trim();
            if (!reason) { showToast('请填写申请理由', 'warning'); return; }
            busy = true; target.disabled = true;
            target.textContent = '提交中…';
            try {
                const data = await api('', { method: 'POST', body: JSON.stringify({ request_type: requestType, subject_id: subjectId, reason }) });
                if (destroyed) return;
                request = data.request; draft = '';
                popover.querySelector('textarea').value = '';
                showToast('申请已提交，教师会收到消息和邮件提醒', 'success');
                render();
                if (typeof onSubmitted === 'function') onSubmitted(request);
            } catch (error) {
                if (!destroyed) { showToast(error.message, 'error'); target.disabled = false; target.textContent = '提交申请'; }
            } finally { busy = false; }
        }
    });

    return { getRequest: () => request, refresh: () => { if (!busy && !destroyed) render(); }, open: show, close: hide,
        destroy() { destroyed = true; cancelTimer(); cancelHide(); abort.abort(); handle?.destroy(); popover.remove(); } };
}

/* ------------------------------------------------------------------ panel (reviewer) */
export function mountPanel(root, options) {
    const {
        scope = 'incoming', assignmentId = '', requestType = '', autoOpenId = null,
        title = '申诉与申请', onDecided = null, startOpen = false, limit = 50, beforeClose = null,
    } = options;
    let items = [];
    let pendingCount = 0;
    let activeId = null, busy = false, destroyed = false, detailVersion = 0, listVersion = 0;
    let drawerLayer = null, modalLayer = null, pollTimer = null;
    const drafts = new Map();
    const layer = getLayerSystem(root.ownerDocument);
    const abort = new AbortController();
    const listen = (node, event, callback) => node.addEventListener(event, callback, { signal: abort.signal });
    const identity = Symbol.for('lanshare.approval.drawer-id');
    const sequence = root.ownerDocument[identity] = (root.ownerDocument[identity] || 0) + 1;
    const drawerId = sequence === 1 ? 'apr-drawer' : `apr-drawer-${sequence}`;

    root.classList.add('apr-drawer-root');
    root.innerHTML = `
        <button type="button" class="apr-drawer-toggle" aria-expanded="false" aria-controls="${drawerId}" title="${escapeHtml(title)}">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 11l3 3L22 4"></path><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"></path></svg>
            <span class="apr-drawer-toggle-label">${escapeHtml(title)}</span>
            <span class="apr-drawer-count" hidden>0</span>
        </button>
        <aside class="apr-drawer lq-glass" data-lq-component="drawer" data-lq-material="raised" id="${drawerId}" aria-label="${escapeHtml(title)}" hidden>
            <div class="apr-drawer-header">
                <div><strong>${escapeHtml(title)}</strong><small class="apr-drawer-sub">待办优先，最新在前</small></div>
                <div class="apr-drawer-tools">
                    <button type="button" class="btn btn-outline btn-sm" data-apr-refresh title="刷新">刷新</button>
                    <button type="button" class="apr-drawer-close" data-apr-close aria-label="收起">&times;</button>
                </div>
            </div>
            <p class="apr-email-hint" data-apr-email hidden></p>
            <div class="apr-list" data-apr-list><div class="apr-empty">正在加载…</div></div>
        </aside>
        <div class="lq-dialog-root apr-modal" data-lq-dialog="modal" data-lq-component="dialog" data-apr-modal hidden>
            <div class="lq-scrim" aria-hidden="true"></div>
            <div class="modal-dialog modal-dialog-wide lq-dialog__surface lq-modal lq-modal--xl lq-glass" role="dialog" aria-label="申请详情">
                <div class="apr-modal-content">
                    <div class="modal-header">
                        <h3 class="modal-title" data-apr-modal-title>申请详情</h3>
                        <button class="modal-close" type="button" data-apr-modal-close aria-label="关闭">&times;</button>
                    </div>
                    <div class="modal-body apr-modal-body" data-apr-modal-body></div>
                </div>
            </div>
        </div>`;
    const toggle = root.querySelector('.apr-drawer-toggle');
    const drawer = root.querySelector('.apr-drawer');
    const countEl = root.querySelector('.apr-drawer-count');
    const listEl = root.querySelector('[data-apr-list]');
    const emailHint = root.querySelector('[data-apr-email]');
    const modal = root.querySelector('[data-apr-modal]');
    const modalTitle = root.querySelector('[data-apr-modal-title]');
    const modalBody = root.querySelector('[data-apr-modal-body]');

    const modalSurface = modal.querySelector('.lq-dialog__surface');
    adoptControls(root);
    // The layer portal keeps all three surfaces outside clipping ancestors and
    // establishes their parent relationship; only it owns Escape/scroll/focus.
    const drawerClose = root.querySelector('[data-apr-close]');
    const refresh = root.querySelector('[data-apr-refresh]');
    const modalClose = root.querySelector('[data-apr-modal-close]');
    drawer.remove(); modal.remove();
    const drawerClosed = () => { toggle.setAttribute('aria-expanded', 'false'); root.classList.remove('is-open'); };
    function setDrawer(openState) {
        if (destroyed) return Promise.resolve(false);
        if (!openState) return drawerLayer ? layer.close(drawerLayer, 'button') : Promise.resolve(true);
        drawerLayer = layer.open(drawer, { type: 'drawer', modality: 'non-modal', owner: root, trigger: toggle,
            closeOnOutside: false, initialFocus: false, onClose: drawerClosed, onDestroy: drawerClosed });
        toggle.setAttribute('aria-expanded', 'true'); root.classList.add('is-open');
        return Promise.resolve(true);
    }

    function captureDraft() {
        if (activeId === null || !modalBody.querySelector('[data-apr-decision]')) return;
        drafts.set(activeId, Object.fromEntries([...modalBody.querySelectorAll('[data-apr-field]')].map(node => [node.dataset.aprField, node.value])));
    }
    function modalClosed() {
        clearTimeout(pollTimer); pollTimer = null;
        detailVersion++; modalBody.replaceChildren(); modalBody._decisionForm = null; activeId = null;
    }

    // Re-render the open request in place; the reviewer's typed draft survives.
    async function refreshActive(id, { quiet = false } = {}) {
        if (destroyed || busy || activeId !== id) return false;
        captureDraft();
        const version = detailVersion;
        try {
            const data = await api(`/${id}`);
            if (destroyed || busy || version !== detailVersion || activeId !== id) return false;
            renderModal(data.request);
            return true;
        } catch (error) {
            if (!quiet && !destroyed && activeId === id) showToast(error.message, 'error');
            return false;
        }
    }

    // Background refresh while an AI job runs. It never replaces the DOM under
    // a focused field (IME composition, caret) and keeps retrying with backoff;
    // the server turns a lost job into "failed", which ends the polling.
    function schedulePoll(id, ms) {
        clearTimeout(pollTimer);
        pollTimer = setTimeout(async () => {
            if (destroyed || activeId !== id) return;
            const focused = modalBody.contains(document.activeElement)
                && document.activeElement.matches('input:not([type=hidden]),textarea,select');
            if (busy || focused) { schedulePoll(id, ms); return; }
            if (!(await refreshActive(id, { quiet: true }))) schedulePoll(id, Math.min(ms * 2, 30000));
        }, ms);
    }

    // Tell reviewers when appeal notices cannot reach their mailbox (plan §7.3).
    function renderEmailHint(channel) {
        const show = channel && ['not_configured', 'no_address'].includes(channel.state);
        emailHint.hidden = !show;
        if (!show) return;
        emailHint.innerHTML = `${escapeHtml(channel.message)} <a href="${escapeHtml(channel.settings_url || '/profile?section=email')}">去设置</a>`;
    }

    function renderList() {
        countEl.hidden = pendingCount <= 0;
        countEl.textContent = String(pendingCount);
        if (!items.length) {
            listEl.innerHTML = '<div class="apr-empty">暂无申请</div>';
            return;
        }
        listEl.innerHTML = items.map((item) => `
            <button type="button" class="apr-item${item.status === 'pending' ? ' is-pending' : ''}" data-apr-open="${item.id}">
                <div class="apr-item-top">
                    <span class="apr-item-type">${escapeHtml(item.request_type_label || '')}</span>
                    ${statusBadge(item)}
                </div>
                <div class="apr-item-title">${escapeHtml(item.applicant_name || '申请人')}</div>
                <div class="apr-item-reason">${escapeHtml(item.reason || '')}</div>
                <div class="apr-item-meta">${escapeHtml(fmtDate(item.created_at))}</div>
            </button>`).join('');
        adoptControls(listEl);
    }

    async function load() {
        if (destroyed) return;
        const version = ++listVersion;
        const params = new URLSearchParams({ scope, limit: String(limit) });
        if (assignmentId) params.set('assignment_id', String(assignmentId));
        if (requestType) params.set('request_type', requestType);
        try {
            const data = await api(`?${params.toString()}`);
            if (destroyed || version !== listVersion) return;
            items = data.items || [];
            pendingCount = Number(data.pending_count || 0);
            renderEmailHint(data.email_channel);
        } catch (error) {
            if (destroyed || version !== listVersion) return;
            items = [];
            pendingCount = 0;
            listEl.innerHTML = `<div class="apr-empty">${escapeHtml(error.message)}</div>`;
            return;
        }
        renderList();
    }

    function closeModal(reason = 'button') {
        return modalLayer ? layer.close(modalLayer, reason) : Promise.resolve(true);
    }

    function renderTimeline(item) {
        const labels = {
            created: '发起申请', approved: '通过', rejected: '拒绝', cancelled: '撤销', auto_cancelled: '自动结束',
            expired: '过期', reminded: '提醒审批人', grading_interrupted: '已暂停进行中的批改',
            ai_regrade_requested: '发起 AI 重改', ai_regrade_completed: 'AI 重改完成', ai_regrade_failed: 'AI 重改失败',
        };
        return `<ol class="apr-timeline">${(item.events || []).map((e) => `
            <li><span class="apr-timeline-when">${escapeHtml(fmtDate(e.created_at))}</span>
                <strong>${escapeHtml(labels[e.event_type] || e.event_type)}</strong>
                ${e.actor_name ? `<span class="text-muted">· ${escapeHtml(e.actor_name)}</span>` : ''}
                ${e.note ? `<div class="apr-timeline-note">${escapeHtml(e.note)}</div>` : ''}</li>`).join('')}</ol>`;
    }

    function renderModal(item) {
        const renderer = DETAIL_RENDERERS[item.request_type] || defaultDetail;
        const decisionForm = item.can_decide && DECISION_FORMS[item.request_type] ? DECISION_FORMS[item.request_type](item) : null;
        modalTitle.textContent = item.title || '申请详情';
        modalBody.innerHTML = `
            <div class="apr-modal-head">
                ${statusBadge(item)}
                <span class="text-muted">${escapeHtml(item.request_type_label || '')} · ${escapeHtml(item.applicant_name || '')} · ${escapeHtml(fmtDate(item.created_at))}</span>
            </div>
            <section class="apr-section">
                <h4>申请理由</h4>
                <p class="apr-reason">${escapeHtml(item.reason || '')}</p>
            </section>
            <section class="apr-section">
                <h4>相关信息</h4>
                ${renderer(item)}
            </section>
            ${item.status !== 'pending' ? `
            <section class="apr-section">
                <h4>处理结果</h4>
                <p>${statusBadge(item)} ${escapeHtml(item.decided_by_name || '')} ${escapeHtml(fmtDate(item.decided_at))}
                ${item.decision_note ? `<br>意见：${escapeHtml(item.decision_note)}` : ''}
                ${item.decision_payload?.resubmission_due_at ? `<br>重交截止：${escapeHtml(fmtDate(item.decision_payload.resubmission_due_at))}` : ''}
                ${item.decision_payload?.notification_suffix ? `<br>${escapeHtml(item.decision_payload.notification_suffix)}` : ''}</p>
            </section>` : ''}
            ${item.can_decide ? `
            <section class="apr-section apr-decision" data-apr-decision>
                <h4>处理</h4>
                ${decisionForm ? decisionForm.html : ''}
                <label class="form-group">
                    <span class="form-label">审批意见 <small class="text-muted">（拒绝时必填）</small></span>
                    <textarea class="form-control" rows="3" maxlength="1000" data-apr-field="note" placeholder="给学生的说明"></textarea>
                </label>
                <div class="apr-decision-actions">
                    <button type="button" class="btn btn-outline" data-apr-decide="reject">${escapeHtml(item.reject_label || '拒绝')}</button>
                    <button type="button" class="btn btn-primary" data-apr-decide="approve">${escapeHtml(item.approve_label || '同意')}</button>
                </div>
            </section>` : ''}
            <section class="apr-section">
                <h4>流转记录</h4>
                ${renderTimeline(item)}
            </section>`;
        modalBody._decisionForm = decisionForm;
        adoptControls(modalBody);
        const draft = item.can_decide ? drafts.get(Number(item.id)) : null;
        if (!item.can_decide) drafts.delete(Number(item.id));
        if (draft) for (const node of modalBody.querySelectorAll('[data-apr-field]')) {
            if (Object.hasOwn(draft, node.dataset.aprField)) node.value = draft[node.dataset.aprField];
        }
        const id = Number(item.id);
        clearTimeout(pollTimer); pollTimer = null;
        decisionForm?.bind?.(modalBody, {
            post: (action) => api(`/${id}/${action}`, { method: 'POST', body: '{}' }),
            refresh: () => refreshActive(id),
            poll: (ms) => schedulePoll(id, ms),
            captureDraft,
        });
    }

    async function openItem(id) {
        if (destroyed || busy) return false;
        captureDraft();
        const version = ++detailVersion;
        activeId = Number(id);
        modalTitle.textContent = '加载中…';
        modalBody.innerHTML = '<div class="apr-empty">正在加载申请详情…</div>';
        modalBody._decisionForm = null;
        const parent = drawerLayer && !['closed', 'destroyed'].includes(drawerLayer.state) ? drawerLayer : null;
        modalLayer = layer.open(modal, { type: 'modal', surface: modalSurface, owner: root,
            trigger: parent ? drawer : toggle, parentLayer: parent,
            returnFocus: () => listEl.querySelector(`[data-apr-open="${activeId}"]`) || toggle,
            beforeClose: reason => { captureDraft(); return busy ? false : beforeClose?.(reason); },
            onCloseRequested: () => { detailVersion++; },
            onClose: modalClosed, onDestroy: modalClosed,
        });
        try {
            const data = await api(`/${id}`);
            if (destroyed || version !== detailVersion || activeId !== Number(id)) return false;
            renderModal(data.request);
        } catch (error) {
            if (destroyed || version !== detailVersion) return false;
            modalBody.innerHTML = `<div class="apr-empty">${escapeHtml(error.message)}</div>`;
        }
        return true;
    }

    async function decide(decision, button) {
        if (busy || destroyed || activeId === null) return;
        const id = activeId, section = modalBody.querySelector('[data-apr-decision]');
        if (!section) return;
        const note = section.querySelector('[data-apr-field="note"]')?.value.trim() || '';
        if (decision === 'reject' && !note) { showToast('拒绝时请填写审批意见', 'warning'); return; }
        const decisionPayload = modalBody._decisionForm ? modalBody._decisionForm.collect(section) : {};
        const originalLabel = button.textContent;
        captureDraft(); busy = true;
        section.querySelectorAll('button').forEach((b) => { b.disabled = true; });
        button.textContent = '处理中…';
        let decided = false, decidedRequest;
        try {
            const data = await api(`/${id}/${decision}`, {
                method: 'POST', body: JSON.stringify({ note, decision_payload: decisionPayload }),
            });
            decided = true; decidedRequest = data.request; drafts.delete(id);
            if (destroyed) return;
            showToast(decision === 'approve' ? '已通过申请，学生将收到通知' : '已拒绝申请，学生将收到通知', 'success');
            await load();
            // A committed decision is never exposed as a retryable action just
            // because refreshing its detail fails afterwards.
            if (destroyed) return;
            const fresh = await api(`/${id}`);
            if (!destroyed && activeId === id) renderModal(fresh.request);
        } catch (error) {
            if (destroyed) return;
            showToast(error.message, 'error');
            if (!decided) {
                section.querySelectorAll('button').forEach((b) => { b.disabled = false; });
                button.textContent = originalLabel;
            } else {
                modalBody.innerHTML = '<div class="apr-empty">处理已完成，详情刷新失败。请关闭后重新打开查看。</div>';
            }
        } finally {
            busy = false;
            if (decided && !destroyed && typeof onDecided === 'function') onDecided(decidedRequest);
        }
    }

    listen(toggle, 'click', () => { const opening = drawer.hidden; setDrawer(opening); if (opening) load(); });
    listen(drawerClose, 'click', () => setDrawer(false));
    listen(refresh, 'click', load);
    listen(listEl, 'click', (event) => {
        const target = event.target.closest('[data-apr-open]');
        if (target) openItem(target.getAttribute('data-apr-open'));
    });
    listen(modalClose, 'click', () => closeModal());
    listen(modalBody, 'input', captureDraft);
    listen(modalBody, 'click', (event) => {
        const target = event.target.closest('[data-apr-decide]');
        if (target) decide(target.getAttribute('data-apr-decide'), target);
    });

    load();
    if (startOpen) setDrawer(true);
    if (autoOpenId) { setDrawer(true); openItem(autoOpenId); }
    return { reload: load, open: openItem, close: closeModal, setDrawer,
        destroy() {
            destroyed = true; detailVersion++; listVersion++; abort.abort(); clearTimeout(pollTimer);
            modalLayer?.destroy(); drawerLayer?.destroy(); modal.remove(); drawer.remove(); drafts.clear();
        } };
}

export default { mountLauncher, mountPanel };
