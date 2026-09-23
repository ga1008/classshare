import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { readFixture, loginStudent, loginTeacher } from '../fixtures/p03';

const layer = '[data-lq-page-backdrop]';
const manifest = JSON.parse(fs.readFileSync('static/img/life_tips/manifest.json', 'utf8')).images;
const backdropUrl = (file: string) => `/static/img/life_tips/${file}`;

test.beforeEach(async ({ page, baseURL }, info) => {
  const fixture = readFixture();
  expect(fixture.runtimeRoot).toContain('lq-background-20260924');
  expect((fixture as any).uiV3Synthetic).toBe(true);
  expect((await (await page.request.get('/api/internal/health')).json()).database_path).toBe(fixture.databasePath);
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin !== baseURL && !['data:', 'blob:'].includes(url.protocol)) return route.abort();
    // Optional pre-build verification only. API/auth/storage always use the
    // real isolated app; the final release run omits this source overlay.
    if (process.env.LQ_BACKGROUND_SOURCE_OVERLAY === '1' && url.pathname.startsWith('/static/')) {
      const source = url.pathname.replace(/^\/static\/(?:assets\/[a-f0-9]{64}\/)?/, 'static/');
      const file = path.resolve(source);
      if (file.startsWith(path.resolve('static') + path.sep) && fs.existsSync(file) && /\.(js|css)$/.test(file)) {
        let body = fs.readFileSync(file, 'utf8');
        if (source === 'static/css/tailwind-app.css') body += ['tokens.css', 'materials.css', 'components/material-boundaries.css', 'components/page-backdrop.css', 'pages/life-tip.css', 'pages/login.css'].map(name => fs.readFileSync(`static/css/lq/${name}`, 'utf8')).join('\n');
        return route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body });
      }
    }
    return route.continue();
  });
  await info.attach('asset-mode', { body: process.env.LQ_BACKGROUND_SOURCE_OVERLAY === '1' ? 'source overlay; real synthetic API' : 'built assets; real synthetic API', contentType: 'text/plain' });
});

async function prefs(page: Page) { const response = await page.request.get('/api/profile/ui-preferences'); expect(response.ok()).toBe(true); return (await response.json()).preferences; }
async function save(page: Page, changes: Record<string, string>) {
  const current = await prefs(page);
  const response = await page.request.patch('/api/profile/ui-preferences', { headers: { 'X-UI-Preferences-Context': current.context_token }, data: { ...changes, version: current.version } });
  expect(response.status()).toBe(200); return (await response.json()).preferences;
}
async function fullScene(page: Page, image?: string) {
  await expect(page.locator(layer)).toHaveCount(1);
  if (image) await expect(page.locator(layer)).toHaveAttribute('data-lq-backdrop-image-url', image);
  await expect(page.locator(layer)).toHaveAttribute('data-lq-backdrop-frost-state', 'ready');
  await expect(page.locator('html')).not.toHaveClass(/has-scene-cover/);
  expect(await page.locator('.app-topbar__scene').count()).toBe(0);
  expect(await page.locator(layer).evaluate(node => {
    const r = node.getBoundingClientRect(), image = node.querySelector('[data-lq-backdrop-image]')!;
    return { x: r.x, y: r.y, width: r.width, height: r.height, vw: innerWidth, vh: innerHeight,
      fixed: getComputedStyle(node).position, source: getComputedStyle(image).backgroundImage, transform: getComputedStyle(image).transform };
  })).toMatchObject({ x: 0, y: 0, fixed: 'fixed', transform: 'none' });
  const rect = await page.locator(layer).boundingBox(); expect(rect!.width).toBe(page.viewportSize()!.width); expect(rect!.height).toBe(page.viewportSize()!.height);
  const paint = await page.locator('[data-lq-backdrop-image]').evaluate(node => {
    const style = getComputedStyle(node);
    return { source: style.backgroundImage, opacity: style.opacity, filter: style.filter };
  });
  expect(paint.source).not.toContain('/frost/');
  await expect.poll(() => page.locator('[data-lq-backdrop-image]').evaluate(node => getComputedStyle(node).opacity)).toBe('1');
  expect(paint.filter).toBe('blur(24px)');
  expect(await page.locator('body').evaluate(node => getComputedStyle(node).isolation)).toBe('isolate');
  await expect(page.locator('.lq-page-backdrop__veil')).toHaveCount(0);
}

