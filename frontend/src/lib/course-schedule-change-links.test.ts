import { describe, expect, it } from 'vitest';
import { scheduleChangeConnections } from '../../../static/js/course_schedule_change_links.js';

function pair(sourceWeek = 4, targetWeek = 4) {
  const change = {
    request_id: 'request-1', phase: 'pending', kind: 'move', endpoint: 'original',
    counterpart_event_key: 'target', counterpart_week_index: targetWeek,
    original: { date: '2026-09-25', sections: [2, 3], room: 'B310' },
    proposed: { date: targetWeek === 4 ? '2026-09-24' : '2026-10-09', sections: [6, 7], room: 'B310' },
  };
  const original = { event_key: 'source', course_name: '计算机网络原理', session_id: 18, class_offering_id: 4, adjustment: change };
  const proposed = { ...original, event_key: 'target', adjustment: { ...structuredClone(change), endpoint: 'proposed', counterpart_event_key: 'source', counterpart_week_index: sourceWeek } };
  const weeks = sourceWeek === targetWeek
    ? [{ week_index: sourceWeek, lessons: [original, proposed] }]
    : [{ week_index: sourceWeek, lessons: [original] }, { week_index: targetWeek, lessons: [proposed] }];
  return { overview: { weeks }, original, proposed, source: weeks[0], target: weeks.at(-1)! };
}

