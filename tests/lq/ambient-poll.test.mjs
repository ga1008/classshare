import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { createAmbientPoll } from '../../static/js/ambient_poll.js';

const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };

beforeEach(() => {
    vi.useFakeTimers();
    vi.spyOn(Math, 'random').mockReturnValue(0.5);
    vi.stubGlobal('window', { setTimeout, clearTimeout });
});
afterEach(() => { vi.clearAllTimers(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it('LQ ambient badges share a pending request and schedule from completion', async () => {
    const slow = deferred();
    const task = vi.fn(() => slow.promise);
    const poll = createAmbientPoll(task, { intervalMs: 15000, active: () => true });
    const first = poll.refresh();
    expect(poll.refresh()).toBe(first);
    await vi.advanceTimersByTimeAsync(5000);
    expect(task).toHaveBeenCalledOnce();
    slow.resolve(); await first;
    expect(poll.inFlight).toBe(false);
    await vi.advanceTimersByTimeAsync(14999);
    expect(task).toHaveBeenCalledOnce();
    await vi.advanceTimersByTimeAsync(1);
    expect(task).toHaveBeenCalledTimes(2);
    poll.dispose();
});

it('LQ polling timeout settles even a noncooperative transport and delays another GET', async () => {
    let signal;
    const task = vi.fn(value => { signal = value; return new Promise(() => {}); });
    const poll = createAmbientPoll(task, { intervalMs: 15000, timeoutMs: 8000, active: () => true });
    const pending = poll.refresh();
    await vi.advanceTimersByTimeAsync(8000);
    await pending;
    expect(signal.aborted).toBe(true);
    expect(poll.inFlight).toBe(false);
    await Promise.all(Array.from({ length: 50 }, () => poll.refresh()));
    expect(task).toHaveBeenCalledOnce();
    await vi.advanceTimersByTimeAsync(29999);
    expect(task).toHaveBeenCalledOnce();
    await vi.advanceTimersByTimeAsync(1);
    expect(task).toHaveBeenCalledTimes(2);
    poll.dispose();
});

it('LQ transient failures back off and respect Retry-After without an event storm', async () => {
    const task = vi.fn().mockRejectedValue(Object.assign(new Error('busy'), { retryAfterSeconds: 90 }));
    const poll = createAmbientPoll(task, { intervalMs: 15000, active: () => true });
    await poll.refresh();
    await vi.advanceTimersByTimeAsync(89999);
    expect(task).toHaveBeenCalledOnce();
    await poll.refresh();
    expect(task).toHaveBeenCalledOnce();
    await vi.advanceTimersByTimeAsync(1);
    expect(task).toHaveBeenCalledTimes(2);
    task.mockResolvedValue(undefined);
    await vi.advanceTimersByTimeAsync(90000);
    expect(task).toHaveBeenCalledTimes(3);
    await vi.advanceTimersByTimeAsync(15000);
    expect(task).toHaveBeenCalledTimes(4);
    poll.dispose();
});

it('LQ hidden owners skip work and an explicit visible refresh resumes once', async () => {
    let visible = false;
    const task = vi.fn().mockResolvedValue(undefined);
    const poll = createAmbientPoll(task, { intervalMs: 15000, active: () => visible });
    poll.start();
    await vi.advanceTimersByTimeAsync(60000);
    expect(task).not.toHaveBeenCalled();
    visible = true;
    await Promise.all([poll.refresh(), poll.refresh()]);
    expect(task).toHaveBeenCalledOnce();
    poll.dispose();
});

it('LQ disposing an owner aborts pending work and removes both timers', async () => {
    const slow = deferred();
    let signal;
    const task = vi.fn(value => { signal = value; return slow.promise; });
    const poll = createAmbientPoll(task, { intervalMs: 15000, active: () => true });
    const pending = poll.refresh(); await Promise.resolve();
    poll.dispose(); await pending;
    expect(signal.aborted).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
    slow.resolve(); await Promise.resolve();
    await poll.refresh();
    expect(task).toHaveBeenCalledOnce();
    expect(vi.getTimerCount()).toBe(0);
});

it('LQ successful polling intervals vary across clients', async () => {
    const task = vi.fn().mockResolvedValue(undefined);
    Math.random.mockReturnValue(0);
    const poll = createAmbientPoll(task, { intervalMs: 15000, active: () => true });
    await poll.refresh();
    await vi.advanceTimersByTimeAsync(13499);
    expect(task).toHaveBeenCalledOnce();
    await vi.advanceTimersByTimeAsync(1);
    expect(task).toHaveBeenCalledTimes(2);
    poll.dispose();
});
