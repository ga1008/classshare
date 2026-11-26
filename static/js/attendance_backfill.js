import { openDialog, confirm } from './lq/dialogs.js';
import { getLayerSystem } from './lq/layer.js';
import { action, calendarRequests, element, feedback, field, lessonLabel, stateOptions, statusText, studentLabel } from './attendance_calendar_controls.js';
import { openCalendarSync } from './attendance_calendar_sync.js';

const time = ms => new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23', timeZone: 'Asia/Shanghai',
}).format(ms);

function evidenceLabel(row) {
    if (!row.eligible) return '已有记录 · ' + statusText(row.effective_status || row.status);
    const fact = row.evidence;
    const parts = [];
    if (fact.access_count) parts.push(`课内认证访问 ${fact.access_count} 次（${time(fact.first_ms)}–${time(fact.last_ms)}）`);
    if (fact.receipt_count) parts.push(`本课学习回执 ${fact.receipt_count} 批`);
    if (!parts.length) {
        parts.push(fact.other_network ? '课内访问未匹配教室网段' : '未查到课内操作');
        if (fact.before_ms) parts.push('课前 ' + time(fact.before_ms) + ' 有访问');
        if (fact.after_ms) parts.push('课后 ' + time(fact.after_ms) + ' 有访问');
    }
    return parts.join(' · ');
}

