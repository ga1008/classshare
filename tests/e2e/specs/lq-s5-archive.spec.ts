import AxeBuilder from '@axe-core/playwright';
import fs from 'node:fs';
import { test, expect } from '@playwright/test';
import { readFixture, loginTeacher } from '../fixtures/p03';
import { settleEntranceAnimations } from '../fixtures/lq-s3';

// S5 R package (archive domain, manage-pages family). The server on LQ_S5_PORT
// must run with LANSHARE_LQ_FAMILIES=manage-shell,manage-pages and
// LANSHARE_LQ_PILOT=true; a second server on LQ_S5_PORT_OFF must run with
// neither, against the same synthetic runtime (runbook §8, package R, 8203/8204).

const graph = process.env.LQ_S5_GRAPH || JSON.parse(fs.readFileSync('static/assets/manifest.json', 'utf8')).revision;
const OFF_ORIGIN = `http://127.0.0.1:${process.env.LQ_S5_PORT_OFF || '8204'}`;

// 11 archive routes collapse onto 6 templates; the 5 routes that share
// manage/materials.html belong to the L package and are not exercised here.
const ARCHIVE_ROUTES = [
  '/manage/archive',                            // archive_pipeline.html
  '/manage/archive/assessment-plans',           // assessment_plans.html
  '/manage/archive/teacher-evaluations',        // teacher_evaluations.html
  '/manage/archive/attendance-reports',         // attendance_reports.html
  '/manage/archive/academic-grade-registers',   // academic_final_materials.html
  '/manage/archive/academic-exam-analyses',     // academic_final_materials.html
];

// loginTeacher() from the P03 fixture always uses the config baseURL, so the
// family-off server needs its own absolute-URL login.
async function loginTeacherAt(page: import('@playwright/test').Page, fixture: ReturnType<typeof readFixture>, origin: string) {
  await page.goto(`${origin}/teacher/login`);
  await expect(page.locator('#email')).toBeVisible();
  await page.locator('#email').fill(fixture.teacher.email);
  await page.locator('#password').fill(fixture.password);
  await Promise.all([
    page.waitForURL(/\/dashboard(?:\?|$)/, { timeout: 20_000 }),
    page.locator('button[type="submit"]').click(),
  ]);
}

