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
    profiles: parseJsonScript('gw-profiles-data', []),
    credentials: parseJsonScript('gw-credentials-data', []),
    capabilities: [],
};

const refs = {
    form: document.getElementById('gw-credential-form'),
    systemSelect: document.getElementById('gw-system-code'),
    username: document.getElementById('gw-username'),
    password: document.getElementById('gw-password'),
    saveBtn: document.getElementById('gw-save-btn'),
    refreshBtn: document.getElementById('gw-refresh-btn'),
    list: document.getElementById('gw-credential-list'),
    profileSystem: document.getElementById('gw-profile-system'),
    profileLogin: document.getElementById('gw-profile-login'),
    profileMethod: document.getElementById('gw-profile-method'),
    profileNote: document.getElementById('gw-profile-note'),
    syncPanel: document.getElementById('gw-auto-sync-panel'),
    syncTitle: document.getElementById('gw-auto-sync-title'),
    syncMessage: document.getElementById('gw-auto-sync-message'),
    syncStages: document.getElementById('gw-auto-sync-stages'),
    accountManageBtn: document.getElementById('gw-account-manage-btn'),
    accountModal: document.getElementById('gw-account-modal'),
    accountModalClose: document.getElementById('gw-account-modal-close'),
    syncAllBtn: document.getElementById('gw-sync-all-btn'),
    capabilityRefreshBtn: document.getElementById('gw-capability-refresh-btn'),
    capabilityList: document.getElementById('gw-capability-list'),
};

const statusLabels = {
    verified: '已验证',
    failed: '验证失败',
    challenge_required: '需复核',
    unavailable: '暂不可用',
    unchecked: '未验证',
};

const syncStatusLabels = {
    success: '已完成',
    partial_success: '部分完成',
    empty: '暂无公文',
    failed: '未完成',
    missing_credential: '缺少凭据',
    unknown: '未知',
};

const countLabels = {
    fetched: '抓取',
    stored: '入库',
    downloaded: '下载附件',
    download_failed: '附件失败',
};

