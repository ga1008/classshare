export const EXAM_ASSIGN_EVENT = 'lanshare:exam-assign-change';
export const EXAM_ASSIGN_COMMAND_EVENT = 'lanshare:exam-assign-command';

export type ExamAssignSnapshot = {
  selectedPaperId: string;
  selectedPaperTitle: string;
  paperCount: number;
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
  latePenaltyIntervalHours: string;
  latePenaltyPoints: string;
  latePenaltyMinScore: string;
  lateScoreCap: string;
  feedbackType: string;
  completedChecks: number;
  totalChecks: number;
  canPublish: boolean;
  isLoading: boolean;
  isPublishing: boolean;
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

export function normalizeExamAssignSnapshot(value: unknown): ExamAssignSnapshot {
  const record = asRecord(value);
  const completedChecks = toCount(record.completedChecks);
  const totalChecks = toCount(record.totalChecks) || 4;
  return {
    selectedPaperId: toText(record.selectedPaperId),
    selectedPaperTitle: toText(record.selectedPaperTitle),
    paperCount: toCount(record.paperCount),
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
    latePenaltyIntervalHours: toText(record.latePenaltyIntervalHours),
    latePenaltyPoints: toText(record.latePenaltyPoints),
    latePenaltyMinScore: toText(record.latePenaltyMinScore),
    lateScoreCap: toText(record.lateScoreCap),
    feedbackType: toText(record.feedbackType),
    completedChecks,
    totalChecks,
    canPublish: toBoolean(record.canPublish),
    isLoading: toBoolean(record.isLoading),
    isPublishing: toBoolean(record.isPublishing),
    lastError: toText(record.lastError),
  };
}

export function getExamAssignReadiness(snapshot: ExamAssignSnapshot): number {
  if (!snapshot.totalChecks) {
    return 0;
  }
  return Math.min(100, Math.round((snapshot.completedChecks / snapshot.totalChecks) * 100));
}

export function getExamScheduleLabel(snapshot: ExamAssignSnapshot): string {
  if (snapshot.scheduleMode === 'deadline') {
    return snapshot.dueAt ? '截止时间' : '待设截止';
  }
  if (snapshot.scheduleMode === 'countdown') {
    return snapshot.durationMinutes ? '倒计时' : '待设时长';
  }
  return '长期有效';
}

export function getExamLatePolicyLabel(snapshot: ExamAssignSnapshot): string {
  if (!snapshot.lateSubmissionEnabled) {
    return '不开放补交';
  }
  return snapshot.latePenaltyStrategy === 'gradient' ? '梯度补交' : '定量补交';
}

export function buildExamAssignMessage(snapshot: ExamAssignSnapshot): string {
  if (snapshot.isPublishing) {
    return '正在沿用原考试发布链路创建课堂考试。';
  }
  if (snapshot.isLoading) {
    return '正在同步试卷库，可先检查发布时间和补交策略。';
  }
  if (snapshot.lastError) {
    return snapshot.lastError;
  }
  if (!snapshot.paperCount) {
    return '试卷库暂无可发布试卷，可前往管理中心创建。';
  }
  if (!snapshot.selectedPaperId) {
    return '先选择一份试卷，后端仍会校验评分标准完整性。';
  }
  if (snapshot.lateSubmissionEnabled && snapshot.scheduleMode === 'permanent') {
    return '补交扣分需要先设置首次截止或倒计时。';
  }
  return snapshot.learningStageKey
    ? '已绑定阶段试炼，发布后会进入课堂考试任务。'
    : '普通课堂考试已具备基本发布条件。';
}
