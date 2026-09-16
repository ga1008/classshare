/**
 * feedback.js
 * Feedback modal with type-accent colours, proper submit flow,
 * and a "my feedback" panel for viewing / withdrawing past feedback.
 */
import { API, apiFetch } from './api.js';
import { showToast } from './ui.js';
import { createEmojiPicker } from './emoji_picker.js';
import { FeedbackConversation, FEEDBACK_TYPES, feedbackStatus, feedbackTime, node, button } from './feedback_conversation.js';

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
        this.closeTimer = null;
        this.conversations = new Map();
        this.myCards = new Map();

        this._init();
    }

    /* ============================================================
     * Initialisation
     * ============================================================ */
    _init() {
        this.modalBackdrop.classList.remove('show');
        this.modalBackdrop.hidden = true;
        this.modalBackdrop.setAttribute('aria-hidden', 'true');
        this.modalBackdrop.style.display = '';

        this._bindEvents();
        this._autoDetectSection();
        this._applyTypeAccent('bug');
        const linkedId = Number(new URLSearchParams(location.search).get('feedback_id'));
        if (Number.isSafeInteger(linkedId) && linkedId > 0 && !document.querySelector('[data-feedback-admin]')) {
            this.open();
            this._openMyFeedback(linkedId);
        }
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
        const closeBtn = this.modalBackdrop.querySelector('[data-feedback-dismiss]');
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

        document.getElementById('fb-open-submitted-btn')?.addEventListener('click', () => this._openMyFeedback(this.feedbackId));

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

        if (this.closeTimer) {
            clearTimeout(this.closeTimer);
            this.closeTimer = null;
        }

        // Show backdrop FIRST so it is always visible regardless of
        // any subsequent state manipulation.
        this.modalBackdrop.hidden = false;
        this.modalBackdrop.setAttribute('aria-hidden', 'false');
        this.modalBackdrop.style.display = '';
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
        this.modalBackdrop.setAttribute('aria-hidden', 'true');
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

        if (this.closeTimer) clearTimeout(this.closeTimer);
        this.closeTimer = window.setTimeout(() => {
            if (!this.modalBackdrop.classList.contains('show')) {
                this.modalBackdrop.hidden = true;
                this.modalBackdrop.style.display = '';
            }
            this.closeTimer = null;
        }, 280);
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
                        `反馈已提交；${failedUploads} 张截图上传失败。您可在“我的反馈”继续补充文字说明。`;
                } else {
                    this.successMessage.textContent =
                        '反馈已提交。您可以在“我的反馈”继续补充、查看超管回复，处理完成后由超管关闭。';
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
    async _openMyFeedback(linkedId = null) {
        if (this.submitting) return;
        if (this.formPanel) this.formPanel.style.display = 'none';
        if (this.successPanel) this.successPanel.setAttribute('hidden', '');
        if (this.footerEl) this.footerEl.style.display = 'none';
        if (this.myPanel) this.myPanel.style.display = 'block';
        this.myPanelVisible = true;
        await this._loadMyFeedback(linkedId);
    }

    async _loadMyFeedback(linkedId = null, append = false) {
        if (this.myLoading) return;
        this.myLoading = true;
        this.conversations.forEach((conversation) => conversation.destroy());
        this.conversations.clear();
        this.myContent.replaceChildren(node('p', 'fb-my-empty', '正在加载反馈…'));
        try {
            const params = new URLSearchParams({ limit: '60' });
            if (append && this.myNextBeforeId) params.set('before_id', this.myNextBeforeId);
            const data = await apiFetch(`/api/feedback/my?${params}`, { silent: true });
            const items = append ? [...(this.myFeedbackData || []), ...(data.items || [])] : data.items || [];
            this.myFeedbackData = [...new Map(items.map((item) => [item.id, item])).values()];
            this.myHasMore = Boolean(data.has_more);
            this.myNextBeforeId = data.next_before_id;
            if (linkedId && !this.myFeedbackData.some((item) => Number(item.id) === Number(linkedId))) {
                const detail = await apiFetch(`/api/feedback/${Number(linkedId)}/detail`, { silent: true });
                this.myFeedbackData.unshift(detail.feedback);
            }
            this._renderMyFeedback();
            if (linkedId) this._toggleCard(Number(linkedId));
        } catch (error) {
            this.myContent.replaceChildren(node('p', 'fb-my-empty', `加载失败：${error.message}`), button('重试', () => this._loadMyFeedback(linkedId, append)));
        } finally { this.myLoading = false; }
    }

    _closeMyFeedback() {
        this.myPanelVisible = false;
        if (this.myPanel) this.myPanel.style.display = 'none';
        if (this.formPanel) this.formPanel.style.display = '';
        if (this.footerEl) this.footerEl.style.display = '';
    }

    _renderMyFeedback() {
        if (!this.myContent) return;
        this.myCards.clear();
        this.myContent.replaceChildren();
        const toolbar = node('div', 'fb-my-refresh');
        toolbar.append(node('span', '', '在这里查看回复、继续沟通；由超管决定关闭时间。'), button('刷新列表', () => this._loadMyFeedback()));
        this.myContent.append(toolbar);
        const items = this.myFeedbackData || [];
        if (!items.length) {
            this.myContent.append(node('p', 'fb-my-empty', '尚未提交反馈。提交后可在这里与超管沟通。'));
            return;
        }
        const list = node('div', 'fb-my-list');
        for (const item of items) {
            const card = node('article', 'fb-my-card');
            card.id = `fb-card-${item.id}`;
            const toggle = button('', () => this._toggleCard(item.id), 'fb-my-card-summary');
            toggle.setAttribute('aria-expanded', 'false');
            const type = ['bug', 'feature', 'report'].includes(item.feedback_type) ? item.feedback_type : 'bug';
            toggle.append(node('span', `fb-my-card-type-badge t-${type}`, FEEDBACK_TYPES[type]), node('span', 'fb-my-card-title', item.title));
            const meta = node('span', 'fb-my-card-meta');
            const status = node('span', 'fb-thread-status', feedbackStatus(item));
            const unread = node('span', 'fb-thread-unread', `${item.unread_count || 0} 条未读`);
            unread.hidden = !item.unread_count;
            meta.append(status, unread, node('span', '', feedbackTime(item.created_at)));
            toggle.append(meta);
            const body = node('div', 'fb-my-card-detail');
            body.id = `fb-card-detail-${item.id}`;
            toggle.setAttribute('aria-controls', body.id);
            card.append(toggle, body);
            list.append(card);
            this.myCards.set(Number(item.id), { card, toggle, status, unread, body });
        }
        this.myContent.append(list);
        if (this.myHasMore) this.myContent.append(button('加载更多反馈', () => this._loadMyFeedback(null, true), 'fb-thread-button fb-admin-more'));
    }

    _toggleCard(feedbackId) {
        feedbackId = Number(feedbackId);
        const target = this.myCards.get(feedbackId);
        if (!target) return;
        const opening = !target.card.classList.contains('is-expanded');
        target.card.classList.toggle('is-expanded', opening);
        target.toggle.setAttribute('aria-expanded', String(opening));
        if (!opening) return;
        if (this.conversations.has(feedbackId)) { this.conversations.get(feedbackId).load(); return; }
        this.conversations.set(feedbackId, new FeedbackConversation(target.body, feedbackId, {
            onChange: (item) => {
                target.status.textContent = feedbackStatus(item);
                target.status.classList.toggle('is-closed', item.status === 'closed');
                target.unread.textContent = `${item.unread_count || 0} 条未读`;
                target.unread.hidden = !item.unread_count;
            },
            onWithdraw: (id) => this._withdrawFeedback(id),
        }));
    }

    async _withdrawFeedback(feedbackId) {
        if (this.withdrawing) return;
        if (!confirm('确定撤回这条尚未沟通的反馈吗？撤回后无法恢复。')) return;
        this.withdrawing = true;

        try {
            await API.delete(`/api/feedback/${feedbackId}`);
            showToast('反馈已撤回', 'success');

            // Remove from local data and re-render
            this.myFeedbackData = (this.myFeedbackData || []).filter(item => item.id !== feedbackId);
            this.conversations.get(feedbackId)?.destroy();
            this.conversations.delete(feedbackId);
            await this._loadMyFeedback();
        } catch (err) {
            showToast(`撤回失败: ${err.message}`, 'error');
            this.conversations.get(feedbackId)?.load();
        } finally { this.withdrawing = false; }
    }

}

/* ============================================================
 * Auto-initialise
 * ============================================================ */
function initFeedback() {
    if (document.getElementById('feedback-modal')) {
        const modal = new FeedbackModal();
        // Retain the existing integration hook used by other page scripts.
        window.__fbModal = modal;
    }
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initFeedback);
} else {
    initFeedback();
}
