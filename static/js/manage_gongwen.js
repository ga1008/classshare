import { apiFetch } from '/static/js/api.js';
import { escapeHtml, showMessage } from '/static/js/ui.js';

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
};

const refs = {
    tbody: document.getElementById('gw-doc-tbody'),
    foot: document.getElementById('gw-doc-foot'),
    search: document.getElementById('gw-doc-search'),
    category: document.getElementById('gw-doc-category'),
    unread: document.getElementById('gw-doc-unread'),
    refresh: document.getElementById('gw-doc-refresh'),
};

function formatDateTime(value) {
    if (!value) return '';
    const text = String(value).trim();
    return text.replace('T', ' ').replace(/\.\d+$/, '').slice(0, 16);
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

function renderRows() {
    if (!refs.tbody) return;
    if (!state.documents.length) {
        refs.tbody.innerHTML = `
            <tr><td colspan="6">
                <div class="gwlist-empty">
                    <strong>没有匹配的公文</strong>
                    调整筛选条件，或前往「公文同步」完成账号验证后再同步。
                </div>
            </td></tr>`;
        if (refs.foot) refs.foot.textContent = '';
        return;
    }
    refs.tbody.innerHTML = state.documents.map((doc) => {
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
    }).join('');
    if (refs.foot) {
        refs.foot.textContent = `显示 ${state.documents.length} 条，共 ${state.total} 条公文。`;
    }
}

let searchTimer = null;

async function reload() {
    const params = new URLSearchParams();
    if (state.keyword) params.set('keyword', state.keyword);
    if (state.category) params.set('category', state.category);
    if (state.unread) params.set('unread', '1');
    params.set('limit', '120');
    setBusy(refs.refresh, true, '加载中');
    try {
        const result = await apiFetch(`/api/manage/gongwen/documents?${params.toString()}`);
        state.documents = result.documents || [];
        state.total = result.total || 0;
        renderRows();
    } catch (error) {
        showMessage(error.message || '读取公文失败。', 'error');
    } finally {
        setBusy(refs.refresh, false);
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

renderRows();
