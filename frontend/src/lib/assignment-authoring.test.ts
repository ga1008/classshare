import { describe, expect, it } from 'vitest';

import {
  buildAssignmentAuthoringMessage,
  getAssignmentAuthoringReadiness,
  getAssignmentModeLabel,
  getAssignmentScheduleLabel,
  normalizeAssignmentAuthoringSnapshot,
} from '@/lib/assignment-authoring';

describe('assignment authoring helpers', () => {
  it('normalizes authoring state and derives readiness', () => {
    const snapshot = normalizeAssignmentAuthoringSnapshot({
      assignmentId: 12,
      title: 'Week 1',
      completedChecks: 4,
      totalChecks: 5,
      gradingMode: 'ai',
      allowedFileTypes: '.pdf,.docx',
      scheduleMode: 'deadline',
      dueAt: '2026-06-10T12:00',
    });

    expect(snapshot.assignmentId).toBe('12');
    expect(snapshot.allowedFileTypes).toEqual(['.pdf', '.docx']);
    expect(getAssignmentAuthoringReadiness(snapshot)).toBe(80);
    expect(getAssignmentModeLabel(snapshot.gradingMode)).toBe('AI 辅助批改');
    expect(getAssignmentScheduleLabel(snapshot)).toBe('截止时间');
  });

  it('prioritizes actionable authoring messages', () => {
    expect(buildAssignmentAuthoringMessage(normalizeAssignmentAuthoringSnapshot({ isSaving: true }))).toBe('正在沿用原发布链路保存作业。');
    expect(buildAssignmentAuthoringMessage(normalizeAssignmentAuthoringSnapshot({ lastError: '保存失败' }))).toBe('保存失败');
    expect(buildAssignmentAuthoringMessage(normalizeAssignmentAuthoringSnapshot({
      title: 'Week 1',
      lateSubmissionEnabled: true,
    }))).toContain('补交');
  });
});
