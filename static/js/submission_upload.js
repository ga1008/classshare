import { openImageLightbox } from './ls_image_lightbox.js';
import { isImageLikeFile } from './submission_image_guard.js';

function normalizeAllowedFileTypes(rawValue) {
    if (!rawValue) return [];
    const values = Array.isArray(rawValue)
        ? rawValue
        : String(rawValue)
            .replace(/\r/g, '\n')
            .replace(/[;，、]/g, ',')
            .replace(/\n/g, ',')
            .split(',');

    const normalized = [];
    const seen = new Set();
    values.forEach((value) => {
        let token = String(value || '').trim().toLowerCase();
        if (!token) return;
        if (token === '*' || token === '*/*' || token === 'all' || token === 'all files' || token === 'any') {
            normalized.length = 0;
            seen.clear();
            return;
        }
        if (!token.includes('/')) {
            token = token.startsWith('.') ? token : `.${token.replace(/^\.+/, '')}`;
        }
        if (seen.has(token)) return;
        seen.add(token);
        normalized.push(token);
    });
    return normalized;
}

function normalizeRelativePath(rawPath, fallbackName = 'upload.bin') {
    const candidate = String(rawPath || fallbackName).replace(/\\/g, '/').trim().replace(/^\/+|\/+$/g, '');
    const parts = candidate.split('/').map((part) => part.trim()).filter((part) => part && part !== '.');
    const safeParts = [];
    parts.forEach((part) => {
        if (part === '..') {
            return;
        }
        safeParts.push(part.replace(/[\\/\0]/g, '_'));
    });
    return safeParts.length ? safeParts.join('/') : fallbackName;
}

function dedupeRelativePath(relativePath, usedPaths) {
    const key = relativePath.toLowerCase();
    if (!usedPaths.has(key)) {
        usedPaths.add(key);
        return relativePath;
    }

    const slashIndex = relativePath.lastIndexOf('/');
    const parent = slashIndex >= 0 ? relativePath.slice(0, slashIndex) : '';
    const fileName = slashIndex >= 0 ? relativePath.slice(slashIndex + 1) : relativePath;
    const dotIndex = fileName.indexOf('.');
    const stem = dotIndex > 0 ? fileName.slice(0, dotIndex) : fileName;
    const suffix = dotIndex > 0 ? fileName.slice(dotIndex) : '';

    for (let index = 2; index < 10000; index += 1) {
        const candidateName = `${stem} (${index})${suffix}`;
        const candidatePath = parent ? `${parent}/${candidateName}` : candidateName;
        const candidateKey = candidatePath.toLowerCase();
        if (usedPaths.has(candidateKey)) continue;
        usedPaths.add(candidateKey);
        return candidatePath;
    }

    return relativePath;
}

function matchesAllowedFileTypes(relativePath, mimeType, allowedFileTypes) {
    if (!allowedFileTypes.length) return true;
    const normalizedPath = String(relativePath || '').toLowerCase();
    const normalizedMimeType = String(mimeType || '').toLowerCase();
    return allowedFileTypes.some((token) => {
        if (token.includes('/')) {
            if (token.endsWith('/*')) {
                return normalizedMimeType.startsWith(token.slice(0, -1));
            }
            return normalizedMimeType === token;
        }
        return normalizedPath.endsWith(token);
    });
}

