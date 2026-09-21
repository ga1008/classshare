import AxeBuilder from '@axe-core/playwright';
import fs from 'node:fs';
import { test, expect, type Page } from '@playwright/test';
import { readFixture, loginTeacher } from '../fixtures/p03';
import { settleEntranceAnimations } from '../fixtures/lq-s3';

// S5 X package (academic domain, manage-pages family). Uses the plain P03
// fixture (not the S3-extended one) because these routes need no exam/report
// card scenario data -- same choice as the F package spec.
//
// Server (family ON) must run with LANSHARE_LQ_FAMILIES=manage-shell,manage-pages
// and LANSHARE_LQ_PILOT=false on LQ_S5_PORT (8205); a second server with neither
// switch runs on LQ_S5_PORT_OFF (8206). See runbook §8, X row.

const graph = process.env.LQ_S5_GRAPH || JSON.parse(fs.readFileSync('static/assets/manifest.json', 'utf8')).revision;
const OFF_PORT = process.env.LQ_S5_PORT_OFF || '8206';
const OFF_BASE = `http://127.0.0.1:${OFF_PORT}`;

const ACADEMIC_ROUTES = [
  '/manage/academic',
  '/manage/academic/classrooms',
  '/manage/academic/course-schedule',
  '/manage/academic/gongwen',
  '/manage/academic/integrations',
  '/manage/academic/gongwen-sync',
  '/manage/academic/smart-classroom',
];

// The integration pages talk to real external academic systems. Every spec in
// this file installs this guard before touching those pages so that a stray
// click can never reach the network; a blocked call fails the test loudly
// instead of silently contacting a school server.
async function blockExternalSync(page: Page): Promise<string[]> {
  const blocked: string[] = [];
  for (const pattern of [
    '**/api/manage/system/academic/**',
    '**/api/manage/system/smart-classroom/**',
    '**/api/manage/system/gongwen/**',
    '**/api/manage/gongwen/**',
    '**/api/manage/course-schedule/**',
    '**/api/manage/classrooms/**',
  ]) {
    await page.route(pattern, async (route) => {
      const method = route.request().method();
      if (method === 'GET') return route.continue();
      blocked.push(`${method} ${route.request().url()}`);
      await route.fulfill({ status: 503, contentType: 'application/json', body: '{"detail":"blocked by spec"}' });
    });
  }
  return blocked;
}


// Measures the same route on the family-OFF server so a pre-existing layout
// defect cannot be mistaken for an LQ regression (and cannot be silently
// tolerated either: the LQ branch must never be wider than the baseline).
async function baselineScrollWidth(browser: any, fixture: any, route: string, width: number): Promise<number> {
  const context = await browser.newContext({ baseURL: OFF_BASE, viewport: { width, height: 900 } });
  try {
    const page = await context.newPage();
    await loginTeacher(page, fixture);
    expect((await page.goto(route))?.status(), `${route} (family off)`).toBe(200);
    return await page.evaluate(() => document.documentElement.scrollWidth);
  } finally {
    await context.close();
  }
}

