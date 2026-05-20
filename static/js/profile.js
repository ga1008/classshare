import { apiFetch } from './api.js';
import { escapeHtml, showToast } from './ui.js';

const root = document.querySelector('[data-profile-root]');
const contextNode = document.getElementById('profile-context-json');

function readContext() {
    if (!contextNode) {
        return {};
    }
    try {
        return JSON.parse(contextNode.textContent || '{}');
    } catch {
        return {};
    }
}

const context = readContext();

function setButtonBusy(button, busy, label = null) {
    if (!button) {
        return;
    }
    if (!button.dataset.originalLabel) {
        button.dataset.originalLabel = button.textContent.trim();
    }
    button.disabled = Boolean(busy);
    button.classList.toggle('loading', Boolean(busy));
    if (label) {
        button.textContent = label;
    } else if (!busy) {
        button.textContent = button.dataset.originalLabel;
    }
}

function renderChart(element, chartConfig) {
    if (!element || !chartConfig || typeof window.echarts === 'undefined') {
        return;
    }
    const labels = Array.isArray(chartConfig.labels) ? chartConfig.labels : [];
    const values = Array.isArray(chartConfig.values) ? chartConfig.values.map((value) => Number(value || 0)) : [];
    const chart = window.echarts.init(element);
    const hasData = values.some((value) => value > 0);

    if (chartConfig.type === 'bar') {
        chart.setOption({
            grid: { left: 38, right: 18, top: 24, bottom: 34 },
            tooltip: { trigger: 'axis' },
            xAxis: { type: 'category', data: labels, axisTick: { show: false } },
            yAxis: { type: 'value', minInterval: 1 },
            series: [{
                type: 'bar',
                data: values,
                barMaxWidth: 34,
                itemStyle: {
                    borderRadius: [8, 8, 0, 0],
                    color: '#14b8a6',
                },
            }],
        });
    } else {
        chart.setOption({
            tooltip: { trigger: 'item' },
            legend: { bottom: 0, left: 'center' },
            color: ['#4f46e5', '#14b8a6', '#f59e0b', '#0ea5e9', '#ef4444'],
            series: [{
                type: 'pie',
                radius: ['48%', '70%'],
                center: ['50%', '43%'],
                avoidLabelOverlap: true,
                label: { formatter: '{b}' },
                data: hasData
                    ? labels.map((label, index) => ({ name: label, value: values[index] || 0 }))
                    : [{ name: '暂无数据', value: 1, itemStyle: { color: '#cbd5e1' } }],
            }],
        });
    }

    window.addEventListener('resize', () => chart.resize(), { passive: true });
}

function initCharts() {
    const chartConfigs = new Map((context.overview?.charts || []).map((chart) => [chart.id, chart]));
    document.querySelectorAll('[data-profile-chart]').forEach((element) => {
        renderChart(element, chartConfigs.get(element.dataset.profileChart));
    });
}

function updateAvatarPreview(url) {
    if (!url) {
        return;
    }
    const cacheBustedUrl = `${url}${url.includes('?') ? '&' : '?'}t=${Date.now()}`;
    document.querySelectorAll('.profile-identity__avatar, #profile-avatar-preview, .profile-entry-button__avatar')
        .forEach((image) => {
            image.src = cacheBustedUrl;
        });
}

function setText(selector, value) {
    document.querySelectorAll(selector).forEach((element) => {
        element.textContent = value;
    });
}

function updateProfileChrome(profile = {}) {
    if (!profile || typeof profile !== 'object') {
        return;
    }
    const displayRole = profile.display_role || profile.nickname || profile.role_label || '未设置';
    setText('[data-profile-display-role]', displayRole);
    setText('[data-profile-mood-display]', profile.today_mood || '未设置');

    const completion = profile.completion || {};
    if (Number.isFinite(Number(completion.percent))) {
        setText('[data-profile-completion-value]', String(completion.percent));
        document.querySelectorAll('[data-profile-completion-bar]').forEach((bar) => {
            bar.style.width = `${completion.percent}%`;
        });
    }

    const homepageChip = document.querySelector('[data-profile-homepage-chip]');
    if (homepageChip) {
        if (profile.homepage_url) {
            homepageChip.href = profile.homepage_url;
            homepageChip.hidden = false;
        } else {
            homepageChip.hidden = true;
        }
    }
}

