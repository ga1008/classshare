import { Download, FileText, Link2, RefreshCw, UploadCloud } from 'lucide-react';
import type { CSSProperties, ReactNode } from 'react';
import { useEffect, useMemo, useState } from 'react';

import { mountReactIslandsWhenReady } from '@/lib/mount-react-island';
import {
  buildResourceWorkspaceMessage,
  formatResourceBytes,
  getResourceReadinessPercent,
  normalizeResourceWorkspaceSnapshot,
  RESOURCE_WORKSPACE_COMMAND_EVENT,
  RESOURCE_WORKSPACE_EVENT,
  type ResourceWorkspaceSnapshot,
} from '@/lib/resource-workspace';

function readInitialSnapshot() {
  return normalizeResourceWorkspaceSnapshot(window.__LANSHARE_RESOURCE_WORKSPACE__);
}

function sendResourceCommand(type: string, detail: Record<string, unknown> = {}) {
  window.dispatchEvent(new CustomEvent(RESOURCE_WORKSPACE_COMMAND_EVENT, {
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
    <div className={`resource-workspace-sync__metric is-${tone}`}>
      <span>{icon}</span>
      <small>{label}</small>
      <strong>{value}</strong>
    </div>
  );
}

function ResourceWorkspace({ snapshot }: { snapshot: ResourceWorkspaceSnapshot }) {
  const message = buildResourceWorkspaceMessage(snapshot);
  const readiness = getResourceReadinessPercent(snapshot);
  const uploadActive = snapshot.upload.activeCount > 0;

  return (
    <section className="resource-workspace-sync" aria-live="polite" data-resource-workspace-sync>
      <div className="resource-workspace-sync__summary">
        <span className="resource-workspace-sync__eyebrow">
          <FileText size={14} aria-hidden="true" />
          课堂资源工作台
        </span>
        <div className="resource-workspace-sync__headline">
          <strong>{snapshot.totalFiles} 个资源</strong>
          <span>{formatResourceBytes(snapshot.totalBytes)} · {snapshot.downloadableFiles} 个可直接下载</span>
        </div>
        <p>{message}</p>
        <div className="resource-workspace-sync__progress" style={{ '--progress': `${uploadActive ? snapshot.upload.averagePercent : readiness}%` } as CSSProperties}>
          <span />
        </div>
      </div>

      <div className="resource-workspace-sync__metrics">
        <Metric icon={<FileText size={14} aria-hidden="true" />} label="详情" value={snapshot.withDescription} tone="primary" />
        <Metric icon={<Link2 size={14} aria-hidden="true" />} label="外链" value={snapshot.withOriginalLink} tone="link" />
        <Metric icon={<Download size={14} aria-hidden="true" />} label="受限" value={snapshot.blockedDownloads} tone={snapshot.blockedDownloads > 0 ? 'warning' : 'success'} />
        <Metric icon={<UploadCloud size={14} aria-hidden="true" />} label="上传" value={snapshot.upload.activeCount} tone={uploadActive ? 'primary' : 'neutral'} />
      </div>

      <div className="resource-workspace-sync__actions" aria-label="课堂资源操作">
        <button type="button" onClick={() => sendResourceCommand('refresh')} disabled={snapshot.isLoading}>
          <RefreshCw size={15} aria-hidden="true" />
          刷新
        </button>
        {snapshot.canUpload ? (
          <button type="button" onClick={() => sendResourceCommand('open-upload')}>
            <UploadCloud size={15} aria-hidden="true" />
            上传
          </button>
        ) : null}
        <button type="button" onClick={() => sendResourceCommand('focus-list')}>
          <FileText size={15} aria-hidden="true" />
          列表
        </button>
      </div>
    </section>
  );
}

function ResourceWorkspaceIsland() {
  const [snapshot, setSnapshot] = useState<ResourceWorkspaceSnapshot>(() => readInitialSnapshot());
  const normalizedSnapshot = useMemo(
    () => normalizeResourceWorkspaceSnapshot(snapshot),
    [snapshot],
  );

  useEffect(() => {
    const handleChange = (event: Event) => {
      const detail = event instanceof CustomEvent ? event.detail : null;
      setSnapshot(normalizeResourceWorkspaceSnapshot(detail));
    };
    window.addEventListener(RESOURCE_WORKSPACE_EVENT, handleChange);
    setSnapshot(readInitialSnapshot());
    return () => window.removeEventListener(RESOURCE_WORKSPACE_EVENT, handleChange);
  }, []);

  return <ResourceWorkspace snapshot={normalizedSnapshot} />;
}

mountReactIslandsWhenReady({
  islandName: 'resource-workspace-sync',
  render: () => <ResourceWorkspaceIsland />,
  getProps: () => ({}),
});
