import { test, expect, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';

const fixture = JSON.parse(execFileSync(process.platform === 'win32' ? 'venv/Scripts/python.exe' : 'venv/bin/python', ['tests/e2e/scripts/render_lq_chip_row_progress.py'], { encoding: 'utf8' }));
async function mount(page: Page, ssr = false) {
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin !== 'https://lq-chip-row.test') return route.abort();
    if (url.pathname.startsWith('/static/')) {
      const file = path.resolve(`.${url.pathname}`);
      if (file.startsWith(path.resolve('static') + path.sep) && fs.existsSync(file)) return route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body: fs.readFileSync(file) });
    }
    if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh-CN" data-theme="lanshare" data-appearance="light" data-ui-palette="teal"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>LQ ChipRow Progress</title><link rel="stylesheet" href="/static/css/tailwind-app.css"><style>body{margin:0}main{padding:16px;box-sizing:border-box;max-width:100%}#fixture{display:grid;gap:24px}h1{margin-bottom:16px}</style></head><body><main><h1>筛选与进度</h1><div id="fixture">${ssr ? fixture.cases[0].html : ''}</div></main><script type="module">import * as row from '/static/js/lq/chip-row.js';import * as components from '/static/js/lq/components.js';import {componentProps} from '/static/js/lq/component-props.js';window.api={row,components,componentProps};document.body.dataset.ready='true';</script></body></html>` });
    return route.abort();
  });
  await page.goto('https://lq-chip-row.test/'); if (!ssr) await expect(page.locator('body')).toHaveAttribute('data-ready', 'true');
}
async function insert(page: Page, entry = 'element', bind = true) {
  await page.evaluate(({ fixture, entry, bind }) => {
    const w = window as any, host = document.getElementById('fixture')!, c = fixture.cases[0], api = w.api.row;
    if (entry === 'jinja') host.innerHTML = c.html;
    else if (entry === 'html') host.innerHTML = api.chipRowMarkup(c.props);
    else host.append(api.createChipRow(c.props));
    if (bind) w.handle = api.bindChipRow(host.firstElementChild);
  }, { fixture, entry, bind });
}

test('LQ ChipRow and ring typed real Jinja HTML Element entries share valid and invalid contracts', async ({ page }) => {
  expect(fixture.isolated).toBe(true); expect(fixture.cases.filter((c: any) => c.error)).toEqual([]);
  expect(fixture.invalid.filter((c: any) => !c.error)).toEqual([]);
  await mount(page);
  const result = await page.evaluate(f => {
    const { row, components, componentProps } = (window as any).api;
    function semantic(n: Node): any {
      if (n.nodeType === 3) return n.textContent?.trim() ? { text: n.textContent } : null;
      const el = n as Element; return { tag: el.tagName.toLowerCase(), attrs: Object.fromEntries([...el.attributes].map(a => [a.name, a.value]).sort()), children: [...n.childNodes].map(semantic).filter(Boolean) };
    }
    const parse = (html: string) => { const t = document.createElement('template'); t.innerHTML = html; return [...t.content.childNodes].map(semantic).filter(Boolean); };
    return { cases: f.cases.map((c: any) => c.kind === 'chip_row'
      ? { normalized: row.chipRowProps(c.props), html: parse(row.chipRowMarkup(c.props)), element: [semantic(row.createChipRow(c.props))], jinja: parse(c.html) }
      : { normalized: componentProps('progress', c.props), html: parse(components.componentMarkup('progress', c.props)), element: [semantic(components.createComponent('progress', c.props))], jinja: parse(c.html) }),
      invalid: f.invalid.map((c: any) => {
        try { c.kind === 'chip_row' ? row.createChipRow(c.props) : components.createComponent('progress', c.props); return false; } catch { return true; }
      }) };
  }, fixture);
  result.cases.forEach((c: any, i: number) => { expect(c.normalized).toEqual(fixture.cases[i].normalized); expect(c.html).toEqual(c.jinja); expect(c.element).toEqual(c.jinja); });
  expect(result.invalid).toEqual(fixture.invalid.map(() => true));
});

