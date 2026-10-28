import { test, expect, type Page, type Response } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';

// A real chat controller and immutable production assets, with synthetic API
// peers only. This deliberately does not connect to an application database.
const graph = JSON.parse(fs.readFileSync('static/assets/manifest.json', 'utf8'));
const asset = (name: string) => `/static/${graph.entries[name]}`;
const ME = 'student:1';
const PEER = 'student:2';
const OTHER = 'student:3';
const dm = (identity = PEER) => `dm:${[ME, identity].sort().join('|')}`;
const clone = (value: any) => structuredClone(value);
const point = (angle: number, radius: number) => ({ x: 120 + Math.sin(angle) * radius, y: 120 - Math.cos(angle) * radius });
const axes = ['研读', '练习', '探索', '互助', '表达', '韧性'].map((label, index) => {
  const angle = index * Math.PI / 3, vertex = point(angle, 56), text = point(angle, 103);
  return { key: `axis${index}`, label, score: 70, average: 60, delta: 10,
    vertex_x: vertex.x, vertex_y: vertex.y, label_x: text.x, label_y: text.y,
    anchor: index === 0 || index === 3 ? 'middle' : index < 3 ? 'start' : 'end' };
});
const radar = { available: true, center: 120, axes, has_average: true, peer_label: '同课堂', peer_count: 26, course_count: 2,
  points: axes.map(axis => `${axis.vertex_x},${axis.vertex_y}`).join(' '), average_points: axes.map((_, i) => { const p = point(i * Math.PI / 3, 48); return `${p.x},${p.y}`; }).join(' '),
  rings: [25, 50, 75, 100].map(ratio => ({ ratio, points: axes.map((_, i) => { const p = point(i * Math.PI / 3, ratio * .8); return `${p.x},${p.y}`; }).join(' ') })),
  spokes: axes.map((_, i) => point(i * Math.PI / 3, 80)) };

function person(identity: string, name: string, options: any = {}) {
  const role = identity.split(':')[0], wanderer = Boolean(options.wanderer);
  const profile: any = { identity, name, role, role_label: role === 'teacher' ? '教师' : '学生', avatar_url: '/fixture-avatar.svg',
    relation: options.relation || 'friend', org_label: role === 'teacher' ? '数字科技学院 · 网络工程系' : '数字科技学院 · 网络工程 · 软工2402班',
    online: true, mood: '平静', remark: '', is_wanderer: wanderer, recent: [{ title: '不应占据主侧栏的最新动态', url: '/blog/synthetic', at: '2026-10-28T01:00:00Z' }],
    cultivation: options.empty || role === 'teacher' ? null : { tier_name: '灵台', score: 86.2, emblem: '/fixture-emblem.svg' }, private_message_url: '/messages?student=2', homepage_url: wanderer ? '/wanderers/synthetic' : null };
  const card: any = { identity, name, role, role_label: profile.role_label, relation: profile.relation, is_wanderer: wanderer,
    avatar_url: profile.avatar_url, org: { college: '数字科技学院', major: '网络工程', class_name: '软工2402班' }, online: true, mood: '平静',
    cultivation_hidden: Boolean(options.hidden), tier_badge: options.empty || options.hidden || role === 'teacher' ? null : { name: '灵台', score: 86.2, emblem_url: '/fixture-emblem.svg' },
    courses: [], radar: options.empty || options.hidden || role === 'teacher' || wanderer ? null : clone(radar),
    interests: wanderer && !options.empty ? ['星空摄影', '推理小说', '古典音乐', '夜间散步', '咖啡', '算法'] : [], friend_extras: null };
  return { profile, card };
}

type MountOptions = { identity?: string; dark?: boolean; glassOff?: boolean; empty?: boolean; hidden?: boolean; wanderer?: boolean; relation?: string; wait?: boolean; gateCard?: Promise<void>; gateProfile?: Promise<void>; failCard?: number };

