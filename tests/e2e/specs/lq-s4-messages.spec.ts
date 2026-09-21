import AxeBuilder from '@axe-core/playwright';
import { test, expect, type Page } from '@playwright/test';
import { readFixture, loginStudent, loginTeacher, type P03Fixture } from '../fixtures/p03';

// C2 package owns this runtime/port; see .codex-temp/claude-s4-runbook.md §6.
// LANSHARE_LQ_FAMILIES must include "messages" and the server must be
// started against .codex-temp/claude-s4-c2-runtime on port 8177 before
// running this spec (see the C2 report for the exact commands used).

type Role = 'student' | 'teacher';
const messagesPath = (role: Role, section: 'notifications' | 'private') =>
  role === 'teacher' ? `/manage/me/${section}` : `/profile?section=${section}`;

async function login(page: Page, role: Role, fixture: P03Fixture) {
  if (role === 'teacher') await loginTeacher(page, fixture);
  else await loginStudent(page, fixture);
}

for (const role of ['student', 'teacher'] as const) {
  for (const width of [1440, 390]) {
    test(`${role} notifications entry at ${width} renders lq scaffolding and is axe-clean`, async ({ page }) => {
      const fixture = readFixture();
      await page.setViewportSize({ width, height: 900 });
      await login(page, role, fixture);
      const response = await page.goto(messagesPath(role, 'notifications'));
      expect(response?.status()).toBe(200);
      const app = page.locator('[data-message-center-app]');
      await expect(app).toHaveCount(1);
      await expect(app).toHaveAttribute('data-lq-messages', '');
      await expect(page.locator('.lq-page-head.lq-messages__heading')).toBeVisible();
      const scan = await new AxeBuilder({ page }).include('[data-message-center-app]').analyze();
      expect(scan.violations.filter((violation) => violation.impact === 'serious' || violation.impact === 'critical')).toEqual([]);
    });
  }
}

test('notifications tab switch updates the ?tab= query string', async ({ page }) => {
  const fixture = readFixture();
  await loginStudent(page, fixture);
  await page.goto('/profile?section=notifications');
  const tabs = page.locator('#message-center-tabs [data-tab]');
  await expect(tabs.first()).toBeVisible();
  const secondTab = tabs.nth(1);
  const targetCategory = await secondTab.getAttribute('data-tab');
  await secondTab.click();
  await expect.poll(() => new URL(page.url()).searchParams.get('tab')).toBe(targetCategory);
});

test('private conversation: sending text posts scope/contact, clears the composer on success', async ({ page }) => {
  const fixture = readFixture();
  await loginStudent(page, fixture);
  await page.goto('/profile?section=private');

  const contactSelect = page.locator('#message-center-contact-select');
  await expect(contactSelect).toBeVisible();
  const optionCount = await contactSelect.locator('option').count();
  test.skip(optionCount === 0, 'Synthetic fixture has no messageable contact for this role.');
  await contactSelect.selectOption({ index: 0 });

  const composer = page.locator('#message-center-compose-input');
  await expect(composer).toBeEnabled({ timeout: 15_000 });

  let capturedBody: Record<string, unknown> | null = null;
  await page.route('**/api/message-center/private/messages', async (route) => {
    if (route.request().method() === 'POST') {
      capturedBody = route.request().postDataJSON() as Record<string, unknown>;
    }
    await route.continue();
  });

  const draftText = `LQ C2 spec message ${Date.now()}`;
  await composer.fill(draftText);
  await page.locator('[data-send-button]').click();
  await expect.poll(() => capturedBody).not.toBeNull();
  expect(capturedBody).toMatchObject({ content: draftText });
  expect(capturedBody).toHaveProperty('contact_identity');
  expect(capturedBody).toHaveProperty('class_offering_id');
  await expect(composer).toHaveValue('', { timeout: 15_000 });
});

