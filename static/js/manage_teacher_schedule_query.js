import { apiFetch } from './api.js';
import { createDomainButton } from './lq/domain-controls.js';
import { createDialog, openDialog } from './lq/dialogs.js';
import { getDropdown, enhanceDropdowns } from './lq/dropdown.js';
import { createScheduleDeck } from './course_schedule_deck.js';

const API = '/api/manage/academic/teachers';
const root = document.querySelector('[data-teacher-query]');
const boot = JSON.parse(document.getElementById('teacher-query-boot')?.textContent || '{}');
const refs = Object.fromEntries([
    ['form', 'teacherQueryForm'], ['term', 'teacherQueryTerm'], ['name', 'teacherQueryName'],
    ['submit', 'teacherQuerySubmit'], ['sync', 'teacherQuerySync'], ['feedback', 'teacherQueryFeedback'],
    ['cards', 'teacherQueryCards'], ['empty', 'teacherQueryEmpty'], ['meta', 'teacherQueryResultMeta'],
    ['pagination', 'teacherQueryPagination'], ['page', 'teacherQueryPage'], ['prev', 'teacherQueryPrev'],
    ['next', 'teacherQueryNext'], ['credential', 'teacherQueryCredential'],
].map(([key, id]) => [key, document.getElementById(id)]));
const filterNames = { campus_id: 'campuses', department_id: 'departments', title_id: 'titles', college_id: 'colleges', external: 'external' };
const state = { generation: 0, request: null, optionsRequest: null, result: null, query: null, busy: false, disposed: false, modal: null, composing: false };
const lifetime = new AbortController();
const on = (element, event, handler) => element?.addEventListener(event, handler, { signal: lifetime.signal });
const node = (tag, className, text) => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = String(text);
    return element;
};
const accountLabels = { linked: '已关联平台账号', unlinked: '尚未关联平台账号', ambiguous: '账号关联待核实' };
const timestamp = value => value ? String(value).replace('T', ' ').slice(0, 16) : '未同步';
const termKey = term => `${term?.year || ''}|${term?.term || ''}`;

function feedback(message, tone = 'info') {
    refs.feedback.textContent = message;
    refs.feedback.dataset.tone = tone;
}

function setBusy(busy, force = false) {
    state.busy = busy;
    refs.form.setAttribute('aria-busy', String(busy));
    refs.submit.disabled = busy;
    refs.sync.disabled = busy;
    refs.submit.textContent = busy && !force ? '查询中…' : '查询';
    refs.sync.textContent = busy && force ? '同步中…' : '同步';
    refs.prev.disabled = busy || !state.result || state.result.pagination?.page <= 1;
    refs.next.disabled = busy || !state.result || state.result.pagination?.page >= state.result.pagination?.total_page;
}

function replaceOptions(select, options, selected, all = false) {
    const entries = all ? [new Option('全部', '')] : [];
    for (const option of options || []) {
        const value = all ? String(option.id ?? option.value ?? '') : termKey(option);
        const label = all ? option.name ?? option.label ?? value : option.label ?? `${option.year} 第${option.term}学期`;
        if (entries.some(entry => entry.value === value)) continue;
        entries.push(new Option(String(label), value));
    }
    // Preserve a submitted filter while refreshed local options arrive.
    if (selected && !entries.some(entry => entry.value === selected)) {
        const previous = [...select.options].find(option => option.value === selected);
        entries.push(new Option(previous?.label || selected, selected));
    }
    select.replaceChildren(...entries);
    if (selected) select.value = selected;
    getDropdown(select)?.refresh();
}

function applyOptions(options, initial = false) {
    replaceOptions(refs.term, options.terms, initial ? termKey(options.default_term) : refs.term.value);
    for (const [name, key] of Object.entries(filterNames)) {
        const select = refs.form.elements.namedItem(name);
        replaceOptions(select, options.filters?.[key], select.value, true);
    }
    refs.credential.hidden = Boolean(options.credential_ready);
    enhanceDropdowns(refs.form);
}

function currentQuery() {
    const [year, term] = refs.term.value.split('|');
    const query = { year, term, q: refs.name.value.trim(), page: 1, page_size: 12 };
    for (const name of Object.keys(filterNames)) query[name] = refs.form.elements.namedItem(name).value;
    return query;
}

function queryKey(query) {
    const { page, page_size, ...filters } = query;
    return JSON.stringify(filters);
}

