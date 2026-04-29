import { apiFetch } from './api.js';

const bellShells = Array.from(document.querySelectorAll('[data-message-center-bell-shell]'));

const bellState = {
    initialized: false,
    lastUnreadTotal: 0,
    latestUnreadId: 0,
    hideTimer: null,
};

function updateBell(summary) {
    const unreadTotal = Number(summary?.unread_total || 0);
    const countText = unreadTotal > 99 ? '99+' : String(unreadTotal);

    bellShells.forEach((shell) => {
        const bellNode = shell.querySelector('[data-message-center-bell]');
        const countNode = shell.querySelector('[data-message-center-bell-count]');

        bellNode?.classList.toggle('is-unread', unreadTotal > 0);
        if (countNode) {
            countNode.hidden = unreadTotal <= 0;
            countNode.textContent = unreadTotal > 0 ? countText : '0';
        }
    });
}

function hideBellToast(immediate = false) {
    const toastNode = bellShells[0]?.querySelector('[data-message-center-bell-toast]');
    if (!toastNode) {
        return;
    }

    window.clearTimeout(bellState.hideTimer);
    bellState.hideTimer = null;

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
    }, 180);
}

function showBellToast(notification) {
    const shell = bellShells[0];
    if (!shell || !notification) {
        return;
    }

    const toastNode = shell.querySelector('[data-message-center-bell-toast]');
    const bodyNode = shell.querySelector('[data-message-center-bell-toast-body]');
    const metaNode = shell.querySelector('[data-message-center-bell-toast-meta]');
    if (!toastNode || !bodyNode || !metaNode) {
        return;
    }

    const actorName = String(notification.actor_display_name || '').trim();
    const categoryLabel = String(notification.category_label || '\u6d88\u606f').trim();
    const title = String(notification.title || '').trim();

    bodyNode.textContent = actorName
        ? `\u6536\u5230\u6765\u81ea ${actorName} \u7684\u65b0\u4fe1\u606f`
        : '\u6536\u5230\u4e00\u6761\u65b0\u7684\u7cfb\u7edf\u4fe1\u606f';
    metaNode.textContent = title || `${categoryLabel} \u5df2\u66f4\u65b0`;

    toastNode.hidden = false;
    window.requestAnimationFrame(() => {
        toastNode.classList.add('is-visible');
    });

    window.clearTimeout(bellState.hideTimer);
    bellState.hideTimer = window.setTimeout(() => hideBellToast(), 5000);
}

function syncBellState(summary, latestUnread, { allowPopup }) {
    const unreadTotal = Number(summary?.unread_total || 0);
    const latestUnreadId = Number(latestUnread?.id || 0);

    const shouldPopup = Boolean(
        allowPopup
        && latestUnread
        && unreadTotal > 0
        && (
            !bellState.initialized
            || latestUnreadId > bellState.latestUnreadId
            || unreadTotal > bellState.lastUnreadTotal
        )
    );

    bellState.initialized = true;
    bellState.lastUnreadTotal = unreadTotal;
    bellState.latestUnreadId = Math.max(bellState.latestUnreadId, latestUnreadId);

    if (unreadTotal <= 0) {
        hideBellToast(true);
        return;
    }

    if (shouldPopup) {
        showBellToast(latestUnread);
    }
}

async function refreshBell(options = {}) {
    if (bellShells.length === 0) {
        return;
    }

    const { allowPopup = true } = options;
    try {
        const response = await apiFetch('/api/message-center/summary', { silent: true });
        updateBell(response?.summary || {});
        syncBellState(response?.summary || {}, response?.latest_unread || null, { allowPopup });
    } catch {
        // Ignore transient polling failures.
    }
}

if (bellShells.length > 0) {
    bellShells.forEach((shell) => {
        shell.querySelector('[data-message-center-bell]')?.addEventListener('click', () => hideBellToast(true));
    });

    refreshBell({ allowPopup: false });

    window.setInterval(() => {
        if (!document.hidden) {
            refreshBell({ allowPopup: true });
        }
    }, 15000);

    document.addEventListener('visibilitychange', () => {
        if (!document.hidden) {
            refreshBell({ allowPopup: true });
        }
    });

    window.addEventListener('message-center:summary-updated', (event) => {
        const summary = event.detail || {};
        updateBell(summary);
        bellState.initialized = true;
        bellState.lastUnreadTotal = Number(summary?.unread_total || 0);
        if (bellState.lastUnreadTotal <= 0) {
            bellState.latestUnreadId = 0;
            hideBellToast(true);
        }
    });
}

window.refreshMessageCenterBell = refreshBell;
