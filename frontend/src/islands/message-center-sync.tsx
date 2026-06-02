import { useEffect, useRef } from 'react';

import { mountReactIslandsWhenReady } from '@/lib/mount-react-island';
import {
  messageBellAriaLabel,
  messageBellCaption,
  normalizeMessageCenterResponse,
  normalizeMessageSummary,
  unreadCountText,
  type MessageNotification,
  type MessageSummary,
  type MessageCenterResponse,
} from '@/lib/message-center-bell';

type RefreshOptions = {
  allowPopup?: boolean;
};

type BellState = {
  initialized: boolean;
  lastUnreadTotal: number;
  latestUnreadId: number;
  hideTimer: number | null;
};

const SUMMARY_URL = '/api/message-center/summary';
const POLL_INTERVAL_MS = 15_000;
const TOAST_VISIBLE_MS = 5_000;
const TOAST_EXIT_MS = 180;

async function fetchMessageCenterSummary(): Promise<MessageCenterResponse> {
  const response = await fetch(SUMMARY_URL, {
    credentials: 'same-origin',
    headers: { Accept: 'application/json' },
  });
  if (!response.ok) {
    throw new Error(`Message center summary failed: ${response.status}`);
  }
  return normalizeMessageCenterResponse(await response.json());
}

function updateShells(shells: HTMLElement[], summary: MessageSummary) {
  const unreadTotal = summary.unreadTotal;
  const countText = unreadCountText(unreadTotal);
  const captionText = messageBellCaption(unreadTotal);

  for (const shell of shells) {
    const bellNode = shell.querySelector<HTMLElement>('[data-message-center-bell]');
    const countNode = shell.querySelector<HTMLElement>('[data-message-center-bell-count]');
    const captionNode = shell.querySelector<HTMLElement>('[data-message-center-bell-caption]');

    bellNode?.classList.toggle('is-unread', unreadTotal > 0);
    bellNode?.setAttribute('aria-label', messageBellAriaLabel(unreadTotal));
    if (captionNode) {
      captionNode.textContent = captionText;
    }
    if (countNode) {
      countNode.hidden = unreadTotal <= 0;
      countNode.textContent = unreadTotal > 0 ? countText : '0';
    }
  }
}

function firstToastShell(shells: HTMLElement[]): HTMLElement | null {
  return shells.find((shell) => shell.querySelector('[data-message-center-bell-toast]')) || null;
}

function hideBellToast(shells: HTMLElement[], state: BellState, immediate = false) {
  const toastNode = firstToastShell(shells)?.querySelector<HTMLElement>('[data-message-center-bell-toast]');
  if (!toastNode) {
    return;
  }

  if (state.hideTimer !== null) {
    window.clearTimeout(state.hideTimer);
    state.hideTimer = null;
  }

  if (immediate) {
    toastNode.classList.remove('is-visible');
    toastNode.hidden = true;
    return;
  }

  toastNode.classList.remove('is-visible');
  window.setTimeout(() => {
    if (!toastNode.classList.contains('is-visible')) {
      toastNode.hidden = true;
    }
  }, TOAST_EXIT_MS);
}

function showBellToast(shells: HTMLElement[], state: BellState, notification: MessageNotification) {
  const shell = firstToastShell(shells);
  if (!shell) {
    return;
  }

  const toastNode = shell.querySelector<HTMLElement>('[data-message-center-bell-toast]');
  const bodyNode = shell.querySelector<HTMLElement>('[data-message-center-bell-toast-body]');
  const metaNode = shell.querySelector<HTMLElement>('[data-message-center-bell-toast-meta]');
  if (!toastNode || !bodyNode || !metaNode) {
    return;
  }

  bodyNode.textContent = notification.actorDisplayName
    ? `收到来自 ${notification.actorDisplayName} 的新信息`
    : '收到一条新的系统信息';
  metaNode.textContent = notification.title || `${notification.categoryLabel} 已更新`;

  toastNode.hidden = false;
  window.requestAnimationFrame(() => {
    toastNode.classList.add('is-visible');
  });

  if (state.hideTimer !== null) {
    window.clearTimeout(state.hideTimer);
  }
  state.hideTimer = window.setTimeout(() => hideBellToast(shells, state), TOAST_VISIBLE_MS);
}

