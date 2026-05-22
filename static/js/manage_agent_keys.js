import { apiFetch } from '/static/js/api.js';
import { escapeHtml, showMessage } from '/static/js/ui.js';

const parseJsonScript = (id, fallback) => {
    const el = document.getElementById(id);
    if (!el) return fallback;
    try {
        return JSON.parse(el.textContent || '');
    } catch {
        return fallback;
    }
};

const state = {
    dashboard: parseJsonScript('agent-key-dashboard-data', {}),
};

const refs = {
    form: document.getElementById('agent-key-form'),
    label: document.getElementById('agent-key-label'),
    apiKey: document.getElementById('agent-key-value'),
    baseUrl: document.getElementById('agent-key-base-url'),
    model: document.getElementById('agent-key-model'),
    testOnSave: document.getElementById('agent-key-test-on-save'),
    makeActive: document.getElementById('agent-key-make-active'),
    saveBtn: document.getElementById('agent-key-save-btn'),
    refreshBtn: document.getElementById('agent-key-refresh-btn'),
    usageRefreshBtn: document.getElementById('agent-usage-refresh-btn'),
    keyList: document.getElementById('agent-key-list'),
    activeKey: document.getElementById('agent-active-key'),
    runtimeState: document.getElementById('agent-runtime-state'),
    usageTurns: document.getElementById('agent-usage-turns'),
    usageCost: document.getElementById('agent-usage-cost'),
    runtimeMessage: document.getElementById('agent-runtime-message'),
    runtimeUrl: document.getElementById('agent-runtime-url'),
    runtimeConfig: document.getElementById('agent-runtime-config'),
    runtimeUsageTime: document.getElementById('agent-runtime-usage-time'),
    usageSummary: document.getElementById('agent-usage-summary'),
    usageBuckets: document.getElementById('agent-usage-buckets'),
};

const statusLabels = {
    valid: '可用',
    failed: '不可用',
    unchecked: '未测试',
    unavailable: '暂不可达',
};

function numberLabel(value, fractionDigits = 0) {
    const numeric = Number(value || 0);
    return numeric.toLocaleString('zh-CN', {
        maximumFractionDigits: fractionDigits,
        minimumFractionDigits: 0,
    });
}

function costLabel(value) {
    const numeric = Number(value || 0);
    if (!Number.isFinite(numeric) || numeric <= 0) return '$0';
    return `$${numeric.toLocaleString('zh-CN', { maximumFractionDigits: 6 })}`;
}

function activeKey() {
    const keys = Array.isArray(state.dashboard.keys) ? state.dashboard.keys : [];
    return keys.find((item) => item.is_active) || null;
}

function latestUsage() {
    const runtime = state.dashboard.runtime || {};
    const snapshot = runtime.usage_snapshot || {};
    const groups = snapshot.groups || {};
    return groups.day || runtime.usage_snapshot?.groups?.day || {};
}

function usageTotals() {
    return latestUsage().totals || {};
}

function statusClass(status) {
    if (status === 'valid') return 'is-valid';
    if (status === 'failed') return 'is-failed';
    if (status === 'unavailable') return 'is-warning';
    return '';
}

function setBusy(button, busy, label = '处理中') {
    if (!button) return;
    if (busy) {
        button.dataset.originalText = button.textContent;
        button.textContent = label;
        button.disabled = true;
    } else {
        button.textContent = button.dataset.originalText || button.textContent;
        button.disabled = false;
    }
}

function setDefaults() {
    const defaults = state.dashboard.defaults || {};
    if (refs.baseUrl && !refs.baseUrl.value) {
        refs.baseUrl.value = defaults.base_url || 'https://api.deepseek.com';
    }
    if (refs.model && !refs.model.value) {
        refs.model.value = defaults.model || 'deepseek-v4-pro';
    }
}

function renderHero() {
    const current = activeKey();
    const runtime = state.dashboard.runtime || {};
    const config = state.dashboard.runtime_config || {};
    const totals = usageTotals();

    refs.activeKey.textContent = current ? `${current.key_label || 'Agent Key'} · ****${current.key_suffix || ''}` : '未启用';
    refs.runtimeState.textContent = runtime.configured ? '已配置' : '未配置';
    refs.usageTurns.textContent = numberLabel(totals.turns);
    refs.usageCost.textContent = costLabel(totals.cost_usd);
    refs.runtimeMessage.textContent = config.message || (runtime.configured ? '运行时已配置。' : '运行时未配置。');
    refs.runtimeUrl.textContent = runtime.url || '-';
    refs.runtimeConfig.textContent = config.config_path || '-';
    refs.runtimeUsageTime.textContent = runtime.usage_fetched_at || (runtime.usage_snapshot?.fetched_at || '-');
}

