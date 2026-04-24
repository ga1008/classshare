import { apiFetch } from './api.js';
import { escapeHtml, formatDate, showToast } from './ui.js';
import { createEmojiPicker } from './emoji_picker.js';

const app = document.querySelector('[data-message-center-app]');

if (app) {
    const PRIVATE_TAB = 'private_message';
    const AI_JOB_POLL_INTERVAL_MS = 2200;
    const ACTIVE_AI_JOB_STATUSES = new Set(['pending', 'running']);
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
    const composeSubmitButtonEl = composeFormEl?.querySelector('[data-send-button]');
    const composeSubmitLabelEl = composeSubmitButtonEl?.querySelector('.message-center-compose-submit__label');
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
        searchTimer: null,
        lastSendAt: 0,
        sendCooldownMs: 12000,
        sendRateLimitTimer: null,
        aiReplyPollTimer: null,
        aiReplyPollInFlight: false,
        isSendingMessage: false,
    };

    let emojiPicker = null;

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
            return state.conversation?.contact || null;
        }
        return state.contacts.find((contact) => (
            contact.identity === state.currentContact
            && normalizeScope(contact.class_offering_id) === normalizeScope(state.currentScope)
        )) || state.conversation?.contact || null;
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

    function setSubmitButtonVisualState({ label = '发送', disabled = false, busy = false, title = '' } = {}) {
        if (!composeSubmitButtonEl) {
            return;
        }
        composeSubmitButtonEl.disabled = Boolean(disabled);
        composeSubmitButtonEl.classList.toggle('is-busy', Boolean(busy));
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

    function updateSendButtonState() {
        const contact = state.conversation?.contact;
        const canSend = Boolean(contact?.can_send);

        if (!contact || !canSend) {
            setSubmitButtonVisualState({ label: '发送', disabled: true });
            return;
        }

        if (state.isSendingMessage) {
            setSubmitButtonVisualState({ label: '发送中', disabled: true, busy: true, title: '正在发送私信' });
            return;
        }

        if (isCurrentConversationAiPending()) {
            setSubmitButtonVisualState({ label: 'AI 回复中', disabled: true, busy: true, title: 'AI 助教正在回复上一条消息' });
            return;
        }

        if (isSendCooldownActive()) {
            const remaining = Math.ceil(getSendCooldownRemainingMs() / 1000);
            setSubmitButtonVisualState({ label: `${remaining}s 后可发送`, disabled: true });
            return;
        }

        setSubmitButtonVisualState({ label: '发送' });
    }

    function activateSendCooldown(retryAfterSeconds) {
        const safeSeconds = Math.max(Number(retryAfterSeconds || 12), 1);
        state.sendCooldownMs = safeSeconds * 1000;
        state.lastSendAt = Date.now();

        if (state.sendRateLimitTimer) {
            window.clearTimeout(state.sendRateLimitTimer);
        }

        updateSendButtonState();
        state.sendRateLimitTimer = window.setTimeout(() => {
            state.sendRateLimitTimer = null;
            state.sendCooldownMs = 12000;
            updateSendButtonState();
        }, safeSeconds * 1000);
    }

    function clearAiReplyPolling() {
        if (state.aiReplyPollTimer) {
            window.clearTimeout(state.aiReplyPollTimer);
            state.aiReplyPollTimer = null;
        }
        state.aiReplyPollInFlight = false;
    }

    function syncAiReplyJob(job) {
        state.aiReplyJob = job || null;
        clearAiReplyPolling();
        if (state.currentTab === PRIVATE_TAB && isActiveAiReplyJob(job)) {
            state.aiReplyPollTimer = window.setTimeout(() => {
                void pollAiReplyJobStatus();
            }, AI_JOB_POLL_INTERVAL_MS);
        }
        updateSendButtonState();
    }

    async function pollAiReplyJobStatus() {
        if (state.aiReplyPollInFlight || state.currentTab !== PRIVATE_TAB || !state.aiReplyJob?.id || !isActiveAiReplyJob(state.aiReplyJob)) {
            return;
        }

        state.aiReplyPollInFlight = true;
        const jobId = Number(state.aiReplyJob.id);
        const contactIdentity = state.currentContact;
        const scope = state.currentScope;

        try {
            const response = await apiFetch(`/api/message-center/private/ai-jobs/${jobId}`, { silent: true });
            const job = response.job || null;
            if (!job || Number(job.id) !== jobId) {
                state.aiReplyJob = null;
                renderConversation();
                return;
            }

            state.aiReplyJob = job;
            if (job.status === 'completed') {
                state.aiReplyJob = null;
                updateSendButtonState();
                if (state.currentTab === PRIVATE_TAB && state.currentContact === contactIdentity && normalizeScope(state.currentScope) === normalizeScope(scope)) {
                    await loadConversation(contactIdentity, scope, { showLoading: false, scrollToBottom: true });
                    showToast('AI 助教已回复', 'success');
                }
                return;
            }

            renderConversation();
            if (job.status === 'failed') {
                showToast('AI 助教这次没有成功回复，你可以稍后再试。', 'warning');
                return;
            }

            syncAiReplyJob(job);
        } catch (error) {
            if (isActiveAiReplyJob(state.aiReplyJob)) {
                syncAiReplyJob(state.aiReplyJob);
            }
        } finally {
            state.aiReplyPollInFlight = false;
        }
    }

    function initEmojiPicker() {
        const emojiAnchor = document.getElementById('message-center-emoji-anchor');
        if (!emojiTriggerEl || !composeInputEl) {
            return;
        }

        emojiPicker = createEmojiPicker({ targetInput: composeInputEl });
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
        tabsEl.innerHTML = (state.summary.tabs || []).map((tab) => `
            <button
                type="button"
                class="message-center-tab ${tab.category === state.currentTab ? 'is-active' : ''}"
                data-tab="${escapeHtml(tab.category)}"
            >
                <span>${escapeHtml(tab.label)}</span>
                <span class="message-center-tab__count">${Number(tab.unread_count || 0)}</span>
            </button>
        `).join('');
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
        container.innerHTML = `
            <div class="message-center-empty">
                <div class="message-center-empty__card">
                    <div class="spinner mx-auto"></div>
                    <p>${escapeHtml(text)}</p>
                </div>
            </div>
        `;
    }

    function formatNotificationAction(item) {
        const actionMap = {
            assignment: '查看作业',
            submission: '查看提交',
            discussion_mention: '进入课堂',
            grading_result: '查看批改结果',
            ai_feedback: '查看 AI 反馈',
            private_message: '查看私信',
        };
        return actionMap[item.category] || '查看详情';
    }

    function renderItems() {
        if (!Array.isArray(state.items) || state.items.length === 0) {
            renderEmpty(feedEl, '当前没有匹配的信息', '可以切换分类、搜索关键词或调整筛选条件后再试。');
            return;
        }

        feedEl.innerHTML = state.items.map((item) => `
            <article class="message-center-card ${item.is_unread ? 'is-unread' : ''}">
                <div class="message-center-card__top">
                    <div class="message-center-card__category">
                        <span class="message-center-pill">${escapeHtml(item.category_label || item.category)}</span>
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
                    <a
                        href="${escapeHtml(item.link_url || '/message-center')}"
                        class="btn btn-primary btn-sm"
                        data-open-notification="${Number(item.id)}"
                    >
                        ${escapeHtml(formatNotificationAction(item))}
                    </a>
                </div>
            </article>
        `).join('');
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
    }

    function renderBlocks() {
        if (!Array.isArray(state.blocks) || state.blocks.length === 0) {
            blockListEl.innerHTML = '<div class="message-center-block-empty">当前没有拉黑任何联系人。</div>';
            blockCountEl.textContent = '0';
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
        state.contacts[existingIndex] = {
            ...state.contacts[existingIndex],
            unread_count: 0,
            last_message_preview: String(message.content || ''),
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
            updateSendButtonState();
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

        const messages = buildRenderableMessages();
        if (messages.length === 0) {
            renderEmpty(conversationBodyEl, '没有匹配的私信内容', '可以调整搜索关键词，或直接发送一条新消息。');
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
                                <div class="${contentClass}">${contentHtml}</div>
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

        const canSend = Boolean(contact.can_send);
        composeInputEl.disabled = !canSend;
        if (emojiTriggerEl) {
            emojiTriggerEl.disabled = !canSend;
        }
        composeInputEl.placeholder = canSend
            ? (isCurrentConversationAiPending() ? 'AI 助教正在回复上一条消息，你可以先整理下一条内容' : `发送给 ${contact.display_name || '联系人'}`)
            : (contact.is_blocked ? '对方已在黑名单中，解除后才能发送' : '当前无法向该联系人发送消息');

        updateSendButtonState();
    }

    async function markRead(payload) {
        const body = {
            ...payload,
            include_private: !isNotificationsMode,
        };
        const response = await apiFetch('/api/message-center/read', {
            method: 'POST',
            body,
        });
        applySummary(response.summary || state.summary);
        return response;
    }

    async function loadItems() {
        clearAiReplyPolling();
        privatePanelEl.hidden = true;
        feedEl.hidden = false;
        setLoading(feedEl, '正在加载消息列表...');
        const params = new URLSearchParams({
            category: state.currentTab,
            keyword: state.keyword,
            filter: state.filterKey,
        });
        appendModeParams(params);
        const response = await apiFetch(`/api/message-center/items?${params.toString()}`, { silent: true });
        state.items = response.items || [];
        renderItems();
    }

    async function loadConversation(contactIdentity, scope, options = {}) {
        const { showLoading = true, scrollToBottom = true, focusComposer = false } = options;
        clearAiReplyPolling();
        state.currentContact = contactIdentity;
        state.currentScope = normalizeScope(scope);
        if (showLoading) {
            setLoading(conversationBodyEl, '正在加载私信会话...');
        }
        const params = new URLSearchParams({
            contact: contactIdentity,
            limit: '150',
        });
        if (state.currentScope != null) {
            params.set('scope', String(state.currentScope));
        }
        const response = await apiFetch(`/api/message-center/private/conversation?${params.toString()}`, { silent: true });
        state.conversation = response.conversation || null;
        syncAiReplyJob(state.conversation?.ai_reply_job || null);
        applySummary(response.summary || state.summary);
        if (state.conversation?.contact) {
            syncContact(state.conversation.contact);
        }
        renderContacts();
        renderConversation();
        if (scrollToBottom && !state.keyword.trim()) {
            scrollConversationToBottom();
        }
        if (focusComposer && !composeInputEl.disabled) {
            composeInputEl.focus();
        }
        updateHeroStats();
        updateUrl();
    }

    async function setTab(tab) {
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
            state.aiReplyJob = null;
            renderConversation();
            return;
        }

        clearAiReplyPolling();
        state.currentContact = '';
        state.currentScope = null;
        state.conversation = null;
        state.aiReplyJob = null;
        await loadItems();
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

        const response = await apiFetch(url, requestConfig);
        state.blocks = response.blocks || [];
        state.contacts = response.contacts || [];
        sortContactsInPlace();
        applySummary(response.summary || state.summary);

        if (state.conversation?.contact?.identity === identity) {
            await loadConversation(identity, scope, { showLoading: false, scrollToBottom: false });
        } else {
            renderContacts();
            renderBlocks();
        }

        showToast(isBlocked ? '已解除黑名单' : '已加入黑名单', 'success');
    }

    async function sendMessage(event) {
        event.preventDefault();

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
        if (!content) {
            showToast('请输入私信内容', 'warning');
            return;
        }

        state.isSendingMessage = true;
        updateSendButtonState();

        try {
            const response = await apiFetch('/api/message-center/private/messages', {
                method: 'POST',
                body: {
                    contact_identity: state.currentContact,
                    class_offering_id: state.currentScope,
                    content,
                },
                silent: true,
            });

            composeInputEl.value = '';
            if (emojiPicker?.isOpen()) {
                emojiPicker.close();
            }

            activateSendCooldown(12);
            state.contacts = response.contacts || state.contacts;
            sortContactsInPlace();
            applySummary(response.summary || state.summary);

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
            if (error.status === 429 && error.data?.retry_after_seconds) {
                activateSendCooldown(error.data.retry_after_seconds);
                showToast(`发送太频繁，请 ${error.data.retry_after_seconds} 秒后再发`, 'warning');
            } else {
                showToast(error.message || '发送失败', 'error');
            }
        } finally {
            state.isSendingMessage = false;
            updateSendButtonState();
        }
    }

    function handleSearchInput() {
        state.keyword = searchEl.value.trim();
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

    async function bootstrap() {
        try {
            const params = appendModeParams(new URLSearchParams(), { includePrivateData: !isNotificationsMode });
            const query = params.toString();
            const response = await apiFetch(`/api/message-center/bootstrap${query ? `?${query}` : ''}`, { silent: true });
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
                    await loadConversation(state.currentContact, state.currentScope, { focusComposer: true });
                    return;
                }

                const nextContact = getFirstVisibleContact();
                if (nextContact) {
                    await loadConversation(nextContact.identity, nextContact.class_offering_id);
                } else {
                    state.conversation = null;
                    state.aiReplyJob = null;
                    renderConversation();
                }
                return;
            }

            await loadItems();
        } catch (error) {
            renderEmpty(feedEl, '信息中心加载失败', error.message || '请稍后刷新重试。');
        }
    }

    tabsEl.addEventListener('click', async (event) => {
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

    markReadEl.addEventListener('click', async () => {
        if (state.currentTab === PRIVATE_TAB) {
            if (!state.currentContact) {
                showToast('当前没有打开私信会话', 'warning');
                return;
            }
            await loadConversation(state.currentContact, state.currentScope, { showLoading: false, scrollToBottom: false });
            showToast('当前私信会话已更新为已读', 'success');
            return;
        }

        await markRead({ category: state.currentTab });
        await loadItems();
        showToast('当前分类已标记为已读', 'success');
    });

    feedEl.addEventListener('click', async (event) => {
        const markButton = event.target.closest('[data-mark-notification]');
        if (markButton) {
            await markRead({ notification_ids: [Number(markButton.dataset.markNotification)] });
            await loadItems();
            return;
        }

        const link = event.target.closest('[data-open-notification]');
        if (!link) {
            return;
        }

        event.preventDefault();
        await markRead({ notification_ids: [Number(link.dataset.openNotification)] });
        window.location.href = link.getAttribute('href') || '/message-center';
    });

    contactSelectEl.addEventListener('change', async () => {
        const selectedOption = contactSelectEl.selectedOptions[0];
        if (!selectedOption?.dataset.contact) {
            return;
        }
        await loadConversation(selectedOption.dataset.contact, selectedOption.dataset.scope);
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
        const blockButton = event.target.closest('[data-block-sender]');
        if (!blockButton) {
            return;
        }
        await toggleBlock(blockButton.dataset.blockSender, state.currentScope, false);
    });

    composeFormEl.addEventListener('submit', sendMessage);
    window.addEventListener('beforeunload', clearAiReplyPolling);

    initEmojiPicker();
    initEditorToolbar();
    updateSendButtonState();
    bootstrap();
}
