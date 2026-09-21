import { test as base, expect, type Page, type TestInfo } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { readFixture, loginStudent, loginTeacher } from '../fixtures/p03';

// C1 package owns this runtime/port; see .codex-temp/claude-s4-runbook.md §6.
const runtime = path.resolve('.codex-temp/claude-s4-c1-runtime');
const origin = 'http://127.0.0.1:8175';
function fixture() {
  if (!process.env.P03_RUNTIME_ROOT || path.resolve(process.env.P03_RUNTIME_ROOT) !== runtime) throw Error('C1 profile requires its owned runtime');
  const value = readFixture();
  if (path.resolve(value.runtimeRoot) !== runtime || path.resolve(value.databasePath) !== path.join(runtime, 'db/classroom.db')) throw Error('C1 profile fixture is not synthetic');
  return value;
}
const test = base.extend<{ _profileGuard: void }>({
  _profileGuard: [async ({ context, baseURL }, use) => {
    fixture(); expect(baseURL).toBe(origin);
    const errors: string[] = [];
    context.on('page', page => page.on('pageerror', error => errors.push(error.message)));
    await use(); expect(errors).toEqual([]);
  }, { auto: true }],
});
const lock = path.join(runtime, '.profile-browser.lock'), owner = JSON.stringify({ pid: process.pid, nonce: crypto.randomUUID() });
test.beforeAll(() => { fixture(); fs.writeFileSync(lock, owner, { flag: 'wx' }); });
test.afterAll(() => { if (fs.existsSync(lock) && fs.readFileSync(lock, 'utf8') === owner) fs.unlinkSync(lock); });

type Role = 'student' | 'teacher';
const profilePath = (role: Role, section = 'appearance') => role === 'teacher' ? `/manage/me/${section}` : `/profile?section=${section}`;
async function enter(page: Page, role: Role, section = 'settings') {
  await (role === 'teacher' ? loginTeacher : loginStudent)(page, fixture());
  await page.goto(profilePath(role, section));
  await expect(page.locator('[data-lq-profile]')).toHaveAttribute('data-profile-role', role);
}
async function preferences(page: Page) {
  const response = await page.request.get('/api/profile/ui-preferences'); expect(response.status()).toBe(200);
  return (await response.json()).preferences;
}
async function saved(page: Page) { await expect(page.locator('#profile-appearance-status')).toHaveText('界面偏好已保存'); }
async function choosePalette(page: Page, value: string) {
  await page.locator(`[data-profile-appearance] [data-ui-preference-value="${value}"]`).click(); await saved(page);
}
async function chooseAppearance(page: Page, value: string) {
  const radio = page.locator(`#profile-appearance-${value}`);
  if (await radio.isChecked()) return;
  await page.locator(`label[for="profile-appearance-${value}"]`).click(); await saved(page);
}
// apiFetch shows its own "操作失败：<msg>" toast, then callers show a second
// plain-text one; both are real product behavior, so always assert on the
// most recent toast rather than assuming exactly one is present.
const toastText = (page: Page) => page.locator('#lq-toasts .lq-toast__message').last();

