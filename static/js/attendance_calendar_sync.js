import { confirm, openDialog } from './lq/dialogs.js';
import { getLayerSystem } from './lq/layer.js';
import { enhanceDropdowns } from './lq/dropdown.js';
import { createAmbientPoll } from './ambient_poll.js';
import { openAttendanceAlignment } from './login_attendance_alignment.js';
import { action, badge, calendarRequests, element, feedback, field, lessonLabel, statusText, studentLabel } from './attendance_calendar_controls.js';

const states = {
    ready: '可推送', create_available: '学校未点名', school_only: '已回读学校', empty: '两端均未点名', school_missing: '学校未点名', local_pending: '等待结算',
    alignment_required: '待对齐', blocked: '暂不能推送', confirmed: '已核对', partial: '部分完成', conflict: '需核对',
    queued: '已排队', running: '处理中', reading: '正在回读', comparing: '正在比对', outcome_unknown: '待查回执',
    pending: '已排队', superseded: '版本已更新', failed: '处理失败', not_sent: '可重试原请求', no_receipt: '未找到回执',
};
const rowStates = { needs_teacher_action: '拟推送', confirmed: '已一致', protected: '保留学校记录', blocked: '已跳过', conflict: '有差异', not_enrolled: '学校无此人', no_local_record: '无本地记录', unmapped: '未匹配' };
const pendingStates = new Set(['queued', 'running', 'reading', 'comparing', 'outcome_unknown', 'pending', 'sending', 'readback']);
const remoteText = value => ({ CHECKED: '出勤', UNCHECKED: '缺课', NOT_APPLICABLE: '免考勤', STUDY_TOUR: '研学', PRACTICE: '实践', VOLUNTEER: '志愿服务' }[value] || statusText(value));

function receiptStore(base) {
    // Store only opaque readback keys and lesson ids, never names, rosters or status rows.
    const teacher = window.APP_CONFIG?.userInfo?.id || window.APP_CONFIG?.userInfo?.teacher_id;
    const key = teacher ? `attendance-receipts:${teacher}:${base}` : null;
    function read() {
        try { return key ? JSON.parse(sessionStorage.getItem(key) || '[]').filter(item => Number(item.session) > 0 && typeof item.key === 'string') : []; }
        catch { return []; }
    }
    return { read, remember(session, id) {
        if (!key) return;
        const rows = read().filter(item => item.key !== id);
        try { sessionStorage.setItem(key, JSON.stringify([...rows, { session, key: id }].slice(-96))); } catch { /* The active dialog still owns this receipt. */ }
    } };
}
function multiField(label, rows, value, labelOf, selected) {
    const { wrapper, input } = field('select', label, { value: '', searchable: true, options: [{ value: '', label: '请选择' },
        ...rows.map(row => ({ value: String(value(row)), label: labelOf(row) }))] });
    input.multiple = true; input.options[0].remove();
    const ids = new Set(selected.map(String));
    for (const option of input.options) option.selected = ids.has(option.value);
    return { wrapper, input };
}

