import AxeBuilder from '@axe-core/playwright';
import fs from 'node:fs';
import path from 'node:path';
import { test, expect, type Page } from '@playwright/test';
import { readFixture, loginTeacher, collectBrowserErrors, expectNoBrowserErrors } from '../fixtures/p03';
import { settleEntranceAnimations } from '../fixtures/lq-s3';

// S6 G package: 公文 list table, runtime-assembled -> static/js/lq/tables.js factories.
//
// Server (family ON) runs with LANSHARE_LQ_FAMILIES=manage-shell,manage-pages and
// LANSHARE_LQ_PILOT=false on LQ_S6_PORT (8213); a second server with neither switch
// runs on LQ_S6_PORT_OFF (8214). Both serve .codex-temp/claude-s6-g-runtime, seeded
// by .codex-temp/claude-s6-g-seed.py (25 synthetic documents). See runbook §10, G row.
//
// 公文 sync talks to the real campus 公文通. Every test installs `guardGongwen`
// first: writes are refused loudly and the reader payload is stubbed, so no test
// can reach a school server or kick off a parse job.

const ROUTE = '/manage/academic/gongwen';
const OFF_PORT = process.env.LQ_S6_PORT_OFF || '8214';
const OFF_BASE = `http://127.0.0.1:${OFF_PORT}`;
const OUTPUT = path.resolve(process.env.LQ_S6_OUTPUT || '.codex-temp/claude-s6-g-e2e');
const SEEDED_TOTAL = 25;
const PAGE_SIZE = 20;
const COLUMN_LABELS = ['标题', '发文单位', '发送人', '分类', '时间', '归属 / 开放', '操作'];

async function guardGongwen(page: Page): Promise<string[]> {
  const blocked: string[] = [];
  await page.route('**/api/manage/gongwen/documents/*/reader**', async (route) => {
    await route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ document: { id: 1, sn: 'QA〔2026〕001号', title: '合成公文', parts: [] } }),
    });
  });
  for (const pattern of ['**/api/manage/gongwen/**', '**/api/manage/system/gongwen-sync**']) {
    await page.route(pattern, async (route) => {
      const method = route.request().method();
      if (method === 'GET') return route.continue();
      blocked.push(`${method} ${route.request().url()}`);
      await route.fulfill({ status: 503, contentType: 'application/json', body: '{"detail":"blocked by spec"}' });
    });
  }
  return blocked;
}

function lqRows(page: Page) {
  return page.locator('#gw-doc-table tbody tr');
}

async function openList(page: Page): Promise<string[]> {
  const fixture = readFixture();
  await loginTeacher(page, fixture);
  const blocked = await guardGongwen(page);
  const response = await page.goto(ROUTE);
  expect(response?.status(), ROUTE).toBe(200);
  return blocked;
}

// The seed is a hard precondition, not an optional nicety: without rows every
// "the table renders" assertion below would pass vacuously on an empty state.
async function expectSeedPresent(page: Page) {
  const total = await page.locator('[data-lq-doc-count] .lq-result-count, #gw-doc-count').first().textContent();
  expect(total ?? '', 'seed missing: run .codex-temp/claude-s6-g-seed.py against the runtime')
    .toContain(String(SEEDED_TOTAL));
}

test('S6 G: LQ branch builds the 公文 table through the lq/tables factory', async ({ page }, info) => {
  const errors = collectBrowserErrors(page);
  await openList(page);

  // Structure comes from the factory, so it carries the factory's own contract
  // markers. `#gw-doc-tbody` (the legacy hand-assembled body) must be gone.
  await expect(page.locator('[data-lq-table]')).toHaveCount(1);
  await expect(page.locator('#gw-doc-table--lq-wrap')).toHaveCount(1);
  await expect(page.locator('table#gw-doc-table.lq-table')).toHaveCount(1);
  await expect(page.locator('#gw-doc-tbody')).toHaveCount(0);
  await expect(page.locator('table.gwlist-table')).toHaveCount(0);

  // Column set is unchanged, in order.
  const headers = await page.locator('#gw-doc-table thead th').allTextContents();
  expect(headers.map((value) => value.trim())).toEqual(COLUMN_LABELS);

  await expectSeedPresent(page);
  await expect(lqRows(page)).toHaveCount(PAGE_SIZE);

  // Every legacy per-row hook survives on every row.
  const rows = lqRows(page);
  for (const hook of ['[data-open-reader]', '[data-scope-edit]']) {
    expect(await rows.locator(hook).count(), hook).toBe(PAGE_SIZE);
  }
  // Row keys come from the document id and are what the delegation resolves.
  const first = rows.first();
  const rowKey = await first.getAttribute('data-lq-row-key');
  expect(rowKey).toMatch(/^\d+$/);
  expect(await first.locator('[data-open-reader]').getAttribute('data-open-reader')).toBe(rowKey);
  expect(await first.locator('[data-scope-edit]').getAttribute('data-scope-edit')).toBe(rowKey);

  // File links keep target=_blank (the LQ anchor button preserves target/rel).
  const fileLink = rows.locator('a.lq-btn[href*="/file?which="]').first();
  expect(await fileLink.count()).toBeGreaterThan(0);
  expect(await fileLink.getAttribute('target')).toBe('_blank');
  expect(await fileLink.getAttribute('rel')).toContain('noopener');

  await expectNoBrowserErrors(errors, info);
});

