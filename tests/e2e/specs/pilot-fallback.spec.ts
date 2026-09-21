import type { Locator, Page, TestInfo } from '@playwright/test';
import { test, expect, guardS3Page, type S3Fixture } from '../fixtures/lq-s3';
import { loginStudent, loginTeacher } from '../fixtures/p03';

type Kind = 'manage' | 'report';
const moduleName = (kind: Kind) => kind === 'manage' ? 'manage_lq_pilot.js' : 'report_card.js';
const moduleMatcher = (kind: Kind) => (url: URL) => url.pathname.endsWith(`/js/${moduleName(kind)}`);
const topbar = (kind: Kind) => kind === 'manage' ? '#manage-pilot-topbar' : '[data-lq-report-card-topbar]';
const rootSelector = (kind: Kind) => kind === 'manage' ? '.lq-manage-pilot' : '[data-lq-report-card]';
const destination = (kind: Kind, fixture: S3Fixture) => kind === 'manage'
  ? '/manage/library/courses' : `/report-card?class_offering_id=${fixture.reportCard.offeringId}`;

async function prepare(page: Page, kind: Kind) {
  await page.setViewportSize({ width: 390, height: 844 });
  const fixture = await guardS3Page(page);
  if (kind === 'manage') await loginTeacher(page, fixture); else await loginStudent(page, fixture);
  return fixture;
}

async function painted(page: Page) {
  await page.waitForLoadState('load');
  await page.evaluate(async () => {
    await document.fonts.ready;
    await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
  });
  await expect.poll(() => page.evaluate(() => performance.getEntriesByName('first-contentful-paint').length)).toBe(1);
}

async function waitForSceneGeometry(chip: Locator) {
  await expect.poll(() => chip.evaluate(element => {
    const chain = new Set<Element>();
    for (let node: Element | null = element; node; node = node.parentElement) chain.add(node);
    return document.getAnimations().every(animation => {
      const effect = animation.effect;
      if (!(effect instanceof KeyframeEffect) || !(effect.target instanceof Element) || !chain.has(effect.target)
          || !Number.isFinite(effect.getComputedTiming().endTime)) return true;
      return !animation.pending && ['finished', 'idle'].includes(animation.playState);
    });
  }), { timeout: 2000, intervals: [16, 32, 64], message: 'Scene chip and ancestor finite entrance animations must finish before measuring' }).toBe(true);
}

async function attach(info: TestInfo, name: string, value: unknown) {
  await info.attach(name, { body: JSON.stringify(value, null, 2), contentType: 'application/json' });
}

async function expectHeadEntry(page: Page, kind: Kind) {
  const script = page.locator(`script[src$="/js/${moduleName(kind)}"]`);
  await expect(script).toHaveCount(1);
  await expect(script).toHaveAttribute('type', 'module');
  await expect(script).toHaveAttribute('blocking', 'render');
  expect(await script.evaluate(node => node.parentElement?.tagName)).toBe('HEAD');
}

async function expectFallback(page: Page, kind: Kind) {
  const bar = page.locator(topbar(kind));
  await expect(bar).not.toHaveAttribute('data-lq-enhanced', 'true');
  const pane = bar.locator('[data-lq-pane="actions"]');
  await expect(pane).toHaveAttribute('open', '');
  await expect(pane).toBeVisible();
  await expect(pane).not.toHaveAttribute('hidden', '');
  await expect(pane.locator('a[href="/profile"]').first()).toBeVisible();
  await expect(page.locator('dialog:modal')).toHaveCount(0);
  if (kind === 'manage') {
    await expect(page.locator('#manage-pilot-nav')).toHaveAttribute('open', '');
    await expect(page.locator('[data-lq-manage-sidebar]')).not.toHaveAttribute('data-lq-enhanced', 'true');
    await expect(page.locator('#courseCardGrid .course-card').first()).toBeVisible();
  } else {
    await expect(page.locator('[data-lq-report-card]')).toContainText('未提交，教师记 0');
    await expect(page.getByRole('region', { name: '已公布课程成绩' })).toContainText(/总评\s+0(?:\.0+)?\s+分/);
  }
}

