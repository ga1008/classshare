/**
 * Small DOM helpers shared by the social modals. Everything visual goes
 * through the LQ factories (Button/Avatar/Badge/Dialog); this file only
 * builds plain structural nodes, formats data and lazy-loads page assets.
 */
import { createComponent } from '../lq/components.js';
import { createDialog } from '../lq/dialogs.js';
import { createDomainModal } from '../lq/domain-modal.js';
import { toast } from '../lq/toast.js';

/**
 * @param {string} tag
 * @param {Record<string, any>} [attrs] `class`, `text`, `on*` handlers, booleans, data-*.
 * @param {Array<Node|string|null|false|undefined>} [children]
 * @returns {HTMLElement}
 */
export function el(tag, attrs = {}, children = []) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
        if (value === null || value === undefined || value === false) continue;
        if (key === 'text') node.textContent = String(value);
        else if (key.startsWith('on') && typeof value === 'function') node.addEventListener(key.slice(2), value);
        else if (value === true) node.setAttribute(key, '');
        else node.setAttribute(key, String(value));
    }
    for (const child of children) {
        if (child === null || child === undefined || child === false) continue;
        node.append(child);
    }
    return node;
}

/** Shared Button factory; `label` empty means icon-only (then `aria` is required). */
export function lqButton({ label = '', icon = null, variant = 'glass', size = 'sm', aria = '', attrs = {}, disabled = false, type = 'button', href = null, onClick } = {}) {
    const props = { label, variant, size, type, disabled, attrs: { ...attrs } };
    if (icon) props.icon = icon;
    if (href) props.href = href;
    if (aria) props.attrs['aria-label'] = aria;
    const node = createComponent('button', props);
    if (onClick) node.addEventListener('click', (event) => { if (!node.disabled) onClick(event); });
    return node;
}

export function lqBadge(value, tone = 'danger') {
    const count = Number(value) || 0;
    return count > 0 ? createComponent('badge', { value: count > 99 ? '99+' : String(count), tone }) : null;
}

/** Avatar with initials fallback; student avatars get the cultivation emblem automatically. */
export function lqAvatar(name, src, size = 40) {
    return createComponent('avatar', { name: String(name || '?').trim() || '?', src: src || null, size });
}

export function notify(message, tone = 'info', extra = {}) {
    try { toast(String(message || '操作完成'), { tone, ...extra }); } catch { /* toast is decorative */ }
}

const MIB = 1024 * 1024;
export function formatSize(bytes) {
    const value = Number(bytes) || 0;
    if (value >= MIB) return `${(value / MIB).toFixed(value >= 10 * MIB ? 0 : 1)} MB`;
    if (value >= 1024) return `${Math.round(value / 1024)} KB`;
    return `${value} B`;
}

export function parseTime(value) {
    // Social stamps are UTC ISO with an offset; legacy message-center stamps are naive UTC.
    let text = String(value || '').trim();
    if (/^\d{4}-\d\d-\d\d[ T]\d\d:\d\d(:\d\d(\.\d+)?)?$/.test(text)) text = `${text.replace(' ', 'T')}Z`;
    const date = value instanceof Date ? value : new Date(text);
    return Number.isNaN(date.getTime()) ? null : date;
}

export function formatClock(value) {
    const date = parseTime(value);
    return date ? date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) : '';
}

export function dayKey(value) {
    const date = parseTime(value);
    return date ? `${date.getFullYear()}-${date.getMonth() + 1}-${date.getDate()}` : '';
}

export function formatDay(value) {
    const date = parseTime(value);
    if (!date) return '';
    const today = new Date();
    if (dayKey(date) === dayKey(today)) return '今天';
    if (dayKey(date) === dayKey(new Date(today.getTime() - 86400000))) return '昨天';
    const options = { month: 'long', day: 'numeric', weekday: 'short' };
    if (date.getFullYear() !== today.getFullYear()) options.year = 'numeric';
    return date.toLocaleDateString('zh-CN', options);
}

export function formatRelative(value) {
    const date = parseTime(value);
    if (!date) return '';
    const minutes = Math.round((Date.now() - date.getTime()) / 60000);
    if (minutes < 1) return '刚刚';
    if (minutes < 60) return `${minutes} 分钟前`;
    if (minutes < 1440) return `${Math.round(minutes / 60)} 小时前`;
    return `${formatDay(date)} ${formatClock(date)}`;
}

