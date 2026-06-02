export const CLASSROOM_ACTIVITY_WORKSPACE_EVENT = 'lanshare:classroom-activity-workspace-change';
export const CLASSROOM_ACTIVITY_WORKSPACE_COMMAND_EVENT = 'lanshare:classroom-activity-workspace-command';

export type ClassroomActivityItem = {
  key: string;
  label: string;
  note: string;
  targetId: string;
  count: number;
  isActive: boolean;
  exists: boolean;
};

export type ClassroomActivityWorkspaceSnapshot = {
  role: string;
  activeKey: string;
  items: ClassroomActivityItem[];
  liveTotal: number;
  resourceTotal: number;
  total: number;
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
  return Number.isFinite(count) && count >= 0 ? Math.round(count) : 0;
}

function normalizeActivityItem(value: unknown, index: number): ClassroomActivityItem {
  const record = asRecord(value);
  const key = toText(record.key, `activity-${index + 1}`);
  return {
    key,
    label: toText(record.label, key),
    note: toText(record.note),
    targetId: toText(record.targetId),
    count: toCount(record.count),
    isActive: record.isActive === true,
    exists: record.exists !== false,
  };
}

export function normalizeClassroomActivityWorkspaceSnapshot(value: unknown): ClassroomActivityWorkspaceSnapshot {
  const record = asRecord(value);
  const items = Array.isArray(record.items)
    ? record.items.map((item, index) => normalizeActivityItem(item, index)).filter((item) => item.key)
    : [];
  const activeKey = toText(record.activeKey, items.find((item) => item.isActive)?.key || 'interaction');
  const liveTotal = toCount(record.liveTotal);
  const resourceTotal = toCount(record.resourceTotal);

  return {
    role: toText(record.role),
    activeKey,
    items: items.map((item) => ({
      ...item,
      isActive: item.isActive || item.key === activeKey,
    })),
    liveTotal,
    resourceTotal,
    total: toCount(record.total) || liveTotal + resourceTotal,
  };
}

export function getActiveActivityItem(snapshot: ClassroomActivityWorkspaceSnapshot): ClassroomActivityItem | null {
  return snapshot.items.find((item) => item.key === snapshot.activeKey)
    || snapshot.items.find((item) => item.isActive)
    || snapshot.items[0]
    || null;
}

export function getActivityWorkspaceRoleLabel(role: string): string {
  return role === 'teacher' ? '教师活动台' : '学生活动台';
}

export function buildActivityWorkspaceMessage(snapshot: ClassroomActivityWorkspaceSnapshot): string {
  const activeItem = getActiveActivityItem(snapshot);
  if (!activeItem) {
    return '课堂活动正在同步。';
  }
  if (activeItem.key === 'discussion') {
    return '研讨室和一对一消息保留原实时链路，可继续发送文字、图片和文件。';
  }
  if (activeItem.key === 'resources') {
    return '资源区继续使用原文件上传、刷新和资料详情链路。';
  }
  if (activeItem.key === 'collaboration') {
    return '协作区继续由原小组协作模块托管创建、刷新和成员操作。';
  }
  return '互动区继续由原互动模块托管创建、刷新和课堂信号同步。';
}
