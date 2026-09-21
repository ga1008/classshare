import { test as base, expect, type BrowserContext, type Page, type TestInfo } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { execFileSync } from 'node:child_process';
import AxeBuilder from '@axe-core/playwright';
import type { P03Fixture } from '../fixtures/p03';

type Mode = 'no-js' | 'module-404';
type Student = P03Fixture['student'];
type NativeFixture = P03Fixture & { uiV3Synthetic: true; authNative: {
  synthetic: true; newPassword: string; classFragment: string;
  identities: Record<string, Student>; unauthorizedTeacher: P03Fixture['teacher'];
} };
const origin = `http://127.0.0.1:${process.env.LQ_S4_AUTH_NATIVE_PORT || '8168'}`;
const runtime = path.resolve(process.env.LQ_S4_AUTH_NATIVE_RUNTIME || '.codex-temp/lq-s4-auth-native-validation');
function fixture(): NativeFixture {
  if (!process.env.P03_RUNTIME_ROOT || path.resolve(process.env.P03_RUNTIME_ROOT) !== runtime) throw Error('Native auth requires its new explicitly owned runtime');
  const value = JSON.parse(fs.readFileSync(path.join(runtime, 'fixture.json'), 'utf8')) as NativeFixture;
  if (path.resolve(value.runtimeRoot) !== runtime || path.resolve(value.databasePath) !== path.join(runtime, 'db/classroom.db')
      || value.uiV3Synthetic !== true || value.authNative?.synthetic !== true) throw Error('Unexpected native-auth fixture');
  if (!process.env.LQ_S4_AUTH_NATIVE_GRAPH || !/^[a-f0-9]{64}$/.test(process.env.LQ_S4_AUTH_NATIVE_GRAPH)) throw Error('Explicit final D2 graph is required; no old-graph fallback');
  if (!value.authNative.newPassword || value.authNative.newPassword === value.password) throw Error('Dedicated changed password required');
  return value;
}
function student(key: string): Student {
  const value = fixture().authNative.identities[key];
  if (!value || !Number.isSafeInteger(value.id) || value.id <= 0 || !value.studentNumber.startsWith('LQS4-NATIVE-')) throw Error(`Missing disjoint native identity: ${key}`);
  return value;
}
function rows(sql: string): Record<string, unknown>[] {
  if (!/^SELECT\b/i.test(sql)) throw Error('Only read-only fixture observations permitted');
  const code = 'import json,sqlite3,sys\nfrom pathlib import Path\nwith sqlite3.connect(Path(sys.argv[1]).as_uri()+"?mode=ro",uri=True) as c:\n c.row_factory=sqlite3.Row\n print(json.dumps([dict(r) for r in c.execute(sys.argv[2])]))';
  return JSON.parse(execFileSync(path.resolve('venv/Scripts/python.exe'), ['-c', code, fixture().databasePath, sql], { encoding: 'utf8' }));
}
function auditCount(id: number) { return Number(rows(`SELECT COUNT(*) AS n FROM student_login_audit_logs WHERE student_id=${id}`)[0].n); }
function accountState(id: number) {
  return rows(`SELECT password_reset_required, CASE WHEN hashed_password IS NULL OR hashed_password='' THEN 0 ELSE 1 END AS has_password FROM students WHERE id=${id}`)[0];
}
async function guard(context: BrowserContext) {
  const value = fixture(), foreign: string[] = [], leaks: string[] = [], errors: string[] = [];
  const response = await context.request.get(`${origin}/api/internal/health`);
  expect(response.status()).toBe(200); expect((await response.json()).database_path).toBe(value.databasePath);
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    return url.origin === origin || ['data:', 'blob:'].includes(url.protocol) ? route.continue() : route.abort();
  });
  context.on('request', request => {
    const url = new URL(request.url());
    if (!['data:', 'blob:'].includes(url.protocol) && url.origin !== origin) foreign.push(url.origin);
    if (decodeURIComponent(url.href).includes(value.password) || decodeURIComponent(url.href).includes(value.authNative.newPassword)
        || ['password', 'confirm_password', 'setup_token', 'access_token'].some(key => url.searchParams.has(key))) leaks.push(url.pathname);
  });
  context.on('page', page => page.on('pageerror', error => errors.push(error.message)));
  return () => { expect(foreign).toEqual([]); expect(leaks, 'No credential or token URL').toEqual([]); expect(errors).toEqual([]); };
}
const test = base.extend<{ _nativeGuard: void }>({
  _nativeGuard: [async ({ context, baseURL }, use) => {
    expect(baseURL).toBe(origin); const verify = await guard(context); await use(); verify();
  }, { auto: true }],
});
const lock = path.join(runtime, '.auth-browser.lock'), owner = JSON.stringify({ pid: process.pid, nonce: crypto.randomUUID() });
test.beforeAll(() => { fixture(); fs.writeFileSync(lock, owner, { flag: 'wx' }); });
test.afterAll(() => { if (fs.existsSync(lock) && fs.readFileSync(lock, 'utf8') === owner) fs.unlinkSync(lock); });

