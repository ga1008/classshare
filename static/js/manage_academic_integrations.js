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
    profiles: parseJsonScript('academic-profiles-data', []),
    credentials: parseJsonScript('academic-credentials-data', []),
    capabilities: [],
    activeCapability: null,
};

const refs = {
    form: document.getElementById('academic-credential-form'),
    schoolSelect: document.getElementById('academic-school-code'),
    username: document.getElementById('academic-username'),
    password: document.getElementById('academic-password'),
    saveBtn: document.getElementById('academic-save-btn'),
    refreshBtn: document.getElementById('academic-refresh-btn'),
    list: document.getElementById('academic-credential-list'),
    profileSchool: document.getElementById('edu-profile-school'),
    profileLogin: document.getElementById('edu-profile-login'),
    profileMethod: document.getElementById('edu-profile-method'),
    profileNote: document.getElementById('edu-profile-note'),
    autoSyncPanel: document.getElementById('academic-auto-sync-panel'),
    autoSyncTitle: document.getElementById('academic-auto-sync-title'),
    autoSyncMessage: document.getElementById('academic-auto-sync-message'),
    autoSyncStages: document.getElementById('academic-auto-sync-stages'),
    accountManageBtn: document.getElementById('academic-account-manage-btn'),
    accountModal: document.getElementById('academic-account-modal'),
    accountModalClose: document.getElementById('academic-account-modal-close'),
    syncAllBtn: document.getElementById('academic-sync-all-btn'),
    capabilityRefreshBtn: document.getElementById('academic-sync-capability-refresh-btn'),
    capabilityList: document.getElementById('academic-sync-capability-list'),
    syncModal: document.getElementById('academic-sync-modal'),
    syncModalTitle: document.getElementById('academic-sync-modal-title'),
    syncModalDescription: document.getElementById('academic-sync-modal-description'),
    syncModalLast: document.getElementById('academic-sync-modal-last'),
    syncModalState: document.getElementById('academic-sync-modal-state'),
    syncModalEndpoint: document.getElementById('academic-sync-modal-endpoint'),
    syncModalScope: document.getElementById('academic-sync-modal-scope'),
    syncModalParams: document.getElementById('academic-sync-modal-params'),
    syncModalSafe: document.getElementById('academic-sync-modal-safe'),
    syncModalRun: document.getElementById('academic-sync-modal-run'),
    syncModalClose: document.getElementById('academic-sync-modal-close'),
    syncModalCancel: document.getElementById('academic-sync-modal-cancel'),
    probeMethod: document.getElementById('academic-probe-method'),
    probeUrl: document.getElementById('academic-probe-url'),
    probeParams: document.getElementById('academic-probe-params'),
    probeHeaders: document.getElementById('academic-probe-headers'),
    probeBodyMode: document.getElementById('academic-probe-body-mode'),
    probeBody: document.getElementById('academic-probe-body'),
    probeBtn: document.getElementById('academic-sync-modal-probe'),
    probeResult: document.getElementById('academic-probe-result'),
    probeResultHead: document.getElementById('academic-probe-result-head'),
    probeResultBody: document.getElementById('academic-probe-result-body'),
};

const statusLabels = {
    verified: '已验证',
    failed: '验证失败',
    challenge_required: '需要人工处理',
    unavailable: '暂不可用',
    unchecked: '未验证',
};

