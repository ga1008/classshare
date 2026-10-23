/**
 * Shared learning-document kind registry (client mirror of
 * classroom_app/services/material_document_kind_service.py).
 *
 * The server writes `document_kind` / `kind_label` / `kind_color` on every
 * material item. Consumers (library cards, explorer, classroom lists) call
 * `getMaterialKind(item)` and never branch on extensions themselves. The
 * fallback classifier only runs for payloads that predate the server field.
 */

export const MATERIAL_KINDS = Object.freeze({
    git_repository: { label: 'Git 仓库', short: 'GIT', tone: 'orange', color: '#ea580c', icon: 'git' },
    lessondoc_pack: { label: '学习文档包', short: 'LD', tone: 'violet', color: '#7c3aed', icon: 'book' },
    html_package: { label: 'HTML 课件包', short: 'PKG', tone: 'indigo', color: '#4f46e5', icon: 'layers' },
    html_page: { label: '网页文档', short: 'HTML', tone: 'amber', color: '#d97706', icon: 'code' },
    markdown: { label: 'Markdown', short: 'MD', tone: 'teal', color: '#0f766e', icon: 'text' },
    pdf: { label: 'PDF', short: 'PDF', tone: 'rose', color: '#e11d48', icon: 'text' },
    office_document: { label: 'Word 文档', short: 'DOC', tone: 'blue', color: '#2563eb', icon: 'text' },
    spreadsheet: { label: 'Excel 表格', short: 'XLS', tone: 'green', color: '#059669', icon: 'table' },
    presentation: { label: '演示文稿', short: 'PPT', tone: 'amber', color: '#f59e0b', icon: 'present' },
    image: { label: '图片', short: 'IMG', tone: 'purple', color: '#8b5cf6', icon: 'image' },
    code: { label: '源码', short: 'SRC', tone: 'sky', color: '#0284c7', icon: 'code' },
    text: { label: '文本', short: 'TXT', tone: 'slate', color: '#64748b', icon: 'text' },
    archive: { label: '压缩包', short: 'ZIP', tone: 'slate', color: '#475569', icon: 'archive' },
    media: { label: '音视频', short: 'AV', tone: 'pink', color: '#db2777', icon: 'play' },
    folder: { label: '文件夹', short: 'DIR', tone: 'sky', color: '#0ea5e9', icon: 'folder' },
    file: { label: '文件', short: 'FILE', tone: 'slate', color: '#94a3b8', icon: 'file' },
});

export const MATERIAL_KIND_GROUPS = Object.freeze([
    { key: 'git_repository', label: 'Git 仓库', kinds: ['git_repository'] },
    { key: 'lessondoc_pack', label: '学习文档包', kinds: ['lessondoc_pack'] },
    { key: 'html', label: 'HTML 课件', kinds: ['html_package', 'html_page'] },
    { key: 'markdown', label: 'Markdown', kinds: ['markdown'] },
    { key: 'document', label: '办公文档', kinds: ['pdf', 'office_document', 'spreadsheet', 'presentation'] },
    { key: 'media', label: '图片 / 媒体', kinds: ['image', 'media'] },
    { key: 'folder', label: '文件夹', kinds: ['folder'] },
    { key: 'other', label: '其他', kinds: ['code', 'text', 'archive', 'file'] },
]);

