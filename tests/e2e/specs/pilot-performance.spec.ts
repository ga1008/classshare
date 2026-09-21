import { createRequire } from 'node:module';
import path from 'node:path';
import type { Page, Request, TestInfo } from '@playwright/test';
import { test, expect, guardS3Page, type S3Fixture } from '../fixtures/lq-s3';
import { loginStudent, loginTeacher } from '../fixtures/p03';
import { attach, painted, installTiming, installResourceProbe, settledResources, noResourceGrowth, listeners, finishTrace, timingSummary } from '../fixtures/lq-performance';

const { inspectGlass } = createRequire(path.resolve('package.json'))('./tools/ui/audit_glass_layers.cjs');
const routes = [
  '/manage/library/courses', '/manage/teaching/classes', '/manage/teaching/classroom-hub',
  '/manage/teaching/semesters', '/manage/library/textbooks', '/manage/library/lesson-plans',
  '/manage/library/materials', '/manage/system/users',
];
type Kind = 'manage' | 'report';
const topbar = (kind: Kind) => kind === 'manage' ? '#manage-pilot-topbar' : '#report-card-topbar';
const reportURL = (fixture: S3Fixture) => `/report-card?class_offering_id=${fixture.reportCard.offeringId}`;
const pollPeriods: Record<string, number> = {
  // Existing message_center_bell.js contract, not a general API exception.
  '/api/message-center/summary': 15000, '/api/blog/summary': 60000,
};
async function ready(page: Page, kind: Kind) {
  await expect(page.locator(topbar(kind))).toHaveAttribute('data-lq-enhanced', 'true');
  if (kind === 'manage') await expect(page.locator('[data-lq-manage-shell-status]')).toBeHidden();
  else await expect.poll(() => page.locator('[data-report-chart]').evaluateAll(elements =>
    elements.length > 0 && elements.every(element => !!(window as any).echarts?.getInstanceByDom(element)))).toBe(true);
  await page.waitForLoadState('networkidle');
  await painted(page);
}
async function theme(page: Page, glass: 'off' | 'tinted') {
  await page.evaluate(glass => {
    const owner = (document as any)[Symbol.for('lanshare.theme.installation')];
    if (!owner) throw Error('Missing shared theme installation');
    owner.refresh({ palette_key: 'indigo', appearance: 'light', glass });
  }, glass);
  await painted(page);
}
async function glassSnapshot(page: Page, info: TestInfo, label: string, maximum: number, requireBlur = false) {
  const result = await inspectGlass(page);
  await attach(info, `${label}.json`, result);
  // The existing utility's optional budget gates every rendered offscreen host.
  // §20's concurrent on-screen budget uses its explicit viewport count instead.
  expect(result.visibleViewportLayerCount, label).toBeLessThanOrEqual(maximum);
  if (requireBlur) expect(result.visibleViewportLayerCount, `${label}: tinted fixture must exercise actual blur`).toBeGreaterThan(0);
}

for (const width of [1440, 390]) test(`S3 LQ nine pilot pages enforce visible blur budgets at ${width}`, async ({ page }, info) => {
  test.setTimeout(180000);
  await page.setViewportSize({ width, height: 900 });
  const fixture = await guardS3Page(page);
  await loginTeacher(page, fixture);
  for (const [index, route] of [...routes, reportURL(fixture)].entries()) {
    const kind: Kind = index === routes.length ? 'report' : 'manage';
    if (route === '/manage/system/users') await loginTeacher(page, fixture, fixture.superTeacher);
    if (kind === 'report') await loginStudent(page, fixture);
    expect((await page.goto(route))?.status()).toBe(200);
    await ready(page, kind); await theme(page, 'tinted');
    await glassSnapshot(page, info, `${index}-rest-${width}`, 2, true);
    const more = page.locator(`${topbar(kind)} > [data-lq-pane-open="actions"]`);
    if (width === 390) {
      await more.click();
      await expect(page.locator(`${topbar(kind)}--lq-actions`)).toBeVisible();
      await painted(page);
      await glassSnapshot(page, info, `${index}-more-${width}`, 3);
      await page.keyboard.press('Escape');
      await expect(page.locator(`${topbar(kind)}--lq-actions`)).toBeHidden();
      if (kind === 'manage') {
        await page.locator('[data-lq-manage-sidebar] > [data-lq-pane-open="nav"]').click();
        await expect(page.locator('#manage-pilot-nav')).toBeVisible();
        await painted(page);
        await glassSnapshot(page, info, `${index}-navigation-${width}`, 3);
        await page.keyboard.press('Escape'); await expect(page.locator('#manage-pilot-nav')).toBeHidden();
      }
    } else {
      const preference = page.locator(`${topbar(kind)} [data-ui-preferences-toggle]`);
      await preference.click(); await painted(page);
      await glassSnapshot(page, info, `${index}-preferences-${width}`, 3);
      await page.keyboard.press('Escape');
    }
    await page.evaluate(() => window.scrollTo({ top: Math.min(400, document.documentElement.scrollHeight - innerHeight), behavior: 'instant' }));
    await painted(page);
    await glassSnapshot(page, info, `${index}-scroll-${width}`, 2);
  }
});

