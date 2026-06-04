import { expect, test } from '@playwright/test';
import {
  apiJson,
  collectBrowserErrors,
  expectNoBrowserErrors,
  loginStudent,
  loginTeacher,
  readFixture,
} from '../fixtures/p03';

test.describe('P03 system permission pages', () => {
  test('super teacher can open system pages and read organization tree API', async ({ page }, testInfo) => {
    const fixture = readFixture();
    const errors = collectBrowserErrors(page);

    await loginTeacher(page, fixture, fixture.superTeacher);
    await page.goto('/manage/system');
    await expect(page.locator('.manage-main')).toContainText(fixture.superTeacher.email);
    await page.goto('/manage/system/super-admin');
    await expect(page).toHaveURL(/\/manage\/system\/users$/);
    await expect(page.locator('.manage-main')).toContainText(fixture.superTeacher.email);

    const tree = await apiJson<{ status: number; ok: boolean; body: any }>(
      page,
      '/api/manage/system/organizations/tree',
    );
    expect(tree.status).toBe(200);
    expect(tree.body.status).toBe('success');

    await expectNoBrowserErrors(errors, testInfo);
  });

  test('ordinary teacher cannot open super-admin page or system API', async ({ page }, testInfo) => {
    const fixture = readFixture();
    const errors = collectBrowserErrors(page);

    await loginTeacher(page, fixture);
    await page.goto('/manage/system/super-admin', { waitUntil: 'domcontentloaded' });
    await page.waitForLoadState('networkidle').catch(() => undefined);
    await expect(page.locator('#super-admin-form')).toHaveCount(0);
    await expect(page.locator('body')).not.toContainText(fixture.superTeacher.email);

    const tree = await apiJson<{ status: number; ok: boolean; body: any }>(
      page,
      '/api/manage/system/organizations/tree',
    );
    expect(tree.status).toBe(403);

    await expectNoBrowserErrors(errors, testInfo);
  });

  test('student cannot open system management pages', async ({ page }, testInfo) => {
    const fixture = readFixture();
    const errors = collectBrowserErrors(page);

    await loginStudent(page, fixture);
    await page.goto('/manage/system/super-admin', { waitUntil: 'domcontentloaded' });
    await page.waitForLoadState('networkidle').catch(() => undefined);
    await expect(page.locator('#super-admin-form')).toHaveCount(0);
    await expect(page.locator('body')).not.toContainText(fixture.superTeacher.email);

    await expectNoBrowserErrors(errors, testInfo);
  });
});
