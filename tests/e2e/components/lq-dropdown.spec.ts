import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import AxeBuilder from '@axe-core/playwright';

const formal = process.env.LQ_DROPDOWN_FORMAL === '1';
const manifest = formal ? JSON.parse(fs.readFileSync('static/assets/manifest.json', 'utf8')) : null;
const asset = (name: string) => `/static/${manifest ? manifest.entries[name] : name}`;
test.beforeAll(() => { if (manifest) console.log(`Dropdown immutable graph: ${manifest.revision}`); });

async function mount(page: Page, html: string) {
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  await page.route('https://dropdown.test/**', route => {
    const url = new URL(route.request().url());
    if (url.pathname.startsWith('/static/')) {
      if (formal && !url.pathname.startsWith(`/static/assets/${manifest.revision}/`)) throw Error(`Mutable asset rejected: ${url.pathname}`);
      const file = path.resolve('.' + url.pathname);
      if (!file.startsWith(path.resolve('static') + path.sep) || !fs.existsSync(file)) throw Error(`Unexpected fixture asset ${file}`);
      return route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body: fs.readFileSync(file) });
    }
    return route.fulfill({ contentType: 'text/html; charset=utf-8', body: `<!doctype html><html lang="zh" data-theme="lanshare" data-appearance="light" data-lq-glass="tinted"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="${asset('css/tailwind-app.css')}"><style>body{padding:24px}#area{max-width:360px}label{display:block}select{min-width:180px}.lq-dropdown{margin-block:8px}</style></head><body><main id="area">${html}</main><div id="unrelated"></div><script type="module">
      import * as dropdown from '${asset('js/lq/dropdown.js')}';import {bindSelection} from '${asset('js/lq/selection.js')}';import {getLayerSystem} from '${asset('js/lq/layer.js')}';import {createForm} from '${asset('js/lq/forms.js')}';
      window.fixture={...dropdown,bindSelection,getLayerSystem,createForm};window.events=[];document.addEventListener('input',e=>{if(e.target.tagName==='SELECT')events.push('input')});document.addEventListener('change',e=>{if(e.target.tagName==='SELECT')events.push('change')});window.ready=true;
    </script></body></html>` });
  });
  await page.goto('https://dropdown.test/'); await page.waitForFunction(() => (window as any).ready); return errors;
}
const basic = '<form id="form"><label for="course">课程</label><select id="course" name="course" required data-lq-dropdown><option value="" selected>全部课程</option><option value="a">Alpha</option><option value="b">Beta</option></select><button id="reset" type="reset">重置</button><button id="next" type="button">下一项</button></form>';
const bind = (page: Page, settings = {}) => page.evaluate(options => { const w = window as any; w.original = document.getElementById('course'); w.binding = w.fixture.bindDropdown(w.original, options); }, settings);
const value = (page: Page) => page.evaluate(() => (document.getElementById('course') as HTMLSelectElement).value);

test('LQ dropdown keeps one native form value, required validation, labels and exactly one event pair', async ({ page }) => {
  const errors = await mount(page, basic); await bind(page);
  const trigger = page.getByRole('combobox', { name: '课程', exact: true });
  await expect(trigger).toHaveText('全部课程');
  expect(await page.evaluate(() => (document.getElementById('form') as HTMLFormElement).reportValidity())).toBe(false);
  await expect(trigger).toBeFocused(); await expect(trigger).toHaveAttribute('aria-invalid', 'true');
  await trigger.click(); await page.getByRole('option', { name: 'Beta', exact: true }).click();
  await expect(trigger).toHaveText('Beta'); expect(await value(page)).toBe('b');
  expect(await page.evaluate(() => ({ data: [...new FormData(document.getElementById('form') as HTMLFormElement)], events: (window as any).events, same: (window as any).original === document.getElementById('course'), nested: !!document.querySelector('.lq-dropdown button button') }))).toEqual({ data: [['course', 'b']], events: ['input', 'change'], same: true, nested: false });
  await expect(trigger).not.toHaveAttribute('aria-invalid');
  await page.locator('#reset').click(); await expect(trigger).toHaveText('全部课程');
  expect(await page.evaluate(() => (window as any).events)).toEqual(['input', 'change']); expect(errors).toEqual([]);
});

