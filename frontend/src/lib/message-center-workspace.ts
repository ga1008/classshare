export const MESSAGE_CENTER_WORKSPACE_EVENT = 'lanshare:message-center-workspace-change';
export const MESSAGE_CENTER_WORKSPACE_COMMAND_EVENT = 'lanshare:message-center-workspace-command';

export type MessageCenterWorkspaceMode = 'full' | 'notifications' | 'private';

export type MessageCenterWorkspaceSnapshot = {
  mode: MessageCenterWorkspaceMode;
  currentTab: string;
  currentTabLabel: string;
  filterKey: string;
  filterLabel: string;
  keyword: string;
  unreadTotal: number;
  currentTabUnread: number;
  itemTotal: number;
  unreadItemTotal: number;
  contactTotal: number;
  visibleContactTotal: number;
  blockCount: number;
  privateOpen: boolean;
  hasConversation: boolean;
  currentContactName: string;
  currentContactSubtitle: string;
  currentContactUnread: number;
  canSend: boolean;
  isBlocked: boolean;
  aiPending: boolean;
  pendingAttachmentCount: number;
  filteredMessageTotal: number;
  isSendingMessage: boolean;
  sendCooldownSeconds: number;
};

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? (value as Record<string, unknown>) : {};
}

function toNumber(value: unknown, fallback = 0): number {
  const numberValue = Number(value);
  return Number.isFinite(numberValue) ? Math.max(0, numberValue) : fallback;
}

function toBoolean(value: unknown): boolean {
  return value === true || value === 1 || value === '1' || value === 'true';
}

function toText(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback;
}

function normalizeMode(value: unknown): MessageCenterWorkspaceMode {
  return value === 'notifications' || value === 'private' || value === 'full'
    ? value
    : 'full';
}

export function normalizeMessageCenterWorkspaceSnapshot(value: unknown): MessageCenterWorkspaceSnapshot {
  const record = asRecord(value);
  const currentTab = toText(record.currentTab, 'all');

  return {
    mode: normalizeMode(record.mode),
    currentTab,
    currentTabLabel: toText(record.currentTabLabel, currentTab === 'private_message' ? '私信' : '全部'),
    filterKey: toText(record.filterKey, 'all'),
    filterLabel: toText(record.filterLabel, '全部'),
    keyword: toText(record.keyword),
    unreadTotal: toNumber(record.unreadTotal),
    currentTabUnread: toNumber(record.currentTabUnread),
    itemTotal: toNumber(record.itemTotal),
    unreadItemTotal: toNumber(record.unreadItemTotal),
    contactTotal: toNumber(record.contactTotal),
    visibleContactTotal: toNumber(record.visibleContactTotal),
    blockCount: toNumber(record.blockCount),
    privateOpen: toBoolean(record.privateOpen),
    hasConversation: toBoolean(record.hasConversation),
    currentContactName: toText(record.currentContactName),
    currentContactSubtitle: toText(record.currentContactSubtitle),
    currentContactUnread: toNumber(record.currentContactUnread),
    canSend: toBoolean(record.canSend),
    isBlocked: toBoolean(record.isBlocked),
    aiPending: toBoolean(record.aiPending),
    pendingAttachmentCount: toNumber(record.pendingAttachmentCount),
    filteredMessageTotal: toNumber(record.filteredMessageTotal),
    isSendingMessage: toBoolean(record.isSendingMessage),
    sendCooldownSeconds: toNumber(record.sendCooldownSeconds),
  };
}

export function buildMessageCenterWorkspaceMessage(snapshot: MessageCenterWorkspaceSnapshot): string {
  if (snapshot.privateOpen) {
    if (!snapshot.hasConversation) {
      return snapshot.visibleContactTotal > 0
        ? '选择联系人后即可查看历史私信并继续沟通。'
        : '当前筛选下没有可见联系人，可以调整搜索或返回通知视图。';
    }
    if (snapshot.aiPending) {
      return 'AI 助教正在回复，完成后会自动同步到当前会话。';
    }
    if (!snapshot.canSend) {
      return snapshot.isBlocked ? '该联系人已在黑名单中，解除后才能继续发送。' : '当前会话只可查看，暂不能发送新消息。';
    }
    return snapshot.currentContactName
      ? `正在和 ${snapshot.currentContactName} 沟通，可继续发送文字或附件。`
      : '当前私信会话已打开，可以继续发送消息。';
  }

  if (snapshot.unreadTotal > 0) {
    return `还有 ${snapshot.unreadTotal} 条未读信息，当前分类包含 ${snapshot.currentTabUnread} 条未读。`;
  }
  if (snapshot.keyword) {
    return '搜索结果已按关键词收窄，可以清空关键词回到完整列表。';
  }
  return '当前没有未读信息，可以切换分类或进入私信会话。';
}

export function getPrimaryMessageCenterMetric(snapshot: MessageCenterWorkspaceSnapshot): {
  label: string;
  value: number;
} {
  if (snapshot.privateOpen) {
    return {
      label: snapshot.hasConversation ? '当前会话消息' : '可见联系人',
      value: snapshot.hasConversation ? snapshot.filteredMessageTotal : snapshot.visibleContactTotal,
    };
  }
  return {
    label: '当前列表',
    value: snapshot.itemTotal,
  };
}

export function canUsePrivateWorkspace(snapshot: MessageCenterWorkspaceSnapshot): boolean {
  return snapshot.mode !== 'notifications';
}
