import { describe, expect, it } from 'vitest';

import {
  buildActivityWorkspaceMessage,
  getActiveActivityItem,
  getActivityWorkspaceRoleLabel,
  normalizeClassroomActivityWorkspaceSnapshot,
} from '@/lib/classroom-activity-workspace';

describe('classroom activity workspace helpers', () => {
  it('normalizes counts and active item state', () => {
    const snapshot = normalizeClassroomActivityWorkspaceSnapshot({
      role: 'teacher',
      activeKey: 'discussion',
      liveTotal: '5',
      resourceTotal: 3,
      items: [
        { key: 'interaction', label: '互动', count: 2 },
        { key: 'discussion', label: '研讨', count: '3' },
        { key: 'resources', label: '资源', count: -1 },
      ],
    });

    expect(snapshot.total).toBe(8);
    expect(snapshot.items[1].isActive).toBe(true);
    expect(getActiveActivityItem(snapshot)?.label).toBe('研讨');
    expect(getActivityWorkspaceRoleLabel(snapshot.role)).toBe('教师活动台');
  });

  it('keeps empty snapshots safe for first render', () => {
    const snapshot = normalizeClassroomActivityWorkspaceSnapshot(null);

    expect(snapshot.activeKey).toBe('interaction');
    expect(snapshot.items).toEqual([]);
    expect(buildActivityWorkspaceMessage(snapshot)).toBe('课堂活动正在同步。');
  });
});