function initAvatarUpload() {
    const input = document.getElementById('profile-avatar-input');
    const pickButton = document.querySelector('[data-profile-avatar-pick]');
    if (!input || !pickButton) {
        return;
    }

    pickButton.addEventListener('click', () => input.click());
    input.addEventListener('change', async () => {
        const file = input.files?.[0];
        if (!file) {
            return;
        }
        const formData = new FormData();
        formData.append('file', file);
        setButtonBusy(pickButton, true, '上传中');
        try {
            const response = await apiFetch('/api/profile/avatar', {
                method: 'POST',
                body: formData,
            });
            updateAvatarPreview(response.profile?.avatar_url || '/api/profile/avatar');
            updateProfileChrome(response.profile);
            showToast(response.message || '头像已更新', 'success');
        } catch (error) {
            showToast(error.message || '头像上传失败', 'error');
        } finally {
            input.value = '';
            setButtonBusy(pickButton, false);
        }
    });
}

function initBasicForm() {
    const form = document.getElementById('profile-basic-form');
    if (!form) {
        return;
    }
    form.addEventListener('submit', async (event) => {
        event.preventDefault();
        const submitButton = form.querySelector('button[type="submit"]');
        const payload = Object.fromEntries(new FormData(form).entries());
        setButtonBusy(submitButton, true, '保存中');
        try {
            const response = await apiFetch('/api/profile/basic', {
                method: 'PUT',
                body: payload,
            });
            updateProfileChrome(response.profile);
            showToast(response.message || '基础信息已保存', 'success');
        } catch (error) {
            showToast(error.message || '保存失败', 'error');
        } finally {
            setButtonBusy(submitButton, false);
        }
    });
}

function applyMoodValue(mood) {
    const input = document.getElementById('profile-mood-input');
    if (input) {
        input.value = mood;
    }
    document.querySelectorAll('[data-profile-mood]').forEach((button) => {
        button.classList.toggle('is-active', button.dataset.profileMood === mood);
    });
}

async function saveMood(mood, button = null) {
    const normalizedMood = String(mood || '').trim();
    setButtonBusy(button, true);
    try {
        const response = await apiFetch('/api/profile/mood', {
            method: 'PUT',
            body: { mood: normalizedMood },
        });
        applyMoodValue(response.profile?.today_mood || normalizedMood);
        updateProfileChrome(response.profile || { today_mood: normalizedMood });
        showToast(response.message || '今日心情已更新', 'success');
    } catch (error) {
        showToast(error.message || '心情保存失败', 'error');
    } finally {
        setButtonBusy(button, false);
    }
}

function initMoodEditor() {
    document.querySelectorAll('[data-profile-mood]').forEach((button) => {
        button.addEventListener('click', () => {
            const mood = button.dataset.profileMood || '';
            applyMoodValue(mood);
            void saveMood(mood, button);
        });
    });
    const saveButton = document.getElementById('profile-mood-save');
    const input = document.getElementById('profile-mood-input');
    if (saveButton && input) {
        saveButton.addEventListener('click', () => {
            void saveMood(input.value, saveButton);
        });
        input.addEventListener('keydown', (event) => {
            if (event.key === 'Enter') {
                event.preventDefault();
                void saveMood(input.value, saveButton);
            }
        });
    }
}

function initPasswordForm() {
    const form = document.getElementById('profile-password-form');
    if (!form) {
        return;
    }
    form.addEventListener('submit', async (event) => {
        event.preventDefault();
        const submitButton = form.querySelector('button[type="submit"]');
        const payload = Object.fromEntries(new FormData(form).entries());
        setButtonBusy(submitButton, true, '更新中');
        try {
            const response = await apiFetch('/api/profile/password', {
                method: 'PUT',
                body: payload,
            });
            form.reset();
            showToast(response.message || '密码已更新', 'success');
        } catch (error) {
            showToast(error.message || '密码更新失败', 'error');
        } finally {
            setButtonBusy(submitButton, false);
        }
    });
}

const emailState = {
    configs: [],
    activeId: null,
};

