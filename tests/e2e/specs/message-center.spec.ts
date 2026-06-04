import { expect, test } from '@playwright/test';
import {
  apiJson,
  collectBrowserErrors,
  expectNoBrowserErrors,
  loginStudent,
  loginTeacher,
  readFixture,
} from '../fixtures/p03';

test.describe('P03 message center', () => {
  test('teacher opens message center from the dashboard bell and marks messages read', async ({ page }, testInfo) => {
    const fixture = readFixture();
    const errors = collectBrowserErrors(page);

    await loginTeacher(page, fixture);
    await expect(page.locator('[data-message-center-bell]').first()).toBeVisible();
    await Promise.all([
      page.waitForURL(/\/profile\?section=notifications.*profile-message-center/, { timeout: 15_000 }),
      page.locator('[data-message-center-bell]').first().click(),
    ]);
    await expect(page.locator('[data-lanshare-island="message-center-workspace-sync"]')).toBeVisible();
    await expect(page.locator('#message-center-feed')).toBeVisible();

    const summary = await apiJson<{ status: number; ok: boolean; body: any }>(page, '/api/message-center/summary');
    expect(summary.status).toBe(200);
    expect(summary.body.status).toBe('success');

    const readResponsePromise = page.waitForResponse((response) =>
      response.url().includes('/api/message-center/read') && response.request().method() === 'POST',
    );
    await page.locator('#message-center-mark-read').click();
    expect((await readResponsePromise).ok()).toBeTruthy();

    await expectNoBrowserErrors(errors, testInfo);
  });

  test('student message center renders only the current student session scope', async ({ page }, testInfo) => {
    const fixture = readFixture();
    const errors = collectBrowserErrors(page);

    await loginStudent(page, fixture);
    await page.goto('/profile?section=notifications#profile-message-center');
    await expect(page.locator('[data-lanshare-island="message-center-workspace-sync"]')).toBeVisible();
    await expect(page.locator('#message-center-feed')).toBeVisible();
    const summary = await apiJson<{ status: number; ok: boolean; body: any }>(page, '/api/message-center/summary');
    expect(summary.status).toBe(200);
    expect(summary.body.status).toBe('success');
    await expect(page.locator('body')).not.toContainText('P03 QA teacher notification');

    await expectNoBrowserErrors(errors, testInfo);
  });
});