async function nativeLibraryNavigation(page: Page, { javaScript = true } = {}) {
  const group = page.locator('#manage-domain-library');
  await expect(group).toHaveAttribute('open', '');
  await group.locator('summary').click();
  await expect(group).not.toHaveAttribute('open', '');
  await group.locator('summary').click();
  await expect(group).toHaveAttribute('open', '');
  await group.locator('a[href="/manage/library/textbooks"]').click();
  await expect(page).toHaveURL(/\/manage\/library\/textbooks(?:\?|$)/);
  await expect(page.getByRole('heading', { name: '教材管理', exact: true })).toBeVisible();
  await expect(page.locator('article').filter({ hasText: '教材总数' })).toContainText('1本');
  // The textbook records are an existing JS renderer, while the page heading,
  // aggregate and navigation are server rendered and remain useful without JS.
  if (javaScript) await expect(page.locator('#textbookCardGrid')).toBeVisible();
}

test('S3 manage pilot module 404 releases rendering and preserves native navigation and the real course form', async ({ page }, info) => {
  await prepare(page, 'manage');
  let failedModules = 0, attemptedSaves = 0;
  await page.route(moduleMatcher('manage'), route => {
    failedModules++;
    return route.fulfill({ status: 404, contentType: 'text/plain', body: 'S3 intentional pilot module absence' });
  });
  await page.route(url => url.pathname === '/api/manage/courses/save', route => {
    if (route.request().method() !== 'POST') return route.fallback();
    attemptedSaves++;
    return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'S3 fallback 保留课程输入', message: 'S3 fallback 保留课程输入' }) });
  });
  expect((await page.goto('/manage/library/courses'))?.status()).toBe(200);
  await expectHeadEntry(page, 'manage');
  await painted(page); await expectFallback(page, 'manage');
  expect(failedModules).toBe(1);
  await expect(page.locator('.course-card[tabindex="0"]').first()).toBeVisible();
  // The original topbar action is inline without the failed Shell enhancer.
  await page.locator('#openCourseCreateBtn').click();
  await expect(page.locator('#courseModal')).toBeVisible();
  await page.locator('#courseNameInput').fill('S3 模块失败后的原课程草稿');
  await page.locator('#courseSaveBtn').click();
  await expect.poll(() => attemptedSaves).toBe(1);
  await expect(page.locator('#courseNameInput')).toHaveValue('S3 模块失败后的原课程草稿');
  await expect(page.locator('#courseSaveBtn')).toBeEnabled();
  await expect(page.getByText('S3 fallback 保留课程输入', { exact: false }).first()).toBeVisible();
  await info.attach('manage-module-failure.png', { body: await page.screenshot(), contentType: 'image/png' });
  await page.locator('#courseModal button[data-dismiss="modal"]').filter({ hasText: '取消' }).click();
  await expect(page.locator('#courseModal')).toBeHidden();
  await nativeLibraryNavigation(page);
  await attach(info, 'fallback.json', { failure: 'module-404', attemptedSaves, failedModules });
});

test('S3 report-card pilot module abort releases rendering with SSR scores and native overflow entries accessible', async ({ page }, info) => {
  const fixture = await prepare(page, 'report');
  let failedModules = 0;
  await page.route(moduleMatcher('report'), route => { failedModules++; return route.abort('failed'); });
  expect((await page.goto(destination('report', fixture)))?.status()).toBe(200);
  await expectHeadEntry(page, 'report');
  await painted(page); await expectFallback(page, 'report');
  expect(failedModules).toBe(1);
  await info.attach('report-module-failure.png', { body: await page.screenshot(), contentType: 'image/png' });
  await page.locator(`${topbar('report')} [data-lq-pane="actions"] a[href="/learning-path"]`).click();
  await expect(page).toHaveURL(/\/learning-path(?:\?|$)/);
  await expect(page.locator('main')).toBeVisible();
  await attach(info, 'fallback.json', { failure: 'module-abort', failedModules });
});

