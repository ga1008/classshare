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

function peerOf(key, me) {
    const parties = String(key).slice(3).split('|');
    return parties.find((item) => item !== me) || parties[0] || '';
}

export function createChatModal(client, { openFriends }) {
    const listItems = el('ul', { class: 'social-convs', role: 'list' });
    const listPane = el('nav', { class: 'social-chat__list', 'aria-label': '会话列表' }, [
        el('div', { class: 'social-chat__list-head' }, [
            el('strong', { text: '会话' }),
            lqButton({ icon: 'users', variant: 'ghost', aria: '打开好友列表', onClick: () => openFriends() }),
        ]),
        listItems,
    ]);
    const heading = el('strong', { class: 'social-chat__title', text: '' });
    const subline = el('small', { class: 'social-chat__subline', 'aria-live': 'polite' });
    const listToggle = lqButton({ icon: 'menu', variant: 'ghost', aria: '显示会话列表', attrs: { 'data-chat-toggle': 'list', 'aria-expanded': 'false' } });
    const sideToggle = lqButton({ icon: 'info', variant: 'ghost', aria: '显示资料与设置', attrs: { 'data-chat-toggle': 'side', 'aria-expanded': 'false' } });
    const scroller = el('div', { class: 'social-chat__messages', role: 'log', 'aria-label': '消息记录', tabindex: '0', 'data-ls-lightbox-scope': '' });
    const composer = createChatComposer({ client, onSent: (message) => upsert(message, true) });
    const main = el('section', { class: 'social-chat__main', 'aria-label': '消息' }, [
        el('header', { class: 'social-chat__head' }, [listToggle, el('div', { class: 'social-chat__heading' }, [heading, subline]), sideToggle]),
        scroller,
        el('div', { class: 'social-chat__composer' }, [composer.element]),
    ]);
    const sidebar = createChatSidebar({ client, onChanged: (reason) => onSidebarChange(reason) });
    const side = el('aside', { class: 'social-chat__side', 'aria-label': '资料与设置' }, [sidebar.element]);
    const layout = el('div', { class: 'social-chat', 'data-list-open': 'false', 'data-side-open': 'false' }, [listPane, main, side]);
    const dialog = createSocialDialog({
        id: 'social-chat', title: '聊天', size: 'full', body: layout, className: 'social-chat-dialog',
        onClose: () => { client.focus(null); active = null; },
    });

    const conversations = new Map();
    let order = [];
    const friends = new Map();
    let active = null;
    let typingTimer = 0;
    let loadingOlder = false;

    /* ------------------------------------------------------------ list */
    function renderList() {
        if (!order.length) {
            listItems.replaceChildren(el('li', {}, [emptyState('还没有会话', '在好友列表中点一位好友开始聊天。')]));
            return;
        }
        listItems.replaceChildren(...order.map((key) => {
            const item = conversations.get(key);
            const online = item.type === 'dm' && (client.isOnline(item.peer_identity) || item.online);
            const current = active?.key === key;
            return el('li', { class: 'social-conv' }, [el('button', {
                type: 'button', class: 'lq-btn lq-btn--ghost lq-btn--md lq-domain-choice social-conv__button',
                'data-lq-component': 'choice', 'data-lq-shape': 'surface', 'data-conv': key,
                'aria-current': current ? 'true' : null,
            }, [
                el('span', { class: 'social-avatar', 'data-online': item.type === 'dm' ? String(online) : null }, [
                    lqAvatar(item.title, item.avatar_url, 40),
                    item.type === 'dm' ? el('span', { class: 'social-avatar__dot', 'aria-hidden': 'true' }) : null,
                ]),
                el('span', { class: 'social-conv__copy' }, [
                    el('strong', { text: `${item.title}${item.muted ? '（免打扰）' : ''}` }),
                    el('small', { text: item.last_message?.preview || (item.type === 'group' ? '群聊' : '开始聊天吧') }),
                ]),
                el('span', { class: 'social-conv__marks' }, [
                    el('small', { text: item.last_message?.at ? formatRelative(item.last_message.at) : '' }),
                    lqBadge(item.unread, item.muted ? 'neutral' : 'danger'),
                ]),
            ])]);
        }));
    }

    async function loadConversations() {
        const data = await socialApi.conversations();
        conversations.clear();
        order = [];
        for (const item of data.items || []) { conversations.set(item.conversation_key, item); order.push(item.conversation_key); }
        renderList();
    }

    async function loadFriends() {
        const data = await socialApi.friends();
        friends.clear();
        for (const item of data.items || []) friends.set(item.identity, item);
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
        if (fresh && !isMine(message, client.me) && message.message_type !== 'system' && !(active?.key === key && !document.hidden)) item.unread = (Number(item.unread) || 0) + 1;
        order = [key, ...order.filter((entry) => entry !== key)];
        renderList();
    }
    const refreshFilesSoon = debounce((groupId) => sidebar.refreshFiles(groupId), 800);
    const refreshListSoon = debounce(() => { void loadConversations().catch(() => {}); }, 500);

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

    function buildNodes(messages, previous = null) {
        const nodes = [];
        const ctx = ctxFor();
        let last = previous;
        for (const message of messages) {
            if (!last || dayKey(last.created_at) !== dayKey(message.created_at)) nodes.push(daySeparator(message.created_at));
            nodes.push(renderMessage(message, ctx, { connected: joinsPrevious(last, message) }));
            last = message;
        }
        return nodes;
    }

    function renderMessages() {
        const nodes = buildNodes(active.messages);
        const top = active.hasMore ? el('p', { class: 'social-chat__more', text: '上滑加载更早的消息' }) : el('p', { class: 'social-chat__more', text: '没有更早的消息了' });
        scroller.replaceChildren(top, ...(nodes.length ? nodes : [emptyState('还没有消息', '打个招呼吧。')]));
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
        const nodes = [...scroller.querySelectorAll('.social-msg--out [data-receipt]')];
        nodes.forEach((node) => { node.textContent = ''; });
        const last = nodes.at(-1);
        if (!last) return;
        const id = Number(last.closest('[data-message-id]')?.dataset.messageId) || 0;
        last.textContent = active.peerRead >= id ? '已读' : '已送达';
    }

    /** Insert or replace a message of the active conversation. */
    function upsert(message, fromMe = false) {
        const isActive = Boolean(active) && message.conversation_key === active.key;
        const index = isActive ? active.messages.findIndex((item) => item.id === message.id) : -1;
        bumpConversation(message, index < 0 && !message.recalled);
        if (!isActive) return;
        if (index >= 0) {
            active.messages[index] = message;
            const node = scroller.querySelector(`[data-message-id="${message.id}"]`);
            const previous = active.messages[index - 1] || null;
            node?.replaceWith(renderMessage(message, ctxFor(), { connected: joinsPrevious(previous, message) }));
            updateReceipts();
            return;
        }
        if (active.type === 'group' && (message.media || []).length) refreshFilesSoon(active.groupId);
        const stick = fromMe || nearBottom();
        const previous = active.messages.at(-1) || null;
        active.messages.push(message);
        scroller.querySelector('.social-empty')?.remove();
        scroller.append(...buildNodes([message], previous));
        updateReceipts();
        if (stick) scrollToBottom();
        markReadSoon();
    }

    async function loadOlder() {
        if (!active?.hasMore || loadingOlder || !active.messages.length) return;
        loadingOlder = true;
        const key = active.key;
        try {
            const data = await socialApi.messages(key, active.messages[0].id, PAGE);
            if (active?.key !== key) return;
            const before = scroller.scrollHeight;
            active.messages = [...(data.items || []), ...active.messages];
            active.hasMore = Boolean(data.has_more);
            renderMessages();
            scroller.scrollTop += scroller.scrollHeight - before;
        } catch (failure) {
            notify(errorText(failure, '更早的消息加载失败。'), 'danger');
        } finally {
            loadingOlder = false;
        }
    }

    const markReadSoon = debounce(async () => {
        if (!active || !dialog.isOpen() || document.hidden || !active.messages.length) return;
        const last = active.messages.at(-1).id;
        if (last <= (active.readSent || 0)) return;
        active.readSent = last;
        const item = conversations.get(active.key);
        if (item) { item.unread = 0; renderList(); }
        try { await socialApi.read(active.key, last); client.refreshCounts(); } catch { active.readSent = 0; }
    }, READ_DEBOUNCE_MS);

    /* ------------------------------------------------------------ select */
    async function select(key) {
        const item = ensureConversation(key);
        const type = key.startsWith('group:') ? 'group' : 'dm';
        active = {
            key, type, peer: type === 'dm' ? peerOf(key, client.me) : null, groupId: type === 'group' ? Number(key.slice(6)) : null,
            messages: [], hasMore: false, peerRead: 0, detail: null, readSent: 0,
        };
        layout.dataset.type = type;
        layout.dataset.listOpen = 'false';
        listToggle.setAttribute('aria-expanded', 'false');
        heading.textContent = item.title;
        dialog.title.textContent = item.title;
        setSubline();
        renderList();
        client.focus(key);
        scroller.replaceChildren(el('p', { class: 'social-hint', text: '正在读取消息…' }));
        const current = active;
        try {
            const [page, detail] = await Promise.all([
                socialApi.messages(key, 0, PAGE),
                type === 'group' ? socialApi.group(current.groupId) : null,
            ]);
            if (active !== current) return;
            current.messages = page.items || [];
            current.hasMore = Boolean(page.has_more);
            current.peerRead = Number(page.peer_read_message_id) || 0;
            current.detail = detail;
            if (detail) { heading.textContent = detail.name; dialog.title.textContent = detail.name; }
            renderMessages();
            scrollToBottom();
            applyComposer();
            showSidebar();
            setSubline();
            markReadSoon();
            composer.focus();
        } catch (failure) {
            if (active !== current) return;
            scroller.replaceChildren(emptyState('消息加载失败', errorText(failure), 'error'));
            composer.setConversation({ key, type, writable: false, readOnlyReason: errorText(failure) });
        }
    }

    function applyComposer() {
        if (!active) return;
        if (active.type === 'dm') {
            const writable = friends.has(active.peer);
            composer.setConversation({ key: active.key, type: 'dm', peer: active.peer, writable, readOnlyReason: writable ? '' : '你们已不是好友，无法继续发送消息。' });
        } else {
            composer.setConversation({
                key: active.key, type: 'group', members: active.detail?.members || [], myRole: active.detail?.my_role,
                writable: Boolean(active.detail), readOnlyReason: active.detail ? '' : '你不在该群聊中。',
            });
        }
    }

    function showSidebar() {
        if (!active) return;
        if (active.type === 'dm') sidebar.showDm(active.peer, { key: active.key, writable: friends.has(active.peer) });
        else if (active.detail) sidebar.showGroup(active.detail, { key: active.key });
        else sidebar.clear();
    }

    function setSubline(text = null) {
        if (!active) { subline.textContent = ''; return; }
        if (text) { subline.textContent = text; return; }
        if (active.type === 'dm') {
            const online = client.isOnline(active.peer) || conversations.get(active.key)?.online;
            subline.textContent = online ? '在线' : '离线';
        } else if (active.detail) {
            subline.textContent = `${active.detail.member_count} 人 · ${active.detail.online_count} 人在线`;
        } else subline.textContent = '';
    }

    async function reloadGroupDetail() {
        if (!active || active.type !== 'group') return;
        const current = active;
        try { current.detail = await socialApi.group(current.groupId); }
        catch { current.detail = null; }
        if (active !== current) return;
        applyComposer();
        showSidebar();
        setSubline();
    }

    async function onSidebarChange(reason) {
        if (reason === 'unfriended' || reason === 'blocked') {
            await loadFriends().catch(() => {});
            applyComposer();
            showSidebar();
        } else if (reason === 'left') {
            await loadConversations().catch(() => {});
            await reloadGroupDetail();
        } else if (reason === 'remark') {
            await Promise.all([loadFriends(), loadConversations()]).catch(() => {});
        } else {
            await Promise.all([reloadGroupDetail(), loadConversations()]).catch(() => {});
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
        client.on('message', ({ message }) => { if (message && dialog.isOpen()) upsert(message); }),
        client.on('message_recalled', ({ id, conversation_key: key }) => {
            if (!dialog.isOpen()) return;
            const message = active?.key === key ? active.messages.find((item) => item.id === id) : null;
            if (message) upsert({ ...message, recalled: true, body: '', media: [] });
            else refreshListSoon();
        }),
        client.on('read', ({ conversation_key: key, reader, last_message_id: last }) => {
            if (active?.key !== key || reader === client.me) return;
            active.peerRead = Math.max(active.peerRead, Number(last) || 0);
            updateReceipts();
        }),
        client.on('typing', ({ conversation_key: key, identity }) => {
            if (active?.key !== key || identity === client.me) return;
            const member = active.detail?.members?.find((item) => item.identity === identity);
            setSubline(active.type === 'dm' ? '对方正在输入…' : `${member?.nickname || member?.name || '有人'}正在输入…`);
            window.clearTimeout(typingTimer);
            typingTimer = window.setTimeout(() => setSubline(), TYPING_SHOW_MS);
        }),
        client.on('nudge', ({ conversation_key: key }) => {
            if (active?.key !== key || !dialog.isOpen()) return;
            // The surface's presence motion belongs to the layer; shake the content inside it.
            layout.classList.remove('is-nudged');
            void layout.offsetWidth;
            layout.classList.add('is-nudged');
            window.setTimeout(() => layout.classList.remove('is-nudged'), NUDGE_SHAKE_MS);
        }),
        client.on('presence-changed', () => { if (dialog.isOpen()) { renderList(); setSubline(); } }),
        client.on('group_changed', ({ group_id: groupId }) => {
            if (!dialog.isOpen()) return;
            refreshListSoon();
            if (active?.type === 'group' && active.groupId === Number(groupId)) void reloadGroupDetail();
        }),
        client.on('friend_removed', async ({ identity }) => {
            if (!dialog.isOpen()) return;
            friends.delete(identity);
            if (active?.type === 'dm' && active.peer === identity) { applyComposer(); showSidebar(); }
        }),
        client.on('friend_accepted', () => { if (dialog.isOpen()) void loadFriends().then(() => { applyComposer(); refreshListSoon(); }).catch(() => {}); }),
        client.on('visible', () => markReadSoon()),
        lanStore.subscribe((mediaId) => {
            const message = active?.messages.find((item) => (item.media || []).some((media) => media.id === mediaId));
            if (message) upsert(message);
        }),
    ];

    return {
        async open(key = null, trigger = null) {
            if (!dialog.isOpen()) dialog.open(trigger);
            composer.mount();
            try {
                await Promise.all([ensureMarkdown(), loadConversations(), loadFriends()]);
            } catch (failure) {
                listItems.replaceChildren(el('li', {}, [emptyState('会话加载失败', errorText(failure), 'error')]));
            }
            const target = key || active?.key || order[0];
            if (target) await select(target);
            else { scroller.replaceChildren(emptyState('选择一个会话', '从左侧选择会话，或在好友列表中点一位好友。')); sidebar.clear(); }
        },
        isOpen: () => dialog.isOpen(),
        destroy() { offs.forEach((off) => off()); },
    };
}
