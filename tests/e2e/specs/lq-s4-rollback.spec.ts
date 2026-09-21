import { test as base, expect, type BrowserContext, type Page, type TestInfo } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { execFileSync } from 'node:child_process';
import AxeBuilder from '@axe-core/playwright';
import { dismissTeacherOnboardingIfOpen, type P03Fixture } from '../fixtures/p03';

type Role = 'student' | 'teacher';
type RollbackFixture = P03Fixture & { uiV3Synthetic: true; authValidation: { synthetic: true };
  rollbackReport: { studentId: number; offeringId: number; publicationId: number;
    assignmentIds: Record<string, number>; expectedMine: (number | null)[] } };
const origin = 'http://127.0.0.1:8167';
const wrongPassword = 'S4-rollback-wrong-URL-sentinel!';
const graph = process.env.LQ_S4_ROLLBACK_GRAPH || JSON.parse(fs.readFileSync('static/assets/manifest.json', 'utf8')).revision;
function fixture(): RollbackFixture {
  const root = path.resolve('.codex-temp/lq-s4-auth-validation');
  if (!process.env.P03_RUNTIME_ROOT || path.resolve(process.env.P03_RUNTIME_ROOT) !== root) throw Error('Explicit owned auth fixture required');
  const value = JSON.parse(fs.readFileSync(path.join(root, 'fixture.json'), 'utf8')) as RollbackFixture;
  if (path.resolve(value.runtimeRoot) !== root || path.resolve(value.databasePath) !== path.join(root, 'db/classroom.db')
      || value.uiV3Synthetic !== true || value.authValidation?.synthetic !== true || !value.rollbackReport) throw Error('Unexpected rollback fixture identity');
  return value;
}
function rows(sql: string, params: unknown[] = []): Record<string, unknown>[] {
  if (!/^SELECT\b/i.test(sql)) throw Error('Only authored SELECT observations are allowed');
  const code = 'import json,sqlite3,sys\nfrom pathlib import Path\nwith sqlite3.connect(Path(sys.argv[1]).as_uri()+"?mode=ro",uri=True) as c:\n c.row_factory=sqlite3.Row\n print(json.dumps([dict(r) for r in c.execute(sys.argv[2],json.loads(sys.argv[3]))]))';
  return JSON.parse(execFileSync(path.resolve('venv/Scripts/python.exe'), ['-c', code, fixture().databasePath, sql, JSON.stringify(params)], { encoding: 'utf8' }));
}
function grades() {
  const report = fixture().rollbackReport, ids = Object.values(report.assignmentIds), placeholders = ids.map(() => '?').join(',');
  return {
    submissions: rows(`SELECT * FROM submissions WHERE assignment_id IN (${placeholders}) ORDER BY id`, ids),
    groups: rows(`SELECT * FROM group_assignment_member_results WHERE assignment_id IN (${placeholders}) ORDER BY student_pk_id`, ids),
    publications: rows('SELECT * FROM grade_publications WHERE id=?', [report.publicationId]),
    students: rows('SELECT * FROM grade_publication_students WHERE publication_id=? ORDER BY student_pk_id', [report.publicationId]),
  };
}
async function guard(context: BrowserContext) {
  const health = await context.request.get(`${origin}/api/internal/health`);
  expect(health.status()).toBe(200);
  expect((await health.json()).database_path).toBe(fixture().databasePath);
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    return url.origin === origin || ['data:', 'blob:'].includes(url.protocol) ? route.continue() : route.abort();
  });
}
const test = base.extend<{ _rollbackGuard: void }>({
  _rollbackGuard: [async ({ context, baseURL }, use) => {
    expect(baseURL).toBe(origin); await guard(context);
    const errors: string[] = [], foreign: string[] = [], leaks: string[] = [];
    context.on('page', page => page.on('pageerror', error => errors.push(error.message)));
    context.on('request', request => {
      const url = new URL(request.url());
      if (!['data:', 'blob:'].includes(url.protocol) && url.origin !== origin) foreign.push(url.origin);
      if ([fixture().password, wrongPassword].some(secret => decodeURIComponent(url.href).includes(secret))
          || ['password', 'token', 'access_token'].some(key => url.searchParams.has(key))) leaks.push(url.pathname);
    });
    await use();
    expect(errors).toEqual([]); expect(foreign).toEqual([]); expect(leaks).toEqual([]);
  }, { auto: true }],
});
// Share the auth ticket's fixture lock: two runners must never rotate these
// synthetic users' sessions concurrently, even though the specs differ.
const lock = path.resolve('.codex-temp/lq-s4-auth-validation/.auth-browser.lock');
const lockOwner = JSON.stringify({ pid: process.pid, nonce: crypto.randomUUID() });
test.beforeAll(() => { fixture(); fs.writeFileSync(lock, lockOwner, { flag: 'wx' }); });
test.afterAll(() => { if (fs.existsSync(lock) && fs.readFileSync(lock, 'utf8') === lockOwner) fs.unlinkSync(lock); });

