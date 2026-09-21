import AxeBuilder from '@axe-core/playwright';
import fs from 'node:fs';
import { test, expect, type Page } from '@playwright/test';
import { readFixture, loginTeacher, type P03Fixture } from '../fixtures/p03';
import { settleEntranceAnimations } from '../fixtures/lq-s3';

// S5 L package (library/resource domain, manage-pages family). Uses the plain
// P03 fixture (not the S3-extended one) because these routes need no exam or
// report-card scenario data -- same choice as the F and X package specs.
//
// Server (family ON) runs with LANSHARE_LQ_FAMILIES=manage-shell,manage-pages
// and LANSHARE_LQ_PILOT=false on LQ_S5_PORT (8201); a second server with
// neither switch runs on LQ_S5_PORT_OFF (8202). See runbook §8, L row.

const graph = process.env.LQ_S5_GRAPH || JSON.parse(fs.readFileSync('static/assets/manifest.json', 'utf8')).revision;
const OFF_PORT = process.env.LQ_S5_PORT_OFF || '8202';
const OFF_BASE = `http://127.0.0.1:${OFF_PORT}`;

const LIBRARY_ROUTES = [
  '/manage/library',
  '/manage/library/courses',
  '/manage/library/textbooks',
  '/manage/library/exams',
  '/manage/library/materials',
  '/manage/library/lesson-plans',
  '/manage/library/polls',
];

