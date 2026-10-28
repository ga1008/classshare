import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const runtime = process.env.AI_SEARCH_RUNTIME ? path.resolve(process.env.AI_SEARCH_RUNTIME) : '';
const fixture = runtime ? JSON.parse(fs.readFileSync(path.join(runtime, 'fixture.json'), 'utf8')) : null;
const origin = `http://127.0.0.1:${process.env.AI_SEARCH_PORT || '8375'}`;
const graph = JSON.parse(fs.readFileSync('static/assets/manifest.json', 'utf8'));
test.skip(!runtime, 'Requires the owned synthetic AI-search fixture');
if (fixture && (!runtime.startsWith(path.resolve('.codex-temp') + path.sep) || !fixture.uiV3Synthetic
  || !fixture.aiSearchSeed || path.resolve(fixture.databasePath) !== path.join(runtime, 'db/classroom.db')))
  throw Error('Live AI tests may never use a business database');

async function login(page: Page, student = false) {
  await page.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
  const health = await page.request.get(`${origin}/api/internal/health`);
  expect(path.resolve((await health.json()).database_path)).toBe(path.resolve(fixture.databasePath));
  const response = await page.request.post(`${origin}${student ? '/api/student/login/password' : '/teacher/login'}`, {
    form: student ? { identifier: fixture.student.studentNumber, password: fixture.password }
      : { email: fixture.teacher.email, password: fixture.password, next: '/dashboard' }, maxRedirects: 0,
  });
  expect(response.status()).toBe(student ? 200 : 303);
  if (!student) await page.request.post(`${origin}/api/manage/teacher-onboarding/dismiss`, { data: { reason: 'later' } });
}
async function visit(page: Page) {
  await page.goto(`${origin}/dashboard`);
  await page.waitForLoadState('networkidle');
  const reveal = page.locator('.cultivation-login-reveal');
  if (await reveal.count()) {
    const skip = page.locator('[data-life-tip-skip]');
    if (await skip.isVisible()) await skip.click(); else await page.keyboard.press('Escape');
    await expect(reveal).toHaveCount(0);
  }
}

for (const scene of [
  { name: 'desktop-light', width: 1440, height: 900, dark: false, glass: true },
  { name: 'mobile-dark', width: 360, height: 640, dark: true, glass: true },
  { name: 'mobile-opaque', width: 360, height: 568, dark: true, glass: false },
]) test(`LQ live AI search controls and account quota ${scene.name}`, async ({ page }) => {
  await page.setViewportSize({ width: scene.width, height: scene.height });
  const errors: string[] = [], models: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => { if (/\/api\/ai\/(workspace-chat|chat|search-dispatch)/.test(request.url())) models.push(request.url()); });
  await login(page);
  const quota = await (await page.request.get(`${origin}/api/ai/search-quota?channel=chat`)).json();
  expect(quota.quota.limit).toBe(10); expect(quota.quota.available).toBe(true);
  const agent = await (await page.request.get(`${origin}/api/ai/search-quota?channel=agent`)).json();
  expect(agent.quota.channel).toBe('agent'); expect(agent.quota.remaining).toBe(10);
  await visit(page);
  await page.evaluate(({ dark, glass }) => {
    document.documentElement.dataset.appearance = dark ? 'dark' : 'light';
    document.documentElement.dataset.lqSceneTone = dark ? 'dark' : 'light';
    document.documentElement.dataset.lqGlass = glass ? 'on' : 'off';
  }, scene);
  await page.locator('#ai-chat-fab').click();
  await expect(page.locator('#ai-chat-modal')).toHaveAttribute('aria-hidden', 'false');
  await expect.poll(() => page.evaluate(src => performance.getEntriesByType('resource')
    .some(entry => new URL(entry.name).pathname === src), `/static/${graph.entries['js/ai_chat_component.js']}`)).toBe(true);
  const toggle = page.locator('#ai-web-search-btn');
  await expect(toggle).toHaveAttribute('aria-pressed', 'false');
  await toggle.click();
  await expect(toggle).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('#ai-web-search-quota')).toContainText(`剩余 ${quota.quota.remaining}/10`);
  await page.locator('#ai-deep-think-btn').click();
  await expect(toggle).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('#ai-web-search-quota')).toContainText(`剩余 ${quota.quota.remaining}/10`);
  const box = await toggle.boundingBox();
  expect(box!.x).toBeGreaterThanOrEqual(0);
  expect(box!.y + box!.height).toBeLessThanOrEqual(scene.height + 1);
  expect(await page.locator('#ai-chat-modal').evaluate(el => el.scrollWidth - el.clientWidth)).toBeLessThanOrEqual(1);
  await page.screenshot({ path: test.info().outputPath(`ai-search-ssr-${scene.name}.png`) });
  await page.locator('#ai-chat-btn-close').click();
  await expect(page.locator('#ai-chat-modal')).toHaveAttribute('aria-hidden', 'true');
  await expect(page.locator('#ai-chat-fab')).toBeFocused();
  expect(models).toEqual([]); // Opening/toggling consumes neither model nor search calls.
  expect(errors).toEqual([]);
});

test('LQ live student has chat quota and cannot claim the teacher Agent bucket', async ({ page }) => {
  await login(page, true);
  const chat = await page.request.get(`${origin}/api/ai/search-quota?channel=chat`);
  expect(chat.ok()).toBeTruthy(); expect((await chat.json()).quota.channel).toBe('chat');
  expect((await page.request.get(`${origin}/api/ai/search-quota?channel=agent`)).status()).toBe(403);
});
