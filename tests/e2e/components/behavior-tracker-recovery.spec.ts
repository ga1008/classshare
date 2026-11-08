import { test, expect } from '@playwright/test';
import fs from 'node:fs';

const trackerSource = fs.readFileSync('static/js/behavior_tracker.js', 'utf8');
const origin = 'https://behavior-recovery.test';

test('behavior collection resumes the immutable batch across 401 and navigation without measuring the auth gap', async ({ page }) => {
  let authenticated = false;
  const batches: any[] = [], diagnostics: any[] = [];
  await page.clock.install({ time: new Date('2026-11-08T00:00:00Z') });
  await page.route(`${origin}/**`, async route => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname.endsWith('/behavior/batch')) {
      const body = route.request().postDataJSON(); batches.push(body);
      return route.fulfill({ status: authenticated ? 202 : 401, json: authenticated
        ? { status: 'success', write_mode: 'durable_queued', batch_id: body.batch_id, current_user_key: 'student:10' }
        : { detail: { error_code: 'auth_required', retryable: true, batch_disposition: 'retain' } } });
    }
    if (pathname.endsWith('/behavior/diagnostics')) {
      const body = route.request().postDataJSON(); diagnostics.push(...body.diagnostics);
      return route.fulfill({ json: { status: 'success', current_user_key: 'student:10',
        accepted_diagnostic_ids: body.diagnostics.map((item: any) => item.diagnostic_id) } });
    }
    return route.fulfill({ contentType: 'text/html', body: `<html><body><button>学习操作</button>
      <script>${trackerSource}</script><script>window.t = new BehaviorTracker({classOfferingId:1,userKey:'student:10',pageKey:'test'}).start();</script></body></html>` });
  });
  await page.goto(origin);
  await page.evaluate(() => (window as any).t.flush());
  expect(await page.evaluate(() => (window as any).t.transportState)).toBe('auth_required');
  const original = batches[0];
  await page.clock.runFor(5000);
  await page.reload();
  expect(await page.evaluate(() => (window as any).t.transportState)).toBe('auth_required');
  expect(await page.evaluate(() => (window as any).t.pendingBatch.body)).toEqual(original);
  expect(await page.evaluate(() => (window as any).t.events.some((item: any) => item.payload?.interval_start_ms !== undefined))).toBe(false);
  authenticated = true;
  await page.evaluate(() => { (window as any).t.retryAt = 0; return (window as any).t.flush(); });
  expect(batches.at(-1)).toEqual(original);
  expect(await page.evaluate(() => (window as any).t.transportState)).toBe('healthy');
  await page.clock.runFor(1000);
  const duration = await page.evaluate(() => {
    const t = (window as any).t;
    const event = t.samplePresence(Math.floor(performance.timeOrigin + performance.now()));
    return event.payload.interval_end_ms - event.payload.interval_start_ms;
  });
  expect(duration).toBeGreaterThanOrEqual(1000);
  expect(duration).toBeLessThan(2000);
  await page.evaluate(async () => {
    const t = (window as any).t;
    while (t.pendingBatch || t.events.length || t.diagnostics.length) await t.flush();
  });
  expect(diagnostics.map(item => item.error_code)).toEqual(expect.arrayContaining(['auth_required', 'collection_resumed']));
  expect(diagnostics.every(item => item.diagnostic_id.startsWith('d1_'))).toBe(true);
});

