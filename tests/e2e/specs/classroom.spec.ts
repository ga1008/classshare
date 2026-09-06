import { expect, test } from '@playwright/test';
import {
  collectBrowserErrors,
  expectNoBrowserErrors,
  loginStudent,
  loginTeacher,
  readFixture,
} from '../fixtures/p03';

test.describe('P03 classroom page', () => {
  test('teacher can render and navigate the target classroom workspace', async ({ page }, testInfo) => {
    const fixture = readFixture();
    const errors = collectBrowserErrors(page);
    const messageText = `P12 classroom chat ${Date.now()}`;

    await loginTeacher(page, fixture);
    await page.goto(`/classroom/${fixture.classOfferingId}`);
    await expect(page.locator('[data-lanshare-island="classroom-page"]')).toBeAttached();
    await expect(page.locator('#assignment-panel')).toBeVisible();
    const materialsEntry = page.locator('.cw-topbar [data-cw-open="materials"]');
    await expect(materialsEntry).toBeVisible();
    await expect(page.locator('#discussion-room')).toBeAttached();
    await expect(page.locator('#classroom-activity-tab-discussion')).toBeVisible();
    await expect(page.locator('[data-lanshare-island="assignment-task-board-sync"]')).toHaveCount(0);
    await expect(page.locator('[data-lanshare-island="material-learning-path-sync"]')).toHaveCount(0);
    await expect(page.locator('#cw-tasks-preview')).toContainText('待处理任务');
    await expect(page.locator('#cw-materials-preview')).toHaveCount(0);
    await expect(page.locator('.cw-primary-grid #classroom-activity-sidebar')).toBeVisible();
    await expect(page.locator('[data-cw-source="tasks"]')).toBeHidden();
    await page.locator('#cw-tasks-preview').getByRole('button', { name: /全部任务/ }).click();
    await expect(page.getByRole('dialog', { name: /任务/ })).toBeVisible();
    await expect(page.locator('[data-cw-source="tasks"]')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.locator('[data-cw-source="tasks"]')).toBeHidden();

    await materialsEntry.click();
    await expect(page.getByRole('dialog', { name: '全部课堂材料' })).toBeVisible();
    await expect(page.locator('[data-cw-source="materials"]')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.locator('[data-cw-source="materials"]')).toBeHidden();
    await expect(materialsEntry).toBeFocused();
    await page.locator('[data-workspace-nav][href="#assignment-panel"]').first().click();
    await expect(page.locator('#assignment-panel')).toBeInViewport();
    await page.locator('[data-workspace-nav][href="#classroom-activity-sidebar"]').first().click();
    await expect(page.locator('#classroom-activity-sidebar')).toBeInViewport();
    await page.locator('#classroom-activity-tab-discussion').click();
    await expect(page.locator('#chat-input')).toBeVisible();
    await expect(page.locator('#ws-status')).toHaveClass(/status-online/, { timeout: 15_000 });
    await page.locator('#chat-input').fill(messageText);
    await page.locator('#chat-form button[type="submit"]').click();
    await expect(page.locator('.chat-message').filter({ hasText: messageText })).toHaveCount(1, { timeout: 15_000 });

    await expectNoBrowserErrors(errors, testInfo);
  });

  test('student can render the assigned classroom but cannot open another class directly', async ({ page }, testInfo) => {
    const fixture = readFixture();
    const errors = collectBrowserErrors(page);

    await loginStudent(page, fixture);
    await page.goto(`/classroom/${fixture.classOfferingId}`);
    await expect(page.locator('[data-lanshare-island="classroom-page"]')).toBeAttached();
    await expect(page.locator('#assignment-panel')).toBeVisible();
    const materialsEntry = page.locator('.cw-topbar [data-cw-open="materials"]');
    await expect(materialsEntry).toBeVisible();
    await expect(page.locator('#discussion-room')).toBeAttached();
    await expect(page.locator('#classroom-activity-tab-discussion')).toBeVisible();

    await materialsEntry.click();
    await expect(page.getByRole('dialog', { name: '全部课堂材料' })).toBeVisible();
    await expect(page.locator('[data-cw-source="materials"]')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.locator('[data-cw-source="materials"]')).toBeHidden();
    await expect(materialsEntry).toBeFocused();

    await page.goto(`/classroom/${fixture.otherClassOfferingId}`, { waitUntil: 'domcontentloaded' });
    await page.waitForLoadState('networkidle').catch(() => undefined);
    await expect(page.locator('#assignment-panel')).toHaveCount(0);

    await expectNoBrowserErrors(errors, testInfo);
  });

  test('ordinary teacher cannot manage another teacher classroom by direct URL', async ({ page }, testInfo) => {
    const fixture = readFixture();
    const errors = collectBrowserErrors(page);

    await loginTeacher(page, fixture, fixture.otherTeacher);
    await page.goto(`/classroom/${fixture.classOfferingId}`, { waitUntil: 'domcontentloaded' });
    await page.waitForLoadState('networkidle').catch(() => undefined);
    await expect(page.locator('#assignment-panel')).toHaveCount(0);

    await expectNoBrowserErrors(errors, testInfo);
  });
});
