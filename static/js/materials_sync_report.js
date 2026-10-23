/**
 * Git teaching-repository sync: progress polling and result rendering.
 *
 * Used by the library cards (one-click update), the explorer's Git panel and
 * the repository modal, so every surface tells the same story:
 *   phase progress → change summary → per-classroom lesson mapping →
 *   warnings / unresolved entries.
 *
 *   const run = startRepositoryUpdate(materialId, { onProgress, onDone, onError })
 *   renderSyncProgress(container, progress)
 *   renderSyncReport(container, result, { compact })
 *   renderBindingPlan(container, plan)
 */
import { apiFetch } from './api.js';
import { escapeHtml, formatDate } from './ui.js';
import { kindIcon } from './materials_kinds.js';

const POLL_INTERVAL_MS = 900;

const STATUS_LABELS = {
    bound: { label: '已对应', tone: 'success' },
    will_bind: { label: '将对应', tone: 'primary' },
    will_move: { label: '将移动', tone: 'warning' },
    suppressed: { label: '已手动解除', tone: 'neutral' },
    missing_session: { label: '无有效课次', tone: 'danger' },
    will_release: { label: '将解除', tone: 'warning' },
    unchanged: { label: '保持', tone: 'neutral' },
};

export function statusMeta(status) {
    return STATUS_LABELS[status] || { label: status || '未知', tone: 'neutral' };
}

/** Run one `update` and poll progress while the request is in flight. */
export function startRepositoryUpdate(materialId, { onProgress, onDone, onError } = {}) {
    const id = Number(materialId);
    let stopped = false;
    let timer = 0;
    const stop = () => {
        stopped = true;
        window.clearTimeout(timer);
    };
    const poll = async () => {
        if (stopped) return;
        try {
            const data = await apiFetch(`/api/materials/${id}/repository/progress`, { silent: true });
            if (!stopped) onProgress?.(data.progress || null);
        } catch {
            /* progress is best effort */
        }
        if (!stopped) timer = window.setTimeout(poll, POLL_INTERVAL_MS);
    };
    onProgress?.({ status: 'running', phase: 'queued', label: '正在提交更新请求', percent: 2, elapsed_seconds: 0 });
    timer = window.setTimeout(poll, 250);
    const request = apiFetch(`/api/materials/${id}/repository/command`, {
        method: 'POST',
        body: { action: 'update', command: '' },
        silent: true,
    }).then((result) => {
        stop();
        onDone?.(result);
        return result;
    }).catch((error) => {
        stop();
        onError?.(error);
        throw error;
    });
    return { promise: request, stop };
}

export function renderSyncProgress(container, progress) {
    if (!container) return;
    if (!progress || progress.status === 'idle') {
        container.innerHTML = '';
        container.hidden = true;
        return;
    }
    container.hidden = false;
    const phases = Array.isArray(progress.phases) && progress.phases.length
        ? progress.phases
        : [{ phase: progress.phase, label: progress.label, percent: progress.percent }];
    const currentIndex = Math.max(0, phases.findIndex((item) => item.phase === progress.phase));
    const running = progress.status === 'running';
    const tone = running ? 'info' : (progress.status === 'success' ? 'success' : 'danger');
    container.innerHTML = `
        <div class="materials-sync-progress is-${tone}" role="status" aria-live="polite">
            <div class="materials-sync-progress__head">
                <span class="materials-sync-progress__spinner" aria-hidden="true">${running ? '<span class="lq-spinner lq-spinner--sm" data-lq-component="spinner"></span>' : (progress.status === 'success' ? kindIcon('check') : kindIcon('alert'))}</span>
                <strong>${escapeHtml(progress.label || (running ? '处理中' : '已结束'))}</strong>
                <span class="materials-sync-progress__meta">${escapeHtml(progress.message || '')}${progress.elapsed_seconds ? ` · ${Math.round(progress.elapsed_seconds)}s` : ''}</span>
            </div>
            <div class="materials-sync-progress__bar"><span style="width:${Math.max(2, Math.min(100, Number(progress.percent) || 0))}%"></span></div>
            <ol class="materials-sync-progress__steps">
                ${phases.filter((item) => item.phase !== 'done').map((item, index) => `
                    <li class="${index < currentIndex ? 'is-done' : (index === currentIndex ? 'is-current' : '')}">${escapeHtml(item.label)}</li>
                `).join('')}
            </ol>
        </div>
    `;
}

function summaryChips(summary = {}) {
    const items = [
        ['新增', summary.inserted, 'success'],
        ['更新', summary.updated, 'primary'],
        ['删除', summary.deleted, 'danger'],
        ['未变化', summary.unchanged, 'neutral'],
    ];
    return items.map(([label, value, tone]) => `<span class="lq-chip lq-chip--sm lq-chip--status" data-lq-status data-lq-tone-level="${tone}">${label} ${Number(value) || 0}</span>`).join('');
}

function classroomLabel(item) {
    const label = [item.course_name, item.class_name].filter(Boolean).join(' · ');
    return label || `课堂 #${item.class_offering_id}`;
}

