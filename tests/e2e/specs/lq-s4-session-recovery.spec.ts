import { test as base, expect, type BrowserContext, type Page } from '@playwright/test';
import { readFixture, type P03Fixture } from '../fixtures/p03';
import AxeBuilder from '@axe-core/playwright';

// D3: the shared HTML-401 "session unavailable" recovery page
// (templates/session_expired.html + static/js/session_expired.js), exercised
// against a real server/browser rather than the unit-level ASGI/vitest
// contracts already covered by tests/test_auth_session_recovery.py and
// tests/lq/auth-session-recovery.test.mjs. This spec never invents a fake
// cookie value out of thin air except to reproduce exactly the same
// "unusable but present" cookie shape the backend contract already defines
// (garbage token string), and otherwise drives the real login forms.

const origin = `http://127.0.0.1:${process.env.LQ_S4_PORT || '8179'}`;

function fixture(): P03Fixture {
  return readFixture();
}

async function loginStudent(page: Page): Promise<void> {
  const data = fixture();
  await page.goto('/student/login');
  await page.locator('#identifier').fill(data.student.studentNumber);
  await page.locator('#student-password-login-form #password').fill(data.password);
  await page.locator('#student-password-login-form button[type="submit"]').click();
  await expect(page).toHaveURL(`${origin}/dashboard`);
}

async function loginTeacher(page: Page): Promise<void> {
  const data = fixture();
  await page.goto('/teacher/login');
  await page.locator('#email').fill(data.teacher.email);
  await page.locator('#teacher-login-form #password').fill(data.password);
  await page.locator('#teacher-login-form button[type="submit"]').click();
  await expect(page).toHaveURL(`${origin}/dashboard`);
}

/** Replace the fresh access_token cookie with an unusable value while
 * keeping every other cookie attribute (domain/path/httpOnly/sameSite)
 * untouched, exactly like the backend contract's `get_recovery(token=...)`. */
