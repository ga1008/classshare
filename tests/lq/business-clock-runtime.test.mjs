import { describe, it, expect, vi, afterEach } from 'vitest';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const source = readFileSync('static/js/assignment_time.js', 'utf8').replaceAll('export function ', 'function ');
function runtime() {
    vi.useFakeTimers(); vi.setSystemTime(new Date('2026-09-20T04:00:00Z'));
    const makeClock = (overrides = {}) => {
        const nodes = new Map(), classes = new Set();
        return { dataset: { assignmentId: '7', serverNow: '2026-09-20 12:00:00', countdownAt: '2026-09-20 13:00:00',
            deadlinePhase: 'regular', accepting: '1', ...overrides }, nodes, classes,
            classList: { toggle(name, on) { if (on) classes.add(name); else classes.delete(name); } },
            querySelector(selector) { if (!nodes.has(selector)) nodes.set(selector, { textContent: '' }); return nodes.get(selector); } };
    };
    const clock = makeClock(), clocks = [clock];
    const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ assignments: [] }) });
    const warnings = vi.fn();
    const document = { querySelectorAll: () => clocks };
    const globals = { Date, Map, console: { warn: warnings }, fetch, document, window: { setInterval, clearInterval, setTimeout, clearTimeout } };
    const context = vm.createContext(globals); vm.runInContext(source, context);
    return { context, globals, clock, clocks, makeClock, fetch, warnings };
}
afterEach(() => vi.useRealTimers());

