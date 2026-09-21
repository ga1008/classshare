import { test, expect, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
const python = process.env.LQ_TEST_PYTHON || (process.platform === 'win32' ? 'venv/Scripts/python.exe' : 'venv/bin/python');
const fixture = JSON.parse(execFileSync(python, ['tests/e2e/scripts/render_lq_upload.py'], { encoding: 'utf8' }));
const sample = { name: '实验.png', mimeType: 'image/png', buffer: Buffer.from('local fixture bytes') };
async function mount(page: Page) {
    await page.route('**/*', route => {
        const url = new URL(route.request().url()); if (url.origin !== 'https://lq-upload.test') return route.abort();
        if (url.pathname.startsWith('/static/js/') || ['/static/css/tailwind-app.css'].includes(url.pathname)) {
            const file = path.resolve(`.${url.pathname}`); if (file.startsWith(path.resolve('static') + path.sep) && fs.existsSync(file)) return route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body: fs.readFileSync(file) });
        }
        if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh-CN" data-theme="lanshare" data-ui-palette="teal" data-appearance="light" data-lq-glass="tinted"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>LQ Upload</title><link rel="stylesheet" href="/static/css/tailwind-app.css"></head><body><main style="padding:24px;max-width:720px"><h1>附件材料</h1><form id="form"><div id="mount"></div><button id="after" type="button">后继操作</button></form></main><script type="module">import * as upload from '/static/js/lq/upload.js';window.upload=upload;window.actions=[];window.filesEvents=[];window.network=[];window.fetch=(...a)=>{window.network.push(a);throw Error('No network permitted')};window.received=[];document.body.dataset.ready='true';</script></body></html>` });
        return route.abort();
    });
    await page.goto('https://lq-upload.test/'); await expect(page.locator('body')).toHaveAttribute('data-ready', 'true');
}
async function create(page: Page, entry = 'dom') {
    await page.evaluate(({ item, entry }) => {
        const w = window as any; let root;
        if (entry === 'dom') root = w.upload.createUpload(item.kind, item.props); else { const template = document.createElement('template'); template.innerHTML = entry === 'jinja' ? item.html : w.upload.uploadMarkup(item.kind, item.props); root = template.content.firstElementChild; }
        document.querySelector('#mount')!.append(root); w.root = root; w.snapshot = item.props.snapshot;
    }, { item: fixture.cases[0], entry });
}
async function bind(page: Page) {
    await page.evaluate(() => { const w = window as any; w.binding = w.upload.bindUpload(w.root, { snapshot: w.snapshot,
        onFiles: (files: FileList, context: any) => { w.filesEvents.push({ source: context.source, actual: files instanceof FileList, same: context.source !== 'input' || files === context.input.files, names: [...files].map(f => f.name), snapshotNames: context.files.map((f: File) => f.name) }); },
        onAction: (context: any) => w.actions.push(context.action),
    }); });
}
async function allStates(page: Page) {
    await page.evaluate(items => { const w = window as any; const states = ['selected', 'validating', 'rejected', 'uploading', 'uploaded', 'failed', 'removing']; w.snapshot = { generation: 1, items: states.map((state, i) => ({ id: String(i), generation: 1, name: `第${i + 1}题-很长的实验步骤说明与截图-${state}.png`, sizeLabel: '240 KB', state, ...(state === 'rejected' ? { reason: '截图与另一题重复，请更换，当前文件未被接受。', rejectionCode: 'duplicate-image', duplicateOfQuestion: '第2题' } : {}), ...(state === 'failed' ? { reason: '网络中断，本地文件仍保留。' } : {}), ...(state === 'uploading' ? { progress: 100 } : {}), ...(state === 'uploaded' ? { confirmation: { id: 42 } } : {}) })) }; w.root = w.upload.createUpload('upload', { ...items.props, snapshot: w.snapshot }); document.querySelector('#mount')!.append(w.root); w.binding = w.upload.bindUpload(w.root, { snapshot: w.snapshot, onAction: (c: any) => w.actions.push(c.action) }); }, fixture.cases[0]);
}
test.describe('LQ Upload controlled presentation', () => {
    test.beforeEach(async ({ page }) => mount(page));
    test('pure helper/Jinja/HTML/DOM parity and full input rejection before DOM movement', async ({ page }) => {
        expect(fixture.isolated).toBe(true);
        for (const item of fixture.cases) {
            expect(item.error).toBeUndefined();
            const result = await page.evaluate(item => {
                const api = (window as any).upload, semantic = (n: Node): any => n.nodeType === Node.TEXT_NODE ? (n.textContent?.trim() ? { text: n.textContent } : null) : ({ tag: (n as Element).tagName.toLowerCase(), attrs: Object.fromEntries([...(n as Element).attributes].map(a => [a.name, a.value]).sort()), children: [...n.childNodes].map(semantic).filter(Boolean) });
                const parse = (html: string) => { const t = document.createElement('template'); t.innerHTML = html; return semantic(t.content.firstElementChild!); };
                return { jinja: parse(item.html), html: parse(api.uploadMarkup(item.kind, item.props)), dom: semantic(api.createUpload(item.kind, item.props)) };
            }, item); expect(result.html).toEqual(result.jinja); expect(result.dom).toEqual(result.jinja);
        }
        for (const item of fixture.invalid) { expect(item.error).toBeTruthy(); expect(await page.evaluate(item => { try { (window as any).upload.uploadProps(item.kind, item.props); return false; } catch { return true; } }, item)).toBe(true); }
        expect(await page.evaluate(() => { const node = document.createElement('p'); document.body.append(node); try { (window as any).upload.createUpload('file_chip', { item: { id: 'x', generation: 1, name: node, state: 'selected' } }); return false; } catch { return node.isConnected; } })).toBe(true);
    });
    for (const entry of ['jinja', 'html', 'dom']) test(`${entry}: native file input keeps actual FileList, one change and FormData before/after binding`, async ({ page }) => {
        await create(page, entry); const input = page.locator('input[type=file]');
        await input.setInputFiles(sample); expect(await input.evaluate((n: HTMLInputElement) => n.files![0].name)).toBe(sample.name);
        await bind(page); await page.evaluate(() => { (window as any).changes = 0; document.querySelector('input')!.addEventListener('change', () => (window as any).changes++); });
        await input.setInputFiles({ ...sample, name: 'second.png' });
        expect(await page.evaluate(() => (window as any).filesEvents)).toEqual([{ source: 'input', actual: true, same: true, names: ['second.png'], snapshotNames: ['second.png'] }]);
        expect(await page.evaluate(() => [(window as any).changes, (new FormData(document.querySelector('#form') as HTMLFormElement).get('files') as File).name])).toEqual([1, 'second.png']);
        await expect(page.locator('[data-file-state]')).toHaveAttribute('data-file-state', 'selected'); expect(await page.evaluate(() => (window as any).network)).toEqual([]);
    });
    test('native keyboard picker is reachable and policy remains visible while selection is empty', async ({ page }) => {
        await create(page); await bind(page); await page.locator('input').focus();
        const chooser = page.waitForEvent('filechooser'); await page.locator('input').press('Enter'); await (await chooser).setFiles(sample);
        await expect(page.locator('.lq-upload__policy')).toBeVisible(); await expect(page.locator('input')).toBeFocused(); await page.locator('input').press('Tab'); await expect(page.getByRole('group', { name: '网络实验.png' }).getByRole('button', { name: '移除', exact: true })).toBeFocused();
    });
    test('drop/paste forward real FileList without replacing input.files, synthesizing change or filtering type/size', async ({ page }) => {
        await create(page); await bind(page); await page.locator('input').setInputFiles(sample);
        await page.evaluate(async () => {
            const w = window as any; w.changes = 0; w.filesEvents = []; const input = document.querySelector('input')!, original = input.files; input.addEventListener('change', () => w.changes++); const zone = document.querySelector('.lq-dropzone')!;
            for (const source of ['drop', 'paste']) { const transfer = new DataTransfer(); transfer.items.add(new File(['controller validates this'], 'not-accepted.exe', { type: 'application/octet-stream' })); const event = source === 'drop' ? new DragEvent('drop', { bubbles: true, cancelable: true, dataTransfer: transfer }) : new ClipboardEvent('paste', { bubbles: true, cancelable: true, clipboardData: transfer }); zone.dispatchEvent(event); await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); }
            w.sameFiles = original === input.files;
        });
        expect(await page.evaluate(() => ({ events: (window as any).filesEvents, changes: (window as any).changes, same: (window as any).sameFiles }))).toEqual({ events: ['drop', 'paste'].map(source => ({ source, actual: true, same: true, names: ['not-accepted.exe'], snapshotNames: ['not-accepted.exe'] })), changes: 0, same: true });
    });
    test('all file and queue states are explicit, duplicate reason/owning question persist, 100% does not imply uploaded', async ({ page }) => {
        await allStates(page); await expect(page.locator('#attachments')).toHaveAttribute('data-upload-state', 'busy'); await expect(page.locator('[data-file-state=rejected]')).toContainText('第2题'); await expect(page.locator('[data-file-state=uploading]')).toContainText('等待服务器确认');
        expect(await page.locator('[data-file-state=uploading]').getByRole('progressbar').getAttribute('value')).toBe('100'); await expect(page.locator('[data-file-state=uploaded]')).toContainText('已保存到服务器');
        await page.evaluate(() => { const w = window as any; w.binding.update({ generation: 2, items: w.snapshot.items.filter((i: any) => i.state === 'failed' || i.state === 'rejected') }); }); await expect(page.locator('#attachments')).toHaveAttribute('data-upload-state', 'partial-failed'); await expect(page.locator('.lq-file-chip__reason')).toHaveCount(2);
        await page.evaluate(() => (window as any).binding.update({ generation: 3, items: [] })); await expect(page.locator('#attachments')).toHaveAttribute('data-upload-state', 'idle'); await expect(page.locator('.lq-upload__policy')).toBeVisible();
    });
    test('pending selection retains File references, releases on veto/reject and does not restore stale input state after destroy', async ({ page }) => {
        await create(page); await page.evaluate(() => { const w = window as any; w.binding = w.upload.bindUpload(w.root, { snapshot: w.snapshot, onFiles: (files, context) => { w.context = context; w.filesEvents.push([...files].map(f => f.name)); return new Promise((resolve, reject) => { w.resolve = resolve; w.reject = reject; }); } }); });
        await page.locator('input').setInputFiles(sample); await expect(page.locator('input')).toBeDisabled(); expect(await page.evaluate(() => (window as any).context.files[0].name)).toBe(sample.name);
        await page.evaluate(() => (window as any).resolve(false)); await expect(page.locator('input')).toBeEnabled(); await expect(page.locator('[data-file-state=selected]')).toHaveCount(1);
        await page.locator('input').setInputFiles({ ...sample, name: 'second.png' }); await page.evaluate(() => (window as any).reject(new Error('检查失败，请重试'))); await expect(page.locator('input')).toBeEnabled(); await expect(page.getByRole('status')).toHaveText('检查失败，请重试');
        await page.locator('input').setInputFiles({ ...sample, name: 'third.png' }); await page.evaluate(() => { const w = window as any; w.binding.destroy(); w.inputAfter = w.root.querySelector('input'); w.inputAfter.disabled = true; w.reject(new Error('late')); }); await expect(page.locator('input')).toBeDisabled(); expect(await page.evaluate(() => (window as any).context.isCurrent())).toBe(false); await expect(page.locator('[data-lq-upload-notice]')).toBeEmpty();
    });
    test('synchronous controller replacement and exception cannot retain busy or operate an old item', async ({ page }) => {
        await create(page); await page.evaluate(() => { const w = window as any; w.binding = w.upload.bindUpload(w.root, { snapshot: w.snapshot, onAction: c => { w.context = c; w.binding.update({ generation: 2, items: [{ id: 'a', generation: 2, name: 'fresh.pdf', state: 'selected' }] }); throw Error('old callback error'); } }); });
        await page.getByRole('group', { name: '网络实验.png' }).getByRole('button', { name: '移除', exact: true }).click(); await expect(page.getByRole('group', { name: 'fresh.pdf' }).getByRole('button', { name: '移除', exact: true })).toBeEnabled(); await expect(page.locator('[data-lq-upload-notice]')).toBeEmpty(); expect(await page.evaluate(() => (window as any).context.isCurrent())).toBe(false);
    });
    test('action promise/veto/reject never removes item, releases busy, and retries exactly once per intent', async ({ page }) => {
        await create(page); await page.evaluate(() => { const w = window as any; w.snapshot.items[0] = { ...w.snapshot.items[0], state: 'failed', reason: '断网' }; w.binding = w.upload.bindUpload(w.root, { snapshot: w.snapshot, onAction: (c: any) => { w.actions.push(c.action); return new Promise((resolve, reject) => { w.resolve = resolve; w.reject = reject; }); } }); });
        const retry = page.getByRole('group', { name: '网络实验.png' }).getByRole('button', { name: '重试', exact: true }); await retry.click(); await expect(retry).toBeDisabled(); await expect(page.getByRole('group', { name: '网络实验.png' }).getByRole('button', { name: '移除', exact: true })).toBeDisabled();
        await page.evaluate(() => (window as any).resolve(false)); await expect(retry).toBeEnabled(); await expect(page.locator('[data-file-id=a]')).toHaveCount(1);
        await retry.click(); await page.evaluate(() => (window as any).reject(new Error('控制器拒绝重试，本地保留'))); await expect(retry).toBeEnabled(); await expect(page.getByRole('status')).toHaveText('控制器拒绝重试，本地保留');
        expect(await page.evaluate(() => (window as any).actions)).toEqual(['retry', 'retry']); await expect(page.locator('[data-file-state=failed]')).toHaveCount(1);
    });
    test('snapshot and item generations block stale results/actions; replacement focus is not stolen by late promises', async ({ page }) => {
        await create(page); await page.evaluate(() => { const w = window as any; w.binding = w.upload.bindUpload(w.root, { snapshot: w.snapshot, onAction: (c: any) => { w.context = c; return new Promise((resolve, reject) => { w.resolve = resolve; w.reject = reject; }); } }); });
        await page.getByRole('group', { name: '网络实验.png' }).getByRole('button', { name: '移除', exact: true }).click();
        expect(await page.evaluate(() => { const w = window as any; const next = { generation: 2, items: [{ ...w.snapshot.items[0], generation: 2, name: 'replacement.png' }] }; w.binding.update(next); return [w.binding.update({ ...next, generation: 1, items: [] }), w.context.isCurrent()]; })).toEqual([false, false]);
        await page.locator('#after').focus(); await page.evaluate(() => (window as any).reject(new Error('late failure'))); await expect(page.locator('#after')).toBeFocused(); await expect(page.locator('[data-lq-upload-notice]')).toBeEmpty(); await expect(page.getByRole('group', { name: 'replacement.png' }).getByRole('button', { name: '移除', exact: true })).toBeEnabled();
    });
    test('disabled snapshots suppress input/drop/actions and late completion cannot unlock a new disabled generation', async ({ page }) => {
        await create(page); await page.evaluate(() => { const w = window as any; w.binding = w.upload.bindUpload(w.root, { snapshot: w.snapshot, onAction: () => new Promise(resolve => w.resolve = resolve), onFiles: () => w.filesEvents.push('bad') }); });
        await page.getByRole('group', { name: '网络实验.png' }).getByRole('button', { name: '移除', exact: true }).click(); await page.evaluate(() => { const w = window as any; w.binding.update({ ...w.snapshot, generation: 2, disabled: true }); w.resolve(); const dt = new DataTransfer(); dt.items.add(new File(['x'], 'x.txt')); document.querySelector('.lq-dropzone')!.dispatchEvent(new DragEvent('drop', { bubbles: true, cancelable: true, dataTransfer: dt })); });
        await expect(page.locator('input')).toBeDisabled(); await expect(page.getByRole('group', { name: '网络实验.png' }).getByRole('button', { name: '移除', exact: true })).toBeDisabled(); expect(await page.evaluate(() => (window as any).filesEvents)).toEqual([]);
        await page.evaluate(() => (window as any).binding.destroy()); await expect(page.locator('input')).toBeDisabled();
    });
    test('progress snapshots preserve existing focused action DOM and invalid snapshots make no partial changes', async ({ page }) => {
        await create(page); await bind(page); await page.getByRole('group', { name: '网络实验.png' }).getByRole('button', { name: '移除', exact: true }).focus();
        const result = await page.evaluate(() => { const w = window as any, button = document.activeElement, input = document.querySelector('input'); w.binding.update({ generation: 1, items: [...w.snapshot.items, { id: 'b', generation: 1, name: 'next.png', state: 'uploading', progress: 20 }] }); const before = w.root.innerHTML; let rejected = false; try { w.binding.update({ generation: 2, items: [{ id: 'x', generation: 1, name: 'bad', state: 'uploaded' }] }); } catch { rejected = true; } return { same: document.activeElement === button, sameInput: document.querySelector('input') === input, unchanged: before === w.root.innerHTML, rejected }; });
        expect(result).toEqual({ same: true, sameInput: true, unchanged: true, rejected: true });
    });
    test('synchronous callback destroy and DOM removal dispose listeners and make pending contexts stale', async ({ page }) => {
        await create(page); await page.evaluate(() => { const w = window as any; w.binding = w.upload.bindUpload(w.root, { snapshot: w.snapshot, onAction: c => { w.context = c; w.binding.destroy(); return Promise.reject(new Error('late')); } }); }); await page.getByRole('group', { name: '网络实验.png' }).getByRole('button', { name: '移除', exact: true }).click();
        expect(await page.evaluate(() => (window as any).context.isCurrent())).toBe(false); await expect(page.locator('[data-lq-upload-notice]')).toBeEmpty();
        await page.evaluate(() => { const w = window as any; w.binding = w.upload.bindUpload(w.root, { snapshot: w.snapshot }); w.root.remove(); }); expect(await page.evaluate(() => (window as any).binding.update({ generation: 4, items: [] }))).toBe(false);
    });
    test('20 cycles release all own observers/listeners with one cross-URL owner and unchanged native FileList', async ({ page }) => {
        await create(page); await page.locator('input').setInputFiles(sample);
        const result = await page.evaluate(async () => {
            const w = window as any, input = document.querySelector('input')!, files = input.files, other = await import('/static/js/lq/upload.js?copy');
            let observers = 0; const ownListeners = new Set<any>(), NativeObserver = window.MutationObserver, add = EventTarget.prototype.addEventListener, remove = EventTarget.prototype.removeEventListener;
            EventTarget.prototype.addEventListener = function(type: string, fn: any, options?: any) { if (this instanceof Element && w.root.contains(this)) ownListeners.add(fn); return add.call(this, type, fn, options); };
            EventTarget.prototype.removeEventListener = function(type: string, fn: any, options?: any) { ownListeners.delete(fn); return remove.call(this, type, fn, options); };
            window.MutationObserver = class extends NativeObserver { tracked = false; observe(...args: Parameters<MutationObserver['observe']>) { if (!this.tracked) { this.tracked = true; observers++; } super.observe(...args); } disconnect() { if (this.tracked) { this.tracked = false; observers--; } super.disconnect(); } };
            for (let i = 0; i < 20; i++) { const binding = w.upload.bindUpload(w.root, { snapshot: w.snapshot }); if (other.bindUpload(w.root) !== binding) throw new Error('duplicate owner'); binding.destroy(); binding.destroy(); }
            const result = { observers, listeners: ownListeners.size, same: input === document.querySelector('input') && files === input.files, files: input.files![0].name, rows: w.root.querySelectorAll('[data-file-id]').length };
            EventTarget.prototype.addEventListener = add; EventTarget.prototype.removeEventListener = remove; window.MutationObserver = NativeObserver; return result;
        }); expect(result).toEqual({ observers: 0, listeners: 0, same: true, files: sample.name, rows: 1 });
    });
    test('multiple upload roots and duplicate module URLs share one document observer and dispose independently', async ({ page }) => {
        const result = await page.evaluate(async item => {
            const w = window as any, other = await import('/static/js/lq/upload.js?shared-lifecycle'); let observers = 0, maximum = 0;
            const NativeObserver = window.MutationObserver;
            window.MutationObserver = class extends NativeObserver { tracked = false; observe(...args: Parameters<MutationObserver['observe']>) { if (!this.tracked) { this.tracked = true; observers++; maximum = Math.max(maximum, observers); } super.observe(...args); } disconnect() { if (this.tracked) { this.tracked = false; observers--; } super.disconnect(); } };
            const roots = [], bindings = [];
            for (let i = 0; i < 3; i++) { const root = w.upload.createUpload('upload', { ...item.props, id: `upload-${i}` }); document.querySelector('#mount')!.append(root); roots.push(root); bindings.push((i % 2 ? other : w.upload).bindUpload(root, { snapshot: item.props.snapshot })); }
            const active = observers; roots[0].remove(); await Promise.resolve(); const firstRemoved = !bindings[0].update({ generation: 1, items: [] }), survivors = observers;
            const secondAlive = bindings[1].update({ generation: 1, items: [] }); roots[1].remove(); await Promise.resolve(); bindings[2].destroy(); const empty = observers;
            const fresh = other.bindUpload(roots[2], { snapshot: item.props.snapshot }); const reopened = observers; fresh.destroy(); roots[2].remove(); await Promise.resolve(); window.MutationObserver = NativeObserver;
            return { maximum, active, firstRemoved, survivors, secondAlive, empty, reopened, final: observers };
        }, fixture.cases[0]); expect(result).toEqual({ maximum: 1, active: 1, firstRemoved: true, survivors: 1, secondAlive: true, empty: 0, reopened: 1, final: 0 });
    });
    test('coarse touch input/actions are 44px and forced-colors/reduced-motion remain usable', async ({ browser }) => {
        const context = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, forcedColors: 'active', reducedMotion: 'reduce' });
        try { const page = await context.newPage(); await mount(page); await create(page); await bind(page); expect((await page.locator('input').boundingBox())!.height).toBeGreaterThanOrEqual(44); const button = page.getByRole('group', { name: '网络实验.png' }).getByRole('button', { name: '移除', exact: true }); expect((await button.boundingBox())!.height).toBeGreaterThanOrEqual(44); await button.tap(); expect(await page.evaluate(() => (window as any).actions)).toEqual(['remove']); await expect(page.locator('.lq-upload__policy')).toBeVisible(); } finally { await context.close(); }
    });
    for (const palette of ['indigo', 'sky', 'mint', 'violet', 'rose', 'teal']) for (const appearance of ['light', 'dark']) test(`${palette}/${appearance}: all states, long file names, mobile layout and axe`, async ({ page }) => {
        await page.setViewportSize({ width: 390, height: 844 }); await page.evaluate(({ palette, appearance }) => { document.documentElement.dataset.uiPalette = palette; document.documentElement.dataset.appearance = appearance; }, { palette, appearance }); await allStates(page);
        expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(390); await expect(page.locator('.lq-upload__policy')).toBeVisible();
        expect((await new AxeBuilder({ page }).analyze()).violations.filter(v => ['serious', 'critical'].includes(v.impact || ''))).toEqual([]);
        if (palette === 'teal') { fs.mkdirSync('.codex-temp/lq-audit/s2/upload', { recursive: true }); await page.screenshot({ path: `.codex-temp/lq-audit/s2/upload/${appearance}-390.png`, fullPage: true }); }
    });
});