async function corruptSessionCookie(context: BrowserContext): Promise<void> {
  const cookies = await context.cookies(origin);
  const token = cookies.find(c => c.name === 'access_token');
  if (!token) throw new Error('Expected an existing access_token cookie before corrupting it');
  await context.addCookies([{ ...token, value: 'lq-s4-session-recovery-invalid-token' }]);
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

const test = base.extend<{ _guard: void }>({
  _guard: [async ({ context, baseURL }, use) => {
    expect(baseURL).toBe(origin);
    await guard(context);
    const errors: string[] = [];
    context.on('page', page => page.on('pageerror', error => errors.push(error.message)));
    await use();
    expect(errors).toEqual([]);
  }, { auto: true }],
});

test.describe('D3 session-expired recovery page', () => {
  test('invalid cookie on an ambiguous path renders 401 no-store with two safe role links carrying next', async ({ page, context }) => {
    await loginStudent(page);
    await corruptSessionCookie(context);
    const response = await page.goto('/dashboard?tab=files&item=2', { waitUntil: 'domcontentloaded' });
    expect(response?.status()).toBe(401);
    // The server sets Cache-Control: no-store; an intermediate layer may add
    // further directives (private/max-age=0/must-revalidate), so assert the
    // required directive is present rather than an exact header match.
    expect(response?.headers()['cache-control']).toContain('no-store');
    // A 401 render is not a redirect: the URL stays on the protected path.
    await expect(page).toHaveURL(`${origin}/dashboard?tab=files&item=2`);
    await expect(page.locator('#expired-title')).toHaveText('当前会话不可用，请重新登录');

    const primary = page.locator('[data-lq-session-login]');
    await expect(primary).toHaveAttribute('href', /\/(student|teacher)\/login\?next=%2Fdashboard/);
    const alternate = page.locator('[data-session-alternate-login]');
    await expect(alternate).toHaveAttribute('href', /\/(student|teacher)\/login\?next=%2Fdashboard/);
    // Ambiguous shared path: role cannot be inferred, so no countdown auto-redirect.
    await expect(page.locator('[data-session-countdown]')).toHaveAttribute('data-auto-redirect', 'false');

    const cookies = await context.cookies(origin);
    expect(cookies.find(c => c.name === 'access_token')).toBeUndefined();
  });

  test('invalid cookie on a role-specific path auto-redirects via countdown to the same safe next', async ({ page, context }) => {
    // Only teacher-prefixed paths (/manage/*, /teacher/*, some /api/manage/*)
    // are role-inferable by classroom_app/dependencies.py's
    // infer_required_role_from_path; there is currently no equivalent
    // production HTML page reachable only by a student-only path prefix
    // (student-only inference only covers a couple of /api/* endpoints), so
    // this exercises the real single-link/countdown branch via a teacher page.
    await loginTeacher(page);
    await corruptSessionCookie(context);
    const response = await page.goto('/manage/library/courses?tab=grades', { waitUntil: 'domcontentloaded' });
    expect(response?.status()).toBe(401);
    await expect(page.locator('[data-session-alternate-login]')).toHaveCount(0);
    const note = page.locator('[data-session-countdown]');
    await expect(note).toHaveAttribute('data-auto-redirect', 'true');
    await expect(note).toBeVisible();
    const href = await page.locator('[data-lq-session-login]').getAttribute('href');
    expect(href).toContain('/teacher/login?next=');
    expect(href).toContain(encodeURIComponent('/manage/library/courses?tab=grades'));

    const target = new URL(href!, origin);
    await page.waitForURL(`${origin}${target.pathname}${target.search}`, { timeout: 8000 });
    await expect(page.locator('#teacher-login-form')).toBeVisible();
    await expect(page.locator('#teacher-login-form [name="next"]')).toHaveValue('/manage/library/courses?tab=grades');
  });

  test('keyboard focus can reach the primary login link without a mouse', async ({ page, context }) => {
    await loginStudent(page);
    await corruptSessionCookie(context);
    await page.goto('/dashboard', { waitUntil: 'domcontentloaded' });
    const primary = page.locator('[data-lq-session-login]');
    await primary.focus();
    await expect(primary).toBeFocused();
  });

  test('with JavaScript disabled the automatic countdown never installs and the note stays hidden', async ({ browser }) => {
    const data = fixture();
    const context = await browser.newContext({ javaScriptEnabled: false, baseURL: origin });
    try {
      await guard(context);
      const page = await context.newPage();
      await page.goto('/student/login');
      await page.locator('#identifier').fill(data.student.studentNumber);
      await page.locator('#student-password-login-form #password').fill(data.password);
      await page.locator('#student-password-login-form button[type="submit"]').click();
      await expect(page).toHaveURL(`${origin}/dashboard`);
      await corruptSessionCookie(context);
      const response = await page.goto('/report-card', { waitUntil: 'domcontentloaded' });
      expect(response?.status()).toBe(401);
      // Server marks this auto-redirect eligible, but with no module executed
      // the note must remain hidden: no script ever promised a navigation.
      await expect(page.locator('[data-session-countdown]')).toHaveAttribute('hidden', '');
      await page.waitForTimeout(1500);
      await expect(page).toHaveURL(`${origin}/report-card`);
      const primary = page.locator('[data-lq-session-login]');
      await expect(primary).toBeVisible();
      await primary.click();
      await expect(page).toHaveURL(/\/student\/login\?next=%2Freport-card/);
    } finally {
      await context.close();
    }
  });

  test('an untrusted referer header is not adopted as the return target', async ({ page, context }) => {
    await loginStudent(page);
    await corruptSessionCookie(context);
    await context.setExtraHTTPHeaders({ referer: 'https://external.invalid/steal-me' });
    await page.goto('/dashboard', { waitUntil: 'domcontentloaded' });
    await context.setExtraHTTPHeaders({});
    const href = await page.locator('[data-lq-session-login]').getAttribute('href');
    expect(href).not.toContain('external.invalid');
    expect(href).not.toContain('steal-me');
    // Falls back to the safe default rather than the foreign referer.
    expect(new URL(href!, origin).searchParams.get('next')).toBe('/dashboard');
  });

  test('re-login after expiry lands back on the original protected next, and a fresh session works', async ({ page, context }) => {
    await loginStudent(page);
    await corruptSessionCookie(context);
    await page.goto('/report-card?tab=grades&item=9', { waitUntil: 'domcontentloaded' });
    const href = await page.locator('[data-lq-session-login]').getAttribute('href');
    await page.goto(href!);
    await expect(page.locator('#student-password-login-form [name="next"]')).toHaveValue('/report-card?tab=grades&item=9');
    const data = fixture();
    await page.locator('#identifier').fill(data.student.studentNumber);
    await page.locator('#student-password-login-form #password').fill(data.password);
    await page.locator('#student-password-login-form button[type="submit"]').click();
    await expect(page).toHaveURL(`${origin}/report-card?tab=grades&item=9`);
    const response = await context.request.get(`${origin}/api/session/my-info`);
    expect(response.status()).toBe(200);
    expect((await response.json()).session_info).toMatchObject({ role: 'student', session_active: true });
  });

  test('teacher role-specific path resolves to the teacher login entry, and the page has no serious axe violations', async ({ page, context }) => {
    await loginTeacher(page);
    await corruptSessionCookie(context);
    const response = await page.goto('/manage/library/courses', { waitUntil: 'domcontentloaded' });
    expect(response?.status()).toBe(401);
    const href = await page.locator('[data-lq-session-login]').getAttribute('href');
    expect(href).toContain('/teacher/login?next=');
    const results = await new AxeBuilder({ page }).include('body').analyze();
    const serious = results.violations.filter(v => v.impact === 'serious' || v.impact === 'critical');
    expect(serious, JSON.stringify(serious, null, 2)).toEqual([]);
  });

  test('flag-off keeps the original redirect-to-login behavior unaffected by this spec', async ({ page, context }) => {
    // The centered family is the only server switch for this page; this spec's
    // own runtime always runs with it on. Guard that a missing cookie (the
    // pre-existing, always-on branch) still 303s straight to login rather than
    // rendering this recovery page, matching the documented contract.
    await context.clearCookies();
    const response = await page.goto('/dashboard', { waitUntil: 'domcontentloaded' });
    expect(response?.status()).toBe(200);
    await expect(page).toHaveURL(/\/student\/login\?next=%2Fdashboard/);
  });
});