export function openAttendanceBackfill({ base, sessionId, trigger, parentLayer, onChange }) {
    const req = calendarRequests(), choices = new Map();
    const body = element('div', 'la-workspace'), notice = element('p', 'la-notice');
    const info = element('p', 'lac-summary'), count = element('p', 'lac-summary');
    const list = element('div', 'lac-records'), nav = element('div', 'la-actions'), footer = element('div', 'la-actions');
    const reason = field('textarea', '人工判定依据', { rows: 2, maxLength: 1000, placeholder: '例如：已核对本课请假单' });
    const search = field('input', '查找学生', { type: 'search', placeholder: '姓名或学号' });
    const reload = action('重新预览', () => load());
    const local = action('仅保存到本地', () => save(false));
    const push = action('补录并同步智慧课堂', () => save(true), 'prominent');
    const done = action('完成', () => getLayerSystem(document).close(handle, 'button'));
    footer.append(reload, local, push, done);
    body.append(info, notice, count, search.wrapper, list, nav, reason.wrapper);
    const url = `${base}/calendar/sessions/${sessionId}/backfill`;
    let view, busy = false, posting = false, stale = false, saved = false, key, lastMode, page = 0;
    function cleanup() { if (!req.closed) { req.close(); if (saved) onChange?.(); } }
    const handle = openDialog({ title: '按操作记录补录点名', body, footer, size: 'lg' }, { trigger, parentLayer,
        beforeClose: async () => !posting && (saved || (!choices.size && !reason.input.value.trim()) ||
            await confirm({ title: '放弃补录草稿？', message: '本次人工选择和依据将清除。', confirmLabel: '放弃草稿', danger: true }, { parentLayer: handle })),
        onClose: cleanup, onDestroy: cleanup });
    handle.root.classList.add('la-dialog');

    function gate() {
        const records = view?.records || [];
        const automatic = records.filter(row => row.suggested && !choices.has(row.student_id)).length;
        const manual = [...choices.values()].filter(status => status !== 'PENDING').length;
        const pending = records.filter(row => row.eligible).length - automatic - manual;
        count.textContent = view && !saved ? `按记录出勤 ${automatic} 人 · 人工判定 ${manual} 人 · 待确认 ${pending} 人` : '';
        reason.wrapper.hidden = saved || (!manual && !reason.input.value);
        search.wrapper.hidden = !view || saved;
        reason.input.disabled = search.input.disabled = busy;
        reload.hidden = saved || (!stale && Boolean(view));
        reload.disabled = busy;
        local.hidden = push.hidden = saved;
        done.hidden = !saved;
        local.disabled = push.disabled = busy || stale || !view || !(automatic + manual);
        for (const input of list.querySelectorAll('select')) input.disabled = busy || saved;
        body.setAttribute('aria-busy', String(busy));
    }
    function render() {
        list.replaceChildren(); nav.replaceChildren();
        if (!view || saved) { gate(); return; }
        const query = search.input.value.trim().toLowerCase();
        const rows = view.records.filter(row => studentLabel(row).toLowerCase().includes(query));
        const pages = Math.max(1, Math.ceil(rows.length / 20));
        page = Math.min(page, pages - 1);
        for (const row of rows.slice(page * 20, page * 20 + 20)) {
            const item = element('div', 'lac-record');
            const name = studentLabel(row);
            if (row.eligible) {
                const options = [{ value: '', label: row.suggested ? '按记录记出勤' : '保留待确认' },
                    ...(row.suggested ? [{ value: 'PENDING', label: '保留待确认' }] : []), ...stateOptions];
                const pick = field('select', name, { options, value: choices.get(row.student_id) || '' });
                pick.input.addEventListener('change', () => {
                    if (pick.input.value) choices.set(row.student_id, pick.input.value); else choices.delete(row.student_id);
                    key = null; gate();
                });
                item.append(pick.wrapper);
            } else item.append(element('strong', '', name));
            item.append(element('small', '', evidenceLabel(row))); list.append(item);
        }
        if (!rows.length) list.append(element('p', '', '没有匹配的学生。'));
        if (pages > 1) {
            const prev = action('上一页', () => { page--; render(); }, 'ghost');
            const next = action('下一页', () => { page++; render(); }, 'ghost');
            prev.disabled = page === 0 || busy; next.disabled = page + 1 === pages || busy;
            nav.append(prev, element('span', '', `${page + 1} / ${pages}`), next);
        }
        gate();
    }
    async function load() {
        if (busy) return;
        busy = true; gate(); feedback(notice, '正在按课表时段核对服务器记录…');
        try {
            const data = await req.read(url);
            if (req.closed) return;
            view = data; key = null; stale = false; page = 0;
            const eligible = new Set(view.records.filter(row => row.eligible).map(row => row.student_id));
            for (const sid of choices.keys()) if (!eligible.has(sid)) choices.delete(sid);
            const rank = row => !row.eligible ? 2 : row.suggested ? 1 : 0;
            view.records.sort((a, b) => rank(a) - rank(b));
            info.textContent = `${data.session.date} · ${lessonLabel(data.session)} · ${time(data.window.starts_at_ms)}–${time(data.window.ends_at_ms)}（北京时间）`;
            feedback(notice, (data.network_required ? '按本课当前教室网段核对。' : '按本课当前不限网段规则核对。') +
                (choices.size ? '请复核保留的人工选择。' : '课前、课后访问仅供核对。'));
        } catch (error) { if (!req.closed) { stale = true; feedback(notice, error.message || '预览失败，请重试。', true); } }
        finally { if (!req.closed) { busy = false; render(); } }
    }
    async function save(sync) {
        if (busy || saved || stale || !view) return;
        const overrides = [...choices].map(([student_id, status]) => ({ student_id, status }));
        if (overrides.some(row => row.status !== 'PENDING') && !reason.input.value.trim()) {
            reason.input.setCustomValidity('请填写人工判定依据。'); reason.input.reportValidity(); return;
        }
        if (!key || lastMode !== sync) key = crypto.randomUUID();
        lastMode = sync; posting = busy = true; gate(); feedback(notice, '正在保存本课补录…');
        try {
            const result = await req.read(url, { method: 'POST', body: { revision: view.revision, idempotency_key: key,
                overrides, reason: reason.input.value.trim(), sync_school: sync } }, 110000);
            if (req.closed) return;
            saved = true; feedback(notice, `本地已保存：按记录出勤 ${result.automatic} 人，人工判定 ${result.manual} 人，仍待确认 ${result.pending} 人。`);
            gate(); done.focus();
            if (result.school) openCalendarSync({ base, students: view.records, sessions: [view.session], fixedScope: true,
                trigger: done, parentLayer: handle, initialReceipt: { key: result.school_key, result: result.school } });
        } catch (error) {
            if (!req.closed) { stale = error.status === 409; feedback(notice, error.message || '未收到保存回执，可重试原提交。', true); }
        } finally { if (!req.closed) { posting = busy = false; render(); } }
    }
    search.input.addEventListener('input', () => { page = 0; render(); });
    reason.input.addEventListener('input', () => { key = null; reason.input.setCustomValidity(''); gate(); });
    void load(); return handle;
}
