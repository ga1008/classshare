import { apiFetch } from './api.js';
import { showToast } from './ui.js';

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
}
