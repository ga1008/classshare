/**
 * 个人信息面板 (social doc §12.4/§12.6 InfoCard)。由 user_card_trigger.js 在首次点击
 * 实名头像/名字时懒加载；一个文档只建一个 LQ 对话框（createSocialDialog →
 * createDomainModal，LQ.layer 持有栈、焦点归还、Esc、滚动锁）。
 *
 * 只展示接口给出的公开字段；隐藏修为者不显示境界、各课境界与能力雷达；
 * friend_extras 仅在接口返回时出现。操作按 relation/can_add 决定：
 * 加好友 / 已申请 / 通过申请 / 发消息 / 尚未解锁（附原因）/ 拉黑 / 管理黑名单。
 */
import { createComponent } from '../lq/components.js';
import { confirm } from '../lq/dialogs.js';
import { renderAbilityRadar } from '../ability_radar.js';
import { socialApi, errorText } from './social_api.js';
import {
    createSocialDialog, el, emptyState, ensureSocialStyles, formatRelative, lqAvatar, lqButton, notify,
} from './social_dom.js';

const ROLE_LABEL = { student: '学生', teacher: '教师' };
const RECENT_KIND = { blog: '博客', post: '博客', achievement: '成就', breakthrough: '境界突破', tier: '境界突破' };

let panel = null;

/** Same-origin relative links only. */
function safeLink(url) {
    const text = String(url || '');
    return text.startsWith('/') && !text.startsWith('//') ? text : null;
}

function emblemSrc(emblem) {
    if (!emblem) return null;
    if (typeof emblem === 'string') return safeLink(emblem);
    return safeLink(emblem.emblem_url || emblem.url || '');
}

function chip(label, tone = 'neutral', attrs = {}) {
    return createComponent('chip', { label, tone, size: 'sm', attrs });
}

function section(title, children) {
    return el('section', { class: 'social-card__section' }, [
        el('h3', { class: 'social-section-title', text: title }), ...children,
    ]);
}

function dmKey(me, identity) {
    return `dm:${[me, identity].sort().join('|')}`;
}

function scoreText(score) {
    return score !== null && score !== undefined && Number.isFinite(Number(score))
        ? `修为 ${Number(score).toFixed(1).replace(/\.0$/, '')}` : '';
}

/* ---------------------------------------------------------------- blocks */

function headBlock(card) {
    const badge = card.cultivation_hidden ? null : card.tier_badge;
    const emblem = emblemSrc(badge);
    const presence = card.online === null || card.online === undefined ? null : card.online === true;
    return el('div', { class: 'social-card__head' }, [
        // The explicit tier line below replaces the automatic avatar decoration.
        el('span', { class: 'social-avatar social-card__avatar', 'data-online': presence ? 'true' : 'false', 'data-cultivation-emblem': 'off' }, [
            lqAvatar(card.name, card.avatar_url, 56),
            presence === null ? null : el('span', { class: 'social-avatar__dot', 'aria-hidden': 'true' }),
        ]),
        el('div', { class: 'social-card__who' }, [
            el('div', { class: 'social-card__chips' }, [
                chip(card.role_label || ROLE_LABEL[card.role] || '用户', card.role === 'teacher' ? 'primary' : 'neutral'),
                card.is_wanderer && card.role_label !== '散修' ? chip('散修', 'info', { 'data-social-wanderer': '' }) : null,
                presence === null ? null : chip(presence ? '在线' : '离线', presence ? 'success' : 'neutral'),
            ]),
            badge ? el('p', { class: 'social-card__tier' }, [
                emblem ? el('img', { class: 'lq-cultivation-emblem-img', src: emblem, alt: '', width: '24', height: '24', decoding: 'async' }) : null,
                el('span', { text: [badge.name || badge.short_name, scoreText(badge.score)].filter(Boolean).join(' · ') }),
            ]) : null,
            orgLine(card),
            card.mood ? el('p', { class: 'social-card__mood', text: `心情：${card.mood}` }) : null,
        ]),
    ]);
}

function orgLine(card) {
    const org = card.org || {};
    const parts = [org.school, org.college, org.major, org.grade, org.class_name].filter(Boolean);
    if (card.student_no_tail) parts.push(`学号尾号 ${card.student_no_tail}`);
    return parts.length ? el('p', { class: 'social-card__org', text: parts.join(' · ') }) : null;
}

