import { describe, expect, it } from 'vitest';

import {
  messageBellAriaLabel,
  messageBellCaption,
  normalizeMessageCenterResponse,
  normalizeMessageSummary,
  unreadCountText,
} from '@/lib/message-center-bell';

describe('message-center-sync helpers', () => {
  it('formats unread badges and accessible labels', () => {
    expect(unreadCountText(0)).toBe('0');
    expect(unreadCountText(120)).toBe('99+');
    expect(messageBellCaption(0)).toBe('通知与私信');
    expect(messageBellCaption(3)).toBe('未读 3 条');
    expect(messageBellAriaLabel(0)).toBe('打开通知中心');
    expect(messageBellAriaLabel(101)).toBe('打开通知中心，99+ 条未读消息');
  });

  it('normalizes the backend summary payload without trusting optional fields', () => {
    expect(
      normalizeMessageCenterResponse({
        summary: { unread_total: 4 },
        latest_unread: {
          id: 12,
          actor_display_name: '张老师',
          category_label: '通知',
          title: '新的作业反馈',
        },
      }),
    ).toEqual({
      summary: { unreadTotal: 4 },
      latestUnread: {
        id: 12,
        actorDisplayName: '张老师',
        categoryLabel: '通知',
        title: '新的作业反馈',
      },
    });
  });

  it('normalizes event summary payloads defensively', () => {
    expect(normalizeMessageSummary({ unread_total: '8' })).toEqual({ unreadTotal: 8 });
    expect(normalizeMessageSummary({ unread_total: -2 })).toEqual({ unreadTotal: 0 });
  });
});
