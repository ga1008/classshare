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

test('student schedule uses the platform endpoint, persists the view and filters actual lessons', async ({ page }) => {
  const errors: string[] = [];
  const scheduleRequests: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => { if (request.url().includes('course-schedule/overview')) scheduleRequests.push(request.url()); });
  await loginStudent(page, readFixture());
  await page.locator('.dw-course-options summary').click();
  await page.locator('[data-group-mode="schedule3d"]').click();
  await expect(page.locator('.dashboard-schedule3d__notice')).toHaveText('仅显示本平台课程，所有课程请查看教务系统');
  await expect(page.locator('.dashboard-schedule3d__status')).toBeHidden();
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
  await page.locator('[data-dashboard-search]').fill('no-such-course-789xyz');
  await expect(page.locator('.cs-lesson--mini')).toHaveCount(0);
  await page.locator('[data-dashboard-search]').fill('');
  await expect(page.locator('.cs-lesson--mini').first()).toBeAttached();
  await page.reload();
  await expect(page.locator('[data-group-mode="schedule3d"]')).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('.dashboard-schedule3d__notice')).toBeVisible();
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
    await route.fulfill({ json: { overview: { terms, selected_term: selected, section_range: { min: 1, max: 11 },
      weeks: [{ week_index: 1, label: selected.label, lesson_count: 0, total_hours: 0, lessons: [] }] } } });
  });
  await loginStudent(page, readFixture());
  await page.locator('.dw-course-options summary').click();
  await page.locator('[data-group-mode="schedule3d"]').click();
  await page.locator('.cs-deck-term').selectOption('semester-90|0');
  await expect(page.locator('.cs-card.is-active .cs-card__bar strong')).toHaveText('自定义学期二');
  await page.locator('[data-dashboard-search]').fill('课程');
  await expect(page.locator('[data-results-summary]')).toContainText('关键词：课程');
  await expect(page.locator('.cs-card.is-active .cs-card__bar strong')).toHaveText('自定义学期二');
  await expect(page.locator('.cs-deck-term')).toHaveValue('semester-90|0');
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
