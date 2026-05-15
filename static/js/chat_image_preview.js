export function normalizeChatImageAttachment(item) {
    if (!item || typeof item !== 'object') {
        return null;
    }
    const attachment = { ...item };
    const attachmentId = Number(attachment.attachment_id || attachment.id || 0) || null;
    if (attachmentId) {
        attachment.attachment_id = attachmentId;
        attachment.id = Number(attachment.id || attachmentId) || attachmentId;
    }
    return attachment;
}

export function getChatImageAttachmentThumbnailUrl(item) {
    return String(item?.thumbnail_url || item?.url || item?.preview_url || item?.original_url || '');
}

export function getChatImageAttachmentPreviewUrl(item) {
    return String(item?.preview_url || item?.thumbnail_url || item?.url || item?.original_url || '');
}

export function getChatImageAttachmentOriginalUrl(item) {
    return String(item?.original_url || item?.download_url || item?.preview_url || item?.url || '');
}

export function getChatImageAttachmentDisplayMeta(item, formatBytes) {
    const dimensions = item?.width && item?.height ? `${item.width}\u00d7${item.height}` : '';
    const size = typeof formatBytes === 'function' ? formatBytes(item?.file_size) : '';
    return [dimensions, size].filter(Boolean).join(' \u00b7 ');
}

export class ChatImagePreviewController {
    constructor(options = {}) {
        this.onError = typeof options.onError === 'function' ? options.onError : () => {};
        this.onMissingPreview = typeof options.onMissingPreview === 'function' ? options.onMissingPreview : () => {};
        this.formatBytes = typeof options.formatBytes === 'function' ? options.formatBytes : () => '';
        this.modal = null;
        this.image = null;
        this.title = null;
        this.meta = null;
        this.originalLink = null;
        this.returnFocus = null;
        this.handleKeydown = (event) => {
            if (event.key === 'Escape' && this.isOpen()) {
                this.close();
            }
        };
    }

    ensure() {
        if (this.modal) {
            return this.modal;
        }

        const modal = document.createElement('div');
        modal.className = 'chat-image-preview-modal';
        modal.hidden = true;
        modal.setAttribute('role', 'dialog');
        modal.setAttribute('aria-modal', 'true');
        modal.setAttribute('aria-labelledby', 'chat-image-preview-title');

        const backdrop = document.createElement('button');
        backdrop.type = 'button';
        backdrop.className = 'chat-image-preview-backdrop';
        backdrop.setAttribute('aria-label', '\u5173\u95ed\u56fe\u7247\u9884\u89c8');
        backdrop.addEventListener('click', () => this.close());
        modal.appendChild(backdrop);

        const shell = document.createElement('div');
        shell.className = 'chat-image-preview-shell';

        const header = document.createElement('div');
        header.className = 'chat-image-preview-header';

        const titleBlock = document.createElement('div');
        titleBlock.className = 'chat-image-preview-title-block';

        const title = document.createElement('strong');
        title.id = 'chat-image-preview-title';
        title.textContent = '\u56fe\u7247\u9884\u89c8';
        titleBlock.appendChild(title);

        const meta = document.createElement('span');
        meta.className = 'chat-image-preview-meta';
        titleBlock.appendChild(meta);
        header.appendChild(titleBlock);

        const actions = document.createElement('div');
        actions.className = 'chat-image-preview-actions';

        const originalLink = document.createElement('a');
        originalLink.className = 'btn btn-outline btn-sm chat-image-preview-original';
        originalLink.target = '_blank';
        originalLink.rel = 'noreferrer noopener';
        originalLink.textContent = '\u4e0b\u8f7d\u539f\u56fe';
        originalLink.title = '\u5728\u65b0\u6807\u7b7e\u9875\u6253\u5f00\u539f\u56fe';
        actions.appendChild(originalLink);

        const closeButton = document.createElement('button');
        closeButton.type = 'button';
        closeButton.className = 'btn btn-ghost btn-sm btn-icon chat-image-preview-close';
        closeButton.setAttribute('aria-label', '\u5173\u95ed\u9884\u89c8');
        closeButton.title = '\u5173\u95ed\u9884\u89c8';
        closeButton.textContent = '\u00d7';
        closeButton.addEventListener('click', () => this.close());
        actions.appendChild(closeButton);

        header.appendChild(actions);
        shell.appendChild(header);

        const body = document.createElement('div');
        body.className = 'chat-image-preview-body';

        const image = document.createElement('img');
        image.className = 'chat-image-preview-image';
        image.alt = '\u56fe\u7247\u9884\u89c8';
        image.decoding = 'async';
        image.addEventListener('error', () => {
            this.onError('\u9884\u89c8\u56fe\u52a0\u8f7d\u5931\u8d25');
        });
        body.appendChild(image);
        shell.appendChild(body);
        modal.appendChild(shell);
        document.body.appendChild(modal);
        document.addEventListener('keydown', this.handleKeydown);

        this.modal = modal;
        this.image = image;
        this.title = title;
        this.meta = meta;
        this.originalLink = originalLink;
        return modal;
    }

    isOpen() {
        return Boolean(this.modal && !this.modal.hidden);
    }

    open(item) {
        const attachment = normalizeChatImageAttachment(item);
        if (!attachment) {
            return;
        }
        const previewUrl = getChatImageAttachmentPreviewUrl(attachment);
        if (!previewUrl) {
            this.onMissingPreview('\u56fe\u7247\u9884\u89c8\u6682\u4e0d\u53ef\u7528');
            return;
        }

        const modal = this.ensure();
        const name = String(attachment.name || '\u56fe\u7247');
        const originalUrl = getChatImageAttachmentOriginalUrl(attachment);
        const metaText = getChatImageAttachmentDisplayMeta(attachment, this.formatBytes);

        this.returnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
        if (this.title) {
            this.title.textContent = name;
        }
        if (this.meta) {
            this.meta.textContent = metaText || '\u9884\u89c8\u56fe\u5df2\u538b\u7f29';
        }
        if (this.originalLink) {
            this.originalLink.href = originalUrl || previewUrl;
            this.originalLink.hidden = !originalUrl;
        }
        if (this.image) {
            this.image.removeAttribute('src');
            this.image.alt = name;
        }

        modal.hidden = false;
        modal.classList.add('is-open');
        document.body.classList.add('has-chat-image-preview-open');
        window.requestAnimationFrame(() => {
            if (this.image) {
                this.image.src = previewUrl;
            }
            this.originalLink?.focus({ preventScroll: true });
        });
    }

    close() {
        if (!this.modal) {
            return;
        }
        this.modal.classList.remove('is-open');
        this.modal.hidden = true;
        document.body.classList.remove('has-chat-image-preview-open');
        if (this.image) {
            this.image.removeAttribute('src');
        }
        if (this.returnFocus) {
            this.returnFocus.focus({ preventScroll: true });
        }
        this.returnFocus = null;
    }
}
