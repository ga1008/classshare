export const TEACHER_SUBMISSION_WORKBENCH_EVENT = 'lanshare:teacher-submission-workbench-change';

export type TeacherSubmissionWorkbenchStats = {
  totalStudents: number;
  submitted: number;
  unsubmitted: number;
  pending: number;
  graded: number;
  returned: number;
  grading: number;
  fail: number;
  averageScore: number | null;
  passRate: number | null;
};

export type TeacherSubmissionWorkbenchSnapshot = {
  currentFilter: string;
  scoreRangeFilter: string | null;
  searchText: string;
  totalEntries: number;
  filteredEntries: number;
  selectedCount: number;
  selectableCount: number;
  visibleSelectableCount: number;
  zeroUnsubmittedCount: number;
  aiReadyCount: number;
  aiBlockedCount: number;
  stats: TeacherSubmissionWorkbenchStats;
};

function toNumber(value: unknown, fallback = 0) {
  const numberValue = Number(value);
  return Number.isFinite(numberValue) && numberValue >= 0 ? numberValue : fallback;
}

function toNullableNumber(value: unknown) {
  if (value === null || value === undefined || value === '') {
    return null;
  }
  const numberValue = Number(value);
  return Number.isFinite(numberValue) ? numberValue : null;
}

function toText(value: unknown, fallback = '') {
  return typeof value === 'string' ? value : fallback;
}

export function normalizeTeacherSubmissionWorkbenchSnapshot(value: unknown): TeacherSubmissionWorkbenchSnapshot {
  const record = value && typeof value === 'object' ? (value as Record<string, unknown>) : {};
  const statsRecord = record.stats && typeof record.stats === 'object'
    ? (record.stats as Record<string, unknown>)
    : {};

  return {
    currentFilter: toText(record.currentFilter, 'all'),
    scoreRangeFilter: typeof record.scoreRangeFilter === 'string' && record.scoreRangeFilter
      ? record.scoreRangeFilter
      : null,
    searchText: toText(record.searchText),
    totalEntries: toNumber(record.totalEntries),
    filteredEntries: toNumber(record.filteredEntries),
    selectedCount: toNumber(record.selectedCount),
    selectableCount: toNumber(record.selectableCount),
    visibleSelectableCount: toNumber(record.visibleSelectableCount),
    zeroUnsubmittedCount: toNumber(record.zeroUnsubmittedCount),
    aiReadyCount: toNumber(record.aiReadyCount),
    aiBlockedCount: toNumber(record.aiBlockedCount),
    stats: {
      totalStudents: toNumber(statsRecord.totalStudents),
      submitted: toNumber(statsRecord.submitted),
      unsubmitted: toNumber(statsRecord.unsubmitted),
      pending: toNumber(statsRecord.pending),
      graded: toNumber(statsRecord.graded),
      returned: toNumber(statsRecord.returned),
      grading: toNumber(statsRecord.grading),
      fail: toNumber(statsRecord.fail),
      averageScore: toNullableNumber(statsRecord.averageScore),
      passRate: toNullableNumber(statsRecord.passRate),
    },
  };
}

export function getSubmissionRate(snapshot: TeacherSubmissionWorkbenchSnapshot) {
  const total = snapshot.stats.totalStudents;
  if (!total) {
    return 0;
  }
  return Math.max(0, Math.min(100, Math.round((snapshot.stats.submitted / total) * 100)));
}

export function getActionNeededCount(snapshot: TeacherSubmissionWorkbenchSnapshot) {
  return snapshot.stats.pending + snapshot.stats.unsubmitted + snapshot.stats.returned;
}

export function buildTeacherWorkbenchMessage(snapshot: TeacherSubmissionWorkbenchSnapshot) {
  const actionNeeded = getActionNeededCount(snapshot);
  if (actionNeeded > 0) {
    return `${snapshot.stats.pending} 份待批改，${snapshot.stats.unsubmitted} 人未提交，${snapshot.stats.returned} 人待重交。`;
  }
  if (snapshot.stats.grading > 0) {
    return `${snapshot.stats.grading} 份正在 AI 批改中，稍后刷新确认结果。`;
  }
  return '当前没有紧急处理项，可以抽查详情或查看成绩分布。';
}

export function getFilterLabel(filter: string, scoreRangeFilter: string | null) {
  if (scoreRangeFilter) {
    const labels: Record<string, string> = {
      none: '无成绩',
      fail: '不及格',
      pass: '及格',
      medium: '良好',
      good: '优秀',
      excellent: '极好',
    };
    return `分数：${labels[scoreRangeFilter] || scoreRangeFilter}`;
  }
  const labels: Record<string, string> = {
    all: '全部',
    submitted: '待批改',
    returned: '待重交',
    graded: '已批改',
    unsubmitted: '未提交',
    fail: '不及格',
  };
  return labels[filter] || filter;
}
