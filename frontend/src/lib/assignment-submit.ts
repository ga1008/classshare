export type AssignmentSubmitPayload = {
  assignmentId: string;
  initialAccepting: boolean;
  canResubmitSubmission: boolean;
  resubmissionDueAt: string | null;
  actionHint: string;
  submitLabel: string;
};

export type AssignmentUploadSnapshot = {
  count: number;
  totalBytes: number;
};

export const ASSIGNMENT_UPLOAD_CHANGE_EVENT = 'lanshare:assignment-upload-change';
export const ASSIGNMENT_SUBMIT_AVAILABILITY_CHANGE_EVENT =
  'lanshare:assignment-submit-availability-change';

function toText(value: unknown, fallback = '') {
  return typeof value === 'string' ? value : fallback;
}

function toBoolean(value: unknown, fallback = false) {
  return typeof value === 'boolean' ? value : fallback;
}

function toNumber(value: unknown, fallback = 0) {
  const numberValue = Number(value);
  return Number.isFinite(numberValue) && numberValue >= 0 ? numberValue : fallback;
}

export function normalizeAssignmentSubmitPayload(value: unknown): AssignmentSubmitPayload {
  const record = value && typeof value === 'object' ? (value as Record<string, unknown>) : {};
  const dueAt = record.resubmissionDueAt;

  return {
    assignmentId: toText(record.assignmentId),
    initialAccepting: toBoolean(record.initialAccepting),
    canResubmitSubmission: toBoolean(record.canResubmitSubmission),
    resubmissionDueAt: typeof dueAt === 'string' && dueAt ? dueAt : null,
    actionHint: toText(record.actionHint, '提交前请确认答案和附件。'),
    submitLabel: toText(record.submitLabel, '确认提交作业'),
  };
}

export function normalizeUploadSnapshot(value: unknown): AssignmentUploadSnapshot {
  const record = value && typeof value === 'object' ? (value as Record<string, unknown>) : {};
  const entries = Array.isArray(record.entries) ? record.entries : [];

  return {
    count: toNumber(record.count, entries.length),
    totalBytes: toNumber(record.totalBytes),
  };
}

export function isResubmissionWindowOpen(
  canResubmitSubmission: boolean,
  resubmissionDueAt: string | null,
  nowMs = Date.now(),
) {
  if (!canResubmitSubmission) {
    return false;
  }
  if (!resubmissionDueAt) {
    return true;
  }
  const dueMs = Date.parse(resubmissionDueAt);
  return Number.isNaN(dueMs) || dueMs > nowMs;
}

export function formatUploadBytes(bytes: number) {
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`;
  }
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function buildAssignmentSubmitStatus({
  accepting,
  answeredCount,
  totalAnswerCount,
  uploadCount,
  canResubmitSubmission,
}: {
  accepting: boolean;
  answeredCount: number;
  totalAnswerCount: number;
  uploadCount: number;
  canResubmitSubmission: boolean;
}) {
  const hasContent = answeredCount > 0 || uploadCount > 0;

  if (!accepting) {
    return {
      tone: 'danger' as const,
      title: '提交窗口已关闭',
      description: '服务器时间已不再允许提交，页面会继续保留当前内容供查看。',
    };
  }

  if (!hasContent) {
    return {
      tone: 'muted' as const,
      title: canResubmitSubmission ? '等待重新作答' : '等待作答',
      description: '输入答案或添加附件后再提交，系统会同步记录文本和文件。',
    };
  }

  if (canResubmitSubmission) {
    return {
      tone: 'warning' as const,
      title: '重交内容已准备',
      description: '重新提交会替换当前版本，请确认答案和附件无遗漏。',
    };
  }

  if (totalAnswerCount > 0 && answeredCount >= totalAnswerCount) {
    return {
      tone: 'success' as const,
      title: '答案已填写完整',
      description: '提交前再检查一次附件与文本内容即可。',
    };
  }

  return {
    tone: 'info' as const,
    title: '已有可提交内容',
    description: '还可以继续补充答案或附件，提交时会一并保存。',
  };
}
