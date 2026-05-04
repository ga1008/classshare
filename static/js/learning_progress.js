import { apiFetch } from '/static/js/api.js';
import { showToast } from '/static/js/ui.js';

function initTopChipScroll() {
    const chip = document.querySelector('[data-learning-scroll]');
    const panel = document.getElementById('learning-progress-panel');
    if (!chip || !panel) return;
    chip.addEventListener('click', () => {
        panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
        panel.classList.remove('is-learning-focus');
        void panel.offsetWidth;
        panel.classList.add('is-learning-focus');
        window.setTimeout(() => panel.classList.remove('is-learning-focus'), 1800);
    });
}

function initStageExamButton(config) {
    const button = document.querySelector('.learning-stage-exam-btn');
    if (!button || !config?.classOfferingId) return;
    button.addEventListener('click', async () => {
        const stageKey = button.dataset.stageKey;
        if (!stageKey) return;
        const originalText = button.textContent;
        button.disabled = true;
        button.textContent = 'AI 正在布置试炼...';
        try {
            const result = await apiFetch(
                `/api/classrooms/${config.classOfferingId}/learning/stages/${stageKey}/exam`,
                { method: 'POST', silent: true },
            );
            if (result.status === 'generating') {
                showToast(result.message || 'AI 正在生成破境试炼，请稍后刷新。', 'info');
                button.disabled = false;
                button.textContent = originalText;
                return;
            }
            showToast(result.status === 'exists' ? '破境试炼已准备好，正在进入。' : '破境试炼已生成。', 'success');
            window.location.href = result.exam_url || `/classroom/${config.classOfferingId}`;
        } catch (error) {
            showToast(error.message || '破境试炼生成失败', 'error');
            button.disabled = false;
            button.textContent = originalText;
        }
    });
}

function initCertificateReveal(config) {
    const backdrop = document.getElementById('learning-certificate-backdrop');
    const closeBtn = document.getElementById('learning-certificate-close');
    const certificate = config?.learningProgress?.latest_certificate;
    if (!backdrop || !certificate?.id) return;
    const storageKey = `learning-cert-seen:${certificate.id}`;
    if (window.localStorage.getItem(storageKey) === '1') {
        return;
    }
    const close = () => {
        backdrop.classList.remove('is-open');
        backdrop.setAttribute('aria-hidden', 'true');
        window.setTimeout(() => {
            backdrop.hidden = true;
            document.body.classList.remove('has-learning-certificate');
        }, 260);
        window.localStorage.setItem(storageKey, '1');
    };
    window.setTimeout(() => {
        backdrop.hidden = false;
        backdrop.setAttribute('aria-hidden', 'false');
        document.body.classList.add('has-learning-certificate');
        window.requestAnimationFrame(() => backdrop.classList.add('is-open'));
    }, 650);
    closeBtn?.addEventListener('click', close);
    backdrop.addEventListener('click', (event) => {
        if (event.target === backdrop) close();
    });
    document.addEventListener('keydown', (event) => {
        if (!backdrop.hidden && event.key === 'Escape') close();
    });
}

export function initLearningProgress(config = window.APP_CONFIG || {}) {
    initTopChipScroll();
    initStageExamButton(config);
    initCertificateReveal(config);
}
