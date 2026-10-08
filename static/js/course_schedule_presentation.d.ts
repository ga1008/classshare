import type { ScheduleLesson } from './course_schedule_deck.js';

export function compactClassroomName(value: unknown): string;
export function classroomChangeState(from: unknown, to: unknown): boolean | null;
export function scheduleChanges(lesson?: ScheduleLesson | null): NonNullable<ScheduleLesson['adjustment']>[];
export function scheduleChangeLabel(lesson: ScheduleLesson, suppliedChange?: ScheduleLesson['adjustment']): string;
export function adjustmentActionText(lesson?: ScheduleLesson | null, suppliedChange?: ScheduleLesson['adjustment']): '' | '改时间' | '改教室' | '教室+时间' | '停课' | '待审变更' | '已调至新位' | '查看原安排' | '已批准待停课' | '已批准待落实' | '计划新位置';

/** Compact stage badge for a lesson card (draft → 审核中 → 已批准/已换教室); null when the card needs none. */
export function scheduleChangeBadge(lesson: unknown): {
  phase: 'draft' | 'pending' | 'planned' | 'approved';
  label: string;
  tone: 'neutral' | 'warning' | 'success' | 'info';
  roomChanged: boolean;
  title: string;
} | null;
