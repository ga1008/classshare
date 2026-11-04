import { escapeHtml } from './lq/html.js';

const REPOSITORY_PROVIDER_META = {
    github: { color: '#24292f', badge: 'GitHub', icon: 'GH' },
    gitee: { color: '#c71d23', badge: 'Gitee', icon: 'GI' },
    gitlab: { color: '#fc6d26', badge: 'GitLab', icon: 'GL' },
    local: { color: '#0f766e', badge: 'Local Git', icon: 'LG' },
    'self-hosted': { color: '#a16207', badge: 'Git', icon: 'GIT' },
};

export function isGitRepository(item) {
    return Boolean(item && item.is_git_repository);
}

export function getRepositoryVisualMeta(item) {
    if (!isGitRepository(item)) return null;
    const provider = String(item.git_provider || 'self-hosted').trim().toLowerCase();
    const fallback = REPOSITORY_PROVIDER_META['self-hosted'];
    return REPOSITORY_PROVIDER_META[provider] || fallback;
}

export function getMaterialTypeLabel(item) {
    if (!item) return '文件';
    if (isGitRepository(item)) return 'Git 仓库';
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

export function isRenderable(item) {
    return Boolean(item && item.is_renderable && item.render_url);
}

export function getRenderUrl(item) {
    // 优先走全屏渲染壳页（带返回/白板/AI 助手），无壳页时退回裸渲染路由。
    return isRenderable(item) ? String(item.render_shell_url || item.render_url || '') : '';
}

export function getRenderLabel(item) {
    return (item && item.render_label) ? String(item.render_label) : '渲染';
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

export function buildMaterialUploadForm(fileList, { parentId, classOfferingId } = {}) {
    const formData = new FormData();
    const manifest = Array.from(fileList, (file) => {
        formData.append('files', file, file.name);
        return { relative_path: file.webkitRelativePath || file.name, content_type: file.type || '' };
    });
    formData.append('manifest', JSON.stringify(manifest));
    if (parentId) formData.append('parent_id', String(parentId));
    if (classOfferingId) formData.append('class_offering_id', String(classOfferingId));
    return formData;
}

export function renderMaterialBreadcrumbs(breadcrumbs, rootLabel) {
    const path = [{ id: 0, name: rootLabel }, ...breadcrumbs];
    return path.map((crumb, index) => index === path.length - 1
        ? `<span aria-current="location" tabindex="-1">${escapeHtml(crumb.name)}</span>`
        : `<button data-lq-component="button" class="lq-btn lq-btn--sm lq-btn--glass" type="button" data-crumb-id="${Number(crumb.id)}">${escapeHtml(crumb.name)}</button>`
    ).join('<span class="separator" aria-hidden="true">/</span>');
}

export function hasInlineMaterialPreview(item) {
    if (item.node_type !== 'file') return false;
    return (item.preview_mode === 'image' && Boolean(item.preview_image_url))
        || (item.preview_mode === 'document' && Boolean(item.preview_frame_url))
        || (item.preview_mode === 'media' && Boolean(item.preview_media_url));
}
