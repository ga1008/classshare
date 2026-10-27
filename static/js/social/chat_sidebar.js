/**
 * Chat window right sidebar.
 *   DM    → profile: avatar, presence, mood, 修为摘要, 最新动态, 备注, 拍一拍,
 *           删除好友, 拉黑, 课堂私信 / 散修主页 links.
 *   Group → 公告 (editable by owner/admin), 群名, members online-first with
 *           role actions per my role, 群文件 by kind, 我的群昵称/免打扰, 退出/解散.
 * Every destructive intent goes through the shared LQ confirm dialog.
 */
import { createComponent } from '../lq/components.js';
import { createForm } from '../lq/forms.js';
import { confirm as confirmDialog } from '../lq/dialogs.js';
import { bindMenu, createMenu } from '../lq/menus.js';
import { socialApi, errorText } from './social_api.js';
import { el, emptyState, formatRelative, lqAvatar, lqButton, notify } from './social_dom.js';
import { createGroupSettings } from './group_settings.js';
import { createGroupInvite } from './group_invite.js';
import { createGroupFiles } from './group_files.js';

const ROLE_LABEL = { owner: '群主', admin: '管理员', member: '成员' };
const REMARK_MAX = 30;

const section = (title, children) => el('section', { class: 'social-side__section' }, [
    el('h3', { class: 'social-section-title', text: title }), ...children,
]);

function safeLink(url) {
    const text = String(url || '');
    return text.startsWith('/') && !text.startsWith('//') ? text : null;
}

