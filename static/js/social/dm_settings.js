/** Private conversation controls live in a child LQ dialog, separate from the public profile. */
import { createForm } from '../lq/forms.js';
import { confirm as confirmDialog } from '../lq/dialogs.js';
import { createStatus } from '../lq/status.js';
import { socialApi, errorText } from './social_api.js';
import { createSocialDialog, el, lqButton, notify } from './social_dom.js';

const safeLink = value => typeof value === 'string' && value.startsWith('/') && !value.startsWith('//') ? value : null;

export function createDmSettings({ client, identity, profile, context, onChanged }) {
    const key = context.key;
    let latest = profile, ctx = context, baseline = { remark: profile.remark || '', muted: Boolean(context.muted) };
    let disposed = false, generation = 0, busy = false, confirming = false, closeCheck = null, loaded = false, loading = false;
    let relationVersion = 0;
    const status = el('div', { class: 'social-dm-settings__status', 'data-dm-settings-status': '', 'aria-live': 'polite' });
    const remark = createForm('input', { id: 'social-remark', label: '备注（仅自己可见）', name: 'remark', value: baseline.remark, maxLength: 30, placeholder: '给好友起个备注' });
    const muted = createForm('switch', { id: 'social-dm-muted', label: '消息免打扰', name: 'muted', checked: baseline.muted });
    const fields = el('fieldset', { class: 'social-dm-settings__fields' }, [remark, muted]);
    const save = lqButton({ label: '保存设置', type: 'submit', variant: 'prominent', attrs: { 'data-dm-settings-save': '' } });
    const retry = lqButton({ label: '重新读取设置', variant: 'soft', attrs: { 'data-dm-settings-retry': '' }, onClick: () => void loadProfile() });
    retry.hidden = true;
    const hint = el('p', { class: 'social-hint', text: '免打扰仅关闭浮窗提醒，消息中心和未读数字仍会保留。' });
    const form = el('form', { class: 'social-dm-settings__section', 'data-dm-private-form': '' }, [
        fields, hint, status, el('div', { class: 'social-dm-settings__actions' }, [retry, save]),
    ]);
    const nudge = lqButton({ label: '拍一拍', variant: 'soft', attrs: { 'data-dm-nudge': '' }, onClick: () => void runNudge() });
    const external = el('div', { class: 'social-dm-settings__actions' });
    const links = el('div', { class: 'social-dm-settings__actions' }, [nudge, external]);
    const remove = lqButton({ label: '删除好友', variant: 'ghost', attrs: { 'data-dm-remove': '' }, onClick: () => void destroyRelation('unfriended') });
    const block = lqButton({ label: '拉黑', variant: 'destructive', attrs: { 'data-dm-block': '' }, onClick: () => void destroyRelation('blocked') });
    const danger = el('div', { class: 'social-dm-settings__actions' }, [remove, block]);
    const body = el('div', { class: 'social-dm-settings' }, [form, links, danger]);
    const read = () => ({ remark: form.elements.remark.value.trim(), muted: form.elements.muted.checked });
    const dirty = () => { const draft = read(); return loaded && (draft.remark !== baseline.remark || draft.muted !== baseline.muted); };
    const friend = () => latest.relation === 'friend' && ctx.writable !== false;
    const canBlock = () => !['self', 'blocked'].includes(latest.relation);
    const dialog = createSocialDialog({ id: 'social-dm-settings', title: '好友设置', size: 'md', body,
        initialFocus: () => loaded && friend() ? form.elements.remark : dialog.root.querySelector('[data-lq-dialog-close]'),
        beforeClose: async reason => {
            if (reason === 'completed') return true;
            if (loading) return true;
            if (busy) return false;
            if (!dirty()) return true;
            closeCheck ||= confirmDialog({ title: '放弃未保存的设置？', message: '已输入的修改尚未保存。', confirmLabel: '放弃修改', danger: true }).finally(() => { closeCheck = null; });
            return await closeCheck && !disposed;
        },
        onClose: () => { generation++; },
    });
    function message(title, text = '', tone = 'info') {
        status.replaceChildren(createStatus('alert', { title, body: text, tone }));
    }
    function setBusy(value) {
        busy = value;
        fields.hidden = hint.hidden = !loaded;
        fields.disabled = value || !loaded || !friend();
        save.disabled = value || !loaded || !friend();
        save.hidden = !loaded || !friend();
        nudge.hidden = !loaded || !ctx.writable || !friend();
        nudge.disabled = value;
        links.hidden = !loaded;
        remove.hidden = !loaded || !friend();
        block.hidden = !loaded || !canBlock();
        danger.hidden = remove.hidden && block.hidden;
        retry.disabled = value;
        // Keep the trigger available while the shared confirmation owns inert/focus.
        remove.disabled = block.disabled = value && !confirming;
        body.setAttribute('aria-busy', String(value));
    }
    function update(next, nextContext = ctx) {
        if (disposed) return;
        const draft = read(), wasFriend = friend();
        if (next.relation !== latest.relation || nextContext.writable !== ctx.writable) relationVersion++;
        latest = { ...latest, ...next }; ctx = nextContext;
        if (loaded && !busy) {
            const nextRemark = Object.hasOwn(next, 'remark') ? (next.remark || '') : baseline.remark;
            if (draft.remark === baseline.remark) form.elements.remark.value = nextRemark;
            if (draft.muted === baseline.muted) form.elements.muted.checked = Boolean(nextContext.muted);
            baseline = { remark: nextRemark, muted: Boolean(nextContext.muted) };
        }
        if (loaded && wasFriend && !friend()) message('好友关系已变更', '未保存的输入已保留，目前不能保存或发送互动。', 'warning');
        setBusy(busy);
    }
    async function loadProfile() {
        if (busy || disposed) return;
        const ticket = generation, relationTicket = relationVersion; loading = true; setBusy(true); retry.hidden = true;
        message('正在读取设置…');
        try {
            const fresh = await socialApi.profile(identity);
            if (disposed || ticket !== generation) return;
            // A newer card / friendship event wins over this read's older snapshot.
            const relation = relationTicket === relationVersion ? fresh.relation : latest.relation;
            latest = { ...latest, ...fresh, relation };
            baseline = { remark: fresh.remark || '', muted: Boolean(ctx.muted) };
            form.elements.remark.value = baseline.remark;
            form.elements.muted.checked = baseline.muted;
            loaded = true; status.replaceChildren();
            external.replaceChildren(...[
                safeLink(fresh.private_message_url) ? lqButton({ label: '课堂私信记录', variant: 'ghost', href: safeLink(fresh.private_message_url) }) : null,
                safeLink(fresh.homepage_url) ? lqButton({ label: '散修主页', variant: 'ghost', href: safeLink(fresh.homepage_url) }) : null,
            ].filter(Boolean));
        } catch (error) {
            if (disposed || ticket !== generation) return;
            retry.hidden = false; message('设置暂时无法读取', errorText(error), 'danger');
        } finally { if (!disposed && ticket === generation) { loading = false; setBusy(false); } }
    }
    async function failure(error, ticket) {
        if ([403, 404].includes(error.status)) {
            try {
                const fresh = await socialApi.profile(identity);
                if (disposed || ticket !== generation) return;
                update(fresh, { ...ctx, writable: ctx.writable && fresh.relation === 'friend' });
            } catch { /* The original failure remains the actionable result. */ }
        }
        if (!disposed && ticket === generation) message('未能完成', errorText(error), 'danger');
    }
    form.addEventListener('submit', async event => {
        event.preventDefault();
        if (busy || disposed || !loaded || !friend()) return;
        const draft = read(), ticket = generation;
        const remarkChanged = draft.remark !== baseline.remark, mutedChanged = draft.muted !== baseline.muted;
        if (!dirty()) { message('没有需要保存的修改'); return; }
        setBusy(true);
        try {
            // Separate existing endpoints: advance only the successfully saved field.
            // A retry after a partial failure never replays the committed remark.
            if (remarkChanged) {
                await socialApi.setRemark(identity, draft.remark);
                if (disposed || ticket !== generation) return;
                baseline.remark = draft.remark;
                latest = { ...latest, remark: draft.remark };
                onChanged?.('remark', { key, identity, profile: latest });
            }
            if (!friend()) return;
            if (mutedChanged && draft.muted !== baseline.muted) {
                const preferences = await socialApi.updateConversationPreferences(key, { muted: draft.muted });
                if (disposed || ticket !== generation) return;
                baseline.muted = Boolean(preferences.muted);
                ctx = { ...ctx, muted: baseline.muted };
                form.elements.muted.checked = baseline.muted;
                client.setMuted(key, baseline.muted);
                onChanged?.('preferences', { key, identity, preferences });
            }
            message('设置已保存');
        } catch (error) { if (!disposed && ticket === generation) await failure(error, ticket); }
        finally { if (!disposed && ticket === generation) setBusy(false); }
    });
    async function runNudge() {
        if (busy || disposed || !loaded || !ctx.writable || !friend()) return;
        const ticket = generation; setBusy(true);
        try { await socialApi.nudge(key); if (!disposed && ticket === generation) message('已拍一拍'); }
        catch (error) { if (!disposed && ticket === generation) await failure(error, ticket); }
        finally { if (!disposed && ticket === generation) setBusy(false); }
    }
    async function destroyRelation(reason) {
        if (busy || disposed || !loaded || (reason === 'unfriended' ? !friend() : !canBlock())) return;
        const ticket = generation, blocking = reason === 'blocked';
        confirming = true; setBusy(true);
        const accepted = await confirmDialog(blocking
            ? { title: '拉黑', message: `拉黑「${latest.name}」会同时解除好友，对方无法再加你或给你发消息。可在黑名单中移出。`, confirmLabel: '拉黑', danger: true }
            : { title: '删除好友', message: `删除后聊天记录会保留，但不能再给「${latest.name}」发消息。`, confirmLabel: '删除', danger: true });
        confirming = false;
        if (disposed || ticket !== generation) return;
        if (!accepted) { setBusy(false); return; }
        if (blocking ? !canBlock() : !friend()) { setBusy(false); return; }
        setBusy(true);
        try {
            if (blocking) await socialApi.block(identity); else await socialApi.deleteFriend(identity);
            if (disposed || ticket !== generation) return;
            latest = { ...latest, relation: blocking ? 'blocked' : 'none' };
            setBusy(false); await dialog.close('completed');
            if (disposed) return;
            notify(blocking ? '已拉黑。' : '已删除好友。', 'success');
            onChanged?.(reason, { key, identity, profile: latest });
        } catch (error) { if (!disposed && ticket === generation) { await failure(error, ticket); setBusy(false); } }
    }
    setBusy(false);
    return {
        open(trigger) {
            if (disposed || dialog.isOpen()) return;
            generation++;
            loaded = false; loading = false;
            status.replaceChildren(); setBusy(false); dialog.open(trigger);
            void loadProfile();
        },
        update,
        updatePreferences({ muted: value }) {
            if (disposed) return;
            if (form.elements.muted.checked === baseline.muted) form.elements.muted.checked = Boolean(value);
            baseline.muted = Boolean(value); ctx = { ...ctx, muted: baseline.muted };
        },
        destroy() { disposed = true; generation++; dialog.destroy(); },
    };
}
