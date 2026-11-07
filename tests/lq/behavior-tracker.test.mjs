import { describe, it, expect, vi, afterEach } from 'vitest';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const source = readFileSync('static/js/behavior_tracker.js', 'utf8');

function runtime() {
    vi.useFakeTimers();
    const epoch = Date.UTC(2026, 10, 7, 0, 0, 0, 125);
    vi.setSystemTime(epoch);
    let monotonicMs = 0;
    let nonce = 0;
    const values = new Map();
    const storage = { getItem: key => values.get(key) || null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) };
    const fetch = vi.fn();
    const navigator = { sendBeacon: vi.fn(() => true) };
    const listeners = new Map();
    const window = { sessionStorage: storage, crypto: { randomUUID: () => `synthetic-nonce-${++nonce}` },
        performance: { timeOrigin: epoch, now: () => monotonicMs },
        setTimeout, clearTimeout, setInterval, clearInterval,
        addEventListener: (name, fn) => listeners.set(name, fn), removeEventListener: name => listeners.delete(name),
        dispatchEvent: vi.fn() };
    const document = { visibilityState: 'visible', hasFocus: () => true, addEventListener() {}, removeEventListener() {} };
    const context = vm.createContext({ window, document, navigator, fetch, Date, Math, Blob, AbortController,
        CustomEvent: class CustomEvent { constructor(type, init) { this.type = type; this.detail = init.detail; } },
        console: { warn: vi.fn() } });
    vm.runInContext(source, context);
    const create = (userKey = 'student:10') => new window.BehaviorTracker({ classOfferingId: 1, userKey, pageKey: 'test' });
    const advance = (ms) => { monotonicMs += ms; vi.setSystemTime(Date.now() + ms); };
    return { create, fetch, navigator, values, listeners, window, document, epoch, advance };
}

function receipt(body, extra = {}) {
    return { ok: true, status: 202, headers: { get: () => null },
        json: async () => ({ status: 'success', write_mode: 'durable_queued', batch_id: body.batch_id, ...extra }) };
}

afterEach(() => vi.useRealTimers());