async function assets(page: Page, info: TestInfo, label: string) {
  const loaded = await page.locator('link[href*="/static/assets/"],script[src*="/static/assets/"]').evaluateAll(nodes => nodes.map(node => node.getAttribute('href') || node.getAttribute('src')));
  expect(loaded.length).toBeGreaterThan(0);
  for (const url of loaded) expect(url).toContain(`/assets/${graph}/`);
  await info.attach(`${label}-flags-and-assets`, { body: JSON.stringify({ runtime: fixture().runtimeRoot, database: fixture().databasePath,
    expectedFamilies: [], expectedPilot: true, graph, loaded }), contentType: 'application/json' });
}
async function oldLogin(page: Page, role: Role, next: string, info: TestInfo, errorRetry = false) {
  await page.goto(`/${role}/login?next=${encodeURIComponent('//outside.invalid/path')}`);
  const form = page.locator(role === 'student' ? '#student-password-login-form' : '#teacher-login-form');
  await expect(form.locator('[name="next"]')).toHaveValue('/dashboard');
  await page.goto(`/${role}/login?next=${encodeURIComponent(next)}`);
  await expect(page.locator('.login-card')).toBeVisible();
  await expect(page.locator('[data-lq-centered],[data-lq-login-card]')).toHaveCount(0);
  await expect(page.locator('body')).not.toHaveClass(/lq-centered-page/);
  await expect(page.locator('link[href$="/css/lq/pages/login.css"],link[href$="/css/lq/pages/status.css"]')).toHaveCount(0);
  await expect(form.locator('#password')).toHaveClass(/form-control/);
  await expect(form).toHaveAttribute('method', 'post');
  await expect(form.locator('[name="next"]')).toHaveValue(next);
  await assets(page, info, `${role}-legacy-login`);
  await expect(role === 'student' ? page.locator('[data-student-login-root]') : form).toHaveAttribute('data-login-mounted', 'true');
  const identity = page.locator(role === 'student' ? '#identifier' : '#email');
  await identity.fill(role === 'student' ? fixture().student.studentNumber : fixture().teacher.email);
  if (errorRetry) {
    await form.locator('#password').fill(wrongPassword);
    const pending = page.waitForResponse(response => response.request().method() === 'POST' && response.url().endsWith(role === 'student' ? '/api/student/login/password' : '/teacher/login'));
    await form.locator('button[type="submit"]').click();
    const failed = await pending; expect(failed.status()).toBe(400); expect(failed.headers()['cache-control']).toContain('no-store');
    await expect(page.getByText(role === 'student' ? '登录失败：账号或密码错误。' : '登录失败：邮箱或密码错误。', { exact: true }).last()).toBeVisible();
    await expect(form.locator('[name="next"]')).toHaveValue(next);
    await page.screenshot({ path: info.outputPath(`${role}-legacy-login-error.png`), fullPage: true });
  }
  await form.locator('#password').fill(fixture().password);
  await form.locator('button[type="submit"]').click();
  await expect(page).toHaveURL(`${origin}${next}`, { timeout: 30000 });
  const session = await page.request.get('/api/session/my-info'); expect(session.status()).toBe(200);
  expect((await session.json()).session_info).toMatchObject({ role, user_id: fixture()[role].id });
  if (role === 'teacher') { await page.waitForLoadState('networkidle'); await dismissTeacherOnboardingIfOpen(page); }
}
async function legacyManage(page: Page, info: TestInfo, label: string) {
  await expect(page.locator('#sidebar.manage-sidebar')).toHaveCount(1);
  await expect(page.locator('.manage-topbar.navbar')).toBeVisible();
  await expect(page.locator('body')).not.toHaveClass(/lq-manage-(shell|pilot)/);
  await expect(page.locator('[data-lq-manage-sidebar],#manage-pilot-topbar')).toHaveCount(0);
  await expect(page.locator('script[src$="/js/manage_lq_pilot.js"],script[src$="/js/navbar_lq.js"]')).toHaveCount(0);
  await assets(page, info, label);
}
async function legacyNavbar(page: Page, info: TestInfo, label: string) {
  await expect(page.locator('header.navbar.app-topbar')).toBeVisible();
  await expect(page.locator('[data-lq-navbar-topbar],[data-lq-navbar-content],#navbar-dock')).toHaveCount(0);
  await expect(page.locator('script[src$="/js/navbar_lq.js"]')).toHaveCount(0);
  await expect(page.locator('[data-app-bottomnav].app-bottomnav')).toHaveCount(1);
  await expect(page.locator('.app-topbar-menu--personal')).toHaveCount(1);
  await assets(page, info, label);
}
async function settleReportAppearance(page: Page, info: TestInfo, appearance: string) {
  // A direct login lands here while the established scene-main-in transition
  // is still fading the whole main area. Axe must inspect the settled page,
  // without disabling motion or replacing initial-state/CLS observations.
  const observe = () => page.evaluate(() => {
    const roots = [...document.querySelectorAll('[data-lq-report-card], [data-lq-report-card-topbar]')];
    const active = document.getAnimations().filter(animation => {
      const effect = animation.effect;
      if (!(effect instanceof KeyframeEffect) || !(effect.target instanceof Element)
          || !Number.isFinite(effect.getComputedTiming().endTime)) return false;
      const target = effect.target;
      return roots.some(root => root.contains(target) || target.contains(root))
        && (animation.pending || !['finished', 'idle'].includes(animation.playState));
    }).map(animation => ({ name: 'animationName' in animation ? animation.animationName : 'transition', state: animation.playState }));
    return { sceneActive: document.documentElement.matches('.has-scene-cover,.scene-cover-collapsing'),
      fonts: document.fonts.status, mainOpacity: getComputedStyle(document.querySelector('.main-content')!).opacity, active };
  });
  const before = await observe();
  await page.evaluate(() => document.fonts.ready.then(() => undefined));
  await expect.poll(async () => {
    const state = await observe();
    return !state.sceneActive && state.fonts === 'loaded' && state.mainOpacity === '1' && state.active.length === 0;
  }, { timeout: 3000, intervals: [16, 32, 64], message: 'The report scene entrance and finite appearance transitions must finish before axe' }).toBe(true);
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
  await info.attach(`report-${appearance}-settled`, { body: JSON.stringify({ before, after: await observe() }), contentType: 'application/json' });
}

