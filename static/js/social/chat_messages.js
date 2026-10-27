/**
 * Message rendering for the chat window. Bubbles are the shared LQ Bubble
 * (createContent('bubble')); Markdown goes through MarkdownRuntime (untrusted,
 * sanitized); images open in the shared lightbox via `data-ls-lightbox`.
 * Pure DOM construction — the chat controller owns data, requests and events.
 */
import { createContent } from '../lq/content.js';
import { createComponent } from '../lq/components.js';
import { createIcon } from '../lq/icons.js';
import { lanStore } from './social_lan.js';
import { dayKey, el, formatClock, formatDay, formatSize, lqAvatar, lqButton, renderMarkdown } from './social_dom.js';

const KIND_LABEL = { image: '图片', video: '视频', audio: '语音', file: '文件' };

/**
 * @typedef {{me: string, type: 'dm'|'group', canRecall: (m: object) => boolean}} RenderContext
 */

export function isMine(message, me) {
    return Boolean(me) && message?.sender?.identity === me;
}

export function daySeparator(createdAt) {
    return el('div', { class: 'social-day', role: 'separator', 'data-day': dayKey(createdAt) }, [
        el('span', { text: formatDay(createdAt) }),
    ]);
}

function lanChip() {
    return createComponent('chip', { label: '局域网直传', tone: 'info', size: 'sm', attrs: { 'data-social-lan-chip': '' } });
}

function mediaUrl(item) {
    const local = lanStore.get(item.id);
    if (local) return { url: local.url, local: true };
    if (item.expired || !item.server_ready) return null;
    return { url: item.url, local: false };
}

function placeholder(item, text, state) {
    return el('div', { class: 'social-file social-file--placeholder', 'data-media-state': state, 'data-media-id': String(item.id) }, [
        createIcon(state === 'expired' ? 'clock' : 'loader-circle'),
        el('span', { class: 'social-file__copy' }, [
            el('strong', { text: item.filename || KIND_LABEL[item.kind] || '文件' }),
            el('small', { text: `${text}${item.size ? ` · ${formatSize(item.size)}` : ''}` }),
        ]),
    ]);
}

function imageGrid(items, messageId) {
    const grid = el('div', { class: 'social-media-grid', 'data-count': String(Math.min(items.length, 4)) });
    for (const { item, source } of items) {
        grid.append(el('button', {
            type: 'button', class: 'social-media-grid__item', 'data-lq-shape': 'surface',
            'data-ls-lightbox': '', 'data-ls-lightbox-group': `social-${messageId}`,
            'data-ls-lightbox-src': source.url, 'data-ls-lightbox-original': source.url,
            'data-ls-lightbox-title': item.filename || '图片',
            'data-ls-lightbox-download': source.local ? '' : `${item.url}?download=true`,
            'aria-label': `查看图片 ${item.filename || ''}`.trim(),
        }, [el('img', {
            src: source.local ? source.url : (item.thumb_url || item.url), alt: item.filename || '图片',
            loading: 'lazy', decoding: 'async',
            width: item.width || null, height: item.height || null,
        })]));
    }
    return grid;
}

function fileCard(item, source) {
    const href = source.local ? source.url : `${item.url}?download=true`;
    return el('div', { class: 'social-file', 'data-media-id': String(item.id) }, [
        createIcon('file-text'),
        el('span', { class: 'social-file__copy' }, [
            el('strong', { text: item.filename || '文件' }),
            el('small', { text: [formatSize(item.size), source.local ? '已通过局域网接收' : ''].filter(Boolean).join(' · ') }),
        ]),
        el('a', { class: 'lq-btn lq-btn--soft lq-btn--sm', 'data-lq-component': 'button', href, download: item.filename || 'file', text: '下载' }),
    ]);
}

