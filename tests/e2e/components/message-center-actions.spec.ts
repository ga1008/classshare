import { test, expect, type Page } from '@playwright/test';
import { bootstrap, contacts, conversation, summary, mountMessageCenter } from '../fixtures/message-center-controller';

const input = '#message-center-compose-input';
const actionContacts = contacts.map(contact => ({ ...contact, can_block: true }));
const data = { ...bootstrap, private_contacts: actionContacts };
const block = { identity: 'teacher:1', display_name: '甲老师', role: 'teacher' };
const notifications = [1, 2].map(id => ({
  id, category: 'system', category_label: '系统', title: `通知${id}`, body_preview: `内容${id}`,
  is_unread: true, link_url: `/destination/${id}`, open_url: `/message-center/notifications/${id}/open`,
  created_at: '2026-09-21T08:00:00',
}));

function current(identity = 'teacher:1', scope = 10, blocked = false) {
  const value = conversation(identity, scope, `${identity}/${scope} current`);
  return { ...value, conversation: { ...value.conversation,
    contact: { ...value.conversation.contact, can_block: true, is_blocked: blocked, can_send: !blocked } } };
}

async function mount(page: Page, mode = 'private', initiallyBlocked = false) {
  await page.addInitScript(() => {
    (window as any).__actionRejections = [];
    window.addEventListener('unhandledrejection', event => (window as any).__actionRejections.push(String(event.reason)));
  });
  const h = await mountMessageCenter(page, mode, false, { ...data, private_blocks: initiallyBlocked ? [block] : [] });
  if (mode === 'private') await h.reply(await h.take('/private/conversation'), current('teacher:1', 10, initiallyBlocked));
  else await h.reply(await h.take('/items'), { items: notifications });
  return h;
}

async function select(page: Page, h: Awaited<ReturnType<typeof mount>>, identity: string, scope = 10) {
  await h.select(identity, scope);
  await h.reply(await h.take('/private/conversation', { contact: identity, scope: String(scope) }), current(identity, scope));
  await expect(page.locator(input)).toBeEditable();
}

async function noUnhandled(page: Page, h: Awaited<ReturnType<typeof mount>>) {
  expect(h.errors).toEqual([]);
  expect(await page.evaluate(() => (window as any).__actionRejections)).toEqual([]);
}

