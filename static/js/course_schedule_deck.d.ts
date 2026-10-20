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
export function scheduleChangeLabel(lesson: ScheduleLesson, suppliedChange?: ScheduleLesson['adjustment']): string;

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
export type ScheduleWeek = { week_index: number; lessons?: ScheduleLesson[]; [key: string]: unknown };
export type ScheduleDeckOptions = {
  title?: string;
  description?: string;
  showTermSelect?: boolean;
  compactSummary?: boolean;
  allowExpand?: boolean;
  onTermChange?: (year: string, term: string) => void;
  onNavigate?: (url: string) => void;
  onLessonClick?: (lesson: ScheduleLesson, context: { trigger: HTMLElement; week: ScheduleWeek }) => void;
  emptyHtml?: (overview: unknown) => string;
  editorUrl?: string | ((term: Record<string, unknown>) => string);
};
export function createScheduleDeck(container: HTMLElement | null, options?: ScheduleDeckOptions): {
  overlay: ScheduleDeckOverlay;
  setOverview: (overview: unknown, options?: { keepWeek?: boolean }) => void;
  goToWeek: (index: number) => void;
  openExpanded: () => void;
  focusLesson: (eventKey: string, weekIndex?: number) => unknown;
  showAdjustment: (eventKey: string) => unknown;
  getActiveWeekIndex: () => number;
  destroy: () => void;
} | null;
