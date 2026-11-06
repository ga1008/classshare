import { describe, it, expect, vi, afterEach } from 'vitest';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const source = readFileSync('static/js/behavior_tracker.js', 'utf8');

function runtime() {
    vi.useFakeTimers();
    const values = new Map();
    const storage = { getItem: key => values.get(key) || null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) };
    const fetch = vi.fn();
    const navigator = { sendBeacon: vi.fn(() => false) };
    const listeners = new Map();
    const window = { sessionStorage: storage, crypto: { randomUUID: () => 'synthetic-stable-nonce' },
        setTimeout, clearTimeout, setInterval, clearInterval,
        addEventListener: (name, fn) => listeners.set(name, fn), removeEventListener: name => listeners.delete(name),
        dispatchEvent: vi.fn() };
    const document = { visibilityState: 'visible', hasFocus: () => true, addEventListener() {}, removeEventListener() {} };
    const context = vm.createContext({ window, document, navigator, fetch, Date, Math, Blob, AbortController,
        CustomEvent: class CustomEvent { constructor(type, init) { this.type = type; this.detail = init.detail; } },
        console: { warn: vi.fn() } });
    vm.runInContext(source, context);
    const create = (userKey = 'student:10') => new window.BehaviorTracker({ classOfferingId: 1, userKey, pageKey: 'test' });
    return { create, fetch, navigator, values, listeners, window };
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
        await tracker.flush({ sync: true });
        expect(f.create().pendingBatch.body.batch_id).toBe(tracker.pendingBatch.body.batch_id);
        expect(f.create('student:11').pendingBatch).toBeNull();
        expect(f.create('teacher:10').events).toHaveLength(0);
        expect(tracker.pendingBatch.body.user_key).toBe('student:10');
        expect(f.values.size).toBe(1); // sendBeacon(false) retains recovery state.
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

    it('keeps heartbeat and focus events ordered and restores after bfcache', async () => {
        const f = runtime(); const tracker = f.create(); tracker.start();
        tracker.enqueueEvent(tracker.buildHeartbeatEvent());
        tracker.handleBlur(); tracker.enqueueEvent(tracker.buildHeartbeatEvent());
        const types = tracker.events.map(event => event.action_type);
        expect(types).toEqual(['page_enter', 'presence_heartbeat', 'page_focus', 'presence_heartbeat']);
        tracker.handlePageHide();
        expect(tracker.started).toBe(false);
        expect(tracker.pendingBatch).not.toBeNull();
        tracker.handlePageShow({ persisted: true });
        expect(tracker.started).toBe(true);
        tracker.stop();
    });
});
