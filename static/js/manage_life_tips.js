import { apiFetch } from './api.js';
import { escapeHtml, showToast } from './ui.js';

const SCOPE_LABELS = { global: '通用', school: '本校', department: '系部' };
const STATUS_META = {
    active: { label: '投放中', tone: 'is-active' },
    draft: { label: '草稿', tone: 'is-draft' },
    retired: { label: '已下架', tone: 'is-retired' },
};
const SOURCE_LABELS = { seed: '内置种子', ai_gongwen: '公文挖掘', manual: '手工录入' };
const AUDIENCE_LABELS = { student: '学生', teacher: '教师', all: '全部' };

function statusMeta(status) {
    return STATUS_META[status] || STATUS_META.draft;
}

function scopeText(tip) {
    const base = SCOPE_LABELS[tip.scope] || tip.scope;
    if (tip.scope === 'department' && tip.department) return `${base} · ${tip.department}`;
    return base;
}

function renderTipCard(tip) {
    const meta = statusMeta(tip.status);
    const nextStatus = tip.status === 'active' ? 'retired' : 'active';
    const nextLabel = tip.status === 'active' ? '下架' : '恢复投放';
    const feedback = (tip.up_votes || tip.down_votes)
        ? `<span class="life-tip-card__votes">👍 ${tip.up_votes} · 👎 ${tip.down_votes}</span>`
        : '';
    return `
        <article class="life-tip-card" data-tip-id="${tip.id}">
            <header>
                <span class="life-tip-card__status ${meta.tone}">${meta.label}</span>
                <span class="life-tip-card__chip">${escapeHtml(tip.category || '')}</span>
                <span class="life-tip-card__chip">${escapeHtml(scopeText(tip))}</span>
                <span class="life-tip-card__chip">${escapeHtml(AUDIENCE_LABELS[tip.audience] || tip.audience)}</span>
                <span class="life-tip-card__chip is-muted">${escapeHtml(SOURCE_LABELS[tip.source_kind] || tip.source_kind)}</span>
                ${feedback}
            </header>
            <p class="life-tip-card__text">${escapeHtml(tip.tip_text)}</p>
            <footer>
                <span class="life-tip-card__source">${tip.source_ref ? escapeHtml(`来源：${tip.source_ref}`) : ''}</span>
                <span class="life-tip-card__weight">权重 ${tip.weight}</span>
                <button type="button" class="btn btn-secondary btn-sm" data-tip-status="${nextStatus}">${nextLabel}</button>
            </footer>
        </article>
    `;
}

