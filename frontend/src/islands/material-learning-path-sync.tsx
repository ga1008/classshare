import {
  BookOpenCheck,
  CalendarClock,
  CheckCircle2,
  CircleAlert,
  ExternalLink,
  FileText,
  RefreshCw,
  Send,
  TimerReset,
} from 'lucide-react';
import type { CSSProperties, ReactNode } from 'react';
import { useEffect, useMemo, useState } from 'react';

import { mountReactIslandsWhenReady } from '@/lib/mount-react-island';
import {
  buildMaterialLearningPathMessage,
  getMaterialLearningFocusSession,
  getMaterialLearningQueue,
  getMaterialLearningReadiness,
  MATERIAL_LEARNING_PATH_COMMAND_EVENT,
  MATERIAL_LEARNING_PATH_EVENT,
  normalizeMaterialLearningPathSnapshot,
  type MaterialLearningPathSnapshot,
  type MaterialLearningSession,
} from '@/lib/material-learning-path';

function readInitialSnapshot() {
  return normalizeMaterialLearningPathSnapshot(window.__LANSHARE_MATERIAL_LEARNING_PATH__);
}

function sendMaterialPathCommand(type: string, detail: Record<string, unknown> = {}) {
  window.dispatchEvent(new CustomEvent(MATERIAL_LEARNING_PATH_COMMAND_EVENT, {
    detail: { type, ...detail },
  }));
}

function Metric({
  icon,
  label,
  value,
  tone = 'neutral',
  command = 'focus-materials',
}: {
  icon: ReactNode;
  label: string;
  value: string | number;
  tone?: string;
  command?: string;
}) {
  return (
    <button className={`material-learning-path-sync__metric is-${tone}`} type="button" onClick={() => sendMaterialPathCommand(command)}>
      <span>{icon}</span>
      <small>{label}</small>
      <strong>{value}</strong>
    </button>
  );
}

function SessionButton({ session }: { session: MaterialLearningSession }) {
  const meta = [
    session.label,
    session.statusLabel,
    session.isShifted ? '变动' : '',
    session.isAcademicExam ? '教务考试' : '',
  ].filter(Boolean).join(' · ');

  return (
    <button
      className={`material-learning-path-sync__session ${session.hasMaterial ? 'is-ready' : 'is-missing'} ${session.isSelected ? 'is-selected' : ''}`}
      type="button"
      onClick={() => sendMaterialPathCommand('focus-session', { orderIndex: session.orderIndex })}
    >
      <span className="material-learning-path-sync__session-icon">
        {session.hasMaterial ? <CheckCircle2 size={15} aria-hidden="true" /> : <CircleAlert size={15} aria-hidden="true" />}
      </span>
      <span className="material-learning-path-sync__session-copy">
        <strong>{session.title}</strong>
        <small>{meta || session.dateLabel || '时间轴节点'}</small>
      </span>
      <em>{session.hasMaterial ? '已配' : '待配'}</em>
    </button>
  );
}

