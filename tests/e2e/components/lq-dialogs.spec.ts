import { test, expect, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';

const python = process.env.LQ_TEST_PYTHON || (process.platform === 'win32' ? 'venv/Scripts/python.exe' : 'venv/bin/python');
const fixture = JSON.parse(execFileSync(python, ['tests/e2e/scripts/render_lq_dialogs.py'], { encoding: 'utf8' }));
const captures = '.codex-temp/lq-audit/s2/dialogs';
fs.mkdirSync(captures, { recursive: true });

async function mount(page: Page) {
    await page.route('**/*', route => {
        const url = new URL(route.request().url());
        if (url.origin !== 'https://lq-dialogs.test') return route.abort();
        if (url.pathname.startsWith('/static/js/') || ['/static/css/tailwind-app.css', '/static/css/lq/components/dialogs.css'].includes(url.pathname)) {
            const file = path.resolve(`.${url.pathname}`);
            if (file.startsWith(path.resolve('static') + path.sep) && fs.existsSync(file)) return route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body: fs.readFileSync(file) });
        }
        if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh-CN" data-theme="lanshare" data-ui-palette="teal" data-appearance="light" data-lq-glass="tinted"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>LQ dialogs</title><link rel="stylesheet" href="/static/css/tailwind-app.css"></head><body><main style="padding:24px"><h1>独立弹层测试</h1><button id="opener">入口</button><button id="anchor">锚点</button><div id="source"><input id="draft" aria-label="业务草稿" value="保留草稿"><span id="after">后继</span></div></main><dialog id="native"><button id="native-opener">原生父入口</button></dialog><script type="module">import * as dialogs from '/static/js/lq/dialogs.js';import {getLayerSystem} from '/static/js/lq/layer.js';window.dialogs=dialogs;window.layer=getLayerSystem(document);window.results=[];document.body.dataset.ready='true';</script></body></html>` });
        return route.abort();
    });
    await page.goto('https://lq-dialogs.test/');
    await expect(page.locator('body')).toHaveAttribute('data-ready', 'true');
}

