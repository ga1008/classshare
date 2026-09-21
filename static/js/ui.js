/**
 * ui.js
 * Global UI utilities for modals, toasts, dropdowns, formatters, and theme management.
 */

import { getLayerSystem } from './lq/layer.js';

const getMarkdownRuntime = () => window.MarkdownRuntime || null;
const TOAST_BRIDGE = Symbol.for('lanshare.ui.toast-bridge.v1');

/** Legacy calls stay synchronous/undefined; the shared notification module is lazy. */
export function showToast(message, type = 'success', duration = 3000) {
    const bridge = document[TOAST_BRIDGE] ||= { loading: null, fallback: null, fallbackLease: null };
    const normalized = String(message ?? '').replace(/\s+/g, ' ').trim().slice(0, 280) || '操作已完成';
    const tone = type === 'error' ? 'danger' : ['primary', 'success', 'warning', 'danger', 'info', 'neutral'].includes(type) ? type : 'info';
    const milliseconds = Number(duration);
    const delay = Number.isFinite(milliseconds) && milliseconds > 0 ? Math.min(milliseconds, 2147483647) : 0;
    bridge.loading ||= import('./lq/toast.js');
    void bridge.loading.then(module => {
        bridge.fallbackLease?.destroy(); bridge.fallbackLease = null;
        bridge.fallback?.remove(); bridge.fallback = null;
        module.toast(normalized, { tone, duration: delay }, document);
    }).catch(() => {
        bridge.loading = null;
        if (!bridge.fallback?.isConnected) {
            bridge.fallback = document.createElement('div');
            bridge.fallback.className = 'lq-toast-fallback';
            bridge.fallback.setAttribute('role', 'status');
            document.body.append(bridge.fallback);
            bridge.fallbackLease = getLayerSystem(document).registerCompanion(bridge.fallback, {
                onRelease: () => { bridge.fallback?.remove(); bridge.fallback = null; bridge.fallbackLease = null; },
            });
        }
        // One plaintext failure notice, never a second timer/notification queue.
        bridge.fallback.textContent = `${normalized}（通知组件暂不可用）`;
        const dismiss = document.createElement('button'); dismiss.type = 'button'; dismiss.textContent = '关闭通知';
        dismiss.addEventListener('click', () => { bridge.fallbackLease?.destroy(); bridge.fallbackLease = null; bridge.fallback?.remove(); bridge.fallback = null; }, { once: true });
        bridge.fallback.append(dismiss);
    });
}

/**
 * showMessage - alias for showToast, used by manage pages and exam_take
 */
export const showMessage = showToast;

// Modals Management
const MODAL_BRIDGE = Symbol.for('lanshare.ui.modal-bridge.v1');
const modalBridge = document[MODAL_BRIDGE] ||= { handles: new WeakMap(), listening: false };

function registerModal(modalOverlay, initialFocus = true) {
    const layer = getLayerSystem(document);
    const handle = layer.open(modalOverlay, {
        type: 'modal', surface: modalOverlay.querySelector('.modal-dialog,.modal-box,.modal-content,.modal') || modalOverlay,
        initialFocus: initialFocus ? undefined : false,
        onCloseRequested: () => modalOverlay.classList.remove('show'),
        onClose: () => { modalOverlay.style.display = 'none'; },
        onDestroy: () => { modalOverlay.classList.remove('show'); modalOverlay.style.display = 'none'; },
    });
    modalBridge.handles.set(modalOverlay, handle);
    return handle;
}

export function openModal(modalId) {
    const modalOverlay = document.getElementById(modalId);
    if (modalOverlay) {
        modalOverlay.style.display = 'flex';
        modalOverlay.hidden = false;
        // The existing show-class transition remains the visual contract.
        window.getComputedStyle(modalOverlay).opacity;
        modalOverlay.classList.add('show');
        registerModal(modalOverlay);
    } else {
        console.error(`Modal with ID '${modalId}' not found.`);
    }
}

export function closeModal(modalId) {
    const modalOverlay = document.getElementById(modalId);
    if (modalOverlay) {
        let handle = modalBridge.handles.get(modalOverlay);
        if (!handle || ['closed', 'destroyed'].includes(handle.state)) {
            if (modalOverlay.hidden || window.getComputedStyle(modalOverlay).display === 'none') return;
            handle = registerModal(modalOverlay, false);
        }
        return getLayerSystem(document).close(handle, 'programmatic');
    }
}

