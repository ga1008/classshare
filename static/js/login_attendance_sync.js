/** Teacher-owned, explicitly enabled durable school sync and exact readback. */
import { apiFetch } from './api.js';
import { openDialog, confirm as confirmDialog } from './lq/dialogs.js';
import { createDomainButton } from './lq/domain-controls.js';
import { createForm } from './lq/forms.js';
import { createComponent } from './lq/components.js';
import { enhanceDropdowns } from './lq/dropdown.js';
import { createAmbientPoll } from './ambient_poll.js';
import { getLayerSystem } from './lq/layer.js';

const el = (tag, text) => { const n = document.createElement(tag); if (text !== undefined) n.textContent = String(text); return n; };
export function openAttendanceSync({ base, round, trigger, parentLayer, readOnly = false, onClose }) {
    const canBind = ['ended', 'closed', 'finalized', 'published', 'settling', 'settled', 'completed'].includes(round.state);
    let closed = false, busy = false, sources = [], checkins = [], binding = null, preview = null, run = null, generation = 0;
    let plan = null, slots = [], location = null, autoEpoch = 0, autoDirty = false, saveKey = crypto.randomUUID(), poll;
    const runKeys = new Map(), controllers = new Set(), body = el('div'); body.className = 'la-workspace';
    let capability = {};
    const message = el('p', '正在读取同步能力…'); message.setAttribute('role', 'status');
    const capabilities = el('div'); capabilities.className = 'la-actions';
    const steps = el('div'); steps.className = 'la-form';
    const output = el('section'); output.setAttribute('aria-label', '学校端学生状态核对结果');
    const autoPanel = el('section'); autoPanel.className = 'la-form'; autoPanel.hidden = true; autoPanel.setAttribute('aria-label', '本轮结束后自动推送');
    const autoStatus = el('div'); autoStatus.className = 'la-form'; autoStatus.setAttribute('aria-live', 'polite');
    const autoForm = el('div'); autoForm.className = 'la-form';
    const autoOutput = el('section'); autoOutput.setAttribute('aria-label', '自动推送逐人结果');
    const autoControls = el('div'); autoControls.className = 'la-actions';
    const autoFields = {};
    const uid = `lasync${Date.now()}`;
    const fields = {};
    function field(name, kind, label, value, extra = {}) { const n = createForm(kind, { id: `${uid}-${name}`, label, value, ...extra }); steps.append(n); fields[name] = n.querySelector('input,select,textarea'); return fields[name]; }
    field('year', 'input', '学年', round.context?.year || '', { help: '须与本地课次所属学期一致，例如 2026-2027。' });
    field('term', 'select', '学期', String(round.context?.term || ''), { options: [{ value: '', label: '选择明确学期' }, { value: '1', label: '第一学期' }, { value: '2', label: '第二学期' }] });
    field('source', 'select', '学校授课班', '', { options: [{ value: '', label: '先读取授课班' }] });
    field('checkin', 'select', '学校已存在的点名', '', { options: [{ value: '', label: '先读取点名' }] });
    function autoField(name, kind, label, extra = {}) { const n = createForm(kind, { id: `${uid}-${name}`, label, ...extra }); autoForm.append(n); autoFields[name] = n.querySelector('input,select'); return autoFields[name]; }
    autoField('slot', 'select', '学校准确课次与轮次', { value: '', options: [{ value: '', label: '先读取本课学校课次' }] });
    const slotSummary = el('p', '请选择准确课次，系统不会根据相似课程名称自动改绑。'); autoForm.append(slotSummary);
    autoField('allowCreate', 'checkbox', '所选课次没有点名时，允许使用本次定位创建并结束学校点名', { checked: false });
    autoField('takeover', 'checkbox', '我知悉学校单会话限制，授权本轮后台使用已保存账号', { checked: false });
    const locationStatus = el('p', '绑定已有点名无需定位。只有明确创建学校点名时才会请求本次浏览器位置。'); autoForm.append(locationStatus);
    const controls = el('div'); controls.className = 'la-actions';
    function btn(label, fn, variant = 'glass', parent = controls) { const n = createDomainButton({ label, variant, size: 'sm' }); n.addEventListener('click', () => action(fn)); parent.append(n); return n; }
    async function call(path, options = {}) { const controller = new AbortController(); controllers.add(controller); const deadline = window.setTimeout(() => controller.abort(), 20000); try { return await apiFetch(base + path, { silent: true, ...options, signal: controller.signal }); } catch(error) { if(error.name === 'AbortError') throw new Error('学校端请求未完成，请刷新核对已有任务后重试。'); throw error; } finally { window.clearTimeout(deadline); controllers.delete(controller); } }
    async function action(fn) { if (busy || closed) return; busy = true; autoEpoch++; body.setAttribute('aria-busy', 'true'); body.querySelectorAll('button,input,select').forEach(n => n.disabled = true); message.setAttribute('role', 'status'); try { await fn(); } catch (error) { if (!closed) { message.textContent = error.status === 409 ? `配置或任务已变化，所选课次与授权草稿已保留。${error.message || '请刷新核对版本后重试。'}` : error.message || '学校端读取失败，请重试。'; message.setAttribute('role', 'alert'); } } finally { busy = false; if (!closed) { body.setAttribute('aria-busy', 'false'); body.querySelectorAll('button,input,select').forEach(n => n.disabled = false); gate(); } } }
    function choices(select, items, label) { select.replaceChildren(new Option(label, ''), ...items.map((text, index) => new Option(text, String(index)))); select.value = ''; }
    const readSources = btn('读取学校授课班', async () => {
        if (!/^20\d{2}-20\d{2}$/.test(fields.year.value) || !fields.term.value) throw new Error('请先核对课次所属学年和学期。');
        const value = await call('/source-options', { method: 'POST', body: { year: fields.year.value, term: Number(fields.term.value) } }); if (closed) return;
        sources = value.items || []; checkins = []; binding = preview = run = null; clearSlots();
        choices(fields.source, sources.map(x => `${x.course_name} · ${x.course_code} · ${x.teaching_class_name}`), '选择准确授课班');
        choices(fields.checkin, [], '先读取点名'); message.textContent = `读取到 ${sources.length} 个授课班，请核对课程和教学班。`;
    });
    const readCheckins = btn('读取该班已有点名', async () => {
        const source = sources[Number(fields.source.value)]; if (fields.source.value === '' || !source) throw new Error('请选择学校授课班。');
        const value = await call('/checkins', { method: 'POST', body: { source_token: source.source_token } }); if (closed) return;
        checkins = value.items || []; binding = preview = run = null;
        choices(fields.checkin, checkins.map(x => `${x.createTime || x.checkinTime || x.date || '时间待核对'} · ${x.section || x.sectionText || ''} · 点名 ${x.id}`), '核对时间后选择点名');
        message.textContent = value.message || '请核对日期、节次和点名编号。';
    });
    const bind = btn('绑定并生成差异预览', async () => {
        const source = sources[Number(fields.source.value)], checkin = checkins[Number(fields.checkin.value)];
        if (fields.source.value === '' || fields.checkin.value === '' || !source || !checkin) throw new Error('请选择准确授课班与已存在的点名。');
        binding = await call('/bindings', { method: 'POST', body: { source_token: source.source_token, remote_checkin_id: String(checkin.id) } });
        if (closed) return; preview = await call('/preview', { method: 'POST', body: { binding_id: binding.id } }); run = preview; paintRun();
    }, 'prominent');
    const enqueue = btn('后台逐人回读核对', async () => { if (!preview) throw new Error('请先生成差异预览。'); if (!runKeys.has(preview.id)) runKeys.set(preview.id, crypto.randomUUID()); run = await call('/runs', { method: 'POST', body: { preview_id: preview.id, idempotency_key: runKeys.get(preview.id) } }); if (closed) return; message.textContent = '已创建持久核对任务；离开页面后仍会继续。'; await refresh(); });
    const refreshButton = btn('刷新核对结果', refresh);
    const pause = btn('暂停后续核对', async () => { if (!run) return; await call(`/runs/${run.id}/pause`, { method: 'POST', body: { expected_revision: run.revision } }); await refresh(); });
    const reauthorize = btn('原学校账号重新验证后续接', async () => { if (!binding) throw new Error('请先绑定点名；若已换学校账号，请重新选择授课班与点名。'); binding = await call(`/bindings/${binding.id}/reauthorize`, { method: 'POST', body: { expected_revision: binding.revision } }); if (closed) return; message.textContent = '原账号已重新授权，请重新生成预览。'; preview = await call('/preview', { method: 'POST', body: { binding_id: binding.id } }); run = preview; paintRun(); });
    const readSlots = btn('读取本课学校课次', async () => {
        const source = sources[Number(fields.source.value)]; if (fields.source.value === '' || !source) throw new Error('请先选择学校授课班。');
        const value = await call('/slot-options', { method: 'POST', body: { source_token: source.source_token } }); if (closed) return;
        slots = value.options || []; choices(autoFields.slot, slots.map(x => x.label), '核对日期、周次、节次和教学班后选择');
        resetLocation(); paintSlot(); autoDirty = true;
        message.textContent = slots.length ? '请选择与本地点名完全对应的学校课次与轮次。' : '未找到可对齐的学校课次，未创建或发送任何学校点名。';
    }, 'glass', autoControls);
    const locate = btn('获取本次教室定位', async () => {
        location = null;
        if (!navigator.geolocation) throw new Error('当前浏览器不支持定位；仍可选择已有学校点名。');
        const value = await new Promise((resolve, reject) => navigator.geolocation.getCurrentPosition(resolve, () => reject(new Error('未取得定位。可在浏览器允许定位后重试，或选择已有学校点名。')), { enableHighAccuracy: true, maximumAge: 0, timeout: 12000 }));
        if (closed) return;
        location = { latitude: value.coords.latitude, longitude: value.coords.longitude, captured_at_ms: value.timestamp };
        locationStatus.textContent = '已取得本次位置，仅用于创建所选学校点名；超过 5 分钟须重新获取。'; autoDirty = true; saveKey = crypto.randomUUID();
    }, 'glass', autoControls);
    const enable = btn('启用本轮结束后自动推送', async () => {
        const source = sources[Number(fields.source.value)], slot = slots[Number(autoFields.slot.value)];
        if (fields.source.value === '' || !source || autoFields.slot.value === '' || !slot) throw new Error('请先核对并选择准确学校课次。');
        if (!autoFields.takeover.checked) throw new Error('请先确认学校账号单会话限制并授权后台使用。');
        const create = !slot.remote_checkin_id;
        if (create && (!slot.can_create || !autoFields.allowCreate.checked)) throw new Error('所选课次尚无点名，请明确允许创建，或选择已有点名。');
        if (create && (!location || Date.now() - location.captured_at_ms > 300000)) throw new Error('创建点名需要最近 5 分钟内取得的位置，请重新获取定位。');
        const value = await call('/auto-sync', { method: 'PUT', body: { source_token: source.source_token, slot_token: slot.slot_token, enabled: true, allow_create: create, ...(create ? { location } : {}), ...(plan ? { expected_revision: plan.revision } : {}), idempotency_key: saveKey, acknowledge_session_takeover: true } });
        if (closed) return; acceptAuto(value); autoDirty = false; saveKey = crypto.randomUUID();
        message.textContent = '本轮自动推送设置已保存。后台会等待本地点名结束并完成补报结算；关闭页面后仍继续。';
    }, 'prominent', autoControls);
    const autoRefresh = btn('刷新自动推送进度', refreshAuto, 'ghost', autoControls);
    const autoPause = btn('暂停自动推送', () => autoCommand('pause'), 'glass', autoControls);
    const autoRetry = btn('核对后继续推送', () => autoCommand('retry'), 'glass', autoControls);
    const autoReauth = btn('重新授权后台连接', () => autoCommand('reauthorize'), 'glass', autoControls);
    async function autoCommand(command) {
        if (!plan) return;
        if (command !== 'pause' && !autoFields.takeover.checked) throw new Error('请先确认学校单会话限制；后台登录可能使微信小程序需要重新登录。');
        const value = await call(`/auto-sync/${command}`, { method: 'POST', body: { expected_revision: plan.revision } });
        if (!closed) { acceptAuto(value); message.textContent = command === 'pause' ? '已暂停后续发送；正在执行的单人请求仍会回读确认。' : '已提交后台继续核对，以逐人回读结果为准。'; }
    }
    function acceptAuto(value) { plan = value.plan || null; paintAuto(); }
    async function refreshAuto() { const id = ++autoEpoch; const value = await call('/auto-sync'); if (!closed && id === autoEpoch) acceptAuto(value); }
    function resetLocation() { location = null; autoFields.allowCreate.checked = false; locationStatus.textContent = '绑定已有点名无需定位。只有明确创建学校点名时才会请求本次浏览器位置。'; }
    function paintSlot() { const slot = autoFields.slot.value === '' ? null : slots[Number(autoFields.slot.value)]; slotSummary.textContent = !slot ? '请选择准确课次，系统不会根据相似课程名称自动改绑。' : slot.remote_checkin_id ? `已选学校点名 ${slot.remote_checkin_id} · ${slot.is_stopped ? '学校已结束点名' : '学校尚未结束点名'}；绑定此轮，无需定位。` : slot.can_create ? '所选课次尚无点名。明确授权并取得定位后，后台会创建学校点名；学生状态在本地结算发布后推送。' : '所选课次尚无可用点名，当前不能在服务器新建；可先在官方端点名后重新读取。'; }
    function clearSlots() { slots = []; resetLocation(); choices(autoFields.slot, [], '先读取本课学校课次'); paintSlot(); autoFields.takeover.checked = false; autoDirty = false; saveKey = crypto.randomUUID(); }
    function paintAuto() {
        const labels = { waiting_local_end: '等待本地点名结束', waiting_settlement: '等待补报结算', pending: '已排队', running: '正在处理', confirmed: '逐人回读已确认', partial: '部分完成，仍需核对', conflict: '存在冲突，需教师核对', outcome_unknown: '发送结果待确认', needs_teacher_reauth: '需要重新授权', paused: '已暂停', void: '已作废，不再发送', failed: '未完成，需核对' };
        autoStatus.replaceChildren(createComponent('chip', { label: plan ? labels[plan.state] || plan.state : '本轮未启用学校自动推送', tone: plan?.state === 'confirmed' ? 'success' : plan?.state === 'needs_teacher_reauth' || plan?.state === 'conflict' ? 'warning' : 'neutral' }));
        if (plan) {
            const target = plan.target || {};
            autoStatus.append(el('p', `${target.course_name || ''} ${target.course_code || ''} · ${target.teaching_class_name || ''} · ${target.academic_year || ''} 第 ${target.academic_term || '—'} 学期 · ${target.session_date || ''} 第 ${target.week || '—'} 周 星期 ${target.day_of_week || '—'} ${target.section_text || (target.section ? `${target.section} 节` : '')}`), el('p', plan.message || '以本页任务状态和逐人回读结果为准。'));
            if (plan.remote_checkin_id) autoStatus.append(el('p', `学校点名编号：${plan.remote_checkin_id} · 配置版本：${plan.revision}`));
        }
        autoOutput.replaceChildren(); if (plan?.last_run) { autoOutput.append(el('p', '以下仅核对本地名单中唯一匹配的学生，不代表学校整轮所有学生已确认。学校其他学生与特殊请假状态保留原值。')); renderRun(autoOutput, plan.last_run, true); }
        gate();
    }
    async function refresh() { if (!run?.id) return; const id = ++generation; const value = await call(readOnly ? `/runs/${run.id}` : `/runs/${run.id}/refresh`, readOnly ? {} : { method: 'POST' }); if (!closed && id === generation) { run = value; paintRun(); } }
    function gate() {
        const locked = busy || readOnly;
        readSources.disabled = locked || !capability.read_sources; readCheckins.disabled = locked || !capability.read_checkins || fields.source.value === ''; bind.disabled = locked || !canBind || !capability.read_checkins || fields.checkin.value === ''; enqueue.disabled = !preview || locked || !capability.readback; refreshButton.disabled = !run || busy || !capability.readback; pause.disabled = !run || locked || !['pending', 'running', 'needs_teacher_action'].includes(run.state); reauthorize.disabled = !binding || locked;
        const slot = autoFields.slot.value === '' ? null : slots[Number(autoFields.slot.value)];
        readSlots.disabled = locked || !capability.auto_sync || fields.source.value === ''; locate.disabled = locked || !slot?.can_create || Boolean(slot?.remote_checkin_id); enable.disabled = locked || !capability.auto_sync || !slot || (!slot.remote_checkin_id && !slot.can_create) || !autoFields.takeover.checked;
        autoFields.allowCreate.disabled = locked || !slot?.can_create || Boolean(slot?.remote_checkin_id);
        autoRefresh.disabled = busy; autoPause.disabled = locked || !plan?.enabled || ['paused', 'void'].includes(plan?.state); autoRetry.disabled = locked || !plan || !autoFields.takeover.checked || !['paused','partial','conflict','outcome_unknown','failed'].includes(plan.state); autoReauth.disabled = locked || !plan || !autoFields.takeover.checked || plan.state === 'void';
        if (readOnly) [...Object.values(fields), ...Object.values(autoFields)].forEach(n => n.disabled = true);
    }
    function paintRun() {
        if (closed) return;
        output.replaceChildren(el('h3', '学校端学生状态核对'), el('p', run.message || run.state || '预览已生成'), el('p', '这里只核对所选点名的逐人状态；不代表学校端已开始、结束点名，也不代表定位或完整远端流程已经完成。'));
        renderRun(output, run, false); gate();
    }
    function renderRun(container, current, automatic) {
        const table = el('table'); table.className = 'lq-table'; table.setAttribute('data-lq-component', 'table');
        const caption = el('caption', automatic ? '自动推送逐人结果（以学校回读确认为准）' : '逐人差异（此处只回读核对）'), head = el('thead'), tr = el('tr');
        ['学号', '本地目标', '学校状态', '核对结果'].forEach(t => { const th = el('th', t); th.scope = 'col'; tr.append(th); }); head.append(tr);
        const rows = el('tbody'); const labels = { CHECKED: '出勤', UNCHECKED: '缺勤', SICK_LEAVE: '病假', PERSONAL_LEAVE: '事假', LATE_OR_EARLY: '迟到或早退', PRESENT: '出勤', ABSENT: '缺课', LATE: '迟到', EARLY: '早退', PENDING: '待确认', STUDY_TOUR: '研学', PRACTICE: '实践', VOLUNTEER: '志愿' };
        const states = { confirmed: '回读确认', conflict: '状态冲突', outcome_unknown: '结果待确认', needs_teacher_action: '需教师处理', needs_teacher_reauth: '需重新授权', pending: '待发送', running: '处理中', failed: '失败', blocked: '未发送，需核对', skipped: '已跳过', protected: '保留学校请假或特殊状态', unmapped: '学生无法唯一对应', superseded: '已由新版本接替' };
        for (const item of current.operations || []) { const row = el('tr'); [item.student_number, labels[item.target_status] || item.target_status || '待确认', labels[item.remote_status] || item.remote_status || '未回读'].forEach(t => row.append(el('td', t))); const result = el('td'); result.append(el('span', states[item.state] || item.state || '待确认')); if (item.reason) result.append(el('p', item.reason)); row.append(result); rows.append(row); }
        table.append(caption, head, rows); container.append(table);
    }
    function clearPreview() { binding = preview = run = null; output.replaceChildren(); gate(); }
    for (const name of ['year', 'term']) fields[name].addEventListener('change', () => { sources = checkins = []; choices(fields.source, [], '先读取授课班'); choices(fields.checkin, [], '先读取点名'); clearSlots(); clearPreview(); });
    fields.source.addEventListener('change', () => { checkins = []; choices(fields.checkin, [], '先读取点名'); clearSlots(); clearPreview(); });
    fields.checkin.addEventListener('change', clearPreview);
    autoForm.addEventListener('change', event => { autoDirty = true; saveKey = crypto.randomUUID(); if (event.target === autoFields.slot) { resetLocation(); paintSlot(); } gate(); });
    autoPanel.append(el('h3', '本轮结束后自动推送'), el('p', '默认不启用。确认准确学校课次后单独授权；本地结束、补报结算完成后由后台继续，人工修订和申诉处理后的新版本也会重新核对。待确认学生、冲突和采集异常不会直接改成缺课。'), autoStatus, autoForm, autoControls, autoOutput);
    const sourceControls = el('div'); sourceControls.className = 'la-actions'; sourceControls.append(readSources); fields.source.closest('.lq-field').before(sourceControls);
    const manualSteps = el('div'); manualSteps.className = 'la-form'; manualSteps.append(fields.checkin.closest('.lq-field'));
    const checkinControls = el('div'); checkinControls.className = 'la-actions'; checkinControls.append(readCheckins); manualSteps.prepend(checkinControls);
    body.append(message, capabilities, el('p', '学校账号只支持一个登录会话。读取或后台恢复登录可能使微信小程序需要重新登录；小程序再次登录也可能中断后台连接。账号凭据与会话仅保存在服务器。'), steps, autoPanel, el('h3', '已有点名回读核对'), el('p', '仍可在学校官方微信小程序手动点名或改签，再在下方绑定准确点名并回读核对。'), manualSteps, controls, output);
    if (!canBind && !readOnly) steps.before(el('p', '本地点名尚未结束。可先配置自动推送；手动绑定和差异预览需要本地点名结束后再进行。'));
    if (readOnly) { steps.hidden = autoForm.hidden = manualSteps.hidden = true; autoControls.replaceChildren(autoRefresh); controls.replaceChildren(refreshButton); body.prepend(el('p', '历史记录只读：可查看已保存的绑定与核验结果，不会重新发送学校点名。')); }
    const handle = openDialog({ title: '智慧课堂核对', size: 'xl', body }, { trigger, parentLayer, beforeClose: async () => !busy && (!autoDirty || await confirmDialog({ title: '关闭并放弃同步配置草稿？', message: '已保存的后台任务会继续执行。', confirmLabel: '关闭', danger: true }, { parentLayer: handle })), onClose: cleanup, onDestroy: cleanup });
    const dropdowns = enhanceDropdowns(body);
    handle.root.classList.add('la-dialog');
    function cleanup() { if (closed) return; closed = true; generation++; autoEpoch++; poll?.dispose(); controllers.forEach(c => c.abort()); dropdowns.forEach(d => d.destroy()); onClose?.(); }
    action(async () => {
        const cap = await call('/capabilities'); if (closed) return; capability = cap;
        message.textContent = cap.message || '当前支持学校端交接与逐人回读。';
        capabilities.replaceChildren(...[['读取名单', cap.read_sources], ['读取点名', cap.read_checkins], ['状态回读', cap.readback], ['学校改签', cap.update_student]].map(([label, supported]) => createComponent('chip', { label: `${label}：${supported ? '可用' : '未开放'}`, tone: supported ? 'success' : 'neutral' })));
        // Read saved history even while the server has temporarily disabled writes.
        autoPanel.hidden = !Object.hasOwn(cap, 'auto_sync');
        if (Object.hasOwn(cap, 'auto_sync')) await refreshAuto();
        const [list, bindings] = await Promise.all([call('/runs'), call('/bindings')]);
        const readback = (list.items || []).find(item => item.mode !== 'auto_sync');
        if (!closed && readback) {
            run = await call(`/runs/${readback.id}`); if (closed) return;
            if (run.mode === 'auto_sync') { run = null; return; }
            binding = (bindings.items || []).find(item => item.id === run.binding_id && Number.isInteger(item.revision)) || null;
            if (run.state === 'preview' && binding) preview = run;
            paintRun();
        }
    });
    poll = createAmbientPoll(() => refreshAuto(), { intervalMs: 15000, timeoutMs: 22000, active: () => !closed && !busy && !document.hidden && Object.hasOwn(capability, 'auto_sync') && getLayerSystem(document).top() === handle }); poll.start(15000);
    return () => handle.destroy();
}
