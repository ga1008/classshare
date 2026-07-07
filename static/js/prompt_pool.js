import { apiFetch } from './api.js';
import { escapeHtml } from './ui.js';

const DEFAULT_LIMIT = 20;
const CACHE_TTL_MS = 45000;
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

function labelText(input) {
    return input?.dataset?.promptPoolLabel || '分享到全局提示词池';
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
    const shareRow = document.createElement('label');
    shareRow.className = 'prompt-pool-share';
    shareRow.innerHTML = `
        <input type="checkbox" data-prompt-pool-share checked>
        <span>${escapeHtml(labelText(input))}</span>
    `;

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
    input.setAttribute('aria-expanded', 'false');
    input.setAttribute('aria-controls', panel.id);
    return { shareRow, panel };
}

function renderPanel(panel, items, { loading = false, query = '', activeIndex = -1 } = {}) {
    const safeItems = Array.isArray(items) ? items : [];
    panel.__promptPoolItems = [];
    if (loading) {
        panel.hidden = false;
        panel.innerHTML = '<div class="prompt-pool-empty">正在读取共享提示词...</div>';
        return;
    }
    if (!safeItems.length) {
        panel.hidden = false;
        panel.innerHTML = `<div class="prompt-pool-empty">${query ? '没有匹配的共享提示词' : '当前功能还没有共享提示词'}</div>`;
        return;
    }
    const title = query ? '匹配的共享提示词' : '常用共享提示词';
    panel.__promptPoolItems = safeItems.map((item) => normalizePrompt(item.prompt));
    panel.hidden = false;
    panel.innerHTML = `
        <div class="prompt-pool-panel__head">
            <strong>${title}</strong>
            <span>按使用次数排序</span>
        </div>
        <div class="prompt-pool-list">
            ${safeItems.map((item, index) => {
                const isActive = index === activeIndex;
                return `
                    <button
                        type="button"
                        class="prompt-pool-item${isActive ? ' is-active' : ''}"
                        data-prompt-pool-use-index="${index}"
                        role="option"
                        aria-selected="${isActive ? 'true' : 'false'}"
                    >
                        <span>${escapeHtml(item.prompt)}</span>
                        <em>${Number(item.use_count || 0)} 次</em>
                    </button>
                `;
            }).join('')}
        </div>
    `;
}

function setPanelExpanded(controller, expanded) {
    controller.input.setAttribute('aria-expanded', expanded ? 'true' : 'false');
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
    visibleSuggestionButtons(controller)[next]?.scrollIntoView({ block: 'nearest' });
}

function selectSuggestion(controller, index) {
    const value = Number.isInteger(index) ? (controller.panel.__promptPoolItems?.[index] || '') : '';
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
    const { panel } = ensurePanel(input, featureKey);
    const checkbox = input.parentElement?.querySelector('[data-prompt-pool-share]')
        || panel.parentElement?.querySelector('[data-prompt-pool-share]');
    const controller = {
        input,
        featureKey,
        panel,
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
            fetchSuggestions(controller, input.value || '');
        },
        record(prompt = input.value) {
            return recordPromptForInput(input, prompt);
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
            if (!panel.matches(':hover') && document.activeElement !== input) {
                controller.hide();
            }
        }, 150);
    });
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
            body: { feature_key: controller.featureKey, prompt: text },
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
    recordPromptForInput,
    recordPromptPoolInputs,
    isPromptShareEnabled,
};