test.describe('LQ C2 user action mutation ownership', () => {
  for (const kind of ['category', 'notification', 'open']) test(`${kind} read failure is visible once and does not reject, succeed or navigate`, async ({ page }) => {
    const h = await mount(page, 'notifications');
    const selector = kind === 'category' ? '#message-center-mark-read'
      : kind === 'notification' ? '[data-mark-notification="1"]' : '[data-open-notification="1"]';
    await page.locator(selector).click();
    const post = await h.take('/read');
    await h.reply(post, { detail: '本次标记失败，请重试' }, 503);
    await expect(page.getByText('本次标记失败，请重试', { exact: true })).toBeVisible();
    await expect(page.getByText('本次标记失败，请重试', { exact: true })).toHaveCount(1);
    await expect(page).toHaveURL(/message-races\.test\/\?/);
    await expect(page.getByText('当前分类已标记为已读', { exact: true })).toHaveCount(0);
    await noUnhandled(page, h);
    // Explicit retry is still possible, without an automatic second mutation.
    expect(h.requests.filter(request => request.startsWith('POST '))).toHaveLength(1);
    await page.locator(selector).click();
    await h.reply(await h.take('/read'), { detail: '重试仍失败' }, 403);
    await expect(page.getByText('重试仍失败', { exact: true })).toBeVisible();
    await noUnhandled(page, h);
  });

  for (const blocked of [false, true]) test(`${blocked ? 'unblock' : 'block'} failure releases its lease and preserves current view`, async ({ page }) => {
    const h = await mount(page, 'private', blocked);
    const selector = blocked ? '[data-unblock="teacher:1"]' : '[data-toggle-block="teacher:1"]';
    await page.locator(selector).click();
    const post = await h.take('/private/blocks');
    await page.locator(selector).evaluate((button: HTMLButtonElement) => button.click());
    expect(h.requests.filter(request => /^(POST|DELETE) /.test(request))).toHaveLength(1);
    await h.reply(post, { detail: '黑名单更新失败' }, 500);
    await expect(page.getByText('黑名单更新失败', { exact: true })).toBeVisible();
    await expect(page.getByText('黑名单更新失败', { exact: true })).toHaveCount(1);
    await expect(page).toHaveURL(/scope=10/);
    await expect(page.locator('#message-center-contact-select')).toHaveValue('teacher:1|scope:10');
    await expect(page.locator(selector)).toBeEnabled();
    await noUnhandled(page, h);
  });

  test('pending scope10 block cannot restore old scope after the user selects scope20', async ({ page }) => {
    const h = await mount(page);
    await page.locator('[data-toggle-block="teacher:1"]').click();
    const post = await h.take('/private/blocks');
    expect(post.request().postDataJSON()).toMatchObject({ contact_identity: 'teacher:1', class_offering_id: 10 });
    await select(page, h, 'teacher:1', 20);
    await page.locator(input).fill('scope20 draft');
    await h.reply(post, { status: 'success', block, blocks: [block], contacts: [], summary: { ...summary, unread_total: 99 } });
    const refresh = await h.take('/bootstrap');
    // Stale POST metadata must never be published while the real refresh waits.
    expect(await page.evaluate(() => (window as any).__LANSHARE_MESSAGE_CENTER_WORKSPACE__.unreadTotal)).not.toBe(99);
    await h.reply(refresh, { ...data, private_blocks: [block] });
    await h.reply(await h.take('/private/conversation', { scope: '20' }), current('teacher:1', 20, true));
    await expect(page.locator('#message-center-contact-select')).toHaveValue('teacher:1|scope:20');
    await expect(page).toHaveURL(/scope=20/);
    await expect(page.locator(input)).toHaveValue('scope20 draft');
    expect(h.requests.filter(request => request.includes('/private/conversation?') && request.includes('scope=10'))).toHaveLength(1);
    await noUnhandled(page, h);
  });

  test('global blacklist removal does not replace the current scoped conversation with null scope', async ({ page }) => {
    const h = await mount(page, 'private', true);
    await page.locator('[data-unblock="teacher:1"]').click();
    const removal = await h.take('/private/blocks');
    expect(removal.request().method()).toBe('DELETE');
    expect(new URL(removal.request().url()).searchParams.get('contact_identity')).toBe('teacher:1');
    await h.reply(removal, { status: 'success', removed_count: 0, blocks: [], contacts: actionContacts, summary });
    await h.reply(await h.take('/bootstrap'), data);
    await h.reply(await h.take('/private/conversation', { scope: '10' }), current());
    await expect(page).toHaveURL(/scope=10/);
    await expect(page.locator('#message-center-contact-select')).toHaveValue('teacher:1|scope:10');
    expect(h.requests.filter(request => request.includes('/private/conversation?')).every(request => request.includes('scope=10'))).toBe(true);
    await noUnhandled(page, h);
  });

  test('A block completion revalidates B without clearing its draft or stealing focused search', async ({ page }) => {
    const h = await mount(page);
    await page.locator('[data-toggle-block="teacher:1"]').click();
    const post = await h.take('/private/blocks');
    await select(page, h, 'teacher:2');
    await page.locator(input).fill('B unrelated draft');
    await page.locator('#message-center-file-input').setInputFiles({ name: 'B.txt', mimeType: 'text/plain', buffer: Buffer.from('B file') });
    await page.locator('#message-center-contact-search').focus();
    await h.reply(post, { status: 'success', block, blocks: [block], contacts: [], summary });
    await h.reply(await h.take('/bootstrap'), { ...data, private_blocks: [block] });
    await h.reply(await h.take('/private/conversation', { contact: 'teacher:2' }), current('teacher:2'));
    await expect(page.locator(input)).toHaveValue('B unrelated draft');
    await expect(page.getByRole('button', { name: '移除 B.txt' })).toBeVisible();
    await expect(page.locator('#message-center-contact-search')).toBeFocused();
    await expect(page).toHaveURL(/contact=teacher%3A2/);
    await noUnhandled(page, h);
  });

  test('read response cannot apply its old summary or restore a departed category', async ({ page }) => {
    const h = await mount(page, 'notifications');
    await page.locator('#message-center-mark-read').click();
    const post = await h.take('/read');
    expect(post.request().postDataJSON()).toMatchObject({ category: 'all', include_private: false });
    await page.locator('[data-tab="system"]').click();
    await h.reply(await h.take('/items', { category: 'system' }), { items: notifications });
    await h.reply(post, { status: 'success', updated_count: 0, summary: { ...summary, unread_total: 99 } });
    expect(await page.evaluate(() => (window as any).__LANSHARE_MESSAGE_CENTER_WORKSPACE__.unreadTotal)).not.toBe(99);
    await h.reply(await h.take('/bootstrap'), bootstrap);
    await h.reply(await h.take('/items', { category: 'system' }), { items: notifications });
    await expect(page).toHaveURL(/tab=system/);
    await expect(page.getByText('当前分类已标记为已读', { exact: true })).toHaveCount(0);
    await noUnhandled(page, h);
  });

  test('confirmed mutation plus failed refresh is partial completion, with only a GET retry', async ({ page }) => {
    const h = await mount(page, 'notifications');
    await page.locator('#message-center-mark-read').click();
    await h.reply(await h.take('/read'), { status: 'success', updated_count: 0, summary });
    await h.reply(await h.take('/bootstrap'), { detail: '最新列表暂不可用' }, 503);
    await expect(page.getByText(/已.*标记.*刷新失败/)).toBeVisible();
    await expect(page.getByText('当前分类已标记为已读', { exact: true })).toHaveCount(0);
    await page.locator('[data-message-load-retry]').click();
    await h.reply(await h.take('/bootstrap'), bootstrap);
    await h.reply(await h.take('/items'), { items: [] });
    expect(h.requests.filter(request => request.startsWith('POST '))).toHaveLength(1);
    await noUnhandled(page, h);
  });

  test('a departed notification open intent does not navigate when its read finishes', async ({ page }) => {
    const h = await mount(page, 'notifications');
    await page.locator('[data-open-notification="1"]').click();
    const post = await h.take('/read');
    await page.locator('[data-tab="system"]').click();
    await h.reply(await h.take('/items', { category: 'system' }), { items: notifications });
    await h.reply(post, { status: 'success', updated_count: 1, summary });
    await expect(page).toHaveURL(/message-races\.test\/\?tab=system/);
    await expect(page.locator('#message-center-feed')).toContainText('通知1');
    await noUnhandled(page, h);
  });

  test('notification open is deduplicated and a hanging auxiliary bell does not delay native destination', async ({ page }) => {
    const h = await mount(page, 'notifications');
    await page.evaluate(() => { (window as any).refreshMessageCenterBell = () => new Promise(() => {}); });
    await page.route('**/message-center/notifications/1/open', route => route.fulfill({ contentType: 'text/html', body: '<h1>Native authorized destination</h1>' }));
    await page.locator('[data-open-notification="1"]').click();
    const post = await h.take('/read');
    await page.locator('[data-open-notification="1"]').evaluate((link: HTMLAnchorElement) => link.click());
    expect(h.requests.filter(request => request.startsWith('POST '))).toHaveLength(1);
    await post.fulfill({ json: { status: 'success', updated_count: 1, summary } });
    await expect(page).toHaveURL('https://message-races.test/message-center/notifications/1/open');
    await expect(page.getByRole('heading', { name: 'Native authorized destination' })).toBeVisible();
    expect(h.errors).toEqual([]);
  });

  test('a newer notification choice cancels an older pending open without queuing another mutation', async ({ page }) => {
    const h = await mount(page, 'notifications');
    await page.locator('[data-open-notification="1"]').click();
    const post = await h.take('/read');
    await page.locator('[data-open-notification="2"]').evaluate((link: HTMLAnchorElement) => link.click());
    await h.reply(post, { status: 'success', updated_count: 1, summary });
    await expect(page).toHaveURL(/message-races\.test\/\?tab=all/);
    expect(h.requests.filter(request => request.startsWith('POST '))).toHaveLength(1);
    await expect(page.locator('[data-open-notification="2"]')).not.toHaveAttribute('aria-disabled', 'true');
    await noUnhandled(page, h);
  });

  for (const kind of ['read', 'block', 'unblock']) test(`${kind} rejects an unknown 200 receipt without pretending success`, async ({ page }) => {
    const h = await mount(page, kind === 'read' ? 'notifications' : 'private', kind === 'unblock');
    await page.locator(kind === 'read' ? '#message-center-mark-read'
      : kind === 'block' ? '[data-toggle-block="teacher:1"]' : '[data-unblock="teacher:1"]').click();
    const post = await h.take(kind === 'read' ? '/read' : '/private/blocks');
    const count = h.requests.length;
    await h.reply(post, { status: 'success', summary });
    await expect(page.getByText('未取得有效的操作回执，状态尚未确认，请刷新后检查。', { exact: true })).toBeVisible();
    expect(h.requests).toHaveLength(count);
    await noUnhandled(page, h);
  });

  test('private mark-read deduplicates native and workspace actions and releases after a failed GET', async ({ page }) => {
    const h = await mount(page);
    await page.locator('#message-center-mark-read').click();
    const refresh = await h.take('/private/conversation');
    await page.evaluate(() => window.dispatchEvent(new CustomEvent('lanshare:message-center-workspace-command', { detail: { type: 'mark-read' } })));
    expect(h.requests.filter(request => request.includes('/private/conversation?'))).toHaveLength(2);
    await h.reply(refresh, { detail: '当前会话无法刷新' }, 503);
    await expect(page.locator('[data-message-load-error]')).toBeVisible();
    await expect(page.locator('#message-center-mark-read')).toBeEnabled();
    await expect(page.getByText('当前私信会话已更新为已读', { exact: true })).toHaveCount(0);
    expect(h.requests.filter(request => request.startsWith('POST '))).toHaveLength(0);
    await noUnhandled(page, h);
  });

  for (const status of [200, 503]) test(`pagehide discards pending mutation ${status} continuation`, async ({ page }) => {
    const h = await mount(page);
    await page.locator('[data-toggle-block="teacher:1"]').click();
    const post = await h.take('/private/blocks');
    await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pagehide', { persisted: false })));
    const count = h.requests.length;
    await h.reply(post, status === 200 ? { status: 'success', blocks: [block], contacts: actionContacts, summary } : { detail: 'Destroyed owner failure' }, status);
    expect(h.requests).toHaveLength(count);
    await expect(page.getByText('Destroyed owner failure', { exact: true })).toHaveCount(0);
    await expect(page.getByText('已加入黑名单', { exact: true })).toHaveCount(0);
    await noUnhandled(page, h);
  });
});
