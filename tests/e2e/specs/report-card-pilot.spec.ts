import AxeBuilder from '@axe-core/playwright';
import type { Page } from '@playwright/test';
import { test, expect, guardS3Page, readS3Fixture, readS3Rows, type S3Fixture } from '../fixtures/lq-s3';
import { loginStudent, loginTeacher } from '../fixtures/p03';

const reportURL = (fixture: S3Fixture) => `/report-card?class_offering_id=${fixture.reportCard.offeringId}`;
const chartSelector = '[data-lq-report-card] [data-report-chart]';

// Observe only the dedicated S3 score/publication fixtures. The existing
// cultivation-profile GET can refresh its own projection; this is not a claim
// that every background GET on the legacy student base is database-read-only.
function gradeSnapshot(fixture: S3Fixture) {
  const ids = Object.values(fixture.reportCard.assignmentIds).map(String);
  const placeholders = ids.map(() => '?').join(',');
  return {
    submissions: readS3Rows(`SELECT id,assignment_id,student_pk_id,score,status,is_absence_score,resubmission_allowed,returned_at,feedback_md FROM submissions WHERE assignment_id IN (${placeholders}) ORDER BY id`, ids),
    publications: readS3Rows('SELECT * FROM grade_publications WHERE id=?', [fixture.reportCard.publicationId]),
    publishedStudents: readS3Rows('SELECT * FROM grade_publication_students WHERE publication_id=? ORDER BY student_pk_id', [fixture.reportCard.publicationId]),
    groups: readS3Rows(`SELECT * FROM group_assignment_member_results WHERE assignment_id IN (${placeholders}) ORDER BY assignment_id,student_pk_id`, ids),
  };
}

async function openReport(page: Page) {
  const fixture = await guardS3Page(page);
  await loginStudent(page, fixture);
  expect((await page.goto(reportURL(fixture)))?.status()).toBe(200);
  await expect(page.locator('[data-lq-report-card-topbar]')).toHaveAttribute('data-lq-enhanced', 'true');
  await expect.poll(() => page.locator(chartSelector).evaluateAll(elements => elements.every(element => !!(window as any).echarts?.getInstanceByDom(element)))).toBe(true);
  await expect(page.locator(chartSelector)).toHaveCount(1);
  return fixture;
}

async function chartState(page: Page) {
  return page.locator(chartSelector).evaluate(element => {
    const chart = (window as any).echarts.getInstanceByDom(element), options = chart.getOption();
    return { id: chart.id, mine: options.series[0].data, average: options.series[1].data,
      primary: options.series[0].lineStyle.color, selected: options.legend[0].selected,
      animation: options.animation, labels: options.xAxis[0].data };
  });
}

async function presentTheme(page: Page, palette: string, appearance: string, glass = 'tinted') {
  await page.evaluate(({ palette, appearance, glass }) => {
    const controller = (document as any)[Symbol.for('lanshare.theme.installation')];
    if (!controller) throw new Error('The shared theme controller must be installed');
    controller.refresh({ palette_key: palette, appearance, glass });
  }, { palette, appearance, glass });
  await expect(page.locator('html')).toHaveAttribute('data-appearance', appearance);
}

async function expectMessageCaptionInOneLine(page: Page) {
  const size = await page.locator('[data-lq-report-card-topbar] [data-message-center-bell]').evaluate(element => {
    const caption = element.querySelector('[data-message-center-bell-caption]')!;
    const range = document.createRange(); range.selectNodeContents(caption);
    return { lines: range.getClientRects().length, width: element.getBoundingClientRect().width, height: element.getBoundingClientRect().height };
  });
  expect(size.lines).toBe(1); expect(size.width).toBeGreaterThan(40); expect(size.height).toBeLessThanOrEqual(64);
}

