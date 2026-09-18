import { describe, expect, it, vi } from 'vitest';
import { countScheduleLessons, pendingScheduleChange, scheduleLessonLanes, scheduleChangeLabel } from '../../../static/js/course_schedule_deck.js';
// @ts-expect-error Native module shared by both teacher entry points.
import { normalizeAcademicSyncTerm, syncAcademicSchedule } from '../../../static/js/academic_schedule_sync.js';

describe('pending academic schedule projections', () => {
  const original = { weekday: 4, sections: [4, 5], hours: 2, adjustment: { phase: 'pending', kind: 'move', endpoint: 'original' } };
  const proposed = { ...original, counts_towards_total: false, adjustment: { ...original.adjustment, endpoint: 'proposed' } };
  it('counts each official lesson once while keeping the pending destination visible', () => {
    expect(countScheduleLessons([original, proposed])).toEqual({ lesson_count: 1, total_hours: 2, proposed_count: 1 });
    expect(countScheduleLessons([original, { ...proposed, counts_towards_total: undefined }]).total_hours).toBe(2);
    expect(scheduleChangeLabel(proposed)).toBe('正在申请变更');
    expect(pendingScheduleChange({ ...original, adjustment: { ...original.adjustment, phase: 'approved' } })).toBeNull();
    expect(scheduleChangeLabel({ ...original, adjustment: { ...original.adjustment, kind: 'room' } })).toBe('更换教室待审');
  });
  it('separates transitive overlapping periods and reuses full width after the cluster', () => {
    const lanes = scheduleLessonLanes([{ weekday: 2, sections: [2, 3] }, { weekday: 2, sections: [3, 4] }, { weekday: 2, sections: [4, 5] }, { weekday: 2, sections: [8, 9] }, { weekday: 3, sections: [2, 3] }]);
    expect([...lanes.entries()]).toEqual([[0, { lane: 0, count: 2 }], [1, { lane: 1, count: 2 }], [2, { lane: 0, count: 2 }], [3, { lane: 0, count: 1 }], [4, { lane: 0, count: 1 }]]);
  });
});

describe('academic sync boundaries', () => {
  it('supports explicit discovery and validates historical semester identity', () => {
    expect(normalizeAcademicSyncTerm({})).toEqual({ year: '', term: '' });
    expect(normalizeAcademicSyncTerm({ year: '2025-2026', term: 2 })).toEqual({ year: '2025-2026', term: '2' });
    for (const input of [{ year: '2025-2027', term: '1' }, { year: '2025-2026', term: '4' }, { year: '', term: '1' }]) expect(() => normalizeAcademicSyncTerm(input)).toThrow();
  });
  it('preserves the explicit summer semester as term 3', () => {
    expect(normalizeAcademicSyncTerm({ year: '2025-2026', term: 3 })).toEqual({ year: '2025-2026', term: '3' });
    expect(normalizeAcademicSyncTerm({ year: '2026-2027', term: '3' })).toEqual({ year: '2026-2027', term: '3' });
  });
  it('joins simultaneous same-term requests and releases the key after completion', async () => {
    let resolve!: (value: unknown) => void;
    const fetcher = vi.fn((_url: string, _options: { body: string }) => new Promise(done => { resolve = done; }));
    const first = syncAcademicSchedule({ year: '2026-2027', term: '1' }, fetcher);
    const second = syncAcademicSchedule({ year: '2026-2027', term: '1' }, fetcher);
    expect(first).toBe(second); expect(fetcher).toHaveBeenCalledTimes(1);
    resolve({ ok: true, json: async () => ({ status: 'success', overview: { weeks: [] } }) });
    await first;
    const nextFetcher = vi.fn(async () => ({ ok: true, json: async () => ({ status: 'success', overview: { weeks: [] } }) }));
    await syncAcademicSchedule({ year: '2026-2027', term: '1' }, nextFetcher);
    expect(nextFetcher).toHaveBeenCalledTimes(1);
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({ year: '2026-2027', term: '1' });
  });
  it.each(['busy', 'missing_credential', 'failed', 'invalid_semester'])('does not accept %s as a replacement snapshot', async status => {
    const fetcher = vi.fn(async () => ({ ok: true, json: async () => ({ status, message: '保留有效课表', overview: { weeks: [] } }) }));
    await expect(syncAcademicSchedule({}, fetcher)).rejects.toThrow('保留有效课表');
  });
});
