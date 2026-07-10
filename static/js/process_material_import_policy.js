export const PROCESS_DOCUMENT_IMPORT_EXTENSIONS = [
    '.doc',
    '.docx',
    '.pdf',
    '.png',
    '.jpg',
    '.jpeg',
    '.webp',
    '.bmp',
    '.gif',
    '.md',
    '.txt',
];

export const PROCESS_DOCUMENT_IMPORT_ACCEPT = PROCESS_DOCUMENT_IMPORT_EXTENSIONS.join(',');
export const PROCESS_DOCUMENT_IMPORT_FORMAT_HINT = '支持 Word（doc/docx）、PDF、Markdown/TXT 和常见图片；不支持 Excel、压缩包或无扩展名文件。';
export const PROCESS_DOCUMENT_IMPORT_MAX_FILES = 8;
export const PROCESS_DOCUMENT_IMPORT_MAX_BYTES = 30 * 1024 * 1024;

function fileExtension(file) {
    const name = String(file?.name || '').trim();
    const index = name.lastIndexOf('.');
    return index > 0 ? name.slice(index).toLowerCase() : '';
}

export function getProcessDocumentImportFileKey(file) {
    if (!file) return '';
    const name = String(file.name || '').trim().toLowerCase();
    if (!name) return '';
    const size = Number(file.size || 0);
    const modified = Number(file.lastModified || 0);
    return `${name}:${size}:${modified}`;
}

export function formatProcessImportFileSize(bytes) {
    const size = Number(bytes || 0);
    if (!Number.isFinite(size) || size <= 0) return '0 KB';
    if (size >= 1024 * 1024) {
        return `${(size / 1024 / 1024).toFixed(size >= 10 * 1024 * 1024 ? 0 : 1)} MB`;
    }
    return `${Math.max(1, Math.round(size / 1024))} KB`;
}

export function getProcessDocumentImportFileProblem(file, currentCount = 0) {
    if (currentCount >= PROCESS_DOCUMENT_IMPORT_MAX_FILES) {
        return `一次最多导入 ${PROCESS_DOCUMENT_IMPORT_MAX_FILES} 个文件，请先移除部分文件。`;
    }
    if (!file) return '请选择要导入的文件。';
    const name = String(file.name || '未命名文件');
    const ext = fileExtension(file);
    if (!ext || !PROCESS_DOCUMENT_IMPORT_EXTENSIONS.includes(ext)) {
        return `不支持《${name}》的格式${ext ? `（${ext}）` : ''}，请上传 Word/PDF、Markdown/TXT 或图片。`;
    }
    if (Number(file.size || 0) <= 0) {
        return `《${name}》是空文件，请重新选择。`;
    }
    if (Number(file.size || 0) > PROCESS_DOCUMENT_IMPORT_MAX_BYTES) {
        return `《${name}》超过 30MB 单文件上限，请压缩或拆分后再导入。`;
    }
    return '';
}

export function getProcessDocumentImportDuplicateProblem(file, pickedFiles = []) {
    const key = getProcessDocumentImportFileKey(file);
    if (!key) return '';
    const hasDuplicate = Array.from(pickedFiles || []).some((picked) => getProcessDocumentImportFileKey(picked) === key);
    if (!hasDuplicate) return '';
    const name = String(file.name || '未命名文件');
    return `文件已在待导入列表中：${name}`;
}