function currentProfile() {
    const selected = refs.schoolSelect?.value || '';
    return state.profiles.find((item) => item.school_code === selected) || state.profiles[0] || null;
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

function renderProfile() {
    const profile = currentProfile();
    if (!profile) {
        refs.profileSchool.textContent = '-';
        refs.profileLogin.textContent = '-';
        refs.profileMethod.textContent = '-';
        refs.profileNote.textContent = '暂无可用学校适配器。';
        return;
    }
    refs.profileSchool.textContent = profile.school_name || '-';
    refs.profileLogin.textContent = profile.login_url || '-';
    refs.profileMethod.textContent = profile.auth_method === 'password_rsa'
        ? '账号密码 + RSA 加密提交'
        : (profile.auth_method || '-');
    refs.profileNote.textContent = profile.note || '该适配器用于登录校验和后续数据同步。';
}

function badgeClass(status) {
    if (status === 'verified') return 'is-verified';
    if (status === 'failed' || status === 'unavailable') return 'is-failed';
    if (status === 'challenge_required') return 'is-challenge';
    return 'is-unchecked';
}

function formatStatusTime(item) {
    return formatDateTime(item.last_verified_at || item.last_status_at || item.updated_at || '');
}

function autoSyncStatusLabel(status) {
    if (status === 'success') return '已完成';
    if (status === 'partial_success') return '部分完成';
    if (status === 'failed') return '未完成';
    if (status === 'missing_credential') return '缺少凭据';
    if (status === 'no_current_semester') return '缺少当前学期';
    return status || '未知';
}

function countLabel(key, value) {
    const labels = {
        course_count: '课程',
        course_sync_item_count: '课表记录',
        created_count: '新增',
        updated_count: '更新',
        schedule_item_count: '课表条目',
        occurrence_count: '真实课次',
        offering_update_count: '课堂更新',
        teaching_class_count: '教学班',
        touched_class_count: '平台班级',
        classes_created: '新增班级',
        classes_updated: '更新班级',
        students_created: '新增学生',
        students_updated: '更新学生',
        students_moved: '转班学生',
        memberships_upserted: '名单关系',
        membership_count: '名单关系',
        roster_student_count: '教务名单',
        class_conflicts: '班级差异',
        student_conflicts: '学生差异',
        contact_conflicts: '联系方式差异',
        stale_students: '待复核学生',
        invigilation_count: '监考安排',
        place_count: '教学场地',
        event_created_count: '新增日历',
        event_updated_count: '更新日历',
        notification_count: '待办提醒',
        stale_count: '待复核',
        semester_count: '学期',
    };
    return `${labels[key] || key} ${Number(value || 0)}`;
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
        // Keep the user-facing error below simple.
    }
    throw new Error(`${label} 必须是 JSON 对象。`);
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
        refs.capabilityList.innerHTML = '<div class="edu-empty">暂无可同步功能。请先在账号管理中保存并验证教务账号。</div>';
        return;
    }
    refs.capabilityList.innerHTML = state.capabilities.map((item) => {
        const syncedAt = formatDateTime(item.last_synced_at);
        const statusMeta = item.has_synced
            ? `<span>已同步</span>${syncedAt ? `<span>${escapeHtml(syncedAt)}</span>` : ''}`
            : '<span class="is-muted">未同步</span>';
        return `
            <button type="button" class="edu-sync-card" data-sync-key="${escapeHtml(item.key || '')}">
                <div class="edu-sync-card-meta">${statusMeta}</div>
                <h4>${escapeHtml(item.label || '同步功能')}</h4>
                <p>${escapeHtml(item.description || '')}</p>
                <div class="edu-sync-card-meta">${renderStats(item)}</div>
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
        const result = await apiFetch('/api/manage/system/academic-sync-capabilities');
        state.capabilities = result.capabilities || [];
        renderCapabilities();
    } catch (error) {
        refs.capabilityList.innerHTML = '<div class="edu-empty">读取同步功能失败。</div>';
        showMessage(error.message || '读取教务同步功能失败。', 'error');
    } finally {
        setBusy(refs.capabilityRefreshBtn, false);
    }
}

function renderAutoSync(autoSync) {
    if (!refs.autoSyncPanel || !refs.autoSyncMessage || !refs.autoSyncStages) return;
    if (!autoSync) {
        refs.autoSyncPanel.hidden = true;
        return;
    }
    refs.autoSyncPanel.hidden = false;
    if (refs.autoSyncTitle) {
        refs.autoSyncTitle.textContent = `教务数据自动同步：${autoSyncStatusLabel(autoSync.status)}`;
    }
    refs.autoSyncMessage.textContent = autoSync.message || '系统已完成自动同步流程。';
    const stages = Array.isArray(autoSync.stages) ? autoSync.stages : [];
    refs.autoSyncStages.innerHTML = stages.map((stage) => {
        const counts = stage.counts && typeof stage.counts === 'object' ? stage.counts : {};
        const countItems = Object.entries(counts)
            .filter(([, value]) => Number(value || 0) > 0)
            .map(([key, value]) => `<span>${escapeHtml(countLabel(key, value))}</span>`)
            .join('');
        const warnings = Array.isArray(stage.warnings) && stage.warnings.length
            ? `<small>复核：${escapeHtml(stage.warnings.slice(0, 2).join('；'))}</small>`
            : '';
        return `
            <article class="edu-auto-sync-stage">
                <strong>${escapeHtml(stage.label || stage.key || '同步任务')} · ${escapeHtml(autoSyncStatusLabel(stage.status))}</strong>
                ${stage.message ? `<small>${escapeHtml(stage.message)}</small>` : ''}
                ${countItems ? `<div class="edu-auto-sync-counts">${countItems}</div>` : ''}
                ${warnings}
            </article>
        `;
    }).join('');
}

function fillCredentialForm(item) {
    if (!item) return;
    refs.schoolSelect.value = item.school_code || refs.schoolSelect.value;
    refs.username.value = item.username || '';
    refs.password.value = '';
    renderProfile();
    refs.password?.focus({ preventScroll: true });
}

function renderCredentials() {
    if (!refs.list) return;
    if (!state.credentials.length) {
        refs.list.innerHTML = '<div class="edu-empty">尚未保存教务系统账号。填写并通过校验后，可同步课程、班级学生、校历、监考和教学场地。</div>';
        return;
    }

    refs.list.innerHTML = state.credentials.map((item) => {
        const status = item.last_status || 'unchecked';
        const error = item.last_error
            ? `<span>最近错误：${escapeHtml(item.last_error)}</span>`
            : '';
        const method = item.access_method?.auth_method === 'password_rsa'
            ? '账号密码 + RSA'
            : (item.access_method?.auth_method || item.auth_method || '-');
        const verifiedAt = formatStatusTime(item) || '暂无';
        return `
            <article class="edu-credential" data-credential-id="${item.id}">
                <div>
                    <div>
                        <span class="edu-badge ${badgeClass(status)}">${escapeHtml(statusLabels[status] || status)}</span>
                    </div>
                    <h4>${escapeHtml(item.school_name || '-')}</h4>
                    <div class="edu-credential-meta">
                        <span>账号：${escapeHtml(item.username || '-')}</span>
                        <span>密码：<span class="edu-password-mask">${item.has_password ? '••••••••' : '未保存'}</span></span>
                        <span>方式：${escapeHtml(method)}</span>
                        <span>校验时间：${escapeHtml(verifiedAt)}</span>
                        ${error}
                    </div>
                </div>
                <div class="edu-credential-actions">
                    <button type="button" class="btn btn-outline btn-sm" data-action="edit" data-id="${item.id}">修改</button>
                    <button type="button" class="btn btn-primary btn-sm" data-action="sync" data-id="${item.id}">同步数据</button>
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
        const result = await apiFetch('/api/manage/system/academic-credentials');
        state.credentials = result.credentials || [];
        renderCredentials();
        await refreshCapabilities();
    } catch (error) {
        showMessage(error.message || '刷新教务对接状态失败。', 'error');
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
        showMessage('请填写教务系统账号和密码。', 'warning');
        return;
    }

    setBusy(refs.saveBtn, true, '正在验证');
    renderAutoSync(null);
    try {
        const result = await apiFetch('/api/manage/system/academic-credentials', {
            method: 'POST',
            body: payload,
        });
        state.credentials = result.credentials || [];
        refs.password.value = '';
        renderCredentials();
        renderAutoSync(result.auto_sync);
        await refreshCapabilities();
        showMessage(result.message || '教务系统账号已保存，并已尝试自动同步教务数据。', 'success');
    } catch (error) {
        showMessage(error.message || '教务系统账号验证失败。', 'error');
    } finally {
        setBusy(refs.saveBtn, false);
    }
}

