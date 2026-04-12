import { apiFetch } from './api.js';
import { ChunkedUploader } from './upload.js';
import { closeModal, escapeHtml, formatDate, formatSize, getFileIcon, openModal, showToast } from './ui.js';

let config = null;

const state = {
    files: [],
    activeFileId: null,
};

function refs() {
    return {
        list: document.getElementById('file-list-container'),
        uploadZone: document.getElementById('uploadZone'),
        fileInput: document.getElementById('fileInput'),
        uploadProgressArea: document.getElementById('uploadProgressArea'),
        modal: document.getElementById('shared-file-modal'),
        modalTitle: document.getElementById('shared-file-modal-title'),
        modalMeta: document.getElementById('shared-file-modal-meta'),
        modalDescriptionView: document.getElementById('shared-file-description-view'),
        modalStatus: document.getElementById('shared-file-download-status'),
        modalStatusText: document.getElementById('shared-file-download-status-text'),
        modalHint: document.getElementById('shared-file-download-hint'),
        modalLinkActions: document.getElementById('shared-file-link-actions'),
        modalCopyLinkBtn: document.getElementById('shared-file-copy-link-btn'),
        modalOpenLinkBtn: document.getElementById('shared-file-open-link-btn'),
        modalDownloadSlot: document.getElementById('shared-file-download-slot'),
        modalDescriptionInput: document.getElementById('shared-file-description-input'),
        modalOriginalLinkInput: document.getElementById('shared-file-original-link-input'),
        modalTeacherForm: document.getElementById('shared-file-teacher-form'),
        modalSaveBtn: document.getElementById('shared-file-save-btn'),
        modalDeleteBtn: document.getElementById('shared-file-delete-btn'),
    };
}

function isTeacherView() {
    return config?.userInfo?.role === 'teacher';
}

function getFileById(fileId) {
    const numericId = Number(fileId);
    return state.files.find((item) => Number(item.id) === numericId) || null;
}

function formatDescription(text) {
    const normalized = String(text || '').trim();
    if (!normalized) {
        return '<p class="text-muted">教师暂未补充文件详情。</p>';
    }
    return normalized
        .split('\n')
        .map((line) => `<p>${escapeHtml(line)}</p>`)
        .join('');
}

function truncateText(text, maxLength = 96) {
    const normalized = String(text || '').trim();
    if (!normalized) return '';
    if (normalized.length <= maxLength) return normalized;
    return `${normalized.slice(0, maxLength - 1)}…`;
}

function buildBlockedAction(file, extraClassName = '') {
    const title = escapeHtml(file.download_blocked_reason || '已限制下载');
    const className = `btn btn-ghost btn-sm btn-icon resource-download-blocked ${extraClassName}`.trim();
    return `
        <button
            type="button"
            class="${className}"
            data-action="blocked"
            data-file-id="${file.id}"
            title="${title}"
            aria-label="${title}"
        >
            <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <circle cx="12" cy="12" r="9"></circle>
                <path d="M5 5l14 14"></path>
            </svg>
        </button>
    `;
}

function buildDownloadAction(file) {
    if (!file.download_allowed) {
        return buildBlockedAction(file);
    }

    return `
        <a
            href="${escapeHtml(file.download_url || `/download/course_file/${file.id}`)}"
            class="btn btn-ghost btn-sm btn-icon"
            data-action="download"
            data-file-id="${file.id}"
            title="下载文件"
            aria-label="下载文件"
        >
            <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
                <polyline points="7 10 12 15 17 10"></polyline>
                <line x1="12" y1="15" x2="12" y2="3"></line>
            </svg>
        </a>
    `;
}

function buildCardBadges(file) {
    const badges = [];
    if (file.description) badges.push('<span class="resource-file-badge">详情</span>');
    if (file.original_link) badges.push('<span class="resource-file-badge">原始链接</span>');
    if (!file.download_allowed) badges.push('<span class="resource-file-badge is-danger">大小超限，请使用原始链接下载</span>');
    return badges.join('');
}

