import { apiFetch } from '/static/js/api.js';
import { showToast } from '/static/js/ui.js';

function initLearningProgressModal() {
    const modal = document.getElementById('learning-progress-modal');
    const panel = document.querySelector('[data-learning-panel]');
    const triggers = Array.from(document.querySelectorAll('[data-learning-modal-open], [data-learning-scroll]'));
    if (!modal || !panel || !triggers.length) return;

    const shell = modal.querySelector('.learning-modal-shell');
    const closeBtn = document.getElementById('learning-modal-close');
    const transitionMs = 260;
    let closeTimer = 0;
    let activeTrigger = null;

    const getFocusableElements = () => Array.from(
        modal.querySelectorAll('a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])'),
    ).filter((element) => element.offsetParent !== null || element === document.activeElement);

    const setTriggerState = (expanded) => {
        triggers.forEach((trigger) => {
            trigger.setAttribute('aria-expanded', String(expanded));
        });
    };

    const openModal = (trigger = null) => {
        window.clearTimeout(closeTimer);
        activeTrigger = trigger || document.activeElement;
        modal.hidden = false;
        modal.setAttribute('aria-hidden', 'false');
        document.body.classList.add('has-learning-modal');
        setTriggerState(true);
        window.requestAnimationFrame(() => {
            modal.classList.add('is-open');
            panel.classList.remove('is-learning-focus');
            void panel.offsetWidth;
            panel.classList.add('is-learning-focus');
            (closeBtn || shell)?.focus({ preventScroll: true });
            window.setTimeout(() => panel.classList.remove('is-learning-focus'), 1600);
        });
    };

    const closeModal = () => {
        modal.classList.remove('is-open');
        modal.setAttribute('aria-hidden', 'true');
        document.body.classList.remove('has-learning-modal');
        setTriggerState(false);
        closeTimer = window.setTimeout(() => {
            if (!modal.classList.contains('is-open')) {
                modal.hidden = true;
                activeTrigger?.focus?.({ preventScroll: true });
                activeTrigger = null;
            }
        }, transitionMs);
    };

    triggers.forEach((trigger) => {
        trigger.setAttribute('aria-haspopup', 'dialog');
        trigger.setAttribute('aria-controls', 'learning-progress-modal');
        trigger.setAttribute('aria-expanded', 'false');
        trigger.addEventListener('click', () => openModal(trigger));
    });
    closeBtn?.addEventListener('click', closeModal);
    modal.addEventListener('click', (event) => {
        if (event.target === modal) closeModal();
    });
    document.addEventListener('keydown', (event) => {
        if (modal.hidden) return;

        if (event.key === 'Escape') {
            closeModal();
            return;
        }

        if (event.key !== 'Tab') return;

        const focusableElements = getFocusableElements();
        if (!focusableElements.length) {
            event.preventDefault();
            shell?.focus({ preventScroll: true });
            return;
        }

        const firstFocusable = focusableElements[0];
        const lastFocusable = focusableElements[focusableElements.length - 1];
        if (event.shiftKey && document.activeElement === firstFocusable) {
            event.preventDefault();
            lastFocusable.focus({ preventScroll: true });
        } else if (!event.shiftKey && document.activeElement === lastFocusable) {
            event.preventDefault();
            firstFocusable.focus({ preventScroll: true });
        }
    });
}

