/** Course attendance read model. Source decisions remain on the server. */
import { apiFetch } from './api.js';
import { openDialog, confirm as confirmDialog } from './lq/dialogs.js';
import { getLayerSystem } from './lq/layer.js';
import { createDomainButton } from './lq/domain-controls.js';
import { createContent } from './lq/content.js';
import { createComponent } from './lq/components.js';
import { createForm } from './lq/forms.js';
import { enhanceDropdowns } from './lq/dropdown.js';
import { ATTENDANCE_STATES } from './login_attendance_states.js';

const active = new Map();
const n = (tag, cls = '', text) => { const el = document.createElement(tag); el.className = cls; if (text !== undefined) el.textContent = String(text); return el; };
const button = (label, action, variant = 'glass') => { const el = createDomainButton({ label, variant, size: 'sm' }); el.addEventListener('click', action); return el; };
const chip = (label, tone = 'neutral') => createComponent('chip', { label: String(label), kind: 'status', tone });
const labels = Object.fromEntries([...ATTENDANCE_STATES, ['PENDING', '待核对'], ['EXEMPT', '免考勤'], ['NOT_APPLICABLE', '免考勤'], ['LATE_OR_EARLY', '迟到或早退'], ['UNKNOWN', '待核对'], ['CHECKED', '出勤'], ['UNCHECKED', '缺课']]);
const sources = { local: '本地点名', smart_synced: '智慧课堂同步', confirmed_pdf: '已确认签到原件', mixed: '多来源核对' };
const states = { scheduled: '待开始', running: '点名中', settling: '等待补报结算', published: '已结束', synced: '已同步', unrecorded: '未纳入统计', void: '已作废' };
const count = value => Number.isFinite(Number(value)) ? Number(value) : 0;
const rate = value => value == null || !Number.isFinite(Number(value)) ? '—' : `${Number(value).toFixed(1)}%`;
const statusLabel = value => labels[value] || '待核对';
const tone = value => value === 'PRESENT' ? 'success' : ['PENDING', 'UNKNOWN', 'ABSENT'].includes(value) ? 'warning' : 'neutral';
const changed = () => document.dispatchEvent(new CustomEvent('classroom:attendance-changed'));

