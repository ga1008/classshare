import { apiFetch } from './api.js';
import { escapeHtml } from './ui.js';

const DEFAULT_LIMIT = 20;
const CONTROLLER_KEY = '__lansharePromptPool';
const debounceTimers = new WeakMap();

function normalizeFeatureKey(value) {
    return String(value || '').trim();
}

function normalizePrompt(value) {
    return String(value || '').replace(/\r\n/g, '\n').replace(/\r/g, '\n').trim();
}

function labelText(input) {
    return input?.dataset?.promptPoolLabel || '分享到全局提示词池';
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
    panel.dataset.promptPoolPanel = featureKey;

    input.insertAdjacentElement('afterend', panel);
    input.insertAdjacentElement('afterend', shareRow);
    return { shareRow, panel };
}

function renderPanel(panel, items, { loading = false, query = '' } = {}) {
    panel.__promptPoolItems = [];
    if (loading) {
        panel.hidden = false;
        panel.innerHTML = '<div class="prompt-pool-empty">正在读取共享提示词...</div>';
        return;
    }
    if (!items.length) {
        panel.hidden = false;
        panel.innerHTML = `<div class="prompt-pool-empty">${query ? '没有匹配的共享提示词' : '当前功能还没有共享提示词'}</div>`;
        return;
    }
    const title = query ? '匹配的共享提示词' : '常用共享提示词';
    panel.__promptPoolItems = items.map((item) => normalizePrompt(item.prompt));
    panel.hidden = false;
    panel.innerHTML = `
        <div class="prompt-pool-panel__head">
            <strong>${title}</strong>
            <span>按使用次数排序</span>
        </div>
        <div class="prompt-pool-list">
            ${items.map((item, index) => `
                <button type="button" class="prompt-pool-item" data-prompt-pool-use-index="${index}">
                    <span>${escapeHtml(item.prompt)}</span>
                    <em>${Number(item.use_count || 0)} 次</em>
                </button>
            `).join('')}
        </div>
    `;
}

async function fetchSuggestions(controller, query = '') {
    const normalizedQuery = normalizePrompt(query).slice(0, 200);
    controller.lastQuery = normalizedQuery;
    renderPanel(controller.panel, [], { loading: true, query: normalizedQuery });
    try {
        const params = new URLSearchParams({
            feature_key: controller.featureKey,
            q: normalizedQuery,
            limit: String(DEFAULT_LIMIT),
        });
        const data = await apiFetch(`/api/prompt-pool?${params.toString()}`, { silent: true });
        if (controller.lastQuery !== normalizedQuery) return;
        renderPanel(controller.panel, data.prompts || [], { query: normalizedQuery });
    } catch (_) {
        controller.panel.hidden = true;
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
        lastQuery: '',
        getShareEnabled() {
            return !checkbox || checkbox.checked;
        },
        hide() {
            panel.hidden = true;
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
    input.addEventListener('input', () => scheduleSuggestions(controller));
    input.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') controller.hide();
    });
    input.addEventListener('blur', () => {
        window.setTimeout(() => {
            if (!panel.matches(':hover') && document.activeElement !== input) {
                controller.hide();
            }
        }, 150);
    });
    panel.addEventListener('click', (event) => {
        const item = event.target.closest('[data-prompt-pool-use-index]');
        if (!item) return;
        const index = Number(item.dataset.promptPoolUseIndex);
        input.value = Number.isInteger(index) ? (panel.__promptPoolItems?.[index] || '') : '';
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
        controller.hide();
        input.focus();
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
        return await apiFetch('/api/prompt-pool/record', {
            method: 'POST',
            body: { feature_key: controller.featureKey, prompt: text },
            silent: true,
        });
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