test('programmatic value, selectedIndex and option.selected refresh without changing native defaults or events', async ({ page }) => {
  await mount(page, basic); await bind(page);
  const trigger = page.locator('.lq-dropdown__trigger');
  await page.evaluate(() => { (document.getElementById('course') as HTMLSelectElement).value = 'a'; }); await expect(trigger).toHaveText('Alpha');
  await page.evaluate(() => { (document.getElementById('course') as HTMLSelectElement).selectedIndex = 2; }); await expect(trigger).toHaveText('Beta');
  await page.evaluate(() => { (document.getElementById('course') as HTMLSelectElement).options[1].selected = true; }); await expect(trigger).toHaveText('Alpha');
  await page.evaluate(() => { document.getElementById('form')!.addEventListener('reset', e => e.preventDefault(), { once: true }); HTMLFormElement.prototype.reset.call(document.getElementById('form')); });
  await expect(trigger).toHaveText('Alpha');
  await page.evaluate(() => { HTMLFormElement.prototype.reset.call(document.getElementById('form')); }); await expect(trigger).toHaveText('全部课程');
  expect(await page.evaluate(() => (window as any).events)).toEqual([]);
});

test('input cleanup preserves one change on a connected native owner and suppresses it after detach', async ({ page }) => {
  await mount(page, basic); await bind(page);
  await page.evaluate(() => { const w = window as any; w.original.addEventListener('input', () => w.binding.destroy(), { once: true }); });
  await page.locator('.lq-dropdown__trigger').click(); await page.getByRole('option', { name: 'Beta', exact: true }).click();
  expect(await page.evaluate(() => (window as any).events)).toEqual(['input', 'change']); expect(await value(page)).toBe('b');
  await bind(page); await page.evaluate(() => { (window as any).original.addEventListener('input', event => event.target.remove(), { once: true }); });
  await page.locator('.lq-dropdown__trigger').click(); await page.getByRole('option', { name: 'Alpha', exact: true }).click();
  expect(await page.evaluate(() => (window as any).events)).toEqual(['input', 'change', 'input']);
});

test('optgroups, disabled fieldsets, hidden controls and option text mutations stay synchronized', async ({ page }) => {
  await mount(page, '<form id="form"><fieldset id="group"><legend>课时范围</legend><label for="course">课程</label><select id="course" name="course"><optgroup label="可用"><option value="a">Alpha</option><option value="b">Beta</option></optgroup><optgroup label="停用" disabled><option value="c">Gamma</option></optgroup><option value="d" hidden>Hidden</option></select></fieldset></form>'); await bind(page);
  const trigger = page.locator('.lq-dropdown__trigger'); await trigger.click();
  await expect(page.getByRole('group', { name: '可用', exact: true })).toBeVisible();
  await expect(page.getByRole('option', { name: 'Gamma', exact: true })).toHaveAttribute('aria-disabled', 'true');
  await expect(page.getByRole('option', { name: 'Hidden', exact: true })).toHaveCount(0);
  await page.evaluate(() => { (document.getElementById('group') as HTMLFieldSetElement).disabled = true; }); await expect(trigger).toBeDisabled(); await expect(page.locator('.lq-dropdown__popup')).toHaveCount(0);
  expect(await page.evaluate(() => [...new FormData(document.getElementById('form') as HTMLFormElement)])).toEqual([]);
  await page.evaluate(() => { (document.getElementById('group') as HTMLFieldSetElement).disabled = false; const el = document.getElementById('course') as HTMLSelectElement; el.options[0].textContent = '改名课程'; el.hidden = true; });
  await expect(page.locator('.lq-dropdown')).toBeHidden();
  await page.evaluate(() => { const el = document.getElementById('course')!; el.hidden = false; el.style.display = 'none'; }); await expect(page.locator('.lq-dropdown')).toBeHidden();
  await page.evaluate(() => { document.getElementById('course')!.style.display = ''; }); await expect(trigger).toHaveText('改名课程'); await expect(trigger).toBeVisible();
  await page.evaluate(() => { document.getElementById('course')!.className = 'new-field'; }); await expect(page.locator('#course')).toHaveClass(/lq-selection-native/);
  await page.evaluate(() => { document.getElementById('area')!.append(document.getElementById('course')!); }); await expect(page.locator('#area > #course + .lq-dropdown')).toBeVisible();
});

test('multiple keeps native FormData entries, disabled choices and reset selection', async ({ page }) => {
  await mount(page, '<form id="form"><label for="course">班级</label><select id="course" name="class" multiple required><option value="a" selected>一班</option><option value="b">二班</option><option value="c" disabled>停用班</option></select><button id="reset" type="reset">重置</button></form>'); await bind(page);
  await page.locator('.lq-dropdown__trigger').click();
  await page.getByRole('option', { name: '二班', exact: true }).click();
  await expect(page.getByRole('listbox')).toBeVisible();
  expect(await page.evaluate(() => [...new FormData(document.getElementById('form') as HTMLFormElement)])).toEqual([['class', 'a'], ['class', 'b']]);
  const disabled = await page.getByRole('option', { name: '停用班', exact: true }).boundingBox(); await page.mouse.click(disabled!.x + 12, disabled!.y + 12); expect(await page.evaluate(() => (window as any).events)).toEqual(['input', 'change']);
  await page.keyboard.press('Escape'); await page.locator('#reset').click(); await expect(page.locator('.lq-dropdown__trigger')).toHaveText('一班');
});