for (const width of [1440, 390]) test(`S5 X academic routes render at ${width}`, async ({ page }, info) => {
  const fixture = readFixture();
  await page.setViewportSize({ width, height: 900 });
  await loginTeacher(page, fixture);
  const blocked = await blockExternalSync(page);
  for (const route of ACADEMIC_ROUTES) {
    const response = await page.goto(route);
    expect(response?.status(), route).toBe(200);
    await expect(page.locator('[data-page-head]').first(), route).toHaveCount(1);
    // /manage/academic/course-schedule overflows horizontally at 390 in the
    // BASELINE too (measured: 550px with the family off, 533px with it on --
    // the offenders are `.cs-progress__anchor` / `.cs-card__bar`, both rendered
    // by manage_course_schedule.js, a file no S5 package may touch). Asserting
    // <= width there would report someone else's pre-existing defect as ours,
    // so that route is held to "no worse than the family-off baseline" instead
    // and the defect is written up in the report rather than hidden.
    const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
    if (route === '/manage/academic/course-schedule') {
      expect(scrollWidth, route).toBeLessThanOrEqual(await baselineScrollWidth(page.context().browser(), fixture, route, width));
    } else {
      expect(scrollWidth, route).toBeLessThanOrEqual(width);
    }
    await settleEntranceAnimations(page);
    const scan = await new AxeBuilder({ page }).include('[data-page-head]').analyze();
    expect(scan.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical'), route).toEqual([]);
    if (width === 1440) await page.screenshot({ path: info.outputPath(`teacher-${route.replace(/\W+/g, '_')}-${width}.png`), fullPage: true });
  }
  expect(blocked, 'no external sync call may be triggered by merely loading a page').toEqual([]);
  expect(graph).toBeTruthy();
});

// Every credential control the three integration pages own, with the exact
// id/name/type/autocomplete/required that their JS (getElementById + new
// FormData(form)) depends on. Drift in any one of them breaks a real login.
const CREDENTIAL_CONTRACT: Array<{ route: string; form: string; open: string; controls: Array<Record<string, unknown>> }> = [
  {
    route: '/manage/academic/integrations', form: '#academic-credential-form', open: '#academic-account-manage-btn',
    controls: [
      { id: 'academic-school-code', tag: 'SELECT', name: 'school_code', type: 'select-one', required: true, autocomplete: '', inField: true },
      { id: 'academic-username', tag: 'INPUT', name: 'username', type: 'text', required: true, autocomplete: 'username', inField: true },
      { id: 'academic-password', tag: 'INPUT', name: 'password', type: 'password', required: true, autocomplete: 'current-password', inField: true },
    ],
  },
  {
    route: '/manage/academic/gongwen-sync', form: '#gw-credential-form', open: '#gw-account-manage-btn',
    controls: [
      { id: 'gw-system-code', tag: 'SELECT', name: 'system_code', type: 'select-one', required: true, autocomplete: '', inField: true },
      { id: 'gw-username', tag: 'INPUT', name: 'username', type: 'text', required: true, autocomplete: 'username', inField: true },
      { id: 'gw-password', tag: 'INPUT', name: 'password', type: 'password', required: true, autocomplete: 'current-password', inField: true },
    ],
  },
  {
    route: '/manage/academic/smart-classroom', form: '#smart-classroom-credential-form', open: '#smart-account-manage-btn',
    controls: [
      { id: 'smart-platform-code', tag: 'SELECT', name: 'platform_code', type: 'select-one', required: true, autocomplete: '', inField: true },
      { id: 'smart-username', tag: 'INPUT', name: 'username', type: 'text', required: true, autocomplete: 'username', inField: true },
      { id: 'smart-password', tag: 'INPUT', name: 'password', type: 'password', required: true, autocomplete: 'current-password', inField: true },
    ],
  },
];

for (const entry of CREDENTIAL_CONTRACT) {
  test(`S5 X ${entry.route}: lq-field keeps the credential id/name/type contract`, async ({ page }, info) => {
    const fixture = readFixture();
    await loginTeacher(page, fixture);
    const blocked = await blockExternalSync(page);
    expect((await page.goto(entry.route))?.status()).toBe(200);

    const actual = await page.evaluate((ids: string[]) => ids.map((id) => {
      const el = document.getElementById(id) as HTMLInputElement | HTMLSelectElement | null;
      if (!el) return { id, missing: true };
      return {
        id, tag: el.tagName, name: el.name, type: el.type,
        required: el.required,
        // A select carries no autocomplete attribute in either branch.
        autocomplete: el.getAttribute('autocomplete') || '',
        inField: Boolean(el.closest('.lq-field')),
      };
    }), entry.controls.map((c) => c.id as string));
    expect(actual).toEqual(entry.controls);

    // The credential controls live inside the account modal; open it so they
    // are actually visible before scanning and screenshotting.
    await page.locator(entry.open).click();
    await expect(page.locator(`${entry.form} #${entry.controls[1].id}`)).toBeVisible({ timeout: 5000 });

    await settleEntranceAnimations(page);
    const scan = await new AxeBuilder({ page }).include(entry.form).analyze();
    expect(scan.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical')).toEqual([]);

    // Required still blocks submission, and the form becomes submittable once
    // filled -- proof lq-field dropped neither `required` nor the name bindings.
    expect(await page.locator(entry.form).evaluate((form: HTMLFormElement) => form.checkValidity())).toBe(false);
    await page.locator(`#${entry.controls[1].id}`).fill('spec-user');
    await page.locator(`#${entry.controls[2].id}`).fill('spec-secret');
    expect(await page.locator(entry.form).evaluate((form: HTMLFormElement) => form.checkValidity())).toBe(true);
    expect(await page.locator(entry.form).evaluate((form: HTMLFormElement) =>
      [...new FormData(form).keys()].sort())).toEqual(entry.controls.map((c) => c.name as string).sort());

    await page.screenshot({ path: info.outputPath(`credential-form-${entry.route.replace(/\W+/g, '_')}.png`) });
    expect(blocked, 'opening the account modal must not call the school system').toEqual([]);
  });
}

test('S5 X manage-pages family closed renders the legacy academic DOM', async ({ browser }) => {
  const fixture = readFixture();
  const context = await browser.newContext({ baseURL: OFF_BASE });
  const page = await context.newPage();
  try {
    await loginTeacher(page, fixture);
    for (const route of ['/manage/academic/integrations', '/manage/academic/gongwen-sync', '/manage/academic/smart-classroom']) {
      expect((await page.goto(route))?.status(), route).toBe(200);
      await expect(page.locator('.lq-page-head'), route).toHaveCount(0);
      await expect(page.locator('.lq-field'), route).toHaveCount(0);
      await expect(page.locator('.lq-chip-row'), route).toHaveCount(0);
      await expect(page.locator('.lq-card'), route).toHaveCount(0);
      await expect(page.locator('.page-head').first(), route).toBeVisible();
      expect(await page.locator('form .form-group .form-control').count(), route).toBe(3);
    }
  } finally {
    await context.close();
  }
});

test('S5 X classrooms: lq-field filters and the lq chip row proxy the same real selects', async ({ page }, info) => {
  const fixture = readFixture();
  await loginTeacher(page, fixture);
  await blockExternalSync(page);
  expect((await page.goto('/manage/academic/classrooms'))?.status()).toBe(200);

  // The real controls survive the migration -- exactly one of each, still a
  // <select>, still inside an .lq-field.
  const controls = await page.evaluate(() => ['classroomCampusFilter', 'classroomBuildingFilter',
    'classroomTypeFilter', 'classroomAvailabilityFilter', 'classroomPageSizeSelect',
    'freeRoomCampusSelect', 'freeRoomBuildingSelect', 'freeRoomTypeSelect', 'freeRoomNameInput',
    'freeRoomSemesterSelect', 'classroomSearchInput'].map((id) => {
    const el = document.getElementById(id) as HTMLSelectElement | HTMLInputElement | null;
    return { id, tag: el?.tagName || null, inField: Boolean(el?.closest('.lq-field')) };
  }));
  expect(controls).toEqual([
    { id: 'classroomCampusFilter', tag: 'SELECT', inField: true },
    { id: 'classroomBuildingFilter', tag: 'SELECT', inField: true },
    { id: 'classroomTypeFilter', tag: 'SELECT', inField: true },
    { id: 'classroomAvailabilityFilter', tag: 'SELECT', inField: true },
    // Deliberately NOT migrated: lq-field is a block field and
    // .classroom-list-toolbar is an inline flex row, so migrating it visibly
    // pushes the pagination summary onto its own line (screenshot-verified).
    { id: 'classroomPageSizeSelect', tag: 'SELECT', inField: false },
    { id: 'freeRoomCampusSelect', tag: 'SELECT', inField: true },
    { id: 'freeRoomBuildingSelect', tag: 'SELECT', inField: true },
    { id: 'freeRoomTypeSelect', tag: 'SELECT', inField: true },
    { id: 'freeRoomNameInput', tag: 'INPUT', inField: true },
    // Deliberately NOT migrated (see report): the semester select carries
    // per-option data-start-date/data-week-count that lq_select cannot express,
    // and the search box's only label is an icon.
    { id: 'freeRoomSemesterSelect', tag: 'SELECT', inField: false },
    { id: 'classroomSearchInput', tag: 'INPUT', inField: false },
  ]);

  // The semester options still carry the data attributes manage_classrooms.js
  // reads (`option.dataset.weekCount`) -- that is why it was left native.
  expect(await page.locator('#freeRoomSemesterSelect option[data-week-count]').count()).toBeGreaterThan(0);

  // Quick-type chip row is a real LQ filter chip row proxying #classroomTypeFilter.
  const chips = page.locator('#classroomQuickTypeChips button.lq-chip--filter[data-room-type]');
  await expect(chips.first()).toBeVisible({ timeout: 5000 });
  expect(await chips.count()).toBeGreaterThan(0);
  expect(await chips.first().getAttribute('aria-pressed')).toBe('true');

  const typed = page.locator('#classroomQuickTypeChips button.lq-chip--filter[data-room-type]:not([data-room-type=""])');
  if (await typed.count()) {
    const value = await typed.first().getAttribute('data-room-type');
    await typed.first().click();
    await expect(typed.first()).toHaveAttribute('aria-pressed', 'true');
    expect(await page.locator('#classroomTypeFilter').inputValue()).toBe(value);
    await expect(chips.first()).toHaveAttribute('aria-pressed', 'false');
    // Changing the real select back must drag the chip row with it.
    await page.locator('#classroomTypeFilter').selectOption('');
    await expect(typed.first()).toHaveAttribute('aria-pressed', 'false');
  }

  await settleEntranceAnimations(page);
  const scan = await new AxeBuilder({ page }).include('.classroom-control-panel').analyze();
  expect(scan.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical')).toEqual([]);
  await page.screenshot({ path: info.outputPath('classrooms-filters-1440.png'), fullPage: true });
});

test('S5 X course-schedule: lq-field keeps every DOM hook manage_course_schedule.js queries', async ({ page }, info) => {
  const fixture = readFixture();
  await loginTeacher(page, fixture);
  await blockExternalSync(page);
  expect((await page.goto('/manage/academic/course-schedule'))?.status()).toBe(200);

  // Exhaustive list grepped out of static/js/manage_course_schedule.js (a file
  // this package may not modify). Every one must still resolve after migration.
  const hooks = ['[data-cs-term]', '[data-cs-course]', '[data-cs-class]', '[data-cs-courses]',
    '[data-cs-deck]', '[data-cs-reset]', '[data-cs-source]', '[data-cs-summary]',
    '[data-cs-sync-time]', '[data-cs-sync]', '[data-cs-toast]', '[data-academic-schedule-sync]'];
  const found = await page.evaluate((selectors: string[]) => selectors.map((selector) => ({
    selector, count: document.querySelectorAll(selector).length,
    tag: document.querySelector(selector)?.tagName || null,
  })), hooks);
  for (const hook of found) expect(hook.count, hook.selector).toBeGreaterThan(0);
  expect(found.find((h) => h.selector === '[data-cs-term]')?.tag).toBe('SELECT');
  expect(found.find((h) => h.selector === '[data-cs-course]')?.tag).toBe('SELECT');
  expect(found.find((h) => h.selector === '[data-cs-class]')?.tag).toBe('SELECT');
  expect(await page.locator('#course-schedule-boot').count()).toBe(1);
  // The reset button stays native on purpose (the JS drives its .disabled).
  expect(await page.locator('[data-cs-reset]').evaluate((el) => el.className)).toBe('cs-reset-btn');

  await page.screenshot({ path: info.outputPath('course-schedule-toolbar-1440.png'), fullPage: true });
});

// --- bespoke-overlay audit regressions ------------------------------------
// Before this round none of these overlays handled Escape or returned focus;
// the gongwen scope editor could not be dismissed with the keyboard at all.
const OVERLAY_ESCAPE: Array<{ route: string; trigger: string; overlay: string }> = [
  { route: '/manage/academic/integrations', trigger: '#academic-account-manage-btn', overlay: '#academic-account-modal' },
  { route: '/manage/academic/gongwen-sync', trigger: '#gw-account-manage-btn', overlay: '#gw-account-modal' },
];

for (const entry of OVERLAY_ESCAPE) {
  test(`S5 X ${entry.route}: the bespoke account overlay now Escape-closes and returns focus`, async ({ page }, info) => {
    const fixture = readFixture();
    await loginTeacher(page, fixture);
    const blocked = await blockExternalSync(page);
    expect((await page.goto(entry.route))?.status()).toBe(200);

    const trigger = page.locator(entry.trigger);
    const overlay = page.locator(entry.overlay);
    await expect(overlay).toBeHidden();
    await trigger.click();
    await expect(overlay).toBeVisible({ timeout: 5000 });
    await page.screenshot({ path: info.outputPath(`overlay-open-${entry.route.replace(/\W+/g, '_')}.png`) });

    await page.keyboard.press('Escape');
    await expect(overlay).toBeHidden({ timeout: 5000 });
    await expect(trigger).toBeFocused();

    // The close button path must return focus too.
    await trigger.click();
    await expect(overlay).toBeVisible({ timeout: 5000 });
    await page.locator(`${entry.overlay} [id$="modal-close"]`).click();
    await expect(overlay).toBeHidden({ timeout: 5000 });
    await expect(trigger).toBeFocused();
    expect(blocked).toEqual([]);
  });
}

test('S5 X gongwen: Escape now closes the scope editor and returns focus to its row button', async ({ page }, info) => {
  const fixture = readFixture();
  await loginTeacher(page, fixture);
  await blockExternalSync(page);
  expect((await page.goto('/manage/academic/gongwen'))?.status()).toBe(200);

  // The row comes from .codex-temp/x-patch/seed_gongwen.py, which inserts one
  // synthetic gongwen_documents row into this package's own runtime DB. This is
  // a hard precondition, never a skip: without a row the Escape fix would go
  // unexercised and the test would pass on nothing.
  const scopeBtn = page.locator('[data-scope-edit]').first();
  await expect(scopeBtn).toBeVisible({ timeout: 10000 });
  await page.screenshot({ path: info.outputPath('gongwen-list-1440.png'), fullPage: true });
  const scopeModal = page.locator('#gw-scope-modal');
  await scopeBtn.click();
  await expect(scopeModal).toBeVisible({ timeout: 10000 });
  await page.keyboard.press('Escape');
  await expect(scopeModal).toBeHidden({ timeout: 5000 });
  await expect(scopeBtn).toBeFocused();
});

test('S5 X a stacked LQ dialog keeps Escape ownership over the academic overlays', async ({ page }) => {
  const fixture = readFixture();
  await loginTeacher(page, fixture);
  await blockExternalSync(page);
  expect((await page.goto('/manage/academic/integrations'))?.status()).toBe(200);

  const trigger = page.locator('#academic-account-manage-btn');
  const overlay = page.locator('#academic-account-modal');
  await trigger.click();
  await expect(overlay).toBeVisible({ timeout: 5000 });

  // Raise a real LQ.confirm on top of the bespoke backdrop.
  const confirmPromise = page.evaluate(async () => {
    const { LQ } = await import('/static/js/lq/index.js');
    return LQ.confirm({ title: '堆叠确认', description: '仅用于验证 Escape 归属。' });
  });
  const dialog = page.locator('[data-lq-dialog]:not([hidden])').last();
  await expect(dialog).toBeVisible({ timeout: 5000 });

  await page.keyboard.press('Escape');
  await expect(dialog).toBeHidden({ timeout: 5000 });
  expect(await confirmPromise).toBe(false);
  // The page overlay underneath must still be open: the old unconditional
  // handler would have torn it down in the same keystroke.
  await expect(overlay).toBeVisible();
});

// --- visual matrix ---------------------------------------------------------
const MATRIX_ROUTES = [
  '/manage/academic/integrations',
  '/manage/academic/gongwen-sync',
  '/manage/academic/classrooms',
  '/manage/academic/course-schedule',
];

for (const colorScheme of ['light', 'dark'] as const) {
  test(`S5 X screenshot matrix ${colorScheme} (family on and off)`, async ({ browser }, info) => {
    const fixture = readFixture();
    for (const [state, base] of [['on', undefined], ['off', OFF_BASE]] as const) {
      const context = await browser.newContext({ ...(base ? { baseURL: base } : {}), colorScheme });
      try {
        const page = await context.newPage();
        await blockExternalSync(page);
        await loginTeacher(page, fixture);
        for (const width of [1440, 390]) {
          await page.setViewportSize({ width, height: 900 });
          for (const route of MATRIX_ROUTES) {
            expect((await page.goto(route))?.status(), `${state} ${route}`).toBe(200);
            await settleEntranceAnimations(page);
            await page.screenshot({
              path: info.outputPath(`matrix-${state}-${colorScheme}-${width}-${route.replace(/\W+/g, '_')}.png`),
              fullPage: true,
            });
          }
        }
      } finally {
        await context.close();
      }
    }
  });
}
