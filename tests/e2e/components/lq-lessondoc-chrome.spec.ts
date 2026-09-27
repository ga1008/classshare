import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

// Render the real editor chrome without importing an application or database.
const python = process.platform === 'win32' ? 'venv/Scripts/python.exe' : 'venv/bin/python';
const body = execFileSync(python, ['-c', String.raw`
from pathlib import Path
from jinja2 import Environment, StrictUndefined
import re,sys
source=Path('templates/lessondoc_editor.html').read_text(encoding='utf-8')
fragment=re.search(r'<main[\s\S]+?</main>',source).group()
sys.stdout.reconfigure(encoding='utf-8')
print(Environment(autoescape=True,undefined=StrictUndefined).from_string(fragment).render(editor_config=dict(returnUrl='/materials',packId=1,lessonNo=0,lessons=[])))
`], { encoding: 'utf8' });

for (const width of [390, 1440]) for (const appearance of ['light', 'dark']) {
  test(`LQ lesson editor chrome preserves document geometry ${width} ${appearance}`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: 900 });
    await page.route('https://editor.test/**', route => {
      const url = new URL(route.request().url());
      if (url.pathname.startsWith('/static/')) {
        const file = path.resolve('.' + url.pathname);
        if (!file.startsWith(path.resolve('static') + path.sep) || !fs.existsSync(file)) return route.abort();
        return route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body: fs.readFileSync(file) });
      }
      if (url.pathname !== '/') return route.fulfill({ contentType: 'text/html', body: '<!doctype html><html><head><meta charset="utf-8"></head><body style="background:white;color:black;margin:24px"><h1>课程正文</h1><p>保留文档原始排版</p></body></html>' });
      return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh-CN" data-theme="lanshare" data-appearance="${appearance}" data-lq-glass="tinted" data-lq-tier="A"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/static/css/tailwind-app.css"><link rel="stylesheet" href="/static/css/lessondoc_editor.css"></head><body class="lde-page">${body}</body></html>` });
    });
    await page.goto('https://editor.test/');
    const chrome = await page.locator('.lde-mobile-bar,.lde-page-rail,.lde-elements,.lde-props,.lde-topbar').evaluateAll(nodes => nodes.map(node => ({
      owned: Boolean(node.getAttribute('data-lq-component')), background: getComputedStyle(node).backgroundColor,
      color: getComputedStyle(node).color, blur: getComputedStyle(node).backdropFilter,
    })));
    expect(chrome.every(node => node.owned)).toBe(true);
    if (appearance === 'dark') expect(chrome.every(node => node.background !== 'rgb(255, 255, 255)' && node.background !== 'rgb(248, 250, 252)')).toBe(true);
    expect(chrome.filter(node => node.blur !== 'none')).toHaveLength(1);
    expect(await page.locator('button,input,select').evaluateAll(nodes => nodes.every(node => getComputedStyle(node).backdropFilter === 'none'))).toBe(true);
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
    await expect(page.locator('#lde-save')).toHaveClass(/lq-btn--prominent/);
    await expect(page.frameLocator('#lde-frame').getByRole('heading', { name: '课程正文' })).toBeVisible();
    await page.screenshot({ path: info.outputPath('editor.png') });
    // Reproduce the dark scene beneath translucent light chrome. On the real
    // page the old muted label measured only 1.62-2.31:1, despite passing on white.
    await page.evaluate(async () => {
      document.body.style.background = '#182025';
      const railLabel = document.createElement('span'); railLabel.className = 'lde-muted'; railLabel.textContent = '课程首页 · 长页面';
      document.querySelector('.lde-page-rail')!.append(railLabel);
      const ui = await import('/static/js/lessondoc_editor/ui.js');
      ui.reportError(Object.assign(new Error('保存失败 <img onerror=alert(1)>'), { details: { diagnostics: [{ path: 'title', message: '请保留原草稿' }] } }), document.getElementById('lde-error'));
      document.getElementById('lde-error')!.append(ui.button('重试', () => { (window as any).retried = true; }));
      const warning = document.getElementById('lde-warning')!; warning.hidden = false; warning.textContent = '离开前请确认修改已保存';
    });
    await expect(page.locator('#lde-error')).toHaveAttribute('data-lq-component', 'alert');
    await expect(page.locator('#lde-warning')).toHaveAttribute('data-lq-tone-level', 'warning');
    await expect(page.locator('#lde-error img')).toHaveCount(0);
    await expect(page.locator('#lde-error')).toContainText('请保留原草稿');
    await page.getByRole('button', { name: '重试', exact: true }).click();
    expect(await page.evaluate(() => (window as any).retried)).toBe(true);
    const contrast = async (selector: string) => page.locator(selector).evaluateAll(nodes => {
      const parse = (color: string): number[] => (color.match(/[\d.]+/g) || []).map(Number);
      const over = (fg: number[], bg: number[]) => fg.slice(0, 3).map((v, i) => v * (fg[3] ?? 1) + bg[i] * (1 - (fg[3] ?? 1)));
      const luminance = (rgb: number[]) => rgb.map(c => c / 255).map(c => c <= .04045 ? c / 12.92 : ((c + .055) / 1.055) ** 2.4).reduce((sum, c, i) => sum + c * [.2126, .7152, .0722][i], 0);
      return nodes.filter(node => node.getBoundingClientRect().width > 0).map(node => {
        const chain: Element[] = []; for (let n: Element | null = node; n; n = n.parentElement) chain.unshift(n);
        let background = [255, 255, 255];
        for (const parent of chain) {
          const style = getComputedStyle(parent); background = over(parse(style.backgroundColor), background);
          // Status/Alert use a uniform semantic tint over their shared base.
          if (parent.matches('[data-lq-status]')) { const tint = style.backgroundImage.match(/rgba?\([^)]+\)/)?.[0]; if (tint) background = over(parse(tint), background); }
        }
        const fg = luminance(parse(getComputedStyle(node).color)), bg = luminance(background);
        return { selector: node.className, ratio: (Math.max(fg, bg) + .05) / (Math.min(fg, bg) + .05) };
      });
    });
    for (const result of await contrast('.lde-eyebrow,.lde-canvas-toolbar,.lde-page-rail .lde-muted,#lde-error,#lde-warning')) expect(result.ratio, result.selector).toBeGreaterThanOrEqual(4.5);
    for (const tone of ['neutral', 'info', 'success', 'warning', 'danger']) {
      await page.locator('#lde-save-state').evaluate((node, tone) => { (node as HTMLElement).dataset.lqToneLevel = tone; }, tone);
      for (const result of await contrast('#lde-save-state')) expect(result.ratio, `save ${tone}`).toBeGreaterThanOrEqual(4.5);
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
    await page.screenshot({ path: info.outputPath('editor-scene-feedback.png') });
    await page.locator('html').evaluate(node => { node.setAttribute('data-lq-glass', 'off'); });
    const off = await page.locator('.lde-topbar').evaluate(node => {
      const reference = document.createElement('span'); reference.style.background = 'hsl(var(--ls-surface-1))'; document.body.append(reference);
      const expected = getComputedStyle(reference).backgroundColor; reference.remove();
      return { blur: getComputedStyle(node).backdropFilter, fill: getComputedStyle(node).backgroundColor, expected };
    });
    expect(off.blur).toBe('none'); expect(off.fill).toBe(off.expected); expect(off.fill).toMatch(/^rgb\(/);
  });
}
