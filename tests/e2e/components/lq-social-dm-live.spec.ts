import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

// Real SSR, auth and social APIs; writes are restricted to this task's owned fixture.
const runtime = process.env.SOCIAL_CHAT_RUNTIME ? path.resolve(process.env.SOCIAL_CHAT_RUNTIME) : '';
const fixture = runtime ? JSON.parse(fs.readFileSync(path.join(runtime, 'fixture.json'), 'utf8')) : null;
const origin = `http://127.0.0.1:${process.env.SOCIAL_CHAT_PORT || '8374'}`;
const graph = JSON.parse(fs.readFileSync('static/assets/manifest.json', 'utf8'));
test.skip(!runtime, 'Requires an explicitly owned synthetic social fixture and loopback server');
if (fixture && (!runtime.startsWith(path.resolve('.codex-temp') + path.sep) || !fixture.uiV3Synthetic
    || !fixture.socialChatSeed || path.resolve(fixture.databasePath) !== path.join(runtime, 'db/classroom.db'))) {
  throw Error('DM live checks must never write a business database');
}
async function login(page: Page, student = false) {
  const health = await page.request.get(`${origin}/api/internal/health`);
  expect(health.ok()).toBeTruthy();
  expect(path.resolve((await health.json()).database_path)).toBe(path.resolve(fixture.databasePath));
  const response = await page.request.post(`${origin}${student ? '/api/student/login/password' : '/teacher/login'}`, {
    form: student ? { identifier: fixture.student.studentNumber, password: fixture.password }
      : { email: fixture.teacher.email, password: fixture.password, next: '/dashboard' }, maxRedirects: 0,
  });
  expect(response.status()).toBe(student ? 200 : 303);
  if (!student) await page.request.post(`${origin}/api/manage/teacher-onboarding/dismiss`, { data: { reason: 'later' } });
  // This fixture grants synthetic tiers for the radar screenshot. Acknowledge
  // their award ceremony via the real student API before testing the chat layer.
  if (student) for (const id of fixture.dmSidebarCertificateIds || [])
    expect((await page.request.post(`${origin}/api/learning/certificates/${id}/revealed`)).ok()).toBeTruthy();
}
async function openDm(page: Page) {
  await page.goto(`${origin}/dashboard`);
  await expect(page.locator(`script[src="/static/${graph.entries['js/social/social_client.js']}"]`).first()).toHaveCount(1);
  await page.locator('[data-social-entry]:visible').first().click();
  await expect(page.locator('#social-chat')).toBeVisible();
  const list = page.locator('[data-chat-toggle="list"]');
  if (await list.isVisible() && await list.getAttribute('aria-expanded') !== 'true') await list.click();
  await page.locator(`[data-conv="${fixture.socialChatSeed.dmKey}"]`).click();
  const side = page.locator('[data-chat-toggle="side"]');
  if (await side.isVisible() && await side.getAttribute('aria-expanded') !== 'true') await side.click();
  await expect(page.locator('[data-dm-card="profile"]')).toBeVisible();
}
async function inside(page: Page, selector: string) {
  const box = await page.locator(selector).boundingBox();
  expect(box).not.toBeNull();
  const viewport = page.viewportSize()!;
  expect(box!.x).toBeGreaterThanOrEqual(-1);
  expect(box!.y).toBeGreaterThanOrEqual(-1);
  expect(box!.x + box!.width).toBeLessThanOrEqual(viewport.width + 1);
  expect(box!.y + box!.height).toBeLessThanOrEqual(viewport.height + 1);
}

