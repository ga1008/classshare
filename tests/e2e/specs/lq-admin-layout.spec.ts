import fs from 'node:fs';
import { test, expect, readS3Fixture } from '../fixtures/lq-s3';
import { loginTeacher } from '../fixtures/p03';

for (const appearance of ['light', 'dark'] as const) for (const width of [390, 1440]) {
  test(`admin crawler/monitor preserve content within shared shell: ${appearance} ${width}`, async ({ page }) => {
    const fixture = readS3Fixture(); await loginTeacher(page, fixture, fixture.superTeacher);
    await page.setViewportSize({ width, height: 900 }); const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
    const revision = JSON.parse(fs.readFileSync('static/assets/manifest.json', 'utf8')).revision;
    for (const route of ['blog-crawler', 'monitor']) {
      await page.goto(`/manage/system/${route}`); await page.waitForLoadState('networkidle');
      await page.evaluate(async appearance => {
        const runtime = (document as any)[Symbol.for('lanshare.theme.installation')], core = (window as any).LanShareTheme;
        runtime.refresh({ ...core.readPreferences(document.documentElement), appearance }); await document.fonts.ready;
      }, appearance);
      await expect(page.locator('html')).toHaveAttribute('data-appearance', appearance);
      expect(await page.locator('link[rel="stylesheet"]').evaluateAll(links => links.some(link => (link as HTMLLinkElement).href.includes('/assets/')))).toBe(true);
      expect(await page.locator('link[rel="stylesheet"]').evaluateAll((links, revision) => links.some(link => (link as HTMLLinkElement).href.includes(`/assets/${revision}/css/tailwind-app.css`)), revision)).toBe(true);
      expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
      if (route === 'blog-crawler') {
        const stats = page.locator('.bc-stat'); await expect(stats).toHaveCount(4);
        for (const stat of await stats.all()) await expect(stat).toHaveAttribute('data-lq-component', 'surface');
        const surfaces = await page.locator('.bc-stat,.bc-switch,.bc-table-wrap').evaluateAll(nodes => nodes.map(node => {
          const probe = document.createElement('span'); probe.style.backgroundColor = 'hsl(var(--lq-material-fill))'; node.append(probe);
          const expected = getComputedStyle(probe).backgroundColor; probe.remove(); const style = getComputedStyle(node);
          return { expected, actual: style.backgroundColor, blur: style.backdropFilter };
        }));
        for (const surface of surfaces) { expect(surface.actual).toBe(surface.expected); expect(surface.blur).toBe('none'); }
        const fields = page.locator('#bc-config-form input[name],#bc-config-form textarea[name],#bc-config-form select[name]');
        const original = await fields.evaluateAll(nodes => nodes.map(node => ({ name: (node as HTMLInputElement).name, value: (node as HTMLInputElement).value })));
        expect(original.length).toBeGreaterThan(10);
        for (const id of ['bc-refresh-btn', 'bc-cancel-btn', 'bc-run-btn']) await expect(page.locator(`#${id}`)).toBeVisible();
        // Horizontal tables retain their original columns inside the local scroller.
        const table = page.locator('.bc-panel table').first(); await expect(table.locator('thead th')).toHaveCount(7);
        const scroller = table.locator('..'); expect(await scroller.evaluate(el => getComputedStyle(el).overflowX)).toBe('auto');
        if (width === 390) { await scroller.evaluate(el => el.scrollLeft = el.scrollWidth); expect(await scroller.evaluate(el => el.scrollLeft)).toBeGreaterThan(0); }
        expect(await fields.evaluateAll(nodes => nodes.map(node => ({ name: (node as HTMLInputElement).name, value: (node as HTMLInputElement).value })))).toEqual(original);
      } else {
        await expect(page.locator('#monitorTiles .monitor-tile')).toHaveCount(6);
        const inks = await page.locator('#monitorBoard').evaluate(board => {
          const probe = document.createElement('span'); board.append(probe); probe.style.color = 'hsl(var(--ls-ink))'; const ink = getComputedStyle(probe).color;
          probe.style.color = 'hsl(var(--ls-ink-2))'; const muted = getComputedStyle(probe).color; probe.remove();
          return { ink, muted, value: getComputedStyle(board.querySelector('.monitor-tile__value')!).color, label: getComputedStyle(board.querySelector('.monitor-tile__label')!).color,
            donut: getComputedStyle(board.querySelector('#statusDonut text')!).fill, chart: getComputedStyle(board.querySelector('#trendChart text')!).fill };
        });
        expect(inks.value).toBe(inks.ink); expect(inks.label).toBe(inks.muted); expect(inks.donut).toBe(inks.ink); expect(inks.chart).toBe(inks.muted);
        const board = (await page.locator('#monitorBoard').boundingBox())!; expect(board.x).toBeGreaterThanOrEqual(0); expect(board.x + board.width).toBeLessThanOrEqual(width);
        await expect(page.locator('#routesTable th')).toHaveCount(6);
        expect(await page.locator('#routesTable').locator('..').evaluate(el => getComputedStyle(el).overflowX)).toBe('auto');
        for (const id of ['monitorAutoBtn', 'monitorRefreshBtn', 'monitorMemBtn', 'monitorAiBtn']) await expect(page.locator(`#${id}`)).toBeVisible();
        // Only read/display controls are exercised; no optimisation, run or AI operation.
        await page.locator('#monitorAutoBtn').click(); await expect(page.locator('#monitorAutoBtn')).toContainText('关');
        await page.locator('#procSearch').fill('不存在的合成进程'); await expect(page.locator('#procSearch')).toHaveValue('不存在的合成进程');
        await page.locator('#procSearch').clear();
      }
      fs.mkdirSync('.codex-temp/lq-admin-layout', { recursive: true });
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.screenshot({ path: `.codex-temp/lq-admin-layout/${route}-${appearance}-${width}.png` });
    }
    expect(errors).toEqual([]);
  });
}
