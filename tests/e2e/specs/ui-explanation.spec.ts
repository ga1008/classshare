import { expect, test } from '@playwright/test';
import { collectBrowserErrors, expectNoBrowserErrors, loginTeacher, readFixture } from '../fixtures/p03';

// The shared explanation popover replaces native titles, topbar captions and
// long page leads. These checks pin the end-to-end contract: hover opens after
// the configured delay, content renders, and simplified headers stay short.

test.describe('unified explanation popover', () => {
  test('manage page headers are short and explain triggers open the popover', async ({ page }, testInfo) => {
    const fixture = readFixture();
    const errors = collectBrowserErrors(page);
    await loginTeacher(page, fixture);

    await page.goto('/manage/teaching/classes');
    await page.waitForLoadState('networkidle').catch(() => undefined);

    const heading = page.locator('.manage-pagehead__title-row h2').first();
    await expect(heading).toHaveText('班级');

    const trigger = page.locator('.ui-explain-trigger').first();
    await expect(trigger).toBeVisible();
    await trigger.click();

    const popover = page.locator('#ui-explanation-popover');
    await expect(popover).toBeVisible();
    await expect(popover).toContainText('班级');
    await expect(popover).toContainText('学生名单');

    await page.keyboard.press('Escape');
    await expect(popover).toBeHidden();

    await expectNoBrowserErrors(errors, testInfo);
  });

  test('topbar actions expose captions through the popover instead of inline text', async ({ page }, testInfo) => {
    const fixture = readFixture();
    const errors = collectBrowserErrors(page);
    await loginTeacher(page, fixture);

    await page.goto('/manage/teaching/classes');
    await page.waitForLoadState('networkidle').catch(() => undefined);

    const action = page.locator('.app-topbar-action[data-explain]').first();
    await expect(action).toBeVisible();
    await expect(action.locator('small')).toHaveCount(0);
    await expect(action).toHaveAttribute('data-explain-text', /.+/);

    // Hover-and-hold matches the 2s desktop contract.
    await action.hover();
    const popover = page.locator('#ui-explanation-popover');
    await expect(popover).toBeVisible({ timeout: 5_000 });

    await page.mouse.move(4, 4);
    await expect(popover).toBeHidden();

    await expectNoBrowserErrors(errors, testInfo);
  });

  test('sidebar navigation items explain themselves without native tooltips', async ({ page }, testInfo) => {
    const fixture = readFixture();
    const errors = collectBrowserErrors(page);
    await loginTeacher(page, fixture);

    await page.goto('/manage/teaching/materials');
    await page.waitForLoadState('networkidle').catch(() => undefined);

    const navItem = page.locator('.manage-nav-item[data-explain]').first();
    await expect(navItem).toBeVisible();
    await expect(navItem).not.toHaveAttribute('title', /.+/);
    await expect(navItem).toHaveAttribute('data-explain-title', /.+/);

    await expectNoBrowserErrors(errors, testInfo);
  });
});