async function mount(page: Page, options: MountOptions = {}) {
  for (const source of ['js/social/chat_sidebar.js', 'js/social/dm_settings.js', 'js/social/chat_modal.js', 'js/social/social_dom.js', 'js/social/social_api.js', 'js/ability_radar.js', 'js/lq/layer.js', 'css/lq/pages/social.css']) {
    expect(createHash('sha256').update(fs.readFileSync(`static/${source}`)).digest('hex'), `stale formal asset: ${source}`).toBe(graph.sourceHashes[source]);
  }
  const identity = options.identity || PEER;
  const primary = person(identity, options.wanderer ? '林晚舟' : identity.startsWith('teacher:') ? '张老师' : '陈清禾', options);
  const secondary = person(OTHER, '谢明');
  const state: any = { identity, peers: { [identity]: primary, [OTHER]: secondary }, calls: [], completed: [], errors: [], unmocked: [], gates: {}, failRemark: 0, failPreferences: 0, failCard: options.failCard || 0, failProfile: 0, preferences: {} };
  if (options.gateCard) state.gates[`/users/${identity}/card`] = options.gateCard;
  if (options.gateProfile) state.gates[`/users/${identity}/profile`] = options.gateProfile;
  await page.addInitScript(() => {
    class Socket extends EventTarget { static OPEN = 1; readyState = 1;
      constructor() { super(); queueMicrotask(() => this.dispatchEvent(new Event('open'))); }
      send() {} close() { this.readyState = 3; }
    }
    (window as any).WebSocket = Socket;
  });
  page.on('pageerror', error => state.errors.push(error.message));
  for (const event of ['requestfinished', 'requestfailed'] as const) page.on(event, request => {
    const url = new URL(request.url());
    if (url.pathname.startsWith('/api/social/')) state.completed.push({ api: decodeURIComponent(url.pathname.slice('/api/social'.length)), event, failure: request.failure()?.errorText });
  });
  const template = fs.readFileSync('templates/partials/ai_workspace_widget.html', 'utf8');
  const buttonStart = template.indexOf('<button '), buttonEnd = template.indexOf('</button>', buttonStart) + 9;
  // Keep the shipped FAB DOM and CSS, including its high stacking position.
  const fab = template.slice(buttonStart, buttonEnd)
    .replace(/{% if ai_widget_deferred[\s\S]*?{% endif %}/g, '')
    .replace(/{%[\s\S]*?%}/g, '').replace(/{{[\s\S]*?}}/g, 'AI 助手与任务中心');
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.origin !== 'https://dm-sidebar.test') return route.abort();
    if (url.pathname.startsWith(`/static/assets/${graph.revision}/`)) {
      const file = path.resolve(`.${url.pathname}`);
      if (!file.startsWith(path.resolve('static/assets', graph.revision) + path.sep) || !fs.existsSync(file)) return route.abort();
      return route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : file.endsWith('.svg') ? 'image/svg+xml' : 'text/javascript', body: fs.readFileSync(file) });
    }
    if (url.pathname.startsWith('/fixture-')) return route.fulfill({ contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 56 56"><circle cx="28" cy="28" r="27" fill="#0f8f83"/><path d="M15 28l9 9 18-20" fill="none" stroke="white" stroke-width="4"/></svg>' });
    if (url.pathname.startsWith('/api/social/')) {
      const api = decodeURIComponent(url.pathname.slice('/api/social'.length)), method = route.request().method();
      const body = route.request().postData() ? route.request().postDataJSON() : {};
      state.calls.push({ api, method, body });
      const json = (value: any, status = 200) => route.fulfill({ status, json: value });
      const snapshot = clone(state.peers);
      if (state.gates[api]) await state.gates[api];
      const match = api.match(/^\/users\/(.+)\/(profile|card)$/);
      if (match) {
        if (match[2] === 'card' && state.failCard) return json({ detail: '资料补充暂时不可用' }, state.failCard);
        if (match[2] === 'profile' && state.failProfile) return json({ detail: '设置资料暂时不可用' }, state.failProfile);
        return json(snapshot[match[1]][match[2]]);
      }
      if (api === '/friends') return json({ items: Object.values(state.peers).filter((item: any) => item.profile.relation === 'friend').map((item: any) => ({ ...item.profile, conversation_key: dm(item.profile.identity) })) });
      if (api === '/conversations') return json({ items: Object.values(state.peers).map((item: any) => ({ conversation_key: dm(item.profile.identity), type: 'dm', peer_identity: item.profile.identity,
        title: item.profile.remark || item.profile.name, muted: Boolean(state.preferences[dm(item.profile.identity)]), unread: 0, last_message: { preview: '一起聊聊吧', at: '2026-10-28T01:00:00Z' } })) });
      if (api === '/friends/requests') return json({ incoming: [], outgoing: [] });
      if (api === '/quota') return json({ daily_limit: 200000000, used_today: 0, file_limit: 100000000 });
      if (api.endsWith('/messages')) return json({ items: [{ id: 1, conversation_key: dm(identity), sender: { identity, name: primary.profile.name, avatar_url: '/fixture-avatar.svg' }, body: '实验先休息一下，晚上再看。', body_format: 'text', message_type: 'text', media: [], created_at: '2026-10-28T01:00:00Z' }], has_more: false,
        muted: Boolean(state.preferences[api.slice('/conversations/'.length, -'/messages'.length)]), mention_quota: { limit: 3, remaining: 3, used: 0 } });
      if (api.endsWith('/read') || api.endsWith('/nudge')) return json({ ok: true });
      if (api.endsWith('/preferences')) {
        if (method === 'PATCH' && state.failPreferences) return json({ detail: '合成提醒设置失败，请重试' }, state.failPreferences);
        const key = api.slice('/conversations/'.length, -'/preferences'.length);
        if (method === 'PATCH') state.preferences[key] = body.muted;
        return json({ conversation_key: key, muted: Boolean(state.preferences[key]) });
      }
      if (api.startsWith('/friends/') && ['PATCH', 'DELETE'].includes(method)) {
        const target = api.slice('/friends/'.length);
        if (method === 'PATCH') {
          if (state.failRemark) return json({ detail: '合成备注保存失败，请重试' }, state.failRemark);
          state.peers[target].profile.remark = body.remark;
          return json({ identity: target, remark: body.remark });
        }
        state.peers[target].profile.relation = state.peers[target].card.relation = 'none';
        return json({ ok: true });
      }
      if (api === '/blocks' && method === 'POST') {
        state.peers[body.identity].profile.relation = state.peers[body.identity].card.relation = 'blocked';
        return json({ ok: true });
      }
      state.unmocked.push(`${method} ${api}`);
      return json({ detail: `Unmocked ${method} ${api}` }, 404);
    }
    return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh-CN" data-theme="lanshare" data-ui-palette="teal" data-appearance="${options.dark ? 'dark' : 'light'}" data-lq-scene-tone="${options.dark ? 'dark' : 'light'}" data-lq-glass="${options.glassOff ? 'off' : 'on'}" data-lq-motion="off"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="${asset('css/tailwind-app.css')}"><link rel="stylesheet" href="${asset('css/ai_workspace.css')}"></head><body class="ls-page"><main><h1>合成好友聊天验收</h1><button data-lq-component="button" class="lq-btn lq-btn--glass" data-social-entry data-social-me="${ME}" data-social-css="${asset('css/lq/pages/social.css')}">好友</button></main>${fab}<script type="module">import {getSocialClient} from '${asset('js/social/social_client.js')}';window.client=getSocialClient();document.body.dataset.ready='true';</script></body></html>` });
  });
  await page.goto('https://dm-sidebar.test');
  await expect(page.locator('body')).toHaveAttribute('data-ready', 'true');
  await page.locator('[data-social-entry]').click();
  await expect(page.locator('#social-chat')).toBeVisible();
  // The actual chat controller intentionally defers the mobile sidebar until
  // its disclosure is opened, so this is a user action rather than a source call.
  if (options.wait !== false) await revealSide(page);
  return state;
}

