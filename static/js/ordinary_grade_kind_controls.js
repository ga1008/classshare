import { apiFetch } from './api.js';
import { showToast } from './ui.js';

const KIND_LABELS = {
    assignment: '平时作业',
    exam: '测验',
};

function effectiveLabel(kind) {
    return KIND_LABELS[kind] || '平时作业';
}

function applyKindState(control, payload) {
    const select = control.querySelector('[data-ordinary-grade-kind-select]');
    const badge = control.querySelector('[data-ordinary-grade-kind-badge]');
    const note = control.querySelector('[data-ordinary-grade-kind-note]');
    const autoOption = select?.querySelector('option[value="auto"]');
    const effectiveKind = payload.ordinary_grade_kind || payload.kind || 'assignment';
    const autoKind = payload.ordinary_grade_auto_kind || effectiveKind;
    const override = payload.ordinary_grade_kind_override || '';
    const isManual = Boolean(override);

    control.dataset.ordinaryGradeKind = effectiveKind;
    control.dataset.ordinaryGradeKindSource = isManual ? 'manual' : 'auto';
    control.classList.toggle('is-manual', isManual);
    if (badge) {
        badge.dataset.kind = effectiveKind;
        badge.textContent = effectiveLabel(effectiveKind);
    }
    if (select) {
        select.value = override || 'auto';
        select.dataset.savedValue = select.value;
    }
    if (autoOption) {
        autoOption.textContent = `自动 · ${effectiveLabel(autoKind)}`;
    }
    if (note) {
        note.textContent = isManual
            ? `已手动指定为${effectiveLabel(effectiveKind)}；只影响平时成绩表，学生答题与批改方式不变。`
            : `自动识别为${effectiveLabel(effectiveKind)}；依据任务名称与试卷来源。`;
    }
}

async function saveKind(select) {
    const control = select.closest('[data-ordinary-grade-kind-control]');
    const assignmentId = String(select.dataset.assignmentId || '').trim();
    if (!control || !assignmentId) return;
    const previousValue = select.dataset.savedValue || 'auto';

    select.disabled = true;
    control.classList.add('is-saving');
    try {
        const data = await apiFetch(
            `/api/assignments/${encodeURIComponent(assignmentId)}/ordinary-grade-kind`,
            {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ kind: select.value }),
                silent: true,
            },
        );
        applyKindState(control, data);
        showToast(data.message || '平时成绩用途已更新', 'success');
        window.dispatchEvent(new CustomEvent('lanshare:ordinary-grade-kind-updated', {
            detail: data,
        }));
    } catch (error) {
        select.value = previousValue;
        showToast(error?.message || '平时成绩用途更新失败', 'error');
    } finally {
        select.disabled = false;
        control.classList.remove('is-saving');
    }
}

function initOrdinaryGradeKindControls() {
    document.querySelectorAll('[data-ordinary-grade-kind-select]').forEach((select) => {
        if (select.dataset.bound === '1') return;
        select.dataset.bound = '1';
        select.dataset.savedValue = select.value || 'auto';
        select.addEventListener('click', (event) => event.stopPropagation());
        select.addEventListener('change', (event) => {
            event.stopPropagation();
            saveKind(select);
        });
        select.closest('[data-ordinary-grade-kind-control]')?.addEventListener(
            'click',
            (event) => event.stopPropagation(),
        );
    });
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initOrdinaryGradeKindControls, { once: true });
} else {
    initOrdinaryGradeKindControls();
}
