/** Read-only conversation browser. The server remains the owner of messages and unread state. */
import { apiFetch } from './api.js';
import { createContent } from './lq/content.js';
import { parseTime } from './social/social_dom.js';
import { node, dateText, badge, action, empty } from './wanderer_world_views.js';

const socialDateText = value => {
    const date = parseTime(value);
    return date ? date.toLocaleString('sv-SE', { hour12: false }) : dateText(value);
};

export function createConversationView({ endpoint, items, pager, viewerIdentity = '', selected = null, onSelect = () => {} }) {
    let disposed = false, generation = 0, request = null, current = null, page = 0, nextBefore = null;
    let total = 0, hasEarlier = false, loading = false, retryOlder = false;
    const seen = new Set();
    const shell = node('div', { class: 'ww-conversations', 'data-ww-conversation-view': 'list' });
    const navigation = node('nav', { class: 'ww-conversation-list', 'aria-label': '会话列表' });
    const sidebar = node('aside', { class: 'ww-conversation-sidebar' }, [navigation, pager]);
    const heading = node('h3', { text: '会话原文', tabindex: '-1' });
    const description = node('p', { class: 'ww-muted', text: '查看不会替散修标记已读，也不会发起回复。' });
    const back = action('会话列表', () => {
        shell.dataset.wwConversationView = 'list';
        navigation.querySelector('[aria-current="true"]')?.focus({ preventScroll: true });
    }, { variant: 'ghost', attrs: { 'data-ww-chat-back': '' } });
    const latest = action('回到最新', () => load(false), { attrs: { 'data-ww-chat-latest': '' } });
    latest.disabled = true;
    const header = node('header', { class: 'ww-transcript-head' }, [
        node('div', { class: 'ww-transcript-title' }, [heading, description]),
        node('div', { class: 'ww-meta' }, [back, latest]),
    ]);
    const stream = node('div', { class: 'ww-chat-stream', tabindex: '0', 'aria-label': '聊天原文', 'data-ww-chat-stream': '' }, [empty('选择一段会话，查看完整消息。')]);
    const earlier = action('加载更早消息', () => load(true), { attrs: { 'data-ww-chat-earlier': '' } });
    earlier.hidden = true;
    const status = node('p', { class: 'ww-muted', role: 'status', 'aria-live': 'polite' });
    const retry = action('重试', () => load(retryOlder), { variant: 'soft' });
    retry.hidden = true;
    const footer = node('footer', { class: 'ww-transcript-foot' }, [earlier, status, retry]);
    const transcript = node('section', { class: 'ww-transcript', 'data-ww-transcript': '', 'aria-label': '会话详情' }, [header, stream, footer]);
    shell.append(sidebar, transcript);

    function choose(item, { focus = true } = {}) {
        const key = item.key || item.conversation_key;
        if (!key) return;
        current = { key, title: item.title || key };
        onSelect(current); shell.dataset.wwConversationView = 'detail';
        navigation.querySelectorAll('[data-wd-conversation]').forEach(button => {
            const active = button.dataset.wdConversation === key;
            if (active) { button.setAttribute('aria-current', 'true'); button.dataset.lqSelected = 'true'; }
            else { button.removeAttribute('aria-current'); button.removeAttribute('data-lq-selected'); }
        });
        heading.textContent = current.title;
        description.textContent = '完整保留的原文 · 查看不会替散修标记已读';
        page = 0; nextBefore = null; seen.clear(); stream.replaceChildren();
        if (focus && shell.ownerDocument.defaultView.matchMedia('(max-width: 800px)').matches) heading.focus({ preventScroll: true });
        load(false);
    }
    for (const item of items) {
        const key = item.key || item.conversation_key;
        const button = action(item.title || key || '会话', () => choose(item), {
            attrs: { 'data-wd-conversation': key, 'data-lq-shape': 'surface' },
        });
        button.classList.add('lq-domain-choice', 'ww-conversation-choice');
        button.dataset.lqComponent = 'choice';
        button.replaceChildren(node('span', { class: 'ww-conversation-choice__title', text: item.title || key || '会话' }),
            node('span', { class: 'ww-meta' }, [badge(item.type === 'group' ? '群聊' : '私聊'),
                ...(Number(item.unread_count) > 0 ? [badge(`${item.unread_count} 未读`, 'info')] : [])]),
            ...(item.last_message_at || item.last_at || item.updated_at ? [node('time', { class: 'ww-muted', text: socialDateText(item.last_message_at || item.last_at || item.updated_at) })] : []));
        navigation.append(button);
    }
    if (!items.length) navigation.append(empty('没有匹配的会话。'));

    function message(item) {
        const own = item.sender_identity === viewerIdentity;
        const attachments = item.attachments || (item.media ? [item.media] : []);
        const links = attachments.map(file => file.download_url
            ? action(file.filename || file.original_filename || '查看附件', null, { variant: 'link', href: file.download_url, attrs: { download: '' } })
            : node('span', { class: 'ww-muted', text: `${file.filename || file.original_filename || '附件'} · 暂不可下载` }));
        const author = `${item.sender_name || item.sender_identity || '成员'}${own ? ' · 本人' : ''}`;
        return node('article', { class: 'ww-chat-message', 'data-ww-message-id': item.id, 'data-ww-own': own ? 'true' : 'false' }, [
            createContent('bubble', { author, side: own ? 'outgoing' : 'incoming',
                text: item.recalled ? '（消息已撤回）' : item.body || '[附件]', time: socialDateText(item.created_at), datetime: item.created_at || undefined }),
            ...(links.length ? [node('div', { class: 'ww-meta', 'aria-label': '消息附件' }, links)] : []),
        ]);
    }
    async function load(older) {
        if (!current || disposed || (older && (loading || !hasEarlier))) return;
        request?.abort(); request = new AbortController();
        retryOlder = older;
        const ticket = ++generation, owner = current.key, requestedPage = older ? page + 1 : 1;
        const params = new URLSearchParams({ page: String(requestedPage), limit: '30' });
        if (older && nextBefore != null) params.set('before_id', String(nextBefore));
        loading = true; transcript.setAttribute('aria-busy', 'true'); earlier.disabled = true; latest.disabled = true;
        retry.hidden = true; status.textContent = older ? '正在读取更早消息…' : '正在读取最新消息…';
        try {
            const data = await apiFetch(`${endpoint}/conversations/${encodeURIComponent(owner)}/messages?${params}`, { silent: true, signal: request.signal });
            if (disposed || ticket !== generation || current.key !== owner) return;
            const beforeHeight = stream.scrollHeight, beforeTop = stream.scrollTop;
            if (!older) { stream.replaceChildren(); seen.clear(); }
            const rows = [];
            for (const item of data.items || []) {
                // Only pagination overlap is merged. Equal text under different IDs remains visible for auditing.
                const id = String(item.id ?? `${item.created_at}:${item.sender_identity}:${item.message_type}`);
                if (seen.has(id)) continue;
                seen.add(id); rows.push(message(item));
            }
            if (older) stream.prepend(...rows); else stream.append(...rows);
            page = requestedPage; total = data.total ?? seen.size;
            nextBefore = data.next_before_id ?? null;
            hasEarlier = Object.hasOwn(data, 'next_before_id') ? data.next_before_id != null : page < (data.pages || Math.ceil(total / 30));
            earlier.hidden = !hasEarlier;
            if (!seen.size) stream.append(empty('这段会话还没有消息。'));
            status.textContent = `已显示 ${seen.size} 条 · 共 ${total} 条${hasEarlier ? '' : ' · 已到最早记录'}`;
            // One layout measurement per explicit load preserves the message under the reader's eyes.
            stream.scrollTop = older ? beforeTop + stream.scrollHeight - beforeHeight : stream.scrollHeight;
        } catch (error) {
            if (disposed || ticket !== generation || error.name === 'AbortError') return;
            status.textContent = `${error.message || '读取失败'}；已加载的消息仍保留`;
            retry.hidden = false;
        } finally {
            if (!disposed && ticket === generation) { loading = false; transcript.removeAttribute('aria-busy'); earlier.disabled = false; latest.disabled = false; }
        }
    }
    if (selected) choose(items.find(item => (item.key || item.conversation_key) === selected.key) || selected, { focus: false });
    return { element: shell, destroy() { disposed = true; generation += 1; request?.abort(); } };
}
