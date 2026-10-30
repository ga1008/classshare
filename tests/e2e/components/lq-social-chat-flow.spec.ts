import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

// Isolated synthetic peers only. Formal acceptance always uses the immutable
// production graph; source mode is an explicit local development convenience.
const manifest = JSON.parse(fs.readFileSync('static/assets/manifest.json', 'utf8'));
const source = process.env.SOCIAL_SOURCE_FIXTURE === '1';
const asset = (name: string) => `/static/${source && name !== 'css/tailwind-app.css' ? name : manifest.entries[name]}`;
const DM = 'dm:student:1|student:2';
const OTHER = 'dm:student:1|student:3';
const out = '.codex-temp/social-chat-flow';
const avatar = '/api/profile/avatar?role=student&user_id=1';
const message = (id: number, key = DM, mine = id % 2 === 0) => ({ id, conversation_key: key,
    sender: { identity: mine ? 'student:1' : 'student:2', name: mine ? '我' : '林晚舟', avatar_url: avatar },
    body: `第 ${id} 条消息，交流学习中的想法。`, body_format: 'text', message_type: 'text', media: [],
    created_at: `2026-10-28T01:${String(Math.floor(id / 60) % 60).padStart(2, '0')}:${String(id % 60).padStart(2, '0')}Z` });

async function mount(page: Page, options: { mobile?: boolean; empty?: boolean; count?: number } = {}) {
    if (options.mobile) await page.setViewportSize({ width: 360, height: 780 });
    const friends = Array.from({ length: options.count || 2 }, (_, index) => ({ identity: `student:${index + 2}`, name: index ? `合成好友${index + 2}` : '林晚舟',
        conversation_key: `dm:student:1|student:${index + 2}`, avatar_url: avatar, online: true, relation: 'friend' }));
    const state: any = { calls: [], friends: options.empty ? [] : friends, messages: Array.from({ length: 50 }, (_, i) => message(i + 1)),
        pageGate: null, pageStarted: false, listGate: null, listStarted: false, friendsGate: null, friendsStarted: false, groupStatus: 200, more: false, older: [],
        group: { id: 7, name: '星光学习小组', display_name: '我的学习搭档', me: { alias: '我的学习搭档', nickname: '', muted: false },
            my_role: 'owner', conversation_key: 'group:7', member_count: 2, online_count: 2, updated_at: '2026-10-28T01:00:00Z',
            members: [{ identity: 'student:1', name: '我', group_role: 'owner' }, { ...friends[0], group_role: 'member' }] } };
    await page.addInitScript(() => {
        class Socket extends EventTarget { static OPEN = 1; readyState = 1;
            constructor() { super(); queueMicrotask(() => this.dispatchEvent(new Event('open'))); }
            send() {} close() { this.readyState = 3; }
        }
        (window as any).WebSocket = Socket;
    });
    await page.route('**/*', async route => {
        const url = new URL(route.request().url());
        if (url.origin !== 'https://chat-flow.test') return route.abort();
        if (url.pathname.startsWith('/static/')) {
            const file = path.resolve(`.${url.pathname}`);
            if (!file.startsWith(path.resolve('static') + path.sep) || !fs.existsSync(file)) return route.abort();
            return route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : file.endsWith('.svg') ? 'image/svg+xml' : 'text/javascript', body: fs.readFileSync(file) });
        }
        if (url.pathname === '/api/profile/avatar') return route.fulfill({ contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40"><circle cx="20" cy="20" r="20" fill="teal"/></svg>' });
        if (url.pathname.startsWith('/api/')) {
            const api = decodeURIComponent(url.pathname.replace('/api/social', ''));
            state.calls.push({ api, method: route.request().method() });
            const json = (value: any, status = 200) => route.fulfill({ status, json: value });
            if (api === '/conversations') {
                state.listStarted = true;
                if (state.listGate) await state.listGate;
                return json({ items: options.empty ? [] : [...state.friends.map((friend: any) => ({ ...friend, type: 'dm', peer_identity: friend.identity, title: friend.name, unread: 0,
                    last_message: { preview: '聊聊学习吧', at: '2026-10-28T01:00:00Z' } })),
                    { conversation_key: 'group:7', type: 'group', title: state.group.display_name, group_name: state.group.name, group_id: 7, unread: 0 }] });
            }
            if (api === '/friends') {
                state.friendsStarted = true;
                const snapshot = JSON.parse(JSON.stringify(state.friends));
                if (state.friendsGate) await state.friendsGate;
                return json({ items: snapshot });
            }
            if (api === '/friends/requests') return json({ incoming: [], outgoing: [] });
            if (api === '/recommendations') return json({ complement: [], similar: [] });
            if (api === '/scope') return json({ can_add: true, tier: 4, tier_name: '灵台', ladder: [] });
            if (api === '/groups') return json({ items: [state.group] });
            if (api === '/groups/7') return json(state.groupStatus === 200 ? state.group : { detail: '合成读取失败' }, state.groupStatus);
            if (api === '/groups/7/files') return json({ items: [] });
            if (api.endsWith('/profile')) return json(friends[0]);
            if (api.endsWith('/messages')) {
                state.pageStarted = true;
                const key = api.slice('/conversations/'.length, -'/messages'.length);
                const snapshot = JSON.parse(JSON.stringify(url.searchParams.has('before_id') ? state.older : state.messages.map((item: any) => ({ ...item, conversation_key: key }))));
                if (state.pageGate) await state.pageGate;
                return json({ items: snapshot, has_more: url.searchParams.has('before_id') ? false : state.more, muted: false, peer_read_message_id: 50, mention_quota: { limit: 3, remaining: 3, used: 0 } });
            }
            if (api.endsWith('/read')) return json({ ok: true });
            if (api === '/quota') return json({ daily_limit: 200000000, used_today: 0, file_limit: 100000000 });
            return json({});
        }
        return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh-CN" data-theme="lanshare" data-appearance="${options.mobile ? 'dark' : 'light'}" data-lq-scene-tone="${options.mobile ? 'dark' : 'light'}" data-lq-motion="off"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="${asset('css/tailwind-app.css')}"></head><body><main><h1>合成聊天验收</h1><button class="lq-btn lq-btn--soft" data-social-entry data-social-me="student:1" data-social-css="${asset('css/lq/pages/social.css')}">好友</button></main><script type="module">import{getSocialClient}from'${asset('js/social/social_client.js')}';window.client=getSocialClient();document.body.dataset.ready='true';</script></body></html>` });
    });
    await page.goto('https://chat-flow.test/');
    await expect(page.locator('body')).toHaveAttribute('data-ready', 'true');
    return state;
}

