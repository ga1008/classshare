/**
 * feedback.js
 * Feedback modal logic supporting bug reports and feature requests.
 */
import { API, apiFetch } from './api.js';
import { showToast } from './ui.js';
import { createEmojiPicker } from './emoji_picker.js';

const MAX_FEEDBACK_ATTACHMENTS = 5;
const MAX_ATTACHMENT_SIZE_BYTES = 10 * 1024 * 1024;
const ALLOWED_IMAGE_TYPES = new Set(['image/png', 'image/jpeg', 'image/gif', 'image/webp', 'image/bmp']);

const AUTOFILL_SECTION_MAP = {
    '/dashboard': '首页/仪表盘',
    '/classroom': '课堂互动',
    '/courses': '课程管理',
    '/assignments': '作业系统',
    '/exams': '考试系统',
    '/materials': '学习资料',
    '/profile': '个人中心',
    '/manage': '管理中心',
    '/blog': '博客中心',
    '/message': '消息中心',
};

const TYPE_CONFIG = {
    bug: {
        titlePlaceholder: '请输入问题的简要标题',
        descriptionPlaceholder: '请详细描述：您进行了什么操作？出现了什么异常？期望的结果是什么？',
        showSection: true,
        showEmoji: false,
    },
    feature: {
        titlePlaceholder: '请输入新功能建议的简要标题',
        descriptionPlaceholder: '请详细描述您希望添加的功能，包括使用场景和预期效果。',
        showSection: false,
        showEmoji: true,
    },
    report: {
        titlePlaceholder: '请输入举报事项的简要标题',
        descriptionPlaceholder: '请尽量说明举报对象、发生位置、具体情况和需要老师关注的原因。',
        showSection: true,
        showEmoji: false,
    },
};

function guessSectionFromPath() {
    const path = window.location.pathname || '';
    for (const [prefix, label] of Object.entries(AUTOFILL_SECTION_MAP)) {
        if (path.startsWith(prefix)) return label;
    }
    return '';
}

class FeedbackModal {
    constructor() {
        this.modalBackdrop = document.getElementById('feedback-modal');
        this.feedbackForm = document.getElementById('feedback-form');
        this.tabBug = document.getElementById('feedback-tab-bug');
        this.tabFeature = document.getElementById('feedback-tab-feature');
        this.tabReport = document.getElementById('feedback-tab-report');
        this.sectionGroup = document.getElementById('feedback-section-group');
        this.sectionInput = document.getElementById('feedback-section');
        this.sectionAutoBadge = document.getElementById('feedback-section-auto-badge');
        this.titleInput = document.getElementById('feedback-title');
        this.descTextarea = document.getElementById('feedback-description');
        this.emojiToggle = document.getElementById('feedback-emoji-toggle');
        this.attachmentsList = document.getElementById('feedback-attachment-list');
        this.attachmentInput = document.getElementById('feedback-attachment-input');
        this.submitBtn = document.getElementById('feedback-submit-btn');
        this.submitLabel = this.submitBtn?.querySelector('.feedback-submit-label');
        this.successPanel = document.getElementById('feedback-success');
        this.successMessage = document.getElementById('feedback-success-message');
        this.formPanel = document.getElementById('feedback-form-panel');
        this.footerEl = this.modalBackdrop?.querySelector('.modal-footer');

        this.currentType = 'bug';
        this.attachments = [];
        this.emojiPicker = null;
        this.submitting = false;

        this._init();
    }

    _init() {
        if (!this.modalBackdrop) return;

        this._bindEvents();
        this._autoDetectSection();
    }

