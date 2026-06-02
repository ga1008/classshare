export const CLASSROOM_WORKSPACE_NAV_EVENT = 'lanshare:classroom-workspace-nav-change';
export const CLASSROOM_WORKSPACE_NAV_COMMAND_EVENT = 'lanshare:classroom-workspace-nav-command';

export type ClassroomWorkspaceNavItem = {
  targetId: string;
  label: string;
  note: string;
  isActive: boolean;
  exists: boolean;
};

export type ClassroomWorkspaceNavSnapshot = {
  role: string;
  classOfferingId: number | string | null;
  courseName: string;
  className: string;
  semester: string;
  activeTargetId: string;
  items: ClassroomWorkspaceNavItem[];
  activityCounts: Record<string, number>;
};

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? (value as Record<string, unknown>) : {};
}

function toText(value: unknown, fallback = ''): string {
  if (typeof value === 'string') {
    return value;
  }
  if (typeof value === 'number' && Number.isFinite(value)) {
    return String(value);
  }
  return fallback;
}

function toCount(value: unknown): number {
  const count = Number(value);
  return Number.isFinite(count) && count >= 0 ? count : 0;
}

function normalizeNavItem(value: unknown, index: number): ClassroomWorkspaceNavItem {
  const record = asRecord(value);
  const targetId = toText(record.targetId, `section-${index + 1}`);
  return {
    targetId,
    label: toText(record.label, targetId),
    note: toText(record.note),
    isActive: record.isActive === true,
    exists: record.exists !== false,
  };
}

function normalizeActivityCounts(value: unknown): Record<string, number> {
  const record = asRecord(value);
  return Object.fromEntries(
    Object.entries(record).map(([key, count]) => [key, toCount(count)]),
  );
}

export function normalizeClassroomWorkspaceNavSnapshot(value: unknown): ClassroomWorkspaceNavSnapshot {
  const record = asRecord(value);
  const items = Array.isArray(record.items)
    ? record.items.map((item, index) => normalizeNavItem(item, index)).filter((item) => item.targetId)
    : [];
  const activeTargetId = toText(record.activeTargetId, items.find((item) => item.isActive)?.targetId || '');

  return {
    role: toText(record.role),
    classOfferingId: typeof record.classOfferingId === 'string' || typeof record.classOfferingId === 'number'
      ? record.classOfferingId
      : null,
    courseName: toText(record.courseName),
    className: toText(record.className),
    semester: toText(record.semester),
    activeTargetId,
    items: items.map((item) => ({
      ...item,
      isActive: item.isActive || (!!activeTargetId && item.targetId === activeTargetId),
    })),
    activityCounts: normalizeActivityCounts(record.activityCounts),
  };
}

export function getActiveWorkspaceNavItem(snapshot: ClassroomWorkspaceNavSnapshot): ClassroomWorkspaceNavItem | null {
  return snapshot.items.find((item) => item.targetId === snapshot.activeTargetId)
    || snapshot.items.find((item) => item.isActive)
    || snapshot.items[0]
    || null;
}

export function getWorkspaceActivityTotal(snapshot: ClassroomWorkspaceNavSnapshot): number {
  return Object.values(snapshot.activityCounts).reduce((total, count) => total + count, 0);
}

export function getWorkspaceRoleLabel(role: string): string {
  if (role === 'teacher') {
    return '教师视图';
  }
  if (role === 'student') {
    return '学生视图';
  }
  return '课堂视图';
}

export function buildClassroomWorkspaceNavMessage(snapshot: ClassroomWorkspaceNavSnapshot): string {
  const activeItem = getActiveWorkspaceNavItem(snapshot);
  if (!activeItem) {
    return '课堂入口正在加载。';
  }

  const note = activeItem.note ? `，${activeItem.note}` : '';
  if (snapshot.role === 'teacher') {
    return `正在查看${activeItem.label}${note}。`;
  }
  if (snapshot.role === 'student') {
    return `正在查看${activeItem.label}${note}。`;
  }
  return `当前位于${activeItem.label}${note}。`;
}
