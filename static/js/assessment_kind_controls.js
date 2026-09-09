import { apiFetch } from './api.js';
import { showToast } from './ui.js';
import './grade_publication_controls.js';

const latestVersions = new Map();
const defaultNote = '分类用于后续批改和成绩归类；原成绩与历史成绩表保留。';
const kindLabels = { homework: '平时作业', midterm: '期中测验', final: '期末测验' };

function validClassification(data, id, allowUnknown = false) {
    return data && String(data.assignment_id) === String(id)
        && (Object.hasOwn(kindLabels, data.assessment_kind) || (allowUnknown && data.assessment_kind === null))
        && Number.isInteger(data.assessment_kind_version) && data.assessment_kind_version >= 0;
}

function updateSaveButton(select) {
    select.closest('[data-assessment-kind-control]').querySelector('[data-assessment-kind-save]').disabled =
        select.disabled || !select.value || select.value === select.dataset.savedValue;
}

function setBusy(select, busy, saving = false) {
    select.disabled = busy;
    const control = select.closest('[data-assessment-kind-control]');
    control.setAttribute('aria-busy', String(busy));
    control.querySelector('[data-assessment-kind-save]').textContent = busy && saving ? '保存中…' : '保存分类';
    const dialog = select.closest('[data-assessment-kind-dialog]');
    if (dialog) {
        dialog.dataset.saving = String(busy && saving);
        dialog.querySelectorAll('[data-assessment-kind-close]').forEach(button => { button.disabled = busy && saving; });
    }
    updateSaveButton(select);
}

function applyClassification(data) {
    if (!validClassification(data, data?.assignment_id, true)) return;
    const id = String(data.assignment_id);
    if ((latestVersions.get(id) || 0) > data.assessment_kind_version) return;
    latestVersions.set(id, data.assessment_kind_version);
    document.querySelectorAll('[data-assessment-kind-select]').forEach(select => {
        if (select.dataset.assignmentId !== id || Number(select.dataset.version || 0) > data.assessment_kind_version) return;
        select.value = data.assessment_kind || '';
        select.dataset.savedValue = data.assessment_kind || '';
        select.dataset.version = String(data.assessment_kind_version);
        const control = select.closest('[data-assessment-kind-control]');
        control.querySelector('[data-assessment-kind-save]').disabled = true;
        control.querySelector('[data-assessment-kind-note]').textContent = '分类已保存，原成绩与历史成绩表保留。';
    });
    document.querySelectorAll('[data-assignment-task-card]').forEach(card => {
        if (card.dataset.assignmentId !== id) return;
        card.dataset.assessmentKind = data.assessment_kind;
        const label = card.querySelector('[data-assessment-kind-label]');
        if (label) label.textContent = kindLabels[data.assessment_kind] || '分类待确认';
    });
    document.querySelectorAll('[data-assessment-detail-label]').forEach(label => {
        if (label.dataset.assignmentId === id) label.textContent = kindLabels[data.assessment_kind] || '分类待确认';
    });
}

async function refreshClassification(select, signal = AbortSignal.timeout(15000)) {
    const id = select.dataset.assignmentId;
    const data = await apiFetch(`/api/assignments/${encodeURIComponent(id)}/assessment-kind`, { silent: true, signal });
    signal.throwIfAborted();
    if (!validClassification(data, id, true)) throw new Error('未能读取最新分类，请关闭后重新打开');
    window.dispatchEvent(new CustomEvent('lanshare:assessment-kind-updated', { detail: data }));
}

