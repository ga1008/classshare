import { Bell, Bot, CalendarClock, CheckCircle2, ClipboardList, FileType2, Save } from 'lucide-react';
import type { CSSProperties } from 'react';
import { useEffect, useMemo, useState } from 'react';

import {
  ASSIGNMENT_AUTHORING_COMMAND_EVENT,
  ASSIGNMENT_AUTHORING_EVENT,
  buildAssignmentAuthoringMessage,
  getAssignmentAuthoringReadiness,
  getAssignmentModeLabel,
  getAssignmentScheduleLabel,
  normalizeAssignmentAuthoringSnapshot,
  type AssignmentAuthoringSnapshot,
} from '@/lib/assignment-authoring';
import { mountReactIslandsWhenReady } from '@/lib/mount-react-island';

function readInitialSnapshot() {
  return normalizeAssignmentAuthoringSnapshot(window.__LANSHARE_ASSIGNMENT_AUTHORING__);
}

function sendAuthoringCommand(type: string, detail: Record<string, unknown> = {}) {
  window.dispatchEvent(new CustomEvent(ASSIGNMENT_AUTHORING_COMMAND_EVENT, {
    detail: { type, ...detail },
  }));
}

function AssignmentAuthoringIslandView({ snapshot }: { snapshot: AssignmentAuthoringSnapshot }) {
  const readiness = getAssignmentAuthoringReadiness(snapshot);
  const message = buildAssignmentAuthoringMessage(snapshot);
  const scheduleLabel = getAssignmentScheduleLabel(snapshot);
  const modeLabel = getAssignmentModeLabel(snapshot.gradingMode);
  const stageLabel = snapshot.learningStageLabel || '普通经验';
  const allowedTypesLabel = snapshot.allowedFileTypes.length ? `${snapshot.allowedFileTypes.length} 类附件` : '附件不限';

  return (
    <section className="assignment-authoring-sync" aria-live="polite" data-assignment-authoring-sync>
      <div className="assignment-authoring-sync__summary">
        <span className="assignment-authoring-sync__eyebrow">
          <ClipboardList size={14} aria-hidden="true" />
          作业发布检查
        </span>
        <div className="assignment-authoring-sync__headline">
          <strong>{snapshot.assignmentId ? '编辑作业' : '新建作业'}</strong>
          <span>{readiness}% 完整 · {snapshot.completedChecks}/{snapshot.totalChecks} 项</span>
        </div>
        <p>{message}</p>
        <div className="assignment-authoring-sync__progress" style={{ '--progress': `${readiness}%` } as CSSProperties}>
          <span />
        </div>
      </div>

      <div className="assignment-authoring-sync__chips" aria-label="作业发布配置">
        <button type="button" onClick={() => sendAuthoringCommand('focus-field', { fieldId: 'assignment-grading-mode' })}>
          {snapshot.gradingMode === 'ai' ? <Bot size={14} aria-hidden="true" /> : <CheckCircle2 size={14} aria-hidden="true" />}
          {modeLabel}
        </button>
        <button type="button" onClick={() => sendAuthoringCommand('focus-field', { fieldId: 'assignment-availability-mode' })}>
          <CalendarClock size={14} aria-hidden="true" />
          {scheduleLabel}
        </button>
        <button type="button" onClick={() => sendAuthoringCommand('focus-field', { fieldId: 'assignment-learning-stage-key' })}>
          <CheckCircle2 size={14} aria-hidden="true" />
          {stageLabel}
        </button>
        <button type="button" onClick={() => sendAuthoringCommand('focus-field', { fieldId: 'assignment-allowed-file-types' })}>
          <FileType2 size={14} aria-hidden="true" />
          {allowedTypesLabel}
        </button>
        <button type="button" onClick={() => sendAuthoringCommand('focus-field', { fieldId: 'assignment-send-email-notification' })}>
          <Bell size={14} aria-hidden="true" />
          {snapshot.sendEmailNotification ? '邮件通知' : '站内通知'}
        </button>
      </div>

      <div className="assignment-authoring-sync__actions" aria-label="作业编辑快捷操作">
        <button type="button" onClick={() => sendAuthoringCommand('focus-field', { fieldId: 'assignment-title' })}>
          标题
        </button>
        <button type="button" onClick={() => sendAuthoringCommand('focus-field', { fieldId: 'assignment-requirements' })}>
          要求
        </button>
        <button type="button" onClick={() => sendAuthoringCommand('focus-field', { fieldId: 'assignment-rubric' })}>
          评分
        </button>
        <button type="button" onClick={() => sendAuthoringCommand('save')} disabled={!snapshot.canSave || snapshot.isSaving}>
          <Save size={15} aria-hidden="true" />
          保存
        </button>
      </div>
    </section>
  );
}

function AssignmentAuthoringIsland() {
  const [snapshot, setSnapshot] = useState<AssignmentAuthoringSnapshot>(() => readInitialSnapshot());
  const normalizedSnapshot = useMemo(
    () => normalizeAssignmentAuthoringSnapshot(snapshot),
    [snapshot],
  );

  useEffect(() => {
    const handleChange = (event: Event) => {
      const detail = event instanceof CustomEvent ? event.detail : null;
      setSnapshot(normalizeAssignmentAuthoringSnapshot(detail));
    };
    window.addEventListener(ASSIGNMENT_AUTHORING_EVENT, handleChange);
    setSnapshot(readInitialSnapshot());
    return () => window.removeEventListener(ASSIGNMENT_AUTHORING_EVENT, handleChange);
  }, []);

  return <AssignmentAuthoringIslandView snapshot={normalizedSnapshot} />;
}

mountReactIslandsWhenReady({
  islandName: 'assignment-authoring-sync',
  render: () => <AssignmentAuthoringIsland />,
  getProps: () => ({}),
});