function MaterialLearningPath({ snapshot }: { snapshot: MaterialLearningPathSnapshot }) {
  const message = buildMaterialLearningPathMessage(snapshot);
  const readiness = getMaterialLearningReadiness(snapshot);
  const focusSession = getMaterialLearningFocusSession(snapshot);
  const queue = getMaterialLearningQueue(snapshot);
  const canOpenCurrent = Boolean((snapshot.materialPanel.ready || focusSession?.hasMaterial) && !focusSession?.isAcademicExam);
  const headline = focusSession?.title || snapshot.materialPanel.name || '课程材料';
  const headlineMeta = [
    `${snapshot.summary.sessionCount} 个节点`,
    `${snapshot.summary.materialItemCount} 项材料`,
    snapshot.breadcrumbs || '',
  ].filter(Boolean).join(' · ');

  return (
    <section className="material-learning-path-sync" aria-live="polite" data-material-learning-path-sync>
      <div className="material-learning-path-sync__summary">
        <span className="material-learning-path-sync__eyebrow">
          <BookOpenCheck size={14} aria-hidden="true" />
          材料学习路径
        </span>
        <div className="material-learning-path-sync__headline">
          <strong>{headline}</strong>
          <span>{headlineMeta || `${readiness}% 完整度`}</span>
        </div>
        <p>{message}</p>
        <div className="material-learning-path-sync__progress" style={{ '--progress': `${readiness}%` } as CSSProperties}>
          <span />
        </div>
      </div>

      <div className="material-learning-path-sync__metrics" aria-label="材料学习路径指标">
        <Metric icon={<CalendarClock size={14} aria-hidden="true" />} label="课次" value={snapshot.summary.sessionCount} tone="primary" command="focus-timeline" />
        <Metric icon={<CheckCircle2 size={14} aria-hidden="true" />} label="已配文档" value={snapshot.summary.materialReadyCount} tone="success" command="focus-timeline" />
        <Metric icon={<FileText size={14} aria-hidden="true" />} label="材料" value={snapshot.summary.materialItemCount} tone="link" />
        <Metric icon={<BookOpenCheck size={14} aria-hidden="true" />} label="README" value={snapshot.summary.documentCount} tone="accent" />
        <Metric icon={<TimerReset size={14} aria-hidden="true" />} label="已选" value={snapshot.summary.selectionCount} tone={snapshot.summary.selectionCount > 0 ? 'primary' : 'neutral'} />
      </div>

      <div className="material-learning-path-sync__sessions" aria-label="材料课次队列">
        {queue.length ? (
          queue.map((session) => <SessionButton key={session.orderIndex || session.title} session={session} />)
        ) : (
          <button className="material-learning-path-sync__empty" type="button" onClick={() => sendMaterialPathCommand('focus-materials')}>
            <FileText size={15} aria-hidden="true" />
            定位材料目录
          </button>
        )}
      </div>

      <div className="material-learning-path-sync__actions" aria-label="材料学习路径操作">
        <button type="button" onClick={() => sendMaterialPathCommand('focus-timeline')}>
          <CalendarClock size={15} aria-hidden="true" />
          课次
        </button>
        <button type="button" onClick={() => sendMaterialPathCommand('open-current-material')} disabled={!canOpenCurrent}>
          <ExternalLink size={15} aria-hidden="true" />
          打开文档
        </button>
        <button type="button" onClick={() => sendMaterialPathCommand('open-home-material')} disabled={!snapshot.summary.homeMaterialReady}>
          <BookOpenCheck size={15} aria-hidden="true" />
          首页
        </button>
        <button type="button" onClick={() => sendMaterialPathCommand('refresh-materials')} disabled={snapshot.isLoadingMaterials}>
          <RefreshCw size={15} aria-hidden="true" />
          刷新
        </button>
        {snapshot.role === 'teacher' ? (
          <button type="button" onClick={() => sendMaterialPathCommand('generate-final-material')}>
            <Send size={15} aria-hidden="true" />
            期末材料
          </button>
        ) : null}
      </div>
    </section>
  );
}

function MaterialLearningPathIsland() {
  const [snapshot, setSnapshot] = useState<MaterialLearningPathSnapshot>(() => readInitialSnapshot());
  const normalizedSnapshot = useMemo(
    () => normalizeMaterialLearningPathSnapshot(snapshot),
    [snapshot],
  );

  useEffect(() => {
    const handleChange = (event: Event) => {
      const detail = event instanceof CustomEvent ? event.detail : null;
      setSnapshot(normalizeMaterialLearningPathSnapshot(detail));
    };
    window.addEventListener(MATERIAL_LEARNING_PATH_EVENT, handleChange);
    setSnapshot(readInitialSnapshot());
    return () => window.removeEventListener(MATERIAL_LEARNING_PATH_EVENT, handleChange);
  }, []);

  return <MaterialLearningPath snapshot={normalizedSnapshot} />;
}

mountReactIslandsWhenReady({
  islandName: 'material-learning-path-sync',
  render: () => <MaterialLearningPathIsland />,
  getProps: () => ({}),
});
