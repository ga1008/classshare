import { describe, expect, it } from 'vitest';

import {
  buildClassroomWorkspaceNavMessage,
  getActiveWorkspaceNavItem,
  getWorkspaceActivityTotal,
  getWorkspaceRoleLabel,
  normalizeClassroomWorkspaceNavSnapshot,
} from '@/lib/classroom-workspace-nav';

describe('classroom workspace nav helpers', () => {
  it('normalizes snapshots and marks the active item from activeTargetId', () => {
    const snapshot = normalizeClassroomWorkspaceNavSnapshot({
      role: 'teacher',
      classOfferingId: 12,
      courseName: 'Web Engineering',
      className: 'JWS2302',
      activeTargetId: 'materials-panel',
      items: [
        { targetId: 'assignment-panel', label: '任务区', note: '作业与考试' },
        { targetId: 'materials-panel', label: '材料区', note: '课程文档' },
      ],
      activityCounts: { discussion: '4', resources: 3, invalid: -1 },
    });

    expect(snapshot.classOfferingId).toBe(12);
    expect(snapshot.items[1].isActive).toBe(true);
    expect(getActiveWorkspaceNavItem(snapshot)?.label).toBe('材料区');
    expect(getWorkspaceActivityTotal(snapshot)).toBe(7);
  });

  it('builds role-aware labels and concise status messages', () => {
    const snapshot = normalizeClassroomWorkspaceNavSnapshot({
      role: 'student',
      activeTargetId: 'assignment-panel',
      items: [
        { targetId: 'assignment-panel', label: '任务区', note: '提交与查看', isActive: true },
      ],
    });

    expect(getWorkspaceRoleLabel(snapshot.role)).toBe('学生视图');
    expect(buildClassroomWorkspaceNavMessage(snapshot)).toContain('任务区');
  });
});
