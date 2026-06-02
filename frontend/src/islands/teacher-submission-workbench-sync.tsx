import { Bot, CheckCircle2, ClipboardCheck, Filter, RefreshCw, RotateCcw, UserX } from 'lucide-react';
import type { CSSProperties } from 'react';
import { useEffect, useMemo, useState } from 'react';

import { mountReactIslandsWhenReady } from '@/lib/mount-react-island';
import {
  buildTeacherWorkbenchMessage,
  getActionNeededCount,
  getFilterLabel,
  getSubmissionRate,
  normalizeTeacherSubmissionWorkbenchSnapshot,
  TEACHER_SUBMISSION_WORKBENCH_EVENT,
  type TeacherSubmissionWorkbenchSnapshot,
} from '@/lib/teacher-submission-workbench';

function emptySnapshot() {
  return normalizeTeacherSubmissionWorkbenchSnapshot(window.__LANSHARE_TEACHER_SUBMISSION_WORKBENCH__);
}

function invokeWindowAction(actionName: string) {
  const action = (window as unknown as Record<string, unknown>)[actionName];
  if (typeof action === 'function') {
    void action();
  }
}

function setTeacherFilter(filter: string) {
  if (typeof window.setFilter === 'function') {
    window.setFilter(filter);
  }
  document.getElementById('submission-section')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function MetricCard({
  label,
  value,
  tone,
  onClick,
}: {
  label: string;
  value: number | string;
  tone: string;
  onClick?: () => void;
}) {
  const content = (
    <>
      <span>{label}</span>
      <strong>{value}</strong>
    </>
  );

  if (onClick) {
    return (
      <button className={`teacher-workbench-sync__metric teacher-workbench-sync__metric--${tone}`} onClick={onClick} type="button">
        {content}
      </button>
    );
  }

  return <div className={`teacher-workbench-sync__metric teacher-workbench-sync__metric--${tone}`}>{content}</div>;
}

function TeacherSubmissionWorkbench({ snapshot }: { snapshot: TeacherSubmissionWorkbenchSnapshot }) {
  const submitRate = getSubmissionRate(snapshot);
  const actionNeeded = getActionNeededCount(snapshot);
  const activeFilterLabel = getFilterLabel(snapshot.currentFilter, snapshot.scoreRangeFilter);
  const message = buildTeacherWorkbenchMessage(snapshot);
  const averageScore = snapshot.stats.averageScore == null ? '-' : snapshot.stats.averageScore.toFixed(1);
  const passRate = snapshot.stats.passRate == null ? '-' : `${snapshot.stats.passRate.toFixed(1)}%`;

  return (
    <section className="teacher-workbench-sync" aria-live="polite" data-teacher-workbench-sync>
      <div className="teacher-workbench-sync__hero">
        <div className="teacher-workbench-sync__eyebrow">批阅工作台</div>
        <div className="teacher-workbench-sync__headline">
          <strong>{actionNeeded}</strong>
          <span>项待处理</span>
        </div>
        <p>{message}</p>
        <div className="teacher-workbench-sync__chips">
          <span><Filter size={14} aria-hidden="true" />{activeFilterLabel}</span>
          <span>{snapshot.filteredEntries}/{snapshot.totalEntries} 条显示</span>
          {snapshot.selectedCount > 0 ? <span>{snapshot.selectedCount} 条已选</span> : null}
        </div>
      </div>

      <div className="teacher-workbench-sync__progress" style={{ '--progress': `${submitRate}%` } as CSSProperties}>
        <div className="teacher-workbench-sync__ring">
          <strong>{submitRate}%</strong>
          <span>提交率</span>
        </div>
        <div className="teacher-workbench-sync__bar" aria-hidden="true"><span /></div>
        <div className="teacher-workbench-sync__score">
          <span>平均分 <strong>{averageScore}</strong></span>
          <span>及格率 <strong>{passRate}</strong></span>
        </div>
      </div>

      <div className="teacher-workbench-sync__metrics">
        <MetricCard label="待批改" value={snapshot.stats.pending} tone="warning" onClick={() => setTeacherFilter('submitted')} />
        <MetricCard label="已批改" value={snapshot.stats.graded} tone="success" onClick={() => setTeacherFilter('graded')} />
        <MetricCard label="未提交" value={snapshot.stats.unsubmitted} tone="danger" onClick={() => setTeacherFilter('unsubmitted')} />
        <MetricCard label="待重交" value={snapshot.stats.returned} tone="primary" onClick={() => setTeacherFilter('returned')} />
      </div>

      <div className="teacher-workbench-sync__actions" aria-label="教师批阅快捷操作">
        <button type="button" onClick={() => invokeWindowAction('refreshSubmissions')}>
          <RefreshCw size={15} aria-hidden="true" />
          刷新
        </button>
        <button type="button" onClick={() => invokeWindowAction('aiGradeAll')} disabled={snapshot.aiReadyCount === 0}>
          <Bot size={15} aria-hidden="true" />
          AI 批改 {snapshot.aiReadyCount || ''}
        </button>
        <button type="button" onClick={() => invokeWindowAction('zeroUnsubmittedScores')} disabled={snapshot.zeroUnsubmittedCount === 0}>
          <UserX size={15} aria-hidden="true" />
          未交记 0 {snapshot.zeroUnsubmittedCount || ''}
        </button>
        <button type="button" onClick={() => invokeWindowAction('openWithdrawModalForSelected')} disabled={snapshot.selectedCount === 0}>
          <RotateCcw size={15} aria-hidden="true" />
          撤回已选 {snapshot.selectedCount || ''}
        </button>
        <button type="button" onClick={() => setTeacherFilter('all')}>
          <CheckCircle2 size={15} aria-hidden="true" />
          全部
        </button>
        {snapshot.aiBlockedCount > 0 ? (
          <span className="teacher-workbench-sync__note">
            <ClipboardCheck size={14} aria-hidden="true" />
            {snapshot.aiBlockedCount} 份因附件类型暂不进入 AI 批改
          </span>
        ) : null}
      </div>
    </section>
  );
}

function TeacherSubmissionWorkbenchIsland() {
  const [snapshot, setSnapshot] = useState<TeacherSubmissionWorkbenchSnapshot>(() => emptySnapshot());
  const normalizedSnapshot = useMemo(
    () => normalizeTeacherSubmissionWorkbenchSnapshot(snapshot),
    [snapshot],
  );

  useEffect(() => {
    const handleChange = (event: Event) => {
      const detail = event instanceof CustomEvent ? event.detail : null;
      setSnapshot(normalizeTeacherSubmissionWorkbenchSnapshot(detail));
    };
    window.addEventListener(TEACHER_SUBMISSION_WORKBENCH_EVENT, handleChange);
    setSnapshot(emptySnapshot());
    return () => window.removeEventListener(TEACHER_SUBMISSION_WORKBENCH_EVENT, handleChange);
  }, []);

  return <TeacherSubmissionWorkbench snapshot={normalizedSnapshot} />;
}

mountReactIslandsWhenReady({
  islandName: 'teacher-submission-workbench-sync',
  render: () => <TeacherSubmissionWorkbenchIsland />,
  getProps: () => ({}),
});
