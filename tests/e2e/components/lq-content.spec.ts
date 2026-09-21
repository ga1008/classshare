import { test, expect, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';

const python = process.env.LQ_TEST_PYTHON || (process.platform === 'win32' ? 'venv/Scripts/python.exe' : 'venv/bin/python');
const fixture = JSON.parse(execFileSync(python, ['tests/e2e/scripts/render_lq_content.py'], { encoding: 'utf8' }));
const palettes = ['teal', 'indigo', 'sky', 'mint', 'violet', 'rose'];
const tokens = JSON.parse(fs.readFileSync('docs/lq-tokens.json', 'utf8'));
async function mount(page: Page, entry = 'jinja', palette = 'indigo', appearance = 'light') {
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin !== 'https://lq-content.test') return route.abort();
    if (url.pathname.startsWith('/static/js/lq/') || ['/static/css/tailwind-app.css', '/static/css/lq/components/content.css', '/static/css/lq/components/forms.css'].includes(url.pathname)) {
      const file = path.resolve(`.${url.pathname}`);
      if (file.startsWith(path.resolve('static') + path.sep) && fs.existsSync(file)) return route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body: fs.readFileSync(file) });
    }
    if (url.pathname === '/image.png') return route.fulfill({ contentType: 'image/png', body: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aL1sAAAAASUVORK5CYII=', 'base64') });
    if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh-CN" data-theme="lanshare" data-ui-palette="${palette}" data-appearance="${appearance}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>LQ 内容组件</title><link rel="stylesheet" href="/static/css/tailwind-app.css"><style>body{margin:0}main{padding:24px;max-width:900px;margin:auto;display:grid;grid-template-columns:minmax(0,1fr);gap:16px}#samples{min-width:0;display:grid;grid-template-columns:minmax(0,1fr);gap:16px}textarea{display:block;max-width:100%}.sample{min-width:0;max-width:100%}</style></head><body><main><h1>内容组件</h1><h2>展示样例</h2><div id="samples"></div></main><script type="module">import * as api from '/static/js/lq/content.js';import {enhanceComponents} from '/static/js/lq/components.js';window.api=api;window.enhancer=enhanceComponents();document.body.dataset.ready='true';</script></body></html>` });
    return route.fulfill({ status: 404, body: 'local fixture only' });
  });
  await page.goto('https://lq-content.test/'); await expect(page.locator('body')).toHaveAttribute('data-ready', 'true');
  await page.evaluate(({ fixture, entry }) => {
    const api = (window as any).api, host = document.querySelector('#samples')!;
    for (const item of fixture.cases) {
      const wrapper = document.createElement(item.kind === 'row' ? 'ul' : 'div'); wrapper.className = 'sample';
      if (entry === 'element') wrapper.append(api.createContent(item.kind, item.props));
      else wrapper.innerHTML = entry === 'jinja' ? item.html : api.html[item.kind](item.props);
      host.append(wrapper);
    }
    const composition = document.createElement('div'); composition.innerHTML = fixture.composition; host.append(composition);
    (window as any).draftNode = document.querySelector('#draft');
    (window as any).clicks = [];
    document.addEventListener('click', event => { const control = (event.target as Element).closest('button,a'); if (control) { (window as any).clicks.push(control.id); if (control.tagName === 'A') event.preventDefault(); } });
    (window as any).submissions = 0; document.addEventListener('submit', event => { event.preventDefault(); (window as any).submissions++; });
  }, { fixture, entry });
}

