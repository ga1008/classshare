import { apiFetch } from './api.js';
import { showToast } from './ui.js';
import './grade_publication_controls.js';

const latestVersions = new Map();

function applyClassification(data) {
    if (!data || !['homework', 'midterm', 'final'].includes(data.assessment_kind) || !Number.isInteger(data.assessment_kind_version)) return;
    const id = String(data.assignment_id);
    if ((latestVersions.get(id) || 0) > data.assessment_kind_version) return;
    latestVersions.set(id, data.assessment_kind_version);
    document.querySelectorAll('[data-assessment-kind-select]').forEach(select => {
        if (select.dataset.assignmentId !== id || Number(select.dataset.version || 0) > data.assessment_kind_version) return;
        select.value = data.assessment_kind;
        select.dataset.savedValue = data.assessment_kind;
        select.dataset.version = String(data.assessment_kind_version);
        const control = select.closest('[data-assessment-kind-control]');
        control.querySelector('[data-assessment-kind-save]').disabled = true;
        control.querySelector('[data-assessment-kind-note]').textContent = '分类已保存，原成绩与历史成绩表保留。';
    });
    document.querySelectorAll('[data-assignment-task-card]').forEach(card => {
        if (card.dataset.assignmentId !== id) return;
        card.dataset.assessmentKind = data.assessment_kind;
        const label = card.querySelector('[data-assessment-kind-label]');
        if (label) label.textContent = data.assessment_kind_label;
    });
    document.querySelectorAll('[data-assessment-detail-label]').forEach(label => { label.textContent = data.assessment_kind_label; });
}

async function saveClassification(select) {
    const control = select.closest('[data-assessment-kind-control]');
    const button = control.querySelector('[data-assessment-kind-save]');
    if (select.disabled || !select.value) return;
    select.disabled = true;
    button.disabled = true;
    try {
        const data = await apiFetch(`/api/assignments/${encodeURIComponent(select.dataset.assignmentId)}/assessment-kind`, {
            method: 'PATCH',
            body: { assessment_kind: select.value, expected_version: Number(select.dataset.version || 0) },
            silent: true,
        });
        window.dispatchEvent(new CustomEvent('lanshare:assessment-kind-updated', { detail: data }));
        showToast('任务分类已更新，原成绩保留', 'success');
    } catch (error) {
        select.value = select.dataset.savedValue || '';
        const message = error?.message || '分类未保存，请刷新后重试';
        control.querySelector('[data-assessment-kind-note]').textContent = message;
        showToast(message, 'error');
    } finally {
        select.disabled = false;
    }
}

function init() {
    document.querySelectorAll('[data-assessment-kind-select]').forEach(select => {
        if (select.dataset.bound) return;
        select.dataset.bound = '1';
        select.dataset.savedValue = select.value;
        latestVersions.set(select.dataset.assignmentId, Math.max(latestVersions.get(select.dataset.assignmentId) || 0, Number(select.dataset.version || 0)));
        const control = select.closest('[data-assessment-kind-control]');
        const button = control.querySelector('[data-assessment-kind-save]');
        select.addEventListener('change', () => { button.disabled = !select.value || select.value === select.dataset.savedValue; });
        button.addEventListener('click', () => void saveClassification(select));
    });
}
window.addEventListener('lanshare:assessment-kind-updated', event => applyClassification(event.detail));
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true });
else init();
