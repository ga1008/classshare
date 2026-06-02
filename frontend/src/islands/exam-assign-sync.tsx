import { Bell, BookOpenCheck, CalendarClock, CheckCircle2, FileType2, RefreshCw, Send, TimerReset } from 'lucide-react';
import type { CSSProperties } from 'react';
import { useEffect, useMemo, useState } from 'react';

import {
  buildExamAssignMessage,
  EXAM_ASSIGN_COMMAND_EVENT,
  EXAM_ASSIGN_EVENT,
  getExamAssignReadiness,
  getExamLatePolicyLabel,
  getExamScheduleLabel,
  normalizeExamAssignSnapshot,
  type ExamAssignSnapshot,
} from '@/lib/exam-assign';
import { mountReactIslandsWhenReady } from '@/lib/mount-react-island';

function readInitialSnapshot() {
  return normalizeExamAssignSnapshot(window.__LANSHARE_EXAM_ASSIGN__);
}

function sendExamAssignCommand(type: string, detail: Record<string, unknown> = {}) {
  window.dispatchEvent(new CustomEvent(EXAM_ASSIGN_COMMAND_EVENT, {
    detail: { type, ...detail },
  }));
}

function ExamAssignIslandView({ snapshot }: { snapshot: ExamAssignSnapshot }) {
  const readiness = getExamAssignReadiness(snapshot);
  const message = buildExamAssignMessage(snapshot);
  const scheduleLabel = getExamScheduleLabel(snapshot);
  const latePolicyLabel = getExamLatePolicyLabel(snapshot);
  const stageLabel = snapshot.learningStageLabel || '普通考试';
  const allowedTypesLabel = snapshot.allowedFileTypes.length ? `${snapshot.allowedFileTypes.length} 类附件` : '附件不限';
  const selectedLabel = snapshot.selectedPaperTitle || (snapshot.paperCount ? '待选择试卷' : '暂无试卷');

  return (
    <section className="exam-assign-sync" aria-live="polite" data-exam-assign-sync>
      <div className="exam-assign-sync__summary">
        <span className="exam-assign-sync__eyebrow">
          <BookOpenCheck size={14} aria-hidden="true" />
          考试发布检查
        </span>
        <div className="exam-assign-sync__headline">
          <strong>{selectedLabel}</strong>
          <span>{readiness}% 完整 · {snapshot.completedChecks}/{snapshot.totalChecks} 项</span>
        </div>
        <p>{message}</p>
        <div className="exam-assign-sync__progress" style={{ '--progress': `${readiness}%` } as CSSProperties}>
          <span />
        </div>
      </div>

      <div className="exam-assign-sync__chips" aria-label="考试发布配置">
        <button type="button" onClick={() => sendExamAssignCommand('focus-list')}>
          <BookOpenCheck size={14} aria-hidden="true" />
          {snapshot.paperCount} 份试卷
        </button>
        <button type="button" onClick={() => sendExamAssignCommand('focus-field', { fieldId: 'exam-availability-mode' })}>
          <CalendarClock size={14} aria-hidden="true" />
          {scheduleLabel}
        </button>
        <button type="button" onClick={() => sendExamAssignCommand('focus-field', { fieldId: 'exam-late-submission-enabled' })}>
          <TimerReset size={14} aria-hidden="true" />
          {latePolicyLabel}
        </button>
        <button type="button" onClick={() => sendExamAssignCommand('focus-field', { fieldId: 'exam-learning-stage-key' })}>
          <CheckCircle2 size={14} aria-hidden="true" />
          {stageLabel}
        </button>
        <button type="button" onClick={() => sendExamAssignCommand('focus-field', { fieldId: 'exam-allowed-file-types' })}>
          <FileType2 size={14} aria-hidden="true" />
          {allowedTypesLabel}
        </button>
        <button type="button" onClick={() => sendExamAssignCommand('focus-field', { fieldId: 'exam-send-email-notification' })}>
          <Bell size={14} aria-hidden="true" />
          {snapshot.sendEmailNotification ? '邮件通知' : '站内通知'}
        </button>
      </div>

      <div className="exam-assign-sync__actions" aria-label="考试发布快捷操作">
        <button type="button" onClick={() => sendExamAssignCommand('reload-papers')} disabled={snapshot.isLoading || snapshot.isPublishing}>
          <RefreshCw size={15} aria-hidden="true" />
          刷新
        </button>
        <button type="button" onClick={() => sendExamAssignCommand('focus-list')}>
          选择试卷
        </button>
        <button type="button" onClick={() => sendExamAssignCommand('focus-field', { fieldId: 'exam-availability-mode' })}>
          时间
        </button>
        <button type="button" onClick={() => sendExamAssignCommand('publish')} disabled={!snapshot.canPublish}>
          <Send size={15} aria-hidden="true" />
          发布
        </button>
      </div>
    </section>
  );
}

function ExamAssignIsland() {
  const [snapshot, setSnapshot] = useState<ExamAssignSnapshot>(() => readInitialSnapshot());
  const normalizedSnapshot = useMemo(
    () => normalizeExamAssignSnapshot(snapshot),
    [snapshot],
  );

  useEffect(() => {
    const handleChange = (event: Event) => {
      const detail = event instanceof CustomEvent ? event.detail : null;
      setSnapshot(normalizeExamAssignSnapshot(detail));
    };
    window.addEventListener(EXAM_ASSIGN_EVENT, handleChange);
    setSnapshot(readInitialSnapshot());
    return () => window.removeEventListener(EXAM_ASSIGN_EVENT, handleChange);
  }, []);

  return <ExamAssignIslandView snapshot={normalizedSnapshot} />;
}

mountReactIslandsWhenReady({
  islandName: 'exam-assign-sync',
  render: () => <ExamAssignIsland />,
  getProps: () => ({}),
});
