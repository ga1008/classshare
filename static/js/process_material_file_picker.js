import { escapeHtml } from './ui.js';
import {
    formatProcessImportFileSize,
    getProcessDocumentImportDuplicateProblem,
    getProcessDocumentImportFileProblem,
} from './process_material_import_policy.js';

const DEFAULT_EMPTY_TEXT = '尚未选择文件，请先选择要导入解析的文件。';

export function setProcessMaterialImportBusyState(overlay, busy) {
    const busyState = Boolean(busy);
    const importRoot = overlay?.querySelector('.lp-import');
    importRoot?.classList.toggle('is-submitting', busyState);
    importRoot?.setAttribute('aria-busy', busyState ? 'true' : 'false');
    overlay?.querySelectorAll('[data-pm-close], [data-lp-close], [data-ap-close], [data-te-close]').forEach((button) => {
        button.disabled = busyState;
    });
    importRoot?.querySelectorAll('input, select, textarea, button').forEach((control) => {
        control.disabled = busyState;
    });
}

export function setupProcessMaterialImportPicker({
    overlay,
    inputSelector,
    pickSelector,
    listSelector,
    dropzoneSelector,
    selectionSelector,
    submitSelector,
    showToast,
    emptyText = DEFAULT_EMPTY_TEXT,
}) {
    const picked = [];
    const notify = typeof showToast === 'function' ? showToast : () => {};
    const input = overlay.querySelector(inputSelector);
    const pickButton = overlay.querySelector(pickSelector);
    const listEl = overlay.querySelector(listSelector);
    const dropzone = overlay.querySelector(dropzoneSelector);
    const selectionEl = overlay.querySelector(selectionSelector);
    const submit = overlay.querySelector(submitSelector);
    const initialMessage = selectionEl?.querySelector('[data-selection-message]');
    const messageId = initialMessage?.id || (selectionEl?.id ? `${selectionEl.id}-message` : '');
    const isBusy = () => submit?.dataset.actionBusy === 'true';
    const renderSelectionMessage = (message) => {
        const idAttr = messageId ? ` id="${escapeHtml(messageId)}"` : '';
        return `<span${idAttr} class="lp-import-selection__message" data-selection-message role="status" aria-live="polite">${escapeHtml(message)}</span>`;
    };

    const updateSubmitState = () => {
        const ready = picked.length > 0;
        const busy = isBusy();
        if (submit) {
            submit.disabled = busy || !ready;
            if (!busy) {
                submit.classList.toggle('lp-btn--disabled', !ready);
                if (ready) {
                    submit.removeAttribute('aria-disabled');
                    submit.title = '';
                } else {
                    submit.setAttribute('aria-disabled', 'true');
                    submit.title = '请先选择要导入解析的文件';
                }
            }
        }
        if (selectionEl) {
            const totalSize = picked.reduce((sum, file) => sum + Number(file.size || 0), 0);
            if (ready) {
                const summary = `已选择 ${picked.length} 个文件，共 ${formatProcessImportFileSize(totalSize)}。`;
                const disabledAttr = busy ? ' disabled aria-disabled="true"' : '';
                selectionEl.innerHTML = `${renderSelectionMessage(summary)}<button type="button" class="lp-link lp-import-selection__clear" data-clear-files aria-label="清空已选择的导入文件"${disabledAttr}>清空</button>`;
            } else {
                selectionEl.innerHTML = renderSelectionMessage(emptyText);
            }
            selectionEl.classList.toggle('is-ready', ready);
        }
        if (pickButton) {
            pickButton.disabled = busy;
            pickButton.classList.toggle('is-disabled', busy);
            if (busy) pickButton.setAttribute('aria-disabled', 'true');
            else pickButton.removeAttribute('aria-disabled');
        }
        if (input) input.disabled = busy;
        dropzone?.classList.toggle('is-disabled', busy);
        listEl?.querySelectorAll('[data-rm]').forEach((button) => {
            button.disabled = busy;
            button.classList.toggle('is-disabled', busy);
            if (busy) button.setAttribute('aria-disabled', 'true');
            else button.removeAttribute('aria-disabled');
        });
    };

    const renderFiles = () => {
        if (listEl) {
            listEl.innerHTML = picked.map((file, index) => {
                const name = escapeHtml(file.name);
                return `
                <li><span class="lp-filelist__main"><strong title="${name}">${name}</strong><small>${formatProcessImportFileSize(file.size)}</small></span>
                <button type="button" class="lp-link" data-rm="${index}" aria-label="移除 ${name}">移除</button></li>`;
            }).join('');
        }
        updateSubmitState();
    };

    const addFiles = (files) => {
        for (const file of Array.from(files || [])) {
            const problem = getProcessDocumentImportFileProblem(file, picked.length);
            if (problem) {
                notify(problem, 'warning');
                continue;
            }
            const duplicateProblem = getProcessDocumentImportDuplicateProblem(file, picked);
            if (duplicateProblem) {
                notify(duplicateProblem, 'warning');
                continue;
            }
            picked.push(file);
        }
        renderFiles();
    };

    const clearFiles = () => {
        if (!picked.length) return;
        picked.splice(0, picked.length);
        if (input) input.value = '';
        renderFiles();
    };

    pickButton?.addEventListener('click', () => {
        if (isBusy()) return;
        input?.click();
    });
    input?.addEventListener('change', () => {
        if (isBusy()) return;
        addFiles(input.files);
        input.value = '';
    });
    listEl?.addEventListener('click', (event) => {
        const target = event.target instanceof Element ? event.target : event.target?.parentElement;
        const removeButton = target?.closest('[data-rm]');
        if (!removeButton) return;
        if (isBusy()) return;
        const index = Number(removeButton.dataset.rm);
        if (Number.isInteger(index) && index >= 0) {
            picked.splice(index, 1);
            renderFiles();
        }
    });
    selectionEl?.addEventListener('click', (event) => {
        const target = event.target instanceof Element ? event.target : event.target?.parentElement;
        if (isBusy()) return;
        if (target?.closest('[data-clear-files]')) clearFiles();
    });
    ['dragover', 'dragenter'].forEach((eventName) => {
        dropzone?.addEventListener(eventName, (event) => {
            event.preventDefault();
            if (isBusy()) {
                dropzone.classList.remove('is-over');
                return;
            }
            dropzone.classList.add('is-over');
        });
    });
    ['dragleave', 'drop'].forEach((eventName) => {
        dropzone?.addEventListener(eventName, (event) => {
            event.preventDefault();
            dropzone.classList.remove('is-over');
        });
    });
    dropzone?.addEventListener('drop', (event) => {
        if (isBusy()) return;
        if (event.dataTransfer?.files) addFiles(event.dataTransfer.files);
    });

    updateSubmitState();
    return {
        getFiles: () => picked.slice(),
        hasFiles: () => picked.length > 0,
        clearFiles,
        updateSubmitState,
    };
}