if (!modalBridge.listening) {
    modalBridge.listening = true;
    document.addEventListener('click', (event) => {
        const button = event.target.closest?.('[data-dismiss="modal"]');
        const backdrop = event.target.matches?.('.modal-backdrop') ? event.target : null;
        const modal = button?.closest('.modal-backdrop,.modal-overlay') || backdrop;
        if (!modal || modal.hasAttribute('data-feedback-managed')) return;
        const current = modalBridge.handles.get(modal);
        if (!button && current && !['closed', 'destroyed'].includes(current.state)) return;
        event.preventDefault(); closeModal(modal.id);
    });
}

// Formatters
export function formatSize(bytes) {
    if (!bytes && bytes !== 0) return '--';
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

export function formatDate(dateString) {
    if (!dateString) return '';
    try {
        const d = new Date(dateString);
        if (isNaN(d.getTime())) return dateString;
        return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
    } catch {
        return dateString;
    }
}

/**
 * formatDateLocal - alias for formatDate, used by assignment detail pages
 */
export const formatDateLocal = formatDate;

export function escapeHtml(unsafe) {
    if (!unsafe && unsafe !== 0) return '';
    return String(unsafe)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

export function getFileIcon(filename) {
    const ext = (filename.split('.').pop() || '').toLowerCase();
    const iconMap = {
        pdf: { color: '#ef4444', label: 'PDF' },
        doc: { color: '#2563eb', label: 'DOC' },
        docx: { color: '#2563eb', label: 'DOC' },
        xls: { color: '#10b981', label: 'XLS' },
        xlsx: { color: '#10b981', label: 'XLS' },
        ppt: { color: '#f59e0b', label: 'PPT' },
        pptx: { color: '#f59e0b', label: 'PPT' },
        zip: { color: '#64748b', label: 'ZIP' },
        rar: { color: '#64748b', label: 'RAR' },
        '7z': { color: '#64748b', label: '7Z' },
        jpg: { color: '#8b5cf6', label: 'IMG' },
        jpeg: { color: '#8b5cf6', label: 'IMG' },
        png: { color: '#8b5cf6', label: 'PNG' },
        gif: { color: '#8b5cf6', label: 'GIF' },
        svg: { color: '#8b5cf6', label: 'SVG' },
        mp4: { color: '#f43f5e', label: 'VID' },
        avi: { color: '#f43f5e', label: 'VID' },
        mp3: { color: '#06b6d4', label: 'AUD' },
        py: { color: '#3b82f6', label: 'PY' },
        js: { color: '#eab308', label: 'JS' },
        java: { color: '#d97706', label: 'JAVA' },
        c: { color: '#64748b', label: 'C' },
        cpp: { color: '#64748b', label: 'C++' },
        html: { color: '#f97316', label: 'HTML' },
        css: { color: '#3b82f6', label: 'CSS' },
        txt: { color: '#94a3b8', label: 'TXT' },
        md: { color: '#475569', label: 'MD' },
    };
    return iconMap[ext] || { color: '#94a3b8', label: ext ? ext.toUpperCase().substring(0, 4) : 'FILE' };
}

/**
 * renderMarkdown - render markdown content into a DOM element by ID
 * Uses the shared markdown runtime when available, falls back to escaped text.
 */
export function renderMarkdown(elementId, content) {
    const el = document.getElementById(elementId);
    if (!el) return;
    if (content == null || content === '') {
        el.innerHTML = '<p class="text-muted">暂无内容</p>';
        return;
    }
    try {
        const text = String(content).trim();
        const runtime = getMarkdownRuntime();
        if (runtime && typeof runtime.renderIntoElement === 'function') {
            runtime.renderIntoElement(el, text, {
                emptyHtml: '<p class="text-muted">暂无内容</p>',
                fallbackMode: 'lines',
                silent: true,
            });
        } else {
            el.innerHTML = escapeHtml(text).replace(/\n/g, '<br>');
        }
    } catch (error) {
        console.error('Markdown rendering error:', error);
        el.innerHTML = escapeHtml(String(content)).replace(/\n/g, '<br>');
    }
}

window.UI = {
    showToast,
    showMessage,
    openModal,
    closeModal,
    formatSize,
    formatDate,
    formatDateLocal,
    escapeHtml,
    getFileIcon,
    renderMarkdown
};

window.showMessage = showToast;
window.sizeFormat = formatSize;
