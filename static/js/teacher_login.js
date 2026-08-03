// 教师登录页：人生一言场景 + fetch 提交（保留原生表单 POST 作为无 JS 回退）。
import { finishLoginWithScene, initLoginScene } from '/static/js/login_scene.js?v=20260803-scene1';
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
    const match = String(html || '').match(/登录失败[^<]*/);
    return match ? match[0].trim() : '';
}

document.addEventListener('DOMContentLoaded', () => {
    const form = document.getElementById('teacher-login-form');
    if (!form) {
        return;
    }

    initLoginScene().then((scene) => {
        loginScene = scene;
    }).catch(() => {
        loginScene = null;
    });

    form.addEventListener('submit', async (event) => {
        event.preventDefault();
        const submitButton = form.querySelector('button[type="submit"]');
        if (submitButton) {
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
                showToast('登录成功。', 'success');
                const payload = await fetchLoginTipPayload();
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
            showToast(extractStatusMessage(html) || '登录失败：邮箱或密码错误。', 'error');
        } catch (error) {
            showToast('网络异常，请稍后重试。', 'error');
        } finally {
            if (submitButton) {
                submitButton.disabled = false;
                if (submitButton.dataset.originalText) {
                    submitButton.innerHTML = submitButton.dataset.originalText;
                }
            }
        }
    });
});
