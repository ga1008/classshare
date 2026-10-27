/**
 * 好友浮窗 (social doc §9, §12): tabs 好友 / 新朋友 / 群组 / 推荐 / 添加, plus
 * the separate 黑名单 modal. 推荐、境界解锁阶梯与 AI 找人 live in
 * friends_discover.js; person rows in social_people.js. LQ owns the dialog (createDomainModal), the tabs
 * (lq/navigation.js), buttons, avatars, badges, rows and fields; this module
 * only fetches, renders and dispatches intents.
 *
 * Friend cards are compound choice buttons in a responsive grid, online
 * first; the online/offline presentation is a state hook (`data-online`) that
 * social.css turns into lift + accent backlight vs. desaturated + sunk.
 */
import { createTabs, tabs as enhanceTabs } from '../lq/navigation.js';
import { createForm } from '../lq/forms.js';
import { confirm } from '../lq/dialogs.js';
import { socialApi, errorText } from './social_api.js';
import {
    createSocialDialog, debounce, el, emptyState, formatRelative, lqAvatar, lqBadge, lqButton, notify,
} from './social_dom.js';
import { list, personLead, row, userRow, wandererChip } from './social_people.js';
import { createAiSearch, isLocked, loadScope, lockedState, renderLadder, renderRecommendations } from './friends_discover.js';

const SEARCH_DEBOUNCE_MS = 320;
const REQUEST_MESSAGE_MAX = 100;
const GROUP_NAME_MAX = 30;

function presenceSort(a, b) {
    return Number(b.online) - Number(a.online)
        || (Number(b.unread) || 0) - (Number(a.unread) || 0)
        || String(a.remark || a.name).localeCompare(String(b.remark || b.name), 'zh-CN');
}

