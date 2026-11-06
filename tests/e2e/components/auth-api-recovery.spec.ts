import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';

const host = 'http://auth-recovery.test';
const required = {
  detail: '登录状态已失效', code: 'login_required', error: { code: 'login_required' },
  redirect_to: '/student/login?next=%2Feditor%3Fdraft%3D7',
};
type Fixture = { requests: string[]; status: number; oldResponse?: () => void };

async function mount(page: Page, dark = false): Promise<Fixture> {
  const root = path.resolve('static');
  const manifest = JSON.parse(fs.readFileSync(path.join(root, 'assets/manifest.json'), 'utf8'));
  for (const name of ['js/auth.js', 'js/api.js']) {
    const digest = createHash('sha256').update(fs.readFileSync(path.join(root, name))).digest('hex');
    expect(manifest.sourceHashes[name], `Run npm run build before testing ${name}`).toBe(digest);
  }
  const asset = (name: string) => `/static/${manifest.entries[name]}`;
  const fixture: Fixture = { requests: [], status: 503 };
  await page.context().route(`${host}/**`, async route => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname.startsWith('/static/')) {
      const filename = path.resolve(root, pathname.slice('/static/'.length));
      if (!filename.startsWith(root + path.sep)) { await route.abort(); return; }
      await route.fulfill({ contentType: filename.endsWith('.css') ? 'text/css' : 'text/javascript', body: fs.readFileSync(filename) });
      return;
    }
    if (pathname === '/student/login') {
      await route.fulfill({ contentType: 'text/html', body: '<title>合成登录页</title><h1>重新登录</h1>' });
      return;
    }
    if (pathname.startsWith('/api/')) {
      fixture.requests.push(pathname);
      if (pathname === '/api/old') await new Promise<void>(resolve => { fixture.oldResponse = resolve; });
      const status = pathname === '/api/current' ? 200 : pathname === '/api/old' ? 401 : fixture.status;
      await route.fulfill({ status, contentType: 'application/json', headers: { 'Retry-After': '3' },
        json: status === 401 ? required : { detail: '合成请求结果', code: status === 403 ? 'permission_denied' : 'service_unavailable' } });
      return;
    }
    await route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh-CN" data-appearance="${dark ? 'dark' : 'light'}" style="color-scheme:${dark ? 'dark' : 'light'}">
      <meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
      <link rel="stylesheet" href="${asset('css/tailwind-app.css')}"><link rel="stylesheet" href="${asset('css/lq/index.css')}">
      <body style="padding:160px 24px 24px"><main><label for="draft">合成草稿</label><textarea id="draft" rows="5" style="width:100%"></textarea>
      <button id="save" type="button" data-lq-component="button" class="lq-btn lq-btn--glass">保存</button><p id="result" role="status"></p></main>
      <script type="module">import { apiFetch } from '${asset('js/api.js')}';
      window.fixtureApi = apiFetch;
      document.querySelector('#save').addEventListener('click', async event => {
        const button = event.currentTarget; button.disabled = true; document.querySelector('#result').textContent = 'pending';
        try { await apiFetch('/api/save', { method: 'POST', body: { text: document.querySelector('#draft').value }, silent: true }); }
        catch (failure) { document.querySelector('#result').textContent = String(failure.status); }
        finally { button.disabled = false; }
      }); window.fixtureReady = true;</script></body></html>` });
  });
  await page.goto(`${host}/editor?draft=7`);
  await page.waitForFunction(() => (window as any).fixtureReady);
  await page.locator('#draft').fill('合成草稿保留。输入法与光标仍由原文本框持有。');
  return fixture;
}

for (const [width, dark] of [[1440, false], [390, true]] as const) {
  test(`LQ API recovery preserves drafts and opens deliberate login at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    const fixture = await mount(page, dark);
    const draft = await page.locator('#draft').inputValue();
    for (const status of [503, 403, 401]) {
      fixture.status = status;
      await page.locator('#draft').focus();
      await page.evaluate(() => (document.querySelector('#save') as HTMLButtonElement).click());
      await expect(page.locator('#result')).toHaveText(String(status));
      await expect(page.locator('#save')).toBeEnabled();
      await expect(page.locator('#draft')).toHaveValue(draft);
      await expect(page.locator('#draft')).toBeFocused();
      expect(page.url()).toBe(`${host}/editor?draft=7`);
      if (status !== 401) await expect(page.locator('.lq-toast')).toHaveCount(0);
    }
    await expect(page.locator('.lq-toast')).toHaveCount(1);
    await expect(page.locator('.lq-toast')).toContainText('回到此页重试');
    await expect(page.locator('.lq-toast')).toHaveCSS('opacity', '1');
    expect(fixture.requests).toEqual(['/api/save', '/api/save', '/api/save']);
    await page.screenshot({ path: test.info().outputPath(`auth-recovery-${width}.png`), fullPage: true });
    const popup = page.waitForEvent('popup');
    await page.getByRole('button', { name: '重新登录', exact: true }).click();
    const login = await popup;
    await expect(login).toHaveURL(`${host}/student/login?next=%2Feditor%3Fdraft%3D7`);
    await login.close();
    await expect(page.locator('#draft')).toHaveValue(draft);
    expect(page.url()).toBe(`${host}/editor?draft=7`);
  });
}

