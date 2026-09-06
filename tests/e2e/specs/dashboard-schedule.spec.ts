import { expect, test } from '@playwright/test';
import { loginStudent, loginTeacher, readFixture } from '../fixtures/p03';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

test.beforeAll(() => {
  const python = process.platform === 'win32' ? 'venv/Scripts/python.exe' : 'venv/bin/python';
  execFileSync(fs.existsSync(python) ? path.resolve(python) : 'python',
    ['tests/e2e/scripts/prepare_schedule_fixture.py', readFixture().runtimeRoot]);
});

test('teacher defaults, explicit all and saved choices survive reload with aligned filters', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await loginTeacher(page, readFixture());
  await expect(page.locator('[data-filter-value="recent"]')).toHaveAttribute('aria-current', 'true');
  await expect(page.locator('[data-group-mode="schedule3d"]')).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('.cs-stage')).toBeVisible();
  const currentSemester = await page.locator('[data-dashboard-root]').getAttribute('data-current-semester-key');
  expect(currentSemester).toMatch(/^\d{4}-\d{4}-[123]$/);
  await expect(page.locator('[data-semester-filter]')).toHaveValue(currentSemester!);
  await expect(page.locator('.cs-lesson--mini').first()).toBeAttached();
  for (const width of [1440, 1024, 390]) {
    await page.setViewportSize({ width, height: 980 });
    const search = await page.locator('.dw-course-search').boundingBox();
    const options = await page.locator('.dw-course-options').boundingBox();
    expect(Math.abs(search!.x - options!.x)).toBeLessThan(2);
    expect(options!.y).toBeGreaterThanOrEqual(search!.y + search!.height);
    expect(options!.x + options!.width).toBeLessThanOrEqual(width);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  }
  await page.locator('[data-filter-value="all"]').click();
  await page.locator('[data-group-mode="flat"]').click();
  if (await page.locator('[data-semester-filter]').count()) await page.locator('[data-semester-filter]').selectOption('');
  await expect(page).toHaveURL(/filter=all/);
  await page.reload();
  await expect(page.locator('[data-filter-value="all"]')).toHaveAttribute('aria-current', 'true');
  // All closes its disclosure on reload; choices remain active inside it.
  await page.locator('.dw-course-options summary').click();
  await expect(page.locator('[data-group-mode="flat"]')).toHaveAttribute('aria-pressed', 'true');
  if (await page.locator('[data-semester-filter]').count()) await expect(page.locator('[data-semester-filter]')).toHaveValue('');
  expect(errors).toEqual([]);
});

test('student schedule defaults to 3D and collection filtering preserves its independent week', async ({ page }) => {
  const errors: string[] = [];
  const scheduleRequests: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => { if (request.url().includes('course-schedule/overview')) scheduleRequests.push(request.url()); });
  await loginStudent(page, readFixture());
  await expect(page.locator('[data-student-schedule-mode="3d"]')).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('#dashboard-class-list')).toHaveCount(0);
  await expect(page.locator('.cs-stage')).toBeVisible();
  expect(scheduleRequests.length).toBeGreaterThan(0);
  expect(scheduleRequests.every(url => url.includes('/api/dashboard/course-schedule/overview'))).toBe(true);
  const payload = await (await page.request.get('/api/dashboard/course-schedule/overview')).json();
  expect(payload.status).toBe('success');
  expect(payload.overview.weeks.flatMap((week: any) => week.lessons).length).toBeGreaterThan(0);
  for (const week of payload.overview.weeks) for (const lesson of week.lessons) {
    expect(lesson.classroom_url).toBe(`/classroom/${lesson.class_offering_id}`);
    expect(lesson.create_url || '').toBe('');
  }
  const activeWeek = await page.locator('.cs-card.is-active .cs-card__bar strong').textContent();
  const requestsBeforeModes = scheduleRequests.length;
  await page.locator('[data-student-schedule-mode="courses"]').click();
  await page.locator('[data-student-course-search]').fill('no-such-course-789xyz');
  await expect(page.locator('[data-student-course-list]')).toContainText('没有匹配的课程');
  await page.locator('[data-student-schedule-mode="3d"]').click();
  await expect(page.locator('.cs-card.is-active .cs-card__bar strong')).toHaveText(activeWeek!);
  await expect(page.locator('.cs-lesson--mini').first()).toBeAttached();
  expect(scheduleRequests.length).toBe(requestsBeforeModes);
  await page.locator('[data-student-schedule-mode="agenda"]').click();
  await page.reload();
  await expect(page.locator('[data-student-schedule-mode="agenda"]')).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('[data-student-schedule-agenda]')).toBeVisible();
  expect(errors).toEqual([]);
});

