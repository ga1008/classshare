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
        return route.fulfill({ contentType: 'text/css', body: fs.readFileSync(file) });
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
    await page.locator('html').evaluate(node => { node.setAttribute('data-lq-glass', 'off'); });
    const off = await page.locator('.lde-topbar').evaluate(node => {
      const reference = document.createElement('span'); reference.style.background = 'hsl(var(--ls-surface-1))'; document.body.append(reference);
      const expected = getComputedStyle(reference).backgroundColor; reference.remove();
      return { blur: getComputedStyle(node).backdropFilter, fill: getComputedStyle(node).backgroundColor, expected };
    });
    expect(off.blur).toBe('none'); expect(off.fill).toBe(off.expected); expect(off.fill).toMatch(/^rgb\(/);
  });
}
