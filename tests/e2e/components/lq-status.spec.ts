import { test, expect, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';

const fixture = JSON.parse(execFileSync(process.platform === 'win32' ? 'venv/Scripts/python.exe' : 'venv/bin/python', ['tests/e2e/scripts/render_lq_status.py'], { encoding: 'utf8' }));
async function mount(page: Page) {
    await page.route('**/*', route => {
        const url = new URL(route.request().url());
        if (url.origin !== 'https://lq-status.test') return route.abort();
        if (url.pathname.startsWith('/static/')) {
            const file = path.resolve(`.${url.pathname}`);
            if (file.startsWith(path.resolve('static') + path.sep) && fs.existsSync(file)) return route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body: fs.readFileSync(file) });
        }
        if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh-CN" data-theme="lanshare" data-appearance="light" data-ui-palette="indigo"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>LQ feedback</title><link rel="stylesheet" href="/static/css/tailwind-app.css"><style>body{margin:0}main{padding:16px;max-width:100%;box-sizing:border-box}#fixture{display:grid;gap:16px}h1{margin-bottom:16px}</style></head><body><main><h1>保存与核对</h1><div id="fixture"></div><label for="business-draft">业务草稿</label><textarea id="business-draft">原始输入</textarea><button id="after">继续编辑</button></main><script type="module">import * as status from '/static/js/lq/status.js';window.statusApi=status;document.body.dataset.ready='true';</script></body></html>` });
        return route.fulfill({ status: 404, body: 'Missing fixture' });
    });
    await page.goto('https://lq-status.test/');
    await expect(page.locator('body')).toHaveAttribute('data-ready', 'true');
}

test('LQ status Jinja, HTML and Element entries agree for nine save states, alerts and neutral fallback', async ({ page }) => {
    expect(fixture.isolated).toBe(true);
    expect(fixture.cases.filter((item: any) => item.error)).toEqual([]);
    expect(fixture.invalid.every((item: any) => item.error)).toBe(true);
    await mount(page);
    const actual = await page.evaluate(serialized => {
        const fixture = JSON.parse(serialized);
        const api = (window as any).statusApi;
        function semantic(node: Node): any {
            if (node.nodeType === Node.TEXT_NODE) return node.textContent?.trim() ? { text: node.textContent } : null;
            const element = node as Element;
            return { tag: element.tagName.toLowerCase(), attrs: Object.fromEntries([...element.attributes].map(attr => [attr.name, attr.value]).sort()), children: [...element.childNodes].map(semantic).filter(Boolean) };
        }
        const parse = (html: string) => { const template = document.createElement('template'); template.innerHTML = html; return [...template.content.childNodes].map(semantic).filter(Boolean); };
        return {
            cases: fixture.cases.map((item: any) => ({ normalized: api.statusProps(item.kind, item.props), jinja: parse(item.html), html: parse(api.statusMarkup(item.kind, item.props)), element: [semantic(api.createStatus(item.kind, item.props))] })),
            invalid: fixture.invalid.map((item: any) => ['statusProps', 'statusMarkup', 'createStatus'].every(method => { try { api[method](item.kind, item.props); return false; } catch { return true; } })),
        };
    }, JSON.stringify(fixture));
    actual.cases.forEach((item: any, index: number) => {
        expect(item.normalized).toEqual(fixture.cases[index].normalized); expect(item.html).toEqual(item.jinja); expect(item.element).toEqual(item.jinja);
    });
    expect(actual.invalid.every(Boolean)).toBe(true);
});

