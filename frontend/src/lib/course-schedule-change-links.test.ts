import { describe, expect, it } from 'vitest';
import { scheduleChangeConnections, scheduleChangeColors } from '../../../static/js/course_schedule_change_links.js';
import { adjustmentActionText } from '../../../static/js/course_schedule_presentation.js';

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

  it('does not draw an arrow for room-only changes that retain the same time', () => {
    const data = pair();
    Object.assign(data.original.adjustment, { kind: 'room', counterpart_event_key: null, counterpart_week_index: null, proposed: { ...data.original.adjustment.original, room: 'B210' } });
    data.source.lessons = [data.original];
    expect(scheduleChangeConnections(data.overview, data.source)).toEqual([]);
    expect(scheduleChangeColors(data.overview).size).toBe(0);
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

function manyPairs(count: number) {
  const overview = { weeks: [4, 6].map(week_index => ({ week_index, lessons: [] as ReturnType<typeof pair>['source']['lessons'] })) };
  for (let index = 0; index < count; index += 1) {
    const data = pair(4, 6), suffix = String(index).padStart(4, '0');
    data.original.event_key = `source-${suffix}`; data.proposed.event_key = `target-${suffix}`;
    data.original.adjustment.request_id = data.proposed.adjustment.request_id = `request-${suffix}`;
    data.original.adjustment.counterpart_event_key = data.proposed.event_key;
    data.proposed.adjustment.counterpart_event_key = data.original.event_key;
    overview.weeks[0].lessons.push(data.original); overview.weeks[1].lessons.push(data.proposed);
  }
  return overview;
}

describe('independent stable change-line colors', () => {
  it('gives every complete pair a distinct color and shares it between both pages', () => {
    const overview = manyPairs(12), colors = scheduleChangeColors(overview);
    expect(colors.size).toBe(12);
    expect(new Set(colors.values()).size).toBe(12);
    const source = scheduleChangeConnections(overview, overview.weeks[0]);
    const target = scheduleChangeConnections(overview, overview.weeks[1]);
    expect(source.map(row => colors.get(row.key))).toEqual(target.map(row => colors.get(row.key)));
    expect([...colors.values()].slice(0, 4)).toEqual(['#b91c1c', '#047857', '#a16207', '#a21caf']);
  });

  it('keeps line captions and action buttons consistent for room aliases and distinct buildings', () => {
    const data = pair();
    for (const item of [data.original, data.proposed]) {
      item.adjustment.original.room = '（大成楼C108）AI数智财经创新中心';
      item.adjustment.proposed.room = 'C108教室';
    }
    expect(scheduleChangeConnections(data.overview, data.source)[0].label).toBe('时间更改');
    expect(adjustmentActionText(data.original)).toBe('改时间');
    for (const item of [data.original, data.proposed]) item.adjustment.proposed.room = '知新楼C108教室';
    expect(scheduleChangeConnections(data.overview, data.source)[0].label).toBe('时间更改 · 教室更改');
    expect(adjustmentActionText(data.original)).toBe('教室+时间');
  });

  it('is deterministic when weeks and lessons arrive in a different order', () => {
    const overview = manyPairs(30), expected = scheduleChangeColors(overview);
    overview.weeks.reverse(); overview.weeks.forEach(week => week.lessons.reverse());
    expect(scheduleChangeColors(overview)).toEqual(expected);
  });

  it('retains assignments after filtering and reopening the complete semester', () => {
    const overview = manyPairs(25), cached = scheduleChangeColors(overview), snapshot = new Map(cached);
    const filtered = structuredClone(overview);
    filtered.weeks.forEach(week => { week.lessons = week.lessons.slice(6, 10); });
    const filteredColors = scheduleChangeColors(filtered, cached);
    expect(filteredColors).toEqual(cached);
    expect(scheduleChangeColors(overview, filteredColors)).toEqual(cached);
    expect(cached).toEqual(snapshot);
  });

  it('does not recolor existing pairs when an earlier key is added later', () => {
    const overview = manyPairs(5), all = structuredClone(overview);
    overview.weeks.forEach(week => week.lessons.shift());
    const before = scheduleChangeColors(overview), after = scheduleChangeColors(all, before);
    for (const [key, color] of before) expect(after.get(key)).toBe(color);
    expect(after.size).toBe(5);
    expect(new Set(after.values()).size).toBe(5);
  });

  it('supports a semester much larger than the palette without collisions or pale white-background lines', () => {
    const colors = scheduleChangeColors(manyPairs(250));
    expect(colors.size).toBe(250);
    expect(new Set(colors.values()).size).toBe(250);
    for (const color of colors.values()) {
      expect(color).toMatch(/^#[\da-f]{6}$/);
      const values = [1, 3, 5].map(offset => parseInt(color.slice(offset, offset + 2), 16) / 255)
        .map(channel => channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4);
      const luminance = 0.2126 * values[0] + 0.7152 * values[1] + 0.0722 * values[2];
      expect(1.05 / (luminance + 0.05)).toBeGreaterThanOrEqual(4.5);
    }
  });

  it('does not allocate a color for an incomplete or invalid pair', () => {
    const overview = manyPairs(3);
    overview.weeks[1].lessons[1].adjustment.request_id = 'mismatched';
    overview.weeks[1].lessons.pop();
    expect(scheduleChangeColors(overview).size).toBe(1);
  });
});