async function revealSide(page: Page) {
  const toggle = page.locator('[data-chat-toggle=side]');
  if (await toggle.isVisible() && await toggle.getAttribute('aria-expanded') !== 'true') await toggle.click();
  await expect(page.locator('[data-dm-settings-open]')).toBeVisible();
}
async function settings(page: Page) { await revealSide(page); await page.locator('[data-dm-settings-open]').click(); await expect(page.locator('#social-dm-settings')).toBeVisible(); }
async function switchPeer(page: Page) { await page.evaluate(key => (window as any).client.openChat(key), dm(OTHER)); await expect(page.locator('.social-chat__title')).toHaveText('谢明'); }
const writes = (state: any, api: string, method = 'PATCH') => state.calls.filter((call: any) => call.api === api && call.method === method);
function clean(state: any) { expect(state.errors).toEqual([]); expect(state.unmocked).toEqual([]); }
function gate() { let release!: () => void; return { pending: new Promise<void>(resolve => { release = resolve; }), release: () => release() }; }
async function afterResponse(page: Page, pending: Promise<Response>) {
  await (await pending).finished();
  // Exercise the application's response continuation before asserting that a
  // stale result was ignored; response headers alone are not that evidence.
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => resolve())));
}
async function afterRequest(page: Page, state: any, api: string) {
  // A superseded public GET may be canceled before headers. That is also a
  // valid terminal state; waiting exclusively for a response would hang.
  await expect.poll(() => state.completed.filter((item: any) => item.api === api)).not.toEqual([]);
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => resolve())));
  return state.completed.filter((item: any) => item.api === api);
}
async function glassLayers(page: Page) {
  return page.evaluate(() => [...document.querySelectorAll('body *')].flatMap(node => {
    const box = node.getBoundingClientRect(), style = getComputedStyle(node);
    if (!box.width || !box.height || style.visibility === 'hidden' || style.display === 'none') return [];
    const filters = [['element', style.backdropFilter], ...['::before', '::after'].map(part => {
      const pseudo = getComputedStyle(node, part); return [part, ['none', 'normal'].includes(pseudo.content) ? 'none' : pseudo.backdropFilter];
    })];
    return filters.filter(([, value]) => value && value !== 'none').map(([part, filter]) => ({ id: node.id, class: node.getAttribute('class'), part, filter }));
  }));
}