for (const entry of ['jinja', 'html', 'element']) test(`LQ ${entry} SaveStatus updates visuals and only crosses live groups`, async ({ page }) => {
    await mount(page);
    const result = await page.evaluate(({ fixture, entry }) => {
        const api = (window as any).statusApi, host = document.getElementById('fixture')!;
        if (entry === 'jinja') host.innerHTML = fixture.cases.find((item: any) => item.props.state === 'dirty').html;
        else if (entry === 'html') host.innerHTML = api.html.save_status({ state: 'dirty' });
        else host.append(api.save_status({ state: 'dirty' }));
        const root = host.firstElementChild!, controller = api.saveStatus(root), live = root.querySelector('[data-lq-save-live]')!;
        const observer = new MutationObserver(() => {}); observer.observe(live, { childList: true, subtree: true, characterData: true });
        const steps: any[] = [];
        const set = (state: string, options = {}) => {
            controller.set(state, options);
            steps.push({ state: controller.state, changes: observer.takeRecords().length, live: live.textContent, label: root.querySelector('[data-lq-save-label]')!.textContent,
                tone: root.getAttribute('data-tone'), spinner: root.querySelectorAll('.lq-spinner').length, action: root.querySelectorAll('[data-lq-status-action]').length });
        };
        const initial = live.textContent;
        set('dirty'); set('local_saved'); set('syncing'); set('synced', { time: '20:45', datetime: '2026-09-20T20:45:00+08:00' });
        set('syncing'); set('synced'); set('offline'); set('error', { action: { label: '重试' } }); set('conflict', { action: { label: '重新核对' } });
        set('submitting'); set('submitted'); set('__proto__', { label: '已保存' }); observer.disconnect();
        return { initial, steps, rootLive: root.getAttribute('aria-live'), actionsInLive: live.querySelectorAll('button,a').length };
    }, { fixture, entry });
    expect(result.initial).toBe(''); expect(result.rootLive).toBeNull(); expect(result.actionsInLive).toBe(0);
    expect(result.steps.map((item: any) => item.changes > 0)).toEqual([false, true, false, false, false, false, true, true, false, true, false, false]);
    expect(result.steps[1].label).toBe('已保存到本机'); expect(result.steps[1].tone).toBe('save-local_saved');
    expect(result.steps[2].spinner).toBe(1); expect(result.steps[3].spinner).toBe(0);
    expect(result.steps[7].action).toBe(1); expect(result.steps[8].action).toBe(1);
    expect(result.steps.at(-1)).toMatchObject({ state: 'unknown', label: '保存状态未知', tone: 'neutral' });
});

test('LQ SaveStatus validates before mutation, preserves business focus/listeners and survives twenty ownership cycles', async ({ page }) => {
    await mount(page);
    const result = await page.evaluate(async () => {
        const api = (window as any).statusApi, host = document.getElementById('fixture')!;
        const root = api.save_status({ state: 'error', action: { label: '重试', id: 'retry' } }); host.append(root);
        let calls = 0; const action = root.querySelector('#retry')!; action.addEventListener('click', () => calls++); action.focus();
        let controller = api.saveStatus(root); const initial = root.outerHTML;
        const moduleUrl = '/static/js/lq/status.js?second-asset-url';
        const duplicate = await import(moduleUrl);
        const sharedOwner = duplicate.saveStatus(root) === controller;
        let rejected = 0;
        for (const value of [{ state: 'conflict' }, { state: 'error', action: { label: 'x', href: 'javascript:alert(1)' } }, { state: 'synced', time: {} }]) {
            const { state, ...options } = value; try { controller.set(state, options); } catch { rejected++; }
        }
        const unchanged = initial === root.outerHTML && document.activeElement === action;
        controller.set('conflict', { action: { label: '重新核对', id: 'retry' } });
        const sameAction = root.querySelector('#retry') === action && document.activeElement === action;
        let valid = true;
        for (let index = 0; index < 20; index++) {
            valid &&= api.saveStatus(root) === controller;
            const old = controller; old.destroy(); controller = api.saveStatus(root); old.destroy();
            valid &&= api.saveStatus(root) === controller && old.set('synced') === false;
            controller.set('error', { action: { label: '重试', id: 'retry' } }); action.click();
        }
        controller.set('synced');
        const focus = document.activeElement === root;
        controller.destroy();
        return { rejected, unchanged, sameAction, sharedOwner, valid, calls, focus, removedAction: root.querySelector('[data-lq-status-action]') === null };
    });
    expect(result).toEqual({ rejected: 3, unchanged: true, sameAction: true, sharedOwner: true, valid: true, calls: 20, focus: true, removedAction: true });
    await expect(page.locator('#business-draft')).toHaveValue('原始输入');
});

