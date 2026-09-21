import AxeBuilder from '@axe-core/playwright';
import fs from 'node:fs';
import path from 'node:path';
import { test, expect, type Page } from '@playwright/test';
import { readFixture, loginTeacher, type P03Fixture } from '../fixtures/p03';
import { settleEntranceAnimations } from '../fixtures/lq-s3';

// S6 P package: the new/edit poll form is assembled at runtime by
// static/js/manage_polls.js. The LQ branch builds it with the lq/forms.js and
// lq/components.js factories; the legacy branch keeps the original template
// literals. Both branches must expose the same control contract (id/name/type/
// required/value/placeholder) and must send the same request body.
//
// Two servers against one synthetic runtime (runbook §10, package P):
//   8211  LANSHARE_LQ_FAMILIES=manage-shell,manage-pages  LANSHARE_LQ_PILOT=true
//   8212  (no families)                                    LANSHARE_LQ_PILOT=false

const OFF_ORIGIN = `http://127.0.0.1:${process.env.LQ_S6_PORT_OFF || '8212'}`;
const SHOT_DIR = path.resolve(process.env.LQ_S6_OUTPUT || '.codex-temp/claude-s6-p-e2e');
fs.mkdirSync(SHOT_DIR, { recursive: true });
const shot = (page: Page, name: string) => page.screenshot({ path: path.join(SHOT_DIR, name), fullPage: true });

type ControlRecord = {
  tag: string; type: string; name: string; id: string;
  required: boolean; disabled: boolean; maxLength: number;
  placeholder: string; value: string; inLqField: boolean;
};

/** Reads every named control of the poll form, in document order. */
async function readControls(page: Page): Promise<ControlRecord[]> {
  return page.evaluate(() => {
    const form = document.querySelector('[data-poll-form]');
    if (!form) return [] as ControlRecord[];
    return Array.from(form.querySelectorAll('input, select, textarea')).map((el) => {
      const control = el as HTMLInputElement & HTMLSelectElement & HTMLTextAreaElement;
      return {
        tag: control.tagName,
        type: control.type,
        name: control.name,
        id: control.id,
        required: control.required,
        disabled: control.disabled,
        maxLength: typeof control.maxLength === 'number' ? control.maxLength : -1,
        placeholder: control.placeholder || '',
        value: control.value,
        inLqField: Boolean(control.closest('.lq-field')),
      };
    });
  }) as Promise<ControlRecord[]>;
}

async function openCreateForm(page: Page, origin = '', lqExpected: boolean | null = null) {
  expect((await page.goto(`${origin}/manage/library/polls`))?.status()).toBe(200);
  if (lqExpected !== null) {
    // The template only emits the marker on the true branch; assert it on the
    // poll page itself, not on whatever page the login flow landed on.
    await expect(page.locator('[data-poll-manage-root][data-poll-lq-forms]')).toHaveCount(lqExpected ? 1 : 0);
  }
  const createButton = page.locator('[data-poll-create-open]').first();
  await expect(createButton).toHaveCount(1);
  // The topbar action collapses out of view at 390px; the click handler is the
  // same either way, so drive it directly when it is not hit-testable.
  if (await createButton.isVisible()) await createButton.click();
  else await createButton.dispatchEvent('click');
  await expect(page.locator('[data-poll-form]')).toBeVisible({ timeout: 5000 });
  return createButton;
}

/**
 * Fills the create form the same way on both branches and returns the JSON body
 * the page actually posted. Nothing here targets an LQ-only or legacy-only hook.
 */
async function submitNewPoll(page: Page, fixture: P03Fixture, title: string) {
  const form = page.locator('[data-poll-form]');
  await form.locator('input[name="title"]').fill(title);
  await form.locator('textarea[name="description"]').fill('S6 P body-equality probe');
  await form.locator('select[name="vote_type"]').selectOption('multiple');
  await form.locator('select[name="result_visibility"]').selectOption('after_close');
  await form.locator('input[name="allow_change"]').check();
  await expect(form.locator('[data-poll-max-changes]')).toBeEnabled({ timeout: 5000 });
  await form.locator('[data-poll-max-changes]').fill('3');
  const optionInputs = form.locator('input[name="poll_option_label"]');
  await expect(optionInputs).toHaveCount(2);
  await optionInputs.nth(0).fill('甲方案');
  await optionInputs.nth(1).fill('乙方案');
  await form.locator(`input[name="poll_class"][value="${fixture.classOfferingId}"]`).check();

  const [request] = await Promise.all([
    page.waitForRequest((r) => r.url().includes('/api/polls/manage/polls') && r.method() === 'POST', { timeout: 15000 }),
    form.locator('[data-poll-save-status="active"]').click(),
  ]);
  return request.postDataJSON();
}

