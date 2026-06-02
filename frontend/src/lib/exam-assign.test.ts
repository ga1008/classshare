import { describe, expect, it } from 'vitest';

import {
  buildExamAssignMessage,
  getExamAssignReadiness,
  getExamLatePolicyLabel,
  getExamScheduleLabel,
  normalizeExamAssignSnapshot,
} from '@/lib/exam-assign';

describe('exam assign helpers', () => {
  it('normalizes exam assign state and derives readiness', () => {
    const snapshot = normalizeExamAssignSnapshot({
      selectedPaperId: 9,
      selectedPaperTitle: 'Final',
      paperCount: 3,
      completedChecks: 3,
      totalChecks: 4,
      allowedFileTypes: '.zip,.py',
      scheduleMode: 'countdown',
      durationMinutes: 90,
      lateSubmissionEnabled: true,
      latePenaltyStrategy: 'gradient',
    });

    expect(snapshot.selectedPaperId).toBe('9');
    expect(snapshot.allowedFileTypes).toEqual(['.zip', '.py']);
    expect(getExamAssignReadiness(snapshot)).toBe(75);
    expect(getExamScheduleLabel(snapshot)).toBe('倒计时');
    expect(getExamLatePolicyLabel(snapshot)).toBe('梯度补交');
  });

  it('prioritizes actionable exam assign messages', () => {
    expect(buildExamAssignMessage(normalizeExamAssignSnapshot({ isPublishing: true }))).toBe('正在沿用原考试发布链路创建课堂考试。');
    expect(buildExamAssignMessage(normalizeExamAssignSnapshot({ isLoading: true }))).toContain('试卷库');
    expect(buildExamAssignMessage(normalizeExamAssignSnapshot({ lastError: '发布失败' }))).toBe('发布失败');
    expect(buildExamAssignMessage(normalizeExamAssignSnapshot({ paperCount: 0 }))).toContain('暂无');
    expect(buildExamAssignMessage(normalizeExamAssignSnapshot({
      paperCount: 2,
      selectedPaperId: 'paper-1',
      lateSubmissionEnabled: true,
    }))).toContain('补交');
  });
});
