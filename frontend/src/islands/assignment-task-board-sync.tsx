import { BookOpenCheck, CheckCircle2, CircleAlert, ClipboardList, Clock3, ExternalLink, LocateFixed, TimerReset } from 'lucide-react';
import type { CSSProperties, ReactNode } from 'react';
import { useEffect, useMemo, useState } from 'react';

import {
  ASSIGNMENT_TASK_BOARD_COMMAND_EVENT,
  ASSIGNMENT_TASK_BOARD_EVENT,
  buildAssignmentTaskBoardMessage,
  getAssignmentTaskBoardReadiness,
  getAssignmentTaskFocusItem,
  getAssignmentTaskKindLabel,
  normalizeAssignmentTaskBoardSnapshot,
  type AssignmentTaskBoardSnapshot,
  type AssignmentTaskItem,
} from '@/lib/assignment-task-board';
import { mountReactIslandsWhenReady } from '@/lib/mount-react-island';

function readInitialSnapshot() {
  return normalizeAssignmentTaskBoardSnapshot(window.__LANSHARE_ASSIGNMENT_TASK_BOARD__);
}

function sendTaskBoardCommand(type: string, detail: Record<string, unknown> = {}) {
  window.dispatchEvent(new CustomEvent(ASSIGNMENT_TASK_BOARD_COMMAND_EVENT, {
    detail: { type, ...detail },
  }));
}

type AssignmentMetricKey = 'total' | 'review' | 'returned' | 'open' | 'todo' | 'urgent' | 'completed';

type AssignmentMetric = {
  key: AssignmentMetricKey;
  icon: ReactNode;
  label: string;
  value: string | number;
  note: string;
  tone: string;
  description: string;
  actionLabel: string;
};

function countStudentCompletedItems(items: AssignmentTaskItem[]) {
  return items.filter((item) => ['submitted', 'grading', 'graded'].includes(item.statusKey)).length;
}

function countStudentGradedItems(items: AssignmentTaskItem[]) {
  return items.filter((item) => item.statusKey === 'graded').length;
}

function buildRoleMetrics(snapshot: AssignmentTaskBoardSnapshot): AssignmentMetric[] {
  const { summary } = snapshot;
  if (snapshot.role === 'teacher') {
    return [
      {
        key: 'total',
        icon: <ClipboardList size={15} aria-hidden="true" />,
        label: '全部任务',
        value: summary.total,
        note: `${summary.assignmentCount} 作业 · ${summary.examCount} 考试`,
        tone: 'primary',
        description: '课堂内由教师创建并分配的作业与考试，个人试炼不混入这里。',
        actionLabel: '查看任务列表',
      },
      {
        key: 'review',
        icon: <CircleAlert size={15} aria-hidden="true" />,
        label: '待批改',
        value: summary.reviewQueue,
        note: `批改中 ${summary.gradingQueue}`,
        tone: summary.reviewQueue > 0 ? 'danger' : 'neutral',
        description: '定位到有待批改提交的任务，进入详情后继续原有批改流程。',
        actionLabel: '定位待批改任务',
      },
      {
        key: 'returned',
        icon: <TimerReset size={15} aria-hidden="true" />,
        label: '待重交',
        value: summary.returnedCount,
        note: '学生需补交',
        tone: summary.returnedCount > 0 ? 'warning' : 'neutral',
        description: '定位到已退回或处于补交流程的任务，便于回看学生后续提交。',
        actionLabel: '定位待重交任务',
      },
      {
        key: 'open',
        icon: <CheckCircle2 size={15} aria-hidden="true" />,
        label: '已发布',
        value: summary.openCount,
        note: '学生可见',
        tone: 'success',
        description: '查看当前学生可进入的任务，包含进行中的作业和考试。',
        actionLabel: '定位已发布任务',
      },
    ];
  }

  const unsubmitted = summary.unsubmittedCount;
  const returned = summary.returnedCount;
  const todo = unsubmitted + returned;
  const completed = countStudentCompletedItems(snapshot.items);
  const graded = countStudentGradedItems(snapshot.items);
  return [
    {
      key: 'todo',
      icon: <CircleAlert size={15} aria-hidden="true" />,
      label: '待完成',
      value: todo,
      note: `未交 ${unsubmitted}`,
      tone: todo > 0 ? 'danger' : 'success',
      description: '定位还需要提交或重新提交的任务，优先处理这些卡片。',
      actionLabel: '定位待完成任务',
    },
    {
      key: 'returned',
      icon: <TimerReset size={15} aria-hidden="true" />,
      label: '待重交',
      value: returned,
      note: '教师退回',
      tone: returned > 0 ? 'warning' : 'neutral',
      description: '定位老师退回后需要再次完善的任务，进入后按原提交流程处理。',
      actionLabel: '定位待重交任务',
    },
    {
      key: 'urgent',
      icon: <Clock3 size={15} aria-hidden="true" />,
      label: '临近截止',
      value: summary.urgentCount,
      note: '优先处理',
      tone: summary.urgentCount > 0 ? 'warning' : 'neutral',
      description: '定位剩余时间较紧的任务，避免错过提交或补交窗口。',
      actionLabel: '定位临近任务',
    },
    {
      key: 'completed',
      icon: <CheckCircle2 size={15} aria-hidden="true" />,
      label: '已提交',
      value: completed,
      note: `已评分 ${graded}`,
      tone: 'success',
      description: '回看已经提交、批改中或已评分的任务，便于确认结果和反馈。',
      actionLabel: '定位已提交任务',
    },
  ];
}

