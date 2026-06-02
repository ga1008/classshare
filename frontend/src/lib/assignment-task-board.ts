export const ASSIGNMENT_TASK_BOARD_EVENT = 'lanshare:assignment-task-board-change';
export const ASSIGNMENT_TASK_BOARD_COMMAND_EVENT = 'lanshare:assignment-task-board-command';

export type AssignmentTaskClock = {
  hasClock: boolean;
  label: string;
  value: string;
  detail: string;
  phase: string;
  accepting: boolean;
  lateOpen: boolean;
  isUrgent: boolean;
  isExpired: boolean;
};

export type AssignmentTaskItem = {
  id: string;
  title: string;
  kind: 'assignment' | 'exam';
  link: string;
  statusKey: string;
  statusLabel: string;
  stageLabel: string;
  createdAt: string;
  submittedCount: number;
  totalStudents: number;
  pendingGradeCount: number;
  gradingCount: number;
  returnedCount: number;
  unsubmittedCount: number;
  lateCount: number;
  clock: AssignmentTaskClock;
  priority: string;
};

export type AssignmentTaskSummary = {
  total: number;
  assignmentCount: number;
  examCount: number;
  stageCount: number;
  openCount: number;
  urgentCount: number;
  lateOpenCount: number;
  reviewQueue: number;
  gradingQueue: number;
  returnedCount: number;
  unsubmittedCount: number;
};

export type AssignmentTaskBoardSnapshot = {
  role: string;
  classOfferingId: number | string | null;
  items: AssignmentTaskItem[];
  summary: AssignmentTaskSummary;
  focusItemId: string;
};

const EMPTY_CLOCK: AssignmentTaskClock = {
  hasClock: false,
  label: '',
  value: '',
  detail: '',
  phase: '',
  accepting: false,
  lateOpen: false,
  isUrgent: false,
  isExpired: false,
};

const EMPTY_SUMMARY: AssignmentTaskSummary = {
  total: 0,
  assignmentCount: 0,
  examCount: 0,
  stageCount: 0,
  openCount: 0,
  urgentCount: 0,
  lateOpenCount: 0,
  reviewQueue: 0,
  gradingQueue: 0,
  returnedCount: 0,
  unsubmittedCount: 0,
};

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? (value as Record<string, unknown>) : {};
}

function toText(value: unknown, fallback = ''): string {
  if (typeof value === 'string') {
    return value;
  }
  if (typeof value === 'number' && Number.isFinite(value)) {
    return String(value);
  }
  return fallback;
}

function toCount(value: unknown): number {
  const count = Number(value);
  return Number.isFinite(count) && count >= 0 ? Math.round(count) : 0;
}

function toBoolean(value: unknown): boolean {
  return value === true || value === 1 || value === '1' || value === 'true';
}

function normalizeClock(value: unknown): AssignmentTaskClock {
  const record = asRecord(value);
  return {
    hasClock: toBoolean(record.hasClock),
    label: toText(record.label),
    value: toText(record.value),
    detail: toText(record.detail),
    phase: toText(record.phase),
    accepting: toBoolean(record.accepting),
    lateOpen: toBoolean(record.lateOpen),
    isUrgent: toBoolean(record.isUrgent),
    isExpired: toBoolean(record.isExpired),
  };
}

function normalizeTaskItem(value: unknown): AssignmentTaskItem {
  const record = asRecord(value);
  const kind = record.kind === 'exam' ? 'exam' : 'assignment';
  return {
    id: toText(record.id),
    title: toText(record.title, '未命名任务'),
    kind,
    link: toText(record.link),
    statusKey: toText(record.statusKey),
    statusLabel: toText(record.statusLabel),
    stageLabel: toText(record.stageLabel),
    createdAt: toText(record.createdAt),
    submittedCount: toCount(record.submittedCount),
    totalStudents: toCount(record.totalStudents),
    pendingGradeCount: toCount(record.pendingGradeCount),
    gradingCount: toCount(record.gradingCount),
    returnedCount: toCount(record.returnedCount),
    unsubmittedCount: toCount(record.unsubmittedCount),
    lateCount: toCount(record.lateCount),
    clock: normalizeClock(record.clock),
    priority: toText(record.priority, 'normal'),
  };
}

