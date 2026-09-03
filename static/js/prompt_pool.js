import { apiFetch } from './api.js';
import { escapeHtml } from './ui.js';

const DEFAULT_LIMIT = 20;
const CACHE_TTL_MS = 45000;
const MAX_HIGHLIGHT_TERMS = 5;
const CONTROLLER_KEY = '__lansharePromptPool';
const debounceTimers = new WeakMap();
const suggestionCache = new Map();
let panelIdSeed = 0;

function normalizeFeatureKey(value) {
    return String(value || '').trim();
}

function normalizePrompt(value) {
    return String(value || '').replace(/\r\n/g, '\n').replace(/\r/g, '\n').trim();
}

function queryTerms(query) {
    const text = normalizePrompt(query).slice(0, 200);
    if (!text) return [];
    const parts = text.split(/\s+/).filter(Boolean);
    return (parts.length ? parts : [text])
        .slice(0, MAX_HIGHLIGHT_TERMS)
        .map((term) => term.toLocaleLowerCase())
        .filter(Boolean);
}

function labelText(input) {
    return input?.dataset?.promptPoolLabel || '分享到全局提示词池';
}

function highlightPrompt(prompt, query) {
    const text = normalizePrompt(prompt);
    const terms = queryTerms(query);
    if (!text || !terms.length) return escapeHtml(text);

    const lowerText = text.toLocaleLowerCase();
    const ranges = [];
    terms.forEach((term) => {
        let from = 0;
        while (term && from < lowerText.length) {
            const index = lowerText.indexOf(term, from);
            if (index < 0) break;
            ranges.push([index, index + term.length]);
            from = index + Math.max(term.length, 1);
        }
    });
    if (!ranges.length) return escapeHtml(text);

    ranges.sort((a, b) => a[0] - b[0] || b[1] - a[1]);
    const merged = [];
    ranges.forEach(([start, end]) => {
        const last = merged[merged.length - 1];
        if (!last || start > last[1]) {
            merged.push([start, end]);
        } else if (end > last[1]) {
            last[1] = end;
        }
    });

    let cursor = 0;
    let html = '';
    merged.forEach(([start, end]) => {
        if (start > cursor) html += escapeHtml(text.slice(cursor, start));
        html += `<mark>${escapeHtml(text.slice(start, end))}</mark>`;
        cursor = end;
    });
    if (cursor < text.length) html += escapeHtml(text.slice(cursor));
    return html;
}

function cacheKey(featureKey, query) {
    return `${featureKey}::${normalizePrompt(query).slice(0, 200)}`;
}

function readCache(featureKey, query) {
    const key = cacheKey(featureKey, query);
    const hit = suggestionCache.get(key);
    if (!hit) return null;
    if (Date.now() - hit.time > CACHE_TTL_MS) {
        suggestionCache.delete(key);
        return null;
    }
    return hit.items;
}

function writeCache(featureKey, query, items) {
    suggestionCache.set(cacheKey(featureKey, query), {
        time: Date.now(),
        items: Array.isArray(items) ? items : [],
    });
}

function invalidateFeatureCache(featureKey) {
    const prefix = `${featureKey}::`;
    for (const key of suggestionCache.keys()) {
        if (key.startsWith(prefix)) suggestionCache.delete(key);
    }
}