const EMAIL_PROVIDER_PRESETS = {
    qq: {
        label: 'QQ邮箱',
        domains: ['qq.com'],
        smtp_host: 'smtp.qq.com',
        smtp_port: 465,
        smtp_security: 'ssl',
        imap_host: 'imap.qq.com',
        imap_port: 993,
        imap_security: 'ssl',
        per_minute_limit: 20,
        daily_limit: 200,
        secret_label: 'QQ邮箱授权码',
    },
    netease_163: {
        label: '网易163邮箱',
        domains: ['163.com'],
        smtp_host: 'smtp.163.com',
        smtp_port: 465,
        smtp_security: 'ssl',
        imap_host: 'imap.163.com',
        imap_port: 993,
        imap_security: 'ssl',
        per_minute_limit: 20,
        daily_limit: 200,
        secret_label: '客户端授权密码',
    },
    netease_126: {
        label: '网易126邮箱',
        domains: ['126.com'],
        smtp_host: 'smtp.126.com',
        smtp_port: 465,
        smtp_security: 'ssl',
        imap_host: 'imap.126.com',
        imap_port: 993,
        imap_security: 'ssl',
        per_minute_limit: 20,
        daily_limit: 200,
        secret_label: '客户端授权密码',
    },
    netease_yeah: {
        label: '网易yeah.net邮箱',
        domains: ['yeah.net'],
        smtp_host: 'smtp.yeah.net',
        smtp_port: 465,
        smtp_security: 'ssl',
        imap_host: 'imap.yeah.net',
        imap_port: 993,
        imap_security: 'ssl',
        per_minute_limit: 20,
        daily_limit: 200,
        secret_label: '客户端授权密码',
    },
    sina: {
        label: '新浪邮箱',
        domains: ['sina.com', 'sina.cn'],
        smtp_host: 'smtp.sina.com',
        smtp_port: 465,
        smtp_security: 'ssl',
        imap_host: 'imap.sina.com',
        imap_port: 993,
        imap_security: 'ssl',
        per_minute_limit: 20,
        daily_limit: 200,
        secret_label: '邮箱授权码',
    },
    sohu: {
        label: '搜狐邮箱',
        domains: ['sohu.com'],
        smtp_host: 'smtp.sohu.com',
        smtp_port: 465,
        smtp_security: 'ssl',
        imap_host: 'imap.sohu.com',
        imap_port: 993,
        imap_security: 'ssl',
        per_minute_limit: 20,
        daily_limit: 200,
        secret_label: '邮箱授权码',
    },
    aliyun: {
        label: '阿里邮箱',
        domains: ['aliyun.com'],
        smtp_host: 'smtp.aliyun.com',
        smtp_port: 465,
        smtp_security: 'ssl',
        imap_host: 'imap.aliyun.com',
        imap_port: 993,
        imap_security: 'ssl',
        per_minute_limit: 20,
        daily_limit: 200,
        secret_label: '邮箱密码或客户端授权码',
    },
};

function normalizedEmailDomain(email) {
    const parts = String(email || '').trim().toLowerCase().split('@');
    return parts.length === 2 ? parts[1] : '';
}

function providerForValue(value) {
    const normalized = String(value || '').trim().toLowerCase();
    if (!normalized) {
        return '';
    }
    const hostOrDomain = normalized.includes('@') ? normalizedEmailDomain(normalized) : normalized;
    return Object.entries(EMAIL_PROVIDER_PRESETS).find(([, preset]) => (
        preset.domains.includes(hostOrDomain)
        || preset.smtp_host === normalized
        || preset.imap_host === normalized
    ))?.[0] || '';
}

function inferEmailProvider(config = {}) {
    if (config.provider === 'custom') {
        return 'custom';
    }
    if (config.provider && EMAIL_PROVIDER_PRESETS[config.provider]) {
        return config.provider;
    }
    return providerForValue(config.from_email)
        || providerForValue(config.smtp_host)
        || providerForValue(config.imap_host)
        || providerForValue(config.smtp_username)
        || providerForValue(config.imap_username)
        || 'custom';
}

function deriveProviderEmail(provider, form = null) {
    const currentEmail = String(form?.querySelector('[name="from_email"]')?.value || context.profile?.email || '').trim();
    const preset = EMAIL_PROVIDER_PRESETS[provider];
    if (!preset) {
        return currentEmail;
    }
    if (preset.domains.includes(normalizedEmailDomain(currentEmail))) {
        return currentEmail;
    }
    if (provider === 'qq') {
        const qqNumber = String(context.profile?.qq || '').replace(/\D/g, '');
        if (qqNumber) {
            return `${qqNumber}@qq.com`;
        }
    }
    return currentEmail;
}

