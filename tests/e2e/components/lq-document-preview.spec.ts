import { test, expect, type Page } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

const fixture = JSON.parse(execFileSync(process.env.LQ_TEST_PYTHON || 'venv/Scripts/python.exe',
  ['tests/e2e/scripts/render_lq_document_preview.py'], { encoding: 'utf8' }));
const origin = 'https://document-preview-lq.test';
const assetRoot = path.resolve(process.env.LQ_DOCUMENT_ASSET_ROOT || 'static');
if (assetRoot !== path.resolve('static') && !assetRoot.startsWith(path.resolve('static/assets') + path.sep)) throw new Error('Only local release assets are allowed.');

async function mount(page: Page, options: { disabled?: boolean; failures?: boolean; embedded?: boolean; held?: boolean } = {}) {
  expect(fixture.isolated).toBe(true);
  const counts = new Map<string, number>();
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.origin !== origin) return route.abort();
    if (url.pathname.startsWith('/static/')) {
      const file = path.resolve(assetRoot, '.' + url.pathname.slice('/static'.length));
      if (file.startsWith(assetRoot + path.sep) && fs.existsSync(file)) return route.fulfill({
        body: fs.readFileSync(file), contentType: file.endsWith('.css') ? 'text/css' : file.endsWith('.js') ? 'text/javascript' : 'application/octet-stream',
      });
      return route.abort();
    }
    if (url.pathname.includes('/pages/')) {
      const key = url.pathname + ':' + url.searchParams.get('size');
      const count = (counts.get(key) || 0) + 1;
      counts.set(key, count);
      expect(url.searchParams.get('token')).toMatch(/^dr1\./);
      if (options.held) return;
      if (options.failures && url.pathname.endsWith('/1') && count === 1) return route.abort('failed');
      const number = url.pathname.split('/').at(-1);
      return route.fulfill({ contentType: 'image/svg+xml', headers: { 'Cache-Control': 'max-age=3600' }, body:
        `<svg xmlns="http://www.w3.org/2000/svg" width="595" height="842"><rect width="595" height="842" fill="white"/><text x="55" y="80" font-size="22" fill="#172033">LanShare document page ${number}</text><path d="M55 105H540" stroke="#5b6882"/>${Array.from({ length: 18 }, (_, n) => `<path d="M55 ${150 + n * 32}H${n % 3 ? 520 : 410}" stroke="#d2d7df" stroke-width="6"/>`).join('')}</svg>` });
    }
    if (url.pathname === '/host') return route.fulfill({ contentType: 'text/html', body:
      `<!doctype html><html data-theme="lanshare" data-appearance-preference="dark" data-ui-palette="rose"><head>${fixture.head}</head><body>
      <iframe id="app" data-lq-theme-bridge="app" src="/preview?app"></iframe>
      <iframe id="content" src="/preview?content"></iframe>
      <iframe id="sandbox" data-lq-theme-bridge="app" sandbox="allow-scripts allow-same-origin" src="/preview?sandbox"></iframe></body></html>` });
    return route.fulfill({ contentType: 'text/html', body: options.disabled ? fixture.disabled : fixture.preview });
  });
  await page.goto(origin + (options.embedded ? '/host' : '/preview'), { waitUntil: 'domcontentloaded' });
  if (!options.embedded) await expect.poll(() => page.evaluate(() => !!(window as any).LQ?.load)).toBe(true);
  return { counts, errors };
}

