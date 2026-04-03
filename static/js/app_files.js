import { apiFetch } from './api.js';
import { showToast, formatSize, getFileIcon, escapeHtml } from './ui.js';
import { ChunkedUploader } from './upload.js';

let config = null;

export function init(appConfig) {
    config = appConfig;
    loadFiles();

    if (config.userInfo.role === 'teacher') {
        setupUploadZone();
    }
}

export async function refreshFiles() {
    await loadFiles();
}

async function loadFiles() {
    const container = document.getElementById('file-list-container');
    if (!container) return;

    try {
        const data = await apiFetch(`/api/courses/${config.classOfferingId}/files`, { silent: true });
        const files = Array.isArray(data?.files) ? data.files : [];

        if (files.length === 0) {
            container.innerHTML = `
                <div class="empty-state">
                    <p class="text-muted">当前课堂还没有共享资源</p>
                </div>
            `;
            return;
        }

        container.innerHTML = files.map((file) => {
            const icon = getFileIcon(file.file_name || file.filename || 'file');
            const fileName = file.file_name || file.filename || '未命名文件';
            const fileSize = formatSize(file.file_size || file.size || 0);
            const uploadTime = file.uploaded_at || file.upload_time || file.created_at || '';
            const fileId = file.id;

            return `
                <div class="card card-interactive" style="padding: var(--spacing-md); margin-bottom: var(--spacing-sm);">
                    <div class="flex items-center gap-3">
                        <div class="file-icon shrink-0" style="width: 40px; height: 40px; border-radius: var(--radius-md); background: ${icon.color}15; color: ${icon.color}; display: flex; align-items: center; justify-content: center; font-size: 0.7rem; font-weight: 700;">${icon.label}</div>
                        <div class="flex-1 min-w-0">
                            <div class="font-semibold truncate" title="${escapeHtml(fileName)}">${escapeHtml(fileName)}</div>
                            <div class="text-muted text-sm">${escapeHtml(fileSize)}${uploadTime ? ` · ${escapeHtml(uploadTime)}` : ''}</div>
                        </div>
                        <div class="flex items-center gap-2 shrink-0">
                            <a href="/download/course_file/${fileId}" class="btn btn-ghost btn-sm btn-icon" title="下载">
                                <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                            </a>
                            ${config.userInfo.role === 'teacher' ? `
                                <button class="btn btn-ghost btn-sm btn-icon text-danger" title="删除" onclick="window.fileApp.deleteFile(${fileId})">
                                    <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                                </button>
                            ` : ''}
                        </div>
                    </div>
                </div>
            `;
        }).join('');
    } catch (error) {
        console.error('Failed to load files:', error);
        container.innerHTML = `
            <div class="empty-state">
                <p class="text-danger">资源加载失败，请稍后重试</p>
            </div>
        `;
    }
}

function setupUploadZone() {
    const zone = document.getElementById('uploadZone');
    const fileInput = document.getElementById('fileInput');
    if (!zone || !fileInput) return;

    fileInput.addEventListener('change', (event) => {
        handleFiles(event.target.files);
        fileInput.value = '';
    });

    zone.addEventListener('click', (event) => {
        if (event.target.tagName !== 'BUTTON') {
            fileInput.click();
        }
    });

    zone.addEventListener('dragover', (event) => {
        event.preventDefault();
        zone.classList.add('drag-over');
    });

    zone.addEventListener('dragleave', () => {
        zone.classList.remove('drag-over');
    });

    zone.addEventListener('drop', (event) => {
        event.preventDefault();
        zone.classList.remove('drag-over');
        handleFiles(event.dataTransfer.files);
    });
}

function handleFiles(fileList) {
    if (!fileList || fileList.length === 0) return;
    const progressArea = document.getElementById('uploadProgressArea');
    if (progressArea) progressArea.classList.remove('hidden');

    Array.from(fileList).forEach((file) => uploadFile(file));
}

function uploadFile(file) {
    const progressArea = document.getElementById('uploadProgressArea');
    if (!progressArea) return;

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
    progressArea.appendChild(item);

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
            setTimeout(() => item.remove(), 2000);
            loadFiles();
        },
        onError(error) {
            console.error('Upload failed:', error);
            showToast(`${file.name} 上传失败：${error.message}`, 'error');
            percentEl.textContent = '失败';
            barEl.style.background = 'var(--danger-color)';
        }
    });

    uploader.start();
}

export async function deleteFile(fileId) {
    if (!window.confirm('确定要删除这个课堂资源吗？')) return;

    try {
        await apiFetch(`/api/courses/${config.courseId}/files/${fileId}`, {
            method: 'DELETE'
        });
        showToast('文件已删除', 'success');
        loadFiles();
    } catch (error) {
        console.error('Failed to delete file:', error);
        showToast(`删除失败：${error.message || '未知错误'}`, 'error');
    }
}