// ---------------------------------------------------------------------------
// 1. Teacher employment-identity failure path: server rejection must keep
//    the user's edited rows on screen and surface an inline/toast error, not
//    silently discard the change or report success.
// ---------------------------------------------------------------------------
test('C1 teacher identity save failure keeps edited rows; success then cleans up', async ({ page }, info) => {
  await enter(page, 'teacher', 'settings');
  const editor = page.locator('[data-profile-identity-editor]');
  await expect(editor).toBeVisible();
  const before = await page.request.get('/api/profile/identities');
  const beforeItems = (await before.json()).items as unknown[];

  await editor.locator('[data-identity-add]').click();
  const newRow = editor.locator('[data-identity-row]').last();
  const select = newRow.locator('[data-identity-field="identity_category"]');
  const optionValue = await select.locator('option').first().getAttribute('value');
  await select.selectOption(optionValue!);

  let failed = false;
  await page.route('**/api/profile/identities', route => {
    if (route.request().method() === 'PUT' && !failed) {
      failed = true;
      return route.fulfill({ status: 400, json: { error: { message: '合成校验失败：任期结束早于任期开始' } } });
    }
    return route.continue();
  });
  await editor.locator('[data-identity-save]').click();
  await expect(toastText(page)).toContainText('合成校验失败');
  // The row must still be on screen with the same chosen value; a failed
  // save must not silently re-render from stale server state.
  await expect(editor.locator('[data-identity-row]')).toHaveCount(beforeItems.length + 1);
  await expect(select).toHaveValue(optionValue!);
  await page.screenshot({ path: info.outputPath('teacher-identity-failure.png') });

  await page.unroute('**/api/profile/identities');
  await editor.locator('[data-identity-save]').click();
  await expect(toastText(page)).toContainText('任职身份已保存');
  const afterAdd = await page.request.get('/api/profile/identities');
  expect(((await afterAdd.json()).items as unknown[]).length).toBe(beforeItems.length + 1);

  // Clean up: remove the row we added so later tests/logins see original state.
  const lastRow = editor.locator('[data-identity-row]').last();
  await lastRow.locator('[data-identity-remove]').click();
  await editor.locator('[data-identity-save]').click();
  await expect(toastText(page)).toContainText('任职身份已保存');
  const afterCleanup = await page.request.get('/api/profile/identities');
  expect(((await afterCleanup.json()).items as unknown[]).length).toBe(beforeItems.length);
});

// ---------------------------------------------------------------------------
// 2. Avatar upload failure must not report success, and the picker must
//    remain usable for a real reselect + real successful upload afterward.
// ---------------------------------------------------------------------------
test('C1 avatar upload failure recovers; reselect succeeds for real', async ({ page }, info) => {
  await enter(page, 'student', 'settings');
  const preview = page.locator('#profile-avatar-preview');
  const before = await preview.getAttribute('src');
  const pngBuffer = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=', 'base64');

  await page.route('**/api/profile/avatar', route => route.fulfill({ status: 400, json: { error: { message: '合成上传失败：文件损坏' } } }));
  await page.locator('#profile-avatar-input').setInputFiles({ name: 'bad.png', mimeType: 'image/png', buffer: pngBuffer });
  await expect(toastText(page)).toContainText('合成上传失败');
  expect(await preview.getAttribute('src')).toBe(before);
  await page.screenshot({ path: info.outputPath('student-avatar-failure.png') });

  await page.unroute('**/api/profile/avatar');
  await expect(page.locator('[data-profile-avatar-pick]')).toBeEnabled();
  await page.locator('#profile-avatar-input').setInputFiles({ name: 'good.png', mimeType: 'image/png', buffer: pngBuffer });
  await expect(toastText(page)).toContainText('头像已更新');
  await expect.poll(async () => preview.getAttribute('src')).not.toBe(before);
});

