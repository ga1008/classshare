import type { ScheduleLesson } from './course_schedule_deck.js';

export function compactClassroomName(value: unknown): string;
export function classroomChangeState(from: unknown, to: unknown): boolean | null;
export function scheduleChanges(lesson?: ScheduleLesson | null): NonNullable<ScheduleLesson['adjustment']>[];
export function adjustmentActionText(lesson?: ScheduleLesson | null, suppliedChange?: ScheduleLesson['adjustment']): '' | '改时间' | '改教室' | '教室+时间' | '停课' | '待审变更' | '已调至新位' | '查看原安排';
