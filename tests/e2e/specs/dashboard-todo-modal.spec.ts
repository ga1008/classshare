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

  test('creates a private todo by default and keeps classroom association optional', async ({ page }, testInfo) => {
    const fixture = readFixture();
    const errors = collectBrowserErrors(page);
    const todoTitle = `P03 private todo ${Date.now()}`;

    await page.setViewportSize({ width: 1180, height: 720 });
    await loginTeacher(page, fixture);

    const modal = page.locator('.agenda-todo-modal:has([data-todo-form])');
    await page.locator('[data-agenda-add-todo]').first().click();
    await expect(modal).toBeVisible();
    await expect(modal.locator('[data-todo-heading]')).toHaveText('记一件待办');

    const titleField = modal.locator('input[name="title"]');
    const scope = modal.locator('[data-todo-scope]');
    const courseSelect = modal.locator('[data-todo-course]');
    await expect(titleField).toBeVisible();
    await expect(scope).not.toHaveAttribute('open', '');
    await expect(modal.locator('[data-todo-scope-title]')).toHaveText('私人待办');
    await expect(courseSelect).toHaveValue('');

    await scope.locator('summary').click();
    await expect(courseSelect).toBeVisible();
    await courseSelect.selectOption(String(fixture.classOfferingId));
    await expect(courseSelect).toHaveValue(String(fixture.classOfferingId));
    await expect(modal.locator('[data-todo-scope-copy]')).toContainText('已关联课堂');
    await courseSelect.selectOption('');
    await expect(modal.locator('[data-todo-scope-title]')).toHaveText('私人待办');

    await titleField.fill(todoTitle);
    await modal.locator('[data-todo-submit]').click();
    await page.waitForLoadState('networkidle').catch(() => undefined);

    const findTodo = async (offeringId: number) => {
      const collection = page.getByRole('dialog', { name: '日程与事项', exact: true });
      if (!await collection.isVisible()) {
        await page.locator('.dw-focus').getByRole('button', { name: /^全部事项/ }).click();
      }
      await collection.getByRole('searchbox', { name: '搜索事项' }).fill(todoTitle);
      const row = collection.locator('.dw-agenda-row').filter({ hasText: todoTitle });
      await expect(row).toHaveCount(1);
      const response = await page.request.get(`/api/dashboard/workspace?q=${encodeURIComponent(todoTitle)}&kind=manual`);
      const payload = await response.json();
      expect(response.ok()).toBe(true);
      expect(payload.workspace.all_items).toHaveLength(1);
      expect(payload.workspace.all_items[0].offering_id).toBe(offeringId);
      return row.getByRole('button', { name: '查看待办' });
    };
    let todoItem = await findTodo(0);

    // Association remains available as an organizational option while editing.
    await todoItem.click();
    await page.locator('.agenda-popover [data-pop-edit]').click();
    await expect(modal).toBeVisible();
    await modal.locator('[data-todo-scope] summary').click();
    await courseSelect.selectOption(String(fixture.classOfferingId));
    await modal.locator('[data-todo-submit]').click();
    await page.waitForLoadState('networkidle').catch(() => undefined);

    todoItem = await findTodo(fixture.classOfferingId);

    // A linked todo can be detached again without recreating it.
    await todoItem.click();
    await page.locator('.agenda-popover [data-pop-edit]').click();
    await expect(modal).toBeVisible();
    await modal.locator('[data-todo-scope] summary').click();
    await courseSelect.selectOption('');
    await modal.locator('[data-todo-submit]').click();
    await page.waitForLoadState('networkidle').catch(() => undefined);

    todoItem = await findTodo(0);

    page.once('dialog', (dialog) => dialog.accept());
    await todoItem.click();
    await page.locator('.agenda-popover [data-pop-delete]').click();
    await expect.poll(async () => {
      const response = await page.request.get(`/api/dashboard/workspace?q=${encodeURIComponent(todoTitle)}&kind=manual`);
      return (await response.json()).workspace.filtered_total;
    }).toBe(0);

    await expectNoBrowserErrors(errors, testInfo);
  });
});
