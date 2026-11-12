/** Local attendance workspace. LQ owns layers/controls; this module owns intent and drafts. */
import { apiFetch } from './api.js';
import { openDialog, confirm as confirmDialog } from './lq/dialogs.js';
import { getLayerSystem } from './lq/layer.js';
import { createDomainButton } from './lq/domain-controls.js';
import { createComponent } from './lq/components.js';
import { createContent } from './lq/content.js';
import { createForm } from './lq/forms.js';
import { enhanceDropdowns } from './lq/dropdown.js';
import { createTable } from './lq/tables.js';
import { createAmbientPoll } from './ambient_poll.js';
import { ATTENDANCE_STATES } from './login_attendance_states.js';
import { createAttendanceWorkflow } from './login_attendance_sync.js';
export { resolveAttendanceLink } from './login_attendance_link.js';
export { ATTENDANCE_STATES } from './login_attendance_states.js';
const ROUND_LABELS = { scheduled: '待开始', running: '进行中', settling: '等待补报结算', published: '已结束', void: '已作废' };
const HEALTH_LABELS = { no_observation: '暂不可观察', observed_zero: '可靠记录为零', measured:'已采集有效学习', partial: '部分记录', error: '采集异常', gap: '记录不完整', collection_error: '采集异常', healthy: '采集正常', pending: '记录处理中' };
const active = new Map();
let sequence = 0;
const node = (tag, className = '', text) => { const n = document.createElement(tag); n.className = className; if (text !== undefined) n.textContent = String(text); return n; };
const time = value => value ? new Date(value).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false }) : '暂无';
const duration = value => value == null ? '无法完整观察' : `${Math.floor(Number(value) / 60)} 分 ${Math.floor(Number(value) % 60)} 秒`;
const statusLabel = value => ATTENDANCE_STATES.find(([key]) => key === value)?.[1] || (value === 'PENDING' ? '待确认' : '暂无');
const chip = (text, tone = 'neutral') => createComponent('chip', { label: String(text), tone, kind: 'status' });
const button = (label, action, variant = 'glass') => { const n = createDomainButton({ label, variant, size: 'sm' }); n.addEventListener('click', action); return n; };
const request = (url, options = {}) => apiFetch(url, { silent: true, ...options });
const safeId = value => /^[1-9]\d*$/.test(String(value || '')) && Number.isSafeInteger(Number(value)) ? Number(value) : null;
const localTimeInput = value => value ? new Date(Number(value) + 8 * 3600000).toISOString().slice(0, 16) : '';

