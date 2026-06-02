import { describe, expect, it } from 'vitest';

import {
  buildMessageCenterWorkspaceMessage,
  canUsePrivateWorkspace,
  getPrimaryMessageCenterMetric,
  normalizeMessageCenterWorkspaceSnapshot,
} from '@/lib/message-center-workspace';

describe('message-center-workspace helpers', () => {
  it('normalizes missing or noisy payloads into a stable snapshot', () => {
    expect(
      normalizeMessageCenterWorkspaceSnapshot({
        mode: 'private',
        currentTab: 'private_message',
        unreadTotal: '7',
        currentTabUnread: -2,
        privateOpen: 'true',
        hasConversation: 1,
        canSend: true,
        filteredMessageTotal: '12',
      }),
    ).toMatchObject({
      mode: 'private',
      currentTab: 'private_message',
      unreadTotal: 7,
      currentTabUnread: 0,
      privateOpen: true,
      hasConversation: true,
      canSend: true,
      filteredMessageTotal: 12,
    });
  });

  it('builds priority messages for notifications and private conversations', () => {
    const notifications = normalizeMessageCenterWorkspaceSnapshot({
      unreadTotal: 4,
      currentTabUnread: 2,
    });
    expect(buildMessageCenterWorkspaceMessage(notifications)).toContain('4 条未读');

    const aiPending = normalizeMessageCenterWorkspaceSnapshot({
      privateOpen: true,
      hasConversation: true,
      aiPending: true,
      currentContactName: 'AI 助教',
    });
    expect(buildMessageCenterWorkspaceMessage(aiPending)).toContain('AI 助教正在回复');
  });

  it('chooses the primary metric by active workspace mode', () => {
    expect(
      getPrimaryMessageCenterMetric(
        normalizeMessageCenterWorkspaceSnapshot({ privateOpen: false, itemTotal: 8 }),
      ),
    ).toEqual({ label: '当前列表', value: 8 });

    expect(
      getPrimaryMessageCenterMetric(
        normalizeMessageCenterWorkspaceSnapshot({
          privateOpen: true,
          hasConversation: true,
          filteredMessageTotal: 5,
        }),
      ),
    ).toEqual({ label: '当前会话消息', value: 5 });
  });

  it('keeps notification-only profile mode from driving the embedded private panel directly', () => {
    expect(canUsePrivateWorkspace(normalizeMessageCenterWorkspaceSnapshot({ mode: 'notifications' }))).toBe(false);
    expect(canUsePrivateWorkspace(normalizeMessageCenterWorkspaceSnapshot({ mode: 'full' }))).toBe(true);
  });
});