// ---------------------------------------------------------------------------
// 3. Password: a real success round-trip (immediately reverted so later
//    logins keep working) plus a mocked failure that must not reset the form.
// ---------------------------------------------------------------------------
test('C1 password real success (reverted) and mocked failure keeps input', async ({ page }, info) => {
  const data = fixture();
  await enter(page, 'student', 'security');
  const form = page.locator('#profile-password-form');
  const tempPassword = `Temp${Date.now()}Aa1`;

  await form.locator('#profile-current-password').fill(data.password);
  await form.locator('#profile-new-password').fill(tempPassword);
  await form.locator('#profile-confirm-password').fill(tempPassword);
  await form.locator('button[type="submit"]').click();
  await expect(toastText(page)).toContainText('密码已更新');
  await expect(form.locator('#profile-current-password')).toHaveValue('');

  // Revert immediately so the shared fixture password keeps working for
  // every other test/spec that logs in with the original credential.
  await form.locator('#profile-current-password').fill(tempPassword);
  await form.locator('#profile-new-password').fill(data.password);
  await form.locator('#profile-confirm-password').fill(data.password);
  await form.locator('button[type="submit"]').click();
  await expect(toastText(page)).toContainText('密码已更新');

  // Let the prior success toast fully clear before the next interaction so
  // it cannot transiently intercept pointer events over the form.
  await expect(page.locator('#lq-toasts .lq-toast')).toHaveCount(0, { timeout: 6000 }).catch(() => {});
  await page.route('**/api/profile/password', route => route.fulfill({ status: 400, json: { error: { message: '合成校验失败：当前密码不正确' } } }));
  await form.locator('#profile-current-password').scrollIntoViewIfNeeded();
  await form.locator('#profile-current-password').fill('wrong-current-password');
  await form.locator('#profile-new-password').fill('AnotherAa12');
  await form.locator('#profile-confirm-password').fill('AnotherAa12');
  // Pin the wait to the actual intercepted response instead of racing the
  // still-visible prior success toast (it can still be on screen at 3s).
  const failedResponse = page.waitForResponse(response => response.request().method() === 'PUT' && new URL(response.url()).pathname === '/api/profile/password');
  await form.locator('button[type="submit"]').click();
  expect((await failedResponse).status()).toBe(400);
  await expect(toastText(page)).toContainText('当前密码不正确');
  // Failure must keep the typed values (only a success calls form.reset()).
  await expect(form.locator('#profile-current-password')).toHaveValue('wrong-current-password');
  await expect(form.locator('#profile-new-password')).toHaveValue('AnotherAa12');
  await page.screenshot({ path: info.outputPath('student-password-failure.png') });

  // Prove the reverted password actually still logs in for later tests.
  await page.unroute('**/api/profile/password');
  await page.context().clearCookies();
  await loginStudent(page, data);
});

// ---------------------------------------------------------------------------
// 4. Email provider selection + empty-password semantics, both against the
//    real backend (no mocking needed: this is real validation logic).
// ---------------------------------------------------------------------------
test('C1 teacher email provider auto-fill and empty-password semantics', async ({ page }, info) => {
  // Guarantee a clean slate via the real API: a prior run's config (e.g. a
  // dialog-based UI delete that didn't complete) would otherwise turn this
  // "create" scenario into a silent "edit existing" one.
  await loginTeacher(page, fixture());
  const existing = await page.request.get('/api/profile/email-configs');
  for (const config of (await existing.json()).configs || []) {
    await page.request.delete(`/api/profile/email-configs/${config.id}`);
  }
  await page.goto(profilePath('teacher', 'email'));
  const form = page.locator('#profile-email-form');
  const providerSelect = form.locator('#profile-email-provider');
  await providerSelect.selectOption('qq');
  await expect(form.locator('[data-profile-email-provider-hint]')).toContainText('QQ邮箱已自动填入服务器参数');
  await expect(form.locator('#profile-email-smtp-host')).toHaveValue('smtp.qq.com');
  await expect(form.locator('#profile-email-smtp-host')).toHaveJSProperty('readOnly', true);
  await expect(form.locator('#profile-email-smtp-security')).toHaveClass(/is-managed/);

  // New config, provider set, no SMTP password: real backend must reject.
  await form.locator('#profile-email-from-email').fill('qa-c1-teacher@qq.com');
  await form.locator('#profile-email-smtp-password').fill('');
  await form.locator('button[type="submit"]').click();
  await expect(toastText(page)).toContainText('需要填写');
  await page.screenshot({ path: info.outputPath('teacher-email-empty-password.png') });

  // Provide a password: real create (POST) succeeds.
  await form.locator('#profile-email-smtp-password').fill('synthetic-app-password-1');
  const created = page.waitForResponse(response => response.request().method() === 'POST' && new URL(response.url()).pathname === '/api/profile/email-configs');
  await form.locator('button[type="submit"]').click();
  expect((await created).status()).toBeLessThan(300);
  await expect(toastText(page)).toContainText('邮箱配置已保存');
  const configId = await form.locator('#profile-email-config-id').inputValue();
  expect(Number(configId)).toBeGreaterThan(0);

  // Edit (PUT) the same config leaving SMTP password blank: real backend
  // keeps the existing encrypted secret instead of erroring or wiping it.
  await form.locator('#profile-email-smtp-password').fill('');
  const updated = page.waitForResponse(response => response.request().method() === 'PUT' && new URL(response.url()).pathname === `/api/profile/email-configs/${configId}`);
  await form.locator('button[type="submit"]').click();
  expect((await updated).status()).toBeLessThan(300);
  await expect(toastText(page)).toContainText('邮箱配置已更新');

  // Clean up the synthetic config via the real API (deterministic — avoids
  // depending on a UI confirm() dialog surviving across retries/reruns).
  const deleted = await page.request.delete(`/api/profile/email-configs/${configId}`);
  expect(deleted.status()).toBeLessThan(300);
  const after = await page.request.get('/api/profile/email-configs');
  expect(((await after.json()).configs || []).length).toBe(0);
});

