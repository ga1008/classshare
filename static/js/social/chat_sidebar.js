/**
 * Chat window right sidebar.
 *   DM    → compact public profile, cultivation/interests and shared ability radar.
 *           Private controls belong to the child settings dialog.
 *   Group → 公告 (editable by owner/admin), 群名, members online-first with
 *           role actions per my role, 群文件 by kind, 我的群昵称/免打扰, 退出/解散.
 * Every destructive intent goes through the shared LQ confirm dialog.
 */
import { createComponent } from '../lq/components.js';
import { confirm as confirmDialog } from '../lq/dialogs.js';
import { bindMenu, createMenu } from '../lq/menus.js';
import { renderAbilityRadar } from '../ability_radar.js';
import { socialApi, errorText } from './social_api.js';
import { el, emptyState, lqAvatar, lqButton, notify } from './social_dom.js';
import { createGroupSettings } from './group_settings.js';
import { createGroupInvite } from './group_invite.js';
import { createGroupFiles } from './group_files.js';
import { createDmSettings } from './dm_settings.js';

const ROLE_LABEL = { owner: '群主', admin: '管理员', member: '成员' };
function safeLink(url) {
    const text = String(url || '');
    return text.startsWith('/') && !text.startsWith('//') ? text : null;
}

export function createChatSidebar({ client, onChanged }) {
    const element = el('div', { class: 'social-side__inner' });
    let token = 0;
    let group = null;
    let dm = null;
    let menus = [];
    const clearMenus = () => { menus.forEach((menu) => menu.destroy()); menus = []; };
    const clearGroup = () => { group?.settings.destroy(); group?.invite.destroy(); group?.files.destroy(); group = null; };
    const clearDm = () => { dm?.readController?.abort(); dm?.settings?.destroy(); dm = null; };

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
        if (dm?.identity !== identity || dm.key !== ctx.key) {
            clearDm();
            dm = { identity, key: ctx.key, ctx, card: null, settings: null };
            element.replaceChildren(el('p', { class: 'social-hint', text: '正在读取资料…' }));
        } else {
            dm.ctx = ctx;
            if (dm.card) dm.settings.update(dm.card, ctx);
        }
        const current = dm;
        current.readController?.abort();
        current.readController = new AbortController();
        let card;
        try { card = await socialApi.card(identity, current.readController.signal); } catch (failure) {
            if (ticket === token && !current.card) element.replaceChildren(
                emptyState('资料暂时无法读取', errorText(failure), 'error'),
                lqButton({ label: '重试', variant: 'soft', onClick: () => void showDm(identity, current.ctx) }),
            );
            return;
        }
        if (ticket !== token || dm !== current) return;
        current.card = card;
        if (!current.settings) current.settings = createDmSettings({ client, identity, profile: card, context: current.ctx,
            onChanged: (reason, payload) => {
                if (dm !== current) return;
                if (payload?.profile) {
                    current.ctx = { ...current.ctx, displayName: payload.profile.remark || payload.profile.name };
                    current.card = { ...current.card, relation: payload.profile.relation };
                }
                if (payload?.preferences) current.ctx = { ...current.ctx, muted: Boolean(payload.preferences.muted) };
                renderDm(current);
                onChanged?.(reason, payload);
            },
        });
        else current.settings.update(card, current.ctx);
        renderDm(current);
    }

    function dmCard(title, children, action, name) {
        return el('section', { class: 'lq-surface social-dm-card', 'data-lq-component': 'surface', 'data-lq-surface': 'embedded', 'data-dm-card': name }, [
            el('div', { class: 'social-dm-card__head' }, [el('h3', { class: 'social-section-title', text: title }), action]), ...children,
        ]);
    }

    function renderDm(current) {
        const { identity, card: profile } = current;
        const publicCard = profile.relation === 'blocked' ? null : profile;
        const presenceKnown = profile.online !== null && profile.online !== undefined;
        const online = profile.relation !== 'blocked' && (client.isOnline(identity) || Boolean(profile.online));
        const org = publicCard?.org || {};
        const orgText = [...new Set([org.college, org.department, org.major, org.class_name].filter(Boolean))].join(' · ');
        const settings = current.settingsButton ||= lqButton({ label: '设置', variant: 'ghost', attrs: { 'data-dm-settings-open': '', 'aria-haspopup': 'dialog' }, onClick: event => current.settings.open(event.currentTarget) });
        const displayName = current.ctx.displayName || profile.name;
        const head = el('div', { class: 'social-side__profile' }, [
            el('span', { class: 'social-avatar', 'data-online': online ? 'true' : 'false' }, [
                lqAvatar(profile.name, profile.avatar_url, 56), el('span', { class: 'social-avatar__dot', 'aria-hidden': 'true' }),
            ]),
            el('div', { class: 'social-side__who' }, [
                el('strong', { text: displayName }),
                el('small', { text: [displayName !== profile.name ? profile.name : '', profile.role_label, orgText].filter(Boolean).join(' · ') }),
                presenceKnown || online ? el('small', { text: online ? '在线' : '离线' }) : null,
            ]),
        ]);
        const blocks = [dmCard('好友资料', [head,
            profile.mood && profile.relation !== 'blocked' ? el('p', { class: 'social-side__mood', text: profile.mood }) : null,
        ], settings, 'profile')];
        const cultivation = [];
        const badge = publicCard?.role !== 'teacher' && !publicCard?.cultivation_hidden ? publicCard?.tier_badge : null;
        if (badge) {
            const score = badge.score !== null && badge.score !== undefined && Number.isFinite(Number(badge.score))
                ? `修为 ${Number(badge.score).toFixed(1).replace(/\.0$/, '')}` : '';
            const emblem = safeLink(typeof badge === 'string' ? badge : badge.emblem_url || badge.url);
            cultivation.push(el('div', { class: 'social-side__cultivation' }, [
                emblem ? el('img', { class: 'lq-cultivation-emblem-img', src: emblem, alt: '', width: '32', height: '32', loading: 'lazy', decoding: 'async' }) : null,
                el('span', { text: [badge.name || badge.short_name, score].filter(Boolean).join(' · ') }),
            ]));
        }
        const interests = [...new Set((Array.isArray(publicCard?.interests) ? publicCard.interests : []).filter(value => typeof value === 'string' && value.trim()).map(value => value.trim()))].slice(0, 6);
        if (interests.length) cultivation.push(el('div', { class: 'social-dm-card__interests', 'data-dm-interests': '', 'aria-label': '兴趣' }, interests.map(label => createComponent('chip', { label, size: 'sm' }))));
        if (cultivation.length) blocks.push(dmCard(badge ? (interests.length ? '修为与兴趣' : '修为') : '兴趣', cultivation, null, 'cultivation'));
        if (publicCard?.role === 'student' && !publicCard.is_wanderer && !publicCard.cultivation_hidden && publicCard.radar?.available && Array.isArray(publicCard.radar.axes) && publicCard.radar.axes.length) {
            const radar = renderAbilityRadar(publicCard.radar, { selfLabel: profile.name || 'TA' });
            // The graph already includes all axis values in its accessible label.
            // Its expanded comparison list belongs to the full personal card.
            radar.querySelector('.lq-radar__detail')?.remove();
            blocks.push(dmCard('能力雷达', [radar], null, 'radar'));
        }
        element.replaceChildren(...blocks);
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
        clearDm();
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
            if (dm?.key === key) {
                dm.ctx = { ...dm.ctx, muted: Boolean(preferences.muted) };
                dm.settings?.updatePreferences(preferences);
            }
            if (group?.key === key) {
                group.detail.me = { ...group.detail.me, muted: Boolean(preferences.muted) };
                group.settings.updatePreferences(preferences);
            }
        },
        clear() { token += 1; clearMenus(); clearGroup(); clearDm(); element.replaceChildren(); },
    };
}
