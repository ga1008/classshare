/** Audited administrator correction; private source text never leaves this authorized workspace. */
import { apiFetch } from './api.js';
import { createCollapsible } from './lq/collapsible.js';
import { createContent } from './lq/content.js';
import { node, action, rawRecord, dateText, empty } from './wanderer_world_views.js';

let serial = 0;
export function memoryCorrectionEditor(item, { endpoint, kind, drafts, onSaved = () => {}, onPending = () => {} }) {
    const key = `${endpoint}:${kind}:${item.id}`, url = `${endpoint}/memory-corrections`;
    let current = item, loaded = false, pending = false;
    const region = node('div', { class: 'ww-memory-editor' });
    const disclosure = createCollapsible({ id: `ww-memory-editor-${++serial}`, title: '修订与历史', mode: 'always', open: drafts.has(key) }, [region]);
    disclosure.dataset.wwMemoryEditor = String(item.id);
    const original = node('p', { class: 'ww-fulltext' });
    const title = node('input', { type: 'text', name: 'title', class: 'lq-input', 'data-lq-component': 'input', maxlength: '120', required: true });
    const content = node('textarea', { name: 'content', class: 'lq-textarea', 'data-lq-component': 'textarea', rows: '7', required: true, maxlength: kind === 'episode' ? '3200' : '12000' });
    const reason = node('textarea', { name: 'reason', class: 'lq-textarea', 'data-lq-component': 'textarea', rows: '2', maxlength: '600', required: true });
    const message = node('p', { class: 'ww-muted', role: 'status', 'aria-live': 'polite' });
    const history = node('div', { class: 'ww-summary', 'data-ww-correction-history': '' });
    const submit = action('保存修订', null, { type: 'submit', variant: 'prominent' });
    const discard = action('放弃这份草稿', () => { if (!pending) { drafts.delete(key); fill(); message.textContent = '草稿已放弃，原记忆未改变。'; } }, { variant: 'ghost' });
    const refresh = action('重新读取当前版本', () => onSaved({ preserveDraft: true }), { variant: 'soft' });
    const form = node('form', { class: 'ww-memory-editor__form' }, [
        node('label', {}, [node('span', { text: '修订标题' }), title]),
        node('label', {}, [node('span', { text: '修订后的内容' }), content]),
        node('label', {}, [node('span', { text: '修订原因与事实依据' }), reason]),
        message, node('div', { class: 'ww-meta' }, [submit, discard, refresh]),
    ]);
    region.append(node('p', { class: 'ww-muted', text: '保留原文、修订人和原因，不改变原有可见范围。草稿只保留在当前页面，尚未保存时不要直接关闭浏览器。' }),
        node('strong', { text: '当前有效原文' }), original, form, node('h4', { text: '修订历史' }), history);
    function fill() {
        const draft = drafts.get(key);
        title.value = draft?.title ?? current.editor_title ?? current.title ?? '';
        content.value = draft?.content ?? current.editor_content ?? current.content ?? current.digest ?? '';
        reason.value = draft?.reason ?? '';
        original.textContent = current.editor_content ?? current.content ?? current.digest ?? '';
        submit.disabled = !current.content_hash;
        if (!current.content_hash) message.textContent = '当前记录缺少可核验的版本信息，请重新读取后再修订。';
        else if (draft && draft.base_hash !== current.content_hash) message.textContent = '原记忆已更新。你的草稿仍保留，请与上方最新原文比较后再提交。';
    }
    function remember({ rebase = false } = {}) {
        const prior = drafts.get(key);
        const changedAfterSubmission = prior?.submitted && (prior.title !== title.value || prior.content !== content.value || prior.reason !== reason.value);
        const rebasedAfterSubmission = rebase && prior?.submitted && prior.base_hash !== current.content_hash;
        drafts.set(key, { title: title.value, content: content.value, reason: reason.value,
            base_hash: rebase ? current.content_hash : prior?.base_hash ?? current.content_hash,
            request_id: changedAfterSubmission || rebasedAfterSubmission ? crypto.randomUUID() : prior?.request_id ?? crypto.randomUUID(), submitted: changedAfterSubmission || rebasedAfterSubmission ? false : prior?.submitted });
    }
    form.addEventListener('input', remember);
    form.addEventListener('submit', async event => {
        event.preventDefault(); if (pending || !form.reportValidity() || !current.content_hash) return;
        remember({ rebase: true }); const draft = drafts.get(key);
        draft.submitted = true;
        pending = true; onPending(1); submit.disabled = true; discard.disabled = true; refresh.disabled = true;
        for (const field of [title, content, reason]) field.readOnly = true;
        message.textContent = '正在保存修订…';
        try {
            const data = await apiFetch(url, { method: 'POST', silent: true, body: {
                target_kind: kind, target_id: current.id, expected_revision: current.correction_revision ?? 0,
                expected_content_hash: current.content_hash, title: title.value, content: content.value,
                reason: reason.value, request_id: draft.request_id,
            } });
            drafts.delete(key); message.textContent = '修订已保存，原文和历史仍保留。';
            if (data.item) current = data.item;
            onSaved({ preserveDraft: false });
        } catch (error) {
            message.setAttribute('role', 'alert');
            message.textContent = `${error.message || '修订未保存'}。草稿已保留；如版本发生变化，请重新读取并核对。`;
        } finally {
            pending = false; onPending(-1); submit.disabled = !current.content_hash; discard.disabled = false; refresh.disabled = false;
            for (const field of [title, content, reason]) field.readOnly = false;
        }
    });
    async function readHistory() {
        history.setAttribute('aria-busy', 'true');
        try {
            const data = await apiFetch(`${url}?${new URLSearchParams({ target_kind: kind, target_id: String(item.id) })}`, { silent: true });
            history.replaceChildren(...(data.items || []).map(entry => createCollapsible({
                id: `ww-memory-history-${++serial}`, title: `${dateText(entry.created_at)} · ${entry.reason || '查看本次修订'}`, mode: 'always', open: false,
            }, [node('p', { class: 'ww-muted', text: `修订 ${entry.revision ?? '—'} · ${entry.actor_kind === 'admin' ? '管理员' : entry.actor_kind || '管理员'} ${entry.actor_id ?? ''}` }),
                node('div', { class: 'ww-memory-compare' }, ['before', 'after'].map(key => createContent('card', {
                    title: key === 'before' ? '修订前' : '修订后', variant: 'flat', meta: entry[key]?.title || '',
                }, { body: [node('p', { class: 'ww-fulltext', text: entry[key]?.content || '' })] }))), rawRecord(entry, '查看完整审计字段')])));
            if (!(data.items || []).length) history.append(empty('还没有人工修订记录。'));
            loaded = true;
        } catch (error) { history.replaceChildren(empty(error.message || '历史读取失败'), action('重试读取历史', readHistory)); }
        finally { history.removeAttribute('aria-busy'); }
    }
    disclosure.addEventListener('toggle', () => { if (disclosure.open && !loaded) readHistory(); });
    fill(); if (disclosure.open) readHistory();
    return disclosure;
}