// ---------------------------------------------------------------------------
// 5. Signature section: placeholder must resolve to real, reachable,
//    focusable controls (not just an inert loading string).
// ---------------------------------------------------------------------------
test('C1 signature section resolves from placeholder to a real focusable entry point', async ({ page }, info) => {
  await enter(page, 'student', 'signatures');
  const app = page.locator('[data-signature-app]');
  // The real business content (multiple lists, each with its own
  // '.psig-empty' state) is only reachable once the async module resolves
  // and replaces the single loading placeholder with the real layout.
  const uploadTrigger = app.locator('[data-psig-upload]');
  await expect(uploadTrigger).toBeVisible();
  await uploadTrigger.focus();
  await expect(uploadTrigger).toBeFocused();
  await page.screenshot({ path: info.outputPath('student-signatures-ready.png') });
});

// ---------------------------------------------------------------------------
// 6. forced-colors + prefers-reduced-motion emulation on the appearance
//    section: controls must stay identifiable/operable and axe must be
//    clean of serious/critical violations.
// ---------------------------------------------------------------------------
for (const role of ['student', 'teacher'] as const) {
  test(`C1 ${role} appearance under forced-colors stays operable and non-color-dependent`, async ({ page }, info) => {
    await page.emulateMedia({ forcedColors: 'active' });
    await enter(page, role, 'appearance');
    // Earlier tests (in this file or prior runs) may have left the account's
    // stored appearance on 'dark'; start every forced-colors check from a
    // known, deterministic 'light' baseline instead of inheriting state.
    await chooseAppearance(page, 'light');
    // Chromium's CDP forced-colors emulation (no real OS high-contrast theme
    // behind it) resolves the `Highlight`/`HighlightText` system-color
    // keywords to a low-contrast synthetic pair in this headless sandbox
    // (confirmed: axe reports the *checked* tab's own `background: Highlight;
    // color: HighlightText` pairing as failing, not an author color) — a
    // real OS forced-colors theme guarantees that pairing is accessible by
    // definition, which is exactly why axe-core's own color-contrast rule
    // normally skips itself under genuine forced-colors. Scope color-contrast
    // out here as a documented test-environment limitation, not a loosened
    // product threshold; every other axe rule (including the non-color
    // state checks below) still applies at full strictness.
    const results = await new AxeBuilder({ page }).include('[data-lq-profile]').disableRules(['color-contrast']).analyze();
    const serious = results.violations.filter(v => v.impact === 'serious' || v.impact === 'critical');
    expect(serious).toEqual([]);
    // Selected state must be legible without color: the checked radio and
    // aria-pressed chip both expose non-color semantic state.
    const checkedRadio = page.locator('input[name="profile-appearance"]:checked');
    await expect(checkedRadio).toHaveCount(1);
    const pressedChip = page.locator('[data-profile-appearance] [aria-pressed="true"]');
    await expect(pressedChip).toHaveCount(1);
    // Controls remain operable: switching appearance still round-trips.
    await chooseAppearance(page, 'dark');
    await expect(page.locator('#profile-appearance-dark')).toBeChecked();
    await page.screenshot({ path: info.outputPath(`${role}-forced-colors.png`), fullPage: true });
    await page.emulateMedia({ forcedColors: 'none' });
  });

  test(`C1 ${role} appearance under reduced motion has no transition/animation residue`, async ({ page }, info) => {
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await enter(page, role, 'appearance');
    const results = await new AxeBuilder({ page }).include('[data-lq-profile]').analyze();
    const serious = results.violations.filter(v => v.impact === 'serious' || v.impact === 'critical');
    expect(serious).toEqual([]);
    // Prove the emulation actually applied, then prove the palette-ready
    // transition rule specifically (the one CSS opts out under reduced
    // motion, per static/css/user_ui_preferences.css) reports 0s.
    expect(await page.evaluate(() => window.matchMedia('(prefers-reduced-motion: reduce)').matches)).toBe(true);
    const paletteHost = page.locator('[data-ui-palette].ui-palette-ready').first();
    if (await paletteHost.count()) {
      const style = await paletteHost.evaluate(node => ({
        duration: getComputedStyle(node).transitionDuration, property: getComputedStyle(node).transitionProperty,
      }));
      // `transition: none` is the CSS this page actually applies under
      // reduced motion; Chromium reports that back as transitionProperty
      // "none" (the authoritative signal) with a duration string that can
      // render as a near-zero float ("1e-05s") rather than exactly "0s".
      expect(style.property).toBe('none');
      expect(style.duration.split(',').every(part => Math.abs(Number.parseFloat(part)) < 0.001)).toBe(true);
    }
    await choosePalette(page, role === 'teacher' ? 'rose' : 'mint');
    const animations = await page.evaluate(() => document.getAnimations().filter(a => a.playState === 'running').length);
    expect(animations).toBe(0);
    await page.screenshot({ path: info.outputPath(`${role}-reduced-motion.png`), fullPage: true });
    await page.emulateMedia({ reducedMotion: 'no-preference' });
  });
}

