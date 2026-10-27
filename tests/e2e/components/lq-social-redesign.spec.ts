import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

// Browser acceptance uses the immutable production asset graph and synthetic
// API/WebSocket peers. It never opens a local application or business database.
const manifest = JSON.parse(fs.readFileSync('static/assets/manifest.json', 'utf8'));
const asset = (name: string) => `/static/${manifest.entries[name]}`;
const DM = 'dm:student:1|student:2';
const friend = { identity: 'student:2', name: '林晚舟', role_label: '散修', org_label: '散修·无宗门', is_wanderer: true, relation: 'friend', online: true, conversation_key: DM, unread: 0 };
const requestUser = { identity: 'student:3', name: '陈清禾', role_label: '学生', org_label: '合成测试班', relation: 'pending_in' };

async function mount(page: Page, options: { empty?: boolean; mobile?: boolean; dark?: boolean; deepLink?: boolean; member?: boolean } = {}) {
    if (options.mobile) await page.setViewportSize({ width: 360, height: 780 });
    const state: any = {
        friends: options.empty ? [] : [friend], incoming: options.empty ? [] : [{ id: 5, direction: 'incoming', user: requestUser, message: '一起学习吧', created_at: '2026-10-27T11:00:00Z' }],
        outgoing: options.empty ? [] : [{ id: 6, direction: 'outgoing', user: { ...friend, identity: 'student:4', name: '听雨' }, created_at: '2026-10-27T11:00:00Z' }],
        recommendations: options.empty ? { complement: [], similar: [] } : { complement: [], similar: [{ identity: 'student:9', name: '谢明', role_label: '学生', org_label: '合成学院·网络工程测试班', hint: '你们的兴趣和节奏很接近', relation: 'none' }] },
        muted: false, unread: 0, groupUnread: 0, failMention: false, sent: [], calls: [], notification: null,
    };
    const group: any = { id: 7, name: '一起看星星', member_count: 2, online_count: 2, my_role: options.member ? 'member' : 'owner', conversation_key: 'group:7', me: { muted: false, nickname: '' }, members: [
        { identity: 'student:1', name: '我', group_role: options.member ? 'member' : 'owner', online: true },
        { ...friend, group_role: options.member ? 'owner' : 'member' },
    ] };
    await page.addInitScript(() => {
        (window as any).__socialLongTasks = [];
        if (PerformanceObserver.supportedEntryTypes.includes('longtask')) new PerformanceObserver((list) => {
            (window as any).__socialLongTasks.push(...list.getEntries().map((entry) => ({ start: entry.startTime, duration: entry.duration })));
        }).observe({ type: 'longtask', buffered: true });
        class QuietWebSocket extends EventTarget {
            static OPEN = 1;
            readyState = 1;
            constructor(_url: string) { super(); setTimeout(() => this.dispatchEvent(new Event('open')), 0); }
            send(_data: string) {}
            close() { this.readyState = 3; }
        }
        (window as any).WebSocket = QuietWebSocket;
    });
    await page.route('**/*', async (route) => {
        const url = new URL(route.request().url());
        if (url.origin !== 'https://social-ui.test') return route.abort();
        if (url.pathname.startsWith('/static/')) {
            const file = path.resolve(`.${url.pathname}`);
            if (!file.startsWith(path.resolve('static') + path.sep) || !fs.existsSync(file)) return route.abort();
            return route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : file.endsWith('.svg') ? 'image/svg+xml' : 'text/javascript', body: fs.readFileSync(file) });
        }
        if (url.pathname.startsWith('/api/')) {
            const api = decodeURIComponent(url.pathname.replace('/api/social', ''));
            const method = route.request().method();
            const body = route.request().postData() ? route.request().postDataJSON() : {};
            state.calls.push({ api, method, body });
            const json = (value: any, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
            if (api === '/friends') return json({ items: state.friends, counts: { pending_in: state.incoming.length } });
            if (api === '/friends/requests' && method === 'GET') return json({ incoming: state.incoming, outgoing: state.outgoing });
            if (api === '/friends/requests/6' && method === 'DELETE') { state.outgoing = []; return json({ ok: true }); }
            if (api === '/friends/requests/5/accept') { state.incoming = []; return json({ status: 'accepted' }); }
            if (api === '/friends/requests' && method === 'POST') return json({ status: 'pending', request_id: 12 });
            if (api === '/recommendations') return json(state.recommendations);
            if (api === '/scope') return json({ can_add: true, tier: 4, tier_name: '灵台', ladder: [], ai_search: false });
            if (api === '/users/search') return json({ items: [{ ...friend, identity: 'student:4', name: url.searchParams.get('q'), relation: 'none' }] });
            if (api === '/blocks') return json({ items: [] });
            if (api.endsWith('/profile')) return json({ ...friend, recent: [] });
            if (api === '/groups') return json({ items: options.empty ? [] : [group] });
            if (api === '/groups/7') return json({ ...group, me: { ...group.me, muted: state.muted } });
            if (api === '/groups/7/files') return json({ items: [] });
            if (api === '/groups/7/me') { state.muted = body.muted; return json({ ok: true }); }
            if (api === '/conversations') return json({ items: options.empty ? [] : [
                { conversation_key: DM, type: 'dm', title: friend.name, peer_identity: friend.identity, muted: state.muted, unread: state.unread },
                { conversation_key: 'group:7', type: 'group', title: group.name, group_id: 7, muted: state.muted, unread: state.groupUnread },
            ] });
            if (api.endsWith('/preferences')) { if (method === 'PATCH') state.muted = body.muted; return json({ conversation_key: DM, muted: state.muted }); }
            if (api.endsWith('/read')) { state.unread = 0; return json({ last_read_message_id: body.last_message_id }); }
            if (api.endsWith('/nudge')) return json({ ok: true });
            if (api.endsWith('/messages')) {
                if (method === 'GET') return json({ items: [], has_more: false, muted: state.muted, mention_quota: { limit: 3, used: 1, remaining: 2 } });
                if (state.failMention && body.mentions?.length) return json({ detail: '今天的 @ 邮件提醒次数已用完，可移除 @ 后发送普通消息。' }, 429);
                state.sent.push(body);
                return json({ id: 100 + state.sent.length, sender: { identity: 'student:1', name: '我' }, conversation_key: api.slice('/conversations/'.length, -'/messages'.length), body: body.body, body_format: 'text', message_type: 'text', media: [], created_at: '2026-10-27T11:00:00Z', mention_quota: { limit: 3, used: 2, remaining: 1 } });
            }
            if (api === '/quota') return json({ daily_limit: 200000000, used_today: 0, file_limit: 100000000 });
            if (api === '/mention-quota') return json({ limit: 3, used: state.failMention ? 3 : 1, remaining: state.failMention ? 0 : 2 });
            if (url.pathname === '/api/message-center/summary') return json({ summary: { unread_total: state.unread + state.groupUnread }, latest_unread: state.notification });
            if (url.pathname === '/api/blog/topbar-summary') return json({ today_new_count: 0 });
            return json({ detail: `Unmocked ${method} ${api}` }, 404);
        }
        return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh-CN" data-theme="lanshare" data-ui-palette="teal" data-appearance="${options.dark ? 'dark' : 'light'}" data-lq-scene-tone="${options.dark ? 'dark' : 'light'}" data-lq-motion="off"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="${asset('css/tailwind-app.css')}"></head><body>
          <main style="min-height:100vh;padding:24px"><h1>合成社交验收</h1><button type="button" class="lq-btn lq-btn--soft relative" data-social-entry data-social-me="student:1" data-social-css="${asset('css/lq/pages/social.css')}">好友<span class="lq-badge absolute -top-1 -right-1" data-tone="danger" data-social-unread hidden>0</span></button></main>
          <span data-message-center-bell-shell data-lq-toast="true"><button data-message-center-bell>消息中心<span data-message-center-bell-count hidden></span></button></span>
          <script type="module" src="${asset('js/message_center_bell.js')}"></script>
          <script type="module">import {getSocialClient} from '${asset('js/social/social_client.js')}';window.socialClient=getSocialClient();document.body.dataset.ready='true';</script>
          </body></html>` });
    });
    await page.goto(`https://social-ui.test/${options.deepLink ? `profile?social_conversation=${encodeURIComponent(DM)}` : ''}`);
    await expect(page.locator('body')).toHaveAttribute('data-ready', 'true');
    return state;
}

test('LQ friends groups hide empty sections and nested new-friend window returns focus', async ({ page }) => {
    const state = await mount(page);
    await page.locator('[data-social-entry]').click();
    await expect(page.locator('#social-friends [role=tab]')).toHaveCount(2);
    await expect(page.locator('#social-friends-accepted')).toHaveAttribute('open', '');
    await expect(page.getByRole('heading', { name: '互补搭档' })).toHaveCount(0);
    await expect(page.locator('#social-friends-incoming')).toContainText('陈清禾');
    await page.locator('[data-social-open-add]').click();
    await expect(page.locator('#social-add')).toBeVisible();
    await page.locator('#social-user-search').fill('新朋友甲');
    await expect(page.locator('.social-results')).toContainText('新朋友甲');
    await page.keyboard.press('Escape');
    await expect(page.locator('#social-add')).toBeHidden();
    await expect(page.locator('[data-social-open-add]')).toBeFocused();
    await page.locator('[data-social-open-add]').click();
    await expect(page.locator('#social-user-search')).toHaveValue('新朋友甲');
    await page.locator('[data-act=withdraw]').click();
    await expect.poll(() => state.outgoing.length).toBe(0);
    await expect(page.locator('.social-add__outgoing')).toBeHidden();
});

test('LQ empty friends page keeps only a single invitation and the two tabs', async ({ page }) => {
    await mount(page, { empty: true });
    await page.locator('[data-social-entry]').click();
    await expect(page.getByText('从一个招呼开始')).toBeVisible();
    await expect(page.locator('#social-friends details')).toHaveCount(0);
    await expect(page.locator('#social-friends [role=tab]')).toHaveCount(2);
});

test('LQ friends and nested search fit a 360px viewport in dark appearance', async ({ page }) => {
    await mount(page, { mobile: true, dark: true });
    await page.locator('[data-social-entry]').click();
    await expect(page.locator('#social-friends-accepted')).toBeVisible();
    expect(await page.locator('#social-friends .lq-dialog__surface').evaluate((node) => node.scrollWidth <= node.clientWidth + 1)).toBe(true);
    await page.screenshot({ path: '.codex-temp/social-redesign/friends-mobile-dark.png' });
    await page.locator('[data-social-open-add]').click();
    await expect(page.locator('#social-user-search')).toBeVisible();
    expect(await page.locator('#social-add .lq-dialog__surface').evaluate((node) => node.scrollWidth <= node.clientWidth + 1)).toBe(true);
    await page.screenshot({ path: '.codex-temp/social-redesign/new-friend-mobile-dark.png' });
    await page.keyboard.press('Escape');
    await expect(page.locator('[data-social-open-add]')).toBeFocused();
});

for (const mobile of [false, true]) test(`LQ group member menu keeps actions in a portal (${mobile ? '360px dark' : 'desktop light'})`, async ({ page: desktopPage, browser }) => {
    const touchContext = mobile ? await browser.newContext({ viewport: { width: 360, height: 780 }, hasTouch: true, isMobile: true }) : null;
    const page = touchContext ? await touchContext.newPage() : desktopPage;
    const state = await mount(page, { mobile, dark: mobile });
    await page.evaluate(() => (window as any).socialClient.openChat('group:7'));
    await expect(page.locator('.social-composer__notice')).toBeHidden();
    if (mobile) await page.locator('[data-chat-toggle=side]').click();
    const trigger = page.locator('[data-member-menu="student:2"]');
    await expect(trigger).toBeVisible();
    await expect(page.getByRole('menuitem', { name: '转让群主' })).toBeHidden();
    if (mobile) await trigger.tap(); else await trigger.click();
    await expect(page.getByRole('menuitem', { name: '转让群主' })).toBeVisible();
    const menu = page.getByRole('menu', { name: '林晚舟的操作' });
    const box = await menu.boundingBox();
    expect(box!.x).toBeGreaterThanOrEqual(0);
    expect(box!.x + box!.width).toBeLessThanOrEqual((mobile ? 360 : 1440) + 1);
    await page.screenshot({ path: `.codex-temp/social-redesign/group-menu-${mobile ? 'mobile-dark' : 'desktop-light'}.png` });
    const metrics = await page.evaluate(() => ({
        backdropHosts: [...document.querySelectorAll<HTMLElement>('*')].filter((node) => {
            const box = node.getBoundingClientRect(); const style = getComputedStyle(node);
            return box.width > 0 && box.height > 0 && style.visibility !== 'hidden' && style.backdropFilter !== 'none';
        }).map((node) => node.className),
        longTasks: (window as any).__socialLongTasks,
        resourceRequests: performance.getEntriesByType('resource').length,
    }));
    fs.mkdirSync('.codex-temp/social-redesign', { recursive: true });
    fs.writeFileSync(`.codex-temp/social-redesign/metrics-${mobile ? 'mobile-dark' : 'desktop-light'}.json`, JSON.stringify({ ...metrics, apiRequests: state.calls.length, assetRevision: manifest.revision }, null, 2));
    await page.keyboard.press('Escape');
    await expect(trigger).toBeFocused();
    await trigger.click();
    await page.getByRole('menuitem', { name: '拍一拍' }).click();
    await expect.poll(() => state.calls.filter((call: any) => call.api.endsWith('/nudge')).length).toBe(1);
    await expect(menu).toBeHidden();
    await touchContext?.close();
});

test('LQ ordinary member cannot see management commands', async ({ page }) => {
    await mount(page, { member: true });
    await page.evaluate(() => (window as any).socialClient.openChat('group:7'));
    await page.locator('[data-member-menu="student:2"]').click();
    await expect(page.getByRole('menuitem')).toHaveCount(1);
    await expect(page.getByRole('menuitem')).toHaveText('拍一拍');
});

test('LQ DM @ survives a draft switch and quota rejection retains the body', async ({ page }) => {
    const state = await mount(page, { deepLink: true });
    const input = page.locator('[data-lq-composer-input]');
    await expect(input).toBeEnabled();
    await input.fill('@');
    await page.locator('[data-mention="student:2"]').click();
    await input.press('End');
    await input.pressSequentially('今晚一起看星星吗');
    await page.locator('[data-conv="group:7"]').click();
    await page.locator(`[data-conv="${DM}"]`).click();
    await expect(input).toHaveValue('@林晚舟 今晚一起看星星吗');
    state.failMention = true;
    await input.press('Enter');
    await expect(page.locator('.social-composer__notice')).toContainText('次数已用完');
    await expect(input).toHaveValue('@林晚舟 今晚一起看星星吗');
    expect(state.sent).toHaveLength(0);
    await input.fill('今晚一起看星星吗');
    await input.press('Enter');
    await expect.poll(() => state.sent.length).toBe(1);
    expect(state.sent[0].mentions).toBeUndefined();
    await expect(input).toHaveValue('');
});

test('LQ mute retains badges, and another open conversation still receives one popup', async ({ page }) => {
    const state = await mount(page);
    await page.evaluate((key) => (window as any).socialClient.openChat(key), DM);
    await page.locator('#social-dm-muted').check();
    await expect.poll(() => state.muted).toBe(true);
    await page.locator('#social-chat [data-lq-dialog-close]').click();
    for (const button of await page.locator('.lq-toast').getByRole('button', { name: '关闭通知' }).all()) await button.click();
    state.unread = 1;
    state.notification = { id: 1, title: '私聊新消息', category: 'social', metadata: { event_type: 'social_message' } };
    const incoming = { id: 300, conversation_key: DM, sender: { identity: 'student:2', name: '林晚舟' }, body: '静默消息', message_type: 'text', muted: true };
    await page.evaluate((message) => (window as any).socialClient.onFrame(JSON.stringify({ type: 'message', message })), incoming);
    await expect(page.locator('[data-social-unread]')).toHaveText('2'); // one message + one friend request
    await page.evaluate(() => (window as any).refreshMessageCenterBell({ allowPopup: true }));
    await expect(page.locator('.lq-toast')).toHaveCount(0);
    state.muted = false;
    await page.evaluate((key) => (window as any).socialClient.openChat(key), DM);
    state.groupUnread = 1;
    await page.evaluate(() => (window as any).socialClient.onFrame(JSON.stringify({ type: 'message', message: { id: 301, conversation_key: 'group:7', sender: { identity: 'student:2', name: '林晚舟' }, body: '另一会话的新消息', message_type: 'text', muted: false } })));
    await expect(page.locator('.lq-toast')).toHaveCount(1);
    await expect(page.locator('.lq-toast')).toContainText('另一会话的新消息');
    await page.evaluate(() => (window as any).socialClient.onFrame(JSON.stringify({ type: 'message', message: { id: 301, conversation_key: 'group:7', sender: { identity: 'student:2', name: '林晚舟' }, body: '另一会话的新消息', message_type: 'text', muted: false } })));
    await expect(page.locator('.lq-toast')).toHaveCount(1);
});
