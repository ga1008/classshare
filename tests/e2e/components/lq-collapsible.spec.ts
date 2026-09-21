import { test, expect, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';

const python = process.env.LQ_TEST_PYTHON || (process.platform === 'win32' ? 'venv/Scripts/python.exe' : 'venv/bin/python');
const fixture = JSON.parse(execFileSync(python, ['tests/e2e/scripts/render_lq_collapsible.py'], { encoding: 'utf8' }));
async function mount(page: Page, { enhance = true, entry = 'jinja', filters = false } = {}) {
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin !== 'https://lq-collapsible.test') return route.abort();
    if (url.pathname.startsWith('/static/js/') || ['/static/css/tailwind-app.css', '/static/css/lq/components/collapsible.css'].includes(url.pathname)) {
      const file = path.resolve(`.${url.pathname}`);
      if (file.startsWith(path.resolve('static') + path.sep) && fs.existsSync(file)) return route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body: fs.readFileSync(file) });
    }
    if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh-CN" data-theme="lanshare" data-ui-palette="indigo" data-appearance="light"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>LQ Collapsible</title><link rel="stylesheet" href="/static/css/tailwind-app.css"><style>body{margin:0}main{max-width:800px;margin:auto;padding:24px;display:grid;gap:16px}textarea{display:block;max-width:100%}.test-filter-row{max-width:100%;width:100%}</style></head><body><main><h1>折叠与筛选</h1><button id="before" type="button">外部操作</button></main><script type="module">import * as api from '/static/js/lq/collapsible.js';window.api=api;document.body.dataset.ready='true';</script></body></html>` });
    return route.abort();
  });
  await page.goto('https://lq-collapsible.test/'); await expect(page.locator('body')).toHaveAttribute('data-ready', 'true');
  await page.evaluate(({ fixture, entry, enhance, filters }) => {
    const api = (window as any).api, host = document.querySelector('main')!;
    for (const item of fixture.cases) {
      let el;
      if (entry === 'element') el = api.createCollapsible(item.props);
      else { const t = document.createElement('template'); t.innerHTML = entry === 'jinja' ? item.html : api.html.collapsible(item.props); el = t.content.firstElementChild; }
      host.append(el);
    }
    const label = document.createElement('label'); label.htmlFor = 'draft'; label.textContent = '草稿';
    const draft = document.createElement('textarea'); draft.id = 'draft'; draft.value = '不重挂的原始草稿';
    document.querySelector('#basic [data-lq-slot]')!.append(label, draft); (window as any).draftNode = draft;
    (window as any).handles = {};
    if (enhance) for (const root of document.querySelectorAll('[data-lq-collapsible]')) (window as any).handles[root.id] = api.enhanceCollapsible(root);
    if (filters) {
      const make = (identity: string, values: string[], chipValues: string[], overflow = '') => {
        const label = document.createElement('label'); label.htmlFor = `${identity}-select`; label.textContent = identity;
        const select = document.createElement('select'); select.id = `${identity}-select`; values.forEach(value => { const option = document.createElement('option'); option.value = value; option.textContent = value; select.append(option); });
        const row = document.createElement('div'); row.id = identity; row.className = 'filter-chips test-filter-row'; row.dataset.filterChips = ''; row.dataset.filterTarget = `#${select.id}`; row.setAttribute('aria-label', identity);
        if (overflow) row.dataset.filterOverflow = overflow;
        chipValues.forEach(value => { const button = document.createElement('button'); button.type = 'button'; button.className = 'filter-chip'; button.dataset.value = value; button.textContent = value; row.append(button); });
        host.append(label, select, row); return { row, select };
      };
      make('courses', ['all', 'active', 'complete', 'pending', 'idle'], ['all', 'active', 'complete', 'idle']);
      make('offerings', ['all', 'active', 'combined', 'missing-textbook', 'missing-ai', 'finished'], ['all', 'active', 'combined', 'missing-textbook', 'missing-ai', 'finished']);
      make('more-row', Array.from({ length: 11 }, (_, i) => `option-${i}`), Array.from({ length: 11 }, (_, i) => `option-${i}`), 'more');
      make('scroll-row', Array.from({ length: 11 }, (_, i) => `很长的筛选条件-${i}`), Array.from({ length: 11 }, (_, i) => `很长的筛选条件-${i}`), 'scroll');
    }
  }, { fixture, entry, enhance, filters });
  if (filters) await page.evaluate(async () => { const module = await import('/static/js/manage_filter_chips.js'); (window as any).filterModule = module; (window as any).filterManager = module.initFilterChips(); });
}