test('behavior collection isolates an expired batch, preserves newer events, and locks an old account document', async ({ page }) => {
  const batches: any[] = [];
  let mode = 'expired';
  await page.route(`${origin}/**`, async route => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname.endsWith('/behavior/batch')) {
      const body = route.request().postDataJSON(); batches.push(body);
      if (mode === 'expired') return route.fulfill({ status: 409, json: { detail: {
        error_code: 'batch_expired', retryable: false, batch_disposition: 'quarantine',
      } } });
      if (mode === 'switched') return route.fulfill({ status: 409, json: { detail: {
        error_code: 'identity_changed', current_user_key: 'student:11', retryable: false, batch_disposition: 'retain',
      } } });
      return route.fulfill({ status: 202, json: { status: 'success', write_mode: 'durable_queued', batch_id: body.batch_id, current_user_key: 'student:10' } });
    }
    return route.fulfill({ contentType: 'text/html', body: `<html><body><script>${trackerSource}</script>
      <script>window.t = new BehaviorTracker({classOfferingId:1,userKey:'student:10',pageKey:'test'});</script></body></html>` });
  });
  await page.goto(origin);
  await page.evaluate(async () => { const t = (window as any).t; t.logClick('old'); t.prepareBatch(); t.logClick('new'); await t.flush(); });
  expect(await page.evaluate(() => (window as any).t.quarantinedBatches[0].body)).toEqual(batches[0]);
  mode = 'healthy';
  await page.evaluate(() => (window as any).t.flush());
  expect(batches[1].events.map((event: any) => event.summary_text)).toEqual(['new']);
  expect(batches[1].batch_id).not.toBe(batches[0].batch_id);
  mode = 'switched';
  await page.evaluate(async () => { const t = (window as any).t; t.start(); await t.flush(); });
  expect(await page.evaluate(() => (window as any).t.transportState)).toBe('identity_changed');
  const requestCount = batches.length;
  await page.evaluate(async () => {
    const t = (window as any).t;
    t.logClick('cannot be attributed'); t.handleOnline(); t.flushHeartbeat(true); await t.flush();
    window.dispatchEvent(new PageTransitionEvent('pagehide', { persisted: true }));
    window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true }));
  });
  expect(await page.evaluate(() => (window as any).t.transportState)).toBe('identity_changed');
  expect(batches).toHaveLength(requestCount);
  expect(await page.evaluate(() => (window as any).t.events.length)).toBe(0);
});

test('bounded collection survives a full unacknowledged archive across reload and excludes the capacity gap', async ({ page }) => {
  let acceptDiagnostics = false;
  await page.clock.install({ time: new Date('2026-11-08T00:00:00Z') });
  await page.route(`${origin}/**`, async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/behavior/batch')) {
      return route.fulfill({ status: 409, json: { detail: { error_code: 'batch_payload_invalid', batch_disposition: 'quarantine' } } });
    }
    if (path.endsWith('/behavior/diagnostics')) {
      const body = route.request().postDataJSON();
      return route.fulfill(acceptDiagnostics
        ? { json: { status: 'success', current_user_key: 'student:10', accepted_diagnostic_ids: body.diagnostics.map((item: any) => item.diagnostic_id) } }
        : { status: 503, headers: { 'Retry-After': '60' }, json: { detail: 'temporary' } });
    }
    return route.fulfill({ contentType: 'text/html', body: `<html><body>合成采集容量页
      <script>${trackerSource}</script><script>window.t = new BehaviorTracker({classOfferingId:1,userKey:'student:10',pageKey:'test',maxBatchSize:1}).start();</script></body></html>` });
  });
  await page.goto(origin);
  const before = await page.evaluate(async () => {
    const t = (window as any).t;
    for (let index = 0; index < 24; index += 1) t.logClick(`poison ${index}`);
    for (let index = 0; index < 17; index += 1) await t.flush();
    return { pending: JSON.stringify(t.pendingBatch.body), queued: t.events.map((item: any) => item.summary_text), archive: t.quarantinedBatches.length };
  });
  expect(before.archive).toBe(16);
  await page.reload();
  await page.clock.runFor(30000);
  await expect.poll(() => page.evaluate(() => (window as any).t.flushing)).toBe(false);
  const saturated = await page.evaluate(() => {
    const t = (window as any).t; t.logClick('not captured while capacity is unavailable');
    return { blocked: t.archiveBlocked, pending: JSON.stringify(t.pendingBatch.body), queued: t.events.map((item: any) => item.summary_text) };
  });
  expect(saturated).toEqual({ blocked: true, pending: before.pending, queued: before.queued });
  acceptDiagnostics = true;
  const resumedAt = await page.evaluate(async () => {
    const t = (window as any).t; t.retryAt = t.diagnosticRetryAt = 0; await t.flush(); return t.presenceAt;
  });
  expect(await page.evaluate(() => (window as any).t.archiveBlocked)).toBe(false);
  expect(await page.evaluate(() => (window as any).t.pendingBatch)).toBeNull();
  expect(await page.evaluate(() => (window as any).t.archiveSummaries.length)).toBeGreaterThan(0);
  await page.clock.runFor(5000);
  const span = await page.evaluate(() => {
    const t = (window as any).t; t.samplePresence(Math.floor(performance.timeOrigin + performance.now()));
    return t.events.at(-1).payload;
  });
  expect(span.interval_start_ms).toBe(resumedAt);
  expect(span.interval_end_ms - span.interval_start_ms).toBeGreaterThanOrEqual(5000);
  expect(span.interval_end_ms - span.interval_start_ms).toBeLessThan(6000);
});

