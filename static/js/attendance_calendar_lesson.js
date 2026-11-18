import { openDialog, confirm } from './lq/dialogs.js';
import { enhanceDropdowns } from './lq/dropdown.js';
import { openCalendarSync } from './attendance_calendar_sync.js';
import { action, badge, calendarRequests, element, feedback, field, lessonLabel, sourceText, stateOptions, statusText, studentLabel } from './attendance_calendar_controls.js';

export function openCalendarLesson({ base, date, lessons, calendarNote, selected, students, trigger, parentLayer, onClose }) {
    const req = calendarRequests(), draft = new Map(), marked = new Set();
    const body = element('div', 'la-workspace lac-editor'), footer = element('div', 'la-actions');
    const lessonField = field('select', '当日课次', { value: String(lessons[0].session_id),
        options: lessons.map(row => ({ value: String(row.session_id), label: lessonLabel(row) })) });
    const info = element('p', 'lac-summary'), notice = element('p', 'la-notice');
    const edit = element('div', 'lac-edit-content'), actions = element('div', 'la-actions');
    const search = field('input', '查找学生', { type: 'search', placeholder: '姓名或学号' }); search.wrapper.hidden = Boolean(selected);
    const markAll = action('选择全部应到学生', () => {
        const editable = records.filter(row => row.editable !== false && row.expected !== false);
        if (marked.size && editable.every(row => marked.has(row.student_id))) marked.clear();
        else editable.forEach(row => marked.add(row.student_id));
        paintRows();
    }, 'ghost');
    const markCount = element('span'), table = element('div', 'lac-records'), pager = element('div', 'la-actions');
    const states = element('div', 'la-actions');
    for (const option of stateOptions) states.append(action('设为' + option.label, () => {
        for (const row of records) {
            if (!marked.has(row.student_id) || row.editable === false) continue;
            if (row.status === option.value && row.manual_confirmed && row.effective_status === option.value) draft.delete(row.student_id);
            else draft.set(row.student_id, option.value);
        }
        writeKey = null; feedback(notice, ''); paintRows();
    }));
    const reason = field('textarea', '更正理由', { rows: 2, required: true, maxLength: 1000, placeholder: '例如：核对课堂签到表' });
    const draftCount = element('strong', 'lac-summary');
    const undo = action('撤销未保存更改', () => { draft.clear(); writeKey = null; reason.input.value = ''; paintRows(); }, 'ghost');
    const draftBar = element('div', 'la-actions'); draftBar.append(draftCount, undo);
    actions.append(markAll, markCount);
    if (selected) edit.append(table, states, draftBar, reason.wrapper);
    else edit.append(search.wrapper, actions, states, draftBar, reason.wrapper, table, pager);
    const retry = action('读取最新名单', () => load(true)); retry.hidden = true;
    const push = action('本课同步', event => syncLesson(event.currentTarget, marked.size ? [...marked] : selected ? [selected] : null));
    const save = action('保存到本地', () => saveDraft(false));
    const savePush = action('保存并同步', () => saveDraft(true), 'prominent');
    footer.append(push, save, savePush);
    body.append(lessonField.wrapper, info, notice, retry, edit);
    let busy = true, saving = false, conflict = false, page = 0, records = [], lesson = lessons[0], changed = false, writeKey = null, loadEpoch = 0;
    function dirty() { return Boolean(draft.size || reason.input.value.trim()); }
    const handle = openDialog({ title: date + ' 出勤记录', body, footer, size: 'lg' }, { trigger, parentLayer,
        beforeClose: async () => !saving && (!dirty() || await confirm({ title: '放弃未保存的更正？', message: '本次选择的状态和更正理由将清除。', confirmLabel: '放弃更正', danger: true }, { parentLayer: handle })),
        onClose: cleanup, onDestroy: cleanup });
    handle.root.classList.add('la-dialog');
    const dropdowns = enhanceDropdowns(body);
    function cleanup() { req.close(); dropdowns.forEach(item => item.destroy()); onClose?.(changed); }
    function syncLesson(button, ids) {
        if (busy || dirty()) return;
        const refreshLesson = () => { changed = true; void load(true); };
        openCalendarSync({ base, students, sessions: [lesson], studentIds: ids,
            fixedScope: true, trigger: button, parentLayer: handle, onLocalChanged: refreshLesson, onChange: refreshLesson });
    }
    function gate() {
        edit.setAttribute('aria-busy', String(busy));
        lessonField.input.disabled = busy;
        reason.input.disabled = busy; search.input.disabled = busy;
        for (const node of states.children) node.disabled = busy || !lesson.can_edit || !marked.size;
        for (const node of table.querySelectorAll('input')) node.disabled = busy;
        markAll.disabled = busy || !lesson.can_edit; undo.disabled = busy;
        retry.disabled = busy; push.disabled = busy || dirty(); save.disabled = savePush.disabled = busy || conflict || !lesson.can_edit || !draft.size;
        draftBar.hidden = reason.wrapper.hidden = !draft.size && !reason.input.value;
    }
    function paintRows() {
        const query = search.input.value.trim().toLowerCase();
        const matches = records.filter(row => !query || studentLabel(row).toLowerCase().includes(query));
        page = Math.min(page, Math.max(0, Math.ceil(matches.length / 30) - 1));
        const rows = document.createDocumentFragment();
        for (const row of matches.slice(page * 30, page * 30 + 30)) {
            const line = element('div', 'lac-record'); const name = studentLabel(row);
            const editable = lesson.can_edit && row.editable !== false;
            if (!selected && editable) {
                const check = field('checkbox', name, { checked: marked.has(row.student_id) });
                check.input.addEventListener('change', () => {
                    if (check.input.checked) marked.add(row.student_id); else marked.delete(row.student_id);
                    updateSelection();
                }); line.append(check.wrapper);
            } else line.append(element('strong', '', name));
            const statuses = element('div', 'la-actions');
            const effective = row.effective_status;
            const fact = effective ? [sourceText(row.source), statusText(effective)].filter(Boolean).join(' · ') : row.expected === false ? '免考勤' : lesson.round_id ? '本地 · ' + statusText(row.status) : '未点名';
            statuses.append(badge(fact));
            if (row.source && row.source !== 'local' && lesson.round_id && row.status !== effective) statuses.append(badge('本地 · ' + statusText(row.status)));
            if (draft.has(row.student_id)) statuses.append(badge('拟改 · ' + statusText(draft.get(row.student_id)), 'info'));
            line.append(statuses); rows.append(line);
        }
        if (!matches.length) rows.append(element('p', '', query ? '没有匹配的学生。' : '本课暂无名单。'));
        table.replaceChildren(rows); pager.replaceChildren(); pager.hidden = matches.length <= 30;
        if (!pager.hidden) {
            const back = action('上一页', () => { page--; paintRows(); }, 'ghost'), next = action('下一页', () => { page++; paintRows(); }, 'ghost');
            back.disabled = page === 0; next.disabled = (page + 1) * 30 >= matches.length;
            pager.append(back, element('span', '', `${page + 1} / ${Math.ceil(matches.length / 30)}`), next);
        }
        updateSelection();
    }
    function updateSelection() {
        const canEdit = Boolean(lesson.can_edit);
        markAll.hidden = !canEdit || Boolean(selected); actions.hidden = !canEdit || Boolean(selected); states.hidden = !canEdit;
        const editable = records.filter(row => row.editable !== false && row.expected !== false);
        markAll.textContent = marked.size && editable.every(row => marked.has(row.student_id)) ? '清除选择' : '选择全部应到学生';
        markCount.textContent = `已选 ${marked.size} 人`; draftCount.textContent = `${draft.size} 人待保存`;
        push.textContent = selected ? '同步本课个人记录' : marked.size ? `同步所选 ${marked.size} 人` : '同步本课全班';
        gate();
    }
    async function load(keepDraft = false) {
        if (req.closed) return;
        const epoch = ++loadEpoch;
        busy = true; gate(); edit.hidden = !keepDraft; retry.hidden = true; feedback(notice, '正在读取课次名单…');
        try {
            const data = await req.read(`${base}/calendar/sessions/${lesson.session_id}${selected ? '?student_id=' + selected : ''}`);
            if (req.closed || epoch !== loadEpoch) return;
            const previous = new Map(records.map(row => [row.student_id, row]));
            const moved = Boolean(lesson.slot_revision && data.session.slot_revision && lesson.slot_revision !== data.session.slot_revision);
            lesson = data.session; records = data.records || [];
            handle.root.querySelector('.lq-dialog__head h2').textContent = lesson.date + ' 出勤记录';
            const currentOption = [...lessonField.input.options].find(option => Number(option.value) === lesson.session_id);
            if (currentOption) { currentOption.textContent = (lesson.date !== date ? lesson.date + ' · ' : '') + lessonLabel(lesson); dropdowns.forEach(item => item.refresh()); }
            if (moved || lesson.date !== date) changed = true;
            const eligible = new Set(records.filter(row => row.editable !== false).map(row => row.student_id));
            for (const id of marked) if (!eligible.has(id)) marked.delete(id);
            if (selected && eligible.has(selected)) marked.add(selected);
            info.textContent = [lessonLabel(lesson), lesson.round_id ? '本地已点名' : lesson.school_cached ? '学校已有记录，可同步到本地' : '本地未开始点名', lesson.date === date && calendarNote, lesson.schedule_status_label, lesson.schedule_note, lesson.edit_note].filter(Boolean).join(' · ');
            const changes = keepDraft ? [...draft.keys()].filter(id => previous.get(id)?.revision !== records.find(row => row.student_id === id)?.revision || !eligible.has(id)) : [];
            for (const id of [...draft.keys()]) if (!eligible.has(id)) draft.delete(id);
            conflict = false; writeKey = null; edit.hidden = false; paintRows();
            const update = moved ? `课次已调整为 ${lesson.date} · ${lessonLabel(lesson)}。请核对该课次与拟改值后保存。` : '';
            feedback(notice, update || (keepDraft && draft.size ? (changes.length ? `${changes.length} 人的记录或资格已变化。请核对最新状态与拟改值，再保存。` : '已读取最新名单，请核对拟改值后保存。') : ''));
        } catch (error) { if (!req.closed && epoch === loadEpoch) { conflict = true; edit.hidden = !keepDraft; retry.hidden = false; feedback(notice, error.message || '读取名单失败', true); } }
        finally { if (!req.closed && epoch === loadEpoch) { busy = false; gate(); } }
    }
    async function saveDraft(andPush) {
        if (busy || conflict || !draft.size) return;
        reason.input.setCustomValidity(reason.input.value.trim() ? '' : '请填写更正理由。');
        if (!reason.input.reportValidity()) return;
        const rows = records.filter(row => draft.has(row.student_id)).map(row => ({ student_id: row.student_id, revision: row.revision, status: draft.get(row.student_id) }));
        if (!rows.length) return;
        saving = busy = true; writeKey ||= crypto.randomUUID(); gate(); feedback(notice, '正在保存本地记录…');
        try {
            await req.read(`${base}/calendar/sessions/${lesson.session_id}`, { method: 'PATCH', body: {
                round_id: lesson.round_id || null, expected_slot_revision: lesson.slot_revision,
                reason: reason.input.value.trim(), records: rows, idempotency_key: writeKey,
            } });
            if (req.closed) return;
            changed = true; draft.clear(); reason.input.value = ''; writeKey = null;
            await load();
            if (req.closed) return;
            if (!conflict) feedback(notice, `已保存 ${rows.length} 人的本地记录。`);
            if (andPush) syncLesson(push, rows.map(row => row.student_id));
        } catch (error) {
            if (req.closed) return;
            conflict = error.status === 409; retry.hidden = !conflict;
            feedback(notice, conflict ? '课次或学生记录已变化，拟改值仍在。请读取最新名单。' : (error.message || '保存未完成') + '。更正内容已保留。', true);
        } finally { if (!req.closed) { saving = busy = false; gate(); } }
    }
    reason.input.addEventListener('input', () => { reason.input.setCustomValidity(''); writeKey = null; gate(); });
    search.input.addEventListener('input', () => { page = 0; paintRows(); });
    lessonField.input.addEventListener('change', async () => {
        if (busy) return;
        const previous = lesson, next = lessons.find(row => row.session_id === Number(lessonField.input.value));
        if (!next) return;
        if (dirty() && !await confirm({ title: '切换课次并放弃更正？', message: '切换后将清除本课尚未保存的更正。', confirmLabel: '切换课次', danger: true }, { parentLayer: handle })) {
            lessonField.input.value = String(previous.session_id); dropdowns.forEach(binding => binding.refresh()); return;
        }
        lesson = next; records = []; draft.clear(); marked.clear(); reason.input.value = ''; search.input.value = ''; page = 0; void load();
    });
    lessonField.wrapper.hidden = lessons.length < 2; void load(); return handle;
}
