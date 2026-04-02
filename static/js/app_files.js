/**
 * app_files.js
 * File management module for classroom workspace.
 * Handles file listing, upload (with chunked support), and deletion.
 */

import { apiFetch } from './api.js';
import { showToast, formatSize, getFileIcon, escapeHtml } from './ui.js';
import { ChunkedUploader } from './upload.js';

let config = null;

/**
 * Initialize the file app module
 * @param {object} appConfig - window.APP_CONFIG from template
 */
export function init(appConfig) {
    config = appConfig;
    loadFiles();

    // Setup drag-and-drop upload zone (teacher only)
    if (config.userInfo.role === 'teacher') {
        setupUploadZone();
    }
}

/**
 * Fetch and render the file list
 */
export async function refreshFiles() {
    await loadFiles();
}

async function loadFiles() {
    const container = document.getElementById('file-list-container');
    if (!container) return;

    try {
        const data = await apiFetch(`/api/courses/${config.classOfferingId}/files`);
        const files = data.files || data || [];

        if (files.length === 0) {
            container.innerHTML = `
                <div class="empty-state">
                    <p class="text-muted">暂无课程资源</p>
                </div>`;
            return;
        }

        container.innerHTML = files.map(f => {
            const icon = getFileIcon(f.file_name || f.filename || 'file');
            const fileName = f.file_name || f.filename || '未知文件';
            const fileSize = formatSize(f.file_size || f.size || 0);
            const uploadTime = f.uploaded_at || f.upload_time || f.created_at || '';
            const uploader = f.uploader_name || '';
            const fileId = f.id;

            return `
            <div class="card card-interactive" style="padding: var(--spacing-md); margin-bottom: var(--spacing-sm);">
                <div class="flex items-center gap-3">
                    <div class="file-icon shrink-0" style="width: 40px; height: 40px; border-radius: var(--radius-md); background: ${icon.color}15; color: ${icon.color}; display: flex; align-items: center; justify-content: center; font-size: 0.7rem; font-weight: 700;">${icon.label}</div>
                    <div class="flex-1 min-w-0">
                        <div class="font-semibold truncate" title="${escapeHtml(fileName)}">${escapeHtml(fileName)}</div>
                        <div class="text-muted text-sm">${fileSize}${uploader ? ' · ' + escapeHtml(uploader) : ''}${uploadTime ? ' · ' + uploadTime : ''}</div>
                    </div>
                    <div class="flex items-center gap-2 shrink-0">
                        <a href="/download/course_file/${fileId}" class="btn btn-ghost btn-sm btn-icon" title="下载">
                            <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                        </a>
                        ${config.userInfo.role === 'teacher' ? `
                        <button class="btn btn-ghost btn-sm btn-icon text-danger" title="删除" onclick="window.fileApp.deleteFile(${fileId})">
                            <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                        </button>` : ''}
                    </div>
                </div>
            </div>`;
        }).join('');
    } catch (e) {
        console.error('Failed to load files:', e);
        container.innerHTML = `<div class="empty-state"><p class="text-danger">加载资源失败</p></div>`;
    }
}

function setupUploadZone() {
    const zone = document.getElementById('uploadZone');
    const fileInput = document.getElementById('fileInput');
    if (!zone || !fileInput) return;

    fileInput.addEventListener('change', (e) => {
        handleFiles(e.target.files);
        fileInput.value = '';
    });

    zone.addEventListener('click', (e) => {
        if (e.target.tagName !== 'BUTTON') fileInput.click();
    });

    zone.addEventListener('dragover', (e) => {
        e.preventDefault();
        zone.classList.add('drag-over');
    });

    zone.addEventListener('dragleave', () => {
        zone.classList.remove('drag-over');
    });

    zone.addEventListener('drop', (e) => {
        e.preventDefault();
        zone.classList.remove('drag-over');
        handleFiles(e.dataTransfer.files);
    });
}

function handleFiles(fileList) {
    if (!fileList || fileList.length === 0) return;
    const progressArea = document.getElementById('uploadProgressArea');
    if (progressArea) progressArea.classList.remove('hidden');

    Array.from(fileList).forEach(file => uploadFile(file));
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
        </div>`;
    progressArea.appendChild(item);

    const percentEl = item.querySelector('.upload-percent');
    const barEl = item.querySelector('.upload-bar');

    const uploader = new ChunkedUploader(file, config.classOfferingId, {
        onProgress(info) {
            percentEl.textContent = `${info.percent}%`;
            barEl.style.width = `${info.percent}%`;
        },
        onComplete(result) {
            if (result.skipped) {
                showToast(result.message, 'info');
            } else {
                showToast(`${file.name} 上传成功！`, 'success');
            }
            percentEl.textContent = '100%';
            barEl.style.width = '100%';
            barEl.style.background = 'var(--success-color)';
            setTimeout(() => item.remove(), 2000);
            loadFiles();
        },
        onError(err) {
            showToast(`${file.name} 上传失败: ${err.message}`, 'error');
            percentEl.textContent = '失败';
            barEl.style.background = 'var(--danger-color)';
        }
    });

    uploader.start();
}

export async function deleteFile(fileId) {
    if (!confirm('确定要删除此文件吗？')) return;

    try {
        await apiFetch(`/api/courses/${config.classOfferingId}/files/${fileId}`, {
            method: 'DELETE'
        });
        showToast('文件已删除', 'success');
        loadFiles();
    } catch (e) {
        showToast('删除失败: ' + (e.message || '未知错误'), 'error');
    }
}