export function createFriendsModal(client, { openChat }) {
    const panels = new Map(['friends', 'requests', 'groups', 'recommend', 'add'].map((key) => [key, el('div', { class: 'social-panel', 'data-social-panel': key })]));
    const tabsRoot = createTabs({
        id: 'social-friends-tabs', label: '好友分类', variant: 'pill', size: 'sm', selected: 'friends',
        items: [
            { key: 'friends', label: '好友' }, { key: 'requests', label: '新朋友' },
            { key: 'groups', label: '群组' }, { key: 'recommend', label: '推荐' }, { key: 'add', label: '添加' },
        ],
    }, document, panels);
    const blockButton = lqButton({ label: '黑名单', icon: 'ban', variant: 'ghost', attrs: { 'data-social-open-blocks': '' } });
    const status = el('p', { class: 'social-status', role: 'status', 'aria-live': 'polite' });
    const body = el('div', { class: 'social-friends' }, [tabsRoot]);
    const dialog = createSocialDialog({ id: 'social-friends', title: '好友', size: 'xl', body, className: 'social-friends-dialog' });
    // 黑名单 sits in the dialog head beside the close button; the count line heads the 好友 panel.
    dialog.title.after(blockButton);
    blockButton.addEventListener('click', () => void openBlocks(blockButton));
    const tabsApi = enhanceTabs(tabsRoot, { onChange: ({ key }) => load(key) });
    const state = { friends: [], counts: {}, requests: { incoming: [], outgoing: [] }, groups: [], creating: false };
    let blocks = null;
    let searchAbort = null;

    /* ------------------------------------------------------------ friends */
    function friendCard(friend) {
        const name = friend.remark || friend.name;
        const meta = [friend.remark && friend.remark !== friend.name ? friend.name : '', friend.role_label, friend.mood].filter(Boolean).join(' · ');
        const button = el('button', {
            type: 'button',
            class: `lq-btn lq-btn--${friend.online ? 'glass' : 'ghost'} lq-btn--md lq-domain-choice social-friend__button`,
            'data-lq-component': 'choice', 'data-lq-shape': 'surface',
            'data-social-friend': friend.identity,
            'aria-label': `${name}，${friend.online ? '在线' : '离线'}${friend.unread ? `，${friend.unread} 条未读` : ''}，打开聊天`,
        }, [
            personLead(friend, 56),
            el('span', { class: 'social-friend__copy' }, [
                el('strong', { class: 'social-friend__name', text: name }),
                meta ? el('small', { class: 'social-friend__meta', text: meta }) : null,
                el('small', { class: 'social-friend__presence', text: friend.online ? '在线' : (friend.last_seen_at ? `${formatRelative(friend.last_seen_at)}在线` : '离线') }),
            ]),
            el('span', { class: 'social-friend__marks', 'aria-hidden': 'true' }, [friend.is_wanderer ? wandererChip() : null, lqBadge(friend.unread)]),
        ]);
        // The card itself opens the chat; the 资料 button beside it opens the 个人信息面板.
        const info = lqButton({ icon: 'user', variant: 'ghost', aria: `查看${name}的资料`, attrs: { 'data-user-card': friend.identity, 'aria-haspopup': 'dialog', 'data-social-friend-info': '' } });
        return el('li', { class: 'social-friend', 'data-online': friend.online ? 'true' : 'false' }, [button, info]);
    }

    function renderFriends() {
        const panel = panels.get('friends');
        const items = state.friends.map((item) => ({ ...item, online: client.isOnline(item.identity) || item.online })).sort(presenceSort);
        const online = items.filter((item) => item.online).length;
        status.textContent = items.length ? `在线 ${online} / 共 ${items.length} 位好友` : '';
        panel.replaceChildren(status, items.length
            ? el('ul', { class: 'social-friend-grid', role: 'list', 'aria-label': '好友列表' }, items.map(friendCard))
            : emptyState('还没有好友', '到「添加」里搜索同学、老师或散修，发出第一条好友申请。'));
    }

    /* ------------------------------------------------------------ requests */
    function requestRow(item) {
        const user = item.user || {};
        const incoming = item.direction === 'incoming';
        const actions = incoming
            ? [{ label: '通过', variant: 'prominent', attrs: { 'data-act': 'accept', 'data-id': String(item.id) } },
                { label: '拒绝', variant: 'ghost', attrs: { 'data-act': 'decline', 'data-id': String(item.id) } }]
            : [{ label: '撤回', variant: 'ghost', attrs: { 'data-act': 'withdraw', 'data-id': String(item.id) } }];
        const meta = [user.role_label, user.org_label, item.message ? `「${item.message}」` : '', formatRelative(item.created_at)].filter(Boolean).join(' · ');
        return row({ title: user.name || '用户', meta, actions, lead: personLead(user), trail: user.is_wanderer ? [wandererChip()] : [], identity: user.identity });
    }

    function renderRequests() {
        const { incoming, outgoing } = state.requests;
        const panel = panels.get('requests');
        panel.replaceChildren(
            el('h3', { class: 'social-section-title', text: `收到的申请（${incoming.length}）` }),
            incoming.length ? list('收到的好友申请', incoming.map(requestRow)) : emptyState('暂无新的好友申请'),
            el('h3', { class: 'social-section-title', text: `我发出的申请（${outgoing.length}）` }),
            outgoing.length ? list('我发出的好友申请', outgoing.map(requestRow)) : emptyState('没有等待回复的申请'),
        );
        setTabBadge('requests', incoming.length);
    }

    function setTabBadge(key, count) {
        const tab = tabsRoot.querySelector(`[data-lq-tab="${key}"]`);
        tab?.querySelector('.lq-badge')?.remove();
        const badge = lqBadge(count);
        if (tab && badge) tab.append(badge);
    }

    /* ------------------------------------------------------------ groups */
    function groupRow(group) {
        const meta = [`${group.member_count} 人 · ${group.online_count} 人在线`, group.my_role === 'owner' ? '群主' : (group.my_role === 'admin' ? '管理员' : ''),
            group.last_message?.preview || ''].filter(Boolean).join(' · ');
        return row({
            title: group.name, meta,
            actions: [{ label: '进入群聊', variant: 'soft', attrs: { 'data-act': 'open-group', 'data-key': group.conversation_key } }],
            lead: lqAvatar(group.name, null, 40),
            trail: [lqBadge(group.unread)].filter(Boolean),
        });
    }

    function createGroupForm() {
        const friends = [...state.friends].sort(presenceSort);
        const name = createForm('input', { id: 'social-group-name', label: '群名称', name: 'name', placeholder: '例如：课题讨论小组', maxLength: GROUP_NAME_MAX, required: true });
        const picks = el('fieldset', { class: 'social-group-picks' }, [
            el('legend', { text: '选择好友（至少 1 位）' }),
            ...friends.map((friend, index) => createForm('checkbox', {
                id: `social-group-pick-${index}`, label: friend.remark || friend.name, name: 'member', value: friend.identity,
            })),
        ]);
        const error = el('p', { class: 'social-form-error', role: 'alert', hidden: true });
        const form = el('form', { class: 'social-group-form', novalidate: true }, [
            name, friends.length ? picks : emptyState('先添加好友，才能建群'), error,
            el('div', { class: 'social-form-actions' }, [
                lqButton({ label: '取消', variant: 'ghost', attrs: { 'data-act': 'cancel-group' } }),
                lqButton({ label: '创建群组', variant: 'prominent', type: 'submit', disabled: !friends.length }),
            ]),
        ]);
        form.addEventListener('submit', async (event) => {
            event.preventDefault();
            const groupName = form.elements.name.value.trim();
            const members = [...form.querySelectorAll('input[name="member"]:checked')].map((input) => input.value);
            error.hidden = true;
            if (!groupName || !members.length) {
                error.textContent = !groupName ? '请填写群名称。' : '请至少选择 1 位好友。';
                error.hidden = false;
                return;
            }
            const submit = form.querySelector('[type="submit"]');
            submit.disabled = true;
            try {
                const group = await socialApi.createGroup(groupName, members);
                const skipped = group.skipped || [];
                notify(skipped.length ? `群组已创建，${skipped.length} 人未能加入。` : '群组已创建。', skipped.length ? 'warning' : 'success');
                state.creating = false;
                await load('groups');
            } catch (failure) {
                error.textContent = errorText(failure, '创建失败，请稍后重试。');
                error.hidden = false;
            } finally {
                submit.disabled = false;
            }
        });
        return form;
    }

    function renderGroups() {
        const panel = panels.get('groups');
        const head = el('div', { class: 'social-panel__head' }, [
            el('h3', { class: 'social-section-title', text: `我的群组（${state.groups.length}）` }),
            state.creating ? null : lqButton({ label: '创建群组', icon: 'plus', variant: 'soft', attrs: { 'data-act': 'new-group' } }),
        ]);
        panel.replaceChildren(...[head,
            state.creating ? createGroupForm() : null,
            state.groups.length ? list('我的群组', state.groups.map(groupRow)) : emptyState('还没有加入群组', '从好友中挑选成员即可建群。')].filter(Boolean));
        if (state.creating) panel.querySelector('#social-group-name')?.focus();
    }

    /* ------------------------------------------------------------ add */
    const addMessage = createForm('input', {
        id: 'social-request-message', label: '附言（可选）', name: 'message', maxLength: REQUEST_MESSAGE_MAX,
        placeholder: '介绍一下自己，例如：我是同班的小王',
    });
    const searchField = createForm('input', {
        id: 'social-user-search', label: '搜索用户', type: 'search', name: 'q',
        placeholder: '姓名、学号、工号、邮箱或散修名',
    });
    const ladderSlot = el('div', { class: 'social-add__ladder' });
    const searchBox = el('div', { class: 'social-add__search' }, [el('div', { class: 'social-add__fields' }, [searchField, addMessage])]);
    const results = el('div', { class: 'social-results', 'aria-live': 'polite' });
    const aiSlot = el('div', { class: 'social-add__ai' });
    searchBox.append(results);
    panels.get('add').append(ladderSlot, searchBox, aiSlot);
    // Revealed once /scope answers, so a tier-0 student never sees the search box flash.
    searchBox.hidden = true;
    let aiSearch = null;

    function renderUsers(items, title) {
        if (!items.length) {
            results.replaceChildren(emptyState('没有找到匹配的用户', '只会列出你当前境界可以添加的人。', 'no-results'));
            return;
        }
        results.replaceChildren(el('h3', { class: 'social-section-title', text: title }), list(title, items.map((user) => userRow(user))));
    }

    async function runSearch() {
        const q = searchField.querySelector('input').value.trim();
        searchAbort?.abort();
        searchAbort = new AbortController();
        if (!q) {
            results.replaceChildren(el('p', { class: 'social-hint', text: '输入姓名、学号、工号或散修名开始搜索；也可以看看「推荐」。' }));
            return;
        }
        try {
            const data = await socialApi.search(q, searchAbort.signal);
            if (data?.locked) { results.replaceChildren(emptyState(data.locked_hint || '当前境界尚不能添加好友', '')); return; }
            renderUsers(data.items || [], `搜索「${q}」`);
        } catch (failure) {
            if (failure?.name === 'AbortError') return;
            results.replaceChildren(emptyState('搜索失败', errorText(failure), 'error'));
        }
    }
    const debouncedSearch = debounce(runSearch, SEARCH_DEBOUNCE_MS);
    searchField.querySelector('input').addEventListener('input', debouncedSearch);

    /** 添加 tab: ladder on top; tier 0 shows the locked state instead of the search box. */
    async function loadAdd(force = false) {
        let scope = null;
        try { scope = await loadScope(force); } catch { scope = null; }
        const ladder = renderLadder(scope);
        const locked = isLocked(scope);
        ladderSlot.replaceChildren(...[ladder, locked ? lockedState() : null].filter(Boolean));
        ladderSlot.hidden = !ladderSlot.childElementCount;
        searchBox.hidden = locked;
        if (scope?.ai_search && !locked) {
            aiSearch ||= createAiSearch();
            if (!aiSlot.contains(aiSearch)) aiSlot.replaceChildren(aiSearch);
        } else aiSlot.replaceChildren();
        aiSlot.hidden = !aiSlot.childElementCount;
        if (!locked) await runSearch();
    }

    /* ------------------------------------------------------------ data */
    async function load(key = currentKey()) {
        const panel = panels.get(key);
        panel.setAttribute('aria-busy', 'true');
        try {
            if (key === 'friends') {
                const data = await socialApi.friends();
                state.friends = data.items || [];
                state.counts = data.counts || {};
                renderFriends();
                setTabBadge('requests', state.counts.pending_in || 0);
            } else if (key === 'requests') {
                state.requests = await socialApi.requests();
                renderRequests();
            } else if (key === 'groups') {
                const [groups, friends] = await Promise.all([socialApi.groups(), state.friends.length ? null : socialApi.friends()]);
                state.groups = groups.items || [];
                if (friends) state.friends = friends.items || [];
                renderGroups();
            } else if (key === 'recommend') {
                await renderRecommendations(panel);
            } else {
                await loadAdd(true);
            }
        } catch (failure) {
            panel.replaceChildren(emptyState('加载失败', errorText(failure), 'error'));
        } finally {
            panel.removeAttribute('aria-busy');
        }
    }

    function currentKey() {
        return tabsRoot.querySelector('[role="tab"][aria-selected="true"]')?.dataset.lqTab || 'friends';
    }

    /** A row's request button becomes a quiet, disabled state in place (search/推荐/AI results stay put). */
    function settle(button, label) {
        const text = button.querySelector('.lq-btn__label');
        if (text) text.textContent = label; else button.textContent = label;
        button.classList.replace('lq-btn--prominent', 'lq-btn--ghost');
        button.removeAttribute('data-act');
        button.dataset.settled = 'true';
    }

    async function act(button) {
        const action = button.dataset.act;
        const id = button.dataset.id;
        const identity = button.dataset.identity;
        button.disabled = true;
        try {
            if (action === 'accept') { await socialApi.acceptRequest(id); notify('已成为好友。', 'success'); }
            else if (action === 'decline') { await socialApi.declineRequest(id); }
            else if (action === 'withdraw') { await socialApi.withdrawRequest(id); }
            else if (action === 'request') {
                const message = addMessage.querySelector('input').value.trim();
                const result = await socialApi.sendRequest(identity, message);
                notify(result.status === 'accepted' ? '对方也申请过你，已直接成为好友。' : '好友申请已发送。', 'success');
                settle(button, result.status === 'accepted' ? '已成为好友' : '已申请');
                client.refreshCounts();
                return;
            } else if (action === 'accept-user') {
                const pending = (await socialApi.requests()).incoming.find((item) => item.user?.identity === identity);
                if (pending) { await socialApi.acceptRequest(pending.id); notify('已成为好友。', 'success'); }
                settle(button, '已成为好友');
                client.refreshCounts();
                return;
            } else if (action === 'chat-user' || action === 'open-group') {
                const key = button.dataset.key || (identity ? `dm:${[client.me, identity].sort().join('|')}` : '');
                if (key) { await dialog.close('button'); openChat(key); }
                return;
            } else if (action === 'new-group') { state.creating = true; renderGroups(); return; }
            else if (action === 'cancel-group') { state.creating = false; renderGroups(); return; }
            client.refreshCounts();
            await load();
        } catch (failure) {
            notify(errorText(failure), 'danger');
        } finally {
            if (button.isConnected && !button.dataset.settled) button.disabled = false;
        }
    }

    body.addEventListener('click', (event) => {
        const friend = event.target.closest('[data-social-friend]');
        if (friend) {
            const item = state.friends.find((entry) => entry.identity === friend.dataset.socialFriend);
            if (item) void dialog.close('button').then(() => openChat(item.conversation_key));
            return;
        }
        const action = event.target.closest('[data-act]');
        if (action && body.contains(action)) { event.preventDefault(); void act(action); }
    });

    /* ------------------------------------------------------------ blacklist */
    async function openBlocks(trigger) {
        if (!blocks) blocks = createBlocksModal();
        blocks.open(trigger);
    }

    const offs = [
        client.on('presence-changed', () => { if (dialog.isOpen() && currentKey() === 'friends') renderFriends(); }),
        client.on('friend_request', () => { if (dialog.isOpen()) void load(); }),
        client.on('friend_accepted', () => { if (dialog.isOpen()) void load(); }),
        client.on('friend_removed', () => { if (dialog.isOpen()) void load(); }),
        client.on('group_changed', () => { if (dialog.isOpen() && currentKey() === 'groups') void load('groups'); }),
        client.on('counts', ({ pendingIn }) => setTabBadge('requests', pendingIn)),
    ];

    return {
        open(trigger, tab = null) {
            dialog.open(trigger);
            if (tab && tab !== currentKey()) tabsApi.select(tab);
            else void load();
        },
        isOpen: () => dialog.isOpen(),
        destroy() { offs.forEach((off) => off()); tabsApi.destroy(); },
    };
}