export function openCalendarSync({ base, students = [], sessions = [], today, studentIds = null, fixedScope = false, trigger, parentLayer, onChange }) {
    const req = calendarRequests(), store = receiptStore(base), body = element('div', 'la-workspace lac-sync'), footer = element('div', 'la-actions');
    const scope = element('div', 'lac-sync-filters'), feedbackNode = element('p', 'la-notice'), list = element('div', 'lac-sync-list');
    const pupilNames = new Map(students.map(student => [Number(student.student_id), studentLabel(student)]));
    const lessonById = new Map(sessions.map(lesson => [Number(lesson.session_id), lesson]));
    const dueLessons = sessions.filter(row => !today || row.date <= today).filter(row => !['cancelled', 'canceled', 'suspended'].includes(row.schedule_status));
    const chooseLessons = multiField('选择课次', sessions, row => row.session_id, row => row.date + ' · ' + lessonLabel(row), dueLessons.map(row => row.session_id));
    const group = field('select', '同步对象', { value: studentIds ? 'selected' : 'all', options: [{ value: 'all', label: '每课全班名单' }, { value: 'selected', label: '指定学生' }] });
    const pupils = multiField('选择学生', students, row => row.student_id, studentLabel, studentIds || []);
    const scopeNote = element('p', 'lac-summary');
    scope.append(chooseLessons.wrapper, group.wrapper, pupils.wrapper);
    const toolbar = element('div', 'la-actions');
    const align = action('对齐课程与课次', async () => {
        if (busy) return;
        busy = true; gate(); feedback(feedbackNode, '正在核对本课程的学期、课表与名单…');
        try {
            const result = await req.read(base + '/calendar/align', { method: 'POST', body: {} }, 100000);
            if (!req.closed) { clear(); feedback(feedbackNode, result.message || `已核对 ${result.session_count || 0} 个课次，请重新预览。`); }
        } catch (error) { if (!req.closed) feedback(feedbackNode, error.message || '课表对齐未完成。', true); }
        finally { if (!req.closed) { busy = false; gate(); } }
    }, 'ghost');
    const receiptButton = action('查看近期推送回执', () => recover(), 'ghost');
    const stop = action('停止后续课次', () => { stopped = true; stop.disabled = true; feedback(feedbackNode, '当前课次处理后停止。'); }, 'ghost');
    const preview = action('核对并预览', () => scan());
    const submit = action('推送已核对差异', () => send(), 'prominent');
    const create = action('定位并新建学校点名', () => send(true));
    footer.append(stop, preview, submit, create); toolbar.append(align, receiptButton);
    body.append(scope, scopeNote, toolbar, feedbackNode, list);
    let busy = false, dispatching = false, closed = false, stopped = false, loop = false, changed = false, pollReading = false, historyView = false;
    const entries = new Map(); let poll, readSequence = 0;
    const handle = openDialog({ title: '智慧课堂同步', body, footer, size: 'lg' }, { trigger, parentLayer, beforeClose: () => !dispatching,
        onClose: cleanup, onDestroy: cleanup });
    handle.root.classList.add('la-dialog');
    const dropdowns = enhanceDropdowns(body);
    function cleanup() { if (closed) return; closed = true; req.close(); poll?.dispose(); dropdowns.forEach(item => item.destroy()); if (changed) onChange?.(); }
    function ids() {
        if (fixedScope) return studentIds;
        return group.input.value === 'all' ? null : [...pupils.input.selectedOptions].map(opt => Number(opt.value));
    }
    function selectedLessons() { return fixedScope ? sessions : [...chooseLessons.input.selectedOptions].map(opt => lessonById.get(Number(opt.value))); }
    function gate() {
        const selected = ids(), size = selectedLessons().length;
        const locked = busy || pollReading;
        chooseLessons.input.disabled = group.input.disabled = pupils.input.disabled = locked;
        pupils.wrapper.hidden = group.input.value !== 'selected'; scope.hidden = fixedScope;
        scopeNote.textContent = historyView ? '本课程近期推送回执按原授权范围显示，包含其他学生或全班的请求。' : [fixedScope ? lessonLabel(sessions[0]) : `${size} 个课次`,
            selected === null ? '每课全班名单' : selected.length === 1 ? pupilNames.get(selected[0]) || '1 名学生' : `${selected.length} 名学生`].join(' · ');
        preview.disabled = locked || !size || (selected && !selected.length);
        const sendable = [...entries.values()].filter(canSend);
        submit.disabled = locked || !sendable.length; submit.textContent = sendable.length ? `推送 ${sendable.length} 个课次的差异` : '推送已核对差异';
        const creatable = [...entries.values()].filter(canCreate);
        const chosen = creatable.filter(entry => entry.authorizeCreate).length;
        create.hidden = !creatable.length; create.disabled = locked || !chosen;
        create.textContent = chosen ? `定位并新建 ${chosen} 个学校点名` : '定位并新建学校点名';
        align.disabled = receiptButton.disabled = locked; receiptButton.hidden = !store.read().some(row => lessonById.has(row.session));
        stop.hidden = !loop; stop.disabled = stopped;
        for (const button of list.querySelectorAll('button')) button.disabled = locked;
        for (const input of list.querySelectorAll('input')) input.disabled = locked;
    }
    function clear() { historyView = false; entries.clear(); list.replaceChildren(); }
    function canSend(entry) { return !entry.createIntent && ['ready', 'not_sent'].includes(entry.value?.state) && Boolean(entry.token); }
    function canCreate(entry) { return ids() === null && entry.createIntent && entry.token && ['create_available', 'not_sent'].includes(entry.value?.state); }
    function render(entry) {
        const { value, lesson } = entry, state = value.state;
        const first = element('div', 'lac-sync-line');
        first.append(element('strong', '', `${lesson.date} · ${lessonLabel(lesson)}`), badge(states[state] || '需核对', ['confirmed', 'school_only'].includes(state) ? 'success' : ['ready', 'queued', 'running'].includes(state) ? 'info' : 'neutral'));
        const content = [first, element('p', '', value.message || '')];
        if (value.counts) {
            const counts = Object.entries(value.counts).filter(([, n]) => Number(n) > 0).map(([k, n]) => (rowStates[k] || states[k] || k) + ' ' + n);
            if (counts.length) content.push(element('small', '', counts.join(' · ')));
            if (value.counts.protected) content.push(element('small', '', '需人工更正时，在本课点名中打开该生详情，核对学校现值后选择“保存并推送”。'));
        }
        if (value.rows?.length) {
            const details = element('details', 'lq-collapsible'), title = element('summary', 'lq-collapsible__summary', `查看 ${value.rows.length} 人的比对`);
            const rows = element('div', 'lq-collapsible__content lac-differences'); details.append(title, rows);
            details.addEventListener('toggle', () => {
                if (!details.open || rows.childElementCount) return;
                for (const row of value.rows) {
                    const name = pupilNames.get(Number(row.student_id)) || row.student_number || '未匹配学生';
                    const reason = row.state === 'needs_teacher_action' ? '拟推送为' + remoteText(row.target_status) : row.reason || rowStates[row.state] || '待核对';
                    rows.append(element('p', '', `${name}　 本地 ${statusText(row.local_status)} / 学校 ${remoteText(row.remote_status)}　·　${reason}`));
                }
            }); content.push(details);
        }
        if (state === 'alignment_required') content.push(action('核对此课对齐', event => review(entry, event.currentTarget)));
        if (canCreate(entry)) {
            const consent = field('checkbox', '为本课全班新建学校点名', { checked: Boolean(entry.authorizeCreate) });
            consent.input.addEventListener('change', () => { entry.authorizeCreate = consent.input.checked; gate(); });
            content.push(consent.wrapper);
        }
        if (state === 'school_missing' && !ids() || value.counts?.protected) content.push(action('查看本课点名设置', async event => {
            const button = event.currentTarget; button.disabled = true;
            try {
                const { openLoginAttendance } = await import('./login_attendance.js');
                if (!closed && button.isConnected) openLoginAttendance({ classOfferingId: Number(base.split('/')[3]), session: { id: lesson.session_id, title: lesson.title, session_date: lesson.date },
                    studentId: ids()?.length === 1 ? ids()[0] : null, userRole: 'teacher', trigger: button });
            } catch (error) { if (!closed) feedback(feedbackNode, error.message, true); }
            finally { if (button.isConnected) button.disabled = false; }
        }, 'ghost'));
        if (value.historical && state === 'outcome_unknown') content.push(element('small', '', '原任务已被替代，可重新预览本课的当前状态。'));
        if (!value.historical && pendingStates.has(state) && entry.key) content.push(action('查询此次回执', () => readOne(entry), 'ghost'));
        if (!value.historical && state === 'outcome_unknown' && entry.key) content.push(action('回读学校', () => readOne(entry, true)));
        entry.node.replaceChildren(...content);
    }
    function newEntry(lesson, key = null) {
        const row = { lesson, key, token: null, lastRead: 0, node: element('article', 'lac-sync-item') };
        entries.set(key || String(lesson.session_id), row); list.append(row.node); return row;
    }
    const endpoint = row => `${base}/sessions/${row.lesson.session_id}/external`;
    async function check(entry, selected) {
        entry.value = await req.read(endpoint(entry) + '/calendar-preview', { method: 'POST', body: { student_ids: selected } }, 100000);
        entry.key = null; entry.token = entry.value.preview_token;
        entry.createIntent = entry.value.state === 'create_available' && selected === null;
        entry.authorizeCreate = false;
        changed ||= ['school_only', 'ready', 'confirmed', 'partial', 'local_pending', 'blocked'].includes(entry.value.state);
    }
    async function review(entry, button) {
        if (busy || closed) return;
        busy = true; gate();
        try {
            const base = endpoint(entry), post = (path, body) => req.read(base + path, { method: 'POST', body }, 100000);
            const result = await post('/match', {});
            if (closed) return;
            const after = async () => {
                if (closed) return;
                busy = true; gate();
                try { await check(entry, ids()); if (!closed) render(entry); }
                catch (error) { if (!closed) feedback(feedbackNode, error.message, true); }
                finally { if (!closed) { busy = false; gate(); } }
            };
            if (result.binding?.state === 'matched') await after();
            else openAttendanceAlignment({ value: result, trigger: button, parentLayer: handle, confirm: payload => post('/confirm', payload), realign: () => post('/match', {}), onClose: after });
        } catch (error) { if (!closed) feedback(feedbackNode, error.message || '对齐暂未完成。', true); }
        finally { if (!closed) { busy = false; gate(); } }
    }
    async function scan() {
        if (busy || pollReading) return;
        const scope = selectedLessons(), selected = ids(); if (!scope.length || (selected && !selected.length)) return;
        clear(); busy = loop = true; stopped = false; gate();
        let index = 0;
        for (const lesson of scope) {
            if (closed || stopped) break;
            feedback(feedbackNode, `正在核对 ${++index} / ${scope.length} 课次…`);
            const entry = newEntry(lesson);
            try { await check(entry, selected); }
            catch (error) {
                entry.value = { state: 'blocked', message: error.message || '预览未完成，请重新核对。' };
                if ([401, 403, 429].includes(error.status)) stopped = true;
            }
            if (!closed) render(entry);
        }
        if (!closed) {
            const ready = [...entries.values()].filter(canSend).length, creatable = [...entries.values()].filter(canCreate).length;
            feedback(feedbackNode, `${stopped ? '已停止，' : ''}已核对 ${index} 个课次，${ready} 个课次有可推送差异。` + (creatable ? `${creatable} 个学校点名待新建，可逐课勾选。` : ''));
            busy = loop = false; gate();
        }
    }
    async function createLocation(targets) {
        const labels = targets.map(({ lesson }) => lesson.date + ' · ' + lessonLabel(lesson));
        const approved = await confirm({ title: `授权新建 ${targets.length} 个全班点名？`,
            message: `将用本次浏览器位置在智慧课堂为以下课次新建全班点名，并推送本地有效记录。\n${labels.join('\n')}`,
            confirmLabel: '定位并授权新建' }, { parentLayer: handle, trigger: create });
        if (!approved || closed) return null;
        if (!navigator.geolocation) throw new Error('当前浏览器不支持定位。已保留所选课次。');
        const fix = await new Promise((resolve, reject) => navigator.geolocation.getCurrentPosition(resolve,
            () => reject(new Error('未取得定位。允许定位后可重试，所选课次已保留。')), { enableHighAccuracy: true, maximumAge: 0, timeout: 12000 }));
        return { latitude: fix.coords.latitude, longitude: fix.coords.longitude, captured_at_ms: fix.timestamp };
    }
    async function send(creating = false) {
        if (busy || pollReading) return;
        const targets = [...entries.values()].filter(entry => creating ? canCreate(entry) && entry.authorizeCreate : canSend(entry)); if (!targets.length) return;
        busy = dispatching = loop = true; stopped = false; gate();
        let index = 0, location = null, expired = false;
        if (creating) {
            try { location = await createLocation(targets); }
            catch (error) { if (!closed) feedback(feedbackNode, error.message || '定位未完成，请重试。', true); }
            if (closed || !location) { busy = dispatching = loop = false; if (!closed) gate(); return; }
        }
        for (const entry of targets) {
            if (closed || stopped) break;
            if (creating && Date.now() - location.captured_at_ms >= 300000) { expired = true; break; }
            feedback(feedbackNode, `正在提交 ${++index} / ${targets.length} 课次…`);
            entry.key ||= crypto.randomUUID(); store.remember(entry.lesson.session_id, entry.key);
            try {
                const payload = { preview_token: entry.token, idempotency_key: entry.key };
                if (creating) Object.assign(payload, { authorize_create: true, location });
                entry.value = await req.read(endpoint(entry) + (creating ? '/calendar-create' : '/calendar-sync'), { method: 'POST', body: payload }, 100000);
                changed = true;
            } catch (error) {
                if (!error.status || error.status >= 500) entry.value = { state: 'outcome_unknown', message: '送达情况待确认，请查询此次回执。' };
                else entry.value = { state: 'blocked', message: error.message || '快照已变化，请重新核对。' };
            }
            if (!closed) render(entry);
        }
        if (!closed) {
            busy = dispatching = loop = false; gate();
            feedback(feedbackNode, `${expired ? '定位已过期，剩余课次须重新定位。' : stopped ? '已停止后续课次。' : ''}已处理 ${index} 项提交。各课次以回执为准。`);
        }
    }
    async function receipt(entry, readSchool = false) {
        entry.lastRead = ++readSequence;
        try {
            const url = endpoint(entry) + '/calendar-sync/' + encodeURIComponent(entry.key);
            const result = readSchool ? await req.read(url + '/readback', { method: 'POST', body: {} }, 100000) : await req.read(url);
            if (closed) return;
            entry.value = result; changed = true;
        } catch (error) {
            if (closed) return;
            if (error.status === 404 && entry.token) entry.value = { state: 'not_sent', message: '尚无此请求的收据，可使用同一请求号重试。' };
            else if (error.status === 404) entry.value = { state: 'no_receipt', message: '未找到这次推送回执，请重新核对两端状态。' };
            else entry.value = { state: 'outcome_unknown', message: error.message || '暂未能查询到收据。' };
        }
        render(entry);
    }
    async function readOne(entry, readSchool = false) {
        if (busy || pollReading) return;
        busy = true; gate();
        try { await receipt(entry, readSchool); } finally { if (!closed) { busy = false; gate(); } }
    }
    async function recover() {
        if (busy || pollReading) return;
        const receipts = store.read().filter(row => lessonById.has(row.session));
        busy = loop = true; stopped = false; clear(); historyView = true; gate();
        try {
            for (const item of receipts.reverse()) {
                if (closed || stopped) break;
                await receipt(newEntry(lessonById.get(item.session), item.key));
            }
            if (!closed) feedback(feedbackNode, '已查询近期推送回执。');
        } finally { if (!closed) { busy = loop = false; gate(); } }
    }
    scope.addEventListener('change', () => { clear(); feedback(feedbackNode, ''); gate(); });
    poll = createAmbientPoll(async () => {
        const pending = [...entries.values()].filter(entry => entry.key && !entry.value?.historical && pendingStates.has(entry.value?.state)).sort((a, b) => a.lastRead - b.lastRead);
        if (!pending.length) return;
        pollReading = true; gate();
        try { for (const entry of pending.slice(0, 3)) { if (closed) break; await receipt(entry); } }
        finally { if (!closed) { pollReading = false; gate(); } }
    }, { intervalMs: 12000, timeoutMs: 85000, active: () => !closed && !busy && !pollReading && !document.hidden && getLayerSystem(document).top() === handle });
    poll.start(12000); gate();
    if (fixedScope && sessions.length === 1) void scan();
    return handle;
}
