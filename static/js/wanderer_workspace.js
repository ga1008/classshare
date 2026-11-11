/** Seven paginated admin projections; mutations use existing authenticated routes. */
import { apiFetch } from './api.js';
import { createContent } from './lq/content.js';
import { createCollapsible } from './lq/collapsible.js';
import { enhanceDropdowns } from './lq/dropdown.js';
import { createConversationView } from './wanderer_conversation_view.js';
import { memoryCorrectionEditor } from './wanderer_memory_editor.js';
import { node, dateText, badge, action, empty, rawRecord, memoryItem, resourceCards, bondView, flowView, relationshipItem, reflectionView } from './wanderer_world_views.js';

const names = { overview: '此刻', memory: '记忆', conversations: '交流', relations: '关系', activity: '运行轨迹', resources: '资源账本', identity: '身份与能力' };
const kinds = {
    memory: [['episodes', '自述记忆'], ['knowledge', '研读笔记'], ['threads', '会话记忆'], ['chapters', '往事章节'], ['reflections', '自省']],
    relations: [['outgoing', '我对别人的好感'], ['incoming', '别人对我的好感'], ['relationships', '全部关系档案'], ['relationship_changes', '好感变更'], ['friends', '好友'], ['requests', '好友申请'], ['groups', '所在群聊'], ['blocked', '黑名单'], ['bond_requests', '道侣申请']],
    activity: [['turns', '思考与执行'], ['events', '唤醒事件'], ['actions', '平台行为'], ['reviews', '天道裁决'], ['todos', '自主待办']],
    resources: [['resource_usage', '额度用量'], ['resource_reservations', '预留与扣款'], ['capacity', '容量占用'], ['life_ledger', '寿元收支'], ['penalties', '处罚分担'], ['debts', '待偿处罚'], ['cultivation', '修为收益']],
    identity: [['', '基础资料'], ['legacy', '前世资料']],
};
const recordNames = {
    ...Object.fromEntries(Object.values(kinds).flat()),
    knowledge_sources: '研读来源', memory_sources: '记忆来源', admin_access: '管理员访问记录',
    todo_evidence: '待办完成依据', self_state_sources: '自省状态来源', knowledge_revisions: '研读修订历史',
    private_message_operations: '旧私信执行记录', gain_debt_payments: '修为与天赋偿债记录', talent_awards: '天赋收益',
    evidence_snapshots: '上下文来源快照', knowledge_growth: '研读增益记录',
    episodes: '自述原始档案', experience_notes: '交流经验', social_inbox: '旧交流收件队列',
    relationship_versions: '好感版本', group_observations: '群聊已读游标', runtime_leases: '思考执行租约',
    commands: '指令执行账本', bonds: '道侣关系档案', bond_requests: '结契与均寿申请',
    penalty_roots: '原始处罚', penalty_reversals: '处罚撤销', dao_history_blocks: '天道全历史审阅块',
    reflection_sessions: '免费自省会话', reflection_dispatches: '自省请求计次', affinity_ratings: '每日好感评分',
    affinity_rating_operations: '评分提交回执', message_deliveries: '消息送达回执', memory_corrections: '管理员记忆修订',
};