for (const scene of [
  { name: 'desktop-light', width: 1440, height: 900, dark: false, glass: true },
  { name: 'mobile-short-dark', width: 360, height: 640, dark: true, glass: true },
  { name: 'mobile-short-opaque', width: 360, height: 640, dark: false, glass: false },
]) test(`LQ live DM ${scene.name} cards, settings and assistant clearance`, async ({ page }) => {
  await page.setViewportSize({ width: scene.width, height: scene.height });
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await login(page);
  expect((await page.request.patch(`${origin}/api/social/friends/${fixture.socialChatSeed.studentIdentity}`, { data: { remark: '' } })).ok()).toBeTruthy();
  expect((await page.request.patch(`${origin}/api/social/conversations/${encodeURIComponent(fixture.socialChatSeed.dmKey)}/preferences`, { data: { muted: false } })).ok()).toBeTruthy();
  await openDm(page);
  await page.evaluate(({ dark, glass }) => {
    document.documentElement.dataset.appearance = dark ? 'dark' : 'light';
    document.documentElement.dataset.lqSceneTone = dark ? 'dark' : 'light';
    document.documentElement.dataset.lqGlass = glass ? 'on' : 'off';
  }, scene);
  await expect(page.locator('[data-dm-card="radar"] svg')).toBeVisible();
  await expect(page.locator('.social-chat__side #social-remark')).toHaveCount(0);
  await expect(page.locator('.social-chat__side [data-dm-remove]')).toHaveCount(0);
  await inside(page, '.social-chat__side');
  const overflow = await page.locator('.social-chat__side').evaluate(el => el.scrollWidth - el.clientWidth);
  expect(overflow).toBeLessThanOrEqual(1);
  await page.screenshot({ path: test.info().outputPath(`live-dm-${scene.name}.png`) });
  await page.locator('[data-dm-settings-open]').click();
  await expect(page.locator('#social-dm-settings')).toBeVisible();
  await expect(page.locator('#social-remark')).toBeEnabled();
  await page.locator('#social-remark').fill('将丢弃的合成草稿');
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: '放弃修改', exact: true }).click();
  await expect(page.locator('#social-dm-settings')).toBeHidden();
  await expect(page.locator('[data-dm-settings-open]')).toBeFocused();
  await page.locator('[data-dm-settings-open]').click();
  await expect(page.locator('#social-remark')).toHaveValue('');
  await page.locator('#social-remark').fill('合成实验搭档');
  await page.locator('#social-dm-muted').check();
  const save = page.locator('[data-dm-settings-save]');
  await save.scrollIntoViewIfNeeded();
  const saveBox = await save.boundingBox(), fab = await page.locator('#ai-chat-fab').boundingBox();
  if (saveBox && fab) expect(saveBox.x + saveBox.width <= fab.x || saveBox.x >= fab.x + fab.width
    || saveBox.y + saveBox.height <= fab.y || saveBox.y >= fab.y + fab.height).toBeTruthy();
  await inside(page, '#social-dm-settings .lq-dialog__surface');
  await page.screenshot({ path: test.info().outputPath(`live-dm-settings-${scene.name}.png`) });
  await save.click();
  await expect(page.locator('[data-dm-settings-status]')).toContainText('设置已保存');
  await page.keyboard.press('Escape');
  await expect(page.locator('#social-dm-settings')).toBeHidden();
  await expect(page.locator('[data-dm-settings-open]')).toBeFocused();
  await expect(page.locator('[data-dm-card="profile"]')).toContainText('合成实验搭档');
  const preferences = await (await page.request.get(`${origin}/api/social/conversations/${encodeURIComponent(fixture.socialChatSeed.dmKey)}/preferences`)).json();
  expect(preferences.muted).toBe(true);
  expect(errors).toEqual([]);
});

test('LQ live student sees a teacher card without invented cultivation or radar', async ({ page }) => {
  await login(page, true);
  await openDm(page);
  await expect(page.locator('[data-dm-card="profile"]')).toContainText('老师');
  await expect(page.locator('[data-dm-card="cultivation"]')).toHaveCount(0);
  await expect(page.locator('[data-dm-card="radar"]')).toHaveCount(0);
  await expect(page.locator('[data-dm-interests]')).toHaveCount(0);
});
