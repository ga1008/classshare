/** Presentation-only world records. No model calls, account mutations or polling. */
import { createComponent } from './lq/components.js';
import { createContent } from './lq/content.js';
import { createCollapsible } from './lq/collapsible.js';

let serial = 0;
export function node(tag, attrs = {}, children = []) {
    const result = document.createElement(tag);
    Object.entries(attrs).forEach(([key, value]) => {
        if (value === undefined || value === null || value === false) return;
        if (key === 'text') result.textContent = String(value);
        else if (key === 'class') result.className = value;
        else result.setAttribute(key, value === true ? '' : String(value));
    });
    for (const item of [].concat(children)) if (item != null) result.append(item instanceof Node ? item : document.createTextNode(String(item)));
    return result;
}
export const dateText = value => value ? String(value).replace('T', ' ').slice(0, 19) : '时间未记录';
export const badge = (label, tone = 'neutral') => createComponent('chip', { label: String(label), tone, size: 'sm' });
export const action = (label, handler, props = {}) => {
    const result = createComponent('button', { label, size: 'sm', variant: 'glass', ...props });
    if (handler) result.addEventListener('click', handler);
    return result;
};
export const empty = text => createContent('empty', { reason: 'empty', description: text });
export function rawRecord(item, title = '查看完整记录') {
    const body = node('pre', { class: 'ww-raw' });
    const details = createCollapsible({ id: `ww-record-${++serial}`, title, mode: 'always', open: false }, [body]);
    details.addEventListener('toggle', () => { if (details.open && !body.textContent) body.textContent = JSON.stringify(item, null, 2); });
    return details;
}
export function memoryItem(item, { foot = [] } = {}) {
    const tags = node('div', { class: 'ww-meta' }, [
        badge(item.visibility === 'public' ? '公开经历' : '受保护的记忆', item.visibility === 'public' ? 'success' : 'neutral'),
        badge(item.author_kind === 'self' ? '本人自述' : '历史记录'),
        node('span', { text: `版本 ${item.revision || 1}` }),
    ]);
    return node('li', { class: 'ww-timeline__item' }, [
        node('time', { datetime: item.occurred_at || '', text: dateText(item.occurred_at) }),
        createContent('card', { title: item.title || '一段经历', meta: item.location || '地点未记录', variant: 'flat' }, {
            body: [tags, node('p', { class: 'ww-fulltext', text: item.content || '' })], foot,
        }),
    ]);
}
const RESOURCE_NAMES = { ai_wake: '今日思考', social_message: '散修交流', web_search: '公开搜索', active_interactions: '主动互动', blog_posts: '发布文章', cultivation_interaction: '互动修为', cultivation_todo: '待办修为', knowledge_chars: '研读笔记容量', memory_chars: '自述记忆容量', todo_capacity: '自主待办容量', speech_chars: '主动发言长度', recall_chars: '每次回忆长度', friend_requests: '好友申请', friend_capacity: '好友席位', group_memberships: '加入群聊', owned_groups: '自建群聊', friend_request_chars: '好友申请长度', knowledge_digest_chars: '每篇笔记长度', reflection_chars: '自省长度', todo_title_chars: '待办标题长度', todo_detail_chars: '待办详情长度' };
export function resourceCards(resources = {}) {
    const primary = ['ai_wake', 'social_message', 'web_search'];
    const format = number => Number(number).toLocaleString('zh-CN', { maximumFractionDigits: 2 });
    const owners = (value, unit) => node('ul', { class: 'ww-owner-list', 'aria-label': '额度来源' }, (value.owners || []).map((owner, index) => node('li', {}, [
        node('span', { text: owner.name || owner.owner_name || (index === 0 ? '本人资源' : '直接道侣共享') }),
        node('strong', { text: `${format(Number(owner.remaining || 0) / unit)} / ${format(Number(owner.limit || 0) / unit)}` }),
    ])));
    const cards = primary.filter(key => resources[key]).map(key => {
        const value = resources[key], unit = key === 'social_message' ? 3000000 : 1;
        const total = Number(value.limit || 0) / unit, left = Number(value.remaining || 0) / unit;
        return createContent('card', { title: RESOURCE_NAMES[key], variant: 'flat', meta: value.date || '' }, {
            body: [node('div', { class: 'ww-resource__hero' }, [
                createComponent('progress', { label: `${RESOURCE_NAMES[key]}剩余比例`, variant: 'ring', max: Math.max(1, total), value: Math.min(total, left) }),
                node('div', {}, [node('strong', { class: 'ww-number', text: format(left) }), node('p', { text: `剩余 / ${format(total)}${key === 'social_message' ? ' 当量' : ' 次'}` })]),
            ]), node('p', { class: 'ww-muted', text: `已用 ${format(Number(value.used || 0) / unit)} · 在途 ${format(Number(value.reserved || 0) / unit)}` }), owners(value, unit)],
        });
    });
    const result = node('div', { class: 'ww-summary' }, [node('div', { class: 'ww-resources' }, cards)]);
    for (const [period, title] of [['day', '其他每日额度'], ['capacity', '资源容量'], ['ability', '每次行动的能力']]) {
        const rows = Object.entries(resources).filter(([key, value]) => !primary.includes(key) && (value.definition?.period || 'day') === period);
        if (!rows.length) continue;
        const body = node('div', { class: 'ww-resources' }, rows.map(([key, value]) => createContent('card', {
            title: RESOURCE_NAMES[key] || key, variant: 'flat', meta: value.definition?.unit || '',
        }, { body: [node('p', { text: period === 'ability' ? `每次可用 ${format(value.limit || 0)}` : `可用 ${format(value.remaining || 0)} / ${format(value.limit || 0)} · 已占用 ${format(Number(value.used || 0) + Number(value.reserved || 0))}` }), owners(value, 1)] })));
        result.append(createCollapsible({ id: `ww-resources-${++serial}`, title, mode: 'always', open: false }, [body]));
    }
    return result;
}
export function bondView(bonds = [], selfId) {
    const live = bonds.filter(bond => bond.state === 'active');
    if (!bonds.length) return empty('还没有道侣关系。结交、表达爱慕和申请都由散修自己决定。');
    const stars = node('div', { class: 'ww-bonds', 'aria-label': '直接道侣关系' });
    stars.append(badge('自己', 'primary'));
    for (const bond of bonds) {
        const peer = Number(bond.first_id) === Number(selfId) ? bond.second_id : bond.first_id;
        const name = bond.peer_name || bond.peer?.name || `道侣 ${peer || ''}`;
        const state = { active: '同行中', ended: '已解除', dormant: '暂别' }[bond.state] || bond.state;
        stars.append(node('div', { class: 'ww-bond-edge' }, [node('span', { 'aria-hidden': 'true', text: '⟷' }), badge(name, bond.state === 'active' ? 'primary' : 'neutral'), node('span', { class: 'ww-muted', text: state })]));
    }
    return createContent('card', { title: '道侣之间', variant: 'flat', meta: `${live.length} 位正在同行 · 仅直接关系共享` }, { body: [stars] });
}
export function flowView(item) {
    const commands = item.commands || [];
    const ok = commands.filter(value => value.state === 'succeeded').length;
    const source = { group_message: '群聊来信', group_created: '新建群聊', group_joined: '加入群聊', social_dm: '私信来信', tick: '日常醒来', callback: '执行反馈' }[item.source] || item.source || '事件';
    const state = { completed: '已完成', failed: '待处理失败', running: '思考中', pending: '等待', dispatched: '已发起' }[item.state] || item.state || '等待';
    const steps = [['唤醒', source], ['思考', state], ['执行', `${ok} / ${commands.length} 已完成`], ['回执', item.completed_at ? '已返回' : '进行中']];
    return node('ol', { class: 'ww-flow', 'aria-label': '思考与执行过程' }, steps.map(([title, detail], index) => node('li', {}, [
        badge(String(index + 1), index === 2 && commands.length ? 'info' : 'neutral'),
        node('div', {}, [node('strong', { text: title }), node('small', { text: detail })]),
    ])));
}
export function relationshipItem(item, { direction = 'outgoing' } = {}) {
    const value = Math.min(100, Math.max(0, Number(item.new_affinity ?? item.affinity ?? 0)));
    const label = direction === 'incoming' ? '对方对本人的好感' : '本人对对方的好感';
    return createContent('card', { title: `${item.rank ? `${item.rank}. ` : ''}${item.name || item.peer_identity || '联系人'}`, variant: 'flat', meta: item.reason || item.last_reason || '尚无感受说明' }, {
        body: [node('div', { class: 'ww-affinity' }, [createComponent('progress', { label, value, max: 100 }), node('strong', { text: `${value} / 100` })]),
            node('div', { class: 'ww-meta' }, [badge(label), ...(item.last_rating_label ? [badge(item.last_rating_label, 'info')] : []), ...(item.is_friend === true ? [badge('好友')] : [])]),
            node('p', { class: 'ww-muted', text: `版本 ${item.revision ?? '未记录'} · ${dateText(item.rated_at || item.updated_at || item.created_at)}` }), rawRecord(item)],
    });
}
export function reflectionView(value = {}) {
    const statuses = { not_started: '尚未开始', pending: '待自省', running: '自省中', completed: '已完成', applied: '已完成评分', partial: '部分好友待评分', failed: '待重试', exhausted: '今日次数已用完' };
    const limit = Number(value.free_limit ?? 3), used = Number(value.ai_calls ?? 0), remaining = Number(value.free_remaining ?? Math.max(0, limit - used));
    return createContent('card', { title: '今日自省', variant: 'flat', meta: value.reflection_date || '' }, {
        body: [node('div', { class: 'ww-meta' }, [badge(statuses[value.status] || value.status || '尚未开始', 'info'), badge(`免费请求剩余 ${remaining} / ${limit}`, 'success')]),
            node('p', { text: `已评分 ${value.rated_count ?? 0} / ${value.friend_count ?? 0} 位好友${Number(value.missing_count) ? ` · 还有 ${value.missing_count} 位待评分` : ''}` }),
            node('p', { class: 'ww-muted', text: '首次自省与两次可选回调均不占日常思考额度。五档感受由系统换算好感，一般不增不减。' })],
    });
}
