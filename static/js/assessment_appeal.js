/**
 * Student 申诉 entry (assignment page + exam page).
 *
 *   mountAppealEntry(root, state)
 *
 * `state` comes from the server (assessment_appeal_service.build_student_appeal_state):
 * which appeal types are open for this answer, why the others are not, how
 * many attempts are used, and the pending / latest request. The entry is a
 * compact bar; the full flow (choose a type → fill the form → follow the
 * status) lives in an LQ sheet so it works the same on phones and desktops.
 * Requests go through the shared approval API (/api/approvals).
 */
import { showToast, escapeHtml } from './ui.js';
import { openDialog } from './lq/dialogs.js';
import { getLayerSystem } from './lq/layer.js';
import { adoptDomainControl } from './lq/domain-controls.js';

const API = '/api/approvals';
const STATUS_TONE = { pending: 'warning', approved: 'success', rejected: 'danger' };
const PROBLEM_KINDS = [
    ['content', '答案内容有误'],
    ['attachment', '附件有误'],
    ['both', '内容和附件都有误'],
];
const REASON_MIN = 10;

function fmtDate(value) {
    if (!value) return '';
    const date = new Date(String(value).replace(' ', 'T'));
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
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
        throw new Error(typeof detail === 'string' ? detail : (detail?.message || '请求失败，请稍后重试'));
    }
    return data;
}

function adopt(container) {
    for (const node of container.querySelectorAll('button,textarea')) {
        const choice = node.matches('[data-appeal-type]');
        adoptDomainControl(node, {
            ...(choice ? { kind: 'choice' } : {}),
            variant: node.matches('[data-appeal-primary]') ? 'prominent' : node.matches('[data-appeal-quiet]') ? 'ghost' : 'glass',
        });
        if (choice) node.dataset.lqShape = 'surface';
    }
}

function statusChip(item) {
    const tone = STATUS_TONE[item.status];
    return `<span class="lq-status" data-lq-status${tone ? ` data-lq-tone-level="${tone}"` : ''}>${escapeHtml(item.status_label || item.status)}</span>`;
}

function requestSummary(item) {
    const decided = item.status !== 'pending' && item.decision_note
        ? `<p class="appeal-note"><strong>教师回复：</strong>${escapeHtml(item.decision_note)}</p>` : '';
    const suffix = item.decision_payload?.notification_suffix
        ? `<p class="appeal-note">${escapeHtml(item.decision_payload.notification_suffix)}</p>` : '';
    return `
        <div class="appeal-request">
            <div class="appeal-request__head">
                <strong>${escapeHtml(item.request_type_label || '申诉')}</strong>${statusChip(item)}
                <span class="appeal-muted">${escapeHtml(fmtDate(item.created_at))}</span>
            </div>
            <p class="appeal-note"><strong>理由：</strong>${escapeHtml(item.reason || '')}</p>
            ${decided}${suffix}
        </div>`;
}

function questionPicker(questions) {
    const rows = questions.map((q) => {
        const no = Number(q.question_no);
        const score = q.score ?? '-';
        const max = q.max_score != null ? ` / ${q.max_score}` : '';
        return `
            <div class="appeal-question">
                <label class="appeal-radio"><input type="checkbox" value="${no}" data-appeal-question><span>第 ${no} 题</span><span class="appeal-muted">得分 ${escapeHtml(String(score))}${escapeHtml(max)}</span></label>
                <input type="text" class="lq-input" maxlength="200" data-appeal-question-note="${no}" hidden
                       placeholder="这道题哪里判得不对（可选）">
            </div>`;
    }).join('');
    return `
        <div class="appeal-choice-group">
            <span class="appeal-choice-group__label" id="appeal-question-label">需要复核的题目（可多选）</span>
            <div class="appeal-question-list" role="group" aria-labelledby="appeal-question-label">${rows}</div>
        </div>`;
}

function barText(state) {
    if (state.pending) return `申诉处理中：${state.pending.request_type_label || ''}`;
    const latest = state.latest;
    if (latest && ['approved', 'rejected'].includes(latest.status)) {
        return `最近一次申诉${latest.status === 'approved' ? '已通过' : '未通过'}：${latest.request_type_label || ''}`;
    }
    return '对批改或提交有疑问？';
}