function setManagedEmailFields(form, provider, { fillPreset = false } = {}) {
    const preset = EMAIL_PROVIDER_PRESETS[provider];
    ['smtp_host', 'smtp_port', 'imap_host', 'imap_port', 'smtp_username', 'imap_username'].forEach((name) => {
        const field = form.querySelector(`[name="${name}"]`);
        if (field) {
            field.readOnly = Boolean(preset);
        }
    });
    ['smtp_security', 'imap_security'].forEach((name) => {
        const field = form.querySelector(`[name="${name}"]`);
        if (field) {
            field.classList.toggle('is-managed', Boolean(preset));
            field.setAttribute('aria-readonly', preset ? 'true' : 'false');
        }
    });
    form.classList.toggle('is-provider-managed', Boolean(preset));

    const hint = form.querySelector('[data-profile-email-provider-hint]');
    const smtpPassword = form.querySelector('[name="smtp_password"]');
    const imapPassword = form.querySelector('[name="imap_password"]');
    if (!preset) {
        if (hint) {
            hint.textContent = '自定义服务器需要手动填写 SMTP/IMAP 参数。';
        }
        if (smtpPassword) {
            smtpPassword.placeholder = '邮箱密码或授权码';
        }
        if (imapPassword) {
            imapPassword.placeholder = '留空则沿用发信密码';
        }
        form.dataset.emailProvider = 'custom';
        return;
    }

    const providerEmail = deriveProviderEmail(provider, form);
    if (hint) {
        hint.textContent = `${preset.label}已自动填入服务器参数，只需填写邮箱和${preset.secret_label}。`;
    }
    if (smtpPassword) {
        smtpPassword.placeholder = preset.secret_label;
    }
    if (imapPassword) {
        imapPassword.placeholder = `留空则沿用${preset.secret_label}`;
    }
    form.dataset.emailProvider = provider;
    if (!fillPreset) {
        return;
    }
    setFieldValue(form, 'label', preset.label);
    setFieldValue(form, 'from_email', providerEmail);
    setFieldValue(form, 'smtp_host', preset.smtp_host);
    setFieldValue(form, 'smtp_port', preset.smtp_port);
    setFieldValue(form, 'smtp_security', preset.smtp_security);
    setFieldValue(form, 'smtp_username', providerEmail);
    setFieldValue(form, 'imap_host', preset.imap_host);
    setFieldValue(form, 'imap_port', preset.imap_port);
    setFieldValue(form, 'imap_security', preset.imap_security);
    setFieldValue(form, 'imap_username', providerEmail);
    setFieldValue(form, 'per_minute_limit', preset.per_minute_limit);
    setFieldValue(form, 'daily_limit', preset.daily_limit);
}

function readEmailPayload(form) {
    const data = Object.fromEntries(new FormData(form).entries());
    const provider = String(form.querySelector('[name="provider"]')?.value || form.dataset.emailProvider || '').trim();
    return {
        ...data,
        provider,
        enabled: Boolean(form.querySelector('[name="enabled"]')?.checked),
        is_default: Boolean(form.querySelector('[name="is_default"]')?.checked),
        smtp_port: Number(data.smtp_port || 0),
        imap_port: Number(data.imap_port || 0),
        per_minute_limit: Number(data.per_minute_limit || 25),
        daily_limit: Number(data.daily_limit || 300),
    };
}

function emptyEmailConfig() {
    const provider = providerForValue(context.profile?.email) || 'qq';
    const preset = EMAIL_PROVIDER_PRESETS[provider] || {};
    const fromEmail = deriveProviderEmail(provider);
    return {
        id: '',
        provider,
        label: preset.label || '默认邮箱',
        from_email: fromEmail,
        from_name: context.profile?.name || '',
        smtp_host: preset.smtp_host || '',
        smtp_port: preset.smtp_port || 465,
        smtp_security: preset.smtp_security || 'ssl',
        smtp_username: fromEmail,
        imap_host: preset.imap_host || '',
        imap_port: preset.imap_port || 993,
        imap_security: preset.imap_security || 'ssl',
        imap_username: fromEmail,
        enabled: true,
        is_default: true,
        per_minute_limit: preset.per_minute_limit || 25,
        daily_limit: preset.daily_limit || 300,
    };
}

