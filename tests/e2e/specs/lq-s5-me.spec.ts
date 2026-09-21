import AxeBuilder from '@axe-core/playwright';
import { test, expect, type Page } from '@playwright/test';
import { readFixture, loginTeacher } from '../fixtures/p03';
import { settleEntranceAnimations } from '../fixtures/lq-s3';

// S5 M package ("我的" domain, manage-pages family). The server on LQ_S5_PORT
// must run with LANSHARE_LQ_FAMILIES including manage-pages; the server on
// LQ_S5_PORT_OFF must run with the family closed against the same synthetic
// runtime (runbook §8, ports 8207/8208).

const ROUTES = [
  '/manage/me/inbox',
  '/manage/me/signatures',
  '/manage/me/signature-workflows',
  '/manage/me/credentials',
  '/manage/me/password-resets',
];

// Scans are scoped to the page head plus the structures this package migrated.
// A whole-`.manage-content` scan also reports pre-existing contrast failures in
// untouched legacy markup (e.g. `.msw-note`/`.msw-tabs` from
// static/css/material_workflows.css), which belong to their own page owners;
// those are listed in the package report instead of being silenced here.
async function scanSerious(page: Page, include: string) {
  const scan = await new AxeBuilder({ page }).include(include).analyze();
  return scan.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical');
}

async function scanMigrated(page: Page, selectors: string[]) {
  const violations = [];
  for (const selector of selectors) {
    if (!(await page.locator(selector).count())) continue;
    violations.push(...(await scanSerious(page, selector)));
  }
  return violations;
}

const MIGRATED_SELECTORS = [
  '[data-page-head]',
  '.wi-sources--lq',
  '.wi-list--lq',
  '.manage-domain-grid--lq',
  '.pr-summary-grid--lq',
  '.lq-table-shell',
  'form[data-filters].lq-filter-bar',
  '.signature-toolbar--lq',
  '.lq-empty[data-page-empty]',
];

for (const width of [1440, 390]) test(`S5 M me routes render at ${width}`, async ({ page }, info) => {
  const fixture = readFixture();
  await page.setViewportSize({ width, height: 900 });
  await loginTeacher(page, fixture);
  for (const route of ROUTES) {
    const response = await page.goto(route);
    expect(response?.status(), route).toBe(200);
    await expect(page.locator('[data-page-head]').first(), route).toBeVisible({ timeout: 10_000 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth), route).toBeLessThanOrEqual(width);
    await settleEntranceAnimations(page);
    expect(await scanMigrated(page, MIGRATED_SELECTORS), route).toEqual([]);
    if (width === 1440) await page.screenshot({ path: info.outputPath(`teacher-${route.replace(/\W+/g, '_')}-${width}.png`), fullPage: true });
  }
});

test('S5 M work inbox renders the LQ list or the LQ empty state, never the legacy markup', async ({ page }) => {
  const fixture = readFixture();
  await loginTeacher(page, fixture);
  expect((await page.goto('/manage/me/inbox'))?.status()).toBe(200);
  // The legacy branch must be gone entirely while the family is on.
  await expect(page.locator('li.wi-item')).toHaveCount(0);
  await expect(page.locator('div.wi-empty')).toHaveCount(0);

  const rows = page.locator('.lq-list .lq-row[data-work-inbox-item]');
  const rowCount = await rows.count();
  if (rowCount) {
    await expect(page.locator('ul.lq-list[role="list"]')).toHaveCount(1);
    await expect(rows.first()).toBeVisible();
    // Every row keeps a reachable action link and a bucket marker.
    const shape = await rows.evaluateAll((items) => items.map((item) => ({
      hasTitle: !!item.querySelector('.lq-row__title')?.textContent?.trim(),
      hasAction: !!item.querySelector('.lq-row__trail a.lq-btn[href]'),
      bucket: item.getAttribute('data-date-bucket'),
      urgent: item.getAttribute('data-urgent'),
    })));
    expect(shape.every((s) => s.hasTitle && s.hasAction && s.bucket !== null && ['true', 'false'].includes(s.urgent || '')), JSON.stringify(shape)).toBe(true);
  } else {
    const empty = page.locator('.lq-empty[data-page-empty]');
    await expect(empty).toBeVisible({ timeout: 10_000 });
    await expect(empty.locator('a.lq-btn[href="/dashboard"]')).toBeVisible();
  }
  await settleEntranceAnimations(page);
  expect(await scanMigrated(page, MIGRATED_SELECTORS)).toEqual([]);
});

