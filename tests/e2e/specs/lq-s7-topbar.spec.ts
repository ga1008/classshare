import { test as base, expect, type Locator, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { readFixture, loginStudent, loginTeacher, type P03Fixture } from '../fixtures/p03';
import { settleEntranceAnimations } from '../fixtures/lq-s3';

// S7 T package owns this runtime and both ports; see .codex-temp/claude-s4-runbook.md §11.3.
const runtime = path.resolve('.codex-temp/claude-s7-t-runtime');
const mode = process.env.LQ_S7_T_MODE === 'off' ? 'off' : 'on';
const origin = `http://127.0.0.1:${mode === 'off' ? '8236' : '8235'}`;
const shots = path.resolve(process.env.LQ_S7_T_OUTPUT || '.codex-temp/claude-s7-t-e2e');

// Every hook the two-row topbar carried at HEAD df392d13, so the condensed row
// can be proved to have lost none of them.
// Present on both branches: the legacy bar in templates/partials/
// app_topbar_utility_actions.html carries exactly these.
const SHARED_HOOKS = [
  '[data-message-center-bell-shell]', '[data-message-center-bell]', '[data-message-center-bell-caption]',
  '[data-message-center-bell-count]', '[data-message-center-bell-toast]', '[data-message-center-bell-toast-body]',
  '[data-message-center-bell-toast-meta]', '[data-blog-topbar-entry]', '[data-blog-topbar-caption]',
  '[data-blog-today-count]', '[data-open-feedback]',
];
// Owned by the S2 shell composition, so they exist only while navbar-shell is on.
const SHELL_HOOKS = [
  '[data-lq-shell="topbar"]', '[data-lq-pane-open="actions"]', '[data-lq-pane="actions"]',
  '[data-ui-preferences-details]', '[data-ui-palette-select]',
];
const HOOKS = mode === 'on' ? [...SHARED_HOOKS, ...SHELL_HOOKS] : SHARED_HOOKS;
const STUDENT_ONLY_HOOKS = ['[data-open-student-security]'];

function fixture(): P03Fixture {
  if (!process.env.P03_RUNTIME_ROOT || path.resolve(process.env.P03_RUNTIME_ROOT) !== runtime) {
    throw Error('S7 T requires its owned runtime');
  }
  const value = readFixture();
  if (path.resolve(value.runtimeRoot) !== runtime
      || path.resolve(value.databasePath) !== path.join(runtime, 'db/classroom.db')
      || (value as unknown as { uiV3Synthetic?: boolean }).uiV3Synthetic !== true) {
    throw Error('S7 T fixture is not synthetic');
  }
  return value;
}

const test = base.extend<{ _topbarGuard: void }>({
  _topbarGuard: [async ({ context, baseURL }, use) => {
    const value = fixture();
    expect(baseURL).toBe(origin);
    const health = await context.request.get('/api/internal/health');
    expect(health.status()).toBe(200);
    expect((await health.json()).database_path).toBe(value.databasePath);
    const errors: string[] = [];
    context.on('page', page => page.on('pageerror', error => errors.push(error.message)));
    await context.route('**/*', route => {
      const url = new URL(route.request().url());
      return url.origin === origin || ['blob:', 'data:'].includes(url.protocol) ? route.continue() : route.abort();
    });
    await use();
    expect(errors).toEqual([]);
  }, { auto: true }],
});

const lock = path.join(runtime, `.topbar-${mode}.lock`);
const owner = JSON.stringify({ pid: process.pid, nonce: crypto.randomUUID() });
test.beforeAll(() => { fixture(); fs.mkdirSync(shots, { recursive: true }); fs.writeFileSync(lock, owner, { flag: 'wx' }); });
test.afterAll(() => { if (fs.existsSync(lock) && fs.readFileSync(lock, 'utf8') === owner) fs.unlinkSync(lock); });

const topbar = '[data-lq-navbar-topbar]';
const trigger = (key: string) => `#navbar-topbar--${key}--lq-trigger`;
const panel = (key: string) => `#navbar-topbar--${key}`;

async function box(locator: Locator) {
  const value = await locator.boundingBox();
  expect(value).not.toBeNull();
  return value as { x: number; y: number; width: number; height: number };
}

/** The six authored top-level entries, in DOM order, for the student role. */
function studentEntries(page: Page): Array<{ name: string; locator: Locator }> {
  return [
    { name: '学习', locator: page.locator(trigger('study')) },
    { name: '职业', locator: page.locator(trigger('career')) },
    { name: '博客', locator: page.locator('[data-blog-topbar-entry]') },
    { name: '通知', locator: page.locator('[data-message-center-bell]') },
    { name: '我的', locator: page.locator(trigger('account')) },
    { name: '外观', locator: page.locator('[data-ui-preferences-toggle]') },
  ];
}

async function appearance(page: Page, scheme: 'light' | 'dark') {
  await page.emulateMedia({ colorScheme: scheme });
  await page.reload({ waitUntil: 'load' });
  await expect(page.locator('html')).toHaveAttribute('data-appearance', scheme);
}

test.describe(`S7 topbar (navbar-shell ${mode})`, () => {
  test('the authored hooks all survive, on both switch branches', async ({ page }) => {
    const value = fixture();
    await loginStudent(page, value);
    for (const hook of [...HOOKS, ...STUDENT_ONLY_HOOKS]) {
      await expect(page.locator(hook).first(), hook).toBeAttached();
    }
    const html = await (await page.request.get('/dashboard')).text();
    for (const hook of [...HOOKS, ...STUDENT_ONLY_HOOKS]) {
      expect(html, `${hook} must be server rendered`).toContain(hook.slice(1, -1).split('=')[0]);
    }
    // The two modal openers are the pair worth pinning: each hook has to land on
    // the same element as its menu item. Assert that relationship in the DOM
    // rather than a rendered attribute order, which no contract promises.
    if (mode === 'on') {
      for (const [item, hook] of [['security', 'data-open-student-security'],
                                  ['feedback', 'data-open-feedback']] as const) {
        await expect(page.locator(`[data-lq-menu-item="${item}"][${hook}]`)).toHaveCount(1);
      }
      expect(await page.locator(topbar).count()).toBe(1);
    } else {
      expect(await page.locator(topbar).count()).toBe(0);
      await expect(page.locator('header.navbar.app-topbar')).toBeAttached();
      expect(html).toContain('const topbarMenus =');
    }
  });

  test('the teacher role renders its own entries and never an empty menu', async ({ page }) => {
    const value = fixture();
    await loginTeacher(page, value);
    // A teacher's /dashboard and /profile are the manage shell; /blog is a
    // base_navbar page both roles reach.
    await page.goto('/blog');
    for (const hook of HOOKS) await expect(page.locator(hook).first(), hook).toBeAttached();
    expect(await page.locator('[data-open-student-security]').count()).toBe(0);
    if (mode === 'off') {
      expect(await page.locator(topbar).count()).toBe(0);
      return;
    }
    expect(await page.locator(trigger('study')).count()).toBe(0);
    expect(await page.locator(trigger('career')).count()).toBe(0);
    await expect(page.locator(`${topbar} a[href="/manage"]`)).toBeVisible();
    await expect(page.locator(trigger('account'))).toBeVisible();
    const items = page.locator(`${panel('account')} [data-lq-menu-item]`);
    expect(await items.count()).toBe(3);
    expect(await items.evaluateAll(nodes => nodes.map(node => (node as HTMLElement).dataset.lqMenuItem)))
      .toEqual(['profile', 'feedback', 'logout']);
  });

  test('the student bar is one row at 1440 and 1600', async ({ page }, testInfo) => {
    test.skip(mode === 'off', 'The off branch keeps the legacy two-row bar by design.');
    const value = fixture();
    await loginStudent(page, value);
    for (const width of [1440, 1600]) {
      await page.setViewportSize({ width, height: 900 });
      await page.waitForFunction(() => document.documentElement.clientWidth > 0);
      await settleEntranceAnimations(page);
      const header = await box(page.locator(topbar));
      const entries = studentEntries(page);
      const centres: number[] = [];
      const tops: number[] = [];
      for (const entry of entries) {
        await expect(entry.locator, `${entry.name} @${width}`).toBeVisible();
        const entryBox = await box(entry.locator);
        centres.push(Math.round(entryBox.y + entryBox.height / 2));
        tops.push(Math.round(entryBox.y));
      }
      const spread = Math.max(...centres) - Math.min(...centres);
      testInfo.annotations.push({ type: 'measurement', description: `${width}px header=${header.height}px centres=${centres.join(',')} tops=${tops.join(',')}` });
      // eslint-disable-next-line no-console
      console.log(`[s7-t] width=${width} headerHeight=${header.height} entryCentres=${centres.join(',')} entryTops=${tops.join(',')}`);
      // One row means every entry shares the bar's single vertical band. The
      // entries differ in height by a pixel or two, so compare their centres.
      expect(spread, `six entries on one line @${width}`).toBeLessThanOrEqual(2);
      expect(header.height, `condensed below the 130px two-row height @${width}`).toBeLessThan(130);
      // The bar must stay a single blur host: the panels and the menu triggers
      // sit inside it, and a nested filter would add a layer for no effect.
      const blurInside = await page.locator(topbar).evaluate(node => [...node.querySelectorAll('*')]
        .filter(child => {
          const filter = getComputedStyle(child).backdropFilter;
          return Boolean(filter) && filter !== 'none';
        }).length);
      expect(blurInside, `no nested blur host inside the bar @${width}`).toBe(0);
    }
    // The page-wide blur-host budget belongs to the dashboard and shell packages,
    // not to this bar: measured at 5 on this page with the bar contributing 0.
    // Recorded rather than gated, and reported to the main task.
    const hosts = await page.evaluate(() => [...document.querySelectorAll('*')].filter(node => {
      const filter = getComputedStyle(node).backdropFilter;
      return Boolean(filter) && filter !== 'none';
    }).map(node => `${node.tagName.toLowerCase()}.${node.className}`.slice(0, 80)));
    // eslint-disable-next-line no-console
    console.log(`[s7-t] page-wide persistent blur hosts=${hosts.length} ${JSON.stringify(hosts)}`);
    testInfo.annotations.push({ type: 'blur-hosts', description: JSON.stringify(hosts) });
  });

  test('each menu opens on hover, survives the trip to its panel, and answers the keyboard', async ({ page }) => {
    test.skip(mode === 'off', 'No nav menus exist on the off branch.');
    const value = fixture();
    await loginStudent(page, value);
    for (const key of ['study', 'career', 'account']) {
      const button = page.locator(trigger(key));
      const list = page.locator(panel(key));
      await button.hover();
      await expect(list, `${key} opens on hover`).toBeVisible();
      await list.hover();
      await page.waitForTimeout(500);
      await expect(list, `${key} stays open while the pointer walks into it`).toBeVisible();
      await page.locator('.app-topbar-brand').hover();
      await expect(list, `${key} closes once the pointer leaves both`).toBeHidden();

      await button.focus();
      await page.keyboard.press('Enter');
      await expect(list, `${key} opens on Enter`).toBeVisible();
      const first = list.locator('[data-lq-menu-item]').first();
      await expect(first).toBeFocused();
      await page.keyboard.press('Escape');
      await expect(list).toBeHidden();
      await expect(button, `${key} returns focus to its trigger`).toBeFocused();
    }
    // At most two levels: no menu panel may contain another trigger.
    expect(await page.locator('.lq-menu [data-lq-nav-trigger]').count()).toBe(0);
  });

  test('the two modal openers still reach their controllers from inside the menu', async ({ page }) => {
    test.skip(mode === 'off', 'The off branch keeps its original standalone buttons.');
    const value = fixture();
    await loginStudent(page, value);
    await page.locator(trigger('account')).click();
    await expect(page.locator(panel('account'))).toBeVisible();
    await page.locator(`${panel('account')} [data-open-student-security]`).click();
    await expect(page.locator('#student-security-modal')).toBeVisible();
    await page.locator('#student-security-modal [data-dismiss="modal"]').first().click();
    await expect(page.locator('#student-security-modal')).toBeHidden();

    await page.locator(trigger('account')).click();
    await page.locator(`${panel('account')} [data-open-feedback]`).click();
    await expect(page.locator('#feedback-modal')).toBeVisible();
  });

  test('the mobile drawer still opens and hosts the menus without a nesting failure', async ({ page }) => {
    const value = fixture();
    await page.setViewportSize({ width: 390, height: 844 });
    await loginStudent(page, value);
    const opener = page.locator('[data-lq-pane-open="actions"]');
    if (mode === 'off') {
      expect(await opener.count()).toBe(0);
      return;
    }
    await expect(opener).toBeVisible();
    await opener.click();
    const dialog = page.locator('#navbar-topbar--lq-actions');
    await expect(dialog).toBeVisible();
    expect(await dialog.evaluate(node => (node as HTMLDialogElement).matches(':modal'))).toBe(true);
    await page.locator(trigger('account')).click();
    const list = page.locator(panel('account'));
    await expect(list).toBeVisible();
    // The layer must host the panel inside the native modal, not behind it.
    expect(await list.evaluate(node => node.closest('dialog')?.id ?? null)).toBe('navbar-topbar--lq-actions');
    await expect(list.locator('[data-lq-menu-item="profile"]')).toBeVisible();
  });

  test('axe finds nothing new, closed or open', async ({ page }, testInfo) => {
    const value = fixture();
    await loginStudent(page, value);
    await settleEntranceAnimations(page);
    // The topbar is the unit under test; the whole page is scanned too, but its
    // own pre-existing findings are the baseline the open state must not grow.
    const scan = async () => (await new AxeBuilder({ page }).analyze()).violations.flatMap(
      violation => violation.nodes.map(node => `${violation.id}|${violation.impact}|${node.target.join(' ')}`));
    const bar = async () => (await new AxeBuilder({ page }).include(mode === 'off' ? '.app-topbar' : topbar).analyze()).violations;
    const closed = await scan();
    expect(await bar(), 'the topbar itself is clean when closed').toEqual([]);
    // eslint-disable-next-line no-console
    console.log(`[s7-t] axe page baseline (closed): ${JSON.stringify(closed)}`);
    testInfo.annotations.push({ type: 'axe-closed', description: JSON.stringify(closed) });
    if (mode === 'off') return;
    await page.locator(trigger('account')).click();
    await expect(page.locator(panel('account'))).toBeVisible();
    await settleEntranceAnimations(page);
    const open = await scan();
    const added = open.filter(entry => !closed.includes(entry));
    // eslint-disable-next-line no-console
    console.log(`[s7-t] axe added by the open menu: ${JSON.stringify(added)}`);
    testInfo.annotations.push({ type: 'axe-open-added', description: JSON.stringify(added) });
    // The component fixture page reported a moderate `region` on `#lq-layers`.
    // A real page's landmarks do NOT absorb it: `layer.js` appends its portal
    // host directly to <body>, so the host itself sits outside every landmark.
    // That belongs to the coordination layer (A package), not to this bar, so
    // it is pinned to exactly one entry rather than filtered out — one more
    // finding of any impact, or a different target, turns this red.
    expect(added, 'the open menu adds only the known #lq-layers region item').toEqual(['region|moderate|#lq-layers']);
    expect(added.filter(entry => !entry.startsWith('region|moderate|'))).toEqual([]);
  });

  test('visual record of both switch branches', async ({ page }) => {
    const value = fixture();
    await loginStudent(page, value);
    for (const scheme of ['light', 'dark'] as const) {
      await appearance(page, scheme);
      for (const width of [1600, 1440, 390]) {
        await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
        // Park the pointer clear of the bar: a resize under a stationary cursor
        // can land it on a trigger and open a menu the record is not about.
        await page.mouse.move(2, 600);
        await expect(page.locator('.lq-menu:not([hidden])')).toHaveCount(0);
        await settleEntranceAnimations(page);
        await page.screenshot({ path: path.join(shots, `${mode}-${scheme}-${width}.png`), fullPage: false });
      }
      if (mode === 'off') continue;
      await page.setViewportSize({ width: 1440, height: 900 });
      await page.locator(trigger('account')).click();
      await expect(page.locator(panel('account'))).toBeVisible();
      await page.screenshot({ path: path.join(shots, `${mode}-${scheme}-1440-open.png`), fullPage: false });
      await page.keyboard.press('Escape');
    }
  });
});