test('LQ DM profile keeps learning cards compact and moves uncommon actions into settings', async ({ page }) => {
  const state = await mount(page); await revealSide(page);
  await expect(page.locator('[data-dm-card]')).toHaveCount(3);
  await expect(page.locator('[data-dm-card=profile]')).toContainText('陈清禾');
  await expect(page.locator('[data-dm-card=cultivation]')).toContainText('灵台');
  await expect(page.locator('[data-dm-card=radar] svg[role=img]')).toHaveAccessibleName(/陈清禾.*能力雷达/);
  await expect(page.locator('.social-chat__side')).not.toContainText('最新动态');
  await expect(page.locator('.social-chat__side input')).toHaveCount(0);
  await expect(page.locator('.social-chat__side')).not.toContainText('删除好友');
  await expect(page.locator('[data-dm-interests]')).toHaveCount(0);
  expect(state.calls.filter((call: any) => call.api === `/users/${PEER}/card`)).toHaveLength(1);
  expect(state.calls.filter((call: any) => call.api === `/users/${PEER}/profile`)).toHaveLength(0);
  await settings(page);
  await expect(page.locator('#social-dm-settings :focus')).toHaveCount(1);
  await expect(page.locator('[data-dm-remove]')).toBeVisible();
  await expect(page.locator('[data-dm-block]')).toBeVisible();
  await expect(page.locator('[data-dm-nudge]')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.locator('[data-dm-settings-open]')).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(page.locator('[data-social-entry]')).toBeFocused();
  clean(state);
});

test('LQ wanderer shows public interests without inventing an empty learning radar', async ({ page }) => {
  const state = await mount(page, { wanderer: true }); await revealSide(page);
  await expect(page.locator('[data-dm-interests]')).toContainText('星空摄影');
  await expect(page.locator('[data-dm-card=radar]')).toHaveCount(0);
  await expect(page.locator('.social-chat__side')).not.toContainText(/暂无|还没有可用|未填写/);
  clean(state);
});

for (const variant of ['teacher', 'hidden', 'empty']) test(`LQ ${variant} data omits unavailable cultivation and radar`, async ({ page }) => {
  const state = await mount(page, { identity: variant === 'teacher' ? 'teacher:2' : PEER, hidden: variant === 'hidden', empty: variant === 'empty' });
  await revealSide(page);
  await expect(page.locator('[data-dm-card]')).toHaveCount(1);
  await expect(page.locator('[data-dm-card=cultivation], [data-dm-card=radar], [data-dm-interests]')).toHaveCount(0);
  await expect(page.locator('.social-chat__side')).not.toContainText(/暂无|还没有可用|未填写|修为 86/);
  if (variant === 'teacher') { await settings(page); await expect(page.locator('[data-dm-block]')).toBeVisible(); }
  clean(state);
});

test('LQ DM settings preserve dirty and failed drafts, save both fields and avoid repeated writes', async ({ page }) => {
  const state = await mount(page); await settings(page);
  await page.locator('#social-remark').fill('实验搭档');
  await page.locator('#social-dm-muted').check();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog', { name: '放弃未保存的设置？', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '取消', exact: true }).click();
  await expect(page.locator('#social-remark')).toHaveValue('实验搭档');
  state.failRemark = 503;
  await page.locator('[data-dm-settings-save]').click();
  await expect(page.locator('[data-dm-settings-status]')).toContainText('合成备注保存失败');
  await expect(page.locator('#social-remark')).toHaveValue('实验搭档');
  await expect(page.locator('#social-dm-muted')).toBeChecked();
  expect(writes(state, `/conversations/${dm()}/preferences`)).toHaveLength(0);
  state.failRemark = 0;
  await page.locator('[data-dm-settings-save]').click();
  await expect.poll(() => state.preferences[dm()]).toBe(true);
  await expect(page.locator('[data-dm-settings-save]')).toBeEnabled();
  await page.locator('[data-dm-settings-save]').click();
  expect(writes(state, `/friends/${PEER}`)).toHaveLength(2);
  expect(writes(state, `/friends/${PEER}`).at(-1).body).toEqual({ remark: '实验搭档' });
  expect(writes(state, `/conversations/${dm()}/preferences`)).toHaveLength(1);
  expect(writes(state, `/conversations/${dm()}/preferences`)[0].body).toEqual({ muted: true });
  expect(await page.evaluate(key => (window as any).client.mutedKeys.has(key), dm())).toBe(true);
  await page.keyboard.press('Escape');
  await expect(page.locator('[data-dm-card=profile]')).toContainText('实验搭档');
  await settings(page); await expect(page.locator('#social-remark')).toHaveValue('实验搭档');
  await expect(page.locator('#social-dm-muted')).toBeChecked();
  clean(state);
});