function coursesBlock(card) {
    const courses = Array.isArray(card.courses) ? card.courses : [];
    const hidden = Boolean(card.cultivation_hidden);
    const title = card.role === 'teacher' ? '任教课程' : '参与课程';
    if (!courses.length) return section(title, [el('p', { class: 'social-hint', text: '暂无公开的课程' })]);
    return section(title, [el('ul', { class: 'social-card__courses', role: 'list' }, courses.map((course) => {
        const emblem = hidden ? null : emblemSrc(course.emblem);
        return el('li', { class: 'social-card__course' }, [
            el('span', { class: 'social-card__course-name', text: course.course_name || '课程' }),
            !hidden && course.tier_name ? el('span', { class: 'social-card__course-tier' }, [
                emblem ? el('img', { class: 'lq-cultivation-emblem-img', src: emblem, alt: '', width: '20', height: '20', decoding: 'async' }) : null,
                chip(course.tier_name, 'info'),
            ]) : null,
        ]);
    }))]);
}

function abilityBlock(card) {
    if (card.role !== 'student' || card.is_wanderer) return null;
    if (card.cultivation_hidden) {
        return section('修为与能力', [el('p', { class: 'social-hint', text: 'TA 选择隐藏修为，境界与能力雷达不对外展示。' })]);
    }
    if (!card.radar) return null;
    return section('能力雷达', [renderAbilityRadar(card.radar, { selfLabel: card.name || 'TA' })]);
}

function wandererBlock(card) {
    const homepage = safeLink(card.homepage_url);
    if (!card.is_wanderer && !card.archetype && !homepage) return null;
    return section('散修档案', [el('div', { class: 'social-card__wanderer' }, [
        card.archetype ? el('p', { class: 'social-card__archetype', text: `原型：${card.archetype}` }) : null,
        homepage ? lqButton({ label: '散修主页', icon: 'external-link', variant: 'soft', href: homepage }) : null,
    ])]);
}

function linkList(items, meta) {
    const rows = items.filter((item) => safeLink(item?.url));
    if (!rows.length) return null;
    return el('ul', { class: 'social-side__links', role: 'list' }, rows.map((item) => el('li', {}, [
        el('a', { href: safeLink(item.url), text: item.title || '查看' }),
        el('small', { text: meta(item) }),
    ])));
}

function extrasBlock(card) {
    const extras = card.friend_extras;
    if (!extras || typeof extras !== 'object') return [];
    const blocks = [];
    const recent = linkList(Array.isArray(extras.recent) ? extras.recent : [],
        (item) => [RECENT_KIND[item.kind] || '', formatRelative(item.at)].filter(Boolean).join(' · '));
    if (recent) blocks.push(section('近期动态', [recent]));
    const creations = linkList(Array.isArray(extras.creations) ? extras.creations : [],
        (item) => [formatRelative(item.at), item.likes ? `${item.likes} 赞` : '', item.comments ? `${item.comments} 评论` : ''].filter(Boolean).join(' · '));
    if (creations) blocks.push(section('创作', [creations]));
    return blocks;
}

/* ---------------------------------------------------------------- actions */

function relationActions(card) {
    const relation = card.relation || 'none';
    if (relation === 'self') return { primary: [], note: '这是你的公开资料，其他人看到的就是这些内容。' };
    if (relation === 'friend') return { primary: [lqButton({ label: '发消息', icon: 'message-circle', variant: 'prominent', attrs: { 'data-card-act': 'chat' } })] };
    if (relation === 'pending_out') return { primary: [lqButton({ label: '已申请', variant: 'ghost', disabled: true })], note: '已发出好友申请，等待对方通过。' };
    if (relation === 'pending_in') return { primary: [lqButton({ label: '通过申请', variant: 'prominent', attrs: { 'data-card-act': 'accept' } })] };
    if (relation === 'blocked') {
        return { primary: [chip('已拉黑', 'danger'), lqButton({ label: '管理黑名单', variant: 'link', attrs: { 'data-card-act': 'blocks' } })] };
    }
    if (card.can_add) return { primary: [lqButton({ label: '加好友', icon: 'plus', variant: 'prominent', attrs: { 'data-card-act': 'request' } })] };
    return {
        primary: [lqButton({ label: '尚未解锁', icon: 'lock', variant: 'ghost', disabled: true })],
        note: card.add_blocked_reason || '当前境界尚不能添加该用户。',
    };
}