test('LQ a delayed 401 after a newer response keeps the editor and XHR callbacks responsive', async ({ page }) => {
  const fixture = await mount(page);
  await page.evaluate(() => { (window as any).older = fetch('/api/old').then(response => response.status); });
  await expect.poll(() => Boolean(fixture.oldResponse)).toBe(true);
  expect(await page.evaluate(async () => (await fetch('/api/current')).status)).toBe(200);
  fixture.oldResponse!();
  expect(await page.evaluate(() => (window as any).older)).toBe(401);
  await expect(page.locator('.lq-toast')).toHaveCount(0);
  fixture.status = 401;
  const status = await page.evaluate(() => new Promise<number>((resolve, reject) => {
    const xhr = new XMLHttpRequest(); xhr.open('GET', '/api/upload-state'); xhr.responseType = 'json';
    xhr.onload = () => resolve(xhr.status); xhr.onerror = reject; xhr.send();
  }));
  expect(status).toBe(401);
  await expect(page.locator('.lq-toast')).toHaveCount(1);
  await expect(page.locator('#draft')).toHaveValue('合成草稿保留。输入法与光标仍由原文本框持有。');
  expect(page.url()).toBe(`${host}/editor?draft=7`);
});

test('LQ legacy bell shares refresh bursts and yields to React without reverting newer unread counts', async ({ page }) => {
  await mount(page);
  const manifest = JSON.parse(fs.readFileSync('static/assets/manifest.json', 'utf8'));
  for (const name of ['js/message_center_bell.js', 'js/ambient_poll.js']) {
    expect(manifest.sourceHashes[name], 'Rebuild formal assets for ambient polling').toBe(
      createHash('sha256').update(fs.readFileSync(path.join('static', name))).digest('hex'));
  }
  let calls = 0;
  let finish: (() => void) | undefined;
  await page.context().route(`${host}/api/message-center/summary`, async route => {
    calls++;
    const unread = calls === 1 ? 1 : 9;
    if (calls === 1) await new Promise<void>(resolve => { finish = resolve; });
    await route.fulfill({ json: { summary: { unread_total: unread }, latest_unread: null } });
  });
  await page.evaluate(async url => {
    const shell = document.createElement('div');
    shell.setAttribute('data-message-center-bell-shell', '');
    shell.innerHTML = '<a data-message-center-bell href="/message-center"><span data-message-center-bell-count>0</span><span data-message-center-bell-caption></span></a>';
    document.body.append(shell);
    await import(url);
    (window as any).pendingBell = Promise.all(Array.from({ length: 12 }, () => (window as any).refreshMessageCenterBell({ allowPopup: false })));
  }, `/static/${manifest.entries['js/message_center_bell.js']}`);
  await expect.poll(() => Boolean(finish)).toBe(true);
  expect(calls).toBe(1);
  await page.evaluate(() => window.dispatchEvent(new CustomEvent('message-center:summary-updated', { detail: { unread_total: 7 } })));
  finish!();
  await page.evaluate(() => (window as any).pendingBell);
  await expect(page.locator('[data-message-center-bell-count]')).toHaveText('7');
  await page.evaluate(() => (window as any).refreshMessageCenterBell({ allowPopup: false }));
  expect(calls).toBe(2);
  await expect(page.locator('[data-message-center-bell-count]')).toHaveText('9');
  await page.evaluate(async () => {
    (document.querySelector('[data-message-center-bell-shell]') as HTMLElement).dataset.messageCenterBellManaged = 'react';
    await Promise.all(Array.from({ length: 12 }, () => (window as any).refreshMessageCenterBell()));
  });
  expect(calls).toBe(2);
});