test('LQ partial success advances only the saved baseline and retries just the failed preference', async ({ page }) => {
  const state = await mount(page); await settings(page);
  await page.locator('#social-remark').fill('夜读搭档'); await page.locator('#social-dm-muted').check();
  state.failPreferences = 503;
  await page.locator('[data-dm-settings-save]').click();
  await expect(page.locator('[data-dm-settings-status]')).toContainText('合成提醒设置失败');
  await expect(page.locator('#social-remark')).toHaveValue('夜读搭档');
  await expect(page.locator('#social-dm-muted')).toBeChecked();
  state.failPreferences = 0;
  await page.locator('[data-dm-settings-save]').click();
  await expect.poll(() => state.preferences[dm()]).toBe(true);
  expect(writes(state, `/friends/${PEER}`)).toHaveLength(1);
  expect(writes(state, `/conversations/${dm()}/preferences`)).toHaveLength(2);
  await page.keyboard.press('Escape'); await expect(page.locator('#social-dm-settings')).toBeHidden();
  clean(state);
});

test('LQ settings profile load can fail and retry without removing public cards', async ({ page }) => {
  const state = await mount(page); state.failProfile = 503; await settings(page);
  await expect(page.locator('[data-dm-settings-status]')).toContainText('设置资料暂时不可用');
  await expect(page.locator('[data-dm-settings-save]')).toBeHidden();
  state.failProfile = 0; await page.locator('[data-dm-settings-retry]').click();
  await expect(page.locator('#social-remark')).toBeEditable();
  await expect(page.locator('[data-dm-settings-retry]')).toBeHidden();
  await page.keyboard.press('Escape'); await expect(page.locator('[data-dm-card]')).toHaveCount(3);
  expect(state.calls.filter((call: any) => call.api === `/users/${PEER}/card`)).toHaveLength(1);
  clean(state);
});

test('LQ failed public card remains retryable without fetching private settings', async ({ page }) => {
  const state = await mount(page, { failCard: 503, wait: false });
  await expect(page.locator('.social-chat__side')).toContainText('资料暂时无法读取');
  state.failCard = 0; await page.locator('.social-chat__side').getByRole('button', { name: '重试', exact: true }).click();
  await revealSide(page); await expect(page.locator('[data-dm-card]')).toHaveCount(3);
  expect(state.calls.filter((call: any) => call.api.endsWith('/profile'))).toHaveLength(0); clean(state);
});

test('LQ canceled profile read cannot overwrite the next settings session', async ({ page }) => {
  const state = await mount(page), delayed = gate(); state.gates[`/users/${PEER}/profile`] = delayed.pending;
  await settings(page); await expect.poll(() => state.calls.filter((call: any) => call.api === `/users/${PEER}/profile`).length).toBe(1);
  await page.keyboard.press('Escape'); await expect(page.locator('#social-dm-settings')).toBeHidden();
  delete state.gates[`/users/${PEER}/profile`]; state.peers[PEER].profile.remark = '服务器最新备注';
  await settings(page); await expect(page.locator('#social-remark')).toHaveValue('服务器最新备注');
  const response = page.waitForResponse(response => decodeURIComponent(new URL(response.url()).pathname).endsWith(`/users/${PEER}/profile`));
  delayed.release(); await afterResponse(page, response);
  await expect(page.locator('#social-remark')).toHaveValue('服务器最新备注');
  await page.keyboard.press('Escape'); await expect(page.locator('[data-dm-settings-open]')).toBeFocused(); clean(state);
});

test('LQ stale profile cannot restore friend privileges after a relationship removal push', async ({ page }) => {
  const state = await mount(page), delayed = gate(); state.gates[`/users/${PEER}/profile`] = delayed.pending;
  await settings(page); await expect.poll(() => state.calls.filter((call: any) => call.api === `/users/${PEER}/profile`).length).toBe(1);
  state.peers[PEER].profile.relation = state.peers[PEER].card.relation = 'none';
  await page.evaluate(identity => (window as any).client.onFrame(JSON.stringify({ type: 'friend_removed', identity })), PEER);
  await expect.poll(() => state.calls.filter((call: any) => call.api === `/users/${PEER}/card`).length).toBe(2);
  await expect(page.locator('[data-lq-composer-input]')).toBeDisabled();
  const response = page.waitForResponse(response => decodeURIComponent(new URL(response.url()).pathname).endsWith(`/users/${PEER}/profile`));
  delayed.release(); await afterResponse(page, response);
  await expect(page.locator('#social-remark')).toBeDisabled();
  for (const selector of ['[data-dm-settings-save]', '[data-dm-remove]', '[data-dm-nudge]']) await expect(page.locator(selector)).toBeHidden();
  expect(writes(state, `/friends/${PEER}`)).toHaveLength(0); clean(state);
});

