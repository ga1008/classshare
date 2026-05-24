const DOCX_MIME = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document';

function fallbackNotify(message, type = 'info') {
    const notifier = window.UI?.showToast || window.UI?.showMessage || window.showToast || window.showMessage;
    if (typeof notifier === 'function') {
        notifier(message, type);
    }
}

function normalizeDocxFilename(name) {
    const cleaned = String(name || '复习导出.docx')
        .replace(/[\\/:*?"<>|]+/g, '_')
        .replace(/\s+/g, ' ')
        .trim();
    return cleaned.toLowerCase().endsWith('.docx') ? cleaned : `${cleaned || '复习导出'}.docx`;
}

function filenameFromDisposition(header) {
    if (!header) return '';
    const starMatch = header.match(/filename\*=UTF-8''([^;]+)/i);
    if (starMatch?.[1]) {
        try {
            return decodeURIComponent(starMatch[1].trim().replace(/^"|"$/g, ''));
        } catch {
            return starMatch[1].trim().replace(/^"|"$/g, '');
        }
    }
    const plainMatch = header.match(/filename="?([^";]+)"?/i);
    return plainMatch?.[1]?.trim() || '';
}

async function readErrorMessage(response) {
    try {
        const data = await response.json();
        return data?.detail || data?.message || `导出失败（${response.status}）`;
    } catch {
        return `导出失败（${response.status}）`;
    }
}

function setButtonBusy(button, busy) {
    if (!button) return;
    if (busy) {
        if (!button.dataset.originalHtml) {
            button.dataset.originalHtml = button.innerHTML;
        }
        button.setAttribute('aria-busy', 'true');
        button.setAttribute('aria-disabled', 'true');
        button.style.pointerEvents = 'none';
        if ('disabled' in button) button.disabled = true;
        button.innerHTML = '<span class="spinner spinner-sm" style="display:inline-block;vertical-align:-0.15em;margin-right:0.35rem;"></span><span>正在准备...</span>';
        return;
    }
    button.removeAttribute('aria-busy');
    button.removeAttribute('aria-disabled');
    button.style.pointerEvents = '';
    if ('disabled' in button) button.disabled = false;
    if (button.dataset.originalHtml) {
        button.innerHTML = button.dataset.originalHtml;
        delete button.dataset.originalHtml;
    }
}

async function chooseSaveHandle(suggestedName) {
    if (!window.isSecureContext || typeof window.showSaveFilePicker !== 'function') {
        return null;
    }
    try {
        return await window.showSaveFilePicker({
            suggestedName,
            types: [{
                description: 'Word 文档',
                accept: { [DOCX_MIME]: ['.docx'] },
            }],
        });
    } catch (error) {
        if (error?.name === 'AbortError') {
            return { cancelled: true };
        }
        throw error;
    }
}

function triggerBrowserDownload(blob, filename) {
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    link.style.display = 'none';
    document.body.appendChild(link);
    link.click();
    setTimeout(() => {
        URL.revokeObjectURL(url);
        link.remove();
    }, 1200);
}

async function saveBlob(handle, blob) {
    const writable = await handle.createWritable();
    await writable.write(blob);
    await writable.close();
}

export function initReviewDocxExportButtons(options = {}) {
    const selector = options.selector || '[data-review-docx-export]';
    const notify = typeof options.notify === 'function' ? options.notify : fallbackNotify;
    document.querySelectorAll(selector).forEach((button) => {
        if (button.dataset.reviewDocxBound === '1') return;
        button.dataset.reviewDocxBound = '1';
        button.addEventListener('click', async (event) => {
            event.preventDefault();
            const url = button.dataset.exportUrl || button.getAttribute('href');
            if (!url || button.getAttribute('aria-busy') === 'true') return;

            const suggestedName = normalizeDocxFilename(button.dataset.suggestedFilename || '复习导出.docx');
            setButtonBusy(button, true);
            try {
                let saveHandle = null;
                if (window.isSecureContext && typeof window.showSaveFilePicker === 'function') {
                    notify('请选择保存位置，随后会开始准备 Word 文档。', 'info');
                    saveHandle = await chooseSaveHandle(suggestedName);
                    if (saveHandle?.cancelled) {
                        notify('已取消导出。', 'info');
                        return;
                    }
                } else {
                    notify('正在准备 Word 文档，请稍候...', 'info');
                }

                const response = await fetch(url, {
                    method: 'GET',
                    credentials: 'same-origin',
                    headers: { Accept: DOCX_MIME },
                });
                if (!response.ok) {
                    throw new Error(await readErrorMessage(response));
                }
                const blob = await response.blob();
                const filename = normalizeDocxFilename(
                    filenameFromDisposition(response.headers.get('Content-Disposition')) || suggestedName
                );

                if (saveHandle) {
                    await saveBlob(saveHandle, blob);
                    notify('复习 Word 已保存。', 'success');
                } else {
                    triggerBrowserDownload(blob, filename);
                    notify('复习 Word 已开始下载，请留意浏览器下载提示。', 'success');
                }
            } catch (error) {
                notify(error?.message || '导出失败，请稍后重试。', 'error');
            } finally {
                setButtonBusy(button, false);
            }
        });
    });
}