async function installInitialProbe(page: Page) {
  await page.addInitScript(() => {
    type Shift = PerformanceEntry & { value: number; hadRecentInput: boolean };
    const shifts: { time: number; value: number; recentInput: boolean }[] = [];
    const supported = PerformanceObserver.supportedEntryTypes.includes('layout-shift');
    const consume = (records: PerformanceEntry[]) => records.forEach(record => {
      const shift = record as Shift;
      shifts.push({ time: shift.startTime, value: shift.value, recentInput: shift.hadRecentInput });
    });
    const observer = supported ? new PerformanceObserver(list => consume(list.getEntries())) : null;
    observer?.observe({ type: 'layout-shift', buffered: true });
    (window as any).__lqFallbackProbe = () => {
      if (observer) consume(observer.takeRecords());
      let cls = 0, value = 0, start = -Infinity, last = -Infinity;
      for (const shift of shifts) {
        if (shift.recentInput) continue;
        if (shift.time - last < 1000 && shift.time - start < 5000) value += shift.value;
        else { value = shift.value; start = shift.time; }
        last = shift.time; cls = Math.max(cls, value);
      }
      return { supported, cls, shifts: [...shifts], paints: performance.getEntriesByType('paint').map(entry => ({ name: entry.name, time: entry.startTime })) };
    };
  });
}

for (const kind of ['manage', 'report'] as const) test(`S3 ${kind} late pilot module initializes once after body parsing with initial CLS at most .05`, async ({ page }, info) => {
  const fixture = await prepare(page, kind);
  await installInitialProbe(page); // Never reset at module, DOMContentLoaded or font readiness.
  let release!: () => void, requests = 0;
  const gate = new Promise<void>(resolve => { release = resolve; });
  const matcher = moduleMatcher(kind);
  await page.route(matcher, async route => { requests++; await gate; await route.continue(); });
  try {
    expect((await page.goto(destination(kind, fixture), { waitUntil: 'commit' }))?.status()).toBe(200);
    await expect.poll(() => requests).toBe(1);
    // Deferred modules hold DOMContentLoaded; render-blocking can also hold rAF.
    // Use timed condition polling, not fixed sleep, load/DCL or an rAF wait.
    await page.waitForFunction(selector => document.readyState === 'interactive'
      && !!document.body && !!document.querySelector(selector), rootSelector(kind), { polling: 50, timeout: 15000 });
    await expectHeadEntry(page, kind);
    await expect(page.locator(topbar(kind))).not.toHaveAttribute('data-lq-enhanced', 'true');
    const held = await page.evaluate(selector => ({ readyState: document.readyState,
      rootParsed: !!document.querySelector(selector), snapshot: (window as any).__lqFallbackProbe() }), rootSelector(kind));
    release();
    await page.waitForLoadState('load');
    await expect(page.locator(topbar(kind))).toHaveAttribute('data-lq-enhanced', 'true');
    if (kind === 'manage') await expect(page.locator('.course-card[tabindex="0"]').first()).toBeVisible();
    else await expect.poll(() => page.locator('[data-lq-report-card] [data-report-chart]').evaluateAll(nodes =>
      nodes.length > 0 && nodes.every(node => !!(window as any).echarts?.getInstanceByDom(node)))).toBe(true);
    await painted(page);
    const initial = await page.evaluate(() => (window as any).__lqFallbackProbe());
    await attach(info, 'held-and-initial-cls.json', { held, initial, requests });
    expect(initial.supported, 'Unsupported Layout Instability API is not zero CLS').toBe(true);
    expect(initial.cls).toBeLessThanOrEqual(0.05);
    expect(requests).toBe(1);

    // Reusing the actual emitted URL and public initializer must retain both
    // the page owner and Shell owners, not only an optimistic mounted attribute.
    const ownership = await page.evaluate(async ({ kind, name, selector }) => {
      const root = kind === 'manage' ? document.body : document.querySelector('[data-lq-report-card]')!;
      const key = Symbol.for(kind === 'manage' ? 'lanshare.manage-lq-pilot' : 'lanshare.report-card.pilot');
      const previous = (root as any)[key];
      const bar = document.querySelector(selector)!;
      const shellKey = Symbol.for('lanshare.lq.shell-owner'), shell = (bar as any)[shellKey];
      const chartIds = () => [...document.querySelectorAll('[data-report-chart]')].map(node => (window as any).echarts?.getInstanceByDom(node)?.id);
      const beforeCharts = chartIds();
      const source = (document.querySelector(`script[src$="/js/${name}"]`) as HTMLScriptElement).src;
      const module = await import(source);
      const next = kind === 'manage' ? module.initManageLqPilot(document) : module.initReportCardPilot(root);
      return { existing: !!previous && !!shell, samePage: previous === next && (root as any)[key] === previous,
        sameShell: (bar as any)[shellKey] === shell, beforeCharts, afterCharts: chartIds() };
    }, { kind, name: moduleName(kind), selector: topbar(kind) });
    expect(ownership.existing).toBe(true); expect(ownership.samePage).toBe(true); expect(ownership.sameShell).toBe(true);
    expect(ownership.afterCharts).toEqual(ownership.beforeCharts);
    expect(requests).toBe(1);
    const bar = page.locator(topbar(kind));
    const pane = bar.locator('[data-lq-pane="actions"]');
    await bar.locator(':scope > [data-lq-pane-open="actions"]').click();
    await expect(pane).toBeVisible(); await expect(page.locator('dialog:modal')).toHaveCount(1);
    await page.keyboard.press('Escape'); await expect(pane).toBeHidden();
    await expect(page.locator('dialog:modal')).toHaveCount(0);
    await expect(bar.locator(':scope > [data-lq-pane-open="actions"]')).toBeFocused();
    await attach(info, 'ownership.json', ownership);
  } finally {
    release(); // A failed parser condition must never leave a held request behind.
    await page.unrouteAll({ behavior: 'wait' });
  }
});