test('S5 M work inbox: the source filter is a row of real link chips that still navigate', async ({ page }) => {
  const fixture = readFixture();
  await loginTeacher(page, fixture);
  expect((await page.goto('/manage/me/inbox'))?.status()).toBe(200);

  // The frozen ChipRow owns the wrapper: role=group + aria-label + its track.
  const row = page.locator('.wi-sources--lq .lq-chip-row#workInboxSourceChips');
  await expect(row).toBeVisible();
  await expect(row).toHaveAttribute('role', 'group');
  await expect(row).toHaveAttribute('aria-label', '按来源筛选');
  await expect(row.locator('.lq-chip-row__track#workInboxSourceChips--lq-track')).toHaveCount(1);
  await expect(page.locator('a.wi-chip')).toHaveCount(0);
  await expect(page.locator('nav.wi-sources--lq')).toHaveCount(0);

  // Every chip must be an <a> carrying a real href plus aria-current (a link is
  // not a toggle), and exactly one of them is current.
  const chips = await row.locator('.lq-chip--filter').evaluateAll((items) => items.map((item) => ({
    tag: item.tagName,
    href: item.getAttribute('href'),
    current: item.getAttribute('aria-current'),
    pressed: item.getAttribute('aria-pressed'),
    sourceKey: item.getAttribute('data-source-key'),
  })));
  expect(chips.length, '至少「全部」这一枚 chip 必须在').toBeGreaterThan(0);
  expect(chips.every((c) => c.tag === 'A'), JSON.stringify(chips)).toBe(true);
  expect(chips.every((c) => (c.href || '').startsWith('/manage/me/inbox')), JSON.stringify(chips)).toBe(true);
  expect(chips.every((c) => c.pressed === null), 'link chips must not carry aria-pressed').toBe(true);
  expect(chips.filter((c) => c.current === 'true')).toHaveLength(1);
  expect(chips[0].href, '第一枚是「全部」').toBe('/manage/me/inbox');
  expect(chips[0].current).toBe('true');

  const filtered = chips.find((c) => c.sourceKey);
  if (filtered) {
    await row.locator(`.lq-chip--filter[data-source-key="${filtered.sourceKey}"]`).click();
    await page.waitForURL(new RegExp(`source=${filtered.sourceKey}`), { timeout: 10_000 });
    // aria-current moves with the server-side filter.
    await expect(page.locator(`.wi-sources--lq .lq-chip--filter[data-source-key="${filtered.sourceKey}"]`)).toHaveAttribute('aria-current', 'true');
    await expect(page.locator('.wi-sources--lq .lq-chip--filter[href="/manage/me/inbox"]')).toHaveAttribute('aria-current', 'false');
  }
  await settleEntranceAnimations(page);
  expect(await scanMigrated(page, MIGRATED_SELECTORS)).toEqual([]);
});

