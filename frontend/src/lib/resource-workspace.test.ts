import { describe, expect, it } from 'vitest';

import {
  buildResourceWorkspaceMessage,
  formatResourceBytes,
  getResourceReadinessPercent,
  normalizeResourceWorkspaceSnapshot,
} from '@/lib/resource-workspace';

describe('resource workspace helpers', () => {
  it('normalizes resource metrics and derives readiness', () => {
    const snapshot = normalizeResourceWorkspaceSnapshot({
      role: 'teacher',
      totalFiles: '4',
      totalBytes: 4096,
      withDescription: 2,
      withOriginalLink: 1,
      blockedDownloads: 1,
      canUpload: true,
      upload: { activeCount: 0 },
    });

    expect(snapshot.totalFiles).toBe(4);
    expect(snapshot.downloadableFiles).toBe(3);
    expect(formatResourceBytes(snapshot.totalBytes)).toBe('4.0 KB');
    expect(getResourceReadinessPercent(snapshot)).toBe(63);
    expect(buildResourceWorkspaceMessage(snapshot)).toContain('受下载限制');
  });

  it('prioritizes loading, error, and upload messages', () => {
    expect(buildResourceWorkspaceMessage(normalizeResourceWorkspaceSnapshot({ isLoading: true }))).toBe('正在同步课堂资源列表。');
    expect(buildResourceWorkspaceMessage(normalizeResourceWorkspaceSnapshot({ lastError: '加载失败' }))).toBe('加载失败');
    expect(buildResourceWorkspaceMessage(normalizeResourceWorkspaceSnapshot({
      totalFiles: 2,
      upload: { activeCount: 1, averagePercent: 42 },
    }))).toContain('42%');
  });
});
