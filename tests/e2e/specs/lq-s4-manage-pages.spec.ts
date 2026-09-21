import AxeBuilder from '@axe-core/playwright';
import fs from 'node:fs';
import { test, expect } from '@playwright/test';
import { readFixture, loginTeacher } from '../fixtures/p03';
import { settleEntranceAnimations } from '../fixtures/lq-s3';

// S4 F package (manage-pages family). Uses the plain P03 fixture (not the
// S3-extended fixture) because these routes need no exam/report-card
// scenario data. Server must already be running with
// LANSHARE_LQ_FAMILIES=manage-shell,manage-pages and LANSHARE_LQ_PILOT=true
// against P03_RUNTIME_ROOT (see runbook §4/§6, package F, port 8183).

const graph = process.env.LQ_S4_GRAPH || JSON.parse(fs.readFileSync('static/assets/manifest.json', 'utf8')).revision;

const REGULAR_ROUTES = [
  '/manage/teaching/classes',
  '/manage/teaching/classroom-hub',
  '/manage/teaching/semesters',
  '/manage/library/polls',
];

const SUPER_ADMIN_ROUTES = [
  '/manage/system/organizations',
  '/manage/system/agent-keys',
];

for (const width of [1440, 390]) test(`S4 F teacher manage-pages routes render at ${width}`, async ({ page }, info) => {
  const fixture = readFixture();
  await page.setViewportSize({ width, height: 900 });
  await loginTeacher(page, fixture);
  for (const route of REGULAR_ROUTES) {
    const response = await page.goto(route);
    expect(response?.status(), route).toBe(200);
    await expect(page.locator('[data-page-head]').first(), route).toHaveCount(1);
    expect(await page.evaluate(() => document.documentElement.scrollWidth), route).toBeLessThanOrEqual(width);
    await settleEntranceAnimations(page);
  const scan = await new AxeBuilder({ page }).include('[data-page-head]').analyze();
    expect(scan.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical'), route).toEqual([]);
    if (width === 1440) await page.screenshot({ path: info.outputPath(`teacher-${route.replace(/\W+/g, '_')}-${width}.png`), fullPage: true });
  }
});

for (const width of [1440, 390]) test(`S4 F super admin manage-pages routes render at ${width}`, async ({ page }, info) => {
  const fixture = readFixture();
  await page.setViewportSize({ width, height: 900 });
  await loginTeacher(page, fixture, fixture.superTeacher);
  for (const route of SUPER_ADMIN_ROUTES) {
    const response = await page.goto(route);
    expect(response?.status(), route).toBe(200);
    await expect(page.locator('[data-page-head]').first(), route).toHaveCount(1);
    expect(await page.evaluate(() => document.documentElement.scrollWidth), route).toBeLessThanOrEqual(width);
    await settleEntranceAnimations(page);
  const scan = await new AxeBuilder({ page }).include('[data-page-head]').analyze();
    expect(scan.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical'), route).toEqual([]);
    if (width === 1440) await page.screenshot({ path: info.outputPath(`super-${route.replace(/\W+/g, '_')}-${width}.png`), fullPage: true });
  }
});

test('S4 F classes: delete-student LQ.confirm cancels without sending the DELETE request', async ({ page }, info) => {
  const fixture = readFixture();
  await page.setViewportSize({ width: 1440, height: 900 });
  await loginTeacher(page, fixture);
  expect((await page.goto('/manage/teaching/classes'))?.status()).toBe(200);

  let deleteFired = false;
  await page.route('**/api/manage/students/**', async (route) => {
    if (route.request().method() === 'DELETE') deleteFired = true;
    await route.continue();
  });

  const openStudentsButton = page.locator(`[data-action="open-students"][data-class-id="${fixture.classId}"]`).first();
  if (!(await openStudentsButton.count())) test.skip(true, 'synthetic fixture has no matching class card for this run');
  await openStudentsButton.click();

  const deleteButton = page.locator('[data-student-action="delete"]').first();
  if (!(await deleteButton.count())) test.skip(true, 'synthetic fixture has no student row to exercise delete on');

  await deleteButton.click();
  const dialog = page.locator('[data-lq-dialog]').last();
  await expect(dialog).toBeVisible({ timeout: 5000 });
  await settleEntranceAnimations(page);
  const scan = await new AxeBuilder({ page }).include('[data-lq-dialog]').analyze();
  expect(scan.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical')).toEqual([]);
  await page.screenshot({ path: info.outputPath('classes-delete-confirm-open.png') });

  await page.keyboard.press('Escape');
  await expect(dialog).toBeHidden({ timeout: 5000 });
  await expect(deleteButton).toBeFocused();
  expect(deleteFired).toBe(false);
});

test('S4 F semesters: page overlay Escape and delete LQ.confirm both return focus to their triggers', async ({ page }, info) => {
  const fixture = readFixture();
  await page.setViewportSize({ width: 1440, height: 900 });
  await loginTeacher(page, fixture);
  expect((await page.goto('/manage/teaching/semesters'))?.status()).toBe(200);

  // 1) Page-level overlay (the create/edit modal) Escape-closes and returns
  // focus to its trigger when no LQ.layer dialog is stacked on top.
  const createButton = page.locator('#heroSemesterCreateBtn');
  await createButton.click();
  await expect(page.locator('#semesterNameInput, [name="name"]').first()).toBeVisible({ timeout: 5000 });
  await page.keyboard.press('Escape');
  await expect(createButton).toBeFocused({ timeout: 5000 });

  // 2) Destructive LQ.confirm cancel returns focus to the delete trigger,
  // without an Escape-race stealing focus back to the underlying modal.
  let deleteFired = false;
  await page.route('**/api/manage/semesters/**', async (route) => {
    if (route.request().method() === 'DELETE') deleteFired = true;
    await route.continue();
  });
  // Semesters whose 校历 has not been synced render their delete button as
  // `disabled`; only an enabled trigger can exercise the confirm path.
  const deleteButton = page.locator('[data-action="delete"][data-semester-id]:not([disabled])').first();
  if (!(await deleteButton.count())) test.skip(true, 'synthetic fixture has no enabled semester delete trigger for this run');
  await deleteButton.click();
  const dialog = page.locator('[data-lq-dialog]').last();
  await expect(dialog).toBeVisible({ timeout: 5000 });
  await page.screenshot({ path: info.outputPath('semesters-delete-confirm-open.png') });
  await page.keyboard.press('Escape');
  await expect(dialog).toBeHidden({ timeout: 5000 });
  await expect(deleteButton).toBeFocused({ timeout: 5000 });
  expect(deleteFired).toBe(false);
});

test('S4 F classroom-hub: edit drawer Escape and delete LQ.confirm both return focus to their triggers', async ({ page }, info) => {
  const fixture = readFixture();
  await page.setViewportSize({ width: 1440, height: 900 });
  await loginTeacher(page, fixture);
  expect((await page.goto('/manage/teaching/classroom-hub'))?.status()).toBe(200);

  // 1) Page-level edit drawer Escape-closes and returns focus to its trigger.
  const editLink = page.locator('[data-action="edit-config"]').first();
  if (!(await editLink.count())) test.skip(true, 'synthetic fixture has no offering card for this run');
  await editLink.click();
  await expect(page.locator('#offeringHubEditDrawer')).not.toHaveAttribute('hidden', '', { timeout: 5000 });
  await page.keyboard.press('Escape');
  await expect(page.locator('#offeringHubEditDrawer')).toHaveAttribute('hidden', '', { timeout: 5000 });
  await expect(editLink).toBeFocused({ timeout: 5000 });

  // 2) Destructive LQ.confirm cancel returns focus to the delete trigger.
  let deleteFired = false;
  await page.route('**/api/manage/class_offerings/**', async (route) => {
    if (route.request().method() === 'DELETE') deleteFired = true;
    await route.continue();
  });
  const deleteButton = page.locator('[data-action="delete-offering"]').first();
  if (!(await deleteButton.count())) test.skip(true, 'synthetic fixture has no deletable offering for this run');
  await deleteButton.click();
  const dialog = page.locator('[data-lq-dialog]').last();
  await expect(dialog).toBeVisible({ timeout: 5000 });
  await page.screenshot({ path: info.outputPath('classroom-hub-delete-confirm-open.png') });
  await page.keyboard.press('Escape');
  await expect(dialog).toBeHidden({ timeout: 5000 });
  await expect(deleteButton).toBeFocused({ timeout: 5000 });
  expect(deleteFired).toBe(false);
});

test('S4 F polls: page overlay Escape and delete LQ.confirm both return focus to their triggers', async ({ page }, info) => {
  const fixture = readFixture();
  await page.setViewportSize({ width: 1440, height: 900 });
  await loginTeacher(page, fixture);

  // 1) Page-level create-form overlay Escape-closes and returns focus to its trigger.
  expect((await page.goto('/manage/library/polls'))?.status()).toBe(200);
  const createButton = page.locator('[data-poll-create-open]').first();
  await createButton.click();
  await expect(page.locator('[data-poll-overlay]')).toBeVisible({ timeout: 5000 });
  await page.keyboard.press('Escape');
  await expect(page.locator('[data-poll-overlay]')).toHaveCount(0, { timeout: 5000 });
  await expect(createButton).toBeFocused({ timeout: 5000 });

  // 2) Seed one real poll via the actual create API (not direct DB access) so
  // there is a delete trigger to exercise the destructive-confirm path on.
  const created = await page.request.post('/api/polls/manage/polls', {
    data: {
      title: 'S4 F round3 Escape focus fixture poll',
      options: [{ label: 'A' }, { label: 'B' }],
      class_offering_ids: [fixture.classOfferingId],
      status: 'draft',
    },
  });
  expect(created.ok(), await created.text().catch(() => '')).toBeTruthy();

  await page.reload();
  let deleteFired = false;
  await page.route('**/api/polls/**', async (route) => {
    if (route.request().method() === 'DELETE') deleteFired = true;
    await route.continue();
  });
  // The delete trigger only exists inside a poll's detail overlay
  // (manage_polls.js renderDetailBody), not on the list card itself.
  // openDetail() awaits a fetch before rendering it, so wait rather than
  // reading .count() synchronously right after the click.
  const pollCard = page.locator('[data-poll-open]').first();
  await expect(pollCard, 'seeded poll did not render a card in this run').toBeVisible({ timeout: 5000 });
  await pollCard.click();
  const deleteButton = page.locator('[data-poll-delete]').first();
  await expect(deleteButton, 'seeded poll detail has no delete trigger in this run').toBeVisible({ timeout: 5000 });
  await deleteButton.click();
  const dialog = page.locator('[data-lq-dialog]').last();
  await expect(dialog).toBeVisible({ timeout: 5000 });
  await page.screenshot({ path: info.outputPath('polls-delete-confirm-open.png') });
  await page.keyboard.press('Escape');
  await expect(dialog).toBeHidden({ timeout: 5000 });
  await expect(deleteButton).toBeFocused({ timeout: 5000 });
  expect(deleteFired).toBe(false);
});

// --- round 4: structural migrations (lq-field / lq-chip-row) -----------------

test('S4 F classes: lq-chip-row proxies the three real selects and resets with them', async ({ page }, info) => {
  const fixture = readFixture();
  await page.setViewportSize({ width: 1440, height: 900 });
  await loginTeacher(page, fixture);
  expect((await page.goto('/manage/teaching/classes'))?.status()).toBe(200);

  // The real <select>s must survive: manage_classes.js reads their .value.
  for (const id of ['#classDepartmentFilter', '#classHealthFilter', '#classSortSelect']) {
    await expect(page.locator(id), id).toHaveCount(1);
  }

  const healthChips = page.locator('#classHealthChips button.lq-chip--filter[data-value]');
  await expect(healthChips.first()).toBeVisible({ timeout: 5000 });
  expect(await healthChips.count()).toBe(8);

  const missingEmailChip = page.locator('#classHealthChips button.lq-chip--filter[data-value="missing-email"]');
  await missingEmailChip.click();
  await expect(page.locator('#classHealthFilter')).toHaveValue('missing-email');
  await expect(missingEmailChip).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('#classHealthChips button.lq-chip--filter[data-value="all"]')).toHaveAttribute('aria-pressed', 'false');

  const sortChip = page.locator('#classSortChips button.lq-chip--filter[data-value="students-desc"]');
  await sortChip.click();
  await expect(page.locator('#classSortSelect')).toHaveValue('students-desc');
  await expect(sortChip).toHaveAttribute('aria-pressed', 'true');

  const deptChip = page.locator('#classDepartmentChips button.lq-chip--filter[data-value]').nth(1);
  const deptValue = await deptChip.getAttribute('data-value');
  await deptChip.click();
  await expect(page.locator('#classDepartmentFilter')).toHaveValue(String(deptValue));
  await expect(deptChip).toHaveAttribute('aria-pressed', 'true');

  await page.screenshot({ path: info.outputPath('classes-chip-rows-1440.png'), fullPage: true });

  // "清除筛选" sets the select values directly; the chips must follow.
  await page.locator('#classFilterResetBtn').click();
  await expect(page.locator('#classHealthFilter')).toHaveValue('all');
  await expect(page.locator('#classSortSelect')).toHaveValue('department');
  await expect(page.locator('#classDepartmentFilter')).toHaveValue('all');
  await expect(page.locator('#classHealthChips button.lq-chip--filter[data-value="all"]')).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('#classSortChips button.lq-chip--filter[data-value="department"]')).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('#classDepartmentChips button.lq-chip--filter[data-value="all"]')).toHaveAttribute('aria-pressed', 'true');
  await expect(deptChip).toHaveAttribute('aria-pressed', 'false');
});