function setFieldValue(form, name, value) {
    const field = form.querySelector(`[name="${name}"]`);
    if (!field) {
        return;
    }
    if (field.type === 'checkbox') {
        field.checked = Boolean(value);
        return;
    }
    field.value = value ?? '';
}

function fillEmailForm(config = emptyEmailConfig()) {
    const form = document.getElementById('profile-email-form');
    if (!form) {
        return;
    }
    emailState.activeId = config.id ? Number(config.id) : null;
    setFieldValue(form, 'config_id', config.id || '');
    [
        'provider',
        'label',
        'from_email',
        'from_name',
        'smtp_host',
        'smtp_port',
        'smtp_security',
        'smtp_username',
        'imap_host',
        'imap_port',
        'imap_security',
        'imap_username',
        'enabled',
        'is_default',
        'per_minute_limit',
        'daily_limit',
    ].forEach((name) => setFieldValue(form, name, config[name]));
    setFieldValue(form, 'smtp_password', '');
    setFieldValue(form, 'imap_password', '');
    const deleteButton = form.querySelector('[data-profile-email-delete]');
    if (deleteButton) {
        deleteButton.hidden = !config.id;
    }
    const provider = inferEmailProvider(config);
    setFieldValue(form, 'provider', provider);
    form.dataset.emailProvider = provider;
    setManagedEmailFields(form, provider);
}

function formatEmailStatus(config) {
    const status = String(config?.last_status || 'unchecked');
    if (status === 'ok') {
        return '连接正常';
    }
    if (status === 'failed') {
        return '连接失败';
    }
    return '未检测';
}

function renderEmailConfigs() {
    const list = document.getElementById('profile-email-config-list');
    if (!list) {
        return;
    }
    if (!emailState.configs.length) {
        list.innerHTML = `
            <div class="profile-email-empty">
                <strong>尚未配置邮箱</strong>
                <span>保存一个 SMTP 配置后，重要通知会进入邮件队列。</span>
            </div>
        `;
        return;
    }
    list.innerHTML = emailState.configs.map((config) => {
        const stats = config.stats || {};
        const statusClass = String(config.last_status || 'unchecked').replace(/[^a-z0-9_-]/gi, '').toLowerCase() || 'unchecked';
        return `
            <button type="button" class="profile-email-card ${Number(config.id) === Number(emailState.activeId) ? 'is-active' : ''}" data-profile-email-select="${Number(config.id)}">
                <span class="profile-email-card__top">
                    <strong>${escapeHtml(config.label || config.from_email)}</strong>
                    <em>${config.enabled ? '启用' : '停用'}${config.is_default ? ' / 默认' : ''}</em>
                </span>
                <span>${escapeHtml(config.from_email || '')}</span>
                <span class="profile-email-card__meta">
                    <b class="profile-email-status is-${escapeHtml(statusClass)}">${escapeHtml(formatEmailStatus(config))}</b>
                    <small>已发 ${Number(stats.sent || 0)} / 队列 ${Number(stats.queued || 0)} / 失败 ${Number(stats.failed || 0)}</small>
                </span>
            </button>
        `;
    }).join('');
}

async function loadEmailConfigs({ selectFirst = true } = {}) {
    const response = await apiFetch('/api/profile/email-configs', { silent: true });
    emailState.configs = response.configs || [];
    if (selectFirst && !emailState.activeId && emailState.configs.length) {
        emailState.activeId = Number(emailState.configs[0].id);
    }
    renderEmailConfigs();
    const active = emailState.configs.find((config) => Number(config.id) === Number(emailState.activeId));
    fillEmailForm(active || emptyEmailConfig());
}