function groupAssignments(bindings) {
    const groups = new Map();
    for (const item of bindings?.assignments || []) {
        const key = Number(item.class_offering_id);
        if (!groups.has(key)) groups.set(key, { ...item, lessons: [] });
        groups.get(key).lessons.push(item);
    }
    for (const warning of bindings?.warnings || []) {
        const key = Number(warning.class_offering_id || 0);
        if (!key) continue;
        if (!groups.has(key)) groups.set(key, { ...warning, lessons: [] });
        (groups.get(key).warnings ||= []).push(warning);
    }
    return [...groups.values()];
}

function renderBindings(bindings, { compact = false } = {}) {
    if (!bindings) return '';
    const groups = groupAssignments(bindings);
    const unresolved = bindings.unresolved || [];
    const groupHtml = groups.length
        ? groups.map((group) => `
            <section class="materials-sync-classroom">
                <header>
                    <strong>${escapeHtml(classroomLabel(group))}</strong>
                    <span>${group.lessons.filter((item) => item.target_type === 'lesson').length} 个课次${group.lessons.some((item) => item.target_type === 'home') ? ' · 首页' : ''}${group.warnings?.length ? ` · ${group.warnings.length} 项提醒` : ''}</span>
                </header>
                ${compact ? '' : `
                <ul class="materials-sync-lessons">
                    ${group.lessons.sort((a, b) => Number(a.order_index) - Number(b.order_index)).map((item) => `
                        <li>
                            <span class="materials-sync-lessons__ordinal">${item.target_type === 'home' ? '首页' : `第 ${escapeHtml(String(item.order_index))} 次课`}</span>
                            <span class="materials-sync-lessons__title" title="${escapeHtml(item.session_title || '')}">${escapeHtml(item.session_title || '')}${item.session_date ? ` <small>${escapeHtml(item.session_date)}</small>` : ''}</span>
                            <span class="materials-sync-lessons__path" title="${escapeHtml(item.material_path || '')}">${escapeHtml(item.relative_path || item.material_path || '')}</span>
                            <span class="lq-chip lq-chip--sm lq-chip--status" data-lq-status data-lq-tone-level="${item.newly_bound ? 'success' : 'neutral'}">${item.newly_bound ? '新对应' : '已核对'}</span>
                        </li>
                    `).join('')}
                    ${(group.warnings || []).map((warning) => `
                        <li class="is-warning">
                            <span class="materials-sync-lessons__ordinal">${warning.lesson_order ? `第 ${escapeHtml(String(warning.lesson_order))} 次课` : '课次'}</span>
                            <span class="materials-sync-lessons__title">${escapeHtml(warning.reason || warning.code || '')}</span>
                            <span></span>
                            <span class="lq-chip lq-chip--sm lq-chip--status" data-lq-status data-lq-tone-level="warning">需核对</span>
                        </li>
                    `).join('')}
                </ul>`}
            </section>
        `).join('')
        : `<p class="materials-sync-empty">${escapeHtml(bindings.message || '仓库尚未分配课堂，未建立课次对应。')}</p>`;
    const unresolvedHtml = unresolved.length
        ? `
            <details class="materials-sync-unresolved" open>
                <summary data-lq-component="disclosure" class="lq-disclosure-trigger">无法自动判定课次的入口（${unresolved.length}）</summary>
                <ul>${unresolved.map((item) => `<li><code>${escapeHtml(item.relative_path || item.material_path || item.name || `#${item.material_id}`)}</code> <span>${escapeHtml(item.reason || '')}</span></li>`).join('')}</ul>
            </details>`
        : '';
    return `
        <div class="materials-sync-bindings">
            <div class="materials-sync-bindings__head">
                <strong>课次对应</strong>
                <span>${escapeHtml(bindings.message || '')}</span>
            </div>
            ${groupHtml}
            ${unresolvedHtml}
        </div>
    `;
}

/** Render the result of `POST …/repository/command`. */
export function renderSyncReport(container, result, { compact = false } = {}) {
    if (!container) return;
    if (!result) {
        container.innerHTML = '';
        return;
    }
    const ok = result.status === 'success';
    const tone = ok ? 'success' : (result.status === 'auth_required' ? 'warning' : 'danger');
    const changed = Array.isArray(result.changed_entries) ? result.changed_entries : [];
    const lessondocWarnings = result.sync_summary?.lessondoc_warnings || [];
    container.innerHTML = `
        <div class="materials-sync-report is-${tone}">
            <div class="materials-sync-report__head">
                <span class="materials-sync-report__icon" aria-hidden="true">${ok ? kindIcon('check') : kindIcon('alert')}</span>
                <div>
                    <strong>${escapeHtml(result.message || (ok ? '仓库更新完成' : '仓库更新失败'))}</strong>
                    <div class="materials-sync-report__chips">${summaryChips(result.sync_summary)}</div>
                </div>
            </div>
            ${changed.length && !compact ? `
                <details class="materials-sync-changes">
                    <summary data-lq-component="disclosure" class="lq-disclosure-trigger">变更文件（${changed.length}）</summary>
                    <ul>${changed.slice(0, 120).map((entry) => `<li data-status="${escapeHtml(entry.status || '')}"><span>${escapeHtml({ inserted: '新增', updated: '更新', deleted: '删除' }[entry.status] || entry.status || '')}</span><code>${escapeHtml(entry.relative_path || entry.material_path || '')}</code></li>`).join('')}${changed.length > 120 ? `<li><span></span><em>还有 ${changed.length - 120} 项…</em></li>` : ''}</ul>
                </details>` : ''}
            ${lessondocWarnings.length ? `<ul class="materials-sync-warnings">${lessondocWarnings.map((item) => `<li>${escapeHtml(typeof item === 'string' ? item : (item.message || JSON.stringify(item)))}</li>`).join('')}</ul>` : ''}
            ${renderBindings(result.learning_bindings, { compact })}
            ${!ok && result.combined_output ? `<details class="materials-sync-output"><summary data-lq-component="disclosure" class="lq-disclosure-trigger">Git 输出</summary><pre>${escapeHtml(result.combined_output)}</pre></details>` : ''}
        </div>
    `;
}

