import { test, expect, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import fs from 'node:fs';
import path from 'node:path';

async function mount(page: Page, loadToast = true, failToast = false) {
    await page.route('**/*', route => {
        const url = new URL(route.request().url());
        if (url.origin !== 'https://lq-toast.test') return route.abort();
        if (failToast && url.pathname === '/static/js/lq/toast.js') return route.abort();
        if (url.pathname.startsWith('/static/js/') || ['/static/css/tailwind-app.css', '/static/css/lq/components/toast.css', '/static/css/lq/components/dialogs.css'].includes(url.pathname)) {
            const file = path.resolve(`.${url.pathname}`);
            if (file.startsWith(path.resolve('static') + path.sep) && fs.existsSync(file)) return route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body: fs.readFileSync(file) });
        }
        if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh-CN" data-theme="lanshare" data-ui-palette="teal" data-appearance="light"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>LQ toast</title><link rel="stylesheet" href="/static/css/tailwind-app.css"></head><body><main style="padding:24px"><h1>通知交互</h1><button id="opener">入口</button><button id="after">下一项</button></main><dialog id="native"><button id="native-button">原生按钮</button></dialog><script type="module">import * as ui from '/static/js/ui.js';import * as dialogs from '/static/js/lq/dialogs.js';import {getLayerSystem} from '/static/js/lq/layer.js';${loadToast ? "import * as notifications from '/static/js/lq/toast.js';window.notifications=notifications;window.system=notifications.getToastSystem(document);" : ''}window.ui=ui;window.dialogs=dialogs;window.layer=getLayerSystem(document);window.calls=[];document.body.dataset.ready='true';</script></body></html>` });
        return route.abort();
    });
    await page.goto('https://lq-toast.test/'); await expect(page.locator('body')).toHaveAttribute('data-ready', 'true');
}

