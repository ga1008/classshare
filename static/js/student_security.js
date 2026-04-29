import { apiFetch } from '/static/js/api.js';
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

document.addEventListener('DOMContentLoaded', () => {
    const modalId = 'student-security-modal';
    const form = document.getElementById('student-password-change-form');

    document.querySelectorAll('[data-open-student-security]').forEach((button) => {
        button.addEventListener('click', () => openModal(modalId));
    });

    if (!form) {
        return;
    }

    form.addEventListener('submit', async (event) => {
        event.preventDefault();
        const submitButton = form.querySelector('button[type="submit"]');
        setSubmitting(submitButton, true, '保存中...');

        try {
            const result = await apiFetch('/api/student/password/change', {
                method: 'POST',
                body: new FormData(form),
                silent: true,
            });
            showToast(result.message || '密码修改成功。', 'success');
            form.reset();
            closeModal(modalId);
        } catch (error) {
            showToast(error.message || '密码修改失败。', 'error');
        } finally {
            setSubmitting(submitButton, false, '');
        }
    });
});
