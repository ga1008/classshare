import { expect, test, type Locator, type Page } from '@playwright/test';
import path from 'node:path';
import {
  collectBrowserErrors, expectHealthUsesRuntimeDb, expectNoBrowserErrors,
  loginStudent, loginTeacher, readFixture,
} from '../fixtures/p03';

const calendarSelector = '[data-semester-calendar-root]';
const scrollSelector = '[data-semester-calendar-scroll]';
const boardSelector = '[data-semester-calendar-board]';

async function openCalendar(page: Page) {
  const opener = page.locator('.dw-page-head [data-dw-open="calendar"]');
  await opener.click();
  const dialog = page.getByRole('dialog', { name: '日程与事项', exact: true });
  await expect(dialog).toBeVisible();
  const calendar = dialog.locator(calendarSelector);
  await expect(calendar.locator('.semester-day-cell[data-date]').first()).toBeAttached();
  await expect(calendar.locator('[data-semester-calendar-select]')).not.toHaveValue('');
  return { opener, dialog, calendar };
}

async function inspectCanvas(calendar: Locator) {
  return calendar.locator(scrollSelector).evaluate(scroll => {
    const board = scroll.querySelector<HTMLElement>('[data-semester-calendar-board]')!;
    const rect = scroll.getBoundingClientRect();
    const right = rect.left + scroll.clientWidth;
    // Pick an actually painted weekday row, below the sticky date headings.
    // This also works when the calendar itself scrolls vertically on a short screen.
    const row = Array.from(board.querySelectorAll<HTMLElement>('.semester-weekday-cell')).find(cell => {
      const box = cell.getBoundingClientRect();
      const y = box.top + box.height / 2;
      return y > Math.max(rect.top, 0) && y < Math.min(rect.bottom, innerHeight)
        && document.elementFromPoint(rect.left + 16, y)?.closest('.semester-weekday-cell') === cell;
    });
    const rowRect = row?.getBoundingClientRect();
    const y = rowRect ? rowRect.top + rowRect.height / 2 : -1;
    return {
      boardWidth: board.offsetWidth,
      boardOverflow: board.scrollWidth - board.clientWidth,
      scrollWidth: scroll.scrollWidth,
      viewportWidth: scroll.clientWidth,
      scrollLeft: scroll.scrollLeft,
      maxScrollLeft: scroll.scrollWidth - scroll.clientWidth,
      stickyOffset: rowRect ? rowRect.left - rect.left : null,
      stickyWidth: rowRect?.width || 0,
      headersHaveBackground: Array.from(board.querySelectorAll('.semester-header-cell, .semester-band-cell')).every(cell => {
        const color = getComputedStyle(cell).backgroundColor;
        return color !== 'transparent' && !/^rgba\([^)]*,\s*0(?:\.0+)?\)$/.test(color);
      }),
      hasWeekdayRow: Boolean(row),
      rightEdgeHasDate: Boolean(document.elementFromPoint(right - 24, y)?.closest('.semester-day-cell[data-date]')),
      viewportLeft: rect.left, viewportRight: right, rowY: y,
      pageOverflow: document.documentElement.scrollWidth - innerWidth,
    };
  });
}

