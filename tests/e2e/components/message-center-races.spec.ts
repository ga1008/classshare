import { test, expect } from '@playwright/test';
import { bootstrap, conversation, mountMessageCenter as mount } from '../fixtures/message-center-controller';

for (const staleStatus of [200, 404]) test(`late A ${staleStatus} cannot replace B, its URL, summary or focus`, async ({ page }) => {
  const h = await mount(page);
  const old = await h.take('/private/conversation', { contact: 'teacher:1' });
  await h.select('teacher:2');
  await h.reply(await h.take('/private/conversation', { contact: 'teacher:2' }), conversation('teacher:2', 10, 'B current'));
  await page.locator('#message-center-compose-input').fill('B draft');
  await h.reply(old, staleStatus === 200 ? conversation('teacher:1', 10, 'A stale') : { detail: 'A missing' }, staleStatus);
  await expect(page.locator('#message-center-conversation-body')).toContainText('B current');
  await expect(page.locator('#message-center-conversation-body')).not.toContainText('A stale');
  await expect(page.locator('#message-center-compose-input')).toBeFocused();
  await expect(page).toHaveURL(/contact=teacher%3A2/);
  expect(await page.evaluate(() => (window as any).__LANSHARE_MESSAGE_CENTER_WORKSPACE__)).toMatchObject({ currentContactName: '乙老师', canSend: true, loadStatus: 'ready' });
  expect(h.errors).toEqual([]);
});

test('same identity scope changes and A to B to A retain the newest request generation', async ({ page }) => {
  const h = await mount(page);
  const firstA = await h.take('/private/conversation', { contact: 'teacher:1', scope: '10' });
  await h.select('teacher:1', 20);
  const scopedA = await h.take('/private/conversation', { contact: 'teacher:1', scope: '20' });
  await h.select('teacher:2');
  const middleB = await h.take('/private/conversation', { contact: 'teacher:2' });
  await h.select('teacher:1');
  await h.reply(await h.take('/private/conversation', { contact: 'teacher:1', scope: '10' }), conversation('teacher:1', 10, 'A latest'));
  await h.reply(middleB, conversation('teacher:2', 10, 'B stale'));
  await h.reply(scopedA, conversation('teacher:1', 20, 'Scope stale'));
  await h.reply(firstA, conversation('teacher:1', 10, 'A stale'));
  await expect(page.locator('#message-center-conversation-body')).toContainText('A latest');
  await expect(page.locator('#message-center-conversation-body')).not.toContainText('stale');
  await expect(page).toHaveURL(/scope=10/);
  expect(h.errors).toEqual([]);
});

for (const failure of [404, 403, 500, 200]) test(`conversation ${failure} failure is visible, retains text and retries without an unsafe send`, async ({ page }) => {
  const h = await mount(page); await h.readyA();
  await h.select('teacher:2');
  const pendingB = await h.take('/private/conversation', { contact: 'teacher:2' });
  await page.locator('#message-center-compose-input').fill('pending B draft');
  await page.locator('#message-center-compose-form').evaluate((form: HTMLFormElement) => form.requestSubmit());
  await expect(page.locator('[data-send-button]')).toBeDisabled();
  await h.reply(pendingB, { detail: `Unavailable ${failure}` }, failure);
  await expect(page.locator('#message-center-private-panel [data-message-load-error]')).toBeVisible();
  await expect(page.locator('#message-center-conversation-body')).not.toContainText('还没有打开');
  await expect(page.locator('#message-center-compose-input')).toHaveValue('pending B draft');
  expect(h.requests.filter(request => request.startsWith('POST '))).toEqual([]);
  await page.locator('#message-center-private-panel [data-message-load-retry]').click();
  await h.reply(await h.take('/private/conversation', { contact: 'teacher:2' }), conversation('teacher:2', 10, 'B recovered'));
  await expect(page.locator('[data-send-button]')).toBeEnabled();
  await expect(page.locator('#message-center-compose-input')).toHaveValue('pending B draft');
  expect(h.errors).toEqual([]);
});

