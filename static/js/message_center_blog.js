/**
 * Blog cards in the message center (docs/blog-closed-loop-plan-2026-10-12.md §6.2, §7.5).
 *
 * The server renders every blog card's actor through the persona service and
 * adds `item.blog` = {event, post_id, comment_id, count, avatar_stack, gone,
 * can_reply}. This module only decorates the LQ Row built by message_center.js:
 * a "内容已不可见" chip, an AvatarStack for aggregated cards, and an inline
 * reply panel (quote + the new comment + shared Composer). It also owns the
 * notification settings dialog. All parts are LQ components; layout hooks
 * live in static/css/lq/pages/message-center.css.
 */
import { apiFetch } from './api.js';
import { createComponent } from './lq/components.js';
import { createContent } from './lq/content.js';
import { createComposer, enhanceComposer } from './lq/composer.js';
import { nativeSelect, switchControl } from './lq/forms.js';
import { createInsight } from './lq/insights.js';
import { getLQ } from './lq/index.js';
import { showToast } from './ui.js';

const LQ = getLQ();
const MODE_LABELS = { real_name: '实名', nickname: '笔名', anonymous: '匿名' };
const STACK_LABELS = { 'blog.follow': '最近关注你的人', 'blog.thread_reply': '最近回复的人' };
const MAX_REPLY_LENGTH = 1000;

function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
}

function postHref(postId, commentId) {
    const base = `/blog?post=${Number(postId)}`;
    return commentId ? `${base}&comment=${Number(commentId)}` : base;
}

/** Decorate one notification Row; `onRead(summary, id)` syncs the page after an inline reply. */
export function decorateBlogRow(row, item, { onRead } = {}) {
    const blog = item?.blog;
    if (!blog) return;
    row.dataset.blogEvent = blog.event || '';
    const trail = row.querySelector('.lq-row__trail');
    if (blog.gone) {
        trail?.prepend(createComponent('chip', { label: '内容已不可见', tone: 'neutral', size: 'sm' }));
    }
    const stack = Array.isArray(blog.avatar_stack) ? blog.avatar_stack : [];
    if (stack.length) {
        row.querySelector('.lq-row__lead')?.append(createInsight('avatar_stack', {
            size: 24,
            label: STACK_LABELS[blog.event] || '相关的人',
            items: stack.map((face) => (face.avatar_url ? { name: face.name, src: face.avatar_url } : { name: face.name })),
        }));
    }
    if (!blog.can_reply || !trail) return;
    const panelId = `mc-blog-reply-${Number(item.id)}`;
    const toggle = createComponent('button', {
        label: '回复', variant: 'soft', size: 'sm', icon: 'message-circle',
        attrs: { 'aria-expanded': 'false', 'aria-controls': panelId, 'data-blog-reply-toggle': String(Number(item.id)) },
    });
    trail.prepend(toggle);
    let panel = null;
    toggle.addEventListener('click', () => {
        const open = toggle.getAttribute('aria-expanded') === 'true';
        toggle.setAttribute('aria-expanded', String(!open));
        if (open) {
            panel?.remove();
            panel = null;
            return;
        }
        panel = element('section', 'mc-blog-reply');
        panel.id = panelId;
        panel.setAttribute('aria-label', '在通知中回复');
        row.querySelector('.lq-row__main')?.append(panel);
        void openInlineReply(panel, item.blog.comment_id, {
            key: item.id,
            afterReply: async () => {
                await markRepliedRead(item, onRead);
                toggle.remove();
            },
        });
    });
}

/**
 * Fill ``panel`` with the reply context of one comment (quote, the comment,
 * the face picker and a Composer). Shared by message-center cards and the
 * creator studio. ``afterReply(result)`` runs once the reply is posted.
 */
export async function openInlineReply(panel, commentId, { key = commentId, afterReply = null } = {}) {
    panel.replaceChildren(element('p', 'mc-blog-reply__status', '正在加载…'));
    let context;
    try {
        context = await apiFetch(`/api/blog/comments/${Number(commentId)}/reply-context`, { silent: true });
    } catch (error) {
        panel.replaceChildren(element('p', 'mc-blog-reply__status', error.message || '加载失败，请稍后重试。'));
        return;
    }
    if (!panel.isConnected) return;
    const parts = [];
    if (context.quote) {
        parts.push(element('p', 'mc-blog-reply__label', context.quote_is_mine ? '你写的' : '回复的是'));
        parts.push(createContent('prose', { text: context.quote, attrs: { 'data-blog-reply-quote': '' } }));
    }
    if (context.comment) {
        const author = context.comment.author?.display_name || '对方';
        parts.push(element('p', 'mc-blog-reply__label', `${author} 说`));
        parts.push(createContent('prose', {
            text: String(context.comment.content_md || '（图片或表情）'), attrs: { 'data-blog-reply-comment': '' },
        }));
    }
    if (!context.can_reply) {
        parts.push(element('p', 'mc-blog-reply__status', context.reason || '现在不能回复'));
        if (context.post) parts.push(goToPost(context.post.id, commentId));
        panel.replaceChildren(...parts);
        return;
    }
    const form = replyForm(panel, key, context, afterReply);
    panel.replaceChildren(...parts, form);
    form.querySelector('[data-lq-composer-input]')?.focus();
}