test('S3 manage pilot without JavaScript keeps native details, links and course content using the existing authenticated session', async ({ page, browser, baseURL }, info) => {
  await prepare(page, 'manage');
  // Clone the already authenticated token; do not log this account in again.
  const storageState = await page.context().storageState();
  const context = await browser.newContext({ baseURL, storageState, javaScriptEnabled: false, viewport: { width: 390, height: 844 } });
  try {
    const origin = new URL(baseURL!).origin;
    await context.route('**/*', route => {
      const url = new URL(route.request().url());
      return url.origin === origin || ['data:', 'blob:'].includes(url.protocol) ? route.continue() : route.abort();
    });
    const nativePage = await context.newPage();
    await guardS3Page(nativePage);
    expect((await nativePage.goto('/manage/library/courses'))?.status()).toBe(200);
    await expectFallback(nativePage, 'manage');
    await info.attach('manage-no-javascript.png', { body: await nativePage.screenshot(), contentType: 'image/png' });
    await nativeLibraryNavigation(nativePage, { javaScript: false });
    await expect(nativePage.locator('#manage-pilot-topbar [data-lq-pane="actions"] a[href="/profile"]')).toBeVisible();
  } finally { await context.close(); }
});

async function inlineGeometry(page: Page, kind: Kind) {
  return page.evaluate(({ bar, kind }) => {
    const selectors = { topbar: bar, actions: `${bar} [data-lq-pane="actions"]`,
      content: kind === 'manage' ? '.manage-content' : '[data-lq-report-card]',
      record: kind === 'manage' ? '#courseCardGrid .course-card' : '[data-lq-report-card] .report-course',
      ...(kind === 'manage' ? { sidebar: '[data-lq-manage-sidebar]' } : { lead: `${bar} .lq-topbar__lead` }) };
    return Object.fromEntries(Object.entries(selectors).map(([name, selector]) => {
      const node = document.querySelector(selector)!;
      const rect = node.getBoundingClientRect();
      return [name, { x: rect.x + scrollX, y: rect.y + scrollY, width: rect.width, height: rect.height }];
    }));
  }, { bar: topbar(kind), kind });
}

