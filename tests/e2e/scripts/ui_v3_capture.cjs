// Actual application screenshots; no mockup, route stubs or credential output.
const { chromium, expect } = require('@playwright/test');
const fs = require('node:fs');
const path = require('node:path');

const [runtimeArg, baseURL, outputArg] = process.argv.slice(2);
if (!runtimeArg || !baseURL || !outputArg) throw new Error('runtime, base URL and output directory are required');
const runtime = path.resolve(runtimeArg);
const fixture = JSON.parse(fs.readFileSync(path.join(runtime, 'fixture.json'), 'utf8'));
if (!fixture.uiV3Synthetic || new URL(baseURL).hostname !== '127.0.0.1') throw new Error('Synthetic loopback runtime required');
const output = path.resolve(outputArg);
fs.mkdirSync(output, { recursive: true });

(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const report = { baseURL, synthetic: true, pages: [], interactions: [] };
  try {
    for (const role of ['student', 'teacher']) {
      const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: 'reduce' });
      const page = await context.newPage();
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.addInitScript(() => {
        window.__uiV3Metrics = { cls: 0, lcp: 0 };
        new PerformanceObserver(list => {
          for (const entry of list.getEntries()) if (!entry.hadRecentInput) window.__uiV3Metrics.cls += entry.value;
        }).observe({ type: 'layout-shift', buffered: true });
        new PerformanceObserver(list => {
          for (const entry of list.getEntries()) window.__uiV3Metrics.lcp = entry.startTime;
        }).observe({ type: 'largest-contentful-paint', buffered: true });
      });
      await page.goto(`${baseURL}/${role}/login`);
      await page.locator(role === 'student' ? '#identifier' : '#email').fill(role === 'student' ? fixture.student.studentNumber : fixture.teacher.email);
      await page.locator('#password').fill(fixture.password);
      await Promise.all([
        page.waitForURL(/\/dashboard(?:\?|$)/),
        page.locator(role === 'student' ? '#student-password-login-form button[type=submit]' : 'button[type=submit]').click(),
      ]);
      const onboarding = page.locator('[data-teacher-onboarding-dismiss]').first();
      if (await onboarding.isVisible().catch(() => false)) await onboarding.click();
      if (role === 'student') {
        const select = page.locator('[data-ui-palette-select]');
        if (await select.count() && await select.inputValue() !== 'indigo') {
          const saved = page.waitForResponse(response => response.url().endsWith('/api/profile/ui-preferences') && response.request().method() === 'PATCH');
          await select.selectOption('indigo');
          expect((await saved).status()).toBe(200);
        }
      }
      const routes = [['home', '/dashboard'], ['classroom', `/classroom/${fixture.classOfferingId}`]];
      if (role === 'student') routes.push(['blog', '/blog']);
      for (const [name, url] of routes) {
        for (const width of name === 'blog' ? [1440] : [2560, 1440, 1366, 1024, 390, 320]) {
          await page.setViewportSize({ width, height: 900 });
          const response = await page.goto(baseURL + url);
          await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});
          await expect(page.locator('main:visible').first()).toBeVisible();
          await page.screenshot({ path: path.join(output, `${role}-${name}-${width}.png`), fullPage: true });
          const measurements = await page.evaluate(() => ({
            ...window.__uiV3Metrics,
            width: innerWidth,
            scrollWidth: document.documentElement.scrollWidth,
            title: document.title,
          }));
          report.pages.push({ role, name, width, status: response.status(), ...measurements, errors: [...errors] });
          expect(response.status()).toBe(200);
          expect(measurements.scrollWidth).toBeLessThanOrEqual(width+1);
        }
      }
      if (role === 'student') {
        await page.setViewportSize({ width: 1440, height: 900 });
        await page.goto(`${baseURL}/classroom/${fixture.classOfferingId}`);
        for (const color of ['sky', 'mint', 'violet', 'rose', 'indigo']) {
          const select = page.locator('[data-ui-palette-select]');
          if (!await select.count()) break; // Historical baseline has no palette preference.
          const saved = page.waitForResponse(response => response.url().endsWith('/api/profile/ui-preferences') && response.request().method() === 'PATCH');
          await select.selectOption(color);
          expect((await saved).status()).toBe(200);
          await page.screenshot({ path: path.join(output, `student-classroom-palette-${color}.png`), fullPage: true });
        }
        const card = page.locator(`#teachingTimelineScroll [data-session-id="${fixture.visualSessionIds[1]}"]`);
        await card.click();
        const action = page.locator('#teachingSessionOpenMaterialBtn');
        await expect(action).toBeEnabled();
        for (const width of [1440, 320]) {
          await page.setViewportSize({ width, height: 900 });
          await page.screenshot({ path: path.join(output, `student-lesson-detail-${width}.png`), fullPage: true });
          report.interactions.push({ name: 'lesson-detail', width, button: await action.boundingBox(), errors: [...errors] });
        }
        const opened = page.waitForEvent('popup');
        await action.click();
        const reader = await opened;
        await reader.waitForURL(/\/materials\/(?:view|render-view)\//);
        await reader.setViewportSize({ width: 1440, height: 900 });
        await reader.screenshot({ path: path.join(output, 'student-material-reader.png'), fullPage: true });
        const closed = reader.waitForEvent('close');
        await reader.locator('[data-classroom-reader-return]').click();
        await closed;
        await page.keyboard.press('Escape');
        await page.setViewportSize({ width: 1440, height: 900 });
        await page.locator(`#teachingTimelineScroll [data-session-id="${fixture.visualSessionIds[2]}"]`).click();
        await expect(action).toBeEnabled();
        await action.click();
        await expect(page.locator('.ls-mat-popup')).toBeVisible();
        await page.screenshot({ path: path.join(output, 'student-material-list.png'), fullPage: true });
        await page.locator('.ls-mat-popup [data-close-mat-popup]').click();
        await page.keyboard.press('Escape');
        await page.getByRole('button', { name: '历史作业与考试', exact: true }).click();
        await expect(page.locator('.cw-dialog')).toBeVisible();
        await page.screenshot({ path: path.join(output, 'student-assignment-history.png'), fullPage: true });
      }
      await context.close();
    }
  } finally {
    await browser.close();
    fs.writeFileSync(path.join(output, 'capture.json'), JSON.stringify(report, null, 2));
  }
  console.log(JSON.stringify({ captured: report.pages.length, output, errors: report.pages.reduce((sum, page) => sum + page.errors.length, 0) }));
})().catch(error => { console.error(error); process.exitCode = 1; });
