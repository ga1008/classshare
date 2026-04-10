import { formatDate } from '/static/js/ui.js';

const root = document.querySelector('[data-dashboard-root]');

if (root) {
    const cards = Array.from(root.querySelectorAll('[data-offering-card]'));
    const filterButtons = Array.from(root.querySelectorAll('[data-filter-value]'));
    const searchInput = root.querySelector('[data-dashboard-search]');
    const visibleCount = root.querySelector('[data-visible-count]');
    const emptySearch = root.querySelector('[data-empty-search]');
    const resetButton = root.querySelector('[data-reset-search]');

    let activeFilter = filterButtons.find((button) => button.classList.contains('is-active'))?.dataset.filterValue || 'all';

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

    const applyFilters = () => {
        const keyword = (searchInput?.value || '').trim().toLowerCase();
        let count = 0;

        cards.forEach((card) => {
            const matchesKeyword = !keyword || (card.dataset.searchText || '').includes(keyword);
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
        if (emptySearch) {
            emptySearch.hidden = count !== 0;
        }
        if (resetButton) {
            resetButton.hidden = !(keyword || activeFilter !== 'all');
        }
    };

    filterButtons.forEach((button) => {
        button.addEventListener('click', () => {
            activeFilter = button.dataset.filterValue || 'all';
            filterButtons.forEach((item) => item.classList.toggle('is-active', item === button));
            applyFilters();
        });
    });

    searchInput?.addEventListener('input', applyFilters);

    resetButton?.addEventListener('click', () => {
        activeFilter = 'all';
        filterButtons.forEach((button) => button.classList.toggle('is-active', button.dataset.filterValue === 'all'));
        if (searchInput) {
            searchInput.value = '';
        }
        applyFilters();
    });

    formatDateNodes();
    revealElements();
    applyFilters();
}
