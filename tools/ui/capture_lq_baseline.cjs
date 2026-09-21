// Page capture against an asserted synthetic loopback server. Optional S1
// preference modes mutate only the fixture account and restore its fields.
const { chromium, expect } = require('@playwright/test');
const fs = require('node:fs');
const path = require('node:path');

const [runtimeArg, baseURL, outputArg] = process.argv.slice(2);
if (!runtimeArg || !baseURL || !outputArg) throw new Error('Usage: node tools/ui/capture_lq_baseline.cjs RUNTIME URL OUTPUT');
const runtime = path.resolve(runtimeArg);
const fixture = JSON.parse(fs.readFileSync(path.join(runtime, 'fixture.json'), 'utf8'));
if (!fixture.uiV3Synthetic || new URL(baseURL).hostname !== '127.0.0.1') throw new Error('Synthetic loopback runtime required');
const output = path.resolve(outputArg);
fs.mkdirSync(output, { recursive: true });
const requestedPreferences = {};
const requestedRoles = process.argv.find(arg => arg.startsWith('--roles='))?.slice(8).split(',') || ['anonymous', 'teacher', 'student'];
if (!requestedRoles.length || new Set(requestedRoles).size !== requestedRoles.length || requestedRoles.some(role => !['anonymous', 'teacher', 'student'].includes(role))) throw new Error('Invalid --roles');
for (const [option, field, values] of [
  ['palette', 'palette_key', ['teal', 'indigo', 'sky', 'mint', 'violet', 'rose']],
  ['appearance', 'appearance', ['auto', 'light', 'dark']], ['glass', 'glass', ['tinted', 'off']],
]) {
  const argument = process.argv.find(arg => arg.startsWith(`--${option}=`));
  if (argument) {
    const value = argument.slice(option.length + 3);
    if (!values.includes(value)) throw new Error(`Invalid --${option}`);
    requestedPreferences[field] = value;
  }
}

const common = [
  ['dashboard', '/dashboard'], ['classroom', `/classroom/${fixture.classOfferingId}`],
  ['profile', '/profile'], ['messages', '/message-center'], ['blog', '/blog'],
];
const teacher = [
  ['classroom-hub', '/manage/teaching/classroom-hub'], ['classes', '/manage/teaching/classes'],
  ['semesters', '/manage/teaching/semesters'], ['offerings', '/manage/teaching/offerings'],
  ['courses', '/manage/library/courses'], ['textbooks', '/manage/library/textbooks'],
  ['exams', '/manage/library/exams'], ['lesson-plans', '/manage/library/lesson-plans'],
  ['assessment-plans', '/manage/archive/assessment-plans'], ['evaluations', '/manage/archive/teacher-evaluations'],
  ['academic', '/manage/academic'], ['schedule', '/manage/academic/course-schedule'],
  ['signatures', '/manage/me/signatures'], ['exam-editor', '/exam/new'],
  ['assignment-teacher', `/assignment/${fixture.teacherReviewAssignmentId}`],
  ['grading', `/submission/${fixture.teacherReviewSubmissionId}`],
];
const student = [
  ['assignment-student', `/assignment/${fixture.studentSubmissionAssignmentId}`],
  ['learning-path', '/learning-path'], ['career-path', '/career-path'],
  ['report-card', '/report-card'], ['achievements', '/achievements'], ['wrong-book', '/wrong-book'],
  ['resume', '/resume'], ['resume-builder', '/resume/builder'],
];