test('S3 LQ report card preserves own zero/null/returned/hidden scores and frozen publications without grade writes', async ({ page }) => {
  const fixture = readS3Fixture(), before = gradeSnapshot(fixture);
  await openReport(page);
  const result = await page.request.get(`/api/report-card?class_offering_id=${fixture.reportCard.offeringId}&student_id=${fixture.otherStudent.id}`);
  expect(result.status()).toBe(200);
  const card = (await result.json()).report_card;
  expect(card.charts).toHaveLength(1); expect(card.charts[0].mine).toEqual(fixture.reportCard.expectedMine);
  expect(card.published_grades).toHaveLength(1); expect(card.published_grades[0].overall_score).toBe(0);
  const records = card.courses.flatMap((course: any) => course.categories.flatMap((category: any) => category.records));
  expect(records).toHaveLength(6);
  const scoreByTitle = Object.fromEntries(records.map((record: any) => [record.title, record.my_score]));
  expect(scoreByTitle['S3 成绩 returned']).toBeNull(); expect(scoreByTitle['S3 成绩 hiddenGroup']).toBeNull();
  expect(JSON.stringify(card)).not.toContain(fixture.otherStudent.studentNumber);
  const view = page.locator('[data-lq-report-card]');
  await expect(view).toContainText('未提交，教师记 0'); await expect(view).toContainText('等待小组揭晓');
  await expect(view).toContainText('已退回待重交'); await expect(view).toContainText('重批中 · 原有效分');
  await expect(page.getByRole('region', { name: '已公布课程成绩' })).toContainText(/总评\s+0(?:\.0+)?\s+分/);
  expect((await chartState(page)).mine).toEqual(fixture.reportCard.expectedMine);
  const filter = page.getByRole('navigation', { name: '成绩类型筛选' });
  for (const anchor of await filter.locator('a').all()) await expect(anchor).toHaveAttribute('href', new RegExp(`class_offering_id=${fixture.reportCard.offeringId}`));
  await filter.getByRole('link', { name: '期末测验', exact: true }).click();
  await expect(view).toContainText('当前范围还没有成绩记录');
  await expect(page.locator(chartSelector)).toHaveCount(0);
  await expect(page.getByRole('region', { name: '已公布课程成绩' })).toContainText(/总评\s+0(?:\.0+)?\s+分/);
  await filter.getByRole('link', { name: '平时作业', exact: true }).click();
  await expect(page.locator(chartSelector)).toHaveCount(1);
  expect(gradeSnapshot(fixture)).toEqual(before);
});

for (const width of [1440, 390]) test(`S3 LQ report card six palettes and both appearances keep chart data and readable controls at ${width}`, async ({ page }, testInfo) => {
  test.setTimeout(150000);
  await page.setViewportSize({ width, height: 900 });
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  const fixture = await openReport(page), before = gradeSnapshot(fixture);
  const preferences = await (await page.request.get('/api/profile/ui-preferences')).json();
  const mutations: string[] = [];
  page.on('request', request => { if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method())) mutations.push(`${request.method()} ${new URL(request.url()).pathname}`); });
  const initial = await chartState(page);
  await page.locator(chartSelector).evaluate(element => (window as any).echarts.getInstanceByDom(element).dispatchAction({ type: 'legendUnSelect', name: '班级平均' }));
  await presentTheme(page, 'indigo', 'dark');
  expect((await chartState(page)).selected['班级平均']).toBe(false);
  await page.locator(chartSelector).evaluate(element => (window as any).echarts.getInstanceByDom(element).dispatchAction({ type: 'legendSelect', name: '班级平均' }));
  for (const palette of ['teal', 'indigo', 'sky', 'mint', 'violet', 'rose']) for (const appearance of ['light', 'dark']) {
    await presentTheme(page, palette, appearance);
    const current = await chartState(page);
    expect(current.id).toBe(initial.id); expect(current.mine).toEqual(initial.mine);
    expect(current.average).toEqual(initial.average); expect(current.labels).toEqual(initial.labels);
    expect(current.primary).toBe(await page.locator(chartSelector).evaluate(element => `hsl(${getComputedStyle(element).getPropertyValue('--ls-primary').trim().split(/\s+/).join(', ')})`));
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await expect(page.locator('.app-topbar-brand')).toHaveCount(1);
    if (width === 1440) await expectMessageCaptionInOneLine(page);
    const scan = await new AxeBuilder({ page }).include('[data-lq-report-card-topbar]').include('[data-lq-report-card]').analyze();
    expect(scan.violations, `${palette}/${appearance}/${width}`).toEqual([]);
    await page.screenshot({ path: testInfo.outputPath(`${palette}-${appearance}-${width}.png`), fullPage: true });
  }
  await presentTheme(page, 'indigo', 'dark', 'off');
  await page.emulateMedia({ reducedMotion: 'reduce', forcedColors: 'active' });
  await expect.poll(async () => (await chartState(page)).animation).toBe(false);
  expect((await chartState(page)).mine).toEqual(initial.mine);
  const forcedInk = await page.locator('[data-lq-report-card]').evaluate(root => {
    const probe = document.createElement('span'); root.append(probe);
    const system = (color: string) => { probe.style.color = color; return getComputedStyle(probe).color; };
    const canvas = system('CanvasText'), link = system('LinkText'); probe.remove();
    return [...root.querySelectorAll('.report-stat span,.report-course h2,.report-course h2 small,.report-course__meta,.report-record__title,.report-record__title small,.report-record__avg,.report-explain strong')].map(element => ({
      text: element.textContent?.trim().slice(0, 48), actual: getComputedStyle(element).color,
      expected: element.matches('a.report-record__title') ? link : canvas,
    }));
  });
  expect(forcedInk.length).toBeGreaterThan(10);
  for (const item of forcedInk) expect(item.actual, item.text).toBe(item.expected);
  expect((await new AxeBuilder({ page }).include('[data-lq-report-card]').analyze()).violations).toEqual([]);
  expect(await (await page.request.get('/api/profile/ui-preferences')).json()).toEqual(preferences);
  expect(mutations).toEqual([]); expect(errors).toEqual([]); expect(gradeSnapshot(fixture)).toEqual(before);
});

