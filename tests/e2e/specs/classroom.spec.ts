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
    await expect(page.locator('#materials-panel')).toBeVisible();
    await expect(page.locator('#discussion-room')).toBeAttached();
    await expect(page.locator('#classroom-activity-tab-discussion')).toBeVisible();
    await expect(page.locator('[data-lanshare-island="assignment-task-board-sync"]')).toBeVisible();
    await expect(page.locator('[data-lanshare-island="material-learning-path-sync"]')).toBeVisible();

    await page.locator('[data-workspace-nav][href="#materials-panel"]').first().click();
    await expect(page.locator('#materials-panel')).toBeInViewport();
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
    await expect(page.locator('#materials-panel')).toBeVisible();
    await expect(page.locator('#discussion-room')).toBeAttached();
    await expect(page.locator('#classroom-activity-tab-discussion')).toBeVisible();

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