(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const report = { synthetic: true, baseURL, requestedPreferences, preferenceRestorations: [], pages: [], failures: [] };
  try {
    for (const role of requestedRoles) {
      const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: 'reduce' });
      // External media is not deterministic; it is not evidence for image contrast.
      await context.route('**/*', route => {
        const u = new URL(route.request().url());
        return ['127.0.0.1', 'localhost'].includes(u.hostname) || ['data:', 'blob:'].includes(u.protocol) ? route.continue() : route.abort();
      });
      const page = await context.newPage();
      let originalPreferences;
      const fetchPreferences = async () => {
        const response = await context.request.get(`${baseURL}/api/profile/ui-preferences`);
        if (!response.ok()) throw new Error(`Preference GET failed: ${response.status()}`);
        return (await response.json()).preferences;
      };
      const savePreferences = async (changes, current) => {
        const response = await context.request.patch(`${baseURL}/api/profile/ui-preferences`, {
          headers: { 'X-UI-Preferences-Context': current.context_token }, data: { ...changes, version: current.version },
        });
        if (!response.ok()) throw new Error(`Preference PATCH failed: ${response.status()}`);
        return (await response.json()).preferences;
      };
      try {
      const health = await context.request.get(`${baseURL}/api/internal/health`);
      const info = await health.json();
      if (path.resolve(info.database_path) !== path.resolve(fixture.databasePath)) throw new Error('Server is not using this synthetic database');
      if (role !== 'anonymous') {
        await page.goto(`${baseURL}/${role}/login`);
        await page.locator(role === 'teacher' ? '#email' : '#identifier').fill(role === 'teacher' ? fixture.teacher.email : fixture.student.studentNumber);
        await page.locator('#password').fill(fixture.password);
        await Promise.all([
          page.waitForURL(/\/dashboard(?:\?|$)/),
          page.locator(role === 'teacher' ? 'button[type=submit]' : '#student-password-login-form button[type=submit]').click(),
        ]);
        await page.waitForLoadState('networkidle');
        const dismiss = page.locator('[data-teacher-onboarding-dismiss]').first();
        if (await dismiss.isVisible()) {
          await dismiss.click();
          await expect(page.locator('[data-teacher-onboarding-modal]')).toBeHidden();
        }
        if (Object.keys(requestedPreferences).length) {
          originalPreferences = await fetchPreferences();
          await savePreferences(requestedPreferences, originalPreferences);
        }
      }
      let routes = role === 'anonymous' ? [['student-login', '/student/login'], ['teacher-login', '/teacher/login']] : [...common, ...(role === 'teacher' ? teacher : student)];
      routes = [...(process.argv.includes('--only-extra') ? [] : routes), ...(fixture.lqCaptureRoutes?.[role] || [])];
      if (process.argv.includes('--only-overlays')) {
        const material = fixture.visualMaterialIds?.[0];
        if (!material) throw new Error('Overlay capture requires the synthetic visual material');
        routes = role === 'anonymous' ? [] : [
          ['material-reader', `/materials/view/${material}?class_offering_id=${fixture.classOfferingId}`],
          ['ai-workspace', role === 'teacher' ? '/dashboard' : `/classroom/${fixture.classOfferingId}`],
          ...(role === 'teacher' ? [['whiteboard', `/materials/view/${material}`]] : []),
        ];
      }
      const requestedPages = process.argv.find(arg => arg.startsWith('--pages='))?.slice(8).split(',');
      if (requestedPages) routes = routes.filter(([name]) => requestedPages.includes(name));
      for (const [name, url] of routes) {
        for (const width of [1440, 390]) {
          const errors = [];
          const onError = error => errors.push(error.message);
          page.on('pageerror', onError);
          try {
            await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
            const response = await page.goto(baseURL + url, { waitUntil: 'load' });
            // SSR load is not enough for islands/document editors; wait for the
            // actual bootstrap requests to settle, and record a failed capture
            // instead of silently photographing an unfinished loading state.
            await page.waitForLoadState('networkidle', { timeout: 15000 });
            await page.evaluate(() => document.fonts.ready);
            if (role === 'teacher') {
              const dismiss = page.locator('[data-teacher-onboarding-dismiss]').first();
              if (await dismiss.isVisible()) {
                await dismiss.click();
                await expect(page.locator('[data-teacher-onboarding-modal]')).toBeHidden();
              }
            }
            if ((await page.title()) === '操作结果') {
              throw new Error('Expected application page, received a status/error page');
            }
            if (name === 'exam-take') {
              await expect(page.locator('#examTopbar')).toBeVisible();
              await expect(page.locator('#q-q1')).toContainText('说明协议分层的作用。');
              await expect(page.locator('[data-text-input="q1"]')).toBeVisible();
              await expect(page.locator('#topbarSubmitBtn')).toBeEnabled();
            }
            const islandState = await page.evaluate(() => ({
              islands: [...document.querySelectorAll('[data-lanshare-island]')].map(node => ({
                name: node.dataset.lanshareIsland, mounted: node.dataset.reactMounted === 'true',
              })),
              hasVite: Boolean(document.querySelector('script[type="module"][src*="/static/dist/"]')),
            }));
            if (islandState.islands.length && !islandState.hasVite) {
              throw new Error('Island page has no built Vite entry; build the frozen source before capturing it');
            }
            if (name === 'ai-workspace') {
              await expect(page.locator('#ai-chat-fab')).toBeEnabled();
              await page.locator('#ai-chat-fab').click();
              await expect(page.locator('#ai-chat-modal')).toHaveAttribute('aria-hidden', 'false');
              await expect(page.locator('#ai-chat-textarea')).toBeVisible();
            } else if (name === 'whiteboard') {
              await expect(page.locator('#teacher-whiteboard-fab')).toBeVisible({ timeout: 15000 });
              await page.locator('#teacher-whiteboard-fab').click();
              await expect(page.locator('#teacher-whiteboard-root')).toHaveClass(/is-open/);
              await expect(page.locator('#teacher-whiteboard-toolbar')).toBeVisible();
            }
            const result = await page.evaluate(() => ({ title: document.title, url: location.pathname, scrollWidth: document.documentElement.scrollWidth, scrollHeight: document.documentElement.scrollHeight, bodyTextLength: document.body.innerText.length,
              theme: { palette_key: document.documentElement.dataset.uiPalette, appearance: document.documentElement.dataset.appearancePreference, glass: document.documentElement.dataset.glassPreference, resolvedAppearance: document.documentElement.dataset.appearance, resolvedGlass: document.documentElement.dataset.lqGlass } }));
            if (role !== 'anonymous' && Object.entries(requestedPreferences).some(([key, value]) => result.theme[key] !== value)) throw new Error(`SSR theme differs from saved preferences: ${JSON.stringify(result.theme)}`);
            const file = `${role}-${name}-${width}.png`;
            await page.screenshot({ path: path.join(output, file), fullPage: true, animations: 'disabled' });
            const entry = { role, name, route: url, width, status: response.status(), ...result, ...islandState, errors, file };
            report.pages.push(entry);
            // These are registered canonical management/message redirects.
            const canonical = name === 'profile' && role === 'teacher' ? '/manage/me'
              : name === 'messages' ? (role === 'teacher' ? '/manage/me/notifications' : '/profile')
              : url.split('?')[0];
            if (response.status() !== 200 || result.url !== canonical || !result.bodyTextLength) report.failures.push({ role, name, width, reason: 'Unexpected response/redirect/empty body', status: response.status(), actual: result.url });
          } catch (error) {
            report.failures.push({ role, name, width, reason: error.message });
          } finally {
            page.off('pageerror', onError);
            fs.writeFileSync(path.join(output, 'capture.json'), JSON.stringify(report, null, 2));
          }
        }
      }
      } catch (error) {
        report.failures.push({ role, phase: 'session/setup', reason: error.message });
      } finally {
        if (originalPreferences) {
          try {
            const restored = await savePreferences(Object.fromEntries(['palette_key', 'appearance', 'glass'].map(key => [key, originalPreferences[key]])), await fetchPreferences());
            report.preferenceRestorations.push({ role, restored: ['palette_key', 'appearance', 'glass'].every(key => restored[key] === originalPreferences[key]), version: restored.version });
          } catch (error) { report.failures.push({ role, reason: `Preference restore failed: ${error.message}` }); }
        }
        await context.close();
        fs.writeFileSync(path.join(output, 'capture.json'), JSON.stringify(report, null, 2));
      }
    }
  } finally {
    await browser.close();
    fs.writeFileSync(path.join(output, 'capture.json'), JSON.stringify(report, null, 2));
  }
  console.log(JSON.stringify({ pages: report.pages.length, failures: report.failures, output }));
  process.exitCode = report.failures.length ? 1 : 0;
})().catch(error => { console.error(error); process.exitCode = 1; });
