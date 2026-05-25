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
    profiles: parseJsonScript('smart-profiles-data', []),
    credentials: parseJsonScript('smart-credentials-data', []),
    capabilities: [],
    activeCapability: null,
};

const refs = {
    form: document.getElementById('smart-classroom-credential-form'),
    platformSelect: document.getElementById('smart-platform-code'),
    username: document.getElementById('smart-username'),
    password: document.getElementById('smart-password'),
    saveBtn: document.getElementById('smart-save-btn'),
    refreshBtn: document.getElementById('smart-refresh-btn'),
    list: document.getElementById('smart-credential-list'),
    profilePlatform: document.getElementById('smart-profile-platform'),
    profileLogin: document.getElementById('smart-profile-login'),
    profileMethod: document.getElementById('smart-profile-method'),
    profileNote: document.getElementById('smart-profile-note'),
    syncPanel: document.getElementById('smart-auto-sync-panel'),
    syncTitle: document.getElementById('smart-auto-sync-title'),
    syncMessage: document.getElementById('smart-auto-sync-message'),
    syncStages: document.getElementById('smart-auto-sync-stages'),
    accountManageBtn: document.getElementById('smart-account-manage-btn'),
    accountModal: document.getElementById('smart-account-modal'),
    accountModalClose: document.getElementById('smart-account-modal-close'),
    syncAllBtn: document.getElementById('smart-sync-all-btn'),
    capabilityRefreshBtn: document.getElementById('smart-sync-capability-refresh-btn'),
    capabilityList: document.getElementById('smart-sync-capability-list'),
    syncModal: document.getElementById('smart-sync-modal'),
    syncModalTitle: document.getElementById('smart-sync-modal-title'),
    syncModalDescription: document.getElementById('smart-sync-modal-description'),
    syncModalLast: document.getElementById('smart-sync-modal-last'),
    syncModalState: document.getElementById('smart-sync-modal-state'),
    syncModalEndpoint: document.getElementById('smart-sync-modal-endpoint'),
    syncModalScope: document.getElementById('smart-sync-modal-scope'),
    syncModalParams: document.getElementById('smart-sync-modal-params'),
    syncModalSafe: document.getElementById('smart-sync-modal-safe'),
    syncModalRun: document.getElementById('smart-sync-modal-run'),
    syncModalClose: document.getElementById('smart-sync-modal-close'),
    syncModalCancel: document.getElementById('smart-sync-modal-cancel'),
    probeMethod: document.getElementById('smart-probe-method'),
    probeUrl: document.getElementById('smart-probe-url'),
    probeParams: document.getElementById('smart-probe-params'),
    probeHeaders: document.getElementById('smart-probe-headers'),
    probeBodyMode: document.getElementById('smart-probe-body-mode'),
    probeBody: document.getElementById('smart-probe-body'),
    probeBtn: document.getElementById('smart-sync-modal-probe'),
    probeResult: document.getElementById('smart-probe-result'),
    probeResultHead: document.getElementById('smart-probe-result-head'),
    probeResultBody: document.getElementById('smart-probe-result-body'),
};

const statusLabels = {
    verified: '已验证',
    failed: '验证失败',
    unavailable: '暂不可用',
    unchecked: '未验证',
};

const syncStatusLabels = {
    success: '已完成',
    partial_success: '部分完成',
    empty: '暂无记录',
    failed: '未完成',
    missing_credential: '缺少凭据',
    unknown: '未知',
};

const countLabels = {
    schedule_count: '授课班',
    matched_schedule_count: '已对齐授课班',
    checkin_count: '点名记录',
    matched_checkin_count: '已对齐记录',
    student_count: '学生状态',
    matched_session_count: '已对齐课次',
    unmatched_session_count: '待复核课次',
};

function currentProfile() {
    const selected = refs.platformSelect?.value || '';
    return state.profiles.find((item) => item.platform_code === selected) || state.profiles[0] || null;
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
    const normalized = text
        .replace('T', ' ')
        .replace(/\.\d+$/, '')
        .replace(/([+-]\d{2}:\d{2}|Z)$/i, '');
    return normalized.length >= 16 ? normalized.slice(0, 16) : normalized;
}

function stringifyJson(value) {
    return JSON.stringify(value || {}, null, 2);
}

