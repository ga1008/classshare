import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';

const python = process.env.LQ_TEST_PYTHON || (process.platform === 'win32' ? 'venv/Scripts/python.exe' : 'venv/bin/python');
const markup = execFileSync(python, ['-c', `
import sys
from jinja2 import Environment, FileSystemLoader, StrictUndefined
sys.stdout.reconfigure(encoding='utf-8')
env = Environment(loader=FileSystemLoader('templates'), autoescape=True, undefined=StrictUndefined)
print(env.get_template('macros/user_ui_preferences.html').module.user_palette_select({'enabled': True, 'presets': [{'key': 'indigo', 'name': '靛蓝'}, {'key': 'teal', 'name': '青绿'}], 'palette_key': 'indigo'}))
`], { encoding: 'utf8' });
let css: string;
function source(file: string): string {
  return fs.readFileSync(file, 'utf8').replace(/@import\s+["']([^"']+)["'];/g,
    (_, relative) => source(path.resolve(path.dirname(file), relative)));
}
test.beforeAll(async () => {
  css = process.env.LQ_CONTROL_BUILT_CSS === '1' ? fs.readFileSync('static/css/tailwind-app.css', 'utf8')
    : (await require('postcss')([require('tailwindcss')(require(path.resolve('tailwind.config.js')))])
      .process(source(path.resolve('static/css/ui-system.src.css')), { from: path.resolve('static/css/ui-system.src.css') })).css;
  css += '\n' + fs.readFileSync('static/css/user_ui_preferences.css', 'utf8');
});

async function mount(page: Page, appearance: string, enhanced = true) {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin !== 'http://preferences-panel.test') return route.abort();
    if (url.pathname === '/source.css') return route.fulfill({ contentType: 'text/css', body: css });
    if (url.pathname.startsWith('/static/js/')) {
      const file = path.resolve(`.${url.pathname}`);
      if (file.startsWith(path.resolve('static/js') + path.sep) && fs.existsSync(file)) return route.fulfill({ contentType: 'text/javascript', body: fs.readFileSync(file) });
    }
    if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: `<!doctype html>
      <html lang="zh-CN" data-ui-palette="indigo" data-appearance="${appearance}" data-lq-tier="A" data-lq-glass="tinted">
      <head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/source.css">
      <style>body{background:hsl(var(--ls-background));min-height:160vh;padding:100px 24px;color:hsl(var(--ls-ink))}
      header{position:fixed!important;top:16px;left:16px;right:16px;display:flex;justify-content:flex-end;padding:8px 16px;z-index:30}
      main{max-width:680px;padding:30px;border-radius:28px;background:linear-gradient(130deg,hsl(var(--ls-primary)/.18),hsl(var(--ls-primary)/.04))}
      #outside{position:fixed;bottom:24px;left:24px}</style></head>
      <body><header data-lq-material="chrome">${markup}</header><main><h1>课堂与学习</h1><p>共享外观菜单 · 柔和玻璃与清晰交互</p><button id="outside" class="lq-btn lq-btn--glass">返回课堂</button></main>
      ${enhanced ? `<script type="module">import {enhancePreferencesPanels} from '/static/js/ui_preferences_panel.js';
      window.originalSelect=document.querySelector('[data-ui-palette-select]');window.changes=0;window.originalSelect.addEventListener('change',()=>window.changes++);
      window.enhance=()=>enhancePreferencesPanels(document.body);window.dispose=window.enhance();window.ready=true;</script>` : ''}</body></html>` });
    return route.fulfill({ status: 404, body: '' });
  });
  await page.goto('http://preferences-panel.test/');
  if (enhanced) await page.waitForFunction(() => (window as any).ready);
}

