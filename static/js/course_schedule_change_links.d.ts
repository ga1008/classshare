import type { ScheduleLesson } from './course_schedule_deck.js';

export type ScheduleConnection = {
  key: string;
  sourceKey: string | null;
  targetKey: string | null;
  direction: 'local' | 'outgoing' | 'incoming';
  edge: 'left' | 'right' | null;
  label: string;
  boundaryLabel: string;
  title: string;
  jumpKey: string;
  jumpWeek: number;
  courseName: string;
};
export type ScheduleConnectionWeek = { week_index?: number; lessons?: ScheduleLesson[] };
export type ApprovedScheduleChange = {
  request_id: string; detail_id: string; phase: 'approved'; kind: 'move';
  target_event_key: string; session_id: number | null; class_offering_id: number | null;
  original_week_index: number; effective_week_index: number;
  original: { date: string; sections: number[]; room: string };
  proposed: { date: string; sections: number[]; room: string };
};
export type PlannedScheduleChange = {
  request_id: string; detail_id: string; phase: 'planned'; approval_status: 'approved'; kind: 'move' | 'room' | 'cancel';
  source_event_key: string; session_id: number | null; class_offering_id: number | null;
  original_week_index: number; proposed_week_index: number | null;
  original: { date: string; sections: number[]; room: string };
  proposed: { date: string; sections: number[]; room: string } | null;
};
export function projectScheduleChanges<T>(overview: T): T;
export function scheduleChangeConnections(
  overview: { weeks?: ScheduleConnectionWeek[] } | null | undefined,
  week: ScheduleConnectionWeek | null | undefined,
): ScheduleConnection[];
export function scheduleChangeColors(
  overview: { weeks?: ScheduleConnectionWeek[] } | null | undefined,
  previous?: ReadonlyMap<string, string>,
): Map<string, string>;