for (const entry of ['jinja', 'html', 'element']) test(`LQ ${entry} ChipRow no-JS fallback disclosure keyboard focus and selected state`, async ({ page }) => {
  await mount(page); await insert(page, entry, false);
  await expect(page.locator('.lq-chip:visible')).toHaveCount(12); await expect(page.locator('.lq-chip-row__disclosure')).toBeHidden();
  await page.evaluate(() => { (window as any).handle = (window as any).api.row.bindChipRow(document.querySelector('.lq-chip-row')); });
  await expect(page.locator('.lq-chip:visible')).toHaveCount(8);
  const more = page.getByRole('button', { name: '更多 (4)', exact: true }); await more.focus(); await page.keyboard.press('Enter');
  await expect(page.locator('.lq-chip:visible')).toHaveCount(12); await expect(page.getByRole('button', { name: '收起' })).toHaveAttribute('aria-expanded', 'true');
  await page.getByRole('button', { name: '课程 10', exact: true }).focus();
  await page.evaluate(() => (window as any).handle.setExpanded(false));
  await expect(more).toBeFocused(); await expect(page.locator('.lq-chip').nth(9)).toHaveAttribute('aria-pressed', 'true');
  await page.keyboard.press('Space'); await expect(page.locator('.lq-chip:visible')).toHaveCount(12);
  await page.evaluate(() => (window as any).handle.destroy());
  await expect(page.locator('.lq-chip:visible')).toHaveCount(12); await expect(page.locator('.lq-chip-row__track')).toBeFocused();
  await expect(page.locator('.lq-chip-row__disclosure')).toBeHidden();
});

test('LQ ChipRow never reparents form controls or iframe and expands native invalid target', async ({ page }) => {
  await mount(page); await insert(page, 'element', false);
  await page.evaluate(async () => {
    const w = window as any, row = document.querySelector('.lq-chip-row')!, host = row.parentElement!, form = document.createElement('form');
    host.append(form); form.append(row); const track = row.firstElementChild!;
    const item = document.createElement('span'); item.className = 'lq-chip lq-chip--tag';
    const label = document.createElement('label'); label.textContent = '必填草稿';
    const input = document.createElement('input'); input.name = 'draft'; input.required = true; label.append(input);
    const frame = document.createElement('iframe'); frame.title = '原文档'; frame.srcdoc = '<input id="draft" value="iframe 草稿">';
    item.append(label, frame); track.append(item); await new Promise<void>(resolve => frame.addEventListener('load', () => resolve(), { once: true }));
    w.original = { item, input, frame, frameDocument: frame.contentDocument, form }; w.frameLoads = 0;
    frame.addEventListener('load', () => w.frameLoads++); w.submits = 0; form.addEventListener('submit', e => { e.preventDefault(); w.submits++; });
    w.handle = w.api.row.bindChipRow(row); form.requestSubmit();
  });
  await expect(page.getByRole('textbox', { name: '必填草稿' })).toBeFocused();
  expect(await page.evaluate(() => (window as any).handle.expanded)).toBe(true);
  await page.getByRole('textbox', { name: '必填草稿' }).fill('保留草稿');
  const result = await page.evaluate(() => {
    const w = window as any, o = w.original;
    for (let i = 0; i < 20; i++) { w.handle.setExpanded(false); w.handle.setExpanded(true); }
    w.handle.destroy();
    return { sameParent: o.item.parentElement === document.querySelector('.lq-chip-row__track'), sameInput: o.input.form === o.form,
      draft: new FormData(o.form).get('draft'), sameDocument: o.frame.contentDocument === o.frameDocument, frameLoads: w.frameLoads, submits: w.submits };
  });
  expect(result).toEqual({ sameParent: true, sameInput: true, draft: '保留草稿', sameDocument: true, frameLoads: 0, submits: 0 });
});