function ensurePanel(input, featureKey) {
    const shareId = `prompt-pool-share-${++panelIdSeed}`;
    const shareRow = document.createElement('label');
    shareRow.className = 'prompt-pool-share';
    shareRow.setAttribute('for', shareId);
    shareRow.title = '取消勾选后，本次输入只用于当前生成，不进入共享提示词池。';
    shareRow.innerHTML = `
        <input id="${shareId}" type="checkbox" data-prompt-pool-share checked>
        <span>${escapeHtml(labelText(input))}</span>
        <small>取消则不记录</small>
    `;
    const checkbox = shareRow.querySelector('[data-prompt-pool-share]');

    const panel = document.createElement('div');
    panel.className = 'prompt-pool-panel';
    panel.hidden = true;
    panel.id = `prompt-pool-panel-${++panelIdSeed}`;
    panel.dataset.promptPoolPanel = featureKey;
    panel.setAttribute('role', 'listbox');
    panel.setAttribute('aria-label', '共享提示词建议');

    input.insertAdjacentElement('afterend', panel);
    input.insertAdjacentElement('afterend', shareRow);
    input.setAttribute('autocomplete', 'off');
    input.setAttribute('aria-autocomplete', 'list');
    input.setAttribute('aria-haspopup', 'listbox');
    input.setAttribute('aria-expanded', 'false');
    input.setAttribute('aria-controls', panel.id);
    return { shareRow, panel, checkbox };
}

function renderPanel(panel, items, { loading = false, query = '', activeIndex = -1 } = {}) {
    const safeItems = Array.isArray(items) ? items : [];
    panel.__promptPoolItems = [];
    if (loading) {
        panel.hidden = false;
        panel.innerHTML = `
            <div class="prompt-pool-empty prompt-pool-empty--loading" role="status">
                <span class="prompt-pool-spinner"></span>
                <span>正在读取共享提示词...</span>
            </div>`;
        return;
    }
    if (!safeItems.length) {
        panel.hidden = false;
        panel.innerHTML = `
            <div class="prompt-pool-empty" role="status">
                <strong>${query ? '没有匹配的共享提示词' : '当前功能还没有共享提示词'}</strong>
                <span>${query ? '换个关键词试试，或直接输入新的提示。' : '输入并生成成功后，可选择分享到这里。'}</span>
            </div>`;
        return;
    }
    const title = query ? '匹配的共享提示词' : '常用共享提示词';
    panel.__promptPoolItems = safeItems.map((item) => normalizePrompt(item.prompt));
    panel.hidden = false;
    panel.innerHTML = `
        <div class="prompt-pool-panel__head">
            <strong>${title}</strong>
            <span>${safeItems.length} 条 · 按使用次数排序</span>
        </div>
        <div class="prompt-pool-list">
            ${safeItems.map((item, index) => {
                const isActive = index === activeIndex;
                const optionId = `${panel.id}-option-${index}`;
                const prompt = normalizePrompt(item.prompt);
                return `
                    <button
                        type="button"
                        id="${optionId}"
                        class="prompt-pool-item${isActive ? ' is-active' : ''}"
                        data-prompt-pool-use-index="${index}"
                        role="option"
                        aria-selected="${isActive ? 'true' : 'false'}"
                        title="${escapeHtml(prompt)}"
                    >
                        <span class="prompt-pool-item__text">${highlightPrompt(prompt, query)}</span>
                        <span class="prompt-pool-item__meta">
                            <em>${Number(item.use_count || 0)} 次</em>
                            <b>点击套用</b>
                        </span>
                    </button>
                `;
            }).join('')}
        </div>
    `;
}

function setPanelExpanded(controller, expanded) {
    controller.input.setAttribute('aria-expanded', expanded ? 'true' : 'false');
    if (!expanded) {
        controller.input.removeAttribute('aria-activedescendant');
    }
}

function syncActiveDescendant(controller) {
    if (!controller || controller.activeIndex < 0) {
        controller?.input?.removeAttribute('aria-activedescendant');
        return;
    }
    const active = controller.panel.querySelector(`[data-prompt-pool-use-index="${controller.activeIndex}"]`);
    if (active?.id) {
        controller.input.setAttribute('aria-activedescendant', active.id);
    } else {
        controller.input.removeAttribute('aria-activedescendant');
    }
}

function visibleSuggestionButtons(controller) {
    return Array.from(controller.panel.querySelectorAll('[data-prompt-pool-use-index]'));
}

function isPanelOpen(controller) {
    return controller.panel && !controller.panel.hidden;
}

