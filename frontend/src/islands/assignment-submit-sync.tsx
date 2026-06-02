import { AlertCircle, CheckCircle2, ClipboardList, FileUp, RotateCcw } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';

import {
  ASSIGNMENT_SUBMIT_AVAILABILITY_CHANGE_EVENT,
  ASSIGNMENT_UPLOAD_CHANGE_EVENT,
  buildAssignmentSubmitStatus,
  formatUploadBytes,
  isResubmissionWindowOpen,
  normalizeAssignmentSubmitPayload,
  normalizeUploadSnapshot,
  type AssignmentSubmitPayload,
  type AssignmentUploadSnapshot,
} from '@/lib/assignment-submit';
import { readIslandJsonPayload } from '@/lib/island-payload';
import { mountReactIslandsWhenReady } from '@/lib/mount-react-island';

type AnswerStats = {
  answeredCount: number;
  totalAnswerCount: number;
};

function readAnswerStats(): AnswerStats {
  const fields = Array.from(document.querySelectorAll<HTMLTextAreaElement>('.answer-textarea'));
  return {
    answeredCount: fields.filter((field) => field.value.trim().length > 0).length,
    totalAnswerCount: fields.length,
  };
}

function readInitialUploadSnapshot(): AssignmentUploadSnapshot {
  return normalizeUploadSnapshot(window.__LANSHARE_ASSIGNMENT_UPLOAD_SNAPSHOT__);
}

function useAssignmentSubmitSync(payload: AssignmentSubmitPayload) {
  const [answerStats, setAnswerStats] = useState<AnswerStats>(() => readAnswerStats());
  const [uploadSnapshot, setUploadSnapshot] = useState<AssignmentUploadSnapshot>(() => readInitialUploadSnapshot());
  const [accepting, setAccepting] = useState(() => (
    payload.initialAccepting
    || isResubmissionWindowOpen(payload.canResubmitSubmission, payload.resubmissionDueAt)
  ));

  useEffect(() => {
    const submitButton = document.getElementById('submit-btn') as HTMLButtonElement | null;
    const formContainer = submitButton?.closest<HTMLElement>('.answer-form-container');
    submitButton?.setAttribute('data-assignment-submit-managed', 'react');
    formContainer?.setAttribute('data-assignment-submit-managed', 'react');

    const updateAnswers = () => setAnswerStats(readAnswerStats());

    const handleInput = (event: Event) => {
      const target = event.target instanceof Element ? event.target : null;
      if (!target?.matches('.answer-textarea')) {
        return;
      }
      updateAnswers();
    };

    const handleUploadChange = (event: Event) => {
      const detail = event instanceof CustomEvent ? event.detail : null;
      setUploadSnapshot(normalizeUploadSnapshot(detail));
    };

    const handleAvailabilityChange = (event: Event) => {
      const detail = event instanceof CustomEvent && event.detail && typeof event.detail === 'object'
        ? (event.detail as Record<string, unknown>)
        : {};
      if (detail.assignmentId && String(detail.assignmentId) !== payload.assignmentId) {
        return;
      }
      if (typeof detail.isAccepting === 'boolean') {
        setAccepting(detail.isAccepting);
      }
    };

    const answerArea = document.getElementById('answer-area');
    const observer = new MutationObserver(updateAnswers);
    if (answerArea) {
      observer.observe(answerArea, { childList: true, subtree: true });
    }

    document.addEventListener('input', handleInput);
    window.addEventListener(ASSIGNMENT_UPLOAD_CHANGE_EVENT, handleUploadChange);
    window.addEventListener(ASSIGNMENT_SUBMIT_AVAILABILITY_CHANGE_EVENT, handleAvailabilityChange);
    updateAnswers();
    setUploadSnapshot(readInitialUploadSnapshot());

    return () => {
      observer?.disconnect();
      document.removeEventListener('input', handleInput);
      window.removeEventListener(ASSIGNMENT_UPLOAD_CHANGE_EVENT, handleUploadChange);
      window.removeEventListener(ASSIGNMENT_SUBMIT_AVAILABILITY_CHANGE_EVENT, handleAvailabilityChange);
      submitButton?.removeAttribute('data-assignment-submit-managed');
      formContainer?.removeAttribute('data-assignment-submit-managed');
    };
  }, [payload.assignmentId]);

  return { accepting, answerStats, uploadSnapshot };
}

function StatusIcon({ tone, canResubmitSubmission }: { tone: string; canResubmitSubmission: boolean }) {
  if (tone === 'danger') {
    return <AlertCircle aria-hidden="true" size={18} />;
  }
  if (canResubmitSubmission) {
    return <RotateCcw aria-hidden="true" size={18} />;
  }
  return <CheckCircle2 aria-hidden="true" size={18} />;
}

function AssignmentSubmitSyncIsland(payload: AssignmentSubmitPayload) {
  const { accepting, answerStats, uploadSnapshot } = useAssignmentSubmitSync(payload);
  const status = useMemo(() => buildAssignmentSubmitStatus({
    accepting,
    answeredCount: answerStats.answeredCount,
    totalAnswerCount: answerStats.totalAnswerCount,
    uploadCount: uploadSnapshot.count,
    canResubmitSubmission: payload.canResubmitSubmission,
  }), [accepting, answerStats.answeredCount, answerStats.totalAnswerCount, uploadSnapshot.count, payload.canResubmitSubmission]);

  return (
    <section
      className={`assignment-submit-sync assignment-submit-sync--${status.tone}`}
      aria-live="polite"
      data-assignment-submit-status={status.tone}
    >
      <div className="assignment-submit-sync__main">
        <span className="assignment-submit-sync__icon">
          <StatusIcon tone={status.tone} canResubmitSubmission={payload.canResubmitSubmission} />
        </span>
        <div>
          <strong>{status.title}</strong>
          <p>{status.description}</p>
        </div>
      </div>
      <div className="assignment-submit-sync__meta" aria-label="提交内容概览">
        <span>
          <ClipboardList aria-hidden="true" size={15} />
          {answerStats.totalAnswerCount > 0
            ? `${answerStats.answeredCount}/${answerStats.totalAnswerCount} 题`
            : '答题区准备中'}
        </span>
        <span>
          <FileUp aria-hidden="true" size={15} />
          {uploadSnapshot.count > 0
            ? `${uploadSnapshot.count} 个附件 · ${formatUploadBytes(uploadSnapshot.totalBytes)}`
            : '无附件'}
        </span>
      </div>
    </section>
  );
}

mountReactIslandsWhenReady({
  islandName: 'assignment-submit-sync',
  getProps: (mountPoint) =>
    normalizeAssignmentSubmitPayload(
      readIslandJsonPayload(mountPoint, '[data-assignment-submit-sync-payload]'),
    ),
  render: (props) => <AssignmentSubmitSyncIsland {...props} />,
});