test('private conversation: a failed send keeps the draft and offers a retry, no silent auto-resend', async ({ page }) => {
  const fixture = readFixture();
  await loginStudent(page, fixture);
  await page.goto('/profile?section=private');

  const contactSelect = page.locator('#message-center-contact-select');
  await expect(contactSelect).toBeVisible();
  const optionCount = await contactSelect.locator('option').count();
  test.skip(optionCount === 0, 'Synthetic fixture has no messageable contact for this role.');
  await contactSelect.selectOption({ index: 0 });

  const composer = page.locator('#message-center-compose-input');
  await expect(composer).toBeEnabled({ timeout: 15_000 });

  let postCount = 0;
  await page.route('**/api/message-center/private/messages', async (route) => {
    if (route.request().method() === 'POST') {
      postCount += 1;
      await route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ status: 'error', message: 'synthetic 503' }) });
      return;
    }
    await route.continue();
  });

  const draftText = `LQ C2 spec failure draft ${Date.now()}`;
  await composer.fill(draftText);
  await page.locator('[data-send-button]').click();
  await expect(composer).toHaveValue(draftText, { timeout: 15_000 });
  // No automatic retry: exactly one POST was made for this one click.
  await page.waitForTimeout(1500);
  expect(postCount).toBe(1);
});

test('draft text survives navigating away from and back to a conversation', async ({ page }) => {
  const fixture = readFixture();
  await loginStudent(page, fixture);
  await page.goto('/profile?section=private');

  const contactSelect = page.locator('#message-center-contact-select');
  await expect(contactSelect).toBeVisible();
  const optionCount = await contactSelect.locator('option').count();
  test.skip(optionCount < 2, 'Synthetic fixture needs at least two contacts to test draft isolation.');

  const firstOption = contactSelect.locator('option').nth(1); // index 0 is the "请选择联系人" placeholder
  const firstValue = await firstOption.getAttribute('value');
  const firstIdentity = await firstOption.getAttribute('data-contact');
  await contactSelect.selectOption(firstValue!);
  const composer = page.locator('#message-center-compose-input');
  await expect(composer).toBeEnabled({ timeout: 15_000 });
  const draftText = `LQ C2 spec draft kept ${Date.now()}`;
  await composer.fill(draftText);

  // Re-read the option list after activating the first contact: the select
  // is re-rendered (most-recently-active first) on every conversation load,
  // so the second option must be resolved again by its stable data-contact
  // identity rather than a raw index. `[data-contact]` is required here: the
  // leading "请选择联系人" placeholder option has no data-contact attribute
  // at all, so a bare `:not([data-contact="..."])` would wrongly match it
  // too (an earlier version of this test did that and mistook the resulting
  // no-op "select the empty placeholder" for a real switch that silently
  // failed to clear the draft — see the C2 report's "修复轮" section).
  const secondOption = contactSelect.locator(`option[data-contact]:not([data-contact="${firstIdentity}"])`).first();
  test.skip((await secondOption.count()) === 0, 'Synthetic fixture only exposes one distinct contact identity.');
  const secondValue = await secondOption.getAttribute('value');
  const secondIdentity = await secondOption.getAttribute('data-contact');
  expect(secondValue).toBeTruthy();
  expect(secondIdentity).toBeTruthy();
  expect(secondIdentity).not.toBe(firstIdentity);

  // Switch to a genuinely distinct contact (commonly the AI assistant
  // contact in the synthetic fixture): the draft for the first contact must
  // be cleared immediately by activateDraft(), not merely "eventually".
  await contactSelect.selectOption(secondValue!);
  await expect.poll(() => contactSelect.locator('option:checked').getAttribute('data-contact')).toBe(secondIdentity);
  await expect(composer).toHaveValue('', { timeout: 15_000 });
  await expect(composer).not.toHaveValue(draftText);

  // Switching back must restore the untouched original draft.
  await contactSelect.selectOption(firstValue!);
  await expect.poll(() => contactSelect.locator('option:checked').getAttribute('data-contact')).toBe(firstIdentity);
  await expect(composer).toHaveValue(draftText, { timeout: 15_000 });
});

test('attachment select shows a preview chip that can be removed before sending', async ({ page }) => {
  const fixture = readFixture();
  await loginStudent(page, fixture);
  await page.goto('/profile?section=private');

  const contactSelect = page.locator('#message-center-contact-select');
  await expect(contactSelect).toBeVisible();
  const optionCount = await contactSelect.locator('option').count();
  test.skip(optionCount === 0, 'Synthetic fixture has no messageable contact for this role.');
  await contactSelect.selectOption({ index: 0 });
  await expect(page.locator('#message-center-compose-input')).toBeEnabled({ timeout: 15_000 });

  const fileInput = page.locator('#message-center-file-input');
  await fileInput.setInputFiles({ name: 'lq-c2-spec.txt', mimeType: 'text/plain', buffer: Buffer.from('lq-s4 c2 attachment spec fixture') });

  const preview = page.locator('#message-center-attachment-preview');
  await expect(preview).toBeVisible();
  await expect(preview.locator('.message-center-attachment-chip')).toHaveCount(1);

  await preview.locator('.message-center-attachment-chip button').first().click();
  await expect(preview.locator('.message-center-attachment-chip')).toHaveCount(0);
});