export function renderMedia(message) {
    const media = message.media || [];
    if (!media.length) return null;
    const box = el('div', { class: 'social-media' });
    const images = [];
    const rest = [];
    for (const item of media) {
        if (item.expired) { rest.push(placeholder(item, '文件已过期', 'expired')); continue; }
        const source = mediaUrl(item);
        if (!source) { rest.push(placeholder(item, '源设备补传中', 'pending')); continue; }
        if (item.kind === 'image') { images.push({ item, source }); continue; }
        if (item.kind === 'video') rest.push(el('video', { class: 'social-video', controls: true, preload: 'metadata', src: source.url, 'aria-label': item.filename || '视频' }));
        else if (item.kind === 'audio') rest.push(el('audio', { class: 'social-audio', controls: true, preload: 'metadata', src: source.url, 'aria-label': item.filename || '语音' }));
        else rest.push(fileCard(item, source));
    }
    if (images.length) box.append(imageGrid(images, message.id));
    box.append(...rest);
    return box;
}

function replyQuote(reply) {
    return el('button', { type: 'button', class: 'social-quote', 'data-lq-shape': 'surface', 'data-jump': String(reply.id), 'aria-label': `跳到被引用的消息：${reply.sender_name}` }, [
        el('strong', { text: reply.sender_name || '' }),
        el('span', { text: reply.preview || '' }),
    ]);
}

function systemLine(message, text, extra = '') {
    return el('div', { class: `social-msg social-msg--system ${extra}`.trim(), 'data-message-id': String(message.id), role: 'note' }, [
        el('span', { text }),
    ]);
}

/**
 * @param {object} message Message (§7)
 * @param {RenderContext} ctx
 * @param {{connected?: boolean}} [options]
 * @returns {HTMLElement}
 */
export function renderMessage(message, ctx, { connected = false } = {}) {
    const mine = isMine(message, ctx.me);
    if (message.message_type === 'system') return systemLine(message, message.body);
    if (message.message_type === 'nudge') return systemLine(message, message.body || '拍了拍', 'social-msg--nudge');
    if (message.recalled) return systemLine(message, `${mine ? '你' : message.sender?.name || '对方'}撤回了一条消息`);

    const slot = [];
    if (message.reply_to) slot.push(replyQuote(message.reply_to));
    if (message.body) slot.push(renderMarkdown(message.body, message.body_format));
    const media = renderMedia(message);
    if (media) slot.push(media);
    const bubble = createContent('bubble', {
        side: mine ? 'outgoing' : 'incoming',
        author: message.sender?.name || '已注销用户',
        time: formatClock(message.created_at) || '—',
        datetime: message.created_at || undefined,
        connected,
    }, { content: slot });

    const actions = el('div', { class: 'social-msg__actions' }, [
        lqButton({ label: '回复', variant: 'ghost', size: 'sm', attrs: { 'data-msg-act': 'reply' } }),
        ctx.canRecall(message) ? lqButton({ label: '撤回', variant: 'ghost', size: 'sm', attrs: { 'data-msg-act': 'recall' } }) : null,
    ]);
    const meta = el('div', { class: 'social-msg__meta' }, [
        (message.media || []).some((item) => item.delivered_via === 'lan') ? lanChip() : null,
        mine && ctx.type === 'dm' ? el('small', { class: 'social-receipt', 'data-receipt': '' }) : null,
        actions,
    ]);
    const showAvatar = !mine && !connected;
    return el('div', {
        class: `social-msg social-msg--${mine ? 'out' : 'in'}${connected ? ' is-connected' : ''}`,
        'data-message-id': String(message.id), 'data-sender': message.sender?.identity || '',
    }, [
        el('span', { class: 'social-msg__avatar', 'aria-hidden': 'true' }, [showAvatar ? lqAvatar(message.sender?.name, message.sender?.avatar_url, 32) : null]),
        el('div', { class: 'social-msg__column' }, [bubble, meta]),
    ]);
}

/** Should `message` visually join the previous one (same sender, same day, < 3 min)? */
export function joinsPrevious(previous, message) {
    const chatty = (item) => item && ['text', 'media'].includes(item.message_type);
    if (!chatty(previous) || !chatty(message)) return false;
    if (previous.recalled || message.recalled || previous.sender?.identity !== message.sender?.identity) return false;
    const gap = new Date(message.created_at).getTime() - new Date(previous.created_at).getTime();
    return dayKey(previous.created_at) === dayKey(message.created_at) && gap >= 0 && gap < 180000;
}