test('LQ conflict keeps caller DOM/input identity and has no implicit action or request', async ({ page }) => {
    await mount(page);
    await page.evaluate(fixture => { document.getElementById('fixture')!.innerHTML = fixture.composition; }, fixture);
    await page.locator('#local-draft').fill('409 后仍须保留的本地评语');
    const result = await page.evaluate(() => {
        const api = (window as any).statusApi, input = document.getElementById('local-draft')!, label = input.previousElementSibling!;
        const local = document.createElement('div'); local.append(label, input);
        const originalParent = local.parentNode;
        const slots = new Map([['local', local], ['bad-slot', document.createElement('div')]]);
        let rejected = false; try { api.conflict({ action: { label: '重新核对' } }, document, slots); } catch { rejected = true; }
        const notMoved = local.parentNode === originalParent;
        let calls = 0;
        const root = api.conflict({ action: { label: '重新核对' }, server: '服务器说明' }, document, new Map([['local', local]]));
        document.getElementById('fixture')!.replaceChildren(root);
        root.querySelector('[data-lq-status-action]').addEventListener('click', () => { calls++; });
        (window as any).actionCalls = () => calls;
        return { rejected, notMoved, sameInput: document.getElementById('local-draft') === input, calls, live: root.querySelectorAll('[aria-live]').length };
    });
    expect(result).toEqual({ rejected: true, notMoved: true, sameInput: true, calls: 0, live: 0 });
    await expect(page.locator('#local-draft')).toHaveValue('409 后仍须保留的本地评语');
    await page.getByRole('button', { name: '重新核对' }).click();
    expect(await page.evaluate(() => (window as any).actionCalls())).toBe(1);
    await expect(page.locator('#local-draft')).toHaveValue('409 后仍须保留的本地评语');
});

for (const appearance of ['light', 'dark']) for (const palette of ['indigo', 'teal', 'rose', 'sky', 'mint', 'violet']) {
    test(`LQ status ${appearance}/${palette} long text stays readable at 390 with axe`, async ({ page }) => {
        await page.setViewportSize({ width: 390, height: 844 }); await mount(page);
        await page.evaluate(({ appearance, palette, fixture }) => {
            Object.assign(document.documentElement.dataset, { appearance, uiPalette: palette });
            const api = (window as any).statusApi, host = document.getElementById('fixture')!;
            for (const item of fixture.cases.slice(0, 9)) host.append(api.createStatus(item.kind, item.props));
            host.append(api.status({ family: 'score', state: 'excellent', label: '优秀' }));
            host.append(api.alert({ tone: 'info', title: '操作说明', body: '请先核对本地修改。' }));
            host.append(api.alert({ tone: 'danger', title: '保存失败', body: '当前输入仍保留在页面中。' }));
            host.append(api.alert({ tone: 'warning', title: '保存前请核对', body: '这是必须保留且能完整阅读的说明。'.repeat(14), action: { label: '查看详细说明' } }));
            host.append(api.conflict({ action: { label: '重新核对服务器最新内容' }, local: '本地草稿'.repeat(25), server: '服务器新内容'.repeat(25) }));
        }, { appearance, palette, fixture });
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        const results = await new AxeBuilder({ page }).include('main').analyze();
        expect(results.violations).toEqual([]);
        if (palette === 'rose') await page.screenshot({ path: `.codex-temp/lq-status-${appearance}-390.png`, fullPage: true });
    });
}

test('LQ status coarse actions remain 44px and forced colors keep a visible boundary', async ({ browser }) => {
    const context = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
    const page = await context.newPage();
    try {
        await mount(page);
        await page.evaluate(() => document.getElementById('fixture')!.append((window as any).statusApi.save_status({ state: 'error', action: { label: '重试' } })));
        const rect = await page.getByRole('button', { name: '重试' }).boundingBox();
        expect(rect!.height).toBeGreaterThanOrEqual(44); expect(rect!.width).toBeGreaterThanOrEqual(44);
        await page.emulateMedia({ forcedColors: 'active' });
        const colors = await page.locator('.lq-status').evaluate(node => { const style = getComputedStyle(node); return { color: style.color, bg: style.backgroundColor, border: style.borderTopColor }; });
        expect(colors.color).not.toBe(colors.bg); expect(colors.border).toBe(colors.color);
    } finally { await context.close(); }
});
