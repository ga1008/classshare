import { test, expect, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';

const fixture = JSON.parse(execFileSync(process.platform === 'win32' ? 'venv/Scripts/python.exe' : 'venv/bin/python', ['tests/e2e/scripts/render_lq_composer.py'], { encoding: 'utf8' }));
async function mount(page: Page, props: Record<string, unknown> = {}) {
    const requests: string[] = [];
    await page.route('**/*', route => {
        const url = new URL(route.request().url());
        if (url.origin !== 'https://lq-composer.test') return route.abort();
        if (url.pathname.startsWith('/static/')) {
            const file = path.resolve(`.${url.pathname}`);
            if (file.startsWith(path.resolve('static') + path.sep) && fs.existsSync(file)) return route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body: fs.readFileSync(file) });
        }
        if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh-CN" data-theme="lanshare" data-appearance="light" data-ui-palette="indigo" data-lq-glass="off" data-lq-tier="C"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>LQ composer</title><link rel="stylesheet" href="/static/css/tailwind-app.css"><style>body{margin:0}main{padding:16px;box-sizing:border-box}h1{margin-bottom:16px}.stage{position:relative;height:600px;max-height:90vh}.stream{height:100%;overflow:auto;box-sizing:border-box}.dock{position:absolute;bottom:0;inset-inline:0;height:68px}#fixture{max-width:640px;margin:auto}</style></head><body><main><h1>消息输入</h1><div id="fixture"><form id="native"></form></div><footer id="page-footer">原页面页脚</footer></main><script type="module">import * as api from '/static/js/lq/composer.js';window.api=api;window.records=[];window.extra=[];document.getElementById('native').addEventListener('submit',e=>{e.preventDefault();window.records.push({submitter:e.submitter?.getAttribute('data-lq-composer-send'),fields:[...new FormData(e.target,e.submitter)]});});document.body.dataset.ready='true';</script></body></html>` });
        requests.push(url.pathname); return route.fulfill({ status: 404, body: 'No application network' });
    });
    await page.goto('https://lq-composer.test/'); await expect(page.locator('body')).toHaveAttribute('data-ready', 'true');
    await page.evaluate(props => {
        const w = window as any; w.root = w.api.composer(props); document.getElementById('native')!.append(w.root); w.handle = w.api.enhanceComposer(w.root);
    }, props);
    return requests;
}

test('LQ composer actual Jinja, HTML and Element preserve typed native fields and text', async ({ page }) => {
    expect(fixture.isolated).toBe(true); expect(fixture.integrated).toBe(true); expect(fixture.cases.filter((c: any) => c.error)).toEqual([]); expect(fixture.invalid.filter((c: any) => !c.error)).toEqual([]);
    await mount(page);
    const actual = await page.evaluate(serialized => {
        const f = JSON.parse(serialized);
        const api = (window as any).api;
        function semantic(n: Node): any { if (n.nodeType === 3) return n.textContent?.trim() ? { text: n.textContent } : null; const el = n as Element; return { tag: el.tagName.toLowerCase(), attrs: Object.fromEntries([...el.attributes].map(a => [a.name, a.value]).sort()), children: [...n.childNodes].map(semantic).filter(Boolean) }; }
        const parse = (html: string) => { const t = document.createElement('template'); t.innerHTML = html; return [...t.content.childNodes].map(semantic).filter(Boolean); };
        return { cases: f.cases.map((c: any) => ({ props: api.composerProps('composer', c.props), html: parse(api.composerMarkup('composer', c.props)), jinja: parse(c.html), element: [semantic(api.composer(c.props))] })), invalid: f.invalid.map((c: any) => ['composerProps', 'composerMarkup', 'createComposer'].map(method => { try { api[method]('composer', c.props); return false; } catch { return true; } })) };
    }, JSON.stringify(fixture));
    actual.cases.forEach((c: any, i: number) => { expect(c.props).toEqual(fixture.cases[i].normalized); expect(c.html).toEqual(c.jinja); expect(c.element).toEqual(c.jinja); });
    actual.invalid.forEach((r: boolean[], i: number) => expect(r, JSON.stringify(fixture.invalid[i].props)).toEqual([true, true, true]));
});

