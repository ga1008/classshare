export type ClassroomTask = {
  id: number; title: string; kind: string; status: string; submissionStatus: string;
  accepting: boolean; lateOpen: boolean; deadlinePhase: string; countdownAt: string;
  serverNow: string; canResubmit: boolean; resubmissionDueAt: string; groupPending: boolean;
  pendingGrade?: number; grading?: number; returned?: number;
  latePolicyLabel?: string;
  startsAt?: string;
  createdAt?: string;
};

export type ClassroomSession = {
  id?: number; order_index?: string | number; entry_type?: string; is_home_entry?: boolean;
  is_academic_exam?: boolean; is_anchor?: boolean; detail_title?: string; title?: string;
  session_number_label?: string; session_date?: string;
  detail_meta?: string; segment_title?: string; session_status_label?: string;
};

export function parseClassroomDate(value: string): number {
  const normalized = value.includes('T') ? value : value.replace(' ', 'T');
  const result = Date.parse(/(?:Z|[+-]\d{2}:?\d{2})$/i.test(normalized) ? normalized : `${normalized}+08:00`);
  return Number.isFinite(result) ? result : Infinity;
}

export function taskPresentation(task: ClassroomTask, teacher: boolean, now = Date.now()) {
  if (teacher) {
    if ((task.pendingGrade || 0) > 0) return { actionable: true, rank: 0, status: `待批改 ${task.pendingGrade}`, action: '去批改' };
    if (task.status === 'new') return { actionable: true, rank: 3, status: '草稿', action: '继续编辑' };
    if ((task.grading || 0) > 0) return { actionable: false, rank: 4, status: `批改中 ${task.grading}`, action: '查看进度' };
    return { actionable: false, rank: 5, status: task.status === 'closed' || task.deadlinePhase === 'closed' ? '已截止' : '已发布', action: '查看任务' };
  }
  const resubmit = task.canResubmit && (!task.resubmissionDueAt || parseClassroomDate(task.resubmissionDueAt) > now);
  if (task.submissionStatus === 'returned') return resubmit
    ? { actionable: true, rank: 0, status: '待重交', action: '去重交' }
    : { actionable: false, rank: 5, status: '重交已关闭', action: '查看反馈' };
  if (task.submissionStatus === 'unsubmitted') {
    if (task.accepting) return { actionable: true, rank: task.lateOpen ? 1 : 2, status: task.lateOpen ? '补交开放中' : '未提交', action: task.lateOpen ? '去补交' : task.kind === 'exam' ? '进入考试' : '去提交' };
    return { actionable: false, rank: 5, status: task.status === 'closed' || task.deadlinePhase === 'closed' ? '已截止 · 未提交' : '尚未开放', action: '查看要求' };
  }
  if (resubmit) return { actionable: true, rank: 0, status: '可重新提交', action: '重新提交' };
  if (task.groupPending) return { actionable: false, rank: 4, status: '小组结果待公布', action: '查看状态' };
  if (task.submissionStatus === 'grading') return { actionable: false, rank: 4, status: '批改中', action: '查看提交' };
  if (task.submissionStatus === 'graded') return { actionable: false, rank: 5, status: '已批改', action: '查看结果' };
  return { actionable: false, rank: 4, status: '已提交', action: '查看提交' };
}

export function taskIsUrgent(task: ClassroomTask, teacher: boolean, now = Date.now()) {
  const due = parseClassroomDate(task.canResubmit ? task.resubmissionDueAt || task.countdownAt : task.countdownAt);
  return taskPresentation(task, teacher, now).actionable && Number.isFinite(due) && due > now && due - now <= 86400000;
}

export function taskMatchesFilter(task: ClassroomTask, teacher: boolean, filter: string, query: string, now = Date.now()) {
  const presentation = taskPresentation(task, teacher, now);
  const matches = filter === 'all' || (filter === 'actionable' && presentation.actionable)
    || (filter === 'urgent' && taskIsUrgent(task, teacher, now))
    || (filter === 'submitted' && ['submitted', 'grading', 'graded'].includes(task.submissionStatus))
    || (filter === 'closed' && (task.status === 'closed' || task.deadlinePhase === 'closed'))
    || (filter === 'draft' && task.status === 'new');
  return matches && task.title.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase());
}

export function taskDeadlineLabel(task: ClassroomTask) {
  const date = parseClassroomDate(task.canResubmit ? task.resubmissionDueAt || task.countdownAt : task.countdownAt);
  if (!Number.isFinite(date)) return '';
  return new Date(date).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false });
}

export function taskConstraintLabel(task: ClassroomTask) {
  return [task.lateOpen ? task.latePolicyLabel : '', task.groupPending ? '小组结果尚未公布' : ''].filter(Boolean).join(' · ');
}

export function taskPreview(tasks: ClassroomTask[], teacher: boolean, limit = 4, now = Date.now()) {
  const effectiveDue = (task: ClassroomTask) => parseClassroomDate(task.canResubmit ? task.resubmissionDueAt || task.countdownAt : task.countdownAt);
  const actionable = tasks.filter(task => taskPresentation(task, teacher, now).actionable)
    .sort((a, b) => (teacher ? taskPresentation(a, true, now).rank - taskPresentation(b, true, now).rank : 0)
      || effectiveDue(a) - effectiveDue(b)
      || taskPresentation(a, teacher, now).rank - taskPresentation(b, teacher, now).rank
      || b.id - a.id);
  const urgent = actionable.filter(task => taskIsUrgent(task, teacher, now));
  const rows = actionable.slice(0, limit);
  return { rows, actionableCount: actionable.length, totalCount: tasks.length,
    urgentCount: urgent.length, urgentOverflow: urgent.filter(task => !rows.includes(task)).length };
}

/** Academic exams have no material relation. Home is the explicit ID 0 contract. */
/** History uses creation time, never the deadline (which can change on resubmission). */
export function taskHistory(tasks: ClassroomTask[]) {
  const created = (task: ClassroomTask) => {
    const value = parseClassroomDate(task.createdAt || '');
    return Number.isFinite(value) ? value : -Infinity;
  };
  return [...tasks].sort((a, b) => {
    const first = created(a), second = created(b);
    return first === second ? b.id - a.id : first > second ? -1 : 1;
  });
}
