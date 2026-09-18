/**
 * Chat image attachment helpers.
 *
 * Preview opening is delegated to the shared liquid-glass lightbox
 * (ls_image_lightbox.js) so chat, private messages, the message center and
 * assignment attachments all look and behave the same (prev/next within
 * the same message, wheel zoom, drag pan, keyboard).  The
 * `ChatImagePreviewController` class name and its `ensure/isOpen/open/close`
 * methods are kept so existing callers keep working.
 */
import { closeImageLightbox, isImageLightboxOpen, openImageLightbox } from './ls_image_lightbox.js';

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
    const dimensions = item?.width && item?.height ? `${item.width}×${item.height}` : '';
    const size = typeof formatBytes === 'function' ? formatBytes(item?.file_size) : '';
    return [dimensions, size].filter(Boolean).join(' · ');
}

function isImageAttachment(item) {
    if (!item) return false;
    if (item.is_image || item.type === 'image' || item.kind === 'image') return true;
    const mime = String(item.mime_type || item.content_type || '').toLowerCase();
    if (mime.startsWith('image/')) return true;
    return Boolean(item.thumbnail_url || item.preview_url);
}

function attachmentKey(item) {
    return String(item?.attachment_id || item?.id || getChatImageAttachmentPreviewUrl(item) || '');
}

export class ChatImagePreviewController {
    constructor(options = {}) {
        this.onError = typeof options.onError === 'function' ? options.onError : () => {};
        this.onMissingPreview = typeof options.onMissingPreview === 'function' ? options.onMissingPreview : () => {};
        this.formatBytes = typeof options.formatBytes === 'function' ? options.formatBytes : () => '';
        this.groupLabel = String(options.groupLabel || '');
    }

    /** Kept for callers that used to pre-create the modal; the lightbox builds itself lazily. */
    ensure() {
        return null;
    }

    isOpen() {
        return isImageLightboxOpen();
    }

    toLightboxItem(attachment) {
        const previewUrl = getChatImageAttachmentPreviewUrl(attachment);
        if (!previewUrl) return null;
        return {
            src: previewUrl,
            previewSrc: '',
            originalSrc: getChatImageAttachmentOriginalUrl(attachment) || previewUrl,
            title: String(attachment.name || '图片'),
            meta: getChatImageAttachmentDisplayMeta(attachment, this.formatBytes),
        };
    }

    /**
     * @param {object} item      the clicked attachment
     * @param {object[]} [siblings] all attachments of the same message; only
     *        images are kept so prev/next stays inside that message
     */
    open(item, siblings = []) {
        const attachment = normalizeChatImageAttachment(item);
        if (!attachment) {
            return;
        }
        if (!getChatImageAttachmentPreviewUrl(attachment)) {
            this.onMissingPreview('图片预览暂不可用');
            return;
        }
        const pool = (Array.isArray(siblings) && siblings.length ? siblings : [attachment])
            .map(normalizeChatImageAttachment)
            .filter((entry) => entry && isImageAttachment(entry) && getChatImageAttachmentPreviewUrl(entry));
        const clickedKey = attachmentKey(attachment);
        let index = pool.findIndex((entry) => attachmentKey(entry) === clickedKey);
        if (index < 0) {
            pool.unshift(attachment);
            index = 0;
        }
        const items = pool.map((entry) => this.toLightboxItem(entry)).filter(Boolean);
        if (!items.length) {
            this.onMissingPreview('图片预览暂不可用');
            return;
        }
        openImageLightbox({ items, index: Math.max(0, Math.min(index, items.length - 1)), groupLabel: this.groupLabel });
    }

    close() {
        closeImageLightbox();
    }
}