const ICON_PATHS = Object.freeze({
    folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    file: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>',
    text: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/><path d="M9 13h6"/><path d="M9 17h6"/>',
    code: '<path d="m8 9-3 3 3 3"/><path d="m16 9 3 3-3 3"/><path d="m13 6-2 12"/>',
    git: '<circle cx="6" cy="6" r="2.5"/><circle cx="18" cy="6" r="2.5"/><circle cx="6" cy="18" r="2.5"/><path d="M6 8.5v7"/><path d="M18 8.5c0 4-4 4-7 5-2 .5-3 1.5-3 2"/>',
    book: '<path d="M4 5a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v15a1 1 0 0 1-1 1H6a2 2 0 0 1-2-2z"/><path d="M8 3v18"/><path d="M12 8h4"/><path d="M12 12h4"/>',
    layers: '<path d="m12 3 9 5-9 5-9-5z"/><path d="m3 13 9 5 9-5"/><path d="m3 17 9 5 9-5"/>',
    table: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 10h18"/><path d="M3 15h18"/><path d="M10 4v16"/>',
    present: '<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M12 16v4"/><path d="M8 20h8"/><path d="m8 12 3-3 2 2 3-4"/>',
    image: '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="1.6"/><path d="m21 16-5-5-7 7"/>',
    archive: '<rect x="3" y="4" width="18" height="5" rx="1"/><path d="M5 9v10a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V9"/><path d="M10 13h4"/>',
    play: '<circle cx="12" cy="12" r="9"/><path d="m10 8 6 4-6 4z"/>',
    refresh: '<path d="M21 12a9 9 0 1 1-2.64-6.36"/><path d="M21 3v6h-6"/>',
    external: '<path d="M14 4h6v6"/><path d="M20 4 10 14"/><path d="M19 13v6a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h6"/>',
    grid: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
    list: '<path d="M8 6h13"/><path d="M8 12h13"/><path d="M8 18h13"/><path d="M3 6h.01"/><path d="M3 12h.01"/><path d="M3 18h.01"/>',
    back: '<path d="m12 19-7-7 7-7"/><path d="M19 12H5"/>',
    forward: '<path d="M5 12h14"/><path d="m12 5 7 7-7 7"/>',
    up: '<path d="m5 12 7-7 7 7"/><path d="M12 19V5"/>',
    chevron: '<path d="m9 6 6 6-6 6"/>',
    search: '<circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/>',
    close: '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
    download: '<path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M4 19h16"/>',
    pencil: '<path d="M17 3a2.8 2.8 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5z"/><path d="m15 5 4 4"/>',
    trash: '<path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M6 6v14a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2V6"/><path d="M10 11v6"/><path d="M14 11v6"/>',
    move: '<path d="M5 9l-3 3 3 3"/><path d="M9 5l3-3 3 3"/><path d="M15 19l-3 3-3-3"/><path d="M19 9l3 3-3 3"/><path d="M2 12h20"/><path d="M12 2v20"/>',
    plus: '<path d="M12 5v14"/><path d="M5 12h14"/>',
    check: '<path d="M20 6 9 17l-5-5"/>',
    alert: '<path d="m10.3 3.9-8 13.8A2 2 0 0 0 4 20.7h16a2 2 0 0 0 1.7-3l-8-13.8a2 2 0 0 0-3.4 0z"/><path d="M12 9v4"/><path d="M12 17h.01"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 16v-4"/><path d="M12 8h.01"/>',
    link: '<path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7"/><path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7"/>',
    users: '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.9"/><path d="M16 3.1a4 4 0 0 1 0 7.8"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    eye: '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
    home: '<path d="m3 11 9-8 9 8"/><path d="M5 10v10a1 1 0 0 0 1 1h4v-6h4v6h4a1 1 0 0 0 1-1V10"/>',
    maximize: '<path d="M8 3H5a2 2 0 0 0-2 2v3"/><path d="M21 8V5a2 2 0 0 0-2-2h-3"/><path d="M3 16v3a2 2 0 0 0 2 2h3"/><path d="M16 21h3a2 2 0 0 0 2-2v-3"/>',
    more: '<circle cx="5" cy="12" r="1.6"/><circle cx="12" cy="12" r="1.6"/><circle cx="19" cy="12" r="1.6"/>',
    tree: '<path d="M4 5h6"/><path d="M8 12h8"/><path d="M12 19h8"/><path d="M4 5v14"/><path d="M8 12v7"/>',
});

