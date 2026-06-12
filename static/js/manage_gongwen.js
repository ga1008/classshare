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
    parseStatus: '',
    hasAttachment: false,
    unread: false,
    follow: false,
    followKeywords: [],
    followAutoKeywords: [],
    page: 1,
    pageSize: 20,
    scopeOptions: null,
    editingId: null,
    readerId: null,
    allParts: [],
    attachParts: [],
    attachIndex: 0,
    fullIndex: null,
};

const refs = {
    tbody: document.getElementById('gw-doc-tbody'),
    count: document.getElementById('gw-doc-count'),
    search: document.getElementById('gw-doc-search'),
    category: document.getElementById('gw-doc-category'),
    author: document.getElementById('gw-doc-author'),
    sender: document.getElementById('gw-doc-sender'),
    parse: document.getElementById('gw-doc-parse'),
    attach: document.getElementById('gw-doc-attach'),
    unread: document.getElementById('gw-doc-unread'),
    follow: document.getElementById('gw-doc-follow'),
    // follow settings modal
    followOpen: document.getElementById('gw-follow-open'),
    followModal: document.getElementById('gw-follow-modal'),
    followClose: document.getElementById('gw-follow-close'),
    followCancel: document.getElementById('gw-follow-cancel'),
    followSave: document.getElementById('gw-follow-save'),
    followRescan: document.getElementById('gw-follow-rescan'),
    followItems: document.getElementById('gw-follow-items'),
    followAddItem: document.getElementById('gw-follow-add-item'),
    followTags: document.getElementById('gw-follow-tags'),
    followKeywordInput: document.getElementById('gw-follow-keyword-input'),
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
    // attachment viewer (secondary modal above the reader)
    attachModal: document.getElementById('gw-attach-modal'),
    attachClose: document.getElementById('gw-attach-close'),
    attachList: document.getElementById('gw-attach-list'),
    attachView: document.getElementById('gw-attach-view'),
    // fullscreen part viewer + parsed-text editor
    fullModal: document.getElementById('gw-full-modal'),
    fullClose: document.getElementById('gw-full-close'),
    fullTitle: document.getElementById('gw-full-title'),
    fullGrid: document.getElementById('gw-full-grid'),
    fullContent: document.getElementById('gw-full-content'),
    fullEditor: document.getElementById('gw-full-editor-text'),
    fullSave: document.getElementById('gw-full-save'),
    fullDownload: document.getElementById('gw-full-download'),
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

const PARSE_LABELS = { done: '已解析', pending: '未解析', idle: '未解析', parsing: '解析中', failed: '解析失败' };
const ACTIVE_PARSE_STATES = ['pending', 'idle', 'parsing'];

function parseBadge(status, justParsed) {
    const st = status || 'pending';
    // 已解析常态下不占视觉；刚转为完成的本次会话内短暂亮绿标。
    if (st === 'done' && !justParsed) return '';
    const label = st === 'done' ? '解析完成' : (PARSE_LABELS[st] || '未解析');
    return `<span class="gwlist-parse st-${escapeHtml(st)}"><span class="gw-dot"></span>${escapeHtml(label)}</span>`;
}

function followBadge(hit) {
    if (!hit) return '';
    const matched = [...(hit.matched_keywords || []), ...(hit.matched_items || [])];
    const detail = [matched.length ? `命中：${matched.join('、')}` : '', hit.ai_reason || ''].filter(Boolean).join('；');
    return `<span class="gwlist-follow" title="${escapeHtml(detail || '命中了你的关注设置')}">★ 关注命中</span>`;
}

function rowHtml(doc) {
    const unreadDot = doc.is_read ? '' : '<span class="gwlist-unread-dot" title="未读"></span>';
    const sn = doc.sn ? `<span class="gwlist-sn">${escapeHtml(doc.sn)}</span>` : '';
    const cat = doc.category_name ? `<span class="gwlist-cat">${escapeHtml(doc.category_name)}</span>` : '';
    const openLevel = doc.openness || 'school';
    return `
        <tr>
            <td><span class="gwlist-title" data-open-reader="${doc.id}" title="点击查看原文">${unreadDot}${sn}${escapeHtml(doc.title || '(无标题)')}</span>${parseBadge(doc.parse_status, doc._justParsed)}${followBadge(doc.follow_hit)}</td>
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
    if (state.parseStatus) params.set('parse_status', state.parseStatus);
    if (state.hasAttachment) params.set('has_attachment', '1');
    if (state.unread) params.set('unread', '1');
    if (state.follow) params.set('follow', '1');
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
        updateSummaryLine(result.summary);
        render();
        scheduleParsePoll();
    } catch (error) {
        showMessage(error.message || '读取公文失败。', 'error');
    } finally {
        setBusy(refs.refresh, false);
    }
}

function updateSummaryLine(summary) {
    if (!refs.lastSync || !summary) return;
    const pending = Number(summary.pending_parses || 0);
    const base = summary.last_synced_at
        ? `上次同步：${formatDateTime(summary.last_synced_at)}`
        : '尚未同步';
    refs.lastSync.textContent = pending > 0 ? `${base} · 待解析 ${pending}` : base;
}

// --------- live parse-status refresh（后台解析时列表标签实时变化） ---------

const PARSE_POLL_MS = 10000;
let parsePollTimer = null;

function hasActiveParses() {
    return state.documents.some((doc) => ACTIVE_PARSE_STATES.includes(String(doc.parse_status || '')));
}

function scheduleParsePoll() {
    if (parsePollTimer) {
        window.clearTimeout(parsePollTimer);
        parsePollTimer = null;
    }
    if (!hasActiveParses()) return;
    parsePollTimer = window.setTimeout(pollParseStatuses, PARSE_POLL_MS);
}

async function pollParseStatuses() {
    parsePollTimer = null;
    if (document.hidden) {
        // 标签页不在前台时不打接口，回到前台后下个周期继续。
        parsePollTimer = window.setTimeout(pollParseStatuses, PARSE_POLL_MS);
        return;
    }
    try {
        const result = await apiFetch(`/api/manage/gongwen/documents?${buildParams().toString()}`);
        const previous = new Map(state.documents.map((doc) => [doc.id, String(doc.parse_status || '')]));
        const fresh = (result.documents || []).map((doc) => {
            const before = previous.get(doc.id);
            const now = String(doc.parse_status || '');
            if (before && before !== 'done' && now === 'done') return { ...doc, _justParsed: true };
            return doc;
        });
        const doneDocs = fresh.filter((doc) => doc._justParsed);
        const failedCount = fresh.filter((doc) => {
            const before = previous.get(doc.id);
            return before && before !== 'failed' && String(doc.parse_status || '') === 'failed';
        }).length;
        state.documents = fresh;
        state.total = result.total || state.total;
        updateSummaryLine(result.summary);
        render();
        if (doneDocs.length === 1) {
            showMessage(`《${doneDocs[0].title || doneDocs[0].sn || '公文'}》解析完成。`, 'success');
        } else if (doneDocs.length > 1) {
            showMessage(`${doneDocs.length} 篇公文解析完成。`, 'success');
        }
        if (failedCount > 0) {
            showMessage(`${failedCount} 篇公文解析失败，打开阅览页可重试。`, 'warning');
        }
    } catch (error) {
        // 轮询失败不打扰用户，下个周期重试。
    }
    scheduleParsePoll();
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
            window.setTimeout(() => { window.location.href = '/manage/academic/gongwen-sync'; }, 1200);
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

function partNoteHtml(part) {
    const warn = (part.warnings || []).length ? `<div class="gw-reader-warn">${escapeHtml(part.warnings.join('；'))}</div>` : '';
    const note = part.note ? `<div class="gw-reader-note">${escapeHtml(part.note)}</div>` : '';
    return note + warn;
}

function partBodyHtml(part) {
    if (part.kind === 'pdf') {
        let inner = `<iframe class="gw-reader-iframe" src="${escapeHtml(part.view_url)}" title="${escapeHtml(part.name || 'PDF')}"></iframe>`;
        if (part.text && part.text.trim()) {
            inner += `<details class="gw-reader-extract" open><summary>解析文本（用于检索 / 智能提醒）</summary><pre class="gw-reader-text">${escapeHtml(part.text)}</pre></details>`;
        }
        return inner;
    }
    if (part.kind === 'table') {
        const inner = (part.tables || []).map(renderSheet).join('');
        return inner || `<pre class="gw-reader-text">${escapeHtml(part.text || '')}</pre>`;
    }
    if (part.kind === 'image') {
        return `<img class="gw-reader-img" src="${escapeHtml(part.view_url)}" alt="${escapeHtml(part.name || '图片')}">`;
    }
    if (part.kind === 'text') {
        return `<pre class="gw-reader-text">${escapeHtml(part.text || '')}</pre>`;
    }
    if (part.kind === 'archive') {
        const listing = (part.text || '').trim()
            ? `<pre class="gw-reader-text">${escapeHtml(part.text)}</pre>`
            : '<div class="gw-reader-unsupported">压缩包内容尚未解压，重新解析后可自动展开。</div>';
        return listing;
    }
    return '<div class="gw-reader-unsupported">该文件暂不支持在线解析，可点击「下载」查看。</div>';
}

function fullscreenButtonHtml(part) {
    if (typeof part._idx !== 'number') return '';
    return `<button type="button" class="btn btn-ghost btn-sm" data-fullscreen="${part._idx}" title="全屏阅览 / 编辑解析文本">全屏</button>`;
}

function renderPart(part) {
    const headBadge = part.truncated ? '<span class="gwlist-cat">已截断</span>' : '';
    const download = part.download_url ? `<a class="btn btn-outline btn-sm" href="${escapeHtml(part.download_url)}" download>下载</a>` : '';
    const head = `<div class="gw-reader-part-head"><strong>${escapeHtml(part.label || '内容')}${part.name ? '：' + escapeHtml(part.name) : ''}</strong>
        <span>${headBadge}${fullscreenButtonHtml(part)}${download}</span></div>`;
    return `<section class="gw-reader-part">${head}${partNoteHtml(part)}${partBodyHtml(part)}</section>`;
}

function renderSheet(sheet) {
    const rows = sheet.rows || [];
    if (!rows.length) return '';
    const body = rows.map((row, idx) => {
        const tag = idx === 0 ? 'th' : 'td';
        const cells = row.map((cell) => `<${tag}>${escapeHtml(cell)}</${tag}>`).join('');
        return `<tr>${cells}</tr>`;
    }).join('');
    const name = sheet.sheet ? `<div class="gw-reader-sheet-name">${escapeHtml(sheet.sheet)}</div>` : '';
    return `${name}<div class="gw-reader-table-wrap"><table class="gw-reader-table">${body}</table></div>`;
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

    // Structured fields extracted by the parse pipeline (正文标题/摘要/关键词/落款).
    const structRows = [
        doc.parsed_title ? `<div class="gw-struct-row"><span>正文标题</span><strong>${escapeHtml(doc.parsed_title)}</strong></div>` : '',
        doc.parsed_summary ? `<div class="gw-struct-row"><span>内容摘要</span><strong>${escapeHtml(doc.parsed_summary)}</strong></div>` : '',
        doc.parsed_keywords ? `<div class="gw-struct-row"><span>关键词</span><strong>${escapeHtml(doc.parsed_keywords)}</strong></div>` : '',
        doc.parsed_signature ? `<div class="gw-struct-row"><span>落款</span><strong>${escapeHtml(doc.parsed_signature)}</strong></div>` : '',
    ].filter(Boolean).join('');
    const existingStruct = refs.readerMeta.parentElement.querySelector('.gw-reader-struct');
    if (existingStruct) existingStruct.remove();
    if (structRows) {
        refs.readerMeta.insertAdjacentHTML('afterend', `<div class="gw-reader-struct">${structRows}</div>`);
    }

    // _idx = 该 part 在解析存档 parts 数组中的下标（全屏编辑保存时定位用）。
    const parts = (doc.parts || []).map((p, i) => ({ ...p, _idx: i }));
    state.allParts = parts;
    // 正文 = content_html + 正文文件；其余（附件 / 压缩包及其解压内容）走二级附件浮窗。
    const bodyParts = parts.filter((p) => p.which === 'primary' && p.kind !== 'archive');
    state.attachParts = parts.filter((p) => p.which !== 'primary' || p.kind === 'archive');

    const blocks = [];
    if (ACTIVE_PARSE_STATES.includes(String(doc.parse_status || ''))) {
        blocks.push('<div class="gw-reader-pending">该公文正在后台解析，稍后可点击右上角「重新解析」查看完整内容。</div>');
    }
    const contentHtml = (doc.content_html || '').trim();
    if (contentHtml) {
        blocks.push(`<section class="gw-reader-part"><div class="gw-reader-part-head"><strong>公文正文</strong></div><div class="gw-reader-html">${contentHtml}</div></section>`);
    }
    bodyParts.forEach((part) => blocks.push(renderPart(part)));
    if (state.attachParts.length) {
        const items = state.attachParts.map((part, index) => `
            <li><button type="button" data-attach-open="${index}">
                <span class="gw-attach-ext">${escapeHtml(part.ext || '文件')}</span>
                <span>${escapeHtml(part.name || '附件')}</span>
                ${part.archive ? `<span class="gw-attach-from">来自 ${escapeHtml(part.archive)}</span>` : ''}
            </button></li>`).join('');
        blocks.push(`<section class="gw-reader-part">
            <div class="gw-reader-part-head"><strong>附件（${state.attachParts.length}）</strong>
                <span><button type="button" class="btn btn-primary btn-sm" data-attach-open="0">查看附件</button></span></div>
            <ul class="gw-attach-names">${items}</ul>
        </section>`);
    }
    if (!blocks.length) {
        blocks.push('<div class="gw-reader-loading">该公文暂无可显示的正文或附件。</div>');
    }
    refs.readerBody.innerHTML = blocks.join('');
}

// ---------------- attachment viewer (secondary modal) ----------------

function selectAttachment(index) {
    const part = state.attachParts[index];
    if (!part) return;
    state.attachIndex = index;
    refs.attachList.querySelectorAll('.gw-attach-item').forEach((el, i) => {
        el.classList.toggle('is-active', i === index);
    });
    const download = part.download_url ? `<a class="btn btn-outline btn-sm" href="${escapeHtml(part.download_url)}" download>下载</a>` : '';
    refs.attachView.innerHTML = `
        <div class="gw-attach-view-head"><strong>${escapeHtml(part.name || '附件')}</strong><span>${fullscreenButtonHtml(part)}${download}</span></div>
        ${partNoteHtml(part)}${partBodyHtml(part)}`;
    refs.attachView.scrollTop = 0;
}

function openAttachModal(index = 0) {
    if (!state.attachParts.length || !refs.attachModal) return;
    refs.attachList.innerHTML = state.attachParts.map((part, i) => `
        <button type="button" class="gw-attach-item" data-attach-index="${i}">
            <span class="gw-attach-ext">${escapeHtml(part.ext || '文件')}</span>
            <span>${escapeHtml(part.name || '附件')}</span>
        </button>`).join('');
    refs.attachModal.hidden = false;
    selectAttachment(Math.min(Math.max(index, 0), state.attachParts.length - 1));
}

function closeAttachModal() {
    if (refs.attachModal) refs.attachModal.hidden = true;
}

// ---------------- fullscreen part viewer + parsed-text editor ----------------

function openFullscreen(index) {
    const part = state.allParts.find((p) => p._idx === index);
    if (!part || !refs.fullModal) return;
    state.fullIndex = index;
    refs.fullTitle.textContent = `${part.label || '内容'}：${part.name || ''}`;
    refs.fullContent.innerHTML = partNoteHtml(part) + partBodyHtml(part);
    refs.fullContent.scrollTop = 0;
    refs.fullEditor.value = part.text || '';
    // 图片没有可编辑的解析文本 → 只展示内容（全宽）。
    const editable = part.kind !== 'image';
    refs.fullGrid.classList.toggle('no-editor', !editable);
    refs.fullSave.hidden = !editable;
    if (part.download_url) {
        refs.fullDownload.href = part.download_url;
        refs.fullDownload.hidden = false;
    } else {
        refs.fullDownload.hidden = true;
    }
    refs.fullModal.hidden = false;
}

function closeFullscreen() {
    if (refs.fullModal) refs.fullModal.hidden = true;
    state.fullIndex = null;
}

async function saveFullscreenText() {
    const index = state.fullIndex;
    const part = state.allParts.find((p) => p._idx === index);
    if (part == null || !state.readerId) return;
    setBusy(refs.fullSave, true, '保存中');
    try {
        const result = await apiFetch(`/api/manage/gongwen/documents/${state.readerId}/parts/${index}/text`, {
            method: 'POST',
            body: { text: refs.fullEditor.value },
        });
        part.text = refs.fullEditor.value;
        part.edited = true;
        // 同步刷新底层视图（reader / 附件浮窗）里这个 part 的渲染。
        if (!refs.attachModal.hidden && state.attachParts[state.attachIndex]?._idx === index) {
            selectAttachment(state.attachIndex);
        }
        showMessage(result.message || '解析文本已保存。', 'success');
    } catch (error) {
        showMessage(error.message || '保存失败。', 'error');
    } finally {
        setBusy(refs.fullSave, false);
    }
}

async function openReader(id, refresh = false) {
    state.readerId = id;
    state.allParts = [];
    state.attachParts = [];
    closeFullscreen();
    closeAttachModal();
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
    closeFullscreen();
    closeAttachModal();
    refs.reader.hidden = true;
    state.readerId = null;
    state.allParts = [];
    state.attachParts = [];
}

// ---------------- 关注设置（关注项目 + 关注关键字） ----------------

function followItemRowHtml(value = '') {
    return `
        <div class="gw-follow-item-row">
            <input type="text" value="${escapeHtml(value)}" maxlength="60" placeholder="如：师范专业认证 / 教学比赛 / 实验室安全">
            <button type="button" class="gw-follow-item-remove" data-follow-remove-item title="删除该关注项">&times;</button>
        </div>`;
}

function addFollowItemRow(value = '') {
    if (!refs.followItems) return;
    refs.followItems.insertAdjacentHTML('beforeend', followItemRowHtml(value));
    if (!value) {
        const inputs = refs.followItems.querySelectorAll('input');
        inputs[inputs.length - 1]?.focus();
    }
}

function renderFollowKeywordTags() {
    if (!refs.followTags) return;
    refs.followTags.querySelectorAll('.gw-follow-tag').forEach((el) => el.remove());
    // 系统自动关注的关键字（教师姓名）：置顶展示、不可删除。
    (state.followAutoKeywords || []).forEach((keyword) => {
        refs.followKeywordInput.insertAdjacentHTML('beforebegin', `
            <span class="gw-follow-tag gw-follow-tag--auto" title="系统自动关注你的姓名，无需配置、不可删除">${escapeHtml(keyword)} <small>自动</small></span>`);
    });
    state.followKeywords.forEach((keyword, index) => {
        refs.followKeywordInput.insertAdjacentHTML('beforebegin', `
            <span class="gw-follow-tag">${escapeHtml(keyword)}
                <button type="button" data-follow-remove-keyword="${index}" title="删除关键字">&times;</button>
            </span>`);
    });
}

function addFollowKeyword(raw) {
    const keyword = String(raw || '').trim();
    if (!keyword) return;
    if (state.followKeywords.some((k) => k.toLowerCase() === keyword.toLowerCase())) return;
    if (state.followKeywords.length >= 30) {
        showMessage('关注关键字最多 30 个。', 'warning');
        return;
    }
    state.followKeywords = [...state.followKeywords, keyword];
    renderFollowKeywordTags();
}

async function openFollowModal() {
    if (!refs.followModal) return;
    setBusy(refs.followOpen, true, '加载中');
    try {
        const result = await apiFetch('/api/manage/gongwen/follow-settings');
        const settings = result.settings || {};
        refs.followItems.innerHTML = '';
        (settings.items || []).forEach((item) => addFollowItemRow(item));
        if (!(settings.items || []).length) addFollowItemRow('');
        state.followKeywords = [...(settings.keywords || [])];
        state.followAutoKeywords = [...(settings.auto_keywords || [])];
        renderFollowKeywordTags();
        refs.followKeywordInput.value = '';
        refs.followModal.hidden = false;
    } catch (error) {
        showMessage(error.message || '读取关注设置失败。', 'error');
    } finally {
        setBusy(refs.followOpen, false);
    }
}

function closeFollowModal() {
    if (refs.followModal) refs.followModal.hidden = true;
}

async function saveFollowSettings() {
    // 输入框里尚未回车的关键字也一并保存，避免用户误以为已添加。
    addFollowKeyword(refs.followKeywordInput?.value);
    if (refs.followKeywordInput) refs.followKeywordInput.value = '';
    const items = Array.from(refs.followItems?.querySelectorAll('input') || [])
        .map((input) => input.value.trim())
        .filter(Boolean);
    setBusy(refs.followSave, true, '保存中');
    try {
        const result = await apiFetch('/api/manage/gongwen/follow-settings', {
            method: 'POST',
            body: { items, keywords: state.followKeywords },
        });
        closeFollowModal();
        showMessage(result.message || '关注设置已保存。', 'success');
    } catch (error) {
        showMessage(error.message || '保存关注设置失败。', 'error');
    } finally {
        setBusy(refs.followSave, false);
    }
}

async function rescanFollowMatches() {
    setBusy(refs.followRescan, true, '提交中');
    try {
        const result = await apiFetch('/api/manage/gongwen/follow-rescan', { method: 'POST' });
        showMessage(result.message || '已开始重新发现，完成后将通过站内信提醒。', 'success');
    } catch (error) {
        showMessage(error.message || '重新发现启动失败。', 'error');
    } finally {
        setBusy(refs.followRescan, false);
    }
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
refs.parse?.addEventListener('change', () => { state.parseStatus = refs.parse.value; applyFilters(); });
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
refs.follow?.addEventListener('click', () => {
    state.follow = !state.follow;
    refs.follow.classList.toggle('is-active', state.follow);
    applyFilters();
});
refs.followOpen?.addEventListener('click', openFollowModal);
refs.followClose?.addEventListener('click', closeFollowModal);
refs.followCancel?.addEventListener('click', closeFollowModal);
refs.followSave?.addEventListener('click', saveFollowSettings);
refs.followRescan?.addEventListener('click', rescanFollowMatches);
refs.followModal?.addEventListener('click', (event) => { if (event.target === refs.followModal) closeFollowModal(); });
refs.followAddItem?.addEventListener('click', () => addFollowItemRow(''));
refs.followItems?.addEventListener('click', (event) => {
    const removeBtn = event.target.closest('[data-follow-remove-item]');
    if (removeBtn) removeBtn.closest('.gw-follow-item-row')?.remove();
});
refs.followKeywordInput?.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter' && event.key !== ',') return;
    event.preventDefault();
    addFollowKeyword(refs.followKeywordInput.value);
    refs.followKeywordInput.value = '';
});
refs.followTags?.addEventListener('click', (event) => {
    const removeBtn = event.target.closest('[data-follow-remove-keyword]');
    if (!removeBtn) return;
    const index = parseInt(removeBtn.dataset.followRemoveKeyword, 10);
    state.followKeywords = state.followKeywords.filter((_, i) => i !== index);
    renderFollowKeywordTags();
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
refs.readerBody?.addEventListener('click', (event) => {
    const full = event.target.closest('[data-fullscreen]');
    if (full) {
        openFullscreen(parseInt(full.dataset.fullscreen, 10));
        return;
    }
    const opener = event.target.closest('[data-attach-open]');
    if (opener) openAttachModal(parseInt(opener.dataset.attachOpen, 10) || 0);
});
refs.attachList?.addEventListener('click', (event) => {
    const item = event.target.closest('[data-attach-index]');
    if (item) selectAttachment(parseInt(item.dataset.attachIndex, 10) || 0);
});
refs.attachView?.addEventListener('click', (event) => {
    const full = event.target.closest('[data-fullscreen]');
    if (full) openFullscreen(parseInt(full.dataset.fullscreen, 10));
});
refs.attachClose?.addEventListener('click', closeAttachModal);
refs.attachModal?.addEventListener('click', (event) => { if (event.target === refs.attachModal) closeAttachModal(); });
refs.fullClose?.addEventListener('click', closeFullscreen);
refs.fullSave?.addEventListener('click', saveFullscreenText);
refs.fullModal?.addEventListener('click', (event) => { if (event.target === refs.fullModal) closeFullscreen(); });
document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    if (refs.fullModal && !refs.fullModal.hidden) { closeFullscreen(); return; }
    if (refs.attachModal && !refs.attachModal.hidden) { closeAttachModal(); return; }
    if (refs.followModal && !refs.followModal.hidden) { closeFollowModal(); return; }
    if (refs.reader && !refs.reader.hidden) closeReader();
});

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

// 入口参数：?follow=1 直接进入「我的关注」视图；?doc=ID 自动打开该公文的阅览
// （配合首页「您的关注」与通知中心的跳转链接形成闭环）。
const initialParams = new URLSearchParams(window.location.search);
if (initialParams.get('follow') === '1' && refs.follow) {
    state.follow = true;
    refs.follow.classList.add('is-active');
    reload();
}
const initialDocId = initialParams.get('doc');
if (initialDocId) openReader(initialDocId);
