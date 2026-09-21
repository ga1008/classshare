import { describe, expect, it, vi } from 'vitest';
import { createClassroomReadiness } from './classroom-bootstrap-ready';

describe('shared classroom bootstrap lifetime', () => {
  it('shares a pending initializer and keeps queued actions blocked until it finishes', async () => {
    const gate = createClassroomReadiness();
    let finish!: () => void;
    const initialize = vi.fn(() => new Promise<void>(resolve => { finish = resolve; }));
    const action = vi.fn();
    const queued = gate.wait().then(action);
    const first = gate.start(initialize);
    const repeatedEffect = gate.start(initialize);
    expect(repeatedEffect).toBe(first);
    await Promise.resolve();
    expect(initialize).toHaveBeenCalledTimes(1);
    expect(action).not.toHaveBeenCalled();
    finish();
    await first;
    await queued;
    expect(action).toHaveBeenCalledTimes(1);
    expect(gate.start(initialize)).toBe(first);
    expect(initialize).toHaveBeenCalledTimes(1);
  });

  it('retains a partial initialization failure for remounts and late waiters', async () => {
    const gate = createClassroomReadiness();
    const failure = new Error('secondary module download failed');
    let fail!: (error: unknown) => void;
    const initialize = vi.fn(() => new Promise<void>((_resolve, reject) => { fail = reject; }));
    const first = gate.start(initialize);
    const queued = gate.wait();
    await Promise.resolve();
    fail(failure);
    await expect(first).rejects.toBe(failure);
    await expect(queued).rejects.toBe(failure);
    expect(gate.start(initialize)).toBe(first);
    await expect(gate.wait()).rejects.toBe(failure);
    expect(initialize).toHaveBeenCalledTimes(1);
  });

  it.each([undefined, null, false, 0, ''])('does not turn a falsy thrown value (%s) into ready', async failure => {
    const gate = createClassroomReadiness();
    await expect(gate.start(() => { throw failure; })).rejects.toBe(failure);
    await expect(gate.wait()).rejects.toBe(failure);
  });
});