test.describe('LQ toast', () => {
    test('legacy is lazy, preserves aliases/defaults/280/error/sticky and returns undefined', async ({ page }) => {
        const requests: string[] = []; page.on('request', request => requests.push(new URL(request.url()).pathname));
        await mount(page, false); expect(requests).not.toContain('/static/js/lq/toast.js');
        expect(await page.evaluate(() => { const w = window as any; return { same: w.ui.showMessage === w.ui.showToast && w.UI.showToast === w.showMessage, value: w.ui.showToast('   保存\n成功  ') }; })).toEqual({ same: true, value: undefined });
        await expect(page.locator('.lq-toast__message')).toHaveText('保存 成功');
        await expect(page.locator('.lq-toast')).toHaveAttribute('data-tone', 'success');
        await page.evaluate(() => (window as any).ui.showMessage('错'.repeat(300), 'error', 0));
        await expect(page.locator('[data-tone="danger"] .lq-toast__message')).toHaveText('错'.repeat(280));
        await page.waitForTimeout(250); await expect(page.locator('[data-tone="danger"]')).toBeVisible();
    });

    test('a failed lazy import gives one visible plaintext fallback without an unhandled rejection', async ({ page }) => {
        const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
        await mount(page, false, true);
        await page.evaluate(() => { const w = window as any; w.ui.showToast('<b>首条</b>'); w.ui.showMessage('保留业务消息', 'error'); });
        await expect(page.locator('.lq-toast-fallback')).toHaveCount(1);
        await expect(page.locator('.lq-toast-fallback')).toContainText('保留业务消息');
        expect(errors).toEqual([]); await expect(page.locator('.lq-toast-fallback b')).toHaveCount(0);
        await page.locator('.lq-toast-fallback button').click(); await expect(page.locator('.lq-toast-fallback')).toHaveCount(0);
    });

    test('document singleton, maximum three and same-message dedupe survive duplicate module URLs', async ({ page }) => {
        await mount(page);
        expect(await page.evaluate(async () => {
            const w = window as any, duplicate = await import('/static/js/lq/toast.js?duplicate');
            const a = w.notifications.toast('相同消息', { duration: 0 });
            const same = duplicate.toast('相同消息', { duration: 0 }) === a;
            for (const message of ['二', '三', '四']) w.notifications.toast(message, { duration: 0 });
            return { singleton: duplicate.getToastSystem(document) === w.system, same, count: w.system.size, roots: document.querySelectorAll('.lq-toast').length, reason: await a.closed };
        })).toEqual({ singleton: true, same: true, count: 3, roots: 3, reason: 'overflow' });
    });

    test('message-only live region uses one polite/assertive source and duplicate refresh does not reannounce', async ({ page }) => {
        await mount(page);
        await page.evaluate(() => { const w = window as any; w.a = w.notifications.toast('<img src=x onerror=bad()>', { duration: 0, action: { label: '打开安全页', href: '/safe' } }); w.b = w.notifications.toast('危险消息', { tone: 'danger', duration: 0 }); });
        await expect(page.locator('[role="status"]')).toHaveCount(2); await expect(page.locator('[aria-live="assertive"]')).toHaveCount(1);
        await expect(page.locator('.lq-toast img')).toHaveCount(0);
        expect(await page.evaluate(async () => {
            const w = window as any; let changes = 0;
            const live = w.a.root.querySelector('[role=status]'); const observer = new MutationObserver(list => changes += list.length); observer.observe(live, { childList: true, subtree: true, characterData: true });
            w.notifications.toast('<img src=x onerror=bad()>', { duration: 0, action: { label: '打开安全页', href: '/safe' } });
            await Promise.resolve(); observer.disconnect();
            return { changes, liveChildren: live.querySelectorAll('button,a').length, containerLive: document.querySelector('#lq-toasts')!.hasAttribute('aria-live') };
        })).toEqual({ changes: 0, liveChildren: 0, containerLive: false });
    });

    test('hover and focus each pause expiry, and hidden time is not charged', async ({ page }) => {
        await mount(page);
        await page.evaluate(() => { (window as any).handle = (window as any).notifications.toast('计时消息', { duration: 350 }); });
        await page.locator('.lq-toast').dispatchEvent('pointerenter'); await page.locator('.lq-toast__close').focus();
        await page.waitForTimeout(420); await page.locator('.lq-toast').dispatchEvent('pointerleave');
        await page.waitForTimeout(420); await expect(page.locator('.lq-toast')).toBeVisible();
        await page.evaluate(() => { Object.defineProperty(document, 'hidden', { configurable: true, value: true }); document.dispatchEvent(new Event('visibilitychange')); });
        await page.locator('#after').focus(); await page.waitForTimeout(420); await expect(page.locator('.lq-toast')).toBeVisible();
        await page.evaluate(() => { Object.defineProperty(document, 'hidden', { configurable: true, value: false }); document.dispatchEvent(new Event('visibilitychange')); });
        await expect(page.locator('.lq-toast')).toHaveCount(0);
    });

    test('close/update reopens the same key without stale cleanup or focus theft', async ({ page }) => {
        await mount(page); await page.locator('#opener').focus();
        expect(await page.evaluate(async () => {
            const w = window as any; const handle = w.notifications.toast('旧消息', { key: 'job', duration: 0 });
            await new Promise(resolve => setTimeout(resolve, 200));
            const closing = handle.close(); const same = w.notifications.toast('新消息', { key: 'job', duration: 0 }) === handle;
            const superseded = await closing; await new Promise(resolve => setTimeout(resolve, 220));
            return { same, superseded, state: handle.state, message: handle.root.textContent, focus: document.activeElement!.id, count: w.system.size };
        })).toMatchObject({ same: true, superseded: false, state: 'open', message: '新消息', focus: 'opener', count: 1 });
    });

    test('callback actions run once, preserve an updated generation and keep failures visible', async ({ page }) => {
        await mount(page);
        await page.evaluate(() => { const w = window as any; w.handle = w.notifications.toast('操作通知', { duration: 0, action: { label: '执行一次', onClick: () => { w.calls.push('action'); return new Promise(resolve => w.done = resolve); } } }); });
        await page.locator('[data-lq-toast-action]').click(); await page.locator('[data-lq-toast-action]').dispatchEvent('click');
        expect(await page.evaluate(() => (window as any).calls)).toEqual(['action']);
        await page.evaluate(() => { const w = window as any; w.handle.update('替代后续通知', { duration: 0 }); w.done(); });
        await expect(page.locator('.lq-toast__message')).toHaveText('替代后续通知');
        await page.evaluate(() => { const w = window as any; w.system.clear(); w.notifications.toast('业务动作失败', { duration: 100, action: { label: '失败动作', onClick: () => Promise.reject(new Error('expected')) }, onError: () => w.calls.push('error') }); });
        await page.locator('[data-lq-toast-action]').click();
        await expect(page.locator('.lq-toast')).toHaveAttribute('data-action-error', 'true');
        await page.waitForTimeout(200); await expect(page.locator('.lq-toast__message')).toContainText('操作未完成');
        expect(await page.evaluate(() => (window as any).calls)).toEqual(['action', 'error']);
        await page.locator('.lq-toast__close').click(); await expect(page.locator('.lq-toast')).toHaveCount(0);
    });

    test('continuous same-turn refresh commits the latest text and navigation actions activate only once', async ({ page }) => {
        await mount(page);
        await page.evaluate(() => { const w = window as any; w.handle = w.notifications.toast('初始消息', { key: 'live', duration: 0 }); });
        await expect(page.locator('.lq-toast__message')).toHaveText('初始消息');
        await page.evaluate(() => { const w = window as any; w.handle.update('最新消息'); w.handle.update('最新消息'); });
        await expect(page.locator('.lq-toast__message')).toHaveText('最新消息');
        await page.evaluate(() => { const w = window as any; w.system.clear(); w.handle = w.notifications.toast('可导航通知', { duration: 0, action: { label: '打开定位', href: '#target' }, onClose: (reason: string) => w.calls.push(reason) }); });
        await page.locator('[data-lq-toast-action]').click();
        await expect(page).toHaveURL(/#target$/); await expect(page.locator('.lq-toast')).toHaveCount(0);
        expect(await page.evaluate(() => (window as any).calls)).toEqual(['action']);
    });

    test('successful action, explicit close and a never-finishing exit each release exactly once', async ({ page }) => {
        await mount(page);
        await page.evaluate(() => { const w = window as any; w.handle = w.notifications.toast('操作完成', { duration: 0, action: { label: '完成', onClick: () => w.calls.push('action') }, onClose: (r: string) => w.calls.push(r) }); });
        await page.locator('[data-lq-toast-action]').click(); await expect(page.locator('.lq-toast')).toHaveCount(0);
        expect(await page.evaluate(() => (window as any).calls)).toEqual(['action', 'action']);
        const bounded = await page.evaluate(async () => {
            const w = window as any; const handle = w.notifications.toast('退出兜底', { duration: 0 }); await new Promise(resolve => setTimeout(resolve, 200));
            handle.root.style.transitionDuration = '60s'; handle.root.getAnimations = () => [{ playState: 'running', effect: { getComputedTiming: () => ({ endTime: 60000 }) }, finished: new Promise(() => {}) }];
            const start = performance.now(); const close = handle.close(); const same = close === handle.close(); await close;
            return { same, elapsed: performance.now() - start, state: handle.state, reason: await handle.closed, roots: document.querySelectorAll('.lq-toast').length };
        });
        expect(bounded).toMatchObject({ same: true, state: 'closed', reason: 'programmatic', roots: 0 });
        expect(bounded.elapsed).toBeLessThan(1300);
    });

    test('toast companion stays accessible in modal/native, survives parent exit and never closes its parent', async ({ page }) => {
        await mount(page);
        await page.evaluate(() => { const w = window as any; w.parent = w.dialogs.openDialog({ title: '父弹窗', body: '父正文' }); w.handle = w.notifications.toast('父层通知', { duration: 0, action: { label: '父层动作', onClick: () => w.calls.push('modal') } }); });
        expect(await page.locator('#lq-toasts').evaluate(node => Boolean(node.closest('[inert],[aria-hidden="true"]')))).toBe(false);
        await page.keyboard.press('Tab'); await expect(page.locator('[data-lq-toast-action]')).toBeFocused();
        await page.locator('[data-lq-toast-action]').click(); await expect(page.locator('.lq-toast')).toHaveCount(0); await expect(page.getByRole('dialog')).toBeVisible();
        await page.evaluate(() => { const w = window as any; w.handle = w.notifications.toast('跨父层保留', { duration: 0, action: { label: '安全链接', href: '#safe' } }); return w.layer.close(w.parent); });
        await expect(page.locator('.lq-toast')).toBeVisible();
        await page.evaluate(() => document.querySelector<HTMLDialogElement>('#native')!.showModal());
        await expect.poll(() => page.locator('#lq-toasts').evaluate(node => node.parentElement!.id)).toBe('native');
        await page.locator('#native-button').focus(); await page.keyboard.press('Tab'); await expect(page.locator('[data-lq-toast-action]')).toBeFocused();
        await page.evaluate(() => document.querySelector<HTMLDialogElement>('#native')!.close());
        await expect.poll(() => page.locator('#lq-toasts').evaluate(node => node.parentElement!.tagName)).toBe('BODY');
        await expect(page.locator('.lq-toast')).toBeVisible();
        await page.evaluate(() => document.querySelector<HTMLDialogElement>('#native')!.showModal());
        await expect.poll(() => page.locator('#lq-toasts').evaluate(node => node.parentElement!.id)).toBe('native');
        await page.locator('.lq-toast__close').click(); await expect(page.locator('#lq-toasts')).toHaveCount(0);
        await expect(page.locator('#native')).toBeVisible(); expect(await page.evaluate(() => (window as any).calls)).toEqual(['modal']);
        await page.evaluate(() => { const w = window as any; w.notifications.toast('原生父动作', { duration: 0, action: { label: '原生中执行', onClick: () => w.calls.push('native') } }); });
        await page.getByRole('button', { name: '原生中执行', exact: true }).click(); await expect(page.locator('.lq-toast')).toHaveCount(0);
        expect(await page.evaluate(() => (window as any).calls)).toEqual(['modal', 'native']);
    });

    test('native host removal rescues notifications; explicit host removal and core destroy release them', async ({ page }) => {
        await mount(page);
        expect(await page.evaluate(async () => {
            const w = window as any, native = document.querySelector<HTMLDialogElement>('#native')!; native.showModal();
            const a = w.notifications.toast('宿主移除', { duration: 0 }); native.remove(); await new Promise(resolve => setTimeout(resolve, 0));
            const rescued = a.root.isConnected && document.querySelector('#lq-toasts')!.parentElement === document.body;
            document.querySelector('#lq-toasts')!.remove(); const removed = await a.closed;
            const b = w.notifications.toast('核心销毁', { duration: 0 }); w.layer.destroy(); const destroyed = await b.closed;
            let forbidden = 0; const layer = (await import('/static/js/lq/layer.js')).getLayerSystem(document);
            for (const root of [document.body, document.documentElement]) try { layer.registerCompanion(root); } catch { forbidden++; }
            return { rescued, removed, destroyed, forbidden, roots: document.querySelectorAll('.lq-toast').length };
        })).toEqual({ rescued: true, removed: 'destroyed', destroyed: 'destroyed', forbidden: 2, roots: 0 });
    });

    test('twenty cycles and duplicate imports leave zero timers, document listeners, observers or notification DOM', async ({ page }) => {
        await mount(page);
        expect(await page.evaluate(async () => {
            const w = window as any; w.system.destroy(); w.layer.destroy();
            const timers = new Set<number>(), listeners = new Set<any>(); let observers = 0;
            const set = window.setTimeout, clear = window.clearTimeout, add = document.addEventListener, remove = document.removeEventListener, NativeObserver = window.MutationObserver;
            window.setTimeout = ((fn: (...args: any[]) => void, delay?: number, ...args: any[]) => { const id = set(() => { timers.delete(id); fn(...args); }, delay); timers.add(id); return id; }) as typeof set;
            window.clearTimeout = ((id: number) => { timers.delete(id); clear(id); }) as typeof clear;
            document.addEventListener = function(type: string, fn: any, opts?: any) { listeners.add(fn); return add.call(this, type, fn, opts); } as typeof add;
            document.removeEventListener = function(type: string, fn: any, opts?: any) { listeners.delete(fn); return remove.call(this, type, fn, opts); } as typeof remove;
            window.MutationObserver = class extends NativeObserver { tracked = false; observe(...args: Parameters<MutationObserver['observe']>) { if (!this.tracked) { this.tracked = true; observers++; } super.observe(...args); } disconnect() { if (this.tracked) { this.tracked = false; observers--; } super.disconnect(); } };
            for (let index = 0; index < 20; index++) { const handle = w.notifications.toast(`循环 ${index}`, { duration: 100 }); if (index % 2) handle.destroy(); else await handle.close(); await handle.closed; }
            w.notifications.getToastSystem(document).destroy();
            const result = { roots: document.querySelectorAll('#lq-toasts,.lq-toast').length, timers: timers.size, listeners: listeners.size, observers, overflow: document.body.style.overflow };
            window.setTimeout = set; window.clearTimeout = clear; document.addEventListener = add; document.removeEventListener = remove; window.MutationObserver = NativeObserver;
            return result;
        })).toEqual({ roots: 0, timers: 0, listeners: 0, observers: 0, overflow: '' });
    });

    for (const palette of ['indigo', 'sky', 'mint', 'violet', 'rose', 'teal']) for (const appearance of ['light', 'dark']) test(`${palette}/${appearance} mobile long text has no overflow, blur or serious axe findings`, async ({ page }) => {
        await page.setViewportSize({ width: 390, height: 844 }); await mount(page);
        await page.evaluate(({ palette, appearance }) => {
            document.documentElement.dataset.uiPalette = palette; document.documentElement.dataset.appearance = appearance;
            document.documentElement.style.setProperty('--lq-toast-top-offset', '24px');
            const w = window as any;
            w.notifications.toast('长文本通知应完整换行，保留对象数量和操作结果。'.repeat(5), { duration: 0, action: { label: '查看详细说明', href: '#details' } });
            w.notifications.toast('失败：附件未删除，请重新打开对应页面检查。', { tone: 'danger', duration: 0 });
            w.notifications.toast('设置已保存', { tone: 'success', duration: 0 });
        }, { palette, appearance });
        const box = await page.locator('#lq-toasts').boundingBox(); expect(box!.x).toBeGreaterThanOrEqual(0); expect(box!.x + box!.width).toBeLessThanOrEqual(390); expect(box!.y).toBeGreaterThanOrEqual(40);
        expect(await page.locator('.lq-toast').evaluateAll(nodes => nodes.map(node => getComputedStyle(node).backdropFilter))).toEqual(['none', 'none', 'none']);
        const results = await new AxeBuilder({ page }).analyze(); expect(results.violations.filter(v => ['serious', 'critical'].includes(v.impact || ''))).toEqual([]);
        if (palette === 'teal') { fs.mkdirSync('.codex-temp/lq-audit/s2/toast', { recursive: true }); await page.screenshot({ path: `.codex-temp/lq-audit/s2/toast/${appearance}-390.png` }); }
    });
});