function syncBellState(
  shells: HTMLElement[],
  state: BellState,
  summary: MessageSummary,
  latestUnread: MessageNotification | null,
  { allowPopup = true }: RefreshOptions = {},
) {
  const unreadTotal = summary.unreadTotal;
  const latestUnreadId = latestUnread?.id || 0;
  const shouldPopup = Boolean(
    allowPopup
      && latestUnread
      && unreadTotal > 0
      && (
        !state.initialized
        || latestUnreadId > state.latestUnreadId
        || unreadTotal > state.lastUnreadTotal
      ),
  );

  state.initialized = true;
  state.lastUnreadTotal = unreadTotal;
  state.latestUnreadId = Math.max(state.latestUnreadId, latestUnreadId);

  if (unreadTotal <= 0) {
    state.latestUnreadId = 0;
    hideBellToast(shells, state, true);
    return;
  }

  if (shouldPopup && latestUnread) {
    showBellToast(shells, state, latestUnread);
  }
}

function useMessageCenterBellSync() {
  const stateRef = useRef<BellState>({
    initialized: false,
    lastUnreadTotal: 0,
    latestUnreadId: 0,
    hideTimer: null,
  });

  useEffect(() => {
    const shells = Array.from(document.querySelectorAll<HTMLElement>('[data-message-center-bell-shell]'));
    if (shells.length === 0) {
      return undefined;
    }

    for (const shell of shells) {
      shell.dataset.messageCenterBellManaged = 'react';
    }

    const refreshBell = async (options: RefreshOptions = {}) => {
      try {
        const response = await fetchMessageCenterSummary();
        updateShells(shells, response.summary);
        syncBellState(shells, stateRef.current, response.summary, response.latestUnread, options);
      } catch {
        // Keep the topbar calm during transient polling or auth-refresh failures.
      }
    };

    const handleBellClick = () => hideBellToast(shells, stateRef.current, true);
    const handleVisibilityChange = () => {
      if (!document.hidden) {
        void refreshBell({ allowPopup: true });
      }
    };
    const handleSummaryUpdated = (event: Event) => {
      const detail = event instanceof CustomEvent ? event.detail : {};
      const summary = normalizeMessageSummary(detail);
      updateShells(shells, summary);
      stateRef.current.initialized = true;
      stateRef.current.lastUnreadTotal = summary.unreadTotal;
      if (summary.unreadTotal <= 0) {
        stateRef.current.latestUnreadId = 0;
        hideBellToast(shells, stateRef.current, true);
      }
    };

    for (const shell of shells) {
      shell.querySelector('[data-message-center-bell]')?.addEventListener('click', handleBellClick);
    }

    window.refreshMessageCenterBell = refreshBell;
    void refreshBell({ allowPopup: false });
    const intervalId = window.setInterval(() => {
      if (!document.hidden) {
        void refreshBell({ allowPopup: true });
      }
    }, POLL_INTERVAL_MS);
    document.addEventListener('visibilitychange', handleVisibilityChange);
    window.addEventListener('message-center:summary-updated', handleSummaryUpdated);

    return () => {
      window.clearInterval(intervalId);
      for (const shell of shells) {
        delete shell.dataset.messageCenterBellManaged;
        shell.querySelector('[data-message-center-bell]')?.removeEventListener('click', handleBellClick);
      }
      document.removeEventListener('visibilitychange', handleVisibilityChange);
      window.removeEventListener('message-center:summary-updated', handleSummaryUpdated);
      if (stateRef.current.hideTimer !== null) {
        window.clearTimeout(stateRef.current.hideTimer);
        stateRef.current.hideTimer = null;
      }
    };
  }, []);
}

function MessageCenterSyncIsland() {
  useMessageCenterBellSync();
  return null;
}

mountReactIslandsWhenReady({
  islandName: 'message-center-sync',
  defaultMountIdPrefix: 'message-center-sync',
  getProps: () => ({}),
  render: () => <MessageCenterSyncIsland />,
});