    _bindEvents() {
        // Open modal from button
        document.addEventListener('click', (e) => {
            const trigger = e.target.closest('[data-open-feedback]');
            if (trigger) {
                e.preventDefault();
                this.open();
            }
        });

        // Close on backdrop click
        this.modalBackdrop.addEventListener('click', (e) => {
            if (e.target === this.modalBackdrop) {
                this.close();
            }
        });

        // Close button
        const closeBtn = this.modalBackdrop.querySelector('[data-dismiss="modal"]');
        if (closeBtn) {
            closeBtn.addEventListener('click', () => this.close());
        }

        // Tab switching
        if (this.tabBug) {
            this.tabBug.addEventListener('click', () => this._switchTab('bug'));
        }
        if (this.tabFeature) {
            this.tabFeature.addEventListener('click', () => this._switchTab('feature'));
        }
        if (this.tabReport) {
            this.tabReport.addEventListener('click', () => this._switchTab('report'));
        }

        // Emoji toggle
        if (this.emojiToggle && this.descTextarea) {
            this.emojiToggle.addEventListener('click', () => this._toggleEmojiPicker());
        }

        // Attachment input
        if (this.attachmentInput) {
            this.attachmentInput.addEventListener('change', (e) => {
                this._handleAttachmentSelect(e);
            });
        }

        // Form submit
        if (this.feedbackForm) {
            this.feedbackForm.addEventListener('submit', (e) => {
                e.preventDefault();
                this._submit();
            });
        }

        // Keyboard shortcut: Esc to close
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && this.modalBackdrop.classList.contains('show')) {
                this.close();
            }
        });
    }

    _autoDetectSection() {
        const section = guessSectionFromPath();
        if (section && this.sectionInput) {
            this.sectionInput.value = section;
            this.sectionInput.style.color = 'var(--success-color)';
            this.sectionInput.style.fontWeight = '550';
        }
    }

    _switchTab(type) {
        const nextType = TYPE_CONFIG[type] ? type : 'bug';
        const config = TYPE_CONFIG[nextType];
        this.currentType = nextType;
        [
            [this.tabBug, 'bug'],
            [this.tabFeature, 'feature'],
            [this.tabReport, 'report'],
        ].forEach(([tab, tabType]) => {
            if (!tab) return;
            const isActive = nextType === tabType;
            tab.classList.toggle('is-active', isActive);
            tab.setAttribute('aria-selected', isActive ? 'true' : 'false');
        });
        if (this.sectionGroup) {
            this.sectionGroup.style.display = config.showSection ? '' : 'none';
        }
        if (this.titleInput) {
            this.titleInput.placeholder = config.titlePlaceholder;
        }
        if (this.descTextarea) {
            this.descTextarea.placeholder = config.descriptionPlaceholder;
        }
        if (this.emojiToggle) {
            this.emojiToggle.style.visibility = config.showEmoji ? '' : 'hidden';
        }
    }

    _toggleEmojiPicker() {
        if (!this.emojiPicker) {
            this.emojiPicker = createEmojiPicker({ targetInput: this.descTextarea });
            const wrapper = document.getElementById('feedback-emoji-picker-wrap');
            if (wrapper) {
                wrapper.appendChild(this.emojiPicker.element);
            }
        }
        this.emojiPicker.toggle();
    }

    _handleAttachmentSelect(e) {
        const files = e.target.files;
        if (!files || !files.length) return;

        let added = 0;
        for (const file of Array.from(files)) {
            if (this.attachments.length >= MAX_FEEDBACK_ATTACHMENTS) {
                showToast(`最多上传 ${MAX_FEEDBACK_ATTACHMENTS} 张截图`, 'warning');
                break;
            }

            if (!ALLOWED_IMAGE_TYPES.has(file.type)) {
                showToast(`${file.name} 格式不支持，仅支持 PNG、JPEG、GIF、WebP、BMP`, 'warning');
                continue;
            }

            if (file.size > MAX_ATTACHMENT_SIZE_BYTES) {
                showToast(`${file.name} 超过 10MB`, 'warning');
                continue;
            }

            this.attachments.push({
                file,
                original_filename: file.name,
                preview_url: URL.createObjectURL(file),
                uploaded: false,
            });
            added += 1;
        }

        if (added > 0) {
            this._renderAttachments();
        }

        // Reset input
        if (this.attachmentInput) this.attachmentInput.value = '';
    }

    async _uploadAttachment(feedbackId, attachment) {
        const formData = new FormData();
        formData.append('file', attachment.file);

        const result = await apiFetch(`/api/feedback/${feedbackId}/upload`, {
            method: 'POST',
            body: formData,
            headers: {},
            silent: true,
        });

        attachment.uploaded = true;
        attachment.file_hash = result.file_hash;
        attachment.attachment_id = result.attachment_id;
        attachment.file_size = result.file_size;
        attachment.mime_type = result.mime_type;
        return result;
    }

    _renderAttachments() {
        if (!this.attachmentsList) return;
        this.attachmentsList.innerHTML = '';
        this.attachments.forEach((att, idx) => {
            const li = document.createElement('li');
            li.className = 'feedback-attachment-item';
            const img = document.createElement('img');
            img.src = att.preview_url || `/api/feedback/${this.feedbackId}/attachment/${att.file_hash}`;
            img.alt = att.original_filename || '反馈截图';
            img.loading = 'lazy';
            const removeBtn = document.createElement('button');
            removeBtn.type = 'button';
            removeBtn.className = 'feedback-attachment-remove';
            removeBtn.innerHTML = '&#x2715;';
            removeBtn.title = '移除';
            removeBtn.addEventListener('click', () => {
                if (att.preview_url) URL.revokeObjectURL(att.preview_url);
                this.attachments.splice(idx, 1);
                this._renderAttachments();
            });
            li.appendChild(img);
            li.appendChild(removeBtn);
            this.attachmentsList.appendChild(li);
        });
    }

    async _submit() {
        if (this.submitting) return;

        const title = this.titleInput?.value.trim() || '';
        const description = this.descTextarea?.value.trim() || '';

        if (!title) {
            showToast('请填写标题', 'warning');
            this.titleInput?.focus();
            return;
        }
        if (!description) {
            showToast('请填写描述', 'warning');
            this.descTextarea?.focus();
            return;
        }

        this.submitting = true;
        this._setSubmitting(true);

        try {
            const result = await API.post('/api/feedback', {
                feedback_type: this.currentType,
                section: this.sectionInput?.value.trim() || '',
                title,
                description,
                page_url: window.location.href,
            });

            this.feedbackId = result.feedback_id;

            let failedUploads = 0;
            if (this.attachments.length > 0) {
                this._setSubmitting(true, '上传截图中...');
                for (const attachment of this.attachments) {
                    try {
                        await this._uploadAttachment(this.feedbackId, attachment);
                    } catch (err) {
                        failedUploads += 1;
                        console.error('Feedback attachment upload failed:', err);
                    }
                }
            }

            // Show success
            if (this.formPanel) this.formPanel.style.display = 'none';
            if (this.successPanel) this.successPanel.removeAttribute('hidden');
            if (this.footerEl) this.footerEl.style.display = 'none';
            if (this.successMessage) {
                this.successMessage.textContent = failedUploads > 0
                    ? `反馈已提交；${failedUploads} 张截图上传失败，可稍后重新提交截图说明。`
                    : '您的反馈已成功提交，我们会尽快处理。每一份意见都让平台变得更好。';
            }

            if (failedUploads > 0) {
                showToast(`反馈已提交，但 ${failedUploads} 张截图上传失败`, 'warning', 4500);
            } else {
                showToast(result.message || '反馈提交成功！', 'success');
            }

            // Reset after 2.5 seconds
            setTimeout(() => {
                this._reset();
            }, 2500);
        } catch (err) {
            showToast(`提交失败: ${err.message}`, 'error');
            this._setSubmitting(false);
            this.submitting = false;
        }
    }

    _setSubmitting(loading, label = '提交反馈') {
        if (this.submitBtn) {
            this.submitBtn.classList.toggle('is-loading', loading);
            this.submitBtn.disabled = loading;
        }
        if (this.modalBackdrop) {
            this.modalBackdrop.classList.toggle('is-submitting', loading);
        }
        if (this.attachmentInput) {
            this.attachmentInput.disabled = loading;
        }
        if (this.submitLabel) {
            this.submitLabel.textContent = loading ? label : '提交反馈';
        }
    }

    open() {
        if (!this.modalBackdrop) return;
        this._autoDetectSection();
        this.modalBackdrop.classList.add('show');
        document.body.style.overflow = 'hidden';
    }

    close() {
        if (!this.modalBackdrop) return;
        this.modalBackdrop.classList.remove('show');
        document.body.style.overflow = '';

        // Close emoji picker if open
        if (this.emojiPicker && this.emojiPicker.isOpen()) {
            this.emojiPicker.close();
        }
    }

    _reset() {
        this.submitting = false;
        this.feedbackId = null;
        this.attachments.forEach((att) => {
            if (att.preview_url) URL.revokeObjectURL(att.preview_url);
        });
        this.attachments = [];
        this._setSubmitting(false);

        if (this.feedbackForm) this.feedbackForm.reset();
        if (this.formPanel) this.formPanel.style.display = '';
        if (this.successPanel) this.successPanel.setAttribute('hidden', '');
        if (this.successMessage) {
            this.successMessage.textContent = '您的反馈已成功提交，我们会尽快处理。每一份意见都让平台变得更好。';
        }
        if (this.footerEl) this.footerEl.style.display = '';
        if (this.attachmentsList) this.attachmentsList.innerHTML = '';
        if (this.attachmentInput) this.attachmentInput.disabled = false;

        this._switchTab('bug');
        this._autoDetectSection();
    }
}

// Auto-initialize when DOM is ready
function initFeedback() {
    if (document.getElementById('feedback-modal')) {
        new FeedbackModal();
    }
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initFeedback);
} else {
    initFeedback();
}
