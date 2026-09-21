import { apiFetch } from './api.js';
import { escapeHtml, formatDate, showToast } from './ui.js';
import { createEmojiPicker } from './emoji_picker.js';
import {
    ChatImagePreviewController,
    getChatImageAttachmentDisplayMeta,
    getChatImageAttachmentOriginalUrl,
    getChatImageAttachmentPreviewUrl,
    getChatImageAttachmentThumbnailUrl,
    normalizeChatImageAttachment,
} from './chat_image_preview.js';

const app = document.querySelector('[data-message-center-app]');

if (app) {
    const lqEnabled = app.hasAttribute('data-lq-messages');
    const [lqPresentation, lqContent, lqForms] = lqEnabled
        ? await Promise.all([import('./lq/components.js'), import('./lq/content.js'), import('./lq/forms.js')])
        : [null, null, null];
    const PRIVATE_TAB = 'private_message';
    const WORKSPACE_EVENT = 'lanshare:message-center-workspace-change';
    const WORKSPACE_COMMAND_EVENT = 'lanshare:message-center-workspace-command';
    const AI_JOB_POLL_INTERVAL_MS = 2200;
    const PRIVATE_ATTACHMENT_MAX_BYTES = 100 * 1024 * 1024;
    const PRIVATE_ATTACHMENT_LIMIT = 8;
    const ACTIVE_AI_JOB_STATUSES = new Set(['pending', 'running']);
    const PRIVATE_IMAGE_TYPES = new Set(['image/png', 'image/jpeg', 'image/gif', 'image/webp']);
    const appMode = app.dataset.messageCenterMode || 'full';
    const isNotificationsMode = appMode === 'notifications';
    const isPrivateMode = appMode === 'private';

    const tabsEl = document.getElementById('message-center-tabs');
    const searchEl = document.getElementById('message-center-search');
    const filterEl = document.getElementById('message-center-filter');
    const markReadEl = document.getElementById('message-center-mark-read');
    const feedEl = document.getElementById('message-center-feed');
    const privatePanelEl = document.getElementById('message-center-private-panel');
    const contactSearchEl = document.getElementById('message-center-contact-search');
    const contactSelectEl = document.getElementById('message-center-contact-select');
    const contactCurrentEl = document.getElementById('message-center-contact-current');
    const blockListEl = document.getElementById('message-center-block-list');
    const conversationHeaderEl = document.getElementById('message-center-conversation-header');
    const conversationBodyEl = document.getElementById('message-center-conversation-body');
    const composeFormEl = document.getElementById('message-center-compose-form');
    const composeInputEl = document.getElementById('message-center-compose-input');
    const emojiTriggerEl = document.getElementById('message-center-emoji-trigger');
    const imageTriggerEl = document.getElementById('message-center-image-trigger');
    const fileTriggerEl = document.getElementById('message-center-file-trigger');
    const imageInputEl = document.getElementById('message-center-image-input');
    const fileInputEl = document.getElementById('message-center-file-input');
    const attachmentPreviewEl = document.getElementById('message-center-attachment-preview');
    const composeSubmitButtonEl = composeFormEl?.querySelector('[data-send-button]');
    const composeSubmitLabelEl = composeSubmitButtonEl?.querySelector('.message-center-compose-submit__label, .lq-btn__label');
    const composeHintEl = lqEnabled ? document.getElementById('message-center-compose-hint') : null;
    const recentContactsEl = lqEnabled ? document.getElementById('message-center-recent-contacts') : null;
    // Forms only measures this input; the original controller owns submission.
    const formPresentation = lqEnabled ? lqForms.enhanceForms(composeFormEl) : null;
    const unreadTotalEl = document.getElementById('message-center-unread-total');
    const currentTabLabelEl = document.getElementById('message-center-current-tab-label');
    const contactTotalEl = document.getElementById('message-center-contact-total');
    const blockCountEl = document.getElementById('message-center-block-count');

    const state = {
        summary: { unread_total: 0, tabs: [], filters: [] },
        contacts: [],
        blocks: [],
        items: [],
        conversation: null,
        aiReplyJob: null,
        currentTab: isPrivateMode ? PRIVATE_TAB : (app.dataset.initialTab || 'all'),
        currentContact: app.dataset.initialContact || '',
        currentScope: normalizeScope(app.dataset.initialScope),
        keyword: '',
        contactKeyword: '',
        filterKey: 'all',
        tabsExpanded: false,
        searchTimer: null,
        lastSendAt: 0,
        sendCooldownMs: 12000,
        sendRateLimitTimer: null,
        aiReplyPollTimer: null,
        aiReplyPollInFlight: null,
        aiReplyPollGeneration: 0,
        aiReplyPollError: '',
        isSendingMessage: false,
        pendingAttachments: [],
        nextAttachmentId: 1,
        attachmentPreviewItems: new Map(),
        alive: true,
        viewEpoch: 0,
        conversationRequest: 0,
        itemsRequest: 0,
        bootstrapRequest: 0,
        loadedConversationKey: '',
        conversationStatus: 'idle',
        conversationError: '',
        itemsStatus: 'idle',
        itemsError: '',
        bootstrapStatus: 'loading',
        bootstrapError: '',
        metadataReady: false,
        drafts: new Map(),
        activeDraftKey: '',
        actionLease: null,
        navigationIntent: 0,
    };

    let emojiPicker = null;
    const imagePreviewController = new ChatImagePreviewController({
        formatBytes,
        onError: (message) => showToast(message, 'error'),
        onMissingPreview: (message) => showToast(message, 'warning'),
    });

    if (isNotificationsMode && state.currentTab === PRIVATE_TAB) {
        state.currentTab = 'all';
    }

    function appendModeParams(params, { includePrivateData = true } = {}) {
        if (isNotificationsMode) {
            params.set('include_private', '0');
            if (!includePrivateData) {
                params.set('private_data', '0');
            }
        }
        return params;
    }

    function normalizeScope(value) {
        if (value === '' || value == null) {
            return null;
        }
        const numericValue = Number(value);
        return Number.isFinite(numericValue) ? numericValue : null;
    }

    function buildContactKey(identity, scope) {
        return `${identity}|scope:${Number(scope || 0)}`;
    }

    function conversationViewKey(identity = state.currentContact, scope = state.currentScope) {
        return JSON.stringify([String(identity || ''), normalizeScope(scope)]);
    }

    function conversationReady() {
        return state.bootstrapStatus === 'ready' && state.currentTab === PRIVATE_TAB && state.conversationStatus === 'ready'
            && state.loadedConversationKey === conversationViewKey() && Boolean(state.conversation?.contact);
    }

    function saveActiveDraft() {
        const key = state.activeDraftKey;
        if (!key) return null;
        let draft = state.drafts.get(key);
        if (!draft) {
            draft = { text: '', textRevision: 0, attachments: [] };
            state.drafts.set(key, draft);
        }
        if (draft.text !== composeInputEl.value) {
            draft.text = composeInputEl.value;
            draft.textRevision++;
        }
        draft.attachments = [...state.pendingAttachments];
        formPresentation?.refresh();
        return draft;
    }

    function activateDraft(key) {
        saveActiveDraft();
        if (state.activeDraftKey === key) return;
        state.activeDraftKey = key;
        const draft = state.drafts.get(key);
        composeInputEl.value = draft?.text || '';
        state.pendingAttachments = [...(draft?.attachments || [])];
        renderPendingAttachments();
    }

    function settleSentDraft(sent) {
        saveActiveDraft();
        const draft = state.drafts.get(sent.key);
        if (!draft) return;
        if (draft.textRevision === sent.textRevision && draft.text === sent.rawText) {
            draft.text = ''; draft.textRevision++;
        }
        draft.attachments = draft.attachments.filter((attachment) => {
            if (!sent.attachmentIds.has(attachment.id)) return true;
            revokeAttachmentPreview(attachment);
            return false;
        });
        if (state.activeDraftKey === sent.key) {
            composeInputEl.value = draft.text;
            state.pendingAttachments = [...draft.attachments];
            renderPendingAttachments();
        }
    }

    function currentLoadState() {
        if (state.bootstrapStatus === 'loading' || state.bootstrapStatus === 'error') {
            return { status: state.bootstrapStatus, error: state.bootstrapError };
        }
        return state.currentTab === PRIVATE_TAB
            ? { status: state.conversationStatus, error: state.conversationError }
            : { status: state.itemsStatus, error: state.itemsError };
    }

    function emitSummaryUpdate() {
        window.dispatchEvent(new CustomEvent('message-center:summary-updated', {
            detail: state.summary,
        }));
    }

    function localTodayKey() {
        const now = new Date();
        const month = String(now.getMonth() + 1).padStart(2, '0');
        const day = String(now.getDate()).padStart(2, '0');
        return `${now.getFullYear()}-${month}-${day}`;
    }

    function getActiveContact() {
        if (!state.currentContact) {
            return null;
        }
        return state.contacts.find((contact) => (
            contact.identity === state.currentContact
            && normalizeScope(contact.class_offering_id) === normalizeScope(state.currentScope)
        )) || (state.loadedConversationKey === conversationViewKey() ? state.conversation?.contact : null) || null;
    }

    function getFirstVisibleContact() {
        return state.contacts.find(contactMatchesFilter) || null;
    }

    function currentTabConfig() {
        return state.summary.tabs.find((tab) => tab.category === state.currentTab) || state.summary.tabs[0] || {
            category: 'all',
            label: '全部',
            unread_count: 0,
        };
    }

    function sortContactsInPlace() {
        state.contacts.sort((left, right) => String(left.display_name || '').localeCompare(String(right.display_name || ''), 'zh-Hans-CN'));
        state.contacts.sort((left, right) => String(right.last_message_at || '').localeCompare(String(left.last_message_at || '')));
        state.contacts.sort((left, right) => (left.last_message_at ? 0 : 1) - (right.last_message_at ? 0 : 1));
        state.contacts.sort((left, right) => (Number(left.unread_count || 0) > 0 ? 0 : 1) - (Number(right.unread_count || 0) > 0 ? 0 : 1));
    }

    function updateHeroStats() {
        unreadTotalEl.textContent = String(Number(state.summary?.unread_total || 0));
        currentTabLabelEl.textContent = currentTabConfig().label || '全部';
        contactTotalEl.textContent = String(state.contacts.length);
        blockCountEl.textContent = String(state.blocks.length);
        if (lqEnabled) {
            blockListEl.replaceChildren(lqContent.createContent('list', { label: '黑名单', items: state.blocks.map(block => ({
                title: block.display_name || '联系人', meta: block.role || '',
                actions: [{ label: '解除', variant: 'ghost', attrs: { 'data-unblock': block.identity } }],
            })) }));
            publishWorkspaceSnapshot();
            return;
        }
        publishWorkspaceSnapshot();
    }

    function updateUrl() {
        const url = new URL(window.location.href);
        url.searchParams.set('tab', state.currentTab);
        if (state.currentTab === PRIVATE_TAB && state.currentContact) {
            url.searchParams.set('contact', state.currentContact);
            if (state.currentScope != null) {
                url.searchParams.set('scope', String(state.currentScope));
            } else {
                url.searchParams.delete('scope');
            }
        } else {
            url.searchParams.delete('contact');
            url.searchParams.delete('scope');
        }
        window.history.replaceState({}, '', url.toString());
    }

    function applySummary(summary) {
        state.summary = summary || { unread_total: 0, tabs: [], filters: [] };
        if (!state.summary.tabs.some((tab) => tab.category === state.currentTab)) {
            state.currentTab = state.summary.tabs[0]?.category || 'all';
        }
        renderTabs();
        renderFilterOptions();
        updateHeroStats();
        emitSummaryUpdate();
        updateUrl();
    }

    function getSendCooldownRemainingMs() {
        return Math.max(0, state.lastSendAt + state.sendCooldownMs - Date.now());
    }

    function isSendCooldownActive() {
        return getSendCooldownRemainingMs() > 0;
    }

    function isActiveAiReplyJob(job = state.aiReplyJob) {
        return Boolean(job && ACTIVE_AI_JOB_STATUSES.has(String(job.status || '')));
    }

    function isCurrentConversationAiPending() {
        return Boolean(
            state.conversation?.contact?.role === 'assistant'
            && state.aiReplyJob
            && state.aiReplyJob.conversation_key === state.conversation?.conversation_key
            && isActiveAiReplyJob(state.aiReplyJob)
        );
    }

    function currentFilterConfig() {
        return (state.summary.filters || []).find((filter) => filter.value === state.filterKey) || {
            value: 'all',
            label: '全部',
        };
    }

    function visibleContacts() {
        return state.contacts.filter(contactMatchesFilter);
    }

    function publishWorkspaceSnapshot() {
        updateActionButtons();
        const activeContact = getActiveContact();
        const messages = state.conversation?.messages ? filteredMessages() : [];
        const filter = currentFilterConfig();
        const tab = currentTabConfig();
        const snapshot = {
            lqEnabled,
            actionBusy: Boolean(state.actionLease),
            privateHref: app.dataset.privateHref || '/profile?section=private&tab=private_message#profile-message-center',
            notificationsHref: app.dataset.notificationsHref || '/profile?section=notifications#profile-message-center',
            loadStatus: currentLoadState().status,
            loadError: currentLoadState().error,
            mode: appMode,
            currentTab: state.currentTab,
            currentTabLabel: tab.label || '全部',
            filterKey: state.filterKey,
            filterLabel: filter.label || '全部',
            keyword: state.keyword,
            unreadTotal: Number(state.summary?.unread_total || 0),
            currentTabUnread: Number(tab.unread_count || 0),
            itemTotal: Array.isArray(state.items) ? state.items.length : 0,
            unreadItemTotal: Array.isArray(state.items) ? state.items.filter((item) => item.is_unread).length : 0,
            contactTotal: state.contacts.length,
            visibleContactTotal: visibleContacts().length,
            blockCount: state.blocks.length,
            privateOpen: state.currentTab === PRIVATE_TAB,
            hasConversation: Boolean(state.conversation?.contact),
            currentContactName: activeContact?.display_name || '',
            currentContactSubtitle: activeContact?.subtitle || activeContact?.role || '',
            currentContactUnread: Number(activeContact?.unread_count || 0),
            canSend: conversationReady() && Boolean(state.conversation?.contact?.can_send),
            isBlocked: Boolean(state.conversation?.contact?.is_blocked),
            aiPending: isCurrentConversationAiPending(),
            pendingAttachmentCount: state.pendingAttachments.length,
            filteredMessageTotal: messages.length,
            isSendingMessage: Boolean(state.isSendingMessage),
            sendCooldownSeconds: Math.ceil(getSendCooldownRemainingMs() / 1000),
        };
        window.__LANSHARE_MESSAGE_CENTER_WORKSPACE__ = snapshot;
        window.dispatchEvent(new CustomEvent(WORKSPACE_EVENT, { detail: snapshot }));
    }

    function setSubmitButtonVisualState({ label = '发送', disabled = false, busy = false, title = '' } = {}) {
        if (!composeSubmitButtonEl) {
            return;
        }
        if (lqEnabled) {
            // The lq send button stays natively enabled/focusable and uses
            // aria-disabled instead of the disabled attribute, so screen
            // reader and keyboard users can still reach it and understand
            // why it is inert via the adjacent visible compose hint.
            // data-lq-disabled="true" hooks into the frozen, already-tested
            // capture-phase click/keydown guard in static/js/lq/components.js
            // (enhanceComponents -> blockDisabled), which stops activation
            // regardless of styling. sendMessage() also re-checks
            // aria-disabled itself as an independent, second guard that
            // does not depend on that shared listener staying registered.
            composeSubmitButtonEl.disabled = false;
            composeSubmitButtonEl.setAttribute('aria-disabled', String(Boolean(disabled)));
            // blockDisabled() in static/js/lq/components.js compares this
            // attribute's *value* to the string "true" (not just presence),
            // so it must be set/removed explicitly rather than via
            // toggleAttribute (which would write "" for the true case).
            if (disabled) {
                composeSubmitButtonEl.setAttribute('data-lq-disabled', 'true');
            } else {
                composeSubmitButtonEl.removeAttribute('data-lq-disabled');
            }
            if (disabled && composeHintEl?.id) {
                composeSubmitButtonEl.setAttribute('aria-describedby', composeHintEl.id);
            } else {
                composeSubmitButtonEl.removeAttribute('aria-describedby');
            }
            // .lq-btn's shared CSS (static/css/lq/components/button.css,
            // not owned by this package) already renders the disabled look
            // for `:is(:disabled, .is-disabled)`; reusing that existing
            // class keeps the dimmed/not-allowed visual without a native
            // disabled attribute and without touching that shared file.
            composeSubmitButtonEl.classList.toggle('is-disabled', Boolean(disabled));
        } else {
            composeSubmitButtonEl.disabled = Boolean(disabled);
        }
        composeSubmitButtonEl.classList.toggle('is-busy', Boolean(busy));
        if (lqEnabled) {
            composeSubmitButtonEl.classList.toggle('is-loading', Boolean(busy));
            let spinner = composeSubmitButtonEl.querySelector('.lq-btn__spinner');
            if (busy && !spinner) {
                spinner = document.createElement('span'); spinner.className = 'lq-btn__spinner'; spinner.setAttribute('aria-hidden', 'true');
                spinner.append(lqPresentation.createComponent('spinner', { size: 'sm' })); composeSubmitButtonEl.append(spinner);
            } else if (!busy) spinner?.remove();
        }
        composeSubmitButtonEl.setAttribute('aria-busy', String(Boolean(busy)));
        if (title) {
            composeSubmitButtonEl.title = title;
        } else {
            composeSubmitButtonEl.removeAttribute('title');
        }
        if (composeSubmitLabelEl) {
            composeSubmitLabelEl.textContent = label;
        }
    }

    function setAttachmentButtonsDisabled(disabled) {
        [imageTriggerEl, fileTriggerEl].forEach((button) => {
            if (button) {
                button.disabled = Boolean(disabled) || (button === fileTriggerEl && state.conversation?.contact?.role === 'assistant');
                if (button === fileTriggerEl) {
                    button.title = state.conversation?.contact?.role === 'assistant'
                        ? 'AI 助教私信支持文字和图片，其他文件请使用课堂即时对话' : '添加文件';
                }
            }
        });
    }

    function updateSendButtonState() {
        const contact = conversationReady() ? state.conversation?.contact : null;
        const canSend = Boolean(contact?.can_send);

        if (!contact || !canSend) {
            setSubmitButtonVisualState({ label: '发送', disabled: true });
            setAttachmentButtonsDisabled(true);
            publishWorkspaceSnapshot();
            return;
        }

        // The send lock belongs to the sender, while attachment selection and
        // text editing belong to the selected conversation's next draft.
        setAttachmentButtonsDisabled(false);

        if (state.isSendingMessage) {
            setSubmitButtonVisualState({ label: '发送中', disabled: true, busy: true, title: '正在发送私信' });
            publishWorkspaceSnapshot();
            return;
        }

        if (isCurrentConversationAiPending()) {
            setSubmitButtonVisualState({ label: 'AI 回复中', disabled: true, busy: true, title: 'AI 助教正在回复上一条消息' });
            publishWorkspaceSnapshot();
            return;
        }

        if (isSendCooldownActive()) {
            const remaining = Math.ceil(getSendCooldownRemainingMs() / 1000);
            setSubmitButtonVisualState({ label: `${remaining}s 后可发送`, disabled: true });
            publishWorkspaceSnapshot();
            return;
        }

        setSubmitButtonVisualState({ label: '发送' });
        publishWorkspaceSnapshot();
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

    function normalizeAttachmentPayload(item) {
        return normalizeChatImageAttachment(item);
    }

    function getAttachmentThumbnailUrl(item) {
        return getChatImageAttachmentThumbnailUrl(item);
    }

    function getAttachmentPreviewUrl(item) {
        return getChatImageAttachmentPreviewUrl(item);
    }

    function getAttachmentOriginalUrl(item) {
        return getChatImageAttachmentOriginalUrl(item);
    }

    function getAttachmentDisplayMeta(item) {
        return getChatImageAttachmentDisplayMeta(item, formatBytes);
    }

    function openAttachmentPreview(item, siblings = []) {
        imagePreviewController.open(normalizeAttachmentPayload(item), siblings);
    }

    function revokeAttachmentPreview(attachment) {
        if (attachment?.previewUrl) {
            URL.revokeObjectURL(attachment.previewUrl);
        }
    }

    function renderPendingAttachments() {
        saveActiveDraft();
        if (!attachmentPreviewEl) {
            publishWorkspaceSnapshot();
            return;
        }
        attachmentPreviewEl.replaceChildren();
        attachmentPreviewEl.hidden = state.pendingAttachments.length === 0;
        if (!state.pendingAttachments.length) {
            publishWorkspaceSnapshot();
            return;
        }

        const fragment = document.createDocumentFragment();
        state.pendingAttachments.forEach((attachment) => {
            const card = document.createElement('div');
            card.className = `message-center-attachment-chip${attachment.isImage ? ' is-image' : ''}`;

            if (attachment.isImage && attachment.previewUrl) {
                const image = document.createElement('img');
                image.src = attachment.previewUrl;
                image.alt = attachment.file.name || '待发送图片';
                image.loading = 'lazy';
                card.appendChild(image);
            } else {
                const icon = document.createElement('span');
                icon.className = 'message-center-attachment-chip__icon';
                icon.textContent = '📎';
                icon.setAttribute('aria-hidden', 'true');
                card.appendChild(icon);
            }

            const meta = document.createElement('span');
            meta.className = 'message-center-attachment-chip__meta';
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
                state.pendingAttachments = state.pendingAttachments.filter((item) => item.id !== attachment.id);
                revokeAttachmentPreview(attachment);
                renderPendingAttachments();
            });
            card.appendChild(removeButton);
            fragment.appendChild(card);
        });
        attachmentPreviewEl.appendChild(fragment);
        publishWorkspaceSnapshot();
    }

    function queuePrivateAttachments(files, { imagesOnly = false } = {}) {
        imagesOnly = imagesOnly || state.conversation?.contact?.role === 'assistant';
        const selectedFiles = Array.from(files || []);
        if (!selectedFiles.length) {
            return;
        }
        if (!conversationReady() || !state.conversation?.contact?.can_send) {
            showToast('请先选择可发送的联系人', 'warning');
            return;
        }

        const remainingSlots = PRIVATE_ATTACHMENT_LIMIT - state.pendingAttachments.length;
        if (remainingSlots <= 0) {
            showToast(`单条私信最多添加 ${PRIVATE_ATTACHMENT_LIMIT} 个附件`, 'warning');
            return;
        }
        const acceptedFiles = selectedFiles.slice(0, remainingSlots);
        if (acceptedFiles.length < selectedFiles.length) {
            showToast(`超出部分已忽略，单条私信最多 ${PRIVATE_ATTACHMENT_LIMIT} 个附件`, 'warning');
        }

        for (const file of acceptedFiles) {
            if (imagesOnly && !isImageFile(file)) {
                showToast('图片入口仅支持 PNG、JPG、GIF 或 WebP', 'warning');
                continue;
            }
            if (Number(file.size || 0) > PRIVATE_ATTACHMENT_MAX_BYTES) {
                showToast(`${file.name || '附件'} 超过 100MB，已忽略`, 'warning');
                continue;
            }
            const isImage = isImageFile(file);
            state.pendingAttachments.push({
                id: state.nextAttachmentId++,
                file,
                isImage,
                previewUrl: isImage ? URL.createObjectURL(file) : '',
            });
        }
        renderPendingAttachments();
    }

    function renderMessageAttachments(attachments) {
        const items = Array.isArray(attachments) ? attachments : [];
        if (!items.length) {
            return '';
        }
        return `
            <div class="message-center-message__attachments">
                ${items.map((attachment) => {
                    const normalizedAttachment = normalizeAttachmentPayload(attachment) || {};
                    const name = escapeHtml(normalizedAttachment.name || '附件');
                    const size = escapeHtml(formatBytes(normalizedAttachment.file_size));
                    if (normalizedAttachment.is_image || normalizedAttachment.type === 'image') {
                        const previewKey = `message-center-attachment-${normalizedAttachment.id || normalizedAttachment.attachment_id || Math.random().toString(36).slice(2)}`;
                        state.attachmentPreviewItems.set(previewKey, normalizedAttachment);
                        const thumbnailUrl = escapeHtml(getAttachmentThumbnailUrl(normalizedAttachment));
                        const meta = escapeHtml(getAttachmentDisplayMeta(normalizedAttachment) || size);
                        return `
                            <button type="button" class="message-center-message__attachment is-image" data-private-image-preview-key="${escapeHtml(previewKey)}">
                                <img src="${thumbnailUrl}" alt="${name}" loading="lazy" decoding="async">
                                <span>${name}${meta ? ` · ${meta}` : ''}</span>
                            </button>
                        `;
                    }
                    return `
                        <a class="message-center-message__attachment is-file" href="${escapeHtml(normalizedAttachment.download_url || normalizedAttachment.url || '#')}" target="_blank" rel="noreferrer noopener">
                            <span class="message-center-message__attachment-icon" aria-hidden="true">📎</span>
                            <span>${name}${size ? `<small>${size}</small>` : ''}</span>
                        </a>
                    `;
                }).join('')}
            </div>
        `;
    }

    function activateSendCooldown(retryAfterSeconds) {
        const safeSeconds = Math.max(Number(retryAfterSeconds || 12), 1);
        state.sendCooldownMs = safeSeconds * 1000;
        state.lastSendAt = Date.now();

        if (state.sendRateLimitTimer) {
            window.clearTimeout(state.sendRateLimitTimer);
        }

        const tick = () => {
            state.sendRateLimitTimer = null;
            if (!state.alive) return;
            const remaining = getSendCooldownRemainingMs();
            if (!remaining) { state.lastSendAt = 0; state.sendCooldownMs = 12000; }
            updateSendButtonState();
            if (remaining) state.sendRateLimitTimer = window.setTimeout(tick, Math.min(1000, remaining));
        };
        tick();
    }

    function clearAiReplyPolling() {
        if (state.aiReplyPollTimer) {
            window.clearTimeout(state.aiReplyPollTimer);
            state.aiReplyPollTimer = null;
        }
        state.aiReplyPollGeneration++;
        state.aiReplyPollInFlight = null;
    }

    function syncAiReplyJob(job) {
        state.aiReplyJob = job || null;
        state.aiReplyPollError = '';
        clearAiReplyPolling();
        scheduleAiReplyPoll(state.aiReplyPollGeneration);
        updateSendButtonState();
    }

    function scheduleAiReplyPoll(generation) {
        if (state.alive && generation === state.aiReplyPollGeneration && state.currentTab === PRIVATE_TAB
            && isActiveAiReplyJob(state.aiReplyJob) && state.aiReplyPollTimer === null) {
            const timer = window.setTimeout(() => {
                if (generation !== state.aiReplyPollGeneration || state.aiReplyPollTimer !== timer) return;
                state.aiReplyPollTimer = null;
                void pollAiReplyJobStatus();
            }, AI_JOB_POLL_INTERVAL_MS);
            state.aiReplyPollTimer = timer;
        }
    }

    async function pollAiReplyJobStatus() {
        if (state.aiReplyPollInFlight || state.currentTab !== PRIVATE_TAB || !state.aiReplyJob?.id || !isActiveAiReplyJob(state.aiReplyJob)) {
            return;
        }

        const lease = {};
        state.aiReplyPollInFlight = lease;
        const generation = state.aiReplyPollGeneration;
        const jobId = Number(state.aiReplyJob.id);
        const contactIdentity = state.currentContact;
        const scope = state.currentScope;
        const key = conversationViewKey();
        const jobKey = state.aiReplyJob.conversation_key;
        const owns = () => state.alive && generation === state.aiReplyPollGeneration
            && state.aiReplyPollInFlight === lease && state.currentTab === PRIVATE_TAB
            && key === conversationViewKey() && Number(state.aiReplyJob?.id) === jobId;

        try {
            const response = await apiFetch(`/api/message-center/private/ai-jobs/${jobId}`, { silent: true });
            if (!owns()) return;
            const job = response?.job || null;
            if (!job || Number(job.id) !== jobId || job.conversation_key !== jobKey
                || !['pending', 'running', 'completed', 'failed'].includes(job.status)) {
                const error = new Error('未取得该回复任务的有效状态，请刷新会话。');
                error.protocolError = true;
                throw error;
            }

            state.aiReplyJob = job;
            state.aiReplyPollError = '';
            if (job.status === 'completed') {
                if (await loadConversation(contactIdentity, scope, { showLoading: false, scrollToBottom: true })) {
                    showToast('AI 助教已回复', 'success');
                }
                return;
            }

            renderConversation();
            if (job.status === 'failed') {
                showToast('AI 助教这次没有成功回复，你可以稍后再试。', 'warning');
                return;
            }

            scheduleAiReplyPoll(generation);
        } catch (error) {
            if (!owns()) return;
            state.aiReplyPollError = error.status === 404 || error.status === 403 || error.protocolError
                ? '暂时无法取得该回复任务，请刷新会话查看最新状态。'
                : '暂未取得回复状态，正在重试；也可以手动刷新会话。';
            renderConversation();
            if (!error.protocolError && ![401, 403, 404].includes(error.status)) scheduleAiReplyPoll(generation);
        } finally {
            if (state.aiReplyPollInFlight === lease) state.aiReplyPollInFlight = null;
        }
    }

    function initEmojiPicker() {
        const emojiAnchor = document.getElementById('message-center-emoji-anchor');
        if (!emojiTriggerEl || !composeInputEl) {
            return;
        }

        emojiPicker = createEmojiPicker({ targetInput: composeInputEl });
        // This picker writes .value directly, so input alone cannot observe it.
        emojiPicker.element.addEventListener('click', saveActiveDraft);
        if (emojiAnchor) {
            emojiAnchor.appendChild(emojiPicker.element);
        } else {
            composeFormEl.appendChild(emojiPicker.element);
        }

        emojiTriggerEl.addEventListener('click', () => {
            if (!emojiTriggerEl.disabled) {
                emojiPicker.toggle();
            }
        });

        document.addEventListener('pointerdown', (event) => {
            if (emojiPicker.isOpen() && !emojiPicker.element.contains(event.target) && !emojiTriggerEl.contains(event.target)) {
                emojiPicker.close();
            }
        });

        document.addEventListener('keydown', (event) => {
            if (event.key === 'Escape' && emojiPicker.isOpen()) {
                emojiPicker.close();
            }
        });
    }

    function initEditorToolbar() {
        const toolbar = document.getElementById('message-center-editor-toolbar');
        if (!toolbar || !composeInputEl) {
            return;
        }

        toolbar.addEventListener('click', (event) => {
            const button = event.target.closest('[data-md-insert]');
            if (!button) {
                return;
            }
            event.preventDefault();
            insertMarkdownSyntax(button.dataset.mdInsert);
        });
    }

    function insertMarkdownSyntax(type) {
        const start = composeInputEl.selectionStart ?? composeInputEl.value.length;
        const end = composeInputEl.selectionEnd ?? composeInputEl.value.length;
        const value = composeInputEl.value;
        const selected = value.slice(start, end);

        const syntaxMap = {
            bold: { before: '**', after: '**', placeholder: '加粗文字' },
            italic: { before: '*', after: '*', placeholder: '斜体文字' },
            heading: { before: '## ', after: '', placeholder: '标题' },
            code: { before: '`', after: '`', placeholder: '代码' },
            codeblock: { before: '```\n', after: '\n```', placeholder: '代码块' },
            ul: { before: '- ', after: '', placeholder: '列表项' },
            ol: { before: '1. ', after: '', placeholder: '列表项' },
            quote: { before: '> ', after: '', placeholder: '引用文字' },
            link: { before: '[', after: '](url)', placeholder: '链接文字' },
        };

        const syntax = syntaxMap[type];
        if (!syntax) {
            return;
        }

        const text = selected || syntax.placeholder;
        const inserted = `${syntax.before}${text}${syntax.after}`;
        composeInputEl.value = `${value.slice(0, start)}${inserted}${value.slice(end)}`;
        saveActiveDraft();

        const nextPos = start + syntax.before.length + text.length + syntax.after.length;
        composeInputEl.focus();
        composeInputEl.setSelectionRange(nextPos, nextPos);
    }

    function renderTabs() {
        if (isPrivateMode) {
            tabsEl.hidden = true;
            tabsEl.innerHTML = '';
            return;
        }
        tabsEl.hidden = false;
        const tabs = state.summary.tabs || [];
        if (lqEnabled) {
            const first = tabs.slice(0, 8);
            const active = tabs.find(tab => tab.category === state.currentTab);
            if (active && !first.includes(active)) first[first.length - 1] = active;
            const buttons = (state.tabsExpanded ? tabs : first).map(tab => lqPresentation.createComponent('chip', {
                label: `${tab.label}${Number(tab.unread_count) > 0 ? ` · ${Number(tab.unread_count)} 未读` : ''}`,
                kind: 'filter', tone: 'primary', pressed: tab.category === state.currentTab,
                attrs: { 'data-tab': tab.category, 'aria-controls': 'message-center-feed' },
            }));
            if (tabs.length > 8) buttons.push(lqPresentation.createComponent('button', {
                label: state.tabsExpanded ? '收起分类' : `更多分类 (${tabs.length - first.length})`, variant: 'ghost',
                attrs: { 'data-tabs-toggle': '', 'aria-expanded': String(state.tabsExpanded) },
            }));
            tabsEl.replaceChildren(...buttons);
            return;
        }
        const isPrimaryTab = (tab) => tab.category === 'all'
            || tab.category === state.currentTab
            || Number(tab.unread_count || 0) > 0;
        const hiddenCount = tabs.filter((tab) => !isPrimaryTab(tab)).length;
        const visibleTabs = state.tabsExpanded ? tabs : tabs.filter(isPrimaryTab);
        const renderTabButton = (tab) => `
            <button
                type="button"
                class="message-center-tab ${tab.category === state.currentTab ? 'is-active' : ''}"
                data-tab="${escapeHtml(tab.category)}"
            >
                <span>${escapeHtml(tab.label)}</span>
                ${Number(tab.unread_count || 0) > 0 ? `<span class="message-center-tab__count">${Number(tab.unread_count || 0)}</span>` : ''}
            </button>
        `;
        const toggleButton = hiddenCount > 0 || state.tabsExpanded
            ? `
            <button type="button" class="message-center-tab message-center-tab--toggle" data-tabs-toggle>
                <span>${state.tabsExpanded ? '收起分类' : `更多分类 (${hiddenCount})`}</span>
            </button>
        `
            : '';
        tabsEl.innerHTML = visibleTabs.map(renderTabButton).join('') + toggleButton;
    }

    function renderFilterOptions() {
        const filters = state.summary.filters || [];
        if (!filters.some((filter) => filter.value === state.filterKey)) {
            state.filterKey = filters[0]?.value || 'all';
        }
        filterEl.innerHTML = filters.map((filter) => `
            <option value="${escapeHtml(filter.value)}">${escapeHtml(filter.label)}</option>
        `).join('');
        filterEl.value = state.filterKey;
    }

    function renderEmpty(container, title, text) {
        if (lqEnabled) {
            container.replaceChildren(lqContent.createContent('empty', { title, description: text, variant: 'card' }));
            return;
        }
        container.innerHTML = `
            <div class="message-center-empty">
                <div class="message-center-empty__card">
                    <h3>${escapeHtml(title)}</h3>
                    <p>${escapeHtml(text)}</p>
                </div>
            </div>
        `;
    }

    function setLoading(container, text = '正在加载...') {
        if (lqEnabled) {
            const loading = lqContent.createContent('empty', { title: text, variant: 'card' });
            loading.prepend(lqPresentation.createComponent('spinner', { size: 'sm' }));
            container.replaceChildren(loading);
            return;
        }
        container.innerHTML = `
            <div class="message-center-empty">
                <div class="message-center-empty__card">
                    <div class="spinner mx-auto"></div>
                    <p>${escapeHtml(text)}</p>
                </div>
            </div>
        `;
    }

    function renderLoadFailure(container, title, message, retry) {
        if (lqEnabled) {
            const error = lqContent.createContent('empty', { reason: 'error', title, description: message, variant: 'card',
                attrs: { 'data-message-load-error': '' }, actions: [{ label: '重试', variant: 'soft', attrs: { 'data-message-load-retry': '' } }] });
            error.querySelector('[data-message-load-retry]').addEventListener('click', retry);
            container.replaceChildren(error);
            return;
        }
        container.innerHTML = `<div class="message-center-empty"><div class="message-center-empty__card" data-message-load-error>
            <h3>${escapeHtml(title)}</h3><p>${escapeHtml(message)}</p>
            <button type="button" class="btn btn-outline" data-message-load-retry>重试</button>
        </div></div>`;
        container.querySelector('[data-message-load-retry]').addEventListener('click', retry);
    }

    function notificationHasJumpTarget(item) {
        // open_url 始终是 /message-center/notifications/{id}/open（仅负责标记已读并跳转），
        // 真正的落点是 link_url。link_url 为空、或指回消息中心自身时都属于无意义跳转。
        const link = String(item.link_url || '').trim();
        if (!link) {
            return false;
        }
        if (link.startsWith('/message-center')) {
            return false;
        }
        if (link.includes('section=notifications') || link.includes('profile-message-center')) {
            return false;
        }
        return true;
    }

    function formatNotificationAction(item) {
        const actionMap = {
            assignment: '查看作业',
            submission: '查看提交',
            discussion_mention: '进入课堂',
            grading_result: '查看批改结果',
            ai_feedback: '查看 AI 反馈',
            learning_progress: '查看学习进度',
            app_feedback: '查看反馈',
            password_reset_request: '审核申请',
            private_message: '查看私信',
        };
        return actionMap[item.category] || '查看详情';
    }

    function renderItems() {
        if (!Array.isArray(state.items) || state.items.length === 0) {
            renderEmpty(feedEl, '当前没有匹配的信息', '可以切换分类、搜索关键词或调整筛选条件后再试。');
            publishWorkspaceSnapshot();
            return;
        }

        if (lqEnabled) {
            const rows = state.items.map(item => {
                const row = lqContent.createContent('row', {
                    title: String(item.title || '通知'), unread: Boolean(item.is_unread),
                    meta: [item.category_label || item.category, item.severity_label || '普通通知', item.actor_display_name || '系统', formatDate(item.created_at || '')].filter(Boolean).join(' · '),
                    ...(notificationHasJumpTarget(item) ? { primary: { href: item.open_url || item.link_url, attrs: { 'data-open-notification': String(Number(item.id)) } } } : {}),
                    actions: [{ label: '标记已读', variant: 'ghost', attrs: { 'data-mark-notification': String(Number(item.id)) } }],
                });
                const body = document.createElement('p'); body.className = 'lq-messages__notification-copy'; body.textContent = item.body_preview || '暂无更多内容';
                row.querySelector('.lq-row__main').append(body);
                if (item.is_unread) row.querySelector('.lq-row__trail').prepend(lqPresentation.createComponent('chip', { label: '未读', tone: 'primary', size: 'sm' }));
                return row;
            });
            feedEl.replaceChildren(lqContent.createContent('list', { label: '通知列表' }, { items: rows }));
            publishWorkspaceSnapshot();
            return;
        }
        feedEl.innerHTML = state.items.map((item) => `
            <article class="message-center-card ${item.is_unread ? 'is-unread' : ''}">
                <div class="message-center-card__top">
                    <div class="message-center-card__category">
                        <span class="message-center-pill">${escapeHtml(item.category_label || item.category)}</span>
                        <span class="message-center-pill message-center-pill--severity is-${escapeHtml(item.severity || 'normal')}">${escapeHtml(item.severity_label || '普通通知')}</span>
                        ${item.is_unread ? '<span class="message-center-pill is-danger">未读</span>' : '<span class="message-center-pill">已读</span>'}
                    </div>
                    <span class="message-center-card__meta">${escapeHtml(formatDate(item.created_at || ''))}</span>
                </div>
                <div>
                    <h3 class="message-center-card__title">${escapeHtml(item.title || '')}</h3>
                    <div class="message-center-card__meta">
                        <span>${escapeHtml(item.actor_display_name || '系统')}</span>
                        ${item.class_offering_id ? `<span>课堂 #${Number(item.class_offering_id)}</span>` : ''}
                    </div>
                </div>
                <div class="message-center-card__body">${escapeHtml(item.body_preview || '暂无更多内容')}</div>
                <div class="message-center-card__actions">
                    <button type="button" class="btn btn-ghost btn-sm" data-mark-notification="${Number(item.id)}">
                        标记已读
                    </button>
                    ${notificationHasJumpTarget(item) ? `
                    <a
                        href="${escapeHtml(item.open_url || item.link_url)}"
                        class="btn btn-primary btn-sm"
                        data-open-notification="${Number(item.id)}"
                    >
                        ${escapeHtml(formatNotificationAction(item))}
                    </a>
                    ` : ''}
                </div>
            </article>
        `).join('');
        publishWorkspaceSnapshot();
    }

    function contactMatchesFilter(contact) {
        const keyword = state.contactKeyword.trim().toLowerCase();
        if (keyword) {
            const haystack = [
                contact.display_name,
                contact.subtitle,
                contact.last_message_preview,
            ].join(' ').toLowerCase();
            if (!haystack.includes(keyword)) {
                return false;
            }
        }
        if (state.filterKey === 'unread' && Number(contact.unread_count || 0) <= 0) {
            return false;
        }
        if (state.filterKey === 'today' && !String(contact.last_message_at || '').startsWith(localTodayKey())) {
            return false;
        }
        return true;
    }

    function renderContacts() {
        const visibleContacts = state.contacts.filter(contactMatchesFilter);
        if (recentContactsEl) {
            // Native select remains the complete authoritative chooser. This
            // bounded shortcut list only contains actual history or unread.
            const recent = visibleContacts.filter(contact => contact.last_message_at || Number(contact.unread_count) > 0).slice(0, 8);
            recentContactsEl.hidden = !recent.length;
            if (recent.length) recentContactsEl.replaceChildren(lqContent.createContent('list', { label: '最近会话与未读', items: recent.map(contact => ({
                title: contact.display_name || '联系人', unread: Number(contact.unread_count) > 0,
                meta: `${contact.subtitle || contact.role || ''}${Number(contact.unread_count) > 0 ? ` · ${Number(contact.unread_count)} 条未读` : ''}`,
                primary: { attrs: { 'data-select-contact': buildContactKey(contact.identity, contact.class_offering_id) } },
            })) }));
            else recentContactsEl.replaceChildren();
        }
        const activeContact = getActiveContact();
        const options = [...visibleContacts];

        if (activeContact) {
            const activeKey = buildContactKey(activeContact.identity, activeContact.class_offering_id);
            if (!options.some((contact) => buildContactKey(contact.identity, contact.class_offering_id) === activeKey)) {
                options.unshift(activeContact);
            }
        }

        contactSelectEl.disabled = options.length === 0;
        contactSelectEl.innerHTML = `
            <option value="">请选择联系人</option>
            ${options.map((contact) => {
                const contactKey = buildContactKey(contact.identity, contact.class_offering_id);
                const unreadSuffix = Number(contact.unread_count || 0) > 0 ? ` (${Number(contact.unread_count || 0)})` : '';
                const optionLabel = [
                    contact.display_name || '联系人',
                    contact.subtitle || contact.role || '',
                ].filter(Boolean).join(' - ');
                return `
                    <option
                        value="${escapeHtml(contactKey)}"
                        data-contact="${escapeHtml(contact.identity)}"
                        data-scope="${contact.class_offering_id == null ? '' : Number(contact.class_offering_id)}"
                    >
                        ${escapeHtml(`${optionLabel}${unreadSuffix}`)}
                    </option>
                `;
            }).join('')}
        `;

        if (activeContact) {
            contactSelectEl.value = buildContactKey(activeContact.identity, activeContact.class_offering_id);
        } else {
            contactSelectEl.value = '';
        }

        if (!activeContact) {
            contactCurrentEl.innerHTML = `
                <div class="message-center-empty__card">
                    <h3>暂未选择联系人</h3>
                    <p class="message-center-conversation__hint">
                        ${options.length > 0 ? '请先通过搜索和下拉列表选择要打开的会话。' : '当前筛选条件下没有匹配的联系人。'}
                    </p>
                </div>
            `;
            publishWorkspaceSnapshot();
            return;
        }

        contactCurrentEl.innerHTML = `
            <div class="message-center-contact__meta">
                <strong class="message-center-contact__name">${escapeHtml(activeContact.display_name || '联系人')}</strong>
                ${Number(activeContact.unread_count || 0) > 0 ? `<span class="message-center-tab__count">${Number(activeContact.unread_count || 0)}</span>` : '<span class="message-center-pill">已选择</span>'}
            </div>
            <div class="message-center-contact__subtitle">${escapeHtml(activeContact.subtitle || activeContact.role || '')}</div>
            <div class="message-center-contact__preview">${escapeHtml(activeContact.last_message_preview || '暂无私信记录')}</div>
        `;
        publishWorkspaceSnapshot();
    }

    function renderBlocks() {
        if (!Array.isArray(state.blocks) || state.blocks.length === 0) {
            blockListEl.innerHTML = '<div class="message-center-block-empty">当前没有拉黑任何联系人。</div>';
            blockCountEl.textContent = '0';
            publishWorkspaceSnapshot();
            return;
        }

        blockCountEl.textContent = String(state.blocks.length);
        blockListEl.innerHTML = state.blocks.map((block) => `
            <div class="message-center-block-item">
                <div>
                    <strong>${escapeHtml(block.display_name || '联系人')}</strong>
                    <div class="message-center-conversation__hint">${escapeHtml(block.role || '')}</div>
                </div>
                <button
                    type="button"
                    class="btn btn-ghost btn-sm"
                    data-unblock="${escapeHtml(block.identity)}"
                >
                    解除
                </button>
            </div>
        `).join('');
        publishWorkspaceSnapshot();
    }

    function syncContact(contact) {
        const contactKey = buildContactKey(contact.identity, contact.class_offering_id);
        const existingIndex = state.contacts.findIndex((item) => buildContactKey(item.identity, item.class_offering_id) === contactKey);
        if (existingIndex >= 0) {
            state.contacts[existingIndex] = {
                ...state.contacts[existingIndex],
                ...contact,
                unread_count: 0,
            };
        } else {
            state.contacts.unshift({
                ...contact,
                unread_count: 0,
            });
        }
        sortContactsInPlace();
    }

    function scrollConversationToBottom() {
        window.requestAnimationFrame(() => {
            conversationBodyEl.scrollTop = conversationBodyEl.scrollHeight;
        });
    }

    function updateContactPreviewFromMessage(message) {
        const contactKey = buildContactKey(state.currentContact, state.currentScope);
        const existingIndex = state.contacts.findIndex((contact) => buildContactKey(contact.identity, contact.class_offering_id) === contactKey);
        if (existingIndex < 0) {
            return;
        }
        const attachmentCount = Array.isArray(message.attachments) ? message.attachments.length : 0;
        state.contacts[existingIndex] = {
            ...state.contacts[existingIndex],
            unread_count: 0,
            last_message_preview: String(message.content || '') || (attachmentCount ? `${attachmentCount} 个附件` : ''),
            last_message_at: String(message.created_at || ''),
            last_message_is_outgoing: Boolean(message.is_outgoing),
        };
        sortContactsInPlace();
    }

    function appendMessageToConversation(message, { shouldRender = true } = {}) {
        if (!state.conversation) {
            return;
        }
        const messages = Array.isArray(state.conversation.messages) ? [...state.conversation.messages] : [];
        messages.push(message);
        state.conversation = {
            ...state.conversation,
            messages,
        };
        updateContactPreviewFromMessage(message);
        if (shouldRender) {
            renderContacts();
            renderConversation();
            scrollConversationToBottom();
        }
    }

    function filteredMessages() {
        if (!state.conversation?.messages) {
            return [];
        }
        const keyword = state.keyword.trim().toLowerCase();
        if (!keyword) {
            return state.conversation.messages;
        }
        return state.conversation.messages.filter((message) => (
            String(message.content || '').toLowerCase().includes(keyword)
            || String(message.sender_display_name || '').toLowerCase().includes(keyword)
            || (Array.isArray(message.attachments) && message.attachments.some((attachment) => (
                String(attachment.name || '').toLowerCase().includes(keyword)
            )))
        ));
    }

    function buildRenderableMessages() {
        const messages = [...filteredMessages()];
        if (!state.aiReplyJob || state.aiReplyJob.conversation_key !== state.conversation?.conversation_key || state.conversation?.contact?.role !== 'assistant') {
            return messages;
        }

        if (state.aiReplyJob.status === 'failed') {
            messages.push({
                id: `ai-reply-job-${state.aiReplyJob.id}`,
                sender_role: 'assistant',
                sender_display_name: state.conversation.contact.display_name || 'AI 助教',
                created_at: state.aiReplyJob.finished_at || state.aiReplyJob.updated_at || state.aiReplyJob.created_at,
                content: 'AI 助教这次没有成功生成回复。',
                status_copy: '稍后可以再发一条消息继续对话。',
                is_outgoing: false,
                is_virtual: true,
                virtual_status: 'failed',
            });
            return messages;
        }

        if (isActiveAiReplyJob(state.aiReplyJob)) {
            messages.push({
                id: `ai-reply-job-${state.aiReplyJob.id}`,
                sender_role: 'assistant',
                sender_display_name: state.conversation.contact.display_name || 'AI 助教',
                created_at: state.aiReplyJob.started_at || state.aiReplyJob.created_at,
                content: 'AI 助教正在整理回复...',
                status_copy: '你现在可以继续浏览其他区域，回复完成后会自动出现在这里。',
                is_outgoing: false,
                is_virtual: true,
                virtual_status: 'pending',
            });
        }

        return messages;
    }

    function renderConversation() {
        const conversation = state.conversation;
        const contact = conversation?.contact;
        state.attachmentPreviewItems.clear();
        if (state.conversationStatus === 'loading' || state.conversationStatus === 'error'
            || state.bootstrapStatus === 'error' || state.bootstrapStatus === 'loading') {
            const activeContact = getActiveContact();
            conversationHeaderEl.innerHTML = `<div><h2 class="message-center-pane-title">${escapeHtml(activeContact?.display_name || '私信会话')}</h2></div>`;
            if (state.bootstrapStatus === 'error') {
                renderLoadFailure(conversationBodyEl, '信息中心加载失败', state.bootstrapError, () => { void bootstrap(); });
            } else if (state.conversationStatus === 'error') {
                renderLoadFailure(conversationBodyEl, '私信会话加载失败', state.conversationError,
                    () => { void loadConversation(state.currentContact, state.currentScope); });
            } else {
                setLoading(conversationBodyEl, '正在加载私信会话...');
            }
            // A draft remains editable while the selected recipient is being
            // checked; sending and attachment selection require a ready reply.
            composeInputEl.disabled = !state.currentContact;
            composeInputEl.placeholder = '可先整理草稿，会话加载成功后再发送';
            if (emojiTriggerEl) emojiTriggerEl.disabled = !state.currentContact;
            updateSendButtonState();
            return;
        }
        if (!contact) {
            conversationHeaderEl.innerHTML = `
                <div>
                    <h2 class="message-center-pane-title">选择一个联系人</h2>
                    <p class="message-center-conversation__hint">通过上方搜索和下拉列表打开会话后，即可查看与发送私信。</p>
                </div>
            `;
            renderEmpty(conversationBodyEl, '还没有打开私信会话', '请先从左侧联系人选择器中选择一个联系人。');
            composeInputEl.disabled = true;
            composeInputEl.placeholder = '请先选择联系人';
            if (emojiTriggerEl) {
                emojiTriggerEl.disabled = true;
            }
            setAttachmentButtonsDisabled(true);
            updateSendButtonState();
            publishWorkspaceSnapshot();
            return;
        }

        const blockAction = contact.can_block ? `
            <button
                type="button"
                class="btn btn-outline btn-sm"
                data-toggle-block="${escapeHtml(contact.identity)}"
                data-toggle-scope="${contact.class_offering_id == null ? '' : Number(contact.class_offering_id)}"
                data-is-blocked="${contact.is_blocked ? '1' : '0'}"
            >
                ${contact.is_blocked ? '解除黑名单' : '加入黑名单'}
            </button>
        ` : '';

        conversationHeaderEl.innerHTML = `
            <div>
                <h2 class="message-center-pane-title">${escapeHtml(contact.display_name || '联系人')}</h2>
                <p class="message-center-conversation__hint">${escapeHtml(contact.subtitle || contact.role || '')}</p>
                ${contact.is_blocked_by_contact ? '<p class="message-center-conversation__hint">对方当前不接收你的私信。</p>' : ''}
            </div>
            <div class="message-center-conversation__tools">
                ${contact.is_blocked ? '<span class="message-center-pill is-danger">已拉黑</span>' : ''}
                ${blockAction}
            </div>
        `;

        if (lqEnabled && contact.can_block) {
            conversationHeaderEl.querySelector('[data-toggle-block]')?.replaceWith(lqPresentation.createComponent('button', {
                label: contact.is_blocked ? '解除黑名单' : '加入黑名单', variant: 'ghost', size: 'sm',
                attrs: { 'data-toggle-block': contact.identity, 'data-toggle-scope': contact.class_offering_id == null ? '' : String(contact.class_offering_id), 'data-is-blocked': contact.is_blocked ? '1' : '0' },
            }));
        }
        const messages = buildRenderableMessages();
        if (messages.length === 0) {
            renderEmpty(conversationBodyEl, '没有匹配的私信内容', '可以调整搜索关键词，或直接发送一条新消息。');
        } else if (lqEnabled) {
            const stream = document.createElement('div'); stream.className = 'lq-messages__stream';
            messages.forEach((message, index) => {
                const virtual = Boolean(message.is_virtual), isAssistant = !virtual && message.sender_role === 'assistant';
                const content = document.createElement('div');
                if (isAssistant && typeof globalThis.MarkdownRuntime?.parse === 'function') {
                    content.className = 'lq-prose'; content.innerHTML = globalThis.MarkdownRuntime.parse(message.content || '');
                } else content.textContent = message.content || '';
                // This is the existing escaped attachment renderer and existing
                // authenticated lightbox bridge, not another HTML input path.
                const attachments = document.createElement('template'); attachments.innerHTML = renderMessageAttachments(message.attachments);
                const previous = messages[index - 1];
                const bubble = lqContent.createContent('bubble', {
                    author: message.sender_display_name || (message.is_outgoing ? '我' : '联系人'),
                    time: formatDate(message.created_at || '') || '时间未知',
                    side: message.is_outgoing ? 'outgoing' : 'incoming',
                    connected: Boolean(previous && message.sender_identity && previous.sender_identity === message.sender_identity && Boolean(previous.is_outgoing) === Boolean(message.is_outgoing) && !virtual),
                    attrs: { 'data-message-id': String(message.id ?? '') },
                }, { content: [content, attachments.content] });
                if (message.status_copy) {
                    const note = document.createElement('p'); note.className = 'lq-messages__status-copy'; note.textContent = message.status_copy;
                    bubble.querySelector('.lq-bubble__content').append(note);
                }
                if (virtual) bubble.dataset.tone = message.virtual_status === 'failed' ? 'danger' : 'info';
                if (!virtual && message.can_block_sender && !message.is_sender_blocked) {
                    bubble.append(lqPresentation.createComponent('button', { label: '拉黑发信人', variant: 'ghost', size: 'sm', attrs: { 'data-block-sender': message.sender_identity } }));
                }
                stream.append(bubble);
            });
            conversationBodyEl.replaceChildren(stream);
        } else {
            conversationBodyEl.innerHTML = `
                <div class="message-center-messages">
                    ${messages.map((message) => {
                        const isVirtual = Boolean(message.is_virtual);
                        const isAiReply = !isVirtual && message.sender_role === 'assistant';
                        const rawContent = message.content || '';
                        const contentHtml = isAiReply && typeof globalThis.MarkdownRuntime?.parse === 'function'
                            ? globalThis.MarkdownRuntime.parse(rawContent)
                            : escapeHtml(rawContent);
                        const contentClass = isAiReply
                            ? 'message-center-message__content md-content'
                            : 'message-center-message__content';
                        const articleClass = [
                            'message-center-message',
                            message.is_outgoing ? 'is-outgoing' : '',
                            isVirtual ? 'is-status-note' : '',
                            message.virtual_status === 'failed' ? 'is-failed' : '',
                        ].filter(Boolean).join(' ');
                        return `
                            <article class="${articleClass}">
                                <div class="message-center-message__meta">
                                    <strong>${escapeHtml(message.sender_display_name || '')}</strong>
                                    <span>${escapeHtml(formatDate(message.created_at || ''))}</span>
                                </div>
                                ${rawContent ? `<div class="${contentClass}">${contentHtml}</div>` : ''}
                                ${renderMessageAttachments(message.attachments)}
                                ${message.status_copy ? `<div class="message-center-message__status">${escapeHtml(message.status_copy)}</div>` : ''}
                                ${!isVirtual && message.can_block_sender && !message.is_sender_blocked ? `
                                    <div class="message-center-message__actions">
                                        <button
                                            type="button"
                                            class="btn btn-ghost btn-sm"
                                            data-block-sender="${escapeHtml(message.sender_identity)}"
                                        >
                                            拉黑发信人
                                        </button>
                                    </div>
                                ` : ''}
                            </article>
                        `;
                    }).join('')}
                </div>
            `;
        }

        if (state.aiReplyPollError) {
            const notice = document.createElement('div');
            notice.className = 'message-center-empty__card';
            notice.dataset.aiPollError = '';
            notice.innerHTML = `<p role="status">${escapeHtml(state.aiReplyPollError)}</p><button type="button" class="btn btn-outline" data-ai-poll-retry>刷新会话</button>`;
            notice.querySelector('button').addEventListener('click', () => { void loadConversation(state.currentContact, state.currentScope, { showLoading: false }); });
            conversationBodyEl.prepend(notice);
        }
        const canSend = Boolean(contact.can_send);
        composeInputEl.disabled = !canSend;
        if (emojiTriggerEl) {
            emojiTriggerEl.disabled = !canSend;
        }
        setAttachmentButtonsDisabled(!canSend);
        composeInputEl.placeholder = canSend
            ? (isCurrentConversationAiPending() ? 'AI 助教正在回复上一条消息，你可以先整理下一条内容' : `发送给 ${contact.display_name || '联系人'}${contact.role === 'assistant' ? '，可仅发送图片（最多 8 张）' : ''}`)
            : (contact.is_blocked ? '对方已在黑名单中，解除后才能发送' : '当前无法向该联系人发送消息');
        if (composeHintEl) composeHintEl.textContent = canSend
            ? (isCurrentConversationAiPending() ? 'AI 助教正在回复，你可以继续整理下一条草稿。' : 'Enter 换行，点击发送提交。图片和附件保存在当前会话草稿中。')
            : (contact.is_blocked ? '对方已在黑名单中，解除后才能发送。' : '当前会话只可查看，暂不能发送新消息。');
        formPresentation?.refresh();

        updateSendButtonState();
        publishWorkspaceSnapshot();
    }

    function updateActionButtons() {
        const busy = Boolean(state.actionLease);
        app.querySelectorAll('#message-center-mark-read, [data-mark-notification], [data-unblock], [data-toggle-block], [data-block-sender]').forEach((button) => {
            button.disabled = busy;
            button.setAttribute('aria-busy', String(busy));
        });
        app.querySelectorAll('[data-open-notification]').forEach((link) => {
            if (busy) link.setAttribute('aria-disabled', 'true');
            else link.removeAttribute('aria-disabled');
        });
    }

    function actionViewKey() {
        return JSON.stringify([state.currentTab, state.currentContact, state.currentScope,
            state.keyword, state.filterKey, state.contactKeyword]);
    }

    function validMutationSummary(response) {
        return response?.status === 'success' && Array.isArray(response.summary?.tabs);
    }

    async function runMessageMutation({ url, config, validate, successMessage, href = '' }) {
        if (!state.alive || state.actionLease) return false;
        const lease = { href, epoch: state.viewEpoch, view: actionViewKey(), navigation: ++state.navigationIntent };
        state.actionLease = lease;
        updateActionButtons();
        try {
            const response = await apiFetch(url, { ...config, silent: true });
            if (!state.alive || state.actionLease !== lease) return false;
            if (!validMutationSummary(response) || !validate(response)) {
                throw new Error('未取得有效的操作回执，状态尚未确认，请刷新后检查。');
            }
            if (href) {
                if (lease.navigation !== state.navigationIntent || lease.epoch !== state.viewEpoch
                    || lease.view !== actionViewKey()) return false;
                // The native open URL retains the server's ownership check. A
                // slow optional bell refresh must not hold the actual action.
                try {
                    if (typeof window.refreshMessageCenterBell === 'function') {
                        void Promise.resolve(window.refreshMessageCenterBell({ allowPopup: false })).catch(() => {});
                    }
                } catch { /* The auxiliary bell never owns navigation. */ }
                window.location.href = href;
                return true;
            }
            // Mutation replies contain full metadata snapshots. Revalidate via
            // the existing current-view owner instead of applying a stale list
            // or restoring the mutation target's old classroom scope.
            const refreshRequest = state.bootstrapRequest + 1;
            const refreshed = await bootstrap({ focusComposer: false });
            if (!state.alive || state.actionLease !== lease) return false;
            if (refreshed) showToast(successMessage, 'success');
            else if (state.bootstrapRequest === refreshRequest && currentLoadState().status === 'error') {
                showToast(`${successMessage}，但刷新失败，请重试。`, 'warning');
            }
            return true;
        } catch (error) {
            if (state.alive && state.actionLease === lease && !error.suppressToast) {
                showToast(error.message || '操作失败，请稍后重试。', 'error');
            }
            return false;
        } finally {
            if (state.actionLease === lease) {
                state.actionLease = null;
                if (state.alive) updateActionButtons();
            }
        }
    }

    function markRead(payload, { successMessage = '该通知已标记为已读', href = '' } = {}) {
        return runMessageMutation({
            url: '/api/message-center/read',
            config: { method: 'POST', body: { ...payload, include_private: !isNotificationsMode } },
            validate: (response) => Number.isSafeInteger(response.updated_count) && response.updated_count >= 0,
            successMessage,
            href,
        });
    }

    async function loadItems() {
        const request = ++state.itemsRequest;
        state.viewEpoch++;
        const queryKey = JSON.stringify([state.currentTab, state.keyword, state.filterKey]);
        const owns = () => state.alive && request === state.itemsRequest && state.currentTab !== PRIVATE_TAB
            && queryKey === JSON.stringify([state.currentTab, state.keyword, state.filterKey]);
        clearAiReplyPolling();
        privatePanelEl.hidden = true;
        feedEl.hidden = false;
        if (!state.metadataReady) {
            if (state.bootstrapStatus === 'error') renderLoadFailure(feedEl, '信息中心加载失败', state.bootstrapError, () => { void bootstrap(); });
            else setLoading(feedEl, '正在加载消息分类...');
            publishWorkspaceSnapshot();
            return false;
        }
        state.bootstrapStatus = 'ready'; state.bootstrapError = '';
        state.itemsStatus = 'loading'; state.itemsError = '';
        setLoading(feedEl, '正在加载消息列表...');
        publishWorkspaceSnapshot();
        const params = new URLSearchParams({
            category: state.currentTab,
            keyword: state.keyword,
            filter: state.filterKey,
        });
        appendModeParams(params);
        try {
            const response = await apiFetch(`/api/message-center/items?${params.toString()}`, { silent: true });
            if (!owns()) return false;
            if (!Array.isArray(response?.items)) throw new Error('未取得完整的消息列表，请重试。');
            state.items = response.items;
            state.itemsStatus = 'ready';
            renderItems();
            publishWorkspaceSnapshot();
            return true;
        } catch (error) {
            if (!owns()) return false;
            state.itemsStatus = 'error'; state.itemsError = error.message || '请稍后重试。';
            renderLoadFailure(feedEl, '消息列表加载失败', state.itemsError, () => { void loadItems(); });
            publishWorkspaceSnapshot();
            return false;
        }
    }

    async function loadConversation(contactIdentity, scope, options = {}) {
        const { showLoading = true, scrollToBottom = true, focusComposer = false } = options;
        const request = ++state.conversationRequest;
        state.viewEpoch++;
        if (state.metadataReady) { state.bootstrapStatus = 'ready'; state.bootstrapError = ''; }
        const key = conversationViewKey(contactIdentity, scope);
        activateDraft(key);
        const owns = () => state.alive && request === state.conversationRequest
            && state.currentTab === PRIVATE_TAB && key === conversationViewKey();
        clearAiReplyPolling();
        state.currentContact = contactIdentity;
        state.currentScope = normalizeScope(scope);
        state.conversationStatus = 'loading'; state.conversationError = '';
        if (state.loadedConversationKey !== key) { state.conversation = null; state.aiReplyJob = null; }
        if (showLoading) {
            renderConversation();
        } else {
            updateSendButtonState();
        }
        updateUrl();
        if (!state.metadataReady) return false;
        const params = new URLSearchParams({
            contact: contactIdentity,
            limit: '150',
        });
        if (state.currentScope != null) {
            params.set('scope', String(state.currentScope));
        }
        try {
            const response = await apiFetch(`/api/message-center/private/conversation?${params.toString()}`, { silent: true });
            if (!owns()) return false;
            if (!response?.conversation?.contact || !Array.isArray(response.conversation.messages)
                || response.conversation.contact.identity !== contactIdentity) throw new Error('未取得完整的会话信息，请重试。');
            state.conversation = response.conversation;
            state.loadedConversationKey = key;
            state.conversationStatus = 'ready';
            syncAiReplyJob(state.conversation?.ai_reply_job || null);
            applySummary(response.summary || state.summary);
            if (state.conversation?.contact) syncContact(state.conversation.contact);
            renderContacts();
            renderConversation();
            if (scrollToBottom && !state.keyword.trim()) scrollConversationToBottom();
            if (focusComposer && !composeInputEl.disabled) composeInputEl.focus();
            updateHeroStats();
            updateUrl();
            return true;
        } catch (error) {
            if (!owns()) return false;
            state.conversationStatus = 'error';
            state.conversationError = error.status === 404 ? '会话当前不可用，请重新选择联系人或重试。'
                : error.message || '暂时无法加载会话，请重试。';
            renderConversation();
            return false;
        }
    }

    async function setTab(tab) {
        state.viewEpoch++; state.conversationRequest++; state.itemsRequest++;
        if (state.metadataReady) { state.bootstrapStatus = 'ready'; state.bootstrapError = ''; }
        state.currentTab = tab;
        renderTabs();
        updateHeroStats();
        updateUrl();

        if (state.currentTab === PRIVATE_TAB) {
            privatePanelEl.hidden = false;
            feedEl.hidden = true;
            renderContacts();
            renderBlocks();

            if (state.currentContact) {
                await loadConversation(state.currentContact, state.currentScope);
                return;
            }

            const nextContact = getFirstVisibleContact();
            if (nextContact) {
                await loadConversation(nextContact.identity, nextContact.class_offering_id);
                return;
            }

            state.conversation = null;
            state.conversationStatus = 'idle';
            state.aiReplyJob = null;
            renderConversation();
            publishWorkspaceSnapshot();
            return;
        }

        clearAiReplyPolling();
        state.currentContact = '';
        activateDraft('');
        state.currentScope = null;
        state.conversation = null;
        state.conversationStatus = 'idle';
        state.aiReplyJob = null;
        await loadItems();
    }

    async function markCurrentRead() {
        if (!state.alive || state.actionLease) return;
        if (state.currentTab === PRIVATE_TAB) {
            if (!state.currentContact) {
                showToast('当前没有打开私信会话', 'warning');
                return;
            }
            const lease = {};
            state.actionLease = lease;
            updateActionButtons();
            try {
                if (await loadConversation(state.currentContact, state.currentScope, { showLoading: false, scrollToBottom: false })) {
                    showToast('当前私信会话已更新为已读', 'success');
                }
            } catch (error) {
                if (state.alive && state.actionLease === lease && !error.suppressToast) {
                    showToast(error.message || '更新已读状态失败，请重试。', 'error');
                }
            } finally {
                if (state.actionLease === lease) {
                    state.actionLease = null;
                    if (state.alive) updateActionButtons();
                }
            }
            return;
        }

        const category = state.currentTab;
        const label = currentTabConfig().label || '所选分类';
        await markRead({ category }, { successMessage: `已将“${label}”标记为已读` });
    }

    async function handleWorkspaceCommand(event) {
        const detail = event instanceof CustomEvent ? event.detail : {};
        const type = String(detail?.type || '');
        if (type === 'set-tab') {
            const tab = String(detail.tab || 'all');
            if (tab === PRIVATE_TAB && isNotificationsMode) {
                window.location.href = '/profile?section=private&tab=private_message#profile-message-center';
                return;
            }
            await setTab(tab);
            return;
        }
        if (type === 'refresh') {
            if (await bootstrap()) showToast('信息中心已刷新', 'success');
            return;
        }
        if (type === 'mark-read') {
            await markCurrentRead();
            return;
        }
        if (type === 'focus-search') {
            searchEl?.focus();
            return;
        }
        if (type === 'focus-composer' && !composeInputEl.disabled) {
            composeInputEl.focus();
        }
    }

    async function toggleBlock(identity, scope, isBlocked) {
        if (!identity) {
            return;
        }

        const requestConfig = isBlocked
            ? { method: 'DELETE', silent: true }
            : {
                method: 'POST',
                body: {
                    contact_identity: identity,
                    class_offering_id: normalizeScope(scope),
                },
                silent: true,
            };

        const url = isBlocked
            ? `/api/message-center/private/blocks?contact_identity=${encodeURIComponent(identity)}`
            : '/api/message-center/private/blocks';

        const name = state.contacts.find((contact) => contact.identity === identity)?.display_name
            || state.blocks.find((block) => block.identity === identity)?.display_name || '该联系人';
        return runMessageMutation({
            url,
            config: requestConfig,
            validate: (response) => Array.isArray(response.blocks) && Array.isArray(response.contacts)
                && (isBlocked ? Number.isSafeInteger(response.removed_count) && response.removed_count >= 0
                    : response.block?.identity === identity),
            successMessage: isBlocked ? `已将“${name}”移出黑名单` : `已将“${name}”加入黑名单`,
        });
    }

    async function sendMessage(event) {
        event.preventDefault();

        // The lq send button is never natively `disabled` (see
        // setSubmitButtonVisualState) so it stays keyboard/AT reachable;
        // this reads the same aria-disabled attribute back as an explicit,
        // self-contained guard that does not depend on the CSS pointer-events
        // trick or on the disabled-derivation logic below staying in sync.
        if (lqEnabled && composeSubmitButtonEl?.getAttribute('aria-disabled') === 'true') {
            return;
        }

        if (!conversationReady() || !state.conversation.contact.can_send) {
            showToast('请先打开可发送的会话，加载失败时可重试。', 'warning');
            return;
        }

        if (state.isSendingMessage || isCurrentConversationAiPending()) {
            showToast('AI 助教正在回复上一条消息，请稍候', 'warning');
            return;
        }

        if (isSendCooldownActive()) {
            const remaining = Math.ceil(getSendCooldownRemainingMs() / 1000);
            showToast(`发送太频繁，请 ${remaining} 秒后再发`, 'warning');
            return;
        }

        if (!state.currentContact) {
            showToast('请先选择联系人', 'warning');
            return;
        }

        const content = composeInputEl.value.trim();
        if (!content && !state.pendingAttachments.length) {
            showToast('请输入私信内容或添加附件', 'warning');
            return;
        }

        const draft = saveActiveDraft();
        const sent = {
            key: conversationViewKey(), epoch: state.viewEpoch,
            identity: state.currentContact, scope: state.currentScope,
            name: state.conversation.contact.display_name || '联系人',
            rawText: composeInputEl.value, textRevision: draft.textRevision,
            attachments: [...state.pendingAttachments],
            attachmentIds: new Set(state.pendingAttachments.map((attachment) => attachment.id)),
        };

        state.isSendingMessage = true;
        updateSendButtonState();

        try {
            let requestBody;
            if (sent.attachments.length) {
                requestBody = new FormData();
                requestBody.append('contact_identity', sent.identity);
                if (sent.scope != null) {
                    requestBody.append('class_offering_id', String(sent.scope));
                }
                requestBody.append('content', content);
                sent.attachments.forEach((attachment) => {
                    requestBody.append('attachments', attachment.file, attachment.file.name || 'attachment');
                });
            } else {
                requestBody = {
                    contact_identity: sent.identity,
                    class_offering_id: sent.scope,
                    content,
                };
            }

            const response = await apiFetch('/api/message-center/private/messages', {
                method: 'POST',
                body: requestBody,
                silent: true,
            });

            if (!state.alive) return;
            if (!response?.sent_message?.id || response.contact?.identity !== sent.identity || !response.conversation_key) {
                throw new Error('未取得有效的发送回执');
            }
            settleSentDraft(sent);
            const sameView = conversationReady() && sent.key === conversationViewKey();
            if (sameView && emojiPicker?.isOpen()) {
                emojiPicker.close();
            }

            activateSendCooldown(12);
            if (sent.epoch === state.viewEpoch) {
                state.contacts = response.contacts || state.contacts;
                sortContactsInPlace();
                applySummary(response.summary || state.summary);
            }
            if (!sameView) {
                // A concurrent refresh may have queried before this POST was
                // committed. Replace its request only if this view is still A.
                if (state.currentTab === PRIVATE_TAB && sent.key === conversationViewKey()) {
                    void loadConversation(sent.identity, sent.scope, { showLoading: false });
                }
                showToast(`发给${sent.name}的私信已发送`, 'success');
                return;
            }

            if (response.contact) {
                if (state.conversation) {
                    state.conversation = {
                        ...state.conversation,
                        contact: {
                            ...state.conversation.contact,
                            ...response.contact,
                        },
                    };
                }
                syncContact(response.contact);
            }

            if (!state.conversation) {
                state.conversation = {
                    contact: response.contact || getActiveContact(),
                    conversation_key: response.conversation_key || state.aiReplyJob?.conversation_key || '',
                    class_offering_id: state.currentScope,
                    messages: [],
                };
            }

            if (response.sent_message) {
                appendMessageToConversation(response.sent_message, { shouldRender: false });
            }

            syncAiReplyJob(response.ai_reply_job || null);
            renderContacts();
            renderConversation();
            scrollConversationToBottom();
            showToast(response.ai_reply_job ? '私信已发送，AI 助教正在回复' : '私信已发送', 'success');
        } catch (error) {
            if (!state.alive) return;
            const retryAfterSeconds = Number(
                error.details?.retry_after_seconds
                || error.data?.retry_after_seconds
                || error.data?.detail?.retry_after_seconds
                || 0
            );
            if (error.code === 'rate_limited' && retryAfterSeconds > 0) {
                activateSendCooldown(retryAfterSeconds);
                showToast(`发送太频繁，请 ${retryAfterSeconds} 秒后再发`, 'warning');
            } else {
                showToast(error.status ? (error.message || '发送失败，草稿已保留')
                    : '发送状态尚未确认，草稿已保留，请查看会话后再决定是否重发。', 'error');
            }
        } finally {
            state.isSendingMessage = false;
            if (state.alive) updateSendButtonState();
        }
    }

    function handleSearchInput() {
        state.keyword = searchEl.value.trim();
        publishWorkspaceSnapshot();
        window.clearTimeout(state.searchTimer);
        state.searchTimer = window.setTimeout(async () => {
            if (state.currentTab === PRIVATE_TAB) {
                renderConversation();
                return;
            }
            await loadItems();
        }, 220);
    }

    function handleContactSearchInput() {
        state.contactKeyword = contactSearchEl.value.trim();
        renderContacts();
    }

    async function bootstrap({ focusComposer = true } = {}) {
        const request = ++state.bootstrapRequest;
        state.conversationRequest++; state.itemsRequest++;
        const epoch = state.viewEpoch;
        const initializing = !state.metadataReady;
        // The first metadata reply must initialize categories/contacts even if
        // a search was typed meanwhile. It then loads the latest UI selection.
        // Later refreshes may not take over a view chosen after they started.
        const owns = () => state.alive && request === state.bootstrapRequest && (initializing || epoch === state.viewEpoch);
        state.bootstrapStatus = 'loading'; state.bootstrapError = '';
        if (state.currentTab === PRIVATE_TAB) {
            privatePanelEl.hidden = false; feedEl.hidden = true;
            setLoading(conversationBodyEl, '正在加载联系人与私信...');
        } else setLoading(feedEl, '正在加载消息列表...');
        publishWorkspaceSnapshot();
        updateSendButtonState();
        try {
            const params = appendModeParams(new URLSearchParams(), { includePrivateData: !isNotificationsMode });
            const query = params.toString();
            const response = await apiFetch(`/api/message-center/bootstrap${query ? `?${query}` : ''}`, { silent: true });
            if (!owns()) return false;
            if (!response?.summary || !Array.isArray(response.summary.tabs)) throw new Error('未取得完整的信息中心数据，请重试。');
            state.metadataReady = true;
            state.bootstrapStatus = 'ready';
            state.contacts = response.private_contacts || [];
            sortContactsInPlace();
            state.blocks = response.private_blocks || [];
            applySummary(response.summary || state.summary);
            renderBlocks();

            if (state.currentTab === PRIVATE_TAB) {
                privatePanelEl.hidden = false;
                feedEl.hidden = true;
                renderContacts();

                if (state.currentContact) {
                    return await loadConversation(state.currentContact, state.currentScope, { focusComposer });
                }

                const nextContact = getFirstVisibleContact();
                if (nextContact) {
                    return await loadConversation(nextContact.identity, nextContact.class_offering_id);
                } else {
                    state.conversation = null;
                    state.conversationStatus = 'idle';
                    state.aiReplyJob = null;
                    renderConversation();
                }
                return true;
            }

            return await loadItems();
        } catch (error) {
            if (!owns()) return false;
            state.bootstrapStatus = 'error'; state.bootstrapError = error.message || '请稍后重试。';
            if (state.currentTab === PRIVATE_TAB) renderConversation();
            else renderLoadFailure(feedEl, '信息中心加载失败', state.bootstrapError, () => { void bootstrap(); });
            publishWorkspaceSnapshot();
            return false;
        }
    }

    tabsEl.addEventListener('click', async (event) => {
        const toggle = event.target.closest('[data-tabs-toggle]');
        if (toggle) {
            state.tabsExpanded = !state.tabsExpanded;
            renderTabs();
            return;
        }
        const button = event.target.closest('[data-tab]');
        if (!button) {
            return;
        }
        await setTab(button.dataset.tab || 'all');
    });

    filterEl.addEventListener('change', async () => {
        state.filterKey = filterEl.value || 'all';
        if (state.currentTab === PRIVATE_TAB) {
            renderContacts();
            renderConversation();
            return;
        }
        await loadItems();
    });

    searchEl.addEventListener('input', handleSearchInput);
    contactSearchEl.addEventListener('input', handleContactSearchInput);

    markReadEl.addEventListener('click', markCurrentRead);
    window.addEventListener(WORKSPACE_COMMAND_EVENT, handleWorkspaceCommand);

    feedEl.addEventListener('click', async (event) => {
        const markButton = event.target.closest('[data-mark-notification]');
        if (markButton) {
            await markRead({ notification_ids: [Number(markButton.dataset.markNotification)] });
            return;
        }

        const link = event.target.closest('[data-open-notification]');
        if (!link) {
            return;
        }

        if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        const href = link.getAttribute('href') || '/message-center';
        if (state.actionLease) {
            if (state.actionLease.href !== href) state.navigationIntent++;
            return;
        }
        await markRead({ notification_ids: [Number(link.dataset.openNotification)] }, { href });
    });

    contactSelectEl.addEventListener('change', async () => {
        const selectedOption = contactSelectEl.selectedOptions[0];
        if (!selectedOption?.dataset.contact) {
            return;
        }
        await loadConversation(selectedOption.dataset.contact, selectedOption.dataset.scope);
    });
    recentContactsEl?.addEventListener('click', event => {
        const button = event.target.closest('[data-select-contact]');
        if (!button) return;
        contactSelectEl.value = button.dataset.selectContact;
        contactSelectEl.dispatchEvent(new Event('change', { bubbles: true }));
    });

    blockListEl.addEventListener('click', async (event) => {
        const unblockButton = event.target.closest('[data-unblock]');
        if (!unblockButton) {
            return;
        }
        await toggleBlock(unblockButton.dataset.unblock, null, true);
    });

    conversationHeaderEl.addEventListener('click', async (event) => {
        const toggleButton = event.target.closest('[data-toggle-block]');
        if (!toggleButton) {
            return;
        }
        await toggleBlock(
            toggleButton.dataset.toggleBlock,
            toggleButton.dataset.toggleScope,
            toggleButton.dataset.isBlocked === '1',
        );
    });

    conversationBodyEl.addEventListener('click', async (event) => {
        const imageButton = event.target.closest('[data-private-image-preview-key]');
        if (imageButton) {
            event.preventDefault();
            const siblings = Array.from(imageButton.parentElement?.querySelectorAll('[data-private-image-preview-key]') || [])
                .map((node) => state.attachmentPreviewItems.get(node.dataset.privateImagePreviewKey))
                .filter(Boolean);
            openAttachmentPreview(state.attachmentPreviewItems.get(imageButton.dataset.privateImagePreviewKey), siblings);
            return;
        }

        const blockButton = event.target.closest('[data-block-sender]');
        if (!blockButton) {
            return;
        }
        await toggleBlock(blockButton.dataset.blockSender, state.currentScope, false);
    });

    imageTriggerEl?.addEventListener('click', () => imageInputEl?.click());
    fileTriggerEl?.addEventListener('click', () => fileInputEl?.click());
    imageInputEl?.addEventListener('change', (event) => {
        queuePrivateAttachments(event.currentTarget?.files || [], { imagesOnly: true });
        if (event.currentTarget) {
            event.currentTarget.value = '';
        }
    });
    fileInputEl?.addEventListener('change', (event) => {
        queuePrivateAttachments(event.currentTarget?.files || []);
        if (event.currentTarget) {
            event.currentTarget.value = '';
        }
    });
    composeInputEl.addEventListener('paste', (event) => {
        const files = Array.from(event.clipboardData?.files || []);
        if (files.length) {
            queuePrivateAttachments(files);
        }
    });
    composeFormEl.addEventListener('dragover', (event) => {
        if (Array.from(event.dataTransfer?.types || []).includes('Files')) {
            event.preventDefault();
            composeFormEl.classList.add('is-dragover');
        }
    });
    composeFormEl.addEventListener('dragleave', (event) => {
        if (!composeFormEl.contains(event.relatedTarget)) {
            composeFormEl.classList.remove('is-dragover');
        }
    });
    composeFormEl.addEventListener('drop', (event) => {
        const files = Array.from(event.dataTransfer?.files || []);
        if (!files.length) {
            return;
        }
        event.preventDefault();
        composeFormEl.classList.remove('is-dragover');
        queuePrivateAttachments(files);
    });
    composeFormEl.addEventListener('submit', sendMessage);
    composeInputEl.addEventListener('input', saveActiveDraft);
    window.addEventListener('pagehide', (event) => {
        if (event.persisted) return;
        state.alive = false;
        formPresentation?.dispose();
        state.actionLease = null;
        state.navigationIntent++;
        window.clearTimeout(state.searchTimer);
        window.clearTimeout(state.sendRateLimitTimer);
        clearAiReplyPolling();
        saveActiveDraft();
        for (const draft of state.drafts.values()) draft.attachments.forEach(revokeAttachmentPreview);
        state.drafts.clear(); state.pendingAttachments = [];
        window.removeEventListener(WORKSPACE_COMMAND_EVENT, handleWorkspaceCommand);
    });

    initEmojiPicker();
    initEditorToolbar();
    updateSendButtonState();
    publishWorkspaceSnapshot();
    bootstrap();
}
