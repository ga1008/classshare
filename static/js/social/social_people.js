/**
 * Person rows shared by the 好友 modal tabs (新朋友 / 添加 / 推荐 / AI 找人).
 * LQ Row/List/Avatar/Chip only; the name of a real person is the Row's
 * primary action carrying `data-user-card`, so it opens the 个人信息面板 via
 * the delegated trigger (user_card_trigger.js) and stays keyboard reachable.
 */
import { createComponent } from '../lq/components.js';
import { createContent } from '../lq/content.js';
import { normalizeCardIdentity } from './user_card_trigger.js';
import { el, lqAvatar } from './social_dom.js';

export function personLead(person, size = 40) {
    // Unknown presence stays unmarked; known presence is the avatar ring.
    const known = person.online !== undefined;
    return el('span', { class: 'social-avatar', 'data-online': known ? (person.online ? 'true' : 'false') : null }, [
        lqAvatar(person.name, person.avatar_url, size),
    ]);
}

export function wandererChip() {
    return createComponent('chip', { label: '散修', tone: 'info', size: 'sm', attrs: { 'data-social-wanderer': '' } });
}

export function list(label, rows) {
    return createContent('list', { label }, { items: rows });
}

export function row({ title, meta = '', actions = [], lead = null, trail = [], identity = null }) {
    const props = { title, meta, actions };
    const card = normalizeCardIdentity(identity);
    if (card) props.primary = { attrs: { 'data-user-card': card, 'aria-haspopup': 'dialog' } };
    return createContent('row', props, { lead: lead ? [lead] : [], trail });
}

/**
 * The single relation → action mapping (§12.6 PublicUser.relation/can_add).
 * @param {Record<string, any>} user PublicUser
 * @returns {Record<string, any>} Button props
 */
export function relationAction(user) {
    const id = { 'data-identity': user.identity };
    switch (user.relation) {
    case 'friend': return { label: '发消息', variant: 'soft', attrs: { ...id, 'data-act': 'chat-user' } };
    case 'pending_out': return { label: '已申请', variant: 'ghost', disabled: true };
    case 'pending_in': return { label: '通过申请', variant: 'prominent', attrs: { ...id, 'data-act': 'accept-user' } };
    case 'blocked': return { label: '已拉黑', variant: 'ghost', disabled: true };
    default:
        // Older servers omit can_add: only an explicit false locks the button.
        if (user.can_add === false) return { label: '尚未解锁', icon: 'lock', variant: 'ghost', disabled: true };
        return { label: '加好友', variant: 'prominent', attrs: { ...id, 'data-act': 'request' } };
    }
}

/** Visible reason under a locked add button. */
export function lockedReason(user) {
    if (user.can_add !== false || ['friend', 'pending_out', 'pending_in', 'blocked'].includes(user.relation)) return '';
    return user.add_blocked_reason || '当前境界尚不能添加该用户';
}

/**
 * Row for a PublicUser; `extra` lines (reason, hint) join the meta.
 * @param {Record<string, any>} user
 * @param {string[]} [extra]
 */
export function userRow(user, extra = []) {
    const meta = [user.role_label, user.org_label, ...extra, lockedReason(user)].filter(Boolean).join(' · ');
    return row({
        title: user.name || '用户', meta, identity: user.identity,
        actions: [relationAction(user)], lead: personLead(user), trail: user.is_wanderer ? [wandererChip()] : [],
    });
}
