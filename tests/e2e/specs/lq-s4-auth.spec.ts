import { test as base, expect, type BrowserContext, type Page, type TestInfo } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { execFileSync } from 'node:child_process';
import AxeBuilder from '@axe-core/playwright';
import type { P03Fixture } from '../fixtures/p03';

type Role = 'student' | 'teacher';
type Mode = 'js' | 'no-js' | 'module-404';
type AuthFixture = P03Fixture & { uiV3Synthetic: true; authValidation: { synthetic: true; identities: {
  suspended: P03Fixture['student']; unset: P03Fixture['student']; reset: P03Fixture['student'];
  inactiveTeacher: Pick<P03Fixture['teacher'], 'id' | 'email'>;
} } };
const origin = 'http://127.0.0.1:8167';
const expectedGraph = process.env.LQ_S4_AUTH_GRAPH || '673c4cf078930a2397be7f4d713ebaec8e81e9341c2f5c25c5df13ee3f0d8d1f';
const wrongPassword = 'S4-wrong-password-URL-sentinel!';

function fixture(): AuthFixture {
  const expected = path.resolve('.codex-temp/lq-s4-auth-validation');
  if (!process.env.P03_RUNTIME_ROOT || path.resolve(process.env.P03_RUNTIME_ROOT) !== expected) {
    throw Error('Auth validation requires its explicitly owned fresh runtime');
  }
  const value = JSON.parse(fs.readFileSync(path.join(expected, 'fixture.json'), 'utf8')) as AuthFixture;
  if (path.resolve(value.runtimeRoot) !== expected || path.resolve(value.databasePath) !== path.join(expected, 'db/classroom.db')
      || value.uiV3Synthetic !== true || value.authValidation?.synthetic !== true) throw Error('Unexpected fixture identity');
  return value;
}

function rows(sql: string): Record<string, unknown>[] {
  if (!/^SELECT\b/i.test(sql)) throw Error('Read-only observation requires SELECT');
  const code = 'import json,sqlite3,sys\nfrom pathlib import Path\nwith sqlite3.connect(Path(sys.argv[1]).as_uri()+"?mode=ro",uri=True) as c:\n c.row_factory=sqlite3.Row\n print(json.dumps([dict(r) for r in c.execute(sys.argv[2])]))';
  return JSON.parse(execFileSync(path.resolve('venv/Scripts/python.exe'), ['-c', code, fixture().databasePath, sql], { encoding: 'utf8' }));
}

async function guard(context: BrowserContext) {
  const value = fixture();
  const health = await context.request.get(`${origin}/api/internal/health`);
  expect(health.status()).toBe(200);
  expect((await health.json()).database_path).toBe(value.databasePath);
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    return url.origin === origin || ['data:', 'blob:'].includes(url.protocol) ? route.continue() : route.abort();
  });
}

const test = base.extend<{ _authGuard: void }>({
  _authGuard: [async ({ context, baseURL }, use) => {
    expect(baseURL).toBe(origin);
    await guard(context);
    const leaks: string[] = [];
    const foreign: string[] = [];
    const errors: string[] = [];
    context.on('request', request => {
      const url = new URL(request.url());
      if (!['data:', 'blob:'].includes(url.protocol) && url.origin !== origin) foreign.push(url.origin);
      if (decodeURIComponent(url.href).includes(fixture().password) || decodeURIComponent(url.href).includes(wrongPassword)
          || ['password', 'token', 'access_token'].some(key => url.searchParams.has(key))) leaks.push(url.pathname);
    });
    context.on('page', page => page.on('pageerror', error => errors.push(error.message)));
    await use();
    expect(leaks, 'Credentials must never appear in URLs').toEqual([]);
    expect(foreign, 'Browser requests must stay on the exact isolated origin').toEqual([]);
    expect(errors, 'Unexpected JavaScript execution errors').toEqual([]);
  }, { auto: true }],
});

