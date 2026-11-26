import { openDialog } from './lq/dialogs.js';
import { getLayerSystem } from './lq/layer.js';
import { enhanceDropdowns } from './lq/dropdown.js';
import { createAmbientPoll } from './ambient_poll.js';
import { openAttendanceAlignment } from './login_attendance_alignment.js';
import { action, badge, calendarRequests, element, feedback, field, lessonLabel, statusText, studentLabel } from './attendance_calendar_controls.js';

const states = {
    imported: '本地已登记', empty: '未开始点名', school_missing: '学校未点名', location_required: '需要定位', local_pending: '等待结算',
    alignment_required: '待对齐', blocked: '暂不能同步', confirmed: '两端一致', partial: '部分需核对', conflict: '需核对',
    queued: '已排队', running: '处理中', reading: '正在回读', comparing: '正在比对', outcome_unknown: '待查回执',
    pending: '已排队', superseded: '版本已更新', failed: '处理失败', not_sent: '可重试原请求', no_receipt: '未找到回执',
    paused: '已暂停', needs_teacher_reauth: '需要重新登录', needs_location: '需要定位',
};
const rowStates = { imported: '已更新本地', needs_teacher_action: '待推送学校', confirmed: '已一致', protected: '保留学校记录', blocked: '已跳过', conflict: '需核对', not_enrolled: '学校无此人', no_local_record: '无本地记录', unmapped: '未匹配' };
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
    function write(rows) {
        if (!key) return;
        try { sessionStorage.setItem(key, JSON.stringify(rows)); } catch { /* The active dialog still owns this receipt. */ }
    }
    return { read, remember(session, id) { write([...read().filter(item => item.key !== id), { session, key: id }].slice(-96)); },
        forget(id) { write(read().filter(item => item.key !== id)); } };
}
function multiField(label, rows, value, labelOf, selected) {
    const { wrapper, input } = field('select', label, { value: '', searchable: true, options: [{ value: '', label: '请选择' },
        ...rows.map(row => ({ value: String(value(row)), label: labelOf(row) }))] });
    input.multiple = true; input.options[0].remove();
    const ids = new Set(selected.map(String));
    for (const option of input.options) option.selected = ids.has(option.value);
    return { wrapper, input };
}