function initStageExamButton(config) {
    const buttons = Array.from(document.querySelectorAll('.learning-stage-exam-btn'));
    const setButtonBusy = (button, busy) => {
        const label = button.querySelector('[data-learning-stage-action-label]');
        const nextText = '生成中';
        if (label && !button.dataset.originalActionLabel) {
            button.dataset.originalActionLabel = label.textContent;
        }
        if (!label && !button.dataset.originalText) {
            button.dataset.originalText = button.textContent;
        }
        button.disabled = busy;
        button.classList.toggle('is-busy', busy);
        if (label) {
            label.textContent = busy ? nextText : button.dataset.originalActionLabel;
        } else {
            button.textContent = busy ? 'AI 正在布置试炼...' : button.dataset.originalText;
        }
    };

    if (buttons.length && config?.classOfferingId) {
        buttons.forEach((button) => {
            button.addEventListener('click', async () => {
                const stageKey = button.dataset.stageKey;
                if (!stageKey) return;
                buttons
                    .filter((candidate) => candidate.dataset.stageKey === stageKey)
                    .forEach((candidate) => setButtonBusy(candidate, true));
                try {
                    const result = await apiFetch(
                        `/api/classrooms/${config.classOfferingId}/learning/stages/${stageKey}/exam`,
                        { method: 'POST', silent: true },
                    );
                    if (result.status === 'generating') {
                        showToast(result.message || 'AI 正在生成破境试炼，请稍后刷新。', 'info');
                        buttons
                            .filter((candidate) => candidate.dataset.stageKey === stageKey)
                            .forEach((candidate) => setButtonBusy(candidate, false));
                        return;
                    }
                    showToast(result.status === 'exists' ? '破境试炼已准备好，正在进入。' : '破境试炼已生成。', 'success');
                    window.location.href = result.exam_url || `/classroom/${config.classOfferingId}`;
                } catch (error) {
                    showToast(error.message || '破境试炼生成失败', 'error');
                    buttons
                        .filter((candidate) => candidate.dataset.stageKey === stageKey)
                        .forEach((candidate) => setButtonBusy(candidate, false));
                }
            });
        });
    }

    const deleteButton = document.querySelector('.learning-stage-delete-btn');
    if (!deleteButton || !config?.classOfferingId) return;
    deleteButton.addEventListener('click', async () => {
        const stageKey = deleteButton.dataset.stageKey;
        if (!stageKey) return;
        if (!window.confirm('确定删除这份个人破境试炼吗？删除后可以重新生成。')) return;
        const originalText = deleteButton.textContent;
        deleteButton.disabled = true;
        deleteButton.textContent = '正在删除...';
        try {
            const result = await apiFetch(
                `/api/classrooms/${config.classOfferingId}/learning/stages/${stageKey}/exam`,
                { method: 'DELETE', silent: true },
            );
            showToast(result.message || '个人破境试炼已删除，可以重新试炼。', 'success');
            window.setTimeout(() => window.location.reload(), 500);
        } catch (error) {
            showToast(error.message || '删除试炼失败', 'error');
            deleteButton.disabled = false;
            deleteButton.textContent = originalText;
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

function initLearningMountain(config) {
    const container = document.querySelector('[data-learning-mountain-chart]');
    const hint = document.querySelector('[data-learning-mountain-hint]');
    const position = config?.learningProgress?.class_position;
    if (!container || !position?.current || !position?.leader || !position?.mountain) return;

    const svgNS = 'http://www.w3.org/2000/svg';
    const width = 360;
    const height = 150;
    const baseY = 130;
    const peakY = 18;
    const peakX = 178;
    const leftBaseX = 30;
    const rightBaseX = 330;
    const labelX = 274;
    const minScore = Number(position.mountain.min_score ?? 0);
    const maxScore = Number(position.mountain.max_score ?? 100);
    const scoreRange = Math.max(1, maxScore - minScore);
    const currentScore = Number(position.current.score ?? 0);
    const leaderScore = Number(position.leader.score ?? maxScore);
    const currentName = position.current.name || config?.userInfo?.name || '您';
    const leaderName = position.leader.name || '同学';
    const samePerson = Boolean(position.leader.is_self) || leaderName === currentName;
    const compactText = (text, maxLength) => {
        const normalized = String(text || '').trim();
        return normalized.length > maxLength ? `${normalized.slice(0, Math.max(1, maxLength - 1))}…` : normalized;
    };

    const create = (tag, attrs = {}) => {
        const node = document.createElementNS(svgNS, tag);
        Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, String(value)));
        return node;
    };
    const yFor = (score) => {
        const ratio = maxScore === minScore ? 0.5 : (Number(score || 0) - minScore) / scoreRange;
        return baseY - Math.max(0, Math.min(1, ratio)) * (baseY - peakY);
    };
    const selfY = Math.max(peakY, Math.min(baseY, yFor(currentScore)));
    const selfLabel = samePerson
        ? `修为 ${currentScore.toFixed(1)}`
        : `${compactText(currentName, 5)} · ${currentScore.toFixed(1)}`;
    const selfLabelY = selfY <= peakY + 12 ? selfY + 18 : selfY - 6;
    const peakLabel = `${compactText(leaderName, 6)} · ${leaderScore.toFixed(1)}`;

    container.textContent = '';
    const svg = create('svg', {
        viewBox: `0 0 ${width} ${height}`,
        role: 'img',
        'aria-label': '全班修为山峰，山顶为最高修为，横向虚线为您的修为位置',
    });
    const defs = create('defs');
    const gradient = create('linearGradient', { id: 'learningMountainFill', x1: '0', x2: '0', y1: '0', y2: '1' });
    gradient.append(
        create('stop', { offset: '0%', 'stop-color': '#14b8a6', 'stop-opacity': '0.28' }),
        create('stop', { offset: '58%', 'stop-color': '#38bdf8', 'stop-opacity': '0.16' }),
        create('stop', { offset: '100%', 'stop-color': '#f59e0b', 'stop-opacity': '0.06' }),
    );
    defs.appendChild(gradient);
    svg.appendChild(defs);

    svg.appendChild(create('path', {
        class: 'learning-mountain__area',
        d: `M ${leftBaseX} ${baseY} C 82 112, 116 60, ${peakX} ${peakY} C 238 58, 284 106, ${rightBaseX} ${baseY} Z`,
        fill: 'url(#learningMountainFill)',
    }));
    svg.appendChild(create('path', {
        class: 'learning-mountain__ridge',
        d: `M ${leftBaseX} ${baseY} C 82 112, 116 60, ${peakX} ${peakY} C 238 58, 284 106, ${rightBaseX} ${baseY}`,
    }));
    svg.appendChild(create('circle', {
        class: 'learning-mountain__peak-dot',
        cx: peakX,
        cy: peakY,
        r: 4.8,
    }));
    const peakText = create('text', {
        class: 'learning-mountain__peak-label',
        x: peakX,
        y: 12,
        'text-anchor': 'middle',
    });
    peakText.textContent = peakLabel;
    svg.appendChild(peakText);

    svg.appendChild(create('line', {
        class: 'learning-mountain__self-line',
        x1: 42,
        x2: labelX - 7,
        y1: selfY,
        y2: selfY,
    }));
    svg.appendChild(create('circle', {
        class: 'learning-mountain__self-dot',
        cx: labelX - 8,
        cy: selfY,
        r: 3.5,
    }));
    const labelBg = create('rect', {
        class: 'learning-mountain__self-label-bg',
        x: labelX - 2,
        y: selfLabelY - 12,
        width: Math.min(82, Math.max(52, selfLabel.length * 8.2)),
        height: 17,
        rx: 8,
    });
    const selfText = create('text', {
        class: 'learning-mountain__self-label',
        x: labelX + 5,
        y: selfLabelY,
    });
    selfText.textContent = selfLabel;
    svg.append(labelBg, selfText);

    const setHint = () => {
        if (!hint) return;
        hint.textContent = samePerson
            ? `${leaderName} 位于山顶，修为 ${leaderScore.toFixed(1)}。`
            : `${currentName} 当前第 ${position.current.rank} / ${position.total} 位，修为 ${currentScore.toFixed(1)}。`;
    };

    svg.addEventListener('pointerenter', setHint);
    svg.addEventListener('focus', setHint);
    container.appendChild(svg);
    setHint();
}

export function initLearningProgress(config = window.APP_CONFIG || {}) {
    initLearningProgressModal();
    initStageExamButton(config);
    initLearningMountain(config);
    initCertificateReveal(config);
}
