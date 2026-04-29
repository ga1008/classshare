/**
 * feedback.js
 * Feedback modal with type-accent colours, proper submit flow,
 * and a "my feedback" panel for viewing / withdrawing past feedback.
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

const TYPE_LABEL_MAP = { bug: 'Bug 修复', feature: '新功能反馈', report: '举报' };

class FeedbackModal {
    constructor() {
        this.modalBackdrop = document.getElementById('feedback-modal');
        if (!this.modalBackdrop) return;

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
        this.footerEl = document.getElementById('feedback-footer');

        // My-feedback elements
        this.myFeedbackBtn = document.getElementById('fb-my-feedback-btn');
        this.myPanel = document.getElementById('fb-my-panel');
        this.myContent = document.getElementById('fb-my-content');
        this.myBackBtn = document.getElementById('fb-my-back-btn');
        this.submitAnotherBtn = document.getElementById('fb-submit-another-btn');

        this.currentType = 'bug';
        this.attachments = [];
        this.emojiPicker = null;
        this.submitting = false;
        this.feedbackId = null;
        this.myFeedbackData = null;
        this.myPanelVisible = false;

        this._init();
    }

    /* ============================================================
     * Initialisation
     * ============================================================ */
    _init() {
        this._bindEvents();
        this._autoDetectSection();
        this._applyTypeAccent('bug');
    }

    _bindEvents() {
        // Open modal
        document.addEventListener('click', (e) => {
            const trigger = e.target.closest('[data-open-feedback]');
            if (trigger) {
                e.preventDefault();
                this.open();
            }
        });

        // Close on backdrop click
        this.modalBackdrop.addEventListener('click', (e) => {
            if (e.target === this.modalBackdrop) this.close();
        });

        // Close button
        const closeBtn = this.modalBackdrop.querySelector('[data-dismiss="modal"]');
        if (closeBtn) closeBtn.addEventListener('click', () => this.close());

        // Tab switching
        if (this.tabBug) this.tabBug.addEventListener('click', () => this._switchTab('bug'));
        if (this.tabFeature) this.tabFeature.addEventListener('click', () => this._switchTab('feature'));
        if (this.tabReport) this.tabReport.addEventListener('click', () => this._switchTab('report'));

        // Emoji toggle
        if (this.emojiToggle && this.descTextarea) {
            this.emojiToggle.addEventListener('click', () => this._toggleEmojiPicker());
        }

        // Attachment input
        if (this.attachmentInput) {
            this.attachmentInput.addEventListener('change', (e) => this._handleAttachmentSelect(e));
        }

        // Form submit
        if (this.feedbackForm) {
            this.feedbackForm.addEventListener('submit', (e) => {
                e.preventDefault();
                this._submit();
            });
        }

        // My feedback button (header)
        if (this.myFeedbackBtn) {
            this.myFeedbackBtn.addEventListener('click', () => this._openMyFeedback());
        }

        // My feedback back button
        if (this.myBackBtn) {
            this.myBackBtn.addEventListener('click', () => this._closeMyFeedback());
        }

        // Submit another button (in success state)
        if (this.submitAnotherBtn) {
            this.submitAnotherBtn.addEventListener('click', () => this._submitAnother());
        }

        // Escape to close
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && this.modalBackdrop.classList.contains('show')) this.close();
        });
    }

    /* ============================================================
     * Type accent
     * ============================================================ */
    _applyTypeAccent(type) {
        if (this.modalBackdrop) {
            this.modalBackdrop.setAttribute('data-type', type);
        }
    }

    /* ============================================================
     * Open / Close
     * ============================================================ */
    open() {
        if (!this.modalBackdrop) return;

        // Show backdrop FIRST so it is always visible regardless of
        // any subsequent state manipulation.
        this.modalBackdrop.classList.add('show');
        document.body.style.overflow = 'hidden';

        // Now reset internal panels to form view
        this._ensureFormVisible();
        this._autoDetectSection();
    }

    close() {
        if (!this.modalBackdrop) return;

        // Hide backdrop immediately
        this.modalBackdrop.classList.remove('show');
        document.body.style.overflow = '';

        // Close emoji picker if open
        if (this.emojiPicker && this.emojiPicker.isOpen()) this.emojiPicker.close();

        // Reset panels directly (no helper that might have side effects)
        if (this.successPanel) this.successPanel.setAttribute('hidden', '');
        if (this.myPanel) this.myPanel.style.display = 'none';
        if (this.formPanel) this.formPanel.style.display = '';
        if (this.footerEl) this.footerEl.style.display = '';
        this.myPanelVisible = false;
        this._setSubmitting(false);
    }

    /** Make sure form is shown (hide success, hide my-panel, show footer). */
    _ensureFormVisible() {
        if (this.successPanel) this.successPanel.setAttribute('hidden', '');
        if (this.formPanel) this.formPanel.style.display = '';
        if (this.footerEl) this.footerEl.style.display = '';
        if (this.myPanel) this.myPanel.style.display = 'none';
        this.myPanelVisible = false;
        this._setSubmitting(false);
    }

    /* ============================================================
     * Auto-detect section
     * ============================================================ */
    _autoDetectSection() {
        const section = guessSectionFromPath();
        if (section && this.sectionInput) {
            this.sectionInput.value = section;
            this.sectionInput.style.color = 'var(--success-color)';
            this.sectionInput.style.fontWeight = '550';
        }
    }

    /* ============================================================
     * Tab switching
     * ============================================================ */
    _switchTab(type) {
        const nextType = TYPE_CONFIG[type] ? type : 'bug';
        const config = TYPE_CONFIG[nextType];
        this.currentType = nextType;

        this._applyTypeAccent(nextType);

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

        if (this.sectionGroup) this.sectionGroup.style.display = config.showSection ? '' : 'none';
        if (this.titleInput) this.titleInput.placeholder = config.titlePlaceholder;
        if (this.descTextarea) this.descTextarea.placeholder = config.descriptionPlaceholder;
        if (this.emojiToggle) this.emojiToggle.style.visibility = config.showEmoji ? '' : 'hidden';
    }

    /* ============================================================
     * Emoji picker
     * ============================================================ */
    _toggleEmojiPicker() {
        if (!this.emojiPicker) {
            this.emojiPicker = createEmojiPicker({ targetInput: this.descTextarea });
            const wrapper = document.getElementById('feedback-emoji-picker-wrap');
            if (wrapper) wrapper.appendChild(this.emojiPicker.element);
        }
        this.emojiPicker.toggle();
    }

    /* ============================================================
     * Attachments
     * ============================================================ */
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

        if (added > 0) this._renderAttachments();
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

    /* ============================================================
     * Submit
     * ============================================================ */
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

            // Show success — hide form, show success panel
            if (this.formPanel) this.formPanel.style.display = 'none';
            if (this.successPanel) this.successPanel.removeAttribute('hidden');
            if (this.footerEl) this.footerEl.style.display = 'none';
            if (this.myPanel) this.myPanel.style.display = 'none';
            this.myPanelVisible = false;

            if (this.successMessage) {
                if (failedUploads > 0) {
                    this.successMessage.textContent =
                        `反馈已提交；${failedUploads} 张截图上传失败，可稍后重新提交截图说明。`;
                } else {
                    this.successMessage.textContent =
                        '您的反馈已成功提交，我们会尽快处理。每一份意见都让平台变得更好。';
                }
            }

            if (failedUploads > 0) {
                showToast(`反馈已提交，但 ${failedUploads} 张截图上传失败`, 'warning', 4500);
            } else {
                showToast(result.message || '反馈提交成功！', 'success');
            }

            this._setSubmitting(false);
            this.submitting = false;
            // DO NOT auto-reset — user controls when to submit again
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

    /* ============================================================
     * "Submit another" button
     * ============================================================ */
    _submitAnother() {
        // Reset form and show it again
        this._resetForm();
        if (this.successPanel) this.successPanel.setAttribute('hidden', '');
        if (this.formPanel) this.formPanel.style.display = '';
        if (this.footerEl) this.footerEl.style.display = '';

        // Auto-detect section for new form
        this._autoDetectSection();
    }

    _resetForm() {
        this.submitting = false;
        this.feedbackId = null;
        this.attachments.forEach((att) => {
            if (att.preview_url) URL.revokeObjectURL(att.preview_url);
        });
        this.attachments = [];
        this._setSubmitting(false);

        if (this.feedbackForm) this.feedbackForm.reset();
        if (this.attachmentsList) this.attachmentsList.innerHTML = '';
        if (this.attachmentInput) this.attachmentInput.disabled = false;

        // Keep current tab — user may want to submit another of the same type
        this._autoDetectSection();
    }

    /* ============================================================
     * My Feedback panel
     * ============================================================ */
    async _openMyFeedback() {
        if (this.myPanelVisible) return;

        // Hide form/success, show my-panel
        if (this.formPanel) this.formPanel.style.display = 'none';
        if (this.successPanel) this.successPanel.setAttribute('hidden', '');
        if (this.footerEl) this.footerEl.style.display = 'none';
        if (this.myPanel) this.myPanel.style.display = '';
        this.myPanelVisible = true;

        // Show loading
        if (this.myContent) {
            this.myContent.innerHTML = '<div class="fb-my-spinner"><div class="spinner"></div></div>';
        }

        try {
            const data = await API.get('/api/feedback/my');
            this.myFeedbackData = data.items || [];
            this._renderMyFeedback();
        } catch (err) {
            console.error('Failed to load my feedback:', err);
            if (this.myContent) {
                this.myContent.innerHTML =
                    '<div class="fb-my-empty"><p>加载失败，请稍后重试</p></div>';
            }
        }
    }

    _closeMyFeedback() {
        this.myPanelVisible = false;
        if (this.myPanel) this.myPanel.style.display = 'none';
        if (this.formPanel) this.formPanel.style.display = '';
        if (this.footerEl) this.footerEl.style.display = '';
    }

    _renderMyFeedback() {
        if (!this.myContent) return;

        const items = this.myFeedbackData || [];

        if (items.length === 0) {
            this.myContent.innerHTML = `
                <div class="fb-my-empty">
                    <svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
                        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
                        <polyline points="14 2 14 8 20 8"></polyline>
                    </svg>
                    <p>尚未提交反馈</p>
                    <p class="fb-my-empty-sub">提交 Bug 反馈、新功能建议或举报内容，帮助平台变得更好</p>
                </div>`;
            return;
        }

        let html = '<div class="fb-my-list">';
        items.forEach((item) => {
            const typeLabel = TYPE_LABEL_MAP[item.feedback_type] || item.feedback_type;
            const isViewed = item.status === 'viewed';
            const statusLabel = isViewed ? '已查看' : '待处理';
            const statusCls = isViewed ? 's-viewed' : 's-pending';
            const timeStr = this._formatTime(item.created_at);

            html += `
            <div class="fb-my-card" id="fb-card-${item.id}">
                <div class="fb-my-card-summary" onclick="window.__fbModal._toggleCard(${item.id})">
                    <span class="fb-my-card-type-badge t-${item.feedback_type}">${typeLabel}</span>
                    <span class="fb-my-card-title">${this._escapeHtml(item.title)}</span>
                    <span class="fb-my-card-meta">
                        <span class="fb-my-card-status ${statusCls}">${statusLabel}</span>
                        <span>${timeStr}</span>
                    </span>
                    <svg class="fb-my-card-chevron" xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <polyline points="6 9 12 15 18 9"></polyline>
                    </svg>
                </div>
                <div class="fb-my-card-detail">
                    <div class="fb-my-card-detail-meta">
                        <span>${item.section ? '板块: ' + this._escapeHtml(item.section) : ''}</span>
                        <span>${item.attachment_count > 0 ? '附件: ' + item.attachment_count + ' 张' : ''}</span>
                    </div>
                    <div class="fb-my-card-desc">${this._escapeHtml(item.description)}</div>
                    <div id="fb-card-att-${item.id}" class="fb-my-card-attachments"></div>
                    <button type="button" class="fb-withdraw-btn" onclick="event.stopPropagation(); window.__fbModal._withdrawFeedback(${item.id})">
                        <svg xmlns="http://www.w3.org/2000/svg" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                            <polyline points="3 6 5 6 21 6"></polyline>
                            <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
                        </svg>
                        撤回此反馈
                    </button>
                </div>
            </div>`;
        });
        html += '</div>';
        this.myContent.innerHTML = html;
    }

    async _toggleCard(feedbackId) {
        const card = document.getElementById(`fb-card-${feedbackId}`);
        if (!card) return;

        const isExpanded = card.classList.contains('is-expanded');

        if (isExpanded) {
            card.classList.remove('is-expanded');
            return;
        }

        // Load detail (attachments) if not already loaded
        const attContainer = document.getElementById(`fb-card-att-${feedbackId}`);
        if (attContainer && attContainer.children.length === 0 && !attContainer.dataset.loaded) {
            attContainer.dataset.loaded = '1';
            try {
                const data = await API.get(`/api/feedback/${feedbackId}/detail`);
                if (data.attachments && data.attachments.length > 0) {
                    attContainer.innerHTML = data.attachments.map(a =>
                        `<img class="fb-my-card-att-thumb"
                             src="/api/feedback/${feedbackId}/attachment/${a.file_hash}"
                             alt="${this._escapeHtml(a.original_filename)}"
                             loading="lazy"
                             onclick="event.stopPropagation(); window.open('/api/feedback/${feedbackId}/attachment/${a.file_hash}')"
                             title="${this._escapeHtml(a.original_filename)}">`
                    ).join('');
                }
            } catch (err) {
                console.error('Failed to load feedback detail:', err);
                attContainer.innerHTML = '';
            }
        }

        card.classList.add('is-expanded');
    }

    async _withdrawFeedback(feedbackId) {
        if (!confirm('确定要撤回此反馈吗？撤回后无法恢复。')) return;

        try {
            await API.delete(`/api/feedback/${feedbackId}`);
            showToast('反馈已撤回', 'success');

            // Remove from local data and re-render
            this.myFeedbackData = (this.myFeedbackData || []).filter(item => item.id !== feedbackId);
            this._renderMyFeedback();
        } catch (err) {
            showToast(`撤回失败: ${err.message}`, 'error');
        }
    }

    /* ============================================================
     * Helpers
     * ============================================================ */
    _escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text || '';
        return div.innerHTML;
    }

    _formatTime(isoString) {
        if (!isoString) return '';
        try {
            const d = new Date(isoString);
            const now = new Date();
            const diffMs = now - d;
            const diffMin = Math.floor(diffMs / 60000);
            if (diffMin < 1) return '刚刚';
            if (diffMin < 60) return `${diffMin} 分钟前`;
            const diffHours = Math.floor(diffMin / 60);
            if (diffHours < 24) return `${diffHours} 小时前`;
            const diffDays = Math.floor(diffHours / 24);
            if (diffDays < 7) return `${diffDays} 天前`;
            return d.toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' });
        } catch {
            return isoString.slice(0, 10);
        }
    }
}

/* ============================================================
 * Auto-initialise
 * ============================================================ */
function initFeedback() {
    if (document.getElementById('feedback-modal')) {
        const modal = new FeedbackModal();
        // Expose on window so HTML onclick handlers can call methods
        window.__fbModal = modal;
    }
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initFeedback);
} else {
    initFeedback();
}
