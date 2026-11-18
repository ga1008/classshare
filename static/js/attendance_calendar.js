/** Teacher's term calendar. The offering and every effective attendance fact come from the server. */
import { openDialog } from './lq/dialogs.js';
import { getLayerSystem } from './lq/layer.js';
import { enhanceDropdowns } from './lq/dropdown.js';
import { createDomainButton } from './lq/domain-controls.js';
import { addDays, formatIsoDate, getMonday, parseIsoDate } from './semester_calendar.js';
import { openCalendarLesson } from './attendance_calendar_lesson.js';
import { openCalendarSync } from './attendance_calendar_sync.js';
import { action, badge, calendarRequests, element, feedback, field, lessonLabel, sourceText, statusText, studentLabel } from './attendance_calendar_controls.js';
import { SCHOOL_ACTIVITY_LABELS } from './login_attendance_states.js';

function totals(row) {
    const counts = row.counts || {}, present = Number(counts.PRESENT || 0);
    const irregular = Number(counts.LATE || 0) + Number(counts.EARLY || 0) + Number(counts.LATE_OR_EARLY || 0);
    const missing = Number(counts.ABSENT || 0) + Number(counts.SICK_LEAVE || 0) + Number(counts.PERSONAL_LEAVE || 0);
    const pending = Number(counts.PENDING || 0) + Number(counts.UNKNOWN || 0) + Number(row.unrecorded_count || 0);
    const activities = Object.keys(SCHOOL_ACTIVITY_LABELS).reduce((sum, key) => sum + Number(counts[key] || 0), 0);
    return { attended: present + irregular, missing, pending, irregular, activities };
}
function sessionMark(row, personal) {
    if (!row.recorded) return { text: personal && (row.round_id || row.school_cached) ? '无个人记录' : '未点名', tone: 'neutral' };
    if (personal) return { text: statusText(row.status), tone: row.status === 'PRESENT' ? 'success' : row.status === 'ABSENT' ? 'danger' : SCHOOL_ACTIVITY_LABELS[row.status] ? 'info' : 'neutral' };
    const { attended, missing, pending, irregular, activities } = totals(row);
    const parts = [];
    if (attended || missing) parts.push(`到${attended} 未到${missing}`);
    if (activities) parts.push(`活动${activities}`);
    if (pending) parts.push(parts.length ? `待${pending}` : `待确认 ${pending}`);
    const tone = missing || irregular ? 'warning' : activities ? 'info' : pending || !attended ? 'neutral' : 'success';
    return { text: parts.join(' ') || '免考勤', tone };
}