async function cycle(page: Page, kind: Kind, width: number) {
  const header = page.locator(topbar(kind));
  if (width === 390) {
    await header.locator(':scope > [data-lq-pane-open="actions"]').click();
    await expect(header.locator('[data-lq-pane="actions"]')).toBeVisible();
  }
  await header.locator('[data-ui-preferences-toggle]').click();
  await expect(header.locator('[data-ui-preferences-panel]')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(header.locator('[data-ui-preferences-panel]')).toBeHidden();
  if (width === 390) {
    await page.keyboard.press('Escape'); await expect(header.locator('[data-lq-pane="actions"]')).toBeHidden();
  }
  if (kind === 'manage') {
    if (width === 390) {
      await page.locator('[data-lq-manage-sidebar] > [data-lq-pane-open="nav"]').click();
      await expect(page.locator('#manage-pilot-nav')).toBeVisible();
    }
    // Navigation disclosure, not following a route or suppressing a real link.
    const group = page.locator('[data-lq-nav-group]').first(), summary = group.locator(':scope > summary');
    const wasOpen = await group.evaluate(element => (element as HTMLDetailsElement).open);
    await summary.click(); await expect(group).toHaveJSProperty('open', !wasOpen);
    await summary.click(); await expect(group).toHaveJSProperty('open', wasOpen);
    if (width === 390) {
      await page.keyboard.press('Escape'); await expect(page.locator('#manage-pilot-nav')).toBeHidden();
    }
  }
}

for (const kind of ['manage', 'report'] as const) for (const width of [1440, 390]) {
  test(`S3 LQ ${kind} twenty shell cycles retain resources and compare off/tinted latency at ${width}`, async ({ page, context }, info) => {
    test.setTimeout(180000);
    await page.setViewportSize({ width, height: 900 });
    const fixture = await guardS3Page(page);
    if (kind === 'manage') await loginTeacher(page, fixture); else await loginStudent(page, fixture);
    await installResourceProbe(page);
    await page.goto(kind === 'manage' ? routes[0] : reportURL(fixture)); await ready(page, kind);
    await theme(page, 'off'); await cycle(page, kind, width); await page.waitForLoadState('networkidle');
    const cdp = await context.newCDPSession(page);
    await cdp.send('Network.enable');
    await cdp.send('Emulation.setCPUThrottlingRate', { rate: 1 });
    await installTiming(page);
    const probeCheck = await page.evaluate(() => (window as any).__lqPilotResources.selfTest());
    expect(probeCheck.after).toEqual(probeCheck.before);
    for (const name of ['interval', 'MutationObserver', 'ResizeObserver']) expect(probeCheck.during[name]).toBe(probeCheck.before[name] + 1);
    const initialResources = await settledResources(page);
    expect(initialResources.totals.ResizeObserver, 'Real topbar must have its observed size owner').toBeGreaterThan(0);
    await attach(info, `${kind}-${width}-resource-baseline.json`, { probeCheck, initialResources });
    const requests: { method: string; path: string; time: number; type: string }[] = [];
    const pending = new Map<Request, string>(), concurrent: Record<string, number> = {}, peaks: Record<string, number> = {};
    const errors: string[] = [];
    let socketStarts = 0, eventSourceStarts = 0;
    const onRequest = (request: Request) => {
      const path = new URL(request.url()).pathname;
      requests.push({ method: request.method(), path, time: performance.now(), type: request.resourceType() });
      pending.set(request, path); concurrent[path] = (concurrent[path] || 0) + 1; peaks[path] = Math.max(peaks[path] || 0, concurrent[path]);
    };
    const onEnd = (request: Request) => { const path = pending.get(request); if (path) { concurrent[path]--; pending.delete(request); } };
    const onSocket = () => { socketStarts++; };
    const onError = (error: Error) => { errors.push(error.message); };
    const onSource = (event: { type?: string }) => { if (event.type === 'EventSource') eventSourceStarts++; };
    page.on('request', onRequest); page.on('requestfinished', onEnd); page.on('requestfailed', onEnd);
    page.on('websocket', onSocket); page.on('pageerror', onError); cdp.on('Network.requestWillBeSent', onSource);
    const baselineListeners = await listeners(cdp), results: Record<string, any> = {};
    await attach(info, `${kind}-${width}-listener-baseline.json`, baselineListeners);
    try {
      for (const glass of ['off', 'tinted'] as const) {
        await theme(page, glass); await cycle(page, kind, width);
        const phaseResources = await settledResources(page);
        noResourceGrowth(initialResources, phaseResources, `${glass} warmup`);
        await cdp.send('Tracing.start', { categories: 'devtools.timeline,disabled-by-default-devtools.timeline,blink.user_timing', transferMode: 'ReturnAsStream' });
        const start = performance.now(), offset = requests.length, sockets = socketStarts, sources = eventSourceStarts;
        await page.evaluate(prefix => (window as any).__lqPilotTiming.reset(prefix), `lq-s3-${kind}-${width}-${glass}`);
        try {
          for (let cycleIndex = 0; cycleIndex < 20; cycleIndex++) await cycle(page, kind, width);
          // Includes pending 240ms preference debounce / next-task requests rather
          // than ending the network measurement on the last synchronous click.
          const activeResources = await settledResources(page);
          const duration = performance.now() - start;
          const timing = timingSummary(await page.evaluate(() => (window as any).__lqPilotTiming.snapshot()));
          const network = requests.slice(offset), resources = await listeners(cdp);
          results[glass] = { duration, timing, network, resources, phaseResources, activeResources, socketStarts: socketStarts - sockets, eventSourceStarts: eventSourceStarts - sources };
          await attach(info, `${kind}-${width}-${glass}-measurements.json`, results[glass]);
          expect(timing.trustedEvents).toBeGreaterThanOrEqual(40);
          if (timing.p95 !== null) expect(timing.p95, `${glass} laboratory p95`).toBeLessThanOrEqual(200);
          expect(resources).toEqual(baselineListeners);
          noResourceGrowth(phaseResources, activeResources, `${glass} twenty cycles`);
          noResourceGrowth(initialResources, activeResources, `${glass} original document baseline`);
          expect(socketStarts - sockets).toBe(0); expect(eventSourceStarts - sources).toBe(0);
          expect(network.filter(item => item.method !== 'GET' || !(item.path in pollPeriods)), `${glass}: shell disclosure must not issue application/asset requests`).toEqual([]);
          for (const [path, period] of Object.entries(pollPeriods)) {
            expect(network.filter(item => item.path === path).length, `${glass}: existing ${period}ms polling contract`).toBeLessThanOrEqual(Math.ceil(duration / period));
            expect(peaks[path] || 0, `${path}: concurrent polling`).toBeLessThanOrEqual(1);
          }
        } finally { await finishTrace(cdp, info, `${kind}-${width}-${glass}-trace.json`); }
      }
      const comparison = { browser: await page.evaluate(() => navigator.userAgent), viewport: { width, height: 900 }, cpuRate: 1,
        operation: '20 identical shell disclosure cycles; off then tinted in the same document; theme changes and warmup excluded',
        off: results.off.timing, tinted: results.tinted.timing,
        requiresLongTaskAttribution: results.tinted.timing.longTasks.length > 0,
        review: 'Any tinted >50ms task requires source-stack/CDP trace comparison against off before S3 sign-off. Counts alone neither prove nor dismiss glass attribution; existing business tasks remain separately ticketed.',
      };
      await attach(info, `${kind}-${width}-comparison.json`, comparison);
      if (comparison.requiresLongTaskAttribution) info.annotations.push({ type: 'long-task-review', description: 'Inspect off/tinted trace attachments; long-task attribution remains open.' });
      // Include the presentation-only theme refresh and per-phase warmup too.
      await attach(info, `${kind}-${width}-all-network.json`, { requests, peaks, socketStarts, eventSourceStarts, errors });
      expect(requests.filter(item => item.method !== 'GET' || !(item.path in pollPeriods))).toEqual([]);
      expect(socketStarts).toBe(0); expect(eventSourceStarts).toBe(0); expect(errors).toEqual([]);
    } finally {
      page.off('request', onRequest); page.off('requestfinished', onEnd); page.off('requestfailed', onEnd);
      page.off('websocket', onSocket); page.off('pageerror', onError); cdp.off('Network.requestWillBeSent', onSource);
      await page.evaluate(() => { (window as any).__lqPilotTiming?.dispose(); (window as any).__lqPilotResources?.dispose(); });
      await cdp.detach();
    }
  });
}
