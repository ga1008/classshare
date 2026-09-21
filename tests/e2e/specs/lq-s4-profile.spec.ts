import { test as base, expect, type Page, type TestInfo } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { createRequire } from 'node:module';
import { readFixture, loginStudent, loginTeacher } from '../fixtures/p03';

const runtime = path.resolve('.codex-temp/lq-s4-profile-validation');
const origin = 'http://127.0.0.1:8169';
const { inspectGlass } = createRequire(path.resolve('package.json'))('./tools/ui/audit_glass_layers.cjs');
function fixture() {
  if (!process.env.P03_RUNTIME_ROOT || path.resolve(process.env.P03_RUNTIME_ROOT) !== runtime) throw Error('Profile requires its owned runtime');
  const value = readFixture();
  if (path.resolve(value.runtimeRoot) !== runtime || path.resolve(value.databasePath) !== path.join(runtime, 'db/classroom.db')
      || (value as unknown as { uiV3Synthetic?: boolean }).uiV3Synthetic !== true) throw Error('Profile fixture is not synthetic');
  if (!/^[a-f0-9]{64}$/.test(process.env.LQ_S4_PROFILE_GRAPH || '')) throw Error('Explicit profile graph is required');
  return value;
}
const test = base.extend<{ _profileGuard: void }>({
  _profileGuard: [async ({ context, baseURL }, use) => {
    const value = fixture(); expect(baseURL).toBe(origin);
    const health = await context.request.get('/api/internal/health');
    expect(health.status()).toBe(200); expect((await health.json()).database_path).toBe(value.databasePath);
    const errors: string[] = [];
    context.on('page', page => page.on('pageerror', error => errors.push(error.message)));
    await context.route('**/*', route => {
      const url = new URL(route.request().url());
      return url.origin === origin || ['blob:', 'data:'].includes(url.protocol) ? route.continue() : route.abort();
    });
    await use(); expect(errors).toEqual([]);
  }, { auto: true }],
});
const lock = path.join(runtime, '.profile-browser.lock'), owner = JSON.stringify({ pid: process.pid, nonce: crypto.randomUUID() });
test.beforeAll(() => { fixture(); fs.writeFileSync(lock, owner, { flag: 'wx' }); });
test.afterAll(() => { if (fs.existsSync(lock) && fs.readFileSync(lock, 'utf8') === owner) fs.unlinkSync(lock); });
type Role = 'student' | 'teacher';
const profilePath = (role: Role, section = 'appearance') => role === 'teacher' ? `/manage/me/${section}` : `/profile?section=${section}`;
async function enter(page: Page, role: Role, info: TestInfo) {
  await (role === 'teacher' ? loginTeacher : loginStudent)(page, fixture());
  await page.goto(profilePath(role));
  await expect(page.locator('[data-profile-appearance]')).toBeVisible();
  await expect(page.locator('[data-lq-profile]')).toHaveAttribute('data-profile-role', role);
  const assets = await page.locator('link[href*="/assets/"],script[src*="/assets/"]').evaluateAll(nodes => nodes.map(node => node.getAttribute('href') || node.getAttribute('src') || ''));
  expect(assets.length).toBeGreaterThan(0);
  const vite = JSON.parse(fs.readFileSync('.codex-temp/lq-s4-c1-d2-vite-frozen.json', 'utf8')) as Record<string, string>;
  for (const asset of assets) {
    if (asset.startsWith('/static/dist/')) {
      // Vite owns a separate content-hashed graph; bind its bytes to this build.
      const relative = asset.slice('/static/dist/'.length);
      expect(vite[relative]).toMatch(/^[a-f0-9]{64}$/);
      const response = await page.request.get(asset); expect(response.status()).toBe(200);
      expect(crypto.createHash('sha256').update(await response.body()).digest('hex')).toBe(vite[relative]);
    } else expect(asset).toContain(`/assets/${process.env.LQ_S4_PROFILE_GRAPH}/`);
  }
  await info.attach('profile-graph', { body: JSON.stringify({ graph: process.env.LQ_S4_PROFILE_GRAPH, assets }), contentType: 'application/json' });
}
async function preferences(page: Page) {
  const response = await page.request.get('/api/profile/ui-preferences'); expect(response.status()).toBe(200);
  return (await response.json()).preferences;
}
async function saved(page: Page) { await expect(page.locator('#profile-appearance-status')).toHaveText('界面偏好已保存'); }
async function chooseAppearance(page: Page, value: string) {
  const radio = page.locator(`#profile-appearance-${value}`);
  if (await radio.isChecked()) { expect((await preferences(page)).appearance).toBe(value); return; }
  await page.locator(`label[for="profile-appearance-${value}"]`).click();
  await saved(page);
}
async function choosePalette(page: Page, value: string) {
  await page.locator(`[data-profile-appearance] [data-ui-preference-value="${value}"]`).click(); await saved(page);
}