test('S6 G: filters still drive the LQ table', async ({ page }) => {
  await openList(page);
  await expect(lqRows(page)).toHaveCount(PAGE_SIZE);

  // Category facet: pick the first real option and check the server round-trip
  // narrowed the table and that every remaining row shows that category.
  const category = await page.locator('#gw-doc-category option:not([value=""])').first().getAttribute('value');
  expect(category, 'seeded facets missing').toBeTruthy();
  const request = page.waitForResponse((response) =>
    response.url().includes('/api/manage/gongwen/documents?') && response.url().includes('category='));
  await page.selectOption('#gw-doc-category', category!);
  await request;
  await expect(lqRows(page).first()).toBeVisible();
  const narrowed = await lqRows(page).count();
  expect(narrowed).toBeGreaterThan(0);
  expect(narrowed).toBeLessThan(PAGE_SIZE);
  for (const value of await lqRows(page).locator('td[data-label="分类"]').allTextContents()) {
    expect(value).toContain(category!);
  }

  // The unread chip is a separate filter path and must also reach the table.
  await page.selectOption('#gw-doc-category', '');
  await page.waitForResponse((response) => response.url().includes('/api/manage/gongwen/documents?'));
  const unreadRequest = page.waitForResponse((response) =>
    response.url().includes('/api/manage/gongwen/documents?') && response.url().includes('unread=1'));
  await page.click('#gw-doc-unread');
  await unreadRequest;
  await expect(lqRows(page).first()).toBeVisible();
  const unreadRows = await lqRows(page).count();
  expect(unreadRows).toBeGreaterThan(0);
  expect(unreadRows).toBeLessThan(PAGE_SIZE);
  expect(await lqRows(page).locator('.gwlist-unread-dot').count()).toBe(unreadRows);
});

test('S6 G: the LQ pager pages the table', async ({ page }) => {
  await openList(page);
  await expectSeedPresent(page);
  await expect(page.locator('[data-lq-doc-pager] .lq-pager')).toHaveCount(1);
  await expect(page.locator('[data-lq-doc-pager] .lq-pager__summary')).toHaveText('第 1 / 2 页');
  // Page 1 is current, so its control is inert -- that is the LQ pager contract.
  // (`[data-lq-page="1"]` alone matches two controls: 上一页 also targets page 1.)
  await expect(page.locator('[data-lq-doc-pager] [aria-label="第 1 页"]')).toHaveAttribute('aria-current', 'page');
  await expect(page.locator('[data-lq-doc-pager] [aria-label="上一页"]')).toBeDisabled();

  const firstPageKeys = await lqRows(page).evaluateAll((rows) =>
    rows.map((row) => row.getAttribute('data-lq-row-key')));
  const next = page.locator('[data-lq-doc-pager] [aria-label="下一页"]');
  await expect(next).toBeEnabled();
  const request = page.waitForResponse((response) =>
    response.url().includes('/api/manage/gongwen/documents?') && response.url().includes('offset=20'));
  await next.click();
  await request;
  await expect(page.locator('[data-lq-doc-pager] .lq-pager__summary')).toHaveText('第 2 / 2 页');
  await expect(lqRows(page)).toHaveCount(SEEDED_TOTAL - PAGE_SIZE);
  const secondPageKeys = await lqRows(page).evaluateAll((rows) =>
    rows.map((row) => row.getAttribute('data-lq-row-key')));
  expect(secondPageKeys.some((key) => firstPageKeys.includes(key))).toBe(false);
  await expect(page.locator('[data-lq-doc-pager] [aria-label="下一页"]')).toBeDisabled();

  // Page size is still a plain select and still re-pages the table.
  const resize = page.waitForResponse((response) =>
    response.url().includes('/api/manage/gongwen/documents?') && response.url().includes('limit=50'));
  await page.selectOption('#gw-doc-pagesize', '50');
  await resize;
  await expect(lqRows(page)).toHaveCount(SEEDED_TOTAL);
  await expect(page.locator('[data-lq-doc-pager] .lq-pager__summary')).toHaveText('第 1 / 1 页');
});

