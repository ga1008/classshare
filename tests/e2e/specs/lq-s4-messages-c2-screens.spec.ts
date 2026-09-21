import { test } from '@playwright/test';
import { readFixture, loginStudent, loginTeacher } from '../fixtures/p03';

for (const role of ['student', 'teacher'] as const) {
  for (const width of [1440, 390]) {
    for (const appearance of ['light', 'dark'] as const) {
      test(`shot-${role}-${width}-${appearance}`, async ({ page }) => {
        const fixture = readFixture();
        await page.setViewportSize({ width, height: 900 });
        if (role === 'teacher') await loginTeacher(page, fixture); else await loginStudent(page, fixture);
        await page.evaluate((mode) => { document.documentElement.setAttribute('data-appearance', mode); }, appearance);
        const path = role === 'teacher' ? '/manage/me/notifications' : '/profile?section=notifications';
        await page.goto(path);
        await page.evaluate((mode) => { document.documentElement.setAttribute('data-appearance', mode); }, appearance);
        await page.waitForTimeout(400);
        await page.screenshot({ path: `.codex-temp/claude-s4-c2-e2e/screens/${role}-notifications-${width}-${appearance}-familyoff.png`, fullPage: true });
        const privatePath = role === 'teacher' ? '/manage/me/private' : '/profile?section=private';
        await page.goto(privatePath);
        await page.evaluate((mode) => { document.documentElement.setAttribute('data-appearance', mode); }, appearance);
        await page.waitForTimeout(400);
        await page.screenshot({ path: `.codex-temp/claude-s4-c2-e2e/screens/${role}-private-${width}-${appearance}-familyoff.png`, fullPage: true });
      });
    }
  }
}