function buildFileCard(file) {
    const icon = getFileIcon(file.file_name || file.filename || 'file');
    const fileName = file.file_name || file.filename || '未命名文件';
    const fileSize = formatSize(file.file_size || file.size || 0);
    const uploadTime = formatDate(file.uploaded_at || file.upload_time || file.created_at || '');
    const summary = truncateText(file.description || '');

    return `
        <article class="card card-interactive resource-file-card" data-file-id="${file.id}" tabindex="0" role="button" aria-label="查看 ${escapeHtml(fileName)} 详情">
            <div class="resource-file-main">
                <div class="resource-file-icon" style="background:${icon.color}15;color:${icon.color};">${escapeHtml(icon.label)}</div>
                <div class="resource-file-copy">
                    <div class="resource-file-title-row">
                        <strong class="resource-file-title" title="${escapeHtml(fileName)}">${escapeHtml(fileName)}</strong>
                        <div class="resource-file-badges">${buildCardBadges(file)}</div>
                    </div>
                    <div class="resource-file-meta">${escapeHtml(fileSize)}${uploadTime ? ` · ${escapeHtml(uploadTime)}` : ''}</div>
                    ${summary ? `<p class="resource-file-summary">${escapeHtml(summary)}</p>` : ''}
                </div>
            </div>
            <div class="resource-file-actions">
                <button type="button" class="btn btn-ghost btn-sm btn-icon" data-action="details" data-file-id="${file.id}" title="查看详情" aria-label="查看详情">
                    <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                        <circle cx="12" cy="12" r="10"></circle>
                        <line x1="12" y1="16" x2="12" y2="12"></line>
                        <line x1="12" y1="8" x2="12.01" y2="8"></line>
                    </svg>
                </button>
                ${buildDownloadAction(file)}
                ${isTeacherView() ? `
                    <button type="button" class="btn btn-ghost btn-sm btn-icon text-danger" data-action="delete" data-file-id="${file.id}" title="删除文件" aria-label="删除文件">
                        <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                            <polyline points="3 6 5 6 21 6"></polyline>
                            <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
                        </svg>
                    </button>
                ` : ''}
            </div>
        </article>
    `;
}

function renderFiles() {
    const { list } = refs();
    if (!list) return;

    if (!state.files.length) {
        list.innerHTML = `
            <div class="empty-state">
                <p class="text-muted">当前课堂还没有共享资源。</p>
            </div>
        `;
        return;
    }

    list.innerHTML = state.files.map((file) => buildFileCard(file)).join('');
}

async function loadFiles() {
    const { list } = refs();
    if (!list) return;

    try {
        const data = await apiFetch(`/api/courses/${config.classOfferingId}/files`, { silent: true });
        state.files = Array.isArray(data?.files) ? data.files : [];
        renderFiles();

        if (state.activeFileId && !getFileById(state.activeFileId)) {
            closeFileDetails();
        } else if (state.activeFileId) {
            renderFileModal(getFileById(state.activeFileId));
        }
    } catch (error) {
        console.error('Failed to load files:', error);
        list.innerHTML = `
            <div class="empty-state">
                <p class="text-danger">资源加载失败，请稍后重试。</p>
            </div>
        `;
    }
}

function closeFileDetails() {
    state.activeFileId = null;
    closeModal('shared-file-modal');
}

function renderFileModal(file) {
    if (!file) return;

    const {
        modalTitle,
        modalMeta,
        modalDescriptionView,
        modalStatus,
        modalStatusText,
        modalHint,
        modalLinkActions,
        modalCopyLinkBtn,
        modalOpenLinkBtn,
        modalDownloadSlot,
        modalDescriptionInput,
        modalOriginalLinkInput,
        modalTeacherForm,
        modalDeleteBtn,
    } = refs();

    if (!modalTitle || !modalMeta || !modalDescriptionView || !modalDownloadSlot) {
        return;
    }

    const fileName = file.file_name || '未命名文件';
    const metaParts = [
        formatSize(file.file_size || 0),
        file.uploaded_at ? formatDate(file.uploaded_at) : '',
        file.download_limit_enabled ? `下载上限 ${file.download_limit_label}` : '下载不限大小',
    ].filter(Boolean);

    modalTitle.textContent = fileName;
    modalMeta.textContent = metaParts.join(' · ');
    modalDescriptionView.innerHTML = formatDescription(file.description);

    if (modalDescriptionInput) modalDescriptionInput.value = file.description || '';
    if (modalOriginalLinkInput) modalOriginalLinkInput.value = file.original_link || '';

    if (modalTeacherForm) {
        modalTeacherForm.hidden = !isTeacherView();
    }
    if (modalDeleteBtn) {
        modalDeleteBtn.hidden = !isTeacherView();
    }

    if (modalStatus && modalStatusText) {
        modalStatus.hidden = !!file.download_allowed;
        modalStatusText.textContent = file.download_blocked_reason || '已限制下载';
    }

    if (modalHint) {
        if (file.original_link && !file.download_allowed) {
            modalHint.textContent = '当前文件已限制从课堂内下载，可尝试打开教师提供的原始链接。';
        } else if (file.original_link) {
            modalHint.textContent = '教师提供了原始链接，必要时可前往外部来源获取文件。';
        } else if (file.download_limit_enabled && file.download_allowed) {
            modalHint.textContent = `当前课堂已启用大文件下载限制，单文件上限为 ${file.download_limit_label}。`;
        } else {
            modalHint.textContent = '点击下载按钮可直接获取当前共享文件。';
        }
    }

    if (modalLinkActions && modalCopyLinkBtn && modalOpenLinkBtn) {
        const hasOriginalLink = Boolean(file.original_link);
        modalLinkActions.hidden = !hasOriginalLink;
        modalCopyLinkBtn.disabled = !hasOriginalLink;
        modalOpenLinkBtn.disabled = !hasOriginalLink;
        modalCopyLinkBtn.dataset.link = file.original_link || '';
        modalOpenLinkBtn.dataset.link = file.original_link || '';
    }

    modalDownloadSlot.innerHTML = file.download_allowed
        ? `
            <a href="${escapeHtml(file.download_url || `/download/course_file/${file.id}`)}" class="btn btn-primary">
                下载文件
            </a>
        `
        : `
            <button type="button" class="btn btn-danger resource-download-blocked-btn" data-action="blocked" data-file-id="${file.id}" title="${escapeHtml(file.download_blocked_reason || '已限制下载')}">
                <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                    <circle cx="12" cy="12" r="9"></circle>
                    <path d="M5 5l14 14"></path>
                </svg>
                已限制下载
            </button>
        `;
}

