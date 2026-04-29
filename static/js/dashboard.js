import { formatDate, showMessage } from '/static/js/ui.js';
import { initSemesterCalendar } from '/static/js/semester_calendar.js';

const root = document.querySelector('[data-dashboard-root]');

function normalizeText(value) {
    return String(value || '')
        .toLowerCase()
        .replace(/\s+/g, ' ')
        .trim();
}

function compactText(value) {
    return normalizeText(value).replace(/\s+/g, '');
}

if (root) {
    const cards = Array.from(root.querySelectorAll('[data-offering-card]'));
    const filterButtons = Array.from(root.querySelectorAll('[data-filter-value]'));
    const searchForm = root.querySelector('[data-dashboard-search-form]');
    const filterField = root.querySelector('[data-dashboard-filter-field]');
    const searchInput = root.querySelector('[data-dashboard-search]');
    const visibleCount = root.querySelector('[data-visible-count]');
    const resultsSummary = root.querySelector('[data-results-summary]');
    const offeringList = root.querySelector('[data-offering-list]');
    const emptySearch = root.querySelector('[data-empty-search]');
    const resetButton = root.querySelector('[data-reset-search]');
    const semesterCalendarRoot = root.querySelector('[data-semester-calendar-root]');

    const filterLabels = new Map(
        filterButtons.map((button) => [
            button.dataset.filterValue || 'all',
            button.dataset.filterLabel || button.textContent.trim(),
        ]),
    );
    const allowedFilters = new Set(filterButtons.map((button) => button.dataset.filterValue || 'all'));
    const initialFilter = root.dataset.initialFilter || 'all';
    let activeFilter = allowedFilters.has(initialFilter)
        ? initialFilter
        : filterButtons.find((button) => button.classList.contains('is-active'))?.dataset.filterValue || 'all';
    let isComposing = false;
    let searchTimerId = 0;

    cards.forEach((card) => {
        const searchText = String(card.dataset.searchText || '');
        card.dataset.searchNormalized = normalizeText(searchText);
        card.dataset.searchCompact = compactText(searchText);
    });

    const formatDateNodes = () => {
        root.querySelectorAll('[data-datetime]').forEach((node) => {
            const value = node.getAttribute('data-datetime');
            if (!value) {
                return;
            }
            node.textContent = formatDate(value);
        });
    };

    const revealElements = () => {
        const targets = root.querySelectorAll('.dashboard-reveal');
        if (!('IntersectionObserver' in window)) {
            targets.forEach((element) => element.classList.add('is-visible'));
            return;
        }

        const observer = new IntersectionObserver(
            (entries) => {
                entries.forEach((entry) => {
                    if (!entry.isIntersecting) {
                        return;
                    }
                    entry.target.classList.add('is-visible');
                    observer.unobserve(entry.target);
                });
            },
            { threshold: 0.15 },
        );

        targets.forEach((element) => observer.observe(element));
    };

    const updateFilterUi = () => {
        filterButtons.forEach((button) => {
            const isActive = (button.dataset.filterValue || 'all') === activeFilter;
            button.classList.toggle('is-active', isActive);
            button.setAttribute('aria-selected', String(isActive));
        });
    };

    const syncSearchForm = () => {
        if (filterField) {
            filterField.value = activeFilter || 'all';
        }
    };

    const buildResultsSummary = (keyword) => {
        const fragments = [];
        if (activeFilter !== 'all') {
            fragments.push(`筛选：${filterLabels.get(activeFilter) || activeFilter}`);
        }
        if (keyword) {
            fragments.push(`关键词：${keyword}`);
        }
        return fragments.length ? fragments.join(' · ') : '显示全部课堂';
    };

    const syncUrlState = (keyword) => {
        const url = new URL(window.location.href);
        if (activeFilter && activeFilter !== 'all') {
            url.searchParams.set('filter', activeFilter);
        } else {
            url.searchParams.delete('filter');
        }
        if (keyword) {
            url.searchParams.set('q', keyword);
        } else {
            url.searchParams.delete('q');
        }
        const nextUrl = `${url.pathname}${url.search}${url.hash}`;
        const currentUrl = `${window.location.pathname}${window.location.search}${window.location.hash}`;
        if (nextUrl !== currentUrl) {
            window.history.replaceState({}, '', nextUrl);
        }
    };

    const matchesFilter = (card) => {
        if (activeFilter === 'attention') {
            return card.dataset.attention === 'true';
        }
        if (activeFilter === 'recent') {
            return card.dataset.recent === 'true';
        }
        if (activeFilter === 'progress') {
            return card.dataset.progress === 'true';
        }
        return true;
    };

    const applyFilters = ({ syncUrl = true } = {}) => {
        const keyword = String(searchInput?.value || '')
            .replace(/\s+/g, ' ')
            .trim();
        const normalizedKeyword = normalizeText(keyword);
        const compactKeyword = normalizedKeyword.replace(/\s+/g, '');
        let count = 0;

        cards.forEach((card) => {
            const normalizedSearch = card.dataset.searchNormalized || '';
            const compactSearch = card.dataset.searchCompact || normalizedSearch.replace(/\s+/g, '');
            const matchesKeyword = !normalizedKeyword
                || normalizedSearch.includes(normalizedKeyword)
                || (compactKeyword && compactSearch.includes(compactKeyword));
            const visible = matchesKeyword && matchesFilter(card);
            card.hidden = !visible;
            card.setAttribute('aria-hidden', visible ? 'false' : 'true');
            if (visible) {
                count += 1;
            }
        });

        if (visibleCount) {
            visibleCount.textContent = String(count);
        }
        if (resultsSummary) {
            resultsSummary.textContent = buildResultsSummary(keyword);
        }
        if (offeringList) {
            offeringList.hidden = count === 0;
        }
        if (emptySearch) {
            emptySearch.hidden = count !== 0;
        }
        if (resetButton) {
            resetButton.hidden = !(keyword || activeFilter !== 'all');
        }

        updateFilterUi();
        syncSearchForm();
        if (syncUrl) {
            syncUrlState(keyword);
        }
    };

    const scheduleApplyFilters = () => {
        window.clearTimeout(searchTimerId);
        searchTimerId = window.setTimeout(() => {
            applyFilters();
        }, 100);
    };

    filterButtons.forEach((button) => {
        button.addEventListener('click', (event) => {
            event.preventDefault();
            activeFilter = button.dataset.filterValue || 'all';
            applyFilters();
        });
    });

    searchForm?.addEventListener('submit', (event) => {
        event.preventDefault();
        applyFilters();
    });

    searchInput?.addEventListener('compositionstart', () => {
        isComposing = true;
    });

    searchInput?.addEventListener('compositionend', () => {
        isComposing = false;
        applyFilters();
    });

    searchInput?.addEventListener('input', () => {
        if (isComposing) {
            return;
        }
        scheduleApplyFilters();
    });

    searchInput?.addEventListener('search', () => {
        applyFilters();
    });

    searchInput?.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
            searchInput.value = '';
            applyFilters();
        }
    });

    resetButton?.addEventListener('click', () => {
        activeFilter = 'all';
        if (searchInput) {
            searchInput.value = '';
        }
        applyFilters();
    });

    formatDateNodes();
    revealElements();
    syncSearchForm();
    updateFilterUi();
    applyFilters({ syncUrl: false });

    initSemesterCalendar(semesterCalendarRoot, window.DASHBOARD_SEMESTER_CALENDAR || {}, {
        onMessage: (message, tone) => showMessage(message, tone || 'info'),
    });
}