test('LQ composer explicit Enter policy preserves IME, Safari composition completion, modifiers and multiline', async ({ page }) => {
    await mount(page, { submit_name: 'action', submit_value: 'send' }); const input = page.getByRole('textbox', { name: '消息内容' });
    await input.fill('默认'); await input.press('Enter'); await expect(input).toHaveValue('默认\n');
    await page.evaluate(() => (window as any).handle.set({ enter: 'send' }));
    await input.press('Shift+Enter'); await expect(input).toHaveValue('默认\n\n');
    for (const key of ['Control+Enter', 'Meta+Enter', 'Alt+Enter']) await input.press(key);
    expect(await page.evaluate(() => (window as any).records)).toEqual([]);
    const committed = await input.evaluate(el => {
        const t = el as HTMLTextAreaElement;
        const fire = (options: KeyboardEventInit = {}) => { const e = new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true, ...options }); t.dispatchEvent(e); return e.defaultPrevented; };
        t.dispatchEvent(new CompositionEvent('compositionstart', { bubbles: true }));
        const during = fire(); t.dispatchEvent(new CompositionEvent('compositionend', { bubbles: true, data: '中文' }));
        return [during, fire(), fire({ isComposing: true }), fire({ keyCode: 229 } as KeyboardEventInit), fire({ repeat: true })];
    });
    expect(committed).toEqual([false, false, false, false, false]); expect(await page.evaluate(() => (window as any).records.length)).toBe(0);
    // After the composition task finishes, an explicit new Enter is a distinct send intent.
    await input.press('Enter'); expect(await page.evaluate(() => (window as any).records.length)).toBe(1);
    await expect(input).toHaveValue(/默认/); // Presentation never clears after a submit.
});

test('LQ composer uses real requestSubmit validation, FormData, submitter and external form association', async ({ page }) => {
    const requests = await mount(page, { enter: 'send', required: true, name: 'message', submit_name: 'action', submit_value: 'send' });
    await page.evaluate(() => { const form = document.getElementById('native')!; const other = document.createElement('input'); other.name = 'permission'; other.required = true; other.setAttribute('aria-label', '许可'); form.prepend(other); });
    await page.getByRole('textbox', { name: '消息内容' }).fill('有内容'); await page.getByRole('textbox', { name: '消息内容' }).press('Enter');
    expect(await page.evaluate(() => (window as any).records)).toEqual([]); await expect(page.getByRole('textbox', { name: '许可' })).toBeFocused();
    await page.getByRole('textbox', { name: '许可' }).fill('allowed'); await page.getByRole('button', { name: '发送', exact: true }).click();
    expect(await page.evaluate(() => (window as any).records[0])).toEqual({ submitter: '', fields: [['permission', 'allowed'], ['message', '有内容'], ['action', 'send']] });
    await page.evaluate(() => { const w = window as any; w.handle.destroy(); w.root.remove(); w.root = w.api.composer({ form: 'native', value: '外部', submit_name: 'action', submit_value: 'external', enter: 'send' }); document.getElementById('fixture')!.append(w.root); w.handle = w.api.enhanceComposer(w.root); });
    await page.getByRole('textbox', { name: '消息内容' }).press('Enter');
    expect(await page.evaluate(() => (window as any).records[1].fields)).toEqual([['permission', 'allowed'], ['content', '外部'], ['action', 'external']]); expect(requests).toEqual([]);
});

