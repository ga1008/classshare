import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { serveProcessMaterialModule as serveLayerModule } from './process-material-fixture-modules';

const calendarSource = fs.readFileSync(path.resolve('static/js/semester_calendar.js'), 'utf8');
// Use the same resolved tokens and utilities as the application. Browsers
// cannot compile the source stylesheet's @tailwind and nested @import tree.
const sharedCss = fs.readFileSync(path.resolve('static/css/tailwind-app.css'), 'utf8');

async function mountCalendar(page: Page, selection = false) {
  await page.route('http://calendar.test/**', async route => {
    const url = new URL(route.request().url());
    if (await serveLayerModule(route)) return;
    if (/^\/lq\/[\w.-]+\.js$/.test(url.pathname)) {
      const file = path.resolve('static/js', url.pathname.slice(1));
      if (fs.existsSync(file)) return route.fulfill({ contentType: 'text/javascript', body: fs.readFileSync(file) });
    }
    if (url.pathname === '/calendar.js') {
      await route.fulfill({ contentType: 'text/javascript', body: calendarSource });
    } else if (url.pathname === '/static/js/api.js') {
      await route.fulfill({ contentType: 'text/javascript', body: 'export function apiFetch() { throw new Error("No API writes in calendar layout fixture"); }' });
    } else if (url.pathname === '/calendar-source.css') {
      await route.fulfill({ contentType: 'text/css', body: fs.readFileSync(path.resolve('static/css/lq/pages/calendar.css')) });
    } else if (url.pathname === '/shared.css' || url.pathname === '/dashboard.css') {
      await route.fulfill({ contentType: 'text/css', body: sharedCss });
    } else {
      await route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh-CN"><meta charset="utf-8">
        <link rel="stylesheet" href="/shared.css"><link rel="stylesheet" href="/dashboard.css"><link rel="stylesheet" href="/calendar-source.css">
        <style>body{margin:0}#host{width:960px;max-width:calc(100vw - 48px);margin:24px;padding:0}
        #host .semester-calendar-panel{padding:0;border:0;box-shadow:none}</style>
        <div class="ls-shell" id="host"><section class="semester-calendar-panel" data-semester-calendar-root ${selection ? 'data-lq-calendar' : ''}>
          <label class="semester-calendar-panel__field"><span>当前查看学期</span><select class="form-control" data-semester-calendar-select aria-label="学期"></select></label>
          <div class="semester-calendar-scroll" data-semester-calendar-scroll>
            <div class="semester-calendar-board" data-semester-calendar-board></div>
          </div><div data-semester-calendar-empty hidden></div>
        </section></div><div id="storage" hidden></div>
        <script type="module">import { initSemesterCalendar } from '/calendar.js';
          window.calendarRoot = document.querySelector('[data-semester-calendar-root]');
          window.calendar = initSemesterCalendar(window.calendarRoot, {
            today_iso:'2026-09-06',default_semester_id:1,semesters:[{id:1,name:'2026-2027第一学期',
              start_date:'2026-08-31',end_date:'2027-01-10',week_count:19,is_current:true}]});
        </script></html>` });
    }
  });
  await page.goto('http://calendar.test/');
  await expect(page.locator('.semester-day-cell').first()).toBeAttached();
}

test('shared semester selection mirrors change, retained options and empty refresh', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 840 });
  await mountCalendar(page, true);
  const selector = page.getByRole('combobox', { name: '学期', exact: true });
  await expect(selector).toHaveValue('2026-2027第一学期 · 2026-08-31');
  await page.evaluate(() => {
    const calendar = (window as any).calendar;
    (window as any).calendarChanges = 0;
    document.querySelector('[data-semester-calendar-select]')!.addEventListener('change', () => (window as any).calendarChanges++);
    calendar.setSemesters([...calendar.getSemesters(), { id: 2, name: '上一学期', start_date: '2026-03-02', end_date: '2026-07-12', week_count: 19 }]);
  });
  await selector.click(); await expect(page.locator('.lq-selection__popup')).toBeVisible();
  await selector.press('End'); await selector.press('Enter');
  await expect(selector).toHaveValue('上一学期 · 2026-03-02');
  expect(await page.evaluate(() => (window as any).calendar.getActiveSemester().id)).toBe(2);
  expect(await page.evaluate(() => (window as any).calendarChanges)).toBe(1);
  await page.evaluate(() => (window as any).calendar.setSemesters((window as any).calendar.getSemesters()));
  await expect(selector).toHaveValue('上一学期 · 2026-03-02');
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(390);
  await page.evaluate(() => (window as any).calendar.setSemesters([]));
  await expect(selector).toBeDisabled(); await expect(selector).toHaveValue('暂无学期');
  await expect(page.locator('[data-semester-calendar-select]')).toBeDisabled();
});

async function inspectBoard(page: Page) {
  return page.locator('[data-semester-calendar-scroll]').evaluate(scroll => {
    const board = scroll.querySelector<HTMLElement>('[data-semester-calendar-board]')!;
    const scrollRect = scroll.getBoundingClientRect();
    const boardRect = board.getBoundingClientRect();
    const sticky = board.querySelector('.semester-sticky-cell')!.getBoundingClientRect();
    const row = board.querySelector('.semester-day-cell')!.getBoundingClientRect();
    const hit = document.elementFromPoint(scrollRect.right - 24, row.top + 24);
    return { boardWidth: board.offsetWidth, boardScrollWidth: board.scrollWidth,
      viewportWidth: scroll.clientWidth, scrollLeft: scroll.scrollLeft,
      boardRight: boardRect.right, viewportRight: scrollRect.right,
      stickyLeft: sticky.left, viewportLeft: scrollRect.left,
      rightEdgeHasDate: Boolean(hit?.closest('.semester-day-cell')),
      documentWidth: document.documentElement.scrollWidth, windowWidth: innerWidth };
  });
}

test('full-width calendar canvas stays painted after horizontal dragging despite host min-width resets', async ({ page }) => {
  await mountCalendar(page);
  let metrics = await inspectBoard(page);
  expect(metrics.boardWidth).toBeGreaterThan(metrics.viewportWidth * 2);
  expect(metrics.boardScrollWidth - metrics.boardWidth).toBeLessThanOrEqual(2);
  const scroll = page.locator('[data-semester-calendar-scroll]');
  await scroll.evaluate(el => { el.scrollLeft = 688; });
  const box = await scroll.boundingBox();
  await page.mouse.move(box!.x + box!.width - 80, box!.y + 180);
  await page.mouse.down();
  await page.mouse.move(box!.x + 200, box!.y + 180, { steps: 8 });
  await page.mouse.up();
  metrics = await inspectBoard(page);
  expect(metrics.scrollLeft).toBeGreaterThan(688);
  expect(metrics.boardRight).toBeGreaterThanOrEqual(metrics.viewportRight - 1);
  expect(metrics.rightEdgeHasDate).toBe(true);
  expect(Math.abs(metrics.stickyLeft - metrics.viewportLeft)).toBeLessThan(2);
  expect(metrics.documentWidth).toBeLessThanOrEqual(metrics.windowWidth);
});

test('moving the existing calendar through hidden storage preserves its full canvas at a new width', async ({ page }) => {
  await mountCalendar(page);
  await page.locator('[data-semester-calendar-scroll]').evaluate(el => { el.scrollLeft = 900; });
  await page.evaluate(() => {
    document.getElementById('storage')!.append((window as any).calendarRoot);
    document.getElementById('host')!.style.width = '720px';
    document.getElementById('host')!.append((window as any).calendarRoot);
  });
  const metrics = await inspectBoard(page);
  expect(metrics.boardWidth).toBeGreaterThan(metrics.viewportWidth * 2);
  expect(metrics.boardScrollWidth - metrics.boardWidth).toBeLessThanOrEqual(2);
  expect(metrics.boardRight).toBeGreaterThanOrEqual(metrics.viewportRight - 1);
  expect(metrics.rightEdgeHasDate).toBe(true);
  expect(metrics.documentWidth).toBeLessThanOrEqual(metrics.windowWidth);
});
