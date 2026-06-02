export const RESOURCE_WORKSPACE_EVENT = 'lanshare:resource-workspace-change';
export const RESOURCE_WORKSPACE_COMMAND_EVENT = 'lanshare:resource-workspace-command';

export type ResourceUploadSummary = {
  activeCount: number;
  failedCount: number;
  completedCount: number;
  averagePercent: number;
};

export type ResourceWorkspaceSnapshot = {
  role: string;
  courseId: number | string | null;
  classOfferingId: number | string | null;
  totalFiles: number;
  totalBytes: number;
  withDescription: number;
  withOriginalLink: number;
  blockedDownloads: number;
  downloadableFiles: number;
  canUpload: boolean;
  upload: ResourceUploadSummary;
  activeFileId: number | string | null;
  activeFileName: string;
  isLoading: boolean;
  lastError: string;
};

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? (value as Record<string, unknown>) : {};
}

function toCount(value: unknown): number {
  const numberValue = Number(value);
  return Number.isFinite(numberValue) && numberValue >= 0 ? Math.round(numberValue) : 0;
}

function toText(value: unknown, fallback = ''): string {
  if (typeof value === 'string') {
    return value;
  }
  if (typeof value === 'number' && Number.isFinite(value)) {
    return String(value);
  }
  return fallback;
}

function toId(value: unknown): number | string | null {
  if (typeof value === 'string' || typeof value === 'number') {
    return value;
  }
  return null;
}

function normalizeUploadSummary(value: unknown): ResourceUploadSummary {
  const record = asRecord(value);
  return {
    activeCount: toCount(record.activeCount),
    failedCount: toCount(record.failedCount),
    completedCount: toCount(record.completedCount),
    averagePercent: Math.min(100, toCount(record.averagePercent)),
  };
}

export function normalizeResourceWorkspaceSnapshot(value: unknown): ResourceWorkspaceSnapshot {
  const record = asRecord(value);
  const totalFiles = toCount(record.totalFiles);
  const blockedDownloads = toCount(record.blockedDownloads);

  return {
    role: toText(record.role),
    courseId: toId(record.courseId),
    classOfferingId: toId(record.classOfferingId),
    totalFiles,
    totalBytes: toCount(record.totalBytes),
    withDescription: toCount(record.withDescription),
    withOriginalLink: toCount(record.withOriginalLink),
    blockedDownloads,
    downloadableFiles: toCount(record.downloadableFiles) || Math.max(totalFiles - blockedDownloads, 0),
    canUpload: record.canUpload === true,
    upload: normalizeUploadSummary(record.upload),
    activeFileId: toId(record.activeFileId),
    activeFileName: toText(record.activeFileName),
    isLoading: record.isLoading === true,
    lastError: toText(record.lastError),
  };
}

export function formatResourceBytes(bytes: number): string {
  if (!bytes) {
    return '0 B';
  }
  const units = ['B', 'KB', 'MB', 'GB'];
  let value = bytes;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  return `${value >= 10 || unitIndex === 0 ? value.toFixed(0) : value.toFixed(1)} ${units[unitIndex]}`;
}

export function getResourceReadinessPercent(snapshot: ResourceWorkspaceSnapshot): number {
  if (!snapshot.totalFiles) {
    return 0;
  }
  const described = Math.min(snapshot.withDescription, snapshot.totalFiles);
  const accessible = Math.max(snapshot.totalFiles - snapshot.blockedDownloads, 0);
  return Math.round(((described + accessible) / (snapshot.totalFiles * 2)) * 100);
}

export function buildResourceWorkspaceMessage(snapshot: ResourceWorkspaceSnapshot): string {
  if (snapshot.isLoading) {
    return '正在同步课堂资源列表。';
  }
  if (snapshot.lastError) {
    return snapshot.lastError;
  }
  if (snapshot.upload.activeCount > 0) {
    return `${snapshot.upload.activeCount} 个文件正在上传，平均进度 ${snapshot.upload.averagePercent}%。`;
  }
  if (!snapshot.totalFiles) {
    return snapshot.canUpload ? '当前课堂还没有共享资源，可以从这里上传。' : '当前课堂还没有可下载资源。';
  }
  if (snapshot.blockedDownloads > 0) {
    return `${snapshot.blockedDownloads} 个资源受下载限制，请优先查看教师提供的原始链接。`;
  }
  return `共 ${snapshot.totalFiles} 个资源，已覆盖 ${snapshot.withDescription} 个详情说明。`;
}