test('latest semester response wins and retry keeps the selected semester', async ({ page }) => {
  await loginTeacher(page, readFixture());
  // Synthetic network timing only; the production dashboard and deck modules run unchanged.
  const overview = (year: string) => ({ terms: [{ year, term: '1', label: year }], selected_term: { year, term: '1', label: year, focus_week: 1 },
    section_range: { min: 1, max: 11 }, weeks: [{ week_index: 1, label: year, is_current: true, lesson_count: 0, total_hours: 0, lessons: [] }] });
  let delayed: any;
  let fail = true;
  await page.route('**/api/manage/teaching/course-schedule/overview?**', async route => {
    const year = new URL(route.request().url()).searchParams.get('year') || '';
    if (year === '2024-2025') { delayed = route; return; }
    if (year === '2025-2026' && fail) { await route.fulfill({ status: 503, body: '{}' }); return; }
    await route.fulfill({ json: { overview: overview(year) } });
  });
  const semester = page.locator('[data-semester-filter]');
  await semester.evaluate((select: HTMLSelectElement) => {
    for (const year of ['2024-2025', '2025-2026']) select.add(new Option(year, `${year}-1`));
  });
  await semester.selectOption('2024-2025-1');
  await expect.poll(() => Boolean(delayed)).toBe(true);
  await semester.selectOption('2025-2026-1');
  await expect(page.locator('[data-schedule3d-retry]')).toBeVisible();
  fail = false;
  await page.locator('[data-schedule3d-retry]').click();
  await expect(page.locator('.cs-card.is-active .cs-card__bar strong')).toHaveText('2025-2026');
  await delayed.fulfill({ json: { overview: overview('2024-2025') } }).catch(() => undefined);
  await expect(page.locator('.cs-card.is-active .cs-card__bar strong')).toHaveText('2025-2026');
});

test('custom deck term survives a search without silently returning to the default term', async ({ page }) => {
  const terms = [{ year: 'semester-80', term: '0', label: '自定义学期一' }, { year: 'semester-90', term: '0', label: '自定义学期二' }];
  await page.route('**/api/dashboard/course-schedule/overview?**', async route => {
    const selected = terms.find(term => term.year === new URL(route.request().url()).searchParams.get('year')) || terms[0];
    await route.fulfill({ json: { status: 'success', overview: { terms, selected_term: selected, authorized_courses: [], section_range: { min: 1, max: 11 },
      weeks: [{ week_index: 1, label: selected.label, lesson_count: 0, total_hours: 0, lessons: [] }] } } });
  });
  await loginStudent(page, readFixture());
  await page.locator('[data-student-schedule-term]').selectOption('semester-90|0');
  await expect(page.locator('.cs-card.is-active .cs-card__bar strong')).toHaveText('自定义学期二');
  await page.locator('[data-student-schedule-mode="courses"]').click();
  await page.locator('[data-student-course-search]').fill('课程');
  await expect(page.locator('[data-student-course-result]')).toContainText('仅筛选课程集合');
  await page.locator('[data-student-schedule-mode="3d"]').click();
  await expect(page.locator('.cs-card.is-active .cs-card__bar strong')).toHaveText('自定义学期二');
  await expect(page.locator('[data-student-schedule-term]')).toHaveValue('semester-90|0');
});

