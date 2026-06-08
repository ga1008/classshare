import { apiFetch } from '/static/js/api.js';
import { escapeHtml, showMessage } from '/static/js/ui.js';

const PAGE_SIZE = 60;

const parseJsonScript = (id, fallback) => {
    const el = document.getElementById(id);
    if (!el) return fallback;
    try {
        return JSON.parse(el.textContent || '');
    } catch {
        return fallback;
    }
};

const state = {
    documents: parseJsonScript('gw-documents-data', []),
    total: parseJsonScript('gw-documents-total', 0),
    keyword: '',
    category: '',
    unread: false,
    offset: 0,
};

const refs = {
    tbody: document.getElementById('gw-doc-tbody'),
    count: document.getElementById('gw-doc-count'),
    more: document.getElementById('gw-doc-more'),
    search: document.getElementById('gw-doc-search'),
    category: document.getElementById('gw-doc-category'),
    unread: document.getElementById('gw-doc-unread'),
    refresh: document.getElementById('gw-doc-refresh'),
    sync: document.getElementById('gw-doc-sync'),
    lastSync: document.getElementById('gw-doc-last-sync'),
};

function formatDateTime(value) {
    if (!value) return '';
    return String(value).trim().replace('T', ' ').replace(/\.\d+$/, '').slice(0, 16);
}

function fileButtons(doc) {
    const buttons = [];
    if (doc.file_url || doc.has_local_file) {
        buttons.push(`<a class="btn btn-outline btn-sm" href="/api/manage/gongwen/documents/${doc.id}/file?which=primary" target="_blank" rel="noopener">正文</a>`);
    }
    if (doc.attachment_url || doc.has_local_attachment) {
        buttons.push(`<a class="btn btn-outline btn-sm" href="/api/manage/gongwen/documents/${doc.id}/file?which=attachment" target="_blank" rel="noopener">附件</a>`);
    }
    if (!buttons.length) {
        buttons.push('<span class="gwlist-time">无附件</span>');
    }
    return buttons.join('');
}

function rowHtml(doc) {
    const unreadDot = doc.is_read ? '' : '<span class="gwlist-unread-dot" title="未读"></span>';
    const sn = doc.sn ? `<span class="gwlist-sn">${escapeHtml(doc.sn)}</span>` : '';
    const cat = doc.category_name ? `<span class="gwlist-cat">${escapeHtml(doc.category_name)}</span>` : '';
    return `
        <tr>
            <td><span class="gwlist-title">${unreadDot}${sn}${escapeHtml(doc.title || '(无标题)')}</span></td>
            <td>${escapeHtml(doc.author || '-')}</td>
            <td>${escapeHtml(doc.sender_name || '-')}</td>
            <td>${cat}</td>
            <td class="gwlist-time">${escapeHtml(formatDateTime(doc.publish_time))}</td>
            <td><div class="gwlist-row-actions">${fileButtons(doc)}</div></td>
        </tr>`;
}

function render() {
    if (!refs.tbody) return;
    if (!state.documents.length) {
        refs.tbody.innerHTML = `
            <tr><td colspan="6">
                <div class="gwlist-empty">
                    <strong>没有匹配的公文</strong>
                    调整筛选条件，或点击「立即同步」从统一认证账号拉取收件箱公文。
                </div>
            </td></tr>`;
    } else {
        refs.tbody.innerHTML = state.documents.map(rowHtml).join('');
    }
    if (refs.count) {
        refs.count.textContent = state.total
            ? `显示 ${state.documents.length} / ${state.total} 条公文`
            : '';
    }
    if (refs.more) {
        refs.more.style.display = state.documents.length < state.total ? '' : 'none';
    }
}

function buildParams(offset) {
    const params = new URLSearchParams();
    if (state.keyword) params.set('keyword', state.keyword);
    if (state.category) params.set('category', state.category);
    if (state.unread) params.set('unread', '1');
    params.set('limit', String(PAGE_SIZE));
    params.set('offset', String(offset));
    return params;
}

let searchTimer = null;

async function reload() {
    setBusy(refs.refresh, true, '加载中');
    try {
        const result = await apiFetch(`/api/manage/gongwen/documents?${buildParams(0).toString()}`);
        state.documents = result.documents || [];
        state.total = result.total || 0;
        state.offset = state.documents.length;
        if (refs.lastSync && result.summary) {
            refs.lastSync.textContent = result.summary.last_synced_at
                ? `上次同步：${formatDateTime(result.summary.last_synced_at)}`
                : '尚未同步';
        }
        render();
    } catch (error) {
        showMessage(error.message || '读取公文失败。', 'error');
    } finally {
        setBusy(refs.refresh, false);
    }
}

async function loadMore() {
    setBusy(refs.more, true, '加载中');
    try {
        const result = await apiFetch(`/api/manage/gongwen/documents?${buildParams(state.offset).toString()}`);
        state.documents = state.documents.concat(result.documents || []);
        state.total = result.total || state.total;
        state.offset = state.documents.length;
        render();
    } catch (error) {
        showMessage(error.message || '加载更多失败。', 'error');
    } finally {
        setBusy(refs.more, false);
    }
}

async function syncNow() {
    setBusy(refs.sync, true, '同步中…');
    try {
        const result = await apiFetch('/api/manage/system/gongwen-sync', { method: 'POST' });
        const status = result.auto_sync?.status || result.status;
        if (status === 'missing_credential') {
            showMessage('尚未对接统一认证账号，请先前往「公文同步」保存账号。', 'warning');
            window.setTimeout(() => { window.location.href = '/manage/system/gongwen-integrations'; }, 1200);
            return;
        }
        showMessage(result.message || '公文同步完成。', status === 'failed' ? 'warning' : 'success');
        await reload();
    } catch (error) {
        showMessage(error.message || '公文同步失败。', 'error');
    } finally {
        setBusy(refs.sync, false);
    }
}

function setBusy(button, busy, label) {
    if (!button) return;
    if (busy) {
        button.dataset.originalText = button.textContent;
        button.textContent = label || '处理中';
        button.disabled = true;
    } else {
        button.textContent = button.dataset.originalText || button.textContent;
        button.disabled = false;
    }
}

refs.search?.addEventListener('input', () => {
    state.keyword = refs.search.value.trim();
    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(reload, 280);
});
refs.category?.addEventListener('change', () => {
    state.category = refs.category.value;
    reload();
});
refs.unread?.addEventListener('click', () => {
    state.unread = !state.unread;
    refs.unread.classList.toggle('is-active', state.unread);
    refs.unread.dataset.active = state.unread ? '1' : '0';
    reload();
});
refs.refresh?.addEventListener('click', reload);
refs.more?.addEventListener('click', loadMore);
refs.sync?.addEventListener('click', syncNow);

state.offset = state.documents.length;
render();