export function openLoginAttendance({ classOfferingId, session, userRole, trigger, attendanceId, studentId, appealId, historyOnly = false }) {
    const offering = safeId(classOfferingId), lesson = safeId(session?.id);
    if (historyOnly && userRole !== 'teacher') return null;
    if (!offering || (!lesson && !(historyOnly && userRole === 'teacher' && safeId(attendanceId)))) return null;
    const key = `${userRole}:${offering}:${historyOnly ? `history-${attendanceId}` : lesson}`;
    if (active.has(key)) return active.get(key);
    const teacher = userRole === 'teacher';
    if (!teacher && userRole !== 'student') return null;
    const base = `/api/classrooms/${offering}/login-attendance`;
    const uid = `la${lesson}x${++sequence}`;
    let data = null, round = null, closed = false, busy = false, dirty = false, epoch = 0, handle, focusedStudent = studentId || null;
    let page = 0, formInitialized = false, createKey = crypto.randomUUID(), selected = new Map();
    let detailEpoch = 0, externalCleanup = null, externalOpen = false, appealData = null;
    let latestLessonRoundId = null;
    const appealDrafts = new Map();
    let detailDropdowns = [];
    let detailHandle = null, detailDirty = false, detailBusy = false, workflow;
    const recordDrafts = new Map();
    const isCurrent = () => round && (round.is_current === undefined || Boolean(round.is_current));
    const canEdit = () => round && (round.can_edit === undefined ? isCurrent() && round.state !== 'void' : Boolean(round.can_edit));
    const shell = node('div', 'la-workspace');
    const notice = node('div', 'la-notice'); notice.setAttribute('role', 'status');
    const heading = node('div', 'la-heading');
    const summary = node('div', 'la-summary');
    const stateCounts = node('div','la-actions'); stateCounts.setAttribute('aria-label','六种签到状态人数');
    const actions = node('div', 'la-actions');
    const secondary = node('details', 'lq-collapsible la-secondary');
    secondary.append(node('summary', 'lq-collapsible__summary', '记录与更多操作'));
    const secondaryActions = node('div', 'lq-collapsible__content la-actions'); secondary.append(secondaryActions);
    const guidance = node('p', 'la-guidance');
    guidance.setAttribute('aria-live', 'polite');
    const workflowHost = node('section', 'la-school-workflow');
    const content = node('div', 'la-content');
    const listPane = node('section', 'la-list-pane');
    const detail = node('section', 'la-detail'); detail.setAttribute('aria-label', teacher ? '学生签到详情' : '我的签到详情');
    const grid = node('div', 'la-students');
    const pager = node('div', 'la-actions');
    const config = node('details', 'lq-collapsible la-config');
    config.append(node('summary', 'lq-collapsible__summary', '课前核对 · 教室网络与上课时间'));
    const configBody = node('div', 'lq-collapsible__content la-form'); config.append(configBody);
    const fields = {};
    function field(kind, label, value = '', extra = {}) {
        const wrapper = createForm(kind, { id: `${uid}-f${++sequence}`, label, value, ...extra });
        return { wrapper, control: wrapper.querySelector('input,textarea,select') };
    }
    function appendField(parent, key, kind, label, value = '', extra = {}) {
        const f = field(kind, label, value, extra); parent.append(f.wrapper); fields[key] = f.control; return f.control;
    }
    const searchbar = node('div', 'la-filters');
    appendField(searchbar, 'search', 'input', '搜索姓名或学号', '', { type: 'search' });
    appendField(searchbar, 'filter', 'select', '筛选', '', { options: [
        { value: '', label: '全部学生' }, { value: 'PENDING', label: '待确认' }, { value: 'error', label: '采集待核对' },
        ...ATTENDANCE_STATES.map(([value, label]) => ({ value, label })),
    ] });
    const selections = node('div', 'la-actions');
    const count = node('span', 'la-selection-count', '未选择学生');
    selections.append(button('全选当前筛选', () => { filtered().forEach(r => selected.set(r.student_id, r.revision)); dirty = true; paintGrid(); }),
        button('全班', () => { (round?.records || []).forEach(r => selected.set(r.student_id, r.revision)); dirty = true; paintGrid(); }),
        button('清除选择', () => { selected.clear(); paintGrid(); }, 'ghost'), count);
    const bulk = createTable('bulk_bar', { selectedCount: 0, actions: [] });
    const bulkFields = node('div', 'la-bulk-fields');
    appendField(bulkFields, 'status', 'select', '批量改为', 'PRESENT', { options: ATTENDANCE_STATES.map(([value, label]) => ({ value, label })) });
    appendField(bulkFields, 'reason', 'textarea', '修改理由', '', { rows: 2, required: true, maxLength: 1000 });
    const apply = button('应用到已选学生', applySelected, 'prominent'); bulkFields.append(apply); bulk.append(bulkFields);
    const batch = node('details', 'lq-collapsible la-batch'); batch.append(node('summary', 'lq-collapsible__summary', '批量修改出勤状态'), selections);
    listPane.append(searchbar, batch, bulk, grid, pager);
    content.append(listPane); if (!teacher) content.append(detail);
    shell.append(heading, guidance, notice, workflowHost, config, actions, summary, stateCounts, content, secondary);
    const cancelRequests = new Set();
    const disabledBeforeBusy = new WeakMap();
    const setNotice = (text = '', error = false) => { notice.textContent = text; notice.setAttribute('role', error ? 'alert' : 'status'); };
    const setBusy = value => { busy = value; shell.setAttribute('aria-busy', String(value)); shell.querySelectorAll('button').forEach(n => { if (value) { if (!disabledBeforeBusy.has(n)) disabledBeforeBusy.set(n, n.disabled); n.disabled = true; } else if (disabledBeforeBusy.has(n)) { n.disabled = disabledBeforeBusy.get(n) || n.dataset.laDisabled === 'true'; disabledBeforeBusy.delete(n); } }); workflow?.update(); };
    function fail(error) { if (!closed) setNotice(error.status === 409 ? `内容已变化，选择与理由已保留。${error.message || '请核对最新结果后重试。'}` : error.message || '读取失败，请重试。', true); }
    async function call(path, options = {}) {
        const controller = new AbortController(); cancelRequests.add(controller);
        const deadline = window.setTimeout(() => controller.abort(), 20000);
        const abort = () => controller.abort(); options.signal?.addEventListener('abort', abort, { once: true });
        if (options.signal?.aborted) controller.abort();
        try { return await request(path.startsWith('/api/') ? path : base + path, { ...options, signal: controller.signal }); }
        catch (error) { if (error.name === 'AbortError') throw new Error('请求未完成，未提交内容已保留。请刷新核对结果后重试。'); throw error; }
        finally { window.clearTimeout(deadline); options.signal?.removeEventListener('abort', abort); cancelRequests.delete(controller); }
    }
    async function mutate(action) {
        if (busy || closed) return;
        setBusy(true); setNotice('正在处理…');
        try { await action(); document.dispatchEvent(new CustomEvent('classroom:attendance-changed')); }
        catch (error) { fail(error); }
        finally { if (!closed) { setBusy(false); if (teacher) paintSelection(); } }
    }
    function filtered() {
        const q = fields.search.value.trim().toLowerCase(), filter = fields.filter.value;
        return (round?.records || []).filter(r => (!q || `${r.name} ${r.student_number}`.toLowerCase().includes(q))
            && (!filter || (filter === 'error' ? !['healthy', 'observed_zero','measured'].includes(r.evidence?.collection?.status) : r.status === filter)));
    }
    function paintGrid() {
        if (!teacher || closed) return;
        const rows = filtered(); page = Math.min(page, Math.max(0, Math.ceil(rows.length / 40) - 1));
        grid.replaceChildren();
        for (const row of rows.slice(page * 40, page * 40 + 40)) {
            const card = node('article', 'la-student-card lq-surface'); card.dataset.lqComponent = 'surface'; card.dataset.lqPadding = 'none'; card.dataset.studentId = String(row.student_id);
            const show = createDomainButton({ className: 'la-student-open', kind: 'choice', variant: 'ghost', attrs: { 'aria-label': `${row.name}，${statusLabel(row.status)}，查看签到详情`, 'aria-haspopup': 'dialog' } });
            show.dataset.lqShape = 'surface';
            const identity = node('span', 'la-student-identity'); identity.append(node('strong', '', row.name), node('small', '', row.student_number || ''));
            const choose = field('checkbox', `选择 ${row.name}`, String(row.student_id), { checked: selected.has(row.student_id) });
            choose.control.addEventListener('change', () => { if (choose.control.checked) selected.set(row.student_id, row.revision); else selected.delete(row.student_id); paintSelection(); card.dataset.lqSelected = String(choose.control.checked); });
            const state = chip(row.status_label || statusLabel(row.status), row.status === 'PRESENT' ? 'success' : row.status === 'PENDING' ? 'warning' : 'neutral');
            show.append(identity, state);
            if (row.expected === false || row.expected === 0) show.append(chip('免考勤'));
            if (row.confirmed_retake) show.append(chip('重修'));
            show.append(node('small', 'la-student-reason', row.reason || '等待核对接入记录'));
            show.addEventListener('click', () => showDetail(row.student_id, show));
            choose.wrapper.classList.add('la-student-select'); choose.wrapper.hidden = !batch.open;
            card.append(show, choose.wrapper);
            card.dataset.lqSelected = String(selected.has(row.student_id)); grid.append(card);
        }
        if (!rows.length) grid.append(createContent('empty', { title: '没有匹配的学生', body: '可切换筛选或搜索条件。' }));
        pager.replaceChildren(button('上一页', () => { page--; paintGrid(); }, 'ghost'), node('span', '', `${rows.length} 人 · 第 ${page + 1} / ${Math.max(1, Math.ceil(rows.length / 40))} 页`), button('下一页', () => { page++; paintGrid(); }, 'ghost'));
        pager.firstChild.disabled = page <= 0; pager.lastChild.disabled = (page + 1) * 40 >= rows.length;
        paintSelection();
    }
    batch.addEventListener('toggle', () => grid.querySelectorAll('.la-student-select').forEach(n => n.hidden = !batch.open));
    function paintSelection() {
        count.textContent = `已选择 ${selected.size} 人`;
        bulk.hidden = selected.size === 0;
        bulk.querySelector('.lq-bulk-bar__count').textContent = `已选择 ${selected.size} 人`;
        apply.disabled = busy || !selected.size || !canEdit();
    }
    async function applySelected() {
        if (!fields.reason.value.trim()) { fields.reason.reportValidity(); return; }
        const records = [...selected].map(([student_id, revision]) => ({ student_id, revision, status: fields.status.value }));
        await mutate(async () => {
            const result = await call(`/rounds/${round.id}/records`, { method: 'PATCH', body: { records, reason: fields.reason.value.trim() } });
            const conflicts = [];
            for (const item of result.results || []) { if (item.result === 'updated') selected.delete(item.student_id); else conflicts.push(item.student_id); }
            round = result.round; paint();
            if (conflicts.length) setNotice(`${conflicts.length} 人的版本发生冲突。选择与理由已保留；请核对详情后重新选择该生，再提交。`, true);
            else { fields.reason.value = ''; dirty = false; setNotice('所选学生的签到状态已更新。'); }
            if (focusedStudent) await showDetail(focusedStudent);
        });
    }
    async function load({ preserveNotice = false, signal } = {}) {
        const generation = ++epoch;
        const result = await call(teacher ? (attendanceId ? `/rounds/${attendanceId}` : `/sessions/${lesson}`) : `/sessions/${lesson}/me`, { signal });
        if (closed || generation !== epoch) return;
        if (teacher) {
            // A voided exact link can point behind a newer live round. Only
            // the actual lesson projection can authorize showing a restart.
            const latest = attendanceId && !historyOnly && result.state === 'void' ? await call(`/sessions/${lesson}`, { signal }) : null;
            if (closed || generation !== epoch) return;
            data = attendanceId ? { ...latest, round: result, policy: latest?.policy || result.policy, session } : result;
            round = data.round;
            latestLessonRoundId = attendanceId ? (latest?.round?.id ?? (round?.is_current ? round.id : null)) : round?.id ?? null;
            if (!historyOnly && round && Number(round.session_id) !== lesson) throw new Error('指定点名不属于本课次，未改选其他课次。');
        } else { data = result; round = result.round; if (studentId && result.student_id !== Number(studentId)) throw new Error('此签到链接不属于当前账号。'); if (attendanceId && round?.id !== Number(attendanceId)) throw new Error('指定点名已变更，请从本人课次记录核对。'); }
        paint();
        if (!preserveNotice) setNotice('');
        if (focusedStudent && teacher && !detailDirty) await showDetail(focusedStudent);
        workflow?.refresh().catch(() => {});
    }
    function paint() {
        // Preserve the nested layer's trigger, focus and configuration draft
        // if a parent request that started earlier completes behind it.
        if (externalOpen || detailHandle) return;
        const context = historyOnly ? round?.context || {} : session || {};
        const originalClasses = [...new Set((round?.records||[]).map(r=>r.class_name).filter(Boolean))];
        const classLabel = context.class_name || context.teaching_class_name || (historyOnly ? originalClasses.slice(0,8).join('、') + (originalClasses.length>8?` 等 ${originalClasses.length} 个班`:'') : '');
        heading.replaceChildren(node('h3', '', context.detail_title || context.session_title || context.title || context.course_name || '历史点名'), node('p', '', `${context.session_date || ''} ${context.academic_section_text || context.section_text || ''} ${classLabel} · 北京时间`), chip(ROUND_LABELS[round?.state] || '尚未开启'));
        if (!teacher) { paintStudent(); return; }
        const readyToStart = !historyOnly && (!round || (round.state === 'void' && latestLessonRoundId === round.id));
        guidance.textContent = historyOnly ? '已保存的历史点名。选择学生查看证据与历次记录。' : readyToStart ? '核对网络与时间后开始记录；下课自动结束。课次暂未对应也可以先完成本地点名。' : ['running', 'scheduled'].includes(round?.state) ? `正在自动记录课堂接入，预计 ${time(round.ends_at_ms)} 结束。关闭浮窗或离开页面后，后台仍继续记录。` : round?.state === 'settling' ? '本地点名已结束，后台正在收齐补报并结算。完成后即可同步与推送。' : '点名结果已生成。点击学生卡片查看详情或改签，确认后点击同步与推送。';
        const counts = round?.summary?.counts || {};
        summary.replaceChildren(...[['出勤', counts.PRESENT || 0], ['待确认', counts.PENDING || 0], ['应到', round?.summary?.expected ?? '—'], ['免考勤', round?.summary?.exempt ?? 0]].map(([label, value]) => createContent('card', { title: label, variant: 'stat', value })));
        stateCounts.replaceChildren(...ATTENDANCE_STATES.map(([key,label])=>chip(`${label} ${counts[key]||0} 人`,key==='PRESENT'?'success':'neutral')));
        actions.replaceChildren(); secondaryActions.replaceChildren(button('刷新记录', () => load().catch(fail)), button('学期签到统计', openDashboard), button('历史点名', async event => { const trigger=event.currentTarget; trigger.disabled=true; try { const mod=await import('./login_attendance_history.js'); if(!closed && trigger.isConnected) mod.openAttendanceHistory({classOfferingId:offering,trigger,parentLayer:handle}); } catch(error){fail(error);} finally{if(trigger.isConnected)trigger.disabled=busy;} }));
        if (readyToStart) { const start = button('开始记录', createRound, 'prominent'); start.disabled = Boolean(data?.policy?.closed_at_ms); start.dataset.laDisabled = String(start.disabled); actions.append(start); }
        if (isCurrent() && ['running', 'scheduled'].includes(round?.state)) actions.append(button('结束本地点名', endRound));
        if (isCurrent() && round.state !== 'void') secondaryActions.append(button('重新核算', () => mutate(async () => { round = await call(`/rounds/${round.id}/recalculate`, { method: 'POST', body: { revision: round.revision } }); paint(); setNotice('已读取最新核算结果。'); })), button('作废本轮', voidRound, 'destructive'));
        if (round) secondaryActions.append(button(historyOnly || !isCurrent() || round.state === 'void' ? '查看学校同步记录' : '高级核对与历史任务', event => showExternal(event).catch(fail)));
        if (!formInitialized && data?.policy) {
            appendField(configBody, 'networks', 'textarea', '本课教室 IP 或网段（每行一个）', (data.policy.networks?.length ? data.policy.networks : [data.teacher_network?.ip].filter(Boolean)).join('\n'), { rows: 2 });
            appendField(configBody, 'starts', 'input', '开始时间（北京时间）', localTimeInput(data.window?.starts_at_ms || round?.starts_at_ms), { type: 'datetime-local', required: true });
            appendField(configBody, 'ends', 'input', '结束时间（北京时间）', localTimeInput(data.window?.ends_at_ms || round?.ends_at_ms), { type: 'datetime-local', required: true });
            configBody.append(node('p', 'la-config-help', data.window_error || '开始记录即确认本次网络与时间；网络会记住供下次使用，下课后自动结束并计算出勤。')); formInitialized = true;
        }
        config.hidden = historyOnly || !readyToStart;
        if (readyToStart) config.open = true;
        content.hidden = !round; summary.hidden = !round; stateCounts.hidden = !round;
        if (round) paintGrid(); else grid.replaceChildren();
        workflow?.update();
        paintSelection();
        if (busy) setBusy(true);
    }
    function networks() {
        const values = fields.networks.value.split(/[\n,，]/).map(s => s.trim()).filter(Boolean);
        if (!values.length) throw new Error('请填写已确认的教室 IP 或网段。');
        return values;
    }
    async function createRound() { await mutate(async () => {
        if (!fields.starts.reportValidity() || !fields.ends.reportValidity()) return;
        const body = { request_key: createKey, networks: networks(), policy_revision: data.policy.revision };
        if (round?.id && round.state !== 'void' && isCurrent()) body.supersedes_id = round.id;
        if (fields.starts.value) body.starts_at = `${fields.starts.value}:00+08:00`;
        if (fields.ends.value) body.ends_at = `${fields.ends.value}:00+08:00`;
        round = await call(`/sessions/${lesson}/rounds`, { method: 'POST', body }); if (round.policy) data.policy = round.policy; attendanceId = round.id; latestLessonRoundId = round.id; dirty = false; createKey = crypto.randomUUID(); paint(); workflow?.refresh().catch(() => {}); setNotice('');
    }); }
    async function endRound() {
        if (!await confirmDialog({ title: '结束本地点名？', message: '将停止本次实时接入认定并等待补报结算；未确认者不会自动变成缺课。', confirmLabel: '结束本地点名' }, { parentLayer: handle })) return;
        await mutate(async () => { round = await call(`/rounds/${round.id}/end`, { method: 'POST', body: { revision: round.revision } }); paint(); setNotice('本地点名已结束，补报结算由后台继续完成。'); });
    }
    async function voidRound() {
        if (!fields.reason.value.trim()) { setNotice('请在修改理由中填写作废原因。', true); bulk.hidden = false; fields.reason.focus(); return; }
        if (!await confirmDialog({ title: '作废本轮点名？', message: '原记录和操作审计会保留。不会自动删除或修改学校端点名。', confirmLabel: '作废本轮', danger: true }, { parentLayer: handle })) return;
        await mutate(async () => { const wasCurrent=isCurrent(); round = await call(`/rounds/${round.id}/void`, { method: 'POST', body: { revision: round.revision, reason: fields.reason.value.trim() } }); if(wasCurrent)latestLessonRoundId=round.id; dirty = false; selected.clear(); fields.reason.value = ''; paint(); setNotice('本轮已作废，历史记录仍可查看。'); });
    }
    function recordFacts(record) {
        const facts = record.evidence || {};
        const box = node('div', 'la-facts');
        for (const [label, value] of [['签到', record.status_label || statusLabel(record.status)], ['班级',record.class_name||'暂无'], ['说明', record.reason || '暂无'], ['平台登录', `${facts.login?.successful_count ?? '—'} 次（独立于课堂访问）`], ['登录网络', `${facts.login?.distinct_ip_count ?? '—'} 个 IP`], ['首次接入', time(record.first_seen_ms)], ['末次接入', time(record.last_seen_ms)], ['课堂访问', `${facts.classroom_access?.page_view_count ?? '—'} 次`], ['平台操作', `${facts.classroom_access?.operation_record_count ?? '—'} 次`], ['有效学习', duration(facts.learning?.measured_seconds)], ['采集情况', HEALTH_LABELS[facts.collection?.status] || '请结合时间线核对']]) { const p = node('p'); p.append(node('strong', '', `${label}：`), document.createTextNode(String(value))); box.append(p); }
        const network=facts.network_access;
        if(network){box.append(node('p','',`平台接入 IP（本轮时间窗）：首次 ${network.first_ip||'暂无'}；末次 ${network.last_ip||'暂无'}。`),node('p','',`接入佐证：${network.observation_count??0} 条；已记录 ${network.distinct_ips?.length??0} 个 IP。`));if(network.distinct_ips?.length)box.append(node('p','',network.distinct_ips.slice(0,16).join('、')));}
        return box;
    }
    async function showDetail(id, detailTrigger) {
        focusedStudent = Number(id); const generation = ++detailEpoch;
        if (!detailHandle) {
            detail.replaceChildren(node('p', '', '正在读取签到详情…'));
            const release = () => { detailEpoch++; focusedStudent = null; detailHandle = null; detailDirty = false; detailDropdowns.forEach(d => d.destroy()); detailDropdowns = []; recordDrafts.delete(Number(id)); if (!closed) { paint(); queueMicrotask(() => { if (!closed && getLayerSystem(document).top() === handle) grid.querySelector(`[data-student-id="${Number(id)}"] .la-student-open`)?.focus({ preventScroll: true }); }); } };
            detailHandle = openDialog({ title: '学生签到详情', body: detail, size: 'lg' }, { trigger: detailTrigger || handle.root.querySelector('button'), parentLayer: handle,
                beforeClose: async () => !busy && !detailBusy && (!detailDirty || await confirmDialog({ title: '放弃本次改签草稿？', message: '已保存的签到状态不受影响。', confirmLabel: '放弃修改', danger: true }, { parentLayer: detailHandle })), onClose: release, onDestroy: release });
            detailHandle.root.classList.add('la-dialog');
        }
        try {
            const value = await call(`/rounds/${round.id}/students/${id}`);
            if (closed || generation !== detailEpoch) return;
            detailDropdowns.forEach(d => d.destroy()); detailDropdowns = [];
            detail.replaceChildren(node('h3', '', `${value.record.name} · 本次签到`));
            if (canEdit()) {
                const draft = recordDrafts.get(Number(id)) || { status: value.record.status === 'PENDING' ? 'PRESENT' : value.record.status, reason: '' };
                const edit = node('section', 'la-record-edit');
                const state = field('select', '出勤状态', draft.status, { options: ATTENDANCE_STATES.map(([value, label]) => ({ value, label })) });
                const reason = field('textarea', '改签理由', draft.reason, { rows: 2, required: true, maxLength: 1000 });
                const feedback = node('p', 'la-notice'); feedback.setAttribute('role', 'status');
                const retain = () => { recordDrafts.set(Number(id), { status: state.control.value, reason: reason.control.value }); detailDirty = true; };
                state.control.addEventListener('change', retain); reason.control.addEventListener('input', retain);
                const save = button('保存出勤状态', async () => {
                    if (busy || detailBusy || !reason.control.reportValidity()) return;
                    detailBusy = true; save.disabled = true; state.control.disabled = reason.control.disabled = true; feedback.setAttribute('role', 'status'); feedback.textContent = '正在保存…';
                    try {
                        const response = await call(`/rounds/${round.id}/records`, { method: 'PATCH', body: { records: [{ student_id: Number(id), revision: value.record.revision, status: state.control.value }], reason: reason.control.value.trim() } });
                        const result = response.results?.find(r => Number(r.student_id) === Number(id));
                        if (!result || result.result !== 'updated') throw new Error('该学生记录已变化，草稿已保留。请重新读取最新记录后再确认。');
                        round = response.round; recordDrafts.delete(Number(id)); detailDirty = false;
                        document.dispatchEvent(new CustomEvent('classroom:attendance-changed')); await showDetail(id); workflow?.refresh().catch(() => {});
                    } catch (error) { feedback.textContent = error.message || '未能保存，草稿已保留。'; feedback.setAttribute('role', 'alert'); }
                    finally { detailBusy = false; if (save.isConnected) { save.disabled = false; state.control.disabled = reason.control.disabled = false; } }
                }, 'prominent');
                edit.append(state.wrapper, reason.wrapper, save, feedback, button('重新读取最新记录', () => showDetail(id), 'ghost')); detail.append(edit); detailDropdowns.push(...enhanceDropdowns(edit));
            }
            detail.append(recordFacts(value.record));
            const targetAppeal = appealId && Number(id) === Number(studentId) ? appealId : value.pending_appeal?.id;
            if (targetAppeal) await paintAppeal(value.record, generation, targetAppeal);
            detail.append(node('h4', '', '前几次同课程记录'));
            for (const row of value.history || []) { const previous = node('details', 'lq-collapsible'); previous.append(node('summary', 'lq-collapsible__summary', `${row.context?.session_date || ''} ${row.context?.session_title || ''} · ${row.status_label || statusLabel(row.status)}`), recordFacts(row)); detail.append(previous); }
            if (!value.history?.length) detail.append(node('p', '', '暂无此前记录。'));
        } catch (error) { if (generation === detailEpoch) { const message = node('p', 'la-notice', error.message || '读取失败，请重试。'); message.setAttribute('role', 'alert'); detail.append(message, button('重试读取详情', () => showDetail(id))); } }
    }
    async function paintAppeal(record, generation, targetAppeal) {
        const value = await call(`/api/approvals/${targetAppeal}`); appealData = value.request || value;
        if (closed || generation !== detailEpoch) return;
        const appeal = node('section', 'la-appeal'); appeal.append(node('h4', '', '签到申诉'), node('p', '', appealData.reason || ''), chip(appealData.status_label || appealData.status));
        if (isCurrent() && appealData.status === 'pending' && appealData.can_decide) {
            const draft = appealDrafts.get(targetAppeal) || { note: '', status: 'PRESENT' };
            const note = field('textarea', '处理说明', draft.note, { rows: 2, required: true }); appeal.append(note.wrapper);
            const choice = field('select', '申诉更正为', draft.status, { options: ATTENDANCE_STATES.map(([value, label]) => ({ value, label })) }); appeal.append(choice.wrapper);
            const saveDraft = () => { appealDrafts.set(targetAppeal, { note: note.control.value, status: choice.control.value }); dirty = true; };
            note.control.addEventListener('input', saveDraft); choice.control.addEventListener('change', saveDraft);
            const decide = action => mutate(async () => { if (!note.control.value.trim()) { note.control.reportValidity(); return; } await call(`/api/approvals/${targetAppeal}/${action}`, { method: 'POST', body: { note: note.control.value.trim(), decision_payload: { status: choice.control.value, record_revision: record.revision } } }); appealDrafts.delete(targetAppeal); dirty = Boolean(appealDrafts.size || fields.reason.value.trim()); await load(); setNotice('申诉已处理。'); });
            appeal.append(button('按所选状态确认更正', () => decide('approve'), 'prominent'), button('维持结果并说明', () => decide('reject')));
        }
        detail.append(appeal);
        detailDropdowns.push(...enhanceDropdowns(appeal));
    }
    function paintStudent() {
        if (!round || data?.status === 'NOT_STARTED' || round.state === 'void') { detail.replaceChildren(); heading.hidden = true; document.dispatchEvent(new CustomEvent('classroom:attendance-changed')); void getLayerSystem(document).close(handle, 'unavailable'); return; }
        listPane.hidden = true; config.hidden = true; summary.replaceChildren(); actions.replaceChildren(button('刷新本人记录', () => load().catch(fail)), button('本学期签到', openDashboard));
        detail.replaceChildren(node('h3', '', '我的签到'), chip(data?.status_label || (data?.status === 'NOT_STARTED' ? '尚未开启' : statusLabel(data?.status)), data?.status === 'PRESENT' ? 'success' : 'neutral'), node('p', '', data?.message || '教师尚未开启本次点名。'));
        if (!round) return;
        if (data.appeal) detail.append(node('p', '', data.appeal.status === 'pending' ? '签到申诉正在处理中。' : '本次申诉已处理，可在通知中查看结果。'));
        detail.append(node('p', '', `点名结束：${time(round.ended_at_ms || round.ends_at_ms)}`), node('p', '', `平台有效学习：${duration(data.participation?.measured_seconds)}。听讲与外部实践不以此判定。`));
        const existingReason = fields.appealReason?.value || '';
        const f = field('textarea', '申诉说明', existingReason, { rows: 3, required: true, maxLength: 1000 }); fields.appealReason = f.control;
        const submit = button('提交签到申诉', () => mutate(async () => { if (!f.control.value.trim()) { f.control.reportValidity(); return; } await call(`/rounds/${round.id}/appeals`, { method: 'POST', body: { reason: f.control.value.trim() } }); dirty = false; f.control.value = ''; await load({ preserveNotice: true }); setNotice('申诉已提交，老师处理后会通知你。'); }), 'prominent');
        submit.disabled = !data.can_appeal; submit.dataset.laDisabled = String(!data.can_appeal);
        detail.append(f.wrapper, submit, node('p', '', data.can_appeal ? '仅提交本人的签到说明。已受理申诉会继续处理。' : '点名结束并开放申诉后可提交；作废轮次和已过期记录不开放新申诉。'));
    }
    async function openDashboard(event) { const trigger=event.currentTarget; trigger.disabled=true; try { const mod=await import('./attendance_dashboard.js'); if(!closed&&trigger.isConnected)mod.openAttendanceDashboard({classOfferingId:offering,userRole,trigger}); } catch(error){fail(error);} finally{if(trigger.isConnected)trigger.disabled=busy;} }
    async function showExternal(event) { const trigger=event.currentTarget; trigger.disabled=true; try { externalCleanup?.(); const mod = await import('./login_attendance_sync.js'); if (!closed && trigger.isConnected) { externalOpen = true; externalCleanup = mod.openAttendanceSync({ base: `${base}/rounds/${round.id}/external`, round, trigger, parentLayer: handle, readOnly: historyOnly || !isCurrent() || round.state === 'void', onClose: () => { externalOpen = false; externalCleanup = null; } }); } } catch(error) { externalOpen = false; throw error; } finally {if(trigger.isConnected)trigger.disabled=busy;} }
    fields.search.addEventListener('input', () => { page = 0; paintGrid(); }); fields.filter.addEventListener('change', () => { page = 0; paintGrid(); });
    shell.addEventListener('input', event => { if (!event.target.matches('input[type="search"]')) dirty = true; });
    listPane.hidden = !teacher; config.hidden = !teacher || historyOnly; workflowHost.hidden = !teacher || historyOnly; secondary.hidden = !teacher;
    let dropdowns = [];
    let poll;
    function cleanup() { if (closed) return; closed = true; epoch++; detailEpoch++; poll?.dispose(); workflow?.destroy(); detailHandle?.destroy(); dropdowns.forEach(d => d.destroy()); detailDropdowns.forEach(d => d.destroy()); externalCleanup?.(); cancelRequests.forEach(c => c.abort()); active.delete(key); }
    const parent = getLayerSystem(document).top();
    handle = openDialog({ title: teacher ? (historyOnly ? '历史点名详情' : '自动点名') : '我的签到', body: shell, size: 'xl' }, { trigger, ...(parent?.root.contains(trigger) ? { parentLayer: parent } : {}),
        beforeClose: async () => !busy && (!dirty || await confirmDialog({ title: '关闭并放弃未提交内容？', message: '已保存的签到记录不会受影响。', confirmLabel: '关闭', danger: true }, { parentLayer: handle })), onClose: cleanup, onDestroy: cleanup });
    handle.root.classList.add('la-dialog');
    if (teacher && !historyOnly) workflow = createAttendanceWorkflow({ host: workflowHost, base, sessionId: lesson, getRound: () => round, getLayer: () => handle, isLocalBusy: () => busy || detailBusy });
    dropdowns = enhanceDropdowns(shell);
    const controller = { handle, refresh: load, destroy: () => handle.destroy() }; active.set(key, controller);
    poll = createAmbientPoll(async signal => { if (!busy && !dirty) { try { await load({ preserveNotice: true, signal }); } catch (error) { fail(error); throw error; } } }, { intervalMs: 15000, active: () => !closed && !historyOnly && !document.hidden && getLayerSystem(document).top() === handle && !busy && !dirty });
    load().catch(fail); poll.start(15000);
    return controller;
}
