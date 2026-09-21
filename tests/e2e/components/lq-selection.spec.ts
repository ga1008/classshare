import { test, expect, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
const python = process.env.LQ_TEST_PYTHON || (process.platform === 'win32' ? 'venv/Scripts/python.exe' : 'venv/bin/python');
const fixture = JSON.parse(execFileSync(python, ['tests/e2e/scripts/render_lq_selection.py'], { encoding: 'utf8' }));
async function mount(page: Page) {
    await page.route('**/*', route => {
        const url = new URL(route.request().url()); if (url.origin !== 'https://lq-selection.test') return route.abort();
        if (url.pathname.startsWith('/static/js/') || ['/static/css/tailwind-app.css', '/static/css/lq/components/selection.css'].includes(url.pathname)) {
            const file = path.resolve(`.${url.pathname}`); if (file.startsWith(path.resolve('static') + path.sep) && fs.existsSync(file)) return route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body: fs.readFileSync(file) });
        }
        if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh-CN" data-theme="lanshare" data-ui-palette="teal" data-appearance="light" data-lq-glass="tinted"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>LQ Selection</title><link rel="stylesheet" href="/static/css/tailwind-app.css"></head><body><main style="padding:32px;max-width:680px"><h1>课程与人员选择</h1><form id="form"><div id="mount"></div><button id="submit" type="submit">提交</button><button id="after" type="button">后继操作</button><button id="reset" type="reset">重置</button></form></main><dialog id="native" aria-label="原生父层"></dialog><script type="module">import * as selection from '/static/js/lq/selection.js';import {getLayerSystem} from '/static/js/lq/layer.js';window.selection=selection;window.layer=getLayerSystem(document);window.changes=0;window.inputs=0;window.submits=0;window.tickets=[];document.querySelector('#form').addEventListener('submit',e=>{e.preventDefault();window.submits++});document.body.dataset.ready='true';</script></body></html>` });
        return route.abort();
    });
    await page.goto('https://lq-selection.test/'); await expect(page.locator('body')).toHaveAttribute('data-ready', 'true');
}
async function create(page: Page, index = 0, bind = true) {
    await page.evaluate(({ item, bind }) => { const w = window as any; const root = w.selection.createSelection(item.kind, item.props); document.querySelector('#mount')!.append(root); w.select = root.querySelector('select'); w.select.addEventListener('change', () => w.changes++); w.select.addEventListener('input', () => w.inputs++); if (bind) w.binding = w.selection.bindSelection(w.select); }, { item: fixture.cases[index], bind });
}
const input = (page: Page) => page.locator('input[role=combobox]');
test.describe('LQ Selection native ownership', () => {
    test.beforeEach(async ({ page }) => mount(page));
    test('pure Jinja/HTML/DOM fallback parity rejects the same unsafe props', async ({ page }) => {
        expect(fixture.isolated).toBe(true);
        for (const item of fixture.cases) {
            expect(item.error).toBeUndefined();
            const result = await page.evaluate(item => {
                const api = (window as any).selection, semantic = (n: Node): any => n.nodeType === Node.TEXT_NODE ? (n.textContent?.trim() ? { text: n.textContent } : null) : ({ tag: (n as Element).tagName.toLowerCase(), attrs: Object.fromEntries([...(n as Element).attributes].map(a => [a.name, a.value]).sort()), children: [...n.childNodes].map(semantic).filter(Boolean) });
                const parse = (html: string) => { const t = document.createElement('template'); t.innerHTML = html; return semantic(t.content.firstElementChild!); };
                return { jinja: parse(item.html), html: parse(api.selectionMarkup(item.kind, item.props)), dom: semantic(api.createSelection(item.kind, item.props)) };
            }, item); expect(result.html).toEqual(result.jinja); expect(result.dom).toEqual(result.jinja);
        }
        for (const item of fixture.invalid) { expect(item.error).toBeTruthy(); expect(await page.evaluate(item => { try { (window as any).selection.selectionProps(item.kind, item.props); return false; } catch { return true; } }, item)).toBe(true); }
    });
    for (const entry of ['jinja', 'html', 'dom']) test(`${entry} begins as a working native select and explicitly enhances without changing name/id or form`, async ({ page }) => {
        await page.evaluate(({ entry, item }) => { const w = window as any; let root; if (entry === 'dom') root = w.selection.createSelection(item.kind, item.props); else { const t = document.createElement('template'); t.innerHTML = entry === 'jinja' ? item.html : w.selection.selectionMarkup(item.kind, item.props); root = t.content.firstElementChild; } document.querySelector('#mount')!.append(root); w.select = root.querySelector('select'); }, { entry, item: fixture.cases[0] });
        await expect(page.locator('#course')).toBeVisible(); await page.locator('#course').selectOption('python');
        expect(await page.evaluate(() => new FormData(document.querySelector('#form') as HTMLFormElement).get('sourceName'))).toBe('python');
        await page.evaluate(() => { const w = window as any; w.binding = w.selection.bindSelection(w.select); });
        await expect(input(page)).toHaveValue('Python 程序设计'); expect(await page.locator('#course').getAttribute('data-source-name')).toBe('existing-contract');
        await page.getByText('课程', { exact: true }).click(); await expect(input(page)).toBeFocused();
        await input(page).press('ArrowDown'); await input(page).press('End'); await input(page).press('Enter'); await expect(input(page)).toHaveValue('计算机网络');
        expect(await page.evaluate(() => [...new FormData(document.querySelector('#form') as HTMLFormElement)])).toEqual([['sourceName', 'network']]);
        await page.evaluate(() => (window as any).binding.destroy()); await expect(page.locator('#course')).toBeVisible(); await expect(page.locator('label[for=course]')).toHaveCount(1);
    });
    test('combobox retains DOM focus, skips disabled options and commits one input/change', async ({ page }) => {
        await create(page); await input(page).focus(); await input(page).press('ArrowDown'); await expect(input(page)).toBeFocused(); await input(page).press('ArrowDown');
        await expect(input(page)).toHaveAttribute('aria-activedescendant', /option/); expect(await page.evaluate(() => (window as any).select.value)).toBe('');
        await input(page).press('ArrowDown'); expect(await page.evaluate(() => document.getElementById((document.querySelector('input[role=combobox]') as HTMLInputElement).getAttribute('aria-activedescendant')!)?.textContent)).toBe('计算机网络');
        await input(page).press('Enter'); await expect.poll(() => page.evaluate(() => [(window as any).changes, (window as any).inputs, (window as any).select.value])).toEqual([1, 1, 'network']); await expect(input(page)).toBeFocused();
        await input(page).press('ArrowDown'); await input(page).press('End'); await input(page).press('Enter'); expect(await page.evaluate(() => (window as any).changes)).toBe(1);
    });
    test('Escape and Tab cancel only the search, retain the committed value and let Tab proceed', async ({ page }) => {
        await create(page); await page.evaluate(() => { const w = window as any; w.select.value = 'python'; w.binding.refresh(); });
        await input(page).fill('网络'); await expect(page.getByRole('option', { name: '计算机网络' })).toBeVisible(); await input(page).press('Escape');
        await expect(input(page)).toHaveValue('Python 程序设计'); expect(await page.evaluate(() => (window as any).select.value)).toBe('python');
        await input(page).fill('不存在'); await expect(page.getByRole('status')).toHaveText('没有匹配选项'); await input(page).press('Tab'); await expect(page.locator('#submit')).toBeFocused(); await expect(input(page)).toHaveValue('Python 程序设计'); expect(await page.evaluate(() => (window as any).changes)).toBe(0);
    });
    test('IME query waits for composition end, emits one ticket and never submits on composing Enter', async ({ page }) => {
        await create(page, 0, false); await page.evaluate(() => { const w = window as any; w.binding = w.selection.bindSelection(w.select, { onQuery: (t: any) => w.tickets.push(t) }); });
        await input(page).focus(); await input(page).evaluate(n => { n.dispatchEvent(new CompositionEvent('compositionstart')); (n as HTMLInputElement).value = '网络'; n.dispatchEvent(new InputEvent('input', { bubbles: true, isComposing: true })); n.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, isComposing: true })); });
        expect(await page.evaluate(() => [(window as any).tickets.length, (window as any).submits])).toEqual([0, 0]);
        await input(page).evaluate(n => { n.dispatchEvent(new CompositionEvent('compositionend')); n.dispatchEvent(new InputEvent('input', { bubbles: true })); }); expect(await page.evaluate(() => (window as any).tickets.map((t: any) => t.query))).toEqual(['网络']);
    });
    test('required reportValidity and submit focus the visible proxy; destroy restores native validation', async ({ page }) => {
        await create(page); const errors: string[] = []; page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
        expect(await page.evaluate(() => (document.querySelector('#form') as HTMLFormElement).reportValidity())).toBe(false); await expect(input(page)).toBeFocused(); await expect(input(page)).toHaveAttribute('aria-invalid', 'true'); await expect(page.getByRole('status')).not.toBeEmpty();
        await page.locator('#submit').click(); expect(await page.evaluate(() => (window as any).submits)).toBe(0);
        await input(page).press('ArrowDown'); await input(page).press('End'); await input(page).press('Enter'); await page.locator('#submit').click(); expect(await page.evaluate(() => (window as any).submits)).toBe(1);
        await page.evaluate(() => { const w = window as any; w.binding.destroy(); w.select.value = ''; }); expect(await page.evaluate(() => (document.querySelector('#form') as HTMLFormElement).reportValidity())).toBe(false); await expect(page.locator('#course')).toBeFocused(); expect(errors.filter(e => /not focusable/i.test(e))).toEqual([]);
    });
    test('multiple listbox owns no second selected array, supports modifier-free Space and preserves native reset', async ({ page }) => {
        await create(page, 1); const list = page.getByRole('listbox'); await expect(list).toHaveAttribute('aria-multiselectable', 'true'); await list.focus(); await list.press('ArrowDown');
        expect(await page.evaluate(() => new FormData(document.querySelector('#form') as HTMLFormElement).getAll('people'))).toEqual(['a']); await list.press('Space');
        expect(await page.evaluate(() => new FormData(document.querySelector('#form') as HTMLFormElement).getAll('people'))).toEqual(['a', 'b']); expect(await page.evaluate(() => (window as any).changes)).toBe(1);
        const disabledBox = (await page.getByRole('option', { name: '陈同学' }).boundingBox())!; await page.mouse.click(disabledBox.x + disabledBox.width / 2, disabledBox.y + disabledBox.height / 2); expect(await page.evaluate(() => (window as any).changes)).toBe(1);
        await page.locator('#reset').click(); await expect.poll(() => page.evaluate(() => new FormData(document.querySelector('#form') as HTMLFormElement).getAll('people'))).toEqual(['a']);
        await list.focus(); await list.press('Home'); await list.press('Space'); expect(await page.evaluate(() => (document.querySelector('#form') as HTMLFormElement).reportValidity())).toBe(false); await expect(list).toBeFocused(); await expect(list).toHaveAttribute('aria-invalid', 'true');
        await list.focus(); await list.press('Control+a'); expect(await page.evaluate(() => new FormData(document.querySelector('#form') as HTMLFormElement).getAll('people'))).toEqual(['a', 'b']); await expect(page.getByRole('menu')).toHaveCount(0);
    });
    test('single listbox follows focus, while disabled fieldset keeps submitted values excluded', async ({ page }) => {
        await create(page, 2); await page.getByRole('listbox').focus(); await page.getByRole('listbox').press('Home'); expect(await page.evaluate(() => (window as any).select.value)).toBe('a');
        await page.evaluate(() => { const field = document.createElement('fieldset'); const mount = document.querySelector('#mount')!; mount.before(field); field.append(mount); field.disabled = true; }); await expect(page.getByRole('listbox')).toHaveAttribute('aria-disabled', 'true');
        expect(await page.evaluate(() => (window as any).select.value)).toBe('a'); expect(await page.evaluate(() => [...new FormData(document.querySelector('#form') as HTMLFormElement)])).toEqual([]);
    });
    test('async results reject stale tickets, preserve selected/default option identities and reset baseline', async ({ page }) => {
        await create(page, 0, false); await page.evaluate(() => { const w = window as any; w.select.value = 'python'; w.initial = [...w.select.options]; w.binding = w.selection.bindSelection(w.select, { onQuery: (t: any) => w.tickets.push(t) }); });
        await input(page).fill('old'); await input(page).fill('new');
        expect(await page.evaluate(() => { const w = window as any; const [old, current] = w.tickets; return [w.binding.setResults({ ...old, options: [{ value: 'old', label: '旧结果' }] }), w.binding.setResults({ ...current, options: [{ value: 'new', label: '新结果' }] }), w.select.value, w.select.options[1] === w.initial[1]]; })).toEqual([false, true, 'python', true]);
        await expect(page.getByRole('option', { name: '新结果' })).toBeVisible(); await page.getByRole('option', { name: '新结果' }).click(); await expect(input(page)).toHaveValue('新结果');
        await page.locator('#reset').click(); await expect(input(page)).toHaveValue('请选择'); expect(await page.evaluate(() => new FormData(document.querySelector('#form') as HTMLFormElement).get('sourceName'))).toBe(''); expect(await page.evaluate(() => (window as any).select.options[0] === (window as any).initial[0])).toBe(true);
    });
    test('disabled, clear, close and destroy each invalidate tickets without reopening or losing values', async ({ page }) => {
        await create(page, 0, false); await page.evaluate(() => { const w = window as any; w.binding = w.selection.bindSelection(w.select, { onQuery: (t: any) => w.tickets.push(t) }); });
        await input(page).fill('a'); await page.evaluate(() => (window as any).select.disabled = true); await expect(input(page)).toBeDisabled();
        expect(await page.evaluate(() => { const w = window as any; return w.binding.setResults({ ...w.tickets[0], options: [{ value: 'a', label: 'A' }] }); })).toBe(false);
        await page.evaluate(() => (window as any).select.disabled = false); await expect(input(page)).toBeEnabled(); await input(page).fill('b'); await input(page).fill('');
        expect(await page.evaluate(() => { const w = window as any; return w.binding.setResults({ ...w.tickets.at(-2), options: [] }); })).toBe(false);
        await input(page).press('Escape'); await expect(input(page)).toHaveAttribute('aria-expanded', 'false'); expect(await page.evaluate(() => { const w = window as any; return w.binding.setResults({ ...w.tickets.at(-1), options: [{ value: 'late', label: '迟到' }] }); })).toBe(false);
        await page.evaluate(() => (window as any).binding.destroy()); expect(await page.evaluate(() => { const w = window as any; return w.binding.setResults({ ...w.tickets.at(-1), options: [] }); })).toBe(false); await expect(page.locator('.lq-selection__popup')).toHaveCount(0);
    });
    test('multiple async no-results and failure preserve selected/defaultSelected values across reset', async ({ page }) => {
        await create(page, 1); await page.getByRole('listbox').focus(); await page.getByRole('listbox').press('ArrowDown'); await page.getByRole('listbox').press('Space');
        await page.evaluate(() => { const w = window as any; const ticket = w.binding.query('unknown'); w.binding.setResults({ ...ticket, options: [] }); w.binding.setResults({ ...ticket, status: 'error', message: '暂时不可用' }); });
        expect(await page.evaluate(() => new FormData(document.querySelector('#form') as HTMLFormElement).getAll('people'))).toEqual(['a', 'b']); await expect(page.getByRole('status')).toHaveText('暂时不可用');
        await page.locator('#reset').click(); await expect.poll(() => page.evaluate(() => new FormData(document.querySelector('#form') as HTMLFormElement).getAll('people'))).toEqual(['a']); await expect(page.getByRole('option')).toHaveCount(3);
    });
    test('failed and malformed results remain visible and never partially mutate native options', async ({ page }) => {
        await create(page, 0, false); await page.evaluate(() => { const w = window as any; w.binding = w.selection.bindSelection(w.select, { onQuery: (t: any) => w.tickets.push(t) }); }); await input(page).fill('x');
        expect(await page.evaluate(() => { const w = window as any; const before = w.select.innerHTML; try { w.binding.setResults({ ...w.tickets[0], options: [{ value: 'valid', label: 'A' }, { value: 'bad', label: {} }] }); } catch {} return before === w.select.innerHTML; })).toBe(true);
        await page.evaluate(() => { const w = window as any; w.binding.setResults({ ...w.tickets[0], status: 'error', message: '查找失败，请重试。' }); }); await expect(page.getByRole('status')).toHaveText('查找失败，请重试。'); expect(await page.evaluate(() => (window as any).select.value)).toBe('');
        expect(await page.evaluate(() => { const w = window as any; const node = document.createElement('div'); document.body.append(node); try { w.selection.createSelection('combobox', { id: 'node', label: 'x', children: node }); return false; } catch { const ok = node.parentNode === document.body; node.remove(); return ok; } })).toBe(true);
    });
    test('an initially empty async select stays unselected on results and resets to its original empty baseline', async ({ page }) => {
        await create(page, 3); await page.evaluate(() => { const w = window as any; const ticket = w.binding.query('a'); w.binding.setResults({ ...ticket, options: [{ value: 'a', label: 'A' }] }); w.binding.open(); });
        expect(await page.evaluate(() => (window as any).select.selectedIndex)).toBe(-1); await page.getByRole('option', { name: 'A', exact: true }).click(); expect(await page.evaluate(() => (window as any).select.value)).toBe('a');
        await page.locator('#reset').click(); await expect(input(page)).toHaveValue(''); expect(await page.evaluate(() => (window as any).select.selectedIndex)).toBe(-1);
    });
    test('synchronous change callbacks may reopen a new query or destroy without stale post-commit cleanup', async ({ page }) => {
        await create(page); await page.evaluate(() => { const w = window as any; w.select.addEventListener('change', () => { w.binding.query('Python'); w.binding.open(); }, { once: true }); });
        await input(page).press('ArrowDown'); await page.getByRole('option', { name: '计算机网络' }).click(); await expect(input(page)).toHaveValue('Python'); await expect(input(page)).toHaveAttribute('aria-expanded', 'true'); await expect(page.getByRole('option', { name: 'Python 程序设计' })).toBeVisible();
        await page.evaluate(() => { const w = window as any; w.select.addEventListener('input', () => w.binding.destroy(), { once: true }); }); await page.getByRole('option', { name: 'Python 程序设计' }).click(); await expect(page.locator('.lq-selection')).toHaveCount(0); expect(await page.evaluate(() => (window as any).select.value)).toBe('python');
    });
    test('native and ordinary modal use the shared layer, actual option clicks retain input focus and owner exit clears popup', async ({ page }) => {
        await create(page, 0, false); await page.evaluate(() => { const parent = document.querySelector('#native') as HTMLDialogElement; parent.append(document.querySelector('#form')!); parent.showModal(); (window as any).binding = (window as any).selection.bindSelection((window as any).select); });
        await input(page).press('ArrowDown'); expect(await page.locator('.lq-selection__popup').evaluate(n => n.parentElement?.id)).toBe('native'); await page.getByRole('option', { name: '计算机网络' }).click(); await expect(input(page)).toBeFocused();
        await input(page).press('ArrowDown'); await input(page).press('Escape'); await expect(input(page)).toHaveAttribute('aria-expanded', 'false'); expect(await page.locator('#native').evaluate(n => (n as HTMLDialogElement).open)).toBe(true);
        await page.evaluate(() => { const w = window as any; w.binding.destroy(); const parent = document.querySelector('#native') as HTMLDialogElement; parent.close(); document.body.append(document.querySelector('#form')!); const shell = document.createElement('section'); shell.setAttribute('aria-label', '父层'); shell.append(document.querySelector('#form')!); w.parent = w.layer.open(shell, { type: 'modal' }); w.binding = w.selection.bindSelection(w.select); w.binding.open(); w.parent.destroy(); });
        await expect(page.locator('.lq-selection__popup,.lq-selection')).toHaveCount(0); expect(await page.evaluate(() => (window as any).layer.top())).toBeNull();
    });
    test('twenty lifecycles restore native DOM/attributes and release timers/listeners/observers across duplicate URLs', async ({ page }) => {
        await create(page, 0, false);
        const result = await page.evaluate(async () => {
            const w = window as any; w.layer.destroy(); const native = w.select, parent = native.parentNode, next = native.nextSibling, initial = native.outerHTML, labelFor = native.labels[0].getAttribute('for');
            const timers = new Set<number>(), listeners = new Set<any>(); let observers = 0; const set = window.setTimeout, clear = window.clearTimeout, add = document.addEventListener, remove = document.removeEventListener, NativeObserver = window.MutationObserver;
            window.setTimeout = ((fn: (...args: any[]) => void, delay?: number, ...args: any[]) => { const id = set(() => { timers.delete(id); fn(...args); }, delay); timers.add(id); return id; }) as typeof set; window.clearTimeout = ((id: number) => { timers.delete(id); clear(id); }) as typeof clear;
            document.addEventListener = function(type: string, fn: any, opts?: any) { listeners.add(fn); return add.call(this, type, fn, opts); } as typeof add; document.removeEventListener = function(type: string, fn: any, opts?: any) { listeners.delete(fn); return remove.call(this, type, fn, opts); } as typeof remove;
            window.MutationObserver = class extends NativeObserver { tracked = false; observe(...args: Parameters<MutationObserver['observe']>) { if (!this.tracked) { this.tracked = true; observers++; } super.observe(...args); } disconnect() { if (this.tracked) { this.tracked = false; observers--; } super.disconnect(); } };
            const other = await import('/static/js/lq/selection.js?duplicate');
            for (let n = 0; n < 20; n++) { const b = w.selection.bindSelection(native); if (other.bindSelection(native) !== b) throw new Error('duplicate owner'); b.open(); if (n % 2) await b.close(); b.destroy(); }
            const result = { roots: document.querySelectorAll('.lq-selection,.lq-selection__popup').length, timers: timers.size, listeners: listeners.size, observers, html: native.outerHTML === initial, parent: native.parentNode === parent, next: native.nextSibling === next, label: native.labels[0].getAttribute('for') === labelFor };
            window.setTimeout = set; window.clearTimeout = clear; document.addEventListener = add; document.removeEventListener = remove; window.MutationObserver = NativeObserver; return result;
        }); expect(result).toEqual({ roots: 0, timers: 0, listeners: 0, observers: 0, html: true, parent: true, next: true, label: true });
    });
    test('coarse touch targets and forced reduced-motion fallback remain usable', async ({ browser }) => {
        const context = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, reducedMotion: 'reduce', forcedColors: 'active' });
        try { const page = await context.newPage(); await mount(page); await create(page); await page.getByRole('button', { name: '展开课程' }).tap(); await expect(page.locator('.lq-selection__popup')).toHaveAttribute('data-lq-layer-state', 'open'); for (const height of await page.getByRole('option').evaluateAll(nodes => nodes.map(n => n.getBoundingClientRect().height))) expect(height).toBeGreaterThanOrEqual(44); expect(await page.locator('.lq-selection__popup').evaluate(n => parseFloat(getComputedStyle(n).transitionDuration))).toBeLessThanOrEqual(.001); }
        finally { await context.close(); }
    });
    for (const palette of ['indigo', 'sky', 'mint', 'violet', 'rose', 'teal']) for (const appearance of ['light', 'dark']) test(`${palette}/${appearance} mobile combobox and multiple listbox use real CSS and pass axe`, async ({ page }) => {
        await page.setViewportSize({ width: 390, height: 844 }); await page.evaluate(({ palette, appearance }) => { document.documentElement.dataset.uiPalette = palette; document.documentElement.dataset.appearance = appearance; }, { palette, appearance }); await create(page);
        await input(page).press('ArrowDown'); await expect(page.locator('.lq-selection__popup')).toHaveAttribute('data-lq-layer-state', 'open'); const box = await page.locator('.lq-selection__popup').boundingBox(); expect(box!.x).toBeGreaterThanOrEqual(12); expect(box!.x + box!.width).toBeLessThanOrEqual(378);
        expect((await new AxeBuilder({ page }).analyze()).violations.filter(v => ['serious', 'critical'].includes(v.impact || ''))).toEqual([]);
        if (palette === 'teal') { fs.mkdirSync('.codex-temp/lq-audit/s2/selection', { recursive: true }); await page.screenshot({ path: `.codex-temp/lq-audit/s2/selection/${appearance}-combobox-390.png` }); }
        await page.evaluate(() => { (window as any).binding.destroy(); document.querySelector('#mount')!.replaceChildren(); }); await create(page, 1); await page.getByRole('listbox').focus(); expect((await new AxeBuilder({ page }).analyze()).violations.filter(v => ['serious', 'critical'].includes(v.impact || ''))).toEqual([]);
        if (palette === 'teal') await page.screenshot({ path: `.codex-temp/lq-audit/s2/selection/${appearance}-listbox-390.png` });
    });
});
