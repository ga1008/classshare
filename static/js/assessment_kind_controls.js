import { apiFetch } from './api.js';
import { showToast } from './ui.js';
import './grade_publication_controls.js';
import { getLayerSystem } from './lq/layer.js';

const latestVersions = new Map();
const dialogStates = new WeakMap();
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
    const dialog = select.closest('[data-assessment-kind-dialog]');
    const session = dialog && dialogStates.get(dialog);
    const current = () => !dialog || (dialogStates.get(dialog) === session && dialog.open);
    const controller = new AbortController();
    if (session) session.saveController = controller;
    const timeout = setTimeout(() => controller.abort(), 30000);
    setBusy(select, true, true);
    note.textContent = '正在保存分类…';
    try {
        const data = await apiFetch(`/api/assignments/${encodeURIComponent(select.dataset.assignmentId)}/assessment-kind`, {
            method: 'PATCH',
            body: { assessment_kind: requestedKind, expected_version: Number(select.dataset.version || 0) },
            silent: true,
            signal: controller.signal,
        });
        controller.signal.throwIfAborted();
        if (!current()) return;
        if (!validClassification(data, select.dataset.assignmentId) || data.assessment_kind !== requestedKind) {
            throw new Error('未收到保存确认，请重试或关闭后重新打开核对分类');
        }
        window.dispatchEvent(new CustomEvent('lanshare:assessment-kind-updated', { detail: data }));
        // Saving vetoes all user dismissals; a confirmed save releases that
        // veto before asking the same coordinator to perform the close.
        setBusy(select, false);
        if (session?.handle) await getLayerSystem(document).close(session.handle, 'programmatic');
        showToast('任务分类已更新，原成绩保留', 'success');
    } catch (error) {
        if (!current()) return;
        let message = error?.message || '分类未保存，请重试';
        if (error?.status === 409) {
            try {
                await refreshClassification(select);
                message = '分类已在其他页面更新，已载入最新分类，请重新选择并保存。';
            } catch {
                message = '分类已在其他页面更新，暂时无法读取最新分类，请关闭后重新打开。';
            }
        }
        if (!current()) return;
        note.textContent = message;
        showToast(message, 'error');
    } finally {
        clearTimeout(timeout);
        if (session?.saveController === controller) session.saveController = null;
        if (current()) setBusy(select, false);
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
        const layers = getLayerSystem(document);
        const cleanup = session => {
            if (dialogStates.get(dialog) !== session) return;
            dialogStates.delete(dialog);
            session.readController?.abort();
            session.saveController?.abort();
            select.value = select.dataset.savedValue || '';
            setBusy(select, false);
        };
        trigger.addEventListener('click', async () => {
            if (dialog.open) return;
            trigger.closest('details')?.removeAttribute('open');
            select.value = select.dataset.savedValue || '';
            const session = { handle: null, readController: null, saveController: null };
            dialogStates.set(dialog, session);
            session.handle = layers.open(dialog, {
                type: 'modal', trigger, returnFocus,
                beforeClose: () => dialog.dataset.saving !== 'true',
                onClose: () => cleanup(session), onDestroy: () => cleanup(session),
            });
            setBusy(select, true);
            const controller = new AbortController();
            session.readController = controller;
            const timeout = setTimeout(() => controller.abort(), 15000);
            const note = dialog.querySelector('[data-assessment-kind-note]');
            note.textContent = '正在读取最新分类…';
            try {
                await refreshClassification(select, controller.signal);
                if (dialogStates.get(dialog) === session) note.textContent = defaultNote;
            } catch (error) {
                if (dialogStates.get(dialog) === session && dialog.open) {
                    note.textContent = `${error?.message || '暂时无法读取最新分类'}。可关闭后重新打开，或选择分类后重试保存。`;
                }
            } finally {
                clearTimeout(timeout);
                if (dialogStates.get(dialog) === session && dialog.open) {
                    session.readController = null;
                    setBusy(select, false);
                    if (layers.top() === session.handle) select.focus();
                }
            }
        });
        const dismiss = reason => {
            const handle = dialogStates.get(dialog)?.handle;
            if (handle) void layers.close(handle, reason);
        };
        dialog.querySelectorAll('[data-assessment-kind-close]').forEach(button => button.addEventListener('click', () => dismiss('button')));
        dialog.addEventListener('click', event => {
            const bounds = dialog.getBoundingClientRect();
            if (event.target === dialog && (event.clientX < bounds.left || event.clientX > bounds.right
                || event.clientY < bounds.top || event.clientY > bounds.bottom)) dismiss('outside');
        });
    });
}
window.addEventListener('lanshare:assessment-kind-updated', event => applyClassification(event.detail));
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true });
else init();
