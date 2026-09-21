import { test, expect, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
const python = process.env.LQ_TEST_PYTHON || (process.platform === 'win32' ? 'venv/Scripts/python.exe' : 'venv/bin/python');
const fixture = JSON.parse(execFileSync(python, ['tests/e2e/scripts/render_lq_menu_tooltip.py'], { encoding: 'utf8' }));
async function mount(page: Page) {
    await page.context().route('**/*', route => {
        const url = new URL(route.request().url());
        if (url.origin !== 'https://lq-menu-tooltip.test') return route.abort();
        if (url.pathname === '/details') return route.fulfill({ contentType: 'text/html; charset=utf-8', body: '<!doctype html><html lang="zh"><meta charset="utf-8"><title>菜单链接目标</title><body><h1>链接目标</h1></body></html>' });
        if (url.pathname.startsWith('/static/js/') || ['/static/css/tailwind-app.css', '/static/css/lq/components/menus.css', '/static/css/lq/components/tooltips.css'].includes(url.pathname)) {
            const file = path.resolve(`.${url.pathname}`); if (file.startsWith(path.resolve('static') + path.sep) && fs.existsSync(file)) return route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body: fs.readFileSync(file) });
        }
        if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh-CN" data-theme="lanshare" data-ui-palette="teal" data-appearance="light" data-lq-glass="tinted"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>LQ menu tooltip</title><link rel="stylesheet" href="/static/css/tailwind-app.css"></head><body><main style="padding:80px 24px"><h1>动作菜单与图标名称</h1><button id="before">前一个</button><button id="opener">文档操作</button><button id="after">后一个</button><div id="icons"></div><div id="source"></div><p id="help">既有描述</p><p id="later">后来添加的描述</p></main><dialog id="native"><button id="native-opener">原生操作</button><button id="native-after">原生后继</button></dialog><script type="module">import * as menus from '/static/js/lq/menus.js';import * as tips from '/static/js/lq/tooltips.js';import {createComponent} from '/static/js/lq/components.js';import {getLayerSystem} from '/static/js/lq/layer.js';window.menus=menus;window.tips=tips;window.createComponent=createComponent;window.layer=getLayerSystem(document);window.actions=[];window.errors=[];const icon=createComponent('button',{icon:'settings',variant:'ghost',attrs:{id:'icon','aria-label':'设置',title:'原始 title','aria-describedby':'help'}});document.querySelector('#icons').append(icon);document.body.dataset.ready='true';</script></body></html>` });
        return route.abort();
    });
    await page.goto('https://lq-menu-tooltip.test/'); await expect(page.locator('body')).toHaveAttribute('data-ready', 'true');
}
async function menu(page: Page, options = {}) {
    await page.evaluate(({ props, options }) => { const w = window as any; w.root = w.menus.createMenu(props); w.binding = w.menus.bindMenu(document.querySelector('#opener'), w.root, { onAction: (id: string) => w.actions.push(id), ...options }); }, { props: fixture.menus[0].props, options });
}
async function tooltip(page: Page) { await page.evaluate(props => { const w = window as any; w.tip = w.tips.bindTooltip(document.querySelector('#icon'), props); }, fixture.tooltips[0].props); }

