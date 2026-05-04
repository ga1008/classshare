import { apiFetch } from '/static/js/api.js';
import { playCultivationReveal } from '/static/js/cultivation_identity.js?v=20260504';
import { closeModal, openModal, showToast } from '/static/js/ui.js';

function setSubmitting(button, submitting, pendingText) {
    if (!button) {
        return;
    }

    if (submitting) {
        button.dataset.originalText = button.innerHTML;
        button.disabled = true;
        button.innerHTML = pendingText;
        return;
    }

    button.disabled = false;
    if (button.dataset.originalText) {
        button.innerHTML = button.dataset.originalText;
    }
}

function redirectAfterLogin(result) {
    const redirectTo = result.redirect_to || '/dashboard';
    const loginCount = Number(result.login_count || 0);
    const message = loginCount > 0
        ? `登录成功，这是您第 ${loginCount} 次登录。`
        : '登录成功。';

    showToast(message, 'success');
    const go = () => window.location.assign(redirectTo);
    if (result.cultivation_profile) {
        playCultivationReveal(result.cultivation_profile, { durationMs: 3600, onDone: go });
        return;
    }
    window.setTimeout(go, 450);
}

document.addEventListener('DOMContentLoaded', () => {
    const root = document.querySelector('[data-student-login-root]');
    if (!root) {
        return;
    }

    const modePanels = Array.from(document.querySelectorAll('[data-login-panel]'));
    const passwordForm = document.getElementById('student-password-login-form');
    const identityForm = document.getElementById('student-identity-login-form');
    const forgotForm = document.getElementById('student-forgot-password-form');
    const setupForm = document.getElementById('student-password-setup-form');
    const forgotTrigger = document.getElementById('forgot-password-trigger');
    const firstLoginSwitch = document.getElementById('first-login-switch');
    const passwordIdentifierInput = document.getElementById('identifier');
    const identityNameInput = document.getElementById('identity-name');
    const wrongPasswordMessage = '登录失败：账号或密码错误。';
    const passwordSetupRequiredMessages = new Set([
        '该账号尚未设置密码，请使用姓名和学号完成首次登录。',
        '教师已通过找回密码申请，请使用姓名和学号重新设置密码。',
    ]);
    let consecutivePasswordFailures = 0;
    let attentionTimer = null;

    function setMode(mode, options = {}) {
        modePanels.forEach((panel) => {
            panel.hidden = panel.dataset.loginPanel !== mode;
        });

        if (options.updateHash !== false) {
            const nextHash = mode === 'identity' ? '#identity-login' : '';
            const currentUrl = new URL(window.location.href);
            if (nextHash) {
                currentUrl.hash = nextHash;
            } else {
                currentUrl.hash = '';
            }
            window.history.replaceState({}, '', `${currentUrl.pathname}${currentUrl.search}${currentUrl.hash}`);
        }

        if (mode === 'identity') {
            window.setTimeout(() => identityNameInput?.focus(), 60);
            return;
        }
        window.setTimeout(() => passwordIdentifierInput?.focus(), 60);
    }

    function clearIdentityAttention() {
        if (attentionTimer) {
            window.clearTimeout(attentionTimer);
            attentionTimer = null;
        }
        firstLoginSwitch?.classList.remove('is-attention');
    }

    function triggerIdentityAttention() {
        if (!firstLoginSwitch) {
            return;
        }

        clearIdentityAttention();
        void firstLoginSwitch.offsetWidth;
        firstLoginSwitch.classList.add('is-attention');
        attentionTimer = window.setTimeout(() => {
            firstLoginSwitch.classList.remove('is-attention');
            attentionTimer = null;
        }, 3000);
    }

    const defaultMode = window.location.hash === '#identity-login'
        ? 'identity'
        : (root.dataset.defaultMode || 'password');
    setMode(defaultMode, { updateHash: false });

    document.querySelectorAll('[data-switch-mode]').forEach((button) => {
        button.addEventListener('click', () => {
            clearIdentityAttention();
            consecutivePasswordFailures = 0;
            setMode(button.dataset.switchMode);
        });
    });

    if (forgotTrigger) {
        forgotTrigger.addEventListener('click', () => openModal('forgot-password-modal'));
    }

    if (passwordForm) {
        passwordForm.addEventListener('submit', async (event) => {
            event.preventDefault();
            const submitButton = passwordForm.querySelector('button[type="submit"]');
            setSubmitting(submitButton, true, '登录中...');

            try {
                const result = await apiFetch('/api/student/login/password', {
                    method: 'POST',
                    body: new FormData(passwordForm),
                    silent: true,
                });
                consecutivePasswordFailures = 0;
                clearIdentityAttention();
                redirectAfterLogin(result);
            } catch (error) {
                if (error.message === wrongPasswordMessage) {
                    consecutivePasswordFailures += 1;
                    if (consecutivePasswordFailures >= 2) {
                        triggerIdentityAttention();
                    }
                } else if (passwordSetupRequiredMessages.has(error.message)) {
                    triggerIdentityAttention();
                } else {
                    consecutivePasswordFailures = 0;
                }
                showToast(error.message || '登录失败。', 'error');
            } finally {
                setSubmitting(submitButton, false, '');
            }
        });
    }

    if (identityForm) {
        identityForm.addEventListener('submit', async (event) => {
            event.preventDefault();
            const submitButton = identityForm.querySelector('button[type="submit"]');
            setSubmitting(submitButton, true, '核验中...');

            try {
                const result = await apiFetch('/api/student/login/identity', {
                    method: 'POST',
                    body: new FormData(identityForm),
                    silent: true,
                });

                document.getElementById('setup-token').value = result.setup_token || '';
                document.getElementById('setup-next').value = identityForm.querySelector('input[name="next"]').value || '/dashboard';
                document.getElementById('setup-student-name').textContent = result.student?.name || '';
                document.getElementById('setup-student-id').textContent = result.student?.student_id_number || '';
                document.getElementById('setup-student-class').textContent = result.student?.class_name || '';
                document.getElementById('setup-flow-title').textContent = result.flow_type === 'password_reset'
                    ? '重新设置密码'
                    : '首次设置密码';
                document.getElementById('setup-flow-copy').textContent = result.flow_type === 'password_reset'
                    ? '教师已通过您的找回密码申请，请先设置新密码，再继续进入课堂。'
                    : '首次登录需要先设置密码，后续请使用“姓名/学号 + 密码”登录。';

                setupForm.reset();
                document.getElementById('setup-token').value = result.setup_token || '';
                document.getElementById('setup-next').value = identityForm.querySelector('input[name="next"]').value || '/dashboard';
                consecutivePasswordFailures = 0;
                clearIdentityAttention();
                openModal('student-password-setup-modal');
            } catch (error) {
                showToast(error.message || '身份核验失败。', 'error');
            } finally {
                setSubmitting(submitButton, false, '');
            }
        });
    }

    if (setupForm) {
        setupForm.addEventListener('submit', async (event) => {
            event.preventDefault();
            const submitButton = setupForm.querySelector('button[type="submit"]');
            setSubmitting(submitButton, true, '保存中...');

            try {
                const result = await apiFetch('/api/student/password/setup', {
                    method: 'POST',
                    body: new FormData(setupForm),
                    silent: true,
                });
                closeModal('student-password-setup-modal');
                redirectAfterLogin(result);
            } catch (error) {
                showToast(error.message || '密码设置失败。', 'error');
            } finally {
                setSubmitting(submitButton, false, '');
            }
        });
    }

    if (forgotForm) {
        forgotForm.addEventListener('submit', async (event) => {
            event.preventDefault();
            const submitButton = forgotForm.querySelector('button[type="submit"]');
            setSubmitting(submitButton, true, '提交中...');

            try {
                const result = await apiFetch('/api/student/password/forgot', {
                    method: 'POST',
                    body: new FormData(forgotForm),
                    silent: true,
                });
                showToast(result.message || '申请已提交。', 'success');
                forgotForm.reset();
                closeModal('forgot-password-modal');
            } catch (error) {
                showToast(error.message || '申请提交失败。', 'error');
            } finally {
                setSubmitting(submitButton, false, '');
            }
        });
    }
});
