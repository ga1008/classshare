import { expect, test, type Locator, type Page, type TestInfo } from '@playwright/test';
import { loginStudent, loginTeacher } from '../fixtures/p03';
import { readS3Fixture } from '../fixtures/lq-s3';
import { installTiming, painted } from '../fixtures/lq-performance';

test.afterEach(async ({ page }, info) => {
  if (page.isClosed()) return;
  const graphs = await page.evaluate(() => [...new Set(performance.getEntriesByType('resource')
    .map(entry => entry.name.match(/\/static\/assets\/([a-f0-9]{64})\//)?.[1]).filter(Boolean))]);
  await info.attach('production-asset-graphs', { body: JSON.stringify(graphs), contentType: 'application/json' });
  if (process.env.LQ_UI_REPAIR_EXPECT_GRAPH) expect(graphs).toEqual([process.env.LQ_UI_REPAIR_EXPECT_GRAPH]);
});

async function prepare(page: Page, role: 'teacher' | 'student', appearance: 'light' | 'dark', glass = true) {
  const fixture = readS3Fixture();
  const health = await page.request.get('/api/internal/health');
  expect(health.status()).toBe(200);
  expect((await health.json()).database_path).toBe(fixture.databasePath);
  await page.route('**/*', route => new URL(route.request().url()).hostname === '127.0.0.1'
    ? route.continue() : route.abort('blockedbyclient'));
  await (role === 'teacher' ? loginTeacher(page, fixture) : loginStudent(page, fixture));
  const pref = (await (await page.request.get('/api/profile/ui-preferences')).json()).preferences;
  const saved = await page.request.patch('/api/profile/ui-preferences', {
    headers: { 'X-UI-Preferences-Context': pref.context_token },
    data: { backdrop: 'scene', appearance, glass: glass ? 'tinted' : 'off', version: pref.version },
  });
  expect(saved.status()).toBe(200);
  return fixture;
}

async function surface(node: Locator, glass = true) {
  await expect(node).toBeVisible();
  const value = await node.evaluate(el => {
    const css = getComputedStyle(el), box = el.getBoundingClientRect();
    return { filter: css.backdropFilter, fill: css.backgroundColor, width: box.width,
      left: box.left, right: box.right, viewport: innerWidth, text: css.color };
  });
  expect(value.width).toBeGreaterThan(120);
  expect(value.left).toBeGreaterThanOrEqual(-1);
  expect(value.right).toBeLessThanOrEqual(value.viewport + 1);
  if (glass) expect(value.filter).toMatch(/blur\((?!0px)/);
  else {
    expect(value.filter).toBe('none');
    expect(value.fill).not.toMatch(/rgba|transparent/);
  }
  return value;
}

async function padded(node: Locator) {
  const padding = await node.evaluate(el => {
    const css = getComputedStyle(el);
    return [css.paddingTop, css.paddingRight, css.paddingBottom, css.paddingLeft].map(parseFloat);
  });
  expect(Math.min(...padding), `padding ${padding.join(', ')}`).toBeGreaterThanOrEqual(12);
}

async function paintedBlurBudget(page: Page) {
  const hosts = await page.evaluate(() => Array.from(document.querySelectorAll('*')).flatMap(el => {
    const rect = el.getBoundingClientRect(), css = getComputedStyle(el);
    if (!rect.width || !rect.height || css.visibility === 'hidden' || el.closest('[hidden]')) return [];
    return [null, '::before', '::after'].flatMap(pseudo => {
      const style = pseudo ? getComputedStyle(el, pseudo) : css;
      if (pseudo && ['none', 'normal', ''].includes(style.content)) return [];
      if (!style.backdropFilter || style.backdropFilter === 'none') return [];
      return [{ tag: el.tagName, id: el.id, cls: el.className, pseudo, filter: style.backdropFilter,
        leaf: !!el.closest('button,input,select,textarea,[role="button"],[role="menuitem"]') }];
    });
  }));
  expect(hosts.filter(host => host.leaf)).toEqual([]);
  expect(hosts.length, JSON.stringify(hosts)).toBeLessThanOrEqual(3);
  return hosts;
}

async function capture(page: Page, name: string, testInfo: TestInfo) {
  await page.screenshot({ path: testInfo.outputPath(`${name}.png`), fullPage: false });
}

async function menuReady(page: Page, triggerId: string) {
  // SSR buttons paint before their deferred owner. Await that exact owner,
  // without arbitrary sleeps or repeated clicks that could close a ready menu.
  await page.waitForFunction(id => (document as any)[Symbol.for('lanshare.lq.nav-menu-hosts.v1')]
    ?.has(document.getElementById(id)?.closest('[data-lq-nav-menu]')), triggerId);
}

for (const role of ['teacher', 'student'] as const) for (const appearance of ['light', 'dark'] as const) {
  test(`${role} ${appearance}: classroom arrows, readable information and material spacing`, async ({ page }, testInfo) => {
    const fixture = await prepare(page, role, appearance);
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(`/classroom/${fixture.classOfferingId}`);
    await expect(page.locator('#hero-course-detail-btn')).toBeVisible();
    const arrows = page.locator('#teachingTimelinePrevBtn svg, #teachingTimelineNextBtn svg');
    await expect(arrows).toHaveCount(2);
    for (const arrow of await arrows.all()) {
      await expect(arrow).toBeVisible();
      const box = await arrow.boundingBox();
      expect(box?.width).toBeGreaterThanOrEqual(16);
      expect(box?.height).toBeGreaterThanOrEqual(16);
    }
    const rail = page.locator('#teachingTimelineScroll');
    await rail.evaluate(el => { el.scrollLeft = 0; });
    const selected = await page.locator('[data-session-select][aria-pressed="true"]').getAttribute('data-session-order');
    await page.locator('#teachingTimelineNextBtn').click();
    await expect.poll(() => rail.evaluate(el => el.scrollLeft)).toBeGreaterThan(80);
    expect(await page.locator('[data-session-select][aria-pressed="true"]').getAttribute('data-session-order')).toBe(selected);
    const opener = page.locator('#hero-course-detail-btn');
    await opener.click();
    const dialog = page.locator('#course-info-popover [role="dialog"]');
    const measurements = await surface(dialog);
    await padded(page.locator('[data-course-popover-panel="details"]'));
    await capture(page, `course-details-${role}-${appearance}`, testInfo);
    const hosts = await paintedBlurBudget(page);
    await page.keyboard.press('Escape');
    await expect(dialog).toBeHidden();
    await expect(opener).toBeFocused();
    await opener.click();
    await page.locator('#course-popover-close').click();
    await expect(dialog).toBeHidden();
    await expect(opener).toBeFocused();
    await opener.click();
    await page.locator('#course-popover-overlay').click({ position: { x: 2, y: 2 } });
    await expect(dialog).toBeHidden();
    await expect(opener).toBeFocused();
    await page.locator('[data-cw-open="materials"]').click();
    const materials = page.getByRole('dialog', { name: '全部课堂材料', exact: true });
    await expect(materials).toBeVisible();
    await padded(materials.locator('#classroom-material-collection'));
    await expect(materials.getByText('协议分层学习讲义.md', { exact: true }).first()).toBeVisible();
    await capture(page, `materials-${role}-${appearance}`, testInfo);
    await paintedBlurBudget(page);
    await page.keyboard.press('Escape');
    await expect(materials).toBeHidden();
    await expect(page.locator('[data-cw-open="materials"]')).toBeFocused();
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
    expect(errors).toEqual([]);
    await testInfo.attach('surface-evidence', { body: JSON.stringify({ measurements, hosts }, null, 2), contentType: 'application/json' });
  });
}

test('classroom menus preserve hover, click, keyboard and domain dialog handoff', async ({ page }, testInfo) => {
  const fixture = await prepare(page, 'teacher', 'light');
  await page.goto(`/classroom/${fixture.classOfferingId}`);
  const more = page.locator('#classroom-more-menu--lq-trigger');
  const personal = page.locator('#classroom-personal-menu--lq-trigger');
  await menuReady(page, 'classroom-more-menu--lq-trigger');
  const overlapping = await page.locator('.cw-topbar').evaluate(root => {
    const controls = Array.from(root.querySelectorAll('.cw-nav-link,[data-lq-nav-trigger]'))
      .map(el => ({ text: el.textContent?.trim(), box: el.getBoundingClientRect() }))
      .filter(({ box }) => box.width > 0 && box.height > 0);
    return controls.flatMap((a, index) => controls.slice(index + 1).flatMap(b =>
      Math.min(a.box.right, b.box.right) - Math.max(a.box.left, b.box.left) > 1
        && Math.min(a.box.bottom, b.box.bottom) - Math.max(a.box.top, b.box.top) > 1
        ? [`${a.text} overlaps ${b.text}`] : []));
  });
  expect(overlapping).toEqual([]);
  const fine = testInfo.project.name === 'desktop';
  await (fine ? more.hover() : more.click());
  const menu = page.locator('#classroom-more-menu');
  await surface(menu);
  if (fine) {
    await menu.hover();
    await expect(menu).toBeVisible();
  }
  await capture(page, 'more-menu', testInfo);
  await menu.locator('[data-course-popover-target="details"]').click();
  const details = page.locator('#course-info-popover [role="dialog"]');
  await expect(details).toBeVisible();
  await expect(menu).toBeHidden();
  await page.keyboard.press('Escape');
  await expect(details).toBeHidden();
  await expect(more).toBeFocused();
  await more.press('ArrowDown');
  await expect(menu).toBeVisible();
  await expect(menu.locator(':focus')).toHaveCount(1);
  const detailsItem = menu.locator('[data-course-popover-target="details"]');
  await detailsItem.focus();
  expect(await detailsItem.locator('small').evaluate(el => getComputedStyle(el).color))
    .toBe(await detailsItem.evaluate(el => getComputedStyle(el).color));
  await page.keyboard.press('Escape');
  await expect(more).toBeFocused();
  await (fine ? personal.hover() : personal.click());
  const personalMenu = page.locator('#classroom-personal-menu');
  await surface(personalMenu);
  await page.evaluate(() => window.dispatchEvent(new CustomEvent('message-center:summary-updated', { detail: { unread_total: 7 } })));
  await expect(personalMenu.locator('[data-message-center-bell-count]')).toHaveText('7');
  await expect(personalMenu.locator('[data-message-center-bell-caption]')).toHaveText('未读 7 条');
  await capture(page, 'personal-menu', testInfo);
  await personalMenu.locator('[data-open-feedback]').click();
  await expect(page.locator('#feedback-modal')).toBeVisible();
  await expect(personalMenu).toBeHidden();
  await page.keyboard.press('Escape');
  await expect(page.locator('#feedback-modal')).toBeHidden();
  await expect(personal).toBeFocused();
  await paintedBlurBudget(page);
});

for (const role of ['teacher', 'student'] as const) test(`${role}: dashboard schedule menu and calendar retain business hooks`, async ({ page }, testInfo) => {
  await prepare(page, role, 'dark');
  await page.goto('/dashboard');
  const trigger = page.locator('#dashboard-schedule-tools--lq-trigger');
  await menuReady(page, 'dashboard-schedule-tools--lq-trigger');
  await (testInfo.project.name === 'desktop' ? trigger.hover() : trigger.click());
  const menu = page.locator('#dashboard-schedule-tools');
  await surface(menu);
  await capture(page, `dashboard-tools-${role}`, testInfo);
  await menu.locator('[data-agenda-calendar-feed]').click();
  const feed = page.getByRole('dialog', { name: '订阅到手机日历', exact: true });
  await expect(feed).toBeVisible();
  await expect(menu).toBeHidden();
  await expect(feed.locator('[data-feed-url]')).not.toHaveValue('加载中…');
  await page.keyboard.press('Escape');
  await expect(feed).toBeHidden();
  await expect(trigger).toBeFocused();
  const calendar = page.locator('.ls-page-head [data-ls-open="calendar"]');
  await calendar.click();
  const dialog = page.getByRole('dialog', { name: '日程与事项', exact: true });
  await expect(dialog).toBeVisible();
  await expect(dialog.locator('.semester-day-cell').first()).toBeAttached();
  await page.keyboard.press('Escape');
  await expect(dialog).toBeHidden();
  await expect(calendar).toBeFocused();
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
});

test('glass-off information dialog remains opaque with no repeated row blur', async ({ page }, testInfo) => {
  const fixture = await prepare(page, 'teacher', 'dark', false);
  await page.goto(`/classroom/${fixture.classOfferingId}`);
  await expect(page.locator('html')).toHaveAttribute('data-lq-glass', 'off');
  await page.locator('#hero-course-detail-btn').click();
  await surface(page.locator('#course-info-popover [role="dialog"]'), false);
  await padded(page.locator('[data-course-popover-panel="details"]'));
  const hosts = await paintedBlurBudget(page);
  expect(hosts).toEqual([]);
  await capture(page, 'course-details-glass-off', testInfo);
});

test('information dialog blocks closing background actions, reopens and closes attendance child first', async ({ page }, testInfo) => {
  const fixture = await prepare(page, 'teacher', 'light');
  await page.route(`**/api/classrooms/${fixture.classOfferingId}/smart-attendance/analytics`, route => route.fulfill({
    contentType: 'application/json', body: JSON.stringify({
      summary: { has_data: true, course_name: '合成出勤课程', attendance_rate: 90, coverage_rate: 100,
        checked: 9, total: 10, abnormal: 1, absent: 1, synced_session_count: 1, total_session_count: 1 },
      students: [{ student_name: '合成学生', student_number: 'TEST01', abnormal_count: 1,
        absent: 1, total: 10, checked: 9, attendance_rate: 90, risk_level: 'watch' }],
      session_chart: [{ label: '第1次课', abnormal: 1, total: 10, rate: 90 }],
      weekly_trend: [], course_comparisons: [], insights: [],
    }),
  }));
  await page.goto(`/classroom/${fixture.classOfferingId}`);
  const opener = page.locator('#hero-course-detail-btn');
  const dialog = page.locator('#course-info-popover [role="dialog"]');
  await opener.click();
  await expect(dialog).toBeVisible();
  await painted(page);
  await installTiming(page);
  await page.evaluate(() => (window as any).__lqPilotTiming.reset('course-dialog-roundtrip'));
  for (let index = 0; index < 4; index++) {
    // The modal retains inert background until its exit completes. A background
    // click during closing must not reverse it or create a second owner.
    await page.evaluate(() => {
      document.getElementById('course-popover-close')!.click();
      document.getElementById('hero-course-detail-btn')!.click();
    });
    await expect(dialog).toBeHidden();
    await expect(opener).toBeFocused();
    await opener.click();
    await expect(dialog).toBeVisible();
    await expect(page.locator('#course-info-popover')).toHaveAttribute('data-lq-layer-state', 'open');
  }
  await page.keyboard.press('Escape');
  await expect(dialog).toBeHidden();
  await expect(opener).toBeFocused();
  const timing = await page.evaluate(() => (window as any).__lqPilotTiming.snapshot());
  await testInfo.attach('dialog-timing', { body: JSON.stringify(timing, null, 2), contentType: 'application/json' });
  const more = page.locator('#classroom-more-menu--lq-trigger');
  await more.click();
  await page.locator('#classroom-more-menu [data-course-popover-target="stats"]').click();
  await expect(dialog).toBeVisible();
  const abnormal = page.locator('[data-smart-attendance-abnormal-trigger]').first();
  await abnormal.click();
  const child = page.locator('#smartAttendanceAbnormalPopover');
  await expect(child).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(child).toBeHidden();
  await expect(dialog).toBeVisible();
  await expect(abnormal).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(dialog).toBeHidden();
  await expect(more).toBeFocused();
  expect(await page.evaluate(() => !!document.querySelector('main[inert]'))).toBe(false);
});

for (const appearance of ['light', 'dark'] as const) test(`${appearance}: selected session labels inherit the shared button ink`, async ({ page }, testInfo) => {
  const fixture = await prepare(page, 'teacher', appearance);
  await page.goto(`/classroom/${fixture.classOfferingId}`);
  const selected = page.locator('[data-session-select][aria-pressed="true"]');
  await expect(selected).toHaveCount(1);
  const colors = await selected.evaluate(el => ({
    button: getComputedStyle(el).color,
    title: getComputedStyle(el.querySelector('.teaching-timeline-segment-title')!).color,
    order: getComputedStyle(el.querySelector('.teaching-timeline-segment-order')!).color,
  }));
  expect(colors.title).toBe(colors.button);
  expect(colors.order).toBe(colors.button);
  await capture(page, `selected-session-${appearance}`, testInfo);
});

for (const role of ['teacher', 'student'] as const) test(`${role}: topbar fits at 320, 390 and 700 without overlapping actions`, async ({ page }, testInfo) => {
  const fixture = await prepare(page, role, 'light');
  await page.goto(`/classroom/${fixture.classOfferingId}`);
  await menuReady(page, 'classroom-more-menu--lq-trigger');
  for (const width of [320, 390, 700]) {
    await page.setViewportSize({ width, height: 900 });
    await painted(page);
    const results = await page.locator('.cw-topbar').evaluate(root => {
      const controls = Array.from(root.querySelectorAll('.cw-nav-link,[data-lq-nav-trigger]'))
        .map(el => ({ text: el.textContent?.trim(), box: el.getBoundingClientRect(),
          clipped: el.scrollWidth > el.clientWidth + 1 }))
        .filter(({ box }) => box.width > 0 && box.height > 0);
      const overlaps = controls.flatMap((a, index) => controls.slice(index + 1).flatMap(b =>
        Math.min(a.box.right, b.box.right) - Math.max(a.box.left, b.box.left) > 1
          && Math.min(a.box.bottom, b.box.bottom) - Math.max(a.box.top, b.box.top) > 1
          ? [`${a.text} overlaps ${b.text}`] : []));
      return { overlaps, clipped: controls.filter(item => item.clipped).map(item => item.text),
        outOfBounds: controls.filter(item => item.box.left < 0 || item.box.right > innerWidth + 1).map(item => item.text),
        pageOverflow: document.documentElement.scrollWidth - innerWidth };
    });
    expect(results.overlaps).toEqual([]);
    expect(results.clipped).toEqual([]);
    expect(results.outOfBounds).toEqual([]);
    expect(results.pageOverflow).toBeLessThanOrEqual(1);
    await capture(page, `topbar-${role}-${width}`, testInfo);
  }
});

for (const role of ['teacher', 'student'] as const) test(`${role}: dashboard topbar actions and content fit narrow screens`, async ({ page }, testInfo) => {
  await prepare(page, role, 'light');
  await page.goto('/dashboard');
  await menuReady(page, 'app-personal-menu--lq-trigger');
  for (const width of [320, 390, 700]) {
    await page.setViewportSize({ width, height: 900 });
    await painted(page);
    const geometry = await page.locator('.app-topbar').evaluate(root => {
      const controls = Array.from(root.querySelectorAll('a,button,select')).filter(el =>
        el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true }) && !el.closest('[hidden],[inert]')).map(el => ({
        id: el.id, text: el.textContent?.trim().slice(0, 60), box: el.getBoundingClientRect(),
      })).filter(({ box }) => box.width > 0 && box.height > 0);
      const overlaps = controls.flatMap((a, index) => controls.slice(index + 1).flatMap(b =>
        Math.min(a.box.right, b.box.right) - Math.max(a.box.left, b.box.left) > 1
          && Math.min(a.box.bottom, b.box.bottom) - Math.max(a.box.top, b.box.top) > 1
          ? [{ a, b }] : []));
      return { controls, overlaps,
        outOfBounds: controls.filter(({ box }) => box.left < -1 || box.right > innerWidth + 1),
        pageOverflow: document.documentElement.scrollWidth - innerWidth };
    });
    await testInfo.attach(`dashboard-topbar-${role}-${width}`, { body: JSON.stringify(geometry), contentType: 'application/json' });
    await capture(page, `dashboard-topbar-${role}-${width}`, testInfo);
    expect.soft(geometry.overlaps).toEqual([]);
    expect.soft(geometry.outOfBounds).toEqual([]);
    expect.soft(geometry.pageOverflow).toBeLessThanOrEqual(1);
  }
  if (role === 'teacher') {
    await padded(page.locator('#dashboard-class-list'));
    for (const card of await page.locator('.ls-domain-card').all()) await padded(card);
  }
  await page.setViewportSize({ width: 390, height: 900 });
  await page.goto(role === 'teacher' ? '/manage/me/settings' : '/profile?section=settings');
  await page.locator('#profile-name').scrollIntoViewIfNeeded();
  await painted(page);
  await capture(page, `profile-settings-${role}-390`, testInfo);
});

test('material panel keeps empty, failure and successful retry states inside padded surface', async ({ page }, testInfo) => {
  const fixture = await prepare(page, 'teacher', 'light');
  let mode = 'empty';
  await page.route(`**/api/classrooms/${fixture.classOfferingId}/materials`, route => {
    if (mode === 'real') return route.continue();
    return route.fulfill({ status: mode === 'error' ? 503 : 200, contentType: 'application/json',
      body: JSON.stringify(mode === 'error' ? { detail: '合成网络错误，请重试' } : { items: [], breadcrumbs: [] }) });
  });
  await page.goto(`/classroom/${fixture.classOfferingId}`);
  const open = page.locator('[data-cw-open="materials"]');
  await open.click();
  const dialog = page.getByRole('dialog', { name: '全部课堂材料', exact: true });
  await expect(dialog.getByText('当前课堂还没有分配课程材料。')).toBeVisible();
  await padded(dialog.locator('#classroom-material-collection'));
  await page.keyboard.press('Escape');
  await expect(dialog).toBeHidden();
  mode = 'error';
  // The directory intentionally caches its successful first load; a fresh page
  // exercises initial-load failure instead of mistaking retained data for a retry.
  await page.reload();
  await open.click();
  await expect(dialog.getByText(/加载材料失败：合成网络错误/)).toBeVisible();
  await capture(page, 'materials-error', testInfo);
  mode = 'real';
  await dialog.locator('#classroom-materials-refresh-btn').click();
  await expect(dialog.getByText('协议分层学习讲义.md', { exact: true }).first()).toBeVisible();
  await expect(dialog.locator('.materials-empty')).toHaveCount(0);
});
