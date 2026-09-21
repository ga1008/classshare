import { test, expect, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
const python = process.env.LQ_TEST_PYTHON || (process.platform === 'win32' ? 'venv/Scripts/python.exe' : 'venv/bin/python');
const fixture = JSON.parse(execFileSync(python, ['tests/e2e/scripts/render_lq_workspace.py'], { encoding: 'utf8' }));
async function mount(page: Page) {
    await page.route('**/*', route => {
        const url = new URL(route.request().url()); if (url.origin !== 'https://lq-workspace.test') return route.abort();
        if (url.pathname.startsWith('/static/js/') || ['/static/css/tailwind-app.css'].includes(url.pathname)) {
            const file = path.resolve(`.${url.pathname}`); if (file.startsWith(path.resolve('static') + path.sep) && fs.existsSync(file)) return route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body: fs.readFileSync(file) });
        }
        if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh-CN" data-theme="lanshare" data-ui-palette="teal" data-appearance="light" data-lq-glass="tinted"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>LQ Workspace</title><link rel="stylesheet" href="/static/css/tailwind-app.css"></head><body><main style="padding:24px"><h1>材料阅读工作区</h1><form id="work-form"><div id="mount"></div><button type="submit" id="submit">提交</button><button type="button" id="after">后继操作</button></form></main><script type="module">import * as workspace from '/static/js/lq/workspace.js';import * as navigation from '/static/js/lq/navigation.js';import {createComponent} from '/static/js/lq/components.js';window.workspace=workspace;window.navigation=navigation;window.createComponent=createComponent;window.resizes=[];window.frameLoads=0;window.submitCount=0;document.querySelector('#work-form').addEventListener('submit',e=>{e.preventDefault();window.submitCount++});document.body.dataset.ready='true';</script></body></html>` });
        return route.abort();
    });
    await page.goto('https://lq-workspace.test/'); await expect(page.locator('body')).toHaveAttribute('data-ready', 'true');
}
async function create(page: Page, entry = 'dom', enhance = true) {
    await page.evaluate(({ item, entry, enhance }) => {
        const w = window as any, side = document.createElement('div');
        const label = document.createElement('label'); label.htmlFor = 'side-input'; label.textContent = '目录备注';
        const input = document.createElement('input'); input.id = 'side-input'; input.name = 'sideDraft'; input.className = 'lq-input'; input.value = '未保存的目录草稿'; side.append(label, input);
        const content = document.createElement('div'), mainLabel = document.createElement('label'), mainInput = document.createElement('input'); mainLabel.htmlFor = 'main-input'; mainLabel.textContent = '正文备注'; mainInput.id = 'main-input'; mainInput.name = 'mainDraft'; mainInput.value = '未保存的正文草稿'; mainInput.className = 'lq-input';
        const frame = document.createElement('iframe'); frame.id = 'document-frame'; frame.title = '原始用户课件'; frame.srcdoc = '<!doctype html><html lang="zh-CN"><head><title>原始课件</title><style>body{background:white;color:black;font:16px sans-serif;padding:20px}</style></head><body><h1>原始纸张内容</h1><label for="inner-draft">内部草稿</label><input id="inner-draft" value="内部文档原值"><script>window.documentIdentity={}; parent.frameLoads++;</script></body></html>';
        const actions = w.createComponent('button', { label: '下载原稿', variant: 'soft' }); actions.addEventListener('click', () => w.downloadClicks = (w.downloadClicks || 0) + 1);
        const viewer = w.workspace.createWorkspace('viewer', { id: 'reader', title: '原始用户课件与长标题，保留文档控制器', kind: 'iframe' }, { actions, content: frame }); content.append(mainLabel, mainInput, viewer);
        let root;
        if (entry === 'dom') root = w.workspace.createWorkspace(item.kind, item.props, { side, main: content });
        else { const template = document.createElement('template'); template.innerHTML = entry === 'jinja' ? item.html : w.workspace.workspaceMarkup(item.kind, item.props); root = template.content.firstElementChild; root.querySelector('[data-lq-split-pane=side]').append(side); root.querySelector('[data-lq-split-pane=main]').append(content); }
        w.root = root; w.nativeInput = input; w.frame = frame; w.viewer = viewer; w.originalForm = document.querySelector('#work-form'); document.querySelector('#mount')!.append(root);
        if (enhance) w.binding = w.workspace.enhanceSplit(root, { onResize: (detail: any) => w.resizes.push(detail) });
    }, { item: fixture.cases[0], entry, enhance });
    await expect.poll(() => page.evaluate(() => (window as any).frameLoads)).toBe(1);
    await expect(page.frameLocator('iframe').getByRole('heading', { name: '原始纸张内容' })).toBeVisible();
    await page.frameLocator('iframe').locator('body').evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    await page.evaluate(() => { const w = window as any; w.originalFrameDocument = w.frame.contentDocument; w.originalFrameWindowIdentity = w.frame.contentWindow.documentIdentity; w.originalFrameDocument.querySelector('#inner-draft').value = '内部未保存草稿'; });
}
const separator = (page: Page) => page.getByRole('separator');
test.describe('LQ Workspace Split and Viewer', () => {
    test.beforeEach(async ({ page }) => mount(page));
    test('pure Jinja/HTML/DOM parity and invalid props agree before any structure is created', async ({ page }) => {
        expect(fixture.cases).toHaveLength(5); expect(fixture.invalid.length).toBeGreaterThanOrEqual(12);
        for (const item of fixture.cases) {
            expect(item.error).toBeUndefined();
            const result = await page.evaluate(item => {
                const api = (window as any).workspace, semantic = (n: Node): any => n.nodeType === Node.TEXT_NODE ? (n.textContent?.trim() ? { text: n.textContent } : null) : ({ tag: (n as Element).tagName.toLowerCase(), attrs: Object.fromEntries([...(n as Element).attributes].map(a => [a.name, a.value]).sort()), children: [...n.childNodes].map(semantic).filter(Boolean) });
                const parse = (html: string) => { const t = document.createElement('template'); t.innerHTML = html; return semantic(t.content.firstElementChild!); };
                return { jinja: parse(item.html), html: parse(api.workspaceMarkup(item.kind, item.props)), dom: semantic(api.createWorkspace(item.kind, item.props)) };
            }, item); expect(result.html).toEqual(result.jinja); expect(result.dom).toEqual(result.jinja);
        }
        for (const item of fixture.invalid) { expect(item.error).toBeTruthy(); expect(await page.evaluate(item => { try { (window as any).workspace.workspaceProps(item.kind, item.props); return false; } catch { return true; } }, item)).toBe(true); }
    });
    test('safe Node slots validate every value before moving any node, reject overlap, foreign document and shadow roots', async ({ page }) => {
        const result = await page.evaluate(props => {
            const api = (window as any).workspace, parent = document.createElement('div'), child = document.createElement('p'); parent.append(child); document.body.append(parent); const foreign = document.implementation.createHTMLDocument('foreign').createElement('p'); const host = document.createElement('div'), shadow = host.attachShadow({ mode: 'open' });
            const rejects = [{ side: child, main: 'raw HTML' }, { side: parent, main: child }, { side: child, main: child }, { side: child, unknown: document.createElement('p') }, { side: document.body }, { main: document.documentElement }, { side: shadow }, { side: foreign }].map(slots => { try { api.createWorkspace('split', props, slots); return false; } catch { return true; } });
            const kept = child.parentNode === parent && parent.isConnected; const fragment = document.createDocumentFragment(), textarea = document.createElement('textarea'); textarea.value = 'node identity'; fragment.append(textarea); const result = api.createWorkspace('split', props, { side: fragment, main: document.createTextNode('<plain>') });
            return { rejects, kept, fragment: result.querySelector('textarea') === textarea, value: textarea.value, text: result.querySelector('[data-lq-split-pane=main]').textContent.includes('<plain>') };
        }, fixture.cases[0].props); expect(result).toEqual({ rejects: Array(8).fill(true), kept: true, fragment: true, value: 'node identity', text: true });
    });
    for (const entry of ['jinja', 'html', 'dom']) test(`${entry} fallback exposes both panes; enhancement preserves form nodes and iframe document`, async ({ page }) => {
        await create(page, entry, false); await expect(page.getByRole('region', { name: '目录', exact: true })).toBeVisible(); await expect(page.getByRole('region', { name: '正文', exact: true })).toBeVisible(); await expect(page.getByRole('tablist')).toHaveCount(0);
        await page.evaluate(() => { const w = window as any; w.binding = w.workspace.enhanceSplit(w.root); }); await expect(page.locator('[data-lq-split]')).toHaveAttribute('data-lq-split-enhanced', 'wide');
        expect(await page.evaluate(() => { const w = window as any; return [w.nativeInput === document.querySelector('#side-input'), w.originalForm === document.querySelector('#work-form'), w.frame.contentDocument === w.originalFrameDocument, w.frameLoads, [...new FormData(w.originalForm)]]; })).toEqual([true, true, true, 1, [['sideDraft', '未保存的目录草稿'], ['mainDraft', '未保存的正文草稿']]]);
    });
    test('desktop separator uses exact eight-pixel keys, Home/End and aria bounds; RTL reverses arrows', async ({ page }) => {
        await create(page); await separator(page).focus(); await separator(page).press('ArrowRight'); await expect(separator(page)).toHaveAttribute('aria-valuenow', '288'); await separator(page).press('ArrowLeft'); await expect(separator(page)).toHaveAttribute('aria-valuenow', '280'); await separator(page).press('Home'); await expect(separator(page)).toHaveAttribute('aria-valuenow', '160'); await separator(page).press('End'); await expect(separator(page)).toHaveAttribute('aria-valuenow', '640');
        await page.locator('[data-lq-split]').evaluate(n => n.setAttribute('dir', 'rtl')); await separator(page).press('ArrowRight'); await expect(separator(page)).toHaveAttribute('aria-valuenow', '632'); await separator(page).press('ArrowLeft'); await expect(separator(page)).toHaveAttribute('aria-valuenow', '640'); expect(await page.evaluate(() => (window as any).resizes.every((r: any) => r.reason === 'keyboard'))).toBe(true);
    });
    test('real pointer capture clamps to min/max and releases; cancel and responsive transition release drag state', async ({ page }) => {
        await create(page); let box = (await separator(page).boundingBox())!; await page.mouse.move(box.x + box.width / 2, box.y + 20); await page.mouse.down(); await page.mouse.move(box.x + 1000, box.y + 20); await expect(separator(page)).toHaveAttribute('aria-valuenow', '640'); await page.mouse.move(0, box.y + 20); await expect(separator(page)).toHaveAttribute('aria-valuenow', '160'); await page.mouse.up(); await expect(page.locator('[data-lq-split]')).not.toHaveClass(/is-resizing/);
        box = (await separator(page).boundingBox())!; await page.mouse.move(box.x + box.width / 2, box.y + 20); await page.mouse.down(); await page.setViewportSize({ width: 390, height: 844 }); await expect(page.locator('[data-lq-split]')).toHaveAttribute('data-lq-split-enhanced', 'narrow'); await expect(page.locator('[data-lq-split]')).not.toHaveClass(/is-resizing/); await page.mouse.up();
    });
    test('RTL pointer direction and pointercancel release capture without touching document listeners', async ({ page }) => {
        await create(page); await page.locator('[data-lq-split]').evaluate(n => n.setAttribute('dir', 'rtl'));
        await separator(page).evaluate(n => n.addEventListener('pointerdown', event => (window as any).pointerId = (event as PointerEvent).pointerId, { once: true }));
        const box = (await separator(page).boundingBox())!; await page.mouse.move(box.x + box.width / 2, box.y + 20); await page.mouse.down(); await page.mouse.move(box.x + box.width / 2 - 24, box.y + 20); await expect(separator(page)).toHaveAttribute('aria-valuenow', '304');
        expect(await separator(page).evaluate(n => n.hasPointerCapture((window as any).pointerId))).toBe(true);
        await separator(page).evaluate(n => n.dispatchEvent(new PointerEvent('pointercancel', { pointerId: (window as any).pointerId, bubbles: true }))); await expect(page.locator('[data-lq-split]')).not.toHaveClass(/is-resizing/); expect(await separator(page).evaluate(n => n.hasPointerCapture((window as any).pointerId))).toBe(false); await page.mouse.up();
    });
    for (const width of [1023, 1024, 1025]) test(`${width}px breakpoint uses existing segment or resizable regions without remounting`, async ({ page }) => {
        await page.setViewportSize({ width, height: 900 }); await create(page); await expect(page.locator('[data-lq-split]')).toHaveAttribute('data-lq-split-enhanced', width < 1024 ? 'narrow' : 'wide');
        if (width < 1024) { await expect(page.getByRole('tablist', { name: '阅读工作区' })).toBeVisible(); expect(await page.evaluate(() => { const w = window as any; return w.navigation.segment(w.root) === w.navigation.segment(w.root); })).toBe(true); await page.getByRole('tab', { name: '正文', exact: true }).press('Home'); await expect(page.getByRole('tab', { name: '目录', exact: true })).toBeFocused(); await expect(page.locator('#side-input')).toBeVisible(); } else { await expect(separator(page)).toBeVisible(); await expect(page.locator('#side-input')).toBeVisible(); await expect(page.locator('#main-input')).toBeVisible(); }
        expect(await page.evaluate(() => (window as any).frameLoads)).toBe(1);
    });
    test('a narrow container switches below its min requirements even on desktop and keeps focused pane', async ({ page }) => {
        await create(page); await page.locator('#side-input').focus(); await page.locator('[data-lq-split]').evaluate(n => (n as HTMLElement).style.width = '491px'); await expect(page.locator('[data-lq-split]')).toHaveAttribute('data-lq-split-enhanced', 'narrow'); await expect(page.locator('#side-input')).toBeFocused(); await expect(page.getByRole('tab', { name: '目录', exact: true })).toHaveAttribute('aria-selected', 'true');
        await page.locator('[data-lq-split]').evaluate(n => (n as HTMLElement).style.width = '492px'); await expect(page.locator('[data-lq-split]')).toHaveAttribute('data-lq-split-enhanced', 'wide'); await expect(separator(page)).toHaveAttribute('aria-valuemax', '160'); await expect(page.locator('#side-input')).toBeFocused();
    });
    test('narrowing while a pane control is focused preserves it; repeated segment changes never reload iframe or draft', async ({ page }) => {
        await create(page); await page.locator('#side-input').fill('新本地草稿'); await page.setViewportSize({ width: 390, height: 844 }); await expect(page.locator('#side-input')).toBeFocused(); await expect(page.getByRole('tab', { name: '目录', exact: true })).toHaveAttribute('aria-selected', 'true');
        for (let i = 0; i < 5; i++) { await page.getByRole('tab', { name: '正文', exact: true }).click(); await page.getByRole('tab', { name: '目录', exact: true }).click(); }
        await page.setViewportSize({ width: 1440, height: 980 }); await expect(page.locator('[data-lq-split]')).toHaveAttribute('data-lq-split-enhanced', 'wide'); expect(await page.evaluate(() => { const w = window as any; return { same: w.frame === document.querySelector('iframe') && w.frame.contentDocument === w.originalFrameDocument && w.frame.contentWindow.documentIdentity === w.originalFrameWindowIdentity, loads: w.frameLoads, inner: w.frame.contentDocument.querySelector('input').value, draft: w.nativeInput.value }; })).toEqual({ same: true, loads: 1, inner: '内部未保存草稿', draft: '新本地草稿' });
    });
    test('focus inside iframe survives narrowing without changing its document or hiding the focused pane', async ({ page }) => {
        await create(page); await page.frameLocator('iframe').locator('#inner-draft').focus(); await page.setViewportSize({ width: 390, height: 844 }); await expect(page.getByRole('tab', { name: '正文', exact: true })).toHaveAttribute('aria-selected', 'true'); await expect(page.frameLocator('iframe').locator('#inner-draft')).toBeFocused();
        expect(await page.evaluate(() => { const w = window as any; return [w.frameLoads, w.frame.contentDocument === w.originalFrameDocument, w.frame.contentDocument.querySelector('input').value]; })).toEqual([1, true, '内部未保存草稿']);
    });
    test('native validity reveals the hidden invalid pane and focuses the native input', async ({ page }) => {
        await page.setViewportSize({ width: 390, height: 844 }); await create(page); const errors: string[] = []; page.on('console', msg => { if (msg.type() === 'error') errors.push(msg.text()); });
        await page.evaluate(() => { const input = document.querySelector('#side-input') as HTMLInputElement; input.required = true; input.value = ''; });
        expect(await page.evaluate(() => (document.querySelector('#work-form') as HTMLFormElement).reportValidity())).toBe(false); await expect(page.locator('#side-input')).toBeFocused(); await expect(page.getByRole('tab', { name: '目录', exact: true })).toHaveAttribute('aria-selected', 'true'); expect(errors.filter(e => /not focusable/i.test(e))).toEqual([]);
    });
    test('two invalid panes keep the first invalid input visible through native validation and submit', async ({ page }) => {
        await page.setViewportSize({ width: 390, height: 844 }); await create(page); const errors: string[] = []; page.on('console', msg => { if (msg.type() === 'error') errors.push(msg.text()); });
        await page.evaluate(() => { for (const input of document.querySelectorAll<HTMLInputElement>('#side-input,#main-input')) { input.required = true; input.value = ''; } });
        await page.locator('#submit').click(); await expect(page.locator('#side-input')).toBeFocused(); await expect(page.locator('#side-input')).toBeVisible(); expect(await page.evaluate(() => (window as any).submitCount)).toBe(0); expect(errors.filter(e => /not focusable/i.test(e))).toEqual([]);
        await page.locator('#side-input').fill('已填写'); await page.locator('#submit').click(); await expect(page.locator('#main-input')).toBeFocused(); await expect(page.locator('#main-input')).toBeVisible(); expect(await page.evaluate(() => (window as any).submitCount)).toBe(0);
    });
    test('destroy clears pending validation timer and retains page-owned classes/styles/control values', async ({ page }) => {
        await page.setViewportSize({ width: 390, height: 844 }); await create(page);
        const result = await page.evaluate(() => {
            const w = window as any, timers = new Set<number>(), set = window.setTimeout, clear = window.clearTimeout;
            window.setTimeout = ((fn: (...args: any[]) => void, delay?: number, ...args: any[]) => { const id = set(() => { timers.delete(id); fn(...args); }, delay); timers.add(id); return id; }) as typeof set;
            window.clearTimeout = ((id: number) => { timers.delete(id); clear(id); }) as typeof clear;
            w.root.classList.add('consumer-class'); w.root.style.setProperty('--consumer-size', '19px'); const input = document.querySelector('#side-input') as HTMLInputElement; input.required = true; input.value = ''; const valid = (document.querySelector('#work-form') as HTMLFormElement).reportValidity(); w.binding.destroy();
            const result = { valid, timers: timers.size, ownClass: w.root.classList.contains('consumer-class'), ownStyle: w.root.style.getPropertyValue('--consumer-size'), same: document.querySelector('#side-input') === input, visible: [...w.root.querySelectorAll('[data-lq-split-pane]')].every((p: any) => !p.hidden) };
            window.setTimeout = set; window.clearTimeout = clear; return result;
        }); expect(result).toEqual({ valid: false, timers: 0, ownClass: true, ownStyle: '19px', same: true, visible: true });
    });
    test('Viewer toolbar stays outside iframe geometry during scrolling and never modifies its document', async ({ page }) => {
        await create(page); await page.locator('.lq-viewer__content').evaluate(n => (n as HTMLElement).style.minHeight = '1500px'); const toolbar = page.locator('.lq-viewer__toolbar'), frame = page.locator('iframe');
        expect(await toolbar.evaluate(n => getComputedStyle(n).position)).toBe('relative'); let a = (await toolbar.boundingBox())!, b = (await frame.boundingBox())!; expect(a.y + a.height).toBeLessThanOrEqual(b.y); expect(b.height).toBeGreaterThanOrEqual(320); expect(b.width).toBeCloseTo((await page.locator('.lq-viewer__content').boundingBox())!.width, 0);
        await page.evaluate(() => window.scrollTo(0, 300)); a = (await toolbar.boundingBox())!; b = (await frame.boundingBox())!; expect(a.y + a.height).toBeLessThanOrEqual(b.y);
        expect(await page.evaluate(() => { const w = window as any; return { loads: w.frameLoads, same: w.frame.contentDocument === w.originalFrameDocument, theme: w.frame.contentDocument.documentElement.dataset.uiPalette || null, sandbox: w.frame.getAttribute('sandbox') }; })).toEqual({ loads: 1, same: true, theme: null, sandbox: null });
    });
    test('destroy and cross-URL ownership complete 20 wide/narrow cycles without retained observers/listeners/frames or remounting', async ({ page }) => {
        await create(page, 'dom', false);
        const result = await page.evaluate(async () => {
            const w = window as any, other = await import('/static/js/lq/workspace.js?copy');
            const semantic = (n: Node): any => n.nodeType === Node.TEXT_NODE ? { text: n.textContent } : { tag: (n as Element).tagName, attrs: Object.fromEntries([...(n as Element).attributes].filter(a => a.name !== 'style' || a.value.trim()).map(a => [a.name, a.value]).sort()), children: [...n.childNodes].map(semantic) };
            const initial = JSON.stringify(semantic(w.root)), listeners = new Set<any>(), frames = new Set<number>(); let observers = 0;
            const add = EventTarget.prototype.addEventListener, remove = EventTarget.prototype.removeEventListener, NativeObserver = window.ResizeObserver, raf = window.requestAnimationFrame, caf = window.cancelAnimationFrame;
            EventTarget.prototype.addEventListener = function(type: string, fn: any, options?: any) { listeners.add(fn); return add.call(this, type, fn, options); };
            EventTarget.prototype.removeEventListener = function(type: string, fn: any, options?: any) { listeners.delete(fn); return remove.call(this, type, fn, options); };
            window.requestAnimationFrame = fn => { const id = raf(t => { frames.delete(id); fn(t); }); frames.add(id); return id; }; window.cancelAnimationFrame = id => { frames.delete(id); caf(id); };
            window.ResizeObserver = class extends NativeObserver { tracked = false; observe(...args: Parameters<ResizeObserver['observe']>) { if (!this.tracked) { this.tracked = true; observers++; } super.observe(...args); } disconnect() { if (this.tracked) { this.tracked = false; observers--; } super.disconnect(); } };
            for (let i = 0; i < 20; i++) { w.root.style.width = '900px'; const binding = w.workspace.enhanceSplit(w.root); if (other.enhanceSplit(w.root) !== binding) throw Error('duplicate owner'); w.root.style.width = '400px'; binding.refresh(); binding.select('side'); binding.select('main'); w.root.style.width = '900px'; binding.refresh(); binding.destroy(); binding.destroy(); }
            w.root.style.removeProperty('width'); if (!w.root.getAttribute('style')) w.root.removeAttribute('style'); const result = { listeners: listeners.size, observers, frames: frames.size, animations: w.root.getAnimations({ subtree: true }).length, sameInput: w.nativeInput === document.querySelector('#side-input'), sameFrame: w.frame.contentDocument === w.originalFrameDocument, loads: w.frameLoads, structure: JSON.stringify(semantic(w.root)) === initial };
            EventTarget.prototype.addEventListener = add; EventTarget.prototype.removeEventListener = remove; window.ResizeObserver = NativeObserver; window.requestAnimationFrame = raf; window.cancelAnimationFrame = caf; return result;
        }); expect(result).toEqual({ listeners: 0, observers: 0, frames: 0, animations: 0, sameInput: true, sameFrame: true, loads: 1, structure: true });
    });
    test('coarse desktop separator is 44px; narrow segment and forced-colors/reduced-motion remain usable', async ({ browser }) => {
        const context = await browser.newContext({ viewport: { width: 1440, height: 980 }, hasTouch: true, forcedColors: 'active', reducedMotion: 'reduce' });
        try { const page = await context.newPage(); await mount(page); await create(page); expect((await separator(page).boundingBox())!.width).toBeGreaterThanOrEqual(44); await separator(page).press('ArrowRight'); await expect(separator(page)).toHaveAttribute('aria-valuenow', '288'); await page.setViewportSize({ width: 390, height: 844 }); await page.getByRole('tab', { name: '目录', exact: true }).tap(); for (const h of await page.getByRole('tab').evaluateAll(nodes => nodes.map(n => n.getBoundingClientRect().height))) expect(h).toBeGreaterThanOrEqual(44);
            const motion = await page.evaluate(() => document.querySelector('[data-lq-split]')!.getAnimations({ subtree: true }).map(a => ({ duration: a.effect!.getTiming().duration, type: a.constructor.name, property: (a as CSSTransition).transitionProperty, target: ((a.effect as KeyframeEffect).target as Element)?.className })));
            expect(motion.filter(a => typeof a.duration !== 'number' || a.duration > 1), JSON.stringify(motion)).toEqual([]);
        } finally { await context.close(); }
    });
    for (const width of [390, 1440]) for (const palette of ['indigo', 'sky', 'mint', 'violet', 'rose', 'teal']) for (const appearance of ['light', 'dark']) test(`${width}px ${palette}/${appearance} Split/Viewer real CSS and axe`, async ({ page }) => {
        await page.setViewportSize({ width, height: 980 }); await page.evaluate(({ palette, appearance }) => { document.documentElement.dataset.uiPalette = palette; document.documentElement.dataset.appearance = appearance; }, { palette, appearance }); await create(page);
        expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width); expect((await new AxeBuilder({ page }).analyze()).violations.filter(v => ['serious', 'critical'].includes(v.impact || ''))).toEqual([]);
        expect(await page.frameLocator('iframe').locator('body').evaluate(n => ({ color: getComputedStyle(n).color, background: getComputedStyle(n).backgroundColor }))).toEqual({ color: 'rgb(0, 0, 0)', background: 'rgb(255, 255, 255)' });
        if (palette === 'teal') { fs.mkdirSync('.codex-temp/lq-audit/s2/workspace', { recursive: true }); await page.screenshot({ path: `.codex-temp/lq-audit/s2/workspace/${appearance}-${width}.png`, fullPage: true }); }
    });
});
