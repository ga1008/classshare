import { expect, test } from '@playwright/test';
import {
  collectBrowserErrors,
  expectNoBrowserErrors,
  loginStudent,
  loginTeacher,
  readFixture,
} from '../fixtures/p03';

// 六域壳（docs/manage-center-improvement-plan-2026-09-11.md §5）：
// 首页 / 教学 / 资源库 / 成绩与归档 / 教务 / 我的，超管另有「平台」。
const domainPages = [
  { path: '/manage/teaching', domain: 'teaching', title: '课堂管理' },
  { path: '/manage/library', domain: 'library', title: '材料检索' },
  { path: '/manage/archive', domain: 'archive', title: '成绩与归档' },
  { path: '/manage/academic', domain: 'academic', title: '教务日程' },
  { path: '/manage/me', domain: 'me', title: '我的概览' },
];

const legacyRedirects = [
  ['/manage/offerings', '/manage/teaching/offerings'],
  ['/manage/classrooms', '/manage/academic/classrooms'],
  ['/manage/gongwen', '/manage/academic/gongwen'],
  ['/manage/signatures', '/manage/me/signatures'],
  ['/manage/system/password-resets', '/manage/me/password-resets'],
  ['/manage/teaching/exams', '/manage/library/exams'],
  ['/manage/teaching/ordinary-grade-records', '/manage/archive/ordinary-grade-records'],
  ['/manage/teaching/smart-classroom-integrations', '/manage/academic/smart-classroom'],
] as const;

test.describe('P03 teacher app shell (six domains)', () => {
  test('teacher can open each domain and the sidebar accordion follows the page', async ({ page }, testInfo) => {
    const fixture = readFixture();
    const errors = collectBrowserErrors(page);

    await loginTeacher(page, fixture);

    for (const item of domainPages) {
      await page.goto(item.path, { waitUntil: 'domcontentloaded' });
      await page.waitForLoadState('networkidle').catch(() => undefined);
      await expect(page.locator('.manage-layout')).toHaveAttribute('data-manage-domain', item.domain);
      await expect(page.locator('.manage-main')).toContainText(item.title);
      const domainSection = page.locator(`.manage-nav-domain[data-nav-domain="${item.domain}"]`);
      await expect(domainSection).toHaveClass(/is-open/);
      await expect(domainSection.locator('.manage-nav-item.active')).toHaveCount(1);
    }

    // 普通教师：六个域（含首页），没有平台域；没有域 Tab。
    await expect(page.locator('.manage-nav-domain')).toHaveCount(6);
    await expect(page.locator('.manage-nav-domain[data-nav-domain="admin"]')).toHaveCount(0);
    await expect(page.locator('.manage-domain-tab')).toHaveCount(0);

    // 手风琴：打开教务域会收起当前域。
    await page.locator('.manage-nav-domain[data-nav-domain="academic"] .manage-nav-domain-toggle').click();
    await expect(page.locator('.manage-nav-domain[data-nav-domain="academic"]')).toHaveClass(/is-open/);
    await expect(page.locator('.manage-nav-domain[data-nav-domain="me"]')).not.toHaveClass(/is-open/);
    await expect(page.locator('.manage-nav-domain[data-nav-domain="academic"] .manage-nav-item').first()).toBeVisible();

    await expectNoBrowserErrors(errors, testInfo);
  });

  test('super admin gets a seventh 平台 domain scoped to platform tools', async ({ page }, testInfo) => {
    const fixture = readFixture();
    const errors = collectBrowserErrors(page);

    await loginTeacher(page, fixture, fixture.superTeacher);
    await page.goto('/manage/teaching', { waitUntil: 'domcontentloaded' });
    await page.waitForLoadState('networkidle').catch(() => undefined);
    await expect(page.locator('.manage-nav-domain')).toHaveCount(7);

    await page.goto('/manage/system/users', { waitUntil: 'domcontentloaded' });
    await page.waitForLoadState('networkidle').catch(() => undefined);
    await expect(page.locator('.manage-layout')).toHaveAttribute('data-manage-domain', 'admin');
    await expect(page.locator('.manage-nav-domain[data-nav-domain="admin"]')).toHaveClass(/is-open/);
    await expect(page.locator('#manage-domain-admin .manage-nav-item').first()).toBeVisible();

    await expectNoBrowserErrors(errors, testInfo);
  });

  test('legacy management URLs redirect to their canonical domain paths', async ({ page }) => {
    const fixture = readFixture();

    await loginTeacher(page, fixture);

    for (const [legacyPath, canonicalPath] of legacyRedirects) {
      await page.goto(`${legacyPath}?p03=1`, { waitUntil: 'domcontentloaded' });
      await page.waitForLoadState('networkidle').catch(() => undefined);
      expect(new URL(page.url()).pathname).toBe(canonicalPath);
      expect(new URL(page.url()).searchParams.get('p03')).toBe('1');
    }
  });

  test('material search keeps its category chips inside the page', async ({ page }, testInfo) => {
    const fixture = readFixture();
    const errors = collectBrowserErrors(page);

    await loginTeacher(page, fixture);
    await page.goto('/manage/library', { waitUntil: 'domcontentloaded' });
    await page.waitForLoadState('networkidle').catch(() => undefined);
    const rail = page.locator('#libraryCategoryRail');
    await expect(rail).toBeVisible();
    await expect(page.locator('.manage-sidebar #libraryCategoryRail')).toHaveCount(0);
    const chips = rail.locator('[data-cat-key]');
    expect(await chips.count()).toBeGreaterThan(10);
    await rail.locator('[data-cat-action="none"]').click();
    await expect(chips.first()).not.toHaveClass(/is-checked/);

    await expectNoBrowserErrors(errors, testInfo);
  });

  test('mobile sidebar exposes the domain accordion without leaking to students', async ({ page }, testInfo) => {
    const fixture = readFixture();
    const errors = collectBrowserErrors(page);

    await page.setViewportSize({ width: 390, height: 844 });
    await loginTeacher(page, fixture);
    await page.goto('/manage/academic', { waitUntil: 'domcontentloaded' });
    await page.locator('.mobile-toggle').click();
    await expect(page.locator('.manage-nav-domain-toggle').first()).toBeVisible();
    await expect(page.locator('.manage-nav-domain')).toHaveCount(6);

    await page.goto('/logout', { waitUntil: 'domcontentloaded' });
    await loginStudent(page, fixture);
    await page.goto('/dashboard', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('[data-dashboard-root]')).toBeVisible();
    await expect(page.locator('.manage-nav-domain')).toHaveCount(0);

    await expectNoBrowserErrors(errors, testInfo);
  });
});
