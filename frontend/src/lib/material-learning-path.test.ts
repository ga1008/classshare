import { describe, expect, it } from 'vitest';

import {
  buildMaterialLearningPathMessage,
  getMaterialLearningFocusSession,
  getMaterialLearningQueue,
  getMaterialLearningReadiness,
  normalizeMaterialLearningPathSnapshot,
} from '@/lib/material-learning-path';

describe('material learning path helpers', () => {
  it('normalizes timeline and material metrics', () => {
    const snapshot = normalizeMaterialLearningPathSnapshot({
      role: 'teacher',
      selectedOrder: '2',
      sessionItems: [
        { orderIndex: 1, title: '首页', isHomeEntry: true, hasMaterial: true },
        { orderIndex: 2, title: '路由实验', hasMaterial: false, isSelected: true },
        { orderIndex: 3, title: '期末考试', isAcademicExam: true },
      ],
      materialItems: [
        { id: 1, name: 'README.md', previewSupported: true, downloadAllowed: true },
        { id: 2, name: '实验包', nodeType: 'folder', hasDocument: true },
        { id: 3, name: '受限文件', downloadAllowed: false },
      ],
    });

    expect(snapshot.summary.sessionCount).toBe(3);
    expect(snapshot.summary.materialReadyCount).toBe(1);
    expect(snapshot.summary.missingMaterialCount).toBe(1);
    expect(snapshot.summary.folderCount).toBe(1);
    expect(snapshot.summary.documentCount).toBe(2);
    expect(snapshot.summary.blockedDownloadCount).toBe(1);
    expect(getMaterialLearningReadiness(snapshot)).toBe(58);
    expect(getMaterialLearningFocusSession(snapshot)?.orderIndex).toBe('2');
  });

  it('prioritizes actionable messages and queue sessions', () => {
    const missing = normalizeMaterialLearningPathSnapshot({
      role: 'teacher',
      selectedOrder: '1',
      sessionItems: [
        { orderIndex: '1', title: '第一课', hasMaterial: true, isSelected: true },
        { orderIndex: '2', title: '第二课', hasMaterial: false },
        { orderIndex: '3', title: '调课', hasMaterial: true, isShifted: true },
      ],
      summary: { sessionCount: 3, materialReadyCount: 2, missingMaterialCount: 1 },
    });
    expect(buildMaterialLearningPathMessage(missing)).toContain('还没有绑定文档');
    expect(getMaterialLearningQueue(missing).map((item) => item.orderIndex)).toEqual(['1', '2', '3']);

    const ready = normalizeMaterialLearningPathSnapshot({
      role: 'student',
      selectedSession: { orderIndex: '1', title: '第一课', hasMaterial: true },
      materialPanel: { ready: true },
      summary: { sessionCount: 1, materialReadyCount: 1 },
    });
    expect(buildMaterialLearningPathMessage(ready)).toContain('直接进入阅读');
  });
});