test('LQ remark-only submit does not overwrite a newer muted preference pushed while saving', async ({ page }) => {
  const state = await mount(page); await settings(page);
  const delayed = gate(); state.gates[`/friends/${PEER}`] = delayed.pending;
  await page.locator('#social-remark').fill('新的备注'); await page.locator('[data-dm-settings-save]').click();
  await expect.poll(() => writes(state, `/friends/${PEER}`).length).toBe(1);
  state.preferences[dm()] = true;
  await page.evaluate(key => (window as any).client.onFrame(JSON.stringify({ type: 'conversation_preferences', conversation_key: key, muted: true })), dm());
  await expect(page.locator('#social-dm-muted')).toBeChecked();
  delayed.release(); await expect(page.locator('[data-dm-settings-status]')).toContainText('设置已保存');
  expect(writes(state, `/conversations/${dm()}/preferences`)).toHaveLength(0);
  await expect(page.locator('#social-dm-muted')).toBeChecked();
  await page.keyboard.press('Escape'); await expect(page.locator('#social-dm-settings')).toBeHidden(); clean(state);
});

test('LQ pending settings save deduplicates submission and prevents accidental dismissal', async ({ page }) => {
  const state = await mount(page); await settings(page);
  const delayed = gate(); state.gates[`/friends/${PEER}`] = delayed.pending;
  await page.locator('#social-remark').fill('一个请求即可'); await page.locator('[data-dm-settings-save]').click();
  await expect.poll(() => writes(state, `/friends/${PEER}`).length).toBe(1);
  await page.locator('[data-dm-private-form]').dispatchEvent('submit'); await page.keyboard.press('Escape');
  await expect(page.locator('#social-dm-settings')).toBeVisible();
  await expect(page.locator('[data-dm-settings-save]')).toBeDisabled();
  delayed.release(); await expect(page.locator('[data-dm-settings-status]')).toContainText('设置已保存');
  expect(writes(state, `/friends/${PEER}`)).toHaveLength(1);
  await page.keyboard.press('Escape'); await expect(page.locator('[data-dm-settings-open]')).toBeFocused(); clean(state);
});

test('LQ blocked contact omits private learning content and unavailable relationship actions', async ({ page }) => {
  const state = await mount(page, { relation: 'blocked' }); await revealSide(page);
  await expect(page.locator('[data-dm-card]')).toHaveCount(1); await settings(page);
  for (const selector of ['[data-dm-remove]', '[data-dm-block]', '[data-dm-nudge]', '[data-dm-settings-save]']) await expect(page.locator(selector)).toBeHidden();
  expect(writes(state, '/blocks', 'POST')).toHaveLength(0); clean(state);
});

for (const action of ['remove', 'block']) test(`LQ ${action} confirms once, cancellation restores focus and success removes write access`, async ({ page }) => {
  const state = await mount(page); await settings(page);
  const trigger = page.locator(`[data-dm-${action}]`), api = action === 'remove' ? `/friends/${PEER}` : '/blocks', method = action === 'remove' ? 'DELETE' : 'POST';
  await trigger.click(); await page.getByRole('button', { name: '取消', exact: true }).click();
  expect(writes(state, api, method)).toHaveLength(0); await expect(trigger).toBeFocused();
  await trigger.click();
  await page.getByRole('dialog', { name: action === 'remove' ? '删除好友' : '拉黑', exact: true }).getByRole('button', { name: action === 'remove' ? '删除' : '拉黑', exact: true }).click();
  await expect(page.locator('#social-dm-settings')).toBeHidden();
  await expect(page.locator('[data-lq-composer-input]')).toBeDisabled();
  expect(writes(state, api, method)).toHaveLength(1);
  if (action === 'block') expect(writes(state, api, method)[0].body).toEqual({ identity: PEER });
  clean(state);
});

test('LQ dirty discard leaves stored values intact on the next opening', async ({ page }, info) => {
  const state = await mount(page); await settings(page);
  await page.locator('#social-remark').fill('未保存的名字'); await page.locator('#social-dm-muted').check();
  await page.keyboard.press('Escape'); await page.getByRole('button', { name: '放弃修改', exact: true }).click();
  await expect(page.locator('#social-dm-settings')).toBeHidden();
  try { await expect(page.locator('[data-dm-settings-open]')).toBeFocused(); }
  catch (error) {
    await info.attach('focus-lifecycle', { contentType: 'application/json', body: JSON.stringify(await page.evaluate(() => ({
      active: { tag: document.activeElement?.tagName, id: document.activeElement?.id },
      layers: [...document.querySelectorAll('[data-lq-layer-state]')].map(node => ({ id: node.id, state: (node as HTMLElement).dataset.lqLayerState, inert: (node as HTMLElement).inert, hidden: node.getAttribute('aria-hidden') })),
    }))) });
    throw error;
  }
  await settings(page); await expect(page.locator('#social-remark')).toHaveValue(''); await expect(page.locator('#social-dm-muted')).not.toBeChecked();
  expect(writes(state, `/friends/${PEER}`)).toHaveLength(0); clean(state);
});

