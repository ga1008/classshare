import { describe, expect, it } from 'vitest';
import { classroomMaterialUrl, materialScope, parseClassroomDate, taskConstraintLabel, taskIsUrgent, taskMatchesFilter, taskPresentation, taskPreview, type ClassroomTask } from './classroom-workspace';

const now = Date.parse('2026-09-05T12:00:00+08:00');
const task = (patch: Partial<ClassroomTask> = {}): ClassroomTask => ({
  id: 1, title: '网络任务', kind: 'assignment', status: 'published', submissionStatus: 'unsubmitted',
  accepting: true, lateOpen: false, deadlinePhase: 'regular', countdownAt: '', serverNow: '',
  canResubmit: false, resubmissionDueAt: '', groupPending: false, ...patch,
});
describe('classroom workspace task semantics', () => {
  it('keeps the complete authorized collection reachable when no task is actionable', () => {
    const result = taskPreview([task({ submissionStatus: 'graded' }), task({ id: 2, accepting: false, deadlinePhase: 'closed' })], false, 4, now);
    expect(result.totalCount).toBe(2); expect(result.actionableCount).toBe(0); expect(result.rows).toEqual([]);
  });
  it('does not offer submission for a closed or unopened assignment', () => {
    expect(taskPresentation(task({ accepting: false, deadlinePhase: 'closed' }), false, now).action).toBe('查看要求');
    expect(taskPresentation(task({ accepting: false, deadlinePhase: 'none' }), false, now).status).toBe('尚未开放');
  });
  it('uses individual resubmission authorization and its own expiry', () => {
    const returned = task({ submissionStatus: 'returned', accepting: false, canResubmit: true, resubmissionDueAt: '2026-09-05T12:01:00+08:00' });
    expect(taskPresentation(returned, false, now).action).toBe('去重交');
    expect(taskPresentation(returned, false, now + 60000).actionable).toBe(false);
    expect(taskPresentation({ ...returned, canResubmit: false }, false, now).actionable).toBe(false);
  });
  it('distinguishes supplement, exam, grading and group result states', () => {
    expect(taskPresentation(task({ lateOpen: true }), false, now).action).toBe('去补交');
    expect(taskPresentation(task({ kind: 'exam' }), false, now).action).toBe('进入考试');
    expect(taskPresentation(task({ submissionStatus: 'grading' }), false, now).actionable).toBe(false);
    expect(taskPresentation(task({ submissionStatus: 'graded', groupPending: true }), false, now).status).toBe('小组结果待公布');
  });
  it('shows urgent overflow rather than implying the four preview rows are the whole queue', () => {
    const result = taskPreview(Array.from({ length: 7 }, (_, i) => task({ id: i + 1, countdownAt: '2026-09-05T15:00:00+08:00' })), false, 4, now);
    expect(result.rows).toHaveLength(4); expect(result.actionableCount).toBe(7); expect(result.urgentOverflow).toBe(3);
  });
  it('limits the urgent collection to actionable tasks with finite deadlines in the next 24 hours', () => {
    const cases = [
      task({ id: 1, countdownAt: '2026-09-05T13:00:00+08:00' }),
      task({ id: 2, countdownAt: '2026-09-06T12:00:00+08:00' }),
      task({ id: 3, countdownAt: '2026-09-06T12:00:01+08:00' }),
      task({ id: 4, countdownAt: '2026-09-05T12:00:00+08:00' }),
      task({ id: 5, countdownAt: '' }),
      task({ id: 6, countdownAt: 'unknown' }),
      task({ id: 7, accepting: false, countdownAt: '2026-09-05T13:00:00+08:00' }),
      task({ id: 8, submissionStatus: 'graded', countdownAt: '2026-09-05T13:00:00+08:00' }),
    ];
    expect(cases.filter(item => taskMatchesFilter(item, false, 'urgent', '', now)).map(item => item.id)).toEqual([1, 2]);
    expect(taskMatchesFilter(cases[0], false, 'urgent', '别的标题', now)).toBe(false);
    expect(taskPreview(cases, false, 1, now).urgentCount).toBe(2);
  });
  it('uses the personal resubmission window for both urgency and overflow filtering', () => {
    const returned = task({ submissionStatus: 'returned', canResubmit: true, resubmissionDueAt: '2026-09-05T12:01:00+08:00', countdownAt: '2026-09-08T12:00:00+08:00' });
    expect(taskIsUrgent(returned, false, now)).toBe(true);
    expect(taskMatchesFilter(returned, false, 'urgent', '', now)).toBe(true);
    expect(taskPreview([returned], false, 0, now).urgentOverflow).toBe(1);
    expect(taskMatchesFilter(returned, false, 'urgent', '', now + 60000)).toBe(false);
    expect(taskIsUrgent({ ...returned, resubmissionDueAt: '2026-09-07T12:00:00+08:00', countdownAt: '2026-09-05T12:01:00+08:00' }, false, now)).toBe(false);
  });
  it('prioritizes teacher grading while retaining drafts and history', () => {
    const result = taskPreview([task({ id: 1, status: 'new' }), task({ id: 2, pendingGrade: 3 }), task({ id: 3, grading: 2 })], true, 4, now);
    expect(result.rows.map(item => item.id)).toEqual([2, 1]); expect(result.totalCount).toBe(3);
  });
  it('searches titles in the full list and does not misclassify unopened tasks as closed', () => {
    expect(taskMatchesFilter(task({ accepting: false, deadlinePhase: 'none' }), false, 'closed', '')).toBe(false);
    expect(taskMatchesFilter(task({ submissionStatus: 'graded' }), false, 'submitted', '网络')).toBe(true);
    expect(taskMatchesFilter(task(), false, 'all', '另一个课程')).toBe(false);
  });
  it('interprets legacy naive dates as Shanghai and preserves Z timestamps', () => {
    expect(parseClassroomDate('2026-09-05 12:00:00')).toBe(now);
    expect(parseClassroomDate('2026-09-05T04:00:00Z')).toBe(now);
    expect(parseClassroomDate('2026-09-05T12:00:00+08:00')).toBe(now);
  });
  it('keeps supplement penalties visible before the student follows the action', () => {
    expect(taskConstraintLabel(task({ lateOpen: true, latePolicyLabel: '补交扣 10 分，最高 80 分' }))).toContain('最高 80 分');
  });
  it('prioritizes the effective deadline across ordinary, supplement and personal resubmission work', () => {
    const result = taskPreview([
      task({ id: 1, lateOpen: true, countdownAt: '2026-09-07T12:00:00+08:00' }),
      task({ id: 2, countdownAt: '2026-09-05T13:00:00+08:00' }),
      task({ id: 3, submissionStatus: 'returned', canResubmit: true, resubmissionDueAt: '2026-09-05T12:30:00+08:00', countdownAt: '2026-09-08T12:00:00+08:00' }),
    ], false, 4, now);
    expect(result.rows.map(item => item.id)).toEqual([3, 2, 1]);
    expect(taskPresentation(result.rows[2], false, now).status).toBe('补交开放中');
  });
});
describe('material relation scope', () => {
  it('uses home 0, real session ID and no academic-exam request', () => {
    expect(materialScope({ is_home_entry: true, id: -1 })).toBe(0);
    expect(materialScope({ id: 42 })).toBe(42);
    expect(materialScope({ id: 42, entry_type: 'academic_exam' })).toBeNull();
    expect(materialScope(null)).toBeNull();
  });
  it('carries classroom and lesson context to existing readers', () => {
    expect(classroomMaterialUrl('/materials/view/9', 7, 42)).toBe('/materials/view/9?class_offering_id=7&session_id=42');
    expect(classroomMaterialUrl('/materials/view/9', 7, 0)).toBe('/materials/view/9?class_offering_id=7');
    expect(classroomMaterialUrl('javascript:alert(1)', 7, 0)).toBe('');
  });
});