async function assertAssets(page: Page, info: TestInfo) {
  const assets = await page.locator('link[href*="/assets/"],script[src*="/assets/"]').evaluateAll(nodes => nodes.map(node => node.getAttribute('href') || node.getAttribute('src') || ''));
  expect(assets.length).toBeGreaterThan(0);
  for (const asset of assets) expect(asset).toContain(`/assets/${process.env.LQ_S4_AUTH_NATIVE_GRAPH}/`);
  await info.attach('native-runtime-graph', { body: JSON.stringify({ runtime, graph: process.env.LQ_S4_AUTH_NATIVE_GRAPH, assets }), contentType: 'application/json' });
}
async function begin(page: Page, mode: Mode, width: number, next: string, info: TestInfo) {
  await page.setViewportSize({ width, height: width === 390 ? 660 : 900 });
  if (mode === 'module-404') await page.route(/\/js\/student_login\.js(?:\?|$)/, route => route.fulfill({ status: 404, body: 'Synthetic module unavailable' }));
  await page.goto(`${origin}/student/login?next=${encodeURIComponent(next)}`);
  await assertAssets(page, info);
  await expect(page.locator('#first-login-switch')).toHaveAttribute('href', /\/student\/login\/identity\?next=/);
  await expect(page.locator('[data-student-login-root]')).not.toHaveAttribute('data-login-mounted', 'true');
}
async function nativeStep(page: Page, step: string) {
  await expect(page.locator('[data-auth-step]')).toHaveAttribute('data-auth-step', step);
  await expect(page.locator('h1')).toHaveCount(1);
  if (step !== 'submitted') await expect(page.locator('.student-auth-flow form')).toHaveAttribute('method', 'post');
  expect(await page.locator('.student-auth-flow [autofocus]').count()).toBe(step === 'submitted' ? 0 : 1);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
}
async function post(page: Page, endpoint: string, expectedStatus: number) {
  const response = page.waitForResponse(value => new URL(value.url()).pathname === endpoint && value.request().method() === 'POST');
  await page.locator('.student-auth-flow button[type="submit"]').click();
  const result = await response; expect(result.status()).toBe(expectedStatus); expect(result.headers()['cache-control']).toBe('no-store');
}
async function identity(page: Page, person: Student) {
  await page.getByLabel('姓名', { exact: true }).fill(person.name);
  await page.getByLabel('学号', { exact: true }).fill(person.studentNumber);
}
async function password(page: Page, value: string, confirmation = value) {
  await page.getByLabel('设置密码', { exact: true }).fill(value);
  await page.getByLabel('确认密码', { exact: true }).fill(confirmation);
}
async function errorFocus(page: Page) {
  await expect(page.locator('#auth-flow-feedback')).toBeFocused();
  await expect(page.locator('#auth-flow-feedback')).toHaveAttribute('role', 'alert');
  await expect(page.locator('form')).toHaveAttribute('aria-describedby', 'auth-flow-feedback');
}
async function checkSession(context: BrowserContext, person: Student) {
  const result = await context.request.get(`${origin}/api/session/my-info`);
  expect(result.status()).toBe(200);
  expect((await result.json()).session_info).toMatchObject({ role: 'student', user_id: person.id, session_active: true });
}
async function teacherLogin(context: BrowserContext, teacher: P03Fixture['teacher']) {
  const result = await context.request.post(`${origin}/teacher/login`, { form: { email: teacher.email, password: fixture().password, next: '/manage/system' }, maxRedirects: 0 });
  expect(result.status()).toBe(303);
  const session = await context.request.get(`${origin}/api/session/my-info`);
  expect((await session.json()).session_info).toMatchObject({ role: 'teacher', user_id: teacher.id });
}

