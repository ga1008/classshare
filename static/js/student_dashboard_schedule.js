import { createScheduleDeck } from '/static/js/course_schedule_deck.js?v=deck3d-20260707';

const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, character => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[character]));
const termKey = (term) => term?.year ? `${term.year}|${term.term}` : '';

/** One deck and one overview serve the week, agenda and complete course collection. */
export function initStudentDashboardSchedule(root) {
  const panel = root?.querySelector('[data-student-schedule]');
  if (!panel || panel.dataset.initialized) return;
  panel.dataset.initialized = 'true';
  const find = name => panel.querySelector(`[data-student-${name}]`);
  const buttons = [...panel.querySelectorAll('[data-student-schedule-mode]')];
  const panes = { '3d': find('schedule-deck'), agenda: find('schedule-agenda'), courses: find('schedule-courses') };
  const key = `dashboard-schedule:v3:student:${root.dataset.dashboardUserId}`;
  let saved = {};
  try { const parsed = JSON.parse(localStorage.getItem(key) || '{}'); if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) saved = parsed; } catch { /* Storage may be disabled. */ }
  let mode = saved.mode === 'agenda' ? 'agenda' : '3d';
  let overview = null;
  let courses = [];
  let controller = null;
  let sequence = 0;
  let transition = 0;
  let lastLoaded = 0;
  let activeIndex = 0;
  let requestedTerm = '';
  try { courses = JSON.parse(find('schedule-courses-payload').textContent || '[]'); } catch { /* SSR links remain usable. */ }
  const save = (changes) => {
    saved = { ...saved, ...changes };
    try { localStorage.setItem(key, JSON.stringify(saved)); } catch { /* Preference is optional. */ }
  };
  const courseURL = course => `/classroom/${Number(course.id)}`;
  const renderCourses = () => {
    const query = find('course-search').value.trim().toLocaleLowerCase();
    const semester = find('course-term').value;
    const state = find('course-state').value;
    const visible = courses.filter(course => (!query || [course.course_name, course.class_name, course.teacher_name].join(' ').toLocaleLowerCase().includes(query))
      && (!semester || course.semester === semester)
      && (!state || (state === 'history' ? course.is_history : course.is_unscheduled || course.unpositioned_session_count > 0)));
    find('course-result').textContent = `${semester || '全部学期'} · ${visible.length} / ${courses.length} 个课堂${query || state ? ' · 仅筛选课程集合' : ''}`;
    find('course-count').textContent = courses.length;
    find('course-list').innerHTML = visible.length ? visible.map(course => `<article class="dw-schedule-course"><h3><a href="${courseURL(course)}">${escapeHtml(course.course_name)}</a></h3><p>${escapeHtml([course.class_name, course.teacher_name].filter(Boolean).join(' · '))}</p><small>${escapeHtml(course.semester || '未配置学期')}${course.is_unscheduled ? ' · 未排定' : course.unpositioned_session_count ? ` · ${Number(course.unpositioned_session_count)} 次课待排定` : ''}${course.is_history ? ' · 往期' : ''}</small><div><a class="dw-button dw-button-primary" href="${courseURL(course)}">进入课堂</a><a class="dw-link" href="${courseURL(course)}#assignment-panel">作业与考试</a></div></article>`).join('')
      : `<div class="dw-schedule-empty"><strong>${courses.length ? '没有匹配的课程' : '还没有加入课堂'}</strong><p>${courses.length ? '可清除筛选，查看全部学期的课程。' : '加入课堂后，课程和待办会出现在这里。'}</p>${courses.length ? '<button type="button" class="dw-button" data-student-course-reset>清除集合筛选</button>' : ''}</div>`;
  };
  const renderAgenda = (week) => {
    const lessons = week?.lessons || [];
    panes.agenda.innerHTML = lessons.length ? `<ol class="dw-week-lessons">${lessons.map(lesson => `<li><div class="dw-lesson-time"><strong>${escapeHtml(lesson.weekday_label)}</strong><span>${escapeHtml(lesson.section_label)}</span></div><div><h3><a href="/classroom/${Number(lesson.class_offering_id)}">${escapeHtml(lesson.course_name)}</a></h3><p>${escapeHtml(lesson.class_label)}${lesson.session_no ? ` · 第 ${Number(lesson.session_no)} 次课` : ''}</p><small>${escapeHtml(lesson.classroom || '地点待定')}</small></div><a class="dw-button" href="/classroom/${Number(lesson.class_offering_id)}">进入课堂</a></li>`).join('')}</ol>`
      : `<div class="dw-schedule-empty"><strong>${courses.length ? '这一周没有已排定课程' : '还没有加入课堂'}</strong><p>${courses.length ? '未排定、本周无课和往期课堂都在“全部课程”中。' : '加入课堂后即可查看课程安排。'}</p><button class="dw-button" type="button" data-student-show-courses>查看全部课程</button></div>`;
  };
  const updateWeek = (week, index) => {
    activeIndex = index;
    const weeks = overview?.weeks || [];
    find('week-label').textContent = week ? `${week.label}${week.is_current ? ' · 本周' : ''}  ${week.date_range_label || ''}` : '暂无已排定周次';
    find('week-prev').disabled = !week || index <= 0;
    find('week-next').disabled = !week || index >= weeks.length - 1;
    find('week-today').disabled = !weeks.some(entry => entry.is_current);
    find('schedule-expand').disabled = !week;
    renderAgenda(week);
  };
  const deck = createScheduleDeck(panes['3d'], {
    title: '本周课程', showTermSelect: false, compactSummary: true, onWeekChange: updateWeek,
    emptyHtml: () => '<strong>暂无已排定课表</strong><p>课程入口始终保留在“全部课程”中。</p>',
  });
  const applyMode = (next, animate = true) => {
    const previousPane = panes[mode];
    mode = next;
    if (next !== 'courses') save({ mode: next });
    const ticket = ++transition;
    buttons.forEach(button => button.setAttribute('aria-pressed', String(button.dataset.studentScheduleMode === next)));
    find('week-nav').hidden = next === 'courses';
    find('schedule-term').parentElement.hidden = next === 'courses';
    find('schedule-hint').hidden = next !== '3d';
    const reveal = () => {
      if (ticket !== transition) return;
      Object.entries(panes).forEach(([name, pane]) => { pane.hidden = name !== next; });
      if (animate && !matchMedia('(prefers-reduced-motion: reduce)').matches) panes[next].animate?.([{ opacity: .2, transform: 'translateY(4px)' }, { opacity: 1, transform: 'translateY(0)' }], { duration: 180, easing: 'ease-out' });
      if (next === 'courses') renderCourses();
    };
    if (animate && previousPane !== panes[next] && !previousPane.hidden && previousPane.animate && !matchMedia('(prefers-reduced-motion: reduce)').matches) {
      previousPane.animate([{ opacity: 1 }, { opacity: .2 }], { duration: 100 }).finished.then(reveal).catch(reveal);
    } else reveal();
  };
  const updateCourseOptions = () => {
    const selected = find('course-term').value;
    const semesters = [...new Set(courses.map(course => course.semester || '未配置学期'))];
    find('course-term').innerHTML = '<option value="">全部学期</option>' + semesters.map(semester => `<option value="${escapeHtml(semester)}">${escapeHtml(semester)}</option>`).join('');
    find('course-term').value = semesters.includes(selected) ? selected : '';
    renderCourses();
  };
  const load = async (selected = '', keepWeek = false) => {
    controller?.abort(); controller = new AbortController();
    const request = controller;
    const ticket = ++sequence;
    requestedTerm = selected;
    panes['3d'].setAttribute('aria-busy', 'true');
    find('schedule-feedback').textContent = '正在读取课程安排…';
    find('schedule-retry').hidden = true;
    find('schedule-term').disabled = !(overview?.terms?.length);
    if (overview && selected !== termKey(overview.selected_term)) deck.setOverview(null);
    try {
      const [year, term] = selected.split('|');
      const query = new URLSearchParams(year ? { year, term: term || '' } : {});
      const response = await fetch(`/api/dashboard/course-schedule/overview?${query}`, { signal: request.signal, credentials: 'same-origin', headers: { Accept: 'application/json' } });
      const data = await response.json().catch(() => ({}));
      if (ticket !== sequence) return;
      if (!response.ok || data.status !== 'success') {
        if (response.status === 401 || response.status === 403) { courses = []; updateCourseOptions(); overview = null; deck.setOverview(null); }
        throw new Error(data.detail || '课程日程暂时无法加载，请重试。');
      }
      overview = data.overview;
      courses = Array.isArray(overview.authorized_courses) ? overview.authorized_courses : [];
      updateCourseOptions();
      // Discard an obsolete user preference after membership/semester changes.
      if (selected && !overview.selected_term && overview.terms?.length) { save({ term: '' }); void load(''); return; }
      const terms = overview.terms || [];
      find('schedule-term').innerHTML = terms.length ? terms.map(entry => `<option value="${escapeHtml(termKey(entry))}">${escapeHtml(entry.label)}${entry.status === 'ended' ? ' · 往期' : ''}</option>`).join('') : '<option value="">暂无已配置学期</option>';
      find('schedule-term').value = termKey(overview.selected_term);
      save({ term: termKey(overview.selected_term) });
      find('schedule-feedback').textContent = overview.message || (courses.length ? '本平台课程安排 · 本周无课和未排定课程也可从全部课程进入。' : '暂无已加入的课堂。');
      deck.setOverview(overview, { keepWeek });
      lastLoaded = Date.now();
    } catch (error) {
      if (request.signal.aborted || ticket !== sequence) return;
      find('schedule-feedback').textContent = error.message || '读取失败，请重试。';
      find('schedule-retry').hidden = false;
    } finally {
      if (ticket === sequence) {
        panes['3d'].setAttribute('aria-busy', 'false');
        find('schedule-term').disabled = !(overview?.terms?.length);
      }
    }
  };
  buttons.forEach(button => button.addEventListener('click', () => applyMode(button.dataset.studentScheduleMode)));
  find('week-prev').addEventListener('click', () => deck.goToWeek(activeIndex - 1));
  find('week-next').addEventListener('click', () => deck.goToWeek(activeIndex + 1));
  find('week-today').addEventListener('click', () => { const index = overview?.weeks?.findIndex(week => week.is_current) ?? -1; if (index >= 0) deck.goToWeek(index); });
  find('schedule-expand').addEventListener('click', () => deck.openExpanded());
  find('schedule-term').addEventListener('change', event => void load(event.target.value));
  find('schedule-retry').addEventListener('click', () => void load(requestedTerm, true));
  find('course-search').addEventListener('input', renderCourses);
  find('course-term').addEventListener('change', renderCourses);
  find('course-state').addEventListener('change', renderCourses);
  panel.addEventListener('click', event => {
    if (event.target.closest('[data-student-show-courses]')) applyMode('courses');
    if (event.target.closest('[data-student-course-reset]')) { ['course-search', 'course-term', 'course-state'].forEach(name => { find(name).value = ''; }); renderCourses(); }
  });
  const onVisible = () => { if (document.visibilityState === 'visible' && Date.now() - lastLoaded > 60_000) void load(termKey(overview?.selected_term), true); };
  document.addEventListener('visibilitychange', onVisible);
  window.addEventListener('pagehide', event => {
    if (event.persisted) return;
    controller?.abort(); document.removeEventListener('visibilitychange', onVisible); deck.destroy();
  });
  updateCourseOptions();
  applyMode(root.dataset.initialSearch ? 'courses' : mode, false);
  void load(typeof saved.term === 'string' ? saved.term : '');
}
