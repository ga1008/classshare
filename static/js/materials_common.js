export function getMaterialTypeLabel(item) {
    if (!item) return '文件';
    if (item.node_type === 'folder') return '文件夹';
    if (item.preview_type === 'markdown') return 'Markdown';
    if (item.preview_type === 'image') return '图片';
    if (item.preview_type === 'text') {
        return item.type_label || (item.file_ext ? item.file_ext.toUpperCase() : '文本');
    }
    if (item.file_ext) return item.file_ext.toUpperCase();
    return item.type_label || '文件';
}

export function hasLearningDocument(item) {
    return Boolean(item && item.node_type === 'folder' && item.document_readme_id);
}

export function getLearningDocumentUrl(item) {
    return hasLearningDocument(item) ? `/materials/view/${item.document_readme_id}` : '';
}

export function getMaterialPreviewUrl(item) {
    if (!item) return '';
    if (item.node_type === 'file' && item.preview_supported) {
        return `/materials/view/${item.id}`;
    }
    return getLearningDocumentUrl(item);
}

export function getMaterialPrimaryAction(item) {
    if (!item) {
        return { action: '', label: '' };
    }
    if (item.node_type === 'folder') {
        return { action: 'open', label: '打开' };
    }
    if (item.preview_supported) {
        return { action: 'preview', label: '预览' };
    }
    return { action: 'download', label: '下载' };
}