test('S3 LQ report card mobile overflow has one native owner and keeps feedback/security reachable', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await openReport(page);
  const trigger = page.locator('[data-lq-report-card-topbar] [data-lq-pane-open="actions"]');
  const pane = page.locator('#report-card-topbar--lq-actions');
  for (let cycle = 0; cycle < 20; cycle++) {
    await trigger.click(); await expect(pane).toBeVisible();
    expect(await pane.evaluate(element => element.matches(':modal'))).toBe(true);
    await page.keyboard.press('Escape'); await expect(pane).toBeHidden(); await expect(trigger).toBeFocused();
  }
  await trigger.click();
  await expectMessageCaptionInOneLine(page);
  await expect(pane.locator('[data-message-center-bell]')).toHaveAttribute('href', '/profile?section=notifications#profile-message-center');
  await expect(pane.getByRole('link', { name: '个人中心', exact: true })).toHaveAttribute('href', '/profile');
  await pane.locator('[data-ui-preferences-toggle]').click();
  await expect(pane.locator('[data-ui-preferences-panel]')).toBeVisible();
  const summaryBox = await pane.locator('[data-ui-preferences-toggle]').boundingBox();
  const preferenceBox = await pane.locator('[data-ui-preferences-panel]').boundingBox();
  expect(preferenceBox!.y).toBeGreaterThanOrEqual(summaryBox!.y + summaryBox!.height);
  expect(await pane.locator('[data-ui-preferences-toggle]').evaluate(element => {
    const rect = element.getBoundingClientRect();
    return element.contains(document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2));
  })).toBe(true);
  await pane.locator('[data-ui-preferences-toggle]').click();
  await expect(pane.locator('[data-ui-preferences-panel]')).toBeHidden();
  const passwordTrigger = pane.locator('[data-open-student-security]');
  await passwordTrigger.click();
  const security = page.locator('#student-security-modal');
  await expect(security).toBeVisible();
  await security.locator('#current-password').fill('local unsent draft');
  await page.keyboard.press('Escape'); await expect(security).toBeHidden();
  await expect(passwordTrigger).toBeFocused();
  await pane.locator('[data-open-feedback]').click();
  const feedback = page.locator('#feedback-modal');
  await expect(pane).toBeHidden(); // The existing body-modal controller owns this handoff.
  await expect(feedback).toBeVisible();
  await expect(feedback.locator('[data-feedback-dismiss]')).toBeFocused();
  await feedback.locator('#feedback-title').fill('local unsent feedback draft');
  await feedback.locator('[data-feedback-dismiss]').click();
  await expect(feedback).toBeHidden();
  await expect(trigger).toBeFocused();
  await trigger.click(); await pane.locator('[data-open-feedback]').click();
  await expect(feedback.locator('#feedback-title')).toHaveValue('local unsent feedback draft');
  await page.keyboard.press('Escape'); await expect(feedback).toBeHidden();
  await expect(trigger).toBeFocused();
  await trigger.click(); await page.keyboard.press('Escape'); await expect(pane).toBeHidden();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test('S3 LQ report card anonymous and teacher identities cannot read student grades', async ({ page }) => {
  const fixture = await guardS3Page(page);
  expect((await page.request.get('/api/report-card')).status()).toBe(401);
  await loginTeacher(page, fixture);
  expect((await page.request.get('/api/report-card')).status()).toBe(403);
  await page.goto(reportURL(fixture));
  await expect(page.locator('[data-lq-report-card]')).toHaveCount(0);
  await expect(page.locator('[data-report-chart-data]')).toHaveCount(0);
});