/** 黑名单浮窗: list + 移出; adding happens from a friend's chat sidebar. */
export function createBlocksModal() {
    const content = el('div', { class: 'social-blocks' });
    const dialog = createSocialDialog({ id: 'social-blocks', title: '黑名单', size: 'md', body: content });
    async function render() {
        content.replaceChildren(el('p', { class: 'social-hint', text: '被拉黑的人不能加你、不能私聊你，也不能把你拉进群。好友侧栏里可以拉黑。' }));
        try {
            const items = (await socialApi.blocks()).items || [];
            content.append(items.length ? list('黑名单', items.map((item) => row({
                title: item.name || item.identity,
                meta: [item.created_at ? `${formatRelative(item.created_at)}拉黑` : '', item.scope === 'social' ? '仅限好友与群聊，课堂私信不受影响' : ''].filter(Boolean).join(' · '),
                actions: [{ label: '移出', variant: 'soft', attrs: { 'data-unblock': item.identity, 'data-name': item.name || '' } }],
                lead: lqAvatar(item.name, item.avatar_url, 40),
            }))) : emptyState('黑名单是空的'));
        } catch (failure) {
            content.append(emptyState('加载失败', errorText(failure), 'error'));
        }
    }
    content.addEventListener('click', async (event) => {
        const button = event.target.closest('[data-unblock]');
        if (!button) return;
        const ok = await confirm({ title: '移出黑名单', message: `确定把「${button.dataset.name || '该用户'}」移出黑名单吗？移出后不会自动恢复好友关系。`, confirmLabel: '移出' });
        if (!ok) return;
        button.disabled = true;
        try { await socialApi.unblock(button.dataset.unblock); notify('已移出黑名单。', 'success'); await render(); }
        catch (failure) { notify(errorText(failure), 'danger'); button.disabled = false; }
    });
    return {
        open(trigger) { dialog.open(trigger); void render(); },
        refresh: render,
    };
}