test.describe('LQ dialogs', () => {
    test.beforeEach(async ({ page }) => mount(page));

    test('real Jinja, safe HTML and Element structures match and reject unsafe input', async ({ page }) => {
        expect(fixture.isolated).toBe(true);
        expect(fixture.cases.filter((item: any) => item.error)).toEqual([]);
        const actual = await page.evaluate(cases => {
            const api = (window as any).dialogs;
            const semantic = (n: Node): any => n.nodeType === Node.TEXT_NODE ? (n.textContent?.trim() ? { text: n.textContent } : null) : ({ tag: (n as Element).tagName.toLowerCase(), attrs: Object.fromEntries([...(n as Element).attributes].map(a => [a.name, a.value]).sort()), children: [...n.childNodes].map(semantic).filter(Boolean) });
            const parse = (markup: string) => { const t = document.createElement('template'); t.innerHTML = markup; return semantic(t.content.firstElementChild!); };
            return cases.map((item: any) => ({ jinja: parse(item.html), html: parse(api.dialogMarkup(item.props)), dom: semantic(api.createDialog(item.props)) }));
        }, fixture.cases);
        for (const item of actual) { expect(item.html).toEqual(item.jinja); expect(item.dom).toEqual(item.jinja); }
        for (const invalid of fixture.invalid) {
            expect(invalid.error).toBeTruthy();
            expect(await page.evaluate(props => { try { (window as any).dialogs.dialogMarkup(props); return false; } catch { return true; } }, invalid.props)).toBe(true);
        }
    });

    for (const entry of ['jinja', 'html', 'element']) test(`${entry} entrance actually opens, traps focus and closes with the shared button`, async ({ page }) => {
        await page.locator('#opener').focus();
        await page.evaluate(({ entry, item }) => {
            const w = window as any;
            let root;
            if (entry === 'element') root = w.dialogs.createDialog(item.props);
            else { const t = document.createElement('template'); t.innerHTML = entry === 'jinja' ? item.html : w.dialogs.dialogMarkup(item.props); root = t.content.firstElementChild; document.body.append(root!); }
            w.handle = w.dialogs.openDialog(root, { trigger: document.querySelector('#opener') });
        }, { entry, item: fixture.cases[0] });
        await expect(page.getByRole('dialog')).toBeVisible();
        await expect(page.getByRole('button', { name: '关闭', exact: true })).toBeFocused();
        await page.keyboard.press('Tab'); await expect(page.getByRole('button', { name: '关闭', exact: true })).toBeFocused();
        await page.getByRole('button', { name: '关闭', exact: true }).click();
        await expect(page.getByRole('dialog')).toHaveCount(0);
        await expect(page.locator('#opener')).toBeFocused();
    });

    test('confirm defaults to cancel, settles once only after exit, and repeated click cannot change its choice', async ({ page }) => {
        await page.evaluate(() => {
            const w = window as any;
            w.promise = w.dialogs.confirm({ title: '删除这 2 项？', message: '删除后不能恢复。', confirmLabel: '删除 2 项', danger: true }, {
                onCloseRequested: () => w.results.push('accepted'), onClose: () => w.results.push('closed'),
            });
            w.promise.then((value: boolean) => w.results.push(value));
        });
        await expect(page.getByRole('button', { name: '取消', exact: true })).toBeFocused();
        await page.evaluate(() => { const buttons = document.querySelectorAll<HTMLButtonElement>('.lq-dialog__foot button'); buttons[1].click(); buttons[0].click(); buttons[1].click(); });
        await expect.poll(() => page.evaluate(() => (window as any).results)).toEqual(['accepted', 'closed', true]);
        expect(await page.evaluate(() => ({ roots: document.querySelectorAll('.lq-dialog-root').length, overflow: document.body.style.overflow }))).toEqual({ roots: 0, overflow: '' });
    });

    test('veto and rejection keep the Promise unresolved and allow a later accepted choice', async ({ page }) => {
        await page.evaluate(() => {
            const w = window as any; w.calls = 0;
            w.promise = w.dialogs.choose({ title: '保存修改', choices: [{ value: 'save', label: '保存' }, { value: 'discard', label: '放弃', danger: true }] }, {
                beforeClose: () => ++w.calls === 1 ? false : w.calls === 2 ? Promise.reject(new Error('veto')) : true,
            });
            w.promise.then((value: any) => w.results.push(value));
        });
        for (const label of ['保存', '放弃']) {
            await page.getByRole('button', { name: label, exact: true }).click();
            await expect(page.locator('.lq-dialog-root')).toHaveAttribute('data-lq-layer-state', 'open');
            expect(await page.evaluate(() => (window as any).results)).toEqual([]);
        }
        await page.getByRole('button', { name: '放弃', exact: true }).click();
        await expect.poll(() => page.evaluate(() => (window as any).results)).toEqual([{ status: 'chosen', value: 'discard' }]);
    });

    for (const reason of ['escape', 'outside', 'button', 'destroy']) test(`${reason} returns dismissed and never an implicit selection`, async ({ page }) => {
        await page.evaluate(() => { const w = window as any; w.promise = w.dialogs.choose({ title: '三态选择', choices: [{ value: 'a', label: '方案一' }, { value: 'b', label: '方案二', disabled: true }, { value: 'c', label: '方案三' }] }); w.promise.then((r: any) => w.results.push(r)); });
        if (reason === 'escape') await page.keyboard.press('Escape');
        else if (reason === 'outside') await page.locator('.lq-scrim').click({ position: { x: 5, y: 5 } });
        else if (reason === 'button') await page.getByRole('button', { name: '返回', exact: true }).click();
        else await page.evaluate(() => (window as any).promise.destroy());
        await expect.poll(() => page.evaluate(() => (window as any).results)).toEqual([{ status: 'dismissed' }]);
        expect(await page.evaluate(() => document.body.style.overflow)).toBe('');
    });

    test('native parent owns a genuinely clickable confirm portal and parent close destroys a pending child', async ({ page }) => {
        await page.evaluate(() => {
            const w = window as any, native = document.querySelector<HTMLDialogElement>('#native')!;
            native.showModal(); document.querySelector<HTMLElement>('#native-opener')!.focus();
            w.promise = w.dialogs.confirm({ title: '原生父确认', confirmLabel: '继续' }); w.promise.then((r: boolean) => w.results.push(r));
        });
        expect(await page.locator('.lq-dialog-root').evaluate(node => node.closest('dialog')?.id)).toBe('native');
        await page.getByRole('button', { name: '继续', exact: true }).click();
        await expect.poll(() => page.evaluate(() => (window as any).results)).toEqual([true]);
        await expect(page.locator('#native-opener')).toBeFocused();
        await page.evaluate(() => { const w = window as any; w.promise = w.dialogs.confirm({ title: '未决确认' }, { beforeClose: () => new Promise(() => {}) }); w.promise.then((r: boolean) => w.results.push(r)); w.layer.close(w.promise.handle); document.querySelector<HTMLDialogElement>('#native')!.close(); });
        await expect.poll(() => page.evaluate(() => (window as any).results)).toEqual([true, false]);
        await expect(page.locator('.lq-dialog-root')).toHaveCount(0);
    });

    test('existing Node slots retain draft, identity and location on close or detached-parent destroy', async ({ page }) => {
        expect(await page.evaluate(async () => {
            const w = window as any, input = document.querySelector<HTMLInputElement>('#draft')!, parent = input.parentNode!, next = input.nextSibling;
            let changes = 0; input.addEventListener('change', () => changes++);
            input.value = '未保存草稿'; const first = w.dialogs.openDialog({ title: '编辑', body: input });
            input.dispatchEvent(new Event('change')); await w.layer.close(first);
            const returned = input.parentNode === parent && input.nextSibling === next && input.value === '未保存草稿' && changes === 1;
            const second = w.dialogs.openDialog({ title: '再次编辑', body: input }); (parent as HTMLElement).remove(); second.destroy();
            const detached = input.parentNode === parent && !(parent as HTMLElement).isConnected && !input.isConnected;
            const temporary = w.dialogs.createDialog({ title: '未打开', body: input }); w.dialogs.disposeDialog(temporary);
            return { returned, detached, unopened: input.parentNode === parent, roots: document.querySelectorAll('.lq-dialog-root').length };
        })).toEqual({ returned: true, detached: true, unopened: true, roots: 0 });
    });

    test('slot validation is atomic, fragments restore order, and foreign business moves are respected', async ({ page }) => {
        expect(await page.evaluate(async () => {
            const w = window as any, source = document.querySelector('#source')!, input = document.querySelector('#draft')!;
            let overlap = false, documentRoot = false;
            try { w.dialogs.createDialog({ title: '重复槽', body: source, footer: input }); } catch { overlap = input.parentNode === source; }
            try { w.dialogs.createDialog({ title: '根槽', body: document.body }); } catch { documentRoot = document.body.isConnected; }
            const fragment = document.createDocumentFragment(), a = document.createTextNode('甲'), b = document.createElement('span'), c = document.createTextNode('丙');
            fragment.append(a, b, c); const handle = w.dialogs.openDialog({ title: '片段', body: fragment, footer: input });
            source.append(input); await w.layer.close(handle);
            return { overlap, documentRoot, fragment: fragment.childNodes[0] === a && fragment.childNodes[1] === b && fragment.childNodes[2] === c, notStolen: input.parentNode === source };
        })).toEqual({ overlap: true, documentRoot: true, fragment: true, notStolen: true });
    });

    test('parent removal and synchronous callbacks cannot leave unresolved or resurrected prompts', async ({ page }) => {
        expect(await page.evaluate(async () => {
            const w = window as any;
            const parent = w.dialogs.openDialog({ title: '父层', body: '正文' });
            const child = w.dialogs.confirm({ title: '子层' }, { parentLayer: parent, beforeClose: () => new Promise(() => {}) });
            w.layer.close(child.handle); parent.root.remove(); const removed = await child;
            const destroyed = w.dialogs.confirm({ title: '同步销毁' }, { beforeClose: (_r: string, h: any) => { h.destroy(); return true; } });
            w.layer.close(destroyed.handle); const synchronous = await destroyed;
            const replaced = w.dialogs.confirm({ title: '回调替换' }, { onClose: () => { w.replacement = w.dialogs.openDialog({ title: '新层' }); } });
            await w.layer.close(replaced.handle); const result = await replaced;
            const survived = w.replacement.state !== 'closed' && w.layer.top() === w.replacement; w.replacement.destroy();
            return { removed, synchronous, result, survived, lock: document.body.style.overflow, roots: document.querySelectorAll('.lq-dialog-root').length };
        })).toEqual({ removed: false, synchronous: false, result: false, survived: true, lock: '', roots: 0 });
    });

    test('twenty open/close/destroy cycles clear dialogs, timers, listeners and coordinator observers', async ({ page }) => {
        expect(await page.evaluate(async () => {
            const w = window as any, timers = new Set<number>(), listeners = new Set<EventListenerOrEventListenerObject>();
            const set = window.setTimeout, clear = window.clearTimeout, add = document.addEventListener, remove = document.removeEventListener;
            const NativeObserver = window.MutationObserver; let observers = 0;
            window.MutationObserver = class extends NativeObserver { tracked = false; observe(...args: Parameters<MutationObserver['observe']>) { if (!this.tracked) { this.tracked = true; observers++; } super.observe(...args); } disconnect() { if (this.tracked) { this.tracked = false; observers--; } super.disconnect(); } };
            w.layer.destroy(); w.layer = (await import('/static/js/lq/layer.js')).getLayerSystem(document);
            window.setTimeout = ((fn: (...args: any[]) => void, delay?: number, ...args: any[]) => { const id = set(() => { timers.delete(id); fn(...args); }, delay); timers.add(id); return id; }) as typeof set;
            window.clearTimeout = ((id: number) => { timers.delete(id); clear(id); }) as typeof clear;
            document.addEventListener = function(type: string, fn: any, opts?: any) { if (['keydown', 'focusin', 'pointerdown', 'click', 'scroll', 'pointercancel'].includes(type)) listeners.add(fn); return add.call(this, type, fn, opts); } as typeof add;
            document.removeEventListener = function(type: string, fn: any, opts?: any) { listeners.delete(fn); return remove.call(this, type, fn, opts); } as typeof remove;
            for (let index = 0; index < 20; index++) { const promise = w.dialogs.confirm({ title: `循环 ${index}` }); if (index % 2) promise.destroy(); else await w.layer.close(promise.handle); await promise; }
            const result = { roots: document.querySelectorAll('.lq-dialog-root').length, timers: timers.size, listeners: listeners.size, observers, lock: document.body.style.overflow, top: w.layer.top() };
            window.setTimeout = set; window.clearTimeout = clear; document.addEventListener = add; document.removeEventListener = remove; window.MutationObserver = NativeObserver;
            return result;
        })).toEqual({ roots: 0, timers: 0, listeners: 0, observers: 0, lock: '', top: null });
    });

    test('initial focus destroy and accepted-close DOM removal both settle as dismissed', async ({ page }) => {
        expect(await page.evaluate(async () => {
            const w = window as any;
            const initial = w.dialogs.confirm({ title: '同步初始销毁' }, { onInitialFocus: (_event: Event, h: any) => h.destroy() });
            const initialResult = await initial;
            const accepted = w.dialogs.confirm({ title: '接受后外部移除', confirmLabel: '接受动作' }, { onCloseRequested: (_reason: string, h: any) => h.root.remove() });
            const buttons = accepted.handle.root.querySelectorAll('footer button'); buttons[1].click();
            const acceptedResult = await accepted;
            return { initialResult, acceptedResult, roots: document.querySelectorAll('.lq-dialog-root').length, lock: document.body.style.overflow };
        })).toEqual({ initialResult: false, acceptedResult: false, roots: 0, lock: '' });
    });

    test('four structures use one material surface and mobile long text remains scrollable and actionable', async ({ page }) => {
        for (const type of ['modal', 'sheet', 'drawer', 'popover']) {
            await page.setViewportSize({ width: 390, height: 844 });
            await page.evaluate(type => { const w = window as any; w.handle = w.dialogs.openDialog({ type, title: '长标题'.repeat(12), body: '长正文没有空格0123456789'.repeat(100) }, type === 'popover' ? { anchor: document.querySelector('#anchor') } : {}); }, type);
            await expect(page.getByRole('dialog')).toBeVisible();
            await expect(page.locator('.lq-dialog-root')).toHaveAttribute('data-lq-layer-state', 'open');
            const metrics = await page.locator('.lq-dialog__surface').evaluate(node => {
                const box = node.getBoundingClientRect(), body = node.querySelector('.lq-dialog__body')!;
                return { left: box.left, right: box.right, bottom: box.bottom, height: box.height, scroll: body.scrollHeight > body.clientHeight, materials: node.parentElement!.querySelectorAll('.lq-glass').length, bodyFilter: getComputedStyle(body).backdropFilter };
            });
            expect(metrics.left).toBeGreaterThanOrEqual(0); expect(metrics.right).toBeLessThanOrEqual(390.5); expect(metrics.bottom).toBeLessThanOrEqual(844.5);
            expect(metrics.scroll).toBe(true); expect(metrics.materials).toBe(1); expect(metrics.bodyFilter).toBe('none');
            await page.screenshot({ path: `${captures}/${type}-mobile-long.png` });
            await page.getByRole('button', { name: '关闭', exact: true }).click(); await expect(page.getByRole('dialog')).toHaveCount(0);
        }
    });

    test('desktop modal sizes and right sheet/drawer widths match the published structures', async ({ page }) => {
        const cases = [
            { type: 'modal', size: 'sm', width: 360 }, { type: 'modal', size: 'md', width: 560 },
            { type: 'modal', size: 'lg', width: 800 }, { type: 'modal', size: 'xl', width: 1080 },
            { type: 'modal', size: 'full', width: 1408 }, { type: 'sheet', side: 'right', width: 480 },
            { type: 'drawer', size: 'md', width: 640 }, { type: 'drawer', size: 'wide', width: 960 },
        ];
        for (const { width, ...props } of cases) {
            await page.evaluate(props => { const w = window as any; w.handle = w.dialogs.openDialog({ ...props, title: '结构尺寸', body: '独立正文' }); }, props);
            await expect(page.locator('.lq-dialog-root')).toHaveAttribute('data-lq-layer-state', 'open');
            expect((await page.locator('.lq-dialog__surface').boundingBox())!.width).toBeCloseTo(width, 0);
            await page.evaluate(() => { const w = window as any; return w.layer.close(w.handle); });
        }
    });

    for (const appearance of ['light', 'dark']) test(`${appearance} mobile confirm has safe focus, stacked actions and no serious axe findings`, async ({ page }) => {
        await page.setViewportSize({ width: 390, height: 844 });
        await page.evaluate(appearance => { document.documentElement.dataset.appearance = appearance; const w = window as any; w.promise = w.dialogs.confirm({ title: '确认删除教学附件？', message: '将删除 2 个附件。删除后无法恢复，请确认对象和数量。', confirmLabel: '删除 2 个附件', danger: true }); }, appearance);
        await expect(page.getByRole('button', { name: '取消', exact: true })).toBeFocused();
        const cancel = await page.getByRole('button', { name: '取消', exact: true }).boundingBox();
        const action = await page.getByRole('button', { name: '删除 2 个附件', exact: true }).boundingBox();
        expect(action!.y).toBeGreaterThan(cancel!.y); expect(action!.width).toBeCloseTo(cancel!.width, 0); expect(action!.height).toBeGreaterThanOrEqual(44);
        const results = await new AxeBuilder({ page }).analyze();
        expect(results.violations.filter(v => ['critical', 'serious'].includes(v.impact || ''))).toEqual([]);
        await page.screenshot({ path: `${captures}/confirm-${appearance}-390.png` });
        await page.keyboard.press('Escape');
        expect(await page.evaluate(() => (window as any).promise)).toBe(false);
    });
});