test('LQ discarded old form cannot make a newly failed profile load appear dirty', async ({ page }) => {
  const state = await mount(page); await settings(page);
  await page.locator('#social-remark').fill('已丢弃的旧草稿');
  await page.keyboard.press('Escape'); await page.getByRole('button', { name: '放弃修改', exact: true }).click();
  await expect(page.locator('#social-dm-settings')).toBeHidden(); state.failProfile = 503;
  await settings(page); await expect(page.locator('[data-dm-settings-status]')).toContainText('设置资料暂时不可用');
  await page.keyboard.press('Escape'); await expect(page.locator('#social-dm-settings')).toBeHidden();
  await expect(page.getByRole('dialog', { name: '放弃未保存的设置？', exact: true })).toBeHidden();
  expect(writes(state, `/friends/${PEER}`)).toHaveLength(0); clean(state);
});

test('LQ muted push received during the first public card read is retained by settings', async ({ page }) => {
  const delayed = gate(); const state = await mount(page, { gateCard: delayed.pending, wait: false });
  await expect.poll(() => state.calls.some((call: any) => call.api === `/users/${PEER}/card`)).toBe(true);
  state.preferences[dm()] = true;
  await page.evaluate(key => (window as any).client.onFrame(JSON.stringify({ type: 'conversation_preferences', conversation_key: key, muted: true })), dm());
  const response = page.waitForResponse(response => decodeURIComponent(new URL(response.url()).pathname).endsWith(`/users/${PEER}/card`));
  delayed.release(); await afterResponse(page, response);
  await settings(page); await expect(page.locator('#social-dm-muted')).toBeChecked();
  await page.keyboard.press('Escape'); await expect(page.locator('#social-dm-settings')).toBeHidden();
  expect(writes(state, `/conversations/${dm()}/preferences`)).toHaveLength(0); clean(state);
});

test('LQ independently delayed card cannot replace a different conversation', async ({ page }, info) => {
  const delayed = gate(); const state = await mount(page, { gateCard: delayed.pending, wait: false });
  await expect.poll(() => state.calls.some((call: any) => call.api === `/users/${PEER}/card`)).toBe(true);
  await switchPeer(page);
  delayed.release(); const terminal = await afterRequest(page, state, `/users/${PEER}/card`);
  fs.writeFileSync(info.outputPath('request-end.json'), JSON.stringify(terminal));
  await revealSide(page); await expect(page.locator('[data-dm-card=profile]')).toContainText('谢明');
  await expect(page.locator('.social-chat__side')).not.toContainText('陈清禾');
  await expect(page.locator('#social-dm-settings')).toBeHidden(); clean(state);
});

test('LQ late settings save is inert after switching the parent conversation', async ({ page }) => {
  const state = await mount(page); await settings(page);
  const delayed = gate(); state.gates[`/friends/${PEER}`] = delayed.pending;
  await page.locator('#social-remark').fill('迟到的保存'); await page.locator('[data-dm-settings-save]').click();
  await expect.poll(() => writes(state, `/friends/${PEER}`).length).toBe(1);
  await switchPeer(page);
  await expect(page.locator('#social-dm-settings')).toBeHidden();
  const response = page.waitForResponse(response => decodeURIComponent(new URL(response.url()).pathname).endsWith(`/friends/${PEER}`));
  delayed.release(); await afterResponse(page, response);
  await revealSide(page); await expect(page.locator('[data-dm-card=profile]')).toContainText('谢明');
  await expect(page.locator('.social-chat__side')).not.toContainText('迟到的保存');
  await expect(page.locator('.lq-toast').filter({ hasText: '备注已保存' })).toHaveCount(0); clean(state);
});

test('LQ late card response cannot restore a closed parent sidebar or steal focus', async ({ page }, info) => {
  const delayed = gate(); const state = await mount(page, { gateCard: delayed.pending, wait: false });
  await expect.poll(() => state.calls.some((call: any) => call.api === `/users/${PEER}/card`)).toBe(true);
  await page.locator('#social-chat [data-lq-dialog-close]').click(); await expect(page.locator('#social-chat')).toBeHidden();
  delayed.release(); const terminal = await afterRequest(page, state, `/users/${PEER}/card`);
  fs.writeFileSync(info.outputPath('request-end.json'), JSON.stringify(terminal));
  await expect(page.locator('[data-dm-card]')).toHaveCount(0); await expect(page.locator('#social-dm-settings')).toHaveCount(0);
  await expect(page.locator('[data-social-entry]')).toBeFocused(); clean(state);
});

