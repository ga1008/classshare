export const MATERIAL_LEARNING_PATH_EVENT = 'lanshare:material-learning-path-change';
export const MATERIAL_LEARNING_PATH_COMMAND_EVENT = 'lanshare:material-learning-path-command';

export type MaterialLearningSession = {
  id: number | string | null;
  orderIndex: string;
  title: string;
  label: string;
  statusLabel: string;
  progressState: string;
  entryType: string;
  hasMaterial: boolean;
  isSelected: boolean;
  isHomeEntry: boolean;
  isAcademicExam: boolean;
  isAcademicSchedule: boolean;
  isShifted: boolean;
  materialName: string;
  materialPath: string;
  viewerUrl: string;
  dateLabel: string;
};

export type MaterialLearningItem = {
  id: string;
  name: string;
  path: string;
  nodeType: string;
  typeLabel: string;
  meta: string;
  previewSupported: boolean;
  downloadAllowed: boolean;
  hasDocument: boolean;
  primaryAction: string;
  actionText: string;
};

export type MaterialLearningSummary = {
  sessionCount: number;
  materialReadyCount: number;
  missingMaterialCount: number;
  homeMaterialReady: boolean;
  academicExamCount: number;
  shiftedCount: number;
  materialItemCount: number;
  folderCount: number;
  documentCount: number;
  blockedDownloadCount: number;
  selectionCount: number;
};

export type MaterialLearningPanelState = {
  ready: boolean;
  entryType: string;
  name: string;
  path: string;
  hint: string;
};

export type MaterialLearningPathSnapshot = {
  role: string;
  classOfferingId: number | string | null;
  selectedOrder: string;
  selectedSession: MaterialLearningSession | null;
  sessionItems: MaterialLearningSession[];
  materialItems: MaterialLearningItem[];
  summary: MaterialLearningSummary;
  materialPanel: MaterialLearningPanelState;
  breadcrumbs: string;
  isLoadingMaterials: boolean;
};

const EMPTY_SUMMARY: MaterialLearningSummary = {
  sessionCount: 0,
  materialReadyCount: 0,
  missingMaterialCount: 0,
  homeMaterialReady: false,
  academicExamCount: 0,
  shiftedCount: 0,
  materialItemCount: 0,
  folderCount: 0,
  documentCount: 0,
  blockedDownloadCount: 0,
  selectionCount: 0,
};

const EMPTY_PANEL: MaterialLearningPanelState = {
  ready: false,
  entryType: '',
  name: '',
  path: '',
  hint: '',
};

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? (value as Record<string, unknown>) : {};
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

function toCount(value: unknown): number {
  const count = Number(value);
  return Number.isFinite(count) && count >= 0 ? Math.round(count) : 0;
}

function toBoolean(value: unknown): boolean {
  return value === true || value === 1 || value === '1' || value === 'true';
}

function toDefaultTrueBoolean(value: unknown): boolean {
  if (value === false || value === 0 || value === '0' || value === 'false') {
    return false;
  }
  return true;
}

function toId(value: unknown): number | string | null {
  if (typeof value === 'string' || typeof value === 'number') {
    return value;
  }
  return null;
}

function normalizeSession(value: unknown): MaterialLearningSession {
  const record = asRecord(value);
  return {
    id: toId(record.id),
    orderIndex: toText(record.orderIndex),
    title: toText(record.title, '课次'),
    label: toText(record.label),
    statusLabel: toText(record.statusLabel),
    progressState: toText(record.progressState),
    entryType: toText(record.entryType),
    hasMaterial: toBoolean(record.hasMaterial),
    isSelected: toBoolean(record.isSelected),
    isHomeEntry: toBoolean(record.isHomeEntry),
    isAcademicExam: toBoolean(record.isAcademicExam),
    isAcademicSchedule: toBoolean(record.isAcademicSchedule),
    isShifted: toBoolean(record.isShifted),
    materialName: toText(record.materialName),
    materialPath: toText(record.materialPath),
    viewerUrl: toText(record.viewerUrl),
    dateLabel: toText(record.dateLabel),
  };
}