test('search IME queries once and stale or invalid async candidates cannot replace the committed value', async ({ page }) => {
  await mount(page, basic);
  await page.evaluate(() => { const w = window as any; w.tickets = []; w.binding = w.fixture.bindDropdown(document.getElementById('course'), { searchable: true, onQuery: ticket => w.tickets.push(ticket) }); w.binding.open(); });
  await page.locator('.lq-dropdown__search').evaluate(input => {
    input.dispatchEvent(new CompositionEvent('compositionstart')); (input as HTMLInputElement).value = '计算机'; input.dispatchEvent(new InputEvent('input', { isComposing: true })); input.dispatchEvent(new CompositionEvent('compositionend')); input.dispatchEvent(new InputEvent('input'));
  });
  expect(await page.evaluate(() => (window as any).tickets.length)).toBe(1);
  const outcomes = await page.evaluate(() => {
    const w = window as any, first = w.tickets[0], second = w.binding.query('网络');
    const stale = w.binding.setResults({ ...first, options: [{ value: 'old', label: '旧候选' }] });
    let invalid = false; try { w.binding.setResults({ ...second, options: [{ value: 'ok', label: '合法' }, { value: 'bad', label: 3 }] }); } catch { invalid = true; }
    const unchanged = ![...w.binding.select.options].some(option => option.value === 'ok');
    const accepted = w.binding.setResults({ ...second, options: [{ value: 'new', label: '网络' }] });
    return { stale, invalid, unchanged, accepted, value: w.binding.select.value };
  });
  expect(outcomes).toEqual({ stale: false, invalid: true, unchanged: true, accepted: true, value: '' });
  await page.getByRole('option', { name: '网络', exact: true }).click(); expect(await value(page)).toBe('new');
  expect(await page.evaluate(() => (window as any).events)).toEqual(['input', 'change']);
});

test('declared dynamic activation, wrapping labels and explicit Selection takeover have one owner', async ({ page }) => {
  await mount(page, '<div id="dynamic"></div>');
  await page.evaluate(() => { const w = window as any; w.fixture.installDropdowns(document); w.fixture.installDropdowns(document); document.getElementById('dynamic')!.innerHTML = '<label>同步范围<select id="course" data-lq-dropdown><option value="a">当前学期</option><option value="b">全部学期</option></select></label>'; });
  await page.locator('#course').click();
  await expect(page.getByRole('combobox', { name: '同步范围', exact: true })).toBeVisible(); await expect(page.getByRole('option', { name: '全部学期', exact: true })).toBeVisible();
  await page.keyboard.press('Escape');
  await page.evaluate(() => { const w = window as any; w.selection = w.fixture.bindSelection(document.getElementById('course'), { label: '同步范围' }); });
  await expect(page.locator('.lq-dropdown')).toHaveCount(0); await expect(page.locator('.lq-selection')).toHaveCount(1);
  expect(await page.evaluate(() => (window as any).fixture.getDropdown(document.getElementById('course')))).toBe(null);
});

test('native modal parent owns popup and Tab continues after the original control', async ({ page }) => {
  await mount(page, `<button id="open">打开</button><dialog id="dialog">${basic}</dialog>`);
  await page.evaluate(() => { const w = window as any; document.getElementById('open')!.onclick = () => { w.parent = w.fixture.getLayerSystem(document).open(document.getElementById('dialog'), { type: 'modal' }); w.binding = w.fixture.bindDropdown(document.getElementById('course')); }; });
  await page.locator('#open').click(); await page.locator('.lq-dropdown__trigger').click();
  expect(await page.locator('.lq-dropdown__popup').evaluate(el => !!el.closest('dialog[open]'))).toBe(true);
  await page.keyboard.press('Tab'); await expect(page.locator('#reset')).toBeFocused();
  await expect(page.locator('.lq-dropdown__popup')).toHaveCount(0); await expect(page.locator('#reset')).toBeFocused();
  await expect(page.locator('#dialog')).toBeVisible(); await page.keyboard.press('Escape'); await expect(page.locator('#dialog')).toBeHidden();
});