export function createChatSidebar({ client, onChanged }) {
    const element = el('div', { class: 'social-side__inner' });
    let token = 0;
    let group = null;
    let menus = [];
    const clearMenus = () => { menus.forEach((menu) => menu.destroy()); menus = []; };
    const clearGroup = () => { group?.settings.destroy(); group?.invite.destroy(); group?.files.destroy(); group = null; };

    async function guarded(button, task) {
        if (button) button.disabled = true;
        try { await task(); } catch (failure) { notify(errorText(failure), 'danger'); }
        finally { if (button?.isConnected) button.disabled = false; }
    }

    /* ------------------------------------------------------------ DM */
    async function showDm(identity, ctx) {
        clearMenus();
        clearGroup();
        const ticket = ++token;
        element.replaceChildren(el('p', { class: 'social-hint', text: '正在读取资料…' }));
        let profile;
        try { profile = await socialApi.profile(identity); } catch (failure) {
            if (ticket === token) element.replaceChildren(emptyState('资料暂时无法读取', errorText(failure), 'error'));
            return;
        }
        if (ticket !== token) return;
        const presenceKnown = profile.online !== null && profile.online !== undefined;
        const online = client.isOnline(identity) || Boolean(profile.online);
        const head = el('div', { class: 'social-side__profile' }, [
            el('span', { class: 'social-avatar', 'data-online': online ? 'true' : 'false' }, [
                lqAvatar(profile.name, profile.avatar_url, 56), el('span', { class: 'social-avatar__dot', 'aria-hidden': 'true' }),
            ]),
            el('div', { class: 'social-side__who' }, [
                el('strong', { text: profile.remark || profile.name }),
                el('small', { text: [profile.remark ? profile.name : '', profile.role_label, profile.org_label].filter(Boolean).join(' · ') }),
                presenceKnown || online ? el('small', { text: online ? '在线' : (profile.last_seen_at ? `${formatRelative(profile.last_seen_at)}在线` : '离线') }) : null,
                profile.is_wanderer ? createComponent('chip', { label: '散修', tone: 'info', size: 'sm' }) : null,
            ]),
        ]);
        const blocks = [head];
        if (profile.mood) blocks.push(el('p', { class: 'social-side__mood', text: `心情：${profile.mood}` }));
        if (profile.cultivation) {
            const c = profile.cultivation;
            blocks.push(section('修为', [el('div', { class: 'social-side__cultivation' }, [
                c.emblem ? el('img', { src: c.emblem, alt: '', width: '32', height: '32', loading: 'lazy' }) : null,
                el('span', { text: [c.tier_name, c.score !== null && c.score !== undefined ? `修为 ${Number(c.score).toFixed(1).replace(/\.0$/, '')}` : ''].filter(Boolean).join(' · ') }),
            ])]));
        }
        const recent = (profile.recent || []).filter((item) => safeLink(item.url));
        blocks.push(section('最新动态', [recent.length
            ? el('ul', { class: 'social-side__links', role: 'list' }, recent.map((item) => el('li', {}, [
                el('a', { href: safeLink(item.url), text: item.title || '动态' }), el('small', { text: formatRelative(item.at) }),
            ])))
            : el('p', { class: 'social-hint', text: '暂无公开动态' })]));

        if (profile.relation === 'friend') {
            const remark = createForm('input', { id: 'social-remark', label: '备注', name: 'remark', value: profile.remark || '', maxLength: REMARK_MAX, placeholder: '给好友起个备注' });
            const remarkForm = el('form', { class: 'social-inline-form' }, [remark, lqButton({ label: '保存', variant: 'soft', type: 'submit' })]);
            remarkForm.addEventListener('submit', (event) => {
                event.preventDefault();
                void guarded(remarkForm.querySelector('[type="submit"]'), async () => {
                    await socialApi.setRemark(identity, remarkForm.elements.remark.value.trim());
                    notify('备注已保存。', 'success');
                    onChanged?.('remark');
                });
            });
            blocks.push(remarkForm);
            const muted = createForm('switch', { id: 'social-dm-muted', label: '消息免打扰', name: 'muted', checked: Boolean(ctx.muted) });
            const input = muted.querySelector('input');
            input.addEventListener('change', async () => {
                const value = input.checked;
                input.disabled = true;
                try {
                    const result = await socialApi.updateConversationPreferences(ctx.key, { muted: value });
                    client.setMuted(ctx.key, result.muted);
                    notify(result.muted ? '已开启免打扰，消息仍会收入消息中心和未读数字。' : '已恢复消息提醒。', 'success');
                    onChanged?.('preferences');
                } catch (failure) { input.checked = !value; notify(errorText(failure), 'danger'); }
                finally { input.disabled = false; }
            });
            blocks.push(section('消息提醒', [muted, el('p', { class: 'social-hint', text: '免打扰只关闭浮窗提醒，消息中心与未读数字照常保留。' })]));
        }
        const links = el('div', { class: 'social-side__actions' }, [
            ctx.writable ? lqButton({ label: '拍一拍', variant: 'soft', onClick: (event) => guarded(event.currentTarget, () => socialApi.nudge(ctx.key)) }) : null,
            safeLink(profile.private_message_url) ? lqButton({ label: '课堂私信记录', variant: 'ghost', href: safeLink(profile.private_message_url) }) : null,
            safeLink(profile.homepage_url) ? lqButton({ label: '散修主页', variant: 'ghost', href: safeLink(profile.homepage_url) }) : null,
        ]);
        const danger = el('div', { class: 'social-side__actions social-side__actions--danger' }, [
            profile.relation === 'friend' ? lqButton({ label: '删除好友', variant: 'ghost', onClick: (event) => removeFriend(event.currentTarget, profile) }) : null,
            // Teachers are classroom duty contacts and can never be blocked (message_center rule).
            profile.relation !== 'blocked' ? lqButton({ label: '拉黑', variant: 'destructive', onClick: (event) => blockUser(event.currentTarget, profile) }) : null,
        ]);
        blocks.push(links, danger);
        element.replaceChildren(...blocks);
    }

    async function removeFriend(button, profile) {
        if (!await confirmDialog({ title: '删除好友', message: `删除后聊天记录会保留，但不能再给「${profile.name}」发消息。`, confirmLabel: '删除', danger: true })) return;
        await guarded(button, async () => { await socialApi.deleteFriend(profile.identity); notify('已删除好友。', 'success'); onChanged?.('unfriended'); });
    }

    async function blockUser(button, profile) {
        if (!await confirmDialog({ title: '拉黑', message: `拉黑「${profile.name}」会同时解除好友，对方无法再加你或给你发消息。可在黑名单中移出。`, confirmLabel: '拉黑', danger: true })) return;
        await guarded(button, async () => { await socialApi.block(profile.identity); notify('已拉黑。', 'success'); onChanged?.('blocked'); });
    }

    /* ------------------------------------------------------------ group */
    function memberRow(member, detail, me) {
        const myRole = detail.my_role;
        const isMe = member.identity === me;
        const actions = [];
        if (!isMe && myRole === 'owner') {
            actions.push({ id: 'admin', label: member.group_role === 'admin' ? '取消管理员' : '设为管理员', attrs: { 'data-member-act': 'admin', 'data-identity': member.identity, 'data-admin': member.group_role === 'admin' ? '0' : '1' } });
            actions.push({ id: 'transfer', label: '转让群主', attrs: { 'data-member-act': 'transfer', 'data-identity': member.identity, 'data-name': member.name } });
        }
        if (!isMe && (myRole === 'owner' || (myRole === 'admin' && member.group_role === 'member'))) {
            actions.push({ id: 'kick', label: '移出', danger: true, attrs: { 'data-member-act': 'kick', 'data-identity': member.identity, 'data-name': member.name } });
        }
        if (!isMe) actions.unshift({ id: 'nudge', label: '拍一拍', attrs: { 'data-member-act': 'nudge', 'data-identity': member.identity } });
        let actionSlot = null;
        if (actions.length) {
            const trigger = lqButton({ icon: 'ellipsis', aria: `${member.nickname || member.name}的更多操作`, variant: 'ghost', attrs: { 'data-member-menu': member.identity } });
            const menu = createMenu({ label: `${member.nickname || member.name}的操作`, items: actions });
            actionSlot = el('span', { class: 'social-member__actions' }, [trigger, menu]);
            menus.push(bindMenu(trigger, menu, { placement: 'bottom-end', onAction: (_id, button) => memberAction(button, detail, { key: detail.conversation_key || `group:${detail.id}` }) }));
        }
        return el('li', { class: 'social-member', 'data-online': member.online ? 'true' : 'false' }, [
            el('span', { class: 'social-avatar', 'data-online': member.online ? 'true' : 'false' }, [
                lqAvatar(member.name, member.avatar_url, 32), el('span', { class: 'social-avatar__dot', 'aria-hidden': 'true' }),
            ]),
            el('span', { class: 'social-member__copy' }, [
                isMe ? el('strong', { text: `${member.nickname || member.name}（我）` })
                    : lqButton({ label: member.nickname || member.name, variant: 'link', attrs: { 'data-user-card': member.identity, 'aria-haspopup': 'dialog', 'data-social-member-name': '' } }),
                el('small', { text: [ROLE_LABEL[member.group_role] || '', member.online ? '在线' : '离线', member.is_wanderer ? '散修' : ''].filter(Boolean).join(' · ') }),
            ]),
            actionSlot,
        ]);
    }

    function groupCard(title, children, action = null, name = '') {
        return el('section', { class: 'lq-surface social-group-card', 'data-lq-component': 'surface', 'data-lq-surface': 'embedded', 'data-group-card': name }, [
            el('div', { class: 'social-group-card__head' }, [el('h3', { class: 'social-section-title', text: title }), action]),
            ...children,
        ]);
    }

    function showGroup(detail, ctx) {
        token += 1;
        const id = Number(detail.id);
        if (group?.id !== id) {
            clearMenus(); clearGroup();
            const current = { id, detail, key: ctx.key };
            const changed = (reason, payload) => {
                if (group !== current) return;
                if (payload?.detail) showGroup(payload.detail, { key: current.key });
                onChanged?.(reason, payload || { groupId: id, key: current.key });
            };
            const unavailable = () => {
                if (group !== current) return;
                clearMenus(); clearGroup(); element.replaceChildren(emptyState('群聊暂不可用', '你可能已离开本群，正在重新确认成员身份。'));
                onChanged?.('unavailable', { groupId: id, key: current.key });
            };
            current.settings = createGroupSettings({ client, detail, onChanged: changed, onUnavailable: unavailable });
            current.invite = createGroupInvite({ detail, onChanged: changed, onUnavailable: unavailable });
            current.files = createGroupFiles({ groupId: id, onUnavailable: unavailable });
            current.title = el('strong', { class: 'social-group-card__title' });
            current.canonical = el('small', { class: 'social-hint' });
            current.counts = el('small', { class: 'social-hint' });
            current.avatar = el('span');
            const settings = lqButton({ label: '设置', variant: 'ghost', attrs: { 'data-group-settings-open': '', 'aria-haspopup': 'dialog' }, onClick: event => current.settings.open(event.currentTarget) });
            const invite = lqButton({ label: '邀请好友', icon: 'plus', variant: 'soft', attrs: { 'data-group-invite-open': '', 'aria-haspopup': 'dialog' }, onClick: event => current.invite.open(event.currentTarget) });
            current.memberTitle = el('span', { class: 'social-hint' });
            current.members = el('ul', { class: 'social-members', role: 'list', 'aria-label': '群成员' });
            group = current;
            element.replaceChildren(
                groupCard('群信息', [el('div', { class: 'social-side__profile' }, [current.avatar,
                    el('div', { class: 'social-side__who' }, [current.title, current.canonical, current.counts])])], settings, 'info'),
                groupCard('成员', [current.memberTitle, current.members], invite, 'members'),
                groupCard('群文件', [current.files.element], null, 'files'),
            );
            void current.files.refresh();
        }
        group.detail = detail;
        group.title.textContent = detail.display_name || detail.name;
        group.canonical.textContent = `群名：${detail.name}`;
        group.canonical.hidden = !detail.display_name || detail.display_name === detail.name;
        group.counts.textContent = `${detail.member_count} 人 · ${detail.online_count} 人在线 · 我是${ROLE_LABEL[detail.my_role] || '成员'}`;
        group.memberTitle.textContent = `${detail.member_count} 位成员 · ${detail.online_count} 人在线`;
        group.avatar.replaceChildren(lqAvatar(detail.name, detail.avatar_url, 56));
        clearMenus();
        group.members.replaceChildren(...(detail.members || []).map(member => memberRow(member, detail, client.me)));
        group.settings.update(detail);
        group.invite.update(detail);
    }

    async function memberAction(button, detail, ctx) {
        const identity = button.dataset.identity;
        const act = button.dataset.memberAct;
        if (act === 'nudge') { await guarded(button, () => socialApi.nudge(ctx.key, identity)); return; }
        if (act === 'kick' && !await confirmDialog({ title: '移出成员', message: `确定把「${button.dataset.name}」移出本群吗？`, confirmLabel: '移出', danger: true })) return;
        if (act === 'transfer' && !await confirmDialog({ title: '转让群主', message: `转让后你将成为普通成员，「${button.dataset.name}」成为群主。`, confirmLabel: '转让', danger: true })) return;
        await guarded(button, async () => {
            if (act === 'kick') await socialApi.removeMember(detail.id, identity);
            else if (act === 'transfer') await socialApi.transferGroup(detail.id, identity);
            else if (act === 'admin') await socialApi.setAdmin(detail.id, identity, button.dataset.admin === '1');
            onChanged?.('group', { groupId: Number(detail.id), key: ctx.key });
        });
    }

    return {
        element, showDm, showGroup,
        /** A new attachment arrived in the open group: reload its file list. */
        refreshFiles(groupId) { if (group?.id === Number(groupId)) void group.files.refresh(); },
        updatePreferences(key, preferences) {
            const input = element.querySelector('#social-dm-muted');
            if (input) input.checked = Boolean(preferences.muted);
            if (group?.key === key) {
                group.detail.me = { ...group.detail.me, muted: Boolean(preferences.muted) };
                group.settings.updatePreferences(preferences);
            }
        },
        clear() { token += 1; clearMenus(); clearGroup(); element.replaceChildren(); },
    };
}