test('real login welcome feedback and skip hand the unchanged full viewport scene to every page', async ({ page }, info) => {
  const fixture = readFixture();
  // The same owned runtime can be reused for visual inspection between runs.
  await loginStudent(page, fixture); await save(page, { backdrop: 'scene' }); await page.context().clearCookies();
  await page.goto('/student/login');
  await expect(page.locator('[data-lq-login-card]')).toHaveAttribute('data-lq-scene-state', 'ready');
  const image = await page.locator('.login-scene-backdrop__image').evaluate(node => new URL(getComputedStyle(node).backgroundImage.slice(5, -2)).pathname);
  await page.locator('#identifier').fill(fixture.student.studentNumber); await page.locator('#password').fill(fixture.password);
  await page.locator('#student-password-login-form button[type=submit]').click();
  await expect(page.locator('.cultivation-login-reveal--tip')).toBeVisible();
  const imageBox = await page.locator('.life-tip-backdrop').boundingBox(); expect(imageBox!.x).toBe(0); expect(imageBox!.width).toBe(page.viewportSize()!.width);
  const feedback = page.waitForResponse(response => response.url().includes('/api/learning/life-tips/feedback') && response.request().method() === 'POST');
  await page.locator('[data-life-tip-feedback="1"]').focus(); await page.keyboard.press('Enter'); expect((await feedback).ok()).toBe(true);
  await expect(page.locator('.cultivation-login-reveal--tip')).toBeVisible();
  await page.locator('[data-life-tip-skip]').click(); await page.waitForURL(/\/dashboard(?:\?|$)/);
  await fullScene(page, image); await page.screenshot({ path: info.outputPath('welcome-to-full-backdrop.png') });
  await page.goto('/resume'); await fullScene(page, image);
  await page.evaluate(() => { document.body.style.minHeight = '300vh'; scrollTo(0, 900); });
  expect((await page.locator(layer).boundingBox())!.y).toBe(0);
});

for (const role of ['student', 'teacher'] as const) test(`fixed image picker saves through real CAS and SSR for ${role}`, async ({ page }, info) => {
  const fixture = readFixture(); await (role === 'student' ? loginStudent : loginTeacher)(page, fixture);
  const profile = role === 'teacher' ? '/manage/me/appearance' : '/profile?section=appearance';
  await save(page, { backdrop: 'scene', appearance: 'light' }); await page.goto(profile);
  const appearance = page.locator('[data-profile-appearance]');
  await appearance.locator('[data-ui-backdrop-gallery] summary').click();
  const file = manifest[0].file; const button = appearance.locator(`[data-ui-backdrop-file="${file}"]`);
  await button.click(); await expect(button).toHaveAttribute('aria-pressed', 'true');
  await expect(appearance.locator('[data-ui-preference-primary-status]')).toHaveAttribute('data-ui-preference-status', 'saved');
  const saved = await prefs(page); expect(saved.backdrop).toBe(`image:${file}`);
  await fullScene(page, backdropUrl(file)); await page.reload(); await fullScene(page, backdropUrl(file));
  await page.goto(role === 'student' ? '/resume' : '/manage/teaching/classroom-hub'); await fullScene(page, backdropUrl(file));
  const headers = { 'X-UI-Preferences-Context': saved.context_token };
  expect((await page.request.patch('/api/profile/ui-preferences', { headers, data: { backdrop: 'image:missing.webp', version: saved.version } })).status()).toBe(422);
  await save(page, { backdrop: 'scene-life' });
  expect((await page.request.patch('/api/profile/ui-preferences', { headers, data: { backdrop: 'off', version: saved.version } })).status()).toBe(409);
  await page.setViewportSize({ width: 390, height: 844 }); await page.goto(profile);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await appearance.locator('[data-ui-backdrop-gallery] summary').click();
  await appearance.locator('[data-ui-backdrop-grid]').scrollIntoViewIfNeeded();
  await expect(appearance.locator('[data-ui-backdrop-file]').first()).toBeVisible();
  await page.screenshot({ path: info.outputPath(`${role}-background-picker-mobile.png`) });
  await save(page, { backdrop: 'scene' });
});

test('explicit pure colour survives replay and session image cannot cross accounts', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' }); const fixture = readFixture(); await loginStudent(page, fixture);
  await save(page, { backdrop: 'off', backdrop_color: '#102030' }); await page.goto('/profile?section=appearance');
  await page.evaluate(async image => { const module = await import('/static/js/cultivation_identity.js'); module.playCultivationReveal(null, { loginTip: { tips: [{ id: 1, text: '保留欢迎语与偏好。', image_url: image }] } }); }, backdropUrl(manifest[0].file));
  await expect(page.locator('.cultivation-login-reveal--tip')).toBeVisible(); await page.keyboard.press('Escape');
  await expect(page.locator('.cultivation-login-reveal--tip')).toHaveCount(0);
  await expect(page.locator(layer)).toHaveAttribute('data-lq-backdrop-mode', 'off');
  expect(await page.locator(layer).evaluate(node => getComputedStyle(node).backgroundColor)).toBe('rgb(16, 32, 48)');
  expect(await page.locator('.app-topbar__scene').count()).toBe(0);
  await save(page, { backdrop: 'scene' });
  await page.evaluate(() => sessionStorage.setItem('lansharePageScene', JSON.stringify({ context: 'another-account', image: '/static/img/life_tips/unknown.webp', tip: 'Other person' })));
  await page.reload(); await fullScene(page);
  expect(await page.locator(layer).getAttribute('data-lq-backdrop-image-url')).not.toContain('unknown.webp');
});

