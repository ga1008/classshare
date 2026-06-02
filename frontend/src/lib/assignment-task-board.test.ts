import { describe, expect, it } from 'vitest';

import {
  buildAssignmentTaskBoardMessage,
  getAssignmentTaskBoardReadiness,
  getAssignmentTaskFocusItem,
  getAssignmentTaskKindLabel,
  normalizeAssignmentTaskBoardSnapshot,
} from '@/lib/assignment-task-board';

describe('assignment task board helpers', () => {
  it('normalizes task items and derives summary fallback', () => {
    const snapshot = normalizeAssignmentTaskBoardSnapshot({
      role: 'teacher',
      items: [
        {
          id: 1,
          title: 'Exam',
          kind: 'exam',
          statusKey: 'published',
          stageLabel: '筑基',
          pendingGradeCount: 2,
          clock: { accepting: true },
        },
        {
          id: 2,
          title: 'Homework',
          kind: 'assignment',
          clock: { isUrgent: true, accepting: true },
          priority: 'urgent',
        },
      ],
    });

    expect(snapshot.items[0].id).toBe('1');
    expect(snapshot.summary.total).toBe(2);
    expect(snapshot.summary.examCount).toBe(1);
    expect(snapshot.summary.stageCount).toBe(1);
    expect(snapshot.summary.reviewQueue).toBe(2);
    expect(getAssignmentTaskBoardReadiness(snapshot.summary)).toBe(67);
    expect(getAssignmentTaskKindLabel(snapshot.items[0].kind)).toBe('考试');
  });

  it('prioritizes urgent and actionable messages', () => {
    const snapshot = normalizeAssignmentTaskBoardSnapshot({
      role: 'teacher',
      items: [
        { id: 'review', title: 'Review', priority: 'review', pendingGradeCount: 3 },
        { id: 'urgent', title: 'Urgent', priority: 'urgent', clock: { isUrgent: true } },
      ],
    });

    expect(getAssignmentTaskFocusItem(snapshot.items)?.id).toBe('urgent');
    expect(buildAssignmentTaskBoardMessage(snapshot)).toContain('即将截止');
    expect(buildAssignmentTaskBoardMessage(normalizeAssignmentTaskBoardSnapshot({
      role: 'teacher',
      summary: { total: 1, reviewQueue: 4 },
    }))).toContain('等待批改');
  });
});
