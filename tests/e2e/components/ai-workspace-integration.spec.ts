import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';

const widget = execFileSync('python', ['-c', `from jinja2 import Environment, FileSystemLoader
env=Environment(loader=FileSystemLoader('templates'), autoescape=True)
print(env.get_template('partials/ai_workspace_widget.html').render(user_info={'role':'teacher','id':21,'name':'Fixture Teacher'},ai_workspace_access={'user_key':'teacher:21','page_path':'/dashboard'}))`], { encoding: 'utf8', env: { ...process.env, PYTHONIOENCODING: 'utf-8' } });

async function mount(page: Page, { visual = false, appearance = 'light' } = {}) {
  const errors: string[] = [], unexpected: string[] = [], sent: string[] = [], apiCalls: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  const messages: Array<{ role: string; message: string; request_id?: string }> = [{ role: 'assistant', message: '这是服务器保存的历史回复。' }];
  let session = 'fixture-session-21';
  await page.addInitScript(() => {
    const w = window as any;
    w.notices = []; w.showMessage = (message: string) => w.notices.push(message);
    w.captureStats = { requests: 0, stops: 0 };
    let handle: { handle: string; origin?: string };
    navigator.mediaDevices.setCaptureHandleConfig = (value: any) => { handle = { handle: value.handle, origin: location.origin }; };
    Object.defineProperty(MediaStreamTrack.prototype, 'getCaptureHandle', { configurable: true, value() { return null; } });
    navigator.mediaDevices.getDisplayMedia = async () => {
      w.captureStats.requests++;
      const bitmap = document.createElement('canvas'); bitmap.width = innerWidth; bitmap.height = innerHeight;
      const ctx = bitmap.getContext('2d')!; ctx.fillStyle = '#7aa3c8'; ctx.fillRect(0, 0, bitmap.width, bitmap.height);
      const stream = bitmap.captureStream(10), track = stream.getVideoTracks()[0], stop = track.stop.bind(track);
      (track as any).getCaptureHandle = () => handle;
      track.getSettings = () => ({ displaySurface: 'browser' });
      track.stop = () => { w.captureStats.stops++; stop(); };
      return stream;
    };
  });
  await page.route('https://assistant.test/**', async route => {
    const request = route.request(), url = new URL(request.url());
    const json = (body: unknown) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
    if (url.pathname.startsWith('/static/')) {
      const file = path.resolve('.' + url.pathname);
      if (!file.startsWith(path.resolve('static') + path.sep) || !fs.existsSync(file)) throw new Error(`Unexpected static file ${file}`);
      return route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : file.endsWith('.webp') ? 'image/webp' : 'text/javascript', body: fs.readFileSync(file) });
    }
    if (url.pathname.startsWith('/api/')) {
      apiCalls.push(`${request.method()} ${url.pathname}`);
      if (url.pathname === '/api/ai/workspace/sessions') return json({ sessions: [{ session_uuid: session, title: '当前页面对话', updated_at: '2026-09-24T09:30:00' }, { session_uuid: 'fixture-earlier-session', title: '之前的课程备课', updated_at: '2026-09-23T18:00:00' }] });
      if (url.pathname === `/api/ai/workspace/history/${session}`) return json({ messages, pending: false });
      if (url.pathname === '/api/ai/workspace/history/fixture-earlier-session') return json({ messages: [{ role: 'user', message: '上次的备课问题' }, { role: 'assistant', message: '这是之前会话的完整备课回复。' }], pending: false });
      if (url.pathname === '/api/ai/workspace/session/new') { session = 'fixture-new-session'; messages.splice(0); return json({ session: { session_uuid: session } }); }
      if (url.pathname === '/api/ai/workspace-chat') {
        const body = request.postDataBuffer()!.toString('utf8'); sent.push(body);
        const read = (name: string) => body.match(new RegExp(`name="${name}"\\r\\n\\r\\n([^\\r]*)`))?.[1] || '';
        messages.push({ role: 'user', message: read('message'), request_id: read('request_id') }, { role: 'assistant', message: '第一段回复。第二段回复。' });
        return route.fulfill({ contentType: 'application/x-ndjson', body: [{ event: 'answer_delta', delta: '第一段回复。' }, { event: 'answer_delta', delta: '第二段回复。' }, { event: 'done' }].map(event => JSON.stringify(event)).join('\n') + '\n' });
      }
      if (url.pathname === '/api/agent-tasks/bootstrap') return json({ runtime_configured: true, workflow_catalog: [], task_types: [], tasks: [], counts: {}, queue_state: {} });
      if (url.pathname === '/api/agent-tasks') return json({ tasks: [], counts: {}, queue_state: {} });
      if (url.pathname === '/api/agent-tasks/subscriptions') return json({ subscriptions: [], recent_tasks: [] });
      if (url.pathname === '/api/agent-tasks/composer') return json({ queue_state: {} });
      unexpected.push(url.pathname); return json({});
    }
    const photo = '/static/img/life_tips/biye-sunny-meadow-kite06-80413526.webp';
    const visualCSS = visual ? `body{background: url('${photo}') center/cover fixed!important;min-height:100vh}#outside-action{position:fixed;left:12px;top:12px;z-index:2;padding:10px 16px;border-radius:999px;background:#fff;color:#172033}.fixture-text{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:24px;padding:82px 24px 24px;font:700 44px/1.5 system-ui;color:${appearance === 'dark' ? '#ffffff' : '#172033'};text-shadow:0 1px 4px ${appearance === 'dark' ? '#172033' : '#fff'};overflow:hidden;max-height:100vh}.fixture-text p{margin:0;opacity:.8}` : '';
    const content = visual ? `<button id="outside-action" onclick="window.outsideClicks=(window.outsideClicks||0)+1">页面操作</button><main class="fixture-text">${Array.from({ length: 24 }, () => '<p>课堂内容<br>PAGE TEXT<br>课程与作业</p>').join('')}</main>` : '<main><h1>合成课堂工作台</h1><p>页面的正文仍可选中。</p></main>';
    return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html data-appearance="${appearance}" data-lq-glass="tinted" data-lq-tier="A"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Fixture dashboard</title><link rel="stylesheet" href="/static/css/ui-system.src.css"><link rel="stylesheet" href="/static/css/ai_workspace.css"><style>${visualCSS}</style></head><body class="ls-page">${content}${widget}<script src="/static/js/ai_chat_component.js"></script><script type="module" src="/static/js/ai_workspace_widget.js"></script></body></html>` });
  });
  await page.goto('https://assistant.test/dashboard');
  await page.waitForFunction(() => Boolean((window as any).aiChat?.windowManager));
  return { errors, unexpected, sent, apiCalls, messages };
}

async function open(page: Page) {
  await page.locator('#ai-chat-fab').click();
  await expect(page.locator('#ai-chat-messages-box')).toContainText('这是服务器保存的历史回复。');
}

test('real template/widget/core inserts capture as an unsent draft and restores it across navigation', async ({ page }, info) => {
  const fixture = await mount(page); await open(page);
  await page.locator('#ai-chat-textarea').fill('请稍后分析这张截图');
  await page.locator('#ai-chat-btn-capture').click();
  await expect(page.locator('.ai-workspace-capture')).toHaveAttribute('data-phase', 'select');
  await expect(page.locator('#ai-chat-modal')).toHaveCSS('visibility', 'hidden');
  expect(await page.evaluate(() => (window as any).captureStats)).toEqual({ requests: 1, stops: 1 });
  await page.mouse.move(100, 130); await page.mouse.down(); await page.mouse.move(480, 390); await page.mouse.up();
  await page.getByRole('button', { name: '插入附件', exact: true }).click();
  await expect(page.locator('.ai-workspace-capture')).toHaveCount(0);
  await expect(page.locator('#ai-chat-modal')).toHaveCSS('visibility', 'visible');
  await expect(page.locator('#ai-chat-previews img')).toHaveCount(1);
  await expect(page.locator('#ai-chat-textarea')).toHaveValue('请稍后分析这张截图');
  await expect(page.locator('#ai-chat-btn-send')).toBeEnabled(); expect(fixture.sent).toEqual([]);
  const file = await page.evaluate(() => { const file = (window as any).aiChat.pendingFiles[0] as File; return { name: file.name, type: file.type, size: file.size }; });
  expect(file.type).toBe('image/png'); expect(file.size).toBeGreaterThan(0);
  await expect.poll(async () => page.evaluate(async () => { const { createWorkspaceState } = await import('/static/js/ai_workspace_state.js'); return (await createWorkspaceState('teacher:21').loadFiles()).length; })).toBe(1);
  await page.goto('https://assistant.test/classroom/fixture');
  await page.waitForFunction(() => Boolean((window as any).aiChat?.windowManager));
  await expect(page.locator('#ai-chat-modal')).toHaveAttribute('aria-hidden', 'false');
  await expect(page.locator('#ai-chat-messages-box')).toContainText('这是服务器保存的历史回复。');
  await expect(page.locator('#ai-chat-textarea')).toHaveValue('请稍后分析这张截图'); await expect(page.locator('#ai-chat-previews img')).toHaveCount(1);
  expect(await page.evaluate(() => { const file = (window as any).aiChat.pendingFiles[0] as File; return { name: file.name, type: file.type, size: file.size }; })).toEqual(file);
  expect(fixture.sent).toEqual([]); expect(fixture.errors).toEqual([]); expect(fixture.unexpected).toEqual([]);
  await page.locator('.ai-workspace-container').evaluate(async node => { await Promise.all(node.getAnimations().map(animation => animation.finished.catch(() => {}))); });
  await page.screenshot({ path: info.outputPath('capture-restored-unsent.png') });
  await page.locator('#ai-chat-btn-capture').click();
  await expect(page.locator('.ai-workspace-capture')).toHaveAttribute('data-phase', 'select');
  await page.keyboard.press('Escape');
  await expect(page.locator('.ai-workspace-capture')).toHaveCount(0); await expect(page.locator('#ai-chat-textarea')).toBeFocused();
  expect(fixture.sent).toEqual([]);
});

test('closed workspace does not bootstrap or poll Agent tasks and ordinary history switches conversations', async ({ page }) => {
  const fixture = await mount(page);
  // Cover more than one historical task refresh interval; a deferred timer
  // must not disguise a closed-page request leak as lazy initialization.
  await page.waitForTimeout(5200);
  expect(fixture.apiCalls.filter(call => call.includes('/api/agent-tasks'))).toEqual([]);
  await expect(page.locator('#ai-chat-modal')).toHaveAttribute('aria-hidden', 'true');
  await open(page);
  await expect(page.locator('#ai-chat-history-toggle')).toHaveAttribute('aria-label', '我的对话');
  await page.locator('#ai-chat-history-toggle').click();
  await expect(page.getByRole('complementary', { name: '我的 AI 对话' })).toBeVisible();
  await page.getByRole('button', { name: /之前的课程备课/ }).click();
  await expect(page.locator('#ai-chat-messages-box')).toContainText('这是之前会话的完整备课回复。');
  await expect(page.getByRole('complementary', { name: '我的 AI 对话' })).toBeHidden();
  expect(await page.evaluate(() => (window as any).aiChat.currentSessionUUID)).toBe('fixture-earlier-session');
  await page.reload(); await page.waitForFunction(() => Boolean((window as any).aiChat?.windowManager));
  await expect(page.locator('#ai-chat-messages-box')).toContainText('这是之前会话的完整备课回复。');
  expect(fixture.errors).toEqual([]); expect(fixture.unexpected).toEqual([]);
});

for (const view of [{ name: 'light', appearance: 'light', width: 1280, height: 900 }, { name: 'dark', appearance: 'dark', width: 1280, height: 900 }, { name: 'mobile-390', appearance: 'dark', width: 390, height: 844 }]) {
  test(`workspace actual welcome photograph and glass ${view.name}`, async ({ page }, info) => {
    await page.setViewportSize({ width: view.width, height: view.height });
    const fixture = await mount(page, { visual: true, appearance: view.appearance });
    await page.evaluate(async () => { const image = new Image(); image.src = '/static/img/life_tips/biye-sunny-meadow-kite06-80413526.webp'; await image.decode(); });
    await open(page);
    await page.locator('.ai-workspace-container').evaluate(async node => { await Promise.all(node.getAnimations().map(animation => animation.finished.catch(() => {}))); });
    await page.locator('#outside-action').click(); expect(await page.evaluate(() => (window as any).outsideClicks)).toBe(1);
    await page.locator('#ai-chat-textarea').fill('保持网页可操作，查看玻璃下的文字模糊。');
    // Typing enables Send and starts its own color transition after the window
    // entrance. Inspect the settled controls too, not an intermediate color.
    await page.locator('.ai-workspace-container').evaluate(async node => { await Promise.all(node.getAnimations({ subtree: true }).filter(animation => animation.effect?.getTiming().iterations !== Infinity).map(animation => animation.finished.catch(() => {}))); });
    const material = await page.locator('.ai-workspace-container').evaluate(node => {
      const glass = getComputedStyle(node, '::before'), modal = getComputedStyle(node.parentElement!), surface = getComputedStyle(node), rect = node.getBoundingClientRect();
      const send = node.querySelector<HTMLButtonElement>('#ai-chat-btn-send')!, button = getComputedStyle(send), svg = getComputedStyle(send.querySelector('svg')!), path = getComputedStyle(send.querySelector('path')!);
      return { pseudoBlur: glass.backdropFilter, pseudoFill: glass.backgroundColor, pseudoImage: glass.backgroundImage, ink: surface.color, surfaceBlur: surface.backdropFilter, wrapperBlur: modal.backdropFilter, wrapperPointerEvents: modal.pointerEvents, surfacePointerEvents: surface.pointerEvents, send: { disabled: send.disabled, ink: button.color, image: button.backgroundImage, opacity: button.opacity, svgInk: svg.color, pathStroke: path.stroke }, rect: { x: rect.x, y: rect.y, width: rect.width, height: rect.height } };
    });
    expect(material.pseudoBlur).toMatch(/blur\([1-9][\d.]*px\)/); expect(material.pseudoFill).not.toBe('rgba(0, 0, 0, 0)');
    expect(material.surfaceBlur).toBe('none'); expect(material.wrapperBlur).toBe('none');
    expect(material.wrapperPointerEvents).toBe('none'); expect(material.surfacePointerEvents).toBe('auto');
    expect(material.rect.x).toBeGreaterThanOrEqual(8); expect(material.rect.x + material.rect.width).toBeLessThanOrEqual(view.width - 8);
    expect(material.rect.y + material.rect.height).toBeLessThanOrEqual(view.height - 8);
    await info.attach('material-computed.json', { body: JSON.stringify(material, null, 2), contentType: 'application/json' });
    fs.writeFileSync(info.outputPath('material-computed.json'), JSON.stringify(material, null, 2));
    await page.screenshot({ path: info.outputPath(`assistant-${view.name}.png`) });
    expect(fixture.errors).toEqual([]); expect(fixture.unexpected).toEqual([]);
  });
}

test('real workspace loads history, submits one scoped request, consumes NDJSON and restores server reply after reload', async ({ page }) => {
  const fixture = await mount(page); await open(page);
  await page.locator('#ai-chat-textarea').fill('核对当前页面'); await page.locator('#ai-chat-btn-send').click();
  await expect(page.locator('#ai-chat-messages-box')).toContainText('第一段回复。第二段回复。');
  await expect(page.locator('#ai-chat-textarea')).toHaveValue('');
  expect(fixture.sent).toHaveLength(1);
  expect(fixture.sent[0]).toContain('name="session_uuid"\r\n\r\nfixture-session-21');
  expect(fixture.sent[0]).toContain('name="page_path"\r\n\r\n/dashboard');
  expect(fixture.sent[0]).toMatch(/name="request_id"\r\n\r\n[0-9a-f-]{36}/);
  await page.reload(); await page.waitForFunction(() => Boolean((window as any).aiChat?.windowManager));
  await expect(page.locator('#ai-chat-messages-box')).toContainText('核对当前页面');
  await expect(page.locator('#ai-chat-messages-box')).toContainText('第一段回复。第二段回复。');
  await expect(page.locator('#ai-chat-textarea')).toHaveValue('');
  expect(fixture.sent).toHaveLength(1); expect(fixture.errors).toEqual([]); expect(fixture.unexpected).toEqual([]);
});

test('HTTP 413 retains message and File draft, unlocks retry, and reuses the same request identity', async ({ page }, info) => {
  const fixture = await mount(page), attempts: string[] = [];
  await page.route('https://assistant.test/api/ai/workspace-chat', async route => {
    attempts.push(route.request().postDataBuffer()!.toString('utf8'));
    if (attempts.length === 1) return route.fulfill({ status: 413, contentType: 'application/json', body: JSON.stringify({ detail: '附件大小超出服务器限制，请调整后重试。' }) });
    return route.fallback();
  });
  await open(page);
  await page.locator('#ai-chat-textarea').fill('请检查附件中的课堂证据');
  await page.locator('#ai-chat-file-input').setInputFiles({ name: 'evidence.txt', mimeType: 'text/plain', buffer: Buffer.from('待发送附件内容 20260924') });
  await expect(page.locator('#ai-chat-previews .preview-item')).toHaveCount(1);
  await page.locator('#ai-chat-btn-send').click();
  await expect.poll(() => attempts.length).toBe(1);
  await expect(page.locator('#ai-chat-btn-send')).toBeEnabled();
  await expect(page.locator('#ai-chat-textarea')).toHaveValue('请检查附件中的课堂证据');
  expect(await page.evaluate(async () => { const chat = (window as any).aiChat, file = chat.pendingFiles[0] as File; return { loading: chat.isLoading, count: chat.pendingFiles.length, name: file.name, type: file.type, text: await file.text(), request: chat.pendingRequest }; })).toMatchObject({ loading: false, count: 1, name: 'evidence.txt', type: 'text/plain', text: '待发送附件内容 20260924' });
  const read = (body: string, name: string) => body.match(new RegExp(`name="${name}"\\r\\n\\r\\n([^\\r]*)`))?.[1];
  const firstId = read(attempts[0], 'request_id'); expect(firstId).toMatch(/^[0-9a-f-]{36}$/);
  expect(await page.evaluate(() => (window as any).aiChat.pendingRequest.id)).toBe(firstId);
  const rejectedFeedback = await page.evaluate(() => ({ notices: (window as any).notices as string[], visibleMessages: document.getElementById('ai-chat-messages-box')!.innerText }));
  expect(rejectedFeedback.notices.join('\n')).toContain('附件大小超出服务器限制，请调整后重试。');
  expect(rejectedFeedback.notices.join('\n')).toContain('草稿已保留');
  expect(rejectedFeedback.notices.join('\n')).not.toContain('{"detail"');
  const rejectedState = JSON.stringify(rejectedFeedback, null, 2);
  await info.attach('rejected-state.json', { body: rejectedState, contentType: 'application/json' });
  fs.writeFileSync(info.outputPath('rejected-state.json'), rejectedState);
  await page.locator('#ai-chat-btn-send').click();
  await expect(page.locator('#ai-chat-messages-box')).toContainText('第一段回复。第二段回复。');
  expect(attempts).toHaveLength(2); expect(read(attempts[1], 'request_id')).toBe(firstId);
  for (const body of attempts) {
    expect(read(body, 'message')).toBe('请检查附件中的课堂证据');
    expect(read(body, 'session_uuid')).toBe('fixture-session-21');
    expect(body).toContain('name="files"; filename="evidence.txt"'); expect(body).toContain('待发送附件内容 20260924');
  }
  await expect(page.locator('#ai-chat-textarea')).toHaveValue(''); await expect(page.locator('#ai-chat-previews .preview-item')).toHaveCount(0);
  expect(await page.evaluate(() => (window as any).aiChat.pendingRequest)).toBeNull();
  expect(fixture.errors).toEqual([]); expect(fixture.unexpected).toEqual([]);
});

test('HTTP 202 holds Send locked while pending, pauses history when closed or hidden, and resumes to completion', async ({ page }) => {
  const fixture = await mount(page); let pending = false, historyReads = 0, posts = 0;
  await page.route('https://assistant.test/api/ai/workspace/history/fixture-session-21', route => {
    historyReads++;
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ messages: fixture.messages, pending }) });
  });
  await page.route('https://assistant.test/api/ai/workspace-chat', route => {
    posts++; pending = true;
    const body = route.request().postDataBuffer()!.toString('utf8');
    const requestId = body.match(/name="request_id"\r\n\r\n([^\r]*)/)?.[1];
    fixture.messages.push({ role: 'user', message: '请在后台处理课堂资料', request_id: requestId });
    return route.fulfill({ status: 202, contentType: 'application/json', body: JSON.stringify({ pending: true }) });
  });
  await open(page); await page.locator('#ai-chat-textarea').fill('请在后台处理课堂资料'); await page.locator('#ai-chat-btn-send').click();
  await expect(page.locator('#ai-chat-textarea')).toHaveValue('');
  await expect.poll(() => page.evaluate(() => Boolean((window as any).aiChat.historyPollTimer))).toBe(true);
  // A new local draft makes the button eligible except for the pending lock.
  // This distinguishes proper locking from an empty-input disabled button.
  await page.locator('#ai-chat-textarea').fill('回复完成后再发送这条');
  await expect(page.locator('#ai-chat-btn-send')).toBeDisabled();
  expect(await page.evaluate(() => (window as any).aiChat.isLoading)).toBe(true); expect(posts).toBe(1);
  await page.locator('#ai-chat-btn-close').click(); await expect(page.locator('#ai-chat-modal')).toHaveAttribute('aria-hidden', 'true');
  expect(await page.evaluate(() => ({ timer: (window as any).aiChat.historyPollTimer, paused: (window as any).aiChat.historyPaused }))).toEqual({ timer: null, paused: true });
  const closedReads = historyReads; await page.waitForTimeout(2100); expect(historyReads).toBe(closedReads);
  await page.locator('#ai-chat-fab').click(); await expect.poll(() => historyReads).toBeGreaterThan(closedReads);
  await expect.poll(() => page.evaluate(() => Boolean((window as any).aiChat.historyPollTimer))).toBe(true);
  await expect(page.locator('#ai-chat-btn-send')).toBeDisabled();
  // Control the browser visibility seam, leaving all production handlers and
  // real network polling timers active in this otherwise visible test tab.
  await page.evaluate(() => { Object.defineProperty(document, 'hidden', { configurable: true, get: () => true }); document.dispatchEvent(new Event('visibilitychange')); });
  expect(await page.evaluate(() => ({ timer: (window as any).aiChat.historyPollTimer, paused: (window as any).aiChat.historyPaused }))).toEqual({ timer: null, paused: true });
  const hiddenReads = historyReads; await page.waitForTimeout(2100); expect(historyReads).toBe(hiddenReads);
  pending = false; fixture.messages.push({ role: 'assistant', message: '后台任务已经完成，历史回复已保存。' });
  await page.evaluate(() => { delete (document as any).hidden; document.dispatchEvent(new Event('visibilitychange')); });
  await expect(page.locator('#ai-chat-messages-box')).toContainText('后台任务已经完成，历史回复已保存。');
  await expect(page.locator('#ai-chat-textarea')).toHaveValue('回复完成后再发送这条'); await expect(page.locator('#ai-chat-btn-send')).toBeEnabled();
  expect(await page.evaluate(() => ({ loading: (window as any).aiChat.isLoading, timer: (window as any).aiChat.historyPollTimer, paused: (window as any).aiChat.historyPaused }))).toEqual({ loading: false, timer: null, paused: false });
  expect(posts).toBe(1); expect(fixture.errors).toEqual([]); expect(fixture.unexpected).toEqual([]);
});

test('real history imports a classroom conversation and confirms deletion without losing drafts on cancel or 403/409', async ({ page }) => {
  const fixture = await mount(page), imports: string[] = [], deletions: number[] = [];
  let imported = false, removed = false, deleteStatus = 403;
  await page.route('https://assistant.test/api/ai/workspace/sessions', route => route.fulfill({ contentType: 'application/json', body: JSON.stringify({
    sessions: [
      ...(imported && !removed ? [{ session_uuid: 'fixture-imported-7', title: '导入的网络课堂问答' }] : []),
      { session_uuid: 'fixture-session-21', title: '当前页面对话' },
    ],
    legacy_sessions: imported ? [] : [{ session_uuid: 'legacy-classroom-7', title: '旧课堂网络问答' }],
  }) }));
  await page.route('https://assistant.test/api/ai/workspace/session/import/legacy-classroom-7', route => {
    imports.push(route.request().method()); imported = true;
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ session: { session_uuid: 'fixture-imported-7' } }) });
  });
  await page.route('https://assistant.test/api/ai/workspace/history/fixture-imported-7', route => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ messages: [{ role: 'assistant', message: '旧课堂导入后的网络分析完整内容。' }], pending: false }) }));
  await page.route('https://assistant.test/api/ai/workspace/session/fixture-imported-7', route => {
    expect(route.request().method()).toBe('DELETE'); deletions.push(deleteStatus);
    if (deleteStatus === 200) removed = true;
    const response = deleteStatus === 403 ? { detail: '你无权删除该对话。' } : deleteStatus === 409 ? { detail: '当前对话仍在生成回复。' } : { deleted: true };
    return route.fulfill({ status: deleteStatus, contentType: 'application/json', body: JSON.stringify(response) });
  });
  await open(page);
  await page.locator('#ai-chat-textarea').fill('这份未发送草稿必须保留');
  await page.locator('#ai-chat-file-input').setInputFiles({ name: 'history-draft.txt', mimeType: 'text/plain', buffer: Buffer.from('尚未发送的课堂附件') });
  await page.locator('#ai-chat-history-toggle').click();
  const history = page.getByRole('complementary', { name: '我的 AI 对话' });
  await expect(history).toBeVisible();
  await expect(history.getByRole('button', { name: '删除对话：旧课堂网络问答', exact: true })).toHaveCount(0);
  await history.getByRole('button', { name: /课堂对话 · 旧课堂网络问答/ }).click();
  await expect(page.locator('#ai-chat-messages-box')).toContainText('旧课堂导入后的网络分析完整内容。');
  expect(imports).toEqual(['POST']); expect(await page.evaluate(() => (window as any).aiChat.currentSessionUUID)).toBe('fixture-imported-7');
  await expect(history).toBeHidden();
  await page.locator('#ai-chat-history-toggle').click();
  const remove = history.getByRole('button', { name: '删除对话：导入的网络课堂问答', exact: true });
  const confirmation = page.getByRole('dialog', { name: '删除对话', exact: true });
  await remove.click(); await expect(confirmation).toBeVisible();
  await expect(confirmation).toHaveClass(/lq-confirm/);
  await confirmation.getByRole('button', { name: '取消', exact: true }).click();
  await expect(confirmation).toHaveCount(0); expect(deletions).toEqual([]); await expect(history).toBeVisible();
  for (const status of [403, 409]) {
    deleteStatus = status;
    await remove.click(); await expect(confirmation).toBeVisible();
    await confirmation.getByRole('button', { name: '删除', exact: true }).click();
    await expect.poll(() => deletions.at(-1)).toBe(status); await expect(remove).toBeEnabled();
    await expect(history).toBeVisible(); await expect(page.locator('#ai-chat-modal')).toHaveAttribute('aria-hidden', 'false');
    await expect(page.locator('#ai-chat-messages-box')).toContainText('旧课堂导入后的网络分析完整内容。');
    expect(await page.evaluate(() => (window as any).aiChat.currentSessionUUID)).toBe('fixture-imported-7');
    expect(await page.evaluate(() => (window as any).notices)).toContain(status === 403 ? '你无权删除该对话。' : '当前对话仍在生成回复。');
    await expect(page.locator('#ai-chat-textarea')).toHaveValue('这份未发送草稿必须保留');
    expect(await page.evaluate(async () => { const file = (window as any).aiChat.pendingFiles[0] as File; return { name: file.name, text: await file.text() }; })).toEqual({ name: 'history-draft.txt', text: '尚未发送的课堂附件' });
  }
  deleteStatus = 200; await remove.click(); await expect(confirmation).toBeVisible();
  await confirmation.getByRole('button', { name: '删除', exact: true }).click();
  await expect(page.locator('#ai-chat-messages-box')).toContainText('这是服务器保存的历史回复。');
  await expect(history).toBeVisible(); await expect(remove).toHaveCount(0);
  expect(deletions).toEqual([403, 409, 200]);
  expect(await page.evaluate(() => (window as any).aiChat.currentSessionUUID)).toBe('fixture-session-21');
  await expect(page.locator('#ai-chat-textarea')).toHaveValue('这份未发送草稿必须保留');
  expect(await page.evaluate(async () => { const file = (window as any).aiChat.pendingFiles[0] as File; return { name: file.name, text: await file.text() }; })).toEqual({ name: 'history-draft.txt', text: '尚未发送的课堂附件' });
  expect(fixture.sent).toEqual([]); expect(fixture.errors).toEqual([]); expect(fixture.unexpected).toEqual([]);
});
