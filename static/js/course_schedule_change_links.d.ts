import type { ScheduleLesson } from './course_schedule_deck.js';

export type ScheduleConnection = {
  key: string;
  sourceKey: string | null;
  targetKey: string | null;
  direction: 'local' | 'room' | 'outgoing' | 'incoming';
  edge: 'left' | 'right' | null;
  label: '时间更改' | '教室更改' | '时间更改 · 教室更改';
  boundaryLabel: string;
  title: string;
  jumpKey: string;
  jumpWeek: number;
  courseName: string;
};
export type ScheduleConnectionWeek = { week_index?: number; lessons?: ScheduleLesson[] };
export function scheduleChangeConnections(
  overview: { weeks?: ScheduleConnectionWeek[] } | null | undefined,
  week: ScheduleConnectionWeek | null | undefined,
): ScheduleConnection[];
