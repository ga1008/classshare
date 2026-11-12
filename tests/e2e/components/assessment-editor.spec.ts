import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const evidence = '.codex-temp/evidence/assessment-editor-20261112';
fs.mkdirSync(evidence, { recursive: true });

async function mount(page: Page, appearance = 'light') {
    const manifest = JSON.parse(fs.readFileSync('static/assets/manifest.json', 'utf8'));
    const asset = (key: string) => `/static/${manifest.entries[key] || key}`;
    await page.route('**/*', route => {
        const url = new URL(route.request().url());
        if (url.origin !== 'https://assessment-editor.test') return route.abort();
        if (url.pathname.startsWith('/static/')) {
            const file = path.resolve(`.${url.pathname}`);
            if (file.startsWith(path.resolve('static') + path.sep) && fs.existsSync(file)) return route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body: fs.readFileSync(file) });
        }
        if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh-CN" data-theme="lanshare" data-ui-palette="teal" data-appearance="${appearance}" data-lq-glass="tinted"><head><meta name="viewport" content="width=device-width,initial-scale=1"><meta charset="utf-8"><title>作答编辑器</title><link rel="stylesheet" href="${asset('css/tailwind-app.css')}"><link rel="stylesheet" href="${asset('css/assessment-editor.css')}"></head><body><main style="max-width:900px;margin:auto;padding:16px"><h1>程序题</h1><form id="answer-form"><label for="answer">请解释并编写程序</label><textarea id="answer" name="answer" rows="5" data-lq-component="textarea" class="lq-textarea" placeholder="输入答案"></textarea><button type="button" id="after">下一题</button></form><div id="rendered" class="md-content"></div></main><script src="${asset('js/marked.min.js')}"></script><script src="${asset('js/markdown_runtime.js')}"></script><script type="module">import * as api from '${asset('js/assessment_editor.js')}';const input=document.getElementById('answer');window.api=api;window.original=input;window.ed=api.mountAssessmentEditor(input,{code:true,label:'程序题答案'});window.inputEvents=0;input.addEventListener('input',()=>window.inputEvents++);document.body.dataset.ready='true';</script></body></html>` });
        return route.abort();
    });
    await page.goto('https://assessment-editor.test/');
    await expect(page.locator('body')).toHaveAttribute('data-ready', 'true');
}

