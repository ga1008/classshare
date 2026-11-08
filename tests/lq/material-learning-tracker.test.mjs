import { describe, it, expect, vi, afterEach } from 'vitest';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const source = readFileSync('static/js/behavior_tracker.js', 'utf8');
const adapter = readFileSync('static/js/material_learning_tracker.js', 'utf8').replace('export function', 'function');

function runtime() {
    vi.useFakeTimers();
    const epoch = Date.UTC(2026, 10, 8, 0, 0, 0);
    vi.setSystemTime(epoch);
    let now = 0, nonce = 0;
    const values = new Map();
    const fetch = vi.fn();
    const navigator = { sendBeacon: vi.fn(() => true) };
    const window = { performance: { timeOrigin: epoch, now: () => now },
        crypto: { randomUUID: () => `nonce-${++nonce}` },
        sessionStorage: { getItem: key => values.get(key) || null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) },
        setTimeout, clearTimeout, setInterval, clearInterval, addEventListener() {}, removeEventListener() {}, dispatchEvent: vi.fn() };
    const context = vm.createContext({ window, document: { visibilityState: 'visible', hasFocus: () => true, addEventListener() {}, removeEventListener() {} },
        navigator, fetch, Date, Math, Blob, AbortController,
        CustomEvent: class { constructor(type, options) { this.type = type; this.detail = options.detail; } }, console: { warn() {} } });
    vm.runInContext(source + '\n' + adapter + '\nwindow.startMaterialLearningTracker = startMaterialLearningTracker;', context);
    const create = options => window.startMaterialLearningTracker({ context: { userRole: 'student', userId: 10, classOfferingId: 1, materialId: 30, sessionId: 7 },
        pageKey: 'material_viewer', ...options });
    return { window, values, fetch, navigator, create,
        advance(ms) { now += ms; vi.setSystemTime(epoch + now); } };
}

function response(body, extra = {}) {
    return { ok: true, status: 200, headers: { get: () => null }, json: async () => ({ status: 'success', write_mode: 'durable_queued',
        batch_id: body.batch_id, current_user_key: body.user_key, ...extra }) };
}

afterEach(() => vi.useRealTimers());

describe('material measured telemetry adapter', () => {
    it('reuses immutable retry and only updates mastery after a durable receipt', async () => {
        const f = runtime(), onReceipt = vi.fn();
        const tracker = f.create({ getProgress: () => ({ scroll_ratio: 0.9, completed: true }), onReceipt });
        f.fetch.mockRejectedValueOnce(new Error('lost acknowledgement'));
        await tracker.flush();
        const first = JSON.parse(f.fetch.mock.calls[0][1].body);
        expect(f.fetch.mock.calls[0][0]).toBe('/api/classrooms/1/learning/material-progress');
        expect(first).toMatchObject({ progress_version: 2, material_id: 30, session_id: 7, user_key: 'student:10', completed: true });
        expect(first.events.some(event => event.payload?.presence_version === 2)).toBe(true);
        expect(onReceipt).not.toHaveBeenCalled();
        f.advance(10000); tracker.retryAt = 0;
        f.fetch.mockImplementationOnce(async (_url, options) => response(JSON.parse(options.body), { mastery_check: { completed: true } }));
        await tracker.flush();
        expect(JSON.parse(f.fetch.mock.calls[1][1].body)).toEqual(first);
        expect(onReceipt).toHaveBeenCalledTimes(1);
        expect(tracker.pendingBatch).toBeNull();
    });

    it('isolates queues per material without consuming the classroom page queue', async () => {
        const f = runtime();
        const classroomKey = 'lanshare:behavior:v1:student:10:1';
        f.values.set(classroomKey, 'unrelated classroom evidence');
        const one = f.create();
        const two = f.create({ context: { userRole: 'student', userId: 10, classOfferingId: 1, materialId: 31 } });
        f.fetch.mockImplementation(async (_url, options) => response(JSON.parse(options.body)));
        await one.flush(); await two.flush();
        expect(one.storageKey).toBe(classroomKey + ':material_30');
        expect(two.storageKey).toBe(classroomKey + ':material_31');
        expect(f.values.get(classroomKey)).toBe('unrelated classroom evidence');
        expect(JSON.parse(f.fetch.mock.calls[1][1].body).material_id).toBe(31);
    });

    it('retains measured intervals on final beacons and never sends cumulative wall-clock counters', async () => {
        const f = runtime(), tracker = f.create();
        f.fetch.mockImplementation(async (_url, options) => response(JSON.parse(options.body)));
        await tracker.flush();
        f.advance(60000);
        tracker.stop();
        expect(f.navigator.sendBeacon).toHaveBeenCalled();
        const body = JSON.parse(await f.navigator.sendBeacon.mock.calls[0][1].text());
        expect(body.duration_seconds).toBeUndefined();
        expect(body.active_seconds).toBeUndefined();
        expect(body.events.some(event => event.payload?.interval_end_ms - event.payload?.interval_start_ms === 60000)).toBe(true);
    });

    it('does not lose the measured batch if optional scroll sampling fails', async () => {
        const f = runtime(), tracker = f.create({ getProgress: () => { throw new Error('removed document'); } });
        f.fetch.mockImplementation(async (_url, options) => response(JSON.parse(options.body)));
        await tracker.flush();
        expect(f.fetch).toHaveBeenCalledTimes(1);
        expect(tracker.pendingBatch).toBeNull();
        expect(tracker.diagnostics.some(item => item.error_code === 'client_material_progress_unavailable')).toBe(true);
    });
});