for (const role of ['student', 'teacher'] as const) for (const width of [320, 390, 1440]) {
  test(`C1 ${role} actual profile layout and keyboard at ${width}`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: width === 1440 ? 900 : 740 });
    await enter(page, role, info);
    for (const appearance of ['light', 'dark'] as const) {
      // This is a real account preference change, not an injected theme state.
      await chooseAppearance(page, appearance);
      await page.evaluate(() => document.fonts.ready);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      expect((await new AxeBuilder({ page }).include('[data-lq-profile]').analyze()).violations).toEqual([]);
      const glassAudit = await inspectGlass(page);
      await info.attach(`${role}-${width}-${appearance}-glass`, { body: JSON.stringify(glassAudit), contentType: 'application/json' });
      expect(glassAudit.visibleViewportLayerCount).toBeLessThanOrEqual(2);
      await page.screenshot({ path: info.outputPath(`${role}-${width}-${appearance}-appearance.png`), fullPage: true });
      if (role === 'student' && width < 768) {
        await page.locator('#profile-appearance-status').scrollIntoViewIfNeeded();
        await expect(page.locator('#global-back-to-top')).toBeVisible();
        expect((await inspectGlass(page)).visibleViewportLayerCount).toBeLessThanOrEqual(2);
        await expect.poll(async () => {
          const top = await page.locator('#global-back-to-top').boundingBox();
          const dock = await page.locator('#navbar-dock').boundingBox();
          return Boolean(top && dock && top.y + top.height + 4 <= dock.y);
        }).toBe(true);
      }
    }
    const radio = page.locator('#profile-appearance-dark'); await radio.focus(); await page.keyboard.press('ArrowLeft');
    await expect(page.locator('#profile-appearance-light')).toBeChecked(); await saved(page);
    await expect(page.locator('#profile-appearance-light')).toBeFocused();
    for (const section of ['settings', 'security']) {
      await page.goto(profilePath(role, section));
      await expect(page.locator(`[data-lq-profile-content="${section}"]`)).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      expect((await new AxeBuilder({ page }).include('[data-lq-profile]').analyze()).violations).toEqual([]);
      expect((await inspectGlass(page)).visibleViewportLayerCount).toBeLessThanOrEqual(2);
      const form = page.locator(section === 'settings' ? '#profile-basic-form' : '#profile-password-form');
      await expect(form).toBeVisible(); await expect(form.locator('button[type="submit"]')).toHaveCount(1);
      await page.screenshot({ path: info.outputPath(`${role}-${width}-${section}.png`), fullPage: true });
    }
  });
}

for (const role of ['student', 'teacher'] as const) {
  test(`C1 ${role} actual field CAS persistence and shared topbar controls`, async ({ page }, info) => {
    await enter(page, role, info);
    await choosePalette(page, (await preferences(page)).palette_key === 'rose' ? 'teal' : 'rose');
    await chooseAppearance(page, 'light');
    const glass = page.locator('#profile-appearance-glass');
    if (await glass.isChecked()) { await glass.uncheck(); await saved(page); }
    const confirmed = await preferences(page);
    expect(confirmed).toMatchObject({ appearance: 'light', glass: 'off' });
    await expect(page.locator('[data-ui-preferences-panel] [data-ui-preference-select="appearance"]')).toHaveValue('light');
    await page.reload();
    await expect(page.locator('#profile-appearance-light')).toBeChecked(); await expect(glass).not.toBeChecked();
    await expect(page.locator(`[data-profile-appearance] [data-ui-preference-value="${confirmed.palette_key}"]`)).toHaveAttribute('aria-pressed', 'true');
    expect(await preferences(page)).toEqual(confirmed);
  });
}

test('C1 server conflict requires explicit field consent; failure recovers before retry', async ({ page }, info) => {
  await enter(page, 'student', info);
  await choosePalette(page, 'indigo');
  const before = await preferences(page);
  const remote = await page.request.patch('/api/profile/ui-preferences', {
    headers: { 'X-UI-Preferences-Context': before.context_token }, data: { version: before.version, palette_key: 'sky' },
  }); expect(remote.status()).toBe(200);
  let patchCount = 0;
  page.on('request', request => { if (request.method() === 'PATCH' && new URL(request.url()).pathname === '/api/profile/ui-preferences') patchCount++; });
  await page.locator('[data-profile-appearance] [data-ui-preference-value="rose"]').click();
  await expect(page.locator('#profile-appearance-status')).toContainText('服务器：晴空');
  expect((await preferences(page)).palette_key).toBe('sky'); expect(patchCount).toBe(1);
  await page.locator('[data-profile-appearance] [data-ui-preference-value="rose"]').press('Enter'); await saved(page);
  expect((await preferences(page)).palette_key).toBe('rose'); expect(patchCount).toBe(2);
  let failNext = true; const order: string[] = [];
  await page.route('**/api/profile/ui-preferences', route => {
    order.push(route.request().method());
    if (route.request().method() === 'PATCH' && failNext) { failNext = false; return route.fulfill({ status: 503, json: { message: '合成暂时故障' } }); }
    return route.continue();
  });
  await page.locator('[data-profile-appearance] [data-ui-preference-value="mint"]').click();
  await expect(page.locator('#profile-appearance-status')).toContainText('临时预览');
  await page.locator('[data-profile-appearance] [data-ui-preference-value="mint"]').press('Enter'); await saved(page);
  expect(order).toEqual(['PATCH', 'GET', 'PATCH']); expect((await preferences(page)).palette_key).toBe('mint');
});

test('C1 stale page cannot write preferences after cookie identity changes', async ({ page, context }, info) => {
  await enter(page, 'student', info);
  const second = await context.newPage(); await loginStudent(second, fixture(), fixture().otherStudent);
  const otherBefore = await preferences(second);
  const blocked = page.waitForResponse(response => response.request().method() === 'PATCH' && new URL(response.url()).pathname === '/api/profile/ui-preferences');
  await page.locator('[data-profile-appearance] [data-ui-preference-value="violet"]').click();
  expect((await blocked).status()).toBe(409);
  await expect(page.locator('#profile-appearance-status')).toContainText('登录账号已变化');
  expect(await preferences(second)).toEqual(otherBefore);
});
