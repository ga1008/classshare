import { escapeHtml } from './ui.js';

export function normalizeFacetValue(value) {
    return String(value || '').replace(/\s+/g, ' ').trim();
}

export function normalizeSearchText(value) {
    return normalizeFacetValue(value).toLowerCase();
}

export function uniqueFacetValues(items, getter) {
    const seen = new Map();
    for (const item of items || []) {
        const value = normalizeFacetValue(getter(item));
        if (!value) continue;
        const key = value.toLowerCase();
        if (!seen.has(key)) seen.set(key, value);
    }
    return [...seen.values()].sort((a, b) => a.localeCompare(b, 'zh'));
}

export function renderFacetOptions(select, values, selectedValue, emptyLabel) {
    if (!select) return '';
    const normalizedSelected = normalizeFacetValue(selectedValue);
    const options = uniqueFacetValues(values || [], (value) => value);
    const selectedPresent = options.some((value) => value === normalizedSelected);
    const extraSelected = normalizedSelected && !selectedPresent ? [normalizedSelected] : [];
    select.innerHTML = [
        `<option value="">${escapeHtml(emptyLabel)}</option>`,
        ...extraSelected.map((value) => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`),
        ...options.map((value) => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`),
    ].join('');
    select.value = normalizedSelected;
    return select.value;
}

export function collectTagCounts(items) {
    const counts = new Map();
    for (const item of items || []) {
        for (const rawTag of item.tags || []) {
            const tag = normalizeFacetValue(rawTag);
            if (!tag) continue;
            counts.set(tag, (counts.get(tag) || 0) + 1);
        }
    }
    return [...counts.entries()]
        .sort((a, b) => (b[1] - a[1]) || a[0].localeCompare(b[0], 'zh'))
        .map(([tag, count]) => ({ tag, count }));
}

export function renderTagButtons({ container, tags, selectedTags, dataAttr }) {
    if (!container) return;
    if (!tags.length) {
        container.hidden = true;
        container.innerHTML = '';
        return;
    }
    container.hidden = false;
    container.innerHTML = [
        '<span class="manage-lp__quick-tags-label">标签</span>',
        ...tags.map(({ tag, count }) => {
            const active = selectedTags.has(tag) ? ' is-active' : '';
            return `<button type="button" class="manage-lp__tag-filter${active}" ${dataAttr}="${escapeHtml(tag)}">` +
                `<span>${escapeHtml(tag)}</span><small>${count}</small></button>`;
        }),
    ].join('');
}

export function hasMatchingSelectedTag(itemTags, selectedTags) {
    if (!selectedTags.size) return true;
    const ownedTags = new Set((itemTags || []).map((tag) => normalizeFacetValue(tag)).filter(Boolean));
    return [...selectedTags].some((tag) => ownedTags.has(tag));
}

export function compareDate(a, b, direction = 'desc') {
    const av = Date.parse(a || '') || 0;
    const bv = Date.parse(b || '') || 0;
    return direction === 'asc' ? av - bv : bv - av;
}

export function compareText(a, b, direction = 'asc') {
    const result = String(a || '').localeCompare(String(b || ''), 'zh');
    return direction === 'desc' ? -result : result;
}

export function compareNumber(a, b, direction = 'desc') {
    const av = Number(a);
    const bv = Number(b);
    const aMissing = !Number.isFinite(av);
    const bMissing = !Number.isFinite(bv);
    if (aMissing && bMissing) return 0;
    if (aMissing) return 1;
    if (bMissing) return -1;
    return direction === 'asc' ? av - bv : bv - av;
}

export function renderActiveFilterPills({ container, entries, clearText = '清空筛选' }) {
    if (!container) return;
    const active = entries.filter((entry) => normalizeFacetValue(entry.value));
    if (!active.length) {
        container.hidden = true;
        container.innerHTML = '';
        return;
    }
    container.hidden = false;
    container.innerHTML = active
        .map((entry) => `<span>${escapeHtml(entry.label)}：${escapeHtml(entry.value)}</span>`)
        .concat(`<small>${escapeHtml(clearText)}</small>`)
        .join('');
}