for (const width of [390, 1440]) for (const appearance of ['light', 'dark'] as const) {
  test(`document controls preserve page navigation, zoom and paper at ${width} ${appearance}`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 980 });
    await page.emulateMedia({ colorScheme: appearance });
    const { counts, errors } = await mount(page);
    const card = page.locator('.doc-preview-card.is-active');
    await expect(card).toHaveAttribute('data-page-status', 'ready');
    await expect(card.locator('[data-page-placeholder]')).toHaveCSS('opacity', '0');
    await expect(card.locator('img')).toHaveCSS('opacity', '1');
    await expect(page.locator('html')).toHaveAttribute('data-appearance', appearance);
    await expect(card).toHaveAttribute('data-lq-component', 'content-slot');
    await expect(card.locator('img')).toHaveCSS('background-color', 'rgb(255, 255, 255)');
    await expect(card).toHaveCSS('backdrop-filter', 'none');
    await expect(page.locator('[data-deck-next]')).toHaveCSS('backdrop-filter', 'none');
    // These fixed controls overlay white document pixels even in a dark UI.
    const contrasts = await page.evaluate(() => {
      function channels(color: string) { return color.match(/[\d.]+/g)!.map(Number); }
      function luminance(rgb: number[]) { return rgb.slice(0, 3).reduce((sum, value, index) => {
        const v = value / 255;
        return sum + (v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4) * [.2126, .7152, .0722][index];
      }, 0); }
      return ['[data-deck-next]', '[data-deck-count]', '.doc-preview-card.is-active [data-page-placeholder]'].map(selector => {
        const style = getComputedStyle(document.querySelector(selector)!);
        const bg = channels(style.backgroundColor), fg = channels(style.color), alpha = bg[3] ?? 1;
        const paper = bg.slice(0, 3).map(channel => channel * alpha + 255 * (1 - alpha));
        const a = luminance(paper), b = luminance(fg);
        return { selector, contrast: (Math.max(a, b) + .05) / (Math.min(a, b) + .05) };
      });
    });
    for (const item of contrasts) expect(item.contrast, item.selector).toBeGreaterThanOrEqual(4.5);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    expect([...counts.keys()].some(key => key.endsWith('/5:medium'))).toBe(false); // Offscreen page remains lazy.
    await page.screenshot({ path: testInfo.outputPath(`deck-${width}-${appearance}.png`) });
    await page.locator('[data-deck-next]').click();
    await expect(page.locator('[data-deck-count]')).toHaveText('2 / 8');
    await page.keyboard.press('ArrowRight');
    await expect(page.locator('[data-deck-count]')).toHaveText('3 / 8');
    await page.locator('.doc-preview-stage').dispatchEvent('wheel', { deltaY: 120 });
    await expect(page.locator('[data-deck-count]')).toHaveText('4 / 8');
    await expect(card).toHaveAttribute('data-page-status', 'ready');
    await card.evaluate(async element => { await Promise.all(element.getAnimations().map(animation => animation.finished)); });
    await card.click();
    await expect(page.locator('[data-lightbox]')).toBeVisible();
    await expect(page.locator('[data-loading]')).toBeHidden();
    await expect(page.locator('[data-lightbox-count]')).toHaveText('4 / 8');
    await page.locator('[data-zoom-in]').click();
    await expect(page.locator('[data-zoom-reset]')).toHaveText('116%');
    const image = page.locator('[data-large-image]');
    const expectedPan = await image.evaluate((element: HTMLImageElement) => {
      const frame = document.querySelector('.doc-preview-lightbox__body')!.getBoundingClientRect();
      return { x: Math.min(25, Math.max(0, (element.offsetWidth * 1.16 - frame.width) / 2 + 80)),
        y: Math.min(20, Math.max(0, (element.offsetHeight * 1.16 - frame.height) / 2 + 80)) };
    });
    const box = await image.boundingBox();
    await page.mouse.move(box!.x + box!.width / 2, box!.y + box!.height / 2);
    await page.mouse.down();
    await page.mouse.move(box!.x + box!.width / 2 + 25, box!.y + box!.height / 2 + 20);
    await page.mouse.up();
    await expect(image).toHaveAttribute('style', `transform: translate3d(${expectedPan.x}px, ${expectedPan.y}px, 0px) scale(1.16);`);
    await page.locator('[data-zoom-reset]').click();
    await expect(page.locator('[data-zoom-reset]')).toHaveText('100%');
    await image.dispatchEvent('wheel', { deltaY: -100, clientX: 160, clientY: 250 });
    await expect(page.locator('[data-zoom-reset]')).toHaveText('114%');
    await page.locator('[data-next]').click();
    await expect(page.locator('[data-lightbox-count]')).toHaveText('5 / 8');
    await expect(page.locator('[data-zoom-reset]')).toHaveText('100%');
    await expect(page.locator('[data-loading]')).toBeHidden();
    await page.screenshot({ path: testInfo.outputPath(`large-${width}-${appearance}.png`) });
    await page.locator('[data-close]').click();
    await expect(page.locator('[data-lightbox]')).toBeHidden();
    await expect(card).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(page.locator('[data-lightbox]')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.locator('[data-lightbox]')).toBeHidden();
    await expect(page.locator('[data-deck-count]')).toHaveText('5 / 8');
    expect(errors).toEqual([]);
  });
}