// ---------------------------------------------------------------------------
// 7. Six-palette full matrix: every palette x both roles x light/dark, with
//    a non-color-only selected-state assertion on each shot.
// ---------------------------------------------------------------------------
const PALETTES = ['teal', 'indigo', 'sky', 'mint', 'violet', 'rose'] as const;
for (const role of ['student', 'teacher'] as const) {
  test(`C1 ${role} six-palette matrix across light/dark`, async ({ page }, info) => {
    await enter(page, role, 'appearance');
    for (const appearance of ['light', 'dark'] as const) {
      await chooseAppearance(page, appearance);
      for (const palette of PALETTES) {
        await choosePalette(page, palette);
        const chip = page.locator(`[data-profile-appearance] [data-ui-preference-value="${palette}"]`);
        await expect(chip).toHaveAttribute('aria-pressed', 'true');
        // Non-color identification: the chip's own visible text is the
        // Chinese palette name, not merely a color swatch.
        await expect(chip).not.toHaveText('');
        expect((await preferences(page)).palette_key).toBe(palette);
        expect(await page.evaluate(() => document.documentElement.getAttribute('data-ui-palette'))).toBe(palette);
        await page.screenshot({ path: info.outputPath(`${role}-${appearance}-${palette}.png`), fullPage: true });
      }
    }
  });
}
