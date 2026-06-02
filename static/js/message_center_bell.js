import { apiFetch } from './api.js';

const bellShells = Array.from(document.querySelectorAll('[data-message-center-bell-shell]'));
const blogEntries = Array.from(document.querySelectorAll('[data-blog-topbar-entry]'));
const blogCountNodes = Array.from(document.querySelectorAll('[data-blog-today-count]'));
const blogCaptionNodes = Array.from(document.querySelectorAll('[data-blog-topbar-caption]'));

const bellState = {
    initialized: false,
    lastUnreadTotal: 0,
    latestUnreadId: 0,
    hideTimer: null,
};

function legacyBellShells() {
    return bellShells.filter((shell) => shell.dataset.messageCenterBellManaged !== 'react');
}

function legacyBlogEntries() {
    return blogEntries.filter((entry) => entry.dataset.blogTopbarManaged !== 'react');
}

function legacyBlogCountNodes() {
    return blogCountNodes.filter((node) => node.dataset.blogTopbarManaged !== 'react');
}

function legacyBlogCaptionNodes() {
    return blogCaptionNodes.filter((node) => node.dataset.blogTopbarManaged !== 'react');
}

function updateBell(summary) {
    const activeBellShells = legacyBellShells();
    const unreadTotal = Number(summary?.unread_total || 0);
    const countText = unreadTotal > 99 ? '99+' : String(unreadTotal);
    const captionText = unreadTotal > 0 ? `\u672a\u8bfb ${countText} \u6761` : '\u901a\u77e5\u4e0e\u79c1\u4fe1';

    activeBellShells.forEach((shell) => {
        const bellNode = shell.querySelector('[data-message-center-bell]');
        const countNode = shell.querySelector('[data-message-center-bell-count]');
        const captionNode = shell.querySelector('[data-message-center-bell-caption]');

        bellNode?.classList.toggle('is-unread', unreadTotal > 0);
        if (bellNode) {
            bellNode.setAttribute(
                'aria-label',
                unreadTotal > 0
                    ? `\u6253\u5f00\u901a\u77e5\u4e2d\u5fc3\uff0c${countText} \u6761\u672a\u8bfb\u6d88\u606f`
                    : '\u6253\u5f00\u901a\u77e5\u4e2d\u5fc3',
            );
        }
        if (captionNode) {
            captionNode.textContent = captionText;
        }
        if (countNode) {
            countNode.hidden = unreadTotal <= 0;
            countNode.textContent = unreadTotal > 0 ? countText : '0';
        }
    });
}

function hideBellToast(immediate = false) {
    const toastNode = legacyBellShells()[0]?.querySelector('[data-message-center-bell-toast]');
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
    const shell = legacyBellShells()[0];
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
    if (legacyBellShells().length === 0) {
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

function updateBlogTopbar(summary) {
    const activeBlogEntries = legacyBlogEntries();
    const activeBlogCountNodes = legacyBlogCountNodes();
    const activeBlogCaptionNodes = legacyBlogCaptionNodes();
    const todayNewCount = Number(summary?.today_new_count || 0);
    const countText = todayNewCount > 99 ? '+99' : `+${todayNewCount}`;
    const captionText = todayNewCount > 0
        ? `\u4eca\u65e5\u65b0\u589e ${todayNewCount > 99 ? '99+' : todayNewCount} \u7bc7`
        : '\u89c2\u70b9\u4e0e\u4ea4\u6d41';

    activeBlogEntries.forEach((entry) => {
        entry.classList.toggle('has-new-count', todayNewCount > 0);
        entry.setAttribute(
            'aria-label',
            todayNewCount > 0
                ? `\u6253\u5f00\u535a\u5ba2\uff0c\u4eca\u65e5\u65b0\u589e ${todayNewCount} \u7bc7`
                : '\u6253\u5f00\u535a\u5ba2',
        );
        entry.title = todayNewCount > 0
            ? `\u535a\u5ba2\uff1a\u4eca\u65e5\u65b0\u589e ${todayNewCount} \u7bc7`
            : '\u535a\u5ba2';
    });

    activeBlogCaptionNodes.forEach((captionNode) => {
        captionNode.textContent = captionText;
    });

    activeBlogCountNodes.forEach((countNode) => {
        countNode.hidden = todayNewCount <= 0;
        countNode.textContent = todayNewCount > 0 ? countText : '+0';
    });
}

async function refreshBlogTopbar() {
    if (legacyBlogEntries().length === 0 && legacyBlogCountNodes().length === 0 && legacyBlogCaptionNodes().length === 0) {
        return;
    }

    try {
        const response = await apiFetch('/api/blog/summary', { silent: true });
        updateBlogTopbar(response?.summary || {});
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

if (blogEntries.length > 0 || blogCountNodes.length > 0) {
    refreshBlogTopbar();

    window.setInterval(() => {
        if (!document.hidden) {
            refreshBlogTopbar();
        }
    }, 60000);

    document.addEventListener('visibilitychange', () => {
        if (!document.hidden) {
            refreshBlogTopbar();
        }
    });
}

window.refreshMessageCenterBell = refreshBell;
window.refreshBlogTopbar = refreshBlogTopbar;
