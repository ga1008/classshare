export const ASSIGNMENT_AUTHORING_EVENT = 'lanshare:assignment-authoring-change';
export const ASSIGNMENT_AUTHORING_COMMAND_EVENT = 'lanshare:assignment-authoring-command';

export type AssignmentAuthoringSnapshot = {
  assignmentId: string | null;
  title: string;
  requirementLength: number;
  rubricLength: number;
  gradingMode: string;
  allowedFileTypes: string[];
  learningStageKey: string;
  learningStageLabel: string;
  sendEmailNotification: boolean;
  scheduleMode: string;
  dueAt: string;
  durationMinutes: string;
  startsAt: string;
  lateSubmissionEnabled: boolean;
  lateSubmissionUntil: string;
  latePenaltyStrategy: string;
  latePenaltyPoints: string;
  lateScoreCap: string;
  completedChecks: number;
  totalChecks: number;
  canSave: boolean;
  isSaving: boolean;
  lastError: string;
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

function normalizeAllowedTypes(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value.map((item) => toText(item).trim()).filter(Boolean);
  }
  return toText(value)
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean);
}

export function normalizeAssignmentAuthoringSnapshot(value: unknown): AssignmentAuthoringSnapshot {
  const record = asRecord(value);
  const completedChecks = toCount(record.completedChecks);
  const totalChecks = toCount(record.totalChecks) || 5;
  return {
    assignmentId: record.assignmentId == null || record.assignmentId === '' ? null : toText(record.assignmentId),
    title: toText(record.title),
    requirementLength: toCount(record.requirementLength),
    rubricLength: toCount(record.rubricLength),
    gradingMode: toText(record.gradingMode, 'manual'),
    allowedFileTypes: normalizeAllowedTypes(record.allowedFileTypes),
    learningStageKey: toText(record.learningStageKey),
    learningStageLabel: toText(record.learningStageLabel),
    sendEmailNotification: toBoolean(record.sendEmailNotification),
    scheduleMode: toText(record.scheduleMode, 'permanent'),
    dueAt: toText(record.dueAt),
    durationMinutes: toText(record.durationMinutes),
    startsAt: toText(record.startsAt),
    lateSubmissionEnabled: toBoolean(record.lateSubmissionEnabled),
    lateSubmissionUntil: toText(record.lateSubmissionUntil),
    latePenaltyStrategy: toText(record.latePenaltyStrategy, 'fixed'),
    latePenaltyPoints: toText(record.latePenaltyPoints),
    lateScoreCap: toText(record.lateScoreCap),
    completedChecks,
    totalChecks,
    canSave: toBoolean(record.canSave),
    isSaving: toBoolean(record.isSaving),
    lastError: toText(record.lastError),
  };
}

export function getAssignmentAuthoringReadiness(snapshot: AssignmentAuthoringSnapshot): number {
  if (!snapshot.totalChecks) {
    return 0;
  }
  return Math.min(100, Math.round((snapshot.completedChecks / snapshot.totalChecks) * 100));
}

export function getAssignmentScheduleLabel(snapshot: AssignmentAuthoringSnapshot): string {
  if (snapshot.scheduleMode === 'deadline') {
    return snapshot.dueAt ? '截止时间' : '待设截止';
  }
  if (snapshot.scheduleMode === 'countdown') {
    return snapshot.durationMinutes ? '倒计时' : '待设时长';
  }
  return '长期有效';
}

export function getAssignmentModeLabel(mode: string): string {
  return mode === 'ai' ? 'AI 辅助批改' : '手动批改';
}

export function buildAssignmentAuthoringMessage(snapshot: AssignmentAuthoringSnapshot): string {
  if (snapshot.isSaving) {
    return '正在沿用原发布链路保存作业。';
  }
  if (snapshot.lastError) {
    return snapshot.lastError;
  }
  if (!snapshot.title) {
    return '先填写标题，旧保存逻辑会继续做最终校验。';
  }
  if (snapshot.lateSubmissionEnabled && snapshot.scheduleMode === 'permanent') {
    return '补交扣分需要先设置首次截止或倒计时。';
  }
  if (!snapshot.rubricLength && snapshot.gradingMode === 'ai') {
    return 'AI 辅助批改建议补齐评分标准。';
  }
  return snapshot.assignmentId ? '正在编辑已有作业，保存后沿原页面刷新。' : '新作业草稿已具备基本发布条件。';
}