test('S5 M signatures: the four datalist inputs keep list/type/autocomplete inside lq_field', async ({ page }) => {
  const fixture = readFixture();
  await loginTeacher(page, fixture);
  expect((await page.goto('/manage/me/signatures'))?.status()).toBe(200);

  const shape = await page.evaluate(() => ['signature-school-search-input', 'signature-edit-subject-input', 'signature-edit-owner-input', 'signature-subject-account-input']
    .map((id) => {
      const el = document.getElementById(id) as HTMLInputElement | null;
      return {
        id,
        tag: el?.tagName || '',
        type: el?.type || '',
        list: el?.getAttribute('list') || null,
        autocomplete: el?.getAttribute('autocomplete') || null,
        name: el?.getAttribute('name'),
        inField: !!el?.closest('.lq-field'),
      };
    }));
  expect(shape).toEqual([
    { id: 'signature-school-search-input', tag: 'INPUT', type: 'search', list: 'signature-school-options', autocomplete: 'off', name: null, inField: true },
    { id: 'signature-edit-subject-input', tag: 'INPUT', type: 'search', list: 'signature-owner-teacher-options', autocomplete: 'off', name: null, inField: true },
    { id: 'signature-edit-owner-input', tag: 'INPUT', type: 'search', list: 'signature-owner-teacher-options', autocomplete: 'off', name: null, inField: true },
    { id: 'signature-subject-account-input', tag: 'INPUT', type: 'search', list: 'signature-owner-teacher-options', autocomplete: 'off', name: null, inField: true },
  ]);
  // The datalist elements the `list` attributes point at must still exist, exactly once each.
  await expect(page.locator('datalist#signature-school-options')).toHaveCount(1);
  await expect(page.locator('datalist#signature-owner-teacher-options')).toHaveCount(1);
  // No legacy native markup left for these four while the family is on.
  await expect(page.locator('#signature-school-search-input.form-control, #signature-edit-owner-input.form-control')).toHaveCount(0);
});

test('S5 M password resets: six LQ stat cards plus an LQ table or an LQ empty state', async ({ page }) => {
  const fixture = readFixture();
  await loginTeacher(page, fixture);
  expect((await page.goto('/manage/me/password-resets'))?.status()).toBe(200);
  await expect(page.locator('.pr-summary-grid .lq-card--stat')).toHaveCount(6);
  await expect(page.locator('.pr-summary-card')).toHaveCount(0);
  // `.pr-actions-cell` only exists in the legacy list table; the modal's login
  // history table keeps its own `table.table` and is not part of this migration.
  await expect(page.locator('.pr-actions-cell')).toHaveCount(0);

  const table = page.locator('table#passwordResetTable.lq-table');
  if (await table.count()) {
    await expect(table).toBeVisible();
    const headers = await table.locator('thead th').allInnerTexts();
    expect(headers.map((t) => t.trim())).toEqual(['学生', '班级', '状态', '提交时间', '累计登录', '最近登录', '操作']);
    // The detail trigger contract the page script relies on.
    await expect(table.locator('tbody [data-open-reset-detail][data-request-id]').first()).toBeVisible();
  } else {
    await expect(page.locator('.lq-empty[data-page-empty]')).toBeVisible({ timeout: 10_000 });
    await expect(page.locator('.pr-empty-state')).toHaveCount(0);
  }
});

test('S5 M password resets: the LQ table trigger still opens the review modal, and Escape returns focus without reviewing', async ({ page }) => {
  const fixture = readFixture();
  await loginTeacher(page, fixture);
  expect((await page.goto('/manage/me/password-resets'))?.status()).toBe(200);

  let reviewFired = false;
  await page.route('**/api/manage/system/password-resets/**', async (route) => {
    if (route.request().method() === 'POST') reviewFired = true;
    await route.continue();
  });

  const trigger = page.locator('table#passwordResetTable [data-open-reset-detail]').first();
  await expect(trigger).toBeVisible({ timeout: 10_000 });
  await trigger.focus();
  await trigger.click();

  const modal = page.locator('#system-reset-detail-modal');
  await expect(modal).toBeVisible({ timeout: 10_000 });
  await expect(page.locator('#system-reset-detail-content')).toBeVisible({ timeout: 10_000 });

  await page.keyboard.press('Escape');
  await expect(modal).toBeHidden({ timeout: 10_000 });
  // The layer system must hand focus back to the row's own trigger.
  expect(await page.evaluate(() => document.activeElement?.getAttribute('data-request-id'))).toBe(await trigger.getAttribute('data-request-id'));
  expect(reviewFired, 'closing the modal must not approve or reject anything').toBe(false);
});

test('S5 M credentials: six LQ stat cards replace the legacy domain cards', async ({ page }) => {
  const fixture = readFixture();
  await loginTeacher(page, fixture);
  expect((await page.goto('/manage/me/credentials'))?.status()).toBe(200);
  await expect(page.locator('.lq-card--stat')).toHaveCount(6);
  await expect(page.locator('a.manage-domain-card')).toHaveCount(0);
  // The first three stay links to their integration pages.
  const links = await page.locator('[data-credential-group] .lq-card__title a.lq-btn[href]').evaluateAll((items) => items.map((i) => i.getAttribute('href')));
  expect(links).toHaveLength(3);
  expect(links.every((href) => !!href && href.startsWith('/manage/'))).toBe(true);
});

