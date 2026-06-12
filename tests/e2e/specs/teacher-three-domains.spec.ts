import { expect, test } from '@playwright/test';
import {
  collectBrowserErrors,
  expectNoBrowserErrors,
  loginStudent,
  loginTeacher,
  readFixture,
} from '../fixtures/p03';

const domainPages = [
  { path: '/manage/teaching', domain: 'teaching', title: '教学流程工作台' },
  { path: '/manage/academic', domain: 'academic', title: '教务总览' },
  { path: '/manage/me', domain: 'teacher', title: '我的概览' },
];

const legacyRedirects = [
  ['/manage/offerings', '/manage/teaching/offerings'],
  ['/manage/classrooms', '/manage/academic/classrooms'],
  ['/manage/gongwen', '/manage/academic/gongwen'],
  ['/manage/signatures', '/manage/me/signatures'],
  ['/manage/system/password-resets', '/manage/me/password-resets'],
] as const;

test.describe('P03 teacher three-domain management shell', () => {
  test('teacher can open each management domain and switch tabs', async ({ page }, testInfo) => {
    const fixture = readFixture();
    const errors = collectBrowserErrors(page);

    await loginTeacher(page, fixture);

    for (const item of domainPages) {
      await page.goto(item.path, { waitUntil: 'domcontentloaded' });
      await page.waitForLoadState('networkidle').catch(() => undefined);
      await expect(page.locator('.manage-layout')).toHaveAttribute('data-manage-domain', item.domain);
      await expect(page.locator('.manage-main')).toContainText(item.title);
      await expect(page.locator(`.manage-domain-tab[data-domain-tab="${item.domain}"]`)).toHaveClass(/is-active/);
    }

    await page.locator('.manage-domain-tab[data-domain-tab="academic"]').click();
    await expect(page.locator('.manage-layout')).toHaveAttribute('data-manage-domain', 'academic');
    await expect(page.locator('[data-manage-domain="academic"] .manage-nav-item').first()).toBeVisible();

    await expectNoBrowserErrors(errors, testInfo);
  });

  test('legacy management URLs redirect to their canonical domain paths', async ({ page }) => {
    const fixture = readFixture();

    await loginTeacher(page, fixture);

    for (const [legacyPath, canonicalPath] of legacyRedirects) {
      await page.goto(`${legacyPath}?p03=1`, { waitUntil: 'domcontentloaded' });
      await page.waitForLoadState('networkidle').catch(() => undefined);
      expect(new URL(page.url()).pathname).toBe(canonicalPath);
    }
  });

  test('mobile sidebar exposes the domain tabs without leaking to students', async ({ page }, testInfo) => {
    const fixture = readFixture();
    const errors = collectBrowserErrors(page);

    await page.setViewportSize({ width: 390, height: 844 });
    await loginTeacher(page, fixture);
    await page.goto('/manage/academic', { waitUntil: 'domcontentloaded' });
    await page.locator('.mobile-toggle').click();
    await expect(page.locator('.manage-domain-tabs')).toBeVisible();
    await expect(page.locator('.manage-domain-tab')).toHaveCount(3);

    await page.goto('/logout', { waitUntil: 'domcontentloaded' });
    await loginStudent(page, fixture);
    await page.goto('/dashboard', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('[data-dashboard-root]')).toBeVisible();
    await expect(page.locator('.manage-domain-tabs')).toHaveCount(0);

    await expectNoBrowserErrors(errors, testInfo);
  });
});