test.describe('LQ Collapsible and existing filter proxies', () => {
  test('real Jinja, normalized props, safe HTML and Element DOM match with escaped text', async ({ page }) => {
    expect(fixture.isolated).toBe(true); expect(fixture.cases.filter((item: any) => item.error)).toEqual([]);
    await mount(page, { enhance: false });
    const result = await page.evaluate(items => {
      const api = (window as any).api;
      const tree = (n: Node): any => n.nodeType === 3 ? (n.textContent?.trim() ? { text: n.textContent } : null) : ({ tag: (n as Element).tagName, attrs: Object.fromEntries([...(n as Element).attributes].map(a => [a.name, a.value]).sort()), children: [...n.childNodes].map(tree).filter(Boolean) });
      const parse = (html: string) => { const t = document.createElement('template'); t.innerHTML = html; return [...t.content.childNodes].map(tree).filter(Boolean); };
      return items.map((item: any) => ({ props: api.collapsibleProps(item.props), ssr: parse(item.html), html: parse(api.html.collapsible(item.props)), element: [tree(api.createCollapsible(item.props))] }));
    }, fixture.cases);
    result.forEach((item: any, i: number) => { expect(item.props).toEqual(fixture.cases[i].normalized); expect(item.html).toEqual(item.ssr); expect(item.element).toEqual(item.ssr); });
    expect(fixture.invalid.every((item: any) => item.error === 'ValueError')).toBe(true);
    const invalid = await page.evaluate(items => items.map((item: any) => ['collapsibleProps', 'collapsibleMarkup', 'createCollapsible'].map(method => { try { (window as any).api[method](item.props); return false; } catch (error) { return error instanceof TypeError; } })), fixture.invalid);
    invalid.forEach((item: boolean[]) => expect(item).toEqual([true, true, true]));
  });

  test('native details remains keyboard usable without enhancement', async ({ page }) => {
    await mount(page, { enhance: false });
    await page.locator('#basic > summary').focus(); await page.keyboard.press('Enter'); await expect(page.locator('#draft')).toBeHidden();
    await page.keyboard.press('Space'); await expect(page.locator('#draft')).toBeVisible();
    await expect(page.locator('#guarded')).toHaveAttribute('open', '');
  });

  for (const entry of ['jinja', 'html', 'element']) test(`${entry}: responsive boundary and explicit guards preserve draft identity and safe focus`, async ({ page }) => {
    await page.setViewportSize({ width: 767, height: 844 }); await mount(page, { entry });
    await page.locator('#draft').fill('输入到一半，必须保留'); await page.locator('#draft').focus();
    await page.evaluate(() => (window as any).handles.basic.setOpen(false));
    await expect(page.locator('#basic > summary')).toBeFocused(); await expect(page.locator('#draft')).toBeHidden();
    await page.setViewportSize({ width: 768, height: 844 }); await expect(page.locator('#draft')).toBeVisible(); await expect(page.locator('#basic > summary')).toHaveAttribute('aria-disabled', 'true');
    await page.locator('#basic > summary').click(); await expect(page.locator('#draft')).toBeVisible();
    await page.setViewportSize({ width: 767, height: 844 }); await expect(page.locator('#draft')).toBeHidden();
    for (const guard of ['keepOpen', 'hasError', 'current', 'dirty']) {
      await page.evaluate(guard => (window as any).handles.basic.refresh({ [guard]: true }), guard); await expect(page.locator('#draft')).toBeVisible();
      await page.locator('#basic > summary').press('Enter'); await expect(page.locator('#draft')).toBeVisible();
      await page.evaluate(guard => (window as any).handles.basic.refresh({ [guard]: false }), guard); await expect(page.locator('#draft')).toBeHidden();
    }
    await page.evaluate(() => (window as any).handles.basic.setOpen(true)); await expect(page.locator('#draft')).toHaveValue('输入到一半，必须保留');
    expect(await page.evaluate(() => document.querySelector('#draft') === (window as any).draftNode)).toBe(true);
  });

  test('scoped persistence restores only exact identity/resource/key and storage failures stay usable', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 }); await mount(page);
    await page.evaluate(() => (window as any).handles.saved.setOpen(false));
    expect(await page.evaluate(() => localStorage.getItem('lq.collapsible:["teacher:1","offering:2","metadata"]'))).toBe('0');
    const values = await page.evaluate(() => {
      const api = (window as any).api; const result = [];
      for (const [identity, resource, key] of [['teacher:1', 'offering:2', 'metadata'], ['teacher:2', 'offering:2', 'metadata'], ['teacher:1', 'offering:3', 'metadata'], ['teacher:1', 'offering:2', 'other']]) {
        const root = api.createCollapsible({ id: `restore-${result.length}`, title: 'Restore', persist: { identity, resource, key } }); document.querySelector('main')!.append(root); api.enhanceCollapsible(root); result.push(root.open);
      }
      Object.defineProperty(window, 'localStorage', { configurable: true, get() { throw new Error('blocked'); } });
      const root = api.createCollapsible({ id: 'blocked-store', title: 'Storage unavailable', persist: { identity: '1', resource: '2', key: '3' } }); document.querySelector('main')!.append(root); const handle = api.enhanceCollapsible(root); handle.setOpen(false); result.push(root.open); handle.setOpen(true); result.push(root.open); return result;
    });
    expect(values).toEqual([false, true, true, true, false, true]);
  });

  test('duplicate init/import and destroy retain nodes and leave native toggling usable', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 }); await mount(page);
    const result = await page.evaluate(async () => {
      const root = document.querySelector('#basic') as HTMLDetailsElement, first = (window as any).handles.basic;
      const duplicate = await import('/static/js/lq/collapsible.js?duplicate'); const second = duplicate.enhanceCollapsible(root);
      first.setOpen(false); first.destroy(); first.destroy();
      const native = !root.querySelector('summary')!.hasAttribute('aria-expanded');
      const third = duplicate.enhanceCollapsible(root); const afterReinit = root.open; third.destroy();
      return { same: first === second, native, afterReinit, sameDraft: document.querySelector('#draft') === (window as any).draftNode };
    });
    expect(result).toEqual({ same: true, native: true, afterReinit: false, sameDraft: true });
    await page.locator('#basic > summary').focus(); await page.keyboard.press('Enter'); await expect(page.locator('#draft')).toBeVisible();
    await page.evaluate(() => (window as any).api.enhanceCollapsible(document.querySelector('#basic')));
    await expect(page.locator('#draft')).toBeVisible();
  });

  test('native toggles persist a choice while forced desktop expansion and reinit do not overwrite it', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 }); await mount(page);
    const key = 'lq.collapsible:["teacher:1","offering:2","metadata"]';
    await expect.poll(() => page.evaluate(key => localStorage.getItem(key), key)).toBe(null);
    await page.locator('#saved > summary').click(); await expect(page.locator('#saved')).not.toHaveAttribute('open');
    await expect.poll(() => page.evaluate(key => localStorage.getItem(key), key)).toBe('0');
    await page.setViewportSize({ width: 768, height: 844 }); await expect(page.locator('#saved')).toHaveAttribute('open', '');
    await page.evaluate(() => { const w = window as any; w.handles.saved.destroy(); w.handles.saved = w.api.enhanceCollapsible(document.querySelector('#saved')); });
    expect(await page.evaluate(key => localStorage.getItem(key), key)).toBe('0');
    await page.setViewportSize({ width: 390, height: 844 }); await expect(page.locator('#saved')).not.toHaveAttribute('open');
  });

  test('real two filter shapes preserve select truth, aria and exactly one change after duplicate init', async ({ page }) => {
    await mount(page, { filters: true });
    await page.evaluate(async () => {
      const duplicate = await import('/static/js/manage_filter_chips.js?duplicate'); duplicate.initFilterChips(); (window as any).changes = 0;
      document.querySelector('#courses-select')!.addEventListener('change', () => (window as any).changes++);
    });
    await page.locator('#courses [data-value="active"]').click(); await page.locator('#courses [data-value="active"]').click();
    expect(await page.evaluate(() => (window as any).changes)).toBe(1); await expect(page.locator('#courses-select')).toHaveValue('active'); await expect(page.locator('#courses [data-value="active"]')).toHaveAttribute('aria-pressed', 'true');
    await page.selectOption('#courses-select', 'pending'); await expect(page.locator('#courses [aria-pressed="true"]')).toHaveCount(0); await expect(page.locator('#courses-select')).toHaveValue('pending');
    await page.locator('#offerings [data-value="missing-ai"]').focus(); await page.keyboard.press('Space'); await expect(page.locator('#offerings-select')).toHaveValue('missing-ai');
    await expect(page.locator('#courses [data-filter-more],#offerings [data-filter-more]')).toHaveCount(0);
  });

  test('filter lifecycle shares overlapping ownership, ignores disabled/invalid values, and destroys cleanly', async ({ page }) => {
    await mount(page, { filters: true });
    const result = await page.evaluate(() => {
      const w = window as any, group = document.querySelector('#courses')!, select = document.querySelector('#courses-select') as HTMLSelectElement;
      const nested = w.filterModule.initFilterChips(group); w.filterManager.destroy();
      const button = group.querySelector('[data-value="active"]') as HTMLButtonElement; button.click(); const owned = select.value;
      select.disabled = true; (group.querySelector('[data-value="idle"]') as HTMLButtonElement).click(); const disabled = select.value; select.disabled = false;
      const unknown = document.createElement('button'); unknown.className = 'filter-chip'; unknown.dataset.value = 'unauthorized'; group.append(unknown); nested.refresh(); unknown.click(); const invalid = select.value;
      nested.destroy(); nested.destroy(); (group.querySelector('[data-value="idle"]') as HTMLButtonElement).click();
      return { owned, disabled, invalid, after: select.value, pressedRemoved: !button.hasAttribute('aria-pressed') };
    });
    expect(result).toEqual({ owned: 'active', disabled: 'active', invalid: 'active', after: 'active', pressedRemoved: true });
  });

  test('opt-in more preserves selected extra and scroll mode keeps keyboard focus visible without page overflow', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 }); await mount(page, { filters: true });
    await expect(page.locator('#more-row .filter-chip:visible')).toHaveCount(8); await expect(page.locator('#more-row [data-filter-more]')).toHaveText('更多 (3)');
    await page.locator('#more-row [data-filter-more]').click(); await expect(page.locator('#more-row .filter-chip:visible')).toHaveCount(11);
    await page.locator('#more-row [data-value="option-10"]').click(); await page.locator('#more-row [data-filter-more]').click();
    await expect(page.locator('#more-row [data-value="option-10"]')).toBeVisible(); await expect(page.locator('#more-row [data-value="option-10"]')).toHaveAttribute('aria-pressed', 'true');
    await expect(page.locator('#more-row [data-filter-more]')).toHaveText('更多 (2)');
    await page.locator('#scroll-row button').last().focus();
    await expect.poll(() => page.locator('#scroll-row').evaluate(el => { const box = el.getBoundingClientRect(), focused = document.activeElement!.getBoundingClientRect(); return focused.left >= box.left - 1 && focused.right <= box.right + 1; })).toBe(true);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });

  test('coarse pointer has actual 44px summary, more and scrolling filter targets', async ({ browser }) => {
    const context = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
    try {
      const page = await context.newPage(); await mount(page, { filters: true });
      expect(await page.evaluate(() => matchMedia('(pointer: coarse)').matches)).toBe(true);
      for (const selector of ['#basic > summary', '#more-row [data-filter-more]', '#scroll-row button']) {
        for (const height of await page.locator(selector).evaluateAll(elements => elements.map(el => el.getBoundingClientRect().height))) expect(height).toBeGreaterThanOrEqual(44);
      }
      await page.locator('#basic > summary').tap(); await expect(page.locator('#draft')).toBeHidden();
    } finally { await context.close(); }
  });

  for (const appearance of ['light', 'dark']) test(`axe/native geometry and reduced/forced colors ${appearance}`, async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 980 }); await mount(page, { filters: true });
    await page.evaluate(appearance => { document.documentElement.dataset.appearance = appearance; }, appearance);
    const selectedColors = await page.evaluate(() => {
      const probe = document.createElement('span'); document.body.append(probe);
      probe.style.color = 'hsl(var(--ls-primary))'; const background = getComputedStyle(probe).color;
      probe.style.color = 'hsl(var(--ls-on-primary))'; const foreground = getComputedStyle(probe).color;
      probe.remove(); return { background, foreground };
    });
    await expect(page.locator('#courses [aria-pressed="true"]')).toHaveCSS('background-color', selectedColors.background);
    await expect(page.locator('#courses [aria-pressed="true"]')).toHaveCSS('color', selectedColors.foreground);
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
    expect(await page.locator('#basic > summary').evaluate(el => el.getBoundingClientRect().height)).toBeGreaterThanOrEqual(44);
    await page.emulateMedia({ reducedMotion: 'reduce' }); await expect(page.locator('#basic .lq-collapsible__indicator')).toHaveCSS('transition-property', 'none');
    await page.emulateMedia({ forcedColors: 'active' }); await page.locator('#basic > summary').focus(); await expect(page.locator('#basic > summary')).toHaveCSS('outline-style', 'solid');
    await expect(page.locator('#basic')).toHaveCSS('backdrop-filter', 'none');
  });
});