test('acknowledged archive compaction and one hundred lifecycle cycles stay bounded in Chrome', async ({ page }) => {
  let rejectBatch = true;
  await page.clock.install({ time: new Date('2026-11-08T00:00:00Z') });
  await page.route(`${origin}/**`, async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/behavior/batch')) {
      const body = route.request().postDataJSON();
      return route.fulfill(rejectBatch
        ? { status: 409, json: { detail: { error_code: 'batch_payload_invalid', batch_disposition: 'quarantine' } } }
        : { json: { status: 'success', write_mode: 'durable_queued', batch_id: body.batch_id, current_user_key: 'student:10' } });
    }
    if (path.endsWith('/behavior/diagnostics')) {
      const body = route.request().postDataJSON();
      return route.fulfill({ json: { status: 'success', current_user_key: 'student:10', accepted_diagnostic_ids: body.diagnostics.map((item: any) => item.diagnostic_id) } });
    }
    return route.fulfill({ contentType: 'text/html', body: `<html><body>合成归档页<script>
      window.trackerTimers=new Set(); const originalSetTimeout=window.setTimeout.bind(window),originalClearTimeout=window.clearTimeout.bind(window);
      window.setTimeout=(fn,ms,...args)=>{let id=originalSetTimeout(()=>{window.trackerTimers.delete(id);fn(...args);},ms);window.trackerTimers.add(id);return id;};
      window.clearTimeout=(id)=>{window.trackerTimers.delete(id);return originalClearTimeout(id);};
      </script><script>${trackerSource}</script><script>window.t = new BehaviorTracker({classOfferingId:1,userKey:'student:10',pageKey:'test',maxBatchSize:1});</script></body></html>` });
  });
  await page.goto(origin);
  const totals = await page.evaluate(async () => {
    const t = (window as any).t;
    for (let index = 0; index < 100; index += 1) { t.logClick(`poison ${index}`); await t.flush(); await t.flush(); }
    return { raw: t.quarantinedBatches.length, summaries: t.archiveSummaries.length,
      total: t.archiveTotals.batches, bytes: sessionStorage.getItem(t.storageKey)?.length };
  });
  expect(totals.raw).toBeLessThan(16);
  expect(totals.summaries).toBe(64);
  expect(totals.total + totals.raw).toBe(100);
  expect(totals.bytes).toBeLessThan(40 * 1024);
  rejectBatch = false;
  const remaining = await page.evaluate(() => {
    Object.defineProperty(navigator, 'sendBeacon', { configurable: true, value: () => true });
    const t = (window as any).t;
    for (let index = 0; index < 100; index += 1) { t.start(); t.stop(); }
    return { timers: (window as any).trackerTimers.size, heartbeat: t.heartbeatTimer, flush: t.flushTimer, storage: t.storageWriteTimer };
  });
  expect(remaining).toEqual({ timers: 0, heartbeat: null, flush: null, storage: null });
});
