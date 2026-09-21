export function scheduleWheelIntent(options: {
  deltaY?: number; deltaMode?: number; ctrlKey?: boolean; metaKey?: boolean;
  index?: number; length?: number; pending?: number;
}): { consume: boolean; step: number; pending: number };

export type ScheduleLesson = {
  weekday?: number; sections?: number[]; hours?: number; counts_towards_total?: boolean;
  adjustment?: { phase: string; kind: string; endpoint: string; [key: string]: unknown } | null;
  [key: string]: unknown;
};
export function pendingScheduleChange(lesson?: ScheduleLesson): ScheduleLesson['adjustment'];
export function countScheduleLessons(lessons?: ScheduleLesson[]): { lesson_count: number; total_hours: number; proposed_count: number };
export function scheduleLessonLanes(lessons?: ScheduleLesson[]): Map<number, { lane: number; count: number }>;
export function scheduleChangeLabel(lesson: ScheduleLesson): string;

/** Optional host adapter; the portable deck owns focus and its visual state. */
export type ScheduleOverlayCoordinator = {
  beforeOpen?: () => void;
  onStateChange?: () => void;
  isTop: () => boolean;
  onDestroy?: () => void;
};
export type ScheduleDeckOverlay = {
  getOwner: () => HTMLElement;
  getRoot: () => HTMLElement;
  getTrigger: () => Element | null;
  isExpanded: () => boolean;
  isPresent: () => boolean;
  dismissTop: (reason?: string) => boolean;
  connect: (coordinator: ScheduleOverlayCoordinator) => () => void;
};
