/**
 * 聊天浮窗 (social doc §9): conversation list | message pane + composer | sidebar.
 *
 * Data owner for the open conversation: loads pages of messages (older pages
 * on scroll-up), merges websocket pushes (message / message_recalled / read /
 * typing / nudge / presence / group_changed / friend_*), marks read while the
 * window is visible and draws DM read receipts. Rendering lives in
 * chat_messages.js, input in chat_composer.js, the side panel in chat_sidebar.js.
 */
import { socialApi, errorText } from './social_api.js';
import { lanStore } from './social_lan.js';
import { createChatComposer } from './chat_composer.js';
import { createChatSidebar } from './chat_sidebar.js';
import { daySeparator, isMine, joinsPrevious, renderMessage } from './chat_messages.js';
import {
    createSocialDialog, dayKey, debounce, el, emptyState, ensureMarkdown, formatRelative, lqAvatar, lqBadge, lqButton, notify,
} from './social_dom.js';

const PAGE = 50;
const LOAD_OLDER_PX = 80;
const STICK_BOTTOM_PX = 120;
const TYPING_SHOW_MS = 4500;
const RECALL_WINDOW_MS = 120_000;
const NUDGE_SHAKE_MS = 700;
const READ_DEBOUNCE_MS = 400;
// A warm window reuses its DOM while the live socket has kept this snapshot
// current. Reconnects, relevant pushes and an older snapshot revalidate it.
const SNAPSHOT_MS = 30_000;

function reconcileChildren(parent, nodes) {
    let cursor = parent.firstChild;
    for (const node of nodes) {
        if (cursor === node) cursor = cursor.nextSibling;
        else parent.insertBefore(node, cursor);
    }
    while (cursor) { const next = cursor.nextSibling; cursor.remove(); cursor = next; }
}

function setText(node, value) {
    const text = String(value ?? '');
    if (node.textContent !== text) node.textContent = text;
}

function peerOf(key, me) {
    const parties = String(key).slice(3).split('|');
    return parties.find((item) => item !== me) || parties[0] || '';
}

