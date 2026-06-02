import { describe, expect, it } from 'vitest';

import {
  buildAssignmentSubmitStatus,
  formatUploadBytes,
  isResubmissionWindowOpen,
  normalizeAssignmentSubmitPayload,
  normalizeUploadSnapshot,
} from '@/lib/assignment-submit';

describe('assignment submit helpers', () => {
  it('normalizes template payloads with safe fallbacks', () => {
    expect(normalizeAssignmentSubmitPayload({
      assignmentId: '42',
      initialAccepting: true,
      canResubmitSubmission: true,
      resubmissionDueAt: '2026-06-03T08:00:00+08:00',
      actionHint: '请确认',
      submitLabel: '提交',
    })).toEqual({
      assignmentId: '42',
      initialAccepting: true,
      canResubmitSubmission: true,
      resubmissionDueAt: '2026-06-03T08:00:00+08:00',
      actionHint: '请确认',
      submitLabel: '提交',
    });

    expect(normalizeAssignmentSubmitPayload(null)).toMatchObject({
      assignmentId: '',
      initialAccepting: false,
      canResubmitSubmission: false,
      resubmissionDueAt: null,
    });
  });

  it('normalizes upload snapshots from current and legacy shapes', () => {
    expect(normalizeUploadSnapshot({ count: 2, totalBytes: 1536 })).toEqual({
      count: 2,
      totalBytes: 1536,
    });
    expect(normalizeUploadSnapshot({ entries: [{}, {}, {}] })).toEqual({
      count: 3,
      totalBytes: 0,
    });
  });

  it('checks resubmission windows against an explicit clock', () => {
    expect(isResubmissionWindowOpen(true, null, 1000)).toBe(true);
    expect(isResubmissionWindowOpen(false, null, 1000)).toBe(false);
    expect(isResubmissionWindowOpen(true, '2026-06-03T08:00:00+08:00', Date.parse('2026-06-02T08:00:00+08:00'))).toBe(true);
    expect(isResubmissionWindowOpen(true, '2026-06-01T08:00:00+08:00', Date.parse('2026-06-02T08:00:00+08:00'))).toBe(false);
  });

  it('builds human status for blocked, empty, partial, complete and resubmission states', () => {
    expect(buildAssignmentSubmitStatus({
      accepting: false,
      answeredCount: 1,
      totalAnswerCount: 1,
      uploadCount: 0,
      canResubmitSubmission: false,
    }).tone).toBe('danger');
    expect(buildAssignmentSubmitStatus({
      accepting: true,
      answeredCount: 0,
      totalAnswerCount: 2,
      uploadCount: 0,
      canResubmitSubmission: false,
    }).tone).toBe('muted');
    expect(buildAssignmentSubmitStatus({
      accepting: true,
      answeredCount: 1,
      totalAnswerCount: 2,
      uploadCount: 0,
      canResubmitSubmission: false,
    }).tone).toBe('info');
    expect(buildAssignmentSubmitStatus({
      accepting: true,
      answeredCount: 2,
      totalAnswerCount: 2,
      uploadCount: 1,
      canResubmitSubmission: false,
    }).tone).toBe('success');
    expect(buildAssignmentSubmitStatus({
      accepting: true,
      answeredCount: 1,
      totalAnswerCount: 2,
      uploadCount: 0,
      canResubmitSubmission: true,
    }).tone).toBe('warning');
  });

  it('formats upload byte counters for compact chips', () => {
    expect(formatUploadBytes(512)).toBe('512 B');
    expect(formatUploadBytes(1536)).toBe('1.5 KB');
    expect(formatUploadBytes(2 * 1024 * 1024)).toBe('2.0 MB');
  });
});
