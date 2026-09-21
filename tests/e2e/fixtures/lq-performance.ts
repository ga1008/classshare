// Shared measurement-only probes extracted verbatim from the accepted S3 gate.
import type { CDPSession, Page, TestInfo } from '@playwright/test';
import { expect } from '@playwright/test';

export async function attach(info: TestInfo, name: string, value: unknown) {
  await info.attach(name, { body: JSON.stringify(value, null, 2), contentType: 'application/json' });
}
export async function painted(page: Page) {
  await page.evaluate(async () => {
    await document.fonts.ready;
    await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
  });
}
export async function installTiming(page: Page) {
  await page.evaluate(() => {
    const events: any[] = [], tasks: any[] = [];
    const supported = ['event', 'longtask'].every(type => PerformanceObserver.supportedEntryTypes.includes(type));
    if (!supported) throw Error('Chromium Event Timing and Long Tasks APIs are required; unsupported is not zero');
    const collect = (destination: any[], records: PerformanceEntry[]) => {
      for (const record of records as any[]) destination.push({ name: record.name, startTime: record.startTime, duration: record.duration,
        interactionId: record.interactionId, processingStart: record.processingStart, processingEnd: record.processingEnd,
        attribution: record.attribution?.map((item: any) => item.toJSON()) });
    };
    const eventObserver = new PerformanceObserver(list => collect(events, list.getEntries()));
    const taskObserver = new PerformanceObserver(list => collect(tasks, list.getEntries()));
    eventObserver.observe({ type: 'event', durationThreshold: 16 } as PerformanceObserverInit);
    taskObserver.observe({ type: 'longtask' });
    let trustedEvents = 0, phaseStart = performance.now(), markPrefix = '';
    const trusted = (event: Event) => { if (event.isTrusted) trustedEvents++; };
    window.addEventListener('click', trusted, true); window.addEventListener('keydown', trusted, true);
    (window as any).__lqPilotTiming = {
      reset(prefix: string) { eventObserver.takeRecords(); taskObserver.takeRecords(); events.length = tasks.length = trustedEvents = 0; markPrefix = prefix; phaseStart = performance.mark(`${markPrefix}-start`).startTime; },
      snapshot() {
        const phaseEnd = performance.mark(`${markPrefix}-end`).startTime;
        collect(events, eventObserver.takeRecords()); collect(tasks, taskObserver.takeRecords());
        return { events: events.filter(item => item.startTime >= phaseStart && item.startTime <= phaseEnd), tasks: tasks.filter(item => item.startTime >= phaseStart && item.startTime <= phaseEnd),
          trustedEvents, phaseStart, phaseEnd, timeOrigin: performance.timeOrigin, marks: { start: `${markPrefix}-start`, end: `${markPrefix}-end` } };
      },
      dispose() { eventObserver.disconnect(); taskObserver.disconnect(); window.removeEventListener('click', trusted, true); window.removeEventListener('keydown', trusted, true); },
    };
  });
}
export async function installResourceProbe(page: Page) {
  // Runs in the target document before application modules. Constructors,
  // observer callbacks, timer handlers/arguments and scheduling stay native.
  await page.addInitScript(() => {
    type Resource = { id: number; kind: string; origin: string; delay?: number | string; targets?: Set<Node> };
    const active = new Map<number, Resource>(), intervals = new Map<number, Resource>();
    const records = new WeakMap<object, Resource>(), restores: (() => void)[] = [];
    let nextId = 0;
    const origin = () => (new Error().stack || '').split('\n').slice(3, 9).join('\n');
    const replace = (target: object, key: string, wrap: (native: Function) => Function) => {
      const descriptor = Object.getOwnPropertyDescriptor(target, key);
      if (!descriptor || typeof descriptor.value !== 'function') throw Error(`Cannot instrument native ${key}`);
      Object.defineProperty(target, key, { ...descriptor, value: wrap(descriptor.value) });
      restores.push(() => Object.defineProperty(target, key, descriptor));
    };
    replace(window, 'setInterval', native => function(this: unknown, ...args: unknown[]) {
      const handle = Reflect.apply(native, this, args);
      // Do not coerce a caller's delay object a second time or wrap its handler.
      intervals.set(handle, { id: ++nextId, kind: 'interval', origin: origin(),
        delay: typeof args[1] === 'number' ? args[1] : 'native-converted' });
      return handle;
    });
    // Browser timer IDs share a pool; either clear method can clear an interval.
    for (const method of ['clearInterval', 'clearTimeout']) replace(window, method, native => function(this: unknown, ...args: unknown[]) {
      const result = Reflect.apply(native, this, args);
      if (typeof args[0] === 'number' || typeof args[0] === 'string') intervals.delete(Number(args[0]));
      return result;
    });
    for (const [kind, Constructor] of [['MutationObserver', window.MutationObserver], ['ResizeObserver', window.ResizeObserver]] as const) {
      if (!Constructor) throw Error(`Required ${kind} is unavailable; unsupported is not zero`);
      replace(Constructor.prototype, 'observe', native => function(this: object, ...args: unknown[]) {
        const result = Reflect.apply(native, this, args); // Native validation first.
        let record = records.get(this);
        if (!record) { record = { id: ++nextId, kind, origin: origin(), targets: new Set() }; records.set(this, record); }
        record.targets!.add(args[0] as Node); active.set(record.id, record);
        return result;
      });
      replace(Constructor.prototype, 'disconnect', native => function(this: object, ...args: unknown[]) {
        const result = Reflect.apply(native, this, args), record = records.get(this);
        if (record) { record.targets!.clear(); active.delete(record.id); }
        return result;
      });
      if (kind === 'ResizeObserver') replace(Constructor.prototype, 'unobserve', native => function(this: object, ...args: unknown[]) {
        const result = Reflect.apply(native, this, args), record = records.get(this);
        if (record) { record.targets!.delete(args[0] as Node); if (!record.targets!.size) active.delete(record.id); }
        return result;
      });
    }
    const snapshot = () => {
      const resources = [...intervals.values(), ...active.values()].map(({ targets, ...record }) => ({ ...record, targets: targets?.size }));
      const totals: Record<string, number> = { interval: 0, MutationObserver: 0, ResizeObserver: 0 };
      const owners: Record<string, number> = {}, targetOwners: Record<string, number> = {};
      for (const resource of resources) {
        totals[resource.kind]++;
        const key = `${resource.kind}:${resource.origin}`; owners[key] = (owners[key] || 0) + 1;
        targetOwners[key] = (targetOwners[key] || 0) + (resource.targets || 0);
      }
      return { totals, owners, targetOwners, resources };
    };
    (window as any).__lqPilotResources = {
      snapshot,
      selfTest() {
        const before = snapshot().totals;
        const timer = window.setInterval(() => {}, 2147483647);
        const mutation = new MutationObserver(() => {}), resize = new ResizeObserver(() => {});
        mutation.observe(document.documentElement, { attributes: true }); mutation.observe(document.documentElement, { attributes: true });
        resize.observe(document.documentElement); resize.observe(document.documentElement);
        const during = snapshot().totals;
        window.clearTimeout(timer); mutation.disconnect(); resize.unobserve(document.documentElement); resize.disconnect();
        return { before, during, after: snapshot().totals };
      },
      dispose() { restores.reverse().forEach(restore => restore()); intervals.clear(); active.clear(); },
    };
  });
}
export async function resourceSnapshot(page: Page) {
  return page.evaluate(() => {
    if (!(window as any).__lqPilotResources) throw Error('Resource probe was not installed before application initialization');
    return (window as any).__lqPilotResources.snapshot();
  });
}
export async function settledResources(page: Page) {
  // Same closed/disclosure state and settling at baseline and after each phase.
  // Core close promises have finished when hidden assertions pass; two frames
  // flush Mutation/Resize deliveries. The existing networkidle tail also spans
  // next-task work and preferences' 240ms debounce without replacing timers.
  await page.waitForLoadState('networkidle'); await painted(page);
  return resourceSnapshot(page);
}
export function noResourceGrowth(before: any, after: any, label: string) {
  for (const [kind, count] of Object.entries(after.totals)) expect(count, `${label}: active ${kind}`).toBeLessThanOrEqual(before.totals[kind]);
  for (const [owner, count] of Object.entries(after.owners)) expect(count, `${label}: active owner ${owner}`).toBeLessThanOrEqual(before.owners[owner] || 0);
  for (const [owner, count] of Object.entries(after.targetOwners)) expect(count, `${label}: observed targets ${owner}`).toBeLessThanOrEqual(before.targetOwners[owner] || 0);
}
export async function listeners(cdp: CDPSession) {
  // Reuse the existing classroom spec's CDP getEventListeners technique, also
  // checking window and the actual authored shell/disclosure nodes.
  const result = await cdp.send('Runtime.evaluate', {
    expression: `JSON.stringify([window,document,...document.querySelectorAll('[data-lq-shell],[data-lq-pane],[data-lq-nav-group],[data-ui-preferences-details]')].map((node,index)=>({index,count:Object.values(getEventListeners(node)).reduce((sum,list)=>sum+list.length,0)})))`,
    includeCommandLineAPI: true, returnByValue: true,
  });
  if (result.exceptionDetails || !result.result.value) throw Error('Listener measurement unavailable');
  return JSON.parse(result.result.value) as { index: number; count: number }[];
}
export async function finishTrace(cdp: CDPSession, info: TestInfo, name: string) {
  const completed = new Promise<{ stream?: string }>(resolve => cdp.once('Tracing.tracingComplete', resolve));
  await cdp.send('Tracing.end');
  const { stream } = await completed;
  if (!stream) throw Error('CDP trace did not return a stream');
  const chunks: Buffer[] = [];
  try {
    for (;;) {
      const chunk = await cdp.send('IO.read', { handle: stream });
      chunks.push(Buffer.from(chunk.data, chunk.base64Encoded ? 'base64' : 'utf8'));
      if (chunk.eof) break;
    }
  } finally { await cdp.send('IO.close', { handle: stream }); }
  await info.attach(name, { body: Buffer.concat(chunks), contentType: 'application/json' });
}
export function timingSummary(raw: any) {
  const interactions = new Map<number, number>();
  for (const event of raw.events) if (event.interactionId > 0) interactions.set(event.interactionId, Math.max(interactions.get(event.interactionId) || 0, event.duration));
  const samples = [...interactions.values()].sort((a, b) => a - b);
  const percentile = (fraction: number) => samples.length ? samples[Math.ceil(samples.length * fraction) - 1] : null;
  return { ...raw, interactionSamples: samples, p50: percentile(.5), p95: percentile(.95),
    observation: 'Event Timing durationThreshold=16ms; null percentiles mean all captured trusted interactions were below the reporting floor, not unsupported or zero latency. These are laboratory samples, not field INP.',
    longTasks: raw.tasks.filter((task: any) => task.duration > 50) };
}

