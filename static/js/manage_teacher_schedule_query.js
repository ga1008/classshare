import { apiFetch } from './api.js';
import { createDomainButton } from './lq/domain-controls.js';
import { createDialog, openDialog } from './lq/dialogs.js';
import { getDropdown, enhanceDropdowns } from './lq/dropdown.js';
import { createScheduleDeck } from './course_schedule_deck.js';
import { createComponent } from './lq/components.js';

const API = '/api/manage/academic/teachers';
const root = document.querySelector('[data-teacher-query]');
const boot = JSON.parse(document.getElementById('teacher-query-boot')?.textContent || '{}');
const refs = Object.fromEntries([
    ['form', 'teacherQueryForm'], ['term', 'teacherQueryTerm'], ['name', 'teacherQueryName'],
    ['submit', 'teacherQuerySubmit'], ['sync', 'teacherQuerySync'], ['feedback', 'teacherQueryFeedback'],
    ['cards', 'teacherQueryCards'], ['empty', 'teacherQueryEmpty'], ['meta', 'teacherQueryResultMeta'],
    ['pagination', 'teacherQueryPagination'], ['page', 'teacherQueryPage'], ['prev', 'teacherQueryPrev'],
    ['next', 'teacherQueryNext'], ['credential', 'teacherQueryCredential'],
    ['syncHint', 'teacherQuerySyncHint'], ['filterHint', 'teacherQueryFilterHint'],
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
const accountLabels = { linked: '已关联平台账号', unlinked: '尚未关联平台账号', ambiguous: '职工号关联冲突，请核实账号' };
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
    refs.sync.disabled = busy || !refs.name.value.trim();
    refs.sync.setAttribute('aria-disabled', String(refs.sync.disabled));
    refs.sync.dataset.lqDisabled = String(refs.sync.disabled);
    refs.sync.classList.toggle('is-disabled', refs.sync.disabled);
    refs.submit.textContent = busy && !force ? '查询中…' : '查询';
    refs.sync.textContent = busy && force ? '同步中…' : '同步';
    refs.prev.disabled = busy || !state.result || state.result.pagination?.page <= 1;
    refs.next.disabled = busy || !state.result || state.result.pagination?.page >= state.result.pagination?.total_page;
}

function updateQueryMode() {
    const browsing = !refs.name.value.trim();
    refs.syncHint.hidden = !browsing;
    refs.filterHint.hidden = !browsing;
    refs.sync.title = browsing ? '先输入教师姓名或工号，再从教务系统同步' : '从教务系统重新同步，再显示查询结果';
    for (const name of Object.keys(filterNames)) {
        const select = refs.form.elements.namedItem(name);
        select.disabled = browsing;
        getDropdown(select)?.refresh();
    }
    setBusy(state.busy);
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
    updateQueryMode();
}

function currentQuery() {
    const [year, term] = refs.term.value.split('|');
    const query = { year, term, q: refs.name.value.trim(), page: 1, page_size: 12 };
    for (const name of Object.keys(filterNames)) query[name] = refs.form.elements.namedItem(name).value;
    return query;
}

function queryKey(query) {
    const { page, page_size, local_only, ...filters } = query;
    return JSON.stringify(filters);
}

function invalidateQuery() {
    state.generation += 1;
    state.request?.abort();
    state.optionsRequest?.abort();
    state.request = null;
    updateQueryMode();
    setBusy(false);
    if (state.result) feedback('筛选条件已修改；下方仍是上次查询结果，点击查询更新。');
    if (!state.composing && !refs.name.value.trim()) void queryTeachers();
}

function renderResults(result) {
    if (state.modal && !state.modal.closed && !(result.items || []).some(item => item.teacher_no === state.modal.teacher.teacher_no)) {
        // A refreshed page may no longer contain its open dialog's trigger.
        // Keep the connected card until LQ has completed focus restoration.
        state.modal.deferredResult = result;
        return;
    }
    state.result = result;
    const items = result.items || [];
    const existing = new Map([...refs.cards.children].map(button => [button.dataset.teacherNo, button]));
    for (const teacher of items) {
        // Retain the teacher card element: an open LQ layer owns it as trigger.
        const button = existing.get(teacher.teacher_no) || createDomainButton({ className: 'teacher-query-card', attrs: { 'data-teacher-no': teacher.teacher_no, 'data-lq-shape': 'surface' } });
        existing.delete(teacher.teacher_no);
        const head = node('span', 'teacher-query-card__head');
        head.append(node('strong', '', teacher.name || '未提供姓名'), node('span', '', teacher.title || '职称未提供'));
        const identity = node('span', 'teacher-query-card__identity');
        identity.append(node('span', '', `工号 ${teacher.teacher_no}`), node('span', '', teacher.college_name || '学院未提供'));
        const stats = node('span', 'teacher-query-card__stats', `${teacher.summary?.course_count || 0} 门课程 · ${teacher.summary?.class_count || 0} 个班级 · ${teacher.summary?.lesson_count || 0} 次安排`);
        const foot = node('span', 'teacher-query-card__foot');
        foot.append(node('span', '', accountLabels[teacher.account_status] || '账号关联待核实'), node('span', '', '查看 3D 课表 →'));
        button.replaceChildren(head, identity, stats, foot);
        refs.cards.append(button);
    }
    for (const button of existing.values()) button.remove();
    refs.empty.hidden = items.length > 0;
    if (!items.length) {
        const browsing = !state.query?.q;
        refs.empty.querySelector('strong, h3, h4')?.replaceChildren(document.createTextNode(browsing ? '本学期暂无本地课表' : '没有匹配的教师'));
        const description = refs.empty.querySelector('p');
        if (description) description.textContent = browsing ? '输入教师姓名或教工号后点击查询，可从教务系统同步完整学期课表。' : '请核对姓名或工号，或减少其他筛选条件后再试。';
    }
    const pagination = result.pagination || { page: 1, total_page: 1, total_count: items.length };
    refs.pagination.hidden = pagination.total_page <= 1;
    refs.page.textContent = `第 ${pagination.page} / ${pagination.total_page || 1} 页 · 共 ${pagination.total_count} 位教师`;
    refs.meta.textContent = `${result.selected_term?.label || state.query?.year || ''} · ${result.source === 'academic' ? '教务已同步' : '本地缓存'}${result.mode === 'local_browse' ? ' · 最近同步优先' : ''} · ${timestamp(result.synced_at)}`;
    const sum = result.summary || {};
    const values = {
        teachers: pagination.total_count ?? items.length,
        courses: sum.course_count ?? items.reduce((total, item) => total + (item.summary?.course_count || 0), 0),
        lessons: sum.lesson_count ?? items.reduce((total, item) => total + (item.summary?.lesson_count || 0), 0),
        linked: sum.linked_count ?? items.filter(item => item.account_status === 'linked').length,
    };
    const metricLabels = { courses: '任教课程', lessons: '课次安排', linked: '已关联平台账号' };
    for (const [key, value] of Object.entries(values)) {
        const metric = root.querySelector(`[data-teacher-metric="${key}"]`);
        metric.textContent = String(value);
        if (metricLabels[key]) metric.previousElementSibling.textContent = `${result.summary_scope === 'page' ? '本页' : ''}${metricLabels[key]}`;
    }
    setBusy(false);
}

async function queryTeachers(force = false, page = 1) {
    if (state.composing || !refs.form.reportValidity()) return;
    if (force && !refs.name.value.trim()) { refs.name.focus(); return; }
    const query = { ...currentQuery(), page };
    state.request?.abort();
    state.optionsRequest?.abort();
    const controller = new AbortController(), generation = ++state.generation;
    state.request = controller;
    setBusy(true, force);
    feedback(force ? '正在从教务系统同步教师及完整学期课表…' : query.q ? '正在查询；没有本地缓存时会自动同步…' : '正在读取本学期的本地课表…');
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
    const cleanup = () => { modal.detail = null; queueMicrotask(() => modal.applyPending?.()); };
    modal.detail = openDialog({ title: full.course_name || '课程详情', body, size: 'lg' }, {
        trigger, parentLayer: modal.handle, onClose: cleanup, onDestroy: cleanup,
    });
}

function renderUnscheduled(modal, host) {
    host.replaceChildren();
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
    const modal = { teacher, closed: false, controller: null, refreshController: null, events: new AbortController(), deck: null, detail: null, handle: null, data: null, busy: false };
    state.modal = modal;
    const body = node('div', 'teacher-timetable-body');
    const identity = node('p', 'teacher-timetable-identity');
    const termIdentity = node('p', 'teacher-timetable-identity');
    body.append(identity, termIdentity);
    const toolbar = node('div', 'lq-domain-toolbar teacher-timetable-toolbar');
    toolbar.dataset.lqComponent = 'toolbar';
    const syncButton = createComponent('button', { label: '同步课表', variant: 'glass' });
    syncButton.dataset.teacherTimetableSync = '';
    const todayButton = createComponent('button', { label: '回到本周', variant: 'ghost', disabled: true });
    todayButton.dataset.teacherTimetableToday = '';
    const position = node('p', 'teacher-timetable-position');
    toolbar.append(syncButton, todayButton);
    const status = node('p', '', '正在读取本地完整课表…'); status.setAttribute('role', 'status');
    const mount = node('div');
    const extra = node('div');
    body.append(toolbar, status, position, mount, extra);
    const dialog = createDialog({ title: `${teacher.name}的课表`, body, size: 'xl' });
    dialog.classList.add('teacher-timetable-dialog');
    const cancel = () => { modal.closed = true; modal.controller?.abort(); modal.refreshController?.abort(); };
    const cleanup = () => {
        cancel(); modal.events.abort(); modal.deck?.destroy(); modal.detail?.destroy();
        if (state.modal === modal) state.modal = null;
        if (modal.deferredResult && !state.disposed) {
            const result = modal.deferredResult;
            modal.deferredResult = null;
            queueMicrotask(() => {
                if (state.disposed || state.modal) return;
                renderResults(result);
                const returned = [...refs.cards.children].find(card => card.dataset.teacherNo === teacher.teacher_no);
                (returned || refs.submit).focus({ preventScroll: true });
            });
        }
    };
    modal.handle = openDialog(dialog, { trigger, onCloseRequested: cancel, onClose: cleanup, onDestroy: cleanup });

    function showIdentity(profile) {
        identity.textContent = `${profile.name} · 工号 ${profile.teacher_no} · ${profile.college_name || '学院未提供'} · ${profile.title || '职称未提供'}`;
        termIdentity.textContent = `${selected.label || `${selected.year} 第${selected.term}学期`} · ${accountLabels[profile.account_status] || '账号关联待核实'}`;
    }
    showIdentity(teacher);

    function setModalBusy(busy, syncing = false) {
        modal.busy = busy;
        // Adopt the canonical loading slots without replacing the focused button.
        const presentation = createComponent('button', { label: '同步课表', variant: 'glass', loading: busy && syncing, disabled: busy });
        syncButton.className = presentation.className;
        syncButton.replaceChildren(...presentation.childNodes);
        syncButton.disabled = busy;
        for (const name of ['aria-busy', 'aria-disabled', 'data-lq-disabled']) {
            if (presentation.hasAttribute(name)) syncButton.setAttribute(name, presentation.getAttribute(name));
            else syncButton.removeAttribute(name);
        }
        const current = modal.data?.overview?.weeks?.some(week => week.is_current);
        todayButton.disabled = busy || !current;
        todayButton.setAttribute('aria-disabled', String(todayButton.disabled));
        todayButton.dataset.lqDisabled = String(todayButton.disabled);
        todayButton.classList.toggle('is-disabled', todayButton.disabled);
    }

    function showData(data, synced) {
        modal.data = data;
        modal.teacher = data.teacher || modal.teacher;
        showIdentity(modal.teacher);
        status.textContent = `${synced ? '同步完成' : '本地课表'} · 上次同步 ${timestamp(data.synced_at)}。切换周次，点击课程查看完整信息。`;
        if (!modal.deck) modal.deck = createScheduleDeck(mount, {
            title: '3D 周课表', description: '切换周次，点击课程查看详情。', allowExpand: false,
            onLessonClick: (lesson, context) => openLesson(lesson, context, modal),
            emptyHtml: () => '<strong>本学期没有已排定的课次</strong><p>如有未排定时间的课程，会列在下方。</p>',
        });
        modal.deck.setOverview(data.overview, { keepWeek: false });
        const term = data.overview?.selected_term || {};
        const current = data.overview?.weeks?.find(week => week.is_current);
        position.textContent = current ? `当前教学周：第 ${current.week_index} 周；本周课表中高亮今天。`
            : term.anchor_source === 'unavailable' ? '尚无该学期校历，按教务周次展示，暂不能定位今天。'
                : '今天不在所选学期内，已定位该学期的边界教学周。';
        renderUnscheduled(modal, extra);
    }

    async function refreshList() {
        if (!state.query || state.disposed || modal.closed) return;
        modal.refreshController?.abort();
        const controller = new AbortController();
        modal.refreshController = controller;
        const generation = state.generation;
        // Local browse returns to page one so the newly synced teacher stays visible.
        const query = { ...state.query, page: state.query.q ? state.query.page : 1, local_only: true };
        try {
            const result = await apiFetch(`${API}/query`, { method: 'POST', body: query, signal: controller.signal, silent: true });
            if (modal.closed || state.disposed || controller.signal.aborted || generation !== state.generation) return;
            state.query = query;
            renderResults(result);
        } catch (error) {
            if (!modal.closed && !controller.signal.aborted && !state.disposed) {
                status.textContent += ' 列表暂未刷新，可关闭浮窗后重新查询；本课表已更新。';
            }
        }
    }

    async function load(syncing = false) {
        if (modal.busy || modal.closed) return;
        modal.controller?.abort();
        const controller = new AbortController(); modal.controller = controller;
        setModalBusy(true, syncing);
        status.textContent = syncing ? '正在同步这位教师的完整学期课表，现有课表仍可查看…' : '正在读取本地完整课表…';
        try {
            const params = new URLSearchParams({ year: selected.year, term: selected.term });
            const path = `${API}/${encodeURIComponent(teacher.teacher_no)}`;
            const data = await apiFetch(syncing ? `${path}/sync` : `${path}/timetable?${params}`, {
                ...(syncing ? { method: 'POST', body: { year: selected.year, term: selected.term } } : {}),
                signal: controller.signal, silent: true,
            });
            if (modal.closed || controller.signal.aborted || state.disposed) return;
            // A child opened while syncing owns its lesson trigger until it closes.
            if (modal.detail) {
                modal.pendingData = data;
                status.textContent = '同步完成；关闭课程详情后显示更新的课表。';
            } else showData(data, syncing);
            if (syncing) await refreshList();
        } catch (error) {
            if (modal.closed || controller.signal.aborted || state.disposed) return;
            status.textContent = `${error.message || '课表读取失败，请重试。'}${modal.data ? ' 当前课表已保留，可再次点击同步课表重试。' : ''}`;
            if (!modal.data) {
                const retry = createDomainButton({ label: '重新读取', variant: 'glass' });
                retry.addEventListener('click', () => { retry.remove(); void load(); }, { once: true, signal: modal.events.signal });
                status.append(' ', retry);
            }
        } finally {
            if (!modal.closed && !state.disposed) setModalBusy(false);
        }
    }
    modal.applyPending = () => {
        if (modal.pendingData && !modal.closed) {
            const data = modal.pendingData; modal.pendingData = null;
            showData(data, true); setModalBusy(modal.busy, true);
            syncButton.focus({ preventScroll: true });
        }
    };
    syncButton.addEventListener('click', () => { void load(true); }, { signal: modal.events.signal });
    todayButton.addEventListener('click', () => {
        const index = modal.data?.overview?.weeks?.findIndex(week => week.is_current) ?? -1;
        if (index >= 0 && !modal.busy) modal.deck?.goToWeek(index);
    }, { signal: modal.events.signal });
    await load();
}

on(refs.form, 'submit', event => { event.preventDefault(); void queryTeachers(); });
on(refs.sync, 'click', () => { void queryTeachers(true); });
on(refs.form, 'change', invalidateQuery);
on(refs.name, 'input', invalidateQuery);
on(refs.name, 'compositionstart', () => { state.composing = true; });
on(refs.name, 'compositionend', () => { state.composing = false; if (!refs.name.value.trim()) invalidateQuery(); });
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
void queryTeachers();