for (const change of ['tab', 'keyword', 'filter']) test(`late notification list cannot replace new ${change} results`, async ({ page }) => {
  const h = await mount(page, 'notifications');
  const old = await h.take('/items');
  if (change === 'tab') await page.locator('[data-tab="system"]').click();
  if (change === 'keyword') await page.locator('#message-center-search').fill('new keyword');
  if (change === 'filter') await page.locator('#message-center-filter').selectOption('unread');
  await h.reply(await h.take('/items'), { items: [] });
  await h.reply(old, { detail: 'stale request failed' }, 500);
  await expect(page.locator('#message-center-feed [data-message-load-error]')).toHaveCount(0);
  expect(await page.evaluate(() => (window as any).__LANSHARE_MESSAGE_CENTER_WORKSPACE__.loadStatus)).toBe('ready');
  expect(h.requests.filter(request => request.includes('/bootstrap'))[0]).toContain('include_private=0');
  expect(h.requests.filter(request => request.includes('/bootstrap'))[0]).toContain('private_data=0');
  expect(h.errors).toEqual([]);
});

test('late refresh bootstrap cannot reopen A after the user selects B', async ({ page }) => {
  const h = await mount(page); await h.readyA();
  await page.evaluate(() => window.dispatchEvent(new CustomEvent('lanshare:message-center-workspace-command', { detail: { type: 'refresh' } })));
  const refresh = await h.take('/bootstrap');
  await h.select('teacher:2');
  await h.reply(await h.take('/private/conversation', { contact: 'teacher:2' }), conversation('teacher:2', 10, 'B selected'));
  await h.reply(refresh, bootstrap);
  await expect(page.locator('#message-center-conversation-body')).toContainText('B selected');
  expect(h.pending.filter(route => new URL(route.request().url()).pathname.endsWith('/conversation'))).toHaveLength(0);
  expect(await page.evaluate(() => (window as any).__LANSHARE_MESSAGE_CENTER_WORKSPACE__.loadStatus)).toBe('ready');
  expect(h.errors).toEqual([]);
});

for (const mode of ['private', 'notifications']) test(`${mode} bootstrap error appears in the visible panel and can retry`, async ({ page }) => {
  const h = await mount(page, mode, true);
  await h.reply(await h.take('/bootstrap'), { detail: 'Offline fixture' }, 500);
  const error = page.locator('[data-message-load-error]');
  await expect(error).toBeVisible();
  expect(await page.evaluate(() => (window as any).__LANSHARE_MESSAGE_CENTER_WORKSPACE__)).toMatchObject({ loadStatus: 'error', canSend: false });
  await error.locator('button').click();
  await h.reply(await h.take('/bootstrap'), bootstrap);
  if (mode === 'private') await h.readyA();
  else await h.reply(await h.take('/items'), { items: [] });
  await expect(page.locator('[data-message-load-error]')).toHaveCount(0);
  expect(h.errors).toEqual([]);
});

for (const mode of ['private', 'notifications']) test(`${mode} search during first bootstrap preserves loading and initializes metadata`, async ({ page }) => {
  const h = await mount(page, mode, true);
  const first = await h.take('/bootstrap');
  await page.clock.install();
  await page.locator('#message-center-search').fill('latest query');
  await page.clock.fastForward(250);
  const panel = page.locator(mode === 'private' ? '#message-center-conversation-body' : '#message-center-feed');
  await expect(panel).toContainText('正在加载');
  await expect(panel).not.toContainText('还没有打开');
  expect(h.requests.filter(request => request.includes('/items?'))).toHaveLength(0);
  await page.clock.resume();
  await h.reply(first, bootstrap);
  if (mode === 'private') await h.readyA();
  else await h.reply(await h.take('/items', { keyword: 'latest query' }), { items: [] });
  expect(await page.evaluate(() => (window as any).__LANSHARE_MESSAGE_CENTER_WORKSPACE__)).toMatchObject({ loadStatus: 'ready', unreadTotal: 3, keyword: 'latest query' });
  await expect(page.locator('#message-center-filter option')).toHaveCount(2);
  if (mode === 'notifications') await expect(page.locator('[data-tab="system"]')).toBeVisible();
  expect(h.errors).toEqual([]);
});