async function verifyCredential(id, button) {
    setBusy(button, true, '验证中');
    renderAutoSync(null);
    try {
        const result = await apiFetch(`/api/manage/system/academic-credentials/${id}/verify`, {
            method: 'POST',
        });
        state.credentials = result.credentials || [];
        renderCredentials();
        renderAutoSync(result.auto_sync);
        await refreshCapabilities();
        showMessage(result.message || '教务系统连接校验完成。', result.status === 'success' ? 'success' : 'warning');
    } catch (error) {
        showMessage(error.message || '重新验证失败。', 'error');
    } finally {
        setBusy(button, false);
    }
}

async function syncAcademicData(button) {
    setBusy(button, true, '同步中');
    renderAutoSync(null);
    try {
        const result = await apiFetch('/api/manage/system/academic-sync', {
            method: 'POST',
        });
        renderAutoSync(result.auto_sync);
        await refreshCapabilities();
        showMessage(result.message || '教务系统数据同步已完成。', result.status === 'failed' ? 'warning' : 'success');
    } catch (error) {
        showMessage(error.message || '教务系统数据同步失败。', 'error');
    } finally {
        setBusy(button, false);
    }
}

async function deleteCredential(id, button) {
    if (!window.confirm('确定删除这个教务系统对接吗？')) {
        return;
    }
    setBusy(button, true, '删除中');
    try {
        const result = await apiFetch(`/api/manage/system/academic-credentials/${id}`, {
            method: 'DELETE',
        });
        state.credentials = result.credentials || [];
        renderCredentials();
        await refreshCapabilities();
        showMessage(result.message || '教务系统对接已删除。', 'success');
    } catch (error) {
        showMessage(error.message || '删除失败。', 'error');
    } finally {
        setBusy(button, false);
    }
}