test.describe('Assessment native Markdown editor', () => {
    test('keeps native form identity, selection splitting, nested lists and undo', async ({ page }) => {
        await mount(page);
        const input = page.locator('#answer');
        await input.fill('3. 前半段后半段');
        await input.evaluate((node: HTMLTextAreaElement) => node.setSelectionRange(6, 6));
        await input.press('Enter');
        await expect(input).toHaveValue('3. 前半段\n4. 后半段');
        await input.press('Tab');
        await expect(input).toHaveValue('3. 前半段\n    4. 后半段');
        await input.press('Shift+Tab');
        await expect(input).toHaveValue('3. 前半段\n4. 后半段');
        await input.fill('需要强调');
        await input.selectText();
        await page.getByRole('button', { name: '加粗', exact: true }).click();
        await expect(input).toHaveValue('**需要强调**');
        await input.press('Control+z');
        await expect(input).toHaveValue('需要强调');
        expect(await page.evaluate(() => ({ same: (window as any).original === document.getElementById('answer'), answer: new FormData(document.getElementById('answer-form') as HTMLFormElement).get('answer') }))).toEqual({ same: true, answer: '需要强调' });
    });

    test('IME and plain Tab preserve native input, while Enter exits an empty list', async ({ page }) => {
        await mount(page);
        const input = page.locator('#answer');
        await input.fill('1. 中文');
        await input.dispatchEvent('compositionstart');
        await input.dispatchEvent('keydown', { key: 'Enter', code: 'Enter', keyCode: 229, isComposing: true });
        await expect(input).toHaveValue('1. 中文');
        await input.dispatchEvent('compositionend');
        await input.fill('1. '); await input.press('End'); await input.press('Enter');
        await expect(input).toHaveValue('');
        await input.press('Tab'); await expect(page.locator('#after')).toBeFocused();
    });

    test('code assists bracket closing and indent, inserts once after inert releases, and undoes natively', async ({ page }) => {
        await mount(page);
        const source = page.locator('#answer');
        await source.fill('代码如下：'); await source.press('End');
        await page.getByRole('button', { name: '插入代码块', exact: true }).click();
        const input = page.getByRole('textbox', { name: '代码内容', exact: true });
        await expect(input).toBeFocused();
        await expect(input).toHaveAttribute('rows', '2');
        await expect.poll(() => input.evaluate(node => {
            const css = getComputedStyle(node);
            const border = parseFloat(css.borderTopWidth) + parseFloat(css.borderBottomWidth);
            // Single-line/empty text is centered in a two-line area by padding;
            // the baseline field padding is 10px at each edge.
            return (node.getBoundingClientRect().height - border - 20) / parseFloat(css.lineHeight);
        })).toBeCloseTo(2, 1);
        await input.fill('print(1)');
        await expect.poll(() => input.evaluate(node => {
            const pre = node.parentElement!.querySelector('pre')!;
            return Math.abs(parseFloat(getComputedStyle(node).paddingTop) - parseFloat(getComputedStyle(pre).paddingTop));
        })).toBeLessThan(0.1);
        await input.fill('def run():'); await input.press('End'); await input.press('Enter');
        await expect(input).toHaveValue('def run():\n    ');
        await input.pressSequentially('print'); await input.press('(');
        await expect(input).toHaveValue('def run():\n    print()');
        await input.pressSequentially('1'); await input.press(')');
        await expect(input).toHaveValue('def run():\n    print(1)');
        await expect(page.locator('.assessment-code-dialog__status')).toContainText('Python');
        await expect(page.locator('.assessment-code-keyword').first()).toBeVisible();
        await page.getByRole('button', { name: '插入代码块', exact: true }).last().click();
        await expect(page.getByRole('dialog')).toHaveCount(0);
        await expect(source).toHaveValue('代码如下：\n\n```python\ndef run():\n    print(1)\n```\n');
        await expect(source).toBeFocused();
        await expect(page.locator('[inert]')).toHaveCount(0);
        await expect(page.locator('[data-code-block-index]')).toHaveCount(1);
        await source.press('Control+z');
        await expect(source).toHaveValue('代码如下：');
    });

    test('code cards reopen, cancel retains the original, and dirty Escape cannot silently discard', async ({ page }) => {
        await mount(page);
        const source = page.locator('#answer');
        await source.fill('```python\nprint(1)\n```');
        await page.locator('[data-code-block-index]').click();
        const code = page.getByRole('textbox', { name: '代码内容', exact: true });
        await code.fill('print(2)'); await code.press('Escape');
        await expect(page.getByRole('dialog')).toBeVisible();
        await expect(page.getByRole('alert')).toContainText('代码尚未插入');
        await page.getByRole('button', { name: '取消', exact: true }).click();
        await expect(page.getByRole('dialog')).toHaveCount(0);
        await expect(source).toHaveValue('```python\nprint(1)\n```');
        await page.locator('[data-code-block-index]').click();
        await code.fill('print(3)');
        await page.getByRole('button', { name: '更新代码块', exact: true }).click();
        await expect(source).toHaveValue('```python\nprint(3)\n```\n');
        await page.getByRole('button', { name: '预览', exact: true }).click();
        await expect(source).toBeHidden();
        await expect(page.locator('.assessment-editor__preview .assessment-editor__code-card')).toContainText('print(3)');
        await page.locator('.assessment-editor__preview .assessment-editor__code-card').click();
        await expect(code).toHaveValue('print(3)');
        await page.getByRole('button', { name: '取消', exact: true }).click();
        await expect(page.getByRole('button', { name: '继续编辑', exact: true })).toBeFocused();
    });

    test('safe Markdown rendering supports alignment without interpreting code or executable HTML', async ({ page }) => {
        await mount(page);
        await page.locator('#answer').fill(':::align center\n**居中标题**\n:::\n\n```html\n<script>window.pwned=1</script>\n```\n<img src=x onerror="window.pwned=2">');
        await page.getByRole('button', { name: '预览', exact: true }).click();
        await expect(page.locator('.assessment-align-center strong')).toHaveText('居中标题');
        expect(await page.locator('.assessment-align-center').evaluate(node => getComputedStyle(node).textAlign)).toBe('center');
        expect(await page.evaluate(() => ({ pwned: (window as any).pwned || null, bad: document.querySelectorAll('.assessment-editor__preview script,.assessment-editor__preview [onerror]').length }))).toEqual({ pwned: null, bad: 0 });
        await expect(page.locator('.assessment-editor__preview .assessment-editor__code-card')).toContainText('<script>window.pwned=1</script>');
    });

    test('unconfirmed code protects browser unload only while dirty and releases on confirm, cancel and destroy', async ({ page }) => {
        await mount(page);
        const unloadPrevented = () => page.evaluate(() => {
            const event = new Event('beforeunload', { cancelable: true });
            window.dispatchEvent(event);
            return event.defaultPrevented;
        });
        const input = page.getByRole('textbox', { name: '代码内容', exact: true });
        await page.getByRole('button', { name: '插入代码块', exact: true }).click();
        expect(await unloadPrevented()).toBe(false);
        await input.fill('print("未确认的代码")');
        expect(await unloadPrevented()).toBe(true);
        await input.fill('');
        expect(await unloadPrevented()).toBe(false);
        await input.fill('print(1)');
        await page.getByRole('button', { name: '插入代码块', exact: true }).last().click();
        await expect(page.getByRole('dialog')).toHaveCount(0);
        await expect(page.locator('#answer')).toHaveValue(/print\(1\)/);
        expect(await unloadPrevented()).toBe(false);
        await page.locator('[data-code-block-index]').click();
        await input.fill('print(2)');
        expect(await unloadPrevented()).toBe(true);
        await page.getByRole('button', { name: '取消', exact: true }).click();
        await expect(page.getByRole('dialog')).toHaveCount(0);
        expect(await unloadPrevented()).toBe(false);
        await page.locator('[data-code-block-index]').click();
        await page.locator('select[aria-label="代码语言"]').selectOption('javascript', { force: true });
        expect(await unloadPrevented()).toBe(true);
        await page.evaluate(() => (window as any).ed.destroy());
        await expect(page.getByRole('dialog')).toHaveCount(0);
        expect(await unloadPrevented()).toBe(false);
    });

    test('alignment toolbar changes the existing block rather than nesting controls', async ({ page }) => {
        await mount(page);
        const source = page.locator('#answer');
        await source.fill('标题'); await source.selectText();
        await page.getByRole('button', { name: '右对齐', exact: true }).click();
        await expect(source).toHaveValue(':::align right\n标题\n:::');
        await page.getByRole('button', { name: '居中', exact: true }).click();
        await expect(source).toHaveValue(':::align center\n标题\n:::');
        await page.getByRole('button', { name: '预览', exact: true }).click();
        await expect(page.locator('.assessment-align-center')).toHaveText('标题');
    });

    for (const appearance of ['light', 'dark']) test(`grows past 14 lines, caps with scrolling, and fits mobile in ${appearance}`, async ({ page }) => {
        await page.setViewportSize({ width: 390, height: 844 }); await mount(page, appearance);
        const input = page.locator('#answer');
        const dimensions = () => input.evaluate(node => { const css = getComputedStyle(node); return { height: node.getBoundingClientRect().height, line: parseFloat(css.lineHeight), padding: parseFloat(css.paddingTop) + parseFloat(css.paddingBottom), overflow: css.overflowY }; });
        const empty = await dimensions();
        await input.fill('第 1 行');
        await expect.poll(async () => (await dimensions()).height).toBeCloseTo(empty.height, 0);
        await input.fill(Array.from({ length: 15 }, (_, i) => `第 ${i + 1} 行`).join('\n'));
        await expect.poll(async () => (await dimensions()).height).toBeGreaterThan(empty.height * 4);
        const fifteen = await dimensions();
        expect((fifteen.height - fifteen.padding) / fifteen.line).toBeGreaterThanOrEqual(15);
        await input.fill(Array.from({ length: 30 }, (_, i) => `第 ${i + 1} 行`).join('\n'));
        await expect.poll(async () => (await dimensions()).overflow).toBe('auto');
        const capped = await dimensions();
        expect((capped.height - capped.padding) / capped.line).toBeGreaterThanOrEqual(16);
        expect((capped.height - capped.padding) / capped.line).toBeLessThan(17);
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        await page.screenshot({ path: `${evidence}/editor-${appearance}-390.png`, fullPage: true });
        await page.getByRole('button', { name: '插入代码块', exact: true }).click();
        await page.getByRole('textbox', { name: '代码内容', exact: true }).fill('def show(value):\n    print(value)');
        await page.screenshot({ path: `${evidence}/code-${appearance}-390.png`, fullPage: true });
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    });

    test('readonly, refresh, repeated mounting and teardown preserve caller state without duplicates', async ({ page }) => {
        await mount(page);
        await page.locator('#answer').fill('保留草稿');
        await page.evaluate(() => { const w = window as any; const input = document.getElementById('answer') as HTMLTextAreaElement; if (w.ed !== w.api.mountAssessmentEditor(input, { code: true })) throw Error('duplicate mount'); input.readOnly = true; w.ed.refresh(); });
        await expect(page.getByRole('button', { name: '加粗', exact: true })).toBeDisabled();
        await page.evaluate(() => { const w = window as any; for (let i = 0; i < 30; i++) { w.ed.destroy(); w.ed = w.api.mountAssessmentEditor(document.getElementById('answer'), { code: true }); } w.ed.destroy(); });
        await expect(page.locator('.assessment-editor')).toHaveCount(0);
        await expect(page.locator('#answer')).toHaveValue('保留草稿');
        await expect(page.locator('#answer')).toHaveAttribute('rows', '5');
        expect(await page.evaluate(() => (window as any).original === document.getElementById('answer'))).toBe(true);
    });
});