describe('durable behavior transport', () => {
    it('keeps one fetch and immutable batch ID through lost response and beacon retry', async () => {
        const f = runtime(); const tracker = f.create();
        tracker.logClick('one'); let reject;
        f.fetch.mockImplementationOnce(() => new Promise((_, fail) => { reject = fail; }));
        const first = tracker.flush();
        const body = JSON.parse(f.fetch.mock.calls[0][1].body);
        tracker.logClick('two');
        await tracker.flush();
        expect(f.fetch).toHaveBeenCalledTimes(1);
        await tracker.flush({ sync: true });
        expect(f.navigator.sendBeacon).toHaveBeenCalledTimes(1);
        expect(tracker.pendingBatch.body.batch_id).toBe(body.batch_id);
        reject(new Error('synthetic lost response')); await first;
        expect(tracker.pendingBatch.body.batch_id).toBe(body.batch_id);
        expect(tracker.retryAt - Date.now()).toBeGreaterThanOrEqual(2000);
        f.fetch.mockImplementationOnce((_, options) => Promise.resolve(receipt(JSON.parse(options.body))));
        tracker.retryAt = 0; await tracker.flush();
        expect(tracker.pendingBatch).toBeNull();
        expect(tracker.events).toHaveLength(1);
        expect(JSON.parse(f.fetch.mock.calls[1][1].body)).toEqual(body);
    });

    it('restores unacknowledged work only within the same identity and classroom', async () => {
        const f = runtime(); const tracker = f.create();
        tracker.logClick('offline');
        f.navigator.sendBeacon.mockReturnValueOnce(false);
        f.fetch.mockResolvedValueOnce({ ok: true });
        await tracker.flush({ sync: true });
        expect(f.create().pendingBatch.body.batch_id).toBe(tracker.pendingBatch.body.batch_id);
        expect(f.create('student:11').pendingBatch).toBeNull();
        expect(f.create('teacher:10').events).toHaveLength(0);
        expect(tracker.pendingBatch.body.user_key).toBe('student:10');
        expect(f.values.size).toBe(1); // sendBeacon(false) retains recovery state.
        expect(f.fetch.mock.calls[0][1].keepalive).toBe(true);
    });

    it('honors Retry-After and never treats a degraded 200 as durable receipt', async () => {
        const f = runtime(); const tracker = f.create(); tracker.started = true;
        tracker.logClick('one');
        f.fetch.mockResolvedValueOnce({ ok: false, status: 429, headers: { get: () => '30' } });
        await tracker.flush();
        expect(tracker.retryAt - Date.now()).toBeGreaterThanOrEqual(30000);
        expect(tracker.pendingBatch).not.toBeNull();
        tracker.retryAt = 0;
        f.fetch.mockImplementationOnce((_, options) => Promise.resolve(receipt(JSON.parse(options.body), { degraded: true })));
        await tracker.flush();
        expect(tracker.pendingBatch).not.toBeNull();
        expect(tracker.retryAttempt).toBe(2);
        tracker.started = false;
    });

    it('pauses foreign-account or forbidden receipts with work retained', async () => {
        const f = runtime(); const tracker = f.create(); tracker.logClick('one');
        f.fetch.mockResolvedValueOnce({ ok: false, status: 409, headers: { get: () => null } });
        await tracker.flush(); await tracker.flush();
        expect(tracker.paused).toBe(true);
        expect(f.fetch).toHaveBeenCalledTimes(1);
        expect(tracker.pendingBatch.body.user_key).toBe('student:10');
        expect(f.values.size).toBe(1);
    });

    it('closes the focused interval at blur and sends an unmeasured status at the boundary', () => {
        const f = runtime(); const tracker = f.create(); tracker.start();
        f.advance(14375);
        // A wall clock correction must not affect a measured span.
        vi.setSystemTime(Date.now() + 600000);
        f.listeners.get('blur')();
        expect(tracker.events.map(event => event.action_type)).toEqual([
            'page_enter', 'presence_heartbeat', 'presence_heartbeat', 'presence_heartbeat', 'page_focus',
        ]);
        const [, initial, measured, blurred] = tracker.events;
        expect(initial.payload).toMatchObject({ presence_version: 2, presence_observed_ms: f.epoch });
        expect(initial.payload).not.toHaveProperty('interval_start_ms');
        expect(measured.payload).toMatchObject({ interval_start_ms: f.epoch, interval_end_ms: f.epoch + 14375,
            focused: true, visibility_state: 'visible', idle_seconds: 15 });
        expect(blurred.payload).toMatchObject({ focused: false, presence_observed_ms: f.epoch + 14375 });
        expect(blurred.payload).not.toHaveProperty('interval_end_ms');
    });

    it('preserves a final foreground interval despite a pending batch and restarts after bfcache', async () => {
        const f = runtime(); const tracker = f.create(); tracker.start();
        f.fetch.mockImplementationOnce(() => new Promise(() => {}));
        void tracker.flush();
        const older = tracker.pendingBatch.body.batch_id;
        f.advance(16000);
        f.document.visibilityState = 'hidden';
        tracker.handleVisibilityChange();
        f.listeners.get('pagehide')({ persisted: true });
        expect(tracker.started).toBe(false);
        expect(tracker.pendingBatch.body.batch_id).toBe(older);
        expect(f.navigator.sendBeacon).toHaveBeenCalledTimes(2);
        const sent = JSON.parse(await f.navigator.sendBeacon.mock.calls[1][1].text());
        expect(sent.batch_id).not.toBe(older);
        expect(sent.events).toHaveLength(1);
        expect(sent.events[0].payload).toMatchObject({ interval_start_ms: f.epoch, interval_end_ms: f.epoch + 16000,
            focused: true, visibility_state: 'visible' });
        expect(tracker.events).toContainEqual(sent.events[0]);
        f.advance(180000);
        f.document.visibilityState = 'visible';
        f.listeners.get('pageshow')({ persisted: true });
        expect(tracker.started).toBe(true);
        expect(tracker.events.at(-1).payload).not.toHaveProperty('interval_start_ms');
        tracker.stop();
    });

    it('closes a past idle span before resuming and discards unobserved timer gaps', () => {
        const f = runtime(); const tracker = f.create(); tracker.start();
        f.advance(90000);
        tracker.samplePresence(f.epoch + 90000);
        f.advance(80000);
        tracker.handleActivity();
        expect(tracker.events.at(-1).payload).toMatchObject({ interval_start_ms: f.epoch + 90000,
            interval_end_ms: f.epoch + 170000, idle_seconds: 170 });
        f.advance(120000);
        tracker.samplePresence(f.epoch + 290000);
        expect(tracker.events.at(-1).payload).not.toHaveProperty('interval_start_ms');
        f.advance(15000);
        tracker.samplePresence(f.epoch + 305000);
        expect(tracker.events.at(-1).payload).toMatchObject({ interval_start_ms: f.epoch + 290000,
            interval_end_ms: f.epoch + 305000, idle_seconds: 135 });
    });
});