test('S3 LQ report card module aliases and twenty chart lifecycles preserve data and dispose removed pages', async ({ page }) => {
  const fixture = await openReport(page);
  const result = await page.evaluate(async () => {
    const root = document.querySelector('[data-lq-report-card]')!, plot = root.querySelector('[data-report-chart]')!;
    const key = Symbol.for('lanshare.report-card.pilot'), owner = (root as any)[key];
    const echarts = (window as any).echarts, original = echarts.getInstanceByDom(plot);
    const moduleURL = new URL('/static/js/report_card.js?lq-contract-alias=1', location.href);
    const module = await import(moduleURL.href);
    const aliasesShareOwner = (root as any)[key] === owner && module.initReportCardPilot(root) === owner;
    const checks = [];
    for (let cycle = 0; cycle < 20; cycle++) {
      const current = (root as any)[key], chart = echarts.getInstanceByDom(plot);
      dispatchEvent(new Event('resize'));
      current.destroy(); current.destroy();
      checks.push({ disposed: chart.isDisposed(), unregistered: !echarts.getInstanceByDom(plot) });
      module.initReportCardPilot(root);
    }
    const finalChart = echarts.getInstanceByDom(plot), mine = finalChart.getOption().series[0].data;
    const finalOwner = (root as any)[key];
    root.remove();
    await Promise.resolve(); await Promise.resolve();
    return { aliasesShareOwner, originalDisposed: original.isDisposed(), checks, mine,
      removedDisposed: finalChart.isDisposed(), removedOwnerDestroyed: finalOwner.destroyed,
      ownerReleased: !(root as any)[key], canvasCount: root.querySelectorAll('canvas').length };
  });
  expect(result.aliasesShareOwner).toBe(true); expect(result.originalDisposed).toBe(true);
  expect(result.checks).toEqual(Array.from({ length: 20 }, () => ({ disposed: true, unregistered: true })));
  expect(result.mine).toEqual(fixture.reportCard.expectedMine);
  expect(result.removedDisposed).toBe(true); expect(result.removedOwnerDestroyed).toBe(true);
  expect(result.ownerReleased).toBe(true); expect(result.canvasCount).toBe(0);
});

test('S3 LQ report card has server-rendered records and navigation when JavaScript is unavailable', async ({ page, browser, baseURL }) => {
  const fixture = await openReport(page);
  const fallback = await browser.newContext({ baseURL, javaScriptEnabled: false, viewport: { width: 390, height: 844 }, storageState: await page.context().storageState() });
  try {
    await fallback.route('**/*', route => ['127.0.0.1', 'localhost'].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort());
    const plain = await fallback.newPage(); await guardS3Page(plain);
    expect((await plain.goto(reportURL(fixture)))?.status()).toBe(200);
    await expect(plain.locator('[data-lq-report-card]')).toContainText('等待小组揭晓');
    await expect(plain.getByRole('region', { name: '已公布课程成绩' })).toContainText(/总评\s+0(?:\.0+)?\s+分/);
    await expect(plain.locator('#report-card-topbar--lq-actions')).toBeVisible();
    await expect(plain.locator('[data-lq-report-card-topbar]').getByRole('link', { name: '个人中心', exact: true })).toBeVisible();
    await plain.getByRole('navigation', { name: '成绩类型筛选' }).getByRole('link', { name: '期末测验', exact: true }).click();
    await expect(plain.locator('[data-lq-report-card]')).toContainText('当前范围还没有成绩记录');
  } finally { await fallback.close(); }
});
