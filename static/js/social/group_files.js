/** Paginated group media; each filter owns a generation so late pages cannot mix. */
import { createComponent } from '../lq/components.js';
import { socialApi, errorText } from './social_api.js';
import { el, emptyState, formatRelative, formatSize, lqButton } from './social_dom.js';

const KINDS = [['', '全部'], ['image', '图片'], ['video', '视频'], ['audio', '语音'], ['file', '文件']];
export function createGroupFiles({ groupId, onUnavailable }) {
    let kind = '', generation = 0, disposed = false, busy = false, items = [], hasMore = false;
    const list = el('div', { class: 'social-side__files', 'aria-live': 'polite' });
    const status = el('div', { class: 'social-group-files__status' });
    const more = lqButton({ label: '加载更多文件', variant: 'ghost', attrs: { 'data-group-files-more': '' }, onClick: () => void load(true) });
    more.hidden = true;
    const filters = el('div', { class: 'social-side__filters', role: 'group', 'aria-label': '按类型筛选群文件' }, KINDS.map(([value, label]) => createComponent('chip', {
        label, kind: 'filter', size: 'sm', pressed: value === kind, attrs: { 'data-file-kind': value },
    })));
    const element = el('div', { class: 'social-group-files' }, [filters, list, status, more]);
    function downloadLink(item) {
        try {
            const url = new URL(item.url, location.href);
            if (url.origin !== location.origin || !item.url) return null;
            url.searchParams.set('download', 'true'); return `${url.pathname}${url.search}`;
        } catch { return null; }
    }
    function render() {
        list.replaceChildren(items.length ? el('ul', { class: 'social-side__links', role: 'list' }, items.map(item => {
            const href = downloadLink(item);
            const unavailable = item.expired ? '已过期' : !item.server_ready ? '补传中' : !href ? '暂不可用' : '';
            return el('li', { 'data-group-file': item.id }, [unavailable
                ? el('span', { text: `${item.filename || '文件'}（${unavailable}）` })
                : el('a', { href, download: item.filename || 'file', text: item.filename || '文件' }),
            el('small', { text: [formatSize(item.size), item.sender_name, formatRelative(item.sent_at)].filter(Boolean).join(' · ') })]);
        })) : emptyState('还没有群文件', kind ? '试试其他文件类型。' : '聊天中分享的图片、语音和文件会收在这里。'));
        more.hidden = !hasMore; more.disabled = busy;
    }
    async function load(append = false) {
        if (disposed || (append && busy)) return;
        const ticket = ++generation, requestedKind = kind;
        const before = append && items.length ? Math.min(...items.map(item => Number(item.id)).filter(Boolean)) : 0;
        busy = true; more.disabled = true; status.replaceChildren();
        if (!append) list.replaceChildren(el('p', { class: 'social-hint', text: '正在读取群文件…' }));
        try {
            const page = await socialApi.groupFiles(groupId, requestedKind, before);
            if (disposed || ticket !== generation) return;
            items = [...new Map([...(append ? items : []), ...(page.items || [])].map(item => [item.id, item])).values()];
            hasMore = Boolean(page.has_more); busy = false; render();
        } catch (error) {
            if (disposed || ticket !== generation) return;
            busy = false;
            if ([403, 404].includes(error.status)) { onUnavailable?.(); return; }
            if (!append) { items = []; hasMore = false; list.replaceChildren(emptyState('群文件暂时无法读取', errorText(error), 'error')); }
            status.replaceChildren(el('p', { class: 'social-hint', role: 'alert', text: append ? errorText(error) : '' }),
                lqButton({ label: '重试', onClick: () => void load(append) }));
            more.hidden = true;
        }
    }
    filters.addEventListener('click', event => {
        const choice = event.target.closest('[data-file-kind]');
        if (!choice || choice.dataset.fileKind === kind) return;
        kind = choice.dataset.fileKind; items = []; hasMore = false; more.hidden = true;
        filters.querySelectorAll('[data-file-kind]').forEach(node => node.setAttribute('aria-pressed', String(node === choice)));
        void load();
    });
    return { element, refresh: () => load(), destroy() { disposed = true; generation++; } };
}