function parseJsonField(textarea, label, fallback = {}) {
    const raw = textarea?.value?.trim() || '';
    if (!raw) return fallback;
    try {
        const parsed = JSON.parse(raw);
        if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) return parsed;
    } catch {
        // The message below is clearer than the native parser text.
    }
    throw new Error(`${label} 必须是 JSON 对象。`);
}

function renderProfile() {
    const profile = currentProfile();
    if (!profile) {
        refs.profilePlatform.textContent = '-';
        refs.profileLogin.textContent = '-';
        refs.profileMethod.textContent = '-';
        refs.profileNote.textContent = '暂无可用智慧课堂适配器。';
        return;
    }
    refs.profilePlatform.textContent = profile.platform_name || '-';
    refs.profileLogin.textContent = profile.login_url || '-';
    refs.profileMethod.textContent = profile.auth_method === 'password_token'
        ? '账号密码 + 临时 Token'
        : (profile.auth_method || '-');
    refs.profileNote.textContent = profile.note || '该适配器用于登录校验和后续点名记录同步。';
}

function badgeClass(status) {
    if (status === 'verified') return 'is-verified';
    if (status === 'failed' || status === 'unavailable') return 'is-failed';
    return 'is-unchecked';
}

function formatStatusTime(item) {
    return formatDateTime(item.last_verified_at || item.last_status_at || item.updated_at || '');
}

function autoSyncStatusLabel(status) {
    return syncStatusLabels[status] || status || '未知';
}

function countLabel(key, value) {
    return `${countLabels[key] || key} ${Number(value || 0)}`;
}

function renderStats(item) {
    const stats = Array.isArray(item.stats) && item.stats.length
        ? item.stats
        : Object.entries(item.counts || {})
            .filter(([, value]) => Number(value || 0) > 0)
            .slice(0, 3)
            .map(([key, value]) => ({ label: countLabel(key, value).replace(/\s+\d+$/, ''), value }));
    if (!stats.length) {
        return `<span class="is-muted">${escapeHtml(item.status_text || '等待同步')}</span>`;
    }
    return stats
        .filter((stat) => Number(stat.value || 0) >= 0)
        .slice(0, 4)
        .map((stat) => `<span class="is-stat">${escapeHtml(stat.label || '统计')} ${escapeHtml(Number(stat.value || 0))}</span>`)
        .join('');
}