function actionsBlock(card) {
    const { primary, note = '' } = relationActions(card);
    const relation = card.relation || 'none';
    const block = ['self', 'blocked'].includes(relation)
        ? null : lqButton({ label: '拉黑', variant: 'ghost', attrs: { 'data-card-act': 'block' } });
    if (!primary.length && !block) return [el('p', { class: 'social-hint social-card__note', text: note })];
    return [
        note ? el('p', { class: 'social-hint social-card__note', text: note }) : null,
        el('div', { class: 'social-card__actions' }, [
            el('div', { class: 'social-card__actions-main' }, primary),
            block ? el('div', { class: 'social-card__actions-side' }, [block]) : null,
        ]),
    ].filter(Boolean);
}

/* ---------------------------------------------------------------- panel */

function createPanel() {
    const content = el('div', { class: 'social-card', 'data-user-card-panel': '' });
    const dialog = createSocialDialog({ id: 'social-user-card', title: '个人信息', size: 'md', body: content, className: 'social-card-dialog' });
    dialog.root.setAttribute('data-user-card-panel', '');
    const state = { card: null, ticket: 0, abort: null, client: null };

    function render(card) {
        state.card = card;
        dialog.title.textContent = card.name || '个人信息';
        content.replaceChildren(...[
            headBlock(card), coursesBlock(card), abilityBlock(card), wandererBlock(card),
            ...extrasBlock(card), ...actionsBlock(card),
        ].filter(Boolean));
    }

    async function load(identity) {
        const ticket = ++state.ticket;
        state.abort?.abort();
        state.abort = new AbortController();
        dialog.title.textContent = '个人信息';
        content.setAttribute('aria-busy', 'true');
        content.replaceChildren(el('p', { class: 'social-hint', role: 'status', text: '正在读取资料…' }));
        try {
            const card = await socialApi.card(identity, state.abort.signal);
            if (ticket !== state.ticket) return;
            render(card || {});
        } catch (failure) {
            if (ticket !== state.ticket || failure?.name === 'AbortError') return;
            const missing = failure?.status === 404;
            content.replaceChildren(emptyState(
                missing ? '资料暂不可查看' : '资料读取失败',
                missing ? '该用户不存在，或个人信息面板尚未开放。' : errorText(failure),
                'error',
            ));
        } finally {
            if (ticket === state.ticket) content.removeAttribute('aria-busy');
        }
    }

    async function run(action, card) {
        if (action === 'request') {
            const result = await socialApi.sendRequest(card.identity, '');
            notify(result?.status === 'accepted' ? '对方也申请过你，已直接成为好友。' : '好友申请已发送。', 'success');
        } else if (action === 'accept') {
            const pending = ((await socialApi.requests()).incoming || []).find((item) => item.user?.identity === card.identity);
            if (!pending) throw new Error('这条申请已处理或已撤回。');
            await socialApi.acceptRequest(pending.id);
            notify('已成为好友。', 'success');
        } else if (action === 'block') {
            await socialApi.block(card.identity);
            notify('已拉黑。', 'success');
        }
    }

    async function act(button) {
        const card = state.card;
        const action = button.dataset.cardAct;
        if (!card) return;
        if (action === 'chat') {
            await dialog.close('button');
            if (state.client?.me) state.client.openChat(dmKey(state.client.me, card.identity));
            return;
        }
        if (action === 'blocks') {
            const { createBlocksModal } = await import('./friends_modal.js');
            createBlocksModal().open(button);
            return;
        }
        if (action === 'block' && !await confirm({
            title: '拉黑', confirmLabel: '拉黑', danger: true,
            message: `拉黑「${card.name}」会同时解除好友，对方无法再加你或给你发消息。可在黑名单中移出。`,
        })) return;
        button.disabled = true;
        try {
            await run(action, card);
            state.client?.refreshCounts?.();
            await load(card.identity);
        } catch (failure) {
            notify(errorText(failure), 'danger');
            if (button.isConnected) button.disabled = false;
        }
    }

    content.addEventListener('click', (event) => {
        const button = event.target.closest?.('[data-card-act]');
        if (!button || !content.contains(button) || button.disabled) return;
        event.preventDefault();
        void act(button);
    });

    return {
        open(identity, { trigger, client }) {
            state.client = client || null;
            state.card = null;
            if (!dialog.isOpen()) dialog.open(trigger);
            void load(identity);
        },
    };
}

/**
 * Open (or re-target) the shared 个人信息 panel.
 * @param {string} identity `role:pk`
 * @param {{trigger?: Element|null, client?: any}} [options]
 * @returns {Promise<void>}
 */
export async function openUserCard(identity, { trigger = null, client = null } = {}) {
    await ensureSocialStyles();
    panel ||= createPanel();
    panel.open(identity, { trigger, client });
}
