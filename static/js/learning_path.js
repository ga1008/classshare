import { showToast } from './ui.js';

const root = document.querySelector('[data-path-root]');

function normalizeText(value) {
    return String(value || '').replace(/\s+/g, ' ').trim().toLowerCase();
}

function getCards() {
    return Array.from(document.querySelectorAll('[data-path-card]'));
}

function currentFilters() {
    const activeTab = document.querySelector('.path-filter.is-active');
    const searchInput = document.querySelector('.path-search input');
    const courseSelect = document.querySelector('.path-course-filter select');
    return {
        status: activeTab?.dataset.pathFilter || root?.dataset.activeStatus || 'active',
        query: normalizeText(searchInput?.value || ''),
        courseId: courseSelect?.value || '0',
    };
}

function statusMatches(card, status) {
    if (!status || status === 'all') return true;
    return card.dataset.status === status;
}

function updateUrlParam(key, value) {
    const url = new URL(window.location.href);
    if (!value || value === '0' || (key === 'q' && !String(value).trim())) {
        url.searchParams.delete(key);
    } else {
        url.searchParams.set(key, value);
    }
    window.history.replaceState({}, '', url);
}

function applyFilters() {
    const filters = currentFilters();
    let visibleCount = 0;
    getCards().forEach((card) => {
        const matchesStatus = statusMatches(card, filters.status);
        const matchesCourse = filters.courseId === '0' || card.dataset.courseId === filters.courseId;
        const matchesQuery = !filters.query || normalizeText(card.dataset.searchText || '').includes(filters.query);
        const visible = matchesStatus && matchesCourse && matchesQuery;
        card.hidden = !visible;
        if (visible) visibleCount += 1;
    });
    const countEl = document.querySelector('[data-path-visible-count]');
    if (countEl) countEl.textContent = String(visibleCount);
}

async function postPathUpdate(card, extra = {}) {
    const reflection = card.querySelector('[data-path-reflection]')?.value || '';
    const nextAction = card.querySelector('[data-path-next-action]')?.value || '';
    const payload = {
        item_key: card.dataset.itemKey,
        status: extra.status || card.dataset.status || 'active',
        reflection,
        next_action: nextAction,
        pinned: Boolean(extra.pinned ?? card.classList.contains('is-pinned')),
    };
    const response = await fetch('/api/learning-path/items', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.status !== 'success') {
        throw new Error(data.detail || '学习路径保存失败');
    }
    return { data, payload };
}

function setCardStatus(card, status, label) {
    const previous = card.dataset.status || 'active';
    card.dataset.status = status;
    card.classList.remove(`path-card--${previous}`);
    card.classList.add(`path-card--${status}`);
    const labelEl = card.querySelector('[data-path-status-label]');
    if (labelEl) {
        labelEl.textContent = label || (status === 'done' ? '已完成' : status === 'snoozed' ? '稍后处理' : '进行中');
    }
}

function setCardPinned(card, pinned) {
    card.classList.toggle('is-pinned', pinned);
    const button = card.querySelector('[data-path-pin]');
    if (button) {
        button.dataset.pathPin = pinned ? '0' : '1';
        button.textContent = pinned ? '取消置顶' : '置顶';
    }
    const label = card.querySelector('[data-path-pin-label]');
    if (label) label.hidden = !pinned;
}

async function handleSaveClick(button) {
    const card = button.closest('[data-path-card]');
    if (!card || button.disabled) return;
    const nextStatus = button.dataset.nextStatus || card.dataset.status || 'active';
    button.disabled = true;
    button.classList.add('is-saving');
    try {
        const { data } = await postPathUpdate(card, { status: nextStatus });
        setCardStatus(card, nextStatus, data.status_label);
        showToast(nextStatus === 'done' ? '已记录完成' : nextStatus === 'snoozed' ? '已放到稍后' : '学习路径已保存', 'success');
        applyFilters();
    } catch (error) {
        showToast(error.message || '学习路径保存失败', 'error');
    } finally {
        button.disabled = false;
        button.classList.remove('is-saving');
    }
}

async function handlePinClick(button) {
    const card = button.closest('[data-path-card]');
    if (!card || button.disabled) return;
    const pinned = button.dataset.pathPin === '1';
    button.disabled = true;
    try {
        await postPathUpdate(card, {
            status: card.dataset.status || 'active',
            pinned,
        });
        setCardPinned(card, pinned);
        showToast(pinned ? '已置顶这一步' : '已取消置顶', 'success');
    } catch (error) {
        showToast(error.message || '置顶状态保存失败', 'error');
    } finally {
        button.disabled = false;
    }
}

function activateFilterLink(link) {
    document.querySelectorAll('.path-filter').forEach((item) => {
        const isActive = item === link;
        item.classList.toggle('is-active', isActive);
        item.setAttribute('aria-selected', isActive ? 'true' : 'false');
    });
    const status = link.dataset.pathFilter || 'active';
    updateUrlParam('status', status);
    applyFilters();
}

function bindLearningPathPage() {
    if (!root) return;
    document.querySelector('.path-search input')?.addEventListener('input', (event) => {
        updateUrlParam('q', event.currentTarget.value);
        applyFilters();
    });
    document.querySelector('.path-course-filter select')?.addEventListener('change', (event) => {
        updateUrlParam('course_id', event.currentTarget.value);
        applyFilters();
    });
    document.addEventListener('click', (event) => {
        const filterLink = event.target.closest('[data-path-filter]');
        if (filterLink) {
            event.preventDefault();
            activateFilterLink(filterLink);
            return;
        }
        const saveButton = event.target.closest('[data-path-save]');
        if (saveButton) {
            handleSaveClick(saveButton);
            return;
        }
        const pinButton = event.target.closest('[data-path-pin]');
        if (pinButton) {
            handlePinClick(pinButton);
        }
    });
    applyFilters();
}

bindLearningPathPage();
