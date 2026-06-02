export type MessageSummary = {
  unreadTotal: number;
};

export type MessageNotification = {
  id: number;
  actorDisplayName: string;
  categoryLabel: string;
  title: string;
};

export type MessageCenterResponse = {
  summary: MessageSummary;
  latestUnread: MessageNotification | null;
};

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? (value as Record<string, unknown>) : {};
}

function numberFrom(value: unknown, fallback = 0): number {
  const normalized = Number(value);
  return Number.isFinite(normalized) ? normalized : fallback;
}

function textFrom(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value.trim() : fallback;
}

export function unreadCountText(unreadTotal: number): string {
  return unreadTotal > 99 ? '99+' : String(Math.max(0, unreadTotal));
}

export function messageBellCaption(unreadTotal: number): string {
  return unreadTotal > 0 ? `未读 ${unreadCountText(unreadTotal)} 条` : '通知与私信';
}

export function messageBellAriaLabel(unreadTotal: number): string {
  return unreadTotal > 0
    ? `打开通知中心，${unreadCountText(unreadTotal)} 条未读消息`
    : '打开通知中心';
}

export function normalizeMessageCenterResponse(value: unknown): MessageCenterResponse {
  const response = asRecord(value);
  const summary = asRecord(response.summary);
  const latestUnread = response.latest_unread == null ? null : asRecord(response.latest_unread);

  return {
    summary: {
      unreadTotal: Math.max(0, numberFrom(summary.unread_total)),
    },
    latestUnread: latestUnread
      ? {
          id: Math.max(0, numberFrom(latestUnread.id)),
          actorDisplayName: textFrom(latestUnread.actor_display_name),
          categoryLabel: textFrom(latestUnread.category_label, '消息'),
          title: textFrom(latestUnread.title),
        }
      : null,
  };
}

export function normalizeMessageSummary(value: unknown): MessageSummary {
  const summary = asRecord(value);
  return {
    unreadTotal: Math.max(0, numberFrom(summary.unread_total)),
  };
}
