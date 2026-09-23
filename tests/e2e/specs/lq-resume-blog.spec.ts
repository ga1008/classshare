import { test, expect, type Page } from '@playwright/test';
import { readFixture, loginStudent, loginTeacher } from '../fixtures/p03';

async function prepare(page: Page, role: 'student' | 'teacher') {
  const fixture = readFixture();
  expect(fixture.runtimeRoot).toContain('lq-resume-blog-20260923');
  const health = await page.request.get('/api/internal/health');
  expect((await health.json()).database_path).toBe(fixture.databasePath);
  await (role === 'student' ? loginStudent(page, fixture) : loginTeacher(page, fixture));
  await page.goto(role === 'student' ? '/profile?section=appearance' : '/manage/me/appearance');
  const context = await page.locator('body').getAttribute('data-ui-palette-context');
  const current = await (await page.request.get('/api/profile/ui-preferences')).json();
  const result = await page.request.patch('/api/profile/ui-preferences', {
    headers: { 'X-UI-Preferences-Context': context || '' },
    data: { appearance: 'dark', backdrop: 'scene', version: current.preferences.version },
  });
  expect(result.ok()).toBeTruthy();
}

for (const width of [1440, 390]) test(`resume material and mobile navigation ${width}`, async ({ page }, info) => {
  await page.setViewportSize({ width, height: 900 });
  await prepare(page, 'student');
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  for (const route of ['/resume', '/resume/profile/personal', '/resume/profile/education', '/resume/job-targets', '/resume/applications', '/resume/builder']) {
    expect((await page.goto(route))?.status(), route).toBe(200);
    await expect(page.locator('[data-lq-page-backdrop]')).toHaveCount(1);
    await expect(page.locator('.rz-topbar')).toHaveAttribute('data-lq-material', 'chrome');
    expect(await page.evaluate(() => document.documentElement.scrollWidth), route).toBeLessThanOrEqual(width);
    if (width === 390) {
      const toggle = page.locator('#rzSidebarToggle');
      await toggle.click();
      await expect(toggle).toHaveAttribute('aria-expanded', 'true');
      await expect(page.locator('#rzSidebar')).toBeVisible();
      expect(await page.locator('#rzSidebar').evaluate(node => node.contains(document.activeElement))).toBe(true);
      await page.keyboard.press('Escape');
      await expect(toggle).toBeFocused();
      await expect(toggle).toHaveAttribute('aria-expanded', 'false');
      await toggle.click();
      await page.locator('.rz-sidebar-scrim').click({ position: { x: 350, y: 300 } });
      await expect(toggle).toHaveAttribute('aria-expanded', 'false');
    }
    await page.screenshot({ path: info.outputPath(route.replace(/\W+/g, '_') + '.png') });
  }
  expect(errors).toEqual([]);
});

for (const role of ['student', 'teacher'] as const) test(`blog preserves filters and content controls for ${role}`, async ({ page }, info) => {
  await page.setViewportSize({ width: 390, height: 900 });
  await prepare(page, role);
  expect((await page.goto('/blog'))?.status()).toBe(200);
  await expect(page.locator('.blog-header')).toHaveAttribute('data-lq-material', 'content');
  await expect(page.locator('[data-lq-page-backdrop]')).toHaveCount(1);
  await page.locator('[data-blog-nav="bookmarks"]').click();
  await expect(page.locator('[data-blog-nav="bookmarks"]')).toHaveClass(/is-active/);
  await page.locator('[data-blog-nav="feed"]').click();
  await expect(page.locator('[data-blog-nav="feed"]')).toHaveClass(/is-active/);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await page.screenshot({ path: info.outputPath('blog-dark-mobile.png') });
  await page.locator('[data-blog-header] [data-blog-action="compose"]').click();
  await expect(page.locator('.blog-modal')).toBeVisible();
  await expect(page.locator('.blog-modal')).toHaveAttribute('data-lq-material', 'raised');
  const bounds = await page.locator('.blog-modal').boundingBox();
  expect(bounds!.x).toBeGreaterThanOrEqual(0);
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(390);
  await page.locator('[data-blog-action="close-composer"]').click();
  await expect(page.locator('[data-blog-composer-modal]')).toBeHidden();
});