test('teacher third semester is requested explicitly and free-text terms do not substitute another semester', async ({ page }) => {
  await loginTeacher(page, readFixture());
  const requests: URL[] = [];
  await page.route('**/api/manage/teaching/course-schedule/overview?**', async route => {
    requests.push(new URL(route.request().url()));
    await route.fulfill({ json: { overview: { terms: [], weeks: [] } } });
  });
  const semester = page.locator('[data-semester-filter]');
  await semester.evaluate((select: HTMLSelectElement) => {
    select.add(new Option('第三学期', '2025-2026-3'));
    select.add(new Option('短学期', 'raw:短学期'));
  });
  await semester.selectOption('2025-2026-3');
  await expect.poll(() => requests.length).toBe(1);
  expect(requests[0].searchParams.get('year')).toBe('2025-2026');
  expect(requests[0].searchParams.get('term')).toBe('3');
  await semester.selectOption('raw:短学期');
  await expect(page.locator('.cs-empty')).toContainText('该学期暂无可用的3D课表');
  expect(requests.length).toBe(1);
});

test('student last-week inertia stays in the deck and a new boundary gesture scrolls the page', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 700 });
  await loginStudent(page, readFixture());
  await page.locator('[data-student-schedule-mode="3d"]').click();
  const stage = page.locator('[data-student-schedule] [data-csd-stage]');
  const active = stage.locator('.cs-card.is-active');
  await expect(active).toHaveCount(1);
  const weekCount = await stage.locator('.cs-card').count();
  expect(weekCount).toBeGreaterThan(1);
  // Use the actual keyboard navigation to position the existing deck. Do not
  // replace the schedule payload or reach into the controller's private state.
  await stage.focus();
  for (let index = 0; index < weekCount; index++) await page.keyboard.press('ArrowRight');
  await expect(page.locator('[data-student-week-next]')).toBeDisabled();
  await page.keyboard.press('ArrowLeft');
  await expect(active).toHaveAttribute('data-week-index', String(weekCount - 2));
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' }));

  // Dispatch one continuous trackpad-shaped stream inside the browser. Keeping
  // the 60ms gaps here avoids driver latency accidentally making a new gesture.
  // The tail continues beyond the 480ms animation lock: every tail event must
  // still be cancelled once the first event has advanced onto the last week.
  const gesture = await stage.evaluate(async node => {
    const scrollBefore = window.scrollY;
    const cancelled: boolean[] = [];
    for (const deltaY of [120, 28, 24, 20, 16, 12, 10, 8, 6, 4]) {
      const event = new WheelEvent('wheel', { deltaY, bubbles: true, cancelable: true });
      cancelled.push(!node.dispatchEvent(event));
      await new Promise(resolve => window.setTimeout(resolve, 60));
    }
    return { cancelled, scrollBefore, scrollAfter: window.scrollY };
  });
  expect(gesture.cancelled).toEqual(Array(10).fill(true));
  expect(gesture.scrollAfter).toBe(gesture.scrollBefore);
  await expect(active).toHaveAttribute('data-week-index', String(weekCount - 1));

  // A deliberate pause is the product's gesture boundary, not a loading wait.
  await page.waitForTimeout(260);
  const box = await stage.boundingBox();
  expect(box).not.toBeNull();
  expect(box!.y + 24).toBeLessThan(700);
  await page.mouse.move(box!.x + 24, box!.y + 24);
  const scrollBeforeNewGesture = await page.evaluate(() => window.scrollY);
  expect(await page.evaluate(() => document.documentElement.scrollHeight - innerHeight - scrollY)).toBeGreaterThan(30);
  // This is a native wheel event, so successful release must visibly scroll the
  // real page while the selected week remains on its last card.
  await page.mouse.wheel(0, 120);
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(scrollBeforeNewGesture + 2);
  await expect(active).toHaveAttribute('data-week-index', String(weekCount - 1));
});

