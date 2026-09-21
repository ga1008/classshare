import { test, expect, readS3Fixture } from '../fixtures/lq-s3';
import { loginTeacher, loginStudent } from '../fixtures/p03';

const pages = [
  ['/manage/library/courses', '#courseCardGrid'],
  ['/manage/teaching/classes', '#classList'],
  ['/manage/teaching/classroom-hub', '#offeringHubList'],
  ['/manage/teaching/semesters', '#semesterList'],
  ['/manage/library/textbooks', '#textbookCardGrid'],
  ['/manage/library/lesson-plans', '[data-lp-grid]'],
  ['/manage/library/materials', '[data-testid="p03-materials-list"]'],
  ['/manage/system/users', '#teacher-table-body'],
] as const;

for (const width of [1440, 390]) test(`S3 disabled pilot restores all eight legacy manage pages at ${width}`, async ({ page }, info) => {
  test.setTimeout(150_000);
  const fixture = readS3Fixture();
  await page.setViewportSize({ width, height: 900 });
  await loginTeacher(page, fixture);
  const failures: string[] = [];
  page.on('pageerror', error => failures.push(error.message));
  for (const [url, hook] of pages) {
    if (url.endsWith('/users')) await loginTeacher(page, fixture, fixture.superTeacher);
    const response = await page.goto(`${url}?lq_pilot=true`);
    expect(response?.status()).toBe(200);
    await expect(page.locator(hook)).toBeAttached();
    await expect(page.locator('body')).not.toHaveClass(/lq-manage-pilot/);
    await expect(page.locator('[data-page-head]')).toHaveCount(1);
    await expect(page.locator('[data-page-head]')).not.toHaveClass(/lq-page-head/);
    await expect(page.locator('.manage-topbar')).toBeVisible();
    await expect(page.locator('#manageNav .manage-nav-domain')).toHaveCount(url.endsWith('/users') ? 7 : 6);
    expect(await page.evaluate(() => !!(document.body as any)[Symbol.for('lanshare.manage-lq-pilot')])).toBe(false);
    expect(await page.locator('.manage-topbar').evaluate(node => getComputedStyle(node).display)).not.toBe('none');
    await page.screenshot({ path: info.outputPath(`${url.split('/').pop()}-${width}.png`), fullPage: true });
  }
  expect(failures).toEqual([]);
});

test('S3 disabled pilot retains legacy search and original course controller', async ({ page }) => {
  await loginTeacher(page, readS3Fixture());
  await page.goto('/manage/library/courses');
  await expect(page.locator('#manage-pilot-topbar')).toHaveCount(0);
  await page.locator('#manageNavSearch').fill('S3不存在的导航');
  await expect(page.locator('.manage-nav-item:visible')).toHaveCount(0);
  await expect(page.locator('#manageNavEmpty')).toBeVisible();
  await page.locator('#manageNavSearch').fill('');
  await expect(page.locator('#manageNavEmpty')).toBeHidden();
  await page.locator('#toolbarCourseCreateBtn').click();
  await expect(page.locator('#courseModal')).toBeVisible();
  await page.locator('#courseNameInput').fill('S3 rollback unsaved course');
  let writes = 0;
  await page.route('**/api/manage/courses/save', route => {
    if (route.request().method() !== 'POST') return route.continue();
    writes++;
    return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'S3 rollback controlled failure', message: 'S3 rollback controlled failure' }) });
  });
  await page.locator('#courseSaveBtn').click();
  await expect.poll(() => writes).toBe(1);
  await expect(page.locator('#courseNameInput')).toHaveValue('S3 rollback unsaved course');
  await expect(page.locator('#courseSaveBtn')).toBeEnabled();
});

test('S3 disabled pilot restores the legacy report chart and keeps own grade semantics', async ({ page }) => {
  const fixture = readS3Fixture();
  await loginStudent(page, fixture);
  const url = `/report-card?class_offering_id=${fixture.reportCard.offeringId}`;
  expect((await page.goto(url))?.status()).toBe(200);
  await expect(page.locator('[data-lq-report-card], [data-lq-report-card-topbar]')).toHaveCount(0);
  await expect(page.locator('.app-topbar')).toBeVisible();
  await expect(page.locator('[data-report-chart]')).toHaveCount(1);
  await expect.poll(() => page.locator('[data-report-chart]').evaluate(element => !!(window as any).echarts?.getInstanceByDom(element))).toBe(true);
  // The shared scene controller also serves the original navbar when the
  // presentation pilot is off. Exercise all existing close paths there too.
  const scene = page.locator('.app-topbar .topbar-scene-chip');
  await expect(scene).toHaveCount(1);
  for (const close of ['toggle', 'outside', 'escape']) {
    await scene.click();
    await expect(page.locator('.topbar-scene-pop')).toHaveCount(1);
    if (close === 'toggle') await scene.click();
    else if (close === 'outside') await page.locator('.report-hero h1').click();
    else await page.keyboard.press('Escape');
    await expect(page.locator('.topbar-scene-pop')).toHaveCount(0);
  }
  const state = await page.locator('[data-report-chart]').evaluate(element => (window as any).echarts.getInstanceByDom(element).getOption().series[0].data);
  expect(state).toEqual(fixture.reportCard.expectedMine);
  await page.getByRole('navigation', { name: '成绩类型筛选' }).getByRole('link', { name: '期末测验', exact: true }).click();
  await expect(page.locator('.report-shell')).toContainText('当前范围还没有成绩记录');
  await expect(page.getByRole('region', { name: '已公布课程成绩' })).toContainText(/总评\s+0(?:\.0+)?\s+分/);
});