const lock = path.resolve('.codex-temp/lq-s4-auth-validation/.auth-browser.lock');
const lockOwner = JSON.stringify({ pid: process.pid, nonce: crypto.randomUUID() });
test.beforeAll(() => {
  fixture();
  fs.writeFileSync(lock, lockOwner, { flag: 'wx' });
});
test.afterAll(() => {
  if (fs.existsSync(lock) && fs.readFileSync(lock, 'utf8') === lockOwner) fs.unlinkSync(lock);
});

function target(role: Role, suffix: string) {
  return `${role === 'student' ? '/report-card' : '/manage/library/courses'}?auth_probe=${encodeURIComponent(suffix)}&scope=all`;
}
const form = (page: Page, role: Role) => page.locator(role === 'student' ? '#student-password-login-form' : '#teacher-login-form');
const identifier = (page: Page, role: Role) => page.locator(role === 'student' ? '#identifier' : '#email');
const account = (role: Role) => role === 'student' ? fixture().student.studentNumber : fixture().teacher.email;

async function assertAssets(page: Page, info: TestInfo) {
  const css = await page.locator('link[rel="stylesheet"][href*="/assets/"]').evaluateAll(nodes => nodes.map(node => (node as HTMLLinkElement).href));
  expect(css.length).toBeGreaterThan(0);
  for (const url of css) expect(url).toContain(`/assets/${expectedGraph}/`);
  await expect(page.locator('[data-lq-login-card]')).toBeVisible();
  await info.attach('runtime-and-graph', { body: JSON.stringify({ runtime: fixture().runtimeRoot, database: fixture().databasePath, graph: expectedGraph, css }), contentType: 'application/json' });
}

async function fillLogin(page: Page, role: Role, password = fixture().password, login = account(role)) {
  await identifier(page, role).fill(login);
  await form(page, role).locator('#password').fill(password);
  await form(page, role).locator('button[type="submit"]').click();
}

async function expectSession(context: BrowserContext, role: Role) {
  const response = await context.request.get(`${origin}/api/session/my-info`);
  expect(response.status()).toBe(200);
  expect((await response.json()).session_info).toMatchObject({ role, user_id: fixture()[role].id, session_active: true });
  const cookie = (await context.cookies()).find(value => value.name === 'access_token');
  expect(cookie).toMatchObject({ httpOnly: true, sameSite: 'Lax', path: '/' });
}