export function openAttendanceDashboard({ classOfferingId, userRole, trigger, sessionId = null, studentId = null, appealId = null }) {
    const offering = Number(classOfferingId), teacher = userRole === 'teacher';
    if (!Number.isSafeInteger(offering) || offering <= 0 || !['teacher', 'student'].includes(userRole)) return null;
    const key = `${userRole}:${offering}`;
    if (active.has(key)) return active.get(key);
    const base = `/api/classrooms/${offering}/login-attendance/dashboard`;
    let data = null, selectedStudent = teacher && Number(studentId) > 0 ? Number(studentId) : null;
    let view = 'sessions', page = 0, busy = false, closed = false, epoch = 0, request = null, handle, refreshPending = false;
    let focusSession = Number(sessionId) || null, scopeSession = null, initialAppeal = Number(appealId) || null;
    const body = n('div', 'la-workspace lad-workspace');
    const heading = n('div', 'la-heading'), notice = n('p', 'la-notice'); notice.setAttribute('role', 'status');
    const stats = n('div', 'la-summary'), statusCounts = n('div', 'la-actions');
    const actions = n('div', 'la-actions'), filters = n('div', 'la-filters');
    const searchField = createForm('input', { id: `lad-search-${crypto.randomUUID()}`, label: '搜索课次、姓名或学号', type: 'search', value: '' });
    const search = searchField.querySelector('input'); filters.append(searchField);
    const list = n('div', 'lad-list'), pager = n('div', 'la-actions');
    const note = n('p', 'lad-note');
    body.append(heading, notice, stats, statusCounts, actions, note, filters, list, pager);
    function fail(error) { if (!closed) { notice.textContent = error.message || '统计读取失败，请重试。'; notice.setAttribute('role', 'alert'); } }
    function gate() { body.setAttribute('aria-busy', String(busy)); actions.querySelectorAll('button').forEach(el => { el.disabled = busy; }); }
    async function load() {
        const generation = ++epoch; request?.abort(); request = new AbortController();
        const focused = document.activeElement, restoreListFocus = list.contains(focused);
        const focusedSession = focused?.closest('[data-attendance-session-id]')?.dataset.attendanceSessionId;
        const focusedLabel = focused?.textContent;
        const controller = request, deadline = window.setTimeout(() => controller.abort(), 20000);
        busy = true; gate(); notice.textContent = '正在读取签到记录…'; notice.setAttribute('role', 'status');
        try {
            const result = await apiFetch(selectedStudent ? `${base}/students/${selectedStudent}` : scopeSession ? `${base}?session_id=${scopeSession}` : base, { silent: true, signal: controller.signal });
            if (closed || generation !== epoch) return;
            data = result; notice.textContent = ''; paint();
            if (restoreListFocus && !focused.isConnected && getLayerSystem(document).top() === handle) {
                const card = [...list.querySelectorAll('[data-attendance-session-id]')].find(el => el.dataset.attendanceSessionId === focusedSession);
                const target = [...(card?.querySelectorAll('button') || [])].find(el => el.textContent === focusedLabel) || card?.querySelector('button') || handle.root.querySelector('[role="dialog"]');
                target?.focus({ preventScroll: true });
            }
            if (!teacher && !result.visible && !initialAppeal) { const entry = document.getElementById('cw-attendance-dashboard'); if (entry) entry.hidden = true; void getLayerSystem(document).close(handle, 'unavailable'); return; }
            if (initialAppeal) { const target = (data.sessions || []).find(row => Number(row.appeal?.id) === initialAppeal), requestId = initialAppeal; initialAppeal = null; void openSmartRecord({ offering, row: target || { title: '原签到申诉', session_id: focusSession }, appealId: requestId, teacher, trigger: refresh, parentLayer: handle }).catch(fail); }
        } catch (error) {
            if (!closed && generation === epoch) {
                fail(error.name === 'AbortError' ? new Error('读取未完成，请重试。') : error);
                if (initialAppeal) { const requestId = initialAppeal; initialAppeal = null; void openSmartRecord({ offering, row: { title: '原签到申诉', session_id: focusSession }, appealId: requestId, teacher, trigger: refresh, parentLayer: handle }).catch(fail); }
            }
        }
        finally { window.clearTimeout(deadline); if (!closed && generation === epoch) { busy = false; gate(); } }
    }
    const refresh = button('刷新统计', load);
    const allStudents = button('课程总览', () => { selectedStudent = null; focusSession = null; scopeSession = null; page = 0; search.value = ''; void load(); });
    const lessonView = button('课次记录', () => { view = 'sessions'; page = 0; search.value = ''; paint(); });
    const studentView = button('学生汇总', () => { view = 'students'; page = 0; focusSession = null; search.value = ''; paint(); });
    const allLessons = button('全部课次', () => { focusSession = null; page = 0; if (scopeSession) { scopeSession = null; void load(); } else paintRows(); }, 'ghost');
    actions.append(refresh, allStudents, lessonView, studentView, allLessons);
    function summaryCards(summary) {
        return [['出勤率', rate(summary.attendance_rate)], ['已统计课次', count(summary.recorded_session_count)], ['已确认人次', count(summary.confirmed)], ['待核对人次', count(summary.pending)]]
            .map(([title, value]) => createContent('card', { title: !teacher || selectedStudent ? title.replace('人次', '课次') : title, value, variant: 'stat' }));
    }
    function paint() {
        const summary = data.summary || {}, person = data.student;
        heading.replaceChildren(n('h3', '', person ? `${person.name} · 本课程签到` : data.offering?.course_name || '课程签到统计'), n('p', '', [data.offering?.semester, person?.student_number].filter(Boolean).join(' · ')));
        stats.replaceChildren(...summaryCards(summary));
        statusCounts.replaceChildren(...Object.entries(summary.counts || {}).map(([status, total]) => chip(`${statusLabel(status)} ${total}`, tone(status))));
        if (summary.pending_appeals) statusCounts.append(chip(`待处理申诉 ${summary.pending_appeals}`, 'warning'));
        note.textContent = data.rate_note || summary.rate_note || '只统计已有有效签到记录的课次。待核对、免考勤和未开展点名的课次不计入出勤率分母；未开启不代表缺课。';
        allStudents.hidden = !teacher || !selectedStudent && !scopeSession;
        studentView.hidden = !teacher || Boolean(selectedStudent);
        studentView.textContent = scopeSession ? '本课学生' : '学生汇总';
        lessonView.hidden = !teacher || Boolean(selectedStudent);
        allLessons.hidden = !focusSession;
        lessonView.setAttribute('aria-pressed', String(view === 'sessions')); studentView.setAttribute('aria-pressed', String(view === 'students'));
        if (selectedStudent || !teacher) view = 'sessions';
        paintRows(); gate();
    }
    async function openLesson(row, event) {
        const target = event.currentTarget;
        if (busy || target.disabled) return;
        target.disabled = true;
        try {
            if (row.source === 'local' || teacher && !selectedStudent && row.local_round_id) {
                const mod = await import('./login_attendance.js');
                if (!closed && target.isConnected) mod.openLoginAttendance({ classOfferingId: offering, userRole, session: { id: row.session_id, title: row.title, session_date: row.date }, attendanceId: row.local_round_id || row.round_id, studentId: selectedStudent, trigger: target });
            } else if (row.source_key) await openSmartRecord({ offering, row, teacher, trigger: target, parentLayer: handle });
        } catch (error) { fail(error); }
        finally { if (target.isConnected) target.disabled = false; }
    }
    function sessionCard(row) {
        const content = n('div', 'lad-session-body'), badges = n('div', 'la-actions');
        badges.append(chip(sources[row.source] || '暂无签到来源'), chip(states[row.state] || '未纳入统计'));
        if (row.status) badges.append(chip(row.status_label || statusLabel(row.status), tone(row.status)));
        content.append(badges);
        if (row.counts) content.append(n('p', '', Object.entries(row.counts).map(([status, total]) => `${statusLabel(status)} ${total}`).join(' · ')));
        if (row.attendance_rate != null) content.append(createComponent('progress', { label: `${row.title || '本课'}出勤率 ${rate(row.attendance_rate)}`, value: Math.min(100, Math.max(0, Number(row.attendance_rate))), max: 100 }));
        if (row.local_state && row.source !== 'local') content.append(n('small', '', `本地点名${states[row.local_state] || ''}；当前统计采用${sources[row.source] || '已核对来源'}。`));
        if (row.conflict_count || row.source_changed) content.append(chip('来源有差异，需教师核对', 'warning'));
        if (row.appeal) content.append(chip(row.appeal.status === 'pending' ? '申诉处理中' : '申诉已处理', row.appeal.status === 'pending' ? 'warning' : 'neutral'));
        if (row.correction) content.append(n('small', '', row.correction.stale ? '来源已变化，原本地更正待重新核对。' : '包含本地核对更正；学校原始记录仍保留。'));
        const controls = [];
        if (teacher && !selectedStudent) controls.push(button('查看该课学生', () => { scopeSession = Number(row.session_id); focusSession = scopeSession; view = 'students'; page = 0; search.value = ''; void load(); }, 'ghost'));
        if (row.source === 'local' || teacher && row.local_round_id || row.source_key) controls.push(button(!teacher && row.can_appeal ? '查看并申诉' : '查看签到详情', event => openLesson(row, event), 'ghost'));
        const card = createContent('card', { title: row.title || '课次记录', meta: row.date || '', attrs: { 'data-attendance-session-id': String(row.session_id) } }, { body: [content], actions: controls });
        if (focusSession === Number(row.session_id)) card.dataset.lqSelected = 'true';
        return card;
    }
    function studentCard(row) {
        const summary = row.summary || {}, content = n('div', 'lad-session-body');
        content.append(n('strong', '', `${scopeSession ? '本课' : '本学期'}出勤率 ${rate(summary.attendance_rate)}`), n('p', '', `${count(summary.recorded_session_count)} 个课次 · 待核对 ${count(summary.pending)}`), n('small', '', Object.entries(summary.counts || {}).map(([status, total]) => `${statusLabel(status)} ${total}`).join(' · ')));
        if (summary.pending_appeals) content.append(chip(`待处理申诉 ${summary.pending_appeals}`, 'warning'));
        const open = button('查看个人课次', () => { selectedStudent = Number(row.student_id); view = 'sessions'; page = 0; search.value = ''; void load(); }, 'ghost');
        return createContent('card', { title: row.name, meta: [row.student_number, row.class_name].filter(Boolean).join(' · ') }, { body: [content], actions: [open] });
    }
    function paintRows() {
        const q = search.value.trim().toLowerCase(), isStudents = teacher && !selectedStudent && view === 'students';
        const rows = (isStudents ? data?.students || [] : data?.sessions || []).filter(row => (isStudents || !focusSession || Number(row.session_id) === focusSession) && (!q || [row.title, row.date, row.name, row.student_number, row.class_name].filter(Boolean).join(' ').toLowerCase().includes(q)));
        page = Math.min(page, Math.max(0, Math.ceil(rows.length / 40) - 1));
        list.replaceChildren(...rows.slice(page * 40, (page + 1) * 40).map(isStudents ? studentCard : sessionCard));
        if (!rows.length) list.append(createContent('empty', { title: q ? '没有匹配记录' : '暂无可统计的签到记录', description: teacher ? '只有已开启的本地点名或已同步并关联的学校签到才纳入统计，未使用签到的课次不会记为缺课。' : '可调整筛选或稍后刷新。' }));
        const previous = button('上一页', () => { page--; paintRows(); }, 'ghost'), next = button('下一页', () => { page++; paintRows(); }, 'ghost');
        previous.disabled = page === 0; next.disabled = (page + 1) * 40 >= rows.length;
        pager.replaceChildren(previous, n('span', '', `共 ${rows.length} 项 · 第 ${page + 1} / ${Math.max(1, Math.ceil(rows.length / 40))} 页`), next);
        allLessons.hidden = !focusSession;
    }
    search.addEventListener('input', () => { page = 0; paintRows(); });
    // Child dialogs own their trigger until LQ completes the close and restores
    // focus. Coalesce changes meanwhile instead of detaching that trigger.
    const resumeRefresh = () => { if (!closed && refreshPending && getLayerSystem(document).top() === handle) { refreshPending = false; void load(); } };
    const onChanged = () => { if (!closed) { refreshPending = true; resumeRefresh(); } };
    function cleanup() { if (closed) return; closed = true; epoch++; request?.abort(); document.removeEventListener('classroom:attendance-changed', onChanged); document.removeEventListener('focusin', resumeRefresh); active.delete(key); }
    const parent = getLayerSystem(document).top();
    handle = openDialog({ title: teacher ? '课程签到统计' : '我的课程签到', body, size: 'xl' }, { trigger, ...(parent?.root.contains(trigger) ? { parentLayer: parent } : {}), onClose: cleanup, onDestroy: cleanup });
    handle.root.classList.add('la-dialog');
    document.addEventListener('classroom:attendance-changed', onChanged);
    document.addEventListener('focusin', resumeRefresh);
    const controller = { handle, refresh: load }; active.set(key, controller); void load(); return controller;
}