function Metric({
  metric,
  isActive,
  onActivate,
}: {
  metric: AssignmentMetric;
  isActive: boolean;
  onActivate: (metric: AssignmentMetric) => void;
}) {
  return (
    <button
      aria-controls="assignment-task-board-metric-popover"
      aria-expanded={isActive}
      aria-pressed={isActive}
      className={`assignment-task-board-sync__metric is-${metric.tone} ${isActive ? 'is-active' : ''}`}
      data-metric-key={metric.key}
      onClick={() => onActivate(metric)}
      type="button"
    >
      <span className="assignment-task-board-sync__metric-icon">{metric.icon}</span>
      <span className="assignment-task-board-sync__metric-copy">
        <small>{metric.label}</small>
        <em>{metric.note}</em>
      </span>
      <strong>{metric.value}</strong>
    </button>
  );
}

function TaskButton({ item }: { item: AssignmentTaskItem }) {
  const kindLabel = getAssignmentTaskKindLabel(item.kind);
  const timeLabel = item.clock.value || item.clock.label || item.statusLabel || '查看详情';
  const meta = [
    kindLabel,
    item.stageLabel ? `试炼 ${item.stageLabel}` : '',
    item.pendingGradeCount > 0 ? `待批 ${item.pendingGradeCount}` : '',
    item.clock.lateOpen ? '补交中' : '',
  ].filter(Boolean).join(' · ');

  return (
    <div className={`assignment-task-board-sync__queue-item is-${item.priority}`}>
      <button type="button" onClick={() => sendTaskBoardCommand('focus-card', { assignmentId: item.id })}>
        <span className="assignment-task-board-sync__queue-icon">
          {item.kind === 'exam' ? <BookOpenCheck size={15} aria-hidden="true" /> : <ClipboardList size={15} aria-hidden="true" />}
        </span>
        <span className="assignment-task-board-sync__queue-copy">
          <strong>{item.title}</strong>
          <small>{meta || item.statusLabel || kindLabel}</small>
        </span>
        <em>{timeLabel}</em>
      </button>
      <button className="assignment-task-board-sync__open" type="button" onClick={() => sendTaskBoardCommand('open-card', { assignmentId: item.id })} aria-label={`打开${item.title}`}>
        <ExternalLink size={15} aria-hidden="true" />
      </button>
    </div>
  );
}

function pickQueueItems(snapshot: AssignmentTaskBoardSnapshot) {
  const byId = new Map<string, AssignmentTaskItem>();
  const focusItem = getAssignmentTaskFocusItem(snapshot.items);
  if (focusItem) {
    byId.set(focusItem.id, focusItem);
  }
  snapshot.items
    .filter((item) => ['urgent', 'late', 'review', 'returned', 'todo'].includes(item.priority))
    .forEach((item) => byId.set(item.id, item));
  return Array.from(byId.values()).slice(0, 3);
}