function formatBytes(bytes) {
    const size = Number(bytes || 0);
    if (size < 1024) return `${size} B`;
    if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
    return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function formatAllowedFileTypesLabel(allowedFileTypes) {
    return allowedFileTypes.length ? allowedFileTypes.join(', ') : '任意类型';
}

function formatFileSampleSuffix(paths, maxCount = 3) {
    const names = (paths || [])
        .map((path) => {
            const value = String(path || '').replace(/\\/g, '/').trim();
            return value.split('/').filter(Boolean).pop() || value;
        })
        .filter(Boolean);
    if (!names.length) return '';
    const preview = names.slice(0, maxCount).join('、');
    const remaining = names.length - maxCount;
    return remaining > 0 ? `：${preview} 等 ${names.length} 个文件` : `：${preview}`;
}

function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function readFileEntry(entry, currentPath = '') {
    return new Promise((resolve, reject) => {
        entry.file(
            (file) => {
                resolve([{
                    file,
                    relativePath: normalizeRelativePath(`${currentPath}${file.name}`, file.name),
                    source: currentPath ? 'folder' : 'file',
                }]);
            },
            reject,
        );
    });
}

function readDirectoryEntries(reader) {
    return new Promise((resolve, reject) => {
        const allEntries = [];
        const readNext = () => {
            reader.readEntries((entries) => {
                if (!entries.length) {
                    resolve(allEntries);
                    return;
                }
                allEntries.push(...entries);
                readNext();
            }, reject);
        };
        readNext();
    });
}

async function readDroppedEntry(entry, parentPath = '') {
    if (!entry) return [];
    if (entry.isFile) {
        return readFileEntry(entry, parentPath);
    }
    if (entry.isDirectory) {
        const childEntries = await readDirectoryEntries(entry.createReader());
        const currentPath = `${parentPath}${entry.name}/`;
        const results = [];
        for (const childEntry of childEntries) {
            const nested = await readDroppedEntry(childEntry, currentPath);
            results.push(...nested);
        }
        return results;
    }
    return [];
}

/**
 * Collect files from a drag-and-drop dataTransfer.
 * Handles multiple files and folders correctly.
 *
 * IMPORTANT: webkitGetAsEntry() must be called synchronously for ALL items
 * before any async work begins. Once the drop event handler yields (await),
 * the browser may invalidate the underlying DataTransferItem objects,
 * causing subsequent webkitGetAsEntry() calls to return null.
 */
async function collectDroppedFiles(dataTransfer) {
    if (!dataTransfer) return [];

    // Prefer webkitGetAsEntry for full directory support
    const items = Array.from(dataTransfer.items || []);
    if (items.length && typeof items[0].webkitGetAsEntry === 'function') {
        // Step 1: Synchronously extract ALL FileSystemEntry handles
        //         before any async operation yields control.
        const entries = [];
        for (const item of items) {
            const entry = item.webkitGetAsEntry();
            if (entry) entries.push(entry);
        }

        if (entries.length) {
            // Step 2: Now it's safe to asynchronously read file contents
            const results = [];
            for (const entry of entries) {
                const nested = await readDroppedEntry(entry);
                results.push(...nested);
            }
            if (results.length) return results;
        }
    }

    // Fallback: use files from dataTransfer directly
    const files = Array.from(dataTransfer.files || []);
    return files.map((file) => ({
        file,
        relativePath: normalizeRelativePath(file.webkitRelativePath || file.name, file.name),
        source: file.webkitRelativePath ? 'folder' : 'file',
    }));
}

/* ------------------------------------------------------------------ */
/* Clipboard paste                                                     */
/* ------------------------------------------------------------------ */

const GENERIC_CLIPBOARD_NAME = /^(image|pasted|screenshot|clipboard)?\.?(png|jpe?g|gif|webp|bmp)$/i;

function pasteTimestamp() {
    const now = new Date();
    const pad = (value) => String(value).padStart(2, '0');
    return `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`;
}

function extensionForMime(mime) {
    const map = { 'image/png': 'png', 'image/jpeg': 'jpg', 'image/gif': 'gif', 'image/webp': 'webp', 'image/bmp': 'bmp' };
    return map[String(mime || '').toLowerCase()] || 'png';
}

/** Give clipboard blobs a meaningful, unique file name (screenshots arrive as "image.png"). */
export function nameClipboardFile(file, index = 0) {
    if (!file) return file;
    const rawName = String(file.name || '').trim();
    const looksGeneric = !rawName || GENERIC_CLIPBOARD_NAME.test(rawName);
    if (!looksGeneric) return file;
    const ext = rawName.includes('.') ? rawName.split('.').pop() : extensionForMime(file.type);
    const suffix = index > 0 ? `-${index + 1}` : '';
    const nextName = `粘贴截图-${pasteTimestamp()}${suffix}.${ext}`;
    try {
        return new File([file], nextName, { type: file.type || 'image/png', lastModified: file.lastModified || Date.now() });
    } catch {
        return file;
    }
}

export function extractClipboardFiles(dataTransfer) {
    if (!dataTransfer) return [];
    const files = [];
    const seen = new Set();
    const push = (file) => {
        if (!file) return;
        const key = `${file.name}|${file.size}|${file.type}|${file.lastModified}`;
        if (seen.has(key)) return;
        seen.add(key);
        files.push(file);
    };
    Array.from(dataTransfer.files || []).forEach(push);
    if (!files.length) {
        Array.from(dataTransfer.items || []).forEach((item) => {
            if (item.kind === 'file') push(item.getAsFile());
        });
    }
    return files.map((file, index) => nameClipboardFile(file, index));
}

/**
 * Routes document-level Ctrl+V to the upload area that owns the focus.
 *
 * Rules: (1) the area whose scope contains the active element / paste target,
 * (2) the last area the user clicked or focused, (3) the only registered area,
 * (4) otherwise a reminder toast telling the student where to click first.
 */
const pasteRouter = {
    managers: new Set(),
    active: null,
    bound: false,
    register(manager) {
        this.managers.add(manager);
        if (!this.bound) this.bind();
    },
    unregister(manager) {
        this.managers.delete(manager);
        if (this.active === manager) this.active = null;
    },
    bind() {
        this.bound = true;
        const remember = (event) => {
            const owner = this.findOwner(event.target);
            if (owner) this.active = owner;
        };
        document.addEventListener('pointerdown', remember, true);
        document.addEventListener('focusin', remember, true);
        document.addEventListener('paste', (event) => this.handlePaste(event));
    },
    findOwner(target) {
        if (!(target instanceof Node)) return null;
        for (const manager of this.managers) {
            if (manager.pasteEnabled && manager.containsPasteTarget(target)) return manager;
        }
        return null;
    },
    liveManagers() {
        return Array.from(this.managers).filter((manager) => manager.pasteEnabled && manager.isConnected());
    },
    resolveTarget(eventTarget) {
        const direct = this.findOwner(eventTarget) || this.findOwner(document.activeElement);
        if (direct) return { manager: direct, reason: 'focus' };
        if (this.active && this.active.pasteEnabled && this.active.isConnected()) {
            return { manager: this.active, reason: 'recent' };
        }
        const live = this.liveManagers();
        if (live.length === 1) return { manager: live[0], reason: 'only' };
        return null;
    },
    handlePaste(event) {
        const live = this.liveManagers();
        if (!live.length) return;
        const files = extractClipboardFiles(event.clipboardData);
        if (!files.length) return; // plain text paste: leave the editor alone
        const target = this.resolveTarget(event.target);
        if (!target) {
            const notifier = live[0];
            notifier.notify(`剪贴板里有 ${files.length} 个文件，但当前没有选中附件区域。请先点击要粘贴到的题目附件区域（或该题的答题框），再按 Ctrl+V；也可以点击附件区的「粘贴」按钮。`, 'warning');
            return;
        }
        event.preventDefault();
        target.manager.acceptPastedFiles(files, target.reason);
    },
};

/* ------------------------------------------------------------------ */
/* Upload manager                                                      */
/* ------------------------------------------------------------------ */

let managerSequence = 0;

/**
 * Entry shape:
 *   { file: File|null, relativePath, source: 'file'|'folder'|'paste'|'server',
 *     hash?: string, previewUrl?: string (object URL for local images),
 *     remote?: { id, thumbnail_url, image_preview_url, download_url, raw_url,
 *                file_size, file_name, relative_path, file_hash, is_image },
 *     syncState?: 'local'|'syncing'|'synced'|'failed' }
 */
export class SubmissionUploadManager {
    constructor(options) {
        this.options = options || {};
        this.entries = [];
        this.allowedFileTypes = normalizeAllowedFileTypes(this.options.allowedFileTypes || []);
        this.maxBytes = Number(this.options.maxBytes || 0);               // total size limit
        this.maxPerFileBytes = Number(this.options.maxPerFileBytes || 0);  // per-file size limit
        this.maxFiles = Number(this.options.maxFiles || 0);
        this.label = String(this.options.label || '附件区');
        this.pasteEnabled = this.options.enablePaste !== false;
        this.gatePending = 0;
        this.id = `upload-manager-${++managerSequence}`;
        this.elements = {
            dropZone: document.getElementById(this.options.dropZoneId),
            fileInput: document.getElementById(this.options.fileInputId),
            folderInput: document.getElementById(this.options.folderInputId),
            list: document.getElementById(this.options.listId),
            summary: document.getElementById(this.options.summaryId),
            pasteButton: this.options.pasteButtonId ? document.getElementById(this.options.pasteButtonId) : null,
        };
    }

    init() {
        const { dropZone, fileInput, folderInput, pasteButton } = this.elements;
        if (fileInput) {
            fileInput.addEventListener('change', () => {
                this.addFileList(fileInput.files, 'file');
                fileInput.value = '';
            });
        }
        if (folderInput) {
            folderInput.addEventListener('change', () => {
                this.addFileList(folderInput.files, 'folder');
                folderInput.value = '';
            });
        }
        if (dropZone) {
            let dragCounter = 0;
            if (!dropZone.hasAttribute('tabindex')) dropZone.setAttribute('tabindex', '0');
            dropZone.addEventListener('dragenter', (event) => {
                event.preventDefault();
                dragCounter++;
                dropZone.classList.add('dragover');
            });
            dropZone.addEventListener('dragover', (event) => {
                event.preventDefault();
            });
            dropZone.addEventListener('dragleave', (event) => {
                event.preventDefault();
                dragCounter--;
                if (dragCounter <= 0) {
                    dragCounter = 0;
                    dropZone.classList.remove('dragover');
                }
            });
            dropZone.addEventListener('drop', async (event) => {
                event.preventDefault();
                dragCounter = 0;
                dropZone.classList.remove('dragover');
                const droppedEntries = await collectDroppedFiles(event.dataTransfer);
                this.addEntries(droppedEntries);
            });
        }
        if (pasteButton) {
            pasteButton.addEventListener('click', () => this.pasteFromClipboard());
        }
        if (this.pasteEnabled) pasteRouter.register(this);
        this.render();
    }

    destroy() {
        pasteRouter.unregister(this);
    }

    /* ---------- paste ---------- */

    getPasteScopeElements() {
        const scope = this.options.pasteScope;
        const extra = [];
        const pushScope = (value) => {
            if (!value) return;
            if (typeof value === 'string') {
                document.querySelectorAll(value).forEach((el) => extra.push(el));
            } else if (value instanceof Element) {
                extra.push(value);
            } else if (Array.isArray(value)) {
                value.forEach(pushScope);
            }
        };
        pushScope(scope);
        return [this.elements.dropZone, this.elements.list, this.elements.summary, this.elements.pasteButton, ...extra].filter(Boolean);
    }

    containsPasteTarget(node) {
        return this.getPasteScopeElements().some((el) => el === node || el.contains(node));
    }

    isConnected() {
        return Boolean(this.elements.dropZone?.isConnected || this.elements.list?.isConnected);
    }

    async acceptPastedFiles(files, reason = 'focus') {
        const entries = (files || []).map((file) => ({
            file,
            relativePath: normalizeRelativePath(file.name, file.name),
            source: 'paste',
        }));
        const before = this.entries.length;
        await this.addEntries(entries);
        const added = this.entries.length - before;
        if (added > 0) {
            const where = reason === 'focus' ? '' : `到「${this.label}」`;
            this.notify(`已粘贴 ${added} 个文件${where}。`, 'success');
        }
        return added;
    }

    async pasteFromClipboard() {
        if (!navigator.clipboard?.read) {
            this.notify('当前浏览器不支持按钮读取剪贴板，请点击附件区域后按 Ctrl+V 粘贴。', 'warning');
            return 0;
        }
        let items;
        try {
            items = await navigator.clipboard.read();
        } catch (error) {
            const denied = error?.name === 'NotAllowedError' || error?.name === 'SecurityError';
            this.notify(denied
                ? '浏览器未授权读取剪贴板。请允许权限后重试，或点击附件区域后按 Ctrl+V 粘贴。'
                : '读取剪贴板失败，请点击附件区域后按 Ctrl+V 粘贴。', 'warning');
            return 0;
        }
        const files = [];
        for (const item of items || []) {
            const types = Array.from(item.types || []);
            const fileType = types.find((type) => type.startsWith('image/')) || types.find((type) => !type.startsWith('text/'));
            if (!fileType) continue;
            try {
                const blob = await item.getType(fileType);
                const ext = extensionForMime(fileType);
                files.push(nameClipboardFile(new File([blob], `clipboard.${ext}`, { type: fileType }), files.length));
            } catch {
                /* skip unreadable item */
            }
        }
        if (!files.length) {
            this.notify('剪贴板里没有图片或文件。复制的文件请在附件区域按 Ctrl+V 粘贴（浏览器按钮只能读取截图/图片）。', 'info');
            return 0;
        }
        return this.acceptPastedFiles(files, 'button');
    }

    /* ---------- basic accessors ---------- */

    openFilePicker() {
        this.elements.fileInput?.click();
    }

    openFolderPicker() {
        this.elements.folderInput?.click();
    }

    entrySize(entry) {
        return Number(entry?.file?.size ?? entry?.remote?.file_size ?? 0);
    }

    getTotalBytes() {
        return this.entries.reduce((sum, entry) => sum + this.entrySize(entry), 0);
    }

    hasFiles() {
        return this.entries.length > 0;
    }

    getUnsyncedEntries() {
        return this.entries.filter((entry) => entry.file && entry.syncState !== 'synced');
    }

    getSyncedEntries() {
        return this.entries.filter((entry) => entry.syncState === 'synced' || (!entry.file && entry.remote));
    }

    clear() {
        this.entries.forEach((entry) => this.releaseEntry(entry));
        this.entries = [];
        this.render();
        this.options.onChange?.(this.getSnapshot());
    }

    releaseEntry(entry) {
        if (entry?.previewUrl) {
            try { URL.revokeObjectURL(entry.previewUrl); } catch { /* ignore */ }
            entry.previewUrl = '';
        }
    }

    removeEntryAt(index) {
        const entry = this.entries[index];
        if (!entry) return;
        this.entries.splice(index, 1);
        this.releaseEntry(entry);
        this.render();
        this.options.onChange?.(this.getSnapshot());
        this.options.onRemove?.(entry, this);
    }

    removeEntries(predicate) {
        const removed = this.entries.filter((entry) => predicate(entry));
        if (!removed.length) return [];
        this.entries = this.entries.filter((entry) => !predicate(entry));
        removed.forEach((entry) => this.releaseEntry(entry));
        this.render();
        this.options.onChange?.(this.getSnapshot());
        return removed;
    }

    addFileList(fileList, source = 'file') {
        const preparedEntries = Array.from(fileList || []).map((file) => ({
            file,
            relativePath: normalizeRelativePath(file.webkitRelativePath || file.name, file.name),
            source,
        }));
        return this.addEntries(preparedEntries);
    }

    /* ---------- server-synced entries ---------- */

    /** Seed remote-only entries (server draft files) that have no local File. */
    addServerEntries(files, { silent = false } = {}) {
        const known = new Set(this.entries.map((entry) => entry.relativePath.toLowerCase()));
        (files || []).forEach((remote) => {
            const relativePath = normalizeRelativePath(remote.relative_path || remote.file_name || 'upload.bin');
            const existing = this.entries.find((entry) => entry.relativePath.toLowerCase() === relativePath.toLowerCase());
            if (existing) {
                existing.remote = remote;
                existing.syncState = 'synced';
                existing.hash = existing.hash || remote.file_hash || '';
                return;
            }
            if (known.has(relativePath.toLowerCase())) return;
            known.add(relativePath.toLowerCase());
            this.entries.push({
                file: null,
                relativePath,
                source: 'server',
                hash: remote.file_hash || '',
                remote,
                syncState: 'synced',
            });
        });
        this.render();
        if (!silent) this.options.onChange?.(this.getSnapshot());
    }

    /** Attach server metadata (thumbnail URLs…) to local entries after an upload. */
    applyRemoteFiles(files) {
        const byPath = new Map();
        (files || []).forEach((remote) => {
            const key = String(remote.relative_path || remote.file_name || '').replace(/\\/g, '/').toLowerCase();
            if (key) byPath.set(key, remote);
        });
        let changed = false;
        this.entries.forEach((entry) => {
            const key = entry.relativePath.toLowerCase();
            const remote = byPath.get(key)
                || Array.from(byPath.entries()).find(([path]) => path.endsWith(`/${key}`))?.[1];
            if (!remote) return;
            entry.remote = remote;
            entry.syncState = 'synced';
            entry.hash = entry.hash || remote.file_hash || '';
            changed = true;
        });
        if (changed) this.render();
        return changed;
    }

    markSyncState(state, predicate = () => true) {
        let changed = false;
        this.entries.forEach((entry) => {
            if (!entry.file || !predicate(entry)) return;
            if (entry.syncState !== state) {
                entry.syncState = state;
                changed = true;
            }
        });
        if (changed) this.render();
    }

    /* ---------- adding ---------- */

    addEntries(rawEntries) {
        if (!rawEntries || !rawEntries.length) return Promise.resolve();
        const accepted = this.filterEntries(rawEntries);
        if (!accepted.length) return Promise.resolve();
        const gate = this.options.beforeAdd;
        if (typeof gate !== 'function') {
            this.commitEntries(accepted);
            return Promise.resolve();
        }
        this.gatePending += 1;
        this.renderSummary();
        return Promise.resolve()
            .then(() => gate(accepted, this))
            .then((result) => {
                const passed = Array.isArray(result) ? result.filter((entry) => accepted.includes(entry)) : accepted;
                this.commitEntries(passed);
            })
            .catch((error) => {
                console.warn('[upload] beforeAdd gate failed, keeping files', error);
                this.commitEntries(accepted);
            })
            .finally(() => {
                this.gatePending = Math.max(0, this.gatePending - 1);
                this.renderSummary();
            });
    }

    filterEntries(rawEntries) {
        const usedPaths = new Set(this.entries.map((entry) => entry.relativePath.toLowerCase()));
        let totalBytes = this.getTotalBytes();
        let nextCount = this.entries.length;
        const accepted = [];
        let skippedCount = 0;
        let oversizedCount = 0;
        let typeFilteredCount = 0;
        let totalOverCount = 0;
        const skippedFiles = [];
        const oversizedFiles = [];
        const typeFilteredFiles = [];
        const totalOverFiles = [];

        rawEntries.forEach((rawEntry) => {
            const file = rawEntry.file;
            if (!file) return;
            const candidatePath = normalizeRelativePath(
                rawEntry.relativePath || file.webkitRelativePath || file.name,
                file.name,
            );

            if (this.maxFiles && nextCount >= this.maxFiles) {
                totalOverCount++;
                totalOverFiles.push(candidatePath);
                return;
            }

            // Duplicate detection (same path + size + mtime) before path dedupe renames it.
            const duplicate = this.entries.some((entry) => (
                entry.relativePath.toLowerCase() === candidatePath.toLowerCase()
                && entry.file
                && entry.file.size === file.size
                && entry.file.lastModified === file.lastModified
            ));
            if (duplicate) {
                return;
            }

            const normalizedPath = dedupeRelativePath(candidatePath, usedPaths);

            if (!matchesAllowedFileTypes(normalizedPath, file.type, this.allowedFileTypes)) {
                typeFilteredCount++;
                typeFilteredFiles.push(normalizedPath);
                return;
            }

            // Per-file size check
            if (this.maxPerFileBytes && file.size > this.maxPerFileBytes) {
                oversizedCount++;
                oversizedFiles.push(normalizedPath);
                return;
            }

            // Total size check
            if (this.maxBytes && totalBytes + file.size > this.maxBytes) {
                skippedCount++;
                skippedFiles.push(normalizedPath);
                return;
            }

            accepted.push({
                file,
                relativePath: normalizedPath,
                source: rawEntry.source || (file.webkitRelativePath ? 'folder' : 'file'),
                kind: rawEntry.kind || '',
                hash: rawEntry.hash || '',
                syncState: 'local',
            });
            totalBytes += file.size;
            nextCount += 1;
        });

        // Show aggregated feedback instead of per-file toasts
        if (oversizedCount > 0) {
            const perFileMB = this.maxPerFileBytes ? (this.maxPerFileBytes / 1024 / 1024).toFixed(0) : '?';
            this.notify(`${oversizedCount} 个文件超过单文件大小限制（${perFileMB}MB），已跳过${formatFileSampleSuffix(oversizedFiles)}。请压缩或拆分后重新上传。`, 'warning');
        }
        if (skippedCount > 0) {
            const totalMB = this.maxBytes ? (this.maxBytes / 1024 / 1024).toFixed(0) : '?';
            this.notify(`${skippedCount} 个文件因总大小超过限制（${totalMB}MB）而跳过${formatFileSampleSuffix(skippedFiles)}。请删除不必要文件或压缩后再上传。`, 'warning');
        }
        if (typeFilteredCount > 0) {
            const allowedLabel = formatAllowedFileTypesLabel(this.allowedFileTypes);
            this.notify(`${typeFilteredCount} 个文件类型不符合要求，已跳过${formatFileSampleSuffix(typeFilteredFiles)}。允许类型：${allowedLabel}。请转换格式后重新选择；若题目有单独附件要求，请上传到对应题目的附件区域。`, 'warning');
        }
        if (totalOverCount > 0) {
            this.notify(`${totalOverCount} 个文件因数量超限而跳过${formatFileSampleSuffix(totalOverFiles)}。最多允许 ${this.maxFiles} 个文件，请删减后重新上传。`, 'warning');
        }
        return accepted;
    }

    commitEntries(entries) {
        if (!entries.length) return;
        const usedPaths = new Set(this.entries.map((entry) => entry.relativePath.toLowerCase()));
        entries.forEach((entry) => {
            // The gate may have taken time; make sure paths are still unique.
            entry.relativePath = dedupeRelativePath(entry.relativePath, usedPaths);
            if (this.isImageEntry(entry) && !entry.previewUrl) {
                try { entry.previewUrl = URL.createObjectURL(entry.file); } catch { entry.previewUrl = ''; }
            }
            this.entries.push(entry);
        });
        this.render();
        this.options.onChange?.(this.getSnapshot());
    }

    /* ---------- images & lightbox ---------- */

    isImageEntry(entry) {
        if (!entry) return false;
        if (entry.remote && typeof entry.remote.is_image === 'boolean' && !entry.file) return entry.remote.is_image;
        if (entry.file) return isImageLikeFile(entry.file, entry.relativePath);
        return Boolean(entry.remote?.is_image);
    }

    entryDisplayName(entry) {
        return entry.relativePath.split('/').filter(Boolean).pop() || entry.remote?.file_name || '附件';
    }

    entryThumbnailUrl(entry) {
        return entry.remote?.thumbnail_url || entry.previewUrl || entry.remote?.raw_url || '';
    }

    entryLightboxItem(entry) {
        const remote = entry.remote || {};
        return {
            src: remote.image_preview_url || remote.raw_url || entry.previewUrl || remote.download_url || '',
            previewSrc: '',
            originalSrc: remote.download_url || remote.raw_url || entry.previewUrl || '',
            title: this.entryDisplayName(entry),
            meta: formatBytes(this.entrySize(entry)),
        };
    }

    getImageEntries() {
        return this.entries.filter((entry) => this.isImageEntry(entry) && this.entryThumbnailUrl(entry));
    }

    openPreview(entry) {
        const images = this.getImageEntries();
        const index = Math.max(0, images.indexOf(entry));
        if (!images.length) return false;
        return openImageLightbox({
            items: images.map((item) => this.entryLightboxItem(item)),
            index,
            groupLabel: this.options.lightboxLabel || this.label,
        });
    }

    /* ---------- form data & snapshot ---------- */

    buildFormData() {
        const formData = new FormData();
        const manifest = [];
        this.entries.forEach((entry) => {
            if (!entry.file) return;
            formData.append('files', entry.file, entry.file.name);
            manifest.push({
                relative_path: entry.relativePath,
                content_type: entry.file.type || '',
            });
        });
        formData.append('manifest', JSON.stringify(manifest));
        return formData;
    }

    getSnapshot() {
        return {
            count: this.entries.length,
            totalBytes: this.getTotalBytes(),
            syncedCount: this.getSyncedEntries().length,
            entries: this.entries.map((entry) => ({
                relativePath: entry.relativePath,
                size: this.entrySize(entry),
                source: entry.source,
                hash: entry.hash || '',
                syncState: entry.syncState || (entry.remote ? 'synced' : 'local'),
                isImage: this.isImageEntry(entry),
            })),
        };
    }

    notify(message, type = 'info') {
        if (!message) return;
        if (typeof this.options.notify === 'function') {
            this.options.notify(message, type);
            return;
        }
        if (type === 'error') {
            console.error(message);
        } else {
            console.warn(message);
        }
    }

    /* ---------- rendering ---------- */

    syncBadge(entry) {
        const state = entry.syncState || (entry.remote ? 'synced' : 'local');
        if (!this.options.showSyncState) return '';
        if (state === 'synced') return `<span class="file-chip__badge file-chip__badge--synced">已上传</span>`;
        if (state === 'syncing') return `<span class="file-chip__badge file-chip__badge--syncing">上传中…</span>`;
        if (state === 'failed') return `<span class="file-chip__badge file-chip__badge--failed" title="提交时会随表单一起上传">待上传</span>`;
        return '';
    }

    renderChip(entry, index) {
        const fileSize = this.entrySize(entry);
        const perFileLimit = this.maxPerFileBytes || 0;
        const isNearLimit = perFileLimit && fileSize > perFileLimit * 0.8;
        const pathParts = entry.relativePath.split('/');
        const displayName = escapeHtml(pathParts[pathParts.length - 1]);
        const displayPath = pathParts.length > 1
            ? `<span class="text-muted" style="font-size:0.75rem">${escapeHtml(pathParts.slice(0, -1).join('/'))}/</span>${displayName}`
            : displayName;
        const sizeStyle = `white-space:nowrap;${isNearLimit ? 'color:var(--warning-color);font-weight:600;' : ''}`;
        const removeButton = `<button type="button" class="btn btn-ghost btn-sm p-1 ml-1 text-danger h-auto file-chip__remove" style="flex-shrink:0;line-height:1;" data-remove-upload="${index}" title="移除">&times;</button>`;
        const thumbnail = this.isImageEntry(entry) ? this.entryThumbnailUrl(entry) : '';

        if (thumbnail) {
            return `
                <div class="file-chip file-chip--image${isNearLimit ? ' is-near-limit' : ''}" data-upload-index="${index}">
                    <button type="button" class="file-chip__thumb" data-preview-upload="${index}" title="点击查看大图" aria-label="查看大图 ${displayName}">
                        <img src="${escapeHtml(thumbnail)}" alt="${displayName}" loading="lazy" decoding="async">
                    </button>
                    <div class="file-chip__body">
                        <span class="file-chip__name" title="${escapeHtml(entry.relativePath)}">${displayPath}</span>
                        <span class="file-chip__meta"><span style="${sizeStyle}">${formatBytes(fileSize)}</span>${this.syncBadge(entry)}</span>
                    </div>
                    ${removeButton}
                </div>
            `;
        }

        const sourceIcon = entry.source === 'folder'
            ? `<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="text-muted" style="flex-shrink:0"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path></svg>`
            : `<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="text-muted" style="flex-shrink:0"><path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"></path><polyline points="13 2 13 9 20 9"></polyline></svg>`;
        return `
            <div class="file-chip" style="align-items:flex-start;gap:6px;${isNearLimit ? 'border-color:var(--warning-color);' : ''}" data-upload-index="${index}">
                ${sourceIcon}
                <span class="truncate" style="max-width:260px;" title="${escapeHtml(entry.relativePath)}">${displayPath}</span>
                <span class="text-xs text-muted" style="${sizeStyle}">${formatBytes(fileSize)}</span>
                ${this.syncBadge(entry)}
                ${removeButton}
            </div>
        `;
    }

    render() {
        if (this.elements.list) {
            this.elements.list.innerHTML = this.entries.map((entry, index) => this.renderChip(entry, index)).join('');
            this.elements.list.classList.toggle('has-image-chips', this.getImageEntries().length > 0);

            this.elements.list.querySelectorAll('[data-remove-upload]').forEach((button) => {
                button.addEventListener('click', () => {
                    this.removeEntryAt(Number(button.dataset.removeUpload));
                });
            });
            this.elements.list.querySelectorAll('[data-preview-upload]').forEach((button) => {
                button.addEventListener('click', (event) => {
                    event.preventDefault();
                    const entry = this.entries[Number(button.dataset.previewUpload)];
                    if (entry) this.openPreview(entry);
                });
            });
        }
        this.renderSummary();
    }

    renderSummary() {
        if (!this.elements.summary) return;
        if (this.gatePending > 0) {
            this.elements.summary.textContent = '正在校验截图…';
            return;
        }
        if (!this.entries.length) {
            this.elements.summary.textContent = '未选择文件';
            return;
        }
        const totalBytes = this.getTotalBytes();
        const totalMB = this.maxBytes ? (this.maxBytes / 1024 / 1024).toFixed(0) : '';
        const perFileMB = this.maxPerFileBytes ? (this.maxPerFileBytes / 1024 / 1024).toFixed(0) : '';
        let text = `${this.entries.length} 个文件 / ${formatBytes(totalBytes)}`;
        if (totalMB) text += `（限 ${perFileMB ? perFileMB + 'MB/个, ' : ''}共${totalMB}MB）`;
        this.elements.summary.textContent = text;
    }
}

export { formatBytes as formatUploadBytes, pasteRouter as uploadPasteRouter };