test('S6 G: row delegation still opens the reader and the scope editor', async ({ page }) => {
  const blocked = await openList(page);
  await expect(lqRows(page)).toHaveCount(PAGE_SIZE);

  await lqRows(page).first().locator('[data-scope-edit]').click();
  await expect(page.locator('#gw-scope-modal')).toBeVisible();
  await expect(page.locator('#gw-scope-subtitle')).not.toHaveText('');
  await page.locator('#gw-scope-cancel').click();
  await expect(page.locator('#gw-scope-modal')).toBeHidden();

  await lqRows(page).first().locator('[data-open-reader]').click();
  await expect(page.locator('#gw-reader')).toBeVisible();
  await page.locator('#gw-reader-close').click();
  await expect(page.locator('#gw-reader')).toBeHidden();

  expect(blocked, 'a test reached a write endpoint').toEqual([]);
});

test('S6 G: the empty state comes from the LQ empty component', async ({ page }) => {
  await openList(page);
  await expect(lqRows(page)).toHaveCount(PAGE_SIZE);
  const request = page.waitForResponse((response) => response.url().includes('/api/manage/gongwen/documents?'));
  await page.fill('#gw-doc-search', 'ZZZ-no-such-document-ZZZ');
  await request;
  await expect(lqRows(page)).toHaveCount(0);
  const empty = page.locator('#gw-doc-table--lq-wrap .lq-table__empty .lq-empty');
  await expect(empty).toBeVisible();
  await expect(empty).toHaveAttribute('data-reason', 'no-results');
  await expect(page.locator('.gwlist-empty')).toHaveCount(0);
  await expect(page.locator('[data-lq-doc-count] .lq-result-count')).toHaveAttribute('data-state', 'empty');
});

test('S6 G: family OFF keeps the legacy hand-assembled table', async ({ browser }) => {
  const fixture = readFixture();
  const context = await browser.newContext({ baseURL: OFF_BASE, viewport: { width: 1440, height: 900 } });
  try {
    const page = await context.newPage();
    await loginTeacher(page, fixture);
    await guardGongwen(page);
    expect((await page.goto(ROUTE))?.status(), `${ROUTE} (family off)`).toBe(200);

    // Nothing LQ, and the legacy structure is intact with real rows.
    await expect(page.locator('[data-lq-table]')).toHaveCount(0);
    await expect(page.locator('[data-lq-doc-table], [data-lq-doc-count], [data-lq-doc-pager]')).toHaveCount(0);
    await expect(page.locator('table.gwlist-table')).toHaveCount(1);
    await expect(page.locator('#gw-doc-tbody tr')).toHaveCount(PAGE_SIZE);
    await expect(page.locator('#gw-doc-count')).toHaveText(`共 ${SEEDED_TOTAL} 条公文`);
    await expect(page.locator('#gw-doc-pageinfo')).toHaveText('1 / 2');
    expect(await page.locator('#gw-doc-tbody [data-open-reader]').count()).toBe(PAGE_SIZE);
    expect(await page.locator('#gw-doc-tbody [data-scope-edit]').count()).toBe(PAGE_SIZE);
    // Legacy rows keep the legacy presentation classes (no LQ chips leaked in).
    expect(await page.locator('#gw-doc-tbody .gwlist-attr').count()).toBe(PAGE_SIZE);
    await expect(page.locator('#gw-doc-tbody .lq-chip')).toHaveCount(0);

    // Legacy pager still pages.
    await page.locator('#gw-doc-next').click();
    await page.waitForResponse((response) => response.url().includes('/api/manage/gongwen/documents?'));
    await expect(page.locator('#gw-doc-pageinfo')).toHaveText('2 / 2');
    await expect(page.locator('#gw-doc-tbody tr')).toHaveCount(SEEDED_TOTAL - PAGE_SIZE);
  } finally {
    await context.close();
  }
});

const summarize = (result: { violations: { id: string; nodes: unknown[] }[] }) =>
  result.violations.map((violation) => `${violation.id}: ${violation.nodes.length}`).sort();