test('LQ composer controlled busy, permissions and attachments retain draft, native reset and failed-state focus', async ({ page }) => {
    await mount(page, { value: '初始' }); const input = page.getByRole('textbox', { name: '消息内容' });
    await input.fill(' 草稿保留 '); await input.focus();
    await page.evaluate(() => (window as any).handle.set({ busy: true })); await expect(input).toHaveAttribute('readonly', ''); await expect(input).toBeFocused(); await expect(page.getByRole('button', { name: '发送', exact: true })).toBeDisabled();
    expect(await page.evaluate(() => [...new FormData(document.getElementById('native') as HTMLFormElement)])).toEqual([['content', ' 草稿保留 ']]);
    await page.evaluate(() => { const w = window as any; (document.getElementById('native') as HTMLFormElement).requestSubmit(w.root.querySelector('[data-lq-composer-send]')); });
    expect(await page.evaluate(() => (window as any).records)).toEqual([]);
    await page.evaluate(() => { const w = window as any, old = w.root.outerHTML; try { w.handle.set({ busy: false, disabled: 'bad' }); } catch {} w.atomic = old === w.root.outerHTML; });
    expect(await page.evaluate(() => (window as any).atomic)).toBe(true);
    await page.evaluate(() => (window as any).handle.set({ busy: false })); await expect(input).toHaveValue(' 草稿保留 '); await expect(input).toBeFocused();
    await input.fill('   '); await expect(page.getByRole('button', { name: '发送', exact: true })).toBeDisabled();
    await page.evaluate(() => (window as any).handle.set({ hasContent: true })); await page.getByRole('button', { name: '发送', exact: true }).click();
    expect(await page.evaluate(() => (window as any).records.length)).toBe(1);
    await page.evaluate(() => (window as any).handle.set({ disabled: true })); await expect(input).toBeDisabled(); expect(await page.evaluate(() => [...new FormData(document.getElementById('native') as HTMLFormElement)])).toEqual([]);
    await page.evaluate(() => { (window as any).handle.set({ disabled: false }); (document.getElementById('native') as HTMLFormElement).reset(); });
    await expect(input).toHaveValue('初始');
});

test('LQ composer autoresizes to six lines, preserves long input scroll and does not move the footer in flow', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 }); await mount(page); const input = page.getByRole('textbox', { name: '消息内容' });
    await input.fill(Array.from({ length: 14 }, (_, i) => `第${i + 1}行中文🙂`).join('\n'));
    const size = await input.evaluate(el => ({ height: el.getBoundingClientRect().height, scroll: el.scrollHeight, line: parseFloat(getComputedStyle(el).lineHeight), overflow: getComputedStyle(el).overflowY }));
    expect(size.height).toBeLessThanOrEqual(size.line * 6 + 17); expect(size.scroll).toBeGreaterThan(size.height); expect(size.overflow).toBe('auto');
    expect(await page.locator('[data-lq-composer]').evaluate(el => getComputedStyle(el).position)).toBe('relative');
    const footer = await page.locator('#page-footer').boundingBox(), composer = await page.locator('[data-lq-composer]').boundingBox(); expect(footer!.y).toBeGreaterThanOrEqual(composer!.y + composer!.height);
    await input.fill('短'); expect((await input.boundingBox())!.height).toBeLessThan(size.height); await expect(input).toHaveValue('短');
});

test('LQ composer explicit fixed container, inherited Dock, viewport keyboard and cleanup are bounded', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 }); await mount(page, { value: '固定消息' });
    const before = await page.evaluate(() => { const w = window as any; w.handle.destroy(); const container = document.createElement('section'); container.className = 'stage'; container.style.setProperty('--lq-dock-h', '80px'); const stream = document.createElement('div'); stream.className = 'stream'; stream.textContent = '聊天记录 '.repeat(500); const dock = document.createElement('div'); dock.className = 'dock'; dock.textContent = '明确的 Dock 占位'; container.append(stream, w.root, dock); document.getElementById('fixture')!.append(container); w.container = container; w.stream = stream; const old = container.outerHTML; w.handle = w.api.enhanceComposer(w.root, { fixed: { container, contentRoot: stream } }); return old; });
    const bottom = async () => page.locator('[data-lq-composer]').evaluate(el => parseFloat(getComputedStyle(el).bottom));
    expect(await bottom()).toBe(92);
    await page.getByRole('textbox', { name: '消息内容' }).focus();
    await page.evaluate(() => { const vv = window.visualViewport!; Object.defineProperty(vv, 'height', { configurable: true, value: 300 }); Object.defineProperty(vv, 'offsetTop', { configurable: true, value: 0 }); vv.dispatchEvent(new Event('resize')); });
    await expect.poll(bottom).toBeGreaterThan(250);
    await page.evaluate(() => { Object.defineProperty(window.visualViewport!, 'scale', { configurable: true, value: 2 }); window.visualViewport!.dispatchEvent(new Event('resize')); }); await expect.poll(bottom).toBe(92);
    const restored = await page.evaluate(() => { const w = window as any; w.handle.destroy(); return w.container.outerHTML; }); expect(restored).toBe(before);
    const failure = await page.evaluate(() => { const w = window as any; w.container.style.position = 'static'; const before = w.container.outerHTML; let threw = false; try { w.api.enhanceComposer(w.root, { fixed: { container: w.container, contentRoot: w.stream } }); } catch { threw = true; } return { threw, unchanged: before === w.container.outerHTML }; });
    expect(failure).toEqual({ threw: true, unchanged: true });
});

