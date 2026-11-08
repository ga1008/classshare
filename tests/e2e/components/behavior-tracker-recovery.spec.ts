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