for (const mode of ['js', 'no-js', 'module-404'] as const) {
  test.describe(mode, () => {
    test.use({ javaScriptEnabled: mode !== 'no-js' });
    for (const role of ['student', 'teacher'] as const) for (const width of [1440, 390]) {
      test(`S4 LQ ${role} ${mode} ${width}: wrong password then retry preserves protected query`, async ({ page, context }, info) => {
        await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
        let blocked = 0;
        if (mode === 'module-404') await context.route(url => url.pathname.endsWith(`/js/${role}_login.js`), route => {
          blocked += 1; return route.fulfill({ status: 404, contentType: 'text/plain', body: 'Synthetic missing login module' });
        });
        const next = target(role, `${mode}-${width}`);
        await page.goto(next);
        await expect(page).toHaveURL(`${origin}/${role}/login?next=${encodeURIComponent(next)}`);
        await expect(form(page, role).locator('[name="next"]')).toHaveValue(next);
        await expect(form(page, role)).toHaveAttribute('method', 'post');
        await expect(form(page, role)).toHaveAttribute('action', `/${role}/login`);
        await assertAssets(page, info);
        if (mode === 'js') await expect(role === 'student' ? page.locator('[data-student-login-root]') : form(page, role)).toHaveAttribute('data-login-mounted', 'true');
        const failed = page.waitForResponse(response => response.request().method() === 'POST' && response.url().endsWith(mode === 'js' && role === 'student' ? '/api/student/login/password' : `/${role}/login`));
        await fillLogin(page, role, wrongPassword);
        const failure = await failed;
        expect(failure.status()).toBe(400);
        expect(failure.headers()['cache-control']).toContain('no-store');
        const feedback = form(page, role).locator('[data-login-feedback]');
        await expect(feedback).toBeVisible();
        await expect(feedback).toContainText('登录失败');
        await expect(feedback).toHaveAttribute('role', 'alert');
        await expect(form(page, role)).toHaveAttribute('aria-describedby', await feedback.getAttribute('id') || 'missing');
        await expect(identifier(page, role)).toHaveValue(account(role));
        await expect(form(page, role).locator('[name="next"]')).toHaveValue(next);
        if (mode !== 'js') await expect(form(page, role).locator('#password')).toHaveValue('');
        if (mode === 'module-404') expect(blocked).toBeGreaterThan(0);
        await page.screenshot({ path: info.outputPath(`${role}-${mode}-${width}-error.png`), fullPage: true });
        if (mode === 'js') {
          const audit = await new AxeBuilder({ page }).include('[data-lq-login-card]').withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze();
          await info.attach('login-error-axe', { body: JSON.stringify({ violations: audit.violations, incomplete: audit.incomplete }), contentType: 'application/json' });
          expect(audit.violations, 'Authentication failure must stay readable and accessible').toEqual([]);
        }
        const succeeded = page.waitForResponse(response => response.request().method() === 'POST' && response.url().endsWith(mode === 'js' && role === 'student' ? '/api/student/login/password' : `/${role}/login`));
        await fillLogin(page, role);
        const success = await succeeded;
        expect(success.status()).toBe(mode === 'js' && role === 'student' ? 200 : 303);
        expect(success.headers()['cache-control']).toContain('no-store');
        await expect(page).toHaveURL(`${origin}${next}`, { timeout: 30000 });
        await expectSession(context, role);
        await page.screenshot({ path: info.outputPath(`${role}-${mode}-${width}-protected.png`), fullPage: false });
      });
    }
  });
}

