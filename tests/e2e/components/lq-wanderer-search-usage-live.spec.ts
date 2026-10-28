import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';

// Opt-in real SSR/auth/API checks. All writes stay in this task-owned fixture.
const runtime = process.env.AI_SEARCH_RUNTIME ? path.resolve(process.env.AI_SEARCH_RUNTIME) : '';
const fixture = runtime ? JSON.parse(fs.readFileSync(path.join(runtime, 'fixture.json'), 'utf8')) : null;
const origin = `http://127.0.0.1:${process.env.AI_SEARCH_PORT || '8375'}`;
const graph = JSON.parse(fs.readFileSync('static/assets/manifest.json', 'utf8'));
test.skip(!runtime, 'Requires the owned synthetic AI-search fixture and loopback server');
if (fixture && (!runtime.startsWith(path.resolve('.codex-temp') + path.sep) || !fixture.uiV3Synthetic
  || !fixture.aiSearchSeed || path.resolve(fixture.databasePath) !== path.join(runtime, 'db/classroom.db'))) {
  throw Error('Wanderer search live checks must never access a business database');
}
const scenes = [
  { name: 'desktop-light', width: 1440, height: 900, dark: false, glass: true },
  { name: 'desktop-dark', width: 1440, height: 900, dark: true, glass: true },
  { name: 'mobile-light', width: 360, height: 780, dark: false, glass: true },
  { name: 'mobile-dark', width: 360, height: 640, dark: true, glass: true },
  { name: 'mobile-dark-opaque', width: 360, height: 640, dark: true, glass: false },
];
async function login(page: Page, student = false) {
  // Browser and server are both restricted to loopback; no model or search request is made.
  await page.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
  const health = await page.request.get(`${origin}/api/internal/health`);
  expect(health.ok()).toBeTruthy();
  expect(path.resolve((await health.json()).database_path)).toBe(path.resolve(fixture.databasePath));
  const response = await page.request.post(`${origin}${student ? '/api/student/login/password' : '/teacher/login'}`, {
    form: student ? { identifier: fixture.student.studentNumber, password: fixture.password }
      : { email: fixture.teacher.email, password: fixture.password, next: '/dashboard' }, maxRedirects: 0,
  });
  expect(response.status()).toBe(student ? 200 : 303);
  if (!student) await page.request.post(`${origin}/api/manage/teacher-onboarding/dismiss`, { data: { reason: 'later' } });
}
async function scene(page: Page, value: typeof scenes[number]) {
  await page.evaluate(({ dark, glass }) => {
    document.documentElement.dataset.appearance = dark ? 'dark' : 'light';
    document.documentElement.dataset.lqSceneTone = dark ? 'dark' : 'light';
    document.documentElement.dataset.lqGlass = glass ? 'on' : 'off';
  }, value);
}
async function visit(page: Page, url: string) {
  // Complete the genuine first-login ceremony before measuring the page;
  // DOM text behind a life-tip overlay is not visual acceptance.
  await page.goto(url);
  await page.waitForLoadState('networkidle');
  const reveal = page.locator('.cultivation-login-reveal');
  if (await reveal.count()) {
    const skip = page.locator('[data-life-tip-skip]');
    if (await skip.isVisible()) await skip.click();
    else await page.keyboard.press('Escape');
    await expect(reveal).toHaveCount(0);
  }
}
async function cardBounds(page: Page, selector: string) {
  const locator = page.locator(selector);
  await locator.scrollIntoViewIfNeeded();
  if (!selector.startsWith('#wd-detail')) await locator.evaluate(el => {
    const header = document.querySelector('header');
    if (!header || !['fixed', 'sticky'].includes(getComputedStyle(header).position)) return;
    const top = header.getBoundingClientRect().bottom, box = el.getBoundingClientRect();
    if (box.top < top + 8) window.scrollBy(0, box.top - top - 8);
  });
  const box = await locator.boundingBox();
  expect(box).not.toBeNull();
  expect(box!.x).toBeGreaterThanOrEqual(-1);
  expect(box!.y).toBeGreaterThanOrEqual(-1);
  expect(box!.x + box!.width).toBeLessThanOrEqual(page.viewportSize()!.width + 1);
  expect(box!.y + box!.height).toBeLessThanOrEqual(page.viewportSize()!.height + 1);
  const blur = await locator.evaluate(el => getComputedStyle(el).backdropFilter);
  expect(blur).toBe('none');
  expect(await locator.evaluate(el => {
    const r = el.getBoundingClientRect(), hit = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
    return hit === el || el.contains(hit);
  })).toBe(true);
  return box;
}
async function assertUsage(page: Page, selector: string) {
  const card = page.locator(selector);
  await expect(card).toHaveCount(1);
  for (const text of ['今日搜索', '1 / 10', '剩余 7 次', '处理中 1 次', '待确认 1 次', '北京时间零点重置'])
    await expect(card).toContainText(text);
  for (const sentinel of fixture.aiSearchSeed.privacySentinels)
    expect(await page.content()).not.toContain(sentinel);
}

