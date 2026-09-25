// Agent workbench rendering: task data -> keyed HTML entries.
// Every step says what kind of step it is: 思考 / 决定 / 工具 / 操作 / 疑问 / 结果.
// Only typed LQ factories and escaped text produce markup; model Markdown goes
// through the shared sanitizer (renderAIChatMarkdown -> MarkdownRuntime).

import { html as lq } from './lq/components.js';

export function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

export function renderMarkdown(value) {
    const text = String(value ?? '').replace(/\r\n?/g, '\n');
    if (!text.trim()) return '';
    if (typeof window.renderAIChatMarkdown === 'function') return window.renderAIChatMarkdown(text, '');
    return escapeHtml(text).replace(/\n/g, '<br>');
}

const ICONS = {
    thinking: '<path d="M12 3a6 6 0 0 0-3.6 10.8c.4.3.6.8.6 1.3V17h6v-1.9c0-.5.2-1 .6-1.3A6 6 0 0 0 12 3Z"/><path d="M9.5 20h5"/>',
    decision: '<path d="M4 21V4"/><path d="M4 4h11l-2 4 2 4H4"/>',
    tool: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
    operation: '<path d="M13 2 4 14h7l-1 8 9-12h-7l1-8Z"/>',
    guard: '<path d="M12 3 4 6v6c0 4.5 3.4 8.2 8 9 4.6-.8 8-4.5 8-9V6l-8-3Z"/><path d="M12 9v4"/><path d="M12 16h.01"/>',
    question: '<circle cx="12" cy="12" r="9"/><path d="M9.2 9a3 3 0 0 1 5.6 1c0 2-2.8 2.5-2.8 4"/><path d="M12 17h.01"/>',
    note: '<path d="M21 15a3 3 0 0 1-3 3H8l-5 4V6a3 3 0 0 1 3-3h12a3 3 0 0 1 3 3Z"/>',
    artifact: '<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9Z"/><path d="M14 3v6h6"/>',
    result: '<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>',
    failed: '<circle cx="12" cy="12" r="9"/><path d="m15 9-6 6"/><path d="m9 9 6 6"/>',
    web: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18"/><path d="M12 3a14 14 0 0 1 0 18 14 14 0 0 1 0-18"/>',
};

export function icon(name) {
    return `<svg class="awb-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name] || ICONS.note}</svg>`;
}

const KIND_LABEL = { thinking: '思考', decision: '决定', tool: '工具', operation: '操作', guard: '安全拦截',
    question: '疑问', note: '说明', artifact: '文件' };

const STATUS_TONE = { queued: 'neutral', running: 'info', completed: 'success', failed: 'danger', canceled: 'neutral' };

export function statusChip(task) {
    let tone = STATUS_TONE[task.status] || 'neutral';
    if (task.runtime_status === 'waiting_input' && !task.is_terminal) tone = 'warning';
    return lq.chip({ label: task.status_label || task.status || '处理中', kind: 'status', tone, size: 'sm' });
}

export function formatTime(value) {
    if (!value) return '';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return '';
    return date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}

export function formatElapsed(seconds) {
    const value = Math.max(0, Number(seconds || 0));
    if (value < 60) return `${value} 秒`;
    if (value < 3600) return `${Math.floor(value / 60)} 分 ${value % 60} 秒`;
    return `${Math.floor(value / 3600)} 小时 ${Math.floor((value % 3600) / 60)} 分`;
}

function step(kind, key, body, { tone = '', iconName = kind } = {}) {
    return { key, kind, html: `
        <article class="awb-step awb-step--${kind}${tone ? ` is-${tone}` : ''}" data-step-kind="${kind}">
            <span class="awb-step__rail" aria-hidden="true">${icon(iconName)}</span>
            <div class="awb-step__body">
                <div class="awb-step__kind">${KIND_LABEL[kind] || ''}</div>
                ${body}
            </div>
        </article>` };
}

// Artifact links are always built from the fixed task artifact route and an
// encoded relative path; server/model supplied URLs are never used as href.
export function artifactUrl(taskId, path) {
    const relative = String(path || '').replace(/^\/+/, '');
    if (!relative || relative.split('/').some((part) => part === '..' || part === '')) return '';
    return `/api/agent-tasks/${Number(taskId)}/artifacts/${relative.split('/').map(encodeURIComponent).join('/')}`;
}