function renderUsage() {
    const totals = usageTotals();
    const cards = [
        ['输入 Tokens', numberLabel(totals.input_tokens)],
        ['输出 Tokens', numberLabel(totals.output_tokens)],
        ['缓存 Tokens', numberLabel(totals.cached_tokens)],
        ['推理 Tokens', numberLabel(totals.reasoning_tokens)],
    ];

    refs.usageSummary.innerHTML = cards.map(([label, value]) => `
        <div class="agent-key-usage-card">
            <span>${escapeHtml(label)}</span>
            <strong>${escapeHtml(value)}</strong>
        </div>
    `).join('');

    const buckets = Array.isArray(latestUsage().buckets) ? latestUsage().buckets : [];
    if (!buckets.length) {
        refs.usageBuckets.innerHTML = '<span>暂无运行用量</span>';
        return;
    }
    refs.usageBuckets.innerHTML = buckets.slice(-8).reverse().map((bucket) => {
        const label = `${bucket.key || '-'} · ${numberLabel(bucket.turns)} 次 · ${costLabel(bucket.cost_usd)}`;
        return `<span>${escapeHtml(label)}</span>`;
    }).join('');
}

function renderKeys() {
    const keys = Array.isArray(state.dashboard.keys) ? state.dashboard.keys : [];
    if (!refs.keyList) return;
    if (!keys.length) {
        refs.keyList.innerHTML = '<div class="agent-key-empty">尚未保存 Agent API Key。</div>';
        return;
    }

    refs.keyList.innerHTML = keys.map((item) => {
        const status = item.last_test_status || 'unchecked';
        const activeBadge = item.is_active
            ? '<span class="agent-key-badge is-active">当前启用</span>'
            : '';
        const canActivate = item.is_active
            ? ''
            : `<button type="button" class="btn btn-primary btn-sm" data-action="activate" data-id="${item.id}">启用</button>`;
        const message = item.last_test_message
            ? `<span>结果：${escapeHtml(item.last_test_message)}</span>`
            : '';
        return `
            <article class="agent-key-item" data-key-id="${item.id}">
                <div>
                    <div>
                        <span class="agent-key-badge ${statusClass(status)}">${escapeHtml(statusLabels[status] || status)}</span>
                        ${activeBadge}
                    </div>
                    <h4>${escapeHtml(item.key_label || 'Agent Key')}</h4>
                    <div class="agent-key-meta">
                        <span>尾号 ****${escapeHtml(item.key_suffix || '-')}</span>
                        <span>${escapeHtml(item.base_url || '-')}</span>
                        <span>${escapeHtml(item.model || '-')}</span>
                        <span>测试 ${escapeHtml(item.last_test_at || '-')}</span>
                        ${message}
                    </div>
                </div>
                <div class="agent-key-actions">
                    <button type="button" class="btn btn-outline btn-sm" data-action="test" data-id="${item.id}">测试</button>
                    ${canActivate}
                    <button type="button" class="btn btn-danger btn-sm" data-action="delete" data-id="${item.id}">删除</button>
                </div>
            </article>
        `;
    }).join('');
}

function renderAll() {
    setDefaults();
    renderHero();
    renderUsage();
    renderKeys();
}

async function refreshDashboard(button = null) {
    setBusy(button, true, '刷新中');
    try {
        const result = await apiFetch('/api/manage/system/agent-keys/status', { silent: true });
        state.dashboard = result.dashboard || {};
        renderAll();
    } catch (error) {
        showMessage(error.message || '刷新 Agent Key 状态失败。', 'error');
    } finally {
        setBusy(button, false);
    }
}