test.describe('native form state and authorization boundaries', () => {
  test.use({ javaScriptEnabled: false });
  for (const state of ['suspended', 'unset', 'reset'] as const) {
    test(`S4 LQ dedicated student ${state} keeps SSR recovery and no session`, async ({ page, context }, info) => {
      const next = target('student', state);
      await page.goto(`/student/login?next=${encodeURIComponent(next)}`);
      const before = rows('SELECT COUNT(*) AS n FROM student_login_audit_logs');
      const pending = page.waitForResponse(response => response.request().method() === 'POST' && response.url().endsWith('/student/login'));
      await fillLogin(page, 'student', fixture().password, fixture().authValidation.identities[state].studentNumber);
      const response = await pending;
      expect(response.status()).toBe({ suspended: 403, unset: 400, reset: 409 }[state]);
      expect(response.headers()['cache-control']).toContain('no-store');
      await expect(form(page, 'student').locator('[data-login-feedback]')).toContainText({ suspended: '暂不纳入课堂学习', unset: '尚未设置密码', reset: '重新设置密码' }[state]);
      await expect(identifier(page, 'student')).toHaveValue(fixture().authValidation.identities[state].studentNumber);
      await expect(form(page, 'student').locator('#password')).toHaveValue('');
      await expect(form(page, 'student').locator('[name="next"]')).toHaveValue(next);
      expect((await context.cookies()).some(cookie => cookie.name === 'access_token')).toBe(false);
      expect(rows('SELECT COUNT(*) AS n FROM student_login_audit_logs')).toEqual(before);
      await page.screenshot({ path: info.outputPath(`${state}-SSR.png`), fullPage: true });
    });
  }
  test('S4 LQ inactive dedicated teacher is rejected without session', async ({ page, context }) => {
    await page.goto('/teacher/login');
    const pending = page.waitForResponse(response => response.request().method() === 'POST' && response.url().endsWith('/teacher/login'));
    await fillLogin(page, 'teacher', fixture().password, fixture().authValidation.identities.inactiveTeacher.email);
    expect((await pending).status()).toBe(400);
    await expect(form(page, 'teacher').locator('[data-login-feedback]')).toContainText('邮箱或密码错误');
    await expect(form(page, 'teacher').locator('#password')).toHaveValue('');
    expect((await context.cookies()).some(cookie => cookie.name === 'access_token')).toBe(false);
  });

  for (const role of ['student', 'teacher'] as const) {
    test(`S4 LQ ${role} replacement session keeps HTML and API next`, async ({ page, context, browser }) => {
      const next = target(role, 'replacement');
      await page.goto(`/${role}/login?next=${encodeURIComponent(next)}`);
      await fillLogin(page, role);
      await expect(page).toHaveURL(`${origin}${next}`);
      await expectSession(context, role);
      const staleCookies = await context.cookies();
      const replacement = await browser.newContext({ javaScriptEnabled: false, baseURL: origin });
      try {
        await guard(replacement);
        const newPage = await replacement.newPage();
        await newPage.goto(`/${role}/login?next=${encodeURIComponent(next)}`);
        await fillLogin(newPage, role);
        await expect(newPage).toHaveURL(`${origin}${next}`);
        await expectSession(replacement, role);
        const response = await context.request.get(`${origin}/api/session/my-info`, { headers: { Referer: `${origin}${next}` } });
        expect(response.status()).toBe(401);
        const redirect = new URL((await response.json()).redirect_to, origin);
        expect(redirect.pathname).toBe(`/${role}/login`);
        expect(redirect.searchParams.get('next')).toBe(next);
        await context.addCookies(staleCookies);
        await page.goto(next);
        await expect(page).toHaveURL(`${origin}/${role}/login?next=${encodeURIComponent(next)}`);
        await expect(form(page, role).locator('[name="next"]')).toHaveValue(next);
        await fillLogin(page, role);
        await expect(page).toHaveURL(`${origin}${next}`);
        await expectSession(context, role);
      } finally { await replacement.close(); }
    });
  }

  test('S4 LQ actual student/teacher identities cannot cross role boundaries', async ({ page, context }) => {
    await page.goto('/student/login');
    await fillLogin(page, 'student');
    await expect(page).toHaveURL(`${origin}/dashboard`);
    const forbidden = await context.request.get(`${origin}/api/session/active`);
    expect(forbidden.status()).toBe(403);
    const destination = new URL((await forbidden.json()).redirect_to, origin);
    expect(destination.pathname).toBe('/auth/forbidden');
    expect(destination.searchParams.get('required_role')).toBe('teacher');
    await page.goto('/manage/library/courses?auth_probe=denied');
    await expect(page).toHaveURL(/\/auth\/forbidden\?/);
    await expect(page.locator('body')).toContainText('权限');
    await context.clearCookies();
    await page.goto('/teacher/login');
    await fillLogin(page, 'teacher');
    await expect(page).toHaveURL(`${origin}/dashboard`);
    const studentOnly = await context.request.get(`${origin}/api/report-card`);
    expect(studentOnly.status()).toBe(403);
    await expectSession(context, 'teacher');
  });

  test('S4 LQ teacher registration stays closed for GET and empty/full POST without writes', async ({ page, context }) => {
    const before = rows('SELECT COUNT(*) AS n FROM teachers');
    const get = await page.goto('/teacher/register');
    expect(get?.status()).toBe(403);
    expect(get?.headers()['cache-control']).toContain('no-store');
    await expect(page.locator('body')).toContainText('已改为由超管教师统一创建');
    const forms: Record<string, string>[] = [{}, { name: 'Synthetic blocked signup', email: 'blocked-s4@example.test', password: fixture().password }];
    for (const data of forms) {
      const response = await context.request.post(`${origin}/teacher/register`, { form: data });
      expect(response.status()).toBe(403);
      expect(response.headers()['cache-control']).toContain('no-store');
      expect(await response.text()).toContain('只能由超管教师创建');
    }
    expect(rows('SELECT COUNT(*) AS n FROM teachers')).toEqual(before);
  });
});