function invalidateQuery() {
    state.generation += 1;
    state.request?.abort();
    state.optionsRequest?.abort();
    state.request = null;
    setBusy(false);
    if (state.result) feedback('筛选条件已修改；下方仍是上次查询结果，点击查询更新。');
}

function renderResults(result) {
    state.result = result;
    refs.cards.replaceChildren();
    const items = result.items || [];
    for (const teacher of items) {
        const button = createDomainButton({ className: 'teacher-query-card', attrs: { 'data-teacher-no': teacher.teacher_no, 'data-lq-shape': 'surface' } });
        const head = node('span', 'teacher-query-card__head');
        head.append(node('strong', '', teacher.name || '未提供姓名'), node('span', '', teacher.title || '职称未提供'));
        const identity = node('span', 'teacher-query-card__identity');
        identity.append(node('span', '', `工号 ${teacher.teacher_no}`), node('span', '', teacher.college_name || '学院未提供'));
        const stats = node('span', 'teacher-query-card__stats', `${teacher.summary?.course_count || 0} 门课程 · ${teacher.summary?.class_count || 0} 个班级 · ${teacher.summary?.lesson_count || 0} 次安排`);
        const foot = node('span', 'teacher-query-card__foot');
        foot.append(node('span', '', accountLabels[teacher.account_status] || '账号关联待核实'), node('span', '', '查看 3D 课表 →'));
        button.append(head, identity, stats, foot);
        refs.cards.append(button);
    }
    refs.empty.hidden = items.length > 0;
    if (!items.length) {
        refs.empty.querySelector('strong, h3, h4')?.replaceChildren(document.createTextNode('没有匹配的教师'));
        const description = refs.empty.querySelector('p');
        if (description) description.textContent = '请核对姓名或工号，或减少其他筛选条件后再试。';
    }
    const pagination = result.pagination || { page: 1, total_page: 1, total_count: items.length };
    refs.pagination.hidden = pagination.total_page <= 1;
    refs.page.textContent = `第 ${pagination.page} / ${pagination.total_page || 1} 页 · 共 ${pagination.total_count} 位教师`;
    refs.meta.textContent = `${result.selected_term?.label || state.query?.year || ''} · ${result.source === 'academic' ? '教务已同步' : '本地缓存'} · ${timestamp(result.synced_at)}`;
    const sum = result.summary || {};
    const values = {
        teachers: pagination.total_count ?? items.length,
        courses: sum.course_count ?? items.reduce((total, item) => total + (item.summary?.course_count || 0), 0),
        lessons: sum.lesson_count ?? items.reduce((total, item) => total + (item.summary?.lesson_count || 0), 0),
        linked: sum.linked_count ?? items.filter(item => item.account_status === 'linked').length,
    };
    for (const [key, value] of Object.entries(values)) root.querySelector(`[data-teacher-metric="${key}"]`).textContent = String(value);
    setBusy(false);
}

async function queryTeachers(force = false, page = 1) {
    if (state.composing || !refs.form.reportValidity()) return;
    if (!refs.name.value.trim()) { refs.name.focus(); return; }
    const query = { ...currentQuery(), page };
    state.request?.abort();
    state.optionsRequest?.abort();
    const controller = new AbortController(), generation = ++state.generation;
    state.request = controller;
    setBusy(true, force);
    feedback(force ? '正在从教务系统同步教师及完整学期课表…' : '正在查询；没有本地缓存时会自动同步…');
    try {
        const result = await apiFetch(`${API}/${force ? 'sync' : 'query'}`, { method: 'POST', body: query, signal: controller.signal, silent: true });
        if (state.disposed || controller.signal.aborted || generation !== state.generation || queryKey(currentQuery()) !== queryKey(query)) return;
        state.query = query;
        renderResults(result);
        feedback(result.message || (result.source === 'academic' ? '同步完成，已保存完整学期课表。' : '已读取本地缓存；需要最新教务数据时点击同步。'), 'success');
        if (result.source === 'academic') {
            const optionsController = new AbortController();
            state.optionsRequest = optionsController;
            apiFetch(`${API}/options`, { signal: optionsController.signal, silent: true }).then(options => {
                if (!state.disposed && !optionsController.signal.aborted && generation === state.generation) applyOptions(options);
            }).catch(() => {});
        }
    } catch (error) {
        if (state.disposed || controller.signal.aborted || generation !== state.generation) return;
        const retained = Boolean(state.result?.items?.length);
        feedback(`${error.message || '查询失败，请重试。'}${retained ? ' 下方保留上次查询结果，仍可查看本地课表。' : ''}`, 'error');
        if (retained) refs.meta.textContent += ' · 本次未更新';
    } finally {
        if (!state.disposed && generation === state.generation) { state.request = null; setBusy(false); }
    }
}