function normalizeSummary(value: unknown, items: AssignmentTaskItem[]): AssignmentTaskSummary {
  const record = asRecord(value);
  if (Object.keys(record).length) {
    return {
      total: toCount(record.total),
      assignmentCount: toCount(record.assignmentCount),
      examCount: toCount(record.examCount),
      stageCount: toCount(record.stageCount),
      openCount: toCount(record.openCount),
      urgentCount: toCount(record.urgentCount),
      lateOpenCount: toCount(record.lateOpenCount),
      reviewQueue: toCount(record.reviewQueue),
      gradingQueue: toCount(record.gradingQueue),
      returnedCount: toCount(record.returnedCount),
      unsubmittedCount: toCount(record.unsubmittedCount),
    };
  }

  return items.reduce((summary, item) => ({
    total: summary.total + 1,
    assignmentCount: summary.assignmentCount + (item.kind === 'assignment' ? 1 : 0),
    examCount: summary.examCount + (item.kind === 'exam' ? 1 : 0),
    stageCount: summary.stageCount + (item.stageLabel ? 1 : 0),
    openCount: summary.openCount + (item.clock.accepting || item.statusKey === 'published' || item.statusKey === 'unsubmitted' ? 1 : 0),
    urgentCount: summary.urgentCount + (item.clock.isUrgent ? 1 : 0),
    lateOpenCount: summary.lateOpenCount + (item.clock.lateOpen || item.lateCount > 0 ? 1 : 0),
    reviewQueue: summary.reviewQueue + item.pendingGradeCount,
    gradingQueue: summary.gradingQueue + item.gradingCount,
    returnedCount: summary.returnedCount + item.returnedCount + (item.statusKey === 'returned' ? 1 : 0),
    unsubmittedCount: summary.unsubmittedCount + item.unsubmittedCount + (item.statusKey === 'unsubmitted' ? 1 : 0),
  }), { ...EMPTY_SUMMARY });
}

export function normalizeAssignmentTaskBoardSnapshot(value: unknown): AssignmentTaskBoardSnapshot {
  const record = asRecord(value);
  const items = Array.isArray(record.items)
    ? record.items.map(normalizeTaskItem).filter((item) => item.id || item.title)
    : [];
  const focusItemId = toText(record.focusItemId) || getAssignmentTaskFocusItem(items)?.id || '';

  return {
    role: toText(record.role),
    classOfferingId: typeof record.classOfferingId === 'string' || typeof record.classOfferingId === 'number'
      ? record.classOfferingId
      : null,
    items,
    summary: normalizeSummary(record.summary, items),
    focusItemId,
  };
}

export function getAssignmentTaskFocusItem(items: AssignmentTaskItem[]): AssignmentTaskItem | null {
  const order = ['urgent', 'late', 'review', 'returned', 'todo'];
  for (const priority of order) {
    const item = items.find((candidate) => candidate.priority === priority);
    if (item) {
      return item;
    }
  }
  return items[0] || null;
}

export function getAssignmentTaskBoardReadiness(summary: AssignmentTaskSummary): number {
  if (!summary.total) {
    return 0;
  }
  const activeSignals = summary.openCount + summary.stageCount + summary.examCount;
  return Math.min(100, Math.round((activeSignals / Math.max(summary.total * 3, 1)) * 100));
}

export function getAssignmentTaskKindLabel(kind: AssignmentTaskItem['kind']): string {
  return kind === 'exam' ? '考试' : '作业';
}

export function buildAssignmentTaskBoardMessage(snapshot: AssignmentTaskBoardSnapshot): string {
  const { summary } = snapshot;
  if (!summary.total) {
    return snapshot.role === 'teacher'
      ? '当前课堂还没有作业或考试，可以新建作业或从试卷库发布。'
      : '当前课堂暂时没有可进入的作业或考试。';
  }
  if (summary.urgentCount > 0) {
    return `${summary.urgentCount} 个任务即将截止，建议优先处理。`;
  }
  if (summary.lateOpenCount > 0) {
    return `${summary.lateOpenCount} 个任务处于补交窗口，请留意扣分策略。`;
  }
  if (snapshot.role === 'teacher' && summary.reviewQueue > 0) {
    return `${summary.reviewQueue} 份提交等待批改，可从统计卡定位到对应任务。`;
  }
  if (snapshot.role !== 'teacher' && (summary.unsubmittedCount > 0 || summary.returnedCount > 0)) {
    return '还有未提交或待重交任务，先处理高优先级项。';
  }
  return `共 ${summary.total} 个任务，包含 ${summary.assignmentCount} 个作业和 ${summary.examCount} 个考试。`;
}