async function openSmartRecord({ offering, row, teacher, trigger, parentLayer, appealId = null }) {
    const base = row.source_key ? `/api/classrooms/${offering}/smart-attendance/records/${encodeURIComponent(row.source_key)}` : null;
    let record = null, closed = false, busy = false, dirty = false, handle, dropdowns = [], controller, draft = '', draftStatus = 'PRESENT', reading = false;
    const body = n('div', 'la-workspace'), message = n('p', 'la-notice'), facts = n('div', 'la-facts'), form = n('div', 'la-form');
    const refresh = button('读取最新记录', () => { if (!busy && !reading) void load(); });
    message.setAttribute('role', 'status'); body.append(message, refresh, facts, form);
    function cleanup() { closed = true; controller?.abort(); dropdowns.forEach(item => item.destroy()); }
    handle = openDialog({ title: '签到记录与申诉', body, size: 'md' }, { trigger, parentLayer, beforeClose: async () => !busy && (!dirty || await confirmDialog({ title: '放弃尚未提交的说明？', message: '已提交的申诉和签到记录不受影响。', confirmLabel: '放弃说明', danger: true }, { parentLayer: handle })), onClose: cleanup, onDestroy: cleanup });
    async function request(path = base, options = {}) { controller = new AbortController(); const deadline = window.setTimeout(() => controller.abort(), 20000); try { return await apiFetch(path, { silent: true, ...options, signal: controller.signal }); } finally { window.clearTimeout(deadline); } }
    async function load() {
        reading = true; refresh.disabled = true;
        try {
            let requestItem = null;
            if (appealId) {
                const value = await request(`/api/approvals/${appealId}`); requestItem = value.request || value;
                if (requestItem.request_type && requestItem.request_type !== 'smart_attendance_appeal') throw new Error('该链接不是智慧课堂签到申诉，请从原通知进入。');
                record = { ...(requestItem.detail?.attendance || {}), appeal: requestItem, can_appeal: false };
            } else record = await request();
            if (closed) return;
            const value = record.record || {}, appeal = record.appeal;
            const effective = record.effective_status || value.effective_status || (record.current_source_available === false ? 'PENDING' : value.status || row.status);
            facts.replaceChildren(n('p', '', `${row.title || '本课'} · ${row.date || ''}`), n('p', '', `平台统计：${statusLabel(effective)}`), n('p', '', `学校原始记录：${value.source_status_label || value.status_label || statusLabel(value.source_status || value.status || row.status)}`), n('p', '', `来源：${sources[row.source] || '已同步签到'}`));
            if (record.correction) facts.append(n('p', '', record.correction.stale ? '来源已经变化，原本地更正待重新核对。' : '已应用本地核对更正；学校原始状态未被修改。'));
            if (record.source_changed || record.current_source_available === false) facts.append(chip('来源已变化，请先核对最新记录', 'warning'));
            if (appeal) facts.append(n('p', '', `申诉：${appeal.status_label || (appeal.status === 'pending' ? '处理中' : '已处理')}`), n('p', '', appeal.reason || ''));
            form.replaceChildren(); dropdowns.forEach(item => item.destroy()); dropdowns = [];
            if (!teacher && record.can_appeal) {
                const reason = createForm('textarea', { id: `lad-appeal-${crypto.randomUUID()}`, label: '申诉说明', rows: 4, required: true, maxLength: 1000, value: draft });
                const input = reason.querySelector('textarea'); input.addEventListener('input', () => { draft = input.value; dirty = Boolean(draft.trim()); });
                const submit = button('提交本人申诉', async () => {
                    if (busy || !input.reportValidity() || !input.value.trim()) return;
                    busy = true; submit.disabled = true; refresh.disabled = true;
                    try { await request(`${base}/appeals`, { method: 'POST', body: { reason: input.value.trim(), source_fingerprint: value.source_fingerprint || record.source_fingerprint || row.source_fingerprint } }); if (closed) return; dirty = false; draft = ''; message.textContent = '申诉已提交，处理结果会通过通知反馈。'; message.setAttribute('role', 'status'); changed(); await load(); }
                    catch (error) { if (!closed) { message.textContent = error.status === 409 ? '记录已变化，说明已保留。请读取最新记录并核对后再提交。' : error.message || '提交失败，说明已保留。'; message.setAttribute('role', 'alert'); } }
                    finally { busy = false; refresh.disabled = false; if (submit.isConnected) submit.disabled = false; }
                }, 'prominent'); form.append(reason, submit);
            } else if (teacher && appeal?.status === 'pending') {
                const detail = requestItem || await request(`/api/approvals/${appeal.id}`); if (closed) return;
                const item = detail.request || detail, attendance = item.detail?.attendance || {};
                if (item.can_decide === false) { form.append(n('p', '', '当前账号不能处理此申请。')); return; }
                const fingerprint = attendance.source_fingerprint || attendance.record?.source_fingerprint;
                const canApprove = attendance.current_source_available !== false && Boolean(fingerprint);
                const status = createForm('select', { id: `lad-status-${crypto.randomUUID()}`, label: '本地更正为', value: draftStatus, options: ATTENDANCE_STATES.map(([value, label]) => ({ value, label })) });
                status.querySelector('select').addEventListener('change', event => { draftStatus = event.target.value; dirty = true; });
                const reason = createForm('textarea', { id: `lad-note-${crypto.randomUUID()}`, label: '核对说明', rows: 3, required: true, maxLength: 1000, value: draft });
                const input = reason.querySelector('textarea'); input.addEventListener('input', () => { draft = input.value; dirty = Boolean(draft.trim()); });
                const decide = async action => {
                    if (busy || !input.reportValidity() || !input.value.trim() || action === 'approve' && !canApprove) return;
                    busy = true; refresh.disabled = true; form.querySelectorAll('button').forEach(el => { el.disabled = true; });
                    try { await request(`/api/approvals/${appeal.id}/${action}`, { method: 'POST', body: { note: input.value.trim(), ...(action === 'approve' ? { decision_payload: { status: status.querySelector('select').value, source_fingerprint: fingerprint } } : {}) } }); dirty = false; draft = ''; message.textContent = '申诉已处理；学校原始记录保持不变。'; message.setAttribute('role', 'status'); changed(); await load(); }
                    catch (error) { if (!closed) { message.textContent = error.message || '处理未完成，说明已保留，请核对最新来源。'; message.setAttribute('role', 'alert'); } }
                    finally { busy = false; refresh.disabled = false; form.querySelectorAll('button').forEach(el => { el.disabled = el.dataset.sourceUnavailable === 'true'; }); }
                };
                const approve = button('确认本地更正', () => decide('approve'), 'prominent'); approve.disabled = !canApprove; approve.dataset.sourceUnavailable = String(!canApprove);
                form.append(status, reason, n('p', '', '批准仅更正平台统计，不会修改学校端记录。'), approve, button('维持结果并说明', () => decide('reject')));
                dropdowns = enhanceDropdowns(form);
            } else if (!teacher && !appeal) form.append(n('p', '', '本记录暂不接受新申诉；可联系任课教师核对。'));
        } catch (error) { if (!closed) { message.textContent = error.message || '记录读取失败，请重试。'; message.setAttribute('role', 'alert'); } }
        finally { reading = false; if (!closed) refresh.disabled = busy; }
    }
    await load(); return handle;
}
