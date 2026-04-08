import { apiFetch } from './api.js';
import {
    DEFAULT_FREQUENT_EMOJIS,
    EMOJI_CATEGORIES,
    UNICODE_EMOJI_MAP,
    getEmojiMeta,
    buildTwemojiUrl,
} from './chat_emoji_catalog.js';

const FALLBACK_EMOJI_SET_NOTE = '标准表情采用 Twemoji';
const DEFAULT_MAX_UPLOAD_BYTES = 5 * 1024 * 1024;
const DEFAULT_MAX_CUSTOM_EMOJIS = 60;
const MAX_FREQUENT_ITEMS = 8;
const DEFAULT_ALIAS_SWITCH_COOLDOWN_SECONDS = 10;
const DEFAULT_ALIAS_SWITCH_LIMIT = 6;
const DISCUSSION_ROOM_DESKTOP_BREAKPOINT = 1120;

const KNOWN_EMOJIS = Array.from(UNICODE_EMOJI_MAP.keys()).sort((left, right) => right.length - left.length);
const KNOWN_EMOJI_REGEX = KNOWN_EMOJIS.length
    ? new RegExp(KNOWN_EMOJIS.map((value) => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|'), 'gu')
    : null;

export class ClassroomChat {
    constructor(options) {
        this.classOfferingId = options.classOfferingId;
        this.messagesBox = document.getElementById(options.chatMessagesContainerId);
        this.chatInput = document.getElementById(options.chatInputId);
        this.chatForm = document.getElementById(options.chatFormId);
        this.statusIndicator = document.getElementById(options.statusIndicatorId);
        this.statusText = document.getElementById(options.statusTextId);
        this.displayNameEl = document.getElementById(options.displayNameId);
        this.aliasMetaEl = document.getElementById(options.aliasMetaId);
        this.switchAliasButton = document.getElementById(options.switchAliasButtonId);
        this.mentionAllButton = document.getElementById(options.mentionAllButtonId);
        this.historyLoader = document.getElementById(options.historyLoaderId);
        this.historyLoadButton = document.getElementById(options.historyLoadButtonId);

        this.emojiTriggerButton = document.getElementById(options.emojiTriggerButtonId);
        this.emojiPopover = document.getElementById(options.emojiPopoverId);
        this.emojiCloseButton = document.getElementById(options.emojiCloseButtonId);
        this.emojiFrequentRow = document.getElementById(options.emojiFrequentRowId);
        this.emojiCategoriesBox = document.getElementById(options.emojiCategoriesId);
        this.customEmojiGrid = document.getElementById(options.customEmojiGridId);
        this.customEmojiUploadButton = document.getElementById(options.customEmojiUploadButtonId);
        this.customEmojiFileInput = document.getElementById(options.customEmojiFileInputId);
        this.customEmojiUploadStatus = document.getElementById(options.customEmojiUploadStatusId);
        this.customEmojiProgress = document.getElementById(options.customEmojiProgressId);
        this.customEmojiProgressBar = document.getElementById(options.customEmojiProgressBarId);
        this.emojiPreviewRow = document.getElementById(options.emojiPreviewRowId);
        this.emojiSetNote = document.getElementById(options.emojiSetNoteId);
        this.currentUser = options.currentUser || {};
        this.discussionRoom = document.getElementById(options.discussionRoomId);
        this.workspaceContent = document.getElementById(options.workspaceContentId);

        this.ws = null;
        this.onFileEvent = null;
        this.displayName = null;
        this.oldestMessageId = null;
        this.hasMoreHistory = false;
        this.isLoadingHistory = false;
        this.knownMessageIds = new Set();
        this.aliasState = {
            availableAliasCount: 0,
            switchLimit: DEFAULT_ALIAS_SWITCH_LIMIT,
            switchesUsed: 0,
            switchesRemaining: DEFAULT_ALIAS_SWITCH_LIMIT,
            cooldownSeconds: DEFAULT_ALIAS_SWITCH_COOLDOWN_SECONDS,
            nextSwitchAvailableAt: null,
            blockReason: null,
        };

        this.selectedCustomEmojis = [];
        this.emojiPanelLoaded = false;
        this.emojiPanelData = {
            emoji_set: null,
            frequent: [],
            custom_emojis: [],
            limits: null,
        };
        this.uploadInFlight = false;
        this.refreshTimer = null;
        this.aliasCountdownTimer = null;
        this.roomHeightFrame = null;
        this.roomHeightObserver = null;

        this.handleDocumentPointerDown = this.handleDocumentPointerDown.bind(this);
        this.handleDocumentKeydown = this.handleDocumentKeydown.bind(this);
        this.scheduleDiscussionRoomResize = this.scheduleDiscussionRoomResize.bind(this);
    }

    init() {
        if (!this.messagesBox || !this.chatInput || !this.chatForm) {
            console.error('ClassroomChat: required DOM elements not found.');
            return;
        }

        this.renderEmojiCategories();
        this.renderFrequentRow();
        this.renderCustomEmojiGrid();
        this.renderSelectedCustomEmojis();
        this.updateUploadStatus('未上传', 'idle');
        this.updateEmojiSetNote();
        this.resizeInput();
        this.setupDiscussionRoomSizing();
        this.refreshAliasSwitchUi();

        const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        this.ws = new WebSocket(`${wsProtocol}//${window.location.host}/ws/${this.classOfferingId}`);

        this.ws.onmessage = this.handleMessage.bind(this);
        this.ws.onopen = () => {
            this.updateConnectionState(true);
            this.refreshAliasSwitchUi();
        };
        this.ws.onerror = (error) => {
            console.error('WebSocket error:', error);
            this.updateConnectionState(false, '连接异常');
            this.showToast('课堂研讨室连接出现错误', 'error');
        };
        this.ws.onclose = () => {
            this.updateConnectionState(false, '连接已断开');
            this.appendSystemMessage('连接已断开，请刷新页面后重试。');
            this.refreshAliasSwitchUi();
        };

        this.chatForm.addEventListener('submit', (event) => {
            event.preventDefault();
            this.sendMessage();
        });

        this.chatInput.addEventListener('keydown', (event) => {
            if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault();
                this.sendMessage();
            }
        });

        this.chatInput.addEventListener('input', () => this.resizeInput());

        this.messagesBox.addEventListener('scroll', () => this.updateHistoryLoader());
        this.switchAliasButton?.addEventListener('click', () => this.requestAliasSwitch());
        this.mentionAllButton?.addEventListener('click', () => this.insertMentionAll());
        this.historyLoadButton?.addEventListener('click', () => this.requestOlderHistory());
        this.emojiTriggerButton?.addEventListener('click', () => this.toggleEmojiPopover());
        this.emojiCloseButton?.addEventListener('click', () => this.closeEmojiPopover());
        this.customEmojiUploadButton?.addEventListener('click', () => this.customEmojiFileInput?.click());
        this.customEmojiFileInput?.addEventListener('change', (event) => {
            const input = event.currentTarget;
            const file = input?.files?.[0];
            if (file) {
                this.uploadCustomEmoji(file);
            }
            if (input) {
                input.value = '';
            }
        });

        document.addEventListener('pointerdown', this.handleDocumentPointerDown);
        document.addEventListener('keydown', this.handleDocumentKeydown);
        this.updateHistoryLoader();
    }

    updateConnectionState(isOnline, text = null) {
        if (this.statusIndicator) {
            this.statusIndicator.classList.toggle('status-online', isOnline);
        }
        if (this.statusText) {
            this.statusText.textContent = text || (isOnline ? '实时在线' : '连接异常');
        }
        if (this.statusIndicator) {
            this.statusIndicator.title = text || (isOnline ? '连接正常' : '连接异常');
        }
        this.refreshAliasSwitchUi();
    }

    setupDiscussionRoomSizing() {
        if (!this.discussionRoom || !this.workspaceContent) {
            return;
        }

        this.scheduleDiscussionRoomResize();
        window.addEventListener('resize', this.scheduleDiscussionRoomResize);

        if (window.ResizeObserver && !this.roomHeightObserver) {
            this.roomHeightObserver = new ResizeObserver(() => this.scheduleDiscussionRoomResize());
            this.roomHeightObserver.observe(this.workspaceContent);
        }
    }

    scheduleDiscussionRoomResize() {
        if (!this.discussionRoom || !this.workspaceContent || this.roomHeightFrame !== null) {
            return;
        }

        this.roomHeightFrame = window.requestAnimationFrame(() => {
            this.roomHeightFrame = null;
            this.syncDiscussionRoomHeight();
        });
    }

    syncDiscussionRoomHeight() {
        if (!this.discussionRoom || !this.workspaceContent) {
            return;
        }

        if (window.innerWidth <= DISCUSSION_ROOM_DESKTOP_BREAKPOINT) {
            this.discussionRoom.style.height = '';
            this.discussionRoom.style.maxHeight = '';
            this.discussionRoom.style.minHeight = '';
            return;
        }

        const visibleSections = Array.from(this.workspaceContent.children).filter((element) => {
            return element instanceof HTMLElement && !element.hidden;
        });

        if (!visibleSections.length) {
            this.discussionRoom.style.height = '';
            this.discussionRoom.style.maxHeight = '';
            this.discussionRoom.style.minHeight = '';
            return;
        }

        const firstRect = visibleSections[0].getBoundingClientRect();
        const lastRect = visibleSections[visibleSections.length - 1].getBoundingClientRect();
        const alignedHeight = Math.round(lastRect.bottom - firstRect.top);
        if (alignedHeight <= 0) {
            return;
        }

        this.discussionRoom.style.height = `${alignedHeight}px`;
        this.discussionRoom.style.maxHeight = `${alignedHeight}px`;
        this.discussionRoom.style.minHeight = `${alignedHeight}px`;
    }

    parseNextSwitchAvailableAt(nextSwitchAt, fallbackRemainingSeconds = 0) {
        if (nextSwitchAt) {
            const parsed = Date.parse(nextSwitchAt);
            if (Number.isFinite(parsed)) {
                return parsed;
            }
        }

        const remainingSeconds = Number(fallbackRemainingSeconds || 0);
        if (remainingSeconds > 0) {
            return Date.now() + (remainingSeconds * 1000);
        }

        return null;
    }

    getAliasCooldownRemainingSeconds() {
        const nextSwitchAvailableAt = Number(this.aliasState.nextSwitchAvailableAt || 0);
        if (!Number.isFinite(nextSwitchAvailableAt) || nextSwitchAvailableAt <= 0) {
            return 0;
        }

        const remainingMilliseconds = nextSwitchAvailableAt - Date.now();
        if (remainingMilliseconds <= 0) {
            this.aliasState.nextSwitchAvailableAt = null;
            return 0;
        }

        return Math.ceil(remainingMilliseconds / 1000);
    }

    stopAliasCountdown() {
        if (this.aliasCountdownTimer) {
            window.clearInterval(this.aliasCountdownTimer);
            this.aliasCountdownTimer = null;
        }
    }

    syncAliasCountdown() {
        this.stopAliasCountdown();
        if (this.getAliasCooldownRemainingSeconds() <= 0) {
            return;
        }

        this.aliasCountdownTimer = window.setInterval(() => {
            this.refreshAliasSwitchUi();
            if (this.getAliasCooldownRemainingSeconds() <= 0) {
                this.stopAliasCountdown();
                this.refreshAliasSwitchUi();
            }
        }, 250);
    }

    applyAliasStatePayload(payload = {}) {
        if (!payload || typeof payload !== 'object') {
            return;
        }

        const switchLimit = Number(payload.switch_limit ?? this.aliasState.switchLimit ?? DEFAULT_ALIAS_SWITCH_LIMIT);
        const switchesUsed = Number(payload.switches_used ?? this.aliasState.switchesUsed ?? 0);
        const switchesRemaining = Number(
            payload.switches_remaining ?? Math.max(switchLimit - switchesUsed, 0),
        );
        const availableAliasCount = Number(
            payload.available_alias_count ?? payload.remaining_alias_count ?? this.aliasState.availableAliasCount ?? 0,
        );
        const cooldownSeconds = Number(
            payload.cooldown_seconds ?? this.aliasState.cooldownSeconds ?? DEFAULT_ALIAS_SWITCH_COOLDOWN_SECONDS,
        );

        this.aliasState = {
            ...this.aliasState,
            availableAliasCount: Number.isFinite(availableAliasCount) ? Math.max(availableAliasCount, 0) : 0,
            switchLimit: Number.isFinite(switchLimit) && switchLimit > 0 ? switchLimit : DEFAULT_ALIAS_SWITCH_LIMIT,
            switchesUsed: Number.isFinite(switchesUsed) ? Math.max(switchesUsed, 0) : 0,
            switchesRemaining: Number.isFinite(switchesRemaining) ? Math.max(switchesRemaining, 0) : 0,
            cooldownSeconds: Number.isFinite(cooldownSeconds) && cooldownSeconds > 0
                ? cooldownSeconds
                : DEFAULT_ALIAS_SWITCH_COOLDOWN_SECONDS,
            nextSwitchAvailableAt: this.parseNextSwitchAvailableAt(
                payload.next_switch_at,
                payload.cooldown_remaining_seconds,
            ),
            blockReason: payload.switch_block_reason || null,
        };

        this.syncAliasCountdown();
    }

    getAliasSwitchBlockMessage() {
        if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
            return '课堂研讨室尚未连接成功';
        }

        const cooldownRemaining = this.getAliasCooldownRemainingSeconds();
        if (cooldownRemaining > 0) {
            return `${cooldownRemaining}s 后才能再次切换代号`;
        }

        if (this.aliasState.switchesRemaining <= 0) {
            return `本次进入最多只能切换 ${this.aliasState.switchLimit} 次代号`;
        }

        if (this.aliasState.availableAliasCount <= 0) {
            return '当前没有可用的新代号';
        }

        return '';
    }

    refreshAliasSwitchUi() {
        const isStudent = this.currentUser?.role === 'student';
        const cooldownRemaining = this.getAliasCooldownRemainingSeconds();
        const hasSwitchesRemaining = this.aliasState.switchesRemaining > 0;
        const hasAvailableAlias = this.aliasState.availableAliasCount > 0;
        const isConnected = Boolean(this.ws && this.ws.readyState === WebSocket.OPEN);

        if (this.aliasMetaEl) {
            let metaText = '';
            let metaState = 'ready';

            if (!isStudent) {
                metaText = '教师使用实名参与讨论';
            } else if (!this.displayName) {
                metaText = '正在同步代号状态...';
                metaState = 'loading';
            } else if (!hasSwitchesRemaining) {
                metaText = `本次进入的 ${this.aliasState.switchLimit} 次切换机会已用完`;
                metaState = 'limit';
            } else if (cooldownRemaining > 0) {
                metaText = `还可切换 ${this.aliasState.switchesRemaining} 次，${cooldownRemaining}s 后可再次切换`;
                metaState = 'cooldown';
            } else if (!hasAvailableAlias) {
                metaText = `还可切换 ${this.aliasState.switchesRemaining} 次，但当前没有可用新代号`;
                metaState = 'empty';
            } else {
                metaText = `还可切换 ${this.aliasState.switchesRemaining} 次`;
            }

            this.aliasMetaEl.textContent = metaText;
            this.aliasMetaEl.dataset.state = metaState;
        }

        if (!this.switchAliasButton) {
            return;
        }

        const canSwitch = isStudent
            && isConnected
            && hasAvailableAlias
            && hasSwitchesRemaining
            && cooldownRemaining <= 0;

        this.switchAliasButton.disabled = !canSwitch;

        if (!isConnected) {
            this.switchAliasButton.textContent = '连接中...';
            this.switchAliasButton.title = '连接成功后才能切换代号';
        } else if (!hasSwitchesRemaining) {
            this.switchAliasButton.textContent = '次数已用完';
            this.switchAliasButton.title = `本次进入最多可切换 ${this.aliasState.switchLimit} 次`;
        } else if (cooldownRemaining > 0) {
            this.switchAliasButton.textContent = `${cooldownRemaining}s 后可切换`;
            this.switchAliasButton.title = `冷却中，还剩 ${cooldownRemaining} 秒`;
        } else if (!hasAvailableAlias) {
            this.switchAliasButton.textContent = '暂无可用代号';
            this.switchAliasButton.title = '当前没有可用的新代号';
        } else {
            this.switchAliasButton.textContent = '一键换代号';
            this.switchAliasButton.title = `还可切换 ${this.aliasState.switchesRemaining} 次`;
        }
    }

    handleMessage(event) {
        try {
            const data = JSON.parse(event.data);

            if (data.type === 'history') {
                this.handleHistoryPayload(data);
                return;
            }

            if (data.type === 'chat') {
                const shouldStickBottom = this.isNearBottom();
                this.appendChatMessage(data, { scrollToBottom: shouldStickBottom });
                if (
                    !this.isCurrentUserMessage(data)
                    && String(data.message || '').includes('@')
                    && typeof window.refreshMessageCenterBell === 'function'
                ) {
                    window.refreshMessageCenterBell();
                }
                if (this.isCurrentUserMessage(data)) {
                    this.scheduleEmojiPanelRefresh();
                }
                return;
            }

            if (data.type === 'user_display_name') {
                this.updateDisplayName(data);
                return;
            }

            if (data.type === 'alias_switch_result') {
                if (data.alias_state) {
                    this.updateDisplayName(data.alias_state);
                }
                this.showToast(
                    data.message || (data.success ? '代号已更新' : '代号切换失败'),
                    data.success ? 'success' : 'warning',
                );
                return;
            }

            if (data.type === 'system') {
                this.appendSystemMessage(data.message, {
                    scrollToBottom: this.isNearBottom(),
                    highlight: Boolean(data.highlight),
                });
                if (data.message && (data.message.includes('上传') || data.message.includes('删除'))) {
                    if (typeof this.onFileEvent === 'function') {
                        this.onFileEvent();
                    }
                }
            }
        } catch (error) {
            console.error('Error parsing WebSocket message:', error, event.data);
        }
    }

    handleHistoryPayload(payload) {
        const messages = Array.isArray(payload.data) ? payload.data : [];
        const isOlderBatch = payload.mode === 'older';
        const hadExistingMessages = isOlderBatch ? Boolean(this.getFirstMessageNode()) : false;
        const previousHeight = isOlderBatch ? this.messagesBox.scrollHeight : 0;
        const previousTop = isOlderBatch ? this.messagesBox.scrollTop : 0;

        if (messages.length) {
            this.removeEmptyState();
        }

        messages.forEach((item) => {
            if (item.type === 'chat') {
                this.appendChatMessage(item, { prepend: isOlderBatch, scrollToBottom: false });
            } else if (item.type === 'system') {
                this.appendSystemMessage(item.message, { prepend: isOlderBatch, scrollToBottom: false });
            }
        });

        this.oldestMessageId = payload.oldest_message_id ?? this.oldestMessageId;
        this.hasMoreHistory = Boolean(payload.has_more);
        this.isLoadingHistory = false;

        if (isOlderBatch) {
            if (hadExistingMessages) {
                const heightDelta = this.messagesBox.scrollHeight - previousHeight;
                this.messagesBox.scrollTop = previousTop + heightDelta;
            } else {
                this.messagesBox.scrollTop = 0;
            }
        } else if (messages.length) {
            this.scrollToBottom();
        }

        this.updateHistoryLoader();
    }

    updateDisplayName(payload) {
        this.displayName = payload.display_name || payload.displayName || this.displayName;
        this.applyAliasStatePayload(payload);

        if (this.displayNameEl) {
            this.displayNameEl.textContent = this.displayName || '分配中...';
        }
        this.refreshAliasSwitchUi();
    }

    requestAliasSwitch() {
        if (!this.ws || this.ws.readyState !== WebSocket.OPEN || !this.switchAliasButton || this.switchAliasButton.disabled) {
            const message = this.getAliasSwitchBlockMessage();
            if (message) {
                this.showToast(message, 'warning');
            }
            return;
        }
        this.ws.send(JSON.stringify({ action: 'switch_alias' }));
    }

    requestOlderHistory() {
        if (this.isLoadingHistory || !this.hasMoreHistory || !this.ws || this.ws.readyState !== WebSocket.OPEN) {
            return;
        }

        this.isLoadingHistory = true;
        if (this.historyLoadButton) {
            this.historyLoadButton.disabled = true;
            this.historyLoadButton.textContent = '加载中...';
        }

        this.ws.send(JSON.stringify({
            action: 'load_history',
            before_id: this.oldestMessageId,
        }));
    }

    async loadEmojiPanelData({ silent = false } = {}) {
        const data = await apiFetch(`/api/classrooms/${this.classOfferingId}/emoji-panel`, { silent });
        this.emojiPanelLoaded = true;
        this.emojiPanelData = {
            emoji_set: data.emoji_set || null,
            frequent: Array.isArray(data.frequent) ? data.frequent : [],
            custom_emojis: Array.isArray(data.custom_emojis) ? data.custom_emojis : [],
            limits: data.limits || null,
        };
        this.updateEmojiSetNote();
        this.renderFrequentRow();
        this.renderCustomEmojiGrid();
        return data;
    }

    updateEmojiSetNote() {
        if (!this.emojiSetNote) {
            return;
        }

        const emojiSet = this.emojiPanelData.emoji_set;
        if (!emojiSet) {
            this.emojiSetNote.textContent = FALLBACK_EMOJI_SET_NOTE;
            return;
        }

        const licenseSuffix = emojiSet.license ? ` · ${emojiSet.license}` : '';
        this.emojiSetNote.textContent = `标准表情采用 ${emojiSet.name || 'Twemoji'}${licenseSuffix}`;
    }

    toggleEmojiPopover(forceOpen = null) {
        const shouldOpen = forceOpen == null ? !this.isEmojiPopoverOpen() : Boolean(forceOpen);
        if (shouldOpen) {
            this.openEmojiPopover();
        } else {
            this.closeEmojiPopover();
        }
    }

    openEmojiPopover() {
        if (!this.emojiPopover) {
            return;
        }

        this.emojiPopover.hidden = false;
        requestAnimationFrame(() => {
            this.emojiPopover.classList.add('is-open');
            this.emojiTriggerButton?.classList.add('is-open');
            this.emojiTriggerButton?.setAttribute('aria-expanded', 'true');
        });

        if (!this.emojiPanelLoaded) {
            this.updateUploadStatus('正在加载表情...', 'loading');
            this.loadEmojiPanelData().then(() => {
                this.updateUploadStatus(this.buildEmojiLibrarySummary(), 'idle');
            }).catch(() => {
                this.updateUploadStatus('表情面板加载失败', 'error');
            });
        }
    }

    closeEmojiPopover() {
        if (!this.emojiPopover) {
            return;
        }

        this.emojiPopover.classList.remove('is-open');
        this.emojiTriggerButton?.classList.remove('is-open');
        this.emojiTriggerButton?.setAttribute('aria-expanded', 'false');

        window.setTimeout(() => {
            if (!this.emojiPopover?.classList.contains('is-open')) {
                this.emojiPopover.hidden = true;
            }
        }, 180);
    }

    isEmojiPopoverOpen() {
        return Boolean(this.emojiPopover && !this.emojiPopover.hidden && this.emojiPopover.classList.contains('is-open'));
    }

    handleDocumentPointerDown(event) {
        if (!this.isEmojiPopoverOpen()) {
            return;
        }

        const target = event.target;
        if (this.emojiPopover?.contains(target) || this.emojiTriggerButton?.contains(target)) {
            return;
        }

        this.closeEmojiPopover();
    }

    handleDocumentKeydown(event) {
        if (event.key === 'Escape' && this.isEmojiPopoverOpen()) {
            this.closeEmojiPopover();
            this.chatInput?.focus();
        }
    }

    renderFrequentRow() {
        if (!this.emojiFrequentRow) {
            return;
        }

        this.emojiFrequentRow.replaceChildren();
        const frequentItems = this.getFrequentItems();
        if (!frequentItems.length) {
            const empty = document.createElement('div');
            empty.className = 'chat-emoji-empty';
            empty.textContent = '发送得越多，这里越懂你。';
            this.emojiFrequentRow.appendChild(empty);
            return;
        }

        const fragment = document.createDocumentFragment();
        frequentItems.forEach((item) => fragment.appendChild(this.createEmojiButton(item)));
        this.emojiFrequentRow.appendChild(fragment);
    }

    getFrequentItems() {
        if (Array.isArray(this.emojiPanelData.frequent) && this.emojiPanelData.frequent.length) {
            return this.emojiPanelData.frequent.slice(0, MAX_FREQUENT_ITEMS);
        }

        return DEFAULT_FREQUENT_EMOJIS.slice(0, MAX_FREQUENT_ITEMS).map((value) => ({
            type: 'unicode',
            value,
        }));
    }

    renderEmojiCategories() {
        if (!this.emojiCategoriesBox) {
            return;
        }

        this.emojiCategoriesBox.replaceChildren();
        const fragment = document.createDocumentFragment();

        EMOJI_CATEGORIES.forEach((category) => {
            const section = document.createElement('section');
            section.className = 'chat-emoji-category';

            const header = document.createElement('div');
            header.className = 'chat-emoji-category-header';
            header.textContent = category.label;
            section.appendChild(header);

            const grid = document.createElement('div');
            grid.className = 'chat-emoji-grid';
            category.emojis.forEach((emoji) => {
                grid.appendChild(this.createUnicodeEmojiButton({
                    type: 'unicode',
                    value: emoji.char,
                    code: emoji.code,
                    name: emoji.name,
                }));
            });
            section.appendChild(grid);

            fragment.appendChild(section);
        });

        this.emojiCategoriesBox.appendChild(fragment);
    }

    renderCustomEmojiGrid() {
        if (!this.customEmojiGrid) {
            return;
        }

        this.customEmojiGrid.replaceChildren();
        const customEmojis = Array.isArray(this.emojiPanelData.custom_emojis) ? this.emojiPanelData.custom_emojis : [];
        if (!customEmojis.length) {
            const empty = document.createElement('div');
            empty.className = 'chat-emoji-empty';
            empty.textContent = '...';
            this.customEmojiGrid.appendChild(empty);
        } else {
            const fragment = document.createDocumentFragment();
            customEmojis.forEach((emoji) => fragment.appendChild(this.createCustomEmojiButton(emoji)));
            this.customEmojiGrid.appendChild(fragment);
        }

        if (!this.uploadInFlight) {
            this.updateUploadStatus(this.buildEmojiLibrarySummary(), 'idle');
        }

        if (this.customEmojiUploadButton) {
            this.customEmojiUploadButton.disabled = this.uploadInFlight || this.isCustomEmojiLimitReached();
        }
    }

    buildEmojiLibrarySummary() {
        const count = Array.isArray(this.emojiPanelData.custom_emojis) ? this.emojiPanelData.custom_emojis.length : 0;
        const limit = this.getMaxCustomEmojiCount();
        return `自定义表情 ${count}/${limit}`;
    }

    getMaxUploadBytes() {
        return Number(this.emojiPanelData.limits?.max_upload_bytes || DEFAULT_MAX_UPLOAD_BYTES);
    }

    getMaxCustomEmojiCount() {
        return Number(this.emojiPanelData.limits?.max_custom_emoji_count || DEFAULT_MAX_CUSTOM_EMOJIS);
    }

    isCustomEmojiLimitReached() {
        const currentCount = Array.isArray(this.emojiPanelData.custom_emojis) ? this.emojiPanelData.custom_emojis.length : 0;
        return currentCount >= this.getMaxCustomEmojiCount();
    }

    createEmojiButton(item) {
        if (item?.type === 'custom' || item?.image_url) {
            return this.createCustomEmojiButton(item);
        }
        return this.createUnicodeEmojiButton(item);
    }

    createUnicodeEmojiButton(item) {
        const value = item?.value || item?.char || '';
        const meta = getEmojiMeta(value) || item || {};
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'chat-emoji-item';
        button.title = meta.name || value;
        button.setAttribute('aria-label', meta.name || value || 'emoji');
        button.appendChild(this.createUnicodeEmojiVisual(value, meta.code));
        button.addEventListener('click', () => this.insertEmojiAtCursor(value));
        return button;
    }

    createUnicodeEmojiVisual(char, code = null) {
        const emojiCode = code || getEmojiMeta(char)?.code || null;
        if (!emojiCode) {
            const fallback = document.createElement('span');
            fallback.textContent = char;
            return fallback;
        }

        const image = document.createElement('img');
        image.src = buildTwemojiUrl(emojiCode);
        image.alt = char;
        image.loading = 'lazy';
        image.decoding = 'async';
        image.onerror = () => {
            const fallback = document.createElement('span');
            fallback.textContent = char;
            image.replaceWith(fallback);
        };
        return image;
    }

    createCustomEmojiButton(item) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'chat-emoji-item chat-emoji-item-custom';
        button.title = item?.name || '自定义表情';
        button.setAttribute('aria-label', item?.name || '自定义表情');

        const image = document.createElement('img');
        image.src = item?.image_url || '';
        image.alt = item?.name || 'custom emoji';
        image.loading = 'lazy';
        image.decoding = 'async';
        button.appendChild(image);

        button.addEventListener('click', () => this.addCustomEmoji(item));
        return button;
    }

    insertEmojiAtCursor(char) {
        this.insertTextAtCursor(char);
    }

    insertMentionAll() {
        if (this.currentUser?.role !== 'teacher') {
            return;
        }
        const prefix = this.chatInput?.value && !this.chatInput.value.endsWith(' ') ? ' ' : '';
        this.insertTextAtCursor(`${prefix}@所有人 `);
    }

    insertTextAtCursor(text) {
        if (!this.chatInput || !text) {
            return;
        }
        const start = this.chatInput.selectionStart ?? this.chatInput.value.length;
        const end = this.chatInput.selectionEnd ?? this.chatInput.value.length;
        const currentValue = this.chatInput.value;
        this.chatInput.value = `${currentValue.slice(0, start)}${text}${currentValue.slice(end)}`;
        const nextPosition = start + text.length;
        this.chatInput.focus();
        this.chatInput.setSelectionRange(nextPosition, nextPosition);
        this.resizeInput();
    }

    addCustomEmoji(item) {
        if (!item || typeof item.id === 'undefined') {
            return;
        }

        this.selectedCustomEmojis.push(item);
        this.renderSelectedCustomEmojis();
    }

    renderSelectedCustomEmojis() {
        if (!this.emojiPreviewRow) {
            return;
        }

        this.emojiPreviewRow.replaceChildren();
        this.emojiPreviewRow.hidden = this.selectedCustomEmojis.length === 0;
        if (!this.selectedCustomEmojis.length) {
            return;
        }

        const fragment = document.createDocumentFragment();
        this.selectedCustomEmojis.forEach((emoji, index) => {
            const chip = document.createElement('div');
            chip.className = 'chat-emoji-preview-chip';

            const image = document.createElement('img');
            image.src = emoji.image_url || '';
            image.alt = emoji.name || '自定义表情';
            image.loading = 'lazy';
            image.decoding = 'async';
            chip.appendChild(image);

            const label = document.createElement('span');
            label.textContent = emoji.name || `表情 ${index + 1}`;
            chip.appendChild(label);

            const removeButton = document.createElement('button');
            removeButton.type = 'button';
            removeButton.setAttribute('aria-label', `移除 ${emoji.name || '表情'}`);
            removeButton.title = '移除';
            removeButton.textContent = '×';
            removeButton.addEventListener('click', () => {
                this.selectedCustomEmojis.splice(index, 1);
                this.renderSelectedCustomEmojis();
            });
            chip.appendChild(removeButton);

            fragment.appendChild(chip);
        });

        this.emojiPreviewRow.appendChild(fragment);
    }

    async uploadCustomEmoji(file) {
        if (!file || this.uploadInFlight) {
            return;
        }

        const allowedTypes = new Set(['image/png', 'image/jpeg', 'image/gif']);
        const normalizedName = String(file.name || '').toLowerCase();
        const extAllowed = ['.png', '.jpg', '.jpeg', '.gif'].some((ext) => normalizedName.endsWith(ext));
        if (!allowedTypes.has(file.type) && !extAllowed) {
            this.updateUploadStatus('仅支持 PNG、JPG、JPEG 或 GIF', 'error');
            this.showToast('仅支持 PNG、JPG、JPEG 或 GIF 表情', 'warning');
            return;
        }

        const maxBytes = this.getMaxUploadBytes();
        if (file.size > maxBytes) {
            this.updateUploadStatus(`表情大小不能超过 ${Math.round(maxBytes / 1024 / 1024)}MB`, 'error');
            this.showToast('表情大小不能超过 5MB', 'warning');
            return;
        }

        if (this.isCustomEmojiLimitReached()) {
            this.updateUploadStatus(`自定义表情已达上限 ${this.getMaxCustomEmojiCount()} 个`, 'error');
            this.showToast('自定义表情数量已达上限', 'warning');
            return;
        }

        this.uploadInFlight = true;
        if (this.customEmojiUploadButton) {
            this.customEmojiUploadButton.disabled = true;
        }

        this.setUploadProgress(0, true);
        this.updateUploadStatus(`正在上传 ${file.name}`, 'uploading');

        const formData = new FormData();
        formData.append('file', file, file.name);

        const xhr = new XMLHttpRequest();
        xhr.open('POST', `/api/classrooms/${this.classOfferingId}/custom-emojis`, true);
        xhr.responseType = 'text';

        xhr.upload.onprogress = (event) => {
            if (!event.lengthComputable) {
                return;
            }
            const percent = Math.max(0, Math.min(100, Math.round((event.loaded / event.total) * 100)));
            this.setUploadProgress(percent, true);
            this.updateUploadStatus(`正在上传 ${percent}%`, 'uploading');
        };

        xhr.onerror = () => {
            this.uploadInFlight = false;
            this.setUploadProgress(0, false);
            this.updateUploadStatus('上传失败，请检查网络后重试', 'error');
            this.showToast('表情上传失败', 'error');
            this.renderCustomEmojiGrid();
        };

        xhr.onload = () => {
            this.uploadInFlight = false;

            let response = null;
            try {
                response = xhr.responseText ? JSON.parse(xhr.responseText) : null;
            } catch (error) {
                response = null;
            }

            if ((xhr.status === 401 || xhr.status === 403) && response?.redirect_to) {
                window.location.href = response.redirect_to;
                return;
            }

            if (xhr.status < 200 || xhr.status >= 300) {
                this.setUploadProgress(0, false);
                const message = response?.detail || response?.message || '表情上传失败';
                this.updateUploadStatus(message, 'error');
                this.showToast(message, 'error');
                this.renderCustomEmojiGrid();
                return;
            }

            const emoji = response?.emoji || null;
            if (emoji) {
                this.mergeCustomEmoji(emoji);
                this.emojiPanelLoaded = true;
            }

            this.setUploadProgress(100, true);
            window.setTimeout(() => this.setUploadProgress(0, false), 500);

            const message = response?.message || '自定义表情上传成功';
            this.updateUploadStatus(message, 'success');
            this.showToast(message, response?.deduplicated ? 'info' : 'success');
            this.renderCustomEmojiGrid();
            this.renderFrequentRow();
        };

        xhr.send(formData);
    }

    mergeCustomEmoji(emoji) {
        const current = Array.isArray(this.emojiPanelData.custom_emojis) ? [...this.emojiPanelData.custom_emojis] : [];
        const filtered = current.filter((item) => Number(item.id) !== Number(emoji.id));
        filtered.unshift(emoji);
        this.emojiPanelData.custom_emojis = filtered;
    }

    setUploadProgress(percent, visible) {
        if (!this.customEmojiProgress || !this.customEmojiProgressBar) {
            return;
        }

        this.customEmojiProgress.hidden = !visible;
        this.customEmojiProgressBar.style.width = `${Math.max(0, Math.min(100, percent))}%`;
    }

    updateUploadStatus(message, state = 'idle') {
        if (!this.customEmojiUploadStatus) {
            return;
        }

        this.customEmojiUploadStatus.textContent = message;
        this.customEmojiUploadStatus.dataset.state = state;
    }

    appendChatMessage(message, options = {}) {
        const messageId = Number(message.id || 0);
        if (messageId && this.knownMessageIds.has(messageId)) {
            return;
        }
        if (messageId) {
            this.knownMessageIds.add(messageId);
        }

        const sender = String(message.sender || '课堂成员');
        const text = String(message.message || '');
        const role = String(message.role || '');
        const isCurrentUser = this.isCurrentUserMessage(message);
        const roleClass = role === 'teacher' ? ' teacher' : (role === 'assistant' ? ' assistant' : '');
        const initials = role === 'assistant'
            ? '助'
            : (sender.trim().slice(0, 1).toUpperCase() || '?');

        const wrapper = document.createElement('div');
        wrapper.className = `chat-message${isCurrentUser ? ' chat-self' : ''}${role === 'assistant' ? ' chat-assistant' : ''}`;

        const row = document.createElement('div');
        row.className = 'chat-message-row';

        const avatar = document.createElement('div');
        avatar.className = 'chat-avatar';
        avatar.setAttribute('aria-hidden', 'true');
        avatar.textContent = initials;
        row.appendChild(avatar);

        const main = document.createElement('div');
        main.className = 'chat-message-main';

        const header = document.createElement('div');
        header.className = 'chat-message-header';

        const senderNode = document.createElement('span');
        senderNode.className = `sender${roleClass}`;
        senderNode.textContent = sender;
        header.appendChild(senderNode);

        const timeNode = document.createElement('span');
        timeNode.className = 'time';
        timeNode.textContent = String(message.timestamp || '');
        header.appendChild(timeNode);
        main.appendChild(header);

        const content = document.createElement('div');
        content.className = 'message-content';
        content.innerHTML = this.escape(text).replace(/\n/g, '<br>');
        main.appendChild(content);

        const customEmojis = Array.isArray(message.custom_emojis) ? message.custom_emojis : [];
        if (customEmojis.length) {
            main.appendChild(this.renderMessageCustomEmojis(customEmojis));
        }

        row.appendChild(main);
        wrapper.appendChild(row);
        this.insertMessageNode(wrapper, options);
    }

    renderMessageCustomEmojis(items) {
        const list = document.createElement('div');
        list.className = 'chat-message-custom-emojis';

        items.forEach((item) => {
            const image = document.createElement('img');
            image.className = 'chat-message-custom-emoji';
            image.src = item?.image_url || '';
            image.alt = item?.name || '自定义表情';
            image.loading = 'lazy';
            image.decoding = 'async';
            list.appendChild(image);
        });

        return list;
    }

    appendSystemMessage(text, options = {}) {
        const wrapper = document.createElement('div');
        wrapper.className = `chat-message system${options.highlight ? ' is-highlight' : ''}`;
        wrapper.innerHTML = `<span class="message-content">${this.escape(String(text || '系统消息'))}</span>`;
        this.insertMessageNode(wrapper, options);
    }

    insertMessageNode(node, options = {}) {
        if (options.prepend) {
            const anchor = this.getFirstMessageNode();
            if (anchor) {
                this.messagesBox.insertBefore(node, anchor);
            } else {
                this.messagesBox.appendChild(node);
            }
        } else {
            this.messagesBox.appendChild(node);
        }

        this.removeEmptyState();

        if (options.scrollToBottom) {
            this.scrollToBottom();
        }
    }

    getFirstMessageNode() {
        const children = Array.from(this.messagesBox.children);
        return children.find((child) => child.id !== 'chat-history-loader' && child.id !== 'chat-empty-state') || null;
    }

    sendMessage() {
        if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
            this.showToast('课堂研讨室尚未连接成功', 'warning');
            return;
        }

        const rawText = this.chatInput.value;
        const messageText = rawText.trim();
        const customEmojiIds = this.selectedCustomEmojis
            .map((item) => Number(item.id))
            .filter((item) => Number.isFinite(item));

        if (!messageText && !customEmojiIds.length) {
            return;
        }

        this.ws.send(JSON.stringify({
            action: 'send_message',
            text: messageText,
            custom_emoji_ids: customEmojiIds,
            used_unicode_emojis: this.extractKnownEmojis(messageText),
        }));

        this.chatInput.value = '';
        this.resizeInput();
        this.selectedCustomEmojis = [];
        this.renderSelectedCustomEmojis();
        this.scheduleEmojiPanelRefresh();
    }

    scheduleEmojiPanelRefresh() {
        if (this.refreshTimer) {
            window.clearTimeout(this.refreshTimer);
        }

        this.refreshTimer = window.setTimeout(() => {
            if (!this.emojiPanelLoaded) {
                return;
            }
            this.loadEmojiPanelData({ silent: true }).catch(() => {});
        }, 350);
    }

    extractKnownEmojis(text) {
        if (!text || !KNOWN_EMOJI_REGEX) {
            return [];
        }

        KNOWN_EMOJI_REGEX.lastIndex = 0;
        const matches = text.match(KNOWN_EMOJI_REGEX);
        return matches ? [...matches] : [];
    }

    isCurrentUserMessage(message) {
        const currentRole = String(this.currentUser.role || '');
        const incomingRole = String(message.role || '');
        if (message.user_id != null && this.currentUser.id != null) {
            return String(message.user_id) === String(this.currentUser.id) && incomingRole === currentRole;
        }

        const myName = this.displayName || this.currentUser.name;
        return String(message.sender || '') === String(myName || '') && incomingRole === currentRole;
    }

    resizeInput() {
        if (!this.chatInput) {
            return;
        }
        this.chatInput.style.height = 'auto';
        this.chatInput.style.height = `${Math.min(this.chatInput.scrollHeight, 160)}px`;
    }

    removeEmptyState() {
        const emptyState = document.getElementById('chat-empty-state');
        if (emptyState && this.getFirstMessageNode()) {
            emptyState.remove();
        }
    }

    isNearBottom() {
        return this.messagesBox.scrollHeight - this.messagesBox.scrollTop - this.messagesBox.clientHeight < 80;
    }

    updateHistoryLoader() {
        if (!this.historyLoader || !this.historyLoadButton) {
            return;
        }

        const shouldShow = this.hasMoreHistory && this.messagesBox.scrollTop <= 24;
        this.historyLoader.hidden = !shouldShow;
        this.historyLoadButton.disabled = this.isLoadingHistory;
        this.historyLoadButton.textContent = this.isLoadingHistory ? '加载中...' : '加载历史消息';
    }

    scrollToBottom() {
        this.messagesBox.scrollTop = this.messagesBox.scrollHeight;
        this.updateHistoryLoader();
    }

    showToast(message, type = 'info') {
        if (window.UI && typeof window.UI.showToast === 'function') {
            window.UI.showToast(message, type);
        }
    }

    escape(value) {
        if (window.UI && typeof window.UI.escapeHtml === 'function') {
            return window.UI.escapeHtml(value);
        }
        return String(value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }
}