const layouts = [
  { width: 1440, height: 900, dark: false, glassOff: false },
  { width: 1440, height: 560, dark: true, glassOff: true },
  { width: 360, height: 780, dark: false, glassOff: false },
  { width: 360, height: 780, dark: true, glassOff: false },
  { width: 360, height: 568, dark: true, glassOff: true },
];
for (const layout of layouts) test(`LQ DM cards/settings fit ${layout.width}x${layout.height} ${layout.dark ? 'dark' : 'light'} glass ${layout.glassOff ? 'off' : 'on'}`, async ({ browser }, info) => {
  const mobile = layout.width === 360;
  const context = await browser.newContext({ viewport: { width: layout.width, height: layout.height }, hasTouch: mobile, isMobile: mobile, reducedMotion: 'reduce' });
  const page = await context.newPage(); const state = await mount(page, layout); await revealSide(page);
  await expect(page.locator('[data-dm-card]')).toHaveCount(3);
  const side = page.locator('.social-chat__side');
  expect(await side.evaluate(node => node.scrollWidth <= node.clientWidth + 1)).toBe(true);
  const sideBox = await side.boundingBox();
  expect(sideBox!.y).toBeGreaterThanOrEqual(0); expect(sideBox!.y + sideBox!.height).toBeLessThanOrEqual(layout.height + 1);
  expect(await page.locator('[data-dm-card]').evaluateAll(nodes => nodes.map(node => getComputedStyle(node).backdropFilter))).toEqual(['none', 'none', 'none']);
  const svg = await page.locator('[data-dm-card=radar] svg').boundingBox();
  expect(svg!.width).toBeLessThanOrEqual(sideBox!.width); expect(svg!.height).toBeLessThanOrEqual(240);
  const scrolling = await side.evaluate(node => ({ clientHeight: node.clientHeight, scrollHeight: node.scrollHeight, overflowY: getComputedStyle(node).overflowY }));
  const cardGlass = await glassLayers(page);
  await page.screenshot({ path: info.outputPath('dm-cards.png') });
  await page.locator('[data-dm-card=radar] svg').scrollIntoViewIfNeeded();
  const scrolledRadar = await page.locator('[data-dm-card=radar] svg').boundingBox();
  expect(scrolledRadar!.y).toBeGreaterThanOrEqual(sideBox!.y - 1);
  expect(scrolledRadar!.y + scrolledRadar!.height).toBeLessThanOrEqual(sideBox!.y + sideBox!.height + 1);
  await page.screenshot({ path: info.outputPath('dm-radar-scroll.png') });
  await settings(page);
  const surface = page.locator('#social-dm-settings .lq-dialog__surface');
  expect(await surface.evaluate(node => node.scrollWidth <= node.clientWidth + 1)).toBe(true);
  const bounds = await surface.boundingBox();
  expect(bounds!.x).toBeGreaterThanOrEqual(0); expect(bounds!.y).toBeGreaterThanOrEqual(0);
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(layout.width + 1); expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(layout.height + 1);
  await page.screenshot({ path: info.outputPath('dm-settings.png') });
  for (const selector of ['[data-dm-settings-save]', '[data-dm-block]']) {
    const button = page.locator(selector); await button.scrollIntoViewIfNeeded();
    const action = await button.boundingBox(), fab = await page.locator('#ai-chat-fab').boundingBox();
    if (mobile) expect(action!.height).toBeGreaterThanOrEqual(44);
    expect(action!.y + action!.height).toBeLessThanOrEqual(layout.height);
    expect(action!.x + action!.width <= fab!.x || action!.x >= fab!.x + fab!.width || action!.y + action!.height <= fab!.y || action!.y >= fab!.y + fab!.height).toBeTruthy();
    await expect(button).toBeEnabled();
  }
  await page.screenshot({ path: info.outputPath('dm-settings-actions.png') });
  const geometry = { graph: graph.revision, layout, side: sideBox, radar: svg, scrolledRadar, scrolling,
    settings: bounds, cardGlass, settingsGlass: await glassLayers(page),
    publicCardReads: state.calls.filter((call: any) => call.api === `/users/${PEER}/card`).length,
    privateProfileReads: state.calls.filter((call: any) => call.api === `/users/${PEER}/profile`).length };
  fs.writeFileSync(info.outputPath('geometry.json'), JSON.stringify(geometry, null, 2));
  await info.attach('layout-material-and-api-evidence', { body: JSON.stringify(geometry), contentType: 'application/json' });
  await page.keyboard.press('Escape'); await expect(page.locator('[data-dm-settings-open]')).toBeFocused();
  await info.attach('formal-resource-graph', { body: graph.revision, contentType: 'text/plain' });
  clean(state); await context.close();
});