function currentProfile() {
    const selected = refs.systemSelect?.value || '';
    return state.profiles.find((item) => item.system_code === selected) || state.profiles[0] || null;
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

function formatDateTime(value) {
    if (!value) return '';
    const text = String(value).trim();
    if (!text) return '';
    const normalized = text.replace('T', ' ').replace(/\.\d+$/, '').replace(/([+-]\d{2}:\d{2}|Z)$/i, '');
    return normalized.length >= 16 ? normalized.slice(0, 16) : normalized;
}

function syncStatusLabel(status) {
    return syncStatusLabels[status] || status || '未知';
}

function countLabel(key, value) {
    return `${countLabels[key] || key} ${Number(value || 0)}`;
}

function renderProfile() {
    const profile = currentProfile();
    if (!profile) {
        refs.profileSystem.textContent = '-';
        refs.profileLogin.textContent = '-';
        refs.profileMethod.textContent = '-';
        refs.profileNote.textContent = '暂无可用公文通适配器。';
        return;
    }
    refs.profileSystem.textContent = profile.system_name || '-';
    refs.profileLogin.textContent = profile.login_url || '-';
    refs.profileMethod.textContent = profile.auth_method === 'sso_captcha'
        ? '统一认证 + 验证码识别'
        : (profile.auth_method || '-');
    refs.profileNote.textContent = profile.note || '该适配器用于统一认证登录校验与公文同步。';
}

function badgeClass(status) {
    if (status === 'verified') return 'is-verified';
    if (status === 'failed' || status === 'unavailable' || status === 'challenge_required') return 'is-failed';
    return 'is-unchecked';
}

function renderStats(item) {
    const stats = Array.isArray(item.stats) ? item.stats : [];
    if (!stats.length) {
        return `<span class="is-muted">${escapeHtml(item.status_text || '等待同步')}</span>`;
    }
    return stats
        .map((stat) => `<span>${escapeHtml(stat.label || '统计')} ${escapeHtml(Number(stat.value || 0))}</span>`)
        .join('');
}

function renderCapabilities() {
    if (!refs.capabilityList) return;
    if (!state.capabilities.length) {
        refs.capabilityList.innerHTML = '<div class="gw-empty">暂无可同步功能。请先在账号管理中保存并验证统一认证账号。</div>';
        return;
    }
    refs.capabilityList.innerHTML = state.capabilities.map((item) => {
        const syncedAt = formatDateTime(item.last_synced_at);
        const statusMeta = item.has_synced
            ? `<span>已同步</span>${syncedAt ? `<span>${escapeHtml(syncedAt)}</span>` : ''}`
            : '<span class="is-muted">未同步</span>';
        return `
            <article class="gw-sync-card">
                <div class="gw-sync-card-meta">${statusMeta}</div>
                <h4>${escapeHtml(item.label || '同步功能')}</h4>
                <p class="gw-help">${escapeHtml(item.description || '')}</p>
                <div class="gw-sync-card-meta">${renderStats(item)}</div>
                <p class="gw-help">${escapeHtml(item.safe_note || '')}</p>
            </article>
        `;
    }).join('');
}

function openAccountModal() {
    if (!refs.accountModal) return;
    refs.accountModal.hidden = false;
    refs.username?.focus({ preventScroll: true });
}

function closeAccountModal() {
    if (refs.accountModal) refs.accountModal.hidden = true;
}

function renderAutoSync(autoSync) {
    if (!refs.syncPanel || !refs.syncMessage || !refs.syncStages) return;
    if (!autoSync) {
        refs.syncPanel.hidden = true;
        return;
    }
    refs.syncPanel.hidden = false;
    if (refs.syncTitle) {
        refs.syncTitle.textContent = `公文同步：${syncStatusLabel(autoSync.status)}`;
    }
    refs.syncMessage.textContent = autoSync.message || '系统已完成公文同步流程。';
    const stages = Array.isArray(autoSync.stages) ? autoSync.stages : [];
    refs.syncStages.innerHTML = stages.map((stage) => {
        const counts = stage.counts && typeof stage.counts === 'object' ? stage.counts : {};
        const countItems = Object.entries(counts)
            .filter(([, value]) => Number(value || 0) > 0)
            .map(([key, value]) => `<span>${escapeHtml(countLabel(key, value))}</span>`)
            .join('');
        const warnings = Array.isArray(stage.warnings) && stage.warnings.length
            ? `<small>提示：${escapeHtml(stage.warnings.slice(0, 2).join('；'))}</small>`
            : '';
        return `
            <article class="gw-stage">
                <strong>${escapeHtml(stage.label || stage.key || '同步任务')} · ${escapeHtml(syncStatusLabel(stage.status))}</strong>
                ${stage.message ? `<small>${escapeHtml(stage.message)}</small>` : ''}
                ${countItems ? `<div class="gw-counts">${countItems}</div>` : ''}
                ${warnings}
            </article>
        `;
    }).join('');
}

function fillCredentialForm(item) {
    if (!item) return;
    refs.systemSelect.value = item.system_code || refs.systemSelect.value;
    refs.username.value = item.username || '';
    refs.password.value = '';
    renderProfile();
    refs.password?.focus({ preventScroll: true });
}

function renderCredentials() {
    if (!refs.list) return;
    if (!state.credentials.length) {
        refs.list.innerHTML = '<div class="gw-empty">尚未保存统一认证账号。填写并通过校验后，可同步公文与附件。</div>';
        return;
    }
    refs.list.innerHTML = state.credentials.map((item) => {
        const status = item.last_status || 'unchecked';
        const error = item.last_error ? `<span>最近错误：${escapeHtml(item.last_error)}</span>` : '';
        const verifiedAt = formatDateTime(item.last_verified_at || item.last_status_at || item.updated_at) || '暂无';
        return `
            <article class="gw-credential" data-credential-id="${item.id}">
                <div>
                    <span class="gw-badge ${badgeClass(status)}">${escapeHtml(statusLabels[status] || status)}</span>
                    <h4>${escapeHtml(item.system_name || '-')}${item.display_name ? `（${escapeHtml(item.display_name)}）` : ''}</h4>
                    <div class="gw-meta">
                        <span>账号：${escapeHtml(item.username || '-')}</span>
                        <span>密码：<span class="gw-password-mask">${item.has_password ? '••••••••' : '未保存'}</span></span>
                        <span>校验时间：${escapeHtml(verifiedAt)}</span>
                        ${error}
                    </div>
                </div>
                <div class="gw-actions">
                    <button type="button" class="btn btn-outline btn-sm" data-action="edit" data-id="${item.id}">修改</button>
                    <button type="button" class="btn btn-primary btn-sm" data-action="sync" data-id="${item.id}">同步公文</button>
                    <button type="button" class="btn btn-outline btn-sm" data-action="verify" data-id="${item.id}">重新验证</button>
                    <button type="button" class="btn btn-danger btn-sm" data-action="delete" data-id="${item.id}">删除</button>
                </div>
            </article>
        `;
    }).join('');
}

async function refreshCapabilities() {
    setBusy(refs.capabilityRefreshBtn, true, '刷新中');
    try {
        const result = await apiFetch('/api/manage/system/gongwen-sync-capabilities');
        state.capabilities = result.capabilities || [];
        renderCapabilities();
    } catch (error) {
        refs.capabilityList.innerHTML = '<div class="gw-empty">读取同步功能失败。</div>';
        showMessage(error.message || '读取公文同步功能失败。', 'error');
    } finally {
        setBusy(refs.capabilityRefreshBtn, false);
    }
}

async function refreshCredentials() {
    setBusy(refs.refreshBtn, true, '刷新中');
    try {
        const result = await apiFetch('/api/manage/system/gongwen-credentials');
        state.credentials = result.credentials || [];
        renderCredentials();
        await refreshCapabilities();
    } catch (error) {
        showMessage(error.message || '刷新公文对接状态失败。', 'error');
    } finally {
        setBusy(refs.refreshBtn, false);
    }
}

async function saveCredential(event) {
    event.preventDefault();
    const formData = new FormData(refs.form);
    const payload = Object.fromEntries(formData.entries());
    payload.enabled = true;
    if (!payload.username || !payload.password) {
        showMessage('请填写统一认证账号和密码。', 'warning');
        return;
    }
    setBusy(refs.saveBtn, true, '正在登录校验');
    renderAutoSync(null);
    try {
        const result = await apiFetch('/api/manage/system/gongwen-credentials', { method: 'POST', body: payload });
        state.credentials = result.credentials || [];
        refs.password.value = '';
        renderCredentials();
        renderAutoSync(result.auto_sync);
        await refreshCapabilities();
        showMessage(result.message || '账号已验证并保存，正在同步公文。', 'success');
    } catch (error) {
        showMessage(error.message || '统一认证账号校验失败。', 'error');
    } finally {
        setBusy(refs.saveBtn, false);
    }
}

async function verifyCredential(id, button) {
    setBusy(button, true, '验证中');
    renderAutoSync(null);
    try {
        const result = await apiFetch(`/api/manage/system/gongwen-credentials/${id}/verify`, { method: 'POST' });
        state.credentials = result.credentials || [];
        renderCredentials();
        renderAutoSync(result.auto_sync);
        await refreshCapabilities();
        showMessage(result.message || '公文通连接校验完成。', result.status === 'success' ? 'success' : 'warning');
    } catch (error) {
        showMessage(error.message || '重新验证失败。', 'error');
    } finally {
        setBusy(button, false);
    }
}

async function syncGongwen(button) {
    setBusy(button, true, '同步中');
    renderAutoSync(null);
    try {
        const result = await apiFetch('/api/manage/system/gongwen-sync', { method: 'POST' });
        renderAutoSync(result.auto_sync);
        await refreshCapabilities();
        showMessage(result.message || '公文同步已完成。', result.status === 'failed' ? 'warning' : 'success');
    } catch (error) {
        showMessage(error.message || '公文同步失败。', 'error');
    } finally {
        setBusy(button, false);
    }
}

async function deleteCredential(id, button) {
    if (!window.confirm('确定删除这个公文通对接吗？已同步的公文不会被删除。')) {
        return;
    }
    setBusy(button, true, '删除中');
    try {
        const result = await apiFetch(`/api/manage/system/gongwen-credentials/${id}`, { method: 'DELETE' });
        state.credentials = result.credentials || [];
        renderCredentials();
        renderAutoSync(null);
        await refreshCapabilities();
        showMessage(result.message || '公文通对接已删除。', 'success');
    } catch (error) {
        showMessage(error.message || '删除失败。', 'error');
    } finally {
        setBusy(button, false);
    }
}

refs.form?.addEventListener('submit', saveCredential);
refs.systemSelect?.addEventListener('change', renderProfile);
refs.refreshBtn?.addEventListener('click', refreshCredentials);
refs.accountManageBtn?.addEventListener('click', openAccountModal);
refs.accountModalClose?.addEventListener('click', closeAccountModal);
refs.accountModal?.addEventListener('click', (event) => {
    if (event.target === refs.accountModal) closeAccountModal();
});
refs.syncAllBtn?.addEventListener('click', (event) => syncGongwen(event.currentTarget));
refs.capabilityRefreshBtn?.addEventListener('click', refreshCapabilities);
refs.list?.addEventListener('click', (event) => {
    const button = event.target.closest('button[data-action]');
    if (!button) return;
    const id = button.dataset.id;
    if (!id) return;
    if (button.dataset.action === 'edit') {
        fillCredentialForm(state.credentials.find((credential) => String(credential.id) === String(id)));
    } else if (button.dataset.action === 'verify') {
        verifyCredential(id, button);
    } else if (button.dataset.action === 'sync') {
        syncGongwen(button);
    } else if (button.dataset.action === 'delete') {
        deleteCredential(id, button);
    }
});

renderProfile();
renderCredentials();
renderAutoSync(null);
refreshCapabilities();