const open = async (page: Page, key: string | null = null) => { await page.evaluate(key => (window as any).client.openChat(key, document.querySelector('[data-social-entry]')), key); };
const close = async (page: Page) => { await page.locator('#social-chat [data-lq-dialog-close]').click(); await expect(page.locator('#social-chat')).toBeHidden(); };
const push = async (page: Page, frame: any) => { await page.evaluate(frame => (window as any).client.onFrame(JSON.stringify(frame)), frame); };

test('LQ topbar opens chat; bottom friend management is a child and returns focus', async ({ page }) => {
    await mount(page);
    await page.locator('[data-social-entry]').click();
    await expect(page.locator('#social-chat')).toBeVisible();
    await expect(page.locator('#social-friends')).toHaveCount(0);
    await expect(page.locator('[data-message-id="50"]')).toBeVisible();
    const manage = page.locator('[data-social-manage-friends]');
    const list = await page.locator('.social-chat__list').boundingBox();
    const button = await manage.boundingBox();
    expect(button!.y).toBeGreaterThan(list!.y + list!.height / 2);
    expect(button!.height).toBeGreaterThanOrEqual(44);
    await manage.click();
    await expect(page.locator('#social-friends')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.locator('#social-friends')).toBeHidden();
    await expect(manage).toBeFocused();
    await expect(page.locator('#social-chat')).toBeVisible();
    await manage.click();
    await page.locator('[data-social-friend="student:3"]').click();
    await expect(page.locator('#social-friends')).toBeHidden();
    await expect(page.locator(`[data-conv="${OTHER}"]`)).toHaveAttribute('aria-current', 'true');
    await expect(page.locator('#social-chat')).toHaveCount(1);
    await expect(page.locator('#social-chat')).toBeVisible();
    await close(page);
    await expect(page.locator('[data-social-entry]')).toBeFocused();
});

