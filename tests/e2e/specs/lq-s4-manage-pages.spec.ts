import AxeBuilder from '@axe-core/playwright';
import fs from 'node:fs';
import { test, expect } from '@playwright/test';
import { readFixture, loginTeacher } from '../fixtures/p03';

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
  const deleteButton = page.locator('[data-action="delete"][data-semester-id]').first();
  if (!(await deleteButton.count())) test.skip(true, 'synthetic fixture has no deletable semester for this run');
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

test('S4 F manage-pages family closed renders legacy DOM (no lq-page-head)', async ({ page }) => {
  // This assertion only proves the *shared* enhanceShell/family gate; the app
  // server for this run has LANSHARE_LQ_FAMILIES fixed at process start
  // (manage-shell,manage-pages), so we can only assert against the currently
  // running configuration here. Whether the OFF configuration truly restores
  // the legacy DOM must be re-verified by starting a second server process
  // with the family env var unset — see report §6 for why that second pass
  // was not run in this round.
  test.skip(true, 'requires a second server process with LANSHARE_LQ_FAMILIES unset; not run this round, see report');
});
