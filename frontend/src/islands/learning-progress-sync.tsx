import { BookOpenCheck, CheckCircle2, CircleAlert, ClipboardList, ExternalLink, FileText, RefreshCw } from 'lucide-react';
import type { CSSProperties, ReactNode } from 'react';
import { useEffect, useMemo } from 'react';

import {
  buildLearningProgressMessage,
  getLearningProgressReadiness,
  LEARNING_PROGRESS_COMMAND_EVENT,
  normalizeLearningProgressSnapshot,
  type LearningProgressMetric,
  type LearningProgressSnapshot,
} from '@/lib/learning-progress';
import { mountReactIslandsWhenReady } from '@/lib/mount-react-island';

function readSnapshot() {
  return normalizeLearningProgressSnapshot(window.APP_CONFIG);
}

function sendLearningCommand(type: string) {
  window.dispatchEvent(new CustomEvent(LEARNING_PROGRESS_COMMAND_EVENT, {
    detail: { type },
  }));
}

function MetricCard({ metric }: { metric: LearningProgressMetric }) {
  const iconMap: Record<string, ReactNode> = {
    学生: <ClipboardList size={14} aria-hidden="true" />,
    材料: <FileText size={14} aria-hidden="true" />,
    任务: <BookOpenCheck size={14} aria-hidden="true" />,
    互动: <CheckCircle2 size={14} aria-hidden="true" />,
    证书: <CheckCircle2 size={14} aria-hidden="true" />,
    待关注: <CircleAlert size={14} aria-hidden="true" />,
    试炼: <BookOpenCheck size={14} aria-hidden="true" />,
  };
  return (
    <button className={`learning-progress-sync__metric is-${metric.tone}`} type="button" onClick={() => sendLearningCommand('open-learning-modal')}>
      <span>{iconMap[metric.label] || <CheckCircle2 size={14} aria-hidden="true" />}</span>
      <small>{metric.label}</small>
      <strong>{metric.value}{metric.suffix}</strong>
      {metric.note ? <em>{metric.note}</em> : null}
    </button>
  );
}

function StageDots({ snapshot }: { snapshot: LearningProgressSnapshot }) {
  const stages = snapshot.stages.slice(0, 6);
  if (!stages.length) {
    return null;
  }
  return (
    <div className="learning-progress-sync__stages" aria-label="学习阶段">
      {stages.map((stage) => (
        <button
          className={`learning-progress-sync__stage is-${stage.status || 'neutral'}`}
          type="button"
          key={stage.key || stage.shortName}
          onClick={() => sendLearningCommand('open-learning-modal')}
          title={stage.name || stage.shortName}
        >
          <span />
          <strong>{stage.shortName}</strong>
        </button>
      ))}
    </div>
  );
}

function LearningProgress({ snapshot }: { snapshot: LearningProgressSnapshot }) {
  const message = buildLearningProgressMessage(snapshot);
  const readiness = getLearningProgressReadiness(snapshot);
  const isTeacher = snapshot.mode === 'teacher';
  const hasPrimaryAction = snapshot.mode !== 'none';

  return (
    <section className={`learning-progress-sync is-${snapshot.mode}`} aria-live="polite" data-learning-progress-sync>
      <div className="learning-progress-sync__summary">
        <span className="learning-progress-sync__eyebrow">
          {isTeacher ? <ClipboardList size={14} aria-hidden="true" /> : <BookOpenCheck size={14} aria-hidden="true" />}
          {isTeacher ? '班级成长' : '修为进度'}
        </span>
        <div className="learning-progress-sync__headline">
          <strong>{snapshot.title}</strong>
          <span>{snapshot.subtitle}</span>
        </div>
        <p>{message}</p>
        <div className="learning-progress-sync__progress" style={{ '--progress': `${readiness}%` } as CSSProperties}>
          <span />
        </div>
      </div>

      <div className="learning-progress-sync__metrics" aria-label="学习进度指标">
        {snapshot.metrics.slice(0, 5).map((metric) => <MetricCard key={metric.label} metric={metric} />)}
      </div>

      <div className="learning-progress-sync__side">
        <StageDots snapshot={snapshot} />
        <div className="learning-progress-sync__actions" aria-label="学习进度操作">
          <button type="button" onClick={() => sendLearningCommand('open-learning-modal')} disabled={!hasPrimaryAction}>
            <ExternalLink size={15} aria-hidden="true" />
            {snapshot.primaryActionLabel}
          </button>
          {snapshot.mode === 'student' ? (
            <button type="button" onClick={() => sendLearningCommand(snapshot.primaryAction)} disabled={snapshot.primaryAction === 'open-learning-modal'}>
              <BookOpenCheck size={15} aria-hidden="true" />
              试炼
            </button>
          ) : null}
          {snapshot.mode === 'teacher' ? (
            <button type="button" onClick={() => sendLearningCommand('sync-exam-roster')}>
              <RefreshCw size={15} aria-hidden="true" />
              {snapshot.secondaryActionLabel}
            </button>
          ) : null}
        </div>
      </div>
    </section>
  );
}

function bindLearningProgressCommands() {
  if (window.__LANSHARE_LEARNING_PROGRESS_COMMANDS_BOUND__) {
    return;
  }
  window.__LANSHARE_LEARNING_PROGRESS_COMMANDS_BOUND__ = true;
  window.addEventListener(LEARNING_PROGRESS_COMMAND_EVENT, (event) => {
    const detail = event instanceof CustomEvent ? event.detail : null;
    const type = detail?.type;
    const openModal = () => {
      const trigger = document.querySelector('[data-learning-modal-open]');
      if (trigger instanceof HTMLElement) {
        trigger.click();
      }
    };
    if (type === 'open-learning-modal') {
      openModal();
    }
    if (type === 'start-stage-exam') {
      const stageButton = document.querySelector('.learning-stage-exam-btn');
      if (stageButton instanceof HTMLElement) {
        stageButton.click();
      } else {
        openModal();
      }
    }
    if (type === 'continue-stage-exam') {
      const continueLink = document.querySelector('.learning-stage-status-actions a[href^="/exam/take/"]');
      if (continueLink instanceof HTMLAnchorElement) {
        window.location.href = continueLink.href;
      } else {
        openModal();
      }
    }
    if (type === 'sync-exam-roster') {
      openModal();
      window.setTimeout(() => {
        const button = document.querySelector('[data-exam-roster-sync]');
        if (button instanceof HTMLElement) {
          button.click();
        }
      }, 120);
    }
  });
}

function LearningProgressIsland() {
  const snapshot = useMemo(() => readSnapshot(), []);
  useEffect(() => {
    bindLearningProgressCommands();
  }, []);
  return <LearningProgress snapshot={snapshot} />;
}

mountReactIslandsWhenReady({
  islandName: 'learning-progress-sync',
  render: () => <LearningProgressIsland />,
  getProps: () => ({}),
});