test('S5 M signature workflows: lq_filter_bar keeps every named control signature_workflows.js reads', async ({ page }) => {
  const fixture = readFixture();
  await loginTeacher(page, fixture);
  expect((await page.goto('/manage/me/signature-workflows'))?.status()).toBe(200);
  const form = page.locator('form[data-filters]');
  await expect(form).toBeVisible();
  await expect(form).toHaveClass(/lq-filter-bar/);
  await expect(page.locator('form.msw-filters')).toHaveCount(0);

  const shape = await form.evaluate((node) => {
    const f = node as HTMLFormElement;
    const names = ['q', 'status', 'document_type', 'request_kind', 'requester_role', 'identity', 'organization'];
    return names.map((name) => {
      const el = f.elements.namedItem(name) as HTMLInputElement | HTMLSelectElement | null;
      return { name, found: !!el, tag: el?.tagName || '', value: el ? el.value : null, inField: !!el?.closest('.lq-field') };
    });
  });
  expect(shape).toEqual([
    { name: 'q', found: true, tag: 'INPUT', value: '', inField: true },
    { name: 'status', found: true, tag: 'SELECT', value: 'pending', inField: true },
    { name: 'document_type', found: true, tag: 'SELECT', value: '', inField: true },
    { name: 'request_kind', found: true, tag: 'SELECT', value: '', inField: true },
    { name: 'requester_role', found: true, tag: 'SELECT', value: '', inField: true },
    { name: 'identity', found: true, tag: 'SELECT', value: '', inField: true },
    { name: 'organization', found: true, tag: 'INPUT', value: '', inField: true },
  ]);
  // FormData (the JS reads it directly) still sees the same default query.
  const serialized = await form.evaluate((node) => {
    const data = new FormData(node as HTMLFormElement);
    const params = new URLSearchParams();
    data.forEach((value, key) => params.append(key, String(value)));
    return params.toString();
  });
  expect(serialized).toContain('status=pending');
  // The reset control still resets the form.
  await form.locator('select[name="document_type"]').selectOption('assessment_plan');
  await form.locator('button[type="reset"]').click();
  await expect(form.locator('select[name="document_type"]')).toHaveValue('');
  await expect(form.locator('select[name="status"]')).toHaveValue('pending');
});

test('S5 M signatures: the four toolbar filters keep their ids and reset through the clear button', async ({ page }) => {
  const fixture = readFixture();
  await loginTeacher(page, fixture);
  expect((await page.goto('/manage/me/signatures'))?.status()).toBe(200);
  const shape = await page.evaluate(() => ['signature-search-input', 'signature-scope-filter', 'signature-identity-filter', 'signature-owner-filter']
    .map((id) => {
      const el = document.getElementById(id) as HTMLInputElement | HTMLSelectElement | null;
      return { id, found: !!el, tag: el?.tagName || '', type: (el as HTMLInputElement)?.type || '', value: el ? el.value : null, inField: !!el?.closest('.lq-field') };
    }));
  expect(shape).toEqual([
    { id: 'signature-search-input', found: true, tag: 'INPUT', type: 'search', value: '', inField: true },
    { id: 'signature-scope-filter', found: true, tag: 'SELECT', type: 'select-one', value: '', inField: true },
    { id: 'signature-identity-filter', found: true, tag: 'SELECT', type: 'select-one', value: '', inField: true },
    { id: 'signature-owner-filter', found: true, tag: 'SELECT', type: 'select-one', value: '', inField: true },
  ]);
  await page.locator('#signature-scope-filter').selectOption('mine');
  await page.locator('#signature-search-input').fill('测试');
  await page.locator('#signature-clear-filter-btn').click();
  await expect(page.locator('#signature-scope-filter')).toHaveValue('');
  await expect(page.locator('#signature-search-input')).toHaveValue('');
  // The school switcher keeps its datalist-backed native input (not migrated).
  await expect(page.locator('#signature-school-search-input[list="signature-school-options"]')).toHaveCount(1);
});