describe('explicit academic change connections', () => {
  it('directs a same-week change from original to proposed even when moving to an earlier day', () => {
    const data = pair();
    data.source.lessons.reverse();
    expect(scheduleChangeConnections(data.overview, data.source)).toEqual([{
      key: '["request-1","source"]', sourceKey: 'source', targetKey: 'target', direction: 'local', edge: null,
      label: '时间更改', boundaryLabel: '', title: '2026-09-25 第2、3节 · B310 → 2026-09-24 第6、7节 · B310',
      jumpKey: 'target', jumpWeek: 4, courseName: '计算机网络原理',
    }]);
  });

  it.each([[4, 6, 'right', 'left'], [6, 4, 'left', 'right']] as const)('continues a %s → %s week move through the correct boundary', (from, to, outEdge, inEdge) => {
    const data = pair(from, to);
    const outgoing = scheduleChangeConnections(data.overview, data.source)[0];
    const incoming = scheduleChangeConnections(data.overview, data.target)[0];
    expect(outgoing).toMatchObject({ sourceKey: 'source', targetKey: null, direction: 'outgoing', edge: outEdge, boundaryLabel: `至第${to}周`, jumpKey: 'target', jumpWeek: to });
    expect(incoming).toMatchObject({ sourceKey: null, targetKey: 'target', direction: 'incoming', edge: inEdge, boundaryLabel: `来自第${from}周`, jumpKey: 'source', jumpWeek: from });
    expect(incoming.key).toBe(outgoing.key);
    expect(incoming.title).toBe(outgoing.title);
  });

  it('only labels a room change when both supplied rooms differ', () => {
    const data = pair();
    data.original.adjustment.proposed.room = data.proposed.adjustment.proposed.room = 'B210';
    expect(scheduleChangeConnections(data.overview, data.source)[0].label).toBe('时间更改 · 教室更改');
    data.original.adjustment.original.room = data.proposed.adjustment.original.room = '';
    expect(scheduleChangeConnections(data.overview, data.source)[0].label).toBe('时间更改');
  });

  it('retains a room-only request on one card with no fictitious time displacement', () => {
    const data = pair();
    Object.assign(data.original.adjustment, { kind: 'room', counterpart_event_key: null, counterpart_week_index: null, proposed: { ...data.original.adjustment.original, room: 'B210' } });
    data.source.lessons = [data.original];
    expect(scheduleChangeConnections(data.overview, data.source)).toEqual([expect.objectContaining({
      sourceKey: 'source', targetKey: 'source', direction: 'room', edge: null, label: '教室更改',
      title: '2026-09-25 第2、3节 · B310 → 2026-09-25 第2、3节 · B210',
    })]);
  });

  it('ignores cancellations, approved requests, and malformed room-only requests', () => {
    const data = pair();
    data.original.adjustment.kind = 'cancel';
    expect(scheduleChangeConnections(data.overview, data.source)).toEqual([]);
    data.original.adjustment.kind = 'move'; data.original.adjustment.phase = 'approved';
    expect(scheduleChangeConnections(data.overview, data.source)).toEqual([]);
    data.original.adjustment.phase = 'pending'; data.original.adjustment.kind = 'room';
    Object.assign(data.original.adjustment, { counterpart_event_key: null });
    expect(scheduleChangeConnections(data.overview, data.source)).toEqual([]);
  });

  it('does not turn filtered or missing endpoints into page boundaries', () => {
    const data = pair();
    expect(scheduleChangeConnections(data.overview, { ...data.source, lessons: [data.original] })).toEqual([]);
    const cross = pair(4, 6);
    cross.overview.weeks[1].lessons = [];
    expect(scheduleChangeConnections(cross.overview, cross.source)).toEqual([]);
  });

  it.each(['request', 'reverse-key', 'endpoint', 'week', 'session', 'offering', 'slot', 'self-loop'])('rejects mismatched %s instead of guessing a pair', problem => {
    const data = pair();
    if (problem === 'request') data.proposed.adjustment.request_id = 'other-request';
    if (problem === 'reverse-key') data.proposed.adjustment.counterpart_event_key = 'different-source';
    if (problem === 'endpoint') data.proposed.adjustment.endpoint = 'original';
    if (problem === 'week') data.proposed.adjustment.counterpart_week_index = 7;
    if (problem === 'session') data.proposed.session_id = 99;
    if (problem === 'offering') data.proposed.class_offering_id = 99;
    if (problem === 'slot') data.proposed.adjustment.proposed.date = '2026-09-26';
    if (problem === 'self-loop') data.original.adjustment.counterpart_event_key = 'source';
    expect(scheduleChangeConnections(data.overview, data.source)).toEqual([]);
  });

  it('requires a known request and unique event identities but permits explicit unbound pairs', () => {
    const data = pair();
    Object.assign(data.original, { session_id: null, class_offering_id: null });
    Object.assign(data.proposed, { session_id: null, class_offering_id: null });
    expect(scheduleChangeConnections(data.overview, data.source)).toHaveLength(1);
    data.source.lessons.push({ ...data.original });
    expect(scheduleChangeConnections(data.overview, data.source)).toEqual([]);
    data.source.lessons.pop();
    data.original.adjustment.request_id = data.proposed.adjustment.request_id = '';
    expect(scheduleChangeConnections(data.overview, data.source)).toEqual([]);
  });

  it('keeps separate details under one request and never pairs by course or session alone', () => {
    const data = pair();
    const second = pair();
    second.original.event_key = 'source-2'; second.proposed.event_key = 'target-2';
    second.original.adjustment.counterpart_event_key = 'target-2'; second.proposed.adjustment.counterpart_event_key = 'source-2';
    data.source.lessons.push(second.original, second.proposed);
    const connections = scheduleChangeConnections(data.overview, data.source);
    expect(connections).toHaveLength(2);
    expect(new Set(connections.map(row => row.key)).size).toBe(2);
  });

  it('never labels absent or invalid time evidence as a time change', () => {
    const data = pair();
    data.original.adjustment.proposed.date = data.proposed.adjustment.proposed.date = '2026-02-30';
    expect(scheduleChangeConnections(data.overview, data.source)).toEqual([]);
    data.original.adjustment.proposed = structuredClone(data.original.adjustment.original);
    data.proposed.adjustment.proposed = structuredClone(data.original.adjustment.original);
    expect(scheduleChangeConnections(data.overview, data.source)).toEqual([]);
  });
});
