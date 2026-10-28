import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';

const manifest = JSON.parse(fs.readFileSync('static/assets/manifest.json', 'utf8'));
const asset = (name: string) => `/static/${manifest.entries[name]}`;
const widget = execFileSync('python', ['-X', 'utf8', '-c', `from jinja2 import Environment, FileSystemLoader
print(Environment(loader=FileSystemLoader('templates'),autoescape=True).get_template('partials/ai_workspace_widget.html').render(user_info={'role':'teacher','id':21,'name':'合成教师'},ai_workspace_access={'user_key':'teacher:21','page_path':'/dashboard'}))`], { encoding: 'utf8' });

async function mount(page: Page, options = { dark: false, glass: true }) {
  for (const source of ['js/ai_chat_component.js', 'css/ai_workspace.css', 'js/ai_workspace_widget.js'])
    expect(createHash('sha256').update(fs.readFileSync(`static/${source}`)).digest('hex')).toBe(manifest.sourceHashes[source]);
  const state: any = { sent: [], calls: [], errors: [], failQuota: false, release: null, defer: false,
    deferQuota: false, releaseQuota: null,
    quota: { limit: 10, used: 2, reserved: 0, remaining: 8, unknown: 0, available: true, reset_at: '2026-10-29T00:00:00+08:00' } };
  page.on('pageerror', error => state.errors.push(error.message));
  await page.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url());
    if (url.origin !== 'https://chat-search.test') return route.abort();
    if (url.pathname.startsWith('/static/')) {
      const file = path.resolve('.' + url.pathname);
      if (!file.startsWith(path.resolve('static') + path.sep) || !fs.existsSync(file)) throw Error('Unexpected static resource: ' + url.pathname);
      return route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body: fs.readFileSync(file) });
    }
    const json = (value: any, status = 200) => route.fulfill({ status, json: value });
    if (url.pathname.startsWith('/api/')) {
      state.calls.push(url.pathname);
      if (url.pathname === '/api/ai/search-quota') {
        const quota = { ...state.quota }, fail = state.failQuota;
        if (state.deferQuota) await new Promise<void>(resolve => state.releaseQuota = resolve);
        return json({ channel: 'chat', quota }, fail ? 503 : 200);
      }
      if (url.pathname === '/api/ai/workspace/sessions') return json({ sessions: [{ session_uuid: 'search-owned-session', title: '合成对话' }] });
      if (url.pathname === '/api/ai/workspace/history/search-owned-session') return json({ messages: [], pending: false });
      if (url.pathname === '/api/ai/workspace/session/new') return json({ session: { session_uuid: 'search-owned-session' } });
      if (url.pathname === '/api/ai/workspace-chat') {
        const body = request.postDataBuffer()!.toString('utf8');
        const read = (name: string) => body.match(new RegExp(`name="${name}"\\r\\n\\r\\n([^\\r]*)`))?.[1] || '';
        state.sent.push({ message: read('message'), deep: read('deep_thinking'), search: read('web_search_enabled'), id: read('request_id') });
        if (state.defer) await new Promise<void>(resolve => state.release = resolve);
        const enabled = read('web_search_enabled') === 'true';
        const events = enabled ? [
          { event: 'search_status', tool: 'web_search', stage: 'searching', message: '正在检索公开网页…' },
          { event: 'web_search_quota', channel: 'chat', quota: state.quota },
          { event: 'answer_delta', delta: state.quota.remaining > 0 ? '参考[官方资料](https://api-docs.deepseek.com/zh-cn/guides/search/)。' : '当前没有可用搜索额度，先依据已有知识回答。' },
        ] : [{ event: 'answer_delta', delta: '未联网的普通回复。' }];
        return route.fulfill({ contentType: 'application/x-ndjson', body: [...events, { event: 'done' }].map(event => JSON.stringify(event)).join('\n') + '\n' });
      }
      if (url.pathname.startsWith('/api/agent-tasks')) return json({ runtime_configured: true, task_types: [], tasks: [], workflow_catalog: [], counts: {}, queue_state: {}, subscriptions: [], recent_tasks: [] });
      throw Error('Unexpected API request: ' + url.pathname);
    }
    return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh-CN" data-appearance="${options.dark ? 'dark' : 'light'}" data-lq-scene-tone="${options.dark ? 'dark' : 'light'}" data-lq-glass="${options.glass ? 'tinted' : 'off'}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="${asset('css/tailwind-app.css')}"><link rel="stylesheet" href="${asset('css/ai_workspace.css')}"></head><body><main>真实组件与合成API</main>${widget}<script src="${asset('js/marked.min.js')}"></script><script src="${asset('js/markdown_runtime.js')}"></script><script src="${asset('js/ai_chat_component.js')}"></script><script type="module" src="${asset('js/ai_workspace_widget.js')}"></script></body></html>` });
  });
  await page.goto('https://chat-search.test/');
  await page.waitForFunction(() => Boolean((window as any).aiChat?.windowManager));
  return state;
}
async function open(page: Page) {
  await page.locator('#ai-chat-fab').click();
  await expect(page.locator('#ai-chat-modal')).toHaveAttribute('aria-hidden', 'false');
  await expect(page.locator('#ai-chat-textarea')).toBeEnabled();
}
async function send(page: Page) {
  await page.locator('#ai-chat-textarea').fill('查询最新公开技术文档');
  await page.locator('#ai-chat-btn-send').click();
}

test('LQ chat search starts off, loads owned quota lazily and deep thinking shares the same switch', async ({ page }) => {
  const state = await mount(page);
  expect(state.calls.filter((value: string) => value.includes('search-quota'))).toEqual([]);
  await open(page); await expect(page.locator('#ai-web-search-quota')).toHaveText('今日搜索剩余 8/10');
  await expect(page.locator('#ai-web-search-btn')).toHaveAttribute('aria-pressed', 'false');
  await page.locator('#ai-deep-think-btn').click(); await send(page);
  await expect(page.locator('#ai-chat-messages-box')).toContainText('未联网的普通回复');
  expect(state.sent[0]).toMatchObject({ deep: 'true', search: 'false' });
  expect(state.sent[0].id).toBeTruthy(); expect(state.errors).toEqual([]);
});

test('LQ enabled search carries preference, freezes the switch while sending and renders safe source links', async ({ page }) => {
  const state = await mount(page); await open(page);
  await page.locator('#ai-web-search-btn').click(); state.defer = true;
  await send(page); await expect.poll(() => state.sent.length).toBe(1);
  await expect(page.locator('#ai-web-search-btn')).toBeDisabled();
  state.quota = { ...state.quota, used: 3, unknown: 1, remaining: 6 }; state.release();
  await expect(page.locator('#ai-web-search-quota')).toHaveText('今日搜索剩余 6/10 · 1次待确认');
  await expect(page.locator('#ai-chat-messages-box a[href="https://api-docs.deepseek.com/zh-cn/guides/search/"]')).toBeVisible();
  await expect(page.locator('#ai-web-search-btn')).toBeEnabled();
  expect(state.sent[0].search).toBe('true'); expect(state.errors).toEqual([]);
});

test('LQ exhausted or unknown search quota never disables normal chat', async ({ page }) => {
  const state = await mount(page); state.quota = { ...state.quota, remaining: 0, used: 9, unknown: 1 };
  await open(page); await page.locator('#ai-web-search-btn').click(); await send(page);
  await expect(page.locator('#ai-chat-messages-box')).toContainText('当前没有可用搜索额度');
  await expect(page.locator('#ai-web-search-quota')).toContainText('0/10 · 1次待确认');
  await page.locator('#ai-web-search-btn').click(); await send(page);
  await expect(page.locator('#ai-chat-messages-box')).toContainText('未联网的普通回复');
  expect(state.sent.map((item: any) => item.search)).toEqual(['true', 'false']); expect(state.errors).toEqual([]);
});

test('LQ quota outage remains readable, chat works and reopening retries quota', async ({ page }) => {
  const state = await mount(page); state.failQuota = true; await open(page);
  await expect(page.locator('#ai-web-search-quota')).toHaveText('搜索额度暂不可用');
  await send(page); await expect(page.locator('#ai-chat-messages-box')).toContainText('未联网的普通回复');
  await page.locator('#ai-chat-btn-close').click(); state.failQuota = false;
  await open(page); await expect(page.locator('#ai-web-search-quota')).toHaveText('今日搜索剩余 8/10');
  expect(state.errors).toEqual([]);
});

for (const failure of [false, true]) test(`LQ stale quota HTTP ${failure ? 'failure' : 'success'} cannot overwrite a newer streamed quota`, async ({ page }) => {
  const state = await mount(page); state.deferQuota = true; state.failQuota = failure;
  await open(page); await expect.poll(() => typeof state.releaseQuota).toBe('function');
  // This GET captured 8 remaining when opening. The real chat component then
  // receives a newer quota event in its streamed response before that GET ends.
  state.quota = { ...state.quota, used: 3, remaining: 7 };
  await page.locator('#ai-web-search-btn').click(); await send(page);
  await expect(page.locator('#ai-web-search-quota')).toHaveText('今日搜索剩余 7/10');
  const finished = page.waitForResponse(response => new URL(response.url()).pathname === '/api/ai/search-quota');
  state.releaseQuota(); await finished;
  await page.waitForFunction(() => (window as any).aiChat.searchQuotaPromise === null);
  await expect(page.locator('#ai-web-search-quota')).toHaveText('今日搜索剩余 7/10');
  expect(state.calls.filter((call: string) => call.includes('search-quota'))).toHaveLength(1);
  expect(state.errors).toEqual([]);
});

for (const scene of [
  { width: 1440, height: 900, dark: false, glass: true },
  { width: 360, height: 780, dark: true, glass: true },
  { width: 360, height: 568, dark: true, glass: false },
]) test(`LQ search controls fit ${scene.width}x${scene.height} ${scene.glass ? 'glass' : 'opaque'}`, async ({ page }) => {
  await page.setViewportSize(scene); const state = await mount(page, scene); await open(page);
  await expect(page.locator('#ai-web-search-quota')).toContainText('8/10');
  await page.locator('#ai-web-search-btn').focus(); await page.keyboard.press('Space');
  await expect(page.locator('#ai-web-search-btn')).toHaveAttribute('aria-pressed', 'true');
  const metrics = await page.locator('.ai-chat-input-area').evaluate(node => {
    const bounds = node.getBoundingClientRect();
    return { x: bounds.x, right: bounds.right, bottom: bounds.bottom, overflow: node.scrollWidth - node.clientWidth };
  });
  expect(metrics.x).toBeGreaterThanOrEqual(0); expect(metrics.right).toBeLessThanOrEqual(scene.width);
  expect(metrics.bottom).toBeLessThanOrEqual(scene.height); expect(metrics.overflow).toBeLessThanOrEqual(1);
  await page.screenshot({ path: test.info().outputPath(`search-${scene.width}-${scene.height}.png`) });
  expect(state.errors).toEqual([]);
});