function toolIconName(detail) {
    return /web_(search|fetch)|public_fetch/.test(String(detail.tool || '')) ? 'web' : 'tool';
}

function renderToolStep(entry) {
    const { call, result } = entry;
    const detail = call?.detail || {};
    const outcome = result?.detail || null;
    const state = outcome ? (outcome.ok === false ? 'failed' : 'ok') : 'pending';
    const summary = outcome ? escapeHtml(result.message || outcome.summary || '') : '<span class="awb-dim">进行中…</span>';
    return step('tool', entry.key, `
        <div class="awb-line">
            <span class="awb-line__title">${escapeHtml(detail.label || call?.message || '调用工具')}</span>
            <span class="awb-state awb-state--${state}">${state === 'ok' ? '完成' : state === 'failed' ? '未成功' : '进行中'}</span>
        </div>
        <div class="awb-line__sub">${summary}</div>`, { tone: state === 'failed' ? 'danger' : '', iconName: toolIconName(detail) });
}

function safetyCheckHtml(check) {
    if (!check || typeof check !== 'object') return '';
    const state = { invalid: '数据已确认无效', expired: '数据已确认过期', user_confirmed: '你已在选项中确认', user_specified: '你明确指定了目标' }[check.data_state] || '';
    const parts = [check.user_requested ? '你明确要求' : '非你直接要求', state, check.target_count ? `影响 ${Number(check.target_count)} 条` : ''].filter(Boolean);
    return `<div class="awb-safety"><strong>执行前自检</strong><span>${escapeHtml(parts.join(' · '))}</span>${check.reason ? `<p>${escapeHtml(check.reason)}</p>` : ''}</div>`;
}

function renderOperationStep(entry) {
    const { call, result } = entry;
    const detail = call?.detail || {};
    const outcome = result?.detail || null;
    const state = outcome ? (outcome.ok === false ? 'failed' : (outcome.status === 'uncertain' ? 'uncertain' : 'ok')) : 'pending';
    const stateLabel = { ok: '已执行', failed: '未成功', uncertain: '待核对', pending: '执行中' }[state];
    const target = [detail.method, detail.path].filter(Boolean).join(' ');
    return step('operation', entry.key, `
        <div class="awb-line">
            <span class="awb-line__title">${escapeHtml(detail.intent || detail.label || call?.message || '平台操作')}</span>
            <span class="awb-state awb-state--${state}">${stateLabel}</span>
        </div>
        ${target ? `<div class="awb-line__meta"><code>${escapeHtml(target)}</code></div>` : ''}
        ${safetyCheckHtml(detail.safety_check)}
        ${outcome ? `<div class="awb-line__sub">${escapeHtml(result.message || outcome.summary || '')}</div>` : ''}`,
        { tone: state === 'failed' ? 'danger' : state === 'uncertain' ? 'warning' : 'strong' });
}

function renderQuestionStatic(event, answered) {
    const question = event.detail?.question || {};
    const answers = answered?.detail?.answers || [];
    const items = (question.questions || []).map((item) => {
        const answer = answers.find((candidate) => candidate.id === item.id);
        const reply = answer ? [...(answer.selected || []), answer.custom ? `“${answer.custom}”` : ''].filter(Boolean).join('、') : '';
        return `<li><span>${escapeHtml(item.question)}</span>${reply ? `<strong>→ ${escapeHtml(reply)}</strong>` : '<em class="awb-dim">未回答</em>'}</li>`;
    }).join('');
    return step('question', `e${event.id}:${answered ? 'a' : 'o'}`, `
        <div class="awb-line"><span class="awb-line__title">${escapeHtml(question.title || '需要你确认')}</span>
        <span class="awb-state awb-state--${answered ? 'ok' : 'neutral'}">${answered ? '已回答' : '已关闭'}</span></div>
        <ul class="awb-qa">${items}</ul>`);
}