test('LQ ChipRow dynamic count and root removal release shared cross-URL resources over 20 cycles', async ({ page }) => {
  await mount(page);
  const result = await page.evaluate(async () => {
    const w = window as any, a = w.api.row, b = await import('/static/js/lq/chip-row.js?second-owner'), host = document.getElementById('fixture')!;
    const key = Symbol.for('lanshare.lq.chip-row-lifetime');
    let identity = true, shared = true, destroyed = true, mutations = 0, resizes = 0, peak = 0;
    const NativeMutation = window.MutationObserver, NativeResize = window.ResizeObserver;
    window.MutationObserver = class extends NativeMutation {
      active = false;
      observe(...args: Parameters<MutationObserver['observe']>) { super.observe(...args); if (!this.active) { this.active = true; mutations++; peak = Math.max(peak, mutations); } }
      disconnect() { super.disconnect(); if (this.active) { this.active = false; mutations--; } }
    };
    window.ResizeObserver = class extends NativeResize {
      active = false;
      observe(...args: Parameters<ResizeObserver['observe']>) { super.observe(...args); if (!this.active) { this.active = true; resizes++; } }
      disconnect() { super.disconnect(); if (this.active) { this.active = false; resizes--; } }
    };
    for (let i = 0; i < 20; i++) {
      const props = { id: `cycle${i}`, label: '标签', items: Array.from({ length: 10 }, (_, j) => ({ label: `${j}`, kind: 'filter' })) };
      const root = a.createChipRow(props), second = b.createChipRow({ ...props, id: `second${i}` }); host.append(root, second);
      const h = a.bindChipRow(root), h2 = b.bindChipRow(second); identity &&= b.bindChipRow(root) === h;
      shared &&= (document as any)[key].owners.size === 2;
      root.remove(); second.remove(); await new Promise(resolve => setTimeout(resolve, 0));
      destroyed &&= h.destroyed && h2.destroyed && !(document as any)[key];
    }
    window.MutationObserver = NativeMutation; window.ResizeObserver = NativeResize;
    return { identity, shared, destroyed, nodes: host.childElementCount, mutations, resizes, peak };
  });
  expect(result).toEqual({ identity: true, shared: true, destroyed: true, nodes: 0, mutations: 0, resizes: 0, peak: 1 });
  await insert(page);
  await page.evaluate(() => { const nodes = [...document.querySelectorAll('.lq-chip')]; nodes.slice(8).forEach(node => node.remove()); });
  await expect(page.locator('.lq-chip-row__disclosure')).toBeHidden();
});

test('LQ ChipRow genuine JavaScript-disabled SSR keeps every chip and scrolling reachable', async ({ browser }) => {
  const context = await browser.newContext({ javaScriptEnabled: false, viewport: { width: 390, height: 900 } }), page = await context.newPage();
  await mount(page, true);
  await expect(page.locator('.lq-chip:visible')).toHaveCount(12); await expect(page.locator('.lq-chip-row__disclosure')).toBeHidden();
  await page.locator('.lq-chip-row__track').focus(); await page.keyboard.press('End');
  await expect(page.locator('.lq-chip').last()).toBeVisible();
  expect(await page.locator('.lq-chip-row__track').evaluate(n => n.scrollWidth > n.clientWidth)).toBe(true);
  await context.close();
});

test('LQ ChipRow teardown during focus callback and invalid structures are atomic', async ({ page }) => {
  await mount(page); await insert(page);
  const result = await page.evaluate(() => {
    const w = window as any, root = document.querySelector('.lq-chip-row')!, more = root.querySelector('.lq-btn') as HTMLElement;
    w.handle.setExpanded(true); (root.querySelectorAll('.lq-chip')[9] as HTMLElement).focus();
    more.addEventListener('focus', () => w.handle.destroy(), { once: true }); w.handle.setExpanded(false);
    const restored = !root.querySelector('[data-lq-chip-collapsed]') && (root.querySelector('.lq-chip-row__disclosure') as HTMLElement).hidden;
    const invalid = document.createElement('div'); invalid.className = 'lq-chip-row'; root.after(invalid); const before = invalid.outerHTML;
    let threw = false; try { w.api.row.bindChipRow(invalid); } catch { threw = true; }
    return { destroyed: w.handle.destroyed, restored, threw, unchanged: before === invalid.outerHTML };
  });
  expect(result).toEqual({ destroyed: true, restored: true, threw: true, unchanged: true });
});

