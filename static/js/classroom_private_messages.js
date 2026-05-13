import { apiFetch } from './api.js';
import { escapeHtml, formatDate, showToast } from './ui.js';

const PRIVATE_ATTACHMENT_MAX_BYTES = 100 * 1024 * 1024;
const PRIVATE_ATTACHMENT_LIMIT = 8;
const PRIVATE_REFRESH_MS = 12000;
const PRIVATE_IMAGE_TYPES = new Set(['image/png', 'image/jpeg', 'image/gif', 'image/webp']);

function normalizeScope(value) {
    if (value === '' || value == null) {
        return null;
    }
    const numericValue = Number(value);
    return Number.isFinite(numericValue) ? numericValue : null;
}

function formatBytes(size) {
    const value = Number(size || 0);
    if (!Number.isFinite(value) || value <= 0) {
        return '';
    }
    if (value < 1024) {
        return `${value} B`;
    }
    if (value < 1024 * 1024) {
        return `${(value / 1024).toFixed(1).replace(/\.0$/, '')} KB`;
    }
    return `${(value / (1024 * 1024)).toFixed(1).replace(/\.0$/, '')} MB`;
}

function isImageFile(file) {
    const type = String(file?.type || '').toLowerCase();
    const name = String(file?.name || '').toLowerCase();
    return PRIVATE_IMAGE_TYPES.has(type) || /\.(png|jpe?g|gif|webp)$/.test(name);
}

function contactKey(contact) {
    return `${contact?.identity || ''}|scope:${Number(contact?.class_offering_id || 0)}`;
}

function normalizeSearchText(value) {
    return String(value || '').toLowerCase().replace(/\s+/g, '');
}

function contactSearchText(contact) {
    return normalizeSearchText([
        contact?.display_name,
        contact?.subtitle,
        contact?.identity,
        contact?.user_pk,
        contactKey(contact),
    ].filter(Boolean).join(' '));
}

export class ClassroomPrivateMessages {
    constructor(options) {
        this.classOfferingId = Number(options.classOfferingId);
        this.root = document.getElementById(options.rootId);
        this.broadcastBody = document.getElementById(options.broadcastBodyId);
        this.broadcastComposer = document.getElementById(options.broadcastComposerId);
        this.privateBody = document.getElementById(options.privateBodyId);
        this.privateComposer = document.getElementById(options.privateComposerId);
        this.tabs = Array.from(this.root?.querySelectorAll(options.tabSelector) || []);
        this.contactSelect = document.getElementById(options.contactSelectId);
        this.contactInput = document.getElementById(options.contactInputId);
        this.contactList = document.getElementById(options.contactListId);
        this.contactToggle = document.getElementById(options.contactToggleId);
        this.statusEl = document.getElementById(options.statusId);
        this.conversationEl = document.getElementById(options.conversationId);
        this.form = document.getElementById(options.formId);
        this.input = document.getElementById(options.inputId);
        this.dropzone = document.getElementById(options.dropzoneId);
        this.imageButton = document.getElementById(options.imageButtonId);
        this.fileButton = document.getElementById(options.fileButtonId);
        this.imageInput = document.getElementById(options.imageInputId);
        this.fileInput = document.getElementById(options.fileInputId);
        this.previewEl = document.getElementById(options.previewId);
        this.sendButton = document.getElementById(options.sendButtonId);
        this.onModeChange = typeof options.onModeChange === 'function' ? options.onModeChange : () => {};

        this.mode = 'broadcast';
        this.contacts = [];
        this.currentContact = null;
        this.conversation = null;
        this.loadedContacts = false;
        this.loadingConversation = false;
        this.isSending = false;
        this.pendingAttachments = [];
        this.nextAttachmentId = 1;
        this.refreshTimer = null;
        this.filteredContacts = [];
        this.activeContactIndex = -1;
        this.isContactListOpen = false;
        this.attachmentLimit = PRIVATE_ATTACHMENT_LIMIT;
        this.attachmentMaxBytes = PRIVATE_ATTACHMENT_MAX_BYTES;
    }

