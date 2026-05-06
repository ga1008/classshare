import { apiFetch } from '/static/js/api.js';
import { showToast } from '/static/js/ui.js';

const modal = document.querySelector('[data-teacher-onboarding-modal]');
const openButtons = Array.from(document.querySelectorAll('[data-teacher-onboarding-open]'));

if (modal && openButtons.length > 0) {
    const elements = {
        closeButtons: Array.from(modal.querySelectorAll('[data-teacher-onboarding-dismiss]')),
        completeButton: modal.querySelector('[data-teacher-onboarding-complete]'),
        stepList: modal.querySelector('[data-teacher-onboarding-step-list]'),
        progressText: modal.querySelector('[data-teacher-onboarding-progress-text]'),
        progressCount: modal.querySelector('[data-teacher-onboarding-progress-count]'),
        progressbar: modal.querySelector('[data-teacher-onboarding-progressbar]'),
        stepMeta: modal.querySelector('[data-teacher-onboarding-step-meta]'),
        stepTitle: modal.querySelector('[data-teacher-onboarding-step-title]'),
        stepDescription: modal.querySelector('[data-teacher-onboarding-step-description]'),
        stepStatus: modal.querySelector('[data-teacher-onboarding-step-status]'),
        stepCount: modal.querySelector('[data-teacher-onboarding-step-count]'),
        tipText: modal.querySelector('[data-teacher-onboarding-tip-text]'),
        prevButton: modal.querySelector('[data-teacher-onboarding-prev]'),
        nextButton: modal.querySelector('[data-teacher-onboarding-next]'),
        actionLink: modal.querySelector('[data-teacher-onboarding-action]'),
    };

    const state = {
        payload: null,
        activeIndex: 0,
        isOpen: false,
        lastFocused: null,
        bodyOverflow: '',
        closeTimer: null,
    };

    const stepTips = {
        classes: '班级是教师端的第一块地基。先建班级，再按需导入学生名单，之后所有课堂都会复用这份班级数据。',
        courses: '课程是可复用的教学模板，课堂是某个学期里给某个班开的具体课。先有课程，后面开课会更顺手。',
        semesters: '学期会影响教学日历、周次和课堂排期。即使只是快速试用，也建议先创建当前学期。',
        offerings: '开设课堂时，把班级、课程和学期绑定在一起。创建完成后，师生都会围绕这个课堂开展作业、材料和讨论。',
        ai: 'AI 助教按课堂独立配置。保存系统提示词和课程大纲后，它就能带着这门课的上下文服务老师和学生。',
    };

    function escapeHtml(value) {
        return String(value ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function getSteps() {
        return Array.isArray(state.payload?.progress?.steps) ? state.payload.progress.steps : [];
    }

    function clampIndex(index) {
        const steps = getSteps();
        if (steps.length === 0) {
            return 0;
        }
        return Math.min(Math.max(Number(index) || 0, 0), steps.length - 1);
    }

    async function loadState({ silent = false } = {}) {
        try {
            state.payload = await apiFetch('/api/manage/teacher-onboarding/state', { silent: true });
            return state.payload;
        } catch (error) {
            if (!silent) {
                showToast(error.message || '新手引导状态读取失败', 'error');
            }
            return null;
        }
    }

    async function markDismissed(reason) {
        try {
            state.payload = await apiFetch('/api/manage/teacher-onboarding/dismiss', {
                method: 'POST',
                body: { reason },
                silent: true,
            });
            return true;
        } catch (error) {
            showToast(error.message || '新手引导状态保存失败，请稍后再试。', 'error');
            return false;
        }
    }

    function resolveAutoIndex() {
        const steps = getSteps();
        const nextStepId = String(state.payload?.progress?.next_step_id || '');
        const nextIndex = steps.findIndex((step) => step.id === nextStepId);
        if (nextIndex >= 0) {
            return nextIndex;
        }
        const firstPending = steps.findIndex((step) => !step.ready);
        return firstPending >= 0 ? firstPending : 0;
    }

    function renderProgress() {
        const progress = state.payload?.progress || {};
        const completed = Number(progress.completed_count || 0);
        const total = Number(progress.total_count || getSteps().length || 1);
        const percent = total > 0 ? Math.round((completed / total) * 100) : 0;

        if (elements.progressText) {
            elements.progressText.textContent = progress.all_core_ready
                ? '基础开课流程已经齐备'
                : '基础开课流程准备度';
        }
        if (elements.progressCount) {
            elements.progressCount.textContent = `${completed}/${total}`;
        }
        if (elements.progressbar) {
            elements.progressbar.style.width = `${percent}%`;
        }
    }

    function renderStepList() {
        const steps = getSteps();
        if (!elements.stepList) {
            return;
        }

        elements.stepList.innerHTML = steps.map((step, index) => `
            <button
                type="button"
                class="teacher-onboarding-step-button${index === state.activeIndex ? ' is-active' : ''}${step.ready ? ' is-ready' : ''}"
                data-teacher-onboarding-step-index="${index}"
                aria-pressed="${index === state.activeIndex ? 'true' : 'false'}"
            >
                <span class="teacher-onboarding-step-index">${index + 1}</span>
                <span class="teacher-onboarding-step-copy">
                    <strong>${escapeHtml(step.title)}</strong>
                    <span>${escapeHtml(step.description)}</span>
                </span>
                <span class="teacher-onboarding-step-badge">${escapeHtml(step.status_label)}</span>
            </button>
        `).join('');

        elements.stepList.querySelectorAll('[data-teacher-onboarding-step-index]').forEach((button) => {
            button.addEventListener('click', () => {
                state.activeIndex = clampIndex(button.dataset.teacherOnboardingStepIndex);
                render();
            });
        });
    }

    function renderActiveStep() {
        const steps = getSteps();
        const step = steps[state.activeIndex] || steps[0];
        if (!step) {
            return;
        }

        if (elements.stepMeta) {
            elements.stepMeta.textContent = `第 ${state.activeIndex + 1} 步 / 共 ${steps.length} 步`;
        }
        if (elements.stepTitle) {
            elements.stepTitle.textContent = step.title;
        }
        if (elements.stepDescription) {
            elements.stepDescription.textContent = step.description;
        }
        if (elements.stepStatus) {
            elements.stepStatus.textContent = step.status_label;
        }
        if (elements.stepCount) {
            elements.stepCount.textContent = `${Number(step.count || 0)} 项`;
        }
        if (elements.tipText) {
            elements.tipText.textContent = stepTips[step.id] || '按当前步骤继续处理，完成后可以回到这里查看下一步。';
        }
        if (elements.actionLink) {
            elements.actionLink.href = step.href || '/manage';
            elements.actionLink.textContent = step.action_label || '前往处理';
        }
        if (elements.prevButton) {
            elements.prevButton.disabled = state.activeIndex <= 0;
        }
        if (elements.nextButton) {
            elements.nextButton.disabled = state.activeIndex >= steps.length - 1;
        }
    }

    function render() {
        state.activeIndex = clampIndex(state.activeIndex);
        renderProgress();
        renderStepList();
        renderActiveStep();
    }

    async function openGuide(source = 'manual') {
        const payload = source === 'manual'
            ? await loadState({ silent: false })
            : (state.payload || await loadState({ silent: true }));
        if (!payload) {
            return;
        }

        state.activeIndex = source === 'auto' ? resolveAutoIndex() : 0;
        state.lastFocused = document.activeElement instanceof HTMLElement ? document.activeElement : null;
        state.bodyOverflow = document.body.style.overflow || '';
        render();

        window.clearTimeout(state.closeTimer);
        modal.hidden = false;
        document.body.style.overflow = 'hidden';
        window.requestAnimationFrame(() => {
            modal.classList.add('is-open');
            state.isOpen = true;
            modal.querySelector('[data-teacher-onboarding-dismiss]')?.focus({ preventScroll: true });
        });
    }

    async function closeGuide(reason = 'manual_exit', { navigateTo = '' } = {}) {
        if (!state.isOpen && !navigateTo) {
            return;
        }

        const persisted = await markDismissed(reason);
        if (!persisted) {
            return;
        }

        modal.classList.remove('is-open');
        state.isOpen = false;
        document.body.style.overflow = state.bodyOverflow;
        state.closeTimer = window.setTimeout(() => {
            if (!state.isOpen) {
                modal.hidden = true;
            }
        }, 220);

        if (state.lastFocused && document.contains(state.lastFocused)) {
            state.lastFocused.focus({ preventScroll: true });
        }

        if (navigateTo) {
            window.location.href = navigateTo;
        }
    }

    openButtons.forEach((button) => {
        button.addEventListener('click', () => openGuide('manual'));
    });

    elements.closeButtons.forEach((button) => {
        button.addEventListener('click', () => closeGuide('manual_exit'));
    });

    elements.completeButton?.addEventListener('click', () => closeGuide('completed'));

    elements.prevButton?.addEventListener('click', () => {
        state.activeIndex = clampIndex(state.activeIndex - 1);
        render();
    });

    elements.nextButton?.addEventListener('click', () => {
        state.activeIndex = clampIndex(state.activeIndex + 1);
        render();
    });

    elements.actionLink?.addEventListener('click', (event) => {
        const href = elements.actionLink?.getAttribute('href') || '/manage';
        event.preventDefault();
        closeGuide('used', { navigateTo: href });
    });

    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && state.isOpen) {
            event.preventDefault();
            closeGuide('manual_exit');
        }
    });

    window.setTimeout(async () => {
        const payload = await loadState({ silent: true });
        if (payload?.should_auto_open) {
            openGuide('auto');
        }
    }, 350);
}