for (const value of scenes) test(`LQ live wanderer daily search usage ${value.name}`, async ({ page }) => {
  await page.setViewportSize({ width: value.width, height: value.height });
  for (const source of ['js/manage_wanderers.js', 'css/lq/pages/wanderer-home.css', 'css/lq/pages/wanderers.css'])
    expect(createHash('sha256').update(fs.readFileSync(`static/${source}`)).digest('hex')).toBe(graph.sourceHashes[source]);
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await login(page);
  const seed = fixture.aiSearchSeed;
  const response = await page.request.get(`${origin}/api/manage/system/wanderers/${seed.wandererId}`);
  expect(response.ok()).toBeTruthy();
  const detail = (await response.json()).detail;
  for (const [key, expected] of Object.entries(seed.expectedUsage)) expect(detail.web_search_usage[key]).toBe(expected);
  for (const sentinel of seed.privacySentinels) expect(JSON.stringify(detail)).not.toContain(sentinel);
  await visit(page, `${origin}/wanderers/${seed.slug}`);
  await scene(page, value);
  await expect(page.locator('[data-wanderer-home]')).toHaveAttribute('data-wanderer-home', seed.slug);
  await assertUsage(page, '[data-wanderer-search-usage]');
  const publicBounds = await cardBounds(page, '[data-wanderer-search-usage]');
  expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(1);
  await page.screenshot({ path: test.info().outputPath(`wanderer-search-public-${value.name}.png`) });
  await visit(page, `${origin}/manage/system/wanderers`);
  await scene(page, value);
  await expect(page.locator(`script[src="/static/${graph.entries['js/manage_wanderers.js']}"]`).first()).toHaveCount(1);
  const trigger = page.locator(`[data-wanderer-id="${seed.wandererId}"]`);
  await trigger.click();
  await expect(page.locator('#wd-detail')).toBeVisible();
  await assertUsage(page, '#wd-detail [data-wanderer-search-usage]');
  const adminBounds = await cardBounds(page, '#wd-detail [data-wanderer-search-usage]');
  const dialog = await page.locator('#wd-detail .lq-dialog__surface').boundingBox();
  expect(dialog!.x).toBeGreaterThanOrEqual(-1);
  expect(dialog!.x + dialog!.width).toBeLessThanOrEqual(value.width + 1);
  await page.screenshot({ path: test.info().outputPath(`wanderer-search-admin-${value.name}.png`) });
  fs.writeFileSync(test.info().outputPath('geometry.json'), JSON.stringify({ graph: graph.revision, scene: value,
    expectedUsage: seed.expectedUsage, publicBounds, adminBounds, dialog }, null, 2));
  await page.locator('[data-wd-detail-close]').click();
  await expect(page.locator('#wd-detail')).toBeHidden();
  await expect(trigger).toBeFocused();
  expect(errors).toEqual([]);
});

test('LQ live student can see aggregate daily search usage while admin detail remains protected', async ({ page }) => {
  await login(page, true);
  await visit(page, `${origin}/wanderers/${fixture.aiSearchSeed.slug}`);
  await assertUsage(page, '[data-wanderer-search-usage]');
  const admin = await page.request.get(`${origin}/api/manage/system/wanderers/${fixture.aiSearchSeed.wandererId}`);
  expect([401, 403]).toContain(admin.status());
});