function initEmailConfigPanel() {
    const form = document.getElementById('profile-email-form');
    const list = document.getElementById('profile-email-config-list');
    if (!form || !list) {
        return;
    }

    loadEmailConfigs().catch((error) => {
        showToast(error.message || '邮箱配置加载失败', 'error');
        fillEmailForm(emptyEmailConfig());
    });

    document.querySelector('[data-profile-email-new]')?.addEventListener('click', () => {
        emailState.activeId = null;
        fillEmailForm(emptyEmailConfig());
        renderEmailConfigs();
    });

    form.querySelector('[name="provider"]')?.addEventListener('change', (event) => {
        const provider = event.currentTarget.value || 'custom';
        setManagedEmailFields(form, provider, { fillPreset: provider !== 'custom' });
        renderEmailConfigs();
        if (provider !== 'custom') {
            const label = EMAIL_PROVIDER_PRESETS[provider]?.label || '邮箱';
            showToast(`已切换为${label}，服务器参数已自动带入。`, 'success');
        }
    });

    form.querySelector('[name="from_email"]')?.addEventListener('input', () => {
        const providerSelect = form.querySelector('[name="provider"]');
        let provider = String(providerSelect?.value || form.dataset.emailProvider || '').trim();
        const fromEmail = String(form.querySelector('[name="from_email"]')?.value || '').trim();
        const inferredProvider = providerForValue(fromEmail);
        if (inferredProvider && inferredProvider !== provider) {
            provider = inferredProvider;
            if (providerSelect) {
                providerSelect.value = provider;
            }
            setManagedEmailFields(form, provider, { fillPreset: true });
        }
        if (!EMAIL_PROVIDER_PRESETS[provider]) {
            return;
        }
        setFieldValue(form, 'smtp_username', fromEmail);
        setFieldValue(form, 'imap_username', fromEmail);
    });

    document.querySelector('[data-profile-email-refresh]')?.addEventListener('click', async (event) => {
        const button = event.currentTarget;
        setButtonBusy(button, true, '刷新中');
        try {
            await loadEmailConfigs({ selectFirst: false });
            showToast('邮箱状态已刷新', 'success');
        } catch (error) {
            showToast(error.message || '刷新失败', 'error');
        } finally {
            setButtonBusy(button, false);
        }
    });

    list.addEventListener('click', (event) => {
        const button = event.target.closest('[data-profile-email-select]');
        if (!button) {
            return;
        }
        emailState.activeId = Number(button.dataset.profileEmailSelect);
        const config = emailState.configs.find((item) => Number(item.id) === Number(emailState.activeId));
        fillEmailForm(config || emptyEmailConfig());
        renderEmailConfigs();
    });

    form.addEventListener('submit', async (event) => {
        event.preventDefault();
        const submitButton = form.querySelector('button[type="submit"]');
        const payload = readEmailPayload(form);
        const configId = Number(payload.config_id || 0);
        setButtonBusy(submitButton, true, '保存中');
        try {
            const response = await apiFetch(configId ? `/api/profile/email-configs/${configId}` : '/api/profile/email-configs', {
                method: configId ? 'PUT' : 'POST',
                body: payload,
            });
            emailState.configs = response.configs || [];
            emailState.activeId = Number(response.config?.id || configId || emailState.configs[0]?.id || 0) || null;
            renderEmailConfigs();
            const active = emailState.configs.find((config) => Number(config.id) === Number(emailState.activeId));
            fillEmailForm(active || emptyEmailConfig());
            showToast(response.message || '邮箱配置已保存', 'success');
        } catch (error) {
            showToast(error.message || '保存失败', 'error');
        } finally {
            setButtonBusy(submitButton, false);
        }
    });

    form.querySelector('[data-profile-email-delete]')?.addEventListener('click', async (event) => {
        const configId = Number(form.querySelector('[name="config_id"]')?.value || 0);
        if (!configId || !window.confirm('确定删除这个邮箱配置吗？')) {
            return;
        }
        const button = event.currentTarget;
        setButtonBusy(button, true, '删除中');
        try {
            const response = await apiFetch(`/api/profile/email-configs/${configId}`, { method: 'DELETE' });
            emailState.configs = response.configs || [];
            emailState.activeId = Number(emailState.configs[0]?.id || 0) || null;
            renderEmailConfigs();
            const active = emailState.configs.find((config) => Number(config.id) === Number(emailState.activeId));
            fillEmailForm(active || emptyEmailConfig());
            showToast(response.message || '邮箱配置已删除', 'success');
        } catch (error) {
            showToast(error.message || '删除失败', 'error');
        } finally {
            setButtonBusy(button, false);
        }
    });

    form.querySelectorAll('[data-profile-email-test]').forEach((button) => {
        button.addEventListener('click', async () => {
            const configId = Number(form.querySelector('[name="config_id"]')?.value || 0);
            if (!configId) {
                showToast('请先保存配置再测试连接', 'warning');
                return;
            }
            const mode = button.dataset.profileEmailTest || 'smtp';
            setButtonBusy(button, true, mode === 'imap' ? '测试收信' : '测试发信');
            try {
                const response = await apiFetch(`/api/profile/email-configs/${configId}/test`, {
                    method: 'POST',
                    body: { mode },
                    silent: true,
                });
                emailState.configs = response.configs || [];
                renderEmailConfigs();
                const result = response.result || {};
                showToast(result.status === 'ok' ? `${mode.toUpperCase()} 连接正常` : (result.message || '连接测试失败'), result.status === 'ok' ? 'success' : 'warning');
            } catch (error) {
                showToast(error.message || '连接测试失败', 'error');
            } finally {
                setButtonBusy(button, false);
            }
        });
    });
}

