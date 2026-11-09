import { apiFetch } from './api.js';
import { node, action, memoryItem, empty } from './wanderer_world_views.js';

const root = document.querySelector('[data-wanderer-home]');
if (root) {
    const form = root.querySelector('[data-wh-memory-search]');
    const list = root.querySelector('[data-wh-memory-list]');
    const pager = root.querySelector('[data-wh-memory-pager]');
    const status = root.querySelector('[data-wh-memory-status]');
    let cursor = null, next = pager.dataset.nextCursor || null, history = [], serial = 0, request;
    async function load() {
        request?.abort(); request = new AbortController();
        const ticket = ++serial;
        const params = new URLSearchParams({ limit: '12', q: form.elements.q.value, order: form.elements.order.value });
        if (cursor != null) params.set('before', String(cursor));
        pager.querySelectorAll('button').forEach(button => { button.disabled = true; });
        list.setAttribute('aria-busy', 'true'); status.textContent = '正在寻找记忆…';
        try {
            const data = await apiFetch(`/api/wanderers/${root.dataset.studentId}/memory?${params}`, { silent: true, signal: request.signal });
            if (ticket !== serial) return;
            next = data.next_cursor;
            const rows = (data.items || []).map(memoryItem);
            list.replaceChildren(...(rows.length ? rows : [node('li', {}, [empty('没有找到这段公开经历。')])]));
            status.textContent = `第 ${history.length + 1} 页 · ${rows.length} 段经历`;
            renderPager();
        } catch (error) {
            if (ticket !== serial || error.name === 'AbortError') return;
            status.textContent = error.message || '记忆读取失败，搜索条件已保留';
            pager.replaceChildren(action('重试', load));
        } finally { if (ticket === serial) list.removeAttribute('aria-busy'); }
    }
    function renderPager() {
        const previous = action('上一页', () => { cursor = history.pop() ?? null; load(); });
        const later = action('下一页', () => { history.push(cursor); cursor = next; load(); });
        previous.disabled = history.length === 0; later.disabled = next == null;
        pager.replaceChildren(previous, node('span', { text: `第 ${history.length + 1} 页` }), later);
    }
    form.addEventListener('submit', event => { event.preventDefault(); cursor = null; history = []; load(); });
    form.elements.order.addEventListener('change', () => { cursor = null; history = []; load(); });
    renderPager();
    window.addEventListener('pagehide', () => request?.abort(), { once: true });
}
