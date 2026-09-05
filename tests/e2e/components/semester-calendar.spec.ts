import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const calendarSource = fs.readFileSync(path.resolve('static/js/semester_calendar.js'), 'utf8');
const sharedCss = fs.readFileSync(path.resolve('static/css/ui-system.src.css'), 'utf8');
const dashboardCss = fs.readFileSync(path.resolve('static/css/dashboard_workspace.css'), 'utf8');

async function mountCalendar(page: Page) {
  await page.route('http://calendar.test/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/calendar.js') {
      await route.fulfill({ contentType: 'text/javascript', body: calendarSource });
    } else if (url.pathname === '/static/js/api.js') {
      await route.fulfill({ contentType: 'text/javascript', body: 'export function apiFetch() { throw new Error("No API writes in calendar layout fixture"); }' });
    } else if (url.pathname === '/shared.css' || url.pathname === '/dashboard.css') {
      await route.fulfill({ contentType: 'text/css', body: url.pathname === '/shared.css' ? sharedCss : dashboardCss });
    } else {
      await route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh-CN"><meta charset="utf-8">
        <link rel="stylesheet" href="/shared.css"><link rel="stylesheet" href="/dashboard.css">
        <style>body{margin:0}#host{width:960px;max-width:calc(100vw - 48px);margin:24px;padding:0}
        #host .semester-calendar-panel{padding:0;border:0;box-shadow:none}</style>
        <div class="dw-shell" id="host"><section class="semester-calendar-panel" data-semester-calendar-root>
          <select data-semester-calendar-select aria-label="学期"></select>
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