for (const role of ['teacher', 'student'] as const) {
  test(`${role} calendar stays painted at the end of a real drag on desktop and a narrow short screen`, async ({ page }, testInfo) => {
    const fixture = readFixture();
    const errors = collectBrowserErrors(page);
    await page.setViewportSize({ width: 1440, height: 900 });
    await (role === 'teacher' ? loginTeacher(page, fixture) : loginStudent(page, fixture));
    await expectHealthUsesRuntimeDb(page, fixture);
    const { dialog, calendar } = await openCalendar(page);

    for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 620 }]) {
      await test.step(`${viewport.width} × ${viewport.height}`, async () => {
        await page.setViewportSize(viewport);
        // The existing calendar rebuilds its cells after debounced resize.
        // Reacquire the locator if that rebuild lands during Playwright's scroll.
        await expect(async () => {
          const weekday = calendar.locator('.semester-weekday-cell').first();
          await weekday.scrollIntoViewIfNeeded();
          await expect(weekday).toBeInViewport();
        }).toPass({ timeout: 10_000, intervals: [100, 250] });
        const scroll = calendar.locator(scrollSelector);
        await scroll.evaluate(node => {
          const maximum = node.scrollWidth - node.clientWidth;
          node.scrollLeft = Math.max(0, maximum - Math.min(80, maximum / 4));
        });
        let metrics = await inspectCanvas(calendar);
        expect(metrics.hasWeekdayRow).toBe(true);
        expect(metrics.boardWidth).toBeGreaterThan(metrics.viewportWidth);
        expect(metrics.boardOverflow).toBeLessThanOrEqual(2);
        expect(Math.abs(metrics.scrollWidth - metrics.boardWidth)).toBeLessThanOrEqual(2);
        const before = metrics.scrollLeft;
        const travel = Math.min(240, metrics.viewportWidth - metrics.stickyWidth - 48);
        expect(travel).toBeGreaterThan(20);
        const start = metrics.viewportRight - 24;
        await page.mouse.move(start, metrics.rowY);
        await page.mouse.down();
        await page.mouse.move(start - travel, metrics.rowY, { steps: 12 });
        await page.mouse.up();
        await expect.poll(async () => (await inspectCanvas(calendar)).scrollLeft).toBeGreaterThan(before + 10);
        await expect.poll(async () => (await inspectCanvas(calendar)).rightEdgeHasDate).toBe(true);
        metrics = await inspectCanvas(calendar);
        expect(metrics.scrollLeft).toBeGreaterThan(metrics.maxScrollLeft * 0.75);
        expect(Math.abs(metrics.stickyOffset!)).toBeLessThanOrEqual(2);
        // Activating/snapping a week must not restore the legacy 132px axis,
        // and sticky date headings need a painted base above scrolled dates.
        expect(Math.abs(metrics.stickyWidth - 72)).toBeLessThanOrEqual(1);
        expect(metrics.headersHaveBackground).toBe(true);
        expect(metrics.pageOverflow).toBeLessThanOrEqual(1);
        expect(await dialog.evaluate(node => node.scrollWidth - node.clientWidth)).toBeLessThanOrEqual(1);
        if (role === 'teacher' && process.env.P03_CALENDAR_VISUAL_DIR) {
          await page.screenshot({ path: path.join(process.env.P03_CALENDAR_VISUAL_DIR, `teacher-${viewport.width}x${viewport.height}-tail.png`) });
        }
        await calendar.locator('[data-semester-calendar-scroll-today]').click();
        const currentWeek = await calendar.locator('.semester-day-cell.is-today').getAttribute('data-week-key');
        const sunday = calendar.locator(`.semester-day-cell[data-week-key="${currentWeek}"]`).nth(6);
        await expect(async () => {
          await sunday.scrollIntoViewIfNeeded();
          expect(await sunday.evaluate(cell => {
            const rect = cell.getBoundingClientRect();
            const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
            return hit?.closest('.semester-day-cell') === cell;
          })).toBe(true);
        }).toPass({ timeout: 10_000, intervals: [100, 250] });
        if (role === 'teacher' && process.env.P03_CALENDAR_VISUAL_DIR) {
          await page.screenshot({ path: path.join(process.env.P03_CALENDAR_VISUAL_DIR, `teacher-${viewport.width}x${viewport.height}-current-sunday.png`) });
        }
      });
    }
    await expectNoBrowserErrors(errors, testInfo);
  });
}

