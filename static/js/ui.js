/**
 * ui.js
 * Global UI utilities for modals, toasts, dropdowns, formatters, and theme management.
 */

// Toast Notifications
const createToastContainer = () => {
    let container = document.getElementById('toast-container');
    if (!container) {
        container = document.createElement('div');
        container.id = 'toast-container';
        container.className = 'toast-container';
        document.body.appendChild(container);
    }
    return container;
};

/**
 * Display a toast notification
 * @param {string} message - Message text
 * @param {string} type - 'success', 'error', 'info', 'warning'
 * @param {number} duration - ms to display
 */
export function showToast(message, type = 'success', duration = 3000) {
    const container = createToastContainer();
    const toast = document.createElement('div');

    const icons = {
        success: `<svg viewBox="0 0 24 24"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>`,
        error: `<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>`,
        info: `<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>`,
        warning: `<svg viewBox="0 0 24 24"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>`
    };

    const typeClass = type === 'error' ? 'danger' : type;
    toast.className = `toast toast-${typeClass}`;
    toast.innerHTML = `
        <div class="toast-icon">${icons[type] || icons.info}</div>
        <div class="toast-content">
            <div class="toast-message">${escapeHtml(message)}</div>
        </div>
        <button class="toast-close" aria-label="Close" onclick="this.parentElement.remove()">
            <svg viewBox="0 0 24 24"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
        </button>
    `;

    container.appendChild(toast);

    // Trigger animation
    requestAnimationFrame(() => {
        toast.classList.add('show');
    });

    if (duration > 0) {
        setTimeout(() => {
            toast.classList.remove('show');
            toast.classList.add('hide');
            toast.addEventListener('transitionend', () => toast.remove());
        }, duration);
    }
}

/**
 * showMessage - alias for showToast, used by manage pages and exam_take
 */
export const showMessage = showToast;

// Modals Management
export function openModal(modalId) {
    const modalOverlay = document.getElementById(modalId);
    if (modalOverlay) {
        modalOverlay.style.display = 'flex';
        // Allow CSS transitions to pick up display change
        requestAnimationFrame(() => {
            modalOverlay.classList.add('show');
        });
        // Prevent body scroll
        document.body.style.overflow = 'hidden';
    } else {
        console.error(`Modal with ID '${modalId}' not found.`);
    }
}

export function closeModal(modalId) {
    const modalOverlay = document.getElementById(modalId);
    if (modalOverlay) {
        modalOverlay.classList.remove('show');
        // Wait for transition to end before hiding
        setTimeout(() => {
            modalOverlay.style.display = 'none';
            document.body.style.overflow = '';
        }, 300); // matches --transition-normal in CSS
    }
}

// Initialize Modal Close Buttons automatically
document.addEventListener('DOMContentLoaded', () => {
    // Close button click
    document.querySelectorAll('[data-dismiss="modal"]').forEach(btn => {
        btn.addEventListener('click', (e) => {
            const modal = e.target.closest('.modal-backdrop');
            if (modal) closeModal(modal.id);
        });
    });

    // Click outside to close
    document.querySelectorAll('.modal-backdrop').forEach(overlay => {
        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) {
                closeModal(overlay.id);
            }
        });
    });
});

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
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

export function getFileIcon(filename) {
    const ext = (filename.split('.').pop() || '').toLowerCase();
    const iconMap = {
        'pdf': { color: '#ef4444', label: 'PDF' },
        'doc': { color: '#2563eb', label: 'DOC' },
        'docx': { color: '#2563eb', label: 'DOC' },
        'xls': { color: '#10b981', label: 'XLS' },
        'xlsx': { color: '#10b981', label: 'XLS' },
        'ppt': { color: '#f59e0b', label: 'PPT' },
        'pptx': { color: '#f59e0b', label: 'PPT' },
        'zip': { color: '#64748b', label: 'ZIP' },
        'rar': { color: '#64748b', label: 'RAR' },
        '7z': { color: '#64748b', label: '7Z' },
        'jpg': { color: '#8b5cf6', label: 'IMG' },
        'jpeg': { color: '#8b5cf6', label: 'IMG' },
        'png': { color: '#8b5cf6', label: 'PNG' },
        'gif': { color: '#8b5cf6', label: 'GIF' },
        'svg': { color: '#8b5cf6', label: 'SVG' },
        'mp4': { color: '#f43f5e', label: 'VID' },
        'avi': { color: '#f43f5e', label: 'VID' },
        'mp3': { color: '#06b6d4', label: 'AUD' },
        'py': { color: '#3b82f6', label: 'PY' },
        'js': { color: '#eab308', label: 'JS' },
        'java': { color: '#d97706', label: 'JAVA' },
        'c': { color: '#64748b', label: 'C' },
        'cpp': { color: '#64748b', label: 'C++' },
        'html': { color: '#f97316', label: 'HTML' },
        'css': { color: '#3b82f6', label: 'CSS' },
        'txt': { color: '#94a3b8', label: 'TXT' },
        'md': { color: '#475569', label: 'MD' },
    };
    return iconMap[ext] || { color: '#94a3b8', label: ext ? ext.toUpperCase().substring(0, 4) : 'FILE' };
}

/**
 * renderMarkdown - render markdown content into a DOM element by ID
 * Uses marked.js if available, falls back to escaped text with line breaks
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
        if (typeof marked !== 'undefined' && marked.parse) {
            el.innerHTML = marked.parse(text);
        } else {
            el.innerHTML = escapeHtml(text).replace(/\n/g, '<br>');
        }
    } catch (e) {
        console.error('Markdown rendering error:', e);
        el.innerHTML = escapeHtml(String(content)).replace(/\n/g, '<br>');
    }
}

// Ensure global scope availability
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

// Aliases for backwards compatibility with old inline scripts
window.showMessage = showToast;
window.sizeFormat = formatSize;