test('LQ progress ring real SVG geometry 0 full custom max unknown and reduced forced colors', async ({ page }) => {
  await mount(page);
  await page.evaluate(() => {
    const p = (window as any).api.components, host = document.getElementById('fixture')!;
    for (const value of [0, 100, null]) host.append(p.progress({ label: value === null ? '待确认' : `进度 ${value}`, variant: 'ring', value }));
    host.append(p.progress({ label: '三项中完成一项', variant: 'ring', max: 3, value: 1 }));
  });
  await expect(page.getByRole('progressbar', { name: '进度 0', exact: true })).toHaveAttribute('aria-valuenow', '0');
  await expect(page.getByRole('progressbar', { name: '进度 100' })).toHaveAttribute('aria-valuenow', '100');
  await expect(page.getByRole('progressbar', { name: '待确认' })).not.toHaveAttribute('aria-valuenow');
  await expect(page.getByRole('progressbar', { name: '待确认' })).toHaveText('…');
  const geometry = await page.locator('.lq-progress__fill').first().evaluate(n => ({ ns: n.namespaceURI, length: (n as SVGCircleElement).getTotalLength(), visible: getComputedStyle(n).visibility }));
  expect(geometry.ns).toBe('http://www.w3.org/2000/svg'); expect(geometry.length).toBeGreaterThan(99); expect(geometry.visible).toBe('hidden');
  await page.emulateMedia({ reducedMotion: 'reduce', forcedColors: 'active' });
  await expect(page.locator('.lq-progress__svg').nth(2)).toHaveCSS('animation-name', 'none');
  expect(await page.locator('.lq-progress__fill').nth(1).evaluate(n => getComputedStyle(n).stroke)).not.toBe('none');
});

for (const width of [390, 1440]) for (const appearance of ['light', 'dark']) test(`LQ ChipRow ring six palettes ${appearance} ${width} readable responsive axe`, async ({ page }) => {
  await page.setViewportSize({ width, height: 900 }); await mount(page); await insert(page);
  await page.evaluate(() => {
    const w = window as any, host = document.getElementById('fixture')!;
    host.append(w.api.components.progress({ variant: 'ring', label: '已完成上传', value: 65 }), w.api.components.progress({ variant: 'ring', label: '等待服务器确认' }));
  });
  for (const palette of ['teal', 'indigo', 'sky', 'mint', 'violet', 'rose']) {
    await page.evaluate(({ palette, appearance }) => { document.documentElement.dataset.uiPalette = palette; document.documentElement.dataset.appearance = appearance; }, { palette, appearance });
    const violations = (await new AxeBuilder({ page }).analyze()).violations.filter(v => ['serious', 'critical'].includes(v.impact || ''));
    expect(violations).toEqual([]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await expect(page.locator('.lq-progress--ring').first()).toHaveCSS('width', '64px');
  }
  await page.locator('.lq-chip-row__track').evaluate(n => { n.scrollLeft = n.scrollWidth; });
  if (width === 390) await expect(page.locator('.lq-chip-row__track')).toHaveAttribute('data-lq-fade-left', '');
  fs.mkdirSync('.codex-temp/lq-s2-chip-row-progress', { recursive: true });
  await page.screenshot({ path: `.codex-temp/lq-s2-chip-row-progress/${appearance}-${width}.png` });
});

test('LQ coarse ChipRow actions retain 44px targets and RTL scroll snap fade', async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 390, height: 900 }, hasTouch: true, isMobile: true }); const page = await context.newPage();
  await mount(page); await insert(page);
  await page.evaluate(() => { document.documentElement.dir = 'rtl'; });
  const rects = await page.locator('.lq-chip:visible,.lq-chip-row .lq-btn').evaluateAll(nodes => nodes.map(n => ({ w: n.getBoundingClientRect().width, h: n.getBoundingClientRect().height })));
  expect(rects.every(r => r.w >= 44 && r.h >= 44)).toBe(true);
  await expect(page.locator('.lq-chip-row__track')).toHaveCSS('scroll-snap-type', 'x');
  await page.locator('.lq-chip-row__track').evaluate(n => { n.scrollLeft = -n.scrollWidth; });
  await expect(page.locator('.lq-chip-row__track')).toHaveAttribute('data-lq-fade-right', '');
  await context.close();
});
