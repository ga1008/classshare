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

export class SubmissionUploadManager {
    constructor(options) {
        this.options = options || {};
        this.entries = [];
        this.allowedFileTypes = normalizeAllowedFileTypes(this.options.allowedFileTypes || []);
        this.maxBytes = Number(this.options.maxBytes || 0);               // total size limit
        this.maxPerFileBytes = Number(this.options.maxPerFileBytes || 0);  // per-file size limit
        this.maxFiles = Number(this.options.maxFiles || 0);
        this.elements = {
            dropZone: document.getElementById(this.options.dropZoneId),
            fileInput: document.getElementById(this.options.fileInputId),
            folderInput: document.getElementById(this.options.folderInputId),
            list: document.getElementById(this.options.listId),
            summary: document.getElementById(this.options.summaryId),
        };
    }

    init() {
        const { dropZone, fileInput, folderInput } = this.elements;
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
        this.render();
    }

    openFilePicker() {
        this.elements.fileInput?.click();
    }

    openFolderPicker() {
        this.elements.folderInput?.click();
    }

    getTotalBytes() {
        return this.entries.reduce((sum, entry) => sum + Number(entry.file.size || 0), 0);
    }

    hasFiles() {
        return this.entries.length > 0;
    }

    clear() {
        this.entries = [];
        this.render();
        this.options.onChange?.(this.getSnapshot());
    }

    addFileList(fileList, source = 'file') {
        const preparedEntries = Array.from(fileList || []).map((file) => ({
            file,
            relativePath: normalizeRelativePath(file.webkitRelativePath || file.name, file.name),
            source,
        }));
        this.addEntries(preparedEntries);
    }

    addEntries(rawEntries) {
        if (!rawEntries || !rawEntries.length) return;

        const usedPaths = new Set(this.entries.map((entry) => entry.relativePath.toLowerCase()));
        let totalBytes = this.getTotalBytes();
        const nextEntries = [...this.entries];
        let skippedCount = 0;
        let oversizedCount = 0;
        let typeFilteredCount = 0;
        let totalOverCount = 0;

        rawEntries.forEach((rawEntry) => {
            const file = rawEntry.file;
            if (!file) return;

            if (this.maxFiles && nextEntries.length >= this.maxFiles) {
                totalOverCount++;
                return;
            }

            const normalizedPath = dedupeRelativePath(
                normalizeRelativePath(rawEntry.relativePath || file.webkitRelativePath || file.name, file.name),
                usedPaths,
            );

            if (!matchesAllowedFileTypes(normalizedPath, file.type, this.allowedFileTypes)) {
                typeFilteredCount++;
                return;
            }

            // Per-file size check
            if (this.maxPerFileBytes && file.size > this.maxPerFileBytes) {
                oversizedCount++;
                return;
            }

            // Total size check
            if (this.maxBytes && totalBytes + file.size > this.maxBytes) {
                skippedCount++;
                return;
            }

            // Duplicate detection
            const duplicate = nextEntries.some((entry) => (
                entry.relativePath.toLowerCase() === normalizedPath.toLowerCase()
                && entry.file.size === file.size
                && entry.file.lastModified === file.lastModified
            ));
            if (duplicate) {
                return;
            }

            nextEntries.push({
                file,
                relativePath: normalizedPath,
                source: rawEntry.source || (file.webkitRelativePath ? 'folder' : 'file'),
            });
            totalBytes += file.size;
        });

        // Show aggregated feedback instead of per-file toasts
        if (oversizedCount > 0) {
            const perFileMB = this.maxPerFileBytes ? (this.maxPerFileBytes / 1024 / 1024).toFixed(0) : '?';
            this.notify(`${oversizedCount} 个文件超过单文件大小限制（${perFileMB}MB），已跳过`, 'warning');
        }
        if (skippedCount > 0) {
            const totalMB = this.maxBytes ? (this.maxBytes / 1024 / 1024).toFixed(0) : '?';
            this.notify(`${skippedCount} 个文件因总大小超过限制（${totalMB}MB）而跳过`, 'warning');
        }
        if (typeFilteredCount > 0) {
            this.notify(`${typeFilteredCount} 个文件类型不符合要求，已跳过`, 'warning');
        }
        if (totalOverCount > 0) {
            this.notify(`${totalOverCount} 个文件因数量超限而跳过（最多 ${this.maxFiles} 个）`, 'warning');
        }

        this.entries = nextEntries;
        this.render();
        this.options.onChange?.(this.getSnapshot());
    }

    buildFormData() {
        const formData = new FormData();
        const manifest = [];
        this.entries.forEach((entry) => {
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
            entries: this.entries.map((entry) => ({
                relativePath: entry.relativePath,
                size: entry.file.size,
                source: entry.source,
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

    render() {
        if (this.elements.list) {
            const totalBytes = this.getTotalBytes();
            const totalLimit = this.maxBytes || 0;
            const perFileLimit = this.maxPerFileBytes || 0;

            this.elements.list.innerHTML = this.entries.map((entry, index) => {
                const fileSize = entry.file.size;
                const isNearLimit = perFileLimit && fileSize > perFileLimit * 0.8;
                const pathParts = entry.relativePath.split('/');
                const displayPath = pathParts.length > 1
                    ? `<span class="text-muted" style="font-size:0.75rem">${pathParts.slice(0, -1).join('/')}/</span>${pathParts[pathParts.length - 1]}`
                    : entry.relativePath;
                const sourceIcon = entry.source === 'folder'
                    ? `<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="text-muted" style="flex-shrink:0"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path></svg>`
                    : `<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="text-muted" style="flex-shrink:0"><path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"></path><polyline points="13 2 13 9 20 9"></polyline></svg>`;

                return `
                <div class="file-chip" style="align-items:flex-start;gap:6px;${isNearLimit ? 'border-color:var(--warning-color);' : ''}">
                    ${sourceIcon}
                    <span class="truncate" style="max-width:260px;" title="${entry.relativePath}">${displayPath}</span>
                    <span class="text-xs text-muted" style="white-space:nowrap;${isNearLimit ? 'color:var(--warning-color);font-weight:600;' : ''}">${formatBytes(fileSize)}</span>
                    <button type="button" class="btn btn-ghost btn-sm p-1 ml-1 text-danger h-auto" style="flex-shrink:0;line-height:1;" data-remove-upload="${index}" title="移除">&times;</button>
                </div>
            `}).join('');

            this.elements.list.querySelectorAll('[data-remove-upload]').forEach((button) => {
                button.addEventListener('click', () => {
                    const index = Number(button.dataset.removeUpload);
                    this.entries.splice(index, 1);
                    this.render();
                    this.options.onChange?.(this.getSnapshot());
                });
            });
        }

        if (this.elements.summary) {
            if (!this.entries.length) {
                this.elements.summary.textContent = '未选择文件';
            } else {
                const totalBytes = this.getTotalBytes();
                const totalMB = this.maxBytes ? (this.maxBytes / 1024 / 1024).toFixed(0) : '';
                const perFileMB = this.maxPerFileBytes ? (this.maxPerFileBytes / 1024 / 1024).toFixed(0) : '';
                let text = `${this.entries.length} 个文件 / ${formatBytes(totalBytes)}`;
                if (totalMB) text += `（限 ${perFileMB ? perFileMB + 'MB/个, ' : ''}共${totalMB}MB）`;
                this.elements.summary.textContent = text;
            }
        }
    }
}
