// 材料中心（/manage/library)：左栏分类多选 + 模糊/AI 搜索。
// 左栏按钮由 layout.html 渲染（#libraryCategoryRail），本模块接管其勾选行为；
// 搜索期间锁定按钮与分类操作，避免重复提交（尤其是 AI 搜索）。
import { apiFetch } from '/static/js/api.js';
import { showMessage } from '/static/js/ui.js';

const rail = document.getElementById('libraryCategoryRail');
const queryInput = document.getElementById('mh-query');
const scopeSelect = document.getElementById('mh-scope');
const searchBtn = document.getElementById('mh-search-btn');
const aiBtn = document.getElementById('mh-ai-btn');
const statusBox = document.getElementById('mh-status');
const statusText = document.getElementById('mh-status-text');
const aiNote = document.getElementById('mh-ai-note');
const resultsBox = document.getElementById('mh-results');
const totalLabel = document.getElementById('mh-total');

const state = {
    busy: false,
    debounceTimer: 0,
};

function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, (ch) => (
        { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]
    ));
}

function escapeRegex(value) {
    return String(value || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function highlight(text, terms) {
    const source = String(text || '');
    const usable = (terms || []).filter(Boolean);
    if (!usable.length) return escapeHtml(source);
    const matcher = new RegExp(`(${usable.map(escapeRegex).join('|')})`, 'ig');
    return source
        .split(matcher)
        .map((segment, index) => (index % 2 === 1 ? `<mark>${escapeHtml(segment)}</mark>` : escapeHtml(segment)))
        .join('');
}

function categoryButtons() {
    return Array.from(rail?.querySelectorAll('[data-cat-key]') || []);
}

function selectedCategories() {
    return categoryButtons()
        .filter((btn) => btn.classList.contains('is-checked'))
        .map((btn) => btn.dataset.catKey);
}

function applyInitialSelection() {
    const params = new URLSearchParams(window.location.search);
    const rawCats = String(params.get('cats') || '').split(',').map((item) => item.trim()).filter(Boolean);
    if (params.get('q')) queryInput.value = String(params.get('q'));
    if (params.get('scope')) scopeSelect.value = String(params.get('scope'));
    if (!rawCats.length) return;
    const wanted = new Set(rawCats);
    categoryButtons().forEach((btn) => {
        btn.classList.toggle('is-checked', wanted.has(btn.dataset.catKey));
    });
}

function syncUrl() {
    const params = new URLSearchParams();
    const query = queryInput.value.trim();
    const cats = selectedCategories();
    if (query) params.set('q', query);
    if (cats.length && cats.length !== categoryButtons().length) params.set('cats', cats.join(','));
    if (scopeSelect.value !== 'all') params.set('scope', scopeSelect.value);
    const search = params.toString();
    window.history.replaceState({}, '', `${window.location.pathname}${search ? `?${search}` : ''}`);
}

function setBusy(busy, message) {
    state.busy = busy;
    searchBtn.disabled = busy;
    aiBtn.disabled = busy;
    queryInput.disabled = busy;
    scopeSelect.disabled = busy;
    rail?.classList.toggle('is-busy', busy);
    if (busy) {
        statusText.textContent = message || '正在搜索…';
        statusBox.classList.add('is-visible');
    } else {
        statusBox.classList.remove('is-visible');
    }
}

function updateRailCounts(counts) {
    categoryButtons().forEach((btn) => {
        const badge = btn.querySelector('[data-cat-count]');
        if (!badge) return;
        const value = counts?.[btn.dataset.catKey];
        if (Number.isFinite(value) && btn.classList.contains('is-checked')) {
            badge.textContent = String(value);
            badge.hidden = false;
        } else {
            badge.hidden = true;
        }
    });
}

function renderResults(data) {
    const groups = Array.isArray(data.groups) ? data.groups : [];
    const terms = Array.isArray(data.terms) ? data.terms : [];
    updateRailCounts(data.counts || {});
    totalLabel.textContent = `共找到 ${Number(data.total || 0)} 项。`;

    if (!groups.length) {
        resultsBox.innerHTML = `
            <div class="mh-empty">
                <strong>没有找到匹配的材料</strong>
                试试更短的关键词、勾选更多分类，或用「AI 搜索」描述你的需求。
            </div>`;
        return;
    }

    resultsBox.innerHTML = groups.map((group) => `
        <section class="mh-group">
            <div class="mh-group-head">
                <strong>${escapeHtml(group.label)}</strong>
                <span class="mh-group-count">${group.items.length}</span>
            </div>
            <div class="mh-items">
                ${group.items.map((item) => `
                    <a class="mh-item" href="${escapeHtml(item.url)}">
                        <span class="mh-item-title">${highlight(item.title, terms)}</span>
                        <span class="mh-item-meta">
                            <span class="mh-chip cat">${escapeHtml(item.category_label)}</span>
                            <span class="mh-chip lvl-${escapeHtml(item.scope_key)}">${escapeHtml(item.scope_label)}</span>
                            ${(item.meta || []).map((chip) => `<span class="mh-chip">${escapeHtml(chip)}</span>`).join('')}
                            ${item.owner ? `<span class="mh-item-owner">归属：${escapeHtml(item.owner)}</span>` : ''}
                            ${item.updated_at ? `<span class="mh-item-owner">${escapeHtml(item.updated_at)}</span>` : ''}
                        </span>
                        ${item.snippet ? `<span class="mh-item-snippet">${highlight(item.snippet, terms)}</span>` : ''}
                    </a>
                `).join('')}
            </div>
        </section>
    `).join('');
}

async function runSearch() {
    if (state.busy) return;
    const cats = selectedCategories();
    aiNote.classList.remove('is-visible');
    if (!cats.length) {
        totalLabel.textContent = '';
        resultsBox.innerHTML = `
            <div class="mh-empty">
                <strong>尚未勾选任何分类</strong>
                请在左侧勾选至少一个材料分类，或点击「全选」。
            </div>`;
        updateRailCounts({});
        syncUrl();
        return;
    }
    setBusy(true, '正在搜索…');
    try {
        const params = new URLSearchParams({
            q: queryInput.value.trim(),
            categories: cats.join(','),
            scope: scopeSelect.value,
        });
        const data = await apiFetch(`/api/materials/hub/search?${params}`, { silent: true });
        renderResults(data);
        syncUrl();
    } catch (error) {
        showMessage(error?.message || '搜索失败，请稍后再试', 'error');
    } finally {
        setBusy(false);
    }
}

async function runAiSearch() {
    if (state.busy) return;
    const query = queryInput.value.trim();
    if (!query) {
        showMessage('请先输入你的检索需求，AI 才能理解', 'info');
        queryInput.focus();
        return;
    }
    const cats = selectedCategories();
    if (!cats.length) {
        showMessage('请先在左侧勾选至少一个分类', 'info');
        return;
    }
    aiNote.classList.remove('is-visible');
    setBusy(true, 'AI 正在理解你的需求并检索…');
    try {
        const data = await apiFetch('/api/materials/hub/ai-search', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ query, categories: cats, scope: scopeSelect.value }),
            silent: true,
        });
        renderResults(data);
        const ai = data.ai || {};
        const keywordChips = (ai.keywords || []).map((kw) => `<span class="mh-kw">${escapeHtml(kw)}</span>`).join('');
        aiNote.innerHTML = `
            <span>${ai.used ? 'AI 理解' : '提示'}：${escapeHtml(ai.explanation || '')}</span>
            ${keywordChips}`;
        aiNote.classList.add('is-visible');
        syncUrl();
    } catch (error) {
        showMessage(error?.message || 'AI 搜索失败，请稍后再试', 'error');
    } finally {
        setBusy(false);
    }
}

function scheduleSearch() {
    window.clearTimeout(state.debounceTimer);
    state.debounceTimer = window.setTimeout(() => { runSearch(); }, 260);
}

function bindRail() {
    if (!rail) return;
    rail.addEventListener('click', (event) => {
        if (state.busy) return;
        const action = event.target.closest('[data-cat-action]');
        if (action) {
            const checked = action.dataset.catAction === 'all';
            categoryButtons().forEach((btn) => btn.classList.toggle('is-checked', checked));
            scheduleSearch();
            return;
        }
        const item = event.target.closest('[data-cat-key]');
        if (item) {
            item.classList.toggle('is-checked');
            scheduleSearch();
        }
    });
}

function init() {
    if (!queryInput || !resultsBox) return;
    applyInitialSelection();
    bindRail();
    searchBtn.addEventListener('click', runSearch);
    aiBtn.addEventListener('click', runAiSearch);
    queryInput.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') {
            event.preventDefault();
            runSearch();
        }
    });
    scopeSelect.addEventListener('change', () => { runSearch(); });
    runSearch();
}

init();
