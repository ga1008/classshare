export const LEARNING_PROGRESS_COMMAND_EVENT = 'lanshare:learning-progress-command';

export type LearningProgressMetric = {
  label: string;
  value: number;
  suffix: string;
  note: string;
  tone: string;
};

export type LearningProgressStage = {
  key: string;
  name: string;
  shortName: string;
  status: string;
  progressPercent: number;
};

export type LearningProgressSnapshot = {
  role: string;
  mode: 'student' | 'teacher' | 'none';
  classOfferingId: number | string | null;
  title: string;
  subtitle: string;
  score: number;
  progressPercent: number;
  metrics: LearningProgressMetric[];
  stages: LearningProgressStage[];
  primaryAction: string;
  primaryActionLabel: string;
  secondaryActionLabel: string;
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

function toScore(value: unknown): number {
  const score = Number(value);
  if (!Number.isFinite(score)) {
    return 0;
  }
  return Math.round(score * 10) / 10;
}

function toId(value: unknown): number | string | null {
  if (typeof value === 'string' || typeof value === 'number') {
    return value;
  }
  return null;
}

function normalizeStage(value: unknown): LearningProgressStage {
  const record = asRecord(value);
  return {
    key: toText(record.key),
    name: toText(record.name, toText(record.level_name)),
    shortName: toText(record.short_name, toText(record.name, '阶段')),
    status: toText(record.status),
    progressPercent: Math.min(100, toCount(record.progress_percent)),
  };
}

function metric(label: string, value: unknown, suffix = '', note = '', tone = 'neutral'): LearningProgressMetric {
  return {
    label,
    value: toScore(value),
    suffix,
    note,
    tone,
  };
}

function fromAppConfig(value: unknown): Record<string, unknown> {
  const record = asRecord(value);
  return {
    role: toText(asRecord(record.userInfo).role, toText(record.userRole)),
    classOfferingId: toId(record.classOfferingId),
    learningProgress: record.learningProgress,
    learningOverview: record.learningOverview,
  };
}

export function normalizeLearningProgressSnapshot(value: unknown): LearningProgressSnapshot {
  const config = fromAppConfig(value);
  const role = toText(config.role);
  const classOfferingId = toId(config.classOfferingId);
  const progress = asRecord(config.learningProgress);
  const overview = asRecord(config.learningOverview);

  if (role === 'student' && Object.keys(progress).length) {
    const currentLevel = asRecord(progress.current_level);
    const eligibleStage = asRecord(progress.eligible_stage);
    const nextStage = asRecord(progress.next_stage);
    const classPosition = asRecord(progress.class_position);
    const currentPosition = asRecord(classPosition.current);
    const components = asRecord(asRecord(progress.metrics).components);
    const stages = Array.isArray(progress.stages) ? progress.stages.map(normalizeStage) : [];
    const primaryAction = Object.keys(eligibleStage).length
      ? 'start-stage-exam'
      : toText(nextStage.status) === 'in_exam'
        ? 'continue-stage-exam'
        : 'open-learning-modal';
    return {
      role,
      mode: 'student',
      classOfferingId,
      title: toText(currentLevel.level_name, '修为进度'),
      subtitle: Object.keys(eligibleStage).length
        ? `可破境 · ${toText(eligibleStage.short_name, toText(eligibleStage.name))}`
        : toText(nextStage.status) === 'generating'
          ? `试炼生成中 · ${toText(nextStage.short_name, toText(nextStage.name))}`
          : `当前修为 ${toScore(progress.score)} / 100`,
      score: toScore(progress.score),
      progressPercent: Math.min(100, toCount(progress.progress_percent)),
      metrics: [
        metric('材料', components.material, '', '研读'),
        metric('任务', components.task, '', '作业考试', 'primary'),
        metric('互动', components.interaction, '', '求助讨论', 'link'),
        metric('证书', Array.isArray(progress.certificates) ? progress.certificates.length : 0, '枚', '已点亮', 'success'),
        metric('排名', currentPosition.rank, '', currentPosition.top_percent ? `前 ${currentPosition.top_percent}%` : '', 'accent'),
      ],
      stages,
      primaryAction,
      primaryActionLabel: Object.keys(eligibleStage).length
        ? '生成试炼'
        : toText(nextStage.status) === 'in_exam'
          ? '继续试炼'
          : '查看修为',
      secondaryActionLabel: '打开详情',
    };
  }

  if (role === 'teacher' && Object.keys(overview).length) {
    const personalStats = asRecord(overview.personal_stage_exam_stats);
    const distribution = Array.isArray(overview.distribution) ? overview.distribution.map(normalizeStage) : [];
    return {
      role,
      mode: 'teacher',
      classOfferingId,
      title: '班级成长概览',
      subtitle: `${toCount(overview.student_count)} 名学生 · ${toCount(overview.active_student_count)} 人活跃`,
      score: toScore(overview.average_score),
      progressPercent: toCount(overview.student_count)
        ? Math.min(100, Math.round((toScore(overview.average_score) / 100) * 100))
        : 0,
      metrics: [
        metric('学生', overview.student_count, '人', `活跃 ${toCount(overview.active_student_count)}`, 'primary'),
        metric('待关注', overview.need_attention_count, '人', `低活跃 ${toCount(overview.quiet_student_count)}`, toCount(overview.need_attention_count) ? 'warning' : 'success'),
        metric('材料', overview.average_material_percent, '%', '班级均值', 'link'),
        metric('任务', overview.average_task_percent, '%', '完成均值'),
        metric('试炼', personalStats.active_count, '个', `${toCount(personalStats.total_count)} 次`, 'accent'),
      ],
      stages: distribution,
      primaryAction: 'open-learning-modal',
      primaryActionLabel: '成员进度',
      secondaryActionLabel: '同步名单',
    };
  }

  return {
    role,
    mode: 'none',
    classOfferingId,
    title: '学习进度',
    subtitle: '当前课堂暂无学习进度数据。',
    score: 0,
    progressPercent: 0,
    metrics: [],
    stages: [],
    primaryAction: 'open-learning-modal',
    primaryActionLabel: '查看',
    secondaryActionLabel: '详情',
  };
}

export function buildLearningProgressMessage(snapshot: LearningProgressSnapshot): string {
  if (snapshot.mode === 'student') {
    if (snapshot.primaryAction === 'start-stage-exam') {
      return '已达到破境条件，可以从原试炼入口生成个性化阶段考试。';
    }
    if (snapshot.primaryAction === 'continue-stage-exam') {
      return '阶段试炼已准备好，继续完成后由原考试流程提交和批改。';
    }
    return `当前修为 ${snapshot.score}，阶段进度 ${snapshot.progressPercent}%。`;
  }
  if (snapshot.mode === 'teacher') {
    const attention = snapshot.metrics.find((item) => item.label === '待关注')?.value || 0;
    if (attention > 0) {
      return `${attention} 名学生需要关注，成员详情仍从原班级成员弹窗进入。`;
    }
    return `班级平均修为 ${snapshot.score}，学习状态整体平稳。`;
  }
  return '当前课堂暂无学习进度数据。';
}

export function getLearningProgressReadiness(snapshot: LearningProgressSnapshot): number {
  if (snapshot.mode === 'student') {
    return snapshot.progressPercent;
  }
  if (snapshot.mode === 'teacher') {
    const material = snapshot.metrics.find((item) => item.label === '材料')?.value || 0;
    const task = snapshot.metrics.find((item) => item.label === '任务')?.value || 0;
    return Math.min(100, Math.round((snapshot.progressPercent + material + task) / 3));
  }
  return 0;
}