// --- 1. control contract -----------------------------------------------------

test('S6 P polls: the LQ branch builds every control through the lq/forms.js factories', async ({ page }) => {
  const fixture = readFixture();
  await loginTeacher(page, fixture);
  await openCreateForm(page, '', true);

  const controls = await readControls(page);
  const named = controls.filter((c) => c.name !== 'poll_class');
  expect(named).toEqual([
    { tag: 'INPUT', type: 'text', name: 'title', id: 'pollFormTitle', required: true, disabled: false, maxLength: 120, placeholder: '例如：xx课程期末考核形式', value: '', inLqField: true },
    { tag: 'TEXTAREA', type: 'textarea', name: 'description', id: 'pollFormDescription', required: false, disabled: false, maxLength: 1000, placeholder: '补充投票背景或说明', value: '', inLqField: true },
    { tag: 'SELECT', type: 'select-one', name: 'vote_type', id: 'pollFormVoteType', required: false, disabled: false, maxLength: -1, placeholder: '', value: 'single', inLqField: true },
    { tag: 'SELECT', type: 'select-one', name: 'result_visibility', id: 'pollFormVisibility', required: false, disabled: false, maxLength: -1, placeholder: '', value: 'after_vote', inLqField: true },
    { tag: 'INPUT', type: 'datetime-local', name: 'deadline_at', id: 'pollFormDeadline', required: false, disabled: false, maxLength: -1, placeholder: '', value: '', inLqField: true },
    { tag: 'INPUT', type: 'checkbox', name: 'allow_change', id: 'pollFormAllowChange', required: false, disabled: false, maxLength: -1, placeholder: '', value: 'on', inLqField: true },
    { tag: 'INPUT', type: 'number', name: 'max_changes', id: 'pollFormMaxChanges', required: false, disabled: true, maxLength: -1, placeholder: '0 = 不限次数', value: '0', inLqField: true },
    { tag: 'INPUT', type: 'text', name: 'poll_option_label', id: 'pollOptionInput1', required: false, disabled: false, maxLength: 160, placeholder: '选项 1', value: '选项一', inLqField: true },
    { tag: 'INPUT', type: 'text', name: 'poll_option_label', id: 'pollOptionInput2', required: false, disabled: false, maxLength: 160, placeholder: '选项 2', value: '选项二', inLqField: true },
  ]);

  // Every poll_class checkbox is a real factory checkbox carrying the offering id.
  const classes = controls.filter((c) => c.name === 'poll_class');
  expect(classes.length).toBeGreaterThan(0);
  for (const item of classes) {
    expect(item).toMatchObject({ tag: 'INPUT', type: 'checkbox', inLqField: true, disabled: false });
    expect(item.id).toMatch(/^pollClassPick\d+$/);
  }

  // No hand-written lq-* markup: every lq-field in the form carries the
  // factory-owned data-lq-field root, and no legacy form class survives.
  const audit = await page.evaluate(() => {
    const form = document.querySelector('[data-poll-form]')!;
    return {
      fields: form.querySelectorAll('.lq-field[data-lq-field]').length,
      fieldsTotal: form.querySelectorAll('.lq-field').length,
      sections: form.querySelectorAll('fieldset.lq-form-section').length,
      actions: form.querySelectorAll('.lq-form-actions .lq-form-actions__content').length,
      buttons: form.querySelectorAll('button.lq-btn').length,
      submits: form.querySelectorAll('button[type="submit"]').length,
      legacy: form.querySelectorAll('.poll-form-field, .poll-form-label, .poll-inline-check, .poll-icon-btn').length,
    };
  });
  expect(audit.fields).toBe(audit.fieldsTotal);
  expect(audit.sections).toBe(3);
  expect(audit.actions).toBe(1);
  expect(audit.submits).toBe(2);
  expect(audit.buttons).toBeGreaterThanOrEqual(5); // 2 remove + add + 2 submit
  expect(audit.legacy).toBe(0);

  await settleEntranceAnimations(page);
  const scan = await new AxeBuilder({ page }).include('[data-poll-form]').analyze();
  expect(scan.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical')).toEqual([]);
  await shot(page, 'lq-on-create-form-1440-light.png');
});

// --- 2. dynamic option rows --------------------------------------------------

test('S6 P polls: added and removed option rows stay on the factory path', async ({ page }) => {
  const fixture = readFixture();
  await loginTeacher(page, fixture);
  await openCreateForm(page);

  const rows = page.locator('[data-poll-option-list] .poll-option-row');
  await expect(rows).toHaveCount(2);
  await expect(page.locator('[data-poll-option-list][data-poll-lq="1"]')).toHaveCount(1);

  await page.locator('[data-poll-add-option]').click();
  await expect(rows).toHaveCount(3);
  // The new row is a factory field, not a string-built input.
  const third = await page.evaluate(() => {
    const row = document.querySelectorAll('[data-poll-option-list] .poll-option-row')[2];
    const input = row.querySelector('input[name="poll_option_label"]') as HTMLInputElement;
    return {
      inLqField: Boolean(input.closest('.lq-field[data-lq-field]')),
      labelFor: (row.querySelector('label.lq-field__label') as HTMLLabelElement | null)?.htmlFor || '',
      inputId: input.id,
      placeholder: input.placeholder,
      maxLength: input.maxLength,
      removeIsLqButton: Boolean(row.querySelector('button.lq-btn[data-poll-remove-option]')),
    };
  });
  expect(third.inLqField).toBe(true);
  expect(third.labelFor).toBe(third.inputId);
  expect(third.placeholder).toBe('选项 3');
  expect(third.maxLength).toBe(160);
  expect(third.removeIsLqButton).toBe(true);

  // Remove goes back to two, and the floor of two is still enforced.
  await page.locator('[data-poll-remove-option]').last().click();
  await expect(rows).toHaveCount(2);
  await page.locator('[data-poll-remove-option]').last().click();
  await expect(rows).toHaveCount(2);
});

// --- 3. allow_change gate ----------------------------------------------------

test('S6 P polls: the allow_change checkbox still gates max_changes on the LQ branch', async ({ page }) => {
  const fixture = readFixture();
  await loginTeacher(page, fixture);
  await openCreateForm(page);

  const maxChanges = page.locator('[data-poll-max-changes]');
  await expect(maxChanges).toBeDisabled();
  await page.locator('input[name="allow_change"]').check();
  await expect(maxChanges).toBeEnabled();
  await page.locator('input[name="allow_change"]').uncheck();
  await expect(maxChanges).toBeDisabled();
});

// --- 4. request body equality across both branches ---------------------------

test('S6 P polls: LQ and legacy branches post an identical request body', async ({ page, browser }) => {
  const fixture = readFixture();

  await loginTeacher(page, fixture);
  await openCreateForm(page);
  const lqPayload = await submitNewPoll(page, fixture, 'S6 P payload probe (lq)');

  const offContext = await browser.newContext({ baseURL: OFF_ORIGIN, viewport: { width: 1440, height: 900 } });
  const offPage = await offContext.newPage();
  try {
    await loginTeacher(offPage, fixture);
    await openCreateForm(offPage, '', false);
    const legacyPayload = await submitNewPoll(offPage, fixture, 'S6 P payload probe (legacy)');

    expect(Object.keys(lqPayload).sort()).toEqual(Object.keys(legacyPayload).sort());
    expect({ ...lqPayload, title: '' }).toEqual({ ...legacyPayload, title: '' });
    expect(lqPayload).toMatchObject({
      description: 'S6 P body-equality probe',
      vote_type: 'multiple',
      result_visibility: 'after_close',
      deadline_at: '',
      allow_change: true,
      max_changes: 3,
      options: [{ label: '甲方案' }, { label: '乙方案' }],
      status: 'active',
      class_offering_ids: [fixture.classOfferingId],
    });
  } finally {
    await offContext.close();
  }
});

// --- 5. switch off renders the legacy DOM ------------------------------------

test('S6 P polls: with the family off the form is the legacy template-literal DOM', async ({ browser }) => {
  const fixture = readFixture();
  const context = await browser.newContext({ baseURL: OFF_ORIGIN, viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  try {
    await loginTeacher(page, fixture);
    await openCreateForm(page, '', false);

    const controls = await readControls(page);
    const named = controls.filter((c) => c.name !== 'poll_class');
    expect(named).toEqual([
      { tag: 'INPUT', type: 'text', name: 'title', id: '', required: true, disabled: false, maxLength: 120, placeholder: '例如：xx课程期末考核形式', value: '', inLqField: false },
      { tag: 'TEXTAREA', type: 'textarea', name: 'description', id: '', required: false, disabled: false, maxLength: 1000, placeholder: '补充投票背景或说明', value: '', inLqField: false },
      { tag: 'SELECT', type: 'select-one', name: 'vote_type', id: '', required: false, disabled: false, maxLength: -1, placeholder: '', value: 'single', inLqField: false },
      { tag: 'SELECT', type: 'select-one', name: 'result_visibility', id: '', required: false, disabled: false, maxLength: -1, placeholder: '', value: 'after_vote', inLqField: false },
      { tag: 'INPUT', type: 'datetime-local', name: 'deadline_at', id: '', required: false, disabled: false, maxLength: -1, placeholder: '', value: '', inLqField: false },
      { tag: 'INPUT', type: 'checkbox', name: 'allow_change', id: '', required: false, disabled: false, maxLength: -1, placeholder: '', value: 'on', inLqField: false },
      { tag: 'INPUT', type: 'number', name: 'max_changes', id: '', required: false, disabled: true, maxLength: -1, placeholder: '0 = 不限次数', value: '0', inLqField: false },
      { tag: 'INPUT', type: 'text', name: 'poll_option_label', id: '', required: false, disabled: false, maxLength: 160, placeholder: '选项 1', value: '选项一', inLqField: false },
      { tag: 'INPUT', type: 'text', name: 'poll_option_label', id: '', required: false, disabled: false, maxLength: 160, placeholder: '选项 2', value: '选项二', inLqField: false },
    ]);

    const audit = await page.evaluate(() => {
      const form = document.querySelector('[data-poll-form]')!;
      return {
        formClass: form.className,
        lq: form.querySelectorAll('[class*="lq-"]').length,
        fields: form.querySelectorAll('.poll-form-field').length,
        labels: form.querySelectorAll('.poll-form-label').length,
        inlineCheck: form.querySelectorAll('.poll-inline-check').length,
        iconButtons: form.querySelectorAll('button.poll-icon-btn[data-poll-remove-option]').length,
        actions: form.querySelectorAll('.poll-form-actions').length,
        participants: form.querySelectorAll('label.poll-participant').length,
        lqOptionFlag: form.querySelectorAll('[data-poll-option-list][data-poll-lq]').length,
      };
    });
    expect(audit.formClass).toBe('poll-form');
    expect(audit.lq).toBe(0);
    expect(audit.fields).toBe(8);
    expect(audit.labels).toBe(8);
    expect(audit.inlineCheck).toBe(1);
    expect(audit.iconButtons).toBe(2);
    expect(audit.actions).toBe(1);
    expect(audit.participants).toBeGreaterThan(0);
    expect(audit.lqOptionFlag).toBe(0);

    // The legacy add-option path still uses insertAdjacentHTML and produces the
    // same bare input row as before this package.
    await page.locator('[data-poll-add-option]').click();
    await expect(page.locator('[data-poll-option-list] .poll-option-row')).toHaveCount(3);
    const thirdRow = await page.evaluate(() => {
      const row = document.querySelectorAll('[data-poll-option-list] .poll-option-row')[2];
      return row.outerHTML.replace(/\s+/g, ' ').trim();
    });
    expect(thirdRow).toBe('<div class="poll-option-row"> <input type="text" name="poll_option_label" maxlength="160" value="" placeholder="选项 3"> <button type="button" class="poll-icon-btn" data-poll-remove-option="" aria-label="删除选项">×</button> </div>');
  } finally {
    await context.close();
  }
});

// --- 6. screenshot matrix ----------------------------------------------------

for (const scheme of ['light', 'dark'] as const) {
  test(`S6 P polls: screenshot matrix ${scheme} (family on and off)`, async ({ page }) => {
    const fixture = readFixture();
    await page.emulateMedia({ colorScheme: scheme });
    for (const [branch, origin] of [['on', ''], ['off', OFF_ORIGIN]] as const) {
      await page.goto(`${origin}/teacher/login`);
      await expect(page.locator('#email')).toBeVisible();
      await page.locator('#email').fill(fixture.teacher.email);
      await page.locator('#password').fill(fixture.password);
      await Promise.all([
        page.waitForURL(/\/dashboard(?:\?|$)/, { timeout: 20_000 }),
        page.locator('button[type="submit"]').click(),
      ]);
      for (const width of [1440, 390]) {
        await page.setViewportSize({ width, height: 900 });
        await openCreateForm(page, origin, branch === 'on');
        await settleEntranceAnimations(page);
        await shot(page, `matrix-${branch}-${scheme}-${width}-poll-form.png`);
      }
    }
  });
}
