import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const manifest = JSON.parse(fs.readFileSync('static/assets/manifest.json', 'utf8'));
const asset = (name: string) => `/static/${manifest.entries[name]}`;

async function mount(page: Page, dark = false) {
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin !== 'https://search-stack.test') return route.abort();
    if (url.pathname.startsWith('/static/')) {
      const file = path.resolve(`.${url.pathname}`);
      if (!file.startsWith(path.resolve('static') + path.sep) || !fs.existsSync(file)) return route.abort();
      return route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body: fs.readFileSync(file) });
    }
    return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh-CN" data-appearance="${dark ? 'dark' : 'light'}" data-lq-motion="off"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><link rel="stylesheet" href="${asset('css/tailwind-app.css')}"></head><body>
      <main class="lq-surface" style="margin:24px;padding:24px;max-width:520px">
        <form id="search-form"><div id="stack" style="position:relative">
          <label class="lq-search" for="query"><input id="query" data-lq-component="input" class="lq-input" type="search" name="query" aria-label="搜索材料" value="合成课程资料" autocomplete="off">
            <button id="clear" type="button" data-lq-component="button" class="lq-btn lq-btn--sm lq-btn--ghost lq-search__end">清空</button>
          </label>
          <div id="later-card" class="lq-surface" hidden style="position:absolute;inset-block:0;inset-inline-end:0;width:90px">后层卡片</div>
        </div></form>
        <button id="open-dialog" type="button" class="lq-btn lq-btn--glass">打开窗口</button>
      </main><script type="module">
        import { createDialog, openDialog } from '${asset('js/lq/dialogs.js')}';
        const input=document.querySelector('#query'); const clear=document.querySelector('#clear');
        window.events={inputs:0,submits:0};
        input.addEventListener('input',()=>window.events.inputs++);
        clear.addEventListener('click',()=>{input.value='';input.dispatchEvent(new Event('input',{bubbles:true}));input.focus();});
        document.querySelector('form').addEventListener('submit',e=>{e.preventDefault();window.events.submits++;});
        document.querySelector('#open-dialog').onclick=()=>{const dialog=createDialog({title:'搜索上方的窗口',body:'窗口内容',closeLabel:'关闭'});document.body.append(dialog);openDialog(dialog,{trigger:document.querySelector('#open-dialog')});};
        document.body.dataset.ready='true';
      </script></body></html>` });
  });
  await page.goto('https://search-stack.test');
  await expect(page.locator('body')).toHaveAttribute('data-ready', 'true');
}

for (const dark of [false, true]) {
  test(`LQ search tail keeps native input and local stacking ${dark ? 'dark' : 'light'}`, async ({ page }) => {
    await mount(page, dark);
    const input = page.locator('#query'), clear = page.locator('#clear');
    await input.focus();
    await page.keyboard.press('Tab');
    await expect(clear).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(input).toHaveValue('');
    await expect(input).toBeFocused();
    expect(await page.evaluate(() => (window as any).events)).toEqual({ inputs: 1, submits: 0 });
    await input.fill('中文输入仍可编辑');
    await input.press('Enter');
    expect(await page.evaluate(() => (window as any).events.submits)).toBe(1);
    await page.locator('#later-card').evaluate(node => { (node as HTMLElement).hidden = false; });
    const box = (await clear.boundingBox())!;
    expect(await page.evaluate(({ x, y }) => document.elementFromPoint(x, y)?.id,
      { x: box.x + box.width / 2, y: box.y + box.height / 2 })).toBe('later-card');
    await page.locator('#later-card').evaluate(node => { (node as HTMLElement).hidden = true; });
    await page.locator('#open-dialog').click();
    await expect(page.getByRole('dialog', { name: '搜索上方的窗口' })).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.locator('#open-dialog')).toBeFocused();
    await clear.evaluate(node => { (node as HTMLButtonElement).disabled = true; });
    await expect(clear).toBeDisabled();
    await clear.evaluate(node => { (node as HTMLButtonElement).disabled = false; node.setAttribute('hidden', ''); });
    await expect(clear).toBeHidden();
    await expect(input).toHaveValue('中文输入仍可编辑');
    const layers = await page.locator('.lq-search').evaluate(node => {
      const css = getComputedStyle(node); return { isolation: css.isolation, backdrop: css.backdropFilter };
    });
    expect(layers).toEqual({ isolation: 'isolate', backdrop: 'none' });
  });
}

test('LQ search tail remains a touch target at 360px and under forced colors', async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 360, height: 780 }, isMobile: true, hasTouch: true });
  const page = await context.newPage();
  try {
    await mount(page, true);
    const clear = page.locator('#clear');
    const box = (await clear.boundingBox())!;
    expect(box.width).toBeGreaterThanOrEqual(44);
    expect(box.height).toBeGreaterThanOrEqual(44);
    await clear.tap();
    await expect(page.locator('#query')).toHaveValue('');
    await page.emulateMedia({ forcedColors: 'active', reducedMotion: 'reduce' });
    await page.locator('#query').fill('高对比搜索');
    await clear.tap();
    await expect(page.locator('#query')).toHaveValue('');
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: '.codex-temp/lq-seven/search-360-forced-colors.png' });
  } finally { await context.close(); }
});
