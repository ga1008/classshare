/** Teacher-owned official-end handoff and exact student-status readback. */
import { apiFetch } from './api.js';
import { openDialog } from './lq/dialogs.js';
import { createDomainButton } from './lq/domain-controls.js';
import { createForm } from './lq/forms.js';
import { createComponent } from './lq/components.js';
import { enhanceDropdowns } from './lq/dropdown.js';

const el = (tag, text) => { const n = document.createElement(tag); if (text !== undefined) n.textContent = String(text); return n; };
export function openAttendanceSync({ base, round, trigger, parentLayer }) {
    const canBind = ['ended', 'closed', 'finalized', 'published', 'settling', 'settled', 'completed'].includes(round.state);
    let closed = false, busy = false, sources = [], checkins = [], binding = null, preview = null, run = null, generation = 0;
    const runKeys = new Map(), controllers = new Set(), body = el('div'); body.className = 'la-workspace';
    let capability = {};
    const message = el('p', '正在读取同步能力…'); message.setAttribute('role', 'status');
    const capabilities = el('div'); capabilities.className = 'la-actions';
    const steps = el('div'); steps.className = 'la-form';
    const output = el('section'); output.setAttribute('aria-label', '学校端学生状态核对结果');
    const uid = `lasync${Date.now()}`;
    const fields = {};
    function field(name, kind, label, value, extra = {}) { const n = createForm(kind, { id: `${uid}-${name}`, label, value, ...extra }); steps.append(n); fields[name] = n.querySelector('input,select,textarea'); return fields[name]; }
    field('year', 'input', '学年', round.context?.year || '', { help: '须与本地课次所属学期一致，例如 2026-2027。' });
    field('term', 'select', '学期', String(round.context?.term || ''), { options: [{ value: '', label: '选择明确学期' }, { value: '1', label: '第一学期' }, { value: '2', label: '第二学期' }] });
    field('source', 'select', '学校授课班', '', { options: [{ value: '', label: '先读取授课班' }] });
    field('checkin', 'select', '学校已存在的点名', '', { options: [{ value: '', label: '先读取点名' }] });
    const controls = el('div'); controls.className = 'la-actions';
    function btn(label, fn, variant = 'glass') { const n = createDomainButton({ label, variant, size: 'sm' }); n.addEventListener('click', () => action(fn)); controls.append(n); return n; }
    async function call(path, options = {}) { const controller = new AbortController(); controllers.add(controller); const deadline = window.setTimeout(() => controller.abort(), 20000); try { return await apiFetch(base + path, { silent: true, ...options, signal: controller.signal }); } catch(error) { if(error.name === 'AbortError') throw new Error('学校端请求未完成，请刷新核对已有任务后重试。'); throw error; } finally { window.clearTimeout(deadline); controllers.delete(controller); } }
    async function action(fn) { if (busy || closed) return; busy = true; body.querySelectorAll('button,input,select').forEach(n => n.disabled = true); try { await fn(); } catch (error) { if (!closed) { message.textContent = error.message || '学校端读取失败，请重试。'; message.setAttribute('role', 'alert'); } } finally { busy = false; if (!closed) { body.querySelectorAll('button,input,select').forEach(n => n.disabled = false); gate(); } } }
    function choices(select, items, label) { select.replaceChildren(new Option(label, ''), ...items.map((text, index) => new Option(text, String(index)))); select.value = ''; }
    const readSources = btn('读取学校授课班', async () => {
        if (!/^20\d{2}-20\d{2}$/.test(fields.year.value) || !fields.term.value) throw new Error('请先核对课次所属学年和学期。');
        const value = await call('/source-options', { method: 'POST', body: { year: fields.year.value, term: Number(fields.term.value) } }); if (closed) return;
        sources = value.items || []; checkins = []; binding = preview = run = null;
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
    async function refresh() { if (!run?.id) return; const id = ++generation; const value = await call(`/runs/${run.id}/refresh`, { method: 'POST' }); if (!closed && id === generation) { run = value; paintRun(); } }
    function gate() { readSources.disabled = busy || !capability.read_sources; readCheckins.disabled = busy || !capability.read_checkins || fields.source.value === ''; bind.disabled = busy || !canBind || !capability.read_checkins || fields.checkin.value === ''; enqueue.disabled = !preview || busy || !capability.readback; refreshButton.disabled = !run || busy || !capability.readback; pause.disabled = !run || busy || !['pending', 'running', 'needs_teacher_action'].includes(run.state); reauthorize.disabled = !binding || busy; }
    function paintRun() {
        if (closed) return;
        output.replaceChildren(el('h3', '学校端学生状态核对'), el('p', run.message || run.state || '预览已生成'), el('p', '这里只核对所选点名的逐人状态；不代表学校端已开始、结束点名，也不代表定位或完整远端流程已经完成。'));
        const table = el('table'); table.className = 'lq-table'; table.setAttribute('data-lq-component', 'table');
        const caption = el('caption', '逐人差异（尚未发送学校改签）'), head = el('thead'), tr = el('tr');
        ['学号', '本地目标', '学校状态', '核对结果'].forEach(t => { const th = el('th', t); th.scope = 'col'; tr.append(th); }); head.append(tr);
        const rows = el('tbody'); const labels = { CHECKED: '出勤', UNCHECKED: '缺勤', SICK_LEAVE: '病假', PERSONAL_LEAVE: '事假', LATE_OR_EARLY: '迟到或早退', PRESENT: '出勤', ABSENT: '缺课', LATE: '迟到', EARLY: '早退', PENDING: '待确认' };
        for (const item of run.operations || []) { const row = el('tr'); [item.student_number, labels[item.target_status] || item.target_status || '待确认', labels[item.remote_status] || item.remote_status || '未回读', item.reason || item.state].forEach(t => row.append(el('td', t))); rows.append(row); }
        table.append(caption, head, rows); output.append(table); gate();
    }
    function clearPreview() { binding = preview = run = null; output.replaceChildren(); gate(); }
    for (const name of ['year', 'term']) fields[name].addEventListener('change', () => { sources = checkins = []; choices(fields.source, [], '先读取授课班'); choices(fields.checkin, [], '先读取点名'); clearPreview(); });
    fields.source.addEventListener('change', () => { checkins = []; choices(fields.checkin, [], '先读取点名'); clearPreview(); });
    fields.checkin.addEventListener('change', clearPreview);
    body.append(message, capabilities, el('p', '请先在学校官方微信小程序完成开始、结束或改签，再在这里绑定准确点名并核对结果。'), steps, controls, output);
    if (!canBind) steps.before(el('p', '本地点名尚未结束。可先核对学校来源；绑定前请关闭此层、结束本地点名并刷新，再进入核对。'));
    const handle = openDialog({ title: '智慧课堂核对', size: 'xl', body }, { trigger, parentLayer, beforeClose: () => !busy, onClose: cleanup, onDestroy: cleanup });
    const dropdowns = enhanceDropdowns(body);
    handle.root.classList.add('la-dialog');
    function cleanup() { if (closed) return; closed = true; generation++; controllers.forEach(c => c.abort()); dropdowns.forEach(d => d.destroy()); }
    action(async () => {
        const cap = await call('/capabilities'); if (closed) return; capability = cap;
        message.textContent = cap.message || '当前支持学校端交接与逐人回读。';
        capabilities.replaceChildren(...[['读取名单', cap.read_sources], ['读取点名', cap.read_checkins], ['状态回读', cap.readback], ['学校改签', cap.update_student]].map(([label, supported]) => createComponent('chip', { label: `${label}：${supported ? '可用' : '未开放'}`, tone: supported ? 'success' : 'neutral' })));
        const [list, bindings] = await Promise.all([call('/runs'), call('/bindings')]);
        if (!closed && list.items?.length) {
            run = await call(`/runs/${list.items[0].id}`); if (closed) return;
            binding = (bindings.items || []).find(item => item.id === run.binding_id && Number.isInteger(item.revision)) || null;
            if (run.state === 'preview' && binding) preview = run;
            paintRun();
        }
    });
    return () => handle.destroy();
}
