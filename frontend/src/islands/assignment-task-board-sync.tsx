import { BookOpenCheck, CircleAlert, ClipboardList, Clock3, ExternalLink, LocateFixed, TimerReset } from 'lucide-react';
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

function Metric({
  icon,
  label,
  value,
  tone = 'neutral',
}: {
  icon: ReactNode;
  label: string;
  value: string | number;
  tone?: string;
}) {
  return (
    <button className={`assignment-task-board-sync__metric is-${tone}`} onClick={() => sendTaskBoardCommand('focus-board')} type="button">
      <span>{icon}</span>
      <small>{label}</small>
      <strong>{value}</strong>
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

  return (
    <section className="assignment-task-board-sync" aria-live="polite" data-assignment-task-board-sync>
      <div className="assignment-task-board-sync__summary">
        <span className="assignment-task-board-sync__eyebrow">
          <ClipboardList size={14} aria-hidden="true" />
          任务主线
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

      <div className="assignment-task-board-sync__metrics" aria-label="课堂任务指标">
        <Metric icon={<ClipboardList size={14} aria-hidden="true" />} label="作业" value={snapshot.summary.assignmentCount} tone="primary" />
        <Metric icon={<BookOpenCheck size={14} aria-hidden="true" />} label="考试" value={snapshot.summary.examCount} tone="exam" />
        <Metric icon={<Clock3 size={14} aria-hidden="true" />} label="临近" value={snapshot.summary.urgentCount} tone={snapshot.summary.urgentCount > 0 ? 'warning' : 'neutral'} />
        <Metric icon={<TimerReset size={14} aria-hidden="true" />} label="补交" value={snapshot.summary.lateOpenCount} tone={snapshot.summary.lateOpenCount > 0 ? 'warning' : 'neutral'} />
        <Metric icon={<CircleAlert size={14} aria-hidden="true" />} label={snapshot.role === 'teacher' ? '待批' : '待办'} value={snapshot.role === 'teacher' ? snapshot.summary.reviewQueue : snapshot.summary.unsubmittedCount + snapshot.summary.returnedCount} tone="danger" />
      </div>

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
