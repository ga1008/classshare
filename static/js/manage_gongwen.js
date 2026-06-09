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
    author: '',
    sender: '',
    hasAttachment: false,
    unread: false,
    page: 1,
    pageSize: 20,
    scopeOptions: null,
    editingId: null,
};

const refs = {
    tbody: document.getElementById('gw-doc-tbody'),
    count: document.getElementById('gw-doc-count'),
    search: document.getElementById('gw-doc-search'),
    category: document.getElementById('gw-doc-category'),
    author: document.getElementById('gw-doc-author'),
    sender: document.getElementById('gw-doc-sender'),
    attach: document.getElementById('gw-doc-attach'),
    unread: document.getElementById('gw-doc-unread'),
    refresh: document.getElementById('gw-doc-refresh'),
    sync: document.getElementById('gw-doc-sync'),
    lastSync: document.getElementById('gw-doc-last-sync'),
    pagesize: document.getElementById('gw-doc-pagesize'),
    prev: document.getElementById('gw-doc-prev'),
    next: document.getElementById('gw-doc-next'),
    pageinfo: document.getElementById('gw-doc-pageinfo'),
    // scope editor
    scopeModal: document.getElementById('gw-scope-modal'),
    scopeClose: document.getElementById('gw-scope-close'),
    scopeCancel: document.getElementById('gw-scope-cancel'),
    scopeSave: document.getElementById('gw-scope-save'),
    scopeSubtitle: document.getElementById('gw-scope-subtitle'),
    scopeLevel: document.getElementById('gw-scope-level'),
    scopeCollegeGroup: document.getElementById('gw-scope-college-group'),
    scopeCollege: document.getElementById('gw-scope-college'),
    scopeDepartmentGroup: document.getElementById('gw-scope-department-group'),
    scopeDepartment: document.getElementById('gw-scope-department'),
    scopeOpenness: document.getElementById('gw-scope-openness'),
    // reader
    reader: document.getElementById('gw-reader'),
    readerClose: document.getElementById('gw-reader-close'),
    readerRefresh: document.getElementById('gw-reader-refresh'),
    readerSn: document.getElementById('gw-reader-sn'),
    readerTitle: document.getElementById('gw-reader-title'),
    readerMeta: document.getElementById('gw-reader-meta'),
    readerBody: document.getElementById('gw-reader-body'),
};

