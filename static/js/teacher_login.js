// 教师登录页：人生一言场景 + fetch 提交（保留原生表单 POST 作为无 JS 回退）。
import { finishLoginWithScene, initLoginScene, setLoginFeedback, setLoginSubmitting } from '/static/js/login_scene.js?v=20260803-scene3';
import { showToast } from '/static/js/ui.js';

let loginScene = null;

async function fetchLoginTipPayload() {
    try {
        const response = await fetch('/api/learning/cultivation-profile?include_tip=1', {
            credentials: 'same-origin',
            headers: { Accept: 'application/json' },
        });
        if (!response.ok) return null;
        return await response.json();
    } catch (error) {
        return null;
    }
}

function extractStatusMessage(html) {
    const feedback = new DOMParser().parseFromString(String(html || ''), 'text/html').querySelector('[data-login-feedback]');
    if (feedback?.textContent.trim()) return feedback.textContent.trim();
    const match = String(html || '').match(/登录失败[^<]*/);
    return match ? match[0].trim() : '';
}

// 登录失败的物理反馈：玻璃卡轻微摇头。
function shakeLoginCard() {
    const card = document.querySelector('.login-card');
    if (!card) return;
    card.classList.remove('login-card--shake');
    void card.offsetWidth;
    card.classList.add('login-card--shake');
    window.setTimeout(() => card.classList.remove('login-card--shake'), 620);
}

function initTeacherLogin() {
    const form = document.getElementById('teacher-login-form');
    if (!form) {
        return;
    }
    if (form.dataset.loginMounted === 'true') return;
    form.dataset.loginMounted = 'true';

    initLoginScene().then((scene) => {
        loginScene = scene;
    }).catch(() => {
        loginScene = null;
    });

    form.addEventListener('submit', async (event) => {
        event.preventDefault();
        const submitButton = form.querySelector('button[type="submit"]');
        if (submitButton?.disabled) return;
        setLoginFeedback(form);
        if (submitButton && !setLoginSubmitting(submitButton, true)) {
            submitButton.dataset.originalText = submitButton.innerHTML;
            submitButton.disabled = true;
            submitButton.innerHTML = '登录中...';
        }

        try {
            const response = await fetch(form.action, {
                method: 'POST',
                body: new FormData(form),
                credentials: 'same-origin',
                redirect: 'follow',
            });

            if (response.redirected) {
                // 登录成功：会话 cookie 已设置，重定向目标就是 next。
                const finalUrl = new URL(response.url, window.location.origin);
                const redirectTo = finalUrl.pathname + finalUrl.search;
                const payload = await fetchLoginTipPayload();
                if (form.closest('[data-lq-login-card]')) form.dataset.loginCompleting = 'true';
                finishLoginWithScene({
                    scene: loginScene,
                    profile: payload?.profile || null,
                    loginTip: payload?.login_tip || null,
                    redirectTo,
                    cardElement: document.querySelector('.login-card'),
                });
                return;
            }

            const html = await response.text();
            shakeLoginCard();
            const message = extractStatusMessage(html) || '登录失败：邮箱或密码错误。';
            if (!setLoginFeedback(form, message)) showToast(message, 'error');
        } catch (error) {
            shakeLoginCard();
            if (!setLoginFeedback(form, '网络异常，请稍后重试。')) showToast('网络异常，请稍后重试。', 'error');
        } finally {
            if (submitButton && form.dataset.loginCompleting !== 'true' && !setLoginSubmitting(submitButton, false)) {
                submitButton.disabled = false;
                if (submitButton.dataset.originalText) {
                    submitButton.innerHTML = submitButton.dataset.originalText;
                }
            }
        }
    });
}
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initTeacherLogin, { once: true });
else initTeacherLogin();