/** Render the read-only `binding-plan` payload. */
export function renderBindingPlan(container, plan) {
    if (!container) return;
    if (!plan) {
        container.innerHTML = '';
        return;
    }
    const entries = plan.entries || [];
    const offerings = plan.offerings || [];
    const unresolved = plan.unresolved || [];
    container.innerHTML = `
        <div class="materials-binding-plan">
            <div class="materials-binding-plan__head">
                <strong>课次对应预览</strong>
                <span>${escapeHtml(plan.message || '')}</span>
            </div>
            <div class="materials-binding-plan__entries">
                ${entries.length ? entries.map((entry) => `<span class="lq-chip lq-chip--sm" title="${escapeHtml(entry.material_path || '')}">${entry.target_type === 'home' ? '首页' : `第 ${escapeHtml(String(entry.lesson_order))} 次`} · ${escapeHtml(entry.relative_path || entry.name)}</span>`).join('') : '<span class="materials-sync-empty">仓库中没有识别到 lesson_N / 第N次课 形式的入口文档。</span>'}
            </div>
            ${offerings.map((offering) => `
                <section class="materials-sync-classroom">
                    <header>
                        <strong>${escapeHtml(classroomLabel(offering))}</strong>
                        <span>${escapeHtml(String(offering.numbered_session_count || 0))} 个有效课次</span>
                    </header>
                    <ul class="materials-sync-lessons">
                        ${(offering.lessons || []).map((lesson) => {
                            const meta = statusMeta(lesson.status);
                            return `
                                <li data-status="${escapeHtml(lesson.status)}">
                                    <span class="materials-sync-lessons__ordinal">${lesson.target_type === 'home' ? '首页' : `第 ${escapeHtml(String(lesson.lesson_order))} 次课`}</span>
                                    <span class="materials-sync-lessons__title">${lesson.session ? `${escapeHtml(lesson.session.title || '')}${lesson.session.session_date ? ` <small>${escapeHtml(lesson.session.session_date)}</small>` : ''}` : '<em>没有对应的有效课次</em>'}</span>
                                    <span class="materials-sync-lessons__path">${lesson.entry ? escapeHtml(lesson.entry.relative_path || lesson.entry.name) : '<em>仓库中已无此入口</em>'}</span>
                                    <span class="lq-chip lq-chip--sm lq-chip--status" data-lq-status data-lq-tone-level="${escapeHtml(meta.tone)}">${escapeHtml(meta.label)}</span>
                                </li>`;
                        }).join('')}
                    </ul>
                </section>
            `).join('')}
            ${unresolved.length ? `
                <details class="materials-sync-unresolved" open>
                    <summary data-lq-component="disclosure" class="lq-disclosure-trigger">需手工确认的入口（${unresolved.length}）</summary>
                    <ul>${unresolved.map((item) => `<li><code>${escapeHtml(item.relative_path || item.material_path || item.name || '')}</code> <span>${escapeHtml(item.reason || '')}</span></li>`).join('')}</ul>
                </details>` : ''}
        </div>
    `;
}

/** Short inline status for a card: "10-23 09:00 同步 · +2 ~1 · 3 课次". */
export function describeLastSync(item) {
    const at = item?.git_last_sync_at || '';
    const summary = item?.git_last_sync_summary || {};
    const status = String(item?.git_last_sync_status || '');
    if (!at) return { text: '尚未同步', tone: 'neutral', title: '' };
    const parts = [];
    if (summary.inserted) parts.push(`+${summary.inserted}`);
    if (summary.updated) parts.push(`~${summary.updated}`);
    if (summary.deleted) parts.push(`-${summary.deleted}`);
    const changes = parts.length ? parts.join(' ') : '无变化';
    const bindings = summary.bound_lessons ? ` · ${summary.bound_lessons} 课次` : '';
    const tone = status === 'success' ? (parts.length ? 'success' : 'neutral') : 'danger';
    return {
        text: `${formatDate(at) || at} 同步 · ${changes}${bindings}`,
        tone,
        title: summary.message || '',
    };
}
