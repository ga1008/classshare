import { describe, expect, it } from 'vitest';
import { projectScheduleChanges, scheduleChangeConnections } from '../../../static/js/course_schedule_change_links.js';
import { countScheduleLessons } from '../../../static/js/course_schedule_deck.js';
import { scheduleChangeBadge, scheduleChangeLabel, scheduleChanges } from '../../../static/js/course_schedule_presentation.js';

function fixture(kind = 'move', phase = 'planned') {
  const original = { date: '2026-09-20', sections: [2, 3], room: 'B310' };
  const proposed = kind === 'cancel' ? null : { date: kind === 'room' ? original.date : '2026-10-11', sections: [2, 3], room: 'B210' };
  const lesson = { event_key: 'official', session_id: 101, class_offering_id: 10, actual_date: original.date,
    weekday: 7, sections: original.sections, classroom: original.room, course_name: '网络', counts_towards_total: true };
  return { calendar: { swaps: [{ workday_date: '2026-10-10', makeup_for_date: '2026-10-07' }] },
    weeks: [{ week_index: 3, lessons: [lesson] as any[] }, { week_index: 6, lessons: [] as any[] }],
    planned_changes: [{ request_id: 'r1', detail_id: 'd1', phase, approval_status: phase === 'draft' ? 'draft' : 'approved', kind,
      source_event_key: lesson.event_key, session_id: 101, class_offering_id: 10, original_week_index: 3,
      proposed_week_index: kind === 'cancel' ? null : kind === 'room' ? 3 : 6, original, proposed }] };
}

describe('approved plans show the final timetable; drafts show the proposed change', () => {
  it('moves an approved-but-not-yet-official lesson to its final slot without an arrow', () => {
    const original = fixture(), before = structuredClone(original), view = projectScheduleChanges(original);
    expect(original).toEqual(before);
    expect(view.calendar).toEqual(before.calendar);
    expect(view.weeks[0].lessons).toEqual([]);
    const moved = view.weeks[1].lessons[0];
    expect(moved).toMatchObject({ actual_date: '2026-10-11', counts_towards_total: true, classroom: 'B210', adjustment: null });
    expect(moved.is_change_plan).toBeFalsy();
    expect(scheduleChangeBadge(moved)).toMatchObject({ label: '已换教室' });
    expect(view.weeks.flatMap(week => scheduleChangeConnections(view, week))).toEqual([]);
    expect(countScheduleLessons(view.weeks.flatMap(week => week.lessons))).toEqual({ lesson_count: 1, total_hours: 2, proposed_count: 0 });
    expect(projectScheduleChanges(view)).toEqual(view);
  });

  it('hides an approved cancellation and relabels an approved room change in place', () => {
    expect(projectScheduleChanges(fixture('cancel')).weeks.flatMap(week => week.lessons)).toEqual([]);
    const room = projectScheduleChanges(fixture('room'));
    expect(room.weeks[0].lessons[0]).toMatchObject({ classroom: 'B210' });
    expect(scheduleChangeBadge(room.weeks[0].lessons[0])).toMatchObject({ label: '已换教室' });
    expect(scheduleChangeConnections(room, room.weeks[0])).toEqual([]);
  });

  it('draws a draft move as original card + uncounted ghost joined by a draft arrow', () => {
    const view = projectScheduleChanges(fixture('move', 'draft'));
    const source = view.weeks[0].lessons[0], ghost = view.weeks[1].lessons[0];
    expect(ghost).toMatchObject({ is_change_plan: true, counts_towards_total: false, actual_date: '2026-10-11' });
    expect(scheduleChangeBadge(source)).toMatchObject({ label: '草稿·换教室' });
    expect(scheduleChangeBadge(ghost)).toMatchObject({ label: '草稿位置' });
    expect(scheduleChangeLabel(ghost)).toBe('草稿拟安排 · 未提交');
    expect(scheduleChangeConnections(view, view.weeks[0])[0]).toMatchObject({ direction: 'outgoing', jumpKey: ghost.event_key, jumpWeek: 6, label: '草稿 · 时间更改 · 教室更改' });
    expect(countScheduleLessons(view.weeks.flatMap(week => week.lessons))).toEqual({ lesson_count: 1, total_hours: 2, proposed_count: 0 });
    expect(projectScheduleChanges(view)).toEqual(view);
  });

  it.each(['source', 'session', 'date', 'destination', 'state'])('rejects unreliable %s evidence without touching the timetable', key => {
    const input = fixture();
    if (key === 'source') input.weeks[0].lessons = [];
    if (key === 'session') input.planned_changes[0].session_id = 999;
    if (key === 'date') input.planned_changes[0].original.date = '2026-09-19';
    if (key === 'destination') input.planned_changes[0].proposed_week_index = 90;
    if (key === 'state') input.planned_changes[0].approval_status = 'pending';
    const view = projectScheduleChanges(input);
    expect(view.weeks[1].lessons).toEqual([]);
    expect(view.weeks[0].lessons).toEqual(input.weeks[0].lessons);
  });

  it('calendar-only data cannot create a request relationship', () => {
    const input = fixture(); input.planned_changes = [];
    expect(projectScheduleChanges(input)).toBe(input);
    expect(scheduleChanges(input.weeks[0].lessons[0])).toEqual([]);
  });
});