test('a delayed real todo save cannot close or overwrite a newer unsaved draft', async ({ page }, testInfo) => {
  const fixture = readFixture();
  await loginStudent(page, fixture);
  const suffix = `${Date.now()}-${testInfo.workerIndex}`;
  const titleA = `延迟保存A-${suffix}`;
  const titleB = `未保存草稿B-${suffix}`;
  const notesB = '这是新打开的B草稿，旧请求返回后也必须保留。';
  const initialDocument = await page.evaluate(() => performance.timeOrigin);
  let releaseResponse = () => {};
  const responseGate = new Promise<void>(resolve => { releaseResponse = resolve; });
  let savedId = 0;
  let savedStatus = 0;
  let postCount = 0;
  const endpoint = `/api/classrooms/${fixture.classOfferingId}/todos`;
  await page.route(`**${endpoint}`, async route => {
    if (route.request().method() !== 'POST' || route.request().postDataJSON()?.title !== titleA) {
      await route.continue(); return;
    }
    postCount += 1;
    // Execute the real authenticated POST against the isolated fixture first.
    // Only delivery to the browser is delayed; no fabricated success is used.
    const response = await route.fetch();
    const payload = await response.json();
    savedStatus = response.status();
    savedId = Number(payload.id) || 0;
    await responseGate;
    await route.fulfill({ response });
  });
  const modal = page.locator('.agenda-todo-modal').filter({ has: page.locator('[data-todo-form]') });
  const add = page.locator('.dw-focus [data-agenda-add-todo]');
  try {
    await add.click();
    await expect(modal).toBeVisible();
    await modal.locator('[data-todo-course]').selectOption(String(fixture.classOfferingId));
    await modal.locator('input[name="title"]').fill(titleA);
    await modal.locator('[data-todo-submit]').click();
    await expect.poll(() => savedId).toBeGreaterThan(0);
    expect(savedStatus).toBe(200);
    await expect(modal.locator('[data-todo-submit]')).toBeDisabled();
    // A second submit event while the same form is pending must not issue a
    // second POST, including submissions initiated through keyboard/form APIs.
    await modal.locator('form').evaluate((form: HTMLFormElement) => form.requestSubmit());
    await modal.locator('button[data-todo-close][aria-label="关闭"]').click();
    await expect(modal).toBeHidden();
    await add.click();
    await expect(modal).toBeVisible();
    await modal.locator('input[name="title"]').fill(titleB);
    await modal.locator('[data-priority-value="high"]').click();
    await modal.locator('.agenda-todo-more > summary').click();
    await modal.locator('textarea[name="notes"]').fill(notesB);

    const refreshed = page.waitForResponse(response => response.url().includes('/api/dashboard/workspace?') && response.request().method() === 'GET');
    releaseResponse();
    await expect(page.locator('.dw-todo-notice')).toContainText('待办已添加');
    expect((await refreshed).ok()).toBe(true);
    await expect(modal).toBeVisible();
    await expect(modal.locator('input[name="title"]')).toHaveValue(titleB);
    await expect(modal.locator('textarea[name="notes"]')).toHaveValue(notesB);
    await expect(modal.locator('[data-priority-value="high"]')).toHaveAttribute('aria-pressed', 'true');
    await expect(modal.locator('input[name="due_date"]')).toHaveValue('');
    await expect(modal.locator('[data-todo-submit]')).toBeEnabled();
    expect(postCount).toBe(1);
    expect(await page.evaluate(() => performance.timeOrigin)).toBe(initialDocument);

    const saved = await page.request.get('/api/dashboard/workspace', { params: { item_key: `manual:${savedId}:${fixture.classOfferingId}` } });
    expect(saved.ok()).toBe(true);
    expect((await saved.json()).workspace.all_items.map((item: { title: string }) => item.title)).toEqual([titleA]);
    const unsaved = await page.request.get('/api/dashboard/workspace', { params: { q: titleB, kind: 'manual' } });
    expect((await unsaved.json()).workspace.filtered_total).toBe(0);
  } finally {
    releaseResponse();
    if (savedId) {
      const cleanup = await page.request.delete(`${endpoint}/${savedId}`);
      expect(cleanup.ok()).toBe(true);
    }
  }
});