export function openAttendanceCalendar({ classOfferingId, studentId = null, trigger, parentLayer, sessionId = null }) {
    if (!(Number(classOfferingId) > 0)) return null;
    const base = `/api/classrooms/${Number(classOfferingId)}/login-attendance`;
    const req = calendarRequests(), body = element('div', 'la-workspace lac-workspace');
    const heading = element('div', 'lac-heading'), title = element('h3'), semester = element('p', 'lac-subtitle');
    const identity = element('div'); identity.append(title, semester);
    const sync = action('同步智慧课堂', event => openSync(event.currentTarget), 'prominent');
    heading.append(identity, sync);
    const notice = element('p', 'la-notice'), content = element('div', 'lac-content');
    const retry = action('重新读取日历', () => load()); retry.hidden = true;
    const tools = element('div', 'lac-controls');
    const person = field('select', '统计对象', { value: '', options: [{ value: '', label: '全班学生' }], searchable: true });
    const summary = element('p', 'lac-summary'), month = element('h4', 'lac-month-title');
    const nav = element('div', 'lac-month-nav');
    const prev = action('上月', () => move(-1), 'ghost'), next = action('下月', () => move(1), 'ghost');
    nav.append(prev, month, next); tools.append(person.wrapper, nav);
    const grid = element('div', 'lac-month-grid'), legend = element('div', 'la-actions lac-legend');
    const basis = element('details', 'lq-collapsible');
    basis.append(element('summary', 'lq-collapsible__summary', '统计口径'), element('p', 'lq-collapsible__content', '到场含正常出勤、迟到和早退，未到含缺课、病假和事假。研学、实践、志愿服务列为学校活动。待确认单列，免考勤和学校活动不计入应到。全员正常出勤计为满勤。'));
    content.append(tools, summary, grid, legend, basis); content.hidden = true;
    body.append(heading, notice, retry, content);
    let data, selected = Number(studentId) || null, monthKey, loading = false, epoch = 0, first = true, changed = false;
    let focusOnRefresh = null;
    const parent = parentLayer || getLayerSystem(document).top();
    const handle = openDialog({ title: '学期出勤日历', body, size: 'xl' }, {
        trigger, ...(parent?.root.contains(trigger) ? { parentLayer: parent } : {}),
        onClose: cleanup, onDestroy: cleanup,
    });
    handle.root.classList.add('la-dialog');
    const dropdowns = enhanceDropdowns(body);
    function cleanup() {
        req.close(); dropdowns.forEach(item => item.destroy());
        if (changed) document.dispatchEvent(new CustomEvent('classroom:attendance-changed'));
    }
    function gate() {
        content.setAttribute('aria-busy', String(loading));
        person.input.disabled = loading;
        sync.disabled = loading || !data?.sessions?.length;
    }
    function openSync(button) {
        if (!loading && data) openCalendarSync({ base, students: data.students, sessions: data.sessions, today: data.calendar.today,
            studentIds: selected ? [selected] : null, trigger: button, parentLayer: handle, onChange: () => { changed = true; void load(); } });
    }
    function openDay(date, lessons, trigger, calendarNote) {
        if (loading) return;
        focusOnRefresh = date;
        openCalendarLesson({ base, date, lessons, calendarNote, selected, students: data.students, trigger, parentLayer: handle,
            onClose: edited => { if (edited) { changed = true; void load(); } } });
    }
    function move(step) {
        const date = parseIsoDate(monthKey + '-01'); date.setMonth(date.getMonth() + step);
        monthKey = formatIsoDate(date).slice(0, 7); paintMonth();
    }
    function paintMonth() {
        const { start, end, days = [], today } = data.calendar;
        const date = parseIsoDate(monthKey + '-01');
        const notes = new Map(days.map(day => [day.date, day])), lessons = new Map();
        for (const row of data.sessions) {
            if (!lessons.has(row.date)) lessons.set(row.date, []);
            lessons.get(row.date).push(row);
        }
        month.textContent = `${date.getFullYear()}年${date.getMonth() + 1}月`;
        prev.disabled = monthKey <= start.slice(0, 7); next.disabled = monthKey >= end.slice(0, 7);
        grid.setAttribute('aria-label', month.textContent);
        const cells = document.createDocumentFragment();
        for (const weekday of ['一', '二', '三', '四', '五', '六', '日']) cells.append(element('span', 'lac-weekday', weekday));
        for (let day = getMonday(date); day <= new Date(date.getFullYear(), date.getMonth() + 1, 0) || day.getDay() !== 1; day = addDays(day, 1)) {
            const iso = formatIsoDate(day), matchingMonth = iso.slice(0, 7) === monthKey;
            const rows = matchingMonth && iso >= start && iso <= end ? lessons.get(iso) || [] : [];
            const note = matchingMonth && notes.get(iso);
            const calendarNote = note ? [note.label || (note.kind === 'workday' ? '调休上课' : '节假日'), note.makeup_for_date && `补 ${note.makeup_for_date} 的课`].filter(Boolean).join(' · ') : '';
            const marked = rows.some(row => sessionMark(row, Boolean(selected)).tone !== 'neutral');
            const cell = rows.length || note ? createDomainButton({ kind: 'choice', variant: marked ? 'soft' : 'ghost', className: 'lac-day' }) : element('div', 'lac-day lac-rest');
            if (!matchingMonth) { cell.setAttribute('aria-hidden', 'true'); cells.append(cell); continue; }
            cell.dataset.date = iso; cell.dataset.weekend = String(day.getDay() === 0 || day.getDay() === 6);
            const head = element('span', 'lac-date-head'); head.append(element('strong', '', day.getDate()));
            if (iso === today) { cell.setAttribute('aria-current', 'date'); head.append(element('small', '', '今')); }
            cell.append(head);
            if (note) {
                const tag = element('small', 'lac-holiday', calendarNote); tag.title = calendarNote; cell.append(tag);
            }
            if (rows.length) {
                cell.dataset.lqShape = 'surface';
                const details = [];
                for (const row of rows) {
                    const mark = sessionMark(row, Boolean(selected));
                    const result = element('span', 'lac-day-result', mark.text);
                    result.dataset.tone = mark.tone; result.dataset.lqVisual = 'category';
                    cell.append(result);
                    const schedule = [row.schedule_status_label, row.schedule_note].filter(Boolean).join(' ');
                    if (schedule) { const tag = element('small', 'lac-holiday', schedule); tag.title = schedule; cell.append(tag); }
                    details.push([lessonLabel(row), mark.text, sourceText(row.source), schedule].filter(Boolean).join('，'));
                }
                cell.setAttribute('aria-label', [iso, note?.label, ...details].filter(Boolean).join('，')); cell.title = details.join('\n');
                cell.addEventListener('click', () => openDay(iso, rows, cell, calendarNote));
            } else if (note) {
                cell.dataset.lqShape = 'surface'; cell.setAttribute('aria-label', iso + '，' + calendarNote);
                cell.addEventListener('click', () => openDialog({ title: iso + ' 校历', body: calendarNote + '。本课程当日无排课。', size: 'sm' }, { trigger: cell, parentLayer: handle }));
            }
            cells.append(cell);
        }
        grid.replaceChildren(cells);
    }
    function paint() {
        const students = data.students || [], rows = data.sessions || [];
        const student = students.find(row => Number(row.student_id) === selected);
        person.input.replaceChildren(new Option('全班学生', ''), ...students.map(row => new Option(studentLabel(row), String(row.student_id))));
        person.input.value = selected ? String(selected) : ''; dropdowns.forEach(binding => binding.refresh());
        title.textContent = student ? student.name : data.offering?.course_name || '课程出勤';
        semester.textContent = [selected && data.offering?.course_name, data.offering?.semester].filter(Boolean).join(' · ');
        const recorded = rows.filter(row => row.recorded);
        const present = recorded.reduce((n, row) => n + (selected ? Number(row.status === 'PRESENT') : totals(row).attended), 0);
        const absent = recorded.reduce((n, row) => n + (selected ? Number(row.status === 'ABSENT') : totals(row).missing), 0);
        const unit = selected ? '次' : '人次';
        summary.textContent = `已统计 ${recorded.length} / ${rows.length} 课次　·　${selected ? '出勤' : '到场'} ${present} ${unit}　·　${selected ? '缺课' : '未到'} ${absent} ${unit}`;
        legend.replaceChildren(badge(selected ? '出勤' : '满勤', 'success'), badge(selected ? '缺课' : '未满勤', selected ? 'danger' : 'warning'), badge(selected ? '其他 / 未点名' : '待确认 / 未点名 / 免考勤'));
        const activities = recorded.reduce((n, row) => n + (selected ? Number(Boolean(SCHOOL_ACTIVITY_LABELS[row.status])) : totals(row).activities), 0);
        if (activities) { summary.append('　·　学校活动 ' + activities + ' ' + unit); legend.append(badge('学校活动', 'info')); }
        basis.hidden = Boolean(selected);
        if (data.calendar?.start && data.calendar.end) {
            const from = data.calendar.start.slice(0, 7), to = data.calendar.end.slice(0, 7);
            const initial = first && rows.find(row => Number(row.session_id) === Number(sessionId))?.date;
            monthKey ||= (initial || data.calendar.today || data.calendar.start).slice(0, 7);
            monthKey = monthKey < from ? from : monthKey > to ? to : monthKey;
            paintMonth(); nav.hidden = false;
        } else { grid.replaceChildren(element('p', 'lac-empty', '本学期暂无可展示的日期。')); nav.hidden = true; }
        first = false; content.hidden = false;
    }
    async function load() {
        const ticket = ++epoch;
        const restoreDay = focusOnRefresh && (grid.contains(document.activeElement) || document.activeElement === document.body);
        loading = true; gate(); retry.hidden = true; feedback(notice, '正在读取日历…');
        try {
            const result = await req.read(base + '/calendar' + (selected ? '?student_id=' + selected : ''));
            if (req.closed || ticket !== epoch) return;
            data = result; paint(); feedback(notice, data.calendar?.scope_note || '');
            if (restoreDay && getLayerSystem(document).top() === handle) grid.querySelector(`button[data-date="${focusOnRefresh}"]`)?.focus({ preventScroll: true });
            focusOnRefresh = null;
        } catch (error) { if (!req.closed && ticket === epoch) { data = null; grid.replaceChildren(); content.hidden = true; retry.hidden = false; feedback(notice, error.message || '日历读取失败', true); } }
        finally { if (!req.closed && ticket === epoch) { loading = false; gate(); } }
    }
    person.input.addEventListener('change', () => { selected = Number(person.input.value) || null; void load(); });
    void load(); return handle;
}