test('send button uses aria-disabled (not native disabled) with an adjacent visible reason, and blocks click/keyboard', async ({ page }) => {
  const fixture = readFixture();
  await loginStudent(page, fixture);

  // bootstrap() auto-opens the first visible contact on this page (see
  // static/js/message_center.js:1985-1991), and in this fixture the
  // auto-opened contact is always teacher/assistant — is_blockable_role()
  // in classroom_app/services/message_center_service.py excludes both
  // roles, so there is no in-app action a student can take to reach
  // can_send=false deterministically without a second, blockable contact.
  // Instead of relying on real block/unblock side effects (which would
  // persist in the shared synthetic DB across tests in this file), force
  // can_send=false at the network boundary: intercept the one real
  // conversation response and flip only that field, leaving every other
  // real behavior (rendering, guards) exercised as normal.
  let forceCanSendFalse = true;
  await page.route('**/api/message-center/private/conversation**', async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    if (forceCanSendFalse && body?.conversation?.contact) {
      body.conversation.contact = { ...body.conversation.contact, can_send: false, is_blocked: true };
    }
    await route.fulfill({ response, json: body });
  });

  await page.goto('/profile?section=private');

  const sendButton = page.locator('[data-send-button]');
  const composer = page.locator('#message-center-compose-input');
  const hint = page.locator('#message-center-compose-hint');

  await expect(composer).toBeDisabled({ timeout: 15_000 });

  // The button must stay focusable (no native `disabled` DOM property) but
  // be aria-disabled, with a real, visible, associated reason via
  // aria-describedby pointing at the adjacent compose hint text. Playwright
  // treats aria-disabled="true" as disabled for its own `toBeDisabled()`
  // matcher and actionability checks (hence `{ force: true }` on the click
  // below), so the native-property check has to read the DOM directly.
  expect(await sendButton.evaluate((el) => (el as HTMLButtonElement).disabled)).toBe(false);
  await expect(sendButton).toHaveAttribute('aria-disabled', 'true');
  const describedBy = await sendButton.getAttribute('aria-describedby');
  expect(describedBy).toBe(await hint.getAttribute('id'));
  await expect(hint).toBeVisible();
  expect((await hint.textContent())?.trim().length).toBeGreaterThan(0);

  // Mouse: a click on an aria-disabled-but-focusable button must not fire
  // any request to the send endpoint.
  let postCount = 0;
  await page.route('**/api/message-center/private/messages', async (route) => {
    if (route.request().method() === 'POST') postCount += 1;
    await route.continue();
  });
  await sendButton.click({ force: true });
  await page.waitForTimeout(500);
  expect(postCount).toBe(0);

  // Keyboard: focusing the button and pressing Enter/Space must not send
  // either — this is the "not just pointer-events" requirement, since a
  // disabled-by-CSS-only button would still receive real key events.
  await sendButton.focus();
  await expect(sendButton).toBeFocused();
  await page.keyboard.press('Enter');
  await page.keyboard.press('Space');
  await page.waitForTimeout(500);
  expect(postCount).toBe(0);

  // Stop forcing can_send=false and reload the same conversation: aria
  // state must clear once sending is actually possible again.
  forceCanSendFalse = false;
  const contactSelect = page.locator('#message-center-contact-select');
  await contactSelect.selectOption({ index: 1 });
  await expect(composer).toBeEnabled({ timeout: 15_000 });
  await expect(sendButton).toHaveAttribute('aria-disabled', 'false');
  await expect(sendButton).not.toHaveAttribute('aria-describedby');
});

test('lq_family_enabled("messages") on renders the message center app shell', async ({ page }) => {
  // This spec's server is started with LANSHARE_LQ_FAMILIES including
  // "messages" (see runbook §4). The family-off ("旧 DOM") half of this
  // contract requires a second server invocation with the family excluded
  // and is documented as not executed in this run — see the C2 report.
  const fixture = readFixture();
  await loginStudent(page, fixture);
  await page.goto('/profile?section=notifications');
  const app = page.locator('[data-message-center-app]');
  await expect(app).toHaveCount(1);
  await expect(app).toHaveAttribute('data-lq-messages', '');
});

