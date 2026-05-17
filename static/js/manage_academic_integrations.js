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
    refs.profileMethod.textContent = profile.auth_method === 'password_rsa' ? '账号密码 + RSA 加密提交' : profile.auth_method;
    refs.profileNote.textContent = profile.note || '该适配器用于登录校验和后续数据同步。';
}

function badgeClass(status) {
    if (status === 'verified') return 'is-verified';
    if (status === 'failed' || status === 'unavailable') return 'is-failed';
    if (status === 'challenge_required') return 'is-challenge';
    return 'is-unchecked';
}

function formatStatusTime(item) {
    return item.last_verified_at || item.last_status_at || item.updated_at || '-';
}

function renderCredentials() {
    if (!refs.list) return;
    if (!state.credentials.length) {
        refs.list.innerHTML = '<div class="edu-empty">尚未保存教务系统对接。填写账号密码并通过校验后，这里会显示可复用的访问方法。</div>';
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
        return `
            <article class="edu-credential" data-credential-id="${item.id}">
                <div>
                    <div>
                        <span class="edu-badge ${badgeClass(status)}">${escapeHtml(statusLabels[status] || status)}</span>
                    </div>
                    <h4>${escapeHtml(item.school_name || '-')}</h4>
                    <div class="edu-credential-meta">
                        <span>账号：${escapeHtml(item.username || '-')}</span>
                        <span>方式：${escapeHtml(method)}</span>
                        <span>校验时间：${escapeHtml(formatStatusTime(item))}</span>
                        ${error}
                    </div>
                </div>
                <div class="edu-credential-actions">
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
    try {
        const result = await apiFetch('/api/manage/system/academic-credentials', {
            method: 'POST',
            body: payload,
        });
        state.credentials = result.credentials || [];
        refs.password.value = '';
        renderCredentials();
        showMessage(result.message || '教务系统账号已保存。', 'success');
    } catch (error) {
        showMessage(error.message || '教务系统账号验证失败。', 'error');
    } finally {
        setBusy(refs.saveBtn, false);
    }
}

async function verifyCredential(id, button) {
    setBusy(button, true, '验证中');
    try {
        const result = await apiFetch(`/api/manage/system/academic-credentials/${id}/verify`, {
            method: 'POST',
        });
        state.credentials = result.credentials || [];
        renderCredentials();
        showMessage(result.message || '教务系统连接校验完成。', result.status === 'success' ? 'success' : 'warning');
    } catch (error) {
        showMessage(error.message || '重新验证失败。', 'error');
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
        showMessage(result.message || '教务系统对接已删除。', 'success');
    } catch (error) {
        showMessage(error.message || '删除失败。', 'error');
    } finally {
        setBusy(button, false);
    }
}

refs.schoolSelect?.addEventListener('change', renderProfile);
refs.refreshBtn?.addEventListener('click', refreshCredentials);
refs.form?.addEventListener('submit', saveCredential);
refs.list?.addEventListener('click', (event) => {
    const button = event.target.closest('button[data-action]');
    if (!button) return;
    const id = button.dataset.id;
    if (!id) return;
    if (button.dataset.action === 'verify') {
        verifyCredential(id, button);
    } else if (button.dataset.action === 'delete') {
        deleteCredential(id, button);
    }
});

renderProfile();
renderCredentials();