function detailRows(entries) {
    const list = node('dl', 'teacher-lesson-details');
    for (const [label, value] of entries) {
        if (value === null || value === undefined || value === '') continue;
        const row = node('div', label === '课程说明' ? 'is-wide' : '');
        row.append(node('dt', '', label), node('dd', '', Array.isArray(value) ? value.join('、') : value));
        list.append(row);
    }
    return list;
}

function openLesson(lesson, { trigger, week } = {}, modal) {
    if (!modal || modal.closed || modal.detail) return;
    const full = { ...lesson, ...(modal.data.lessons?.[String(lesson.id)] || {}) };
    const teacher = modal.data.teacher || modal.teacher;
    const body = node('div');
    const weekday = full.weekday_label || (full.weekday ? `星期${'一二三四五六日'[Number(full.weekday) - 1]}` : '');
    const weeks = Array.isArray(full.weeks) ? full.weeks.map(value => `第${value}周`).join('、') : full.weeks_text || full.week_text || '';
    body.append(detailRows([
        ['课程名称', full.course_name], ['课程号', full.course_code],
        ['本次安排', week ? `第${week.week_index}周 · ${weekday} · 第${(full.sections || []).join('、')}节` : '未排定具体上课时间'],
        ['全部周次', weeks], ['日期', lesson.actual_date || lesson.date], ['上课时间', lesson.time_label || [lesson.start_time, lesson.end_time].filter(Boolean).join('–')],
        ['班级名称', full.class_label], ['教学班', full.teaching_class_name], ['教学班编号', full.teaching_class_id],
        ['上课地点', full.classroom], ['校区', full.campus_name || full.campus], ['课程性质', full.course_nature || full.course_type],
        ['课程类别', full.course_category], ['学分', full.credits],
        ['课程总学时', full.course_total_hours_text || full.course_hours], ['教学班总学时', full.total_hours_text],
        ['学时组成', full.course_hour_text], ['周学时', full.weekly_hours_text],
        ['本安排累计节数', full.total_hours], ['选课人数', full.student_count], ['教学班人数', full.teaching_class_student_count],
        ['考核方式', full.exam_method], ['考试方式', full.exam_mode], ['专业方向', full.major_direction],
        ['课程主题', full.course_topic_name], ['分层教学', full.block_level], ['线上教学', full.online_info],
        ['教师', `${teacher.name || ''} · ${teacher.teacher_no || ''}`], ['所属学院', teacher.college_name],
        ['职称', teacher.title], ['教师角色', full.teacher_role], ['课程说明', full.description || full.course_note || full.note],
    ]));
    const raw = full.raw || full.source_data || full.metadata;
    if (raw && Object.keys(raw).length) {
        const disclosure = node('details', 'teacher-lesson-source');
        disclosure.append(node('summary', '', '教务补充信息'));
        disclosure.append(detailRows(Object.entries(raw).map(([key, value]) => [key, value && typeof value === 'object' ? JSON.stringify(value, null, 2) : value])));
        body.append(disclosure);
    }
    const cleanup = () => { modal.detail = null; };
    modal.detail = openDialog({ title: full.course_name || '课程详情', body, size: 'lg' }, {
        trigger, parentLayer: modal.handle, onClose: cleanup, onDestroy: cleanup,
    });
}

function renderUnscheduled(modal, host) {
    const courses = modal.data.unscheduled_courses || modal.data.teacher?.unscheduled_courses || [];
    if (!courses.length) return;
    const section = node('section', 'teacher-unscheduled');
    section.append(node('h3', '', '其他课程 · 未排定时间'), node('p', '', '教务提供了以下课程，但尚无可放入周课表的时间安排。'));
    const list = node('div', 'teacher-query-cards');
    for (const course of courses) {
        const button = createDomainButton({ label: course.course_name && course.course_name !== '未排定时间课程'
            ? course.course_name : course.description?.split('/')[0] || '其他课程', className: 'teacher-unscheduled-course' });
        button.addEventListener('click', () => openLesson(course, { trigger: button }, modal), { signal: modal.events.signal });
        list.append(button);
    }
    section.append(list); host.append(section);
}