function normalizeMaterialItem(value: unknown): MaterialLearningItem {
  const record = asRecord(value);
  return {
    id: toText(record.id),
    name: toText(record.name, '未命名材料'),
    path: toText(record.path),
    nodeType: toText(record.nodeType),
    typeLabel: toText(record.typeLabel),
    meta: toText(record.meta),
    previewSupported: toBoolean(record.previewSupported),
    downloadAllowed: toDefaultTrueBoolean(record.downloadAllowed),
    hasDocument: toBoolean(record.hasDocument),
    primaryAction: toText(record.primaryAction),
    actionText: toText(record.actionText),
  };
}

function normalizeSummary(value: unknown, sessions: MaterialLearningSession[], materials: MaterialLearningItem[]): MaterialLearningSummary {
  const record = asRecord(value);
  if (Object.keys(record).length) {
    return {
      sessionCount: toCount(record.sessionCount),
      materialReadyCount: toCount(record.materialReadyCount),
      missingMaterialCount: toCount(record.missingMaterialCount),
      homeMaterialReady: toBoolean(record.homeMaterialReady),
      academicExamCount: toCount(record.academicExamCount),
      shiftedCount: toCount(record.shiftedCount),
      materialItemCount: toCount(record.materialItemCount),
      folderCount: toCount(record.folderCount),
      documentCount: toCount(record.documentCount),
      blockedDownloadCount: toCount(record.blockedDownloadCount),
      selectionCount: toCount(record.selectionCount),
    };
  }

  const eligibleSessions = sessions.filter((session) => !session.isAcademicExam);
  return {
    sessionCount: sessions.length,
    materialReadyCount: eligibleSessions.filter((session) => session.hasMaterial).length,
    missingMaterialCount: eligibleSessions.filter((session) => !session.hasMaterial).length,
    homeMaterialReady: sessions.some((session) => session.isHomeEntry && session.hasMaterial),
    academicExamCount: sessions.filter((session) => session.isAcademicExam).length,
    shiftedCount: sessions.filter((session) => session.isShifted).length,
    materialItemCount: materials.length,
    folderCount: materials.filter((item) => item.nodeType === 'folder').length,
    documentCount: materials.filter((item) => item.hasDocument || item.previewSupported).length,
    blockedDownloadCount: materials.filter((item) => !item.downloadAllowed).length,
    selectionCount: 0,
  };
}

function normalizePanel(value: unknown): MaterialLearningPanelState {
  const record = asRecord(value);
  return {
    ready: toBoolean(record.ready),
    entryType: toText(record.entryType),
    name: toText(record.name),
    path: toText(record.path),
    hint: toText(record.hint),
  };
}

export function normalizeMaterialLearningPathSnapshot(value: unknown): MaterialLearningPathSnapshot {
  const record = asRecord(value);
  const sessionItems = Array.isArray(record.sessionItems)
    ? record.sessionItems.map(normalizeSession).filter((session) => session.orderIndex || session.title)
    : [];
  const materialItems = Array.isArray(record.materialItems)
    ? record.materialItems.map(normalizeMaterialItem).filter((item) => item.id || item.name)
    : [];
  const selectedOrder = toText(record.selectedOrder);
  const selectedSession = record.selectedSession
    ? normalizeSession(record.selectedSession)
    : sessionItems.find((session) => session.orderIndex === selectedOrder || session.isSelected) || null;

  return {
    role: toText(record.role),
    classOfferingId: toId(record.classOfferingId),
    selectedOrder,
    selectedSession,
    sessionItems,
    materialItems,
    summary: normalizeSummary(record.summary, sessionItems, materialItems),
    materialPanel: normalizePanel(record.materialPanel),
    breadcrumbs: toText(record.breadcrumbs),
    isLoadingMaterials: toBoolean(record.isLoadingMaterials),
  };
}

