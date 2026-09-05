import { describe, expect, it } from 'vitest';
import { createClassroomReadiness } from './classroom-bootstrap-ready';

describe('classroom controller readiness', () => {
  it('retains an early action until the same initialization finishes', async () => {
    const gate = createClassroomReadiness();
    let calls = 0;
    const action = gate.wait().then(() => { calls += 1; });
    await Promise.resolve();
    expect(calls).toBe(0);
    gate.complete();
    await action;
    expect(calls).toBe(1);
    await gate.wait();
    expect(calls).toBe(1);
  });

  it('surfaces initialization failure for a queued or later action', async () => {
    const gate = createClassroomReadiness();
    const failure = new Error('module unavailable');
    const pending = gate.wait();
    gate.complete(failure);
    await expect(pending).rejects.toBe(failure);
    await expect(gate.wait()).rejects.toBe(failure);
  });
});