function formatDateTime(value) {
    if (!value) return '';
    return String(value).trim().replace('T', ' ').replace(/\.\d+$/, '').slice(0, 16);
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

// ---------------- list + pagination ----------------

function fileButtons(doc) {
    const buttons = [];
    if (doc.file_url || doc.has_local_file) {
        buttons.push(`<a class="btn btn-outline btn-sm" href="/api/manage/gongwen/documents/${doc.id}/file?which=primary" target="_blank" rel="noopener">正文</a>`);
    }
    if (doc.attachment_url || doc.has_local_attachment) {
        buttons.push(`<a class="btn btn-outline btn-sm" href="/api/manage/gongwen/documents/${doc.id}/file?which=attachment" target="_blank" rel="noopener">附件</a>`);
    }
    buttons.push(`<button type="button" class="btn btn-ghost btn-sm" data-scope-edit="${doc.id}">归属</button>`);
    return buttons.join('');
}

function rowHtml(doc) {
    const unreadDot = doc.is_read ? '' : '<span class="gwlist-unread-dot" title="未读"></span>';
    const sn = doc.sn ? `<span class="gwlist-sn">${escapeHtml(doc.sn)}</span>` : '';
    const cat = doc.category_name ? `<span class="gwlist-cat">${escapeHtml(doc.category_name)}</span>` : '';
    const openLevel = doc.openness || 'school';
    return `
        <tr>
            <td><span class="gwlist-title" data-open-reader="${doc.id}" title="点击查看原文">${unreadDot}${sn}${escapeHtml(doc.title || '(无标题)')}</span></td>
            <td>${escapeHtml(doc.author || '-')}</td>
            <td>${escapeHtml(doc.sender_name || '-')}</td>
            <td>${cat}</td>
            <td class="gwlist-time">${escapeHtml(formatDateTime(doc.publish_time))}</td>
            <td>
                <div class="gwlist-scope">
                    <span class="gwlist-attr">${escapeHtml(doc.attribution_label || '本校')}</span>
                    <span class="gwlist-open lvl-${escapeHtml(openLevel)}">${escapeHtml(doc.openness_label || '本校可见')}</span>
                </div>
            </td>
            <td><div class="gwlist-row-actions">${fileButtons(doc)}</div></td>
        </tr>`;
}

function totalPages() {
    return Math.max(1, Math.ceil(state.total / state.pageSize));
}

function render() {
    if (!refs.tbody) return;
    if (!state.documents.length) {
        refs.tbody.innerHTML = `
            <tr><td colspan="7">
                <div class="gwlist-empty">
                    <strong>没有匹配的公文</strong>
                    调整筛选条件，或点击「立即同步」从统一认证账号拉取收件箱公文。
                </div>
            </td></tr>`;
    } else {
        refs.tbody.innerHTML = state.documents.map(rowHtml).join('');
    }
    const pages = totalPages();
    if (refs.count) refs.count.textContent = `共 ${state.total} 条公文`;
    if (refs.pageinfo) refs.pageinfo.textContent = `${state.page} / ${pages}`;
    if (refs.prev) refs.prev.disabled = state.page <= 1;
    if (refs.next) refs.next.disabled = state.page >= pages;
}

function buildParams() {
    const params = new URLSearchParams();
    if (state.keyword) params.set('keyword', state.keyword);
    if (state.category) params.set('category', state.category);
    if (state.author) params.set('author', state.author);
    if (state.sender) params.set('sender', state.sender);
    if (state.hasAttachment) params.set('has_attachment', '1');
    if (state.unread) params.set('unread', '1');
    params.set('limit', String(state.pageSize));
    params.set('offset', String((state.page - 1) * state.pageSize));
    return params;
}

async function reload() {
    setBusy(refs.refresh, true, '加载中');
    try {
        const result = await apiFetch(`/api/manage/gongwen/documents?${buildParams().toString()}`);
        state.documents = result.documents || [];
        state.total = result.total || 0;
        // Clamp page if filters shrank the result set.
        const pages = totalPages();
        if (state.page > pages) {
            state.page = pages;
            const retry = await apiFetch(`/api/manage/gongwen/documents?${buildParams().toString()}`);
            state.documents = retry.documents || [];
            state.total = retry.total || 0;
        }
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

function goPage(delta) {
    const pages = totalPages();
    const next = Math.min(Math.max(state.page + delta, 1), pages);
    if (next === state.page) return;
    state.page = next;
    reload();
}

function applyFilters() {
    state.page = 1;
    reload();
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
        state.page = 1;
        await reload();
    } catch (error) {
        showMessage(error.message || '公文同步失败。', 'error');
    } finally {
        setBusy(refs.sync, false);
    }
}

// ---------------- reader drawer ----------------

function metaRow(label, value) {
    if (!value) return '';
    return `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`;
}

function renderPart(part) {
    const headBadge = part.truncated ? '<span class="gwlist-cat">已截断</span>' : '';
    const head = `<div class="gw-reader-part-head"><strong>${escapeHtml(part.label || '内容')}${part.name ? '：' + escapeHtml(part.name) : ''}</strong>
        <span>${headBadge}<a class="btn btn-outline btn-sm" href="${escapeHtml(part.download_url || '#')}" download>下载</a></span></div>`;
    const warn = (part.warnings || []).length ? `<div class="gw-reader-warn">${escapeHtml(part.warnings.join('；'))}</div>` : '';
    let inner = '';
    if (part.kind === 'pdf') {
        inner = `<iframe class="gw-reader-iframe" src="${escapeHtml(part.view_url)}" title="${escapeHtml(part.name || 'PDF')}"></iframe>`;
        if (part.text && part.text.trim()) {
            inner += `<details class="gw-reader-extract"><summary>查看解析文本</summary><pre class="gw-reader-text">${escapeHtml(part.text)}</pre></details>`;
        }
    } else if (part.kind === 'image') {
        inner = `<img class="gw-reader-img" src="${escapeHtml(part.view_url)}" alt="${escapeHtml(part.name || '图片')}">`;
    } else if (part.kind === 'text') {
        inner = `<pre class="gw-reader-text">${escapeHtml(part.text || '')}</pre>`;
    } else {
        inner = `<div class="gw-reader-unsupported">该文件暂不支持在线解析，请点击右上角「下载」查看。</div>`;
    }
    return `<section class="gw-reader-part">${head}${warn}${inner}</section>`;
}

function renderReader(doc) {
    refs.readerSn.textContent = doc.sn || '';
    refs.readerTitle.textContent = doc.title || '公文详情';
    refs.readerMeta.innerHTML = [
        metaRow('文号', doc.sn),
        metaRow('发文单位', doc.author),
        metaRow('发送人', doc.sender_name),
        metaRow('分类', doc.category_name),
        metaRow('时间', formatDateTime(doc.publish_time)),
        metaRow('归属', doc.attribution_label),
        metaRow('开放', doc.openness_label),
    ].join('');

    const blocks = [];
    const contentHtml = (doc.content_html || '').trim();
    if (contentHtml) {
        blocks.push(`<section class="gw-reader-part"><div class="gw-reader-part-head"><strong>公文正文</strong></div><div class="gw-reader-html">${contentHtml}</div></section>`);
    } else if ((doc.summary || '').trim()) {
        blocks.push(`<section class="gw-reader-part"><div class="gw-reader-part-head"><strong>摘要</strong></div><div class="gw-reader-html">${escapeHtml(doc.summary)}</div></section>`);
    }
    (doc.parts || []).forEach((part) => blocks.push(renderPart(part)));
    if (!blocks.length) {
        blocks.push('<div class="gw-reader-loading">该公文暂无可显示的正文或附件。</div>');
    }
    refs.readerBody.innerHTML = blocks.join('');
}

async function openReader(id, refresh = false) {
    state.readerId = id;
    refs.reader.hidden = false;
    refs.readerSn.textContent = '';
    refs.readerTitle.textContent = '公文详情';
    refs.readerMeta.innerHTML = '';
    refs.readerBody.innerHTML = '<div class="gw-reader-loading">正在解析公文内容…</div>';
    try {
        const result = await apiFetch(`/api/manage/gongwen/documents/${id}/reader${refresh ? '?refresh=1' : ''}`);
        renderReader(result.document);
    } catch (error) {
        refs.readerBody.innerHTML = `<div class="gw-reader-unsupported">${escapeHtml(error.message || '读取公文失败。')}</div>`;
    }
}

function closeReader() {
    refs.reader.hidden = true;
    state.readerId = null;
}

// ---------------- 归属 / 开放范围 editor ----------------

async function ensureScopeOptions() {
    if (state.scopeOptions) return state.scopeOptions;
    state.scopeOptions = await apiFetch('/api/manage/gongwen/scope-options');
    return state.scopeOptions;
}

function fillOpennessOptions(level, selected) {
    const options = (state.scopeOptions?.openness_by_level?.[level]) || [];
    refs.scopeOpenness.innerHTML = options
        .map((opt) => `<option value="${escapeHtml(opt.value)}"${opt.value === selected ? ' selected' : ''}>${escapeHtml(opt.label)}</option>`)
        .join('');
    if (!options.some((opt) => opt.value === selected) && options.length) {
        refs.scopeOpenness.value = options[0].value;
    }
}

function applyLevelVisibility(level) {
    refs.scopeCollegeGroup.hidden = level === 'school';
    refs.scopeDepartmentGroup.hidden = level !== 'department';
}

async function openScopeEditor(doc) {
    try {
        await ensureScopeOptions();
    } catch (error) {
        showMessage(error.message || '读取归属选项失败。', 'error');
        return;
    }
    state.editingId = doc.id;
    const teacherOrg = state.scopeOptions?.teacher_org || {};
    refs.scopeSubtitle.textContent = doc.title || '';
    const level = doc.attr_level || 'school';
    refs.scopeLevel.value = level;
    refs.scopeCollege.value = doc.attr_college || teacherOrg.college || '';
    refs.scopeDepartment.value = doc.attr_department || teacherOrg.department || '';
    applyLevelVisibility(level);
    fillOpennessOptions(level, doc.openness || 'school');
    refs.scopeModal.hidden = false;
}

function closeScopeEditor() {
    refs.scopeModal.hidden = true;
    state.editingId = null;
}

async function saveScope() {
    if (!state.editingId) return;
    const level = refs.scopeLevel.value;
    const college = refs.scopeCollege.value.trim();
    const department = refs.scopeDepartment.value.trim();
    if ((level === 'college' || level === 'department') && !college) {
        showMessage('请填写归属学院。', 'warning');
        return;
    }
    if (level === 'department' && !department) {
        showMessage('请填写归属系部。', 'warning');
        return;
    }
    const payload = {
        college: level === 'school' ? '' : college,
        department: level === 'department' ? department : '',
        openness: refs.scopeOpenness.value,
    };
    setBusy(refs.scopeSave, true, '保存中');
    try {
        const result = await apiFetch(`/api/manage/gongwen/documents/${state.editingId}/scope`, { method: 'POST', body: payload });
        const updated = result.document;
        const idx = state.documents.findIndex((d) => String(d.id) === String(state.editingId));
        if (idx >= 0 && updated) {
            state.documents[idx] = { ...state.documents[idx], ...updated };
        }
        render();
        closeScopeEditor();
        showMessage(result.message || '已更新归属与开放范围。', 'success');
    } catch (error) {
        showMessage(error.message || '更新失败。', 'error');
    } finally {
        setBusy(refs.scopeSave, false);
    }
}

// ---------------- wiring ----------------

let searchTimer = null;
refs.search?.addEventListener('input', () => {
    state.keyword = refs.search.value.trim();
    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(applyFilters, 300);
});
refs.category?.addEventListener('change', () => { state.category = refs.category.value; applyFilters(); });
refs.author?.addEventListener('change', () => { state.author = refs.author.value; applyFilters(); });
refs.sender?.addEventListener('change', () => { state.sender = refs.sender.value; applyFilters(); });
refs.attach?.addEventListener('click', () => {
    state.hasAttachment = !state.hasAttachment;
    refs.attach.classList.toggle('is-active', state.hasAttachment);
    applyFilters();
});
refs.unread?.addEventListener('click', () => {
    state.unread = !state.unread;
    refs.unread.classList.toggle('is-active', state.unread);
    applyFilters();
});
refs.refresh?.addEventListener('click', reload);
refs.sync?.addEventListener('click', syncNow);
refs.pagesize?.addEventListener('change', () => {
    state.pageSize = parseInt(refs.pagesize.value, 10) || 20;
    state.page = 1;
    reload();
});
refs.prev?.addEventListener('click', () => goPage(-1));
refs.next?.addEventListener('click', () => goPage(1));

refs.tbody?.addEventListener('click', (event) => {
    const scopeBtn = event.target.closest('[data-scope-edit]');
    if (scopeBtn) {
        const doc = state.documents.find((d) => String(d.id) === String(scopeBtn.dataset.scopeEdit));
        if (doc) openScopeEditor(doc);
        return;
    }
    const titleEl = event.target.closest('[data-open-reader]');
    if (titleEl) openReader(titleEl.dataset.openReader);
});

refs.readerClose?.addEventListener('click', closeReader);
refs.readerRefresh?.addEventListener('click', () => { if (state.readerId) openReader(state.readerId, true); });
refs.reader?.addEventListener('click', (event) => { if (event.target === refs.reader) closeReader(); });
document.addEventListener('keydown', (event) => { if (event.key === 'Escape' && refs.reader && !refs.reader.hidden) closeReader(); });

refs.scopeLevel?.addEventListener('change', () => {
    const level = refs.scopeLevel.value;
    applyLevelVisibility(level);
    fillOpennessOptions(level, refs.scopeOpenness.value);
});
refs.scopeClose?.addEventListener('click', closeScopeEditor);
refs.scopeCancel?.addEventListener('click', closeScopeEditor);
refs.scopeSave?.addEventListener('click', saveScope);
refs.scopeModal?.addEventListener('click', (event) => { if (event.target === refs.scopeModal) closeScopeEditor(); });

render();
