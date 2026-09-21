import fs from 'node:fs';
import type { CDPSession, Page, Request } from '@playwright/test';
import { test, expect, readS3Fixture } from '../fixtures/lq-s3';
import { loginStudent, loginTeacher } from '../fixtures/p03';
import { attach, painted, installTiming, installResourceProbe, settledResources, finishTrace, timingSummary } from '../fixtures/lq-performance';

type Kind = 'student' | 'teacher';
const graph = process.env.LQ_S4_GRAPH || JSON.parse(fs.readFileSync('static/assets/manifest.json', 'utf8')).revision;
const topbar = (kind: Kind) => kind === 'student' ? '#navbar-topbar' : '#manage-pilot-topbar';
const moduleName = (kind: Kind) => kind === 'student' ? 'navbar_lq.js' : 'manage_lq_pilot.js';
const pollPeriods: Record<string, number> = { '/api/message-center/summary': 15000, '/api/blog/summary': 60000 };

async function initialProbe(page: Page) {
  await page.addInitScript(() => {
    const shifts: any[] = [];
    const supported = PerformanceObserver.supportedEntryTypes.includes('layout-shift');
    const collect = (entries: PerformanceEntry[]) => {
      for (const entry of entries as any[]) shifts.push({ time: entry.startTime, value: entry.value,
        recentInput: entry.hadRecentInput, sources: entry.sources?.map((source: any) => ({
          tag: source.node?.tagName, id: source.node?.id, className: source.node?.className,
          previousRect: source.previousRect?.toJSON(), currentRect: source.currentRect?.toJSON(),
        })) });
    };
    const observer = supported ? new PerformanceObserver(list => collect(list.getEntries())) : null;
    observer?.observe({ type: 'layout-shift', buffered: true });
    (window as any).__lqS4Initial = () => {
      if (observer) collect(observer.takeRecords());
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

async function finiteTimers(page: Page) {
  // Complements the S3 interval/observer probe. Native scheduling, callback this,
  // extra arguments and delay coercion remain unchanged. String timers are not
  // rewritten: encountering one is explicit unsupported evidence, never zero.
  await page.addInitScript(() => {
    const pending = new Map<number, { kind: string; owner: string }>();
    const originals: (() => void)[] = [];
    let opaque = 0;
    const owner = () => (new Error().stack || '').split('\n').find(line => /\/static\//.test(line))?.trim() || 'fixture-or-browser';
    const replace = (key: string, wrap: (native: Function) => Function) => {
      const descriptor = Object.getOwnPropertyDescriptor(window, key)!;
      Object.defineProperty(window, key, { ...descriptor, value: wrap(descriptor.value) });
      originals.push(() => Object.defineProperty(window, key, descriptor));
    };
    replace('setTimeout', native => function(this: unknown, callback: unknown, ...args: unknown[]) {
      if (typeof callback !== 'function') { opaque++; return Reflect.apply(native, this, [callback, ...args]); }
      const record = { kind: 'timeout', owner: owner() };
      let id: number;
      id = Reflect.apply(native, this, [function(this: unknown, ...values: unknown[]) {
        if (pending.get(id) === record) pending.delete(id);
        return Reflect.apply(callback, this, values);
      }, ...args]);
      pending.set(id, record); return id;
    });
    for (const key of ['clearTimeout', 'clearInterval']) replace(key, native => function(this: unknown, id: unknown) {
      const result = Reflect.apply(native, this, [id]);
      if (typeof id === 'number' || typeof id === 'string') pending.delete(Number(id));
      return result;
    });
    // rAF handles live in their own namespace, unlike the shared timer pool.
    const frames = new Map<number, { kind: string; owner: string }>();
    replace('requestAnimationFrame', native => function(this: unknown, callback: Function) {
      if (typeof callback !== 'function') return Reflect.apply(native, this, [callback]);
      const record = { kind: 'animationFrame', owner: owner() };
      let id: number;
      id = Reflect.apply(native, this, [function(this: unknown, time: number) {
        if (frames.get(id) === record) frames.delete(id);
        return Reflect.apply(callback, this, [time]);
      }]);
      frames.set(id, record); return id;
    });
    replace('cancelAnimationFrame', native => function(this: unknown, id: number) {
      const result = Reflect.apply(native, this, [id]); frames.delete(id); return result;
    });
    (window as any).__lqS4Timers = {
      snapshot() { return { opaque, resources: [...pending.values(), ...frames.values()] }; },
      async selfTest() {
        const before = { timeout: pending.size, animationFrame: frames.size };
        const timeout = window.setTimeout(() => {}, 2147483647), frame = window.requestAnimationFrame(() => {});
        const during = { timeout: pending.size, animationFrame: frames.size };
        window.clearInterval(timeout); window.cancelAnimationFrame(frame);
        const after = { timeout: pending.size, animationFrame: frames.size };
        const callback = await new Promise(resolve => window.setTimeout(function(this: unknown, first: string, second: number) {
          resolve({ correctThis: this === window, first, second });
        }, 0, 'preserved', 7));
        return { before, during, after, callback };
      },
      dispose() { originals.reverse().forEach(restore => restore()); },
    };
  });
}

async function ready(page: Page, kind: Kind) {
  await expect(page.locator(topbar(kind))).toHaveAttribute('data-lq-enhanced', 'true');
  if (kind === 'teacher') {
    await expect(page.locator('body')).toHaveClass(/lq-manage-shell/);
    await expect(page.locator('body')).not.toHaveClass(/lq-manage-pilot/);
    await expect(page.locator('[data-lq-manage-shell-status]')).toBeHidden();
  }
  await expect(page.locator(`script[src$="/js/${moduleName(kind)}"]`)).toHaveAttribute('src', new RegExp(graph));
  await page.waitForLoadState('networkidle'); await painted(page);
  await page.evaluate(async () => {
    const finite = document.getAnimations().filter(animation => {
      const end = animation.effect?.getComputedTiming().endTime;
      return typeof end === 'number' && Number.isFinite(end);
    });
    await Promise.race([Promise.all(finite.map(animation => animation.finished.catch(() => {}))),
      new Promise<void>(resolve => setTimeout(resolve, 2000))]);
  });
  await painted(page);
}

async function theme(page: Page, glass: 'off' | 'tinted') {
  await page.evaluate(glass => {
    const owner = (document as any)[Symbol.for('lanshare.theme.installation')];
    if (!owner) throw Error('Missing shared theme owner');
    owner.refresh({ palette_key: 'indigo', appearance: 'light', glass });
  }, glass);
  await painted(page);
  await expect(page.locator('html')).toHaveAttribute('data-lq-glass', glass);
}

async function cycle(page: Page, kind: Kind, width: number) {
  const header = page.locator(topbar(kind));
  if (width < 1024) {
    await header.locator(':scope > [data-lq-pane-open="actions"]').click();
    await expect(header.locator('[data-lq-pane="actions"]')).toBeVisible();
  }
  await header.locator('[data-ui-preferences-toggle]').click();
  await expect(header.locator('[data-ui-preferences-panel]')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(header.locator('[data-ui-preferences-panel]')).toBeHidden();
  if (width < 1024) {
    await page.keyboard.press('Escape');
    await expect(header.locator('[data-lq-pane="actions"]')).toBeHidden();
  }
  if (kind === 'teacher') {
    if (width < 1024) {
      await page.locator('[data-lq-manage-sidebar] > [data-lq-pane-open="nav"]').click();
      await expect(page.locator('#manage-pilot-nav')).toBeVisible();
    }
    const group = page.locator('[data-lq-nav-group]').first();
    const open = await group.evaluate(element => (element as HTMLDetailsElement).open);
    await group.locator(':scope > summary').click(); await expect(group).toHaveJSProperty('open', !open);
    await group.locator(':scope > summary').click(); await expect(group).toHaveJSProperty('open', open);
    // A real click on the existing neutral search field ends hover/focus on
    // explanation-bearing summaries. Otherwise delayed help legitimately owns
    // the first Escape and its scheduled timer is not a shell lifecycle leak.
    await page.locator('#manageNavSearch').click();
    await expect(page.locator('#ui-explanation-popover')).toBeHidden();
    if (width < 1024) { await page.keyboard.press('Escape'); await expect(page.locator('#manage-pilot-nav')).toBeHidden(); }
  }
}

async function listenerSnapshot(cdp: CDPSession) {
  const result = await cdp.send('Runtime.evaluate', {
    expression: `JSON.stringify([...new Set([window,document,document.documentElement,document.body,window.visualViewport,...document.querySelectorAll('[data-lq-shell],[data-lq-shell] *,#navbar-dock,#navbar-dock *,[data-ui-preferences-details],#profile-basic-form,#profile-nickname')])].filter(Boolean).map((node,index)=>({index,tag:node.tagName||node.constructor.name,id:node.id||'',count:Object.values(getEventListeners(node)).reduce((sum,list)=>sum+list.length,0)})))`,
    includeCommandLineAPI: true, returnByValue: true,
  });
  if (result.exceptionDetails || !result.result.value) throw Error('Listener measurement unavailable');
  return JSON.parse(result.result.value);
}

async function resourceSnapshot(page: Page) {
  const native = await settledResources(page);
  const timers = await page.evaluate(() => (window as any).__lqS4Timers.snapshot());
  // The first application frame is the allocating owner. Further call frames
  // intentionally differ between automatic mount and explicit lifecycle tests.
  const resources = [...native.resources.map((resource: any) => ({ ...resource,
    owner: resource.origin.split('\n').find((line: string) => /\/static\//.test(line))?.trim() || resource.origin })),
    ...timers.resources];
  const owners: Record<string, number> = {}, targets: Record<string, number> = {};
  for (const resource of resources) {
    if (resource.owner === 'fixture-or-browser') continue;
    const key = `${resource.kind}:${resource.owner}`;
    owners[key] = (owners[key] || 0) + 1;
    targets[key] = (targets[key] || 0) + (resource.targets || 0);
  }
  return { owners, targets, native, timers };
}
function noGrowth(before: any, after: any, label: string) {
  expect(after.timers.opaque, 'String timers must be reported, not counted as cleaned up').toBe(0);
  for (const [owner, count] of Object.entries(after.owners)) expect(count, `${label}: ${owner}`).toBeLessThanOrEqual(before.owners[owner] || 0);
  for (const [owner, count] of Object.entries(after.targets)) expect(count, `${label}: observed targets ${owner}`).toBeLessThanOrEqual(before.targets[owner] || 0);
}

async function rememberOwnerAndDraft(page: Page, kind: Kind) {
  await page.locator('#profile-nickname').fill('S4 生命周期保留草稿');
  await page.evaluate(async ({ kind, file }) => {
    const module = await import(document.querySelector<HTMLScriptElement>(`script[src$="/js/${file}"]`)!.src);
    const init = kind === 'student' ? module.initNavbarLq : module.initManageLqPilot;
    const input = document.querySelector<HTMLInputElement>('#profile-nickname')!;
    const form = input.form!, bar = document.querySelector(kind === 'student' ? '#navbar-topbar' : '#manage-pilot-topbar')!;
    const dock = document.querySelector('#navbar-dock');
    const ownerTarget = kind === 'student' ? bar : document.body;
    const key = Symbol.for(kind === 'student' ? 'lanshare.navbar-lq' : 'lanshare.manage-lq-pilot');
    (window as any).__lqS4Owner = {
      remount() {
        const old = init(document);
        if (!old || init(document) !== old || (ownerTarget as any)[key] !== old) throw Error('Duplicate or missing shell owner');
        old.destroy(); old.destroy();
        const next = init(document); old.destroy();
        if (!next || next === old || init(document) !== next || (ownerTarget as any)[key] !== next) throw Error('Stale destroy damaged new owner');
      },
      snapshot() { return { sameInput: input === document.querySelector('#profile-nickname'),
        sameForm: form === document.querySelector('#profile-basic-form') && input.form === form,
        sameBar: bar === document.querySelector(kind === 'student' ? '#navbar-topbar' : '#manage-pilot-topbar'),
        sameDock: dock === document.querySelector('#navbar-dock'), value: input.value,
        duplicateIds: [...document.querySelectorAll('[id]')].map(node => node.id).filter((id, index, ids) => ids.indexOf(id) !== index) }; },
    };
  }, { kind, file: moduleName(kind) });
}

for (const kind of ['student', 'teacher'] as const) for (const width of [1440, 390]) {
  test(`S4 ${kind} shared shell initial CLS, twenty remounts and 4x CPU off/tinted latency at ${width}`, async ({ page, context }, info) => {
    await page.setViewportSize({ width, height: 900 });
    const fixture = readS3Fixture();
    if (kind === 'student') await loginStudent(page, fixture); else await loginTeacher(page, fixture);
    await initialProbe(page); await installResourceProbe(page); await finiteTimers(page);
    for (const route of kind === 'teacher' ? ['/manage/me', '/manage/me/settings'] : ['/profile?section=settings']) {
      expect((await page.goto(route))?.status()).toBe(200); await ready(page, kind);
      const initial = await page.evaluate(() => (window as any).__lqS4Initial());
      await attach(info, `${kind}-${width}-${route.endsWith('settings') ? 'settings' : 'overview'}-initial.json`, { graph, route, initial });
      expect(initial.supported).toBe(true); expect(initial.paints.some((entry: any) => entry.name === 'first-contentful-paint')).toBe(true);
      expect(initial.cls, route).toBeLessThanOrEqual(.05);
    }
    await theme(page, 'off');
    // Warm both real modes before a stable resource baseline. The first native
    // drawer lazily installs the shared layer service's document observer.
    for (const warmWidth of [390, 1440, width]) {
      await page.setViewportSize({ width: warmWidth, height: 900 }); await cycle(page, kind, warmWidth);
    }
    await rememberOwnerAndDraft(page, kind);
    const cdp = await context.newCDPSession(page); await cdp.send('Network.enable');
    await installTiming(page);
    const probeCheck = await page.evaluate(async () => ({ native: (window as any).__lqPilotResources.selfTest(),
      finite: await (window as any).__lqS4Timers.selfTest() }));
    expect(probeCheck.native.after).toEqual(probeCheck.native.before);
    for (const name of ['interval', 'MutationObserver', 'ResizeObserver']) expect(probeCheck.native.during[name]).toBe(probeCheck.native.before[name] + 1);
    expect(probeCheck.finite.after).toEqual(probeCheck.finite.before);
    for (const name of ['timeout', 'animationFrame']) expect(probeCheck.finite.during[name]).toBe(probeCheck.finite.before[name] + 1);
    expect(probeCheck.finite.callback).toEqual({ correctThis: true, first: 'preserved', second: 7 });
    const initialResources = await resourceSnapshot(page), baselineListeners = await listenerSnapshot(cdp);
    const requests: { method: string; path: string; time: number }[] = [], errors: string[] = [];
    const pending = new Map<Request, string>(), concurrent: Record<string, number> = {}, peaks: Record<string, number> = {};
    let sockets = 0, sources = 0;
    const onRequest = (request: Request) => {
      const path = new URL(request.url()).pathname;
      requests.push({ path, method: request.method(), time: performance.now() }); pending.set(request, path);
      concurrent[path] = (concurrent[path] || 0) + 1; peaks[path] = Math.max(peaks[path] || 0, concurrent[path]);
    };
    const onEnd = (request: Request) => { const path = pending.get(request); if (path) { concurrent[path]--; pending.delete(request); } };
    const onSocket = () => { sockets++; }, onError = (error: Error) => errors.push(error.message);
    const onSource = (event: { type?: string }) => { if (event.type === 'EventSource') sources++; };
    page.on('request', onRequest); page.on('requestfinished', onEnd); page.on('requestfailed', onEnd);
    page.on('websocket', onSocket); page.on('pageerror', onError); cdp.on('Network.requestWillBeSent', onSource);
    const start = performance.now(), results: Record<string, any> = {};
    try {
      await attach(info, 'baseline.json', { graph, probeCheck, initialResources, baselineListeners });
      for (let index = 0; index < 20; index++) {
        const cycleWidth = index % 2 ? 1440 : 390;
        await page.setViewportSize({ width: cycleWidth, height: 900 });
        await cycle(page, kind, cycleWidth);
        await page.evaluate(() => (window as any).__lqS4Owner.remount());
        await expect(page.locator(topbar(kind))).toHaveAttribute('data-lq-enhanced', 'true');
      }
      await page.setViewportSize({ width, height: 900 }); await cycle(page, kind, width);
      const afterMounts = await resourceSnapshot(page), mountedListeners = await listenerSnapshot(cdp);
      const draft = await page.evaluate(() => (window as any).__lqS4Owner.snapshot());
      await attach(info, 'twenty-remounts.json', { afterMounts, mountedListeners, draft });
      expect(draft).toEqual({ sameInput: true, sameForm: true, sameBar: true, sameDock: true,
        value: 'S4 生命周期保留草稿', duplicateIds: [] });
      noGrowth(initialResources, afterMounts, '20 remounts'); expect(mountedListeners).toEqual(baselineListeners);
      await cdp.send('Emulation.setCPUThrottlingRate', { rate: 4 });
      for (const glass of ['off', 'tinted'] as const) {
        await theme(page, glass); await cycle(page, kind, width);
        const baseline = await resourceSnapshot(page);
        await cdp.send('Tracing.start', { categories: 'devtools.timeline,disabled-by-default-devtools.timeline,blink.user_timing', transferMode: 'ReturnAsStream' });
        await page.evaluate(prefix => (window as any).__lqPilotTiming.reset(prefix), `lq-s4-${kind}-${width}-${glass}`);
        try {
          for (let index = 0; index < 20; index++) await cycle(page, kind, width);
          const resources = await resourceSnapshot(page);
          const timing = timingSummary(await page.evaluate(() => (window as any).__lqPilotTiming.snapshot()));
          results[glass] = { timing, resources };
          await attach(info, `${glass}-measurements.json`, results[glass]);
          expect(timing.trustedEvents).toBeGreaterThanOrEqual(40);
          if (timing.p95 !== null) expect(timing.p95, `${glass} 4x laboratory p95`).toBeLessThanOrEqual(200);
          noGrowth(baseline, resources, `${glass} interaction cycles`);
          noGrowth(initialResources, resources, `${glass} document baseline`);
          expect(await listenerSnapshot(cdp)).toEqual(baselineListeners);
        } finally { await finishTrace(cdp, info, `${kind}-${width}-${glass}-4x-trace.json`); }
      }
      const duration = performance.now() - start;
      await attach(info, 'comparison-and-network.json', { graph, browser: await page.evaluate(() => navigator.userAgent),
        viewport: { width, height: 900 }, cpuRate: 4, operation: '20 remounts with real breakpoint disclosures; then 20 identical off/tinted interaction cycles each',
        results, requests, peaks, sockets, sources, errors, duration,
        limitation: 'Desktop Chrome laboratory CPU emulation; not a phone, field INP or physical keyboard/viewport measurement.' });
      expect(requests.filter(item => item.method !== 'GET' || !(item.path in pollPeriods))).toEqual([]);
      for (const [path, period] of Object.entries(pollPeriods)) {
        expect(requests.filter(item => item.path === path).length, path).toBeLessThanOrEqual(Math.ceil(duration / period));
        expect(peaks[path] || 0, `${path}: concurrent polling`).toBeLessThanOrEqual(1);
      }
      expect(sockets).toBe(0); expect(sources).toBe(0); expect(errors).toEqual([]);
      // A failure here retains both traces for attribution. Existing business
      // long tasks must be reported separately, never erased by raising 50ms.
      expect(results.off.timing.longTasks, 'Off baseline >50ms tasks require attribution').toEqual([]);
      expect(results.tinted.timing.longTasks, 'Tinted >50ms tasks require off/trace attribution').toEqual([]);
    } finally {
      page.off('request', onRequest); page.off('requestfinished', onEnd); page.off('requestfailed', onEnd);
      page.off('websocket', onSocket); page.off('pageerror', onError); cdp.off('Network.requestWillBeSent', onSource);
      await cdp.send('Emulation.setCPUThrottlingRate', { rate: 1 });
      await page.evaluate(() => { (window as any).__lqPilotTiming?.dispose(); (window as any).__lqS4Timers?.dispose(); (window as any).__lqPilotResources?.dispose(); });
      await cdp.detach();
    }
  });
}