for (const width of [1440, 390]) {
  test(`S4 rollback teacher centered/manage flags off preserves old navigation at ${width}`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: 900 });
    await oldLogin(page, 'teacher', `/dashboard?rollback_probe=teacher-${width}`, info, true);
    await legacyManage(page, info, 'teacher-dashboard');
    await page.screenshot({ path: info.outputPath(`teacher-dashboard-${width}.png`), fullPage: false });
    if (width < 1024) { await page.locator('.mobile-toggle').click(); await expect(page.locator('#sidebar')).toHaveClass(/open/); }
    const me = page.locator('#manage-domain-me .manage-nav-domain-toggle');
    if (await me.getAttribute('aria-expanded') !== 'true') await me.click();
    await page.locator('#manageNav a[href="/manage/me"]').click();
    await expect(page).toHaveURL(`${origin}/manage/me`); await legacyManage(page, info, 'teacher-profile');
    await page.screenshot({ path: info.outputPath(`teacher-profile-${width}.png`), fullPage: false });
    // S3 is a separate flag and must remain enabled on its exact route.
    await page.goto('/manage/library/courses');
    await expect(page.locator('body')).toHaveClass(/lq-manage-pilot/);
    await expect(page.locator('#manage-pilot-topbar')).toHaveAttribute('data-lq-enhanced', 'true');
    await expect(page.locator('script[src$="/js/manage_lq_pilot.js"]')).toHaveCount(1);
    await page.goto(`/profile?rollback_probe=teacher-${width}`);
    await expect(page).toHaveURL(`${origin}/manage/me?rollback_probe=teacher-${width}`);
    await legacyManage(page, info, 'teacher-profile-redirect');
  });

  test(`S4 rollback student centered/navbar flags off preserves old navigation at ${width}`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: 900 });
    await oldLogin(page, 'student', `/dashboard?rollback_probe=student-${width}`, info, true);
    await legacyNavbar(page, info, 'student-dashboard');
    await page.screenshot({ path: info.outputPath(`student-dashboard-${width}.png`), fullPage: false });
    if (width < 768) {
      await expect(page.locator('[data-app-bottomnav]')).toBeVisible();
      await page.locator('[data-app-bottomnav] a[href="/profile"]').click();
    } else {
      await page.locator('.app-topbar-menu--personal > summary').click();
      await page.getByRole('menuitem', { name: '打开个人中心', exact: true }).click();
    }
    await expect(page).toHaveURL(`${origin}/profile`); await legacyNavbar(page, info, 'student-profile');
    await page.screenshot({ path: info.outputPath(`student-profile-${width}.png`), fullPage: false });
    await page.locator('.app-topbar-brand').click();
    await expect(page).toHaveURL(`${origin}/dashboard`); await legacyNavbar(page, info, 'student-return');
  });

  test(`S4 rollback keeps independent S3 report and shared topbar contract at ${width}`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: 900 });
    const data = fixture(), report = data.rollbackReport, before = grades();
    const url = `/report-card?class_offering_id=${report.offeringId}`;
    await oldLogin(page, 'student', url, info);
    await expect(page.locator('[data-lq-navbar-topbar],#navbar-dock')).toHaveCount(0);
    await expect(page.locator('[data-lq-report-card-topbar]')).toHaveAttribute('data-lq-enhanced', 'true');
    await expect(page.locator('[data-lq-report-card-topbar]')).toHaveCount(1);
    await expect(page.locator('script[src$="/js/navbar_lq.js"]')).toHaveCount(0);
    await expect(page.locator('script[src$="/js/report_card.js"]')).toHaveCount(1);
    await assets(page, info, 'S3-report-retained');
    const result = await page.request.get(`/api/report-card?class_offering_id=${report.offeringId}&student_id=${data.otherStudent.id}`);
    expect(result.status()).toBe(200);
    const card = (await result.json()).report_card;
    expect(card.charts).toHaveLength(1); expect(card.charts[0].mine).toEqual(report.expectedMine);
    expect(card.published_grades).toHaveLength(1); expect(card.published_grades[0].overall_score).toBe(0);
    expect(JSON.stringify(card)).not.toContain(data.otherStudent.studentNumber);
    const view = page.locator('[data-lq-report-card]');
    for (const text of ['未提交，教师记 0', '等待小组揭晓', '已退回待重交', '重批中 · 原有效分']) await expect(view).toContainText(text);
    await expect(page.getByRole('region', { name: '已公布课程成绩' })).toContainText(/总评\s+0(?:\.0+)?\s+分/);
    const chart = view.locator('[data-report-chart]'); await expect(chart).toHaveCount(1);
    await expect.poll(() => chart.evaluate(element => !!(window as any).echarts?.getInstanceByDom(element))).toBe(true);
    const initial = await chart.evaluate(element => { const chart = (window as any).echarts.getInstanceByDom(element); return { id: chart.id, mine: chart.getOption().series[0].data }; });
    expect(initial.mine).toEqual(report.expectedMine);
    const preferences = await (await page.request.get('/api/profile/ui-preferences')).json();
    const mutations: string[] = [];
    page.on('request', request => { if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method())) mutations.push(`${request.method()} ${new URL(request.url()).pathname}`); });
    for (const appearance of ['light', 'dark']) {
      await page.evaluate(appearance => (document as any)[Symbol.for('lanshare.theme.installation')].refresh({ palette_key: 'indigo', appearance, glass: 'tinted' }), appearance);
      await expect(page.locator('html')).toHaveAttribute('data-appearance', appearance);
      await settleReportAppearance(page, info, appearance);
      expect(await chart.evaluate(element => { const chart = (window as any).echarts.getInstanceByDom(element); return { id: chart.id, mine: chart.getOption().series[0].data }; })).toEqual(initial);
      const scan = await new AxeBuilder({ page }).include('[data-lq-report-card-topbar]').include('[data-lq-report-card]').analyze();
      await info.attach(`report-${appearance}-axe`, { body: JSON.stringify({ violations: scan.violations, incomplete: scan.incomplete }), contentType: 'application/json' });
      expect(scan.violations).toEqual([]);
      await page.screenshot({ path: info.outputPath(`report-${appearance}-${width}.png`), fullPage: true });
    }
    if (width < 1024) {
      const trigger = page.locator('[data-lq-report-card-topbar] [data-lq-pane-open="actions"]');
      const pane = page.locator('#report-card-topbar--lq-actions');
      for (let n = 0; n < 2; n++) {
        await trigger.click(); await expect(pane).toBeVisible(); expect(await pane.evaluate(element => element.matches(':modal'))).toBe(true);
        await page.keyboard.press('Escape'); await expect(pane).toBeHidden(); await expect(trigger).toBeFocused();
      }
      await trigger.click(); await pane.locator('[data-ui-preferences-toggle]').click();
      await expect(pane.locator('[data-ui-preferences-panel]')).toBeVisible();
      await pane.locator('[data-ui-preferences-toggle]').click(); await expect(pane.locator('[data-ui-preferences-panel]')).toBeHidden();
      const securityTrigger = pane.locator('[data-open-student-security]'); await securityTrigger.click();
      const security = page.locator('#student-security-modal'); await expect(security).toBeVisible();
      await security.locator('#current-password').fill('local unsent rollback draft');
      await page.keyboard.press('Escape'); await expect(security).toBeHidden(); await expect(securityTrigger).toBeFocused();
      await pane.locator('[data-open-feedback]').click();
      await expect(pane).toBeHidden(); await expect(page.locator('#feedback-modal')).toBeVisible();
      await page.locator('#feedback-modal [data-feedback-dismiss]').click(); await expect(page.locator('#feedback-modal')).toBeHidden();
      await expect(trigger).toBeFocused();
    }
    const filter = page.getByRole('navigation', { name: '成绩类型筛选' });
    await filter.getByRole('link', { name: '期末测验', exact: true }).click();
    await expect(view).toContainText('当前范围还没有成绩记录'); await expect(chart).toHaveCount(0);
    await expect(page.getByRole('region', { name: '已公布课程成绩' })).toContainText(/总评\s+0(?:\.0+)?\s+分/);
    await filter.getByRole('link', { name: '平时作业', exact: true }).click(); await expect(chart).toHaveCount(1);
    expect(await (await page.request.get('/api/profile/ui-preferences')).json()).toEqual(preferences);
    expect(mutations).toEqual([]); expect(grades()).toEqual(before);
  });
}