async function openTeacher(teacher, trigger) {
    if (state.modal || state.disposed) return;
    const selected = state.result.selected_term || state.query;
    const modal = { teacher, closed: false, controller: null, events: new AbortController(), deck: null, detail: null, handle: null, data: null };
    state.modal = modal;
    const body = node('div', 'teacher-timetable-body');
    body.append(node('p', 'teacher-timetable-identity', `${teacher.name} · 工号 ${teacher.teacher_no} · ${teacher.college_name || '学院未提供'} · ${teacher.title || '职称未提供'}`));
    body.append(node('p', 'teacher-timetable-identity', `${selected.label || `${selected.year} 第${selected.term}学期`} · ${accountLabels[teacher.account_status] || '账号关联待核实'}`));
    const status = node('p', '', '正在读取本地完整课表…'); status.setAttribute('role', 'status');
    const mount = node('div');
    const extra = node('div');
    body.append(status, mount, extra);
    const dialog = createDialog({ title: `${teacher.name}的课表`, body, size: 'xl' });
    dialog.classList.add('teacher-timetable-dialog');
    const cancel = () => { modal.closed = true; modal.controller?.abort(); };
    const cleanup = () => {
        cancel(); modal.events.abort(); modal.deck?.destroy(); modal.detail?.destroy();
        if (state.modal === modal) state.modal = null;
    };
    modal.handle = openDialog(dialog, { trigger, onCloseRequested: cancel, onClose: cleanup, onDestroy: cleanup });
    async function load() {
        modal.controller?.abort();
        const controller = new AbortController(); modal.controller = controller;
        status.textContent = '正在读取本地完整课表…';
        try {
            const params = new URLSearchParams({ year: selected.year, term: selected.term });
            const data = await apiFetch(`${API}/${encodeURIComponent(teacher.teacher_no)}/timetable?${params}`, { signal: controller.signal, silent: true });
            if (modal.closed || controller.signal.aborted || state.disposed) return;
            modal.data = data;
            status.textContent = `本地课表 · 上次同步 ${timestamp(data.synced_at)}。滚轮、方向键或下方滑杆切换周次，点击课程查看完整信息。`;
            modal.deck = createScheduleDeck(mount, {
                title: '3D 周课表', description: '切换周次，点击课程查看详情。', allowExpand: false,
                onLessonClick: (lesson, context) => openLesson(lesson, context, modal),
                emptyHtml: () => '<strong>本学期没有已排定的课次</strong><p>如有未排定时间的课程，会列在下方。</p>',
            });
            modal.deck.setOverview(data.overview);
            renderUnscheduled(modal, extra);
        } catch (error) {
            if (modal.closed || controller.signal.aborted || state.disposed) return;
            status.textContent = error.message || '课表读取失败，请重试。';
            const retry = createDomainButton({ label: '重新读取', variant: 'glass' });
            retry.addEventListener('click', () => { retry.remove(); void load(); }, { once: true, signal: modal.events.signal });
            status.append(' ', retry);
        }
    }
    await load();
}

on(refs.form, 'submit', event => { event.preventDefault(); void queryTeachers(); });
on(refs.sync, 'click', () => { void queryTeachers(true); });
on(refs.form, 'change', invalidateQuery);
on(refs.name, 'input', invalidateQuery);
on(refs.name, 'compositionstart', () => { state.composing = true; });
on(refs.name, 'compositionend', () => { state.composing = false; });
on(refs.prev, 'click', () => { void queryTeachers(false, (state.result?.pagination?.page || 1) - 1); });
on(refs.next, 'click', () => { void queryTeachers(false, (state.result?.pagination?.page || 1) + 1); });
on(refs.cards, 'click', event => {
    const button = event.target.closest('[data-teacher-no]');
    const teacher = state.result?.items?.find(item => item.teacher_no === button?.dataset.teacherNo);
    if (teacher) void openTeacher(teacher, button);
});
on(window, 'pagehide', event => {
    if (event.persisted) return;
    state.disposed = true; state.generation += 1;
    state.request?.abort(); state.optionsRequest?.abort(); state.modal?.handle.destroy();
    refs.form.querySelectorAll('select').forEach(select => getDropdown(select)?.destroy());
    lifetime.abort();
});
applyOptions(boot, true);