for (const kind of ['manage', 'report'] as const) test(`S3 ${kind} simulated unsupported render blocking paints SSR first and keeps stable inline navigation`, async ({ page }, info) => {
  const fixture = await prepare(page, kind);
  await installInitialProbe(page);
  await page.addInitScript(() => {
    // Simulate the exact feature query used by lq_shell_prepaint, without
    // setting its resulting flag ourselves. This is not an older-engine test.
    Object.defineProperty(HTMLScriptElement.prototype, 'blocking', {
      configurable: true, get() { return { supports: () => false }; },
    });
  });
  let release!: () => void, requests = 0, removedBlocking = 0;
  const gate = new Promise<void>(resolve => { release = resolve; });
  const pathname = new URL(destination(kind, fixture), 'http://localhost').pathname;
  await page.route(url => url.pathname === pathname, async route => {
    if (!route.request().isNavigationRequest() || route.request().frame() !== page.mainFrame()) return route.fallback();
    const response = await route.fetch();
    if (response.status() !== 200) return route.fulfill({ response });
    const html = (await response.text()).replace(/<script\b[^>]*>/gi, tag => {
      if (!tag.includes(`/js/${moduleName(kind)}`)) return tag;
      return tag.replace(/\sblocking\s*=\s*(?:"render"|'render'|render(?=\s|>))/gi, () => { removedBlocking++; return ''; });
    });
    // Removing the actual attribute is essential: merely overriding the JS
    // getter would leave Chromium's native render blocker active and mask CLS.
    await route.fulfill({ response, body: html });
  });
  await page.route(moduleMatcher(kind), async route => { requests++; await gate; await route.continue(); });
  try {
    expect((await page.goto(destination(kind, fixture), { waitUntil: 'commit' }))?.status()).toBe(200);
    await expect.poll(() => requests).toBe(1);
    await page.waitForFunction(selector => document.readyState === 'interactive'
      && !!document.querySelector(selector) && performance.getEntriesByName('first-contentful-paint').length > 0,
    rootSelector(kind), { polling: 50, timeout: 15000 });
    expect(removedBlocking).toBe(1);
    const script = page.locator(`head > script[src$="/js/${moduleName(kind)}"]`);
    await expect(script).toHaveCount(1); await expect(script).toHaveAttribute('type', 'module');
    expect(await script.getAttribute('blocking')).toBeNull();
    await expect(script).not.toHaveAttribute('async', ''); // Preserve standard module defer.
    await expect(page.locator('html')).toHaveAttribute('data-lq-shell-fallback', 'inline');
    await expectFallback(page, kind);
    // load/DCL are deliberately still pending. FontFaceSet.ready can wait for
    // the document lifecycle, so it must not be awaited behind our module gate.
    // The current font load status and two painted frames are bounded separately.
    await page.waitForFunction(() => document.fonts.status === 'loaded', undefined, { polling: 50, timeout: 15000 });
    await page.evaluate(async () => {
      await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
    });
    await page.evaluate(selector => {
      const panes = [...document.querySelectorAll(`${selector} [data-lq-pane], [data-lq-manage-sidebar] [data-lq-pane]`)];
      const violations: string[] = [];
      const observer = new MutationObserver(records => {
        for (const record of records) {
          const pane = record.target as Element;
          if (record.attributeName === 'data-lq-pane-mode' && (record.oldValue === 'drawer' || pane.getAttribute('data-lq-pane-mode') === 'drawer')
            || record.attributeName === 'hidden' && (record.oldValue !== null || pane.hasAttribute('hidden'))) {
            violations.push(`${pane.id}:${record.attributeName}:${record.oldValue}`);
          }
        }
      });
      for (const pane of panes) observer.observe(pane, { attributes: true, attributeOldValue: true, attributeFilter: ['data-lq-pane-mode', 'hidden'] });
      (window as any).__lqInlineTransitions = { violations, stop: () => observer.disconnect() };
    }, topbar(kind));
    const before = await inlineGeometry(page, kind);
    const held = await page.evaluate(() => ({ readyState: document.readyState,
      capability: (document.createElement('script') as any).blocking.supports('render'),
      snapshot: (window as any).__lqFallbackProbe(), releasedAt: performance.now() }));
    expect(held.readyState).toBe('interactive'); expect(held.capability).toBe(false);
    expect(held.snapshot.paints.some((entry: { name: string }) => entry.name === 'first-contentful-paint')).toBe(true);
    release();
    await page.waitForLoadState('load');
    const bar = page.locator(topbar(kind)), pane = bar.locator('[data-lq-pane="actions"]');
    await expect(bar).toHaveAttribute('data-lq-enhanced', 'true');
    if (kind === 'manage') await expect(page.locator('.course-card[tabindex="0"]').first()).toBeVisible();
    else await expect.poll(() => page.locator('[data-report-chart]').evaluateAll(nodes => nodes.length > 0
      && nodes.every(node => !!(window as any).echarts?.getInstanceByDom(node)))).toBe(true);
    await painted(page);
    if (kind === 'report') {
      const chip = bar.locator('.topbar-scene-chip');
      await expect(chip).toHaveCount(1);
      await waitForSceneGeometry(chip);
    }
    const after = await inlineGeometry(page, kind);
    const measurement = await page.evaluate(() => (window as any).__lqFallbackProbe());
    await attach(info, 'unsupported-inline-geometry.json', { simulated: true, held, before, after, measurement, requests, removedBlocking });
    expect(measurement.supported).toBe(true); expect(measurement.cls).toBeLessThanOrEqual(0.05);
    for (const name of Object.keys(before)) for (const axis of ['x', 'y', 'width', 'height'] as const) {
      expect(Math.abs(after[name][axis] - before[name][axis]), `${kind} ${name}.${axis} SSR→enhanced`).toBeLessThanOrEqual(1);
    }
    if (kind === 'report') {
      const chip = bar.locator('.topbar-scene-chip');
      await expect(chip).toHaveCount(1); await expect(chip).toBeVisible(); await expect(chip).toBeEnabled();
      const bounds = (await chip.boundingBox())!;
      expect(bounds.width).toBeCloseTo(44, 4); expect(bounds.height).toBeCloseTo(44, 4);
      // Use the existing cultivation_identity controller, never a fixture node.
      await chip.click();
      const pop = page.locator('.topbar-scene-pop');
      await expect(pop).toHaveCount(1); await expect(pop).toBeVisible(); await expect(pop).toContainText('今日一言');
      // The page's left gutter is outside the inset popup and fixed bottom nav.
      // A real pointer click dismisses without scrolling a distant heading into view.
      await page.mouse.click(2, 220); await expect(pop).toHaveCount(0);
      const afterScene = await inlineGeometry(page, kind);
      for (const name of ['topbar', 'lead']) for (const axis of ['x', 'y', 'width', 'height'] as const) {
        expect(Math.abs(afterScene[name][axis] - after[name][axis]), `${name}.${axis} after real scene disclosure`).toBeLessThanOrEqual(1);
      }
      await attach(info, 'unsupported-scene-chip.json', { bounds, afterScene });
    }
    await expect(pane).toHaveAttribute('data-lq-pane-mode', 'inline');
    await expect(pane).toHaveAttribute('open', ''); await expect(pane).toBeVisible();
    await expect(bar.locator(':scope > [data-lq-pane-open="actions"]')).toBeHidden();
    await expect(page.locator('dialog:modal')).toHaveCount(0);
    const singleOwner = await page.evaluate(async ({ kind, name, selector }) => {
      const root = kind === 'manage' ? document.body : document.querySelector('[data-lq-report-card]')!;
      const key = Symbol.for(kind === 'manage' ? 'lanshare.manage-lq-pilot' : 'lanshare.report-card.pilot');
      const owner = (root as any)[key], bar = document.querySelector(selector)!;
      const shellKey = Symbol.for('lanshare.lq.shell-owner'), shell = (bar as any)[shellKey];
      const module = await import((document.querySelector(`script[src$="/js/${name}"]`) as HTMLScriptElement).src);
      const again = kind === 'manage' ? module.initManageLqPilot(document) : module.initReportCardPilot(root);
      return !!owner && !!shell && again === owner && (root as any)[key] === owner && (bar as any)[shellKey] === shell;
    }, { kind, name: moduleName(kind), selector: topbar(kind) });
    expect(singleOwner).toBe(true); expect(requests).toBe(1);
    if (kind === 'manage') {
      await expect(page.locator('#manage-pilot-nav')).toHaveAttribute('data-lq-pane-mode', 'inline');
      const search = page.locator('#manageNavSearch');
      await search.fill('S3__不存在的导航项目__');
      await expect(page.locator('#manageNavEmpty')).toBeVisible();
      await expect(page.locator('#manageNav .manage-nav-item:visible')).toHaveCount(0);
      await search.press('Escape'); await expect(search).toHaveValue('');
      await expect(page.locator('#manageNavEmpty')).toBeHidden();
      await expect(page.locator('#manageNav a[href="/manage/library/courses"]')).toBeVisible();
    }
    const preferences = pane.locator('[data-ui-preferences-details]');
    const toggle = preferences.locator('[data-ui-preferences-toggle]'), panel = preferences.locator('[data-ui-preferences-panel]');
    await toggle.click(); await expect(preferences).toHaveAttribute('open', ''); await expect(panel).toBeVisible();
    await expect(panel.locator('select')).toHaveCount(3);
    for (const field of await panel.locator('select').all()) await expect(field).toBeEnabled();
    await toggle.scrollIntoViewIfNeeded();
    const disclosure = await toggle.evaluate(element => {
      const summary = element.getBoundingClientRect();
      const panel = element.closest('details')!.querySelector('[data-ui-preferences-panel]')!.getBoundingClientRect();
      const hit = document.elementFromPoint(summary.x + summary.width / 2, summary.y + summary.height / 2);
      return { summaryHit: !!hit && element.contains(hit),
        overlapWidth: Math.max(0, Math.min(summary.right, panel.right) - Math.max(summary.left, panel.left)),
        overlapHeight: Math.max(0, Math.min(summary.bottom, panel.bottom) - Math.max(summary.top, panel.top)) };
    });
    await attach(info, 'unsupported-preferences.json', disclosure);
    expect(disclosure.summaryHit, 'The open preference panel must not cover its own close summary').toBe(true);
    expect(disclosure.overlapWidth * disclosure.overlapHeight).toBeLessThanOrEqual(1);
    await toggle.click(); await expect(preferences).not.toHaveAttribute('open', '');
    const transitions = await page.evaluate(() => {
      const probe = (window as any).__lqInlineTransitions; probe.stop(); return probe.violations;
    });
    expect(transitions).toEqual([]);
    await expect(page.locator('html')).toHaveAttribute('data-lq-shell-fallback', 'inline');
    await expect(pane).toHaveAttribute('data-lq-pane-mode', 'inline');
    if (kind === 'manage') await nativeLibraryNavigation(page);
    else {
      await pane.locator('a[href="/learning-path"]').click();
      await expect(page).toHaveURL(/\/learning-path(?:\?|$)/); await expect(page.locator('main')).toBeVisible();
    }
  } finally { release(); await page.unrouteAll({ behavior: 'wait' }); }
});
