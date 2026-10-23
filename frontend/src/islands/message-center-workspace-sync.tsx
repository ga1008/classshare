import { useEffect, useMemo, useState } from 'react';

import { mountReactIslandsWhenReady } from '@/lib/mount-react-island';
import { LqButton } from '@/components/lq-presentation';
import {
  buildMessageCenterWorkspaceMessage,
  getPrimaryMessageCenterMetric,
  MESSAGE_CENTER_WORKSPACE_COMMAND_EVENT,
  MESSAGE_CENTER_WORKSPACE_EVENT,
  normalizeMessageCenterWorkspaceSnapshot,
  type MessageCenterWorkspaceSnapshot,
} from '@/lib/message-center-workspace';

function readInitialSnapshot() {
  return normalizeMessageCenterWorkspaceSnapshot(window.__LANSHARE_MESSAGE_CENTER_WORKSPACE__ || { loadStatus: 'loading' });
}

function sendWorkspaceCommand(type: string, detail: Record<string, unknown> = {}) {
  window.dispatchEvent(new CustomEvent(MESSAGE_CENTER_WORKSPACE_COMMAND_EVENT, {
    detail: { type, ...detail },
  }));
}

function MessageCenterWorkspace({ snapshot }: { snapshot: MessageCenterWorkspaceSnapshot }) {
  const message = buildMessageCenterWorkspaceMessage(snapshot);
  const primaryMetric = getPrimaryMessageCenterMetric(snapshot);

  // The messages family is unconditionally LQ (2026-10-24): one presentation, shared components only.
  return (
    <section className="lq-message-workspace" aria-label="消息概况" data-message-center-workspace-sync>
      <p className="lq-message-workspace__status" role="status">{message}</p>
      {snapshot.loadStatus === 'ready' ? <dl className="lq-message-workspace__counts">
        <div><dt>{primaryMetric.label}</dt><dd>{primaryMetric.value}</dd></div>
        <div><dt>总未读</dt><dd>{snapshot.unreadTotal}</dd></div>
        {snapshot.blockCount > 0 ? <div><dt>黑名单</dt><dd>{snapshot.blockCount}</dd></div> : null}
      </dl> : null}
      <div className="lq-message-workspace__actions" aria-label="消息中心快捷操作">
        <LqButton label="刷新" icon="refresh-cw" variant="ghost" disabled={snapshot.loadStatus === 'loading' || snapshot.actionBusy} onClick={() => sendWorkspaceCommand('refresh')} />
        <LqButton label="通知" icon="bell" href={snapshot.notificationsHref} variant={snapshot.privateOpen ? 'ghost' : 'soft'} attrs={{ 'aria-current': snapshot.privateOpen ? null : 'page' }} />
        <LqButton label="私信" icon="message-circle" href={snapshot.privateHref} variant={snapshot.privateOpen ? 'soft' : 'ghost'} attrs={{ 'aria-current': snapshot.privateOpen ? 'page' : null }} />
        {snapshot.privateOpen ? <LqButton label="输入" icon="pencil" variant="ghost" disabled={!snapshot.hasConversation || !snapshot.canSend} onClick={() => sendWorkspaceCommand('focus-composer')} /> : null}
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