for (const appearance of ['light', 'dark']) for (const width of [1280, 390]) {
  test(`appearance details uses shared portal glass ${appearance}/${width}`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: 844 }); await mount(page, appearance);
    const toggle = page.locator('[data-ui-preferences-toggle]'), panel = page.locator('[data-ui-preferences-panel]');
    await toggle.click();
    await expect(panel).toBeVisible(); await expect(toggle).toHaveAttribute('aria-expanded', 'true');
    expect(await panel.evaluate(node => node.parentElement?.id)).toBe('lq-layers');
    const material = await panel.evaluate(node => {
      const style = getComputedStyle(node); return { blur: style.backdropFilter, color: style.color, fill: style.backgroundColor };
    });
    expect(material.blur).toContain('blur('); expect(material.fill).toMatch(/^rgba\(/);
    const select = panel.locator('[data-ui-palette-select]');
    await expect(select).toBeFocused();
    const contrast = await select.evaluate(node => {
      const rgb = (value: string) => value.match(/[\d.]+/g)!.map(Number);
      const composite = (a: number[], b: number[]) => a.slice(0, 3).map((v, i) => v * (a[3] ?? 1) + b[i] * (1 - (a[3] ?? 1)));
      const style = getComputedStyle(node), panel = getComputedStyle(node.closest('[data-ui-preferences-panel]')!);
      const fill = composite(rgb(style.backgroundColor), composite(rgb(panel.backgroundColor), rgb(getComputedStyle(document.body).backgroundColor)));
      const luminance = (a: number[]) => a.slice(0, 3).map(v => v / 255).map(v => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4).reduce((s, v, i) => s + v * [.2126, .7152, .0722][i], 0);
      const a = luminance(rgb(style.color)), b = luminance(fill); return (Math.max(a, b) + .05) / (Math.min(a, b) + .05);
    });
    expect(contrast).toBeGreaterThanOrEqual(4.5);
    // Even reduced-motion's global 0.00001s transition still needs a render
    // frame before its computed top/color leaves the closed native position.
    await expect.poll(async () => { const r = (await panel.boundingBox())!; return r.y >= 12 && r.y + r.height <= 832; }).toBe(true);
    const rect = (await panel.boundingBox())!;
    expect(rect.x).toBeGreaterThanOrEqual(12); expect(rect.x + rect.width).toBeLessThanOrEqual(width - 12);
    expect(rect.y).toBeGreaterThanOrEqual(12); expect(rect.y + rect.height).toBeLessThanOrEqual(844 - 12);
    if (width === 390) { expect(rect.x).toBe(16); expect(rect.width).toBe(358); expect(rect.y).toBe(72); }
    await select.selectOption('teal'); expect(await page.evaluate(() => (window as any).changes)).toBe(1);
    const togglePair = () => toggle.evaluate(node => {
      const token = (name: string) => {
        const probe = document.createElement('i'); probe.style.color = `hsl(var(${name}))`; node.append(probe);
        const value = getComputedStyle(probe).color; probe.remove(); return value;
      };
      const ink = token('--ls-on-primary'), primary = token('--ls-primary');
      const fill = getComputedStyle(node).backgroundColor, label = getComputedStyle(node.querySelector('span')!).color;
      const luminance = (color: string) => color.match(/[\d.]+/g)!.slice(0, 3).map(Number).map(v => v / 255)
        .map(v => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4).reduce((s, v, i) => s + v * [.2126, .7152, .0722][i], 0);
      const a = luminance(fill), b = luminance(label);
      return { fill, primary, label, ink, contrast: (Math.max(a, b) + .05) / (Math.min(a, b) + .05) };
    });
    await expect.poll(async () => { const pair = await togglePair(); return pair.fill === pair.primary && pair.label === pair.ink; }).toBe(true);
    const pair = await togglePair(); expect(pair.contrast).toBeGreaterThanOrEqual(4.5);
    await info.attach('menu-material-and-text', { body: JSON.stringify({ appearance, width, material, selectContrast: contrast, toggle: pair, rect }), contentType: 'application/json' });
    await page.screenshot({ path: info.outputPath(`preferences-${appearance}-${width}.png`) });
    await page.keyboard.press('Escape');
    await expect(panel).toBeHidden(); await expect(toggle).toBeFocused();
    expect(await panel.evaluate(node => node.parentElement?.tagName)).toBe('DETAILS');
    await toggle.click(); await page.locator('#outside').click(); await expect(panel).toBeHidden();
    await toggle.click(); await page.evaluate(() => (window as any).dispose());
    await expect(panel).toBeHidden();
    expect(await page.evaluate(() => document.querySelector('[data-ui-palette-select]') === (window as any).originalSelect)).toBe(true);
    await toggle.click(); await expect(panel).toBeVisible(); // restored native details
    await toggle.click(); await page.evaluate(() => { (window as any).dispose = (window as any).enhance(); });
    await toggle.click(); expect(await panel.evaluate(node => node.parentElement?.id)).toBe('lq-layers');
    await page.setViewportSize({ width: width === 390 ? 1280 : 390, height: 844 });
    await expect.poll(async () => {
      const box = (await panel.boundingBox())!, anchor = (await toggle.boundingBox())!;
      return Math.round(width === 390 ? box.x + box.width - anchor.x - anchor.width : box.x - 16);
    }).toBe(0);
    await page.keyboard.press('Escape'); await expect(panel).toBeHidden();
  });
}

test('appearance remains a native disclosure without enhancement', async ({ page }) => {
  await mount(page, 'light', false);
  const toggle = page.locator('[data-ui-preferences-toggle]'), panel = page.locator('[data-ui-preferences-panel]');
  await expect(panel).toBeHidden(); await toggle.focus(); await page.keyboard.press('Enter'); await expect(panel).toBeVisible();
  await panel.locator('[data-ui-palette-select]').selectOption('teal');
  await toggle.focus(); await page.keyboard.press('Enter'); await expect(panel).toBeHidden();
});