test('destroy restores native descriptors and labels; unrelated DOM updates do not rescan or rerender', async ({ page }) => {
  await mount(page, basic); await bind(page);
  const result = await page.evaluate(async () => {
    const w = window as any, select = document.getElementById('course');
    let reads = 0; const computed = window.getComputedStyle; window.getComputedStyle = (el, pseudo) => { if (el === select) reads++; return computed.call(window, el, pseudo); };
    await new Promise(resolve => setTimeout(resolve, 30)); reads = 0;
    for (let i = 0; i < 100; i++) document.getElementById('unrelated')!.append(document.createElement('span'));
    await new Promise(resolve => setTimeout(resolve, 30)); const idleReads = reads;
    w.binding.destroy(); window.getComputedStyle = computed;
    return { idleReads, valueOwn: Object.hasOwn(select, 'value'), selectedOwn: Object.hasOwn(select.options[1], 'selected'), labelFor: document.querySelector('label')!.htmlFor, same: select === w.original, ariaHidden: select.getAttribute('aria-hidden') };
  });
  expect(result).toEqual({ idleReads: 0, valueOwn: false, selectedOwn: false, labelFor: 'course', same: true, ariaHidden: null });
});

test('hidden declarations, explicit owners, manual lifecycle and unnamed diagnostics remain isolated', async ({ page }) => {
  await mount(page, '<select id="hidden" hidden aria-label="隐藏" data-lq-dropdown><option>A</option></select><select id="manual" data-lq-dropdown data-lq-dropdown-manual="react" aria-label="React"><option>A</option></select><select id="reserved" data-lq-dropdown data-lq-selection-owner aria-label="业务"><option>A</option></select><select id="unnamed" data-lq-dropdown><option>A</option></select><select id="visible" data-lq-dropdown aria-labelledby="label"><option>A</option></select><span id="label">选择范围<span aria-hidden="true">装饰</span><input value="非标签"></span>');
  const result = await page.evaluate(() => {
    const w = window as any, warnings = []; const warn = console.warn; console.warn = (...args) => warnings.push(args.join(' '));
    const bindings = w.fixture.enhanceDropdowns(document); console.warn = warn;
    return { ids: bindings.map(binding => binding.select.id), warnings, reserved: w.fixture.getDropdown(document.getElementById('reserved')), manual: w.fixture.getDropdown(document.getElementById('manual')) };
  });
  expect(result.ids).toEqual(['hidden', 'visible']); expect(result.manual).toBe(null); expect(result.reserved).toBe(null); expect(result.warnings).toHaveLength(1);
  await expect(page.getByRole('combobox', { name: '选择范围', exact: true })).toBeVisible();
  await expect(page.locator('#hidden + .lq-dropdown')).toBeHidden();
  await page.evaluate(() => { const source = document.getElementById('hidden')!; source.hidden = false; source.style.visibility = 'hidden'; }); await expect(page.locator('#hidden + .lq-dropdown')).toBeHidden();
  await page.evaluate(() => { document.getElementById('hidden')!.style.visibility = ''; }); await expect(page.locator('#hidden + .lq-dropdown')).toBeVisible();
});

test('detached owner releases popup and instance hooks, then the same native node can bind again', async ({ page }) => {
  await mount(page, basic); await bind(page); await page.locator('.lq-dropdown__trigger').click();
  await page.evaluate(() => { (window as any).detached = document.getElementById('form'); (window as any).detached.remove(); });
  await expect(page.locator('.lq-dropdown__popup')).toHaveCount(0);
  expect(await page.evaluate(() => { const w = window as any; return { binding: w.fixture.getDropdown(w.original), hooked: Object.hasOwn(w.original, 'value') }; })).toEqual({ binding: null, hooked: false });
  await page.evaluate(() => { const w = window as any; document.getElementById('area')!.append(w.detached); w.binding = w.fixture.bindDropdown(w.original); w.original.value = 'b'; });
  await expect(page.locator('.lq-dropdown__trigger')).toHaveText('Beta'); await expect(page.locator('.lq-dropdown')).toHaveCount(1);
});

test('closing invalidates asynchronous candidates and immediate reopen preserves the active layer', async ({ page }) => {
  await mount(page, basic);
  const result = await page.evaluate(async () => {
    const w = window as any; w.binding = w.fixture.bindDropdown(document.getElementById('course'), { searchable: true }); w.binding.open();
    const ticket = w.binding.query('旧搜索'); const closing = w.binding.close('test'); w.binding.open();
    const accepted = w.binding.setResults({ ...ticket, options: [{ value: 'old', label: '不应加入' }] }); await closing;
    return { accepted, value: w.binding.select.value, added: [...w.binding.select.options].some(option => option.value === 'old') };
  });
  expect(result).toEqual({ accepted: false, value: '', added: false }); await expect(page.locator('.lq-dropdown__popup')).toBeVisible(); await expect(page.locator('.lq-dropdown__trigger')).toHaveAttribute('aria-expanded', 'true');
  await page.keyboard.press('Escape'); await expect(page.locator('.lq-dropdown__popup')).toHaveCount(0); await expect(page.locator('.lq-dropdown__trigger')).toBeFocused();
});

