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
    const create = (userKey = 'student:10', options = {}) => new window.BehaviorTracker({ classOfferingId: 1, userKey, pageKey: 'test', ...options });
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
        expect(tracker.diagnostics[0]).toMatchObject({ interval_start_ms: f.epoch, interval_end_ms: f.epoch });
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

describe('bounded behavior collection under classroom load', () => {
    function acknowledgeAll(f) {
        f.fetch.mockImplementation((url, options) => {
            const body = JSON.parse(options.body);
            return Promise.resolve(url.endsWith('/diagnostics')
                ? { ok: true, status: 200, headers: { get: () => null }, json: async () => ({
                    status: 'success', current_user_key: 'student:10',
                    accepted_diagnostic_ids: body.diagnostics.map(item => item.diagnostic_id),
                }) }
                : receipt(body, { current_user_key: 'student:10' }));
        });
    }

    it('bounds invalid options so configuration cannot create a zero-delay collection loop', () => {
        const f = runtime();
        const tiny = f.create('student:10', { heartbeatMs: 1, maxBatchSize: 0.1, requestTimeoutMs: 1, maxQueuedEvents: 1 });
        expect(tiny.heartbeatMs).toBe(15000);
        expect(tiny.maxBatchSize).toBe(1);
        expect(tiny.requestTimeoutMs).toBe(3000);
        expect(tiny.maxQueuedEvents).toBe(48);
        const huge = f.create('student:11', { heartbeatMs: 1e9, maxBatchSize: 1000, requestTimeoutMs: 1e9, maxQueuedEvents: 1e9 });
        expect([huge.heartbeatMs, huge.maxBatchSize, huge.requestTimeoutMs, huge.maxQueuedEvents]).toEqual([75000, 20, 30000, 512]);
        expect(f.create('student:12', { heartbeatMs: NaN }).heartbeatMs).toBe(60000);
    });

    it('coalesces ordinary storage writes but persists pending work synchronously before beacon or fetch', async () => {
        const f = runtime(); const tracker = f.create();
        const store = vi.spyOn(f.window.sessionStorage, 'setItem');
        for (let index = 0; index < 20; index += 1) tracker.logClick(`action ${index}`);
        expect(store).not.toHaveBeenCalled();
        await vi.advanceTimersByTimeAsync(100);
        expect(store).toHaveBeenCalledTimes(1);
        const pending = tracker.prepareBatch().body;
        expect(store).toHaveBeenCalledTimes(2);
        tracker.logClick('tail');
        await tracker.flush({ sync: true });
        const restored = f.create();
        expect(restored.pendingBatch.body).toEqual(pending);
        expect(restored.events).toHaveLength(5);
        expect(f.navigator.sendBeacon).toHaveBeenCalledTimes(1);
    });

    it('caps total queued bytes and excludes the capacity gap when acknowledgements free the buffer', async () => {
        const f = runtime(); const tracker = f.create(); tracker.start();
        const pending = tracker.prepareBatch().body;
        const immutable = JSON.stringify(pending);
        for (let index = 0; index < 100; index += 1) tracker.logClick(`large ${index}`, { text: '测'.repeat(3000) });
        expect(tracker.bufferBlocked).toBe(true);
        expect(tracker.events.length).toBeLessThan(100);
        expect(tracker.queuedBytes + tracker.pendingBytes).toBeLessThanOrEqual(256 * 1024);
        expect(JSON.stringify(tracker.pendingBatch.body)).toBe(immutable);
        f.advance(80000);
        tracker.samplePresence(f.epoch + 80000);
        expect(tracker.events.some(item => item.payload?.interval_start_ms !== undefined)).toBe(false);
        acknowledgeAll(f);
        for (let index = 0; index < 10 && tracker.bufferBlocked; index += 1) await tracker.flush();
        expect(tracker.bufferBlocked).toBe(false);
        expect(tracker.presenceAt).toBe(f.epoch + 80000);
        f.advance(10000); tracker.samplePresence(f.epoch + 90000);
        expect(tracker.events.at(-1).payload).toMatchObject({ interval_start_ms: f.epoch + 80000, interval_end_ms: f.epoch + 90000 });
        tracker.stop();
    });

    it('rejects one oversized or unserializable sample without throwing into the business action', () => {
        const f = runtime(); const tracker = f.create();
        tracker.logClick('huge', { text: 'x'.repeat(20000) });
        const circular = {}; circular.self = circular;
        expect(() => tracker.logClick('circular', circular)).not.toThrow();
        tracker.logClick('valid');
        expect(tracker.events.map(item => item.summary_text)).toEqual(['valid']);
        expect(tracker.diagnostics.map(item => item.error_code)).toEqual(['client_event_too_large', 'client_event_invalid']);
    });

    it('splits payload-heavy queues into bounded immutable batches without dropping events', async () => {
        const f = runtime(); const tracker = f.create(); acknowledgeAll(f);
        for (let index = 0; index < 6; index += 1) tracker.logClick(`item ${index}`, { text: 'x'.repeat(12000) });
        await tracker.flush(); await tracker.flush();
        const bodies = f.fetch.mock.calls.map(([, options]) => JSON.parse(options.body));
        expect(bodies.flatMap(body => body.events).map(item => item.summary_text)).toEqual(Array.from({ length: 6 }, (_, index) => `item ${index}`));
        expect(bodies.every(body => new Blob([JSON.stringify(body)]).size < 50 * 1024)).toBe(true);
    });

    it('gives diagnostics a turn after four behavior batches without delaying independent behavior on a diagnostic outage', async () => {
        const f = runtime(); const tracker = f.create(); acknowledgeAll(f);
        tracker.recordDiagnostic('client_observation_gap');
        for (let index = 0; index < 100; index += 1) tracker.logClick(`action ${index}`);
        for (let index = 0; index < 5; index += 1) await tracker.flush();
        expect(f.fetch.mock.calls.slice(0, 4).every(([url]) => url.endsWith('/batch'))).toBe(true);
        expect(f.fetch.mock.calls[4][0]).toMatch(/\/diagnostics$/);
        expect(tracker.diagnostics).toHaveLength(0);
        const prepared = JSON.stringify(tracker.pendingBatch.body);
        await tracker.flush();
        expect(f.fetch.mock.calls[5][1].body).toBe(prepared);
    });

    it('bounds active diagnostics and emits an immutable exact overflow delta after the original packet is acknowledged', async () => {
        const f = runtime(); const tracker = f.create();
        for (let index = 0; index < 32; index += 1) tracker.recordDiagnostic(`client_case_${index}`);
        let resolve;
        f.fetch.mockImplementationOnce(() => new Promise(done => { resolve = done; }));
        const task = tracker.flush();
        const wire = f.fetch.mock.calls[0][1].body;
        const sent = JSON.parse(wire);
        for (let index = 0; index < 100; index += 1) tracker.recordDiagnostic(`client_overflow_${index}`, { affected_batch_id: `b1_${Date.now()}_${index}` });
        expect(tracker.diagnostics).toHaveLength(32);
        expect(tracker.diagnosticOverflow).toMatchObject({ occurrence_count: 100, affected_batch_count: 100 });
        expect(f.fetch.mock.calls[0][1].body).toBe(wire);
        resolve({ ok: true, status: 200, headers: { get: () => null }, json: async () => ({ status: 'success',
            accepted_diagnostic_ids: sent.diagnostics.map(item => item.diagnostic_id) }) });
        await task;
        const delta = tracker.diagnostics.find(item => item.error_code === 'client_diagnostic_overflow');
        expect(delta).toMatchObject({ occurrence_count: 100, affected_batch_count: 100 });
        expect(tracker.diagnosticOverflow.occurrence_count).toBe(0);
        expect(f.create().diagnostics.find(item => item.diagnostic_id === delta.diagnostic_id)).toEqual(delta);
    });

    it('pauses at the raw archive cap, preserves the terminal pending packet, and resumes only after durable diagnostic evidence', async () => {
        const f = runtime(); const tracker = f.create('student:10', { maxBatchSize: 1 });
        for (let index = 0; index < 24; index += 1) tracker.logClick(`poison ${index}`);
        f.fetch.mockResolvedValue(rejection(409, 'batch_payload_invalid', 'quarantine'));
        for (let index = 0; index < 17; index += 1) await tracker.flush();
        expect(tracker.quarantinedBatches).toHaveLength(16);
        expect(tracker.archiveBlocked).toBe(true);
        expect(tracker.pendingBatch.terminal_error_code).toBe('batch_payload_invalid');
        const pending = JSON.stringify(tracker.pendingBatch.body);
        const queued = tracker.events.map(item => item.summary_text);
        tracker.logClick('must not invent capture while saturated');
        expect(tracker.events.map(item => item.summary_text)).toEqual(queued);
        expect(f.create().pendingBatch.body).toEqual(JSON.parse(pending));
        acknowledgeAll(f);
        await tracker.flush();
        expect(tracker.pendingBatch).toBeNull();
        expect(tracker.archiveBlocked).toBe(false);
        expect(tracker.quarantinedBatches.length).toBeLessThanOrEqual(16);
        expect(tracker.archiveSummaries.length).toBeGreaterThan(0);
        expect(tracker.events.map(item => item.summary_text)).toEqual(queued);
        expect(tracker.archiveSummaries.every(item => item.diagnostic_id && item.payload_fingerprint)).toBe(true);
    });

    it('does not let a successful behavior receipt release an unacknowledged full archive', async () => {
        const f = runtime(); const tracker = f.create('student:10', { maxBatchSize: 1 });
        for (let index = 0; index < 20; index += 1) tracker.logClick(`action ${index}`);
        f.fetch.mockResolvedValue(rejection(409, 'batch_payload_invalid', 'quarantine'));
        for (let index = 0; index < 16; index += 1) await tracker.flush();
        f.fetch.mockImplementationOnce((_, options) => Promise.resolve(receipt(JSON.parse(options.body))));
        await tracker.flush();
        expect(tracker.transportState).toBe('healthy');
        expect(tracker.archiveBlocked).toBe(true);
        expect(tracker.quarantinedBatches.every(item => !item.diagnostic_acknowledged)).toBe(true);
    });

    it('reissues valid archive-release reports from a rejected mixed diagnostic packet and avoids a permanent capacity lock', async () => {
        const f = runtime(); const tracker = f.create('student:10', { maxBatchSize: 1 });
        for (let index = 0; index < 16; index += 1) tracker.logClick(`poison ${index}`);
        f.fetch.mockResolvedValue(rejection(409, 'batch_payload_invalid', 'quarantine'));
        for (let index = 0; index < 16; index += 1) await tracker.flush();
        const oldIds = new Set(tracker.diagnostics.map(item => item.diagnostic_id));
        f.fetch.mockResolvedValueOnce(rejection(422, 'diagnostic_payload_invalid', 'quarantine'));
        await tracker.flush();
        expect(tracker.quarantinedDiagnostics).toHaveLength(1);
        expect(tracker.quarantinedDiagnostics[0].diagnostics.every(item => oldIds.has(item.diagnostic_id))).toBe(true);
        expect(tracker.quarantinedBatches.every(item => item.diagnostic_id && !oldIds.has(item.diagnostic_id))).toBe(true);
        acknowledgeAll(f); await tracker.flush();
        expect(tracker.archiveBlocked).toBe(false);
        expect(tracker.quarantinedBatches.every(item => item.diagnostic_acknowledged)).toBe(true);
    });

    it('bounds acknowledged archive summaries while retaining exact cumulative compaction counts', async () => {
        const f = runtime(); const tracker = f.create('student:10', { maxBatchSize: 1 });
        for (let index = 0; index < 120; index += 1) {
            tracker.logClick(`poison ${index}`);
            f.fetch.mockResolvedValueOnce(rejection(409, 'batch_payload_invalid', 'quarantine'));
            await tracker.flush();
            acknowledgeAll(f); await tracker.flush();
        }
        expect(tracker.quarantinedBatches.length).toBeLessThan(16);
        expect(tracker.archiveSummaries).toHaveLength(64);
        expect(tracker.archiveTotals.batches + tracker.quarantinedBatches.length).toBe(120);
        expect(tracker.archiveTotals.events).toBe(tracker.archiveTotals.batches);
        expect(tracker.archiveTotals.summaries_retired).toBe(tracker.archiveTotals.batches - 64);
        expect(tracker.diagnostics.length).toBeLessThanOrEqual(64);
        expect(f.values.get(tracker.storageKey).length).toBeLessThan(40 * 1024);
        expect(f.create().archiveTotals).toEqual(tracker.archiveTotals);
    });

    it('preserves a saturated identity-isolated queue and never replays its events from a fresh old-account document', async () => {
        const f = runtime(); const tracker = f.create();
        for (let index = 0; index < 480; index += 1) tracker.logClick(`old ${index}`);
        tracker.prepareBatch(); tracker.isolateIdentity();
        expect(tracker.transportState).toBe('identity_changed');
        expect(tracker.eventsQuarantined).toBe(true);
        expect(tracker.quarantinedBatches).toHaveLength(16);
        expect(tracker.events.length).toBeGreaterThan(0);
        expect(f.create('student:11').events).toHaveLength(0);
        const sameAccount = f.create();
        acknowledgeAll(f); await sameAccount.flush();
        expect(f.fetch.mock.calls.every(([url]) => url.endsWith('/diagnostics'))).toBe(true);
        expect(sameAccount.events).toHaveLength(0);
        expect(sameAccount.identityArchiveBatch).toBeNull();
        const retained = sameAccount.quarantinedBatches.reduce((total, item) => total + item.body.events.length, 0);
        expect(sameAccount.archiveTotals.events + retained).toBe(480);
        expect(sameAccount.quarantinedBatches.length).toBeLessThanOrEqual(16);
        expect(sameAccount.paused).toBe(false);
    });

    it('unblocks a bfcache document when its successor has durably reconciled and removed the shared normal buffer', async () => {
        const f = runtime(); const old = f.create(); old.start();
        for (let index = 0; index < 100; index += 1) old.logClick('large', { text: '测'.repeat(3000) });
        expect(old.bufferBlocked).toBe(true);
        old.stop({ resumeOnPageShow: true });
        const successor = f.create(); acknowledgeAll(f);
        for (let index = 0; index < 30 && (successor.events.length || successor.pendingBatch || successor.diagnostics.length); index += 1) await successor.flush();
        expect(f.values.has(old.storageKey)).toBe(false);
        f.advance(120000); old.handlePageShow({ persisted: true });
        expect(old.isCollectionSuspended()).toBe(false);
        expect(old.pendingBatch).toBeNull();
        f.advance(5000); old.samplePresence(f.epoch + 125000);
        expect(old.events.at(-1).payload).toMatchObject({ interval_start_ms: f.epoch + 120000, interval_end_ms: f.epoch + 125000 });
        old.stop();
    });

    it('retains an in-memory unacknowledged archive if storage unexpectedly disappears', async () => {
        const f = runtime(); const tracker = f.create(); tracker.logClick('old');
        f.fetch.mockResolvedValueOnce(rejection(409, 'batch_expired', 'quarantine')); await tracker.flush();
        const original = JSON.stringify(tracker.quarantinedBatches[0]);
        f.values.delete(tracker.storageKey); tracker.restore(); tracker.persist();
        expect(JSON.stringify(tracker.quarantinedBatches[0])).toBe(original);
        expect(tracker.diagnostics.some(item => item.error_code === 'client_recovery_unavailable')).toBe(true);
        expect(f.create().quarantinedBatches[0]).toEqual(JSON.parse(original));
    });

    it('can compact an oversized legacy pending packet only after its rejection diagnostic is durable', async () => {
        const f = runtime(); const initial = f.create();
        const body = initial.makeBatchBody([{ action_type: 'page_click', payload: { text: 'x'.repeat(200000) } }]);
        f.values.set(initial.storageKey, JSON.stringify({ userKey: initial.userKey, events: [], pendingBatch: { body } }));
        const tracker = f.create();
        f.fetch.mockResolvedValueOnce(rejection(413, 'batch_payload_too_large', 'quarantine'));
        await tracker.flush();
        expect(tracker.pendingBatch.body).toEqual(body);
        expect(tracker.archiveSummaries).toHaveLength(0);
        acknowledgeAll(f); await tracker.flush();
        expect(tracker.pendingBatch).toBeNull();
        expect(tracker.archiveTotals).toMatchObject({ batches: 1, events: 1 });
        expect(tracker.archiveSummaries[0].batch_id).toBe(body.batch_id);
        expect(f.values.get(tracker.storageKey).length).toBeLessThan(5000);
    });

    it('bounds diagnostic envelope details instead of retaining arbitrary payloads in the control channel', () => {
        const f = runtime(); const tracker = f.create();
        const item = tracker.recordDiagnostic('invalid:'.repeat(10000), {
            affected_batch_id: 'x'.repeat(100000), extra: 'private payload'.repeat(10000),
            event_count: 1e9, occurrence_count: 1e9, affected_batch_count: 1e9,
        });
        expect(item).toMatchObject({ error_code: 'client_diagnostic_code_invalid', event_count: 512,
            occurrence_count: 1000000, affected_batch_count: 1000000 });
        expect(item).not.toHaveProperty('extra');
        expect(item).not.toHaveProperty('affected_batch_id');
        expect(JSON.stringify(item).length).toBeLessThan(1000);
    });

    it('keeps healthy start/stop cycles free of residual timers and shares no namespace queues', async () => {
        const f = runtime(); const tracker = f.create();
        for (let index = 0; index < 100; index += 1) { tracker.start(); tracker.stop(); }
        expect(vi.getTimerCount()).toBe(0);
        expect(f.listeners.size).toBe(0);
        const material = f.create('student:10', { storageNamespace: 'material_1' });
        material.logClick('material'); material.prepareBatch();
        const other = f.create('student:10', { storageNamespace: 'material_2' });
        expect(other.pendingBatch).toBeNull();
        expect(f.create('student:10', { storageNamespace: 'material_1' }).pendingBatch.body).toEqual(material.pendingBatch.body);
        expect(f.create('student:10', { storageNamespace: 'bad/key' }).storageKey).toBeNull();
        expect(f.create('student:10', { storageNamespace: 'x'.repeat(65) }).diagnostics[0].error_code).toBe('client_namespace_invalid');
    });

    it('notifies a consumer only after a validated and persisted receipt and isolates consumer exceptions from retry', async () => {
        const f = runtime(); let tracker;
        const callback = vi.fn(() => {
            expect(tracker.pendingBatch).toBeNull();
            expect(f.values.has(tracker.storageKey)).toBe(false);
            throw new Error('consumer failed');
        });
        tracker = f.create('student:10', { onReceipt: callback });
        tracker.logClick('one');
        f.fetch.mockImplementationOnce((_, options) => Promise.resolve(receipt(JSON.parse(options.body))));
        await tracker.flush();
        expect(callback).toHaveBeenCalledTimes(1);
        expect(tracker.pendingBatch).toBeNull();
        expect(tracker.retryAttempt).toBe(0);
        expect(tracker.diagnostics[0].error_code).toBe('client_receipt_callback_failed');
        acknowledgeAll(f); await tracker.flush();
        expect(f.fetch.mock.calls[1][0]).toMatch(/\/diagnostics$/);
        expect(callback).toHaveBeenCalledTimes(1);
    });

    it('never notifies a consumer or releases its packet for a missing, foreign, or degraded receipt', async () => {
        const f = runtime(); const callback = vi.fn();
        const tracker = f.create('student:10', { onReceipt: callback });
        tracker.logClick('one');
        const body = tracker.prepareBatch().body;
        for (const extra of [{ batch_id: undefined }, { batch_id: 'b1_1794009600125_foreign' }, { degraded: true }]) {
            tracker.retryAt = 0;
            f.fetch.mockResolvedValueOnce(receipt(body, extra));
            await tracker.flush();
            expect(tracker.pendingBatch.body).toEqual(body);
        }
        expect(callback).not.toHaveBeenCalled();
        tracker.retryAt = 0;
        f.fetch.mockResolvedValueOnce(receipt(body)); await tracker.flush();
        expect(callback).toHaveBeenCalledTimes(1);
    });
});
