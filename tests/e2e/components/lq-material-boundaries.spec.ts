import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';

async function mount(page: Page) {
  await page.setContent(`<!doctype html><html lang="zh-CN" data-appearance="light" data-lq-glass="tinted" data-lq-tier="A"><head></head><body>
    <main style="padding:24px;display:grid;gap:24px">
      <header id="chrome" data-lq-material="chrome">导航</header>
      <section id="content" data-lq-material="content">内容
        <section id="nested" data-lq-material="content">内层
          <button id="disabled" data-lq-material="control" data-lq-interactive disabled>不可用</button>
        </section>
      </section>
      <section id="menu" class="lq-menu lq-glass lq-glass--thick">浮动菜单</section>
      <section id="off" data-lq-glass="off"><section id="off-content" class="lq-surface">关闭透明</section></section>
    </main></body></html>`);
  await page.addStyleTag({ content: fs.readFileSync('static/css/tailwind-app.css', 'utf8') });
}
const style = (page: Page, selector: string) => page.locator(selector).evaluate(el => {
  const s = getComputedStyle(el);
  return { blur: s.backdropFilter, image: s.backgroundImage, fill: s.backgroundColor, transform: s.transform };
});

test('LQ floating surfaces sample content; panels never duplicate the viewport wallpaper', async ({ page }) => {
  await mount(page);
  expect((await style(page, '#chrome')).blur).toContain('blur(16px)');
  expect((await style(page, '#menu')).blur).toContain('blur(24px)');
  expect((await style(page, '#menu')).image).not.toContain('url(');
  expect((await style(page, '#content')).image).not.toContain('url(');
  expect((await style(page, '#content')).blur).toBe('none');
  expect((await style(page, '#nested')).blur).toBe('none');
  expect((await style(page, '#off-content')).fill).toBe('rgb(255, 255, 255)');
  await page.locator('#disabled').hover({ force: true });
  expect((await style(page, '#disabled')).transform).toBe('none');
});

test('LQ touch shares the viewport frost without a blur layer for every child', async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  try {
    const page = await context.newPage();
    await mount(page);
    expect((await style(page, '#content')).blur).toBe('none');
    expect((await style(page, '#content')).image).not.toContain('url(');
    expect((await style(page, '#nested')).blur).toBe('none');
    expect((await style(page, '#disabled')).blur).toBe('none');
    await page.locator('html').evaluate(el => el.setAttribute('data-lq-glass', 'off'));
    for (const id of ['#content', '#nested', '#chrome', '#menu']) {
      expect((await style(page, id)).blur).toBe('none');
      expect((await style(page, id)).fill).toBe('rgb(255, 255, 255)');
    }
  } finally { await context.close(); }
});

test('LQ reduced motion and contrast preserve usable opaque controls', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce', contrast: 'more' });
  await mount(page);
  for (const id of ['#content', '#chrome', '#menu']) {
    expect((await style(page, id)).blur).toBe('none');
    expect((await style(page, id)).image).toBe('none');
  }
  await page.emulateMedia({ forcedColors: 'active' });
  expect((await style(page, '#content')).blur).toBe('none');
});