for (const mode of ['no-js', 'module-404'] as const) test.describe(mode, () => {
  test.use({ javaScriptEnabled: mode !== 'no-js' });
  for (const width of [1440, 390]) {
    test(`S4 D2 LQ native first setup ${mode} ${width} preserves source and safe retry`, async ({ page, context }, info) => {
      const person = student(`${mode}-${width}-first`), next = `/report-card?auth_native=${mode}-${width}-first&scope=mine`;
      expect(accountState(person.id)).toEqual({ password_reset_required: 0, has_password: 0 });
      expect(auditCount(person.id)).toBe(0);
      await begin(page, mode, width, next, info);
      await page.locator('#first-login-switch').click(); await nativeStep(page, 'identity');
      await identity(page, { ...person, name: 'Unknown synthetic student' });
      await post(page, '/student/login/identity', 400); await errorFocus(page);
      await expect(page.getByLabel('学号', { exact: true })).toHaveValue(person.studentNumber);
      await identity(page, person); await post(page, '/student/login/identity', 200); await nativeStep(page, 'setup');
      const token = await page.locator('input[name=setup_token]').inputValue();
      expect(Boolean(token)).toBe(true);
      const digest = (value: string) => crypto.createHash('sha256').update(value).digest('hex');
      await password(page, 'short'); await post(page, '/student/password/setup', 400); await errorFocus(page);
      expect(digest(await page.locator('input[name=setup_token]').inputValue())).toBe(digest(token));
      await expect(page.getByLabel('设置密码', { exact: true })).toHaveValue('');
      await expect(page.getByLabel('确认密码', { exact: true })).toHaveValue('');
      await page.screenshot({ path: info.outputPath(`first-error-${mode}-${width}.png`), fullPage: true });
      if (mode === 'module-404') expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
      await password(page, fixture().authNative.newPassword); await post(page, '/student/password/setup', 303);
      await expect(page).toHaveURL(`${origin}${next}`); await checkSession(context, person);
      expect(auditCount(person.id)).toBe(1); expect(accountState(person.id)).toEqual({ password_reset_required: 0, has_password: 1 });
      const replay = await context.request.post(`${origin}/student/password/setup`, { form: { setup_token: token, password: fixture().authNative.newPassword, confirm_password: fixture().authNative.newPassword, next }, maxRedirects: 0 });
      expect(replay.status()).toBe(400); expect((await replay.text()).includes(token)).toBe(false); expect(auditCount(person.id)).toBe(1);
    });

    test(`S4 D2 LQ native forgot real teacher approval then reset ${mode} ${width}`, async ({ page, context, browser }, info) => {
      const person = student(`${mode}-${width}-recovery`), next = `/report-card?auth_native=${mode}-${width}-reset&scope=mine`;
      expect(accountState(person.id)).toEqual({ password_reset_required: 0, has_password: 1 });
      expect(rows(`SELECT id FROM student_password_reset_requests WHERE student_id=${person.id}`)).toEqual([]);
      const stale = await browser.newContext({ baseURL: origin, javaScriptEnabled: false });
      const reviewer = await browser.newContext({ baseURL: origin, javaScriptEnabled: false });
      const unauthorized = await browser.newContext({ baseURL: origin, javaScriptEnabled: false });
      const checks: Array<() => void> = [];
      try {
        for (const extra of [stale, reviewer, unauthorized]) checks.push(await guard(extra));
        const login = await stale.request.post(`${origin}/student/login`, { form: { identifier: person.studentNumber, password: fixture().password, next }, maxRedirects: 0 });
        expect(login.status()).toBe(303); await checkSession(stale, person); const before = auditCount(person.id);
        await begin(page, mode, width, next, info);
        await page.locator('#forgot-password-trigger').click(); await nativeStep(page, 'forgot');
        await identity(page, person); await page.getByLabel('班级名称', { exact: true }).fill('wrong-synthetic-class');
        await post(page, '/student/password/forgot', 400); await errorFocus(page);
        await page.getByLabel('班级名称', { exact: true }).fill(fixture().authNative.classFragment);
        await post(page, '/student/password/forgot', 200); await nativeStep(page, 'submitted');
        await page.screenshot({ path: info.outputPath(`forgot-submitted-${mode}-${width}.png`), fullPage: true });
        const requests = rows(`SELECT id,status,teacher_id FROM student_password_reset_requests WHERE student_id=${person.id}`);
        expect(requests).toHaveLength(1); expect(requests[0]).toMatchObject({ status: 'pending', teacher_id: fixture().teacher.id });
        const requestId = Number(requests[0].id); expect(Number.isSafeInteger(requestId)).toBe(true);
        const duplicate = await context.request.post(`${origin}/student/password/forgot`, { form: { name: person.name, student_id_number: person.studentNumber, class_name: fixture().authNative.classFragment, next }, maxRedirects: 0 });
        expect(duplicate.status()).toBe(400); expect(rows(`SELECT id FROM student_password_reset_requests WHERE student_id=${person.id}`)).toHaveLength(1);
        await teacherLogin(unauthorized, fixture().authNative.unauthorizedTeacher);
        const denied = await unauthorized.request.post(`${origin}/api/manage/system/password-resets/${requestId}/approve`, { form: { review_note: 'Isolated unauthorized attempt' } });
        expect(denied.status()).toBe(404);
        await teacherLogin(reviewer, fixture().teacher);
        const approved = await reviewer.request.post(`${origin}/api/manage/system/password-resets/${requestId}/approve`, { form: { review_note: 'Synthetic identity checked through native workflow' } });
        expect(approved.status()).toBe(200);
        expect(rows(`SELECT status FROM student_password_reset_requests WHERE id=${requestId}`)[0].status).toBe('approved');
        expect(accountState(person.id).password_reset_required).toBe(1);
        expect((await stale.request.get(`${origin}/api/session/my-info`)).status()).toBe(401);
        await page.getByRole('link', { name: '首次登录 / 重置后登录' }).click(); await nativeStep(page, 'identity');
        await identity(page, person); await post(page, '/student/login/identity', 200); await nativeStep(page, 'setup');
        await password(page, fixture().authNative.newPassword, 'different-synthetic-confirmation');
        await post(page, '/student/password/setup', 400); await errorFocus(page);
        await expect(page.getByLabel('设置密码', { exact: true })).toHaveValue('');
        await page.screenshot({ path: info.outputPath(`reset-error-${mode}-${width}.png`), fullPage: true });
        if (mode === 'module-404') expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
        await password(page, fixture().authNative.newPassword); await post(page, '/student/password/setup', 303);
        await expect(page).toHaveURL(`${origin}${next}`); await checkSession(context, person);
        expect(rows(`SELECT status FROM student_password_reset_requests WHERE id=${requestId}`)[0].status).toBe('completed');
        expect(accountState(person.id).password_reset_required).toBe(0); expect(auditCount(person.id)).toBe(before + 1);
      } finally {
        for (const extra of [stale, reviewer, unauthorized]) await extra.close();
        checks.forEach(check => check());
      }
    });
  }
});