/** Inline SVG so the module has no dependency on the generated LQ icon set. */
export function kindIcon(name, size = 16) {
    const body = ICON_PATHS[name] || ICON_PATHS.file;
    return `<svg class="materials-kind-icon" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${body}</svg>`;
}

const HTML_EXTENSIONS = new Set(['html', 'htm', 'xhtml']);
const CODE_EXTENSIONS = new Set(['py', 'js', 'mjs', 'ts', 'tsx', 'jsx', 'json', 'yaml', 'yml', 'toml', 'css', 'scss', 'sh', 'sql', 'java', 'c', 'cpp', 'h', 'cs', 'go', 'rs', 'rb', 'php', 'xml', 'csv', 'ipynb']);
const ARCHIVE_EXTENSIONS = new Set(['zip', 'rar', '7z', 'tar', 'gz', 'tgz']);
const MEDIA_EXTENSIONS = new Set(['mp4', 'webm', 'mov', 'avi', 'mkv', 'mp3', 'wav', 'ogg', 'm4a', 'flac', 'aac']);

function extensionOf(item) {
    const ext = String(item?.file_ext || '').trim().toLowerCase().replace(/^\./, '');
    if (ext) return ext;
    const name = String(item?.name || '');
    return name.includes('.') ? name.split('.').pop().toLowerCase() : '';
}

/** Fallback classifier for payloads without `document_kind` (keeps older APIs working). */
export function classifyMaterialKind(item) {
    if (!item) return 'file';
    if (item.is_git_repository) return 'git_repository';
    if (item.lessondoc_pack) return 'lessondoc_pack';
    if (item.node_type === 'folder') {
        if (item.is_html_package) return 'html_package';
        if (item.is_renderable && item.render_kind === 'html') return 'html_page';
        return 'folder';
    }
    const ext = extensionOf(item);
    const previewType = String(item.preview_type || '');
    if (HTML_EXTENSIONS.has(ext)) return 'html_page';
    if (previewType === 'markdown' || ext === 'md' || ext === 'markdown') return 'markdown';
    if (previewType === 'pdf' || ext === 'pdf') return 'pdf';
    if (previewType === 'document' || ['doc', 'docx', 'odt', 'rtf'].includes(ext)) return 'office_document';
    if (previewType === 'spreadsheet' || ['xls', 'xlsx', 'ods'].includes(ext)) return 'spreadsheet';
    if (previewType === 'presentation' || ['ppt', 'pptx', 'odp'].includes(ext)) return 'presentation';
    if (previewType === 'image' || ['png', 'jpg', 'jpeg', 'gif', 'svg', 'webp', 'bmp'].includes(ext)) return 'image';
    if (ARCHIVE_EXTENSIONS.has(ext)) return 'archive';
    if (MEDIA_EXTENSIONS.has(ext)) return 'media';
    if (CODE_EXTENSIONS.has(ext)) return 'code';
    if (previewType === 'text' || ext === 'txt' || ext === 'log') return 'text';
    return 'file';
}

/** @returns {{kind:string,label:string,short:string,tone:string,color:string,icon:string}} */
export function getMaterialKind(item) {
    const kind = String(item?.document_kind || classifyMaterialKind(item));
    const meta = MATERIAL_KINDS[kind] || MATERIAL_KINDS.file;
    return {
        kind: MATERIAL_KINDS[kind] ? kind : 'file',
        label: String(item?.kind_label || meta.label),
        short: String(item?.kind_short || meta.short),
        tone: String(item?.kind_tone || meta.tone),
        color: String(item?.kind_color || meta.color),
        icon: meta.icon,
    };
}

export function kindGroupFor(kind) {
    return MATERIAL_KIND_GROUPS.find((group) => group.kinds.includes(kind))?.key || 'other';
}

/** Label for a git provider badge, shared with legacy lists. */
export function providerLabel(item) {
    const provider = String(item?.git_provider || '').toLowerCase();
    return { github: 'GitHub', gitee: 'Gitee', gitlab: 'GitLab', local: 'Local Git' }[provider] || 'Git';
}