test('S4 F semesters: lq-field keeps the id/name/type contract manage_semesters.js depends on', async ({ page }, info) => {
  const fixture = readFixture();
  await page.setViewportSize({ width: 1440, height: 900 });
  await loginTeacher(page, fixture);
  expect((await page.goto('/manage/teaching/semesters'))?.status()).toBe(200);
  await page.locator('#heroSemesterCreateBtn').click();

  const fields = await page.evaluate(() => ['semesterNameInput', 'semesterStartInput', 'semesterEndInput', 'semesterIdInput']
    .map((id) => {
      const el = document.getElementById(id) as HTMLInputElement | null;
      return { id, found: Boolean(el), name: el?.name, type: el?.type, required: el?.required, inLqField: Boolean(el?.closest('.lq-field')) };
    }));
  expect(fields).toEqual([
    { id: 'semesterNameInput', found: true, name: 'name', type: 'text', required: false, inLqField: true },
    { id: 'semesterStartInput', found: true, name: 'start_date', type: 'date', required: true, inLqField: true },
    { id: 'semesterEndInput', found: true, name: 'end_date', type: 'date', required: true, inLqField: true },
    // The hidden id carrier stays a plain hidden input in both branches.
    { id: 'semesterIdInput', found: true, name: 'semester_id', type: 'hidden', required: false, inLqField: false },
  ]);
  // The date-range pairing contract ls_date_picker.js reads must survive.
  await expect(page.locator('#semesterStartInput')).toHaveAttribute('data-dp-pair', '#semesterEndInput');
  await expect(page.locator('#semesterEndInput')).toHaveAttribute('data-dp-role', 'end');

  await settleEntranceAnimations(page);
  const scan = await new AxeBuilder({ page }).include('#semesterForm').analyze();
  expect(scan.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical')).toEqual([]);
  await page.screenshot({ path: info.outputPath('semesters-lq-form-1440.png') });

  // manage_semesters.js prefills start/end from the server defaults, so clear
  // one first: the migrated `required` must still block submission natively.
  let savePosted = false;
  await page.route('**/api/manage/semesters/save', async (route) => { savePosted = true; await route.abort(); });
  await page.locator('#semesterStartInput').evaluate((el: HTMLInputElement) => { el.value = ''; });
  expect(await page.locator('#semesterStartInput').evaluate((el: HTMLInputElement) => el.checkValidity())).toBe(false);
  await page.locator('#semesterSubmitBtn').click();
  await page.waitForTimeout(500);
  expect(savePosted).toBe(false);
  // …and a complete form is still accepted by the same native check.
  await page.locator('#semesterStartInput').evaluate((el: HTMLInputElement) => { el.value = '2025-09-01'; });
  expect(await page.locator('#semesterForm').evaluate((el: HTMLFormElement) => el.checkValidity())).toBe(true);
});

test('S4 F organizations: lq-field forms keep their named-element contract and still submit', async ({ page }, info) => {
  const fixture = readFixture();
  await page.setViewportSize({ width: 1440, height: 900 });
  await loginTeacher(page, fixture, fixture.superTeacher);
  expect((await page.goto('/manage/system/organizations'))?.status()).toBe(200);

  // manage_organizations.js reaches every control through form.<name>.
  const named = await page.evaluate(() => {
    const read = (formId: string, names: string[]) => {
      const form = document.getElementById(formId) as HTMLFormElement | null;
      return names.map((name) => {
        const el = form?.elements.namedItem(name) as HTMLInputElement | HTMLSelectElement | null;
        return { formId, name, found: Boolean(el), tag: el?.tagName, inLqField: Boolean(el?.closest('.lq-field')) };
      });
    };
    return [
      ...read('org-school-form', ['school_code', 'school_name', 'display_order']),
      ...read('org-current-school-form', ['school_name', 'display_order', 'is_active']),
      ...read('org-college-form', ['college_name', 'display_order']),
      ...read('org-department-form', ['department_name', 'display_order']),
    ];
  });
  expect(named.filter((item) => !item.found)).toEqual([]);
  expect(named.filter((item) => !item.inLqField)).toEqual([]);
  expect(named.find((item) => item.name === 'is_active')?.tag).toBe('SELECT');

  await settleEntranceAnimations(page);
  const scan = await new AxeBuilder({ page }).include('#org-school-form').analyze();
  expect(scan.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical')).toEqual([]);
  await page.screenshot({ path: info.outputPath('organizations-lq-form-1440.png'), fullPage: true });

  // A real create round-trip through the migrated form.
  const suffix = `s4f4${Date.now().toString().slice(-6)}`;
  await page.locator('#org-school-code-input').fill(suffix);
  await page.locator('#org-school-name-input').fill(`S4 F4 验证学校 ${suffix}`);
  const [created] = await Promise.all([
    page.waitForResponse((r) => r.url().includes('/api/manage/system/organizations') && r.request().method() === 'POST', { timeout: 15000 }),
    page.locator('#org-school-form button[type="submit"]').click(),
  ]);
  expect(created.status(), await created.text().catch(() => '')).toBeLessThan(400);
});

// Material-library domain: the bespoke textbook modal had NO Escape handling
// and NO focus return at all before this round (round 4). Two separate tests so
// the focus-return evidence stands on its own even if the fixture state makes
// the destructive-confirm half unreachable.

test('S4 F textbooks: bespoke modal now Escape-closes and returns focus to its trigger', async ({ page }, info) => {
  const fixture = readFixture();
  await page.setViewportSize({ width: 1440, height: 900 });
  await loginTeacher(page, fixture);
  expect((await page.goto('/manage/library/textbooks'))?.status()).toBe(200);

  const createButton = page.locator('#heroTextbookCreateBtn, #openTextbookCreateBtn').first();
  await expect(createButton).toBeVisible({ timeout: 5000 });
  await createButton.click();
  await expect(page.locator('#textbookModalBackdrop')).toHaveClass(/is-open/, { timeout: 5000 });
  await page.keyboard.press('Escape');
  await expect(page.locator('#textbookModalBackdrop')).not.toHaveClass(/is-open/, { timeout: 5000 });
  await expect(createButton).toBeFocused({ timeout: 5000 });
  await page.screenshot({ path: info.outputPath('textbooks-after-escape-1440.png') });

  // The close/cancel buttons take the same path and must also hand focus back.
  await createButton.click();
  await expect(page.locator('#textbookModalBackdrop')).toHaveClass(/is-open/, { timeout: 5000 });
  await page.locator('#textbookModalCancelBtn').click();
  await expect(page.locator('#textbookModalBackdrop')).not.toHaveClass(/is-open/, { timeout: 5000 });
  await expect(createButton).toBeFocused({ timeout: 5000 });
});

test('S4 F textbooks: a stacked LQ.confirm owns Escape and the page modal stays put', async ({ page }) => {
  const fixture = readFixture();
  await page.setViewportSize({ width: 1440, height: 900 });
  await loginTeacher(page, fixture);

  // Seed one real textbook through the actual save endpoint (not the DB), so a
  // delete trigger exists to exercise the stacked-confirm path on.
  const seeded = await page.request.post('/api/manage/textbooks/save', {
    multipart: { title: `S4 F4 Escape 竞争验证教材 ${Date.now().toString().slice(-6)}`, publisher: 'S4 F4 QA' },
  });
  expect(seeded.status(), await seeded.text().catch(() => '')).toBeLessThan(400);

  expect((await page.goto('/manage/library/textbooks'))?.status()).toBe(200);
  let deleteFired = false;
  await page.route('**/api/manage/textbooks/**', async (route) => {
    if (route.request().method() === 'DELETE') deleteFired = true;
    await route.continue();
  });
  const deleteButton = page.locator('[data-action="delete"][data-textbook-id]').first();
  await expect(deleteButton, 'seeded textbook did not render a delete trigger').toBeVisible({ timeout: 5000 });
  await deleteButton.click();
  const dialog = page.locator('[data-lq-dialog]').last();
  await expect(dialog).toBeVisible({ timeout: 5000 });
  await page.keyboard.press('Escape');
  await expect(dialog).toBeHidden({ timeout: 5000 });
  await expect(deleteButton).toBeFocused({ timeout: 5000 });
  expect(deleteFired).toBe(false);
  // The page modal was never opened here, and Escape must not have opened or
  // closed anything else underneath the LQ dialog.
  await expect(page.locator('#textbookModalBackdrop')).not.toHaveClass(/is-open/);
});

test('S4 F manage-pages family closed renders legacy DOM (no lq-page-head, no lq-field, no lq-chip-row)', async ({ page }) => {
  // Second server process (runbook §6 port 8184) started WITHOUT
  // LANSHARE_LQ_FAMILIES and with LANSHARE_LQ_PILOT=false, against the same
  // synthetic runtime. Absolute URLs so the config baseURL (8183) is bypassed.
  const offOrigin = process.env.LQ_S4_PORT_OFF ? `http://127.0.0.1:${process.env.LQ_S4_PORT_OFF}` : '';
  test.skip(!offOrigin, 'set LQ_S4_PORT_OFF to the port of a server with LANSHARE_LQ_FAMILIES unset');
  const fixture = readFixture();

  await page.goto(`${offOrigin}/teacher/login`);
  await expect(page.locator('#email')).toBeVisible();
  await page.locator('#email').fill(fixture.teacher.email);
  await page.locator('#password').fill(fixture.password);
  await Promise.all([
    page.waitForURL(/\/dashboard(?:\?|$)/, { timeout: 20_000 }),
    page.locator('button[type="submit"]').click(),
  ]);

  const response = await page.goto(`${offOrigin}/manage/teaching/classes`);
  expect(response?.status()).toBe(200);
  expect(await page.locator('.lq-page-head').count()).toBe(0);
  expect(await page.locator('.page-head, [data-page-head]').count()).toBeGreaterThan(0);
  // Round 4 structural migrations must be invisible with the family closed.
  expect(await page.locator('.lq-chip-row').count()).toBe(0);
  expect(await page.locator('#classDepartmentChips [data-department-chip]').count()).toBeGreaterThan(0);
  expect(await page.locator('#classHealthChips').count()).toBe(0);

  expect((await page.goto(`${offOrigin}/manage/teaching/semesters`))?.status()).toBe(200);
  expect(await page.locator('#semesterForm .lq-field').count()).toBe(0);
  expect(await page.locator('#semesterNameInput.form-control').count()).toBe(1);
});

// --- round 4: screenshot matrix (1440/390 x light/dark x family on/off) ------

const SHOT_ROUTES = ['/manage/teaching/classes', '/manage/teaching/semesters'];

for (const scheme of ['light', 'dark'] as const) {
  test(`S4 F screenshot matrix ${scheme} (family on and off)`, async ({ page }, info) => {
    const fixture = readFixture();
    const offOrigin = process.env.LQ_S4_PORT_OFF ? `http://127.0.0.1:${process.env.LQ_S4_PORT_OFF}` : '';
    await page.emulateMedia({ colorScheme: scheme });

    for (const [branch, origin] of [['on', ''], ['off', offOrigin]] as const) {
      if (branch === 'off' && !origin) continue;
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
        for (const route of SHOT_ROUTES) {
          expect((await page.goto(`${origin}${route}`))?.status(), `${branch} ${route}`).toBe(200);
          await settleEntranceAnimations(page);
          const name = `matrix-${branch}-${scheme}-${width}-${route.replace(/\W+/g, '_')}.png`;
          await page.screenshot({ path: info.outputPath(name), fullPage: true });
        }
      }
    }
  });
}