export function renderQuestionCard(question) {
    const groups = (question.questions || []).map((item, index) => {
        const options = (item.options || []).map((option, optionIndex) => `
            <button type="button" class="awb-option" data-awb-option="${escapeHtml(option.label)}" aria-pressed="false">
                <span class="awb-option__head">
                    <span class="awb-option__label">${escapeHtml(option.label)}</span>
                    ${optionIndex === 0 ? '<span class="awb-option__badge">推荐</span>' : ''}
                </span>
                ${option.description ? `<small>${escapeHtml(option.description)}</small>` : ''}
            </button>`).join('');
        return `
            <fieldset class="awb-question__group" data-awb-qid="${escapeHtml(item.id)}" data-multi="${item.multi_select ? '1' : '0'}">
                <legend><span class="awb-question__num">${index + 1}</span>${escapeHtml(item.question)}${item.multi_select ? '<span class="awb-dim">（可多选）</span>' : ''}</legend>
                ${item.detail ? `<p class="awb-question__detail">${escapeHtml(item.detail)}</p>` : ''}
                <div class="awb-options">
                    ${options}
                    <button type="button" class="awb-option awb-option--custom" data-awb-custom aria-pressed="false">
                        <span class="awb-option__head"><span class="awb-option__label">自定义输入…</span></span>
                        <small>以上都不合适，我来详细说明</small>
                    </button>
                </div>
                <textarea class="awb-custom" data-awb-custom-input rows="2" hidden aria-label="自定义回答" placeholder="直接告诉 Agent 你的想法或正确做法…"></textarea>
            </fieldset>`;
    }).join('');
    return { key: `q:${question.id}`, kind: 'question', html: `
        <section class="awb-question" data-awb-question="${escapeHtml(question.id)}" aria-label="Agent 的疑问">
            <header class="awb-question__head">${icon('question')}<div><strong>需要你确认</strong><span>${escapeHtml(question.title || '')}</span></div></header>
            ${question.context ? `<p class="awb-question__context">${escapeHtml(question.context)}</p>` : ''}
            <form data-awb-question-form>
                ${groups}
                <footer class="awb-question__foot">
                    <small class="awb-dim">回答后自动继续</small>
                    ${lq.button({ label: '提交回答', variant: 'prominent', size: 'sm', icon: 'send', type: 'submit', attrs: { 'data-awb-answer-submit': '' } })}
                </footer>
            </form>
        </section>` };
}

const SYSTEM_EVENTS = new Set(['queued', 'started', 'resumed', 'task_paused', 'pause_requested', 'pause_withdrawn', 'task_resumed',
    'cancel_requested', 'canceled', 'runtime_stop_pending', 'attachments_saved', 'supplements_delivered', 'action_executed',
    'action_failed', 'auto_retry']);
const HIDDEN_EVENTS = new Set(['title_ready', 'usage', 'completed', 'failed', 'runtime_update', 'question_answered']);

/** Merge the event log into ordered, keyed timeline entries. */
export function buildTimeline(task) {
    const events = Array.isArray(task.events) ? [...task.events].sort((a, b) => a.id - b.id) : [];
    const entries = [];
    const calls = new Map();
    const answers = events.filter((event) => event.event_type === 'question_answered');
    const pendingId = task.pending_question?.id || '';
    for (const event of events) {
        const type = event.event_type;
        const detail = event.detail || {};
        if (HIDDEN_EVENTS.has(type)) continue;
        if (type === 'tool_call' || type === 'operation') {
            const entry = { key: `call:${event.id}`, kind: type === 'operation' ? 'operation' : 'tool', call: event, result: null };
            if (detail.call_id) calls.set(detail.call_id, entry);
            entries.push(entry);
        } else if (type === 'tool_result' || type === 'operation_result') {
            const entry = calls.get(detail.call_id);
            if (entry && !entry.result) {
                entry.result = event;
                entry.key = `${entry.key}:${event.id}`;
            }
        } else if (type === 'thinking') {
            const text = String(detail.text || event.message || '');
            entries.push(step('thinking', `e${event.id}`, `
                <details class="awb-thinking"><summary><span class="awb-thinking__preview">${escapeHtml(text.slice(0, 90))}</span></summary>
                <div class="awb-thinking__text">${escapeHtml(text)}</div></details>`));
        } else if (type === 'decision') {
            const next = (detail.next_steps || []).map((item) => `<li>${escapeHtml(item)}</li>`).join('');
            entries.push(step('decision', `e${event.id}`, `
                <p class="awb-decision">${escapeHtml(detail.decision || event.message)}</p>
                ${detail.rationale ? `<p class="awb-line__sub">${escapeHtml(detail.rationale)}</p>` : ''}
                ${next ? `<ol class="awb-next">${next}</ol>` : ''}`, { tone: detail.system ? '' : 'accent' }));
        } else if (type === 'guard') {
            entries.push(step('guard', `e${event.id}`, `<p>${escapeHtml(detail.message || event.message)}</p>`, { tone: 'warning' }));
        } else if (type === 'assistant_text') {
            entries.push(step('note', `e${event.id}`, `<div class="awb-prose md-content ai-chat-markdown">${renderMarkdown(detail.text || event.message)}</div>`));
        } else if (type === 'artifact') {
            const url = artifactUrl(task.id, detail.path);
            const name = escapeHtml(detail.name || detail.path);
            entries.push(step('artifact', `e${event.id}`, url ? `<a class="awb-file" href="${escapeHtml(url)}" target="_blank" rel="noopener">${name}</a>` : `<span class="awb-file">${name}</span>`));
        } else if (type === 'question_requested') {
            const question = detail.question || {};
            if (question.id && question.id === pendingId) continue; // rendered as the live card
            entries.push(renderQuestionStatic(event, answers.find((item) => item.detail?.question_id === question.id)));
        } else if (type === 'pending_supplement') {
            entries.push({ key: `e${event.id}`, kind: 'user', html: `<div class="awb-user awb-user--note"><span>补充说明</span><div class="awb-user__text">${escapeHtml(detail.supplement || event.message)}</div></div>` });
        } else if (SYSTEM_EVENTS.has(type) || event.message) {
            entries.push({ key: `e${event.id}`, kind: 'system', html: `<div class="awb-system"><span>${escapeHtml(event.message)}</span><time>${escapeHtml(formatTime(event.created_at))}</time></div>` });
        }
    }
    return entries.map((entry) => {
        if (entry.html) return entry;
        return entry.kind === 'operation' ? renderOperationStep(entry) : renderToolStep(entry);
    });
}