function renderCapabilities() {
    if (!refs.capabilityList) return;
    if (!state.capabilities.length) {
        refs.capabilityList.innerHTML = '<div class="smart-classroom-empty">暂无可同步功能。请先在账号管理中保存并验证智慧课堂账号。</div>';
        return;
    }
    refs.capabilityList.innerHTML = state.capabilities.map((item) => {
        const syncedAt = formatDateTime(item.last_synced_at);
        const statusMeta = item.has_synced
            ? `<span>已同步</span>${syncedAt ? `<span>${escapeHtml(syncedAt)}</span>` : ''}`
            : '<span class="is-muted">未同步</span>';
        return `
            <button type="button" class="smart-classroom-sync-card" data-sync-key="${escapeHtml(item.key || '')}">
                <div class="smart-classroom-sync-card-meta">${statusMeta}</div>
                <h4>${escapeHtml(item.label || '同步功能')}</h4>
                <p>${escapeHtml(item.description || '')}</p>
                <div class="smart-classroom-sync-card-meta">${renderStats(item)}</div>
            </button>
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

function closeSyncModal() {
    if (!refs.syncModal) return;
    refs.syncModal.hidden = true;
    state.activeCapability = null;
}

function fillProbeForm(item) {
    const template = item.request_template || {};
    refs.probeMethod.value = (template.method || item.method || 'POST').toUpperCase();
    refs.probeUrl.value = template.url || item.endpoint || '';
    refs.probeParams.value = stringifyJson(template.params || {});
    refs.probeHeaders.value = stringifyJson(template.headers || {});
    refs.probeBodyMode.value = template.body_mode || 'form';
    refs.probeBody.value = refs.probeBodyMode.value === 'raw'
        ? String(template.body || '')
        : stringifyJson(template.body || {});
    refs.probeResult.hidden = true;
    refs.probeResultHead.innerHTML = '';
    refs.probeResultBody.textContent = '';
}

function openSyncModal(item) {
    if (!refs.syncModal || !item) return;
    state.activeCapability = item;
    refs.syncModalTitle.textContent = item.label || '同步详情';
    refs.syncModalDescription.textContent = item.description || '';
    refs.syncModalLast.textContent = item.last_synced_at ? formatDateTime(item.last_synced_at) : '暂无同步记录';
    refs.syncModalState.textContent = item.has_synced ? (item.status_text || '已同步') : '未同步';
    refs.syncModalEndpoint.textContent = `${item.method || 'POST'} ${item.endpoint || '-'}`;
    refs.syncModalScope.textContent = item.scope || '当前教师账号可访问的数据';
    const parameters = Array.isArray(item.parameters) ? item.parameters : [];
    refs.syncModalParams.innerHTML = parameters.length
        ? parameters.map((param) => `
            <div>
                <span>${escapeHtml(param.name || '参数')}</span>
                <strong>${escapeHtml(param.value || '-')}</strong>
            </div>
        `).join('')
        : '<div><span>同步参数</span><strong>由系统根据已保存账号和当前教师自动生成</strong></div>';
    refs.syncModalSafe.textContent = item.safe_note || '';
    fillProbeForm(item);
    refs.syncModal.hidden = false;
    refs.syncModalRun?.focus({ preventScroll: true });
}

async function refreshCapabilities() {
    setBusy(refs.capabilityRefreshBtn, true, '刷新中');
    try {
        const result = await apiFetch('/api/manage/system/smart-classroom-sync-capabilities');
        state.capabilities = result.capabilities || [];
        renderCapabilities();
    } catch (error) {
        refs.capabilityList.innerHTML = '<div class="smart-classroom-empty">读取同步功能失败。</div>';
        showMessage(error.message || '读取智慧课堂同步功能失败。', 'error');
    } finally {
        setBusy(refs.capabilityRefreshBtn, false);
    }
}

function renderAutoSync(autoSync) {
    if (!refs.syncPanel || !refs.syncMessage || !refs.syncStages) return;
    if (!autoSync) {
        refs.syncPanel.hidden = true;
        return;
    }
    refs.syncPanel.hidden = false;
    if (refs.syncTitle) {
        refs.syncTitle.textContent = `智慧课堂同步：${autoSyncStatusLabel(autoSync.status)}`;
    }
    refs.syncMessage.textContent = autoSync.message || '系统已完成智慧课堂同步流程。';
    const stages = Array.isArray(autoSync.stages) ? autoSync.stages : [];
    refs.syncStages.innerHTML = stages.map((stage) => {
        const counts = stage.counts && typeof stage.counts === 'object' ? stage.counts : {};
        const countItems = Object.entries(counts)
            .filter(([, value]) => Number(value || 0) > 0)
            .map(([key, value]) => `<span>${escapeHtml(countLabel(key, value))}</span>`)
            .join('');
        const warnings = Array.isArray(stage.warnings) && stage.warnings.length
            ? `<small>复核：${escapeHtml(stage.warnings.slice(0, 2).join('；'))}</small>`
            : '';
        return `
            <article class="smart-classroom-stage">
                <strong>${escapeHtml(stage.label || stage.key || '同步任务')} · ${escapeHtml(autoSyncStatusLabel(stage.status))}</strong>
                ${stage.message ? `<small>${escapeHtml(stage.message)}</small>` : ''}
                ${countItems ? `<div class="smart-classroom-counts">${countItems}</div>` : ''}
                ${warnings}
            </article>
        `;
    }).join('');
}

function fillCredentialForm(item) {
    if (!item) return;
    refs.platformSelect.value = item.platform_code || refs.platformSelect.value;
    refs.username.value = item.username || '';
    refs.password.value = '';
    renderProfile();
    refs.password?.focus({ preventScroll: true });
}

function renderCredentials() {
    if (!refs.list) return;
    if (!state.credentials.length) {
        refs.list.innerHTML = '<div class="smart-classroom-empty">尚未保存智慧课堂账号。填写并通过校验后，可同步点名记录和学生签到状态。</div>';
        return;
    }

    refs.list.innerHTML = state.credentials.map((item) => {
        const status = item.last_status || 'unchecked';
        const error = item.last_error
            ? `<span>最近错误：${escapeHtml(item.last_error)}</span>`
            : '';
        const method = item.access_method?.auth_method === 'password_token'
            ? '账号密码 + Token'
            : (item.access_method?.auth_method || item.auth_method || '-');
        const verifiedAt = formatStatusTime(item) || '暂无';
        return `
            <article class="smart-classroom-credential" data-credential-id="${item.id}">
                <div>
                    <span class="smart-classroom-badge ${badgeClass(status)}">${escapeHtml(statusLabels[status] || status)}</span>
                    <h4>${escapeHtml(item.platform_name || '-')}</h4>
                    <div class="smart-classroom-meta">
                        <span>账号：${escapeHtml(item.username || '-')}</span>
                        <span>密码：<span class="smart-classroom-password-mask">${item.has_password ? '••••••••' : '未保存'}</span></span>
                        <span>方式：${escapeHtml(method)}</span>
                        <span>校验时间：${escapeHtml(verifiedAt)}</span>
                        ${error}
                    </div>
                </div>
                <div class="smart-classroom-actions">
                    <button type="button" class="btn btn-outline btn-sm" data-action="edit" data-id="${item.id}">修改</button>
                    <button type="button" class="btn btn-primary btn-sm" data-action="sync" data-id="${item.id}">同步点名</button>
                    <button type="button" class="btn btn-outline btn-sm" data-action="verify" data-id="${item.id}">重新验证</button>
                    <button type="button" class="btn btn-danger btn-sm" data-action="delete" data-id="${item.id}">删除</button>
                </div>
            </article>
        `;
    }).join('');
}

async function refreshCredentials() {
    setBusy(refs.refreshBtn, true, '刷新中');
    try {
        const result = await apiFetch('/api/manage/system/smart-classroom-credentials');
        state.credentials = result.credentials || [];
        renderCredentials();
        await refreshCapabilities();
    } catch (error) {
        showMessage(error.message || '刷新智慧课堂对接状态失败。', 'error');
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
        showMessage('请填写智慧课堂账号和密码。', 'warning');
        return;
    }

    setBusy(refs.saveBtn, true, '正在验证');
    renderAutoSync(null);
    try {
        const result = await apiFetch('/api/manage/system/smart-classroom-credentials', {
            method: 'POST',
            body: payload,
        });
        state.credentials = result.credentials || [];
        refs.password.value = '';
        renderCredentials();
        renderAutoSync(result.auto_sync);
        await refreshCapabilities();
        showMessage(result.message || '智慧课堂账号已保存，并已尝试自动同步点名记录。', 'success');
    } catch (error) {
        showMessage(error.message || '智慧课堂账号验证失败。', 'error');
    } finally {
        setBusy(refs.saveBtn, false);
    }
}

async function verifyCredential(id, button) {
    setBusy(button, true, '验证中');
    renderAutoSync(null);
    try {
        const result = await apiFetch(`/api/manage/system/smart-classroom-credentials/${id}/verify`, {
            method: 'POST',
        });
        state.credentials = result.credentials || [];
        renderCredentials();
        renderAutoSync(result.auto_sync);
        await refreshCapabilities();
        showMessage(result.message || '智慧课堂连接校验完成。', result.status === 'success' ? 'success' : 'warning');
    } catch (error) {
        showMessage(error.message || '重新验证失败。', 'error');
    } finally {
        setBusy(button, false);
    }
}

async function syncSmartClassroom(button) {
    setBusy(button, true, '同步中');
    renderAutoSync(null);
    try {
        const result = await apiFetch('/api/manage/system/smart-classroom-sync', {
            method: 'POST',
        });
        renderAutoSync(result.auto_sync);
        await refreshCapabilities();
        closeSyncModal();
        showMessage(result.message || '智慧课堂点名同步已完成。', result.status === 'failed' ? 'warning' : 'success');
    } catch (error) {
        showMessage(error.message || '智慧课堂点名同步失败。', 'error');
    } finally {
        setBusy(button, false);
    }
}

async function deleteCredential(id, button) {
    if (!window.confirm('确定删除这个智慧课堂对接吗？')) {
        return;
    }
    setBusy(button, true, '删除中');
    try {
        const result = await apiFetch(`/api/manage/system/smart-classroom-credentials/${id}`, {
            method: 'DELETE',
        });
        state.credentials = result.credentials || [];
        renderCredentials();
        renderAutoSync(null);
        await refreshCapabilities();
        showMessage(result.message || '智慧课堂对接已删除。', 'success');
    } catch (error) {
        showMessage(error.message || '删除失败。', 'error');
    } finally {
        setBusy(button, false);
    }
}

function renderProbeResult(result) {
    const response = result.response || {};
    const okText = response.ok ? '成功' : '需要复核';
    refs.probeResultHead.innerHTML = `
        <span>${escapeHtml(okText)}</span>
        <span>HTTP ${escapeHtml(response.status_code || '-')}</span>
        <span>${escapeHtml(response.elapsed_ms || 0)} ms</span>
        <span>${escapeHtml(response.content_type || '未知类型')}</span>
        ${response.truncated ? '<span>已截断</span>' : ''}
    `;
    const bodyText = response.preview_kind === 'json'
        ? JSON.stringify(response.json, null, 2)
        : (response.text_preview || response.preview || '');
    const wrapper = {
        login: result.login || {},
        request: result.request || {},
        response_hint: response.result_hint || response.warning || {},
        response_body: bodyText,
    };
    refs.probeResultBody.textContent = JSON.stringify(wrapper, null, 2);
    refs.probeResult.hidden = false;
}

async function probeSmartRequest(button) {
    const capability = state.activeCapability;
    if (!capability) {
        showMessage('请先选择一个同步功能。', 'warning');
        return;
    }
    let payload;
    try {
        const bodyMode = refs.probeBodyMode.value || 'form';
        payload = {
            provider: 'smart_classroom',
            capability_key: capability.key,
            method: refs.probeMethod.value || 'POST',
            url: refs.probeUrl.value,
            params: parseJsonField(refs.probeParams, '查询参数'),
            headers: parseJsonField(refs.probeHeaders, '请求头'),
            body_mode: bodyMode,
            body: bodyMode === 'raw' ? refs.probeBody.value : parseJsonField(refs.probeBody, '请求载荷'),
        };
    } catch (error) {
        showMessage(error.message, 'warning');
        return;
    }
    setBusy(button, true, '验证中');
    refs.probeResult.hidden = true;
    try {
        const result = await apiFetch('/api/manage/system/integration-request-probe', {
            method: 'POST',
            body: payload,
        });
        renderProbeResult(result);
        showMessage('请求验证完成，已显示返回摘要。', result.response?.ok ? 'success' : 'warning');
    } catch (error) {
        showMessage(error.message || '请求验证失败。', 'error');
    } finally {
        setBusy(button, false);
    }
}

refs.form?.addEventListener('submit', saveCredential);
refs.platformSelect?.addEventListener('change', renderProfile);
refs.refreshBtn?.addEventListener('click', refreshCredentials);
refs.accountManageBtn?.addEventListener('click', openAccountModal);
refs.accountModalClose?.addEventListener('click', closeAccountModal);
refs.accountModal?.addEventListener('click', (event) => {
    if (event.target === refs.accountModal) closeAccountModal();
});
refs.syncAllBtn?.addEventListener('click', (event) => syncSmartClassroom(event.currentTarget));
refs.capabilityRefreshBtn?.addEventListener('click', refreshCapabilities);
refs.capabilityList?.addEventListener('click', (event) => {
    const card = event.target.closest('[data-sync-key]');
    if (!card) return;
    const item = state.capabilities.find((capability) => capability.key === card.dataset.syncKey);
    openSyncModal(item);
});
refs.syncModalRun?.addEventListener('click', (event) => syncSmartClassroom(event.currentTarget));
refs.probeBtn?.addEventListener('click', (event) => probeSmartRequest(event.currentTarget));
refs.syncModalClose?.addEventListener('click', closeSyncModal);
refs.syncModalCancel?.addEventListener('click', closeSyncModal);
refs.syncModal?.addEventListener('click', (event) => {
    if (event.target === refs.syncModal) closeSyncModal();
});
refs.list?.addEventListener('click', (event) => {
    const button = event.target.closest('button[data-action]');
    if (!button) return;
    const id = button.dataset.id;
    if (!id) return;
    if (button.dataset.action === 'edit') {
        const item = state.credentials.find((credential) => String(credential.id) === String(id));
        fillCredentialForm(item);
    } else if (button.dataset.action === 'verify') {
        verifyCredential(id, button);
    } else if (button.dataset.action === 'sync') {
        syncSmartClassroom(button);
    } else if (button.dataset.action === 'delete') {
        deleteCredential(id, button);
    }
});

renderProfile();
renderCredentials();
renderAutoSync(null);
refreshCapabilities();