export function mountAppealEntry(root, state) {
    if (!root || !state || root.dataset.appealMounted) return;
    root.dataset.appealMounted = '1';
    const anyOpen = (state.types || []).some(type => type.available);
    if (!anyOpen && !state.pending && !state.latest) return;
    root.classList.add('appeal-entry');
    root.innerHTML = `
        <span class="appeal-entry__text">${escapeHtml(barText(state))}</span>
        <button type="button" class="lq-btn lq-btn--sm" data-appeal-open>${state.pending ? '查看申诉' : '申诉'}</button>`;
    adopt(root);
    root.querySelector('[data-appeal-open]').addEventListener('click', () => openAppealSheet(state));
}

function openAppealSheet(state) {
    const body = document.createElement('div');
    body.className = 'appeal-sheet';
    const footer = document.createElement('div');
    footer.className = 'appeal-sheet__foot';
    let handle = null;
    const close = () => handle && getLayerSystem(document).close(handle, 'button');
    const reloadSoon = () => setTimeout(() => location.reload(), 500);

    const renderHistory = () => {
        const past = (state.history || []).filter(item => item.status !== 'pending');
        if (!past.length) return '';
        return `<details class="appeal-history"><summary data-lq-component="disclosure" class="lq-disclosure-trigger">历史申诉（${past.length}）</summary>
            ${past.map(requestSummary).join('')}</details>`;
    };

    const renderPending = () => {
        const item = state.pending;
        body.innerHTML = `
            ${requestSummary(item)}
            <p class="appeal-muted">教师处理后你会收到站内通知和邮件。${item.request_type === 'submission_correction' ? '申请期间这份答题的批改会暂停。' : ''}</p>
            ${renderHistory()}`;
        footer.innerHTML = item.can_cancel
            ? '<button type="button" class="lq-btn" data-appeal-quiet data-appeal-cancel>撤销申请</button>' : '';
        adopt(body); adopt(footer);
        footer.querySelector('[data-appeal-cancel]')?.addEventListener('click', async (event) => {
            const button = event.currentTarget;
            button.disabled = true;
            try {
                await api(`/${item.id}/cancel`, { method: 'POST', body: JSON.stringify({}) });
                showToast('申诉已撤销（撤销也会计入申请次数）', 'success');
                close();
                reloadSoon();
            } catch (error) {
                showToast(error.message, 'error');
                button.disabled = false;
            }
        });
    };

    const renderChooser = () => {
        const cards = (state.types || []).map(type => `
            <button type="button" class="appeal-type" data-appeal-type="${escapeHtml(type.key)}" ${type.available ? '' : 'disabled aria-disabled="true"'}>
                <span class="appeal-type__title">${escapeHtml(type.label)}</span>
                <span class="appeal-type__desc">${escapeHtml(type.description)}</span>
                <span class="appeal-type__meta">${type.available ? `${type.key === 'late_submission' ? '已获批' : '已用'} ${type.used} / ${type.limit} 次` : escapeHtml(type.reason || '当前不可申请')}</span>
            </button>`).join('');
        body.innerHTML = `
            <p class="appeal-muted">选择申诉类型。每种申诉都有次数上限，请确认后再提交。</p>
            <div class="appeal-types">${cards}</div>
            ${renderHistory()}`;
        footer.innerHTML = '';
        adopt(body);
        for (const card of body.querySelectorAll('[data-appeal-type]:not([disabled])')) {
            card.addEventListener('click', () => renderForm(state.types.find(type => type.key === card.dataset.appealType)));
        }
    };

    const renderForm = (type) => {
        const isCorrection = type.key === 'submission_correction';
        const isGrading = type.key === 'grading_appeal';
        const isLate = type.key === 'late_submission';
        const gradingQuestions = isGrading ? (state.grading_questions || []) : [];
        const problemField = isCorrection ? `
            <div class="appeal-choice-group">
                <span class="appeal-choice-group__label" id="appeal-problem-label">哪里提交错了</span>
                <div class="appeal-radio-group" role="radiogroup" aria-labelledby="appeal-problem-label">${PROBLEM_KINDS.map(([value, label], index) => `
                    <label class="appeal-radio"><input type="radio" name="appeal-problem" value="${value}" ${index === 0 ? 'checked' : ''}> ${escapeHtml(label)}</label>`).join('')}
                </div>
            </div>` : '';
        let hint = '';
        if (isCorrection) {
            hint = '提交后这份答题的批改会暂停。教师同意后，你的原答案和附件会保留为草稿，可直接修改后重新提交；逾期未重新提交则以原提交为准。';
        } else if (isLate) {
            hint = '教师同意后会为你单独开启补交通道，并设定截止时间和扣分规则；通道开启后请在页面倒计时结束前提交。';
        } else if (isGrading) {
            hint = '教师会逐题复核，也可能请 AI 重新评分。复核结果可能上调、维持或下调分数；复核期间当前成绩保持不变。';
        }
        body.innerHTML = `
            <h3 class="appeal-form__title">${escapeHtml(type.label)}</h3>
            ${hint ? `<p class="appeal-muted">${escapeHtml(hint)}</p>` : ''}
            ${problemField}
            ${gradingQuestions.length ? questionPicker(gradingQuestions) : ''}
            ${isLate ? `<label class="lq-field">
                <span class="lq-field__label">预计需要多少分钟完成（可选）</span>
                <input type="number" class="lq-input" min="1" max="20160" step="1" inputmode="numeric" data-appeal-expected placeholder="例如 90">
            </label>` : ''}
            <label class="lq-field">
                <span class="lq-field__label">申诉理由（至少 ${REASON_MIN} 字）</span>
                <textarea class="lq-textarea" rows="4" maxlength="1000" data-appeal-reason placeholder="请具体说明情况，便于教师判断"></textarea>
            </label>
            <p class="appeal-muted">${isLate
                ? `补交最多获批 ${type.limit} 次，目前已获批 ${type.used} 次。`
                : `这是第 ${type.used + 1} / ${type.limit} 次“${escapeHtml(type.label)}”申诉。`}</p>`;
        footer.innerHTML = `
            <button type="button" class="lq-btn" data-appeal-back>返回</button>
            <button type="button" class="lq-btn" data-appeal-primary data-appeal-submit>提交申诉</button>`;
        adopt(body); adopt(footer);
        footer.querySelector('[data-appeal-back]').addEventListener('click', renderChooser);
        footer.querySelector('[data-appeal-submit]').addEventListener('click', async (event) => {
            const button = event.currentTarget;
            const reasonField = body.querySelector('[data-appeal-reason]');
            const reason = reasonField.value.trim();
            if (reason.length < REASON_MIN) {
                showToast(`请把理由写具体一些（至少 ${REASON_MIN} 字）`, 'warning');
                reasonField.focus();
                return;
            }
            const payload = {};
            const problem = body.querySelector('input[name="appeal-problem"]:checked');
            if (problem) payload.problem_kind = problem.value;
            const expected = body.querySelector('[data-appeal-expected]')?.value.trim();
            if (expected) payload.expected_minutes = Number(expected);
            if (gradingQuestions.length) {
                payload.questions = [...body.querySelectorAll('[data-appeal-question]:checked')].map(box => ({
                    question_no: Number(box.value),
                    note: body.querySelector(`[data-appeal-question-note="${box.value}"]`)?.value.trim() || '',
                }));
                if (!payload.questions.length) {
                    showToast('请勾选需要复核的题目', 'warning');
                    return;
                }
            }
            button.disabled = true;
            try {
                await api('', {
                    method: 'POST',
                    body: JSON.stringify({ request_type: type.key, subject_id: state.subject_id, reason, payload }),
                });
                showToast('申诉已提交，教师会收到提醒', 'success');
                close();
                reloadSoon();
            } catch (error) {
                showToast(error.message, 'error');
                button.disabled = false;
            }
        });
        for (const box of body.querySelectorAll('[data-appeal-question]')) {
            box.addEventListener('change', () => {
                const note = body.querySelector(`[data-appeal-question-note="${box.value}"]`);
                if (note) note.hidden = !box.checked;
            });
        }
        body.querySelector('[data-appeal-reason]').focus();
    };

    if (state.pending) renderPending();
    else renderChooser();
    handle = openDialog({ type: 'sheet', title: '申诉', body, footer });
}