function applySuggestions(controller, items, query, activeIndex = -1) {
    controller.items = Array.isArray(items) ? items : [];
    controller.activeIndex = activeIndex;
    renderPanel(controller.panel, controller.items, { query, activeIndex });
    setPanelExpanded(controller, true);
    syncActiveDescendant(controller);
}

async function fetchSuggestions(controller, query = '') {
    const normalizedQuery = normalizePrompt(query).slice(0, 200);
    controller.lastQuery = normalizedQuery;

    const cached = readCache(controller.featureKey, normalizedQuery);
    if (cached) {
        applySuggestions(controller, cached, normalizedQuery);
        return;
    }

    renderPanel(controller.panel, [], { loading: true, query: normalizedQuery });
    setPanelExpanded(controller, true);
    try {
        const params = new URLSearchParams({
            feature_key: controller.featureKey,
            q: normalizedQuery,
            limit: String(DEFAULT_LIMIT),
        });
        const data = await apiFetch(`/api/prompt-pool?${params.toString()}`, { silent: true });
        if (controller.lastQuery !== normalizedQuery) return;
        const prompts = data.prompts || [];
        writeCache(controller.featureKey, normalizedQuery, prompts);
        applySuggestions(controller, prompts, normalizedQuery);
    } catch (_) {
        controller.panel.hidden = true;
        setPanelExpanded(controller, false);
    }
}

function scheduleSuggestions(controller) {
    const existing = debounceTimers.get(controller.input);
    if (existing) window.clearTimeout(existing);
    const timer = window.setTimeout(() => {
        fetchSuggestions(controller, controller.input.value || '');
    }, 160);
    debounceTimers.set(controller.input, timer);
}

function hidePanel(controller) {
    controller.panel.hidden = true;
    controller.activeIndex = -1;
    setPanelExpanded(controller, false);
    syncActiveDescendant(controller);
}

function shouldDeferPromptPoolHide(event) {
    const target = event?.target;
    if (!target?.closest) return false;
    return Boolean(target.closest('button, a, input, select, textarea, [role="button"]'));
}

function moveActive(controller, direction) {
    const buttons = visibleSuggestionButtons(controller);
    if (!buttons.length) return;
    const current = Number.isInteger(controller.activeIndex) ? controller.activeIndex : -1;
    const next = current < 0
        ? (direction > 0 ? 0 : buttons.length - 1)
        : (current + direction + buttons.length) % buttons.length;
    controller.activeIndex = next;
    renderPanel(controller.panel, controller.items || [], {
        query: controller.lastQuery,
        activeIndex: next,
    });
    syncActiveDescendant(controller);
    visibleSuggestionButtons(controller)[next]?.scrollIntoView({ block: 'nearest' });
}

function selectSuggestion(controller, index) {
    const value = Number.isInteger(index)
        ? normalizePrompt(controller.items?.[index]?.prompt || controller.panel.__promptPoolItems?.[index] || '')
        : '';
    if (!value) return;
    controller.input.value = value;
    controller.suppressNextInputSuggestions = true;
    controller.input.dispatchEvent(new Event('input', { bubbles: true }));
    controller.input.dispatchEvent(new Event('change', { bubbles: true }));
    hidePanel(controller);
    controller.input.focus();
}

