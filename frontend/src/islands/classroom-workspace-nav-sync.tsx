import {
  ArrowUp,
  BookOpen,
  ClipboardList,
  Compass,
  FolderOpen,
  LayoutDashboard,
  MessageSquareText,
  PanelsTopLeft,
} from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';

import {
  buildClassroomWorkspaceNavMessage,
  CLASSROOM_WORKSPACE_NAV_COMMAND_EVENT,
  CLASSROOM_WORKSPACE_NAV_EVENT,
  getActiveWorkspaceNavItem,
  getWorkspaceActivityTotal,
  getWorkspaceRoleLabel,
  normalizeClassroomWorkspaceNavSnapshot,
  type ClassroomWorkspaceNavItem,
  type ClassroomWorkspaceNavSnapshot,
} from '@/lib/classroom-workspace-nav';
import { mountReactIslandsWhenReady } from '@/lib/mount-react-island';

function readInitialSnapshot() {
  return normalizeClassroomWorkspaceNavSnapshot(window.__LANSHARE_CLASSROOM_WORKSPACE_NAV__);
}

function sendWorkspaceNavCommand(type: string, detail: Record<string, unknown> = {}) {
  window.dispatchEvent(new CustomEvent(CLASSROOM_WORKSPACE_NAV_COMMAND_EVENT, {
    detail: { type, ...detail },
  }));
}

function NavItemIcon({ targetId }: { targetId: string }) {
  if (targetId.includes('assignment')) {
    return <ClipboardList size={15} aria-hidden="true" />;
  }
  if (targetId.includes('material')) {
    return <BookOpen size={15} aria-hidden="true" />;
  }
  if (targetId.includes('resource')) {
    return <FolderOpen size={15} aria-hidden="true" />;
  }
  if (targetId.includes('discussion') || targetId.includes('activity')) {
    return <MessageSquareText size={15} aria-hidden="true" />;
  }
  if (targetId.includes('timeline')) {
    return <PanelsTopLeft size={15} aria-hidden="true" />;
  }
  return <LayoutDashboard size={15} aria-hidden="true" />;
}

function WorkspaceNavButton({ item }: { item: ClassroomWorkspaceNavItem }) {
  return (
    <button
      className={`classroom-workspace-nav-sync__target ${item.isActive ? 'is-active' : ''}`}
      disabled={!item.exists}
      onClick={() => sendWorkspaceNavCommand('focus-section', { targetId: item.targetId })}
      type="button"
    >
      <span className="classroom-workspace-nav-sync__target-icon">
        <NavItemIcon targetId={item.targetId} />
      </span>
      <span>
        <strong>{item.label}</strong>
        {item.note ? <small>{item.note}</small> : null}
      </span>
    </button>
  );
}

function ClassroomWorkspaceNav({ snapshot }: { snapshot: ClassroomWorkspaceNavSnapshot }) {
  const activeItem = getActiveWorkspaceNavItem(snapshot);
  const message = buildClassroomWorkspaceNavMessage(snapshot);
  const visibleItems = snapshot.items.filter((item) => item.exists);
  const roleLabel = getWorkspaceRoleLabel(snapshot.role);
  const activityTotal = getWorkspaceActivityTotal(snapshot);
  const courseLine = [snapshot.courseName, snapshot.className, snapshot.semester].filter(Boolean).join(' · ');

  return (
    <section className="classroom-workspace-nav-sync" aria-live="polite" data-classroom-workspace-nav-sync>
      <div className="classroom-workspace-nav-sync__summary">
        <span className="classroom-workspace-nav-sync__eyebrow">
          <Compass size={14} aria-hidden="true" />
          课堂导航工作台
        </span>
        <h2>{activeItem ? activeItem.label : '课堂主页'}</h2>
        <p>{message}</p>
        <div className="classroom-workspace-nav-sync__chips">
          <span>{roleLabel}</span>
          {courseLine ? <span>{courseLine}</span> : null}
          <span>{visibleItems.length} 个入口</span>
          {activityTotal > 0 ? <span>{activityTotal} 条活动</span> : null}
        </div>
      </div>

      <div className="classroom-workspace-nav-sync__targets" aria-label="课堂区块">
        {visibleItems.map((item) => (
          <WorkspaceNavButton item={item} key={item.targetId} />
        ))}
      </div>

      <div className="classroom-workspace-nav-sync__actions" aria-label="课堂导航操作">
        {activeItem ? (
          <button type="button" onClick={() => sendWorkspaceNavCommand('focus-section', { targetId: activeItem.targetId })}>
            <Compass size={15} aria-hidden="true" />
            当前区块
          </button>
        ) : null}
        <button type="button" onClick={() => sendWorkspaceNavCommand('focus-top')}>
          <ArrowUp size={15} aria-hidden="true" />
          返回顶部
        </button>
      </div>
    </section>
  );
}

function ClassroomWorkspaceNavIsland() {
  const [snapshot, setSnapshot] = useState<ClassroomWorkspaceNavSnapshot>(() => readInitialSnapshot());
  const normalizedSnapshot = useMemo(
    () => normalizeClassroomWorkspaceNavSnapshot(snapshot),
    [snapshot],
  );

  useEffect(() => {
    const handleChange = (event: Event) => {
      const detail = event instanceof CustomEvent ? event.detail : null;
      setSnapshot(normalizeClassroomWorkspaceNavSnapshot(detail));
    };
    window.addEventListener(CLASSROOM_WORKSPACE_NAV_EVENT, handleChange);
    setSnapshot(readInitialSnapshot());
    return () => window.removeEventListener(CLASSROOM_WORKSPACE_NAV_EVENT, handleChange);
  }, []);

  return <ClassroomWorkspaceNav snapshot={normalizedSnapshot} />;
}

mountReactIslandsWhenReady({
  islandName: 'classroom-workspace-nav-sync',
  render: () => <ClassroomWorkspaceNavIsland />,
  getProps: () => ({}),
});