test.describe('LQ content presentation', () => {
  test('actual Jinja, Python props, HTML and Element trees agree across valid and invalid inputs', async ({ page }) => {
    expect(fixture.isolated).toBe(true); expect(fixture.cases.filter((item: any) => item.error)).toEqual([]);
    await mount(page);
    const results = await page.evaluate(cases => {
      const api = (window as any).api;
      const tree = (n: Node): any => n.nodeType === 3 ? (n.textContent?.trim() ? { text: n.textContent } : null) : { tag: (n as Element).tagName, attrs: Object.fromEntries([...(n as Element).attributes].map(a => [a.name, a.value]).sort()), children: [...n.childNodes].map(tree).filter(Boolean) };
      const parse = (html: string) => { const t = document.createElement('template'); t.innerHTML = html; return [...t.content.childNodes].map(tree).filter(Boolean); };
      return cases.map((item: any) => ({ props: api.contentProps(item.kind, item.props), ssr: parse(item.html), html: parse(api.html[item.kind](item.props)), element: [tree(api.createContent(item.kind, item.props))] }));
    }, fixture.cases);
    results.forEach((item: any, i: number) => { expect(item.props).toEqual(fixture.cases[i].normalized); expect(item.html).toEqual(item.ssr); expect(item.element).toEqual(item.ssr); });
    expect(fixture.invalid.every((item: any) => item.error === 'ValueError')).toBe(true);
    const invalid = await page.evaluate(cases => cases.map((item: any) => ['contentProps', 'contentMarkup', 'createContent'].map(method => { try { (window as any).api[method](item.kind, item.props); return false; } catch (error) { return error instanceof TypeError; } })), fixture.invalid);
    invalid.forEach((item: boolean[]) => expect(item).toEqual([true, true, true]));
  });

  for (const entry of ['jinja', 'html', 'element']) test(`${entry}: main/secondary native keyboard actions are independent and disabled never submits`, async ({ page }) => {
    await mount(page, entry);
    await page.locator('#primary-link').focus(); await page.keyboard.press('Enter');
    await page.keyboard.press('Tab'); await expect(page.locator('#secondary-action')).toBeFocused(); await page.keyboard.press('Space');
    await page.locator('#primary-command').focus(); await page.keyboard.press('Space');
    expect(await page.evaluate(() => (window as any).clicks)).toEqual(['primary-link', 'secondary-action', 'primary-command']);
    await expect(page.locator('#disabled-primary')).toBeDisabled();
    await page.evaluate(() => (document.querySelector('#disabled-primary') as HTMLButtonElement).click());
    expect(await page.evaluate(() => (window as any).submissions)).toBe(0);
    expect(await page.locator('a a,a button,button a,button button').count()).toBe(0);
    await expect(page.locator('#card-zero .lq-card__value')).toHaveText('0');
    await expect(page.locator('#empty-error')).toHaveText('内容加载失败服务器暂时不可用重试');
    await page.locator('#content-search').fill('修改后保留'); await page.locator('#content-search').press('Enter');
    expect(await page.evaluate(() => (window as any).submissions)).toBe(1); await expect(page.locator('#content-search')).toHaveValue('修改后保留');
  });

  test('slots validate the full input before moving nodes and retain real drafts/listeners/lightbox declarations', async ({ page }) => {
    await mount(page);
    const result = await page.evaluate(() => {
      const api = (window as any).api, host = document.querySelector('main')!, origin = document.createElement('div'); host.append(origin);
      const row = api.createContent('row', { title: '受保护节点' }), bad = document.createElement('div'); origin.append(row, bad);
      let rejected = 0;
      for (const slots of [{ items: [row, bad] }, { items: [row], trail: [bad] }]) try { api.createContent('list', { label: 'x' }, slots); } catch { rejected++; }
      const listUnmoved = row.parentNode === origin && bad.parentNode === origin;
      const draft = document.querySelector('#draft') as HTMLTextAreaElement; draft.value = '真实的未保存草稿'; let events = 0; draft.addEventListener('input', () => events++);
      const parent = draft.parentNode;
      try { api.createContent('card', { title: 'x' }, { body: [draft], foot: ['raw HTML'] }); } catch { rejected++; }
      const draftUnmoved = draft.parentNode === parent;
      const box = document.createElement('div'), child = document.createElement('span'); box.append(child); origin.append(box);
      try { api.createContent('card', { title: 'x' }, { body: [box], foot: [child] }); } catch { rejected++; }
      const overlapUnmoved = child.parentNode === box && box.parentNode === origin;
      const card = api.createContent('card', { title: 'Node 内容', id: 'node-card' }, { body: [draft] }); host.append(card);
      draft.dispatchEvent(new Event('input', { bubbles: true }));
      const image = document.createElement('img'); image.src = '/image.png'; image.alt = '作业图片'; image.dataset.lsLightbox = ''; image.dataset.lsLightboxGroup = 'message-1';
      const bubble = api.createContent('bubble', { author: '同学', time: '14:20', id: 'image-bubble' }, { content: [image] }); host.append(bubble);
      return { rejected, listUnmoved, draftUnmoved, overlapUnmoved, sameDraft: card.querySelector('textarea') === draft, value: draft.value, events, sameImage: bubble.querySelector('img') === image, lightbox: image.dataset.lsLightboxGroup };
    });
    expect(result).toEqual({ rejected: 4, listUnmoved: true, draftUnmoved: true, overlapUnmoved: true, sameDraft: true, value: '真实的未保存草稿', events: 1, sameImage: true, lightbox: 'message-1' });
    await expect(page.locator('#compat-head')).toHaveAttribute('data-page-head', '');
    await expect(page.locator('#compat-head .page-head__aside #compat-aside')).toHaveText('调用者摘要');
    await expect(page.locator('#compat-filter .lq-filter-bar__controls select')).toBeVisible();
  });

  test('group slots validate every group before moving and keep native named list relations', async ({ page }) => {
    await mount(page);
    const result = await page.evaluate(() => {
      const api = (window as any).api, origin = document.createElement('div'); document.querySelector('main')!.append(origin);
      const a = api.createContent('row', { title: 'A' }), b = api.createContent('row', { title: 'B' }), bad = document.createElement('span'); origin.append(a, b, bad);
      const props = { id: 'slot-groups', label: '节点分组', groups: [{ key: 'a', title: '一组' }, { key: 'b', title: '二组' }] };
      let rejected = false; try { api.createContent('list', props, { 'items:a': [a], 'items:b': [bad] }); } catch { rejected = true; }
      const unchanged = a.parentNode === origin && bad.parentNode === origin;
      const swipe = api.createContent('row', { id: 'divider-row', title: '分隔线', swipe: { key: 'delete', label: '删除' } });
      const list = api.createContent('list', props, { 'items:a': [a, swipe], 'items:b': [b] }); origin.append(list);
      return { rejected, unchanged, same: list.querySelector('li.lq-row') === a, divider: getComputedStyle(swipe.querySelector('.lq-row__front'), '::before').borderTopWidth, groups: [...list.querySelectorAll('.lq-list__items')].map(el => ({ tag: el.tagName, role: el.getAttribute('role'), name: document.getElementById(el.getAttribute('aria-labelledby')!)?.textContent, parent: el.parentElement?.tagName })) };
    });
    expect(result).toEqual({ rejected: true, unchanged: true, same: true, divider: '1px', groups: [{ tag: 'UL', role: 'list', name: '一组', parent: 'LI' }, { tag: 'UL', role: 'list', name: '二组', parent: 'LI' }] });
    await page.evaluate(() => {
      const host = document.createElement('div'); host.id = 'scroll-groups'; host.style.cssText = 'height:240px;overflow:auto';
      host.append((window as any).api.createContent('list', { id: 'sticky-groups', label: '滚动分组', groups: [{ key: 'first', title: '第一组', items: Array.from({ length: 12 }, (_, i) => ({ title: `条目 ${i}` })) }, { key: 'second', title: '第二组', items: [{ title: '尾项' }] }] })); document.querySelector('main')!.prepend(host); host.scrollTop = 100;
    });
    const sticky = await page.locator('#sticky-groups .lq-list__heading').first().evaluate(el => ({ top: el.getBoundingClientRect().top, ownerTop: document.querySelector('#scroll-groups')!.getBoundingClientRect().top, font: getComputedStyle(el).fontSize, weight: getComputedStyle(el).fontWeight, position: getComputedStyle(el).position }));
    expect(sticky.font).toBe('12px'); expect(sticky.weight).toBe('600'); expect(sticky.position).toBe('sticky'); expect(Math.abs(sticky.top - sticky.ownerTop)).toBeLessThanOrEqual(1);
  });

  test('390 coarse layout retains long text, visible actions/time and no document overflow', async ({ browser }) => {
    const context = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
    try {
      const page = await context.newPage(); await mount(page, 'element');
      await page.evaluate(() => {
        const api = (window as any).api, host = document.querySelector('main')!;
        host.append(api.createContent('card', { id: 'long-card', title: 'VeryLongUnbrokenCourseTitle'.repeat(16), meta: '非常长的描述'.repeat(22), primary: { id: 'long-primary' }, actions: [{ id: 'long-secondary', label: '说明文字非常长的次要操作'.repeat(5) }] }));
        host.append(api.createContent('list', { label: '长行', items: [{ title: 'LongAttachmentFileName'.repeat(15), meta: '没有缩略图和状态', actions: [{ label: '更多操作' }] }] }));
      });
      expect(await page.evaluate(() => matchMedia('(pointer: coarse)').matches)).toBe(true);
      await expect(page.locator('#long-secondary')).toBeVisible();
      await expect(page.locator('#bubble-out time')).toBeVisible();
      for (const selector of ['#long-primary', '#long-secondary', '#secondary-action']) expect(await page.locator(selector).evaluate(el => el.getBoundingClientRect().height)).toBeGreaterThanOrEqual(44);
      const overflow = await page.evaluate(() => ({ width: innerWidth, scroll: document.documentElement.scrollWidth, nodes: [...document.querySelectorAll('main *')].filter(el => el.getBoundingClientRect().right > innerWidth + 1).slice(0, 12).map(el => ({ tag: el.tagName, id: el.id, class: el.className, width: el.getBoundingClientRect().width, min: getComputedStyle(el).minWidth })) }));
      expect(overflow.width).toBe(390);
      expect(overflow.scroll, JSON.stringify(overflow)).toBeLessThanOrEqual(overflow.width);
      await page.locator('#long-secondary').tap(); expect(await page.evaluate(() => (window as any).clicks.at(-1))).toBe('long-secondary');
      await page.screenshot({ path: '.codex-temp/lq-content-mobile.png', fullPage: true });
    } finally { await context.close(); }
  });

  test('reduced and forced colors retain content and focus with no blur', async ({ page }) => {
    await mount(page); await page.emulateMedia({ reducedMotion: 'reduce' });
    await expect(page.locator('#card-link')).toHaveCSS('transition-property', 'none');
    await page.emulateMedia({ forcedColors: 'active' }); await page.locator('#primary-link').focus();
    await expect(page.locator('#primary-link')).toHaveCSS('outline-style', 'solid'); await expect(page.locator('#bubble-out time')).toBeVisible();
    await expect(page.locator('#card-link')).toHaveCSS('backdrop-filter', 'none');
  });

  for (const palette of palettes) for (const appearance of ['light', 'dark']) test(`axe whole fixture ${palette}/${appearance} desktop and mobile`, async ({ page }) => {
    const declared = [...new Set([...fs.readFileSync('static/css/lq/tokens.css', 'utf8').matchAll(/data-ui-palette="([^"]+)"/g)].map(match => match[1]))];
    expect(palettes.slice().sort()).toEqual(declared.sort());
    expect(createHash('sha256').update(fs.readFileSync('static/css/lq/tokens.css')).digest('hex')).toBe(tokens.source_sha256);
    await mount(page, 'jinja', palette, appearance);
    const colors = await page.evaluate(expected => {
      const probe = document.createElement('span'); document.body.append(probe);
      const rgb = (value: string) => { probe.style.color = `hsl(${value})`; return getComputedStyle(probe).color; };
      const root = getComputedStyle(document.documentElement), result = { actual: rgb(root.getPropertyValue('--ls-primary')), expected: rgb(expected['--ls-primary']) }; probe.remove(); return result;
    }, tokens.themes[palette][appearance]);
    expect(colors.actual).toBe(colors.expected);
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: 980 });
      const scan = await new AxeBuilder({ page }).analyze();
      expect(scan.violations.map(item => ({ id: item.id, nodes: item.nodes.map(node => ({ target: node.target, summary: node.failureSummary })) })), `${palette}/${appearance}/${width}`).toEqual([]);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    }
  });
});