/* ---------------------------------------------------------------- assets */

const loaded = new Map();
function entryAsset(name) {
    return document.querySelector('[data-social-entry]')?.dataset?.[name] || '';
}

function loadOnce(url, build) {
    if (!url) return Promise.resolve(false);
    if (!loaded.has(url)) {
        loaded.set(url, new Promise((resolve) => {
            const node = build(url);
            node.addEventListener('load', () => resolve(true), { once: true });
            node.addEventListener('error', () => resolve(false), { once: true });
            document.head.append(node);
        }));
    }
    return loaded.get(url);
}

/** social.css is linked lazily: the topbar entry itself only needs shared LQ CSS. */
export function ensureSocialStyles() {
    return loadOnce(entryAsset('socialCss'), (href) => el('link', { rel: 'stylesheet', href }));
}

/** MarkdownRuntime is a classic script trio; reuse the page copy when present. */
export async function ensureMarkdown() {
    if (window.MarkdownRuntime?.parse) return true;
    for (const name of ['socialPolyfills', 'socialMarked', 'socialMarkdown']) {
        await loadOnce(entryAsset(name), (src) => el('script', { src }));
    }
    return Boolean(window.MarkdownRuntime?.parse);
}

/** Sanitized Markdown (untrusted) into a `.lq-prose` node; plain text when the runtime is missing. */
export function renderMarkdown(body, format = 'md') {
    const node = el('div', { class: 'lq-prose social-md' });
    const text = String(body || '');
    const runtime = window.MarkdownRuntime;
    if (format === 'md' && runtime?.parse) {
        node.innerHTML = runtime.parse(text, { untrusted: true, breaks: true, fallbackMode: 'lines', silent: true, emptyHtml: '' });
        node.querySelectorAll('a[href]').forEach((link) => { link.target = '_blank'; link.rel = 'noopener noreferrer'; });
    } else {
        node.textContent = text;
    }
    return node;
}

/* ---------------------------------------------------------------- dialogs */

/**
 * A persistent LQ dialog: built once by the shared factory, owned by
 * createDomainModal (LQ.layer holds stack, focus, inert and scroll lock).
 */
export function createSocialDialog({ id, title, size = 'xl', body, className = '', onClose, initialFocus }) {
    const root = createDialog({ id, title, size, body, closeLabel: `关闭${title}` });
    const surface = root.querySelector('.lq-dialog__surface');
    surface.classList.add('social-dialog', ...className.split(/\s+/).filter(Boolean));
    document.body.append(root);
    let isOpen = false;
    const owner = createDomainModal(root, {
        surface,
        initialFocus,
        onClose: (...args) => { isOpen = false; onClose?.(...args); },
    });
    root.addEventListener('click', (event) => {
        if (event.target.closest?.('[data-lq-dialog-close]')) { event.preventDefault(); void owner.close('button'); }
    });
    return {
        root, surface,
        body: root.querySelector('.lq-dialog__body'),
        title: root.querySelector('.lq-dialog__head > h2'),
        open(trigger) { isOpen = true; return owner.open({ trigger }); },
        close(reason = 'programmatic') { return owner.close(reason); },
        isOpen: () => isOpen,
    };
}

export function emptyState(title, description = '', reason = 'empty') {
    return el('div', { class: 'lq-empty lq-empty--inline social-empty', 'data-reason': reason, role: reason === 'error' ? 'alert' : null }, [
        el('div', { class: 'lq-empty__copy' }, [
            el('strong', { class: 'lq-empty__title', text: title }),
            description ? el('p', { class: 'lq-empty__description', text: description }) : null,
        ]),
    ]);
}

export function debounce(fn, wait) {
    let timer = 0;
    const wrapped = (...args) => { window.clearTimeout(timer); timer = window.setTimeout(() => fn(...args), wait); };
    wrapped.cancel = () => window.clearTimeout(timer);
    return wrapped;
}

export const nonce = () => (crypto.randomUUID ? crypto.randomUUID() : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`);