export function enhancePromptPoolInput(input, options = {}) {
    if (!input || input[CONTROLLER_KEY]) return input?.[CONTROLLER_KEY] || null;
    const featureKey = normalizeFeatureKey(options.featureKey || input.dataset.promptPoolKey);
    if (!featureKey) return null;
    input.dataset.promptPoolKey = featureKey;
    const { shareRow, panel, checkbox } = ensurePanel(input, featureKey);
    const controller = {
        input,
        featureKey,
        panel,
        shareRow,
        checkbox,
        items: [],
        activeIndex: -1,
        lastQuery: '',
        suppressNextInputSuggestions: false,
        getShareEnabled() {
            return !checkbox || checkbox.checked;
        },
        hide() {
            hidePanel(controller);
        },
        show() {
            if (input.disabled) return;
            fetchSuggestions(controller, input.value || '');
        },
        record(prompt = input.value) {
            return recordPromptForInput(input, prompt);
        },
        destroy() {
            document.removeEventListener('pointerdown', onOutsidePointer);
            window.clearTimeout(debounceTimers.get(input));
            debounceTimers.delete(input);
            controller.hide();
        },
    };
    input[CONTROLLER_KEY] = controller;

    input.addEventListener('focus', () => controller.show());
    input.addEventListener('input', () => {
        if (controller.suppressNextInputSuggestions) {
            controller.suppressNextInputSuggestions = false;
            controller.activeIndex = -1;
            return;
        }
        controller.activeIndex = -1;
        scheduleSuggestions(controller);
    });
    input.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
            controller.hide();
            return;
        }
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            if (!isPanelOpen(controller)) controller.show();
            moveActive(controller, event.key === 'ArrowDown' ? 1 : -1);
            event.preventDefault();
            return;
        }
        if (event.key === 'Enter' && isPanelOpen(controller) && controller.activeIndex >= 0) {
            selectSuggestion(controller, controller.activeIndex);
            event.preventDefault();
        }
    });
    input.addEventListener('blur', () => {
        window.setTimeout(() => {
            if (!panel.matches(':hover') && !shareRow.matches(':hover') && document.activeElement !== input) {
                controller.hide();
            }
        }, 150);
    });
    checkbox?.addEventListener('change', () => {
        shareRow.classList.toggle('is-off', !checkbox.checked);
    });
    function onOutsidePointer(event) {
        if (
            event.target === input
            || panel.contains(event.target)
            || shareRow.contains(event.target)
        ) {
            return;
        }
        if (shouldDeferPromptPoolHide(event)) {
            window.setTimeout(() => controller.hide(), 0);
            return;
        }
        controller.hide();
    }
    document.addEventListener('pointerdown', onOutsidePointer);
    panel.addEventListener('mousedown', (event) => {
        if (event.target.closest('[data-prompt-pool-use-index]')) event.preventDefault();
    });
    panel.addEventListener('click', (event) => {
        const item = event.target.closest('[data-prompt-pool-use-index]');
        if (!item) return;
        selectSuggestion(controller, Number(item.dataset.promptPoolUseIndex));
    });
    return controller;
}

export function enhancePromptPoolInputs(root = document) {
    return Array.from(root.querySelectorAll('[data-prompt-pool-key]'))
        .map((input) => enhancePromptPoolInput(input))
        .filter(Boolean);
}

export function getPromptPoolController(input) {
    return input?.[CONTROLLER_KEY] || null;
}

export async function recordPromptForInput(input, prompt = input?.value) {
    const controller = getPromptPoolController(input) || enhancePromptPoolInput(input);
    const text = normalizePrompt(prompt);
    if (!controller || !controller.getShareEnabled() || !text) return null;
    try {
        const result = await apiFetch('/api/prompt-pool/record', {
            method: 'POST',
            body: { feature_key: controller.featureKey, prompt: text, share: controller.getShareEnabled() },
            silent: true,
        });
        invalidateFeatureCache(controller.featureKey);
        return result;
    } catch (_) {
        return null;
    }
}

export async function recordPromptPoolInputs(root = document) {
    const inputs = Array.from(root.querySelectorAll('[data-prompt-pool-key]'));
    const results = [];
    for (const input of inputs) {
        results.push(await recordPromptForInput(input));
    }
    return results;
}

export function isPromptShareEnabled(input) {
    const controller = getPromptPoolController(input) || enhancePromptPoolInput(input);
    return controller ? controller.getShareEnabled() : true;
}

window.PromptPool = {
    enhancePromptPoolInput,
    enhancePromptPoolInputs,
    getPromptPoolController,
    recordPromptForInput,
    recordPromptPoolInputs,
    isPromptShareEnabled,
};
