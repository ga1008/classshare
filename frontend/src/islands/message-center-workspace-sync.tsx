import { Bell, CheckCheck, MessageCircle, RefreshCw, Search, Send, ShieldMinus } from 'lucide-react';
import type { MouseEvent } from 'react';
import { useEffect, useMemo, useState } from 'react';

import { mountReactIslandsWhenReady } from '@/lib/mount-react-island';
import {
  buildMessageCenterWorkspaceMessage,
  canUsePrivateWorkspace,
  getPrimaryMessageCenterMetric,
  MESSAGE_CENTER_WORKSPACE_COMMAND_EVENT,
  MESSAGE_CENTER_WORKSPACE_EVENT,
  normalizeMessageCenterWorkspaceSnapshot,
  type MessageCenterWorkspaceSnapshot,
} from '@/lib/message-center-workspace';

function readInitialSnapshot() {
  return normalizeMessageCenterWorkspaceSnapshot(window.__LANSHARE_MESSAGE_CENTER_WORKSPACE__);
}

function sendWorkspaceCommand(type: string, detail: Record<string, unknown> = {}) {
  window.dispatchEvent(new CustomEvent(MESSAGE_CENTER_WORKSPACE_COMMAND_EVENT, {
    detail: { type, ...detail },
  }));
}

function Metric({
  label,
  value,
  tone = 'neutral',
}: {
  label: string;
  value: number | string;
  tone?: string;
}) {
  return (
    <div className={`message-center-workspace-sync__metric is-${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function MessageCenterWorkspace({ snapshot }: { snapshot: MessageCenterWorkspaceSnapshot }) {
  const message = buildMessageCenterWorkspaceMessage(snapshot);
  const primaryMetric = getPrimaryMessageCenterMetric(snapshot);
  const privateDirect = canUsePrivateWorkspace(snapshot);
  const contactLabel = snapshot.currentContactName || '未选择';
  const contactMeta = snapshot.currentContactSubtitle || (snapshot.privateOpen ? '私信会话' : '通知列表');
  const privateHref = '/profile?section=private&tab=private_message#profile-message-center';
  const notificationsHref = '/profile?section=notifications#profile-message-center';

  const handlePrivateClick = (event: MouseEvent<HTMLAnchorElement>) => {
    if (!privateDirect) {
      return;
    }
    event.preventDefault();
    sendWorkspaceCommand('set-tab', { tab: 'private_message' });
  };

  const handleNotificationsClick = (event: MouseEvent<HTMLAnchorElement>) => {
    if (snapshot.mode !== 'private') {
      event.preventDefault();
      sendWorkspaceCommand('set-tab', { tab: 'all' });
    }
  };

  return (
    <section className="message-center-workspace-sync" aria-live="polite" data-message-center-workspace-sync>
      <div className="message-center-workspace-sync__summary">
        <span className="message-center-workspace-sync__eyebrow">
          <Bell size={14} aria-hidden="true" />
          {snapshot.privateOpen ? '私信工作台' : '通知工作台'}
        </span>
        <h2>{snapshot.privateOpen ? contactLabel : snapshot.currentTabLabel}</h2>
        <p>{message}</p>
        <div className="message-center-workspace-sync__chips">
          <span>{snapshot.filterLabel}</span>
          {snapshot.keyword ? <span>关键词：{snapshot.keyword}</span> : null}
          {snapshot.pendingAttachmentCount > 0 ? <span>待发附件 {snapshot.pendingAttachmentCount}</span> : null}
          {snapshot.isSendingMessage ? <span>发送中</span> : null}
          {snapshot.sendCooldownSeconds > 0 ? <span>{snapshot.sendCooldownSeconds}s 后可发送</span> : null}
        </div>
      </div>

      <div className="message-center-workspace-sync__metrics">
        <Metric label={primaryMetric.label} value={primaryMetric.value} tone="primary" />
        <Metric label="总未读" value={snapshot.unreadTotal} tone={snapshot.unreadTotal > 0 ? 'danger' : 'success'} />
        <Metric label="联系人" value={`${snapshot.visibleContactTotal}/${snapshot.contactTotal}`} tone="contact" />
        <Metric label="黑名单" value={snapshot.blockCount} tone={snapshot.blockCount > 0 ? 'warning' : 'neutral'} />
      </div>

      <div className="message-center-workspace-sync__contact">
        <div>
          <span>{snapshot.privateOpen ? '当前联系人' : '当前视图'}</span>
          <strong>{snapshot.privateOpen ? contactLabel : snapshot.currentTabLabel}</strong>
          <small>{contactMeta}</small>
        </div>
        {snapshot.privateOpen && snapshot.currentContactUnread > 0 ? (
          <em>{snapshot.currentContactUnread} 条未读</em>
        ) : null}
        {snapshot.privateOpen && snapshot.isBlocked ? (
          <em className="is-warning"><ShieldMinus size={13} aria-hidden="true" /> 已拉黑</em>
        ) : null}
      </div>

      <div className="message-center-workspace-sync__actions" aria-label="消息中心快捷操作">
        <button type="button" onClick={() => sendWorkspaceCommand('refresh')}>
          <RefreshCw size={15} aria-hidden="true" />
          刷新
        </button>
        <button type="button" onClick={() => sendWorkspaceCommand('mark-read')} disabled={snapshot.unreadTotal === 0 && snapshot.currentContactUnread === 0}>
          <CheckCheck size={15} aria-hidden="true" />
          标记已读
        </button>
        <a href={notificationsHref} onClick={handleNotificationsClick}>
          <Bell size={15} aria-hidden="true" />
          通知
        </a>
        <a href={privateHref} onClick={handlePrivateClick}>
          <MessageCircle size={15} aria-hidden="true" />
          私信
        </a>
        <button type="button" onClick={() => sendWorkspaceCommand('focus-search')}>
          <Search size={15} aria-hidden="true" />
          搜索
        </button>
        <button
          type="button"
          onClick={() => sendWorkspaceCommand('focus-composer')}
          disabled={!snapshot.privateOpen || !snapshot.hasConversation || !snapshot.canSend}
        >
          <Send size={15} aria-hidden="true" />
          输入
        </button>
      </div>
    </section>
  );
}

function MessageCenterWorkspaceIsland() {
  const [snapshot, setSnapshot] = useState<MessageCenterWorkspaceSnapshot>(() => readInitialSnapshot());
  const normalizedSnapshot = useMemo(
    () => normalizeMessageCenterWorkspaceSnapshot(snapshot),
    [snapshot],
  );

  useEffect(() => {
    const handleChange = (event: Event) => {
      const detail = event instanceof CustomEvent ? event.detail : null;
      setSnapshot(normalizeMessageCenterWorkspaceSnapshot(detail));
    };
    window.addEventListener(MESSAGE_CENTER_WORKSPACE_EVENT, handleChange);
    setSnapshot(readInitialSnapshot());
    return () => window.removeEventListener(MESSAGE_CENTER_WORKSPACE_EVENT, handleChange);
  }, []);

  return <MessageCenterWorkspace snapshot={normalizedSnapshot} />;
}

mountReactIslandsWhenReady({
  islandName: 'message-center-workspace-sync',
  render: () => <MessageCenterWorkspaceIsland />,
  getProps: () => ({}),
});