test('LQ composer Node and real caller slots retain input identity and business action handlers', async ({ page }) => {
    await mount(page); await page.evaluate(fixture => { const w = window as any; w.handle.destroy(); document.getElementById('fixture')!.innerHTML = fixture.composition; w.root = document.querySelector('[data-lq-composer]'); w.handle = w.api.enhanceComposer(w.root); }, fixture);
    await expect(page.getByRole('textbox', { name: '业务上下文' })).toHaveValue('原值'); await expect(page.locator('#warning')).toBeVisible();
    const result = await page.evaluate(() => {
        const w = window as any, input = document.getElementById('context')!, original = input.parentNode; let events = 0; input.addEventListener('input', () => events++);
        let threw = false; try { w.api.composer({ busy: 'bad' }, { content: [input] }); } catch { threw = true; }
        const unchanged = input.parentNode === original; const created = w.api.composer({}, { content: [input] }); document.getElementById('fixture')!.append(created); input.dispatchEvent(new Event('input'));
        let badSlot = false; try { w.api.composer({}, { content: ['<b>unsafe</b>'] }); } catch { badSlot = true; }
        return { threw, unchanged, identity: created.contains(input), events, badSlot };
    }); expect(result).toEqual({ threw: true, unchanged: true, identity: true, events: 1, badSlot: true });
    const invalid = await page.evaluate(() => {
        const w = window as any, other = document.implementation.createHTMLDocument('same realm'), host = document.createElement('div'), shadow = host.attachShadow({ mode: 'open' });
        const fragment = document.createDocumentFragment(); fragment.append(document.createElement('body'));
        const input = document.getElementById('context')!, parent = input.parentNode;
        return [other.createElement('span'), document.body, document.documentElement, shadow, fragment].map(node => {
            let rejected = false; try { w.api.composer({}, { content: [input, node] }); } catch { rejected = true; }
            return rejected && input.parentNode === parent;
        });
    }); expect(invalid).toEqual([true, true, true, true, true]);
});

test('LQ composer twenty init/destroy cycles and duplicate module imports release every resource', async ({ page }) => {
    const requests = await mount(page, { value: '保留', enter: 'send' });
    const result = await page.evaluate(async () => {
        const w = window as any; w.handle.destroy();
        const container = document.createElement('section'); container.className = 'stage'; const stream = document.createElement('div'); container.append(stream, w.root); document.getElementById('fixture')!.append(container);
        const fixed = { container, contentRoot: stream };
        const add = EventTarget.prototype.addEventListener, remove = EventTarget.prototype.removeEventListener, active = new Map<EventTarget, Map<string, Set<any>>>();
        EventTarget.prototype.addEventListener = function(type: string, fn: any, opts: any) { if (!active.has(this)) active.set(this, new Map()); const by = active.get(this)!; if (!by.has(type)) by.set(type, new Set()); by.get(type)!.add(fn); return add.call(this, type, fn, opts); };
        EventTarget.prototype.removeEventListener = function(type: string, fn: any, opts: any) { active.get(this)?.get(type)?.delete(fn); return remove.call(this, type, fn, opts); };
        const RO = window.ResizeObserver; let observed = 0;
        window.ResizeObserver = class extends RO { constructor(fn: ResizeObserverCallback) { super(fn); observed++; } disconnect() { observed--; super.disconnect(); } };
        const alias = await import('/static/js/lq/composer.js?distinct-url') as any;
        let singleton = true, oldSafe = true;
        for (let i = 0; i < 20; i++) { const a = w.api.enhanceComposer(w.root, { fixed }); singleton &&= alias.enhanceComposer(w.root, { fixed }) === a; a.destroy(); const b = alias.enhanceComposer(w.root, { fixed }); a.destroy(); oldSafe &&= w.api.enhanceComposer(w.root, { fixed }) === b; b.destroy(); }
        const listeners = [...active.values()].reduce((sum, m) => sum + [...m.values()].reduce((s, v) => s + v.size, 0), 0);
        EventTarget.prototype.addEventListener = add; EventTarget.prototype.removeEventListener = remove; window.ResizeObserver = RO;
        document.getElementById('native')!.append(w.root); w.handle = w.api.enhanceComposer(w.root); return { singleton, oldSafe, listeners, observed, value: w.root.querySelector('textarea').value };
    }); expect(result).toEqual({ singleton: true, oldSafe: true, listeners: 0, observed: 0, value: '保留' });
    await page.getByRole('textbox', { name: '消息内容' }).press('Enter'); expect(await page.evaluate(() => (window as any).records.length)).toBe(1); expect(requests).toEqual([]);
});