    init() {
        if (!this.root || !this.form || !this.input || !this.contactSelect || !this.contactInput || !this.contactList || !this.conversationEl) {
            return;
        }

        this.tabs.forEach((tab) => {
            tab.addEventListener('click', () => this.setMode(tab.dataset.classroomMessageTab || 'broadcast'));
        });
        this.contactSelect.addEventListener('change', () => {
            const selected = this.contactSelect.selectedOptions[0];
            if (!selected?.dataset.identity) {
                this.currentContact = null;
                this.renderConversationEmpty('请选择同学', '打开一个本班同学后即可发送一对一消息。');
                this.updateControls();
                return;
            }
            this.clearPendingAttachments();
            this.loadConversation({
                identity: selected.dataset.identity,
                class_offering_id: normalizeScope(selected.dataset.scope),
            });
        });
        this.contactInput.addEventListener('input', () => {
            this.openContactList({ preserveActive: false });
            this.renderContactList(this.contactInput.value);
        });
        this.contactInput.addEventListener('focus', () => {
            this.openContactList({ preserveActive: true });
        });
        this.contactInput.addEventListener('keydown', (event) => this.handleContactInputKeydown(event));
        this.contactToggle?.addEventListener('click', () => {
            if (this.isContactListOpen) {
                this.closeContactList();
            } else {
                this.openContactList({ preserveActive: true });
                this.contactInput.focus({ preventScroll: true });
            }
        });
        this.contactList.addEventListener('mousedown', (event) => event.preventDefault());
        this.contactList.addEventListener('click', (event) => {
            const option = event.target.closest('[data-contact-key]');
            if (!option) {
                return;
            }
            const contact = this.contacts.find((item) => contactKey(item) === option.dataset.contactKey);
            this.selectContact(contact);
        });
        document.addEventListener('click', (event) => {
            if (!event.target.closest('#classroom-private-contact-bar')) {
                this.closeContactList();
            }
        });
        this.imageButton?.addEventListener('click', () => this.imageInput?.click());
        this.fileButton?.addEventListener('click', () => this.fileInput?.click());
        this.imageInput?.addEventListener('change', (event) => {
            this.queueAttachments(event.currentTarget?.files || [], { imagesOnly: true });
            if (event.currentTarget) {
                event.currentTarget.value = '';
            }
        });
        this.fileInput?.addEventListener('change', (event) => {
            this.queueAttachments(event.currentTarget?.files || []);
            if (event.currentTarget) {
                event.currentTarget.value = '';
            }
        });
        this.input.addEventListener('paste', (event) => {
            const files = Array.from(event.clipboardData?.files || []);
            if (files.length) {
                this.queueAttachments(files);
            }
        });
        this.input.addEventListener('input', () => {
            this.resizeInput();
            this.updateControls();
        });
        this.dropzone?.addEventListener('dragover', (event) => {
            if (Array.from(event.dataTransfer?.types || []).includes('Files')) {
                event.preventDefault();
                this.dropzone.classList.add('is-dragover');
            }
        });
        this.dropzone?.addEventListener('dragleave', (event) => {
            if (!this.dropzone.contains(event.relatedTarget)) {
                this.dropzone.classList.remove('is-dragover');
            }
        });
        this.dropzone?.addEventListener('drop', (event) => {
            const files = Array.from(event.dataTransfer?.files || []);
            if (!files.length) {
                return;
            }
            event.preventDefault();
            this.dropzone.classList.remove('is-dragover');
            this.queueAttachments(files);
        });
        this.form.addEventListener('submit', (event) => {
            event.preventDefault();
            this.sendMessage();
        });
        this.input.addEventListener('keydown', (event) => {
            if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault();
                this.sendMessage();
            }
        });
        window.addEventListener('beforeunload', () => this.clearPendingAttachments());
        this.applyModeState('broadcast');
        this.updateControls();
    }

    setMode(mode) {
        const nextMode = mode === 'private' ? 'private' : 'broadcast';
        this.applyModeState(nextMode);
        this.onModeChange();
        window.setTimeout(this.onModeChange, 230);

        if (nextMode === 'private') {
            this.loadContacts({ silent: this.loadedContacts });
            this.startRefresh();
            this.contactInput?.focus({ preventScroll: true });
        } else {
            this.stopRefresh();
            this.closeContactList();
        }
    }

    applyModeState(mode) {
        const nextMode = mode === 'private' ? 'private' : 'broadcast';
        const isPrivate = nextMode === 'private';
        this.mode = nextMode;
        this.root.classList.toggle('is-private-mode', isPrivate);
        this.root.classList.toggle('is-broadcast-mode', !isPrivate);
        this.setPanelActive(this.broadcastBody, !isPrivate);
        this.setPanelActive(this.broadcastComposer, !isPrivate);
        this.setPanelActive(this.privateBody, isPrivate);
        this.setPanelActive(this.privateComposer, isPrivate);
        this.tabs.forEach((tab) => {
            const active = (tab.dataset.classroomMessageTab || 'broadcast') === nextMode;
            tab.classList.toggle('is-active', active);
            tab.setAttribute('aria-selected', active ? 'true' : 'false');
            tab.tabIndex = active ? 0 : -1;
        });
    }

    setPanelActive(panel, active) {
        if (!panel) {
            return;
        }
        panel.hidden = !active;
        panel.classList.toggle('is-active', active);
        panel.setAttribute('aria-hidden', active ? 'false' : 'true');
    }

    startRefresh() {
        this.stopRefresh();
        this.refreshTimer = window.setInterval(() => {
            if (this.mode !== 'private' || this.isSending) {
                return;
            }
            this.refreshPrivateState();
        }, PRIVATE_REFRESH_MS);
    }

    stopRefresh() {
        if (this.refreshTimer) {
            window.clearInterval(this.refreshTimer);
            this.refreshTimer = null;
        }
    }

    async refreshPrivateState() {
        await this.loadContacts({ silent: true, keepConversation: true });
        if (this.currentContact) {
            await this.loadConversation(this.currentContact, { showLoading: false, scrollToBottom: false });
        }
    }

    async loadContacts({ silent = false, keepConversation = false } = {}) {
        if (!silent) {
            this.setStatus('正在同步本班同学...');
        }
        try {
            const response = await apiFetch(`/api/classrooms/${this.classOfferingId}/private/contacts`, { silent: true });
            this.contacts = Array.isArray(response.contacts) ? response.contacts : [];
            this.loadedContacts = true;
            if (response.limits) {
                this.attachmentLimit = Number(response.limits.max_attachment_count || this.attachmentLimit);
                this.attachmentMaxBytes = Number(response.limits.max_attachment_bytes || this.attachmentMaxBytes);
            }
            this.renderContacts();
            if (!this.contacts.length) {
                this.currentContact = null;
                this.renderConversationEmpty('暂无可一对一联系的同学', '本课堂没有可选的本班同学。');
                this.setStatus('没有可选同学');
                this.updateControls();
                return;
            }
            const currentKey = contactKey(this.currentContact);
            const nextContact = this.contacts.find((item) => contactKey(item) === currentKey) || this.contacts[0];
            if (!keepConversation || !this.currentContact) {
                await this.loadConversation(nextContact, { showLoading: !silent });
            }
        } catch (error) {
            this.renderConversationEmpty('一对一加载失败', error.message || '请稍后刷新重试。');
            this.setStatus('加载失败');
        }
    }

    renderContacts() {
        const selectedKey = contactKey(this.currentContact);
        this.contactSelect.innerHTML = this.contacts.length
            ? this.contacts.map((contact) => {
                const unread = Number(contact.unread_count || 0);
                const label = `${contact.display_name || '同学'}${unread ? `（${unread}）` : ''}`;
                return `
                    <option
                        value="${escapeHtml(contactKey(contact))}"
                        data-identity="${escapeHtml(contact.identity)}"
                        data-scope="${contact.class_offering_id == null ? '' : Number(contact.class_offering_id)}"
                        ${contactKey(contact) === selectedKey ? 'selected' : ''}
                    >${escapeHtml(label)}</option>
                `;
            }).join('')
            : '<option value="">暂无本班同学</option>';
        this.contactSelect.value = selectedKey || '';
        if (this.currentContact?.identity) {
            this.contactInput.value = this.currentContact.display_name || '';
        } else if (!this.isContactListOpen) {
            this.contactInput.value = '';
        }
        this.renderContactList(this.contactInput.value);
    }

    renderContactList(query = '') {
        const normalizedQuery = normalizeSearchText(query);
        const selectedKey = contactKey(this.currentContact);
        this.filteredContacts = this.contacts.filter((contact) => {
            if (!normalizedQuery) {
                return true;
            }
            return contactSearchText(contact).includes(normalizedQuery);
        });
        if (this.activeContactIndex >= this.filteredContacts.length) {
            this.activeContactIndex = this.filteredContacts.length ? 0 : -1;
        }
        if (this.activeContactIndex < 0 && this.filteredContacts.length) {
            this.activeContactIndex = 0;
        }

        if (!this.filteredContacts.length) {
            this.contactList.innerHTML = `
                <div class="classroom-private-contact-empty" role="option" aria-disabled="true">
                    没有匹配的同学
                </div>
            `;
            this.contactList.hidden = !this.isContactListOpen;
            this.contactInput.setAttribute('aria-expanded', this.isContactListOpen ? 'true' : 'false');
            this.contactInput.removeAttribute('aria-activedescendant');
            return;
        }

        this.contactList.innerHTML = this.filteredContacts.map((contact, index) => {
            const key = contactKey(contact);
            const unread = Number(contact.unread_count || 0);
            const active = index === this.activeContactIndex;
            const selected = key === selectedKey;
            const optionId = `classroom-private-contact-option-${index}`;
            return `
                <button
                    type="button"
                    class="classroom-private-contact-option${active ? ' is-active' : ''}${selected ? ' is-selected' : ''}"
                    id="${optionId}"
                    role="option"
                    aria-selected="${selected ? 'true' : 'false'}"
                    data-contact-key="${escapeHtml(key)}"
                >
                    <span class="classroom-private-contact-option-main">
                        <strong>${escapeHtml(contact.display_name || '同学')}</strong>
                        ${unread ? `<em>${unread}</em>` : ''}
                    </span>
                    <small>${escapeHtml(contact.subtitle || '本班同学')}</small>
                </button>
            `;
        }).join('');
        this.contactList.hidden = !this.isContactListOpen;
        this.contactInput.setAttribute('aria-expanded', this.isContactListOpen ? 'true' : 'false');
        const activeOption = this.contactList.querySelector('.classroom-private-contact-option.is-active');
        if (activeOption) {
            this.contactInput.setAttribute('aria-activedescendant', activeOption.id);
        }
    }

    openContactList({ preserveActive = false } = {}) {
        this.isContactListOpen = true;
        if (!preserveActive) {
            this.activeContactIndex = 0;
        }
        this.renderContactList(this.contactInput.value);
    }

    closeContactList() {
        this.isContactListOpen = false;
        this.contactList.hidden = true;
        this.contactInput?.setAttribute('aria-expanded', 'false');
        this.contactInput?.removeAttribute('aria-activedescendant');
    }

    handleContactInputKeydown(event) {
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault();
            if (!this.isContactListOpen) {
                this.openContactList({ preserveActive: true });
                return;
            }
            const direction = event.key === 'ArrowDown' ? 1 : -1;
            const total = this.filteredContacts.length;
            if (!total) {
                return;
            }
            this.activeContactIndex = (this.activeContactIndex + direction + total) % total;
            this.renderContactList(this.contactInput.value);
            this.contactList.querySelector('.classroom-private-contact-option.is-active')?.scrollIntoView({ block: 'nearest' });
            return;
        }
        if (event.key === 'Enter' && this.isContactListOpen) {
            const contact = this.filteredContacts[this.activeContactIndex] || this.filteredContacts[0];
            if (contact) {
                event.preventDefault();
                this.selectContact(contact);
            }
            return;
        }
        if (event.key === 'Escape') {
            this.closeContactList();
            if (this.currentContact?.display_name) {
                this.contactInput.value = this.currentContact.display_name;
            }
        }
    }

    selectContact(contact) {
        if (!contact?.identity) {
            return;
        }
        this.contactInput.value = contact.display_name || '';
        this.contactSelect.value = contactKey(contact);
        this.closeContactList();
        this.clearPendingAttachments();
        this.loadConversation({
            identity: contact.identity,
            class_offering_id: normalizeScope(contact.class_offering_id),
        });
    }

    async loadConversation(contact, { showLoading = true, scrollToBottom = true } = {}) {
        if (!contact?.identity || this.loadingConversation) {
            return;
        }
        this.currentContact = {
            ...contact,
            class_offering_id: normalizeScope(contact.class_offering_id) ?? this.classOfferingId,
        };
        this.renderContacts();
        this.updateControls();
        if (showLoading) {
            this.setConversationLoading('正在加载一对一消息...');
        }
        this.loadingConversation = true;
        try {
            const params = new URLSearchParams({
                contact: this.currentContact.identity,
                scope: String(this.currentContact.class_offering_id || this.classOfferingId),
                limit: '120',
            });
            const response = await apiFetch(`/api/message-center/private/conversation?${params.toString()}`, { silent: true });
            this.conversation = response.conversation || null;
            if (this.conversation?.contact) {
                this.currentContact = this.conversation.contact;
            }
            this.renderContacts();
            this.renderConversation();
            if (scrollToBottom) {
                this.scrollToBottom();
            }
            this.setStatus(this.currentContact?.display_name ? `正在一对一联系 ${this.currentContact.display_name}` : '一对一已打开');
        } catch (error) {
            this.renderConversationEmpty('会话加载失败', error.message || '请稍后重试。');
            this.setStatus('会话加载失败');
        } finally {
            this.loadingConversation = false;
            this.updateControls();
        }
    }

    renderConversation() {
        const messages = Array.isArray(this.conversation?.messages) ? this.conversation.messages : [];
        if (!this.currentContact) {
            this.renderConversationEmpty('选择同学开始一对一', '这里的内容只会进入你和对方的一对一会话。');
            return;
        }
        if (!messages.length) {
            this.renderConversationEmpty(`还没有和 ${this.currentContact.display_name || '这位同学'} 的一对一消息`, '可以发送文字、图片或文件，附件在点击发送前只保留在本机预览。');
            return;
        }
        this.conversationEl.innerHTML = `
            <div class="classroom-private-message-list">
                ${messages.map((message) => this.renderMessage(message)).join('')}
            </div>
        `;
    }

    renderMessage(message) {
        const content = String(message.content || '');
        const attachments = this.renderMessageAttachments(message.attachments);
        const articleClass = `classroom-private-message${message.is_outgoing ? ' is-outgoing' : ''}`;
        return `
            <article class="${articleClass}">
                <div class="classroom-private-message__meta">
                    <strong>${escapeHtml(message.sender_display_name || '')}</strong>
                    <span>${escapeHtml(formatDate(message.created_at || ''))}</span>
                </div>
                ${content ? `<div class="classroom-private-message__content">${escapeHtml(content)}</div>` : ''}
                ${attachments}
            </article>
        `;
    }

    renderMessageAttachments(attachments) {
        const items = Array.isArray(attachments) ? attachments : [];
        if (!items.length) {
            return '';
        }
        return `
            <div class="classroom-private-message__attachments">
                ${items.map((attachment) => {
                    const name = escapeHtml(attachment.name || '附件');
                    const size = escapeHtml(formatBytes(attachment.file_size));
                    if (attachment.is_image || attachment.type === 'image') {
                        return `
                            <a class="classroom-private-attachment is-image" href="${escapeHtml(attachment.url || '#')}" target="_blank" rel="noreferrer noopener">
                                <img src="${escapeHtml(attachment.url || '')}" alt="${name}" loading="lazy" decoding="async">
                                <span>${name}${size ? ` · ${size}` : ''}</span>
                            </a>
                        `;
                    }
                    return `
                        <a class="classroom-private-attachment is-file" href="${escapeHtml(attachment.download_url || attachment.url || '#')}" target="_blank" rel="noreferrer noopener">
                            <span class="classroom-private-file-icon" aria-hidden="true">📎</span>
                            <span>${name}${size ? `<small>${size}</small>` : ''}</span>
                        </a>
                    `;
                }).join('')}
            </div>
        `;
    }

    renderConversationEmpty(title, text) {
        this.conversationEl.innerHTML = `
            <div class="chat-empty-state classroom-private-empty">
                <div class="chat-empty-icon">
                    <svg xmlns="http://www.w3.org/2000/svg" width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a4 4 0 0 1-4 4H8l-5 3V7a4 4 0 0 1 4-4h10a4 4 0 0 1 4 4Z"/><path d="M8 10h8"/><path d="M8 14h5"/></svg>
                </div>
                <strong>${escapeHtml(title)}</strong>
                <p>${escapeHtml(text)}</p>
            </div>
        `;
    }

    setConversationLoading(text) {
        this.conversationEl.innerHTML = `<div class="classroom-private-loading">${escapeHtml(text)}</div>`;
    }

    queueAttachments(files, { imagesOnly = false } = {}) {
        const selectedFiles = Array.from(files || []);
        if (!selectedFiles.length) {
            return;
        }
        if (!this.currentContact?.can_send) {
            showToast('请先选择可发送一对一消息的同学', 'warning');
            return;
        }
        const remainingSlots = this.attachmentLimit - this.pendingAttachments.length;
        if (remainingSlots <= 0) {
            showToast(`单条一对一消息最多添加 ${this.attachmentLimit} 个附件`, 'warning');
            return;
        }
        const acceptedFiles = selectedFiles.slice(0, remainingSlots);
        if (acceptedFiles.length < selectedFiles.length) {
            showToast(`超出部分已忽略，单条一对一消息最多 ${this.attachmentLimit} 个附件`, 'warning');
        }
        acceptedFiles.forEach((file) => {
            if (imagesOnly && !isImageFile(file)) {
                showToast('图片入口仅支持 PNG、JPG、GIF 或 WebP', 'warning');
                return;
            }
            if (Number(file.size || 0) > this.attachmentMaxBytes) {
                showToast(`${file.name || '附件'} 超过 100MB，已忽略`, 'warning');
                return;
            }
            const isImage = isImageFile(file);
            this.pendingAttachments.push({
                id: this.nextAttachmentId++,
                file,
                isImage,
                previewUrl: isImage ? URL.createObjectURL(file) : '',
            });
        });
        this.renderPendingAttachments();
    }

    renderPendingAttachments() {
        if (!this.previewEl) {
            return;
        }
        this.previewEl.replaceChildren();
        this.previewEl.hidden = this.pendingAttachments.length === 0;
        if (!this.pendingAttachments.length) {
            this.updateControls();
            return;
        }
        const fragment = document.createDocumentFragment();
        this.pendingAttachments.forEach((attachment) => {
            const card = document.createElement('div');
            card.className = `classroom-private-preview-card${attachment.isImage ? ' is-image' : ''}`;
            if (attachment.isImage && attachment.previewUrl) {
                const image = document.createElement('img');
                image.src = attachment.previewUrl;
                image.alt = attachment.file.name || '待发送图片';
                image.loading = 'lazy';
                card.appendChild(image);
            } else {
                const icon = document.createElement('span');
                icon.className = 'classroom-private-preview-icon';
                icon.textContent = '📎';
                icon.setAttribute('aria-hidden', 'true');
                card.appendChild(icon);
            }
            const meta = document.createElement('span');
            meta.className = 'classroom-private-preview-meta';
            meta.innerHTML = `
                <strong>${escapeHtml(attachment.file.name || '附件')}</strong>
                <small>${escapeHtml(formatBytes(attachment.file.size) || '待发送')}</small>
            `;
            card.appendChild(meta);
            const removeButton = document.createElement('button');
            removeButton.type = 'button';
            removeButton.textContent = '×';
            removeButton.title = '移除附件';
            removeButton.setAttribute('aria-label', `移除 ${attachment.file.name || '附件'}`);
            removeButton.addEventListener('click', () => {
                this.pendingAttachments = this.pendingAttachments.filter((item) => item.id !== attachment.id);
                this.revokeAttachmentPreview(attachment);
                this.renderPendingAttachments();
            });
            card.appendChild(removeButton);
            fragment.appendChild(card);
        });
        this.previewEl.appendChild(fragment);
        this.updateControls();
        this.onModeChange();
    }

    async sendMessage() {
        if (this.mode !== 'private' || this.isSending) {
            return;
        }
        if (!this.currentContact?.identity) {
            showToast('请先选择一对一同学', 'warning');
            return;
        }
        const content = this.input.value.trim();
        if (!content && !this.pendingAttachments.length) {
            showToast('请输入一对一内容或添加附件', 'warning');
            return;
        }

        const formData = new FormData();
        formData.append('contact_identity', this.currentContact.identity);
        formData.append('class_offering_id', String(this.currentContact.class_offering_id || this.classOfferingId));
        formData.append('content', content);
        this.pendingAttachments.forEach((attachment) => {
            formData.append('attachments', attachment.file, attachment.file.name || 'attachment');
        });

        this.isSending = true;
        this.updateControls();
        try {
            const response = await apiFetch('/api/message-center/private/messages', {
                method: 'POST',
                body: formData,
                silent: true,
            });
            this.input.value = '';
            this.resizeInput();
            this.clearPendingAttachments();
            if (!this.conversation) {
                this.conversation = {
                    contact: response.contact || this.currentContact,
                    messages: [],
                };
            }
            if (response.sent_message) {
                const messages = Array.isArray(this.conversation.messages) ? [...this.conversation.messages] : [];
                messages.push(response.sent_message);
                this.conversation = { ...this.conversation, messages };
                this.renderConversation();
                this.scrollToBottom();
            }
            this.setStatus('一对一消息已发送');
            showToast('一对一消息已发送', 'success');
            window.dispatchEvent(new CustomEvent('message-center:summary-updated', { detail: response.summary || null }));
            await this.loadContacts({ silent: true, keepConversation: true });
        } catch (error) {
            showToast(error.message || '一对一发送失败', 'error');
            this.setStatus('发送失败');
        } finally {
            this.isSending = false;
            this.updateControls();
        }
    }

    updateControls() {
        const canSend = Boolean(this.currentContact?.can_send) && !this.isSending;
        [this.contactInput, this.contactToggle, this.input, this.imageButton, this.fileButton].forEach((element) => {
            if (element) {
                element.disabled = element === this.contactInput || element === this.contactToggle
                    ? !this.contacts.length
                    : !canSend;
            }
        });
        if (this.sendButton) {
            const hasDraft = Boolean(this.input?.value.trim() || this.pendingAttachments.length);
            this.sendButton.disabled = !canSend || !hasDraft;
            this.sendButton.classList.toggle('is-uploading', this.isSending);
            this.sendButton.title = this.isSending ? '正在发送一对一消息' : '发送一对一消息';
        }
        if (this.input) {
            this.input.placeholder = this.currentContact?.display_name
                ? `发送给 ${this.currentContact.display_name}，可粘贴或拖入图片/文件`
                : '选择同学后发送一对一消息，可粘贴或拖入图片/文件';
        }
    }

    setStatus(text) {
        if (this.statusEl) {
            this.statusEl.textContent = text;
        }
    }

    resizeInput() {
        if (!this.input) {
            return;
        }
        this.input.style.height = 'auto';
        this.input.style.height = `${Math.min(Math.max(this.input.scrollHeight, 72), 180)}px`;
        this.onModeChange();
    }

    scrollToBottom() {
        window.requestAnimationFrame(() => {
            this.conversationEl.scrollTop = this.conversationEl.scrollHeight;
        });
    }

    revokeAttachmentPreview(attachment) {
        if (attachment?.previewUrl) {
            URL.revokeObjectURL(attachment.previewUrl);
        }
    }

    clearPendingAttachments() {
        this.pendingAttachments.forEach((attachment) => this.revokeAttachmentPreview(attachment));
        this.pendingAttachments = [];
        this.renderPendingAttachments();
    }
}