async function syncAcademicCapability(button) {
    const capability = state.activeCapability;
    if (!capability?.endpoint) {
        showMessage('未找到可执行的同步接口。', 'warning');
        return;
    }
    setBusy(button, true, '同步中');
    try {
        const result = await apiFetch(capability.endpoint, { method: capability.method || 'POST' });
        await refreshCapabilities();
        const stage = {
            key: capability.key,
            label: capability.label,
            status: result.status || 'success',
            message: result.message || `${capability.label || '同步'}已完成。`,
            counts: result.counts || result,
            warnings: result.warnings || [],
        };
        renderAutoSync({
            status: stage.status,
            message: stage.message,
            stages: [stage],
        });
        closeSyncModal();
        showMessage(stage.message, stage.status === 'failed' ? 'warning' : 'success');
    } catch (error) {
        showMessage(error.message || '同步失败。', 'error');
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

async function probeAcademicRequest(button) {
    const capability = state.activeCapability;
    if (!capability) {
        showMessage('请先选择一个同步功能。', 'warning');
        return;
    }
    let payload;
    try {
        const bodyMode = refs.probeBodyMode.value || 'form';
        payload = {
            provider: 'academic',
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

refs.schoolSelect?.addEventListener('change', renderProfile);
refs.refreshBtn?.addEventListener('click', refreshCredentials);
refs.form?.addEventListener('submit', saveCredential);
refs.accountManageBtn?.addEventListener('click', openAccountModal);
refs.accountModalClose?.addEventListener('click', closeAccountModal);
refs.accountModal?.addEventListener('click', (event) => {
    if (event.target === refs.accountModal) closeAccountModal();
});
refs.syncAllBtn?.addEventListener('click', (event) => syncAcademicData(event.currentTarget));
refs.capabilityRefreshBtn?.addEventListener('click', refreshCapabilities);
refs.capabilityList?.addEventListener('click', (event) => {
    const card = event.target.closest('[data-sync-key]');
    if (!card) return;
    const item = state.capabilities.find((capability) => capability.key === card.dataset.syncKey);
    openSyncModal(item);
});
refs.syncModalRun?.addEventListener('click', (event) => syncAcademicCapability(event.currentTarget));
refs.probeBtn?.addEventListener('click', (event) => probeAcademicRequest(event.currentTarget));
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
        syncAcademicData(button);
    } else if (button.dataset.action === 'delete') {
        deleteCredential(id, button);
    }
});

renderProfile();
renderCredentials();
refreshCapabilities();