function openFileDetails(fileId) {
    const file = getFileById(fileId);
    if (!file) return;
    state.activeFileId = Number(file.id);
    renderFileModal(file);
    openModal('shared-file-modal');
}

async function copyText(value) {
    if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(value);
        return;
    }

    const helper = document.createElement('textarea');
    helper.value = value;
    helper.setAttribute('readonly', 'true');
    helper.style.position = 'fixed';
    helper.style.opacity = '0';
    document.body.appendChild(helper);
    helper.focus();
    helper.select();
    try {
        document.execCommand('copy');
    } finally {
        helper.remove();
    }
}

async function saveActiveFileMetadata() {
    if (!isTeacherView() || !state.activeFileId) return;

    const { modalSaveBtn, modalDescriptionInput, modalOriginalLinkInput } = refs();
    if (!modalSaveBtn || !modalDescriptionInput || !modalOriginalLinkInput) return;

    const originalLabel = modalSaveBtn.textContent;
    modalSaveBtn.disabled = true;
    modalSaveBtn.textContent = '保存中...';

    try {
        const result = await apiFetch(`/api/files/${state.activeFileId}/metadata`, {
            method: 'PUT',
            body: {
                description: modalDescriptionInput.value,
                original_link: modalOriginalLinkInput.value,
            },
        });

        const updatedFile = result?.file || {};
        state.files = state.files.map((item) => {
            if (Number(item.id) !== Number(state.activeFileId)) return item;
            return {
                ...item,
                description: updatedFile.description ?? item.description,
                original_link: updatedFile.original_link ?? item.original_link,
            };
        });
        renderFiles();
        renderFileModal(getFileById(state.activeFileId));
        showToast(result?.message || '文件详情已更新', 'success');
    } catch (error) {
        console.error('Failed to update file metadata:', error);
        showToast(error.message || '文件详情保存失败', 'error');
    } finally {
        modalSaveBtn.disabled = false;
        modalSaveBtn.textContent = originalLabel;
    }
}

function setupUploadZone() {
    const { uploadZone, fileInput } = refs();
    if (!uploadZone || !fileInput) return;

    fileInput.addEventListener('change', (event) => {
        handleFiles(event.target.files);
        fileInput.value = '';
    });

    uploadZone.addEventListener('click', (event) => {
        if (event.target.tagName !== 'BUTTON') {
            fileInput.click();
        }
    });

    uploadZone.addEventListener('dragover', (event) => {
        event.preventDefault();
        uploadZone.classList.add('drag-over');
    });

    uploadZone.addEventListener('dragleave', () => {
        uploadZone.classList.remove('drag-over');
    });

    uploadZone.addEventListener('drop', (event) => {
        event.preventDefault();
        uploadZone.classList.remove('drag-over');
        handleFiles(event.dataTransfer.files);
    });
}

function handleFiles(fileList) {
    const { uploadProgressArea } = refs();
    if (!fileList || fileList.length === 0 || !uploadProgressArea) return;
    uploadProgressArea.classList.remove('hidden');
    Array.from(fileList).forEach((file) => uploadFile(file));
}

