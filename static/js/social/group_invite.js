/** Existing friends only; the server decides capacity and every invite result. */
import { createForm } from '../lq/forms.js';
import { createStatus } from '../lq/status.js';
import { socialApi, errorText } from './social_api.js';
import { createSocialDialog, el, emptyState, lqAvatar, lqButton } from './social_dom.js';

export function createGroupInvite({ detail, onChanged, onUnavailable }) {
    const groupId = Number(detail.id), key = detail.conversation_key || `group:${groupId}`;
    let latest = detail, friends = [], selected = new Set(), skipped = new Map();
    let generation = 0, disposed = false, busy = false, loading = false;
    const search = createForm('input', { id: 'social-group-invite-search', label: '查找好友', name: 'q', type: 'search', placeholder: '姓名或备注' });
    const input = search.querySelector('input');
    const summary = el('p', { class: 'social-hint', 'data-group-invite-summary': '', 'aria-live': 'polite' });
    const status = el('div', { class: 'social-group-invite__status', 'aria-live': 'polite' });
    const choices = el('div', { class: 'social-group-invite__choices' });
    const submit = lqButton({ label: '邀请加入', type: 'submit', variant: 'prominent', attrs: { 'data-group-invite-submit': '' } });
    const cancel = lqButton({ label: '返回群聊', variant: 'ghost', onClick: () => void dialog.close('button') });
    const form = el('form', { class: 'social-group-invite' }, [
        el('p', { class: 'social-hint', text: '从已有好友中选择邀请对象。加入后可查看入群后的消息和文件。' }),
        search, summary, choices, status, el('div', { class: 'social-group-invite__actions' }, [cancel, submit]),
    ]);
    const dialog = createSocialDialog({ id: 'social-group-invite', title: '邀请好友', size: 'md', body: form,
        initialFocus: () => input, beforeClose: () => !busy, onClose: () => { generation++; loading = false; },
    });
    const members = () => new Set((latest.members || []).map(item => item.identity));
    const capacity = () => Math.max(0, Number(latest.max_members || 200) - Number(latest.member_count || 0));
    function controls() {
        submit.disabled = busy || loading || selected.size === 0 || capacity() === 0;
        cancel.disabled = busy;
        input.disabled = busy;
        choices.querySelectorAll('input').forEach(node => { node.disabled = busy || members().has(node.value); });
        form.setAttribute('aria-busy', String(busy || loading));
        summary.textContent = `已选 ${selected.size} 人 · 群内 ${latest.member_count} / ${latest.max_members || 200} 人${capacity() ? '' : ' · 群成员已满'}`;
    }
    function render() {
        const present = members();
        const eligible = new Set(friends.filter(friend => !present.has(friend.identity)).map(friend => friend.identity));
        selected = new Set([...selected].filter(identity => eligible.has(identity)));
        const q = input.value.trim().toLocaleLowerCase();
        const visible = friends.filter(friend => `${friend.remark || ''} ${friend.name || ''}`.toLocaleLowerCase().includes(q));
        if (!friends.length) choices.replaceChildren(emptyState('暂无可邀请的好友', '先添加好友，再邀请他们一起聊天。'));
        else if (!visible.length) choices.replaceChildren(emptyState('没有找到匹配的好友', '试试姓名或备注。', 'no-results'));
        else choices.replaceChildren(el('ul', { class: 'social-group-invite__list', 'aria-label': '选择要邀请的好友' }, visible.map((friend, index) => {
            const already = present.has(friend.identity);
            const checkbox = createForm('checkbox', { id: `social-group-invite-pick-${index}`, name: 'member', value: friend.identity,
                label: friend.remark || friend.name, checked: selected.has(friend.identity), disabled: already || busy });
            const reason = already ? '已在群中' : skipped.get(friend.identity);
            const metadata = [friend.remark ? friend.name : '', friend.role_label, friend.org_label].filter(Boolean).join(' · ');
            return el('li', { class: 'social-group-invite__person', 'data-invite-identity': friend.identity }, [
                lqAvatar(friend.name, friend.avatar_url, 32),
                el('div', { class: 'social-group-invite__copy' }, [checkbox,
                    metadata ? el('small', { class: 'social-hint', text: metadata }) : null,
                    reason ? el('small', { class: 'social-hint', text: reason, 'data-invite-reason': '' }) : null,
                ]),
            ]);
        })));
        controls();
    }
    choices.addEventListener('change', event => {
        const checkbox = event.target.closest('input[name="member"]');
        if (!checkbox || busy) return;
        if (checkbox.checked) selected.add(checkbox.value); else selected.delete(checkbox.value);
        controls();
    });
    input.addEventListener('input', event => { if (!event.isComposing) render(); });
    input.addEventListener('compositionend', render);
    async function load() {
        const ticket = ++generation; loading = true; controls();
        choices.replaceChildren(el('p', { class: 'social-hint', text: '正在读取好友与群成员…' }));
        try {
            const [friendData, group] = await Promise.all([socialApi.friends(), socialApi.group(groupId)]);
            if (disposed || ticket !== generation || !dialog.isOpen()) return;
            friends = friendData.items || []; latest = group; status.replaceChildren(); loading = false; render();
        } catch (error) {
            if (disposed || ticket !== generation) return;
            loading = false;
            if ([403, 404].includes(error.status)) { onUnavailable?.(); return; }
            choices.replaceChildren(emptyState('暂时无法读取好友', errorText(error), 'error'), lqButton({ label: '重新读取', onClick: () => void load() }));
            controls(); submit.disabled = true;
        }
    }
    form.addEventListener('submit', async event => {
        event.preventDefault();
        if (busy || loading || !selected.size || !capacity()) return;
        const ticket = generation, wanted = [...selected]; busy = true; controls(); status.replaceChildren();
        try {
            const result = await socialApi.addMembers(groupId, wanted);
            if (disposed || ticket !== generation) return;
            const added = new Set(result.added || []);
            selected = new Set([...selected].filter(identity => !added.has(identity)));
            skipped = new Map((result.skipped || []).map(item => [item.identity, item.reason]));
            latest = { ...latest, member_count: Number(latest.member_count || 0) + [...added].filter(identity => !members().has(identity)).length,
                members: [...(latest.members || []), ...friends.filter(friend => added.has(friend.identity) && !members().has(friend.identity))] };
            busy = false; render();
            if (added.size) onChanged?.('group', { groupId, key });
            if (!skipped.size && added.size) { selected.clear(); await dialog.close('completed'); return; }
            const labelFor = identity => { const friend = friends.find(item => item.identity === identity); return friend?.remark || friend?.name || '好友'; };
            status.replaceChildren(createStatus('alert', { tone: 'warning', title: added.size ? `已邀请 ${added.size} 人，部分好友未能加入` : '本次没有好友加入', body: '请查看原因，调整选择后重试。' }),
                el('ul', { class: 'social-group-invite__results' }, [...skipped].map(([identity, reason]) => el('li', { text: `${labelFor(identity)}：${reason}` }))));
        } catch (error) {
            if (disposed || ticket !== generation) return;
            if ([403, 404].includes(error.status)) { onUnavailable?.(); return; }
            status.replaceChildren(createStatus('alert', { tone: 'danger', title: '邀请未完成', body: `${errorText(error)} 所选好友已保留，可重试。` }));
        } finally { if (!disposed && ticket === generation) { busy = false; controls(); } }
    });
    return {
        open(trigger) { if (!disposed) { dialog.open(trigger); void load(); } },
        update(next) {
            if (disposed || Number(next.id) !== groupId) return;
            latest = next;
            if (dialog.isOpen() && !busy && !loading) render();
        },
        destroy() { disposed = true; generation++; dialog.destroy(); },
    };
}
