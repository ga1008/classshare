import { apiFetch } from './api.js';
import { escapeHtml, formatDate, showToast } from './ui.js';

const app = document.querySelector('[data-message-center-app]');

if (app) {
    const PRIVATE_TAB = 'private_message';
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
        currentTab: app.dataset.initialTab || 'all',
        currentContact: app.dataset.initialContact || '',
        currentScope: normalizeScope(app.dataset.initialScope),
        keyword: '',
        contactKeyword: '',
        filterKey: 'all',
        searchTimer: null,
    };

    function normalizeScope(value) {
        if (value === '' || value == null) {
            return null;
        }
        const numericValue = Number(value);
        return Number.isFinite(numericValue) ? numericValue : null;
    }

    function emitSummaryUpdate() {
        window.dispatchEvent(new CustomEvent('message-center:summary-updated', {
            detail: state.summary,
        }));
    }

    function buildContactKey(identity, scope) {
        return `${identity}|scope:${Number(scope || 0)}`;
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

    function renderTabs() {
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
            return;
        }
        state.contacts.unshift({
            ...contact,
            unread_count: 0,
        });
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

        const messages = filteredMessages();
        if (messages.length === 0) {
            renderEmpty(conversationBodyEl, '没有匹配的私信内容', '可以调整搜索关键词，或直接发送一条新消息。');
        } else {
            conversationBodyEl.innerHTML = `
                <div class="message-center-messages">
                    ${messages.map((message) => `
                        <article class="message-center-message ${message.is_outgoing ? 'is-outgoing' : ''}">
                            <div class="message-center-message__meta">
                                <strong>${escapeHtml(message.sender_display_name || '')}</strong>
                                <span>${escapeHtml(formatDate(message.created_at || ''))}</span>
                            </div>
                            <div class="message-center-message__content">${escapeHtml(message.content || '')}</div>
                            ${message.can_block_sender && !message.is_sender_blocked ? `
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
                    `).join('')}
                </div>
            `;
        }

        const canSend = Boolean(contact.can_send);
        composeInputEl.disabled = !canSend;
        composeInputEl.placeholder = canSend
            ? `发送给 ${contact.display_name || '联系人'}`
            : (contact.is_blocked ? '对方已在黑名单中，解除后才能发送' : '当前无法向该联系人发送消息');
    }

    async function markRead(payload) {
        const response = await apiFetch('/api/message-center/read', {
            method: 'POST',
            body: payload,
        });
        applySummary(response.summary || state.summary);
        return response;
    }

    async function loadItems() {
        privatePanelEl.hidden = true;
        feedEl.hidden = false;
        setLoading(feedEl, '正在加载消息列表...');
        const params = new URLSearchParams({
            category: state.currentTab,
            keyword: state.keyword,
            filter: state.filterKey,
        });
        const response = await apiFetch(`/api/message-center/items?${params.toString()}`, { silent: true });
        state.items = response.items || [];
        renderItems();
    }

    async function loadConversation(contactIdentity, scope) {
        state.currentContact = contactIdentity;
        state.currentScope = normalizeScope(scope);
        setLoading(conversationBodyEl, '正在加载私信会话...');
        const params = new URLSearchParams({
            contact: contactIdentity,
            limit: '150',
        });
        if (state.currentScope != null) {
            params.set('scope', String(state.currentScope));
        }
        const response = await apiFetch(`/api/message-center/private/conversation?${params.toString()}`, { silent: true });
        state.conversation = response.conversation || null;
        applySummary(response.summary || state.summary);
        if (state.conversation?.contact) {
            syncContact(state.conversation.contact);
        }
        renderContacts();
        renderConversation();
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
            renderConversation();
            return;
        }

        state.currentContact = '';
        state.currentScope = null;
        state.conversation = null;
        await loadItems();
    }

    async function toggleBlock(identity, scope, isBlocked) {
        if (!identity) {
            return;
        }

        const requestConfig = isBlocked
            ? {
                method: 'DELETE',
                silent: true,
            }
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
        applySummary(response.summary || state.summary);

        if (state.conversation?.contact?.identity === identity) {
            await loadConversation(identity, scope);
        } else {
            renderContacts();
            renderBlocks();
        }

        showToast(isBlocked ? '已解除黑名单' : '已加入黑名单', 'success');
    }

    async function sendMessage(event) {
        event.preventDefault();

        if (!state.currentContact) {
            showToast('请先选择联系人', 'warning');
            return;
        }

        const content = composeInputEl.value.trim();
        if (!content) {
            showToast('请输入私信内容', 'warning');
            return;
        }

        const submitButton = composeFormEl.querySelector('button[type="submit"]');
        submitButton.disabled = true;

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
            state.contacts = response.contacts || state.contacts;
            applySummary(response.summary || state.summary);
            renderContacts();
            await loadConversation(state.currentContact, state.currentScope);
            showToast(response.assistant_reply ? '私信已发送，AI 助教已自动回复' : '私信已发送', 'success');
        } catch (error) {
            showToast(error.message || '发送失败', 'error');
        } finally {
            submitButton.disabled = false;
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
            const response = await apiFetch('/api/message-center/bootstrap', { silent: true });
            state.contacts = response.private_contacts || [];
            state.blocks = response.private_blocks || [];
            applySummary(response.summary || state.summary);
            renderBlocks();

            if (state.currentTab === PRIVATE_TAB) {
                privatePanelEl.hidden = false;
                feedEl.hidden = true;
                renderContacts();

                if (state.currentContact) {
                    await loadConversation(state.currentContact, state.currentScope);
                    return;
                }

                const nextContact = getFirstVisibleContact();
                if (nextContact) {
                    await loadConversation(nextContact.identity, nextContact.class_offering_id);
                } else {
                    state.conversation = null;
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
            await loadConversation(state.currentContact, state.currentScope);
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

    bootstrap();
}