// The four toolbar facet selects (#gw-doc-category / -author / -sender / -parse)
// have carried no accessible name since long before this package; they are plain
// `<select class="form-control gwlist-select">` with no label and no aria-label in
// templates/manage/gongwen.html, and they sit OUTSIDE the migrated table, in lines
// the switch-off branch must keep byte-identical. The list is pinned rather than
// filtered away: if it ever grows or shrinks this test fails and someone looks.
// The four toolbar selects and the search box had no accessible name in either
// branch. Labelled on 2026-09-22, so the baseline is now clean; this stays as a
// list so a regression here still fails loudly.
const KNOWN_BASELINE_VIOLATIONS: string[] = [];

// Measures the OFF server too, so a pre-existing layout defect cannot be mistaken
// for an LQ regression -- and cannot be silently tolerated either: the LQ branch
// must never be wider than the baseline.
for (const width of [1440, 390]) test(`S6 G: LQ branch is no wider than the baseline at ${width}`, async ({ browser }) => {
  const fixture = readFixture();
  const measure = async (base?: string) => {
    const context = await browser.newContext({ ...(base ? { baseURL: base } : {}), viewport: { width, height: 900 } });
    try {
      const page = await context.newPage();
      await loginTeacher(page, fixture);
      await guardGongwen(page);
      expect((await page.goto(ROUTE))?.status()).toBe(200);
      await expect(page.locator(base ? '#gw-doc-tbody tr' : '#gw-doc-table tbody tr')).toHaveCount(PAGE_SIZE);
      return await page.evaluate(() => document.documentElement.scrollWidth);
    } finally {
      await context.close();
    }
  };
  const on = await measure();
  const off = await measure(OFF_BASE);
  expect(on, `LQ ${on}px vs baseline ${off}px at ${width}`).toBeLessThanOrEqual(Math.max(off, width));
});

test('S6 G: axe — migrated subtree clean, and no new violation vs the OFF baseline', async ({ page, browser }) => {
  await openList(page);
  await expect(lqRows(page)).toHaveCount(PAGE_SIZE);
  await settleEntranceAnimations(page);

  // What this package actually built must be clean on its own terms.
  const migrated = await new AxeBuilder({ page })
    .include('#gw-doc-table--lq-wrap').include('[data-lq-doc-count]').include('[data-lq-doc-pager]')
    .analyze();
  expect(summarize(migrated)).toEqual([]);

  const on = await new AxeBuilder({ page }).include('.gwlist-shell').analyze();

  const context = await browser.newContext({ baseURL: OFF_BASE, viewport: { width: 1440, height: 900 } });
  try {
    const offPage = await context.newPage();
    const fixture = readFixture();
    await loginTeacher(offPage, fixture);
    await guardGongwen(offPage);
    await offPage.goto(ROUTE);
    await expect(offPage.locator('#gw-doc-tbody tr')).toHaveCount(PAGE_SIZE);
    await settleEntranceAnimations(offPage);
    const off = await new AxeBuilder({ page: offPage }).include('.gwlist-shell').analyze();
    expect(summarize(off), 'baseline a11y defects changed').toEqual(KNOWN_BASELINE_VIOLATIONS);
    expect(summarize(on), 'LQ branch introduced an a11y violation').toEqual(summarize(off));
  } finally {
    await context.close();
  }
});

test('S6 G: screenshots 1440/390 x light/dark x switch on/off', async ({ browser }) => {
  fs.mkdirSync(OUTPUT, { recursive: true });
  const fixture = readFixture();
  for (const branch of ['on', 'off'] as const) {
    for (const width of [1440, 390]) {
      for (const appearance of ['light', 'dark'] as const) {
        const context = await browser.newContext({
          ...(branch === 'off' ? { baseURL: OFF_BASE } : {}),
          viewport: { width, height: 900 },
        });
        try {
          const page = await context.newPage();
          await loginTeacher(page, fixture);
          await guardGongwen(page);
          await page.goto(ROUTE);
          await page.evaluate((value) => {
            document.documentElement.setAttribute('data-appearance', value);
          }, appearance);
          const rows = branch === 'on' ? lqRows(page) : page.locator('#gw-doc-tbody tr');
          await expect(rows).toHaveCount(PAGE_SIZE);
          await settleEntranceAnimations(page);
          await page.screenshot({
            path: path.join(OUTPUT, `gongwen-${branch}-${width}-${appearance}.png`),
            fullPage: true,
          });
        } finally {
          await context.close();
        }
      }
    }
  }
});