test('LQ composer attachment and emoji controls retain explicit business handlers and registered icons', async ({ page }) => {
    const requests = await mount(page);
    const icons = await page.evaluate(async () => {
        const w = window as any, icons = await import('/static/js/lq/icons.js') as any;
        w.actions = [];
        for (const kind of ['attachment', 'emoji']) w.root.querySelector(`[data-lq-composer-${kind}]`).addEventListener('click', () => { w.actions.push(kind); w.handle.set({ hasContent: true }); });
        return ['send', 'paperclip', 'smile'].map(name => icons.resolveIcon(name));
    }); expect(icons).toEqual(['send', 'paperclip', 'smile']);
    await page.getByRole('button', { name: '添加附件' }).click(); await page.getByRole('button', { name: '表情', exact: true }).click();
    expect(await page.evaluate(() => (window as any).actions)).toEqual(['attachment', 'emoji']); expect(await page.evaluate(() => (window as any).records)).toEqual([]);
    await page.getByRole('button', { name: '发送', exact: true }).click(); expect(await page.evaluate(() => (window as any).records.length)).toBe(1); await expect(page.getByRole('textbox', { name: '消息内容' })).toHaveValue(''); expect(requests).toEqual([]);
});

for (const appearance of ['light', 'dark']) for (const palette of ['indigo', 'teal', 'rose', 'sky', 'mint', 'violet']) test(`LQ composer ${appearance} ${palette} 390px accessible real controls`, async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 }); await mount(page, { value: '这段消息保留原样，等待业务确认发送。\n支持多行与中文输入。' });
    await page.evaluate(({ appearance, palette }) => { document.documentElement.dataset.appearance = appearance; document.documentElement.dataset.uiPalette = palette; }, { appearance, palette });
    const axe = await new AxeBuilder({ page }).analyze(); expect(axe.violations).toEqual([]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await expect(page.getByRole('button')).toHaveCount(3); await expect(page.getByRole('textbox', { name: '消息内容' })).toHaveCount(1);
    if (palette === 'indigo') await page.screenshot({ path: `.codex-temp/lq-composer-${appearance}-390.png`, fullPage: true });
});

test('LQ composer coarse controls, forced colors and reduced motion keep native semantics', async ({ browser }) => {
    const context = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true }); const page = await context.newPage();
    await mount(page, { value: '触屏' }); for (const button of await page.getByRole('button').all()) { const rect = await button.boundingBox(); expect(rect!.width).toBeGreaterThanOrEqual(44); expect(rect!.height).toBeGreaterThanOrEqual(44); }
    await page.emulateMedia({ forcedColors: 'active', reducedMotion: 'reduce' }); await page.getByRole('textbox', { name: '消息内容' }).focus();
    const color = await page.locator('[data-lq-composer]').evaluate(el => ({ color: getComputedStyle(el).color, background: getComputedStyle(el).backgroundColor })); expect(color.color).not.toBe(color.background);
    await page.getByRole('button', { name: '发送', exact: true }).click(); expect(await page.evaluate(() => (window as any).records.length)).toBe(1); await context.close();
});