for (const mobile of [false, true]) test(`LQ own and incoming avatars retain gutters and connected grouping (${mobile ? '360 dark' : 'desktop light'})`, async ({ page }) => {
    const state = await mount(page, { mobile });
    state.messages = [message(1), message(2, DM, false), message(3, DM, true), message(4, DM, true)];
    await open(page);
    await expect(page.locator('.social-msg--out .lq-avatar')).toHaveCount(1);
    await expect(page.locator('.social-msg--in .lq-avatar')).toHaveCount(1);
    await expect(page.locator('.social-msg.is-connected')).toHaveCount(2);
    const geometry = await page.locator('.social-chat__messages').evaluate(node => {
        const rect = node.getBoundingClientRect();
        return [...node.querySelectorAll('.social-msg__avatar .lq-avatar, .lq-bubble')].map(item => { const r = item.getBoundingClientRect(); return { left: r.left - rect.left, right: rect.right - r.right, width: r.width }; });
    });
    for (const box of geometry) { expect(box.left).toBeGreaterThanOrEqual(7); expect(box.right).toBeGreaterThanOrEqual(7); expect(box.width).toBeGreaterThan(0); }
    expect(await page.locator('#social-chat .lq-dialog__surface').evaluate(node => node.scrollWidth <= node.clientWidth + 1)).toBe(true);
    await page.screenshot({ path: `${out}/avatars-${mobile ? 'mobile-dark' : 'desktop-light'}.png` });
    if (mobile) { await page.locator('[data-chat-toggle=list]').click(); await expect(page.locator('[data-social-manage-friends]')).toBeVisible(); }
});

test('LQ warm reopen preserves selected conversation, message nodes, draft and scroll without core refetch', async ({ page }) => {
    const state = await mount(page, { count: 200 });
    await open(page, OTHER);
    await page.locator('[data-lq-composer-input]').fill('尚未发送的想法');
    await page.evaluate(() => {
        (window as any).savedMessage = document.querySelector('[data-message-id="20"]');
        (window as any).savedRow = document.querySelector('[data-conv="dm:student:1|student:3"]');
        document.querySelector('.social-chat__messages')!.scrollTop = 100;
    });
    const before = state.calls.filter((call: any) => call.api.endsWith('/messages') || call.api === '/friends').length;
    await close(page); await open(page);
    await expect(page.locator('[data-lq-composer-input]')).toHaveValue('尚未发送的想法');
    await expect(page.locator(`[data-conv="${OTHER}"]`)).toHaveAttribute('aria-current', 'true');
    expect(await page.evaluate(() => (window as any).savedMessage === document.querySelector('[data-message-id="20"]') && (window as any).savedRow === document.querySelector('[data-conv="dm:student:1|student:3"]'))).toBe(true);
    expect(await page.locator('.social-chat__messages').evaluate(node => node.scrollTop)).toBe(100);
    expect(state.calls.filter((call: any) => call.api.endsWith('/messages') || call.api === '/friends').length).toBe(before);
    await push(page, { type: 'presence', identity: 'student:3', online: false });
    expect(await page.evaluate(() => (window as any).savedRow === document.querySelector('[data-conv="dm:student:1|student:3"]'))).toBe(true);
});

test('presence is an avatar ring, never a dot over the cultivation emblem', async ({ page }, testInfo) => {
    await mount(page);
    await open(page);
    await expect(page.locator('.social-avatar__dot')).toHaveCount(0);
    const avatar = page.locator(`[data-conv="${DM}"] .social-avatar`);
    const face = avatar.locator('.lq-avatar');
    await expect(avatar).toHaveAttribute('data-online', 'true');
    expect(await face.evaluate(el => getComputedStyle(el).boxShadow)).toContain('rgb');
    await page.screenshot({ path: testInfo.outputPath('presence-ring-online.png') });
    await push(page, { type: 'presence', identity: 'student:2', online: false });
    await expect(avatar).toHaveAttribute('data-online', 'false');
    await expect.poll(() => face.evaluate(el => getComputedStyle(el).boxShadow)).toBe('none');
    // Offline conversations match the group roster: desaturated and dimmed.
    await expect.poll(() => face.evaluate(el => getComputedStyle(el).filter)).toContain('grayscale');
    await page.screenshot({ path: testInfo.outputPath('presence-ring-offline.png') });
});