test.describe('LQ Menu and Tooltip', () => {
    test.beforeEach(async ({ page }) => mount(page));
    test('real Jinja, safe HTML and DOM have the same semantics and invalid-input boundary', async ({ page }) => {
        expect(fixture.isolated).toBe(true);
        for (const [group, module, prefix] of [['menus', 'menus', 'menu'], ['tooltips', 'tips', 'tooltip']]) {
            for (const item of fixture[group]) {
                expect(item.error).toBeUndefined();
                const result = await page.evaluate(({ item, module, prefix }) => {
                    const api = (window as any)[module];
                    const semantic = (node: Node): any => node.nodeType === Node.TEXT_NODE ? (node.textContent?.trim() ? { text: node.textContent } : null) : ({ tag: (node as Element).tagName.toLowerCase(), attrs: Object.fromEntries([...(node as Element).attributes].map(a => [a.name, a.value]).sort()), children: [...node.childNodes].map(semantic).filter(Boolean) });
                    const parse = (html: string) => { const t = document.createElement('template'); t.innerHTML = html; return semantic(t.content.firstElementChild!); };
                    return { jinja: parse(item.html), html: parse(api[`${prefix}Markup`](item.props)), dom: semantic(api[prefix === 'menu' ? 'createMenu' : 'createTooltip'](item.props)) };
                }, { item, module, prefix });
                expect(result.html).toEqual(result.jinja); expect(result.dom).toEqual(result.jinja);
            }
            for (const item of fixture[`invalid${group[0].toUpperCase()}${group.slice(1)}`]) {
                expect(item.error).toBeTruthy(); expect(await page.evaluate(({ props, module, prefix }) => { try { (window as any)[module][`${prefix}Markup`](props); return false; } catch { return true; } }, { props: item.props, module, prefix })).toBe(true);
            }
        }
    });
    for (const entry of ['jinja', 'html', 'element']) test(`${entry} menu and tooltip entrances actually open and restore their original node`, async ({ page }) => {
        await page.evaluate(({ entry, menu, tip }) => {
            const w = window as any, parse = (html: string) => { const t = document.createElement('template'); t.innerHTML = html; const n = t.content.firstElementChild!; document.querySelector('#source')!.append(n); return n; };
            const root = entry === 'element' ? w.menus.createMenu(menu.props) : parse(entry === 'jinja' ? menu.html : w.menus.menuMarkup(menu.props));
            const tipRoot = entry === 'element' ? w.tips.createTooltip(tip.props) : parse(entry === 'jinja' ? tip.html : w.tips.tooltipMarkup(tip.props));
            w.root = root; w.binding = w.menus.bindMenu(document.querySelector('#opener'), root, { onAction: (id: string) => w.actions.push(id) }); w.tip = w.tips.bindTooltip(document.querySelector('#icon'), tipRoot);
        }, { entry, menu: fixture.menus[0], tip: fixture.tooltips[0] });
        await page.locator('#opener').click(); await expect(page.getByRole('menuitem', { name: '编辑' })).toBeFocused();
        await page.keyboard.press(entry === 'html' ? 'Space' : 'Enter'); await expect.poll(() => page.evaluate(() => (window as any).actions)).toEqual(['edit']); await expect(page.locator('#opener')).toBeFocused();
        await page.locator('#icon').focus(); await expect(page.getByRole('tooltip')).toBeVisible(); await expect(page.locator('#icon')).toBeFocused();
        await page.keyboard.press('Escape'); await expect(page.getByRole('tooltip')).toBeHidden();
        await page.evaluate(() => { (window as any).binding.destroy(); (window as any).tip.destroy(); });
        expect(await page.locator('#source').locator('> *').count()).toBe(entry === 'element' ? 0 : 2);
    });
    test('arrows, Home/End, repeated typeahead and IME retain menu semantics including disabled focus', async ({ page }) => {
        await menu(page); await page.locator('#opener').focus(); await page.keyboard.press('ArrowUp'); await expect(page.getByRole('menuitem', { name: '删除' })).toBeFocused();
        await page.keyboard.press('Home'); await page.keyboard.press('ArrowDown'); await expect(page.getByRole('menuitem', { name: '复制' })).toBeFocused();
        await page.keyboard.press('Enter'); expect(await page.evaluate(() => (window as any).actions)).toEqual([]); await expect(page.getByRole('menu')).toBeVisible();
        await page.locator('[role=menuitem]').nth(1).evaluate(n => n.dispatchEvent(new KeyboardEvent('keydown', { key: 'End', bubbles: true, isComposing: true })));
        await expect(page.getByRole('menuitem', { name: '复制' })).toBeFocused(); await page.keyboard.press('End'); await expect(page.getByRole('menuitem', { name: '删除' })).toBeFocused();
        await page.evaluate(() => { const w = window as any; w.binding.destroy(); w.root = w.menus.createMenu({ id: 'english', label: 'Actions', items: [{ id: 'a', label: 'Apple' }, { id: 'b', label: 'Apricot' }, { id: 'c', label: 'Banana' }] }); w.binding = w.menus.bindMenu(document.querySelector('#opener'), w.root); w.binding.open(); });
        await page.keyboard.press('a'); await expect(page.getByRole('menuitem', { name: 'Apricot' })).toBeFocused(); await page.keyboard.press('a'); await expect(page.getByRole('menuitem', { name: 'Apple' })).toBeFocused();
        await page.keyboard.press('Escape'); await expect(page.getByRole('menu')).toBeHidden(); await expect(page.locator('#opener')).toBeFocused();
    });
    test('Tab exits to the trigger successor and Shift Tab to predecessor without closing a parent', async ({ page }) => {
        await menu(page); await page.locator('#opener').click(); await page.keyboard.press('Tab'); await expect(page.locator('#after')).toBeFocused(); await expect(page.getByRole('menu')).toBeHidden();
        await page.locator('#opener').click(); await page.keyboard.press('Shift+Tab'); await expect(page.locator('#before')).toBeFocused();
    });
    test('vetoed Tab restores later Escape return-focus behavior', async ({ page }) => {
        await menu(page); await page.evaluate(() => { const w = window as any; w.binding.destroy(); w.binding = w.menus.bindMenu(document.querySelector('#opener'), w.root, { beforeClose: (reason: string) => reason !== 'tab' }); });
        await page.locator('#opener').click(); await page.keyboard.press('Tab'); await expect(page.getByRole('menu')).toHaveAttribute('data-lq-layer-state', 'open'); await expect(page.getByRole('menuitem', { name: '编辑' })).toBeFocused();
        await page.keyboard.press('Escape'); await expect(page.getByRole('menu')).toBeHidden(); await expect(page.locator('#opener')).toBeFocused();
    });
    test('veto/reject do not run commands, repeat clicks run once, reopen invalidates a delayed old command', async ({ page }) => {
        await menu(page); await page.evaluate(() => { const w = window as any; w.binding.destroy(); w.allowed = false; w.binding = w.menus.bindMenu(document.querySelector('#opener'), w.root, { beforeClose: () => w.reject ? Promise.reject(new Error('no')) : w.allowed, onAction: (id: string) => w.actions.push(id) }); });
        await page.locator('#opener').click(); await page.getByRole('menuitem', { name: '编辑' }).click(); await expect(page.getByRole('menu')).toHaveAttribute('data-lq-layer-state', 'open'); expect(await page.evaluate(() => (window as any).actions)).toEqual([]);
        await page.evaluate(() => { (window as any).reject = true; }); await page.getByRole('menuitem', { name: '编辑' }).click(); expect(await page.evaluate(() => (window as any).actions)).toEqual([]);
        await page.evaluate(() => { const w = window as any; w.reject = false; w.allowed = new Promise(resolve => w.decision = resolve); }); await page.getByRole('menuitem', { name: '编辑' }).click();
        await page.evaluate(() => { const w = window as any; w.binding.open(); w.decision(true); }); await expect(page.getByRole('menu')).toHaveAttribute('data-lq-layer-state', 'open'); expect(await page.evaluate(() => (window as any).actions)).toEqual([]);
        await page.evaluate(() => { const w = window as any; w.allowed = true; const n = document.querySelector('[data-lq-menu-item=edit]') as HTMLElement; n.click(); n.click(); }); await expect.poll(() => page.evaluate(() => (window as any).actions)).toEqual(['edit']);
    });
    test('onClose reentry and owner destruction cancel old commands without reviving detached pages', async ({ page }) => {
        await menu(page); await page.evaluate(() => { const w = window as any; w.binding.destroy(); w.binding = w.menus.bindMenu(document.querySelector('#opener'), w.root, { onAction: (id: string) => w.actions.push(id), onClose: () => w.binding.open() }); w.binding.open(); });
        await page.getByRole('menuitem', { name: '编辑' }).click(); await expect(page.getByRole('menu')).toHaveAttribute('data-lq-layer-state', 'open'); expect(await page.evaluate(() => (window as any).actions)).toEqual([]);
        await page.evaluate(() => document.querySelector('#opener')!.remove()); await expect(page.getByRole('menu')).toHaveCount(0); expect(await page.evaluate(() => (window as any).layer.top())).toBeNull();
    });
    test('native links retain modifier and new-page activation while command veto remains separate', async ({ page, context }) => {
        await menu(page); await page.evaluate(() => { const w = window as any; w.binding.destroy(); w.root.querySelector('a').setAttribute('href', '/details'); w.binding = w.menus.bindMenu(document.querySelector('#opener'), w.root, { beforeClose: () => false }); });
        await page.locator('#opener').click(); const popup = context.waitForEvent('page'); await page.getByRole('menuitem', { name: '查看' }).click(); const newPage = await popup; await expect(newPage.getByRole('heading')).toHaveText('链接目标'); expect(newPage.url()).toContain('/details'); await newPage.close();
        await expect(page.getByRole('menu')).toBeHidden(); expect(await page.evaluate(() => (window as any).actions)).toEqual([]);
        await page.locator('#opener').click();
        // Chrome's Ctrl+_blank first request bypasses context.route even for a bare
        // anchor. Consume at the fixture's last bubble listener after observing the
        // product's native event; no network request escapes this fixture.
        await page.evaluate(() => document.addEventListener('click', event => { if ((event.target as Element).closest('[role=menuitem]')) { (window as any).nativeEvent = { ctrl: event.ctrlKey, prevented: event.defaultPrevented }; event.preventDefault(); } }, { once: true }));
        await page.getByRole('menuitem', { name: '查看' }).click({ modifiers: ['Control'] }); expect(await page.evaluate(() => (window as any).nativeEvent)).toEqual({ ctrl: true, prevented: false });
    });
    test('native modal hosts the physical menu, Tab stays in native context and action returns focus', async ({ page }) => {
        await page.evaluate(props => { const w = window as any; (document.querySelector('#native') as HTMLDialogElement).showModal(); w.root = w.menus.createMenu(props); w.binding = w.menus.bindMenu(document.querySelector('#native-opener'), w.root, { onAction: (id: string) => w.actions.push(id) }); }, fixture.menus[0].props);
        await page.locator('#native-opener').click(); expect(await page.getByRole('menu').evaluate(n => n.parentElement?.id)).toBe('native'); await page.getByRole('menuitem', { name: '编辑' }).click(); await expect.poll(() => page.evaluate(() => (window as any).actions)).toEqual(['edit']); await expect(page.locator('#native-opener')).toBeFocused();
        await page.locator('#native-opener').click(); await page.keyboard.press('Tab'); await expect(page.locator('#native-after')).toBeFocused(); expect(await page.locator('#native').evaluate(n => (n as HTMLDialogElement).open)).toBe(true);
        await page.locator('#native-opener').click(); await page.keyboard.press('Shift+Tab'); await expect(page.locator('#native-after')).toBeFocused();
    });
    test('tooltip hover waits 400ms, remains across tooltip hover and uses one noninteractive description', async ({ page }) => {
        await tooltip(page); await page.locator('#icon').hover(); await page.waitForTimeout(240); await expect(page.getByRole('tooltip')).toHaveCount(0); await expect(page.getByRole('tooltip')).toBeVisible();
        expect(await page.locator('#icon').getAttribute('aria-describedby')).toBe('help icon-name'); await expect(page.getByRole('tooltip').locator('a,button,input')).toHaveCount(0);
        await page.getByRole('tooltip').hover(); await page.waitForTimeout(160); await expect(page.getByRole('tooltip')).toBeVisible(); await page.locator('#after').hover(); await expect(page.getByRole('tooltip')).toBeHidden();
    });
    test('keyboard tooltip is immediate; Escape leaves parent and focus intact, title/tokens are precisely restored', async ({ page }) => {
        await tooltip(page); await page.locator('#icon').focus(); await expect(page.getByRole('tooltip')).toBeVisible(); await expect(page.locator('#icon')).toBeFocused();
        expect(await page.locator('#icon').getAttribute('title')).toBeNull(); await page.evaluate(() => document.querySelector('#icon')!.setAttribute('aria-describedby', 'help icon-name later'));
        await page.keyboard.press('Escape'); await expect(page.getByRole('tooltip')).toBeHidden(); await expect(page.locator('#icon')).toHaveAttribute('aria-describedby', 'help later');
        await page.evaluate(() => (window as any).tip.destroy()); await expect(page.locator('#icon')).toHaveAttribute('title', '原始 title'); await expect(page.locator('#icon')).toHaveAttribute('aria-label', '设置');
    });
    test('tooltip duplicate URLs share ownership, late hover is cancelled and functional explain is rejected', async ({ page }) => {
        await tooltip(page);
        expect(await page.evaluate(async props => { const w = window as any; const module = await import('/static/js/lq/tooltips.js?duplicate'); return module.bindTooltip(document.querySelector('#icon'), props) === w.tip; }, fixture.tooltips[0].props)).toBe(true);
        await page.locator('#icon').hover(); await page.evaluate(() => (window as any).tip.destroy()); await page.waitForTimeout(450); await expect(page.getByRole('tooltip')).toHaveCount(0);
        expect(await page.evaluate(props => { const w = window as any; const node = document.querySelector('#icon')!; node.setAttribute('data-explain', '功能说明'); try { w.tips.bindTooltip(node, props); return false; } catch { return true; } }, fixture.tooltips[0].props)).toBe(true);
    });
    test('native tooltip is pointer reachable; deletion clears its layer and external attribute changes survive destroy', async ({ page }) => {
        await page.evaluate(props => { const w = window as any; const parent = document.querySelector('#native') as HTMLDialogElement; parent.append(document.querySelector('#icon')!); parent.showModal(); w.tip = w.tips.bindTooltip(document.querySelector('#icon'), props); w.tip.show(); }, fixture.tooltips[0].props);
        await expect(page.getByRole('tooltip')).toBeVisible(); expect(await page.getByRole('tooltip').evaluate(n => n.parentElement?.id)).toBe('native');
        await page.keyboard.press('Escape'); await expect(page.getByRole('tooltip')).toBeHidden(); expect(await page.locator('#native').evaluate(n => (n as HTMLDialogElement).open)).toBe(true);
        await page.evaluate(() => { const w = window as any; const n = document.querySelector('#icon')!; n.setAttribute('title', '新 title'); w.tip.destroy(); }); await expect(page.locator('#icon')).toHaveAttribute('title', '新 title');
        await page.evaluate(props => { const w = window as any; w.tip = w.tips.bindTooltip(document.querySelector('#icon'), props); w.tip.show(); document.querySelector('#icon')!.remove(); }, fixture.tooltips[0].props); await expect(page.getByRole('tooltip')).toHaveCount(0); expect(await page.evaluate(() => (window as any).layer.top())).toBeNull();
    });
    test('twenty bind/open/close/destroy cycles release timers, listeners and observers, including duplicate imports', async ({ page }) => {
        const result = await page.evaluate(async ({ menu, tip }) => {
            const w = window as any; w.layer.destroy(); const timers = new Set<number>(), listeners = new Set<any>(); let observers = 0;
            const set = window.setTimeout, clear = window.clearTimeout, add = document.addEventListener, remove = document.removeEventListener, NativeObserver = window.MutationObserver;
            window.setTimeout = ((fn: (...args: any[]) => void, delay?: number, ...args: any[]) => { const id = set(() => { timers.delete(id); fn(...args); }, delay); timers.add(id); return id; }) as typeof set;
            window.clearTimeout = ((id: number) => { timers.delete(id); clear(id); }) as typeof clear;
            document.addEventListener = function(type: string, fn: any, opts?: any) { listeners.add(fn); return add.call(this, type, fn, opts); } as typeof add;
            document.removeEventListener = function(type: string, fn: any, opts?: any) { listeners.delete(fn); return remove.call(this, type, fn, opts); } as typeof remove;
            window.MutationObserver = class extends NativeObserver { tracked = false; observe(...args: Parameters<MutationObserver['observe']>) { if (!this.tracked) { this.tracked = true; observers++; } super.observe(...args); } disconnect() { if (this.tracked) { this.tracked = false; observers--; } super.disconnect(); } };
            const other = await import('/static/js/lq/menus.js?duplicate');
            for (let index = 0; index < 20; index++) {
                const root = w.menus.createMenu(menu), m = w.menus.bindMenu(document.querySelector('#opener'), root); if (other.bindMenu(document.querySelector('#opener'), root) !== m) throw new Error('not singleton');
                m.open(); root.dispatchEvent(new KeyboardEvent('keydown', { key: 'a', bubbles: true })); if (index % 2) await m.close(); m.destroy();
                const t = w.tips.bindTooltip(document.querySelector('#icon'), tip); t.show(); if (index % 2) await t.hide(); t.destroy();
            }
            const result = { roots: document.querySelectorAll('.lq-menu,.lq-tooltip').length, timers: timers.size, listeners: listeners.size, observers, overflow: document.body.style.overflow };
            window.setTimeout = set; window.clearTimeout = clear; document.addEventListener = add; document.removeEventListener = remove; window.MutationObserver = NativeObserver; return result;
        }, { menu: fixture.menus[0].props, tip: fixture.tooltips[0].props });
        expect(result).toEqual({ roots: 0, timers: 0, listeners: 0, observers: 0, overflow: '' });
    });
    test('forced colors and reduced motion retain menu and tooltip visibility without extra motion', async ({ page }) => {
        await page.emulateMedia({ reducedMotion: 'reduce', forcedColors: 'active' }); await menu(page); await tooltip(page); await page.locator('#opener').click();
        expect(await page.getByRole('menu').evaluate(n => parseFloat(getComputedStyle(n).transitionDuration))).toBeLessThanOrEqual(0.001); await page.keyboard.press('Escape'); await page.locator('#icon').focus();
        await expect(page.getByRole('tooltip')).toBeVisible(); expect(await page.getByRole('tooltip').evaluate(n => parseFloat(getComputedStyle(n).transitionDuration))).toBeLessThanOrEqual(0.001);
    });
    test('a DOM root has one trigger owner, and untouched description whitespace survives hide plus destroy', async ({ page }) => {
        await menu(page); await tooltip(page);
        expect(await page.evaluate(() => { const w = window as any; try { w.menus.bindMenu(document.querySelector('#after'), w.root); return false; } catch { return true; } })).toBe(true);
        expect(await page.evaluate(() => { const w = window as any; const other = w.createComponent('button', { icon: 'info', attrs: { 'aria-label': '信息' } }); document.body.append(other); try { w.tips.bindTooltip(other, w.tip.root); return false; } catch { other.remove(); return true; } })).toBe(true);
        await page.evaluate(() => { const w = window as any; w.tip.destroy(); document.querySelector('#icon')!.setAttribute('aria-describedby', '  help   later '); w.tip = w.tips.bindTooltip(document.querySelector('#icon'), { id: 'precise', text: '设置' }); w.tip.show(); });
        await page.evaluate(async () => { const w = window as any; await w.tip.hide(); w.tip.destroy(); }); await expect(page.locator('#icon')).toHaveAttribute('aria-describedby', '  help   later ');
    });
    test('ordinary modal owns menu and tooltip Escape independently, with Tab returning inside the parent', async ({ page }) => {
        await page.evaluate(props => {
            const w = window as any; const parent = document.createElement('section'); parent.setAttribute('aria-label', '父层'); parent.append(document.querySelector('#opener')!, document.querySelector('#after')!, document.querySelector('#icon')!); w.parent = w.layer.open(parent, { type: 'modal' });
            w.root = w.menus.createMenu(props); w.binding = w.menus.bindMenu(document.querySelector('#opener'), w.root); w.tip = w.tips.bindTooltip(document.querySelector('#icon'), { id: 'inside', text: '设置' });
        }, fixture.menus[0].props);
        await page.locator('#opener').click(); await page.keyboard.press('Tab'); await expect(page.locator('#after')).toBeFocused(); await expect(page.getByRole('dialog', { name: '父层' })).toBeVisible();
        await page.locator('#icon').focus(); await expect(page.getByRole('tooltip')).toBeVisible(); await page.keyboard.press('Escape'); await expect(page.getByRole('tooltip')).toBeHidden(); await expect(page.locator('#icon')).toBeFocused(); await expect(page.getByRole('dialog', { name: '父层' })).toBeVisible();
    });
    test('coarse-pointer menu items keep 44px touch targets', async ({ browser }) => {
        const context = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
        try { const page = await context.newPage(); await mount(page); await menu(page); await page.locator('#opener').tap(); await expect(page.getByRole('menu')).toHaveAttribute('data-lq-layer-state', 'open'); expect(await page.evaluate(() => matchMedia('(pointer: coarse)').matches)).toBe(true); for (const height of await page.getByRole('menuitem').evaluateAll(nodes => nodes.map(n => n.getBoundingClientRect().height))) expect(height).toBeGreaterThanOrEqual(44); }
        finally { await context.close(); }
    });
    for (const palette of ['indigo', 'sky', 'mint', 'violet', 'rose', 'teal']) for (const appearance of ['light', 'dark']) test(`${palette}/${appearance} mobile long menu and tooltip use real CSS, remain in viewport and pass axe`, async ({ page }) => {
        await page.setViewportSize({ width: 390, height: 844 }); await page.evaluate(({ palette, appearance }) => { document.documentElement.dataset.uiPalette = palette; document.documentElement.dataset.appearance = appearance; }, { palette, appearance });
        await menu(page); await tooltip(page); await page.evaluate(() => { const label = document.querySelector('[data-lq-menu-item=edit] .lq-btn__label'); if (label) label.textContent = '编辑当前课堂的完整文档与保留草稿内容'.repeat(3); else (window as any).root.querySelector('.lq-btn__label').textContent = '编辑当前课堂的完整文档与保留草稿内容'.repeat(3); }); await page.locator('#opener').click();
        const box = await page.getByRole('menu').boundingBox(); expect(box!.x).toBeGreaterThanOrEqual(12); expect(box!.x + box!.width).toBeLessThanOrEqual(378);
        expect((await new AxeBuilder({ page }).analyze()).violations.filter(v => ['serious', 'critical'].includes(v.impact || ''))).toEqual([]);
        await page.keyboard.press('Escape'); await page.locator('#icon').focus(); await expect(page.getByRole('tooltip')).toBeVisible();
        expect((await new AxeBuilder({ page }).analyze()).violations.filter(v => ['serious', 'critical'].includes(v.impact || ''))).toEqual([]);
        if (palette === 'teal') { fs.mkdirSync('.codex-temp/lq-audit/s2/menu-tooltip', { recursive: true }); await expect(page.getByRole('tooltip')).toHaveAttribute('data-lq-layer-state', 'open'); await page.screenshot({ path: `.codex-temp/lq-audit/s2/menu-tooltip/${appearance}-tooltip-390.png` }); await page.locator('#opener').click(); await expect(page.getByRole('menu')).toHaveAttribute('data-lq-layer-state', 'open'); await page.screenshot({ path: `.codex-temp/lq-audit/s2/menu-tooltip/${appearance}-menu-390.png` }); }
    });
});