function goToPost(postId, commentId) {
    return createComponent('button', { label: '去帖子查看', variant: 'link', size: 'sm', href: postHref(postId, commentId) });
}

function faceControl(key, context) {
    if (context.forced_mode) {
        return element('p', 'mc-blog-reply__hint', `你是这篇匿名帖的作者，将以“${context.forced_label || '楼主'}”身份回复。`);
    }
    const modes = Array.isArray(context.modes) ? context.modes : ['real_name'];
    if (modes.length < 2) return null;
    return nativeSelect({
        id: `mc-blog-mode-${Number(key)}`, label: '回复身份', name: 'author_display_mode', size: 'sm',
        value: modes.includes(context.default_mode) ? context.default_mode : modes[0],
        options: modes.map((mode) => ({ value: mode, label: MODE_LABELS[mode] || mode })),
    });
}

function replyForm(panel, key, context, afterReply) {
    const form = element('form', 'mc-blog-reply__form');
    form.noValidate = true;
    const face = faceControl(key, context);
    if (face) form.append(face);
    const composer = createComposer('composer', {
        label: '回复内容', name: 'content_md', placeholder: '写下你的回复…', maxlength: MAX_REPLY_LENGTH,
        required: true, attachment: null, emoji: null, sendLabel: '回复',
    });
    form.append(composer);
    let handle = null;
    let sending = false;
    // The composer must be connected before it can be enhanced.
    queueMicrotask(() => { if (composer.isConnected) handle = enhanceComposer(composer); });
    form.addEventListener('submit', async (event) => {
        event.preventDefault();
        const values = new FormData(form);
        const content = String(values.get('content_md') || '').trim();
        if (!content || sending) return;
        sending = true;
        handle?.set({ busy: true });
        try {
            const result = await apiFetch(`/api/blog/posts/${Number(context.post.id)}/comments`, {
                method: 'POST',
                body: {
                    content_md: content,
                    parent_comment_id: Number(context.comment.id),
                    author_display_mode: context.forced_mode || String(values.get('author_display_mode') || 'real_name'),
                },
                silent: true,
            });
            await afterReply?.(result);
            const done = [createComponent('chip', { label: '已回复', tone: 'success', size: 'sm' })];
            if (result.moderation_state === 'pending') {
                done.push(element('p', 'mc-blog-reply__hint', '回复正在检查，通过后对方就能看到。'));
            }
            done.push(goToPost(context.post.id, result.id));
            handle?.destroy();
            panel.replaceChildren(...done);
        } catch (error) {
            sending = false;
            handle?.set({ busy: false });
            showToast(error.message || '回复失败，请稍后重试。', 'error');
        }
    });
    return form;
}

async function markRepliedRead(item, onRead) {
    if (!item.is_unread) return;
    try {
        const response = await apiFetch('/api/message-center/read', {
            method: 'POST', body: { notification_ids: [Number(item.id)], include_private: false }, silent: true,
        });
        onRead?.(response.summary, Number(item.id));
    } catch {
        // The reply itself succeeded; the card simply stays unread.
    }
}

/** The "通知设置" dialog: one LQ switch per blog event; forced events are listed, not switchable. */
export async function openNotificationSettings() {
    const [dialogs, data] = await Promise.all([
        LQ.load('dialogs'),
        apiFetch('/api/message-center/preferences', { silent: true }).catch((error) => ({ error })),
    ]);
    const body = element('div', 'mc-notify-settings');
    if (data.error || !Array.isArray(data.groups)) {
        body.append(element('p', 'mc-blog-reply__status', data.error?.message || '设置加载失败，请稍后重试。'));
    }
    for (const group of data.groups || []) {
        const section = element('section', 'mc-notify-settings__group');
        section.append(element('h3', 'mc-notify-settings__title', group.label));
        for (const event of group.events || []) {
            if (event.forced) {
                section.append(element('p', 'mc-notify-settings__forced', `${event.label}：${event.help || '始终通知'}`));
                continue;
            }
            const field = switchControl({
                id: `mc-pref-${event.key.replace(/[^a-z0-9]+/gi, '-')}`, label: event.label, name: event.key,
                checked: Boolean(event.enabled), help: event.help || '', size: 'sm',
            });
            const input = field.querySelector('input');
            input?.addEventListener('change', () => { void savePreference(input, event.key); });
            section.append(field);
        }
        body.append(section);
    }
    const footer = element('div', 'mc-notify-settings__actions');
    footer.append(createComponent('button', { label: '完成', variant: 'prominent', size: 'sm', attrs: { 'data-lq-dialog-close': '' } }));
    dialogs.openDialog(dialogs.createDialog({ title: '通知设置', body, footer, size: 'sm' }));
}

async function savePreference(input, eventKey) {
    const enabled = input.checked;
    input.disabled = true;
    try {
        await apiFetch('/api/message-center/preferences', { method: 'PUT', body: { event_key: eventKey, enabled }, silent: true });
    } catch (error) {
        input.checked = !enabled;
        showToast(error.message || '保存失败，请稍后重试。', 'error');
    } finally {
        input.disabled = false;
    }
}