export function getMaterialLearningEligibleSessionCount(snapshot: MaterialLearningPathSnapshot): number {
  const fromSummary = snapshot.summary.sessionCount - snapshot.summary.academicExamCount;
  if (fromSummary > 0) {
    return fromSummary;
  }
  return snapshot.sessionItems.filter((session) => !session.isAcademicExam).length;
}

export function getMaterialLearningReadiness(snapshot: MaterialLearningPathSnapshot): number {
  const eligibleSessions = getMaterialLearningEligibleSessionCount(snapshot);
  const sessionScore = eligibleSessions
    ? snapshot.summary.materialReadyCount / eligibleSessions
    : 0;
  const materialScore = snapshot.summary.materialItemCount
    ? (snapshot.summary.documentCount + Math.max(snapshot.summary.materialItemCount - snapshot.summary.blockedDownloadCount, 0)) / (snapshot.summary.materialItemCount * 2)
    : 0;
  const scoreCount = (eligibleSessions ? 1 : 0) + (snapshot.summary.materialItemCount ? 1 : 0);
  if (!scoreCount) {
    return 0;
  }
  return Math.min(100, Math.round(((sessionScore + materialScore) / scoreCount) * 100));
}

export function getMaterialLearningFocusSession(snapshot: MaterialLearningPathSnapshot): MaterialLearningSession | null {
  return snapshot.selectedSession
    || snapshot.sessionItems.find((session) => session.isSelected)
    || snapshot.sessionItems.find((session) => !session.isAcademicExam && !session.hasMaterial)
    || snapshot.sessionItems[0]
    || null;
}

export function getMaterialLearningQueue(snapshot: MaterialLearningPathSnapshot): MaterialLearningSession[] {
  const byOrder = new Map<string, MaterialLearningSession>();
  const focusSession = getMaterialLearningFocusSession(snapshot);
  if (focusSession?.orderIndex) {
    byOrder.set(focusSession.orderIndex, focusSession);
  }
  snapshot.sessionItems
    .filter((session) => !session.isAcademicExam && !session.hasMaterial)
    .forEach((session) => byOrder.set(session.orderIndex, session));
  snapshot.sessionItems
    .filter((session) => session.isShifted || session.isAcademicSchedule)
    .forEach((session) => byOrder.set(session.orderIndex, session));
  return Array.from(byOrder.values()).slice(0, 3);
}

export function buildMaterialLearningPathMessage(snapshot: MaterialLearningPathSnapshot): string {
  const focusSession = getMaterialLearningFocusSession(snapshot);
  if (snapshot.isLoadingMaterials) {
    return '正在同步课程材料目录。';
  }
  if (!snapshot.summary.sessionCount && !snapshot.summary.materialItemCount) {
    return snapshot.role === 'teacher'
      ? '当前课堂还没有可同步的课次或材料，请先完成课程计划和材料分配。'
      : '当前课堂还没有可打开的学习材料。';
  }
  if (focusSession?.isAcademicExam) {
    return '当前节点来自教务考试安排，学习文档入口会保持在课程首页和相邻课次。';
  }
  if (snapshot.role === 'teacher' && snapshot.summary.missingMaterialCount > 0) {
    return `${snapshot.summary.missingMaterialCount} 个非考试节点还没有绑定文档，可从时间轴选择材料或调用 AI 助教生成。`;
  }
  if (snapshot.materialPanel.ready || focusSession?.hasMaterial) {
    const label = focusSession?.isHomeEntry ? '课程首页' : '当前课次';
    return `${label}已关联学习文档，可以直接进入阅读。`;
  }
  if (snapshot.summary.blockedDownloadCount > 0) {
    return `${snapshot.summary.blockedDownloadCount} 个材料限制下载，建议优先使用在线阅读入口。`;
  }
  return `共 ${snapshot.summary.sessionCount} 个时间轴节点，材料目录包含 ${snapshot.summary.materialItemCount} 项。`;
}