async function saveKey(event) {
    event.preventDefault();
    if (!refs.apiKey.value.trim()) {
        showMessage('请填写 DeepSeek API Key。', 'warning');
        refs.apiKey.focus();
        return;
    }

    const payload = Object.fromEntries(new FormData(refs.form).entries());
    payload.test_on_save = Boolean(refs.testOnSave?.checked);
    payload.make_active = Boolean(refs.makeActive?.checked);

    setBusy(refs.saveBtn, true, payload.test_on_save ? '测试中' : '保存中');
    try {
        const result = await apiFetch('/api/manage/system/agent-keys', {
            method: 'POST',
            body: payload,
        });
        state.dashboard = {
            ...state.dashboard,
            keys: result.keys || [],
            runtime_config: result.runtime_config || state.dashboard.runtime_config || {},
        };
        if (result.saved) {
            refs.apiKey.value = '';
            refs.label.value = '';
            showMessage(result.message || 'Agent API Key 已保存。', 'success');
        } else {
            showMessage(result.message || 'Agent API Key 测试失败，未保存。', 'warning');
        }
        renderAll();
    } catch (error) {
        showMessage(error.message || '保存 Agent API Key 失败。', 'error');
    } finally {
        setBusy(refs.saveBtn, false);
    }
}

async function refreshUsage(button) {
    setBusy(button, true, '读取中');
    try {
        const result = await apiFetch('/api/manage/system/agent-keys/usage/refresh', {
            method: 'POST',
        });
        state.dashboard = result.dashboard || state.dashboard;
        renderAll();
        const usage = result.usage || {};
        showMessage(usage.message || '运行用量已刷新。', usage.status === 'success' ? 'success' : 'warning');
    } catch (error) {
        showMessage(error.message || '刷新运行用量失败。', 'error');
    } finally {
        setBusy(button, false);
    }
}

async function testKey(id, button) {
    setBusy(button, true, '测试中');
    try {
        const result = await apiFetch(`/api/manage/system/agent-keys/${id}/test`, {
            method: 'POST',
        });
        state.dashboard = {
            ...state.dashboard,
            keys: result.keys || [],
            runtime_config: result.runtime_config || state.dashboard.runtime_config || {},
        };
        renderAll();
        showMessage(result.message || '测试完成。', result.status === 'success' ? 'success' : 'warning');
    } catch (error) {
        showMessage(error.message || '测试 Agent API Key 失败。', 'error');
    } finally {
        setBusy(button, false);
    }
}

async function activateKey(id, button) {
    setBusy(button, true, '启用中');
    try {
        const result = await apiFetch(`/api/manage/system/agent-keys/${id}/activate`, {
            method: 'POST',
        });
        state.dashboard = {
            ...state.dashboard,
            keys: result.keys || [],
            runtime_config: result.runtime_config || state.dashboard.runtime_config || {},
        };
        renderAll();
        showMessage(result.message || 'Agent API Key 已启用。', 'success');
    } catch (error) {
        showMessage(error.message || '启用 Agent API Key 失败。', 'error');
    } finally {
        setBusy(button, false);
    }
}

async function deleteKey(id, button) {
    if (!window.confirm('确定删除这个 Agent API Key 吗？')) {
        return;
    }
    setBusy(button, true, '删除中');
    try {
        const result = await apiFetch(`/api/manage/system/agent-keys/${id}`, {
            method: 'DELETE',
        });
        state.dashboard = {
            ...state.dashboard,
            keys: result.keys || [],
            runtime_config: result.runtime_config || state.dashboard.runtime_config || {},
        };
        renderAll();
        showMessage(result.message || 'Agent API Key 已删除。', 'success');
    } catch (error) {
        showMessage(error.message || '删除 Agent API Key 失败。', 'error');
    } finally {
        setBusy(button, false);
    }
}

refs.form?.addEventListener('submit', saveKey);
refs.refreshBtn?.addEventListener('click', () => refreshDashboard(refs.refreshBtn));
refs.usageRefreshBtn?.addEventListener('click', () => refreshUsage(refs.usageRefreshBtn));
refs.keyList?.addEventListener('click', (event) => {
    const button = event.target.closest('button[data-action]');
    if (!button) return;
    const id = button.dataset.id;
    if (!id) return;
    if (button.dataset.action === 'test') {
        testKey(id, button);
    } else if (button.dataset.action === 'activate') {
        activateKey(id, button);
    } else if (button.dataset.action === 'delete') {
        deleteKey(id, button);
    }
});

renderAll();
