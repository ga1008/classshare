import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

test('LQ early/late readiness, repeated bootstrap and duplicate module URLs share one runtime', async ({ page }) => {
  let formRequests = 0;
  page.on('request', request => { if (new URL(request.url()).pathname.endsWith('/lq/forms.js')) formRequests++; });
  const bootstrap = fs.readFileSync('templates/partials/lq_ready_core.js', 'utf8');
  await page.route('**/*', route => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname.startsWith('/static/js/')) {
      const file = path.resolve(`.${pathname}`);
      if (file.startsWith(path.resolve('static/js') + path.sep) && fs.existsSync(file)) {
        return route.fulfill({ contentType: 'text/javascript', body: fs.readFileSync(file) });
      }
    }
    if (pathname === '/') return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh-CN"><head><title>LQ runtime</title>
      <script>${bootstrap}</script><script>
      window.events = []; window.earlyLQ = LQ; window.retainedReady = LQ.ready;
      LQ.ready(api => { events.push('early'); window.earlyAPI = api; });
      const original = document.addEventListener.bind(document); window.listeners = {};
      document.addEventListener = (type, fn, options) => { listeners[type] = (listeners[type] || 0) + 1; original(type, fn, options); };
      </script><script>${bootstrap}</script></head><body><main><h1>Ready</h1>
      <button class="lq-btn" data-lq-disabled="true">Disabled</button></main></body></html>` });
    return route.abort();
  });
  await page.goto('http://lq-runtime.test/');
  expect(await page.evaluate(() => (window as any).events)).toEqual([]);
  const result = await page.evaluate(async () => {
    const w = window as any;
    const first = await import('/static/js/lq/index.js');
    await w.retainedReady((api: unknown) => { w.events.push('retained'); w.retainedAPI = api; });
    const before = { ...w.listeners };
    const second = await import('/static/js/lq/index.js?other-graph');
    await w.LQ.ready((api: any) => { w.events.push('late'); document.body.append(api.button({ label: '保存' })); });
    let otherRan = false;
    const failed = w.LQ.ready(() => { throw new Error('consumer failure'); }).catch((error: Error) => error.message);
    await w.LQ.ready(() => { otherRan = true; });
    return { events: w.events, same: w.earlyLQ === first.LQ && first.LQ === second.LQ && w.earlyAPI === w.retainedAPI,
      layer: first.LQ.layer === second.getLayerSystem(document), noNewListeners: JSON.stringify(before) === JSON.stringify(w.listeners),
      listeners: before, otherRan, failure: await failed, readyIdentity: await w.LQ.ready() === w.LQ };
  });
  expect(result).toMatchObject({ events: ['early', 'retained', 'late'], same: true, layer: true,
    noNewListeners: true, otherRan: true, failure: 'consumer failure', readyIdentity: true,
    listeners: { click: 1, keydown: 1, error: 1 } });
  await expect(page.getByRole('button', { name: '保存' })).toBeVisible();
  expect(formRequests).toBe(0);
  expect(await page.evaluate(async () => {
    const api = (window as any).LQ;
    const first = api.load('forms'); const second = api.load('forms');
    const module = await first;
    return { same: first === second, loaded: Boolean(module), invalid: await api.load('__proto__').catch((error: Error) => error.name) };
  })).toEqual({ same: true, loaded: true, invalid: 'TypeError' });
  expect(formRequests).toBe(1);
  expect(await page.locator('.lq-btn').first().evaluate(node => {
    let activated = false;
    node.addEventListener('click', () => { activated = true; });
    const event = new MouseEvent('click', { bubbles: true, cancelable: true });
    node.dispatchEvent(event);
    return { activated, prevented: event.defaultPrevented };
  })).toEqual({ activated: false, prevented: true });
});

test('LQ waits for DOM readiness and keeps skeletons decorative across motion modes', async ({ page }) => {
  let releaseFinish!: () => void;
  const finish = new Promise<void>(resolve => { releaseFinish = resolve; });
  const bootstrap = fs.readFileSync('templates/partials/lq_ready_core.js', 'utf8');
  await page.route('**/*', async route => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname === '/finish.js') {
      await finish;
      return route.fulfill({ contentType: 'text/javascript', body: 'window.parserReleased = true;' });
    }
    if (pathname.startsWith('/static/js/') || pathname === '/static/css/tailwind-app.css') {
      const file = path.resolve(`.${pathname}`);
      if (file.startsWith(path.resolve('static') + path.sep) && fs.existsSync(file)) {
        return route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body: fs.readFileSync(file) });
      }
    }
    if (pathname === '/') return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh-CN" data-theme="lanshare" data-appearance="light"><head><title>LQ loading</title>
      <link rel="stylesheet" href="/static/css/tailwind-app.css"><script>${bootstrap}</script></head><body><main aria-label="Loading content" aria-busy="true"><h1>Loading</h1></main>
      <script>window.callbacks=0;LQ.ready(api=>{callbacks++;document.querySelector('main').append(api.skeleton({lines:3}));document.body.dataset.ready='true';});
      import('/static/js/lq/index.js').then(()=>window.moduleLoaded=true);</script><script src="/finish.js"></script></body></html>` });
    return route.abort();
  });
  await page.goto('http://lq-runtime.test/', { waitUntil: 'commit' });
  try {
    await expect.poll(() => page.evaluate(() => Boolean((window as any).moduleLoaded))).toBe(true);
    expect(await page.evaluate(() => ({ state: document.readyState, callbacks: (window as any).callbacks })))
      .toEqual({ state: 'loading', callbacks: 0 });
  } finally { releaseFinish(); }
  await expect(page.locator('body')).toHaveAttribute('data-ready', 'true');
  expect(await page.evaluate(() => (window as any).callbacks)).toBe(1);
  await expect(page.locator('.lq-skeleton')).toHaveAttribute('aria-hidden', 'true');
  expect(await page.locator('.lq-skeleton').evaluate(node => node.querySelectorAll('a,button,input,[tabindex]').length)).toBe(0);
  const part = page.locator('.lq-skeleton__part').first();
  await expect(part).toHaveCSS('animation-duration', '1.6s');
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await expect(part).toHaveCSS('animation-name', 'none');
  await page.emulateMedia({ reducedMotion: 'no-preference', forcedColors: 'active' });
  await expect(part).toHaveCSS('animation-name', 'none');
});

