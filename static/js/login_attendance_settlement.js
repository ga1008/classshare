/** Persistent local settlement intent. LQ owns controls and confirmation layers. */
import { createDomainButton } from './lq/domain-controls.js';
import { createComponent } from './lq/components.js';
import { confirm as confirmDialog } from './lq/dialogs.js';
import { getLayerSystem } from './lq/layer.js';
import { createAmbientPoll } from './ambient_poll.js';

const ACTIVE = new Set(['queued', 'running']);
const STAGES = { queued: '等待后台结算', collecting: '读取本轮接入证据', calculating: '核算出勤结果', publishing: '保存结算结果', completed: '结算完成', failed: '结算未完成', superseded: '已由新结算替代' };
const node = (tag, text = '') => { const n = document.createElement(tag); n.textContent = text; return n; };

export function createAttendanceSettlement({ host, base, getRound, getLayer, isBusy, request, onAccepted, onCompleted, onStateChange }) {
    const button = createDomainButton({ label: '立即结算', variant: 'glass', size: 'sm' });
    const status = node('p'), progress = node('div'), feedback = node('p'), controls = node('div');
    status.setAttribute('role', 'status'); feedback.setAttribute('role', 'alert');
    progress.className = 'la-settlement-progress'; controls.className = 'la-actions';
    const refreshButton = createDomainButton({ label: '刷新结算状态', variant: 'ghost', size: 'sm' }); controls.append(refreshButton);
    host.className = 'la-settlement'; host.setAttribute('aria-label', '本地结算进度'); host.append(status, progress, feedback, controls);
    let disposed = false, roundId = null, contextKey = '', value = null, serial = 0, reading = null, submitting = false, confirming = false;
    let intent = null, errorMessage = '', completedKey = null, resultPending = false;
    const controllers = new Set();
    const currentRound = () => getRound();
    const eligible = () => { const r = currentRound(); return Boolean(r?.id && r.state !== 'void' && (r.is_current === undefined || r.is_current)); };
    const isActive = () => ACTIVE.has(value?.task?.state);
    const restart = () => value?.gate?.can_start ? false : Boolean(value?.gate?.can_restart || isActive() || value?.task?.state === 'failed' || currentRound()?.state === 'settling');
    const endpoint = () => `${base}/rounds/${roundId}/settlement`;
    const disabled = (n, state) => { n.disabled = state; n.setAttribute('aria-disabled', String(state)); n.dataset.lqDisabled = String(state); };
    function render() {
        if (disposed) return;
        const locked = submitting || isBusy(), gate = value?.gate;
        const allowed = restart() ? gate?.can_restart : gate?.can_start;
        disabled(button, locked || !eligible() || (!intent && !allowed)); disabled(refreshButton, locked || Boolean(reading) || !eligible());
        button.textContent = intent && !submitting ? '确认结算请求' : '立即结算';
        button.setAttribute('aria-busy', String(submitting));
        button.title = !currentRound() ? '先开始记录，再立即结算' : isActive() ? '再次点击可确认中断当前结算并重算' : gate?.message || '结束记录并按当前证据结算';
        host.hidden = !currentRound() || (!value?.task && !errorMessage && (!gate || gate.can_start));
        host.setAttribute('aria-busy', String(submitting || isActive()));
        const blocked = gate && !gate.can_start && !gate.can_restart;
        feedback.textContent = errorMessage; feedback.hidden = !errorMessage; controls.hidden = !(errorMessage || (blocked && eligible()));
        progress.replaceChildren();
        const task = value?.task;
        progress.hidden = !task;
        if (task) {
            const label = STAGES[task.stage] || STAGES[task.state] || '正在结算';
            const percent = task.percent != null && Number.isFinite(Number(task.percent)) ? Math.max(0, Math.min(100, Number(task.percent))) : null;
            progress.append(createComponent('progress', { label: '本地点名结算进度', value: percent, max: 100 }));
            const counts = Number.isFinite(Number(task.completed)) && Number(task.total) > 0 ? ` · 阶段 ${task.completed}/${task.total}` : '';
            status.textContent = `${label}${counts}${percent == null ? '' : ` · ${Math.round(percent)}%`}。${task.message || ''}${isActive() ? ' 可关闭浮窗，后台继续；再次点击“立即结算”可中断重算。' : ''}`;
            if (blocked && gate.message) status.textContent += ` ${gate.message}`;
        } else status.textContent = gate?.message || '正在读取本轮结算状态…';
    }
    async function call(options = {}, signal) {
        const controller = new AbortController(); controllers.add(controller);
        const abort = () => controller.abort(); signal?.addEventListener('abort', abort, { once: true });
        if (signal?.aborted) controller.abort();
        try { return await request(endpoint(), { ...options, signal: controller.signal }); }
        finally { signal?.removeEventListener('abort', abort); controllers.delete(controller); }
    }
    function completionIdentity() {
        const task = value?.task;
        return task?.state === 'completed' ? `${roundId}:${task.id}:${task.generation}` : !task && currentRound()?.state === 'settling' && value?.gate?.can_start ? `${roundId}:automatic:${value.round_revision}` : null;
    }
    function refreshResults() {
        const key = completionIdentity();
        if (!key || key === completedKey || resultPending) return;
        completedKey = key; resultPending = true; let failed = false;
        Promise.resolve(onCompleted(roundId)).catch(error => {
            failed = true;
            if (disposed || key !== completedKey) return;
            completedKey = null; errorMessage = `结算已完成，学生结果暂未刷新。${error.message || '请刷新结算状态后重试。'}`; render();
        }).finally(() => { resultPending = false; if (!disposed && (!failed || completionIdentity() !== key)) refreshResults(); });
    }
    function accept(result, preserveError = false) {
        value = result; if (!preserveError) errorMessage = ''; render(); onStateChange(); refreshResults();
    }
    async function refresh(signal, preserveError = false) {
        if (disposed || submitting || !eligible() || reading) return;
        const generation = ++serial, id = roundId;
        const operation = call({}, signal); reading = operation; render();
        try {
            const result = await operation;
            if (!disposed && generation === serial && id === roundId) accept(result, preserveError);
        } catch (error) {
            if (disposed || generation !== serial || id !== roundId) return;
            errorMessage = `结算状态暂未读取，已有记录与任务会保留。${error.message || ''}`; render();
            if (signal) throw error;
        } finally { if (reading === operation) { reading = null; render(); } }
    }
    async function submit() {
        if (disposed || submitting || confirming || isBusy() || !eligible()) return;
        const id = roundId, observed = value, needsRestart = restart();
        if (!intent && !(needsRestart ? observed?.gate?.can_restart : observed?.gate?.can_start)) return;
        if (!intent && needsRestart) {
            confirming = true; render();
            const confirmed = await confirmDialog({ title: '中断当前结算并重新开始？', message: '将中断当前补报等待或结算任务，立即按已收到的证据重新核算；人工改签会保留。', confirmLabel: '中断并重新结算', danger: true }, { trigger: button, parentLayer: getLayer() });
            confirming = false; render();
            if (!confirmed || disposed || id !== roundId || isBusy()) return;
        }
        if (!intent) intent = { idempotency_key: crypto.randomUUID(), expected_round_revision: Math.max(Number(currentRound()?.revision) || 0, Number(observed.round_revision) || 0), restart: needsRestart,
            expected_task_id: observed.task?.id || null, expected_task_generation: observed.task?.generation ?? null };
        submitting = true; serial++; controllers.forEach(controller => controller.abort()); reading = null; errorMessage = ''; render(); onStateChange();
        try {
            const result = await call({ method: 'POST', body: intent });
            if (disposed || id !== roundId) return;
            intent = null;
            if (result.round) onAccepted(result.round);
            accept(result);
        } catch (error) {
            if (disposed || id !== roundId) return;
            const rejected = [400, 401, 403, 404, 409, 422].includes(error.status);
            if (rejected) { intent = null; if (value) value = { ...value, gate: { can_start: false, can_restart: false, code: 'refreshing', message: '正在核对最新任务状态后恢复操作。' } }; }
            errorMessage = rejected ? `本次结算请求未执行。${error.message || '请读取最新状态后重试。'}` : `结算请求结果尚未确认，请使用原请求恢复。${error.message || ''}`;
        } finally { submitting = false; if (!disposed && id === roundId) { render(); onStateChange(); if (!intent && errorMessage) void refresh(undefined, true); } }
    }
    function update() {
        if (disposed) return;
        const r = currentRound(), nextId = r?.id || null, nextContext = `${nextId}:${r?.revision}:${r?.state}:${r?.is_current}`;
        if (nextId !== roundId) {
            serial++; controllers.forEach(controller => controller.abort()); reading = null;
            roundId = nextId; value = null; intent = null; errorMessage = ''; completedKey = null;
        }
        const changed = contextKey !== nextContext; contextKey = nextContext; render();
        if (changed && eligible() && !submitting) void refresh();
    }
    const poll = createAmbientPoll(signal => refresh(signal), { intervalMs: 2500, timeoutMs: 22000,
        active: () => !disposed && !submitting && !confirming && !reading && !document.hidden && eligible() && getLayerSystem(document).top() === getLayer() && (isActive() || (!value?.task && currentRound()?.state === 'settling')) });
    button.addEventListener('click', submit); refreshButton.addEventListener('click', () => refresh());
    poll.start(2500); render();
    return { button, update, refresh, isActive, hasTask: () => Boolean(value?.task), taskState: () => value?.task?.state, isSubmitting: () => submitting || confirming, destroy() { disposed = true; serial++; poll.dispose(); controllers.forEach(controller => controller.abort()); } };
}
