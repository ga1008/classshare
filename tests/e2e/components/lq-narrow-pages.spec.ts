import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
const fixture = JSON.parse(execFileSync(process.env.LQ_TEST_PYTHON || 'venv/Scripts/python.exe', ['tests/e2e/scripts/render_lq_narrow_pages.py'], { encoding: 'utf8' }));
const origin = 'https://narrow-lq.test';
for (const name of ['schedule', 'submission']) for (const width of [390, 1440]) {
  test(`${name} actual template stays within ${width}px with all controls available`, async ({ page }, testInfo) => {
    expect(fixture.isolated).toBe(true);
    await page.setViewportSize({ width, height: 980 });
    await page.route('**/*', route => {
      const url = new URL(route.request().url());
      if (url.origin !== origin) return route.abort();
      if (url.pathname.startsWith('/static/')) {
        const file = path.resolve('.' + url.pathname);
        if (file.startsWith(path.resolve('static') + path.sep) && fs.existsSync(file)) return route.fulfill({ body: fs.readFileSync(file), contentType: file.endsWith('.css') ? 'text/css' : file.endsWith('.js') ? 'text/javascript' : 'application/octet-stream' });
        return route.abort();
      }
      if (url.pathname.endsWith('/overview')) return route.fulfill({ json: fixture.overview });
      if (url.pathname === '/' + name) return route.fulfill({ contentType: 'text/html', body: fixture[name] });
      return route.fulfill({ json: {} });
    });
    await page.goto(origin + '/' + name);
    if (name === 'schedule') await expect(page.locator('.cs-progress')).toBeVisible();
    const layout = await page.evaluate(() => ({ overflow: document.documentElement.scrollWidth - innerWidth,
      candidates: [...document.body.querySelectorAll('*')].map(el => ({ tag: el.tagName, class: el.getAttribute('class'), left: el.getBoundingClientRect().left, right: el.getBoundingClientRect().right, width: el.getBoundingClientRect().width, scroll: el.scrollWidth, client: el.clientWidth })).filter(el => el.width && (el.right > innerWidth + 1 || el.left < -1)).slice(0, 35) }));
    await testInfo.attach('overflow-diagnostics', { body: JSON.stringify(layout, null, 2), contentType: 'application/json' });
    await page.screenshot({ path: testInfo.outputPath(`${name}-${width}.png`), fullPage: true });
    expect(layout.overflow, JSON.stringify(layout.candidates)).toBeLessThanOrEqual(1);
    if (name === 'schedule') {
      await expect(page.locator('.cs-progress__anchor')).toHaveText('第1周周一 2026-09-14 · 平台学期设置（2026-2027第1学期）');
      await expect(page.locator('.cs-progress [role="progressbar"]')).toHaveAttribute('aria-valuemax', '20');
      for (const hook of ['data-cs-term', 'data-cs-course', 'data-cs-class']) await expect(page.locator(`[${hook}]`)).toBeVisible();
    } else {
      await expect(page.locator('.attachment-manage-copy')).toContainText('jpg/jpeg/png/gif/webp/bmp/tiff/ico/icns/sgi/jp2/heic/heif');
      await expect(page.locator('.attachment-actions button')).toHaveCount(3);
      for (const button of await page.locator('.attachment-actions button').all()) {
        await button.scrollIntoViewIfNeeded();
        const box = await button.boundingBox();
        expect(box!.x).toBeGreaterThanOrEqual(0);
        expect(box!.x + box!.width).toBeLessThanOrEqual(width);
      }
      const nav = page.locator('.submission-jump-section-list');
      if (width === 390) {
        expect(await nav.evaluate(el => el.scrollWidth > el.clientWidth)).toBe(true);
        await nav.evaluate(el => { el.scrollLeft = el.scrollWidth; });
        await expect(nav.locator('button').last()).toContainText('批改操作');
        const fits = await nav.evaluate(el => el.lastElementChild!.getBoundingClientRect().right <= el.getBoundingClientRect().right + 1);
        expect(fits).toBe(true);
      }
      await page.locator('#grade-feedback').fill('保留教师批改草稿');
      await page.setViewportSize({ width: width === 390 ? 1440 : 390, height: 980 });
      await expect(page.locator('#grade-feedback')).toHaveValue('保留教师批改草稿');
    }
  });
}
