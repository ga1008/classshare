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

async function collectDroppedFiles(dataTransfer) {
    const items = Array.from(dataTransfer?.items || []);
    if (items.length && typeof items[0].webkitGetAsEntry === 'function') {
        const results = [];
        for (const item of items) {
            const entry = item.webkitGetAsEntry();
            if (!entry) continue;
            const nested = await readDroppedEntry(entry);
            results.push(...nested);
        }
        if (results.length) return results;
    }

    return Array.from(dataTransfer?.files || []).map((file) => ({
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
        this.maxBytes = Number(this.options.maxBytes || 0);
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
            ['dragenter', 'dragover'].forEach((eventName) => {
                dropZone.addEventListener(eventName, (event) => {
                    event.preventDefault();
                    dropZone.classList.add('dragover');
                });
            });
            ['dragleave', 'drop'].forEach((eventName) => {
                dropZone.addEventListener(eventName, (event) => {
                    event.preventDefault();
                    dropZone.classList.remove('dragover');
                });
            });
            dropZone.addEventListener('drop', async (event) => {
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

        rawEntries.forEach((rawEntry) => {
            const file = rawEntry.file;
            if (!file) return;

            if (this.maxFiles && nextEntries.length >= this.maxFiles) {
                this.notify(`文件数量不能超过 ${this.maxFiles} 个`, 'warning');
                return;
            }

            const normalizedPath = dedupeRelativePath(
                normalizeRelativePath(rawEntry.relativePath || file.webkitRelativePath || file.name, file.name),
                usedPaths,
            );

            if (!matchesAllowedFileTypes(normalizedPath, file.type, this.allowedFileTypes)) {
                this.notify(`文件 ${normalizedPath} 不在允许类型范围内`, 'warning');
                return;
            }

            if (this.maxBytes && file.size > this.maxBytes) {
                this.notify(`文件 ${normalizedPath} 超过大小限制`, 'error');
                return;
            }

            if (this.maxBytes && totalBytes + file.size > this.maxBytes) {
                this.notify('总文件大小超过限制', 'error');
                return;
            }

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
            this.elements.list.innerHTML = this.entries.map((entry, index) => `
                <div class="file-chip">
                    <span class="truncate" style="max-width: 220px;" title="${entry.relativePath}">${entry.relativePath}</span>
                    <span class="text-xs text-muted">${formatBytes(entry.file.size)}</span>
                    <button type="button" class="btn btn-ghost btn-sm p-1 ml-1 text-danger h-auto" data-remove-upload="${index}">&times;</button>
                </div>
            `).join('');
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
                this.elements.summary.textContent = `已选 ${this.entries.length} 个文件 / ${formatBytes(this.getTotalBytes())}`;
            }
        }
    }
}
