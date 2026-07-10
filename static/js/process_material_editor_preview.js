import { APIError, handleAuthFailureResponse } from './api.js';

export function openPendingPreviewWindow(showToast) {
    const previewWindow = window.open('about:blank', '_blank');
    if (!previewWindow) {
        showToast('浏览器阻止了新窗口，请允许弹出窗口后重试', 'error');
        return null;
    }
    try {
        previewWindow.opener = null;
        previewWindow.document.title = '正在准备预览';
        previewWindow.document.body.innerHTML = `
            <main style="font-family: system-ui, sans-serif; padding: 28px; color: #1f2937;">
                <strong>正在保存并准备预览…</strong>
                <p style="margin-top: 8px; color: #6b7280;">请稍候，完成后会自动显示最新内容。</p>
            </main>`;
    } catch (_) {
        // Some browser policies may block writing to the placeholder window.
    }
    return previewWindow;
}

export function setPreviewLinkBusy(link, busy, busyText = '保存中…') {
    if (!link) return;
    if (busy) {
        if (link.dataset.previewBusy === 'true') return;
        link.dataset.previewBusy = 'true';
        link.dataset.originalText = link.textContent || '';
        link.textContent = busyText;
        link.classList.add('lp-btn--disabled');
        link.setAttribute('aria-disabled', 'true');
        return;
    }
    if ('originalText' in link.dataset) {
        link.textContent = link.dataset.originalText;
        delete link.dataset.originalText;
    }
    delete link.dataset.previewBusy;
    link.classList.remove('lp-btn--disabled');
    link.removeAttribute('aria-disabled');
}

export function isPreviewLinkBusy(link) {
    return link?.dataset.previewBusy === 'true';
}

export function movePendingPreviewWindow(previewWindow, url) {
    if (!previewWindow) return;
    previewWindow.location.href = url;
}

export function closePendingPreviewWindow(previewWindow) {
    try {
        previewWindow?.close();
    } catch (_) {
        // Ignore close errors from browser policies.
    }
}

function normalizeDownloadErrorMessage(message) {
    const text = String(message || '').replace(/\s+/g, ' ').trim();
    if (!text) return '下载失败，请稍后重试。';
    if (/<!doctype html>|<html[\s>]/i.test(text)) return '服务异常，请稍后重试。';
    return text.length > 220 ? `${text.slice(0, 220)}...` : text;
}

async function parseDownloadError(response) {
    const contentType = response.headers.get('content-type') || '';
    let data = null;
    try {
        data = contentType.includes('application/json') ? await response.json() : await response.text();
    } catch (_) {
        data = null;
    }
    data = await handleAuthFailureResponse(response, data);
    if (data && typeof data === 'object') {
        const message = data.error?.message || data.message || data.detail || response.statusText;
        return { data, message: normalizeDownloadErrorMessage(message) };
    }
    return { data, message: normalizeDownloadErrorMessage(data || response.statusText) };
}

function decodeDispositionFilename(value) {
    if (!value) return '';
    const encoded = value.match(/filename\*=UTF-8''([^;]+)/i);
    if (encoded?.[1]) {
        try { return decodeURIComponent(encoded[1].replace(/^"|"$/g, '')); }
        catch (_) { return encoded[1].replace(/^"|"$/g, ''); }
    }
    const plain = value.match(/filename="?([^";]+)"?/i);
    return plain?.[1] ? plain[1].trim() : '';
}

function fallbackExportFilename(url, label) {
    let ext = 'bin';
    try {
        const parsed = new URL(url, window.location.origin);
        const fmt = parsed.searchParams.get('fmt') || parsed.searchParams.get('format');
        if (/^(docx|pdf|png|xlsx|xls)$/i.test(fmt || '')) ext = fmt.toLowerCase();
    } catch (_) {
        // Keep the generic extension if URL parsing fails.
    }
    return `process-material-${String(label || 'file').toLowerCase()}.${ext}`;
}

function saveBlob(blob, filename) {
    const objectUrl = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = objectUrl;
    anchor.download = filename;
    anchor.style.display = 'none';
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
}

async function downloadProcessMaterialExport(url, label) {
    const response = await fetch(url, {
        method: 'GET',
        credentials: 'same-origin',
        headers: {
            Accept: 'application/octet-stream,application/json;q=0.9,text/plain;q=0.8',
        },
    });
    if (!response.ok) {
        const parsed = await parseDownloadError(response);
        throw new APIError(parsed.message, response.status, parsed.data);
    }
    const blob = await response.blob();
    const filename = decodeDispositionFilename(response.headers.get('content-disposition'))
        || fallbackExportFilename(url, label);
    saveBlob(blob, filename);
    return { filename };
}

function showExportDownloadToast(showToast, label, { saved = true } = {}) {
    if (typeof showToast !== 'function') return;
    const prefix = saved ? '已保存，' : '';
    showToast(`${prefix}正在准备下载${label}。`, 'success');
}

function showExportDownloadError(showToast, error, label) {
    if (error?.suppressToast) return;
    if (typeof showToast === 'function') {
        showToast(error?.message || `下载${label}失败，请稍后重试。`, 'error');
    }
}

export async function startProcessMaterialExportDownload(url, showToast, label = '文件') {
    if (!url) return false;
    showExportDownloadToast(showToast, label, { saved: true });
    try {
        await downloadProcessMaterialExport(url, label);
        return true;
    } catch (error) {
        showExportDownloadError(showToast, error, label);
        return false;
    }
}

function setTemporaryExportBusy(trigger, busy) {
    if (!(trigger instanceof HTMLElement)) return;
    if (busy) {
        if (trigger.dataset.exportBusy === 'true') return;
        trigger.dataset.exportBusy = 'true';
        trigger.dataset.originalText = trigger.textContent || '';
        trigger.textContent = '准备下载…';
        trigger.classList.add('lp-btn--disabled');
        trigger.setAttribute('aria-disabled', 'true');
        if ('disabled' in trigger) trigger.disabled = true;
        return;
    }
    if ('originalText' in trigger.dataset) {
        trigger.textContent = trigger.dataset.originalText;
        delete trigger.dataset.originalText;
    }
    delete trigger.dataset.exportBusy;
    trigger.classList.remove('lp-btn--disabled');
    trigger.removeAttribute('aria-disabled');
    if ('disabled' in trigger) trigger.disabled = false;
}

export async function startProcessMaterialExportDownloadFromTrigger(trigger, showToast, { saved = false } = {}) {
    if (!(trigger instanceof HTMLElement)) return;
    if (trigger.dataset.exportBusy === 'true') return;
    const url = trigger.dataset.processExportUrl || '';
    if (!url) return;
    const label = trigger.dataset.processExportLabel || '文件';
    setTemporaryExportBusy(trigger, true);
    showExportDownloadToast(showToast, label, { saved });
    try {
        await downloadProcessMaterialExport(url, label);
    } catch (error) {
        showExportDownloadError(showToast, error, label);
    } finally {
        setTemporaryExportBusy(trigger, false);
    }
}

export function bindProcessMaterialExportDownloadActions(root, showToast, options = {}) {
    root?.addEventListener('click', async (event) => {
        const target = event.target instanceof Element ? event.target : event.target?.parentElement;
        const trigger = target?.closest('[data-process-export-url]');
        if (!trigger || !root.contains(trigger)) return;
        event.preventDefault();
        await startProcessMaterialExportDownloadFromTrigger(trigger, showToast, options);
    });
}