async function saveClassification(select) {
    const control = select.closest('[data-assessment-kind-control]');
    const note = control.querySelector('[data-assessment-kind-note]');
    if (select.disabled || !select.value || select.value === select.dataset.savedValue) return;
    const requestedKind = select.value;
    setBusy(select, true, true);
    note.textContent = '正在保存分类…';
    try {
        const data = await apiFetch(`/api/assignments/${encodeURIComponent(select.dataset.assignmentId)}/assessment-kind`, {
            method: 'PATCH',
            body: { assessment_kind: requestedKind, expected_version: Number(select.dataset.version || 0) },
            silent: true,
            signal: AbortSignal.timeout(30000),
        });
        if (!validClassification(data, select.dataset.assignmentId) || data.assessment_kind !== requestedKind) {
            throw new Error('未收到保存确认，请重试或关闭后重新打开核对分类');
        }
        window.dispatchEvent(new CustomEvent('lanshare:assessment-kind-updated', { detail: data }));
        select.closest('[data-assessment-kind-dialog]')?.close();
        showToast('任务分类已更新，原成绩保留', 'success');
    } catch (error) {
        let message = error?.message || '分类未保存，请重试';
        if (error?.status === 409) {
            try {
                await refreshClassification(select);
                message = '分类已在其他页面更新，已载入最新分类，请重新选择并保存。';
            } catch {
                message = '分类已在其他页面更新，暂时无法读取最新分类，请关闭后重新打开。';
            }
        }
        note.textContent = message;
        showToast(message, 'error');
    } finally {
        setBusy(select, false);
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
        select.addEventListener('change', () => {
            updateSaveButton(select);
            control.querySelector('[data-assessment-kind-note]').textContent = defaultNote;
        });
        button.addEventListener('click', () => void saveClassification(select));
    });
    document.querySelectorAll('[data-assessment-kind-open]').forEach(trigger => {
        if (trigger.dataset.bound) return;
        trigger.dataset.bound = '1';
        const dialog = document.getElementById(trigger.dataset.assessmentKindOpen);
        const select = dialog?.querySelector('[data-assessment-kind-select]');
        if (!select) return;
        const returnFocus = trigger.closest('details')?.querySelector('summary') || trigger;
        let previousOverflow = '';
        let readController = null;
        trigger.addEventListener('click', async () => {
            if (dialog.open) return;
            trigger.closest('details')?.removeAttribute('open');
            select.value = select.dataset.savedValue || '';
            previousOverflow = document.body.style.overflow;
            document.body.style.overflow = 'hidden';
            dialog.showModal();
            setBusy(select, true);
            const controller = new AbortController();
            readController = controller;
            const timeout = setTimeout(() => controller.abort(), 15000);
            const note = dialog.querySelector('[data-assessment-kind-note]');
            note.textContent = '正在读取最新分类…';
            try {
                await refreshClassification(select, controller.signal);
                note.textContent = defaultNote;
            } catch (error) {
                if (readController === controller && dialog.open) {
                    note.textContent = `${error?.message || '暂时无法读取最新分类'}。可关闭后重新打开，或选择分类后重试保存。`;
                }
            } finally {
                clearTimeout(timeout);
                if (readController === controller && dialog.open) {
                    readController = null;
                    setBusy(select, false);
                    select.focus();
                }
            }
        });
        const dismiss = () => { if (dialog.dataset.saving !== 'true') dialog.close(); };
        dialog.querySelectorAll('[data-assessment-kind-close]').forEach(button => button.addEventListener('click', dismiss));
        dialog.addEventListener('cancel', event => {
            event.preventDefault();
            dismiss();
        });
        dialog.addEventListener('click', event => {
            const bounds = dialog.getBoundingClientRect();
            if (event.target === dialog && (event.clientX < bounds.left || event.clientX > bounds.right
                || event.clientY < bounds.top || event.clientY > bounds.bottom)) dismiss();
        });
        dialog.addEventListener('close', () => {
            readController?.abort();
            readController = null;
            document.body.style.overflow = previousOverflow;
            select.value = select.dataset.savedValue || '';
            updateSaveButton(select);
            returnFocus.focus();
        });
    });
}
window.addEventListener('lanshare:assessment-kind-updated', event => applyClassification(event.detail));
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true });
else init();