test('failed medium and large images retry without exposing a disabled download', async ({ page }) => {
  const { counts, errors } = await mount(page, { failures: true, disabled: true });
  const card = page.locator('.doc-preview-card.is-active');
  await expect(card).toHaveAttribute('data-page-status', 'error');
  await expect(card.locator('[data-page-placeholder]')).toContainText('点击重试第 1 页');
  await expect(page.locator('.doc-preview-download')).toBeDisabled();
  await expect(page.locator('a[href*="/download"]')).toHaveCount(0);
  await card.click();
  await expect(card).toHaveAttribute('data-page-status', 'ready');
  await expect(page.locator('[data-lightbox]')).toBeHidden();
  await card.click();
  await expect(page.locator('[data-loading]')).toHaveClass(/is-error/);
  await expect(page.locator('[data-large-image]')).toHaveAttribute('src', /size=medium/);
  await page.locator('[data-retry-large]').click();
  await expect(page.locator('[data-loading]')).toBeHidden();
  await expect(page.locator('[data-large-image]')).toHaveAttribute('src', /size=large/);
  expect([...counts.entries()].filter(([key]) => key.includes('/1:')).map(([, count]) => count)).toEqual([2, 2]);
  for (let step = 0; step < 12 && !await page.locator('[data-zoom-in]').isDisabled(); step++) await page.locator('[data-zoom-in]').click();
  await expect(page.locator('[data-zoom-in]')).toBeDisabled();
  await expect(page.locator('[data-zoom-reset]')).toHaveText('400%');
  expect(errors).toEqual([]);
});

test('only explicitly trusted app frames receive account presentation preferences', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'light' });
  await mount(page, { embedded: true });
  const app = page.frameLocator('#app');
  await expect(app.locator('html')).toHaveAttribute('data-appearance', 'dark');
  await expect(app.locator('html')).toHaveAttribute('data-ui-palette', 'rose');
  for (const id of ['content', 'sandbox']) await expect(page.frameLocator('#' + id).locator('html')).toHaveAttribute('data-appearance', 'light');
  await page.evaluate(async () => {
    const theme = await import('/static/js/lq/theme.js');
    theme.initTheme().refresh({ palette_key: 'mint', appearance: 'light', glass: 'off' });
  });
  await expect(app.locator('html')).toHaveAttribute('data-ui-palette', 'mint');
  await expect(app.locator('html')).toHaveAttribute('data-lq-glass', 'off');
  await expect(app.locator('.doc-preview-card.is-active img')).toHaveCSS('background-color', 'rgb(255, 255, 255)');
});

test('loading uses the shared spinner and respects reduced motion and glass off', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await mount(page, { held: true });
  const spinner = page.locator('.doc-preview-card.is-active .lq-spinner');
  await expect(spinner).toBeVisible();
  await expect(spinner).toHaveCSS('animation-name', 'none');
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await page.evaluate(() => { document.documentElement.dataset.lqMotion = 'off'; });
  await expect(spinner).toHaveCSS('animation-play-state', 'paused');
  await page.evaluate(() => {
    const theme = (window as any).LanShareTheme;
    theme.applyTheme({ preferences: { appearance: 'dark', glass: 'off' }, capabilities: theme.detectCapabilities(window) });
  });
  await expect(page.locator('.doc-preview-topbar')).toHaveCSS('backdrop-filter', 'none');
  await expect(spinner).toHaveCSS('animation-play-state', 'paused');
});