test('LQ lazy confirmation can be canceled before download and after opening without late dialogs', async ({ page }) => {
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/static/js/lq/dialogs.js') await gate;
    if (url.pathname.startsWith('/static/')) {
      const file = path.resolve(`.${url.pathname}`);
      if (file.startsWith(path.resolve('static') + path.sep) && fs.existsSync(file)) return route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body: fs.readFileSync(file) });
    }
    if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh-CN" data-theme="lanshare" data-appearance="light"><head><title>LQ lazy dialogs</title><link rel="stylesheet" href="/static/css/tailwind-app.css"></head><body><main><h1>Dialogs</h1><button id="opener">继续</button></main><script type="module">import LQ from '/static/js/lq/index.js';await LQ.ready();document.body.dataset.ready='true';</script></body></html>` });
    return route.abort();
  });
  await page.goto('http://lq-runtime.test/');
  await expect(page.locator('body')).toHaveAttribute('data-ready', 'true');
  try {
    expect(await page.evaluate(async () => {
      const w = window as any;
      const confirm = w.LQ.confirm({ title: '延迟确认', message: '取消后不能突然打开' });
      const choose = w.LQ.choose({ title: '延迟选择', message: '取消后不能突然打开', choices: [{ value: 'a', label: '一项' }] });
      const initially = confirm.handle; confirm.destroy(); confirm.destroy(); choose.destroy();
      return { initially, confirm: await confirm, choose: await choose };
    })).toEqual({ initially: null, confirm: false, choose: { status: 'dismissed' } });
  } finally { release(); }
  await page.evaluate(() => (window as any).LQ.load('dialogs'));
  await expect(page.locator('[role=dialog]')).toHaveCount(0);
  await page.locator('#opener').focus();
  await page.evaluate(() => { const w = window as any; w.result = w.LQ.confirm({ title: '明确确认', message: '等待取消' }); });
  await expect(page.getByRole('dialog', { name: '明确确认' })).toBeVisible();
  expect(await page.evaluate(async () => { const w = window as any; w.result.destroy(); return await w.result; })).toBe(false);
  await expect(page.locator('[role=dialog]')).toHaveCount(0);
  expect(await page.evaluate(async () => (window as any).LQ.confirm({ title: '' }).catch((error: Error) => error.name))).toBe('TypeError');
  expect(await page.evaluate(async () => {
    const w = window as any, nav = await w.LQ.load('navigation');
    const root = nav.createSegment({ id: 'facade', label: '视图', items: [{ key: 'a', label: 'A' }, { key: 'b', label: 'B' }] });
    document.body.append(root);
    const pending = w.LQ.segment(root); pending.destroy(); pending.destroy();
    const canceled = await pending;
    const marker = root.querySelector('[role=tablist]').getAttribute('data-lq-thumb');
    const mounted = await w.LQ.segment(root);
    const same = mounted === nav.tabs(root);
    mounted.select('b'); mounted.destroy();
    const removed = w.LQ.tabs(root); root.remove();
    const discarded = await removed;
    const notice = await w.LQ.toast('来自统一入口', { duration: 0 });
    const toast = await w.LQ.load('toast');
    const singleton = toast.getToastSystem(document).size;
    notice.destroy();
    return { canceled, marker, same, value: mounted.value, discarded, singleton, remaining: toast.getToastSystem(document).size,
      tone: w.LQ.tone('save', 'local_saved') };
  })).toEqual({ canceled: null, marker: null, same: true, value: 'b', discarded: null, singleton: 1, remaining: 0,
    tone: { name: 'save-local_saved', level: 'neutral', known: true } });
});