for (const width of [1440, 390]) test(`S5 R archive routes render at ${width} (graph ${graph.slice(0, 8)})`, async ({ page }, info) => {
  const fixture = readFixture();
  await page.setViewportSize({ width, height: 900 });
  await loginTeacher(page, fixture);
  for (const route of ARCHIVE_ROUTES) {
    const response = await page.goto(route);
    expect(response?.status(), route).toBe(200);
    await expect(page.locator('[data-page-head]').first(), route).toHaveCount(1);
    expect(await page.evaluate(() => document.documentElement.scrollWidth), route).toBeLessThanOrEqual(width);
    await settleEntranceAnimations(page);
    // Scoped to the structures this package renders. A whole-page scan also
    // picks up .msw-note inside the shared material-selection sidebar, whose
    // contrast failure is pre-existing legacy debt outside the R package (it is
    // asserted explicitly, on both switch branches, further down this file).
    let scanner = new AxeBuilder({ page }).include('[data-page-head]');
    for (const selector of ['.lq-list', '.lq-filter-bar', '.lq-empty', 'form.att-filters', '.att-list-head']) {
      if (await page.locator(selector).count()) scanner = scanner.include(selector);
    }
    const scan = await scanner.analyze();
    expect(scan.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical'), route).toEqual([]);
    if (width === 1440) await page.screenshot({ path: info.outputPath(`teacher-${route.replace(/\W+/g, '_')}-${width}.png`), fullPage: true });
  }
});

test('S5 R archive pipeline renders a grouped lq-list and keeps every step link', async ({ page }, info) => {
  const fixture = readFixture();
  await loginTeacher(page, fixture);
  await page.goto('/manage/archive');
  await expect(page.locator('ol.lq-list--grouped[data-archive-pipeline]')).toBeVisible();
  await expect(page.locator('.ap-steps')).toHaveCount(0);

  // Every legacy step still exists as an lq-row carrying its own key, and the
  // title is still a real link to the same href the legacy anchor used.
  const steps = await page.evaluate(() => [...document.querySelectorAll('[data-archive-step]')].map((row) => ({
    key: row.getAttribute('data-archive-step'),
    state: row.getAttribute('data-archive-state'),
    titleHref: row.querySelector('.lq-row__title a')?.getAttribute('href') || null,
    actionHref: row.querySelector('.lq-row__trail a')?.getAttribute('href') || null,
    actionLabel: row.querySelector('.lq-row__trail a')?.textContent?.trim() || null,
  })));
  expect(steps.length).toBeGreaterThanOrEqual(10);
  expect(steps.map((s) => s.key)).toContain('assessment_plans');
  expect(steps.map((s) => s.key)).toContain('attendance_reports');
  for (const step of steps) {
    expect(step.titleHref, step.key || '').toBeTruthy();
    expect(step.actionHref, step.key || '').toBe(step.titleHref);
    expect(['查看', '开始'], step.key || '').toContain(step.actionLabel);
    expect(['done', 'todo'], step.key || '').toContain(step.state);
  }
  // Group headings survive the migration (one per distinct pipeline group).
  expect(await page.locator('.lq-list__heading').count()).toBeGreaterThan(1);
  await page.screenshot({ path: info.outputPath('archive-pipeline-lq-list-1440.png'), fullPage: true });
});

// The two process-material libraries are template twins; assert both against
// the same contract so a drift in either is caught.
for (const [label, route, pfx] of [
  ['assessment-plans', '/manage/archive/assessment-plans', 'ap'],
  ['teacher-evaluations', '/manage/archive/teacher-evaluations', 'te'],
] as const) {
  test(`S5 R ${label}: lq-filter-bar keeps the exact selector contract the page JS reads`, async ({ page }, info) => {
    const fixture = readFixture();
    await loginTeacher(page, fixture);
    await page.goto(route);
    await expect(page.locator('.lq-filter-bar[data-filter-bar]')).toBeVisible();
    await expect(page.locator('.manage-lp__toolbar')).toHaveCount(0);

    const probe = await page.evaluate((prefix) => {
      const read = (selector: string) => {
        const el = document.querySelector(selector) as HTMLElement | null;
        return el ? { tag: el.tagName, inField: Boolean(el.closest('.lq-field')), id: el.id, tip: el.getAttribute('data-lp-tip') !== null } : null;
      };
      const scope = document.querySelector(`[data-${prefix}-filter-scope]`) as HTMLSelectElement | null;
      return {
        search: read(`[data-${prefix}-search]`),
        scope: read(`[data-${prefix}-filter-scope]`),
        school: read(`[data-${prefix}-filter-school]`),
        college: read(`[data-${prefix}-filter-college]`),
        course: read(`[data-${prefix}-filter-course]`),
        className: read(`[data-${prefix}-filter-class]`),
        sort: read(`[data-${prefix}-sort]`),
        sortValue: (document.querySelector(`[data-${prefix}-sort]`) as HTMLSelectElement | null)?.value || null,
        clear: Boolean(document.querySelector(`[data-${prefix}-clear-filters]`)),
        grid: Boolean(document.querySelector(`[data-${prefix}-grid]`)),
        tags: Boolean(document.querySelector(`[data-${prefix}-tags]`)),
        activeFilters: Boolean(document.querySelector(`[data-${prefix}-active-filters]`)),
        scopeOptions: scope ? [...scope.options].map((o) => o.value) : null,
      };
    }, pfx);

    expect(probe.search).toEqual({ tag: 'INPUT', inField: true, id: `${pfx}FilterSearch`, tip: true });
    for (const key of ['scope', 'school', 'college', 'course', 'className', 'sort'] as const) {
      expect(probe[key], key).toMatchObject({ tag: 'SELECT', inField: true, tip: true });
    }
    // Defaults the JS assumes: sort starts at updated_desc, scope starts empty.
    expect(probe.sortValue).toBe('updated_desc');
    expect(probe.scopeOptions).toEqual(['', 'mine', 'shared', 'private', 'department', 'college', 'school']);
    // Nodes the page JS writes into must still be present and untouched.
    expect(probe.clear && probe.grid && probe.tags && probe.activeFilters).toBe(true);

    // The empty state is a real lq-empty, still toggled by the JS-owned wrapper.
    await expect(page.locator(`[data-${pfx}-empty] .lq-empty--card`)).toHaveCount(1);
    await expect(page.locator(`[data-${pfx}-empty] .lq-empty__title`)).not.toBeEmpty();

    // The real select still drives the page: changing scope updates the live
    // active-filter pills the JS renders from the select's own value.
    await page.selectOption(`[data-${pfx}-filter-scope]`, 'mine');
    await expect(page.locator(`[data-${pfx}-active-filters]`)).toContainText('我的');
    await page.click(`[data-${pfx}-clear-filters]`);
    await expect.poll(async () => page.$eval(`[data-${pfx}-filter-scope]`, (el) => (el as HTMLSelectElement).value)).toBe('');

    await page.screenshot({ path: info.outputPath(`${label}-lq-filter-bar-1440.png`), fullPage: true });
  });
}

test('S5 R attendance-reports: lq-field keeps the native form contract attendance_reports.js depends on', async ({ page }, info) => {
  const fixture = readFixture();
  await loginTeacher(page, fixture);
  await page.goto('/manage/archive/attendance-reports');
  await expect(page.locator('form.att-filters[data-att-filters]')).toBeVisible();

  const probe = await page.evaluate(() => {
    const form = document.querySelector('[data-att-filters]') as HTMLFormElement;
    const names = ['year', 'term', 'course', 'teaching_class', 'status', 'q', 'deleted'];
    const sort = document.querySelector('[data-att-sort]') as HTMLSelectElement | null;
    return {
      isForm: form.tagName === 'FORM',
      role: form.getAttribute('role'),
      controls: names.map((name) => {
        const el = form.elements.namedItem(name) as HTMLElement | null;
        return { name, found: Boolean(el), tag: el ? el.tagName : null, inField: Boolean(el && el.closest('.lq-field')) };
      }),
      formDataKeys: [...new FormData(form).keys()].sort(),
      deletedValue: (form.elements.namedItem('deleted') as HTMLSelectElement).value,
      hasReset: Boolean(form.querySelector('button[type="reset"]')),
      sort: sort ? { tag: sort.tagName, value: sort.value, inField: Boolean(sort.closest('.lq-field')) } : null,
    };
  });

  expect(probe.isForm).toBe(true);
  // role="search" cannot be carried through lq_field, so it must still be on
  // the hand-written <form> element the lq branch keeps.
  expect(probe.role).toBe('search');
  expect(probe.controls).toEqual([
    { name: 'year', found: true, tag: 'SELECT', inField: true },
    { name: 'term', found: true, tag: 'SELECT', inField: true },
    { name: 'course', found: true, tag: 'SELECT', inField: true },
    { name: 'teaching_class', found: true, tag: 'SELECT', inField: true },
    { name: 'status', found: true, tag: 'SELECT', inField: true },
    { name: 'q', found: true, tag: 'INPUT', inField: true },
    { name: 'deleted', found: true, tag: 'SELECT', inField: true },
  ]);
  expect(probe.formDataKeys).toEqual(['course', 'deleted', 'q', 'status', 'teaching_class', 'term', 'year']);
  expect(probe.deletedValue).toBe('0');
  expect(probe.hasReset).toBe(true);
  expect(probe.sort).toEqual({ tag: 'SELECT', value: 'updated_desc', inField: true });

  // Reset must restore the rendered defaults, which is what the page relies on.
  await page.selectOption('[data-att-filters] [name="status"]', 'confirmed');
  await page.fill('[data-att-filters] [name="q"]', 'lq-probe');
  await page.click('[data-att-filters] button[type="reset"]');
  await expect.poll(async () => page.$eval('[data-att-filters] [name="status"]', (el) => (el as HTMLSelectElement).value)).toBe('');
  await expect.poll(async () => page.$eval('[data-att-filters] [name="q"]', (el) => (el as HTMLInputElement).value)).toBe('');

  await page.screenshot({ path: info.outputPath('attendance-lq-fields-1440.png'), fullPage: true });
});

for (const [label, route] of [
  ['grade-registers', '/manage/archive/academic-grade-registers'],
  ['exam-analyses', '/manage/archive/academic-exam-analyses'],
] as const) {
  test(`S5 R ${label}: lq-empty carries the working sync action`, async ({ page }, info) => {
    const fixture = readFixture();
    await loginTeacher(page, fixture);
    await page.goto(route);
    await expect(page.locator('[data-afm-empty] .lq-empty--card')).toHaveCount(1);
    await expect(page.locator('.afm-empty')).toHaveCount(0);
    // The action inside lq-empty is a real trigger, not decoration.
    const trigger = page.locator('[data-afm-empty] .lq-empty__actions [data-afm-open-sync]');
    await expect(trigger).toBeVisible();
    await trigger.click();
    await expect.poll(async () => page.$eval('[data-afm-sync-dialog]', (el) => (el as HTMLDialogElement).open)).toBe(true);
    // Native <dialog> Escape must both close it and return focus to the trigger.
    await page.keyboard.press('Escape');
    await expect.poll(async () => page.$eval('[data-afm-sync-dialog]', (el) => (el as HTMLDialogElement).open)).toBe(false);
    expect(await page.evaluate(() => document.activeElement?.hasAttribute('data-afm-open-sync'))).toBe(true);
    if (label === 'grade-registers') await page.screenshot({ path: info.outputPath(`${label}-lq-empty-1440.png`), fullPage: true });
  });
}

test('S5 R academic final materials: Escape on the preview dialog releases the iframe', async ({ page }) => {
  const fixture = readFixture();
  await loginTeacher(page, fixture);
  await page.goto('/manage/archive/academic-grade-registers');
  // Drive the dialog the way openPreview() does, then leave via Escape only.
  await page.evaluate(() => {
    const dialog = document.querySelector('[data-afm-preview-dialog]') as HTMLDialogElement;
    const frame = document.querySelector('[data-afm-preview-frame]') as HTMLIFrameElement;
    frame.src = '/static/assets/manifest.json';
    dialog.showModal();
  });
  await expect.poll(async () => page.$eval('[data-afm-preview-dialog]', (el) => (el as HTMLDialogElement).open)).toBe(true);
  await page.keyboard.press('Escape');
  await expect.poll(async () => page.$eval('[data-afm-preview-dialog]', (el) => (el as HTMLDialogElement).open)).toBe(false);
  // Before the fix only the close button cleared the frame, so Escape left the
  // previous document loaded in the hidden iframe.
  await expect.poll(async () => page.$eval('[data-afm-preview-frame]', (el) => el.getAttribute('src'))).toBe('about:blank');
});

test('S5 R manage-pages family closed renders the legacy archive DOM', async ({ page }) => {
  const fixture = readFixture();
  await loginTeacherAt(page, fixture, OFF_ORIGIN);
  const probe = async (route: string) => {
    const response = await page.goto(`${OFF_ORIGIN}${route}`);
    expect(response?.status(), route).toBe(200);
    return page.evaluate(() => ({
      lqList: document.querySelectorAll('.lq-list').length,
      lqFilterBar: document.querySelectorAll('.lq-filter-bar').length,
      lqField: document.querySelectorAll('.lq-field').length,
      lqEmpty: document.querySelectorAll('.lq-empty').length,
      lqPageHead: document.querySelectorAll('.lq-page-head').length,
      legacyPageHead: document.querySelectorAll('.page-head').length,
    }));
  };

  const pipeline = await probe('/manage/archive');
  expect(pipeline.lqList + pipeline.lqFilterBar + pipeline.lqField + pipeline.lqEmpty + pipeline.lqPageHead).toBe(0);
  expect(await page.locator('.ap-steps').count()).toBe(1);
  expect(pipeline.legacyPageHead).toBeGreaterThan(0);

  const plans = await probe('/manage/archive/assessment-plans');
  expect(plans.lqFilterBar + plans.lqField + plans.lqEmpty + plans.lqPageHead).toBe(0);
  expect(await page.locator('.manage-lp__toolbar').count()).toBe(1);
  expect(await page.locator('.manage-lp__empty[data-ap-empty]').count()).toBe(1);
  expect(await page.locator('.manage-lp__filters select').count()).toBe(6);

  const evaluations = await probe('/manage/archive/teacher-evaluations');
  expect(evaluations.lqFilterBar + evaluations.lqField + evaluations.lqEmpty).toBe(0);
  expect(await page.locator('.manage-lp__filters select').count()).toBe(6);

  const attendance = await probe('/manage/archive/attendance-reports');
  expect(attendance.lqField).toBe(0);
  expect(await page.locator('form.att-filters > label').count()).toBe(7);
  expect(await page.locator('label.att-sort > select[data-att-sort]').count()).toBe(1);

  const registers = await probe('/manage/archive/academic-grade-registers');
  expect(registers.lqEmpty).toBe(0);
  expect(await page.locator('.afm-empty[data-afm-empty]').count()).toBe(1);
});

// Recorded, not hidden: the shared material-selection sidebar rendered by
// static/js/material_selection_panel.js styles .msw-note with a hardcoded
// #6a7890 (static/css/material_workflows.css), giving 4.46:1 on white where
// WCAG AA needs 4.5:1. Neither file belongs to the R package and neither the
// element nor the rule is touched by this migration, so the assertion below
// pins it as identical on both switch branches instead of pretending it is
// clean. Delete this test once the owning package moves .msw-note onto --ls-*.
test('S5 R pre-existing: .msw-note contrast fails identically with the family on and off', async ({ page }) => {
  const fixture = readFixture();
  const measure = async () => {
    await page.goto('/manage/archive/assessment-plans');
    await settleEntranceAnimations(page);
    const scan = await new AxeBuilder({ page }).include('.msw-sidebar').analyze();
    return scan.violations
      .filter((v) => v.impact === 'serious' || v.impact === 'critical')
      .map((v) => v.id)
      .sort();
  };

  await loginTeacher(page, fixture);
  const familyOn = await measure();

  await loginTeacherAt(page, fixture, OFF_ORIGIN);
  await page.goto(`${OFF_ORIGIN}/manage/archive/assessment-plans`);
  await settleEntranceAnimations(page);
  const offScan = await new AxeBuilder({ page }).include('.msw-sidebar').analyze();
  const familyOff = offScan.violations
    .filter((v) => v.impact === 'serious' || v.impact === 'critical')
    .map((v) => v.id)
    .sort();

  expect(familyOn).toEqual(['color-contrast']);
  // Identical with the switch closed proves the migration did not introduce it.
  expect(familyOff).toEqual(familyOn);
});

for (const scheme of ['light', 'dark'] as const) {
  test(`S5 R screenshot matrix ${scheme} (family on and off)`, async ({ page }, info) => {
    const fixture = readFixture();
    await page.emulateMedia({ colorScheme: scheme });
    for (const [state, origin] of [['on', ''], ['off', OFF_ORIGIN]] as const) {
      if (origin) await loginTeacherAt(page, fixture, origin); else await loginTeacher(page, fixture);
      for (const width of [1440, 390]) {
        await page.setViewportSize({ width, height: 900 });
        for (const route of ARCHIVE_ROUTES) {
          await page.goto(`${origin}${route}`);
          await settleEntranceAnimations(page);
          await page.screenshot({
            path: info.outputPath(`matrix-${state}-${scheme}-${width}-${route.replace(/\W+/g, '_')}.png`),
            fullPage: true,
          });
        }
      }
    }
  });
}
