import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import vm from 'node:vm';
import { afterEach, describe, expect, it, vi } from 'vitest';

type TimeState = { is_accepting_submissions: boolean; deadline_phase: string; countdown_at: string };

describe('legacy assignment clock boundary contract', () => {
  afterEach(() => vi.useRealTimers());
  it('transitions regular to supplement to closed without waiting for the network poll', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-09-05T04:00:00Z'));
    const labels = new Map<string, { textContent: string }>();
    const clock = {
      dataset: { assignmentId: '7', serverNow: '2026-09-05 12:00:00', countdownAt: '2026-09-05 12:00:01',
        lateUntil: '2026-09-05 12:00:02', deadlinePhase: 'regular', accepting: '1', lateOpen: '0', latePolicyLabel: '补交封顶 80 分' },
      classList: { toggle: () => undefined },
      querySelector: (selector: string) => { const node = { textContent: '' }; labels.set(selector, node); return node; },
    };
    const context = vm.createContext({ Date, Map, console,
      window: { setInterval, clearInterval, setTimeout, clearTimeout },
      document: { querySelectorAll: () => [clock] },
    });
    const source = readFileSync(resolve('static/js/assignment_time.js'), 'utf8').replaceAll('export function ', 'function ');
    vm.runInContext(source, context);
    let states = new Map<string, TimeState>();
    context.initAssignmentClocks({ onStateChange: (value: Map<string, TimeState>) => { states = value; } });
    expect(states.get('7')?.deadline_phase).toBe('regular');
    vi.advanceTimersByTime(1000);
    expect(states.get('7')?.deadline_phase).toBe('late');
    expect(states.get('7')?.is_accepting_submissions).toBe(true);
    expect(states.get('7')?.countdown_at).toBe('2026-09-05T04:00:02.000Z');
    vi.advanceTimersByTime(1000);
    expect(states.get('7')?.deadline_phase).toBe('closed');
    expect(states.get('7')?.is_accepting_submissions).toBe(false);
    expect(labels.get('[data-assignment-clock-value]')?.textContent).toBe('已截止');
  });
  it('shows the personal resubmission deadline instead of the later classroom cutoff', () => {
    vi.useFakeTimers(); vi.setSystemTime(new Date('2026-09-05T04:00:00Z'));
    const labels = new Map<string, { textContent: string }>();
    const clock = {
      dataset: { assignmentId: '8', serverNow: '2026-09-05 12:00:00', countdownAt: '2026-09-05 14:00:00',
        deadlinePhase: 'regular', accepting: '1', personalResubmission: '1', canResubmit: '1', resubmissionDueAt: '2026-09-05 12:00:01' },
      classList: { toggle: () => undefined },
      querySelector: (selector: string) => { const node = { textContent: '' }; labels.set(selector, node); return node; },
    };
    const context = vm.createContext({ Date, Map, console, window: { setInterval, clearInterval, setTimeout, clearTimeout }, document: { querySelectorAll: () => [clock] } });
    vm.runInContext(readFileSync(resolve('static/js/assignment_time.js'), 'utf8').replaceAll('export function ', 'function '), context);
    let states = new Map<string, TimeState>();
    context.initAssignmentClocks({ onStateChange: (value: Map<string, TimeState>) => { states = value; } });
    expect(labels.get('[data-assignment-clock-label]')?.textContent).toBe('重交截止');
    expect(labels.get('[data-assignment-clock-value]')?.textContent).toContain('12:00');
    vi.advanceTimersByTime(1000);
    expect(labels.get('[data-assignment-clock-label]')?.textContent).toBe('重交已关闭');
    expect(states.get('8')?.is_accepting_submissions).toBe(true);
  });
  it('refreshes authoritative permission at the start boundary without guessing local permission', async () => {
    vi.useFakeTimers(); vi.setSystemTime(new Date('2026-09-05T04:00:00Z'));
    const clock = { dataset: { assignmentId: '9', serverNow: '2026-09-05 12:00:00', startsAt: '2026-09-05 12:00:01', countdownAt: '2026-09-05 14:00:00', deadlinePhase: 'regular', accepting: '0' },
      classList: { toggle: () => undefined }, querySelector: () => ({ textContent: '' }) };
    const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ server_now: '2026-09-05 12:00:01', assignments: [{ assignment_id: '9', countdown_at: '2026-09-05 14:00:00', deadline_phase: 'regular', is_accepting_submissions: true }] }) });
    const context = vm.createContext({ Date, Map, console, fetch, window: { setInterval, clearInterval, setTimeout, clearTimeout }, document: { querySelectorAll: () => [clock] } });
    vm.runInContext(readFileSync(resolve('static/js/assignment_time.js'), 'utf8').replaceAll('export function ', 'function '), context);
    let states = new Map<string, TimeState>();
    context.initAssignmentClocks({ onStateChange: (value: Map<string, TimeState>) => { states = value; } });
    expect(states.get('9')?.is_accepting_submissions).toBe(false);
    await vi.advanceTimersByTimeAsync(1001);
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(states.get('9')?.is_accepting_submissions).toBe(true);
  });
});