function readPortfolioPayload(form) {
    const data = Object.fromEntries(new FormData(form).entries());
    data.featured = Boolean(form.querySelector('[name="featured"]')?.checked);
    data.ability_tags = Array.from(form.querySelectorAll('[name="ability_tags"]:checked'))
        .map((item) => item.value)
        .filter(Boolean);
    return data;
}

function reloadAfterPortfolioChange(message) {
    if (message) {
        showToast(message, 'success');
    }
    window.setTimeout(() => window.location.reload(), 420);
}

function initPortfolioPanel() {
    const portfolioRoot = document.querySelector('.profile-portfolio-layout');
    if (!portfolioRoot) {
        return;
    }

    portfolioRoot.addEventListener('click', async (event) => {
        const addButton = event.target.closest('[data-portfolio-add]');
        if (addButton) {
            const sourceType = addButton.dataset.sourceType || '';
            const sourceId = addButton.dataset.sourceId || '';
            setButtonBusy(addButton, true, '加入中');
            try {
                const response = await apiFetch('/api/profile/portfolio/items', {
                    method: 'POST',
                    body: {
                        source_type: sourceType,
                        source_id: sourceId,
                    },
                });
                reloadAfterPortfolioChange(response.message || '作品已收入成长档案');
            } catch (error) {
                showToast(error.message || '加入成长档案失败', 'error');
                setButtonBusy(addButton, false);
            }
            return;
        }

        const removeButton = event.target.closest('[data-portfolio-remove]');
        if (!removeButton) {
            return;
        }
        const form = removeButton.closest('[data-portfolio-item-form]');
        const itemId = Number(form?.dataset.itemId || 0);
        if (!itemId || !window.confirm('确定把这件作品移出成长档案吗？原作业、博客或证书不会被删除。')) {
            return;
        }
        setButtonBusy(removeButton, true, '移出中');
        try {
            const response = await apiFetch(`/api/profile/portfolio/items/${itemId}`, {
                method: 'DELETE',
            });
            reloadAfterPortfolioChange(response.message || '作品已移出成长档案');
        } catch (error) {
            showToast(error.message || '移出作品失败', 'error');
            setButtonBusy(removeButton, false);
        }
    });

    portfolioRoot.querySelectorAll('[data-portfolio-item-form]').forEach((form) => {
        form.addEventListener('submit', async (event) => {
            event.preventDefault();
            const itemId = Number(form.dataset.itemId || 0);
            if (!itemId) {
                return;
            }
            const submitButton = form.querySelector('button[type="submit"]');
            setButtonBusy(submitButton, true, '保存中');
            try {
                const response = await apiFetch(`/api/profile/portfolio/items/${itemId}`, {
                    method: 'PUT',
                    body: readPortfolioPayload(form),
                });
                reloadAfterPortfolioChange(response.message || '成长档案已更新');
            } catch (error) {
                showToast(error.message || '保存作品失败', 'error');
                setButtonBusy(submitButton, false);
            }
        });
    });
}

function initActiveNavScroll() {
    const active = document.querySelector('.profile-nav__link.is-active');
    if (active && typeof active.scrollIntoView === 'function') {
        active.scrollIntoView({ block: 'nearest', inline: 'center' });
    }
}

if (root) {
    initActiveNavScroll();
    initCharts();
    initAvatarUpload();
    initBasicForm();
    initMoodEditor();
    initPasswordForm();
    initEmailConfigPanel();
    initPortfolioPanel();
}
