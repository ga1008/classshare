/** Group/public and personal/private settings keep independent save boundaries. */
import { createForm } from '../lq/forms.js';
import { confirm as confirmDialog } from '../lq/dialogs.js';
import { createStatus } from '../lq/status.js';
import { socialApi, errorText } from './social_api.js';
import { createSocialDialog, el, lqButton, notify } from './social_dom.js';

const groupValues = detail => ({ name: detail.name || '', announcement: detail.announcement || '' });
const myValues = detail => ({ alias: detail.me?.alias || '', nickname: detail.me?.nickname || '', muted: Boolean(detail.me?.muted) });
const differences = (values, baseline) => Object.fromEntries(Object.entries(values).filter(([key, value]) => value !== baseline[key]));
const manages = detail => ['owner', 'admin'].includes(detail.my_role);

export function createGroupSettings({ client, detail, onChanged, onUnavailable }) {
    const groupId = Number(detail.id), key = detail.conversation_key || `group:${groupId}`;
    let latest = detail, groupBase = groupValues(detail), mineBase = myValues(detail), version = detail.updated_at;
    let busy = false, confirming = false, disposed = false, conflict = false, generation = 0;
    const publicStatus = el('div', { class: 'social-group-settings__status', 'aria-live': 'polite' });
    const privateStatus = el('div', { class: 'social-group-settings__status', 'aria-live': 'polite' });
    const name = createForm('input', { id: 'social-group-rename', label: '群名称', name: 'name', value: detail.name, maxLength: 30, required: true });
    const announcement = createForm('textarea', { id: 'social-group-announcement', label: '群公告', name: 'announcement', value: detail.announcement || '', rows: 4, maxLength: 500 });
    const publicFields = el('fieldset', { class: 'social-group-settings__fields' }, [name, announcement]);
    const saveGroup = lqButton({ label: '保存群资料', type: 'submit', variant: 'soft' });
    const reload = lqButton({ label: '读取最新资料并核对', variant: 'ghost', attrs: { 'data-group-settings-reload': '' }, onClick: () => void reloadPublic() });
    reload.hidden = true;
    const publicForm = el('form', { class: 'social-group-settings__section', 'data-group-public-form': '' }, [
        el('h3', { class: 'social-section-title', text: '群资料' }), publicFields, publicStatus,
        el('div', { class: 'social-group-settings__actions' }, [reload, saveGroup]),
    ]);
    const alias = createForm('input', { id: 'social-group-alias', label: '我的群代称（仅自己可见）', name: 'alias', value: mineBase.alias, maxLength: 30, placeholder: '留空使用群名称' });
    const nickname = createForm('input', { id: 'social-group-nickname', label: '我的群昵称（群成员可见）', name: 'nickname', value: mineBase.nickname, maxLength: 20, placeholder: '留空使用原姓名' });
    const muted = createForm('switch', { id: 'social-group-muted', label: '消息免打扰', name: 'muted', checked: mineBase.muted });
    const privateFields = el('fieldset', { class: 'social-group-settings__fields' }, [alias, nickname, muted]);
    const saveMine = lqButton({ label: '保存我的设置', type: 'submit', variant: 'prominent' });
    const privateForm = el('form', { class: 'social-group-settings__section', 'data-group-private-form': '' }, [
        el('h3', { class: 'social-section-title', text: '我的设置' }), privateFields,
        el('p', { class: 'social-hint', text: '免打扰只关闭浮窗提醒，消息中心与未读数字照常保留。' }), privateStatus, saveMine,
    ]);
    const danger = lqButton({ label: detail.my_role === 'owner' ? '解散群组' : '退出群组', variant: 'destructive', attrs: { 'data-group-danger': '' }, onClick: () => void leave() });
    const body = el('div', { class: 'social-group-settings' }, [publicForm, privateForm,
        el('div', { class: 'social-group-settings__danger' }, [danger]),
    ]);
    const readPublic = () => ({ name: publicForm.elements.name.value.trim().replace(/\s+/g, ' '), announcement: publicForm.elements.announcement.value.trim() });
    const readMine = () => ({ alias: privateForm.elements.alias.value.trim(), nickname: privateForm.elements.nickname.value.trim(), muted: privateForm.elements.muted.checked });
    const dirty = () => Object.keys(differences(readPublic(), groupBase)).length || Object.keys(differences(readMine(), mineBase)).length;
    const dialog = createSocialDialog({ id: 'social-group-settings', title: '群设置', size: 'md', body,
        initialFocus: () => privateForm.elements.alias,
        beforeClose: async reason => {
            if (reason === 'completed') return true;
            if (busy) return false;
            if (!dirty()) return true;
            return confirmDialog({ title: '放弃未保存的设置？', message: '已输入的修改尚未保存。', confirmLabel: '放弃修改', danger: true });
        },
        onClose: () => { generation++; },
    });
    function message(holder, title, text = '', tone = 'info') {
        holder.replaceChildren(createStatus('alert', { title, body: text, tone }));
    }
    function setBusy(value) {
        busy = value;
        publicFields.disabled = value;
        publicForm.elements.name.readOnly = !manages(latest);
        publicForm.elements.announcement.readOnly = !manages(latest);
        privateFields.disabled = value;
        saveGroup.disabled = value || conflict || !manages(latest);
        saveGroup.hidden = !manages(latest);
        saveMine.disabled = value;
        reload.disabled = value;
        // The confirmation layer owns inertness; keep its return-focus target available.
        danger.disabled = value && !confirming;
        body.setAttribute('aria-busy', String(value));
    }
    function applyPublic(next) {
        groupBase = groupValues(next); version = next.updated_at;
        publicForm.elements.name.value = groupBase.name;
        publicForm.elements.announcement.value = groupBase.announcement;
    }
    function applyMine(next) {
        mineBase = myValues(next);
        privateForm.elements.alias.value = mineBase.alias;
        privateForm.elements.nickname.value = mineBase.nickname;
        privateForm.elements.muted.checked = mineBase.muted;
    }
    function update(next) {
        if (disposed || Number(next.id) !== groupId) return;
        latest = next;
        if (!busy && !conflict && !Object.keys(differences(readPublic(), groupBase)).length) applyPublic(next);
        if (!busy && !Object.keys(differences(readMine(), mineBase)).length) applyMine(next);
        if (!manages(next) && Object.keys(differences(readPublic(), groupBase)).length) message(publicStatus, '群资料权限已变更', '你的输入已保留，目前不能保存群资料。', 'warning');
        danger.textContent = next.my_role === 'owner' ? '解散群组' : '退出群组';
        danger.setAttribute('aria-label', danger.textContent);
        setBusy(busy);
    }
    async function failure(error, holder) {
        if ([403, 404].includes(error.status)) {
            // A 403 can be a lost management role, not a lost membership.
            try { const fresh = await socialApi.group(groupId); if (!disposed) update(fresh); }
            catch (readError) { if (!disposed && [403, 404].includes(readError.status)) { onUnavailable?.(); return; } }
        }
        if (!disposed) message(holder, '未能保存', errorText(error), 'danger');
    }
    publicForm.addEventListener('submit', async event => {
        event.preventDefault();
        if (busy || conflict || !manages(latest)) return;
        const patch = differences(readPublic(), groupBase);
        if (!Object.keys(patch).length) { message(publicStatus, '没有需要保存的修改'); return; }
        const ticket = generation; setBusy(true);
        try {
            const fresh = await socialApi.updateGroup(groupId, { ...patch, expected_updated_at: version });
            if (disposed || ticket !== generation) return;
            latest = fresh; applyPublic(fresh); conflict = false; reload.hidden = true;
            message(publicStatus, '群资料已保存');
            onChanged?.('group', { groupId, key, detail: fresh });
        } catch (error) {
            if (disposed || ticket !== generation) return;
            if (error.status === 409) {
                conflict = true; reload.hidden = false;
                message(publicStatus, '群资料已有新的修改', '你的输入已保留。先读取最新资料并核对，再保存。', 'warning');
            } else await failure(error, publicStatus);
        } finally { if (!disposed && ticket === generation) setBusy(false); }
    });
    privateForm.addEventListener('submit', async event => {
        event.preventDefault();
        if (busy) return;
        const patch = differences(readMine(), mineBase);
        if (!Object.keys(patch).length) { message(privateStatus, '没有需要保存的修改'); return; }
        const ticket = generation; setBusy(true);
        try {
            const me = await socialApi.updateMe(groupId, patch);
            if (disposed || ticket !== generation) return;
            latest = { ...latest, display_name: me.display_name || latest.name, me: { ...latest.me, ...me } };
            applyMine(latest); client.setMuted(key, me.muted); client.refreshCounts();
            message(privateStatus, '我的设置已保存');
            onChanged?.('me', { groupId, key, me, detail: latest });
        } catch (error) { if (!disposed && ticket === generation) await failure(error, privateStatus); }
        finally { if (!disposed && ticket === generation) setBusy(false); }
    });
    async function reloadPublic() {
        if (busy) return;
        const retained = differences(readPublic(), groupBase), ticket = generation;
        setBusy(true);
        try {
            const fresh = await socialApi.group(groupId);
            if (disposed || ticket !== generation) return;
            latest = fresh; applyPublic(fresh);
            for (const [field, value] of Object.entries(retained)) publicForm.elements[field].value = value;
            conflict = false; reload.hidden = true;
            message(publicStatus, '已读取最新群资料', `你的修改仍已保留，请核对后保存。当前群名称：${fresh.name}；当前公告：${fresh.announcement || '暂无公告'}`, 'info');
            update(fresh);
        } catch (error) { if (!disposed && ticket === generation) await failure(error, publicStatus); }
        finally { if (!disposed && ticket === generation) setBusy(false); }
    }
    async function leave() {
        if (busy || disposed) return;
        const dissolve = latest.my_role === 'owner', ticket = generation;
        confirming = true; setBusy(true);
        const accepted = await confirmDialog(dissolve
            ? { title: '解散群组', message: `解散「${latest.name}」后所有成员都不能继续发言，且无法恢复。`, confirmLabel: '解散', danger: true }
            : { title: '退出群组', message: `确定退出「${latest.name}」吗？`, confirmLabel: '退出', danger: true });
        confirming = false;
        if (disposed || ticket !== generation) return;
        if (!accepted) { setBusy(false); return; }
        setBusy(true);
        try {
            if (dissolve) await socialApi.dissolveGroup(groupId); else await socialApi.leaveGroup(groupId);
            if (disposed || ticket !== generation) return;
            setBusy(false); await dialog.close('completed');
            notify(dissolve ? '群组已解散。' : '已退出群组。', 'success');
            onChanged?.('left', { groupId, key });
        } catch (error) { if (!disposed && ticket === generation) { await failure(error, privateStatus); setBusy(false); } }
    }
    update(detail);
    return {
        open(trigger) {
            if (disposed) return;
            generation++; conflict = false; reload.hidden = true;
            publicStatus.replaceChildren(); privateStatus.replaceChildren();
            applyPublic(latest); applyMine(latest); update(latest); dialog.open(trigger);
        },
        update,
        updatePreferences({ muted: value }) {
            if (privateForm.elements.muted.checked === mineBase.muted) privateForm.elements.muted.checked = Boolean(value);
            mineBase.muted = Boolean(value); latest = { ...latest, me: { ...latest.me, muted: Boolean(value) } };
        },
        destroy() { disposed = true; generation++; dialog.destroy(); },
    };
}