test('a 200-friend presence burst repaints the list once, not per frame event', async ({ page }) => {
    await mount(page, { count: 200 });
    await open(page);
    await page.evaluate(() => {
        const list = document.querySelector('.social-convs')!;
        const state: any = (window as any).presenceBurst = { mutations: 0 };
        const observer = new MutationObserver(records => { state.mutations += records.length; });
        observer.observe(list, { subtree: true, attributes: true, childList: true, characterData: true });
        for (let i = 2; i <= 201; i += 1) {
            (window as any).client.onFrame(JSON.stringify({ type: 'presence', identity: `student:${i}`, online: false }));
        }
    });
    await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => setTimeout(resolve, 0))));
    // One coalesced repaint touches one attribute per row (~200). Without
    // coalescing the same burst is 200 full-list repaints (tens of thousands).
    const mutations = await page.evaluate(() => (window as any).presenceBurst.mutations);
    expect(mutations).toBeGreaterThan(0);
    expect(mutations).toBeLessThan(2_000);
});

test('LQ duplicate opening coalesces requests and a late closed response never focuses or marks read', async ({ page }) => {
    const state = await mount(page);
    let release!: () => void;
    state.pageGate = new Promise<void>(resolve => { release = resolve; });
    await page.evaluate(() => { (window as any).client.openChat(null); (window as any).client.openChat(null); });
    await expect.poll(() => state.pageStarted).toBe(true);
    expect(state.calls.filter((call: any) => call.api === '/friends')).toHaveLength(1);
    expect(state.calls.filter((call: any) => call.api.endsWith('/messages'))).toHaveLength(1);
    await close(page);
    release(); state.pageGate = null;
    await expect.poll(() => page.evaluate(() => (window as any).client.activeKey)).toBe(null);
    expect(state.calls.filter((call: any) => call.api.endsWith('/read'))).toHaveLength(0);
    await open(page);
    await expect(page.locator('[data-message-id="50"]')).toBeVisible();
    await expect(page.locator('[data-lq-composer-input]')).toBeEnabled();
});

test('LQ offscreen conversations remain keyboard reachable in a long scrollable list', async ({ page }) => {
    await mount(page, { count: 200 });
    await open(page);
    const last = page.locator('[data-conv="dm:student:1|student:201"]');
    await last.focus();
    await expect(last).toBeFocused();
    await expect(last).toBeInViewport();
    await last.press('Enter');
    await expect(last).toHaveAttribute('aria-current', 'true');
    await expect(page.locator('.social-chat__title')).toHaveText('合成好友201');
    const size = await page.locator('.social-convs').evaluate(node => ({ height: node.clientHeight, total: node.scrollHeight, scroll: node.scrollTop }));
    expect(size.total).toBeGreaterThan(size.height * 10);
    expect(size.scroll).toBeGreaterThan(0);
    await expect(page.locator('[data-social-manage-friends]')).toBeInViewport();
});

test('LQ reconnect refreshes a still-open chat and websocket pushes win over an older HTTP snapshot', async ({ page }) => {
    const state = await mount(page);
    await open(page);
    let release!: () => void;
    state.pageStarted = false;
    state.pageGate = new Promise<void>(resolve => { release = resolve; });
    await page.evaluate(() => { (window as any).client.setStatus('offline'); (window as any).client.setStatus('online'); });
    await expect.poll(() => state.pageStarted).toBe(true);
    await push(page, { type: 'message', message: message(51) });
    await push(page, { type: 'message_recalled', conversation_key: DM, id: 20 });
    release(); state.pageGate = null;
    await expect(page.locator('[data-lq-composer-input]')).toBeEnabled();
    await expect(page.locator('[data-message-id="51"]')).toHaveCount(1);
    await expect(page.locator('[data-message-id="20"]')).toContainText('撤回了一条消息');
    await expect(page.locator('.social-msg')).toHaveCount(51);
});

test('LQ a recall during the very first HTTP page is retained before its row exists', async ({ page }) => {
    const state = await mount(page);
    let release!: () => void;
    state.pageGate = new Promise<void>(resolve => { release = resolve; });
    await page.evaluate(() => { (window as any).client.openChat(null); });
    await expect.poll(() => state.pageStarted).toBe(true);
    await push(page, { type: 'message_recalled', conversation_key: DM, id: 20 });
    release(); state.pageGate = null;
    await expect(page.locator('[data-message-id="20"]')).toContainText('撤回了一条消息');
});

