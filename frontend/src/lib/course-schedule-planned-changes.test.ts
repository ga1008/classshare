import { describe, expect, it } from 'vitest';
import { projectScheduleChanges, scheduleChangeConnections } from '../../../static/js/course_schedule_change_links.js';
import { countScheduleLessons } from '../../../static/js/course_schedule_deck.js';
import { scheduleChangeLabel, scheduleChanges, adjustmentActionText } from '../../../static/js/course_schedule_presentation.js';

function fixture(kind = 'move') {
  const original = { date: '2026-09-20', sections: [2, 3], room: 'B310' };
  const proposed = kind === 'cancel' ? null : { date: kind === 'room' ? original.date : '2026-10-11', sections: [2, 3], room: 'B210' };
  const lesson = { event_key: 'official', session_id: 101, class_offering_id: 10, actual_date: original.date,
    weekday: 7, sections: original.sections, classroom: original.room, course_name: '网络', counts_towards_total: true };
  return { calendar: { swaps: [{ workday_date: '2026-10-10', makeup_for_date: '2026-10-07' }] },
    weeks: [{ week_index: 3, lessons: [lesson] as any[] }, { week_index: 6, lessons: [] as any[] }],
    planned_changes: [{ request_id: 'r1', detail_id: 'd1', phase: 'planned', approval_status: 'approved', kind,
      source_event_key: lesson.event_key, session_id: 101, class_offering_id: 10, original_week_index: 3,
      proposed_week_index: kind === 'cancel' ? null : kind === 'room' ? 3 : 6, original, proposed }] };
}

describe('approved plans remain a display-only change distinct from holidays and applied moves', () => {
  it('projects two reversible endpoints with truthful labels and unchanged canonical hours', () => {
    const original = fixture(), before = structuredClone(original), view = projectScheduleChanges(original);
    expect(original).toEqual(before);
    expect(view.calendar).toEqual(before.calendar);
    const source = view.weeks[0].lessons[0], target = view.weeks[1].lessons[0];
    expect(target).toMatchObject({ is_change_plan: true, counts_towards_total: false, actual_date: '2026-10-11', edit_ghost: false, edit_draft: null });
    expect(scheduleChangeLabel(source)).toBe('原安排 · 已批准·待落实');
    expect(scheduleChangeLabel(target)).toBe('计划安排 · 已批准·待落实');
    expect(adjustmentActionText(source)).toBe('已批准待落实');
    expect(scheduleChangeConnections(view, view.weeks[0])[0]).toMatchObject({ direction: 'outgoing', jumpKey: target.event_key, jumpWeek: 6, label: '已批准·待落实 · 时间更改 · 教室更改' });
    expect(scheduleChangeConnections(view, view.weeks[1])[0]).toMatchObject({ direction: 'incoming', jumpKey: 'official', jumpWeek: 3 });
    expect(countScheduleLessons(view.weeks.flatMap(week => week.lessons))).toEqual({ lesson_count: 1, total_hours: 2, proposed_count: 0 });
    expect(projectScheduleChanges(view)).toEqual(view);
  });

  it.each(['room', 'cancel'])('%s uses a single comparison card without a time arrow', kind => {
    const view = projectScheduleChanges(fixture(kind));
    expect(view.weeks[1].lessons).toHaveLength(0);
    expect(scheduleChanges(view.weeks[0].lessons[0])).toHaveLength(1);
    expect(scheduleChangeConnections(view, view.weeks[0])).toEqual([]);
  });

  it.each(['source', 'session', 'date', 'destination', 'state'])('rejects unreliable %s evidence without ghost cards', key => {
    const input = fixture();
    if (key === 'source') input.weeks[0].lessons = [];
    if (key === 'session') input.planned_changes[0].session_id = 999;
    if (key === 'date') input.planned_changes[0].original.date = '2026-09-19';
    if (key === 'destination') input.planned_changes[0].proposed_week_index = 90;
    if (key === 'state') input.planned_changes[0].approval_status = 'pending';
    expect(projectScheduleChanges(input).weeks[1].lessons).toEqual([]);
  });

  it('calendar-only data cannot create a request relationship', () => {
    const input = fixture(); input.planned_changes = [];
    expect(projectScheduleChanges(input)).toBe(input);
    expect(scheduleChanges(input.weeks[0].lessons[0])).toEqual([]);
  });

  it('keeps an applied A to B history alongside a planned B to C without overwriting either relation', () => {
    const input: any = fixture();
    input.weeks.unshift({ week_index: 2, lessons: [] });
    input.approved_changes = [{ request_id: 'earlier', detail_id: 'first', phase: 'approved', kind: 'move',
      target_event_key: 'official', session_id: 101, class_offering_id: 10, original_week_index: 2, effective_week_index: 3,
      original: { date: '2026-09-13', sections: [2, 3], room: 'B310' }, proposed: input.planned_changes[0].original }];
    const view = projectScheduleChanges(input);
    expect(scheduleChanges(view.weeks[1].lessons[0]).map(change => change.phase)).toEqual(['approved', 'planned']);
    expect(scheduleChangeConnections(view, view.weeks[1])).toHaveLength(2);
    expect(countScheduleLessons(view.weeks.flatMap((week: any) => week.lessons))).toEqual({ lesson_count: 1, total_hours: 2, proposed_count: 0 });
  });
});
