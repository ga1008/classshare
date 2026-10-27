import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';

const manifest = JSON.parse(fs.readFileSync('static/assets/manifest.json', 'utf8'));
const asset = (name: string) => `/static/${manifest.entries[name]}`;
const member = { identity: 'student:2', name: '林晚舟', group_role: 'member', online: true };
const friend = { identity: 'student:3', name: '陈清禾', remark: '实验搭档', role_label: '学生', org_label: '合成学院 · 网络工程', relation: 'friend' };
const another = { ...friend, identity: 'student:4', name: '谢明', remark: '' };
const originalGroup = { id: 7, name: '网络实验与星空摄影讨论小组', display_name: '网络实验与星空摄影讨论小组', announcement: '周五带上本周实验记录。欢迎分享思路，文件和截图都可以发在群里。', my_role: 'owner',
  updated_at: 'v1', max_members: 200, member_count: 2, online_count: 2, conversation_key: 'group:7', me: { alias: '', nickname: '', muted: false },
  members: [{ identity: 'student:1', name: '我', group_role: 'owner', online: true }, member] };

async function mount(page: Page, options: { role?: string; dark?: boolean } = {}) {
  for (const source of ['js/social/chat_sidebar.js', 'js/social/group_settings.js', 'js/social/group_invite.js', 'js/social/group_files.js', 'js/social/social_dom.js', 'css/lq/pages/social.css']) {
    expect(createHash('sha256').update(fs.readFileSync(`static/${source}`)).digest('hex')).toBe(manifest.sourceHashes[source]);
  }
  const state: any = { group: structuredClone(originalGroup), friends: [member, friend, another], calls: [], errors: [], updates: [], failPrivate: 0, failInvite: 0, inviteResult: null };
  if (options.role) { state.group.my_role = options.role; state.group.members[0].group_role = options.role; }
  page.on('pageerror', error => state.errors.push(error.message));
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.origin !== 'https://group-sidebar.test') return route.abort();
    if (url.pathname.startsWith(`/static/assets/${manifest.revision}/`)) {
      const file = path.resolve(`.${url.pathname}`);
      return route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body: fs.readFileSync(file) });
    }
    if (url.pathname.startsWith('/api/social/')) {
      const endpoint = url.pathname.slice('/api/social'.length), method = route.request().method();
      const body = route.request().postData() ? route.request().postDataJSON() : {};
      state.calls.push({ endpoint, method, body, query: url.search });
      const json = (value: any, status = 200) => route.fulfill({ status, json: value });
      if (endpoint === '/friends') return json({ items: state.friends });
      if (endpoint === '/groups/7') {
        if (method === 'PATCH') {
          if (body.expected_updated_at !== state.group.updated_at) return json({ detail: '群资料已更新' }, 409);
          if (state.group.my_role === 'member') return json({ detail: '需要群管理员权限' }, 403);
          state.group = { ...state.group, ...body, updated_at: `${state.group.updated_at}x` };
          state.group.display_name = state.group.me.alias || state.group.name;
        }
        return json(state.group);
      }
      if (endpoint === '/groups/7/me') {
        if (state.failPrivate) return json({ detail: '暂时无法保存，请稍后重试' }, state.failPrivate);
        state.group.me = { ...state.group.me, ...body };
        state.group.display_name = state.group.me.alias || state.group.name;
        return json({ group_id: 7, ...state.group.me, display_name: state.group.display_name });
      }
      if (endpoint === '/groups/7/members') {
        if (state.failInvite) return json({ detail: '网络暂时不可用' }, state.failInvite);
        const result = state.inviteResult || { added: body.identities, skipped: [] };
        for (const identity of result.added) if (!state.group.members.some((item: any) => item.identity === identity)) state.group.members.push({ ...state.friends.find((item: any) => item.identity === identity), group_role: 'member' });
        state.group.member_count = state.group.members.length;
        return json(result);
      }
      if (/\/files$/.test(endpoint)) return json({ items: [], has_more: false });
      if (/\/(leave|dissolve|nudge)$/.test(endpoint)) return json({ ok: true });
      return json({ detail: `Unmocked ${method} ${endpoint}` }, 404);
    }
    return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh-CN" data-theme="lanshare" data-ui-palette="teal" data-appearance="${options.dark ? 'dark' : 'light'}" data-lq-scene-tone="${options.dark ? 'dark' : 'light'}" data-lq-glass="${options.dark ? 'off' : 'on'}" data-lq-motion="off"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="${asset('css/tailwind-app.css')}"><link rel="stylesheet" href="${asset('css/lq/pages/social.css')}"></head><body><button id="open" class="lq-btn lq-btn--glass">打开群聊</button><script type="module">
      import {createChatSidebar} from '${asset('js/social/chat_sidebar.js')}';
      import {createSocialDialog,el} from '${asset('js/social/social_dom.js')}';
      const detail=${JSON.stringify(state.group)};
      const updates=[];
      const client={me:'student:1',setMuted:(key,value)=>window.fixture.muted=value,refreshCounts(){},isOnline(){return true;}};
      const sidebar=createChatSidebar({client,onChanged:(reason,data)=>updates.push({reason,...data})});
      const parent=createSocialDialog({id:'group-parent',title:'聊天',size:'md',body:sidebar.element});
      document.querySelector('#open').onclick=event=>{parent.open(event.currentTarget);sidebar.showGroup(detail,{key:'group:7'});};
      window.fixture={sidebar,parent,updates,detail,muted:false,push(next){sidebar.showGroup(next,{key:'group:'+next.id});}};
      document.body.dataset.ready='true';
    </script></body></html>` });
  });
  await page.goto('https://group-sidebar.test');
  await expect(page.locator('body')).toHaveAttribute('data-ready', 'true');
  await page.locator('#open').click();
  await expect(page.locator('[data-group-card=members]')).toBeVisible();
  return state;
}

async function settings(page: Page) {
  await page.locator('[data-group-settings-open]').click();
  await expect(page.locator('#social-group-settings')).toBeVisible();
}
async function invite(page: Page) {
  await page.locator('[data-group-invite-open]').click();
  await expect(page.locator('[data-invite-identity="student:3"]')).toBeVisible();
}
async function push(page: Page, group: any) { await page.evaluate(value => (window as any).fixture.push(value), group); }

for (const role of ['owner', 'admin', 'member']) test(`LQ group sidebar separates cards and preserves ${role} permissions`, async ({ page }) => {
  const state = await mount(page, { role });
  await expect(page.locator('[data-group-card]')).toHaveCount(3);
  expect(await page.locator('[data-group-card]').evaluateAll(nodes => nodes.map(node => node.getAttribute('data-group-card')))).toEqual(['info', 'members', 'files']);
  await expect(page.locator('#group-parent input[name=name]')).toHaveCount(0);
  await expect(page.locator('[data-group-invite-open]')).toBeEnabled();
  await settings(page);
  if (role === 'member') {
    await expect(page.locator('#social-group-rename')).toHaveJSProperty('readOnly', true);
    await expect(page.locator('#social-group-announcement')).toHaveValue(originalGroup.announcement);
    await expect(page.getByRole('button', { name: '保存群资料', exact: true })).toBeHidden();
  }
  else await expect(page.locator('#social-group-rename')).toBeEditable();
  await expect(page.locator('[data-group-danger]')).toHaveAccessibleName(role === 'owner' ? '解散群组' : '退出群组');
  await page.keyboard.press('Escape');
  await expect(page.locator('[data-group-settings-open]')).toBeFocused();
  expect(state.errors).toEqual([]);
});

test('LQ group settings retain drafts through dirty cancellation, CAS conflict and explicit rebase', async ({ page }) => {
  const state = await mount(page); await settings(page);
  await page.locator('#social-group-rename').fill('我的群名草稿');
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog', { name: '放弃未保存的设置？' })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.locator('#social-group-settings')).toBeVisible();
  await expect(page.locator('#social-group-rename')).toHaveValue('我的群名草稿');
  state.group.updated_at = 'v2'; state.group.announcement = '其他管理员刚更新的公告';
  await page.getByRole('button', { name: '保存群资料', exact: true }).click();
  await expect(page.getByText('群资料已有新的修改')).toBeVisible();
  expect(state.calls.filter((item: any) => item.method === 'PATCH')[0].body).toEqual({ name: '我的群名草稿', expected_updated_at: 'v1' });
  await page.locator('[data-group-settings-reload]').click();
  await expect(page.locator('#social-group-announcement')).toHaveValue('其他管理员刚更新的公告');
  await expect(page.locator('#social-group-rename')).toHaveValue('我的群名草稿');
  await page.getByRole('button', { name: '保存群资料', exact: true }).click();
  await expect(page.getByText('群资料已保存', { exact: true })).toBeVisible();
  expect(state.calls.filter((item: any) => item.method === 'PATCH').at(-1).body).toEqual({ name: '我的群名草稿', expected_updated_at: 'v2' });
  expect(state.errors).toEqual([]);
});

test('LQ personal alias and nickname keep a failed draft and update only their own fields', async ({ page }) => {
  const state = await mount(page); await settings(page);
  state.failPrivate = 503;
  await page.locator('#social-group-alias').fill('我的实验小队');
  await page.locator('#social-group-nickname').fill('夜航');
  await page.locator('#social-group-muted').check();
  await page.getByRole('button', { name: '保存我的设置', exact: true }).click();
  await expect(page.getByText('未能保存', { exact: true })).toBeVisible();
  await expect(page.locator('#social-group-alias')).toHaveValue('我的实验小队');
  state.failPrivate = 0;
  await page.getByRole('button', { name: '保存我的设置', exact: true }).click();
  await expect(page.getByText('我的设置已保存', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '保存我的设置', exact: true }).click();
  expect(state.calls.filter((item: any) => item.endpoint === '/groups/7/me')).toHaveLength(2);
  await page.keyboard.press('Escape');
  await expect(page.locator('.social-group-card__title')).toHaveText('我的实验小队');
  await expect(page.locator('[data-group-card=info]')).toContainText(`群名：${originalGroup.name}`);
  expect(state.group.name).toBe(originalGroup.name);
  expect(state.calls.filter((item: any) => item.endpoint === '/groups/7/me').at(-1).body).toEqual({ alias: '我的实验小队', nickname: '夜航', muted: true });
  expect(await page.evaluate(() => (window as any).fixture.muted)).toBe(true);
  expect(state.errors).toEqual([]);
});

test('LQ invite lets an ordinary member select existing friends and explains partial success', async ({ page }) => {
  const state = await mount(page, { role: 'member' }); await invite(page);
  await expect(page.locator('[data-invite-identity="student:2"] input')).toBeDisabled();
  await page.locator('[data-invite-identity="student:3"] input').check();
  await page.locator('[data-invite-identity="student:4"] input').check();
  state.inviteResult = { added: ['student:3'], skipped: [{ identity: 'student:4', reason: '对方加入的群聊已达 50 个上限' }] };
  await page.locator('[data-group-invite-submit]').click();
  await expect(page.getByText('已邀请 1 人，部分好友未能加入')).toBeVisible();
  await expect(page.locator('[data-invite-identity="student:3"] input')).toBeDisabled();
  await expect(page.locator('[data-invite-identity="student:4"] input')).toBeChecked();
  await expect(page.locator('.social-group-invite__results')).toContainText('谢明：对方加入的群聊已达 50 个上限');
  state.inviteResult = { added: ['student:4'], skipped: [] };
  await page.locator('[data-group-invite-submit]').click();
  await expect(page.locator('#social-group-invite')).toBeHidden();
  await expect(page.locator('[data-group-invite-open]')).toBeFocused();
  expect(state.calls.filter((item: any) => item.endpoint.endsWith('/members')).at(-1).body).toEqual({ identities: ['student:4'] });
  expect(state.errors).toEqual([]);
});

test('LQ invite retains selection on error and submits only once while pending', async ({ page }) => {
  const state = await mount(page); await invite(page);
  await page.locator('[data-invite-identity="student:3"] input').check();
  state.failInvite = 503;
  await page.locator('[data-group-invite-submit]').click();
  await expect(page.getByText('邀请未完成')).toBeVisible();
  await expect(page.locator('[data-invite-identity="student:3"] input')).toBeChecked();
  let release!: () => void; const pending = new Promise<void>(resolve => { release = resolve; }); let count = 0;
  await page.route('**/api/social/groups/7/members', async route => { count++; await pending; await route.fulfill({ json: { added: ['student:3'], skipped: [] } }); });
  await page.locator('[data-group-invite-submit]').click();
  await expect.poll(() => count).toBe(1);
  await page.locator('.social-group-invite').dispatchEvent('submit');
  await page.keyboard.press('Escape');
  await expect(page.locator('#social-group-invite')).toBeVisible();
  release();
  await expect(page.locator('#social-group-invite')).toBeHidden();
  expect(count).toBe(1); expect(state.errors).toEqual([]);
});

test('LQ invite handles no friends and waits for IME completion before filtering', async ({ page }) => {
  const state = await mount(page); state.friends = [];
  await page.locator('[data-group-invite-open]').click();
  await expect(page.getByText('暂无可邀请的好友')).toBeVisible();
  await expect(page.locator('[data-group-invite-submit]')).toBeDisabled();
  await page.keyboard.press('Escape');
  state.friends = [member, friend, another]; await invite(page);
  const input = page.locator('#social-group-invite-search');
  await input.evaluate((node: HTMLInputElement) => {
    node.dispatchEvent(new CompositionEvent('compositionstart', { bubbles: true })); node.value = '实验';
    node.dispatchEvent(new InputEvent('input', { bubbles: true, isComposing: true }));
  });
  await expect(page.locator('[data-invite-identity]')).toHaveCount(3);
  await input.dispatchEvent('compositionend');
  await expect(page.locator('[data-invite-identity]')).toHaveCount(1);
  await page.locator('[data-invite-identity="student:3"] input').check();
  await input.fill('');
  await expect(page.locator('[data-group-invite-summary]')).toContainText('已选 1 人');
  expect(state.errors).toEqual([]);
});

test('LQ dissolve confirmation cancels safely and submits only after confirmation', async ({ page }) => {
  const state = await mount(page); await settings(page);
  await page.locator('[data-group-danger]').click();
  await page.getByRole('button', { name: '取消', exact: true }).click();
  expect(state.calls.some((item: any) => item.endpoint.endsWith('/dissolve'))).toBe(false);
  await expect(page.locator('[data-group-danger]')).toBeFocused();
  await page.locator('[data-group-danger]').click();
  await page.getByRole('button', { name: '解散', exact: true }).click();
  await expect(page.locator('#social-group-settings')).toBeHidden();
  expect(state.calls.filter((item: any) => item.endpoint.endsWith('/dissolve'))).toHaveLength(1);
  expect(await page.evaluate(() => (window as any).fixture.updates.some((item: any) => item.reason === 'left'))).toBe(true);
  expect(state.errors).toEqual([]);
});

test('LQ role changes preserve dirty public fields and change the dangerous action name', async ({ page }) => {
  const state = await mount(page); await settings(page);
  await page.locator('#social-group-rename').fill('保留输入');
  state.group.my_role = 'member';
  await push(page, state.group);
  await expect(page.locator('#social-group-rename')).toHaveValue('保留输入');
  await expect(page.locator('#social-group-rename')).toHaveJSProperty('readOnly', true);
  await expect(page.getByRole('button', { name: '保存群资料', exact: true })).toBeHidden();
  await expect(page.locator('[data-group-danger]')).toHaveAccessibleName('退出群组');
  expect(state.errors).toEqual([]);
});

test('LQ group files ignore a stale filter and paginate without duplicate media', async ({ page }) => {
  const state = await mount(page);
  let release!: () => void; const pending = new Promise<void>(resolve => { release = resolve; });
  const file = (id: number, filename: string, extra = {}) => ({ id, filename, url: `/api/social/media/${id}`, server_ready: true, size: 4096, sender_name: '林晚舟', ...extra });
  await page.route('**/api/social/groups/7/files?*', async route => {
    const url = new URL(route.request().url());
    if (url.searchParams.get('kind') === 'image') { await pending; return route.fulfill({ json: { items: [file(9, '旧图片.png')], has_more: false } }); }
    const append = url.searchParams.has('before_id');
    return route.fulfill({ json: append ? { items: [file(2, '过期文件.txt', { expired: true }), file(1, '最早文件.txt')], has_more: false }
      : { items: [file(3, '当前文件.txt'), file(2, '过期文件.txt', { expired: true })], has_more: true } });
  });
  await page.locator('[data-file-kind=image]').click();
  await page.locator('[data-file-kind=file]').click();
  await expect(page.getByRole('link', { name: '当前文件.txt', exact: true })).toBeVisible();
  release();
  await page.locator('[data-group-files-more]').click();
  await expect(page.locator('[data-group-file]')).toHaveCount(3);
  await expect(page.getByRole('link', { name: '旧图片.png', exact: true })).toHaveCount(0);
  await expect(page.getByRole('link', { name: '过期文件.txt', exact: true })).toHaveCount(0);
  await expect(page.getByRole('link', { name: '当前文件.txt', exact: true })).toHaveAttribute('href', '/api/social/media/3?download=true');
  expect(state.errors).toEqual([]);
});

test('LQ switching groups destroys children and late invite data cannot repopulate the new group', async ({ page }) => {
  const state = await mount(page);
  let release!: () => void; const pending = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/api/social/friends', async route => { await pending; await route.fulfill({ json: { items: state.friends } }); });
  await page.locator('[data-group-invite-open]').click();
  const next = { ...state.group, id: 8, conversation_key: 'group:8', name: '另一个群', display_name: '另一个群' };
  await push(page, next); release();
  await expect(page.locator('#social-group-invite')).toBeHidden();
  await expect(page.locator('.social-group-card__title')).toHaveText('另一个群');
  await expect(page.locator('#group-parent')).toBeVisible();
  expect(state.errors).toEqual([]);
});

for (const width of [1440, 360]) test(`LQ group cards and settings fit ${width}px with real content and native touch targets`, async ({ browser }, info) => {
  const height = width === 360 ? 780 : 900;
  const context = await browser.newContext({ viewport: { width, height }, hasTouch: width === 360, isMobile: width === 360, reducedMotion: 'reduce' });
  const page = await context.newPage(); const state = await mount(page, { dark: width === 360 });
  await page.screenshot({ path: info.outputPath('group-cards.png') });
  const trigger = page.locator('[data-group-settings-open]');
  if (width === 360) await trigger.tap(); else await trigger.click();
  await expect(page.locator('#social-group-alias')).toBeEditable();
  expect(await page.locator('#social-group-settings .lq-dialog__surface').evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true);
  const box = await page.locator('#social-group-settings .lq-dialog__surface').boundingBox();
  expect(box!.x).toBeGreaterThanOrEqual(0); expect(box!.x + box!.width).toBeLessThanOrEqual(width);
  expect(await page.locator('[data-group-card]').evaluateAll(nodes => nodes.map(node => getComputedStyle(node).backdropFilter))).toEqual(['none', 'none', 'none']);
  await page.screenshot({ path: info.outputPath('group-settings.png') });
  if (width === 360) {
    const danger = page.locator('[data-group-danger]');
    await danger.scrollIntoViewIfNeeded();
    await page.locator('#social-group-settings .lq-dialog__body').evaluate(node => { node.scrollTop = node.scrollHeight; });
    const bounds = await danger.boundingBox();
    expect(bounds!.height).toBeGreaterThanOrEqual(44);
    expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(height);
    await page.screenshot({ path: info.outputPath('group-settings-bottom.png') });
    await danger.tap();
    await expect(page.getByRole('dialog', { name: '解散群组', exact: true })).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(danger).toBeFocused();
  }
  await page.keyboard.press('Escape');
  await expect(trigger).toBeFocused();
  await invite(page);
  await page.screenshot({ path: info.outputPath('group-invite.png') });
  await info.attach('resource-graph', { body: manifest.revision, contentType: 'text/plain' });
  expect(state.errors).toEqual([]); await context.close();
});