test('LQ reconnect during an in-flight page revalidates missed messages after that snapshot', async ({ page }) => {
    const state = await mount(page);
    let release!: () => void;
    state.pageGate = new Promise<void>(resolve => { release = resolve; });
    await page.evaluate(() => { (window as any).client.openChat(null); });
    await expect.poll(() => state.pageStarted).toBe(true);
    await page.evaluate(() => (window as any).client.setStatus('offline'));
    state.messages.push(message(51)); // No socket delivery while disconnected.
    await page.evaluate(() => (window as any).client.setStatus('online'));
    state.pageGate = null; release();
    await expect(page.locator('[data-message-id="51"]')).toHaveCount(1);
    await expect(page.locator('[data-lq-composer-input]')).toBeEnabled();
});

test('LQ prepend older history preserves existing message elements and scroll anchor', async ({ page }) => {
    const state = await mount(page);
    state.messages = Array.from({ length: 50 }, (_, i) => message(i + 51));
    state.older = Array.from({ length: 50 }, (_, i) => message(i + 1));
    state.more = true;
    await open(page);
    await page.evaluate(() => { (window as any).savedMessage = document.querySelector('[data-message-id="80"]'); document.querySelector('.social-chat__messages')!.scrollTop = 0; });
    await expect(page.locator('[data-message-id="1"]')).toHaveCount(1);
    expect(await page.evaluate(() => (window as any).savedMessage === document.querySelector('[data-message-id="80"]'))).toBe(true);
    expect(await page.locator('.social-chat__messages').evaluate(node => node.scrollTop)).toBeGreaterThan(0);
});

test('LQ hidden pushes refresh on reopen and friend removal disables the retained composer', async ({ page }) => {
    const state = await mount(page);
    await open(page); await close(page);
    state.messages.push(message(51));
    await push(page, { type: 'message', message: message(51) });
    state.friends = state.friends.filter((friend: any) => friend.identity !== 'student:2');
    await push(page, { type: 'friend_removed', identity: 'student:2' });
    await open(page);
    await expect(page.locator('[data-message-id="51"]')).toHaveCount(1);
    await expect(page.locator('[data-lq-composer-input]')).toBeDisabled();
    await expect(page.locator('.social-composer__notice')).toContainText('不是好友');
});

test('LQ friend removal wins over an older in-flight friend-list response', async ({ page }) => {
    const state = await mount(page);
    let release!: () => void;
    state.friendsGate = new Promise<void>(resolve => { release = resolve; });
    await page.evaluate(key => { (window as any).client.openChat(key); }, DM);
    await expect.poll(() => state.friendsStarted).toBe(true);
    state.friends = state.friends.filter((friend: any) => friend.identity !== 'student:2');
    await push(page, { type: 'friend_removed', identity: 'student:2' });
    state.friendsGate = null; release();
    await expect(page.locator('.social-composer__notice')).toContainText('不是好友');
    await expect(page.locator('[data-lq-composer-input]')).toBeDisabled();
});

test('LQ personalized group title updates; transient reads preserve membership and 403 removes it', async ({ page }) => {
    const state = await mount(page);
    await open(page, 'group:7');
    await expect(page.locator('.social-chat__title')).toHaveText('我的学习搭档');
    state.group.display_name = '期末复习伙伴';
    state.group.me.alias = '期末复习伙伴';
    await push(page, { type: 'group_changed', group_id: 7, reason: 'self_settings' });
    await expect(page.locator('.social-chat__title')).toHaveText('期末复习伙伴');
    state.groupStatus = 500;
    await push(page, { type: 'group_changed', group_id: 7 });
    await expect(page.locator('[data-lq-composer-input]')).toBeEnabled();
    state.groupStatus = 403;
    await push(page, { type: 'group_changed', group_id: 7 });
    await expect(page.locator('[data-lq-composer-input]')).toBeDisabled();
    await expect(page.locator('.social-composer__notice')).toContainText('不在该群');
});

test('LQ narrow empty chat exposes its bottom friend-management entry', async ({ page }) => {
    await mount(page, { mobile: true, empty: true });
    await page.locator('[data-social-entry]').click();
    await expect(page.locator('[data-social-manage-friends]')).toBeVisible();
    await page.locator('[data-social-manage-friends]').click();
    await expect(page.locator('#social-friends')).toBeVisible();
    await page.screenshot({ path: `${out}/empty-mobile-friends.png` });
});

test.afterAll(() => {
    fs.mkdirSync(out, { recursive: true });
    fs.writeFileSync(`${out}/graph.json`, JSON.stringify({ revision: manifest.revision, source }, null, 2));
});
