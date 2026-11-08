import { test, expect } from '@playwright/test';
import fs from 'node:fs';

const tracker = fs.readFileSync('static/js/behavior_tracker.js', 'utf8');
const adapter = fs.readFileSync('static/js/material_learning_tracker.js', 'utf8');
const origin = 'https://material-recorder.test';

for (const viewport of [{ width: 1440, height: 980 }, { width: 390, height: 844 }]) {
  test(`material telemetry preserves its pending receipt and auth gap at ${viewport.width}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await page.clock.install({ time: new Date('2026-11-08T00:00:00Z') });
    let authenticated = false;
    const batches: any[] = [];
    await page.route(`${origin}/**`, async route => {
      const path = new URL(route.request().url()).pathname;
      if (path === '/adapter.js') return route.fulfill({ contentType: 'text/javascript', body: adapter });
      if (path.endsWith('/material-progress')) {
        const body = route.request().postDataJSON(); batches.push(body);
        return route.fulfill({ status: authenticated ? 200 : 401, json: authenticated
          ? { status: 'success', write_mode: 'durable_queued', batch_id: body.batch_id,
              current_user_key: 'student:10', completed: true, mastery_check: { completed: true } }
          : { detail: { error_code: 'auth_required', batch_disposition: 'retain', retryable: true } } });
      }
      if (path.endsWith('/diagnostics')) {
        const body = route.request().postDataJSON();
        return route.fulfill({ json: { status: 'success', current_user_key: 'student:10',
          accepted_diagnostic_ids: body.diagnostics.map((item: any) => item.diagnostic_id) } });
      }
      return route.fulfill({ contentType: 'text/html', body: `<html><body><p>材料阅读</p>
        <script>${tracker}</script><script type="module">
        import {startMaterialLearningTracker} from '/adapter.js';
        window.receipts=[];
        window.reader=startMaterialLearningTracker({context:{userId:10,userRole:'student',classOfferingId:1,materialId:30,sessionId:7},
          pageKey:'material_viewer',getProgress:()=>({scroll_ratio:.9,completed:true}),onReceipt:result=>window.receipts.push(result)});
        </script></body></html>` });
    });
    await page.goto(origin);
    await page.waitForFunction(() => Boolean((window as any).reader));
    await page.evaluate(() => (window as any).reader.flush());
    const original = batches[0];
    expect(original).toMatchObject({ material_id: 30, progress_version: 2, session_id: 7, scroll_ratio: .9 });
    expect(await page.evaluate(() => (window as any).reader.transportState)).toBe('auth_required');
    await page.clock.runFor(120000);
    await page.reload();
    await page.waitForFunction(() => Boolean((window as any).reader));
    expect(await page.evaluate(() => (window as any).reader.pendingBatch.body)).toEqual(original);
    expect(await page.evaluate(() => (window as any).receipts.length)).toBe(0);
    const resumed = await page.evaluate(() => Math.floor(performance.timeOrigin + performance.now()));
    authenticated = true;
    await page.evaluate(async () => { const r = (window as any).reader; r.retryAt = r.retryAfterAt = 0; await r.flush(); });
    expect(batches.at(-1)).toEqual(original);
    expect(await page.evaluate(() => (window as any).receipts.length)).toBe(1);
    await page.clock.runFor(60000);
    await page.evaluate(async () => { const r = (window as any).reader; r.flushHeartbeat(); await r.flush(); });
    const intervals = batches.flatMap(body => body === original ? [] : body.events)
      .map((event: any) => event.payload).filter((payload: any) => payload?.interval_start_ms !== undefined);
    expect(intervals.length).toBeGreaterThan(0);
    expect(intervals.every((payload: any) => payload.interval_start_ms >= resumed)).toBe(true);
    expect(batches.every(body => body.duration_seconds === undefined && body.active_seconds === undefined)).toBe(true);
    expect(await page.evaluate(() => (window as any).reader.storageKey)).toContain(':material_30');
  });
}