// The library pages can reach AI endpoints (hub AI search, textbook intro/catalog
// formatting, lesson-plan generation). Every spec installs this guard before
// touching them so a stray click can never spend a real AI call; a blocked call
// fails the test loudly instead of quietly hitting a provider.
async function blockAiAndWrites(page: Page): Promise<string[]> {
  const blocked: string[] = [];
  for (const pattern of [
    '**/api/materials/hub/ai-search',
    '**/api/manage/textbooks/**',
    '**/api/manage/lesson-plans/**',
    '**/api/manage/courses/**',
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

async function loginOn(page: Page, fixture: P03Fixture, origin = '') {
  await page.goto(`${origin}/teacher/login`);
  await expect(page.locator('#email')).toBeVisible();
  await page.locator('#email').fill(fixture.teacher.email);
  await page.locator('#password').fill(fixture.password);
  await Promise.all([
    page.waitForURL(/\/dashboard(?:\?|$)/, { timeout: 20_000 }),
    page.locator('button[type="submit"]').click(),
  ]);
}

for (const width of [1440, 390]) test(`S5 L library routes render at ${width}`, async ({ page }, info) => {
  const fixture = readFixture();
  await page.setViewportSize({ width, height: 900 });
  await loginTeacher(page, fixture);
  const blocked = await blockAiAndWrites(page);
  for (const route of LIBRARY_ROUTES) {
    const response = await page.goto(route);
    expect(response?.status(), route).toBe(200);
    await expect(page.locator('[data-page-head]').first(), route).toHaveCount(1);
    expect(await page.evaluate(() => document.documentElement.scrollWidth), route).toBeLessThanOrEqual(width);
    await settleEntranceAnimations(page);
    const scan = await new AxeBuilder({ page }).include('[data-page-head]').analyze();
    expect(scan.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical'), route).toEqual([]);
    if (width === 1440) await page.screenshot({ path: info.outputPath(`teacher-${route.replace(/\W+/g, '_')}-${width}.png`), fullPage: true });
  }
  expect(blocked, 'merely loading a library page must not trigger an AI or write call').toEqual([]);
  expect(graph).toBeTruthy();
});

// Every control manage_textbooks.js resolves with getElementById, with the exact
// id/tag/type it depends on, plus whether it now sits inside an .lq-field. Drift
// in any one of them silently breaks the real filter or the real save.
test('S5 L textbooks: lq-filter-bar keeps every id/type manage_textbooks.js resolves', async ({ page }, info) => {
  const fixture = readFixture();
  await loginTeacher(page, fixture);
  await blockAiAndWrites(page);
  expect((await page.goto('/manage/library/textbooks'))?.status()).toBe(200);
  await expect(page.locator('#textbookCardGrid')).toBeAttached();

  const contract = await page.evaluate(() => ['textbookSearchInput', 'textbookPublisherFilter', 'textbookTagFilter', 'textbookAttachmentFilter'].map((id) => {
    const node = document.getElementById(id) as HTMLInputElement | HTMLSelectElement | null;
    return node && { id, tag: node.tagName, type: node.type, inField: Boolean(node.closest('.lq-field')), inBar: Boolean(node.closest('.lq-filter-bar')) };
  }));
  expect(contract).toEqual([
    { id: 'textbookSearchInput', tag: 'INPUT', type: 'search', inField: true, inBar: true },
    { id: 'textbookPublisherFilter', tag: 'SELECT', type: 'select-one', inField: true, inBar: true },
    { id: 'textbookTagFilter', tag: 'SELECT', type: 'select-one', inField: true, inBar: true },
    { id: 'textbookAttachmentFilter', tag: 'SELECT', type: 'select-one', inField: true, inBar: true },
  ]);
  // Both the card and the clear button survived the migration as real nodes.
  await expect(page.locator('#textbookLibraryCard.lq-card')).toHaveCount(1);
  await expect(page.locator('#textbookClearFiltersBtn')).toBeVisible();

  // renderCards() drives #textbookEmptyState.hidden, and the container (not the
  // lq-empty itself) carries the id because lq_empty's attrs allowlist has no
  // `hidden`. The synthetic runtime starts with zero textbooks, so the migrated
  // empty state must be showing right now.
  await expect(page.locator('#textbookEmptyState')).toBeVisible();
  await expect(page.locator('#textbookEmptyState .lq-empty')).toHaveCount(1);

  // Seed one real textbook through the real save endpoint (page.request carries
  // the session cookies and is not touched by the page.route guard above), then
  // prove the empty state hides and the filter still drives it both ways.
  const seeded = await page.request.post('/api/manage/textbooks/save', {
    multipart: { title: 'LQ S5 合成教材', publisher: '合成出版社', authors_json: '[]', tags_json: '[]', remove_attachment: 'false' },
  });
  expect(seeded.status(), await seeded.text()).toBeLessThan(400);
  await page.reload();
  await expect(page.locator('#textbookEmptyState')).toBeHidden();
  // Not toHaveCount(1): this seed accumulates across re-runs of the same runtime.
  expect(await page.locator('#textbookCardGrid .academic-resource-card').count()).toBeGreaterThan(0);

  await page.locator('#textbookSearchInput').fill('zzz-no-such-textbook-zzz');
  await expect(page.locator('#textbookEmptyState')).toBeVisible();
  await page.locator('#textbookClearFiltersBtn').click();
  await expect(page.locator('#textbookSearchInput')).toHaveValue('');
  await expect(page.locator('#textbookEmptyState')).toBeHidden();

  await settleEntranceAnimations(page);
  const scan = await new AxeBuilder({ page }).include('.lq-filter-bar').analyze();
  expect(scan.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical')).toEqual([]);
  await page.screenshot({ path: info.outputPath('textbooks-lq-filter-bar-1440.png'), fullPage: true });
});

test('S5 L textbooks: lq-field form keeps the name/required contract the save endpoint needs', async ({ page }, info) => {
  const fixture = readFixture();
  await loginTeacher(page, fixture);
  await blockAiAndWrites(page);
  await page.goto('/manage/library/textbooks');
  await page.locator('#openTextbookCreateBtn').click();
  await expect(page.locator('#textbookModalBackdrop')).toHaveClass(/is-open/);

  const fields = await page.evaluate(() => ['textbookTitleInput', 'textbookPublisherInput', 'textbookPublicationDateInput', 'textbookAttachmentInput', 'textbookIdInput'].map((id) => {
    const node = document.getElementById(id) as HTMLInputElement | null;
    return node && { id, tag: node.tagName, type: node.type, name: node.name, required: node.required, inField: Boolean(node.closest('.lq-field')) };
  }));
  expect(fields).toEqual([
    { id: 'textbookTitleInput', tag: 'INPUT', type: 'text', name: 'title', required: true, inField: true },
    { id: 'textbookPublisherInput', tag: 'INPUT', type: 'text', name: 'publisher', required: false, inField: true },
    { id: 'textbookPublicationDateInput', tag: 'INPUT', type: 'date', name: 'publication_date', required: false, inField: true },
    // The file input and the hidden carriers stay native: lq_forms.py has no
    // `file` input type, and hidden carriers have (and should have) no label.
    { id: 'textbookAttachmentInput', tag: 'INPUT', type: 'file', name: 'attachment', required: false, inField: false },
    { id: 'textbookIdInput', tag: 'INPUT', type: 'hidden', name: 'textbook_id', required: false, inField: false },
  ]);

  // Required still blocks a real submit, and the form is still the same form.
  expect(await page.evaluate(() => (document.getElementById('textbookForm') as HTMLFormElement).checkValidity())).toBe(false);
  await page.locator('#textbookTitleInput').fill('LQ S5 教材契约');
  expect(await page.evaluate(() => (document.getElementById('textbookForm') as HTMLFormElement).checkValidity())).toBe(true);
  expect(await page.evaluate(() => (document.getElementById('textbookForm') as HTMLFormElement).action)).toContain('/api/manage/textbooks/save');

  // The intro/catalog overlay's three textareas migrated too.
  await page.locator('#textbookOpenIntroCatalogBtn').click();
  await expect(page.locator('#textbookIntroCatalogBackdrop')).toHaveClass(/is-open/);
  const areas = await page.evaluate(() => ['textbookRawIntroInput', 'textbookRawCatalogInput', 'textbookCustomRequirementsInput'].map((id) => {
    const node = document.getElementById(id) as HTMLTextAreaElement | null;
    return node && { id, tag: node.tagName, rows: node.rows, inField: Boolean(node.closest('.lq-field')) };
  }));
  expect(areas).toEqual([
    { id: 'textbookRawIntroInput', tag: 'TEXTAREA', rows: 5, inField: true },
    { id: 'textbookRawCatalogInput', tag: 'TEXTAREA', rows: 12, inField: true },
    { id: 'textbookCustomRequirementsInput', tag: 'TEXTAREA', rows: 3, inField: true },
  ]);
  await page.screenshot({ path: info.outputPath('textbooks-lq-form-1440.png'), fullPage: true });

  // Escape closes the stacked overlay and hands focus back to its trigger
  // (manage_textbooks.js restoreFocus) -- unchanged by this migration.
  await page.keyboard.press('Escape');
  await expect(page.locator('#textbookIntroCatalogBackdrop')).not.toHaveClass(/is-open/);
  expect(await page.evaluate(() => document.activeElement?.id)).toBe('textbookOpenIntroCatalogBtn');
});

test('S5 L material hub: lq-chip-row proxies the real #mh-scope select', async ({ page }, info) => {
  const fixture = readFixture();
  await loginTeacher(page, fixture);
  await blockAiAndWrites(page);
  expect((await page.goto('/manage/library'))?.status()).toBe(200);

  // The real select is still there, still a select, still carrying all six
  // options -- material_hub.js reads its .value and nothing else.
  const scope = page.locator('#mh-scope');
  await expect(scope).toBeAttached();
  expect(await scope.evaluate((node: HTMLSelectElement) => [...node.options].map((o) => o.value)))
    .toEqual(['all', 'private', 'department', 'college', 'school', 'public']);
  await expect(page.locator('#mhScopeChipRow button.lq-chip--filter[data-value]')).toHaveCount(6);
  await expect(page.locator('#mhScopeChipRow button[data-value="all"]')).toHaveAttribute('aria-pressed', 'true');

  // material_hub.js fires an initial search on load and disables #mh-scope while
  // it runs (setBusy). The proxy evaluates that live on click, so wait for the
  // real select to be enabled again before driving a chip.
  await expect(page.locator('#mh-scope')).toBeEnabled();
  await page.locator('#mhScopeChipRow button[data-value="department"]').click();
  await expect(scope).toHaveValue('department');
  await expect(page.locator('#mhScopeChipRow button[data-value="department"]')).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('#mhScopeChipRow button[data-value="all"]')).toHaveAttribute('aria-pressed', 'false');
  await page.screenshot({ path: info.outputPath('material-hub-chip-row-1440.png'), fullPage: true });
});

test('S5 L lesson plans: lq-field selects keep every data-lp-* hook the controller queries', async ({ page }, info) => {
  const fixture = readFixture();
  await loginTeacher(page, fixture);
  await blockAiAndWrites(page);
  expect((await page.goto('/manage/library/lesson-plans'))?.status()).toBe(200);

  const hooks = await page.evaluate(() => ['data-lp-search', 'data-lp-filter-scope', 'data-lp-filter-school', 'data-lp-filter-college', 'data-lp-filter-course', 'data-lp-filter-class', 'data-lp-sort', 'data-lp-clear-filters'].map((hook) => {
    const node = document.querySelector(`[${hook}]`) as HTMLElement | null;
    return node && { hook, tag: node.tagName, inField: Boolean(node.closest('.lq-field')), inBar: Boolean(node.closest('.lq-filter-bar')) };
  }));
  expect(hooks).toEqual([
    { hook: 'data-lp-search', tag: 'INPUT', inField: true, inBar: true },
    { hook: 'data-lp-filter-scope', tag: 'SELECT', inField: true, inBar: true },
    { hook: 'data-lp-filter-school', tag: 'SELECT', inField: true, inBar: true },
    { hook: 'data-lp-filter-college', tag: 'SELECT', inField: true, inBar: true },
    { hook: 'data-lp-filter-course', tag: 'SELECT', inField: true, inBar: true },
    { hook: 'data-lp-filter-class', tag: 'SELECT', inField: true, inBar: true },
    { hook: 'data-lp-sort', tag: 'SELECT', inField: true, inBar: true },
    // The clear button keeps its legacy class/`hidden` because lq_btn cannot
    // carry `hidden` and the controller toggles it directly.
    { hook: 'data-lp-clear-filters', tag: 'BUTTON', inField: false, inBar: true },
  ]);
  // Scope options and the default sort are still exactly what the filter logic expects.
  expect(await page.locator('[data-lp-filter-scope]').evaluate((n: HTMLSelectElement) => [...n.options].map((o) => o.value)))
    .toEqual(['', 'mine', 'shared', 'private', 'department', 'college', 'school']);
  await expect(page.locator('[data-lp-sort]')).toHaveValue('updated_desc');
  // The empty state container keeps the data-lp-empty hook the controller toggles.
  await expect(page.locator('[data-lp-empty]')).toHaveCount(1);
  await page.screenshot({ path: info.outputPath('lesson-plans-lq-filter-bar-1440.png'), fullPage: true });
});

test('S5 L courses: lq-chip-row proxies #courseFilterSelect and the modal lq-fields keep their ids', async ({ page }, info) => {
  const fixture = readFixture();
  await loginTeacher(page, fixture);
  await blockAiAndWrites(page);
  expect((await page.goto('/manage/library/courses'))?.status()).toBe(200);

  await expect(page.locator('#courseFilterChipRow button.lq-chip--filter[data-value]')).toHaveCount(4);
  await page.locator('#courseFilterChipRow button[data-value="active"]').click();
  await expect(page.locator('#courseFilterSelect')).toHaveValue('active');
  await expect(page.locator('#courseFilterChipRow button[data-value="active"]')).toHaveAttribute('aria-pressed', 'true');

  await page.locator('#openCourseCreateBtn').click();
  await expect(page.locator('#courseModal')).toBeVisible();
  const fields = await page.evaluate(() => ['courseNameInput', 'courseCreditsInput', 'courseTotalHoursInput', 'courseSectNameInput', 'courseDescriptionInput', 'courseDepartmentInput'].map((id) => {
    const node = document.getElementById(id) as HTMLInputElement | HTMLTextAreaElement | null;
    return node && { id, tag: node.tagName, type: (node as HTMLInputElement).type, inField: Boolean(node.closest('.lq-field')) };
  }));
  expect(fields).toEqual([
    { id: 'courseNameInput', tag: 'INPUT', type: 'text', inField: true },
    { id: 'courseCreditsInput', tag: 'INPUT', type: 'number', inField: true },
    { id: 'courseTotalHoursInput', tag: 'INPUT', type: 'number', inField: true },
    { id: 'courseSectNameInput', tag: 'INPUT', type: 'text', inField: true },
    { id: 'courseDescriptionInput', tag: 'TEXTAREA', type: 'textarea', inField: true },
    // Deliberately NOT migrated: it binds a <datalist> through `list=`, which
    // lq_forms.py's attrs allowlist does not pass through.
    { id: 'courseDepartmentInput', tag: 'INPUT', type: 'text', inField: false },
  ]);
  expect(await page.locator('#courseDepartmentInput').getAttribute('list')).toBe('courseDepartmentOptions');
  expect(await page.evaluate(() => document.querySelectorAll('#courseDepartmentOptions option').length)).toBeGreaterThan(0);
  // Number bounds survived the migration (the save path validates against them).
  expect(await page.evaluate(() => {
    const hours = document.getElementById('courseTotalHoursInput') as HTMLInputElement;
    const credits = document.getElementById('courseCreditsInput') as HTMLInputElement;
    return { hoursMax: hours.max, hoursStep: hours.step, creditsStep: credits.step, hoursValue: hours.value };
  })).toEqual({ hoursMax: '512', hoursStep: '1', creditsStep: '0.5', hoursValue: '0' });
  await page.screenshot({ path: info.outputPath('courses-lq-form-1440.png'), fullPage: true });
});

test('S5 L manage-pages family closed renders the legacy library DOM', async ({ page }) => {
  const fixture = readFixture();
  await loginOn(page, fixture, OFF_BASE);

  expect((await page.goto(`${OFF_BASE}/manage/library/textbooks`))?.status()).toBe(200);
  expect(await page.evaluate(() => ({
    lqField: document.querySelectorAll('.lq-field').length,
    lqFilterBar: document.querySelectorAll('.lq-filter-bar').length,
    lqEmpty: document.querySelectorAll('.lq-empty').length,
    lqCard: document.querySelectorAll('.lq-card').length,
    legacySearch: document.querySelectorAll('#textbookSearchInput.form-control').length,
    legacyCard: document.querySelectorAll('section.academic-card').length,
    legacyEmpty: document.querySelectorAll('#textbookEmptyState.academic-empty').length,
  }))).toEqual({ lqField: 0, lqFilterBar: 0, lqEmpty: 0, lqCard: 0, legacySearch: 1, legacyCard: 1, legacyEmpty: 1 });

  expect((await page.goto(`${OFF_BASE}/manage/library`))?.status()).toBe(200);
  expect(await page.evaluate(() => ({
    chipRow: document.querySelectorAll('.lq-chip-row').length,
    proxied: document.querySelectorAll('#mh-scope.filter-proxy-select').length,
    scopeSelect: document.querySelectorAll('select#mh-scope').length,
  }))).toEqual({ chipRow: 0, proxied: 0, scopeSelect: 1 });

  expect((await page.goto(`${OFF_BASE}/manage/library/lesson-plans`))?.status()).toBe(200);
  expect(await page.evaluate(() => ({
    lqField: document.querySelectorAll('.lq-field').length,
    legacyToolbar: document.querySelectorAll('.manage-lp__toolbar').length,
    legacyEmpty: document.querySelectorAll('.manage-lp__empty').length,
  }))).toEqual({ lqField: 0, legacyToolbar: 1, legacyEmpty: 1 });

  expect((await page.goto(`${OFF_BASE}/manage/library/courses`))?.status()).toBe(200);
  expect(await page.evaluate(() => ({
    chipRow: document.querySelectorAll('.lq-chip-row').length,
    legacyChips: document.querySelectorAll('.filter-chips button.filter-chip').length,
    lqField: document.querySelectorAll('.lq-field').length,
  }))).toEqual({ chipRow: 0, legacyChips: 4, lqField: 0 });
});

const SHOT_ROUTES = ['/manage/library', '/manage/library/textbooks', '/manage/library/lesson-plans', '/manage/library/courses'];

for (const scheme of ['light', 'dark'] as const) {
  test(`S5 L screenshot matrix ${scheme} (family on and off)`, async ({ page }, info) => {
    const fixture = readFixture();
    await page.emulateMedia({ colorScheme: scheme });
    for (const [branch, origin] of [['on', ''], ['off', OFF_BASE]] as const) {
      await loginOn(page, fixture, origin);
      for (const width of [1440, 390]) {
        await page.setViewportSize({ width, height: 900 });
        for (const route of SHOT_ROUTES) {
          expect((await page.goto(`${origin}${route}`))?.status(), `${branch} ${route}`).toBe(200);
          await settleEntranceAnimations(page);
          await page.screenshot({ path: info.outputPath(`matrix-${branch}-${scheme}-${width}-${route.replace(/\W+/g, '_')}.png`), fullPage: true });
        }
      }
    }
  });
}
