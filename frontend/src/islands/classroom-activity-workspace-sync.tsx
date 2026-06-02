import {
  FolderOpen,
  LayoutDashboard,
  MessageSquareText,
  PanelsTopLeft,
  RefreshCw,
  Users,
} from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';

import {
  buildActivityWorkspaceMessage,
  CLASSROOM_ACTIVITY_WORKSPACE_COMMAND_EVENT,
  CLASSROOM_ACTIVITY_WORKSPACE_EVENT,
  getActiveActivityItem,
  getActivityWorkspaceRoleLabel,
  normalizeClassroomActivityWorkspaceSnapshot,
  type ClassroomActivityItem,
  type ClassroomActivityWorkspaceSnapshot,
} from '@/lib/classroom-activity-workspace';
import { mountReactIslandsWhenReady } from '@/lib/mount-react-island';

function readInitialSnapshot() {
  return normalizeClassroomActivityWorkspaceSnapshot(window.__LANSHARE_CLASSROOM_ACTIVITY_WORKSPACE__);
}

function sendActivityCommand(type: string, detail: Record<string, unknown> = {}) {
  window.dispatchEvent(new CustomEvent(CLASSROOM_ACTIVITY_WORKSPACE_COMMAND_EVENT, {
    detail: { type, ...detail },
  }));
}

function ActivityIcon({ itemKey }: { itemKey: string }) {
  if (itemKey === 'discussion') {
    return <MessageSquareText size={15} aria-hidden="true" />;
  }
  if (itemKey === 'collaboration') {
    return <Users size={15} aria-hidden="true" />;
  }
  if (itemKey === 'resources') {
    return <FolderOpen size={15} aria-hidden="true" />;
  }
  return <LayoutDashboard size={15} aria-hidden="true" />;
}

function ActivityButton({ item }: { item: ClassroomActivityItem }) {
  return (
    <button
      className={`classroom-activity-workspace-sync__item ${item.isActive ? 'is-active' : ''}`}
      disabled={!item.exists}
      onClick={() => sendActivityCommand('open-activity', { key: item.key })}
      type="button"
    >
      <span className="classroom-activity-workspace-sync__item-icon">
        <ActivityIcon itemKey={item.key} />
      </span>
      <span className="classroom-activity-workspace-sync__item-copy">
        <strong>{item.label}</strong>
        {item.note ? <small>{item.note}</small> : null}
      </span>
      <em>{item.count}</em>
    </button>
  );
}

function ClassroomActivityWorkspace({ snapshot }: { snapshot: ClassroomActivityWorkspaceSnapshot }) {
  const activeItem = getActiveActivityItem(snapshot);
  const message = buildActivityWorkspaceMessage(snapshot);
  const roleLabel = getActivityWorkspaceRoleLabel(snapshot.role);
  const visibleItems = snapshot.items.filter((item) => item.exists);

  return (
    <section className="classroom-activity-workspace-sync" aria-live="polite" data-classroom-activity-workspace-sync>
      <div className="classroom-activity-workspace-sync__summary">
        <span className="classroom-activity-workspace-sync__eyebrow">
          <PanelsTopLeft size={14} aria-hidden="true" />
          {roleLabel}
        </span>
        <div className="classroom-activity-workspace-sync__headline">
          <strong>{activeItem ? activeItem.label : '课堂活动'}</strong>
          <span>{snapshot.liveTotal} 条实时 · {snapshot.resourceTotal} 份资源</span>
        </div>
        <p>{message}</p>
      </div>

      <div className="classroom-activity-workspace-sync__items" aria-label="课堂活动入口">
        {visibleItems.map((item) => (
          <ActivityButton item={item} key={item.key} />
        ))}
      </div>

      <div className="classroom-activity-workspace-sync__actions" aria-label="课堂活动操作">
        <button type="button" onClick={() => sendActivityCommand('focus-active')}>
          <PanelsTopLeft size={15} aria-hidden="true" />
          定位
        </button>
        <button type="button" onClick={() => sendActivityCommand('refresh-active')}>
          <RefreshCw size={15} aria-hidden="true" />
          刷新当前
        </button>
      </div>
    </section>
  );
}

function ClassroomActivityWorkspaceIsland() {
  const [snapshot, setSnapshot] = useState<ClassroomActivityWorkspaceSnapshot>(() => readInitialSnapshot());
  const normalizedSnapshot = useMemo(
    () => normalizeClassroomActivityWorkspaceSnapshot(snapshot),
    [snapshot],
  );

  useEffect(() => {
    const handleChange = (event: Event) => {
      const detail = event instanceof CustomEvent ? event.detail : null;
      setSnapshot(normalizeClassroomActivityWorkspaceSnapshot(detail));
    };
    window.addEventListener(CLASSROOM_ACTIVITY_WORKSPACE_EVENT, handleChange);
    setSnapshot(readInitialSnapshot());
    return () => window.removeEventListener(CLASSROOM_ACTIVITY_WORKSPACE_EVENT, handleChange);
  }, []);

  return <ClassroomActivityWorkspace snapshot={normalizedSnapshot} />;
}

mountReactIslandsWhenReady({
  islandName: 'classroom-activity-workspace-sync',
  render: () => <ClassroomActivityWorkspaceIsland />,
  getProps: () => ({}),
});