export function renderInstruction(task) {
    const attachments = Array.isArray(task.attachments) ? task.attachments : [];
    const files = attachments.map((item) => lq.chip({ label: item.name || item.stored_name || '附件', kind: 'tag', size: 'sm' })).join('');
    return { key: 'instruction', kind: 'user', html: `
        <div class="awb-user">
            <div class="awb-user__text">${escapeHtml(task.private_instruction || task.title || '')}</div>
            ${files ? `<div class="awb-user__files">${files}</div>` : ''}
        </div>` };
}

export function renderLiveState(task, queueState = {}) {
    if (task.is_terminal) return null;
    let body;
    if (task.runtime_status === 'waiting_input') body = `${icon('question')}<span>等你回答，不占用队列</span>`;
    else if (task.is_parked) body = `${icon('decision')}<span>已暂停，点“继续”恢复</span>`;
    else if (task.status === 'queued') {
        const paused = queueState.queue_paused ? '（队列已暂停）' : '';
        const ahead = task.queue_position ? `，前面 ${Math.max(0, task.queue_position - 1)} 个` : '';
        body = `${lq.spinner({ size: 'sm' })}<span>排队中${ahead}${task.estimated_wait_label ? ` · ${escapeHtml(task.estimated_wait_label)}` : ''}${paused}</span>`;
    } else {
        body = `${lq.spinner({ size: 'sm' })}<span>${task.pause_requested ? '本步完成后暂停' : '执行中'} · ${escapeHtml(formatElapsed(task.elapsed_seconds))}</span>`;
    }
    return { key: `live:${task.status}:${task.runtime_status}:${task.pause_requested ? 1 : 0}:${task.queue_position || 0}`, kind: 'live',
        html: `<div class="awb-live" role="status">${body}</div>` };
}