test('S5 M manage-pages family closed renders the legacy DOM for every me route', async ({ page }) => {
  const offPort = process.env.LQ_S5_PORT_OFF;
  expect(offPort, 'LQ_S5_PORT_OFF must point at a family-closed server').toBeTruthy();
  const origin = `http://127.0.0.1:${offPort}`;
  const fixture = readFixture();
  await page.goto(`${origin}/teacher/login`);
  await expect(page.locator('#email')).toBeVisible();
  await page.locator('#email').fill(fixture.teacher.email);
  await page.locator('#password').fill(fixture.password);
  await Promise.all([
    page.waitForURL(/\/dashboard(?:\?|$)/, { timeout: 20_000 }),
    page.locator('button[type="submit"]').click(),
  ]);

  expect((await page.goto(`${origin}/manage/me/inbox`))?.status()).toBe(200);
  await expect(page.locator('.lq-list, .lq-row, .lq-empty, .lq-chip, .lq-chip-row')).toHaveCount(0);
  await expect(page.locator('ul.wi-list, div.wi-empty')).toHaveCount(1);
  await expect(page.locator('.wi-sources--lq')).toHaveCount(0);
  await expect(page.locator('nav.wi-sources a.wi-chip[href="/manage/me/inbox"]')).toHaveCount(1);

  expect((await page.goto(`${origin}/manage/me/password-resets`))?.status()).toBe(200);
  await expect(page.locator('.lq-card, .lq-table, .lq-empty')).toHaveCount(0);
  await expect(page.locator('.pr-summary-card')).toHaveCount(6);
  await expect(page.locator('table.table, .pr-empty-state').first()).toBeVisible();

  expect((await page.goto(`${origin}/manage/me/credentials`))?.status()).toBe(200);
  await expect(page.locator('.lq-card')).toHaveCount(0);
  await expect(page.locator('a.manage-domain-card')).toHaveCount(3);

  expect((await page.goto(`${origin}/manage/me/signature-workflows`))?.status()).toBe(200);
  await expect(page.locator('.lq-filter-bar, .lq-field')).toHaveCount(0);
  await expect(page.locator('form.msw-filters')).toHaveCount(1);
  await expect(page.locator('form.msw-filters select[name="status"]')).toHaveValue('pending');

  expect((await page.goto(`${origin}/manage/me/signatures`))?.status()).toBe(200);
  await expect(page.locator('.signature-toolbar .lq-field')).toHaveCount(0);
  await expect(page.locator('#signature-search-input.form-control')).toHaveCount(1);
  await expect(page.locator('#signature-scope-filter.form-control')).toHaveCount(1);
  // The four datalist inputs fall back to their native markup too.
  await expect(page.locator('#signature-school-search-input.form-control[list="signature-school-options"]')).toHaveCount(1);
  await expect(page.locator('#signature-edit-subject-input.form-control[list="signature-owner-teacher-options"]')).toHaveCount(1);
  await expect(page.locator('#signature-edit-owner-input.form-control[list="signature-owner-teacher-options"]')).toHaveCount(1);
  await expect(page.locator('#signature-subject-account-input.form-control[list="signature-owner-teacher-options"]')).toHaveCount(1);
  await expect(page.locator('.lq-field')).toHaveCount(0);
});

const SHOT_ROUTES = ['/manage/me/inbox', '/manage/me/password-resets', '/manage/me/signature-workflows'];

for (const scheme of ['light', 'dark'] as const) {
  test(`S5 M screenshot matrix ${scheme} (family on and off)`, async ({ page }, info) => {
    const fixture = readFixture();
    const offOrigin = process.env.LQ_S5_PORT_OFF ? `http://127.0.0.1:${process.env.LQ_S5_PORT_OFF}` : '';
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
          await page.screenshot({ path: info.outputPath(`matrix-${branch}-${scheme}-${width}-${route.replace(/\W+/g, '_')}.png`), fullPage: true });
        }
      }
    }
  });
}
