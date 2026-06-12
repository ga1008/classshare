import { expect, test } from '@playwright/test';
import {
  apiJson,
  collectBrowserErrors,
  expectHealthUsesRuntimeDb,
  expectNoBrowserErrors,
  expectSessionRole,
  loginStudent,
  loginTeacher,
  readFixture,
} from '../fixtures/p03';

test.describe('P03 auth and runtime safety', () => {
  test('teacher logs in through the real browser form', async ({ page }, testInfo) => {
    const fixture = readFixture();
    const errors = collectBrowserErrors(page);

    await loginTeacher(page, fixture);
    await expect(page.locator('[data-dashboard-root]')).toBeVisible();
    await expect(page.locator('[data-message-center-bell]').first()).toBeVisible();
    await expectSessionRole(page, 'teacher');
    await expectHealthUsesRuntimeDb(page, fixture);

    const summary = await apiJson<{ status: number; ok: boolean; body: any }>(page, '/api/message-center/summary');
    expect(summary.status).toBe(200);
    expect(summary.body.status).toBe('success');

    await expectNoBrowserErrors(errors, testInfo);
  });

  test('student logs in through the real browser form and stays out of teacher admin UI', async ({ page }, testInfo) => {
    const fixture = readFixture();
    const errors = collectBrowserErrors(page);

    await loginStudent(page, fixture);
    await expect(page.locator('[data-dashboard-root]')).toBeVisible();
    await expectSessionRole(page, 'student');

    await page.goto('/manage/system/super-admin', { waitUntil: 'domcontentloaded' });
    await page.waitForLoadState('networkidle').catch(() => undefined);
    await expect(page.locator('#super-admin-form')).toHaveCount(0);
    await expect(page.locator('body')).not.toContainText(fixture.superTeacher.email);

    await expectNoBrowserErrors(errors, testInfo);
  });

  test('protected pages reject anonymous browser sessions', async ({ page }, testInfo) => {
    const errors = collectBrowserErrors(page);

    await page.goto('/manage/teaching/materials', { waitUntil: 'domcontentloaded' });
    await page.waitForLoadState('networkidle').catch(() => undefined);
    await expect(page.locator('#materials-list-body')).toHaveCount(0);
    await expect(page.locator('#email, #identifier').first()).toBeVisible();

    await expectNoBrowserErrors(errors, testInfo);
  });
});