test.describe('LQ grouped list and mobile swipe intents', () => {
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
  async function enhance(page: Page, entry = 'jinja') {
    await mount(page, entry);
    await page.evaluate(() => {
      const w = window as any; w.intents = []; w.errors = [];
      w.row = w.api.enhanceRow(document.querySelector('#swipe-row'), { onAction: (intent: any) => { w.intents.push(intent); return new Promise((resolve, reject) => { w.resolveIntent = resolve; w.rejectIntent = reject; }); }, onError: (error: Error) => w.errors.push(error.message) });
    });
    await page.locator('#swipe-row').scrollIntoViewIfNeeded();
  }
  async function touchDrag(page: Page, dx: number, dy = 0, cancel = false) {
    const box = await page.locator('#swipe-row .lq-row__main').boundingBox(); if (!box) throw new Error('Missing Row');
    const x = box.x + box.width / 2, y = box.y + box.height / 2;
    const cdp = await page.context().newCDPSession(page);
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x, y }] });
    for (let i = 1; i <= 5; i++) await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: x + dx * i / 5, y: y + dy * i / 5 }] });
    await cdp.send('Input.dispatchTouchEvent', { type: cancel ? 'touchCancel' : 'touchEnd', touchPoints: [] }); return cdp;
  }
  for (const entry of ['jinja', 'html', 'element']) test(`${entry}: keyboard reveal, one intent, pending guard and disabled state`, async ({ page }) => {
    await enhance(page, entry);
    const root = page.locator('#swipe-row'), toggle = root.locator('[data-lq-row-reveal]'), action = root.locator('[data-lq-row-action]');
    await expect(root.locator('.lq-row__swipe-actions')).toHaveAttribute('inert', '');
    await toggle.focus(); await page.keyboard.press('Enter'); await expect(action).toBeFocused();
    await expect.poll(() => toggle.evaluate(el => { const a = el.getBoundingClientRect(), b = el.closest('.lq-row')!.getBoundingClientRect(); return a.left >= b.left && a.right <= b.right; })).toBe(true);
    expect(await toggle.evaluate(el => el.getBoundingClientRect().height)).toBeGreaterThanOrEqual(44);
    await page.keyboard.press('Space'); await expect(action).toBeDisabled();
    await page.evaluate(() => { const button = document.querySelector('#swipe-row [data-lq-row-action]') as HTMLButtonElement; button.click(); button.dispatchEvent(new MouseEvent('click', { bubbles: true })); });
    expect(await page.evaluate(() => (window as any).intents)).toEqual([{ key: 'delete', rowId: 'swipe-row' }]);
    await page.evaluate(() => { (window as any).row.refresh({ disabled: true }); (window as any).resolveIntent(); });
    await expect(root).toHaveAttribute('data-lq-row-busy', 'false'); await expect(action).toBeDisabled(); await expect(root).toHaveAttribute('data-lq-row-open', 'false');
    await page.evaluate(() => (window as any).row.refresh({ disabled: false })); await toggle.focus(); await page.keyboard.press('Enter'); await page.keyboard.press('Escape'); await expect(toggle).toBeFocused();
    expect(await page.evaluate(() => (window as any).submissions)).toBe(0);
    await expect(page.locator('#swipe-disabled [data-lq-row-action]')).toBeDisabled(); await expect(page.locator('#swipe-busy [data-lq-row-action]')).toBeDisabled();
  });

  test('real touch direction lock, cancellation, scroll and RTL never create an action', async ({ page }) => {
    await enhance(page);
    await page.evaluate(() => { (window as any).pointerLog = []; for (const name of ['pointerdown','pointermove','pointerup','pointercancel','lostpointercapture']) document.querySelector('#swipe-row')!.addEventListener(name, (e: any) => (window as any).pointerLog.push({ type: e.type, target: e.target.className, x: e.clientX, y: e.clientY, primary: e.isPrimary, pointer: e.pointerType, button: e.button, open: document.querySelector('#swipe-row')!.getAttribute('data-lq-row-open') })); });
    await touchDrag(page, -105); await expect(page.locator('#swipe-row'), JSON.stringify(await page.evaluate(() => (window as any).pointerLog))).toHaveAttribute('data-lq-row-open', 'true');
    expect(await page.evaluate(() => (window as any).intents.length)).toBe(0);
    await page.evaluate(() => (window as any).row.close()); await touchDrag(page, -90, 0, true);
    await expect(page.locator('#swipe-row')).toHaveAttribute('data-lq-row-open', 'false');
    await expect(page.locator('#swipe-row .lq-row__front')).toHaveCSS('transform', 'matrix(1, 0, 0, 1, 0, 0)');
    const before = await page.evaluate(() => scrollY); await touchDrag(page, 4, -130);
    expect(await page.evaluate(() => scrollY)).toBeGreaterThan(before + 20); await expect(page.locator('#swipe-row')).toHaveAttribute('data-lq-row-open', 'false');
    expect(await page.evaluate(() => (window as any).intents.length)).toBe(0);
  });
  test('fresh RTL touch reveals and native tap emits one recoverable intent', async ({ page }) => {
    await enhance(page); await page.evaluate(() => document.querySelector('#swipe-row')!.setAttribute('dir', 'rtl'));
    const cdp = await page.context().newCDPSession(page), area = await page.locator('#swipe-row .lq-row__main').boundingBox(); if (!area) throw Error('Missing Row');
    await cdp.send('Input.synthesizeScrollGesture', { x: area.x + area.width / 2, y: area.y + area.height / 2, xDistance: 105, yDistance: 0, speed: 500, preventFling: true, gestureSourceType: 'touch' });
    await expect(page.locator('#swipe-row')).toHaveAttribute('data-lq-row-open', 'true');
    const revealWidth = await page.locator('#swipe-row .lq-row__swipe-actions').evaluate(el => el.getBoundingClientRect().width);
    await expect(page.locator('#swipe-row .lq-row__front')).toHaveCSS('transform', `matrix(1, 0, 0, 1, ${revealWidth}, 0)`);
    await page.locator('#swipe-row [data-lq-row-action]').tap();
    await expect.poll(() => page.evaluate(() => (window as any).intents.length)).toBe(1);
    await page.evaluate(() => (window as any).rejectIntent(new Error('控制器失败'))); await expect(page.locator('#swipe-row')).toHaveAttribute('data-lq-row-busy', 'false');
    expect(await page.evaluate(() => (window as any).errors)).toEqual(['控制器失败']);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });
  test('native tap reveal uses same action without a preceding gesture', async ({ page }) => {
    await enhance(page); await page.locator('#swipe-row [data-lq-row-reveal]').tap();
    await expect(page.locator('#swipe-row')).toHaveAttribute('data-lq-row-open', 'true');
    await page.locator('#swipe-row [data-lq-row-action]').tap(); await expect.poll(() => page.evaluate(() => (window as any).intents.length)).toBe(1);
  });

  test('20 mount cycles restore nodes, styles, drafts and listeners; pending destroy is inert', async ({ page }) => {
    await mount(page);
    const result = await page.evaluate(async () => {
      const w = window as any, root = document.querySelector('#swipe-row')!, front = root.querySelector('.lq-row__front')!;
      const input = document.createElement('input'); input.value = '保留的草稿'; input.setAttribute('aria-label', '草稿'); front.append(input);
      const original = root.outerHTML; let calls = 0, changes = 0; input.addEventListener('input', () => changes++);
      const listeners: any[] = [], observers = new Set(), nativeAdd = EventTarget.prototype.addEventListener, nativeRemove = EventTarget.prototype.removeEventListener, NativeObserver = window.ResizeObserver;
      EventTarget.prototype.addEventListener = function(type: string, listener: any, options: any) { if (this === root || this === front || this instanceof MediaQueryList) listeners.push({ target: this, type, listener }); nativeAdd.call(this, type, listener, options); };
      EventTarget.prototype.removeEventListener = function(type: string, listener: any, options: any) { const i = listeners.findIndex(item => item.target === this && item.type === type && item.listener === listener); if (i >= 0) listeners.splice(i, 1); nativeRemove.call(this, type, listener, options); };
      window.ResizeObserver = class extends NativeObserver { observe(el: Element) { observers.add(this); super.observe(el); } disconnect() { observers.delete(this); super.disconnect(); } };
      for (let i = 0; i < 20; i++) {
        const row = w.api.enhanceRow(root, { onAction: () => { calls++; } });
        if (row !== w.api.enhanceRow(root, { onAction: () => { calls += 1000; } })) throw Error('Duplicate owner');
        row.reveal(); (root.querySelector('[data-lq-row-action]') as HTMLButtonElement).click();
        await new Promise(resolve => setTimeout(resolve, 0)); row.destroy(); row.destroy();
        if (root.outerHTML.replaceAll(' style=""', '') !== original) throw Error(`State not restored: ${JSON.stringify({ i, original, after: root.outerHTML })}`);
        if ((front as HTMLElement).style.getPropertyValue('--lq-row-shift')) throw Error('Leaked gesture style');
        (root.querySelector('[data-lq-row-action]') as HTMLButtonElement).click();
      }
      let release: any; const row = w.api.enhanceRow(root, { onAction: () => { calls++; return new Promise(resolve => { release = resolve; }); } }); row.reveal(); (root.querySelector('[data-lq-row-action]') as HTMLButtonElement).click(); await Promise.resolve(); row.destroy(); release(); await new Promise(resolve => setTimeout(resolve, 0)); input.dispatchEvent(new Event('input'));
      EventTarget.prototype.addEventListener = nativeAdd; EventTarget.prototype.removeEventListener = nativeRemove; window.ResizeObserver = NativeObserver;
      return { calls, changes, listeners: listeners.length, observers: observers.size, same: root.contains(input), value: input.value, restored: root.outerHTML.replaceAll(' style=""', '') === original, rowCount: document.querySelectorAll('#swipe-row').length };
    });
    expect(result).toEqual({ calls: 21, changes: 1, listeners: 0, observers: 0, same: true, value: '保留的草稿', restored: true, rowCount: 1 });
  });

  test('desktop equivalence, no-JS action, breakpoint and reduced/forced colors', async ({ page }) => {
    await mount(page); await expect(page.locator('#swipe-row [data-lq-row-action]')).toBeVisible(); await expect(page.locator('#swipe-row [data-lq-row-reveal]')).toBeHidden();
    await page.evaluate(() => { const w = window as any; w.count = 0; w.row = w.api.enhanceRow(document.querySelector('#swipe-row'), { onAction: () => w.count++ }); w.row.reveal(true); });
    await page.setViewportSize({ width: 1024, height: 844 }); await expect(page.locator('#swipe-row')).toHaveAttribute('data-lq-row-open', 'false');
    await expect(page.locator('#swipe-row .lq-row__swipe-actions')).not.toHaveAttribute('inert', '');
    await page.locator('#swipe-row [data-lq-row-action]').focus(); await page.keyboard.press('Enter'); expect(await page.evaluate(() => (window as any).count)).toBe(1);
    await page.setViewportSize({ width: 390, height: 844 }); await page.emulateMedia({ reducedMotion: 'reduce', forcedColors: 'active' });
    await page.evaluate(() => (window as any).row.reveal(true)); await expect(page.locator('#swipe-row .lq-row__front')).toHaveCSS('transition-property', 'none');
    await expect(page.locator('#swipe-row [data-lq-row-action]')).toHaveCSS('outline-style', 'solid');
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });

  test('initial disabled and busy states refresh without stale disabled styling or unsafe props', async ({ page }) => {
    await mount(page);
    const result = await page.evaluate(() => {
      const api = (window as any).api, root = document.querySelector('#swipe-disabled')!, original = root.outerHTML;
      const handle = api.enhanceRow(root, { onAction() {} }); let rejected = 0;
      for (const state of [null, { disabled: 1 }, { busy: null }, { html: 'x' }]) try { handle.refresh(state); } catch (error) { if (error instanceof TypeError) rejected++; }
      const blocked = handle.reveal() === false; handle.refresh({ disabled: false }); handle.reveal();
      const enabled = !root.querySelector('button')!.disabled && !root.querySelector('button')!.classList.contains('is-disabled'); handle.destroy();
      return { rejected, blocked, enabled, restored: root.outerHTML.replaceAll(' style=""', '') === original };
    });
    expect(result).toEqual({ rejected: 4, blocked: true, enabled: true, restored: true });
  });

  for (const palette of palettes) for (const appearance of ['light', 'dark']) test(`mobile revealed action axe ${palette}/${appearance}`, async ({ page }) => {
    await mount(page, 'jinja', palette, appearance);
    expect(createHash('sha256').update(fs.readFileSync('static/css/lq/tokens.css')).digest('hex')).toBe(tokens.source_sha256);
    expect(await page.evaluate(expected => { const probe = document.createElement('span'); document.body.append(probe); probe.style.color = 'hsl(var(--ls-primary))'; const actual = getComputedStyle(probe).color; probe.style.color = `hsl(${expected})`; const correct = getComputedStyle(probe).color; probe.remove(); return actual === correct; }, tokens.themes[palette][appearance]['--ls-primary'])).toBe(true);
    await page.evaluate(() => { const w = window as any; w.row = w.api.enhanceRow(document.querySelector('#swipe-row'), { onAction: () => {} }); });
    for (const opened of [false, true]) {
      if (opened) await page.evaluate(() => (window as any).row.reveal(true));
      const scan = await new AxeBuilder({ page }).analyze(); expect(scan.violations.map(v => ({ id: v.id, nodes: v.nodes.map(n => n.target) }))).toEqual([]);
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    if (palette === 'rose' && appearance === 'dark') { await page.locator('#swipe-row').scrollIntoViewIfNeeded(); await page.screenshot({ path: '.codex-temp/lq-content-swipe-dark-mobile.png' }); }
  });
});


