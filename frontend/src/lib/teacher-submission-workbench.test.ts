import { describe, expect, it } from 'vitest';

import {
  buildTeacherWorkbenchMessage,
  getActionNeededCount,
  getFilterLabel,
  getSubmissionRate,
  normalizeTeacherSubmissionWorkbenchSnapshot,
} from '@/lib/teacher-submission-workbench';

describe('teacher submission workbench helpers', () => {
  it('normalizes snapshots with defensive defaults', () => {
    const snapshot = normalizeTeacherSubmissionWorkbenchSnapshot({
      currentFilter: 'submitted',
      selectedCount: 2,
      stats: {
        totalStudents: 40,
        submitted: 30,
        pending: 5,
        averageScore: '86.5',
      },
    });

    expect(snapshot.currentFilter).toBe('submitted');
    expect(snapshot.selectedCount).toBe(2);
    expect(snapshot.stats.totalStudents).toBe(40);
    expect(snapshot.stats.averageScore).toBe(86.5);
    expect(snapshot.stats.unsubmitted).toBe(0);
  });

  it('calculates submission rate and action-needed counts', () => {
    const snapshot = normalizeTeacherSubmissionWorkbenchSnapshot({
      stats: {
        totalStudents: 20,
        submitted: 15,
        pending: 3,
        unsubmitted: 5,
        returned: 2,
      },
    });

    expect(getSubmissionRate(snapshot)).toBe(75);
    expect(getActionNeededCount(snapshot)).toBe(10);
  });

  it('builds teacher-facing workbench messages by priority', () => {
    expect(buildTeacherWorkbenchMessage(normalizeTeacherSubmissionWorkbenchSnapshot({
      stats: { pending: 1, unsubmitted: 2, returned: 3 },
    }))).toBe('1 份待批改，2 人未提交，3 人待重交。');

    expect(buildTeacherWorkbenchMessage(normalizeTeacherSubmissionWorkbenchSnapshot({
      stats: { grading: 4 },
    }))).toBe('4 份正在 AI 批改中，稍后刷新确认结果。');

    expect(buildTeacherWorkbenchMessage(normalizeTeacherSubmissionWorkbenchSnapshot({}))).toBe(
      '当前没有紧急处理项，可以抽查详情或查看成绩分布。',
    );
  });

  it('formats active filter labels including score ranges', () => {
    expect(getFilterLabel('submitted', null)).toBe('待批改');
    expect(getFilterLabel('all', 'fail')).toBe('分数：不及格');
  });
});
