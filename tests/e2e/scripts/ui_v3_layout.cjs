// Diagnose first-load layout stability on the isolated, real application.
const { chromium, expect } = require('@playwright/test');
const fs = require('node:fs');
const path = require('node:path');
const [runtimeArg, baseURL, outputArg] = process.argv.slice(2);
if (!runtimeArg || !baseURL || !outputArg) throw new Error('runtime, base URL and output file required');
const fixture = JSON.parse(fs.readFileSync(path.join(path.resolve(runtimeArg), 'fixture.json'), 'utf8'));
if (!fixture.uiV3Synthetic || new URL(baseURL).hostname !== '127.0.0.1') throw new Error('Synthetic loopback runtime required');
(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const results = [];
  try {
    for (const role of ['student', 'teacher']) {
      const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
      const page = await context.newPage();
      await page.addInitScript(() => {
        window.__layout = { cls: 0, shifts: [] };
        new PerformanceObserver(list => {
          for (const entry of list.getEntries()) {
            if (entry.hadRecentInput) continue;
            window.__layout.cls += entry.value;
            window.__layout.shifts.push({ value: entry.value, time: entry.startTime, sources: entry.sources.map(source => ({
              node: source.node ? [source.node.tagName, source.node.id, source.node.className].join(' ') : null,
              previous: source.previousRect.toJSON(), current: source.currentRect.toJSON(),
            })) });
          }
        }).observe({ type: 'layout-shift', buffered: true });
      });
      await page.goto(`${baseURL}/${role}/login`);
      await page.locator(role === 'student' ? '#identifier' : '#email').fill(role === 'student' ? fixture.student.studentNumber : fixture.teacher.email);
      await page.locator('#password').fill(fixture.password);
      await Promise.all([page.waitForURL(/\/dashboard(?:\?|$)/), page.locator(role === 'student' ? '#student-password-login-form button[type=submit]' : 'button[type=submit]').click()]);
      const onboarding = page.locator('[data-teacher-onboarding-dismiss]').first();
      if (await onboarding.isVisible().catch(() => false)) await onboarding.click();
      for (const name of ['home', 'classroom']) {
        for (const width of [1024, 390, 320]) {
          await page.setViewportSize({ width, height: 900 });
          const response = await page.goto(`${baseURL}${name === 'home' ? '/dashboard' : `/classroom/${fixture.classOfferingId}`}`);
          await page.waitForLoadState('networkidle');
          await expect(page.locator('main:visible').first()).toBeVisible();
          results.push({ role, name, width, status: response.status(), ...await page.evaluate(() => window.__layout) });
        }
      }
      await context.close();
    }
  } finally {
    await browser.close();
    fs.mkdirSync(path.dirname(path.resolve(outputArg)), { recursive: true });
    fs.writeFileSync(outputArg, JSON.stringify(results, null, 2));
  }
  console.log(JSON.stringify(results.map(({role,name,width,status,cls})=>({role,name,width,status,cls})), null, 2));
})().catch(error => { console.error(error); process.exitCode = 1; });
