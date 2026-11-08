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

function rejection(status, errorCode, disposition = 'retain', extra = {}) {
    return { ok: false, status, headers: { get: () => null }, json: async () => ({ detail: {
        error_code: errorCode, batch_disposition: disposition, retryable: disposition === 'retain', ...extra,
    } }) };
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

    it('retains an unknown 409 and permits a later retry instead of permanently pausing', async () => {
        const f = runtime(); const tracker = f.create(); tracker.logClick('one');
        f.fetch.mockResolvedValueOnce({ ok: false, status: 409, headers: { get: () => null } });
        await tracker.flush(); await tracker.flush();
        expect(tracker.paused).toBe(false);
        expect(f.fetch).toHaveBeenCalledTimes(1);
        expect(tracker.pendingBatch.body.user_key).toBe('student:10');
        expect(f.values.size).toBe(1);
        tracker.retryAt = 0;
        f.fetch.mockImplementationOnce((_, options) => Promise.resolve(receipt(JSON.parse(options.body))));
        await tracker.flush();
        expect(tracker.pendingBatch).toBeNull();
    });

    it('recovers the same immutable batch after a 401 and excludes the unauthenticated interval', async () => {
        const f = runtime(); const tracker = f.create(); tracker.start();
        f.fetch.mockResolvedValueOnce({ ok: false, status: 401, headers: { get: () => null } });
        await tracker.flush();
        const pending = JSON.stringify(tracker.pendingBatch.body);
        expect(tracker.transportState).toBe('auth_required');
        f.advance(45000);
        tracker.samplePresence(f.epoch + 45000);
        expect(tracker.events.filter(item => item.payload?.interval_start_ms !== undefined)).toHaveLength(0);
        tracker.retryAt = 0;
        f.fetch.mockImplementationOnce((_, options) => Promise.resolve(receipt(JSON.parse(options.body))));
        await tracker.flush();
        expect(JSON.stringify(JSON.parse(f.fetch.mock.calls[1][1].body))).toBe(pending);
        expect(tracker.transportState).toBe('healthy');
        f.advance(12000); tracker.samplePresence(f.epoch + 57000);
        expect(tracker.events.at(-1).payload).toMatchObject({ interval_start_ms: f.epoch + 45000,
            interval_end_ms: f.epoch + 57000 });
        tracker.stop();
    });

    it.each(['batch_expired', 'batch_payload_conflict', 'batch_timestamp_invalid', 'batch_payload_invalid'])
    ('quarantines %s once and sends only the independent legal queue afterward', async code => {
        const f = runtime(); const tracker = f.create(); tracker.logClick('old');
        const original = JSON.stringify(tracker.prepareBatch().body);
        tracker.logClick('new');
        f.fetch.mockResolvedValueOnce(rejection(409, code, 'quarantine'));
        await tracker.flush();
        expect(tracker.pendingBatch).toBeNull();
        expect(tracker.paused).toBe(false);
        expect(JSON.stringify(tracker.quarantinedBatches[0].body)).toBe(original);
        expect(tracker.diagnostics[0]).toMatchObject({ error_code: code, event_count: 1 });
        f.fetch.mockImplementationOnce((_, options) => Promise.resolve(receipt(JSON.parse(options.body))));
        await tracker.flush();
        const legal = JSON.parse(f.fetch.mock.calls[1][1].body);
        expect(legal.events.map(event => event.summary_text)).toEqual(['new']);
        expect(legal.batch_id).not.toBe(JSON.parse(original).batch_id);
        expect(f.create().quarantinedBatches[0].body).toEqual(JSON.parse(original));
    });

    it('locks the old document after identity change, isolates all work, and never beacons it', async () => {
        const f = runtime(); const tracker = f.create(); tracker.start();
        const original = JSON.stringify(tracker.prepareBatch().body);
        tracker.logClick('also old');
        f.fetch.mockResolvedValueOnce(rejection(409, 'identity_changed', 'retain', { current_user_key: 'student:11' }));
        await tracker.flush();
        expect(tracker.transportState).toBe('identity_changed');
        expect(tracker.events).toHaveLength(0);
        expect(tracker.quarantinedBatches).toHaveLength(2);
        expect(JSON.stringify(tracker.quarantinedBatches[0].body)).toBe(original);
        f.advance(20000); tracker.flushHeartbeat(true); tracker.handleOnline(); tracker.handleFocus();
        await tracker.flush();
        expect(f.fetch).toHaveBeenCalledTimes(1);
        expect(f.navigator.sendBeacon).not.toHaveBeenCalled();
        expect(f.create('student:11').diagnostics).toHaveLength(0);
        // A fresh server-rendered document for the old account can collect new work; it does not replay the archive.
        const sameAccount = f.create();
        expect(sameAccount.transportState).toBe('healthy');
        expect(sameAccount.pendingBatch).toBeNull();
        expect(sameAccount.quarantinedBatches).toHaveLength(2);
        tracker.stop();
    });

    it('sends diagnostics separately with stable IDs and removes them only after durable acknowledgement', async () => {
        const f = runtime(); const tracker = f.create();
        tracker.recordDiagnostic('client_observation_gap', { interval_start_ms: f.epoch, interval_end_ms: f.epoch + 120000 });
        tracker.persist();
        const original = structuredClone(tracker.diagnostics);
        f.fetch.mockRejectedValueOnce(new Error('lost diagnostic receipt'));
        await tracker.flush();
        expect(f.fetch.mock.calls[0][0]).toBe('/api/classrooms/1/behavior/diagnostics');
        expect(tracker.diagnostics[0]).toEqual(original[0]);
        expect(f.create().diagnostics[0]).toEqual(original[0]);
        tracker.retryAt = 0; tracker.diagnosticRetryAt = 0;
        f.fetch.mockImplementationOnce((_, options) => {
            const body = JSON.parse(options.body);
            return Promise.resolve({ ok: true, status: 200, headers: { get: () => null }, json: async () => ({
                status: 'success', current_user_key: 'student:10', accepted_diagnostic_ids: body.diagnostics.map(item => item.diagnostic_id),
            }) });
        });
        await tracker.flush();
        expect(tracker.diagnostics).toHaveLength(0);
        expect(tracker.transportState).toBe('healthy');
        expect(JSON.parse(f.fetch.mock.calls[1][1].body).diagnostics[0]).toEqual(original[0]);
    });

    it('does not let a diagnostic outage postpone an independent behavior batch', async () => {
        const f = runtime(); const tracker = f.create();
        tracker.recordDiagnostic('client_storage_unavailable');
        f.fetch.mockResolvedValueOnce({ ok: false, status: 503, headers: { get: () => '90' } });
        await tracker.flush();
        expect(tracker.diagnosticRetryAt - Date.now()).toBeGreaterThanOrEqual(90000);
        tracker.logClick('valid action');
        f.fetch.mockImplementationOnce((_, options) => Promise.resolve(receipt(JSON.parse(options.body))));
        await tracker.flush();
        expect(f.fetch.mock.calls[1][0]).toBe('/api/classrooms/1/behavior/batch');
        expect(tracker.pendingBatch).toBeNull();
        expect(tracker.diagnostics.length).toBeGreaterThan(0);
    });

    it('isolates rejected diagnostics unchanged and lets a current failure report continue', async () => {
        const f = runtime(); const tracker = f.create();
        tracker.recordDiagnostic('client_observation_gap', { interval_start_ms: 1, interval_end_ms: 1000 });
        const invalid = structuredClone(tracker.diagnostics[0]);
        f.fetch.mockResolvedValueOnce(rejection(422, 'diagnostic_interval_invalid', 'quarantine'));
        await tracker.flush();
        expect(tracker.quarantinedDiagnostics[0].diagnostics).toEqual([invalid]);
        expect(tracker.diagnostics).toHaveLength(1);
        expect(tracker.diagnostics[0].error_code).toBe('diagnostic_interval_invalid');
        expect(tracker.diagnostics[0]).not.toHaveProperty('interval_start_ms');
        expect(f.create().quarantinedDiagnostics[0].diagnostics).toEqual([invalid]);
    });

    it('keeps in-memory recovery when storage is unavailable without generating a diagnostic loop', async () => {
        const f = runtime(); const tracker = f.create();
        f.window.sessionStorage.setItem = () => { throw new Error('synthetic quota'); };
        tracker.logClick('valid action'); tracker.persist();
        expect(tracker.diagnostics).toHaveLength(1);
        f.fetch.mockImplementationOnce((_, options) => Promise.resolve(receipt(JSON.parse(options.body))));
        await tracker.flush();
        f.fetch.mockImplementationOnce((_, options) => {
            const body = JSON.parse(options.body);
            return Promise.resolve({ ok: true, status: 200, headers: { get: () => null }, json: async () => ({
                status: 'success', accepted_diagnostic_ids: body.diagnostics.map(item => item.diagnostic_id),
            }) });
        });
        await tracker.flush();
        expect(tracker.diagnostics).toHaveLength(0);
        expect(tracker.events).toHaveLength(0);
    });

    it('honors an HTTP-date Retry-After even when online and focus try to wake recovery', async () => {
        const f = runtime(); const tracker = f.create(); tracker.start();
        const retryDate = new Date(Date.now() + 120000).toUTCString();
        f.fetch.mockResolvedValueOnce({ ok: false, status: 503, headers: { get: () => retryDate } });
        await tracker.flush();
        tracker.handleOnline(); tracker.handleFocus(); await tracker.flush();
        expect(f.fetch).toHaveBeenCalledTimes(1);
        expect(tracker.retryAt).toBeGreaterThanOrEqual(Date.parse(retryDate));
        tracker.stop();
    });

    it('aborts a hung request and retries its exact original body', async () => {
        const f = runtime(); const tracker = f.create(); tracker.logClick('one');
        f.fetch.mockImplementationOnce((_, options) => new Promise((resolve, reject) => {
            options.signal.addEventListener('abort', () => reject(new Error('synthetic abort')));
        }));
        const task = tracker.flush();
        const body = f.fetch.mock.calls[0][1].body;
        await vi.advanceTimersByTimeAsync(12000); await task;
        expect(tracker.flushing).toBe(false);
        expect(tracker.diagnostics[0].error_code).toBe('client_request_timeout');
        tracker.retryAt = 0;
        f.fetch.mockImplementationOnce((_, options) => Promise.resolve(receipt(JSON.parse(options.body))));
        await tracker.flush();
        expect(f.fetch.mock.calls[1][1].body).toBe(body);
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