export function renderResult(task) {
    if (!task.is_terminal) return null;
    const detail = task.result_detail || {};
    const ok = task.status === 'completed';
    const deliverable = renderMarkdown(detail.deliverable_markdown || '');
    const operations = Array.isArray(detail.operations) ? detail.operations : [];
    const artifacts = Array.isArray(detail.artifacts) ? detail.artifacts : [];
    const usage = detail.usage || {};
    const opsHtml = operations.length ? `
        <div class="awb-result__section"><h4>已执行的操作</h4><ul class="awb-ops">${operations.map((item) => `
            <li class="${item.ok ? 'is-ok' : 'is-failed'}"><span>${escapeHtml(item.label || item.capability_key || item.action)}</span>
            <strong>${item.ok ? (item.status === 'submitted' ? '已提交' : '成功') : (item.status === 'uncertain' ? '待核对' : '未成功')}</strong></li>`).join('')}</ul></div>` : '';
    const filesHtml = artifacts.length ? `
        <div class="awb-result__section"><h4>产物文件</h4><div class="awb-files">${artifacts.map((item) => {
            const url = artifactUrl(task.id, item.path);
            const name = escapeHtml(item.name || item.path);
            return url ? `<a class="awb-file" href="${escapeHtml(url)}" target="_blank" rel="noopener">${name}</a>` : `<span class="awb-file">${name}</span>`;
        }).join('')}</div></div>` : '';
    const error = !ok && (task.error_message || task.result_summary)
        ? `<p class="awb-result__error">${escapeHtml(task.error_message || task.result_summary)}</p>` : '';
    const legacy = Array.isArray(detail.proposed_actions) && detail.proposed_actions.length
        ? `<p class="awb-dim">此任务由旧版 Agent 生成了 ${detail.proposed_actions.length} 条待确认提案；新版 Agent 会在确认意图后直接执行，如仍需要请追问让 Agent 执行。</p>` : '';
    const usageLine = usage.requests ? `模型调用 ${usage.requests} 次 · 输入 ${usage.input_tokens || 0} · 输出 ${usage.output_tokens || 0} tokens` : '';
    const actions = [];
    if (task.status === 'failed' || task.status === 'canceled') {
        actions.push(lq.button({ label: '原样重试', variant: 'soft', size: 'sm', icon: 'refresh-cw', attrs: { 'data-awb-action': 'retry' } }));
        actions.push(lq.button({ label: '修改后重试', variant: 'ghost', size: 'sm', icon: 'pencil', attrs: { 'data-awb-action': 'retry-edit' } }));
    }
    const title = ok ? '结果' : task.status === 'canceled' ? '已取消' : '未完成';
    return { key: `result:${task.status}:${task.completed_at}`, kind: 'result', html: `
        <section class="awb-result ${ok ? 'is-ok' : 'is-failed'}">
            <header class="awb-result__head">${icon(ok ? 'result' : 'failed')}<strong>${title}</strong>${statusChip(task)}</header>
            ${error}
            ${deliverable ? `<div class="awb-prose md-content ai-chat-markdown">${deliverable}</div>` : ''}
            ${opsHtml}${filesHtml}${legacy}
            <footer class="awb-result__foot"><small class="awb-dim">${escapeHtml(usageLine)}</small><span class="awb-result__actions">${actions.join('')}</span></footer>
        </section>` };
}

export function renderTaskListItem(task, selectedId) {
    const meta = [task.origin_label, formatTime(task.created_at)].filter(Boolean).join(' · ');
    return `
        <li class="awb-list__row${Number(task.id) === Number(selectedId) ? ' is-current' : ''}">
            <button type="button" class="awb-list__item" data-awb-open="${task.id}">
                <span class="awb-list__title">${escapeHtml(task.title || task.public_summary || `任务 #${task.id}`)}</span>
                <span class="awb-list__meta">${statusChip(task)}<small>${escapeHtml(meta)}</small></span>
            </button>
        </li>`;
}

export function renderAdminRow(task) {
    const buttons = [];
    const id = String(task.id);
    if (task.is_parked && task.runtime_status !== 'waiting_input') {
        buttons.push(lq.button({ label: '恢复', size: 'sm', variant: 'soft', attrs: { 'data-awb-admin': 'resume', 'data-task-id': id } }));
    } else if (!task.is_terminal && task.runtime_status !== 'waiting_input') {
        buttons.push(lq.button({ label: '暂停', size: 'sm', variant: 'soft', attrs: { 'data-awb-admin': 'pause', 'data-task-id': id } }));
    }
    if (!task.is_terminal) {
        buttons.push(lq.button({ label: '停止', size: 'sm', variant: 'destructive', attrs: { 'data-awb-admin': 'stop', 'data-task-id': id } }));
    }
    const where = task.status === 'running' ? `执行 ${formatElapsed(task.elapsed_seconds)}` : task.queue_position ? `第 ${task.queue_position} 位` : '';
    return `
        <li class="awb-admin__row">
            <div class="awb-admin__info">
                <strong>${escapeHtml(task.title || task.public_summary || `任务 #${task.id}`)}</strong>
                <span>${statusChip(task)}<small>${escapeHtml([task.owner_name, where].filter(Boolean).join(' · '))}</small></span>
            </div>
            <div class="awb-admin__actions">${buttons.join('')}</div>
        </li>`;
}
