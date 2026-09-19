import type { ScheduleLesson } from './course_schedule_deck.js';

export function compactClassroomName(value: unknown): string;
export function classroomChangeState(from: unknown, to: unknown): boolean | null;
export function adjustmentActionText(lesson?: ScheduleLesson | null): '' | '改时间' | '改教室' | '教室+时间' | '停课' | '待审变更';