document.addEventListener('DOMContentLoaded', () => {
    const root = document.querySelector('[data-life-tip-root]');
    if (!root) return;

    const filtersForm = root.querySelector('[data-life-tip-filters]');
    const loadingNode = root.querySelector('[data-life-tip-loading]');
    const contentNode = root.querySelector('[data-life-tip-content]');
    const pagerNode = root.querySelector('[data-life-tip-pager]');
    const summaryNode = root.querySelector('[data-life-tip-summary]');
    const modal = document.getElementById('life-tip-create-modal');
    const createForm = modal?.querySelector('[data-life-tip-create-form]');
    const departmentField = modal?.querySelector('[data-life-tip-department-field]');
    const scopeInput = modal?.querySelector('[data-life-tip-scope-input]');

    let currentPage = 1;

    let categories = [];
    try {
        categories = JSON.parse(root.dataset.categories || '[]');
    } catch (error) {
        categories = [];
    }
    const categoryFilter = root.querySelector('[data-life-tip-category-filter]');
    const categoryInput = modal?.querySelector('[data-life-tip-category-input]');
    categories.forEach((category) => {
        for (const select of [categoryFilter, categoryInput]) {
            if (!select) continue;
            const option = document.createElement('option');
            option.value = category;
            option.textContent = category;
            select.appendChild(option);
        }
    });

    function filterParams() {
        const data = new FormData(filtersForm);
        const params = new URLSearchParams();
        for (const [key, value] of data.entries()) {
            if (String(value).trim()) params.set(key, String(value).trim());
        }
        params.set('page', String(currentPage));
        return params;
    }

    async function loadTips() {
        loadingNode.hidden = false;
        contentNode.hidden = true;
        pagerNode.hidden = true;
        try {
            const result = await apiFetch(`/api/life-tips/manage/list?${filterParams()}`, { silent: true });
            const items = result.items || [];
            summaryNode.textContent = `共 ${result.total} 条`;
            contentNode.innerHTML = items.length
                ? items.map(renderTipCard).join('')
                : '<p class="life-tip-empty">没有符合筛选条件的提示语。</p>';
            const pageCount = Math.max(1, Math.ceil(result.total / result.page_size));
            if (pageCount > 1) {
                pagerNode.innerHTML = `
                    <button type="button" class="btn btn-secondary btn-sm" data-page-prev ${currentPage <= 1 ? 'disabled' : ''}>上一页</button>
                    <span>${result.page} / ${pageCount}</span>
                    <button type="button" class="btn btn-secondary btn-sm" data-page-next ${currentPage >= pageCount ? 'disabled' : ''}>下一页</button>
                `;
                pagerNode.hidden = false;
            }
            contentNode.hidden = false;
        } catch (error) {
            showToast(error.message || '提示语加载失败。', 'error');
        } finally {
            loadingNode.hidden = true;
        }
    }

    filtersForm.addEventListener('submit', (event) => {
        event.preventDefault();
        currentPage = 1;
        loadTips();
    });

    pagerNode.addEventListener('click', (event) => {
        const target = event.target;
        if (!(target instanceof HTMLElement)) return;
        if (target.hasAttribute('data-page-prev') && currentPage > 1) {
            currentPage -= 1;
            loadTips();
        } else if (target.hasAttribute('data-page-next')) {
            currentPage += 1;
            loadTips();
        }
    });

    contentNode.addEventListener('click', async (event) => {
        const button = event.target instanceof HTMLElement
            ? event.target.closest('[data-tip-status]')
            : null;
        if (!button) return;
        const card = button.closest('[data-tip-id]');
        const tipId = card?.dataset.tipId;
        if (!tipId) return;
        button.disabled = true;
        try {
            const body = new FormData();
            body.set('status', button.dataset.tipStatus || 'active');
            const result = await apiFetch(`/api/life-tips/manage/${tipId}/status`, {
                method: 'POST',
                body,
                silent: true,
            });
            showToast(result.message || '状态已更新。', 'success');
            await loadTips();
        } catch (error) {
            showToast(error.message || '状态更新失败。', 'error');
            button.disabled = false;
        }
    });

    document.querySelector('[data-life-tip-create-open]')?.addEventListener('click', () => {
        if (modal) modal.hidden = false;
    });
    modal?.querySelector('[data-life-tip-create-close]')?.addEventListener('click', () => {
        modal.hidden = true;
    });
    modal?.addEventListener('click', (event) => {
        if (event.target === modal) modal.hidden = true;
    });
    scopeInput?.addEventListener('change', () => {
        if (departmentField) departmentField.hidden = scopeInput.value !== 'department';
    });

    createForm?.addEventListener('submit', async (event) => {
        event.preventDefault();
        const submitButton = createForm.querySelector('button[type="submit"]');
        if (submitButton) submitButton.disabled = true;
        try {
            const result = await apiFetch('/api/life-tips/manage/create', {
                method: 'POST',
                body: new FormData(createForm),
                silent: true,
            });
            showToast(result.message || '提示语已入库。', 'success');
            createForm.reset();
            if (departmentField) departmentField.hidden = true;
            if (modal) modal.hidden = true;
            currentPage = 1;
            await loadTips();
        } catch (error) {
            showToast(error.message || '保存失败。', 'error');
        } finally {
            if (submitButton) submitButton.disabled = false;
        }
    });

    loadTips();
});