function AssignmentTaskBoard({ snapshot }: { snapshot: AssignmentTaskBoardSnapshot }) {
  const readiness = getAssignmentTaskBoardReadiness(snapshot.summary);
  const message = buildAssignmentTaskBoardMessage(snapshot);
  const queueItems = pickQueueItems(snapshot);
  const focusItem = getAssignmentTaskFocusItem(snapshot.items);
  const isTeacher = snapshot.role === 'teacher';
  const metrics = useMemo(() => buildRoleMetrics(snapshot), [snapshot]);
  const [activeMetricKey, setActiveMetricKey] = useState<AssignmentMetricKey | ''>('');
  const activeMetric = metrics.find((metric) => metric.key === activeMetricKey) || null;

  useEffect(() => {
    if (activeMetricKey && !metrics.some((metric) => metric.key === activeMetricKey)) {
      setActiveMetricKey('');
    }
  }, [activeMetricKey, metrics]);

  const activateMetric = (metric: AssignmentMetric) => {
    setActiveMetricKey(metric.key);
    sendTaskBoardCommand('focus-metric', { metricKey: metric.key });
  };

  return (
    <section className={`assignment-task-board-sync is-${isTeacher ? 'teacher' : 'student'}`} aria-live="polite" data-assignment-task-board-sync>
      {!isTeacher ? (
      <div className="assignment-task-board-sync__summary">
        <span className="assignment-task-board-sync__eyebrow">
          <ClipboardList size={14} aria-hidden="true" />
          学习清单
        </span>
        <div className="assignment-task-board-sync__headline">
          <strong>{focusItem ? focusItem.title : '课堂任务'}</strong>
          <span>{snapshot.summary.total} 个任务 · {readiness}% 活跃</span>
        </div>
        <p>{message}</p>
        <div className="assignment-task-board-sync__progress" style={{ '--progress': `${readiness}%` } as CSSProperties}>
          <span />
        </div>
      </div>
      ) : null}

      <div className="assignment-task-board-sync__metrics" aria-label="课堂任务指标">
        {metrics.map((metric) => (
          <Metric
            isActive={metric.key === activeMetric?.key}
            key={metric.key}
            metric={metric}
            onActivate={activateMetric}
          />
        ))}
      </div>

      {!isTeacher ? (
      <div className="assignment-task-board-sync__queue" aria-label="优先任务">
        {queueItems.length ? (
          queueItems.map((item) => <TaskButton item={item} key={item.id || item.title} />)
        ) : (
          <button className="assignment-task-board-sync__empty" type="button" onClick={() => sendTaskBoardCommand('focus-board')}>
            <LocateFixed size={15} aria-hidden="true" />
            定位任务列表
          </button>
        )}
      </div>
      ) : null}

      {activeMetric ? (
        <div className="assignment-task-board-sync__popover" id="assignment-task-board-metric-popover" role="status">
          <div>
            <span>{activeMetric.label}</span>
            <strong>{activeMetric.value}</strong>
            <p>{activeMetric.description}</p>
          </div>
          <button type="button" onClick={() => sendTaskBoardCommand('focus-metric', { metricKey: activeMetric.key })}>
            <LocateFixed size={15} aria-hidden="true" />
            {activeMetric.actionLabel}
          </button>
        </div>
      ) : null}
    </section>
  );
}

function AssignmentTaskBoardIsland() {
  const [snapshot, setSnapshot] = useState<AssignmentTaskBoardSnapshot>(() => readInitialSnapshot());
  const normalizedSnapshot = useMemo(
    () => normalizeAssignmentTaskBoardSnapshot(snapshot),
    [snapshot],
  );

  useEffect(() => {
    const handleChange = (event: Event) => {
      const detail = event instanceof CustomEvent ? event.detail : null;
      setSnapshot(normalizeAssignmentTaskBoardSnapshot(detail));
    };
    window.addEventListener(ASSIGNMENT_TASK_BOARD_EVENT, handleChange);
    setSnapshot(readInitialSnapshot());
    return () => window.removeEventListener(ASSIGNMENT_TASK_BOARD_EVENT, handleChange);
  }, []);

  return <AssignmentTaskBoard snapshot={normalizedSnapshot} />;
}

mountReactIslandsWhenReady({
  islandName: 'assignment-task-board-sync',
  render: () => <AssignmentTaskBoardIsland />,
  getProps: () => ({}),
});