describe('assignment clock presentation ownership', () => {
    it('subscriptions are passive, frozen, independent leases and cannot suppress other listeners', () => {
        const f = runtime(); const values = [], broken = () => { throw new Error('presentation failure'); };
        const one = f.context.subscribeAssignmentClock(f.clock, broken);
        const listener = value => values.push(value);
        const two = f.context.subscribeAssignmentClock(f.clock, listener);
        const three = f.context.subscribeAssignmentClock(f.clock, listener);
        expect(vi.getTimerCount()).toBe(0); expect(f.fetch).not.toHaveBeenCalled();
        const business = vi.fn(); const owner = f.context.initAssignmentClocks({ onStateChange: business });
        expect(values).toHaveLength(2); expect(Object.isFrozen(values[0])).toBe(true);
        expect(values[0]).toMatchObject({ phase: 'regular', urgent: true, remainingSeconds: 3600, deadlineAt: '2026-09-20T05:00:00.000Z' });
        expect(business).toHaveBeenCalledTimes(2); expect(f.warnings).toHaveBeenCalledTimes(1);
        two.dispose(); vi.advanceTimersByTime(1000); expect(values).toHaveLength(3);
        three.dispose(); one.dispose(); owner.dispose(); expect(vi.getTimerCount()).toBe(0);
    });
    it('late network and JSON completions cannot write into a newer owner or clear its in-flight guard', async () => {
        const f = runtime(); let releaseJson, releaseSecond, entered;
        const jsonEntered = new Promise(resolve => { entered = resolve; });
        f.fetch.mockReset().mockResolvedValueOnce({ ok: true, json: () => new Promise(resolve => { releaseJson = resolve; entered(); }) })
            .mockImplementationOnce(() => new Promise(resolve => { releaseSecond = resolve; }));
        const first = f.context.initAssignmentClocks(); const pendingFirst = first.syncNow(); await jsonEntered;
        const nextClock = f.makeClock({ assignmentId: '7', accepting: '0' }); f.clocks.splice(0, 1, nextClock);
        const second = f.context.initAssignmentClocks(); const pendingSecond = second.syncNow();
        first.dispose(); expect(first.refresh()).toBe(false); expect(first.getStates().size).toBe(0);
        releaseJson({ assignments: [{ assignment_id: 7, is_accepting_submissions: true, countdown_at: '2030-01-01', deadline_phase: 'regular' }] });
        await pendingFirst;
        expect(nextClock.dataset.accepting).toBe('0'); await second.syncNow(); expect(f.fetch).toHaveBeenCalledTimes(2);
        releaseSecond({ ok: true, json: async () => ({ server_now: '2026-09-20 12:00:00', assignments: [{ assignment_id: 7,
            countdown_at: '2026-09-20 14:00:00', deadline_phase: 'regular', is_accepting_submissions: true }] }) });
        await pendingSecond; expect(nextClock.dataset.accepting).toBe('1'); second.dispose(); expect(vi.getTimerCount()).toBe(0);
    });
    it('refresh discovers new nodes without resetting existing server offsets or starting a second interval', () => {
        const f = runtime(); const owner = f.context.initAssignmentClocks();
        vi.advanceTimersByTime(2000);
        const second = f.makeClock({ assignmentId: '8', serverNow: '2026-09-20 12:00:02' }); f.clocks.push(second);
        const firstValues = [], secondValues = [];
        const one = f.context.subscribeAssignmentClock(f.clock, value => firstValues.push(value));
        const two = f.context.subscribeAssignmentClock(second, value => secondValues.push(value));
        owner.refresh();
        expect(firstValues.at(-1).remainingSeconds).toBe(3598); expect(secondValues.at(-1).remainingSeconds).toBe(3598);
        expect(vi.getTimerCount()).toBe(2); expect(f.fetch).not.toHaveBeenCalled();
        one.dispose(); two.dispose(); owner.dispose();
    });
    it('refresh invalidates requests for replaced nodes without letting their finally release a newer request', async () => {
        const f = runtime(); const releases = [];
        f.fetch.mockImplementation(() => new Promise(resolve => releases.push(resolve)));
        const owner = f.context.initAssignmentClocks(); const pending = owner.syncNow();
        const replacement = f.makeClock({ accepting: '0' }); f.clocks.splice(0, 1, replacement); owner.refresh();
        const fresh = owner.syncNow();
        releases[0]({ ok: true, json: async () => ({ assignments: [{ assignment_id: 7, is_accepting_submissions: true }] }) });
        await pending; await owner.syncNow();
        expect(replacement.dataset.accepting).toBe('0'); expect(f.fetch).toHaveBeenCalledTimes(2);
        releases[1]({ ok: true, json: async () => ({ assignments: [] }) }); await fresh; owner.dispose();
        expect(vi.getTimerCount()).toBe(0);
    });
    it('refresh preserves an already queued authoritative start-boundary request for the current owner', async () => {
        const f = runtime(); f.clock.dataset.startsAt = '2026-09-20 12:00:01'; f.clock.dataset.accepting = '0';
        const owner = f.context.initAssignmentClocks();
        vi.advanceTimersByTime(1000); owner.refresh();
        expect(f.fetch).not.toHaveBeenCalled();
        await vi.advanceTimersByTimeAsync(1);
        expect(f.fetch).toHaveBeenCalledTimes(1); expect(f.clock.dataset.accepting).toBe('0');
        owner.dispose(); expect(vi.getTimerCount()).toBe(0);
    });
    it('same Document module aliases share one owner; twenty cycles release all timers and old disposers do not stop the new owner', () => {
        const f = runtime();
        const alias = vm.createContext({ ...f.globals }); vm.runInContext(source, alias);
        for (let index = 0; index < 20; index++) {
            const first = f.context.initAssignmentClocks(); const second = alias.initAssignmentClocks(); first.dispose();
            expect(vi.getTimerCount()).toBe(2); expect(second.getStates().size).toBe(1);
            second.dispose(); second.dispose(); expect(vi.getTimerCount()).toBe(0);
        }
    });
    it('disposal cancels a queued start-boundary refresh and no late response reschedules polling', async () => {
        const f = runtime(); f.clock.dataset.startsAt = '2026-09-20 12:00:01';
        const owner = f.context.initAssignmentClocks();
        vi.advanceTimersByTime(1000); owner.dispose(); await vi.advanceTimersByTimeAsync(1);
        expect(f.fetch).not.toHaveBeenCalled(); expect(vi.getTimerCount()).toBe(0);
        let release; f.fetch.mockImplementation(() => new Promise(resolve => { release = resolve; }));
        const next = f.context.initAssignmentClocks(); const pending = next.syncNow(); next.dispose();
        release({ ok: true, json: async () => ({ assignments: [] }) }); await pending;
        expect(vi.getTimerCount()).toBe(0);
    });
    it('does not swallow the original business callback failure on init or network refresh', async () => {
        const f = runtime(); const error = new Error('business failure');
        expect(() => f.context.initAssignmentClocks({ onStateChange() { throw error; } })).toThrow(error);
        let failing = false;
        const owner = f.context.initAssignmentClocks({ onStateChange() { if (failing) throw error; } });
        const listener = vi.fn(); const lease = f.context.subscribeAssignmentClock(f.clock, listener); failing = true;
        await expect(owner.syncNow()).rejects.toThrow(error); expect(listener).toHaveBeenCalledTimes(2);
        expect(f.warnings).not.toHaveBeenCalled(); lease.dispose(); owner.dispose();
    });
});
