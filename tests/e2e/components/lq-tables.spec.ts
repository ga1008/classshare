import { test, expect, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';

const python = process.env.LQ_TEST_PYTHON || (process.platform === 'win32' ? 'venv/Scripts/python.exe' : 'venv/bin/python');
const fixture = JSON.parse(execFileSync(python, ['tests/e2e/scripts/render_lq_tables.py'], { encoding: 'utf8' }));
const palettes = ['teal', 'indigo', 'sky', 'mint', 'violet', 'rose'];
const tokens = JSON.parse(fs.readFileSync('docs/lq-tokens.json', 'utf8'));
async function mount(page: Page, { entry = 'jinja', selection = 'native', palette = 'indigo', appearance = 'light' } = {}) {
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin !== 'https://lq-tables.test') return route.abort();
    if (url.pathname.startsWith('/static/js/lq/') || ['/static/css/tailwind-app.css', '/static/css/lq/components/tables.css', '/static/css/lq/components/content.css'].includes(url.pathname)) {
      const file = path.resolve(`.${url.pathname}`);
      if (file.startsWith(path.resolve('static') + path.sep) && fs.existsSync(file)) return route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body: fs.readFileSync(file) });
    }
    if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh-CN" data-theme="lanshare" data-ui-palette="${palette}" data-appearance="${appearance}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>LQ Tables</title><link rel="stylesheet" href="/static/css/tailwind-app.css"><style>body{margin:0}main{padding:24px;max-width:1100px;margin:auto;display:grid;grid-template-columns:minmax(0,1fr);gap:16px}.sample{min-width:0;max-width:100%}input[type=text]{max-width:100%}</style></head><body><main><h1>表格组件</h1></main><script type="module">import * as api from '/static/js/lq/tables.js';import {enhanceComponents} from '/static/js/lq/components.js';window.api=api;window.enhancer=enhanceComponents();document.body.dataset.ready='true';</script></body></html>` });
    return route.fulfill({ status: 404, body: 'local fixture only' });
  });
  await page.goto('https://lq-tables.test/'); await expect(page.locator('body')).toHaveAttribute('data-ready', 'true');
  await page.evaluate(({ fixture, entry, selection }) => {
    const api = (window as any).api, host = document.querySelector('main')!;
    for (const item of fixture.cases) {
      const wrapper = document.createElement(item.props.id === 'records' ? 'form' : 'div'); wrapper.className = 'sample';
      if (wrapper.tagName === 'FORM') wrapper.id = 'record-form';
      if (entry === 'element') wrapper.append(api.createTable(item.kind, item.props));
      else wrapper.innerHTML = entry === 'jinja' ? item.html : api.html[item.kind](item.props);
      host.append(wrapper);
    }
    const composition = document.createElement('div'); composition.className = 'sample'; composition.innerHTML = fixture.composition; host.append(composition);
    (window as any).draftNode = document.querySelector('#table-draft');
    (window as any).handles = {}; for (const root of document.querySelectorAll('[data-lq-table]')) (window as any).handles[root.id] = api.enhanceTable(root, { selection });
    (window as any).changes = []; (window as any).submits = 0; (window as any).intents = [];
    document.addEventListener('change', event => { const input = event.target as HTMLInputElement; if (input.matches('[data-lq-select-row]')) (window as any).changes.push({ value: input.value, checked: input.checked }); });
    document.addEventListener('submit', event => { event.preventDefault(); (window as any).submits++; });
    document.addEventListener('click', event => {
      const control = (event.target as Element).closest('[data-lq-sort],[data-lq-page]');
      if (control && !(control as HTMLButtonElement).disabled) (window as any).intents.push(control.getAttribute('data-lq-sort') || control.getAttribute('data-lq-page'));
      if ((event.target as Element).closest('a')) event.preventDefault();
    });
  }, { fixture, entry, selection });
}

test.describe('LQ Table Pager BulkBar ResultCount', () => {
  test('real Jinja/Python props and HTML/Element paths agree for safety and semantic structure', async ({ page }) => {
    expect(fixture.isolated).toBe(true); expect(fixture.cases.filter((item: any) => item.error)).toEqual([]);
    await mount(page);
    const result = await page.evaluate(cases => {
      const api = (window as any).api;
      const tree = (n: Node): any => n.nodeType === 3 ? (n.textContent?.trim() ? { text: n.textContent } : null) : { tag: (n as Element).tagName, attrs: Object.fromEntries([...(n as Element).attributes].map(a => [a.name, a.value]).sort()), children: [...n.childNodes].map(tree).filter(Boolean) };
      const parse = (html: string) => { const t = document.createElement('template'); t.innerHTML = html; return [...t.content.childNodes].map(tree).filter(Boolean); };
      return cases.map((item: any) => ({ props: api.tableProps(item.kind, item.props), jinja: parse(item.html), html: parse(api.html[item.kind](item.props)), element: [tree(api.createTable(item.kind, item.props))] }));
    }, fixture.cases);
    result.forEach((item: any, i: number) => { expect(item.props).toEqual(fixture.cases[i].normalized); expect(item.html).toEqual(item.jinja); expect(item.element).toEqual(item.jinja); });
    expect(fixture.invalid.every((item: any) => item.error === 'ValueError')).toBe(true);
    const invalid = await page.evaluate(cases => cases.map((item: any) => ['tableProps', 'tableMarkup', 'createTable'].map(method => { try { (window as any).api[method](item.kind, item.props); return false; } catch (error) { return error instanceof TypeError; } })), fixture.invalid);
    invalid.forEach((item: boolean[]) => expect(item).toEqual([true, true, true]));
  });

  for (const entry of ['jinja', 'html', 'element']) test(`${entry}: native mixed selection preserves disabled choices and emits one change per changed row`, async ({ page }) => {
    await mount(page, { entry });
    const master = page.locator('#records [data-lq-select-all]');
    await expect(master).toHaveJSProperty('indeterminate', true); await expect(master).toHaveAttribute('aria-checked', 'mixed');
    await master.focus(); await page.keyboard.press('Space');
    await expect(master).toBeChecked(); await expect(master).toHaveJSProperty('indeterminate', false);
    expect(await page.evaluate(() => (window as any).changes)).toEqual([{ value: 'b', checked: true }]);
    await page.keyboard.press('Space');
    expect(await page.evaluate(() => (window as any).changes)).toEqual([{ value: 'b', checked: true }, { value: 'a', checked: false }, { value: 'b', checked: false }]);
    await expect(page.locator('#records [value="c"]')).toBeChecked(); await expect(page.locator('#records [value="c"]')).toBeDisabled();
    await page.locator('#records [value="a"]').focus(); await page.keyboard.press('Space'); await expect(master).toHaveAttribute('aria-checked', 'mixed');
    await expect(page.locator('#disabled-table [data-lq-select-all]')).toBeDisabled(); await expect(page.locator('#disabled-table [value="locked"]')).toBeChecked();
    await expect(page.locator('#empty-table [data-lq-select-all]')).toBeDisabled();
    expect(await page.evaluate(() => (window as any).submits)).toBe(0);
  });

  test('controller-owned selection does not get a second selected array or automatic master writes', async ({ page }) => {
    await mount(page, { selection: 'controller' });
    await page.locator('#records [data-lq-select-all]').click();
    await expect(page.locator('#records [value="a"]')).toBeChecked(); await expect(page.locator('#records [value="b"]')).not.toBeChecked();
    await expect(page.locator('#records [data-lq-select-all]')).toHaveAttribute('aria-checked', 'mixed');
    expect(await page.evaluate(() => (window as any).changes)).toEqual([]);
    await page.evaluate(() => { const input = document.querySelector('#records [value="b"]') as HTMLInputElement; input.checked = true; (window as any).handles['records--lq-wrap'].refresh(); });
    await expect(page.locator('#records [data-lq-select-all]')).toBeChecked();
  });

  test('sorting and pagination emit native intent without changing records or submitting forms', async ({ page }) => {
    await mount(page);
    const order = await page.locator('#records tbody tr').evaluateAll(rows => rows.map(row => row.getAttribute('data-lq-row-key')));
    await page.locator('#records [data-lq-sort="score"]').focus(); await page.keyboard.press('Enter');
    await expect(page.locator('#records--lq-col-score')).toHaveAttribute('aria-sort', 'none');
    await page.evaluate(() => { document.querySelector('#records--lq-col-name')!.setAttribute('aria-sort', 'none'); document.querySelector('#records--lq-col-score')!.setAttribute('aria-sort', 'descending'); });
    await expect(page.locator('#records--lq-col-score')).toHaveAttribute('aria-sort', 'descending');
    expect(await page.locator('#records tbody tr').evaluateAll(rows => rows.map(row => row.getAttribute('data-lq-row-key')))).toEqual(order);
    await page.locator('#pager-middle [data-lq-page="4"]').first().focus(); await page.keyboard.press('Space');
    await page.locator('#pager-links a').first().focus(); await page.keyboard.press('Enter');
    expect(await page.evaluate(() => (window as any).intents)).toEqual(['score', '4', '1']);
    await expect(page.locator('#pager-first button')).toHaveCount(3); await expect(page.locator('#pager-first button:enabled')).toHaveCount(0);
    await expect(page.locator('#pager-empty button:enabled')).toHaveCount(0); await expect(page.locator('#pager-disabled button:enabled')).toHaveCount(0);
    expect(await page.locator('#pager-huge a,#pager-huge button').count()).toBeLessThanOrEqual(7);
    await expect(page.locator('#bulk-zero')).toBeHidden(); await expect(page.locator('#hidden-delete')).toHaveCount(0);
    await expect(page.locator('#count-zero')).toHaveText('0 条结果'); await expect(page.locator('#count-loading')).not.toHaveAttribute('aria-live');
    expect(await page.evaluate(() => (window as any).submits)).toBe(0);
  });

  test('duplicate import/init, reset and destroy are bounded and retain real input nodes', async ({ page }) => {
    await mount(page);
    const result = await page.evaluate(async () => {
      const w = window as any, root = document.querySelector('#records--lq-wrap')!, first = w.handles[root.id];
      const duplicate = await import('/static/js/lq/tables.js?duplicate'); const second = duplicate.enhanceTable(root, { selection: 'native' });
      let wrongOwner = false; try { duplicate.enhanceTable(root, { selection: 'controller' }); } catch { wrongOwner = true; }
      first.destroy(); first.destroy();
      (root.querySelector('[data-lq-select-all]') as HTMLInputElement).click();
      const noWrite = !(root.querySelector('[value="b"]') as HTMLInputElement).checked;
      w.handles[root.id] = duplicate.enhanceTable(root, { selection: 'native' });
      return { same: first === second, wrongOwner, noWrite };
    });
    expect(result).toEqual({ same: true, wrongOwner: true, noWrite: true });
    await page.locator('#records [value="b"]').check(); await expect(page.locator('#records [data-lq-select-all]')).toBeChecked();
    await page.evaluate(() => (document.querySelector('#record-form') as HTMLFormElement).reset());
    await expect(page.locator('#records [data-lq-select-all]')).toHaveAttribute('aria-checked', 'mixed');
    await page.locator('#table-draft').fill('未保存 91.5');
    await page.setViewportSize({ width: 390, height: 844 }); await page.setViewportSize({ width: 1440, height: 980 });
    await expect(page.locator('#table-draft')).toHaveValue('未保存 91.5'); expect(await page.evaluate(() => document.querySelector('#table-draft') === (window as any).draftNode)).toBe(true);
  });

  test('destroy during a native batch stops later events and writes without rolling back native values', async ({ page }) => {
    await mount(page);
    const result = await page.evaluate(() => {
      const w = window as any, root = document.querySelector('#records--lq-wrap')!;
      const first = root.querySelector('[value="a"]') as HTMLInputElement;
      const second = root.querySelector('[value="b"]') as HTMLInputElement;
      first.checked = false; second.checked = false; w.handles[root.id].refresh();
      let inputs = 0;
      first.addEventListener('input', () => { inputs++; w.handles[root.id].destroy(); }, { once: true });
      (root.querySelector('[data-lq-select-all]') as HTMLInputElement).click();
      const values = [first.checked, second.checked, (root.querySelector('[value="c"]') as HTMLInputElement).checked];
      w.handles[root.id] = w.api.enhanceTable(root, { selection: 'native' });
      return { inputs, values, changes: w.changes, mixed: (root.querySelector('[data-lq-select-all]') as HTMLInputElement).indeterminate };
    });
    expect(result).toEqual({ inputs: 1, values: [true, false, true], changes: [], mixed: true });
  });

  test('all cell slots validate before any node moves and retain listeners and input values', async ({ page }) => {
    await mount(page);
    const result = await page.evaluate(() => {
      const api = (window as any).api, input = document.querySelector('#table-draft') as HTMLInputElement, parent = input.parentNode;
      const props = { id: 'slot-table', caption: '节点槽', columns: [{ key: 'a', label: 'A' }, { key: 'b', label: 'B' }], rows: [{ key: 'r', cells: { a: '', b: '' } }] };
      let rejected = 0;
      for (const slots of [{ 'cell:r:a': [input], 'cell:r:b': ['unsafe'] }, { 'cell:r:a': [input], wrong: [] }, { 'cell:r:a': [input], 'cell:r:b': [input] }]) try { api.createTable('table', props, slots); } catch { rejected++; }
      const unchanged = input.parentNode === parent; let events = 0; input.value = '原始评分 88.5'; input.addEventListener('input', () => events++);
      const root = api.createTable('table', props, { 'cell:r:a': [input] }); document.querySelector('main')!.append(root); input.dispatchEvent(new Event('input'));
      return { rejected, unchanged, same: root.querySelector('input') === input, value: input.value, events };
    });
    expect(result).toEqual({ rejected: 3, unchanged: true, same: true, value: '原始评分 88.5', events: 1 });
    for (const reason of ['empty', 'error', 'offline']) await expect(page.locator(`#state-${reason}--lq-wrap [data-reason]`)).toHaveAttribute('data-reason', reason);
  });

  for (const width of [767, 768, 769]) test(`record breakpoint ${width} retains real browser AX table/header/row/cell relationships`, async ({ page }) => {
    await page.setViewportSize({ width, height: 980 }); await mount(page);
    await expect(page.locator('#records')).toHaveCSS('display', width <= 768 ? 'block' : 'table');
    await expect(page.locator('#records [data-lq-sort="name"]')).toBeVisible(); await expect(page.locator('#records [data-lq-select-all]')).toBeVisible();
    const relation = await page.locator('#records').evaluate(table => [...table.querySelectorAll('[headers]')].every(cell => cell.getAttribute('headers')!.split(' ').every(id => !!document.getElementById(id)?.matches('th'))));
    expect(relation).toBe(true);
    const client = await page.context().newCDPSession(page); const ax = await client.send('Accessibility.getFullAXTree'); await client.detach();
    const tableNode = ax.nodes.find(n => !n.ignored && n.role?.value === 'table' && n.name?.value === '可管理的课程记录'); expect(tableNode).toBeTruthy();
    const nodes = new Map(ax.nodes.map(n => [n.nodeId, n])); const descendants: any[] = [];
    const visit = (id: string) => { const n = nodes.get(id)!; if (!n.ignored) descendants.push(n); for (const child of n.childIds || []) visit(child); }; visit(tableNode!.nodeId);
    const roles = descendants.map(n => n.role?.value);
    expect(roles.filter(r => r === 'row')).toHaveLength(4); expect(roles.filter(r => r === 'columnheader')).toHaveLength(4); expect(roles.filter(r => r === 'rowheader')).toHaveLength(3); expect(roles.filter(r => r === 'cell')).toHaveLength(9);
    for (const n of descendants.filter(n => ['cell', 'columnheader', 'rowheader'].includes(n.role?.value))) expect(nodes.get(n.parentId)?.role?.value).toBe('row');
    fs.writeFileSync(`.codex-temp/lq-table-ax-${width}.json`, JSON.stringify(descendants.map(n => ({ id: n.nodeId, role: n.role?.value, name: n.name?.value, parent: n.parentId })), null, 2));
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });

  test('390 coarse matrix scrolls locally, records wrap long content and targets remain 44px', async ({ browser }) => {
    const context = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
    try {
      const page = await context.newPage(); await mount(page);
      await page.evaluate(() => { document.querySelector('#records [data-lq-slot="cell:a:note"]')!.textContent = 'LongUnbrokenName'.repeat(50); });
      const matrix = page.locator('#matrix--lq-wrap .lq-table__scroll');
      expect(await matrix.evaluate(el => el.scrollWidth > el.clientWidth)).toBe(true);
      await matrix.focus(); await page.keyboard.press('End');
      await expect(matrix).toHaveCSS('outline-style', 'solid');
      for (const selector of ['#records .lq-table__sort', '#records .lq-table__check', '#pager-middle .lq-pager__control']) for (const height of await page.locator(selector).evaluateAll(nodes => nodes.map(n => n.getBoundingClientRect().height))) expect(height).toBeGreaterThanOrEqual(44);
      expect(await page.evaluate(() => innerWidth)).toBe(390); expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(390);
      await page.screenshot({ path: '.codex-temp/lq-tables-mobile.png', fullPage: true });
    } finally { await context.close(); }
  });

  test('forced colors, reduced motion and dense fine-pointer rows preserve visible focus', async ({ page }) => {
    await mount(page); expect(await page.locator('#dense tbody tr').evaluate(el => el.getBoundingClientRect().height)).toBeGreaterThanOrEqual(40);
    await page.emulateMedia({ forcedColors: 'active', reducedMotion: 'reduce' }); await page.locator('#records [data-lq-sort="name"]').focus();
    await expect(page.locator('#records [data-lq-sort="name"]')).toHaveCSS('outline-style', 'solid');
    await expect(page.locator('#records--lq-wrap')).toHaveCSS('backdrop-filter', 'none');
    await expect(page.locator('#records [data-lq-select-all]')).toHaveJSProperty('indeterminate', true);
  });

  for (const palette of palettes) for (const appearance of ['light', 'dark']) test(`axe whole table fixture ${palette}/${appearance} desktop and mobile`, async ({ page }) => {
    const declared = [...new Set([...fs.readFileSync('static/css/lq/tokens.css', 'utf8').matchAll(/data-ui-palette="([^"]+)"/g)].map(match => match[1]))];
    expect(palettes.slice().sort()).toEqual(declared.sort()); expect(createHash('sha256').update(fs.readFileSync('static/css/lq/tokens.css')).digest('hex')).toBe(tokens.source_sha256);
    await mount(page, { palette, appearance });
    const colors = await page.evaluate(expected => { const probe = document.createElement('span'); document.body.append(probe); const rgb = (v: string) => { probe.style.color = `hsl(${v})`; return getComputedStyle(probe).color; }; const result = { actual: rgb(getComputedStyle(document.documentElement).getPropertyValue('--ls-primary')), expected: rgb(expected['--ls-primary']) }; probe.remove(); return result; }, tokens.themes[palette][appearance]);
    expect(colors.actual).toBe(colors.expected);
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: 980 }); const scan = await new AxeBuilder({ page }).analyze();
      expect(scan.violations.map(item => ({ id: item.id, nodes: item.nodes.map(node => ({ target: node.target, summary: node.failureSummary })) })), `${palette}/${appearance}/${width}`).toEqual([]);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    }
  });
});
