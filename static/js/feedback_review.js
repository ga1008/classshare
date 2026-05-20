import { showToast } from './ui.js';

const root = document.querySelector('[data-review-root]');

function normalizeText(value) {
    return String(value || '').replace(/\s+/g, ' ').trim().toLowerCase();
}

function getCards() {
    return Array.from(document.querySelectorAll('[data-review-card]'));
}

function currentFilters() {
    const activeTab = document.querySelector('.review-filter.is-active');
    const searchInput = document.querySelector('.review-search input');
    const courseSelect = document.querySelector('.review-course-filter select');
    return {
        status: activeTab?.dataset.reviewFilter || root?.dataset.activeStatus || 'active',
        query: normalizeText(searchInput?.value || ''),
        courseId: courseSelect?.value || '0',
    };
}

function statusMatches(card, status) {
    if (!status || status === 'all') return true;
    if (status === 'active') return card.dataset.status !== 'mastered';
    return card.dataset.status === status;
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
    const countEl = document.querySelector('[data-review-visible-count]');
    if (countEl) countEl.textContent = String(visibleCount);
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

async function postReviewUpdate(card, extra = {}) {
    const reflection = card.querySelector('[data-review-reflection]')?.value || '';
    const nextAction = card.querySelector('[data-review-next-action]')?.value || '';
    const payload = {
        submission_id: Number(card.dataset.submissionId),
        question_key: card.dataset.questionKey,
        status: extra.status || card.dataset.status || 'open',
        reflection,
        next_action: nextAction,
        pinned: Boolean(extra.pinned ?? card.classList.contains('is-pinned')),
    };
    const response = await fetch('/api/feedback-review/items', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.status !== 'success') {
        throw new Error(data.detail || '复盘保存失败');
    }
    return { data, payload };
}

function setCardStatus(card, status, label) {
    const previous = card.dataset.status || 'open';
    card.dataset.status = status;
    card.classList.remove(`review-card--${previous}`);
    card.classList.add(`review-card--${status}`);
    const labelEl = card.querySelector('[data-review-status-label]');
    if (labelEl) labelEl.textContent = label || (status === 'mastered' ? '已掌握' : '复盘中');
}

function setCardPinned(card, pinned) {
    card.classList.toggle('is-pinned', pinned);
    const button = card.querySelector('[data-review-pin]');
    if (button) {
        button.dataset.reviewPin = pinned ? '0' : '1';
        button.textContent = pinned ? '取消置顶' : '置顶';
    }
    const label = card.querySelector('[data-review-pin-label]');
    if (label) label.hidden = !pinned;
}

async function handleSaveClick(button) {
    const card = button.closest('[data-review-card]');
    if (!card || button.disabled) return;
    const nextStatus = button.dataset.nextStatus || card.dataset.status || 'reviewing';
    button.disabled = true;
    button.classList.add('is-saving');
    try {
        const { data } = await postReviewUpdate(card, { status: nextStatus });
        setCardStatus(card, nextStatus, data.status_label);
        showToast(nextStatus === 'mastered' ? '已标记为掌握' : '复盘已保存', 'success');
        applyFilters();
    } catch (error) {
        showToast(error.message || '复盘保存失败', 'error');
    } finally {
        button.disabled = false;
        button.classList.remove('is-saving');
    }
}

async function handlePinClick(button) {
    const card = button.closest('[data-review-card]');
    if (!card || button.disabled) return;
    const pinned = button.dataset.reviewPin === '1';
    button.disabled = true;
    try {
        await postReviewUpdate(card, {
            status: card.dataset.status || 'open',
            pinned,
        });
        setCardPinned(card, pinned);
        showToast(pinned ? '已置顶这条复盘' : '已取消置顶', 'success');
    } catch (error) {
        showToast(error.message || '置顶状态保存失败', 'error');
    } finally {
        button.disabled = false;
    }
}

function bindReviewPage() {
    if (!root) return;
    document.querySelector('.review-search input')?.addEventListener('input', (event) => {
        updateUrlParam('q', event.currentTarget.value);
        applyFilters();
    });
    document.querySelector('.review-course-filter select')?.addEventListener('change', (event) => {
        updateUrlParam('course_id', event.currentTarget.value);
        applyFilters();
    });
    document.addEventListener('click', (event) => {
        const saveButton = event.target.closest('[data-review-save]');
        if (saveButton) {
            handleSaveClick(saveButton);
            return;
        }
        const pinButton = event.target.closest('[data-review-pin]');
        if (pinButton) {
            handlePinClick(pinButton);
        }
    });
    applyFilters();
}

bindReviewPage();