test('today, semester overview and the selected week survive switching views without duplicating the calendar', async ({ page }, testInfo) => {
  const fixture = readFixture();
  const errors = collectBrowserErrors(page);
  await loginStudent(page, fixture);
  const { opener, dialog, calendar } = await openCalendar(page);
  const originalCalendar = await calendar.elementHandle();
  expect(originalCalendar).not.toBeNull();
  const details = calendar.locator('.semester-calendar-details');
  await expect(details).not.toHaveAttribute('open', '');
  await expect(details.locator('[data-semester-calendar-overview]')).toBeHidden();
  await details.locator('summary').click();
  for (const attribute of ['period', 'week-range', 'progress', 'holiday-summary']) {
    const value = details.locator(`[data-semester-calendar-${attribute}]`);
    await expect(value).toBeVisible();
    await expect(value).not.toHaveText(/^(--|\s*)$/);
  }
  const overviewValues = await details.locator('.semester-calendar-overview-card strong').allTextContents();
  await details.locator('summary').click();

  const makeup = calendar.locator('.semester-mini-tag.workday[data-explain]').first();
  await expect(makeup).toHaveCount(1);
  const fullMakeupText = (await makeup.getAttribute('data-explain-text'))!;
  const makeupDate = /补课对应：(\d{4})-(\d{2})-(\d{2})/.exec(fullMakeupText);
  expect(makeupDate).not.toBeNull();
  await expect(makeup).toHaveText(`调休上课 · 补 ${Number(makeupDate![2])}/${Number(makeupDate![3])}`);
  await makeup.focus();
  const explanation = page.locator('#ui-explanation-popover');
  await expect(explanation).toBeVisible();
  await expect(explanation.locator('#ui-explanation-text')).toHaveText(fullMakeupText);
  await explanation.getByRole('button', { name: '关闭说明', exact: true }).click();
  await expect(explanation).toBeHidden();
  await expect(dialog).toBeVisible();

  const week = calendar.locator('.semester-header-cell[data-week-key]').nth(3);
  const selectedWeek = await week.getAttribute('data-week-key');
  await week.click();
  const activeWeek = calendar.locator('.semester-header-cell.is-active-week[data-week-key]');
  await expect(activeWeek).toHaveAttribute('data-week-key', selectedWeek!);
  const semester = await calendar.locator('[data-semester-calendar-select]').inputValue();
  await dialog.getByRole('button', { name: '全部事项', exact: true }).click();
  await expect(dialog.locator('.dw-all-items')).toHaveAttribute('aria-busy', 'false');
  await expect(page.locator(calendarSelector)).toHaveCount(1);
  await dialog.getByRole('button', { name: '学期日历', exact: true }).click();
  await expect(calendar).toBeVisible();
  expect(await calendar.evaluate((node, original) => node === original, originalCalendar)).toBe(true);
  await expect(page.locator(calendarSelector)).toHaveCount(1);
  await expect(calendar.locator('[data-semester-calendar-select]')).toHaveValue(semester);
  await expect(activeWeek).toHaveAttribute('data-week-key', selectedWeek!);
  expect(await details.locator('.semester-calendar-overview-card strong').allTextContents()).toEqual(overviewValues);

  const todayCell = calendar.locator('.semester-day-cell.is-today[data-date]');
  await expect(todayCell).toHaveCount(1);
  const todayWeek = await todayCell.getAttribute('data-week-key');
  await calendar.locator('[data-semester-calendar-scroll-today]').click();
  await expect(activeWeek).toHaveAttribute('data-week-key', todayWeek!);
  await expect.poll(async () => calendar.locator(scrollSelector).evaluate((scroll, key) => {
    const active = scroll.querySelector<HTMLElement>(`.semester-header-cell[data-week-key="${key}"]`)!;
    const viewport = scroll.getBoundingClientRect();
    const target = active.getBoundingClientRect();
    const weekdayWidth = scroll.querySelector('.semester-weekday-cell')!.getBoundingClientRect().width;
    return target.left >= viewport.left + weekdayWidth - 2 && target.right <= viewport.left + scroll.clientWidth + 2;
  }, todayWeek)).toBe(true);
  await page.keyboard.press('Escape');
  await expect(dialog).toBeHidden();
  await expect(opener).toBeFocused();
  await expect(page.locator(calendarSelector)).toHaveCount(1);
  await originalCalendar?.dispose();
  await expectNoBrowserErrors(errors, testInfo);
});

test('calendar tools hand off to the existing todo and subscription dialogs on a short viewport', async ({ page }, testInfo) => {
  const fixture = readFixture();
  const errors = collectBrowserErrors(page);
  await page.setViewportSize({ width: 390, height: 620 });
  await loginTeacher(page, fixture);
  // A real subscription GET can issue a token; it must run only in the isolated fixture.
  await expectHealthUsesRuntimeDb(page, fixture);
  const { dialog } = await openCalendar(page);
  await dialog.locator('.dw-dialog-tools').getByRole('button', { name: '新增待办', exact: true }).click();
  const todo = page.locator('.agenda-todo-modal:has([data-todo-form])');
  await expect(dialog).toBeHidden();
  await expect(todo).toBeVisible();
  await expect(todo.locator('input[name="title"]')).toBeFocused();
  await todo.locator('button[data-todo-close]:not([aria-label])').click();
  await expect(todo).toBeHidden();

  await openCalendar(page);
  const feedResponse = page.waitForResponse(response => new URL(response.url()).pathname === '/api/calendar-feed');
  await dialog.locator('.dw-dialog-tools').getByRole('button', { name: '订阅日历', exact: true }).click();
  expect((await feedResponse).ok()).toBe(true);
  const feed = page.getByRole('dialog', { name: '订阅到手机日历', exact: true });
  await expect(dialog).toBeHidden();
  await expect(feed).toBeVisible();
  await expect(feed.locator('[data-feed-url]')).toHaveValue(/\/calendar\/feed\/[^/]+\.ics$/);
  await expect(feed.locator('[data-feed-url]')).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(feed).toBeHidden();
  await openCalendar(page);
  await expect(page.locator(calendarSelector)).toHaveCount(1);
  await expectNoBrowserErrors(errors, testInfo);
});
