import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import {
  collectBrowserErrors,
  expectNoBrowserErrors,
  loginStudent,
  loginTeacher,
  readFixture,
} from '../fixtures/p03';

test.describe('P03 materials management', () => {
  test('teacher renders materials page and uploads a temporary QA material', async ({ page }, testInfo) => {
    const fixture = readFixture();
    const errors = collectBrowserErrors(page);
    const uploadDir = path.join(fixture.runtimeRoot, 'uploads');
    fs.mkdirSync(uploadDir, { recursive: true });
    const uploadPath = path.join(uploadDir, `p03-material-${Date.now()}.md`);
    fs.writeFileSync(uploadPath, '# P03 material\n\nThis file is created only for the copied runtime database.\n', 'utf8');

    await loginTeacher(page, fixture);
    await page.goto('/manage/materials');
    await expect(page.locator('[data-lanshare-island="materials-manage-page"]')).toBeAttached();
    await expect(page.getByTestId('p03-materials-list')).toBeVisible();
    await expect(page.getByTestId('p03-materials-refresh')).toBeVisible();
    await page.getByTestId('p03-materials-refresh').click();

    const uploadResponsePromise = page.waitForResponse((response) =>
      response.url().includes('/api/materials/upload') && response.request().method() === 'POST',
    );
    await page.getByTestId('p03-materials-file-input').setInputFiles(uploadPath);
    const uploadResponse = await uploadResponsePromise;
    expect(uploadResponse.ok()).toBeTruthy();

    await expect(page.getByTestId('p03-materials-list')).toContainText(path.basename(uploadPath), { timeout: 15_000 });
    await page.getByTestId('p03-materials-search').fill('p03-material');
    await expect(page.getByTestId('p03-materials-list')).toContainText(path.basename(uploadPath));

    await expectNoBrowserErrors(errors, testInfo);
  });

  test('student cannot open teacher materials management page', async ({ page }, testInfo) => {
    const fixture = readFixture();
    const errors = collectBrowserErrors(page);

    await loginStudent(page, fixture);
    await page.goto('/manage/materials', { waitUntil: 'domcontentloaded' });
    await page.waitForLoadState('networkidle').catch(() => undefined);
    await expect(page.locator('[data-lanshare-island="materials-manage-page"]')).toHaveCount(0);
    await expect(page.getByTestId('p03-materials-list')).toHaveCount(0);

    await expectNoBrowserErrors(errors, testInfo);
  });
});