export function createWandererWorkspace(root, { getId }) {
    let generation = 0, request = null, dropdowns = [], current = 'overview', history = [], cursor = null;
    let search, filter, list, summary, paging, info, host, conversationView = null, pendingWrites = 0;
    const drafts = new Map(), selectedConversations = new Map();
    root.ownerDocument.defaultView.addEventListener('beforeunload', event => {
        if (drafts.size || pendingWrites) { event.preventDefault(); event.returnValue = ''; }
    });
    const endpoint = () => `/api/manage/system/wanderers/${getId()}`;
    const panel = key => root.querySelector(`[data-wd-workspace="${key}"]`);
    function cancel() {
        generation += 1;
        request?.abort(); request = null;
        conversationView?.destroy(); conversationView = null;
        dropdowns.forEach(control => control.destroy()); dropdowns = [];
    }
    function build(key) {
        cancel(); current = key; history = []; cursor = null;
        host = panel(key);
        if (!host) return;
        root.querySelector('.wd-detail__surface')?.setAttribute('data-ww-active', key);
        const form = node('form', { class: 'ww-filter', role: 'search', 'aria-label': `${names[key]}检索` });
        search = node('input', { type: 'search', class: 'lq-input', 'data-lq-component': 'input', name: 'q', maxlength: 120, placeholder: key === 'conversations' ? '查找会话或成员' : '搜索记录', 'aria-label': key === 'conversations' ? '查找会话或成员' : '搜索记录' });
        form.append(search);
        filter = node('select', { class: 'lq-select', 'data-lq-component': 'select', 'data-lq-dropdown': '', name: 'kind', 'aria-label': '记录类型' },
            (kinds[key] || [['', '全部记录']]).map(([value, label]) => node('option', { value, text: label })));
        if (kinds[key]) form.append(filter);
        form.append(action('查找', null, { type: 'submit' }));
        form.addEventListener('submit', event => { event.preventDefault(); cursor = null; history = []; if (key === 'conversations') selectedConversations.delete(getId()); load(); });
        filter.addEventListener('change', () => { cursor = null; history = []; load(); });
        info = node('p', { class: 'ww-muted', 'aria-live': 'polite' });
        summary = node('div', { class: 'ww-summary' });
        list = node('div', { class: 'ww-records' });
        paging = node('div', { class: 'ww-pager', role: 'group', 'aria-label': '记录分页' });
        host.replaceChildren(...(key === 'overview' ? [] : [form]), info, summary, list, paging);
        dropdowns = enhanceDropdowns(form);
    }
    function reconciliation(command) {
        const owner = getId(), ticket = generation;
        const body = node('div', { class: 'ww-summary' });
        const host = createCollapsible({ id: `ww-reconcile-${command.operation_id}-${ticket}`, title: '核对执行结果', mode: 'always', open: false }, [body]);
        host.dataset.wwReconcile = command.operation_id;
        let loaded = false, busy = false, decision = '', reason = '';
        const url = `${endpoint()}/commands/${encodeURIComponent(command.operation_id)}/reconcile`;
        const currentOwner = () => owner === getId() && ticket === generation;
        async function read() {
            if (busy) return;
            busy = true; body.setAttribute('aria-busy', 'true');
            try {
                const data = await apiFetch(url, { silent: true });
                if (!currentOwner()) return;
                const inspection = data.request;
                const evidence = rawRecord(inspection.observation, '后端保存的执行证据');
                const description = node('p', { class: 'ww-muted', text: '先核对平台中的实际结果，再登记结论。此操作不会重发原指令；记录会保留核对人和理由。' });
                body.replaceChildren(description, evidence);
                if (!inspection.can_reconcile) {
                    body.append(node('p', { text: inspection.block_reason || '这条记录当前不需要核对。' }));
                    loaded = true; return;
                }
                const form = node('form', { class: 'ww-summary' });
                const choice = node('select', { class: 'lq-select', 'data-lq-component': 'select', name: 'outcome', required: true, 'aria-label': '实际执行结果' }, [
                    node('option', { value: '', text: '请选择核对结论' }), node('option', { value: 'occurred', text: '确认已经发生' }), node('option', { value: 'not_occurred', text: '确认没有发生' }),
                ]);
                choice.value = decision;
                const note = node('textarea', { class: 'lq-textarea', 'data-lq-component': 'textarea', name: 'reason', rows: 3, maxlength: 1000, required: true, 'aria-label': '核对依据与理由', placeholder: '填写已核对的实际结果和依据' });
                note.value = reason;
                const error = node('p', { role: 'alert', class: 'ww-muted' });
                const submit = action('记录核对结论', null, { type: 'submit', variant: 'prominent' });
                form.append(choice, note, error, node('div', { class: 'ww-meta' }, [submit, action('读取最新证据', () => { decision = choice.value; reason = note.value; read(); })]));
                form.addEventListener('submit', async event => {
                    event.preventDefault(); if (!form.reportValidity()) return;
                    decision = choice.value; reason = note.value; submit.disabled = true;
                    try {
                        await apiFetch(url, { method: 'POST', body: { expected_revision: inspection.revision, outcome: decision, reason }, silent: true });
                        if (currentOwner()) load();
                    } catch (failure) { if (currentOwner()) error.textContent = failure.message || '未提交，请核对最新证据后重试'; }
                    finally { submit.disabled = false; }
                });
                body.append(form); loaded = true;
            } catch (error) {
                if (currentOwner()) body.replaceChildren(empty(error.message || '执行证据读取失败'), action('重试', read));
            } finally { busy = false; body.removeAttribute('aria-busy'); }
        }
        host.addEventListener('toggle', () => { if (host.open && !loaded) read(); });
        return host;
    }
    function record(item) {
        if (current === 'memory' && ['episodes', 'knowledge'].includes(filter.value)) {
            const owner = getId(), ticket = generation;
            const editor = memoryCorrectionEditor(item, { endpoint: endpoint(), kind: filter.value === 'episodes' ? 'episode' : 'knowledge', drafts,
                onPending: delta => { pendingWrites += delta; }, onSaved: () => { if (owner === getId() && ticket === generation) load(); } });
            if (filter.value === 'episodes') return memoryItem(item, { foot: [editor] });
            return createContent('card', { title: item.title || item.topic || '研读笔记', meta: dateText(item.updated_at || item.created_at), variant: 'flat' }, {
                body: [node('p', { class: 'ww-fulltext', text: item.editor_content ?? item.digest ?? item.content ?? '' })], foot: [editor],
            });
        }
        if (current === 'relations' && ['outgoing', 'incoming', 'relationships', 'relationship_changes'].includes(filter.value)) return relationshipItem(item, { direction: filter.value });
        const states = { pending: '等待处理', processing: '处理中', failed: '等待重新评估', blocked: '暂受限制', deferred: '暂缓处理', completed: '已完成', active: '生效中', ended: '已结束' };
        const title = Array.isArray(item.commands) ? '思考与执行' : item.title || item.name || item.action_kind || item.review_kind || item.resource || states[item.state] || item.source || '记录';
        const body = item.content || item.digest || item.reason || item.content_preview || item.note || '';
        const extras = [];
        if (Array.isArray(item.commands)) {
            extras.push(flowView(item));
            const ended = ['completed', 'failed', 'interrupted', 'cancelled', 'canceled'].includes(item.state);
            extras.push(...item.commands.filter(command => command.state === 'uncertain' || (ended && command.state === 'running')).map(reconciliation));
        }
        if (body) extras.push(node('p', { class: 'ww-fulltext', text: body }));
        if (current === 'activity' && filter.value === 'events' && ['failed', 'blocked', 'deferred'].includes(item.state)) {
            const again = action('重新评估（消耗思考额度）', async () => {
                const id = getId(), ticket = generation;
                again.disabled = true;
                try {
                    await apiFetch(`${endpoint()}/events/${encodeURIComponent(item.event_id)}/reconsider`, { method: 'POST', body: {}, silent: true });
                    if (id === getId() && ticket === generation) load();
                } catch (error) {
                    if (id === getId() && ticket === generation) info.textContent = error.message || '重新评估未提交，请重试';
                } finally { again.disabled = false; }
            });
            extras.push(node('p', { class: 'ww-muted', text: '根据当前情况重新思考；原来的执行指令不会直接重放。' }), again);
        }
        extras.push(rawRecord(item));
        return createContent('card', { title: String(title), variant: 'flat', meta: dateText(item.occurred_at || item.created_at || item.updated_at || item.budget_date) }, { body: extras });
    }
    function renderSummary(data) {
        const value = data.summary || {}, nodes = [];
        if (current === 'identity' && Array.isArray(value.record_kinds)) {
            const options = [['', '基础资料'], ...value.record_kinds.map(key => [key, recordNames[key] || key])];
            const signature = JSON.stringify(options);
            if (filter.dataset.recordKinds !== signature) {
                const selected = filter.value;
                dropdowns.forEach(control => control.destroy());
                filter.replaceChildren(...options.map(([value, text]) => node('option', { value, text })));
                filter.value = selected;
                filter.dataset.recordKinds = signature;
                dropdowns = enhanceDropdowns(filter.form);
            }
        }
        if (value.resources) nodes.push(resourceCards(value.resources));
        if (value.status) {
            const status = value.status, life = status.life || {};
            nodes.unshift(node('div', { class: 'ww-status' }, [badge(status.name || '散修', 'primary'), badge(`第 ${status.tier ?? 0} 境`), badge(`剩余 ${life.remaining_days ?? '—'} 天`, Number(life.remaining_days) < 7 ? 'warning' : 'success'), badge(`第 ${status.life_epoch ?? 1} 世`)]));
            if (status.daily_reflection) nodes.splice(1, 0, reflectionView(status.daily_reflection));
            if (status.relationships?.items?.length) nodes.push(createContent('card', { title: '我对朋友的好感', meta: '系统依据每日自省评分计算', variant: 'flat' }, {
                body: [node('div', { class: 'ww-ranking-preview' }, status.relationships.items.slice(0, 5).map(item => relationshipItem(item)))],
            }));
        }
        if (value.reflection && !value.status?.daily_reflection) {
            if (current === 'relations') nodes.push(node('div', { class: 'ww-meta' }, [badge(`今日自省免费剩余 ${value.reflection.free_remaining ?? 3} / ${value.reflection.free_limit ?? 3}`, 'success'), badge(`已评分 ${value.reflection.rated_count ?? 0} / ${value.reflection.friend_count ?? 0} 位好友`)]));
            else nodes.push(reflectionView(value.reflection));
        }
        if (current === 'relations' && ['outgoing', 'incoming'].includes(filter.value)) nodes.unshift(node('p', { class: 'ww-muted', text: filter.value === 'incoming' ? '只展示其他散修对本人的实际评分；真人没有虚构的反向好感值。' : '按当前好感值从高到低排序。每日自省五档评分，由系统统一计算；一般不增不减。' }));
        if (value.bonds) {
            const bonds = bondView(value.bonds, getId());
            nodes.push(current === 'relations' ? createCollapsible({ id: `ww-relation-bonds-${getId()}-${generation}`, title: '道侣与共享关系', mode: 'always', open: false }, [bonds]) : bonds);
        }
        if (value.profile) nodes.push(rawRecord(value.profile, '完整身份、能力与运行参数'));
        summary.replaceChildren(...nodes);
    }
    async function load() {
        request?.abort(); request = new AbortController();
        conversationView?.destroy(); conversationView = null;
        const ticket = ++generation, id = getId();
        const params = new URLSearchParams({ section: current, limit: '30', q: search.value, kind: filter.value });
        if (cursor != null) params.set('cursor', String(cursor));
        const owned = host;
        owned.setAttribute('aria-busy', 'true'); info.textContent = '正在读取…';
        paging.replaceChildren();
        try {
            const data = await apiFetch(`${endpoint()}/workspace?${params}`, { silent: true, signal: request.signal });
            if (ticket !== generation || id !== getId()) return;
            renderSummary(data);
            const records = current === 'conversations' ? data.items || [] : (data.items || []).map(record);
            if (current === 'memory' && filter.value === 'episodes') list.replaceChildren(node('ol', { class: 'ww-timeline' }, records));
            else if (current === 'conversations') {
                conversationView = createConversationView({ endpoint: endpoint(), items: records, pager: paging, viewerIdentity: data.summary?.viewer_identity || '',
                    selected: selectedConversations.get(id), onSelect: value => selectedConversations.set(id, value) });
                list.replaceChildren(conversationView.element);
            }
            else list.replaceChildren(...records);
            if (!records.length && !['overview', 'conversations'].includes(current)) list.append(empty('暂时没有这类记录。'));
            info.textContent = current === 'overview' ? '当日余额、共同资源与最近的真实执行' : `本页 ${records.length} 条 · 共 ${data.total ?? records.length} 条`;
            const prev = action(current === 'conversations' ? '上一页会话' : '上一页', () => { cursor = history.pop() ?? null; load(); });
            const next = action(current === 'conversations' ? '下一页会话' : '下一页', () => { history.push(cursor); cursor = data.next_cursor; load(); });
            prev.disabled = history.length === 0; next.disabled = data.next_cursor == null;
            paging.hidden = current === 'overview';
            paging.setAttribute('aria-label', current === 'conversations' ? '会话列表分页' : '记录分页');
            paging.replaceChildren(prev, node('span', { class: 'ww-muted', text: `${current === 'conversations' ? '会话列表 · ' : ''}第 ${history.length + 1} 页` }), next);
        } catch (error) {
            if (ticket !== generation || error.name === 'AbortError') return;
            info.textContent = '读取失败，筛选条件已保留';
            list.replaceChildren(createContent('empty', { reason: 'error', description: error.message || '读取失败' }), action('重试', load));
        } finally { if (ticket === generation) owned.removeAttribute('aria-busy'); }
    }
    return {
        activate(key) { build(key); if (host) load(); },
        refresh() { if (host?.isConnected) load(); },
        cancel,
        canClose() { return pendingWrites === 0; },
        reset() { cancel(); host = null; },
    };
}
