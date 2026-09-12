/**
 * 重修 / 插班生管理面板（课堂教师页 · 班级名单区）。
 *
 * AI 只负责按学号前缀识别候选（POST detect），敲定必须教师本人点"确认"：
 * 确认时可设默认平时分（留空 = 70），后端会自动补记历史缺交默认分并刷新
 * 已生成的平时成绩表 / 考核登分表。撤销后按普通学生处理。
 */

import { apiFetch } from './api.js';
import { escapeHtml, showToast } from './ui.js';

export function initClassroomRetakePanel(panel) {
if (!panel) return;
if (panel.retakeController) return panel.retakeController;

const STATUS_META = {
    confirmed: { label: '已确认', tone: 'confirmed' },
    suggested: { label: 'AI 建议', tone: 'suggested' },
    dismissed: { label: '已撤销', tone: 'dismissed' },
};

const state = {
    classOfferingId: 0,
    items: [],
    busy: false,
    loaded: false,
    epoch: 0,
    abort: null,
};

function renderList() {
    const list = panel?.querySelector('[data-retake-list]');
    if (!list) return;
    if (!state.items.length) {
        list.hidden = false;
        list.innerHTML = '<p class="classroom-retake-empty">尚无重修/插班生记录。点击"AI 识别插班生"按学号前缀扫描候选，确认后系统自动处理平时分与期末材料。</p>';
        return;
    }
    list.hidden = false;
    list.innerHTML = state.items.map((item) => {
        const meta = STATUS_META[item.status] || STATUS_META.suggested;
        const score = Number(item.default_ordinary_score ?? 70);
        const confirmControls = item.status === 'confirmed'
            ? `
                <span class="classroom-retake-score">默认平时分 <b>${escapeHtml(String(score))}</b> 分</span>
                <button type="button" class="btn btn-ghost btn-sm text-warning" data-retake-revoke="${escapeHtml(String(item.student_id))}">撤销</button>
            `
            : `
                <label class="classroom-retake-score-input">
                    <span>默认平时分</span>
                    <input type="number" min="0" max="100" step="1" value="${escapeHtml(String(score))}" data-retake-score-input="${escapeHtml(String(item.student_id))}" inputmode="decimal">
                </label>
                <button type="button" class="btn btn-primary btn-sm" data-retake-confirm="${escapeHtml(String(item.student_id))}">确认为插班生</button>
            `;
        return `
            <article class="classroom-retake-item" data-tone="${escapeHtml(meta.tone)}">
                <div class="classroom-retake-item__main">
                    <span class="classroom-retake-item__badge">${escapeHtml(meta.label)}</span>
                    <strong>${escapeHtml(item.student_name || '未命名')}</strong>
                    <small>${escapeHtml(item.student_number || '')}</small>
                </div>
                ${item.suggested_reason ? `<p class="classroom-retake-item__reason">${escapeHtml(item.suggested_reason)}</p>` : ''}
                <div class="classroom-retake-item__actions">${confirmControls}</div>
            </article>
        `;
    }).join('');
}

function setBusy(busy) {
    state.busy = busy;
    const detectBtn = panel?.querySelector('[data-retake-detect]');
    if (detectBtn) {
        detectBtn.disabled = busy;
        detectBtn.textContent = busy ? '处理中…' : 'AI 识别插班生';
    }
    panel?.querySelectorAll('[data-retake-confirm], [data-retake-revoke], [data-retake-score-input]').forEach((button) => {
        button.disabled = busy;
    });
}

async function loadList() {
    if (state.loaded || state.busy || state.abort) return;
    const epoch = ++state.epoch;
    const abort = state.abort = new AbortController();
    try {
        const data = await apiFetch(`/api/classroom/${state.classOfferingId}/retake-students`, { silent: true, signal: abort.signal });
        if (abort.signal.aborted || epoch !== state.epoch) return;
        state.loaded = true;
        state.items = Array.isArray(data.items) ? data.items : [];
        renderList();
    } catch (error) {
        if (abort.signal.aborted || epoch !== state.epoch) return;
        const list = panel.querySelector('[data-retake-list]');
        list.hidden = false;
        list.innerHTML = `<p role="alert">${escapeHtml(error.message || '读取插班生名单失败')}</p><button type="button" class="btn btn-outline btn-sm" data-retake-retry>重新读取</button>`;
    } finally { if (state.abort === abort) state.abort = null; }
}

async function detect() {
    if (state.busy) return;
    state.abort?.abort(); state.abort = null; state.epoch++;
    setBusy(true);
    try {
        const data = await apiFetch(`/api/classroom/${state.classOfferingId}/retake-students/detect`, {
            method: 'POST',
            body: {},
        });
        state.items = Array.isArray(data.items) ? data.items : [];
        renderList();
        showToast(data.message || 'AI 识别完成', data.detection?.suggestions?.length ? 'success' : 'info', 6200);
    } catch (error) {
        showToast(error.message || 'AI 识别失败，请稍后重试', 'error');
    } finally {
        setBusy(false);
    }
}

async function confirmStudent(studentId) {
    if (state.busy) return;
    const input = panel?.querySelector(`[data-retake-score-input="${studentId}"]`);
    const raw = String(input?.value ?? '').trim();
    const score = raw === '' ? null : Number(raw);
    if (score !== null && (!Number.isFinite(score) || score < 0 || score > 100)) {
        showToast('默认平时分必须在 0 到 100 之间。', 'warning');
        return;
    }
    state.abort?.abort(); state.abort = null; state.epoch++;
    setBusy(true);
    try {
        const data = await apiFetch(`/api/classroom/${state.classOfferingId}/retake-students/confirm`, {
            method: 'POST',
            body: { student_id: Number(studentId), default_score: score },
        });
        state.items = Array.isArray(data.items) ? data.items : [];
        renderList();
        showToast(data.message || '已确认为重修/插班学生', 'success', 8200);
    } catch (error) {
        showToast(error.message || '确认失败，请稍后重试', 'error');
    } finally {
        setBusy(false);
    }
}

async function revokeStudent(studentId) {
    if (state.busy) return;
    state.abort?.abort(); state.abort = null; state.epoch++;
    setBusy(true);
    try {
        const data = await apiFetch(`/api/classroom/${state.classOfferingId}/retake-students/revoke`, {
            method: 'POST',
            body: { student_id: Number(studentId) },
        });
        state.items = Array.isArray(data.items) ? data.items : [];
        renderList();
        showToast(data.message || '已撤销重修/插班标记', 'success', 6200);
    } catch (error) {
        showToast(error.message || '撤销失败，请稍后重试', 'error');
    } finally {
        setBusy(false);
    }
}

function init() {
    if (!panel) return;
    state.classOfferingId = Number(panel.dataset.classOfferingId || 0);
    if (!state.classOfferingId) return;
    panel.querySelector('[data-retake-detect]')?.addEventListener('click', detect);
    panel.addEventListener('click', (event) => {
        if (event.target.closest('[data-retake-retry]')) { loadList(); return; }
        const confirmBtn = event.target.closest('[data-retake-confirm]');
        if (confirmBtn) {
            confirmStudent(confirmBtn.dataset.retakeConfirm);
            return;
        }
        const revokeBtn = event.target.closest('[data-retake-revoke]');
        if (revokeBtn) {
            revokeStudent(revokeBtn.dataset.retakeRevoke);
        }
    });
}

init();
panel.retakeController = {
    activate: loadList,
    deactivate: () => { state.abort?.abort(); state.abort = null; state.epoch++; },
    isDirty: () => [...panel.querySelectorAll('[data-retake-score-input]')].some(input => input.value !== input.defaultValue),
    isBusy: () => state.busy,
    reset: () => { if (!state.busy) renderList(); },
};
return panel.retakeController;
}
