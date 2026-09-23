import { test, expect, readS3Fixture } from '../fixtures/lq-s3';
import { loginTeacher, loginStudent } from '../fixtures/p03';
import fs from 'node:fs';

for (const role of ['student', 'teacher'] as const) {
  for (const mobile of [false, true]) {
    for (const appearance of ['light', 'dark'] as const) {
      test(`${role} ${mobile ? 'touch' : 'desktop'} ${appearance}: shared scene and page materials`, async ({ browser, baseURL }) => {
        const fixture = readS3Fixture();
        const context = await browser.newContext({ baseURL, viewport: { width: mobile ? 390 : 1440, height: 900 },
          isMobile: mobile, hasTouch: mobile, reducedMotion: 'reduce' });
        try {
          const page = await context.newPage();
          const errors: string[] = [];
          page.on('pageerror', error => errors.push(error.message));
          await (role === 'student' ? loginStudent : loginTeacher)(page, fixture);
          const prefs = await (await page.request.get('/api/profile/ui-preferences')).json();
          const changed = await page.request.patch('/api/profile/ui-preferences', {
            headers: { 'X-UI-Preferences-Context': prefs.preferences.context_token },
            data: { appearance, backdrop: 'scene', glass: 'tinted', version: prefs.preferences.version },
          });
          expect(changed.status()).toBe(200);
          const routes = role === 'student'
            ? ['/dashboard', `/classroom/${fixture.classOfferingId}`, `/assignment/${fixture.s3.draftAssignmentId}`,
              `/exam/take/${fixture.s3.examDraftAssignmentId}`, '/profile?section=appearance', '/profile?section=notifications',
              '/learning-path', '/points', '/wrong-book', '/feedback-review', '/resume', '/blog', '/report-card']
            : ['/dashboard', `/classroom/${fixture.classOfferingId}`, '/manage/teaching/classes', '/manage/teaching/semesters',
              '/manage/teaching/classroom-hub', '/manage/library/courses', '/manage/library/materials',
              '/manage/library/lesson-plans', '/manage/library/textbooks', '/profile?section=appearance', '/blog'];
          const evidence: unknown[] = [];
          for (const route of routes) {
            const response = await page.goto(route);
            expect.soft(response?.status(), route).toBe(200);
            if (response?.status() !== 200) continue;
            await page.waitForLoadState('networkidle');
            await expect(page.locator('html')).toHaveAttribute('data-appearance', appearance);
            await expect(page.locator('[data-lq-page-backdrop]')).toHaveCount(1);
            await expect(page.locator('[data-lq-page-backdrop]')).toHaveAttribute('data-lq-backdrop-mode', 'scene');
            const measured = await page.evaluate(() => {
              const visible = (el: Element) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && r.bottom > 0 && r.top < innerHeight; };
              const material = '[data-lq-material],.lq-glass,.lq-surface';
              const hosts = [...document.querySelectorAll<HTMLElement>(material)].filter(visible);
              return { overflow: document.documentElement.scrollWidth - innerWidth,
                materialCount: hosts.length,
                liveBlur: hosts.filter(el => getComputedStyle(el).backdropFilter !== 'none').length,
                image: getComputedStyle(document.querySelector('.lq-page-backdrop__image')!).backgroundImage };
            });
            expect.soft(measured.overflow, `${route} horizontal overflow`).toBeLessThanOrEqual(2);
            expect.soft(measured.materialCount, `${route} no material adopted`).toBeGreaterThan(0);
            expect.soft(measured.image, `${route} missing scene`).toContain('url(');
            evidence.push({ route, ...measured });
            const name = `${role}-${mobile ? 'touch' : 'desktop'}-${appearance}-${route.replace(/[^a-z0-9]+/gi, '-')}`;
            fs.mkdirSync('.codex-temp/glass-unified-browser/screens', { recursive: true });
            await page.screenshot({ path: `.codex-temp/glass-unified-browser/screens/${name}.png`, fullPage: false });
          }
          fs.writeFileSync(`.codex-temp/glass-unified-browser/${role}-${mobile}-${appearance}.json`, JSON.stringify(evidence, null, 2));
          expect(errors).toEqual([]);
        } finally { await context.close(); }
      });
    }
  }
}
