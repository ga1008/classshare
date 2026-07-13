import { expect, test } from '@playwright/test';
import {
  collectBrowserErrors,
  expectNoBrowserErrors,
  loginTeacher,
  readFixture,
} from '../fixtures/p03';

test.describe('teacher dashboard todo modal', () => {
  test('all dismiss controls work and the footer stays clickable in a short viewport', async ({ page }, testInfo) => {
    const fixture = readFixture();
    const errors = collectBrowserErrors(page);

    await page.setViewportSize({ width: 1180, height: 720 });
    await loginTeacher(page, fixture);

    const modal = page.locator('.agenda-todo-modal:has([data-todo-form])');
    const openButton = page.locator('[data-agenda-add-todo]').first();
    await expect(openButton).toBeVisible();

    await openButton.click();
    await expect(modal).toBeVisible();
    await modal.locator('[data-todo-close][aria-label="关闭"]').click();
    await expect(modal).toBeHidden();

    await openButton.click();
    await expect(modal).toBeVisible();
    const cancelButton = modal.locator('button[data-todo-close]:not([aria-label])');
    await expect(cancelButton).toBeVisible();
    await cancelButton.click();
    await expect(modal).toBeHidden();

    await openButton.click();
    await expect(modal).toBeVisible();
    await modal.locator('.agenda-todo-modal__backdrop').click({ position: { x: 8, y: 8 } });
    await expect(modal).toBeHidden();

    await expectNoBrowserErrors(errors, testInfo);
  });
});
