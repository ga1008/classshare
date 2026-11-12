/** One student's local correction and durable school push; no class-wide dispatch. */
import { createDomainButton } from './lq/domain-controls.js';
import { createAmbientPoll } from './ambient_poll.js';
import { getLayerSystem } from './lq/layer.js';
import { ATTENDANCE_STATES } from './login_attendance_states.js';

const el = (tag, text) => { const node = document.createElement(tag); if (text !== undefined) node.textContent = String(text); return node; };
const ACTIVE = new Set(['queued', 'checking', 'sending', 'outcome_unknown']);
const stateLabel = state => ATTENDANCE_STATES.find(([key]) => key === state)?.[1] || state;

export function mountStudentCorrection({ host, endpoint, record, field, request, initialDraft, intentState,
    editable, parentBusy, getLayer, onBusy, onDirty, onDraft, onSaved, onReload }) {
    let disposed = false, busy = false, checking = false, readEpoch = 0, readController = null;
    let dirty = Boolean(initialDraft), gate = { allowed: false, code: 'checking', message: '正在核对智慧课堂点名状态；可先保存本地。' };
    let push = intentState.push || null;
    const draft = initialDraft || { status: record.status === 'PENDING' ? 'PRESENT' : record.status, reason: '' };
    const status = field('select', '出勤状态', draft.status, { options: ATTENDANCE_STATES.map(([value, label]) => ({ value, label })) });
    const reason = field('textarea', '改签理由', draft.reason, { rows: 2, required: true, maxLength: 1000 });
    const actions = el('div'); actions.className = 'la-actions';
    const save = createDomainButton({ label: '保存', variant: 'glass', size: 'sm' });
    const savePush = createDomainButton({ label: '保存并推送', variant: 'prominent', size: 'sm' });
    const feedback = el('p'); feedback.className = 'la-notice'; feedback.setAttribute('role', 'status');
    const school = el('p'); school.className = 'la-notice'; school.setAttribute('role', 'status');
    const extra = el('div'); extra.className = 'la-actions';
    const check = createDomainButton({ label: '刷新学校点名状态', variant: 'ghost', size: 'sm' });
    const reload = createDomainButton({ label: '重新读取最新记录', variant: 'ghost', size: 'sm' });
    actions.append(save, savePush); extra.append(check, reload); host.append(status.wrapper, reason.wrapper, actions, feedback, school, extra);
    const currentPush = () => push && (push.readback_only || push.is_current_decision === true || (push.is_current_decision === undefined && Number(push.local_revision) === Number(record.revision))) ? push : null;
    const retryable = () => (!dirty || currentPush()?.readback_only) && currentPush()?.can_retry === true;
    const inputSignature = mode => JSON.stringify({ status: status.control.value, reason: reason.control.value.trim(), mode });
    const recovering = mode => intentState.pending?.signature === inputSignature(mode);
    const needsGateCheck = () => gate.code === 'checking' && !ACTIVE.has(currentPush()?.state) && !currentPush()?.readback_only;
    function disable(node, value) { node.disabled = value; node.setAttribute('aria-disabled', String(value)); node.dataset.lqDisabled = String(value); }
    function renderGate() {
        if (disposed) return;
        const locked = busy || parentBusy() || !editable(), active = ACTIVE.has(currentPush()?.state);
        const readback = retryable() && currentPush()?.readback_only;
        disable(save, locked); disable(savePush, locked || (!recovering('push') && !readback && (checking || !gate.allowed)) || (active && !retryable() && !recovering('push')));
        disable(reload, locked); disable(check, busy || checking || parentBusy());
        status.control.disabled = reason.control.disabled = locked;
        savePush.textContent = recovering('push') ? '确认保存并推送结果' : readback ? '核对推送结果' : retryable() ? '重试推送' : active ? '正在推送' : '保存并推送';
        save.setAttribute('aria-busy', String(busy)); savePush.setAttribute('aria-busy', String(busy));
        school.textContent = checking ? '正在核对学校点名状态；本地保存不受影响。' : gate.message || (gate.allowed ? '仅推送当前学生；学校已开始过点名，结束后仍可更新。' : '暂不能推送；本地保存不受影响。');
        host.setAttribute('aria-busy', String(busy));
    }
    function renderPush() {
        if (disposed || !push) return;
        const current = currentPush();
        feedback.setAttribute('role', ['failed', 'blocked', 'conflict'].includes(current?.state) ? 'alert' : 'status');
        if (!current) feedback.textContent = '本地记录已更新，此前推送不代表当前状态。';
        else if (current.readback_only) feedback.textContent = `${dirty ? '当前修改尚未保存。' : '本地记录已保留。'}上次推送目标为${stateLabel(current.target_status)}，结果待确认；仅回读学校结果，不会提交当前修改。${current.message || ''}`;
        else if (current.state === 'confirmed') feedback.textContent = `本地已保存，智慧课堂已确认该学生为${stateLabel(current.target_status)}。`;
        else if (ACTIVE.has(current.state)) feedback.textContent = `本地已保存，${current.state === 'outcome_unknown' ? '正在核对学校是否已收到；请勿重复新建请求。' : '正在后台推送当前学生。'}${current.message || ''}`;
        else if (current.state === 'superseded') feedback.textContent = '本地记录已有更新，旧推送已停止。';
        else feedback.textContent = `本地已保存，智慧课堂推送未完成。${current.message || '可核对学校状态后重试推送。'}`;
    }
    function applyStatus(value) {
        if (value.gate) gate = value.gate;
        if (Object.hasOwn(value, 'push')) { push = value.push; intentState.push = push; }
        renderPush(); renderGate();
    }
    async function readStatus(verify = false, signal) {
        if (disposed || busy || checking) return;
        const generation = ++readEpoch, controller = new AbortController(); readController?.abort(); readController = controller;
        const abort = () => controller.abort(); signal?.addEventListener('abort', abort, { once: true });
        checking = verify; renderGate();
        try {
            const value = await request(`${endpoint}/push${verify ? '/check' : ''}`, { ...(verify ? { method: 'POST', body: {} } : {}), signal: controller.signal }, verify ? 95000 : 20000);
            if (disposed || generation !== readEpoch) return;
            applyStatus(value);
        } catch (error) {
            if (disposed || generation !== readEpoch) return;
            gate = { allowed: false, code: 'unavailable', message: `学校状态暂未确认：${error.message || '请重试'}。可继续保存本地。` };
            if (signal) throw error; // Let the shared ambient owner back off failed reads.
        } finally {
            signal?.removeEventListener('abort', abort);
            if (!disposed && generation === readEpoch) { checking = false; readController = null; renderGate(); if (!verify && needsGateCheck()) void readStatus(true); }
        }
    }
    function retain() {
        reason.control.setCustomValidity(''); dirty = true; onDirty(true); onDraft({ status: status.control.value, reason: reason.control.value }); if (push?.readback_only) renderPush(); renderGate();
    }
    status.control.addEventListener('change', retain); reason.control.addEventListener('input', retain);
    async function run(action, mode) {
        if (disposed || busy || parentBusy() || !editable()) return;
        busy = true; readEpoch++; readController?.abort(); checking = false; onBusy(true); renderGate();
        feedback.setAttribute('role', 'status'); feedback.textContent = mode === 'retry' ? '本地已保存，正在恢复原推送请求…' : mode === 'push' ? '正在保存本地并提交该学生的推送…' : '正在保存本地…';
        try {
            const response = await action(); if (disposed) return;
            if (mode !== 'retry') {
                if (!response.local_saved || !response.record) throw new Error('保存结果尚未确认，请使用原请求重试。');
                record = response.record; intentState.pending = null; intentState.localSaved = true;
                dirty = false; onDirty(false); onDraft(null); reason.control.value = ''; status.control.value = record.status;
                onSaved(response);
                feedback.setAttribute('role', 'status'); feedback.textContent = '本地已保存。';
            }
            applyStatus(response);
        } catch (error) {
            if (disposed) return;
            const rejected = [400, 403, 404, 409, 422].includes(error.status);
            if (rejected) intentState.pending = null;
            feedback.setAttribute('role', 'alert');
            feedback.textContent = mode === 'retry' ? `本地记录已保留，推送尚未完成。${error.message || '可使用原请求重试。'}` : rejected
                ? `本次修改未保存，选择与理由已保留。${error.message || '请检查填写内容与权限后重试。'}${error.status === 409 ? '可重新读取最新记录后再保存。' : ''}`
                : `保存结果未确认，选择与理由已保留。${error.message || '请使用原请求重试，或重新读取最新记录核对。'}`;
        } finally {
            busy = false; onBusy(false); if (!disposed) { renderGate(); if (needsGateCheck()) void readStatus(true); }
        }
    }
    function submit(mode) {
        if (disposed || busy || (mode === 'push' && !recovering(mode) && !(retryable() && currentPush()?.readback_only) && (!gate.allowed || checking))) return;
        if (mode === 'push' && retryable()) { void run(() => request(`${endpoint}/push/${encodeURIComponent(push.id)}/retry`, { method: 'POST', body: {} }, 95000), 'retry'); return; }
        reason.control.setCustomValidity(reason.control.value.trim() ? '' : '请填写改签理由。');
        if (!reason.control.reportValidity()) return;
        const data = { status: status.control.value, revision: record.revision, reason: reason.control.value.trim(), mode,
            ...(mode === 'push' ? { expected_binding_revision: gate.binding_revision, expected_remote_checkin_id: gate.remote_checkin_id } : {}) };
        const signature = inputSignature(mode);
        if (intentState.pending?.signature !== signature) intentState.pending = { signature, payload: { ...data, idempotency_key: crypto.randomUUID() } };
        void run(() => request(`${endpoint}/corrections`, { method: 'POST', body: intentState.pending.payload }, 95000), mode);
    }
    save.addEventListener('click', () => submit('local')); savePush.addEventListener('click', () => submit('push'));
    check.addEventListener('click', () => readStatus(true)); reload.addEventListener('click', () => { if (!busy) onReload(); });
    const poll = createAmbientPoll(signal => readStatus(false, signal), { intervalMs: 3000, timeoutMs: 22000,
        active: () => !disposed && !busy && !checking && !document.hidden && ACTIVE.has(currentPush()?.state) && getLayerSystem(document).top() === getLayer() });
    renderPush(); renderGate(); poll.start(3000);
    void readStatus().then(() => {
        if (!disposed && !gate.allowed && !currentPush()?.readback_only && !['alignment_required', 'historical_round', 'student_push_busy', 'not_current', 'void', 'permission_denied', 'busy'].includes(gate.code)) return readStatus(true);
    });
    return { update: renderGate, destroy() { if (disposed) return; disposed = true; readEpoch++; readController?.abort(); poll.dispose(); } };
}
