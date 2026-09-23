import { test, expect, readS3Fixture } from '../fixtures/lq-s3';
import { loginStudent } from '../fixtures/p03';

test('student assessment material stays usable across appearances and viewport sizes', async ({ page }, info) => {
  const fixture = readS3Fixture();
  await loginStudent(page, fixture);
  for (const appearance of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: appearance });
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: 900 });
      for (const [name, route, panel] of [
        ['homework', `/assignment/${fixture.s3.draftAssignmentId}`, '.workspace-container > .card'],
        ['exam', `/exam/take/${fixture.s3.examTakeAssignmentId}`, '.exam-paper-container'],
      ]) {
        const response = await page.goto(route);
        expect(response?.status()).toBe(200);
        await expect(page.locator(panel).first()).toBeVisible();
        await expect(page.locator('[data-lq-page-backdrop]')).toHaveCount(1);
        expect(await page.locator(panel).first().getAttribute('data-lq-material')).toBe('content');
        const metrics = await page.evaluate((selector) => {
          const target = document.querySelector(selector)!;
          const style = getComputedStyle(target);
          return { width: document.documentElement.scrollWidth, viewport: innerWidth,
            image: style.backgroundImage, fill: style.getPropertyValue('--lq-material-fill').trim(),
            radius: parseFloat(style.borderRadius), appearance: document.documentElement.dataset.appearance };
        }, panel);
        expect(metrics.width).toBeLessThanOrEqual(metrics.viewport + 1);
        expect(metrics.radius).toBeGreaterThanOrEqual(14);
        expect(metrics.appearance).toBe(appearance);
        expect(metrics.fill).not.toBe('');
        await page.screenshot({ path: info.outputPath(`${name}-${appearance}-${width}.png`), fullPage: true });
      }
    }
  }
});

test('exam organize menu uses shared keyboard and focus behavior without clipping on mobile', async ({ page }) => {
  const fixture = readS3Fixture();
  await loginStudent(page, fixture);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`/exam/take/${fixture.s3.examFailureAssignmentId}`);
  const trigger = page.locator('#examMoreTrigger');
  await expect(trigger).toBeVisible();
  await trigger.focus();
  await trigger.press('ArrowDown');
  const menu = page.locator('#examMoreMenu');
  await expect(menu).toBeVisible();
  await expect(page.locator('#topbarClearPageBtn')).toBeFocused();
  await page.keyboard.press('ArrowDown');
  await expect(page.locator('#topbarClearAllBtn')).toBeFocused();
  const rect = await menu.boundingBox();
  expect(rect).not.toBeNull();
  expect(rect!.x).toBeGreaterThanOrEqual(0);
  expect(rect!.x + rect!.width).toBeLessThanOrEqual(390);
  await page.keyboard.press('Escape');
  await expect(menu).toBeHidden();
  await expect(trigger).toBeFocused();
  await page.locator('#topbarSidebarToggle').click();
  await expect(page.locator('#topbarSidebarToggle')).toHaveAttribute('aria-expanded', 'true');
  await page.keyboard.press('Escape');
  await expect(page.locator('#topbarSidebarToggle')).toHaveAttribute('aria-expanded', 'false');
  await expect(page.locator('#topbarSidebarToggle')).toBeFocused();
});

test.describe('touch assessment materials', () => {
  test.use({ hasTouch: true, isMobile: true, viewport: { width: 390, height: 844 } });
  test('touch chrome uses shared frost and respects glass opt-out', async ({ page }) => {
    const fixture = readS3Fixture();
    await loginStudent(page, fixture);
    await page.goto(`/exam/take/${fixture.s3.examFailureAssignmentId}`);
    expect(await page.evaluate(() => matchMedia('(pointer: coarse)').matches)).toBe(true);
    const topbar = page.locator('#examTopbar');
    expect(await topbar.evaluate(node => getComputedStyle(node).backdropFilter)).toContain('blur(');
    await page.evaluate(() => document.documentElement.dataset.lqGlass = 'off');
    expect(await topbar.evaluate(node => getComputedStyle(node).backdropFilter)).toBe('none');
    expect(await page.locator('.exam-paper-container').evaluate(node => getComputedStyle(node).backgroundImage)).toBe('none');
  });
});