function uploadFile(file) {
    const { uploadProgressArea } = refs();
    if (!uploadProgressArea) return;

    const item = document.createElement('div');
    item.className = 'card';
    item.style.cssText = 'padding: var(--spacing-md); margin-bottom: var(--spacing-sm);';
    item.innerHTML = `
        <div class="flex items-center justify-between mb-1">
            <span class="font-semibold truncate pr-2">${escapeHtml(file.name)}</span>
            <span class="text-sm text-muted upload-percent">0%</span>
        </div>
        <div style="height: 4px; background: var(--bg-color); border-radius: 2px; overflow: hidden;">
            <div class="upload-bar" style="height: 100%; background: var(--primary-color); border-radius: 2px; width: 0%; transition: width 0.3s;"></div>
        </div>
    `;
    uploadProgressArea.appendChild(item);

    const percentEl = item.querySelector('.upload-percent');
    const barEl = item.querySelector('.upload-bar');
    const uploader = new ChunkedUploader(file, config.courseId, {
        onProgress(info) {
            percentEl.textContent = `${info.percent}%`;
            barEl.style.width = `${info.percent}%`;
        },
        onComplete(result) {
            if (result.skipped) {
                showToast(result.message, 'info');
            } else {
                showToast(`${file.name} 上传成功`, 'success');
            }
            percentEl.textContent = '100%';
            barEl.style.width = '100%';
            barEl.style.background = 'var(--success-color)';
            window.setTimeout(() => item.remove(), 1800);
            loadFiles();
        },
        onError(error) {
            console.error('Upload failed:', error);
            showToast(`${file.name} 上传失败：${error.message}`, 'error');
            percentEl.textContent = '失败';
            barEl.style.background = 'var(--danger-color)';
        },
    });

    uploader.start();
}

function bindListEvents() {
    const { list } = refs();
    if (!list) return;

    list.addEventListener('click', (event) => {
        const actionEl = event.target.closest('[data-action]');
        const cardEl = event.target.closest('[data-file-id]');
        const fileId = Number(actionEl?.dataset.fileId || cardEl?.dataset.fileId || 0);
        if (!fileId) return;

        const action = actionEl?.dataset.action;
        if (action === 'delete') {
            event.preventDefault();
            deleteFile(fileId);
            return;
        }
        if (action === 'blocked') {
            event.preventDefault();
            const file = getFileById(fileId);
            showToast(file?.download_blocked_reason || '当前文件已限制下载', 'warning');
            return;
        }
        if (action === 'details') {
            event.preventDefault();
            openFileDetails(fileId);
            return;
        }
        if (action === 'download') {
            return;
        }

        if (cardEl) {
            openFileDetails(fileId);
        }
    });

    list.addEventListener('keydown', (event) => {
        const cardEl = event.target.closest('.resource-file-card');
        if (!cardEl) return;
        if (event.key !== 'Enter' && event.key !== ' ') return;
        event.preventDefault();
        openFileDetails(Number(cardEl.dataset.fileId));
    });
}

function bindModalEvents() {
    const {
        modal,
        modalSaveBtn,
        modalDeleteBtn,
        modalCopyLinkBtn,
        modalOpenLinkBtn,
    } = refs();
    if (!modal) return;

    modal.addEventListener('click', (event) => {
        if (event.target === modal) {
            state.activeFileId = null;
            return;
        }
        const blockedBtn = event.target.closest('[data-action="blocked"]');
        if (blockedBtn) {
            const file = getFileById(blockedBtn.dataset.fileId);
            showToast(file?.download_blocked_reason || '当前文件已限制下载', 'warning');
        }
    });

    modal.querySelectorAll('[data-dismiss="modal"]').forEach((button) => {
        button.addEventListener('click', () => {
            state.activeFileId = null;
        });
    });

    modalSaveBtn?.addEventListener('click', () => {
        saveActiveFileMetadata();
    });

    modalDeleteBtn?.addEventListener('click', () => {
        if (!state.activeFileId) return;
        deleteFile(state.activeFileId);
    });

    modalCopyLinkBtn?.addEventListener('click', async () => {
        const link = modalCopyLinkBtn.dataset.link || '';
        if (!link) return;
        try {
            await copyText(link);
            showToast('原始链接已复制', 'success');
        } catch (error) {
            console.error('Copy original link failed:', error);
            showToast('复制原始链接失败', 'error');
        }
    });

    modalOpenLinkBtn?.addEventListener('click', () => {
        const link = modalOpenLinkBtn.dataset.link || '';
        if (!link) return;
        window.open(link, '_blank', 'noopener,noreferrer');
    });
}

export function init(appConfig) {
    config = appConfig;
    bindListEvents();
    bindModalEvents();
    loadFiles();

    if (isTeacherView()) {
        setupUploadZone();
    }
}

export async function refreshFiles() {
    await loadFiles();
}

export async function deleteFile(fileId) {
    const file = getFileById(fileId);
    const fileName = file?.file_name || '当前文件';
    if (!window.confirm(`确定要删除“${fileName}”吗？`)) return;

    try {
        await apiFetch(`/api/courses/${config.courseId}/files/${fileId}`, {
            method: 'DELETE',
        });
        showToast('文件已删除', 'success');
        if (Number(state.activeFileId) === Number(fileId)) {
            closeFileDetails();
        }
        await loadFiles();
    } catch (error) {
        console.error('Failed to delete file:', error);
        showToast(`删除失败：${error.message || '未知错误'}`, 'error');
    }
}