test('a first touch on a dynamically declared select opens only its LQ popup', async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true }); const page = await context.newPage();
  try {
    await mount(page, '<div id="dynamic"></div>');
    await page.evaluate(() => { (window as any).fixture.installDropdowns(document); document.getElementById('dynamic')!.innerHTML = '<label>同步范围<select id="course" data-lq-dropdown><option value="a">当前学期</option><option value="b">全部学期</option></select></label>'; });
    await page.locator('#course').tap(); await expect(page.getByRole('option', { name: '全部学期', exact: true })).toBeVisible();
    await page.getByRole('option', { name: '全部学期', exact: true }).tap(); expect(await value(page)).toBe('b'); expect(await page.evaluate(() => (window as any).events)).toEqual(['input', 'change']);
    await expect(page.locator('.lq-dropdown__popup')).toHaveCount(0);
  } finally { await context.close(); }
});

for (const width of [390, 1440]) test(`long labels and grouped choices stay within ${width}px and pass serious accessibility checks`, async ({ page }) => {
  await page.setViewportSize({ width, height: 860 });
  await mount(page, '<label for="course">联合班级</label><select id="course"><option value="a">人工智能2601班（专升本） · 人工智能2602班（专升本） · 网络工程2601班（专升本）</option><optgroup label="其他班级"><option value="b">软件工程2601班</option></optgroup></select>'); await bind(page);
  await page.locator('.lq-dropdown__trigger').click();
  const rect = await page.locator('.lq-dropdown__popup').boundingBox(); expect(rect!.x).toBeGreaterThanOrEqual(0); expect(rect!.x + rect!.width).toBeLessThanOrEqual(width);
  const violations = (await new AxeBuilder({ page }).include('#area').include('.lq-dropdown__popup').analyze()).violations.filter(item => ['serious', 'critical'].includes(item.impact || ''));
  expect(violations).toEqual([]);
});

for (const width of [390, 1440]) test(`typed searchable Fields keep full control-slot width and real sm/md/lg geometry at ${width}px`, async ({ page }) => {
  await page.setViewportSize({ width, height: 860 }); await mount(page, '');
  await page.evaluate(() => {
    const api = (window as any).fixture;
    for (const size of ['sm', 'md', 'lg']) document.getElementById('area')!.append(api.createForm('field', {
      id: `field-${size}`, label: `课程${size}`, control: 'select', size, searchable: true, value: 'a',
      options: [{ value: 'a', label: 'Alpha' }, { value: 'b', label: 'Beta' }],
    }));
    api.installDropdowns(document);
  });
  const geometry = await page.evaluate(() => [...document.querySelectorAll('.lq-field__control')].map(slot => {
    const trigger = slot.querySelector('.lq-dropdown__trigger')!, style = getComputedStyle(trigger);
    return { slot: slot.getBoundingClientRect().width, width: trigger.getBoundingClientRect().width, height: trigger.getBoundingClientRect().height, minHeight: parseFloat(style.minHeight), padding: parseFloat(style.paddingTop), font: parseFloat(style.fontSize) };
  }));
  for (const item of geometry) expect(Math.abs(item.width - item.slot)).toBeLessThan(1);
  expect(geometry.map(item => item.minHeight)).toEqual([32, 40, 48]);
  expect(geometry[0].height).toBeLessThan(geometry[1].height); expect(geometry[1].height).toBeLessThan(geometry[2].height);
  expect(geometry[0].padding).toBeLessThan(geometry[1].padding); expect(geometry[2].font).toBeGreaterThan(geometry[1].font);
  await page.locator('#field-md + .lq-dropdown .lq-dropdown__trigger').click();
  const search = page.getByRole('combobox', { name: '筛选课程md', exact: true }); await expect(search).toBeVisible(); await search.fill('Beta');
  await expect(page.getByRole('option', { name: 'Alpha', exact: true })).toHaveCount(0); await page.getByRole('option', { name: 'Beta', exact: true }).click();
  expect(await page.locator('#field-md').inputValue()).toBe('b'); expect(await page.evaluate(() => (window as any).events)).toEqual(['input', 'change']);
});
