import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { readFixture, loginStudent, loginTeacher } from '../fixtures/p03';

async function preferences(page: Page) {
  const response = await page.request.get('/api/profile/ui-preferences');
  expect(response.ok()).toBe(true); return (await response.json()).preferences;
}
async function save(page: Page, changes: Record<string, string>) {
  const current = await preferences(page);
  const response = await page.request.patch('/api/profile/ui-preferences', {
    headers: { 'X-UI-Preferences-Context': current.context_token }, data: { ...changes, version: current.version },
  });
  expect(response.status()).toBe(200);
}

test.beforeEach(async ({ page, baseURL }, info) => {
  const fixture = readFixture();
  expect((fixture as any).uiV3Synthetic).toBe(true);
  expect((await (await page.request.get('/api/internal/health')).json()).database_path).toBe(fixture.databasePath);
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin !== baseURL && !['data:', 'blob:'].includes(url.protocol)) return route.abort();
    // Optional pre-build source verification; authentication and all message
    // requests still run through the real isolated application's routes.
    if (process.env.LQ_MESSAGE_SOURCE_OVERLAY === '1' && url.pathname.startsWith('/static/')) {
      const source = url.pathname.replace(/^\/static\/(?:assets\/[a-f0-9]{64}\/)?/, 'static/');
      const file = path.resolve(source);
      if (file.startsWith(path.resolve('static') + path.sep) && fs.existsSync(file) && /\.(js|css)$/.test(file)) {
        return route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body: fs.readFileSync(file) });
      }
    }
    return route.continue();
  });
  await info.attach('asset-mode', { body: process.env.LQ_MESSAGE_SOURCE_OVERLAY === '1' ? 'source overlay; real synthetic API' : 'built assets; real synthetic API', contentType: 'text/plain' });
});

for (const role of ['student', 'teacher'] as const) test(`${role} message overview and filters remain readable and usable across widths`, async ({ page }, info) => {
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  await (role === 'student' ? loginStudent : loginTeacher)(page, readFixture());
  const previous = await preferences(page);
  try {
    await save(page, { appearance: 'light', glass: 'tinted', backdrop: 'image:xueye-night-lab05-6a25be23.webp' });
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto(role === 'teacher' ? '/manage/me/notifications' : '/profile?section=notifications');
    const app = page.locator('[data-lq-messages]');
    const overview = app.locator('.lq-messages__overview');
    const status = app.getByRole('combobox', { name: '阅读状态' });
    await expect(status).toHaveValue('全部');
    await expect(overview.locator('.lq-message-workspace__counts')).toBeVisible();
    await expect(overview).toHaveAttribute('data-lq-material', 'content');
    expect(await overview.evaluate(node => getComputedStyle(node).backgroundColor)).not.toBe('rgba(0, 0, 0, 0)');
    await expect(overview.locator('h2')).toHaveText('通知中心');
    await expect(overview.locator('dd').first()).toHaveCSS('margin-left', '0px');
    const notificationList = app.locator('#message-center-feed > .lq-list');
    await expect(notificationList.locator('.lq-row')).toHaveCount(1);
    expect((await notificationList.boundingBox())!.height).toBeLessThan(200);
    const search = app.getByRole('searchbox', { name: '搜索消息' });
    const searchBox = await search.boundingBox(), filterBox = await status.boundingBox();
    expect(Math.abs(searchBox!.y - filterBox!.y)).toBeLessThan(4);
    expect(searchBox!.x + searchBox!.width).toBeLessThanOrEqual(filterBox!.x);
    await app.scrollIntoViewIfNeeded();
    await page.screenshot({ path: info.outputPath(`${role}-desktop-photo.png`), fullPage: true });

    const initialTabs = await app.locator('[data-tab]').count();
    await app.locator('[data-tabs-toggle]').click();
    expect(await app.locator('[data-tab]').count()).toBeGreaterThan(initialTabs);
    await app.locator('[data-tabs-toggle]').click();
    await expect(app.locator('[data-tab]')).toHaveCount(initialTabs);

    await status.focus(); await page.keyboard.press('ArrowDown');
    await expect(page.getByRole('listbox', { name: '阅读状态' })).toBeVisible();
    await expect(page.locator('.lq-selection__popup')).toHaveAttribute('data-ui-overlay-state', 'open');
    expect(await page.locator('.lq-selection__popup').evaluate(node => getComputedStyle(node).backdropFilter)).toContain('blur(');
    await page.screenshot({ path: info.outputPath(`${role}-status-popup.png`) });
    const unread = page.getByRole('option', { name: '仅未读', exact: true });
    const filtered = page.waitForResponse(r => r.url().includes('/api/message-center/items?') && new URL(r.url()).searchParams.get('filter') === 'unread');
    await unread.click(); expect((await filtered).ok()).toBe(true);
    await expect(app.locator('#message-center-filter')).toHaveValue('unread');
    await expect(status).toHaveValue('仅未读');
    const searched = page.waitForResponse(r => r.url().includes('/api/message-center/items?') && new URL(r.url()).searchParams.get('keyword') === '布局回归');
    await search.fill('布局回归'); expect((await searched).ok()).toBe(true);
    const read = page.waitForResponse(r => r.url().endsWith('/api/message-center/read') && r.request().method() === 'POST');
    await app.locator('#message-center-mark-read').click(); expect((await read).ok()).toBe(true);

    await page.setViewportSize({ width: 390, height: 844 });
    await app.scrollIntoViewIfNeeded();
    await expect(app.locator('#message-center-mark-read')).toBeVisible();
    const mobile = await app.evaluate(node => ({ scroll: node.scrollWidth, width: node.clientWidth, viewport: innerWidth, right: node.getBoundingClientRect().right }));
    expect(mobile.scroll).toBeLessThanOrEqual(mobile.width + 1); expect(mobile.right).toBeLessThanOrEqual(mobile.viewport);
    await page.screenshot({ path: info.outputPath(`${role}-mobile-photo.png`), fullPage: true });
    await page.evaluate(() => { document.documentElement.dataset.appearance = 'dark'; });
    await page.screenshot({ path: info.outputPath(`${role}-mobile-dark.png`), fullPage: true });

    await app.locator('.lq-message-workspace__actions').getByRole('link', { name: '私信' }).click();
    await expect(page.locator('[data-message-center-mode="private"]')).toBeVisible();
    await expect(page.locator('#message-center-private-panel')).toBeVisible();
    await expect(page.locator('#message-center-compose-input')).toBeAttached();
    await page.locator('.lq-message-workspace__actions').getByRole('link', { name: '通知' }).click();
    await expect(page.locator('[data-message-center-mode="notifications"]')).toBeVisible();
    expect(errors).toEqual([]);
  } finally {
    await save(page, Object.fromEntries(['appearance', 'glass', 'palette_key', 'backdrop', 'backdrop_color'].map(key => [key, previous[key]])));
  }
});