export function createChatModal(client, { openFriends }) {
    const listItems = el('ul', { class: 'social-convs', role: 'list' });
    const manageFriends = lqButton({ label: '好友管理', icon: 'users', variant: 'ghost', size: 'lg',
        attrs: { 'data-social-manage-friends': '', 'aria-haspopup': 'dialog' }, onClick: (event) => openFriends(event.currentTarget) });
    let requestBadge = null;
    let requestCount = -1;
    const listPane = el('nav', { class: 'social-chat__list', 'aria-label': '会话列表' }, [
        el('div', { class: 'social-chat__list-head' }, [
            el('strong', { text: '会话' }),
        ]),
        listItems,
        el('footer', { class: 'social-chat__list-foot' }, [manageFriends]),
    ]);
    const heading = el('strong', { class: 'social-chat__title', text: '' });
    const subline = el('small', { class: 'social-chat__subline', 'aria-live': 'polite' });
    const listToggle = lqButton({ icon: 'menu', variant: 'ghost', aria: '显示会话列表', attrs: { 'data-chat-toggle': 'list', 'aria-expanded': 'false' } });
    // DM only: the peer's 个人信息面板 (delegated data-user-card trigger).
    const profileButton = lqButton({ icon: 'user', variant: 'ghost', aria: '查看对方的个人信息', attrs: { 'data-user-card': 'off', 'aria-haspopup': 'dialog' } });
    profileButton.hidden = true;
    const sideToggle = lqButton({ icon: 'info', variant: 'ghost', aria: '显示资料与设置', attrs: { 'data-chat-toggle': 'side', 'aria-expanded': 'false' } });
    const scroller = el('div', { class: 'social-chat__messages', role: 'log', 'aria-label': '消息记录', tabindex: '0', 'data-ls-lightbox-scope': '' });
    const composer = createChatComposer({ client, onSent: (message) => upsert(message, true) });
    const main = el('section', { class: 'social-chat__main', 'aria-label': '消息' }, [
        el('header', { class: 'social-chat__head' }, [listToggle, el('div', { class: 'social-chat__heading' }, [heading, subline]), profileButton, sideToggle]),
        scroller,
        el('div', { class: 'social-chat__composer' }, [composer.element]),
    ]);
    const sidebar = createChatSidebar({ client, onChanged: (reason, meta) => onSidebarChange(reason, meta) });
    const side = el('aside', { class: 'social-chat__side', 'aria-label': '资料与设置' }, [sidebar.element]);
    const layout = el('div', { class: 'social-chat', 'data-list-open': 'false', 'data-side-open': 'false' }, [listPane, main, side]);
    const dialog = createSocialDialog({
        id: 'social-chat', title: '聊天', size: 'full', body: layout, className: 'social-chat-dialog',
        beforeClose: () => {
            if (active) { active.scrollTop = scroller.scrollTop; active.pinned = pinned; }
            return true;
        },
        onClose: () => {
            windowVersion++;
            groupVersion++;
            client.focus(null);
            sidebar.clear();
            sidebarKey = null;
            markReadSoon.cancel();
            refreshFilesSoon.cancel();
            refreshListSoon.cancel();
            window.clearTimeout(typingTimer);
        },
    });

    const conversations = new Map();
    let order = [];
    const friends = new Map();
    let active = null;
    let typingTimer = 0;
    let loadingOlder = false;
    let windowVersion = 0;
    let openSequence = 0;
    let sidebarKey = null;
    let groupVersion = 0;
    let listLoadedAt = 0;
    let friendsLoadedAt = 0;
    let listVersion = 0;
    let friendsVersion = 0;
    let listFlight = null;
    let friendsFlight = null;
    let lastReceipt = null;
    const rows = new Map();
    const messageNodes = new Map();
    const dayNodes = new Map();
    const historyHint = el('p', { class: 'social-chat__more' });
    const isVisible = () => dialog.isOpen() && dialog.root.dataset.lqLayerState !== 'closing';
    const isFresh = (stamp) => client.status === 'online' && Date.now() - stamp < SNAPSHOT_MS;

    function paintRequestCount() {
        const count = Number(client.pendingIn) || 0;
        if (count === requestCount) return;
        requestCount = count;
        requestBadge?.remove();
        requestBadge = lqBadge(count);
        if (requestBadge) manageFriends.append(requestBadge);
        manageFriends.setAttribute('aria-label', count ? `好友管理，${count} 个待处理申请` : '好友管理');
    }

    /* ------------------------------------------------------------ list */
    function renderList() {
        if (!isVisible()) return;
        paintRequestCount();
        if (!order.length) {
            if (!listItems.querySelector('.social-empty')) listItems.replaceChildren(el('li', {}, [emptyState('还没有会话', '从左下角的好友管理开始认识新朋友。')]));
            rows.clear();
            return;
        }
        const nodes = order.map((key) => {
            const item = conversations.get(key);
            const online = item.type === 'dm' && (client.presence.has(item.peer_identity) ? client.isOnline(item.peer_identity) : item.online);
            const current = active?.key === key;
            let row = rows.get(key);
            if (!row) {
                const avatar = el('span', { class: 'social-avatar' });
                const title = el('strong');
                const preview = el('small');
                const time = el('small');
                const badge = el('span');
                const button = el('button', {
                type: 'button', class: 'lq-btn lq-btn--ghost lq-btn--md lq-domain-choice social-conv__button',
                'data-lq-component': 'choice', 'data-lq-shape': 'surface', 'data-conv': key,
                }, [avatar, el('span', { class: 'social-conv__copy' }, [title, preview]), el('span', { class: 'social-conv__marks' }, [time, badge])]);
                row = { node: el('li', { class: 'social-conv' }, [button]), button, avatar, title, preview, time, badge };
                rows.set(key, row);
            }
            if (row.button.getAttribute('aria-current') !== String(current)) row.button.setAttribute('aria-current', String(current));
            const avatarKey = `${item.title}|${item.avatar_url || ''}|${item.type}`;
            if (row.avatarKey !== avatarKey) {
                row.avatar.replaceChildren(lqAvatar(item.title, item.avatar_url, 40));
                if (item.type === 'dm') row.avatar.append(el('span', { class: 'social-avatar__dot', 'aria-hidden': 'true' }));
                row.avatarKey = avatarKey;
            }
            if (item.type === 'dm' && row.avatar.dataset.online !== String(online)) row.avatar.dataset.online = String(online);
            setText(row.title, `${item.title}${item.muted ? '（免打扰）' : ''}`);
            setText(row.preview, item.last_message?.preview || (item.type === 'group' ? '群聊' : '开始聊天吧'));
            setText(row.time, item.last_message?.at ? formatRelative(item.last_message.at) : '');
            const badgeKey = `${Number(item.unread) || 0}|${Boolean(item.muted)}`;
            if (row.badgeKey !== badgeKey) { row.badge.replaceChildren(...[lqBadge(item.unread, item.muted ? 'neutral' : 'danger')].filter(Boolean)); row.badgeKey = badgeKey; }
            return row.node;
        });
        for (const key of rows.keys()) if (!conversations.has(key)) rows.delete(key);
        reconcileChildren(listItems, nodes);
    }

    function acceptConversations(items) {
        conversations.clear();
        order = [];
        for (const item of items || []) { conversations.set(item.conversation_key, item); order.push(item.conversation_key); }
        renderList();
        if (active && conversations.has(active.key)) setHeading(conversations.get(active.key).title);
    }

    async function loadConversations(force = false) {
        if (!force && isFresh(listLoadedAt)) { renderList(); return; }
        if (listFlight) return listFlight;
        const version = listVersion;
        const pending = socialApi.conversations().then((data) => {
            if (version !== listVersion) { listFlight = null; return loadConversations(true); }
            acceptConversations(data.items);
            listLoadedAt = Date.now();
        }).finally(() => { if (listFlight === pending) listFlight = null; });
        listFlight = pending;
        return pending;
    }

    async function loadFriends(force = false) {
        if (!force && isFresh(friendsLoadedAt)) return;
        if (friendsFlight) return friendsFlight;
        const version = friendsVersion;
        const pending = socialApi.friends().then((data) => {
            if (version !== friendsVersion) { friendsFlight = null; return loadFriends(true); }
            friends.clear();
            for (const item of data.items || []) friends.set(item.identity, item);
            friendsLoadedAt = Date.now();
        }).finally(() => { if (friendsFlight === pending) friendsFlight = null; });
        friendsFlight = pending;
        return pending;
    }

    function ensureConversation(key) {
        if (conversations.has(key)) return conversations.get(key);
        const isGroup = key.startsWith('group:');
        const peer = isGroup ? null : peerOf(key, client.me);
        const friend = peer ? friends.get(peer) : null;
        const item = {
            conversation_key: key, type: isGroup ? 'group' : 'dm', title: friend ? (friend.remark || friend.name) : (isGroup ? '群聊' : '私聊'),
            avatar_url: friend?.avatar_url || null, last_message: null, unread: 0, online: Boolean(friend?.online),
            peer_identity: peer, group_id: isGroup ? Number(key.slice(6)) : undefined,
        };
        conversations.set(key, item);
        order.unshift(key);
        return item;
    }

    function bumpConversation(message, fresh) {
        const key = message.conversation_key;
        if (!conversations.has(key)) { refreshListSoon(); return; }
        const item = conversations.get(key);
        if (message.id <= (item.seenId || 0)) fresh = false;
        item.seenId = Math.max(item.seenId || 0, Number(message.id) || 0);
        item.last_message = { preview: message.recalled ? '撤回了一条消息' : (String(message.body || '').replace(/[*_`>#~[\]()!|-]+/g, ' ').trim().slice(0, 60) || '[附件]'), at: message.created_at };
        if (fresh && !isMine(message, client.me) && message.message_type !== 'system' && !(isVisible() && active?.key === key && !document.hidden)) item.unread = (Number(item.unread) || 0) + 1;
        order = [key, ...order.filter((entry) => entry !== key)];
        renderList();
    }
    const refreshFilesSoon = debounce((groupId) => { if (isVisible()) sidebar.refreshFiles(groupId); }, 800);
    const refreshListSoon = debounce(() => { if (isVisible()) void loadConversations(true).catch(() => {}); }, 500);

    /* ------------------------------------------------------------ messages */
    const ctxFor = () => ({
        me: client.me,
        type: active?.type || 'dm',
        canRecall: (message) => {
            if (!active || message.message_type === 'system' || message.recalled) return false;
            if (active.type === 'group' && ['owner', 'admin'].includes(active.detail?.my_role)) return true;
            return isMine(message, client.me) && Date.now() - new Date(message.created_at).getTime() < RECALL_WINDOW_MS;
        },
    });

    function buildNodes(messages) {
        const nodes = [];
        const ctx = ctxFor();
        let last = null;
        const kept = new Set();
        for (const message of messages) {
            const day = dayKey(message.created_at);
            if (!last || dayKey(last.created_at) !== day) {
                if (!dayNodes.has(day)) dayNodes.set(day, daySeparator(message.created_at));
                nodes.push(dayNodes.get(day));
            }
            const connected = joinsPrevious(last, message);
            const signature = `${connected}|${ctx.canRecall(message)}|${JSON.stringify(message)}`;
            let cached = messageNodes.get(message.id);
            if (cached?.signature !== signature) {
                cached = { signature, node: renderMessage(message, ctx, { connected }) };
                messageNodes.set(message.id, cached);
            }
            nodes.push(cached.node);
            kept.add(message.id);
            last = message;
        }
        for (const id of messageNodes.keys()) if (!kept.has(id)) messageNodes.delete(id);
        return nodes;
    }

    function renderMessages() {
        const nodes = buildNodes(active.messages);
        setText(historyHint, active.hasMore ? '上滑加载更早的消息' : '没有更早的消息了');
        reconcileChildren(scroller, [historyHint, ...(nodes.length ? nodes : [scroller.querySelector('.social-empty') || emptyState('还没有消息', '打个招呼吧。')])]);
        updateReceipts();
    }

    function nearBottom() {
        return scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < STICK_BOTTOM_PX;
    }

    function scrollToBottom() {
        scroller.scrollTop = scroller.scrollHeight;
    }

    function updateReceipts() {
        if (!active || active.type !== 'dm') return;
        const message = [...active.messages].reverse().find((item) => isMine(item, client.me) && !item.recalled && ['text', 'media'].includes(item.message_type));
        const receipt = messageNodes.get(message?.id)?.node.querySelector('[data-receipt]');
        if (lastReceipt && lastReceipt !== receipt) setText(lastReceipt, '');
        lastReceipt = receipt;
        if (receipt) setText(receipt, active.peerRead >= message.id ? '已读' : '已送达');
    }

    /** Insert or replace a message of the active conversation. */
    function upsert(message, fromMe = false) {
        const isActive = Boolean(active) && message.conversation_key === active.key;
        const index = isActive ? active.messages.findIndex((item) => item.id === message.id) : -1;
        if (isActive && active.pendingUpdates) active.pendingUpdates.set(message.id, message);
        bumpConversation(message, index < 0 && !message.recalled);
        if (!isActive) return;
        if (index >= 0) {
            active.messages[index] = message;
            renderMessages();
            return;
        }
        if (active.type === 'group' && (message.media || []).length) refreshFilesSoon(active.groupId);
        const stick = fromMe || nearBottom();
        active.messages.push(message);
        renderMessages();
        if (stick) scrollToBottom();
        markReadSoon();
    }

    async function loadOlder() {
        if (!isVisible() || !active?.hasMore || loadingOlder || !active.messages.length) return;
        loadingOlder = true;
        const current = active;
        try {
            const data = await socialApi.messages(current.key, current.messages[0].id, PAGE);
            if (active !== current || !isVisible()) return;
            const before = scroller.scrollHeight;
            active.messages = [...(data.items || []), ...active.messages];
            active.hasMore = Boolean(data.has_more);
            renderMessages();
            scroller.scrollTop += scroller.scrollHeight - before;
        } catch (failure) {
            if (active === current && isVisible()) notify(errorText(failure, '更早的消息加载失败。'), 'danger');
        } finally {
            loadingOlder = false;
        }
    }

    const markReadSoon = debounce(async () => {
        if (!active || !isVisible() || document.hidden || !active.messages.length) return;
        const current = active;
        const last = current.messages.at(-1).id;
        if (last <= (current.readSent || 0)) return;
        current.readSent = last;
        const item = conversations.get(current.key);
        const cleared = Number(item?.unread) || 0;
        if (item) { item.unread = 0; renderList(); }
        try { await socialApi.read(current.key, last); client.settleRead(cleared); } catch { current.readSent = 0; }
    }, READ_DEBOUNCE_MS);

    /* ------------------------------------------------------------ select */
    async function select(key) {
        if (!isVisible()) return;
        if (active?.key !== key && composer.isBusy()) { notify('消息正在发送，请稍候再切换会话。', 'info'); return; }
        const item = ensureConversation(key);
        const type = key.startsWith('group:') ? 'group' : 'dm';
        const same = active?.key === key;
        if (!same) {
            active = {
                key, type, peer: type === 'dm' ? peerOf(key, client.me) : null, groupId: type === 'group' ? Number(key.slice(6)) : null,
                messages: [], hasMore: false, peerRead: 0, detail: null, readSent: 0, muted: Boolean(item.muted), mentionQuota: null,
                loadedAt: 0, dirty: false,
            };
            messageNodes.clear();
            dayNodes.clear();
            lastReceipt = null;
            sidebar.clear();
            sidebarKey = null;
            window.clearTimeout(typingTimer);
            pinned = true;
        }
        layout.dataset.type = type;
        profileButton.dataset.userCard = type === 'dm' && active.peer ? active.peer : 'off';
        profileButton.hidden = type !== 'dm' || !active.peer;
        layout.dataset.listOpen = 'false';
        listToggle.setAttribute('aria-expanded', 'false');
        setHeading(item.title);
        setSubline();
        renderList();
        client.focus(key);
        const current = active;
        if (current.resumeScroll) {
            scroller.scrollTop = current.scrollTop || 0;
            pinned = current.pinned !== false;
            current.resumeScroll = false;
        }
        if (same && !current.dirty && isFresh(current.loadedAt)) {
            applyComposer();
            showSidebar();
            updateReceipts();
            markReadSoon();
            return;
        }
        const version = windowVersion;
        if (current.loading && current.loadingVersion === version) return current.loading;
        const stick = !same || pinned;
        composer.setConversation({ key, type, writable: false, readOnlyReason: same ? '正在同步会话…' : '正在读取会话…' });
        if (!same) scroller.replaceChildren(el('p', { class: 'social-hint', text: '正在读取消息…' }));
        current.loadingVersion = version;
        current.dirty = false;
        current.loadedAt = 0;
        current.invalidatedWhileLoading = false;
        const pushed = new Map();
        const recalled = new Set();
        current.pendingUpdates = pushed;
        current.pendingRecalls = recalled;
        const detailVersion = type === 'group' ? ++groupVersion : groupVersion;
        const pending = readConversation();
        current.loading = pending;
        return pending;

        async function readConversation() {
        let completed = false;
        try {
            const [page, detail] = await Promise.all([
                socialApi.messages(key, 0, PAGE),
                type === 'group' ? socialApi.group(current.groupId) : null,
            ]);
            if (active !== current || version !== windowVersion || !isVisible()) return;
            const latest = page.items || [];
            // Keep previously loaded older pages and media elements on a refresh.
            const oldest = latest[0]?.id;
            const older = oldest ? current.messages.filter((message) => message.id < oldest) : [];
            const merged = new Map([...older, ...latest].map((message) => [message.id, message]));
            // Socket changes received after this HTTP snapshot was requested win,
            // including recalls/backfills of rows that already existed in it.
            for (const [id, message] of pushed) merged.set(id, message);
            for (const id of recalled) if (merged.has(id)) merged.set(id, { ...merged.get(id), recalled: true, body: '', media: [] });
            current.messages = [...merged.values()].sort((a, b) => a.id - b.id);
            if (!older.length) current.hasMore = Boolean(page.has_more);
            current.peerRead = Number(page.peer_read_message_id) || 0;
            if (detailVersion === groupVersion) current.detail = detail;
            current.muted = Boolean(page.muted);
            current.mentionQuota = page.mention_quota || null;
            current.loadedAt = Date.now();
            client.setMuted(key, current.muted);
            if (current.detail) setHeading(current.detail.display_name || current.detail.name);
            renderMessages();
            if (stick) scrollToBottom();
            applyComposer();
            showSidebar();
            setSubline();
            markReadSoon();
            if (!same) composer.focus();
            completed = true;
        } catch (failure) {
            if (active !== current || version !== windowVersion || !isVisible()) return;
            current.dirty = true;
            if (!current.messages.length) scroller.replaceChildren(emptyState('消息加载失败', errorText(failure), 'error'));
            else notify(errorText(failure, '消息同步失败，已有记录已保留。'), 'danger');
            composer.setConversation({ key, type, writable: false, readOnlyReason: errorText(failure) });
        } finally {
            if (current.loadingVersion === version) { current.loading = null; current.pendingUpdates = null; current.pendingRecalls = null; }
            if (completed && current.invalidatedWhileLoading && client.status === 'online' && active === current && isVisible()) void refreshSession();
        }
        }
    }

    function setHeading(title) {
        setText(heading, title);
        setText(dialog.title, title || '聊天');
    }

    async function refreshSession() {
        const version = windowVersion;
        const current = active;
        try {
            await Promise.all([loadConversations(true), loadFriends(true)]);
            if (version === windowVersion && active === current && isVisible() && current) await select(current.key);
        } catch { /* A subsequent visible/open event retries; existing content stays. */ }
    }

    function applyComposer() {
        if (!active) return;
        if (active.type === 'dm') {
            const writable = friends.has(active.peer);
            const friend = friends.get(active.peer);
            composer.setConversation({ key: active.key, type: 'dm', peer: active.peer, members: friend ? [friend] : [], mentionQuota: active.mentionQuota,
                writable, readOnlyReason: writable ? '' : '你们已不是好友，无法继续发送消息。' });
        } else {
            composer.setConversation({
                key: active.key, type: 'group', members: active.detail?.members || [], myRole: active.detail?.my_role,
                mentionQuota: active.mentionQuota,
                writable: Boolean(active.detail), readOnlyReason: active.detail ? '' : '你不在该群聊中。',
            });
        }
    }

    function showSidebar() {
        if (!active || !isVisible() || (!window.matchMedia('(min-width: 1101px)').matches && layout.dataset.sideOpen !== 'true')) return;
        if (sidebarKey === active.key) return;
        sidebarKey = active.key;
        if (active.type === 'dm') {
            const friend = friends.get(active.peer);
            sidebar.showDm(active.peer, { key: active.key, writable: friends.has(active.peer),
                muted: client.mutedKeys.has(active.key), displayName: friend?.remark || friend?.name || conversations.get(active.key)?.title });
        }
        else if (active.detail) sidebar.showGroup(active.detail, { key: active.key });
        else sidebar.clear();
    }

    function setSubline(text = null) {
        if (!active) { subline.textContent = ''; return; }
        if (text) { subline.textContent = text; return; }
        if (active.type === 'dm') {
            const online = client.presence.has(active.peer) ? client.isOnline(active.peer) : conversations.get(active.key)?.online;
            subline.textContent = online ? '在线' : '离线';
        } else if (active.detail) {
            subline.textContent = `${active.detail.member_count} 人 · ${active.detail.online_count} 人在线`;
        } else subline.textContent = '';
    }

    async function reloadGroupDetail() {
        if (!active || active.type !== 'group' || !isVisible()) return;
        const current = active;
        const version = ++groupVersion;
        let detail;
        try { detail = await socialApi.group(current.groupId); }
        catch (failure) {
            if (![403, 404].includes(failure.status)) return;
            detail = null;
        }
        if (active !== current || version !== groupVersion || !isVisible()) return;
        current.detail = detail;
        if (detail) {
            const title = detail.display_name || detail.name;
            const item = conversations.get(current.key);
            if (item) { item.title = title; item.group_name = detail.name; }
            setHeading(title);
            renderList();
        }
        applyComposer();
        sidebarKey = null;
        showSidebar();
        setSubline();
    }

    async function onSidebarChange(reason, meta = {}) {
        listLoadedAt = 0;
        listVersion++;
        const key = active?.key;
        if ((meta.key && meta.key !== key) || (meta.groupId && active?.groupId !== Number(meta.groupId))) { refreshListSoon(); return; }
        if (reason === 'unfriended' || reason === 'blocked') {
            friendsLoadedAt = 0;
            friendsVersion++;
            await loadFriends(true).catch(() => {});
            if (active?.key !== key || !isVisible()) return;
            applyComposer();
            sidebarKey = null;
            showSidebar();
        } else if (reason === 'left') {
            await loadConversations(true).catch(() => {});
            if (active?.key !== key) return;
            await reloadGroupDetail();
        } else if (reason === 'remark') {
            await Promise.all([loadFriends(true), loadConversations(true)]).catch(() => {});
        } else {
            await Promise.all([reloadGroupDetail(), loadConversations(true)]).catch(() => {});
        }
    }

    /* ------------------------------------------------------------ interactions */
    layout.addEventListener('click', async (event) => {
        const conv = event.target.closest('[data-conv]');
        if (conv) { void select(conv.dataset.conv); return; }
        const toggle = event.target.closest('[data-chat-toggle]');
        if (toggle) {
            const name = toggle.dataset.chatToggle === 'list' ? 'listOpen' : 'sideOpen';
            const open = layout.dataset[name] !== 'true';
            layout.dataset[name] = String(open);
            toggle.setAttribute('aria-expanded', String(open));
            if (name === 'sideOpen' && open) showSidebar();
            return;
        }
        const jump = event.target.closest('[data-jump]');
        if (jump) {
            const target = scroller.querySelector(`[data-message-id="${jump.dataset.jump}"]`);
            if (target) { target.scrollIntoView({ block: 'center' }); target.classList.add('is-flash'); setTimeout(() => target.classList.remove('is-flash'), 1200); }
            else notify('原消息不在已加载的记录中。', 'info');
            return;
        }
        const action = event.target.closest('[data-msg-act]');
        if (!action || !active) return;
        const id = Number(action.closest('[data-message-id]')?.dataset.messageId);
        const message = active.messages.find((item) => item.id === id);
        if (!message) return;
        if (action.dataset.msgAct === 'reply') composer.setReply(message);
        else if (action.dataset.msgAct === 'recall') {
            action.disabled = true;
            try { await socialApi.recall(id); upsert({ ...message, recalled: true, body: '', media: [] }); }
            catch (failure) { notify(errorText(failure), 'danger'); action.disabled = false; }
        }
    });
    let pinned = true; // the reader is at the newest message; late image/video loads keep it there
    scroller.addEventListener('scroll', () => {
        pinned = nearBottom();
        if (scroller.scrollTop < LOAD_OLDER_PX) void loadOlder();
    }, { passive: true });
    scroller.addEventListener('load', () => { if (pinned) scrollToBottom(); }, true);

    /* ------------------------------------------------------------ pushes */
    const offs = [
        client.on('message', ({ message }) => {
            if (!message) return;
            if (isVisible()) upsert(message);
            else {
                listLoadedAt = 0;
                listVersion++;
                if (active?.key === message.conversation_key) active.dirty = true;
            }
        }),
        client.on('message_recalled', ({ id, conversation_key: key }) => {
            if (!isVisible()) { listLoadedAt = 0; listVersion++; if (active?.key === key) active.dirty = true; return; }
            if (active?.key === key) active.pendingRecalls?.add(id);
            const message = active?.key === key ? active.messages.find((item) => item.id === id) : null;
            if (message) upsert({ ...message, recalled: true, body: '', media: [] });
            else refreshListSoon();
        }),
        client.on('read', ({ conversation_key: key, reader, last_message_id: last }) => {
            if (reader === client.me) {
                const item = conversations.get(key);
                if (item) { item.unread = 0; renderList(); }
                return;
            }
            if (active?.key !== key) return;
            active.peerRead = Math.max(active.peerRead, Number(last) || 0);
            if (isVisible()) updateReceipts();
        }),
        client.on('conversation_preferences', ({ conversation_key: key, muted }) => {
            const item = conversations.get(key);
            if (item) item.muted = Boolean(muted);
            renderList();
            if (active?.key === key) {
                active.muted = Boolean(muted);
                sidebar.updatePreferences(key, { muted: Boolean(muted) });
            }
        }),
        client.on('typing', ({ conversation_key: key, identity }) => {
            if (!isVisible() || active?.key !== key || identity === client.me) return;
            const member = active.detail?.members?.find((item) => item.identity === identity);
            setSubline(active.type === 'dm' ? '对方正在输入…' : `${member?.nickname || member?.name || '有人'}正在输入…`);
            window.clearTimeout(typingTimer);
            typingTimer = window.setTimeout(() => setSubline(), TYPING_SHOW_MS);
        }),
        client.on('nudge', ({ conversation_key: key }) => {
            if (active?.key !== key || !isVisible()) return;
            // The surface's presence motion belongs to the layer; shake the content inside it.
            layout.classList.remove('is-nudged');
            void layout.offsetWidth;
            layout.classList.add('is-nudged');
            window.setTimeout(() => layout.classList.remove('is-nudged'), NUDGE_SHAKE_MS);
        }),
        client.on('presence-changed', () => { if (isVisible()) { renderList(); setSubline(); } }),
        client.on('group_changed', ({ group_id: groupId }) => {
            listLoadedAt = 0;
            listVersion++;
            if (active?.groupId === Number(groupId)) active.dirty = true;
            if (!isVisible()) return;
            refreshListSoon();
            if (active?.type === 'group' && active.groupId === Number(groupId)) void reloadGroupDetail();
        }),
        client.on('friend_removed', async ({ identity }) => {
            friendsLoadedAt = 0;
            friendsVersion++;
            listLoadedAt = 0;
            listVersion++;
            friends.delete(identity);
            if (isVisible() && active?.type === 'dm' && active.peer === identity) { applyComposer(); sidebarKey = null; showSidebar(); }
        }),
        client.on('friend_accepted', () => {
            friendsLoadedAt = 0;
            friendsVersion++;
            listLoadedAt = 0;
            listVersion++;
            if (isVisible()) void loadFriends(true).then(() => { applyComposer(); refreshListSoon(); }).catch(() => {});
        }),
        client.on('counts', ({ conversations: items }) => {
            paintRequestCount();
            if (items) { acceptConversations(items); listLoadedAt = Date.now(); }
        }),
        client.on('status', (status) => {
            listLoadedAt = 0;
            friendsLoadedAt = 0;
            listVersion++;
            friendsVersion++;
            if (active) { active.dirty = true; if (active.loading) active.invalidatedWhileLoading = true; }
            if (status === 'online' && isVisible()) void refreshSession();
        }),
        client.on('visible', () => {
            if (isVisible() && active && (active.dirty || !isFresh(active.loadedAt))) void refreshSession();
            else markReadSoon();
        }),
        lanStore.subscribe((mediaId) => {
            const message = active?.messages.find((item) => (item.media || []).some((media) => media.id === mediaId));
            if (message) { if (isVisible()) upsert(message); else active.dirty = true; }
        }),
    ];

    return {
        async open(key = null, trigger = null) {
            const sequence = ++openSequence;
            if (!isVisible()) {
                windowVersion++;
                if (active) active.resumeScroll = true;
                dialog.open(trigger);
            }
            const version = windowVersion;
            composer.mount();
            paintRequestCount();
            let ready = true;
            try {
                await Promise.all([ensureMarkdown(), loadConversations(), loadFriends()]);
            } catch (failure) {
                ready = false;
                if (version !== windowVersion || sequence !== openSequence || !isVisible()) return;
                if (!order.length) listItems.replaceChildren(el('li', {}, [emptyState('会话加载失败', errorText(failure), 'error')]));
                else notify(errorText(failure, '会话同步失败，请重试。'), 'danger');
            }
            if (version !== windowVersion || sequence !== openSequence || !isVisible()) return;
            const target = key || active?.key || order[0];
            if (target && ready) await select(target);
            else if (!target) {
                scroller.replaceChildren(emptyState('从一个招呼开始', '在左下角打开好友管理，添加朋友或加入群组。'));
                layout.dataset.listOpen = 'true';
                listToggle.setAttribute('aria-expanded', 'true');
                sidebar.clear();
            } else composer.setConversation({ key: target, type: target.startsWith('group:') ? 'group' : 'dm', writable: false, readOnlyReason: '会话同步失败，请关闭后重试。' });
        },
        isOpen: () => dialog.isOpen(),
        destroy() {
            windowVersion++;
            groupVersion++;
            offs.forEach((off) => off());
            markReadSoon.cancel();
            refreshFilesSoon.cancel();
            refreshListSoon.cancel();
            window.clearTimeout(typingTimer);
            sidebar.clear();
            dialog.destroy();
        },
    };
}