test('original image uses one Gaussian filter with bleed and off cancels late loading', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' }); await loginStudent(page, readFixture());
  const file = manifest[1].file; await save(page, { backdrop: `image:${file}` });
  await page.goto('/profile?section=appearance');
  await expect(page.locator(layer)).toHaveAttribute('data-lq-backdrop-frost-state', 'ready');
  const image = page.locator('[data-lq-backdrop-image]');
  expect(await image.evaluate(node => getComputedStyle(node).filter)).toContain('blur(24px)');
  expect((await image.boundingBox())!.x).toBeLessThan(0);
  const scope = page.locator('[data-profile-appearance]');
  let requested: () => void = () => {}, release: () => void = () => {};
  const loading = new Promise<void>(resolve => { requested = resolve; });
  const held = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/img/life_tips/*.webp', async route => { requested(); await held; await route.continue(); });
  const change = (value: string) => scope.locator('[data-ui-preference-select="backdrop"]').evaluate((select: HTMLSelectElement, value) => {
    select.value = value; select.dispatchEvent(new Event('change', { bubbles: true }));
  }, value);
  await change('scene-career');
  await loading;
  await change('off');
  await expect(page.locator(layer)).toHaveAttribute('data-lq-backdrop-frost-state', 'off');
  release();
  await expect(scope.locator('[data-ui-preference-primary-status]')).toHaveAttribute('data-ui-preference-status', 'saved');
  await save(page, { backdrop: 'scene' });
});

test('unavailable or invalid manifest can retry and a missing replacement preserves the usable scene', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' }); await loginStudent(page, readFixture());
  await save(page, { backdrop: 'scene' });
  const usable = manifest[0].file, missing = manifest[1].file;
  // Install before the gallery renders its thumbnail; a previously decoded
  // thumbnail legitimately satisfies Image preloading without a new request.
  await page.route(`**/img/life_tips/${missing}`, route => route.fulfill({ status: 404, body: '' }));
  let attempts = 0;
  await page.route('**/img/life_tips/manifest.json', route => {
    attempts++;
    if (attempts === 1) return route.fulfill({ status: 503, body: 'unavailable' });
    if (attempts === 2) return route.fulfill({ contentType: 'application/json', body: '{"images":null}' });
    return route.continue();
  });
  await page.goto('/profile?section=appearance');
  await expect.poll(() => attempts).toBe(1);
  const gallery = page.locator('[data-profile-appearance] [data-ui-backdrop-gallery]');
  await gallery.locator('summary').click();
  await expect(gallery.locator('[data-ui-backdrop-gallery-status]')).toContainText('暂时不可用');
  expect(attempts).toBe(2);
  await gallery.locator('summary').click(); await gallery.locator('summary').click();
  await expect(gallery.locator('[data-ui-backdrop-file]').first()).toBeVisible(); expect(attempts).toBe(3);
  await gallery.locator(`[data-ui-backdrop-file="${usable}"]`).click(); await fullScene(page, backdropUrl(usable));
  await gallery.locator(`[data-ui-backdrop-file="${missing}"]`).click();
  await expect(page.locator('[data-profile-appearance] [data-ui-preference-primary-status]')).toHaveAttribute('data-ui-preference-status', 'saved');
  await expect(page.locator(layer)).toHaveAttribute('data-lq-backdrop-image-url', backdropUrl(usable));
  // Re-running the completed selection promises proves the preserved source is
  // the settled result, not just a screenshot captured before the requests end.
  await page.evaluate(async () => { const owner = (document as any)[Symbol.for('lanshare.page-backdrop')]; await owner.apply({ backdrop: owner.layer.dataset.lqBackdropMode, backdrop_color: owner.layer.dataset.lqBackdropColor }); });
  await fullScene(page, backdropUrl(usable));
  await save(page, { backdrop: 'scene' });
});

test('an unresponsive manifest times out, releases welcome and cover, and can recover', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' }); await loginStudent(page, readFixture());
  await save(page, { backdrop: 'scene' });
  const image = backdropUrl(manifest[0].file);
  await page.evaluate(image => sessionStorage.setItem('lanshareLoginScene', JSON.stringify({ image, tip: '欢迎继续。', t: Date.now() })), image);
  let attempts = 0, release: () => void = () => {};
  const held = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/img/life_tips/manifest.json', async route => {
    attempts++;
    if (attempts === 1) await held;
    await route.continue().catch(() => {});
  });
  await page.goto('/profile?section=appearance');
  await expect(page.locator('html')).toHaveClass(/has-scene-cover/);
  await page.evaluate(async image => {
    const module = await import('/static/js/cultivation_identity.js');
    module.playCultivationReveal(null, { loginTip: { tips: [{ id: 1, text: '网络缓慢时也能继续。', image_url: image }] } });
  }, image);
  await expect(page.locator('.cultivation-login-reveal--tip')).toBeVisible(); await page.keyboard.press('Escape');
  await expect(page.locator('.cultivation-login-reveal--tip')).toHaveCount(0);
  await fullScene(page, image);
  // The first network request is still held by the test. Completion therefore
  // depends on the bounded owner, not a response supplied by the fixture.
  expect(attempts).toBe(1);
  const gallery = page.locator('[data-profile-appearance] [data-ui-backdrop-gallery]');
  await gallery.locator('summary').click();
  await expect(gallery.locator('[data-ui-backdrop-file]').first()).toBeVisible(); expect(attempts).toBe(2);
  release();
});