export function openCalendarSync({ base, students = [], sessions = [], today, studentIds = null, fixedScope = false, initialReceipt, trigger, parentLayer, onLocalChanged, onChange }) {
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
    const receiptButton = action('近期同步回执', () => recover(), 'ghost');
    const stop = action('停止后续课次', () => {
        stopped = true; stop.disabled = true; feedback(feedbackNode, '当前课次处理后停止。');
    }, 'ghost');
    const submit = action('同步所选课次', () => synchronize(), 'prominent');
    toolbar.append(receiptButton); footer.append(stop, submit);
    body.append(scope, scopeNote, toolbar, feedbackNode, list);
    const entries = new Map(), notifiedLocal = new Set();
    let busy = false, closed = false, stopped = false, loop = false, changed = false, historyView = false, readSequence = 0;
    let fix = null, locationError = '', posting = false, poll;
    const handle = openDialog({ title: '智慧课堂同步', body, footer, size: 'lg' }, { trigger, parentLayer,
        beforeClose: () => !posting && !loop, onClose: cleanup, onDestroy: cleanup });
    handle.root.classList.add('la-dialog');
    const dropdowns = enhanceDropdowns(body);
    function cleanup() {
        if (closed) return;
        closed = true; req.close(); poll?.dispose(); dropdowns.forEach(item => item.destroy());
        fix = null; if (changed) onChange?.();
    }
    function ids() {
        if (fixedScope) return studentIds;
        return group.input.value === 'all' ? null : [...pupils.input.selectedOptions].map(opt => Number(opt.value));
    }
    function selectedLessons() { return fixedScope ? sessions : [...chooseLessons.input.selectedOptions].map(opt => lessonById.get(Number(opt.value))); }
    function gate() {
        const selected = ids(), size = selectedLessons().length;
        chooseLessons.input.disabled = group.input.disabled = pupils.input.disabled = busy;
        pupils.wrapper.hidden = group.input.value !== 'selected'; scope.hidden = fixedScope;
        scopeNote.textContent = historyView ? '本课程近期回执按各次同步时的范围显示。' : [fixedScope ? lessonLabel(sessions[0]) : size + ' 个课次',
            selected === null ? '每课全班名单' : selected.length === 1 ? pupilNames.get(selected[0]) || '1 名学生' : selected.length + ' 名学生'].join(' · ');
        stop.hidden = !loop || size < 2; stop.disabled = stopped;
        submit.textContent = fixedScope ? '再次同步本课' : '同步所选课次';
        submit.disabled = busy || !size || (selected && !selected.length);
        receiptButton.disabled = busy; toolbar.hidden = !store.read().some(row => lessonById.has(row.session));
        for (const button of list.querySelectorAll('button')) button.disabled = busy;
    }
    function clear() { historyView = false; entries.clear(); list.replaceChildren(); }
    function acceptResult(entry, result) {
        entry.value = result;
        changed ||= Boolean(result.local_changed || result.receipt);
        if (fixedScope && !historyView && result.local_changed && !notifiedLocal.has(entry.key)) {
            notifiedLocal.add(entry.key); onLocalChanged?.();
        }
    }
    function makeEntry(lesson, key = null, selected) {
        const lookup = historyView ? key : lesson.session_id;
        const entry = { lesson, key: key || crypto.randomUUID(), selected, value: {}, lastRead: 0, node: element('article', 'lac-sync-item') };
        const previous = entries.get(lookup);
        if (previous) previous.node.replaceWith(entry.node); else list.append(entry.node);
        entries.set(lookup, entry);
        return entry;
    }
    const endpoint = entry => base + '/sessions/' + entry.lesson.session_id + '/external';
    function render(entry) {
        const { value, lesson } = entry, state = value.state;
        const first = element('div', 'lac-sync-line');
        const tone = ['confirmed', 'imported'].includes(state) ? 'success' : pendingStates.has(state) ? 'info' : ['partial', 'conflict', 'failed'].includes(state) ? 'warning' : 'neutral';
        first.append(element('strong', '', lesson.date + ' · ' + lessonLabel(lesson)), badge(states[state] || '需核对', tone));
        const content = [first];
        if (entry.expanded) content.push(element('small', '', '本次范围 · 本课全班'));
        if (value.message) content.push(element('p', '', value.message));
        const counts = Object.entries(value.counts || {}).filter(([, n]) => Number(n) > 0).map(([k, n]) => (rowStates[k] || states[k] || '待核对') + ' ' + n);
        if (counts.length) content.push(element('small', '', counts.join(' · ')));
        if (value.rows?.length) {
            const details = element('details', 'lq-collapsible'), title = element('summary', 'lq-collapsible__summary', '查看 ' + value.rows.length + ' 人的记录');
            const rows = element('div', 'lq-collapsible__content lac-differences'); details.append(title, rows);
            details.addEventListener('toggle', () => {
                if (!details.open || rows.childElementCount) return;
                for (const row of value.rows) {
                    const name = pupilNames.get(Number(row.student_id)) || studentLabel(row) || '未匹配学生';
                    const statuses = '本地 ' + statusText(row.local_status) + ' / 学校 ' + remoteText(row.remote_status);
                    const reason = row.reason || rowStates[row.state];
                    rows.append(element('p', '', [name, row.state === 'unconfirmed' ? '' : statuses, reason].filter(Boolean).join('　·　')));
                }
            }); content.push(details);
        }
        if (state === 'alignment_required') content.push(action('核对此课对齐', event => review(entry, event.currentTarget)));
        if (state === 'school_missing' && value.requires_full_roster && Array.isArray(entry.selected)) {
            content.push(action('同步本课全班', () => run(async () => {
                const full = makeEntry(lesson, null, null); full.expanded = true;
                await reconcile(full);
            })));
        }
        if (state === 'location_required') content.push(action('重新定位并同步本课', () => run(() => reconcile(entry))));
        if (state === 'not_sent' && entry.selected !== undefined) content.push(action('重试此次同步', () => run(() => reconcile(entry))));
        if (value.historical && state === 'outcome_unknown') content.push(element('small', '', '可重新同步本课，核对当前两端状态。'));
        if (!value.historical && pendingStates.has(state)) content.push(action('查询此次回执', () => run(() => receipt(entry))));
        if (!value.historical && state === 'outcome_unknown') content.push(action('回读学校', () => run(() => receipt(entry, true))));
        entry.node.replaceChildren(...content); gate();
    }
    async function run(task, batch = false) {
        if (busy || closed) return;
        busy = true; loop = batch; stopped = false; locationError = ''; gate();
        try { await task(); }
        catch (error) { if (!closed) feedback(feedbackNode, error.message || '同步未完成，请重试。', true); }
        finally { if (!closed) { busy = loop = false; gate(); } }
    }
    async function location() {
        if (fix && Date.now() - fix.captured_at_ms < 270000) return fix;
        if (locationError) throw new Error(locationError);
        try {
            if (!navigator.geolocation) throw new Error('当前浏览器不支持定位。');
            const position = await new Promise((resolve, reject) => navigator.geolocation.getCurrentPosition(resolve,
                () => reject(new Error('未取得浏览器定位。')), { enableHighAccuracy: true, maximumAge: 0, timeout: 12000 }));
            fix = { latitude: position.coords.latitude, longitude: position.coords.longitude, captured_at_ms: position.timestamp };
            return fix;
        } catch (error) {
            locationError = error.message + '允许定位后可继续为本课全班新建学校点名。'; throw new Error(locationError);
        }
    }
    async function reconcile(entry, initialResult) {
        const payload = { student_ids: entry.selected, idempotency_key: entry.key };
        const post = () => req.read(endpoint(entry) + '/calendar-reconcile', { method: 'POST', body: payload }, 100000);
        posting = true;
        store.remember(entry.lesson.session_id, entry.key);
        try {
            let result = initialResult || await post();
            if (closed) return;
            if (result.state === 'location_required' && entry.selected === null && !stopped) {
                entry.value = { ...result, message: '正在取得浏览器定位，以便为本课全班新建学校点名。' }; render(entry); gate();
                try { payload.location = await location(); }
                catch (error) { result = { ...result, message: error.message }; }
                if (closed) return;
                if (payload.location && !stopped) result = await post();
            }
            if (closed) return;
            acceptResult(entry, result);
            const hasReceipt = result.has_receipt || result.receipt;
            if (!hasReceipt && result.state !== 'outcome_unknown') store.forget(entry.key);
            if (!hasReceipt && result.state === 'outcome_unknown') { await receipt(entry); return; }
            if (hasReceipt && result.state === 'outcome_unknown' && !result.historical && !entry.triedReadback) {
                await receipt(entry, true); return;
            }
        } catch (error) {
            if (closed) return;
            if (!error.status || error.status >= 500) {
                changed = true; entry.value = { state: 'outcome_unknown', message: '正在查询原请求回执…' }; render(entry);
                await receipt(entry); return;
            }
            store.forget(entry.key);
            entry.value = { state: 'blocked', message: error.message || '此次同步未完成。' };
            if ([401, 403, 429].includes(error.status)) stopped = true;
        } finally { posting = false; }
        render(entry);
    }
    async function review(entry, button) {
        await run(async () => {
            const post = (path, body) => req.read(endpoint(entry) + path, { method: 'POST', body }, 100000);
            const result = await post('/match', {});
            if (closed) return;
            if (result.binding?.state === 'matched') { await reconcile(entry); return; }
            let matched = false;
            const update = async (path, payload) => {
                const value = await post(path, payload); matched = value.binding?.state === 'matched'; return value;
            };
            openAttendanceAlignment({ value: result, trigger: button, parentLayer: handle, confirm: payload => update('/confirm', payload),
                realign: () => update('/match', {}), onClose: () => { if (matched && !closed) void run(() => reconcile(entry)); } });
        });
    }
    function synchronize() {
        const lessons = selectedLessons(), selected = ids();
        if (!lessons.length || (selected && !selected.length)) return;
        return run(async () => {
            if (historyView) clear();
            let count = 0;
            for (const lesson of lessons) {
                if (closed || stopped) break;
                feedback(feedbackNode, '正在同步 ' + (++count) + ' / ' + lessons.length + ' 课次…');
                const prior = entries.get(lesson.session_id);
                if (prior && !prior.value.historical && pendingStates.has(prior.value.state)) await receipt(prior);
                else if (prior?.value.state === 'not_sent') await reconcile(prior);
                else await reconcile(makeEntry(lesson, null, selected));
            }
            if (closed) return;
            const pending = [...entries.values()].filter(row => !row.value.historical && pendingStates.has(row.value.state)).length;
            feedback(feedbackNode, (stopped ? '已停止后续课次。' : '') + (lessons.length > 1 ? '已处理 ' + count + ' 个课次。' : '') + (pending ? pending + ' 个课次正在等待学校回执。' : ''));
        }, true);
    }
    async function receipt(entry, readSchool = false) {
        entry.lastRead = ++readSequence;
        if (readSchool) entry.triedReadback = true;
        const url = endpoint(entry) + '/calendar-sync/' + encodeURIComponent(entry.key);
        try {
            let result = readSchool ? await req.read(url + '/readback', { method: 'POST', body: {} }, 100000) : await req.read(url);
            if (closed) return;
            if (!readSchool && result.state === 'outcome_unknown' && !result.historical && !entry.triedReadback) {
                entry.triedReadback = true;
                result = await req.read(url + '/readback', { method: 'POST', body: {} }, 100000);
            }
            if (closed) return;
            acceptResult(entry, result); changed = true;
        } catch (error) {
            if (closed) return;
            if (error.status === 404) {
                entry.value = entry.selected !== undefined ? { state: 'not_sent', message: '未查到后台任务，可按原范围重试此次同步。' }
                    : { state: 'no_receipt', message: '未查到后台任务，可重新选择范围同步。' };
                store.forget(entry.key);
            } else entry.value = { state: 'outcome_unknown', message: error.message || '回执暂不可用，可稍后查询。' };
        }
        render(entry);
    }
    function recover() {
        const receipts = store.read().filter(row => lessonById.has(row.session));
        return run(async () => {
            clear(); historyView = true; gate();
            for (const item of receipts.reverse()) {
                if (closed || stopped) break;
                await receipt(makeEntry(lessonById.get(item.session), item.key));
            }
            if (!closed) feedback(feedbackNode, '已查询近期同步回执。');
        }, true);
    }
    scope.addEventListener('change', () => { clear(); feedback(feedbackNode, ''); gate(); });
    poll = createAmbientPoll(async () => {
        const pending = [...entries.values()].filter(entry => !entry.value.historical && pendingStates.has(entry.value.state)).sort((a, b) => a.lastRead - b.lastRead);
        if (pending.length) await run(async () => { for (const entry of pending.slice(0, 3)) { if (closed) break; await receipt(entry); } });
    }, { intervalMs: 12000, timeoutMs: 85000, active: () => !closed && !busy && !document.hidden && getLayerSystem(document).top() === handle });
    poll.start(12000); gate();
    if (fixedScope && sessions.length === 1) {
        if (initialReceipt) void run(() => reconcile(makeEntry(sessions[0], initialReceipt.key, studentIds), initialReceipt.result));
        else void synchronize();
    }
    return handle;
}
