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
const DEFAULT_DISCUSSION_ATTACHMENT_MAX_BYTES = 10 * 1024 * 1024;
const DEFAULT_DISCUSSION_ATTACHMENT_LIMIT = 4;
const DEFAULT_MAX_CUSTOM_EMOJIS = 60;
const MAX_FREQUENT_ITEMS = 8;
const DEFAULT_ALIAS_SWITCH_COOLDOWN_SECONDS = 10;
const DEFAULT_ALIAS_SWITCH_LIMIT = 6;
const DISCUSSION_ROOM_DESKTOP_BREAKPOINT = 1120;
const MESSAGE_MENU_HOVER_DELAY_MS = 260;
const MESSAGE_MENU_CLOSE_DELAY_MS = 120;
const MESSAGE_SOURCE_HIGHLIGHT_MS = 1800;
const DISCUSSION_UI_TEXT = Object.freeze({
    defaultSender: '\u8bfe\u5802\u6210\u5458',
    quoteActiveLabel: '\u6b63\u5728\u5f15\u7528',
    quotedMessageLabel: '\u5f15\u7528\u6d88\u606f',
    quoteSourceAriaPrefix: '\u67e5\u770b',
    quoteSourceAriaSuffix: '\u7684\u539f\u6d88\u606f',
    cancelQuote: '\u53d6\u6d88\u5f15\u7528',
    quoteInserted: '\u5df2\u63d2\u5165\u5f15\u7528',
    quoteSourceMissing: '\u5f15\u7528\u6d88\u606f\u6682\u672a\u52a0\u8f7d\uff0c\u53ef\u5148\u52a0\u8f7d\u66f4\u591a\u5386\u53f2\u6d88\u606f',
    messageActionsLabel: '\u6d88\u606f\u64cd\u4f5c',
    quoteActionLabel: '\u5f15\u7528',
    quoteActionTitle: '\u5f15\u7528\u8fd9\u6761\u6d88\u606f',
    copyActionLabel: '\u590d\u5236',
    copyActionTitle: '\u590d\u5236\u8fd9\u6761\u6d88\u606f',
    copySuccess: '\u6d88\u606f\u5df2\u590d\u5236\u5230\u526a\u8d34\u677f',
    copyFailed: '\u590d\u5236\u5931\u8d25\uff0c\u8bf7\u68c0\u67e5\u6d4f\u89c8\u5668\u6743\u9650',
});

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
        this.sendButton = this.chatForm?.querySelector('.chat-send-btn') || null;
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
        this.attachmentTriggerButton = document.getElementById(options.attachmentTriggerButtonId);
        this.attachmentFileInput = document.getElementById(options.attachmentFileInputId);
        this.attachmentPreviewRow = document.getElementById(options.attachmentPreviewRowId);
        this.quotePreview = document.getElementById(options.quotePreviewId);
        this.messageMenu = document.getElementById(options.messageMenuId);
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
        this.messageRecords = new Map();
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
        this.pendingAttachments = [];
        this.pendingQuote = null;
        this.attachmentUploadInFlight = false;
        this.discussionAttachmentLimits = {
            maxAttachmentCount: DEFAULT_DISCUSSION_ATTACHMENT_LIMIT,
            maxUploadBytes: DEFAULT_DISCUSSION_ATTACHMENT_MAX_BYTES,
        };
        this.activeMessageMenuId = null;
        this.pendingMessageMenuId = null;
        this.pendingMessageMenuAnchor = null;
        this.messageMenuHoverTimer = null;
        this.messageMenuCloseTimer = null;
        this.messageHighlightTimer = null;
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
        this.sendRateLimitTimer = null;
        this.sendRateLimitUntil = 0;
        this.roomHeightFrame = null;
        this.roomHeightObserver = null;
        this.lastDiscussionRoomHeight = 0;
        this.defaultSendButtonMarkup = this.sendButton?.innerHTML || '';

        this.handleDocumentPointerDown = this.handleDocumentPointerDown.bind(this);
        this.handleDocumentKeydown = this.handleDocumentKeydown.bind(this);
        this.scheduleDiscussionRoomResize = this.scheduleDiscussionRoomResize.bind(this);
        this.handleMessagesWheel = this.handleMessagesWheel.bind(this);
        this.handleMessageMouseOver = this.handleMessageMouseOver.bind(this);
        this.handleMessageMouseOut = this.handleMessageMouseOut.bind(this);
        this.handleMessageContextMenu = this.handleMessageContextMenu.bind(this);
        this.handleMessageClick = this.handleMessageClick.bind(this);
        this.handleMessageKeydown = this.handleMessageKeydown.bind(this);
        this.handleMessageMenuMouseEnter = this.handleMessageMenuMouseEnter.bind(this);
        this.handleMessageMenuMouseLeave = this.handleMessageMenuMouseLeave.bind(this);
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
        this.renderPendingAttachments();
        this.renderQuotePreview();
        this.updateUploadStatus('未上传', 'idle');
        this.updateEmojiSetNote();
        this.resizeInput();
        this.setupDiscussionRoomSizing();
        this.refreshAliasSwitchUi();
        this.renderSendButtonState();
        this.updateAttachmentTriggerState();

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

        this.messagesBox.addEventListener('scroll', () => {
            this.updateHistoryLoader();
            this.cancelMessageMenuHover();
            this.cancelMessageMenuClose();
            this.closeMessageActionMenu();
        });
        this.messagesBox.addEventListener('wheel', this.handleMessagesWheel, { passive: false });
        this.messagesBox.addEventListener('contextmenu', this.handleMessageContextMenu);
        this.messagesBox.addEventListener('click', this.handleMessageClick);
        this.messagesBox.addEventListener('keydown', this.handleMessageKeydown);
        this.switchAliasButton?.addEventListener('click', () => this.requestAliasSwitch());
        this.mentionAllButton?.addEventListener('click', () => this.insertMentionAll());
        this.historyLoadButton?.addEventListener('click', () => this.requestOlderHistory());
        this.emojiTriggerButton?.addEventListener('click', () => this.toggleEmojiPopover());
        this.emojiCloseButton?.addEventListener('click', () => this.closeEmojiPopover());
        this.attachmentTriggerButton?.addEventListener('click', () => this.attachmentFileInput?.click());
        this.attachmentFileInput?.addEventListener('change', (event) => {
            const input = event.currentTarget;
            const files = Array.from(input?.files || []);
            if (files.length) {
                this.queueDiscussionAttachments(files);
            }
            if (input) {
                input.value = '';
            }
        });
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
        this.messageMenu?.addEventListener('click', (event) => {
            void this.handleMessageMenuAction(event);
        });
        this.messageMenu?.addEventListener('mouseenter', this.handleMessageMenuMouseEnter);
        this.messageMenu?.addEventListener('mouseleave', this.handleMessageMenuMouseLeave);
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
            this.lastDiscussionRoomHeight = 0;
            return;
        }

        const visibleSections = Array.from(this.workspaceContent.children).filter((element) => {
            return element instanceof HTMLElement && !element.hidden;
        });

        if (!visibleSections.length) {
            this.discussionRoom.style.height = '';
            this.discussionRoom.style.maxHeight = '';
            this.discussionRoom.style.minHeight = '';
            this.lastDiscussionRoomHeight = 0;
            return;
        }

        const firstRect = visibleSections[0].getBoundingClientRect();
        const lastRect = visibleSections[visibleSections.length - 1].getBoundingClientRect();
        const alignedHeight = Math.round(lastRect.bottom - firstRect.top);
        if (alignedHeight <= 0) {
            return;
        }

        if (alignedHeight === this.lastDiscussionRoomHeight) {
            return;
        }

        this.discussionRoom.style.height = `${alignedHeight}px`;
        this.discussionRoom.style.maxHeight = `${alignedHeight}px`;
        this.discussionRoom.style.minHeight = `${alignedHeight}px`;
        this.lastDiscussionRoomHeight = alignedHeight;
    }

    handleMessagesWheel(event) {
        if (!this.messagesBox || window.innerWidth <= DISCUSSION_ROOM_DESKTOP_BREAKPOINT) {
            return;
        }

        const scrollableDistance = this.messagesBox.scrollHeight - this.messagesBox.clientHeight;
        if (scrollableDistance <= 1) {
            event.preventDefault();
            window.scrollBy({ top: event.deltaY, left: 0, behavior: 'auto' });
            return;
        }

        const nextScrollTop = this.messagesBox.scrollTop + event.deltaY;
        const willOverflowTop = event.deltaY < 0 && nextScrollTop <= 0;
        const willOverflowBottom = event.deltaY > 0 && nextScrollTop >= scrollableDistance;

        if (!willOverflowTop && !willOverflowBottom) {
            return;
        }

        event.preventDefault();
        this.messagesBox.scrollTop = willOverflowTop ? 0 : scrollableDistance;
        window.scrollBy({ top: event.deltaY, left: 0, behavior: 'auto' });
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

            if (data.type === 'send_rate_limited') {
                this.activateSendRateLimit(data.retry_after_seconds, data.message);
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
        document.dispatchEvent(new CustomEvent('classroom:alias-change', {
            detail: {
                displayName: this.displayName || '',
            },
        }));
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

    getSendRateLimitRemainingSeconds() {
        const remainingMilliseconds = Number(this.sendRateLimitUntil || 0) - Date.now();
        if (remainingMilliseconds <= 0) {
            return 0;
        }
        return Math.max(Math.ceil(remainingMilliseconds / 1000), 1);
    }

    isSendRateLimited() {
        return this.getSendRateLimitRemainingSeconds() > 0;
    }

    activateSendRateLimit(retryAfterSeconds, message = null) {
        const safeSeconds = Math.max(Number(retryAfterSeconds || 0), 1);
        this.sendRateLimitUntil = Date.now() + (safeSeconds * 1000);

        if (this.sendRateLimitTimer) {
            window.clearTimeout(this.sendRateLimitTimer);
        }

        this.renderSendButtonState();
        this.sendRateLimitTimer = window.setTimeout(() => {
            this.sendRateLimitTimer = null;
            this.sendRateLimitUntil = 0;
            this.renderSendButtonState();
        }, safeSeconds * 1000);

        this.showToast(message || '\u53d1\u4fe1\u592a\u9891\u7e41\u7a0d\u540e\u518d\u53d1', 'warning');
    }

    renderSendButtonState() {
        if (!this.sendButton) {
            return;
        }

        const uploadingAttachments = this.attachmentUploadInFlight;
        const limited = this.isSendRateLimited();
        this.sendButton.disabled = limited || uploadingAttachments;
        this.sendButton.classList.toggle('is-rate-limited', limited);
        this.sendButton.classList.toggle('is-uploading', uploadingAttachments);
        this.sendButton.title = limited
            ? '\u53d1\u4fe1\u592a\u9891\u7e41\u7a0d\u540e\u518d\u53d1'
            : (uploadingAttachments ? '图片上传中' : '\u53d1\u9001');
        this.sendButton.setAttribute('aria-label', this.sendButton.title);

        if (!limited && !uploadingAttachments) {
            if (this.sendButton.innerHTML !== this.defaultSendButtonMarkup) {
                this.sendButton.innerHTML = this.defaultSendButtonMarkup;
            }
            return;
        }

        if (uploadingAttachments) {
            this.sendButton.innerHTML = [
                '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">',
                '<path d="M12 2v6"></path>',
                '<path d="M12 16v6"></path>',
                '<path d="m4.93 4.93 4.24 4.24"></path>',
                '<path d="m14.83 14.83 4.24 4.24"></path>',
                '<path d="M2 12h6"></path>',
                '<path d="M16 12h6"></path>',
                '<path d="m4.93 19.07 4.24-4.24"></path>',
                '<path d="m14.83 9.17 4.24-4.24"></path>',
                '</svg>',
                '<span>上传中</span>',
            ].join('');
            return;
        }

        this.sendButton.innerHTML = [
            '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">',
            '<circle cx="12" cy="12" r="9"></circle>',
            '<path d="M8.5 8.5l7 7"></path>',
            '<path d="M15.5 8.5l-7 7"></path>',
            '</svg>',
            '<span>\u7a0d\u540e\u518d\u53d1</span>',
        ].join('');
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
        const target = event.target;
        if (this.isEmojiPopoverOpen()) {
            if (!this.emojiPopover?.contains(target) && !this.emojiTriggerButton?.contains(target)) {
                this.closeEmojiPopover();
            }
        }

        if (this.messageMenu && !this.messageMenu.hidden) {
            if (!this.messageMenu.contains(target)) {
                this.cancelMessageMenuHover();
                this.cancelMessageMenuClose();
                this.closeMessageActionMenu();
            }
        }
    }

    handleDocumentKeydown(event) {
        if (event.key === 'Escape') {
            if (this.isEmojiPopoverOpen()) {
                this.closeEmojiPopover();
            }
            if (this.messageMenu && !this.messageMenu.hidden) {
                this.cancelMessageMenuHover();
                this.cancelMessageMenuClose();
                this.closeMessageActionMenu();
            }
            this.chatInput?.focus();
        }
    }

    resolveMessageMenuAnchor(target) {
        if (!(target instanceof Element)) {
            return null;
        }

        const anchor = target.closest('.chat-message-main');
        if (!anchor) {
            return null;
        }

        const messageNode = anchor.closest('.chat-message[data-message-id]');
        if (!messageNode || messageNode.classList.contains('system')) {
            return null;
        }

        return anchor;
    }

    getMessageNodeFromAnchor(anchor) {
        if (!(anchor instanceof Element)) {
            return null;
        }
        return anchor.closest('.chat-message[data-message-id]');
    }

    cancelMessageMenuHover() {
        if (this.messageMenuHoverTimer) {
            window.clearTimeout(this.messageMenuHoverTimer);
            this.messageMenuHoverTimer = null;
        }
        this.pendingMessageMenuId = null;
        this.pendingMessageMenuAnchor = null;
    }

    cancelMessageMenuClose() {
        if (this.messageMenuCloseTimer) {
            window.clearTimeout(this.messageMenuCloseTimer);
            this.messageMenuCloseTimer = null;
        }
    }

    scheduleMessageMenuOpen(messageNode, anchor) {
        if (!messageNode || !anchor) {
            return;
        }

        const messageId = Number(messageNode.dataset.messageId || 0);
        if (!Number.isFinite(messageId) || messageId <= 0) {
            return;
        }

        this.cancelMessageMenuHover();
        this.cancelMessageMenuClose();

        if (this.activeMessageMenuId && this.activeMessageMenuId !== messageId) {
            this.closeMessageActionMenu();
        }

        this.pendingMessageMenuId = messageId;
        this.pendingMessageMenuAnchor = anchor;
        this.messageMenuHoverTimer = window.setTimeout(() => {
            this.messageMenuHoverTimer = null;
            const currentMessageNode = this.getMessageNodeFromAnchor(anchor);
            if (!currentMessageNode) {
                this.pendingMessageMenuId = null;
                this.pendingMessageMenuAnchor = null;
                return;
            }

            this.openMessageActionMenu(currentMessageNode, anchor);
        }, MESSAGE_MENU_HOVER_DELAY_MS);
    }

    scheduleMessageMenuClose() {
        this.cancelMessageMenuHover();
        this.cancelMessageMenuClose();
        this.messageMenuCloseTimer = window.setTimeout(() => {
            this.messageMenuCloseTimer = null;
            this.closeMessageActionMenu();
        }, MESSAGE_MENU_CLOSE_DELAY_MS);
    }

    handleMessageMouseOver(event) {
        const anchor = this.resolveMessageMenuAnchor(event.target);
        if (!anchor) {
            return;
        }

        const messageNode = this.getMessageNodeFromAnchor(anchor);
        if (!messageNode) {
            return;
        }

        const relatedAnchor = this.resolveMessageMenuAnchor(event.relatedTarget);
        if (relatedAnchor === anchor) {
            return;
        }

        if (this.messageMenu?.contains(event.relatedTarget)) {
            return;
        }

        const messageId = Number(messageNode.dataset.messageId || 0);
        if (this.activeMessageMenuId === messageId && !this.messageMenu?.hidden) {
            this.cancelMessageMenuClose();
            return;
        }

        this.scheduleMessageMenuOpen(messageNode, anchor);
    }

    handleMessageMouseOut(event) {
        const anchor = this.resolveMessageMenuAnchor(event.target);
        if (!anchor) {
            return;
        }

        const nextTarget = event.relatedTarget;
        if (nextTarget && anchor.contains(nextTarget)) {
            return;
        }

        const nextAnchor = this.resolveMessageMenuAnchor(nextTarget);
        if (nextAnchor === anchor) {
            return;
        }

        if (this.messageMenu?.contains(nextTarget)) {
            this.cancelMessageMenuHover();
            this.cancelMessageMenuClose();
            return;
        }

        this.scheduleMessageMenuClose();
    }

    handleMessageContextMenu(event) {
        const anchor = this.resolveMessageMenuAnchor(event.target);
        if (!anchor) {
            return;
        }

        const messageNode = this.getMessageNodeFromAnchor(anchor);
        if (!messageNode) {
            return;
        }

        event.preventDefault();
        this.cancelMessageMenuHover();
        this.cancelMessageMenuClose();
        this.openMessageActionMenu(messageNode, anchor);
    }

    handleMessageClick(event) {
        if (!(event.target instanceof Element)) {
            return;
        }

        const actionButton = event.target.closest('[data-message-action]');
        if (actionButton) {
            event.preventDefault();
            event.stopPropagation();
            const messageNode = actionButton.closest('.chat-message[data-message-id]');
            const message = messageNode
                ? this.getMessageRecord(messageNode.dataset.messageId)
                : null;
            if (message) {
                void this.performMessageAction(actionButton.dataset.messageAction, message);
            }
            return;
        }

        if (event.target.closest('a, button')) {
            return;
        }

        const quoteBlock = event.target.closest('.chat-quote-block[data-quote-message-id]');
        if (!quoteBlock) {
            return;
        }

        this.revealQuotedMessage(quoteBlock.dataset.quoteMessageId);
    }

    handleMessageKeydown(event) {
        if (!(event.target instanceof Element)) {
            return;
        }
        if (event.key !== 'Enter' && event.key !== ' ') {
            return;
        }

        const quoteBlock = event.target.closest('.chat-quote-block[data-quote-message-id]');
        if (!quoteBlock) {
            return;
        }

        event.preventDefault();
        this.revealQuotedMessage(quoteBlock.dataset.quoteMessageId);
    }

    handleMessageMenuMouseEnter() {
        this.cancelMessageMenuHover();
        this.cancelMessageMenuClose();
    }

    handleMessageMenuMouseLeave(event) {
        const nextAnchor = this.resolveMessageMenuAnchor(event.relatedTarget);
        if (nextAnchor) {
            this.cancelMessageMenuHover();
            this.cancelMessageMenuClose();
            return;
        }

        this.scheduleMessageMenuClose();
    }

    async handleMessageMenuAction(event) {
        const actionButton = event.target.closest('[data-message-action]');
        if (!actionButton) {
            return;
        }

        const action = actionButton.dataset.messageAction;
        const message = this.getMessageRecord(this.activeMessageMenuId);
        if (!message) {
            this.closeMessageActionMenu();
            return;
        }

        await this.performMessageAction(action, message);
    }

    async performMessageAction(action, message) {
        if (!message) {
            return;
        }

        this.cancelMessageMenuHover();
        this.cancelMessageMenuClose();
        this.closeMessageActionMenu();

        if (action === 'quote') {
            this.pendingQuote = this.createQuotePayload(message);
            this.renderQuotePreview();
            this.chatInput?.focus();
            this.showToast(DISCUSSION_UI_TEXT.quoteInserted, 'success');
            return;
        }

        if (action === 'copy') {
            await this.copyMessageToClipboard(message);
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

    insertMentionAll() {
        if (this.currentUser?.role !== 'teacher') {
            return;
        }
        const prefix = this.chatInput?.value && !this.chatInput.value.endsWith(' ') ? ' ' : '';
        this.insertTextAtCursor(`${prefix}@\u6240\u6709\u4eba `);
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

    getMaxDiscussionAttachmentCount() {
        return Number(this.discussionAttachmentLimits.maxAttachmentCount || DEFAULT_DISCUSSION_ATTACHMENT_LIMIT);
    }

    getMaxDiscussionAttachmentBytes() {
        return Number(this.discussionAttachmentLimits.maxUploadBytes || DEFAULT_DISCUSSION_ATTACHMENT_MAX_BYTES);
    }

    updateAttachmentTriggerState() {
        if (!this.attachmentTriggerButton) {
            return;
        }

        const limitReached = this.pendingAttachments.length >= this.getMaxDiscussionAttachmentCount();
        this.attachmentTriggerButton.disabled = this.attachmentUploadInFlight || limitReached;
        if (this.attachmentUploadInFlight) {
            this.attachmentTriggerButton.title = '图片上传中';
        } else if (limitReached) {
            this.attachmentTriggerButton.title = `最多上传 ${this.getMaxDiscussionAttachmentCount()} 张图片`;
        } else {
            this.attachmentTriggerButton.title = '发送图片';
        }
    }

    formatBytes(size) {
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

    renderPendingAttachments() {
        if (!this.attachmentPreviewRow) {
            return;
        }

        this.attachmentPreviewRow.replaceChildren();
        this.attachmentPreviewRow.hidden = this.pendingAttachments.length === 0;
        if (!this.pendingAttachments.length) {
            this.updateAttachmentTriggerState();
            return;
        }

        const fragment = document.createDocumentFragment();
        this.pendingAttachments.forEach((attachment, index) => {
            const card = document.createElement('div');
            card.className = 'chat-attachment-preview-card';

            const previewLink = document.createElement('a');
            previewLink.className = 'chat-attachment-preview-link';
            previewLink.href = attachment.url || '#';
            previewLink.target = '_blank';
            previewLink.rel = 'noreferrer noopener';

            const image = document.createElement('img');
            image.src = attachment.url || '';
            image.alt = attachment.name || `图片 ${index + 1}`;
            image.loading = 'lazy';
            image.decoding = 'async';
            previewLink.appendChild(image);
            card.appendChild(previewLink);

            const meta = document.createElement('div');
            meta.className = 'chat-attachment-preview-meta';

            const name = document.createElement('strong');
            name.textContent = attachment.name || `图片 ${index + 1}`;
            meta.appendChild(name);

            const desc = document.createElement('span');
            desc.textContent = [
                attachment.width && attachment.height ? `${attachment.width}×${attachment.height}` : '',
                this.formatBytes(attachment.file_size),
            ].filter(Boolean).join(' · ') || '已上传';
            meta.appendChild(desc);
            card.appendChild(meta);

            const removeButton = document.createElement('button');
            removeButton.type = 'button';
            removeButton.className = 'chat-attachment-preview-remove';
            removeButton.textContent = '×';
            removeButton.title = '移除图片';
            removeButton.setAttribute('aria-label', `移除 ${attachment.name || '图片'}`);
            removeButton.addEventListener('click', () => {
                this.pendingAttachments.splice(index, 1);
                this.renderPendingAttachments();
            });
            card.appendChild(removeButton);

            fragment.appendChild(card);
        });

        this.attachmentPreviewRow.appendChild(fragment);
        this.updateAttachmentTriggerState();
    }

    renderQuotePreview() {
        if (!this.quotePreview) {
            return;
        }

        this.quotePreview.replaceChildren();
        this.quotePreview.hidden = !this.pendingQuote;
        if (!this.pendingQuote) {
            return;
        }

        this.quotePreview.appendChild(this.renderQuoteBlock(this.pendingQuote, {
            isComposer: true,
            showRemoveButton: true,
        }));
    }

    async queueDiscussionAttachments(files) {
        const selectedFiles = Array.isArray(files) ? files : Array.from(files || []);
        if (!selectedFiles.length || this.attachmentUploadInFlight) {
            return;
        }

        const remainingSlots = this.getMaxDiscussionAttachmentCount() - this.pendingAttachments.length;
        if (remainingSlots <= 0) {
            this.showToast(`单条消息最多发送 ${this.getMaxDiscussionAttachmentCount()} 张图片`, 'warning');
            this.updateAttachmentTriggerState();
            return;
        }

        const filesToUpload = selectedFiles.slice(0, remainingSlots);
        if (filesToUpload.length < selectedFiles.length) {
            this.showToast(`超出部分已忽略，单条消息最多 ${this.getMaxDiscussionAttachmentCount()} 张图片`, 'warning');
        }

        const maxBytes = this.getMaxDiscussionAttachmentBytes();
        const allowedTypes = new Set(['image/png', 'image/jpeg', 'image/gif', 'image/webp']);
        for (const file of filesToUpload) {
            const normalizedName = String(file.name || '').toLowerCase();
            const extAllowed = ['.png', '.jpg', '.jpeg', '.gif', '.webp'].some((ext) => normalizedName.endsWith(ext));
            if (!allowedTypes.has(file.type) && !extAllowed) {
                this.showToast('讨论区仅支持 PNG、JPG、GIF 或 WebP 图片', 'warning');
                return;
            }
            if (Number(file.size || 0) > maxBytes) {
                this.showToast(`讨论区图片大小不能超过 ${Math.round(maxBytes / 1024 / 1024)}MB`, 'warning');
                return;
            }
        }

        const formData = new FormData();
        filesToUpload.forEach((file) => formData.append('files', file));

        this.attachmentUploadInFlight = true;
        this.renderSendButtonState();
        this.updateAttachmentTriggerState();

        try {
            const data = await apiFetch(`/api/classrooms/${this.classOfferingId}/discussion-attachments`, {
                method: 'POST',
                body: formData,
            });
            if (data?.limits) {
                this.discussionAttachmentLimits = {
                    maxAttachmentCount: Number(data.limits.max_attachment_count || this.discussionAttachmentLimits.maxAttachmentCount),
                    maxUploadBytes: Number(data.limits.max_upload_bytes || this.discussionAttachmentLimits.maxUploadBytes),
                };
            }

            const attachments = Array.isArray(data?.attachments) ? data.attachments : [];
            if (attachments.length) {
                this.pendingAttachments.push(...attachments);
                this.renderPendingAttachments();
                this.showToast(`已添加 ${attachments.length} 张图片`, 'success');
            }
        } catch (error) {
            console.error('Failed to upload discussion attachments:', error);
        } finally {
            this.attachmentUploadInFlight = false;
            this.renderSendButtonState();
            this.updateAttachmentTriggerState();
        }
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
        const normalizedMessage = this.normalizeMessagePayload(message);
        const messageId = Number(normalizedMessage.id || 0);
        if (messageId && this.knownMessageIds.has(messageId)) {
            return;
        }
        if (messageId) {
            this.knownMessageIds.add(messageId);
            this.messageRecords.set(messageId, normalizedMessage);
        }

        const sender = String(normalizedMessage.sender || '课堂成员');
        const text = String(normalizedMessage.message || '');
        const role = String(normalizedMessage.role || '');
        const isCurrentUser = this.isCurrentUserMessage(normalizedMessage);
        const roleClass = role === 'teacher' ? ' teacher' : (role === 'assistant' ? ' assistant' : '');
        const initials = role === 'assistant'
            ? '助'
            : (sender.trim().slice(0, 1).toUpperCase() || '?');

        const wrapper = document.createElement('div');
        wrapper.className = `chat-message${isCurrentUser ? ' chat-self' : ''}${role === 'assistant' ? ' chat-assistant' : ''}`;
        if (messageId) {
            wrapper.dataset.messageId = String(messageId);
        }

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
        timeNode.textContent = String(normalizedMessage.timestamp || '');
        header.appendChild(timeNode);

        if (messageId) {
            header.classList.add('has-actions');
            header.appendChild(this.createMessageActionBar(messageId));
        }
        main.appendChild(header);

        if (normalizedMessage.quote) {
            main.appendChild(this.renderQuoteBlock(normalizedMessage.quote, {
                quoteMessageId: normalizedMessage.quote_message_id,
            }));
        }

        if (text) {
            const content = document.createElement('div');
            content.className = 'message-content';
            content.innerHTML = this.escape(text).replace(/\n/g, '<br>');
            main.appendChild(content);
        }

        const attachments = Array.isArray(normalizedMessage.attachments) ? normalizedMessage.attachments : [];
        if (attachments.length) {
            main.appendChild(this.renderMessageAttachments(attachments));
        }

        const customEmojis = Array.isArray(normalizedMessage.custom_emojis) ? normalizedMessage.custom_emojis : [];
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

    renderMessageAttachments(items, options = {}) {
        const list = document.createElement('div');
        list.className = `chat-message-attachments${options.compact ? ' is-compact' : ''}`;

        items.forEach((item, index) => {
            const link = document.createElement('a');
            link.className = `chat-message-attachment-link${options.quote ? ' chat-quote-attachment-link' : ''}`;
            link.href = item?.url || '#';
            link.target = '_blank';
            link.rel = 'noreferrer noopener';

            const image = document.createElement('img');
            image.className = 'chat-message-attachment-image';
            image.src = item?.url || '';
            image.alt = item?.name || `图片 ${index + 1}`;
            image.loading = 'lazy';
            image.decoding = 'async';
            link.appendChild(image);

            const meta = document.createElement('span');
            meta.className = 'chat-message-attachment-meta';
            meta.textContent = item?.name || `图片 ${index + 1}`;
            link.appendChild(meta);

            list.appendChild(link);
        });

        return list;
    }

    renderQuoteBlock(quote, options = {}) {
        const normalizedQuote = this.normalizeQuotePayload(quote, options.quoteMessageId);
        const block = document.createElement('div');
        block.className = `chat-quote-block${options.isComposer ? ' is-composer' : ''}`;
        if (!normalizedQuote) {
            return block;
        }

        const quoteMessageId = Number(options.quoteMessageId || normalizedQuote.id || 0) || null;
        if (quoteMessageId && !options.showRemoveButton) {
            block.dataset.quoteMessageId = String(quoteMessageId);
            block.classList.add('is-clickable');
            block.tabIndex = 0;
            block.setAttribute('role', 'button');
            block.setAttribute(
                'aria-label',
                `${DISCUSSION_UI_TEXT.quoteSourceAriaPrefix} ${normalizedQuote.sender} ${DISCUSSION_UI_TEXT.quoteSourceAriaSuffix}`,
            );
        }

        const header = document.createElement('div');
        header.className = 'chat-quote-header';

        const label = document.createElement('span');
        label.className = 'chat-quote-label';
        label.textContent = options.isComposer
            ? DISCUSSION_UI_TEXT.quoteActiveLabel
            : DISCUSSION_UI_TEXT.quotedMessageLabel;
        header.appendChild(label);

        const author = document.createElement('strong');
        author.textContent = normalizedQuote.sender || DISCUSSION_UI_TEXT.defaultSender;
        header.appendChild(author);

        const time = document.createElement('span');
        time.textContent = normalizedQuote.timestamp;
        header.appendChild(time);

        if (options.showRemoveButton) {
            const removeButton = document.createElement('button');
            removeButton.type = 'button';
            removeButton.className = 'chat-quote-remove';
            removeButton.textContent = '×';
            removeButton.title = DISCUSSION_UI_TEXT.cancelQuote;
            removeButton.setAttribute('aria-label', DISCUSSION_UI_TEXT.cancelQuote);
            removeButton.addEventListener('click', () => {
                this.pendingQuote = null;
                this.renderQuotePreview();
                this.chatInput?.focus();
            });
            header.appendChild(removeButton);
        }

        block.appendChild(header);

        if (normalizedQuote.message) {
            const text = document.createElement('div');
            text.className = 'chat-quote-text';
            text.innerHTML = this.escape(normalizedQuote.message).replace(/\n/g, '<br>');
            block.appendChild(text);
        } else {
            const placeholder = this.getMessageRichContentLabel(normalizedQuote);
            if (placeholder) {
                const empty = document.createElement('div');
                empty.className = 'chat-quote-empty';
                empty.textContent = placeholder;
                block.appendChild(empty);
            }
        }

        const attachments = Array.isArray(normalizedQuote.attachments) ? normalizedQuote.attachments : [];
        if (attachments.length) {
            block.appendChild(this.renderMessageAttachments(attachments, {
                compact: true,
                quote: true,
            }));
        }

        const customEmojis = Array.isArray(normalizedQuote.custom_emojis) ? normalizedQuote.custom_emojis : [];
        if (customEmojis.length) {
            block.appendChild(this.renderMessageCustomEmojis(customEmojis));
        }

        return block;
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
        if (!this.isSendRateLimited() && this.sendRateLimitUntil) {
            this.sendRateLimitUntil = 0;
            this.renderSendButtonState();
        }

        if (this.isSendRateLimited()) {
            this.showToast('\u53d1\u4fe1\u592a\u9891\u7e41\u7a0d\u540e\u518d\u53d1', 'warning');
            return;
        }

        if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
            this.showToast('课堂研讨室尚未连接成功', 'warning');
            return;
        }

        if (this.attachmentUploadInFlight) {
            this.showToast('图片仍在上传，请稍候再发送', 'warning');
            return;
        }

        const rawText = this.chatInput.value;
        const messageText = rawText.trim();
        const customEmojiIds = this.selectedCustomEmojis
            .map((item) => Number(item.id))
            .filter((item) => Number.isFinite(item));
        const attachmentIds = this.pendingAttachments
            .map((item) => Number(item.attachment_id))
            .filter((item) => Number.isFinite(item));
        const quoteMessageId = Number(this.pendingQuote?.id || 0) || null;

        if (!messageText && !customEmojiIds.length && !attachmentIds.length && !quoteMessageId) {
            return;
        }

        this.ws.send(JSON.stringify({
            action: 'send_message',
            text: messageText,
            custom_emoji_ids: customEmojiIds,
            used_unicode_emojis: this.extractKnownEmojis(messageText),
            attachment_ids: attachmentIds,
            quote_message_id: quoteMessageId,
        }));

        this.chatInput.value = '';
        this.resizeInput();
        this.selectedCustomEmojis = [];
        this.pendingAttachments = [];
        this.pendingQuote = null;
        this.renderSelectedCustomEmojis();
        this.renderPendingAttachments();
        this.renderQuotePreview();
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

    normalizeQuotePayload(quote, fallbackId = null) {
        if (!quote || typeof quote !== 'object') {
            return null;
        }

        return {
            id: Number(quote?.id || fallbackId || 0) || null,
            sender: String(quote?.sender || DISCUSSION_UI_TEXT.defaultSender),
            role: String(quote?.role || 'student'),
            message: String(quote?.message || ''),
            timestamp: String(quote?.timestamp || ''),
            logged_at: quote?.logged_at || null,
            message_type: String(quote?.message_type || 'text'),
            custom_emojis: Array.isArray(quote?.custom_emojis) ? quote.custom_emojis : [],
            attachments: Array.isArray(quote?.attachments) ? quote.attachments : [],
        };
    }

    normalizeMessagePayload(message) {
        const normalized = {
            ...message,
            id: Number(message?.id || 0) || null,
            sender: String(message?.sender || DISCUSSION_UI_TEXT.defaultSender),
            role: String(message?.role || 'student'),
            message: String(message?.message || ''),
            timestamp: String(message?.timestamp || ''),
            logged_at: message?.logged_at || null,
            message_type: String(message?.message_type || 'text'),
            custom_emojis: Array.isArray(message?.custom_emojis) ? message.custom_emojis : [],
            attachments: Array.isArray(message?.attachments) ? message.attachments : [],
            quote: this.normalizeQuotePayload(message?.quote, message?.quote_message_id),
            quote_message_id: Number(message?.quote_message_id || 0) || null,
        };
        return normalized;
    }

    getMessageRecord(messageId) {
        const normalizedId = Number(messageId || 0);
        if (!Number.isFinite(normalizedId) || normalizedId <= 0) {
            return null;
        }
        return this.messageRecords.get(normalizedId) || null;
    }

    createQuotePayload(message) {
        if (!message) {
            return null;
        }

        return {
            id: Number(message.id || 0) || null,
            sender: String(message.sender || DISCUSSION_UI_TEXT.defaultSender),
            role: String(message.role || 'student'),
            message: String(message.message || ''),
            timestamp: String(message.timestamp || ''),
            logged_at: message.logged_at || null,
            message_type: String(message.message_type || 'text'),
            custom_emojis: Array.isArray(message.custom_emojis) ? [...message.custom_emojis] : [],
            attachments: Array.isArray(message.attachments) ? [...message.attachments] : [],
        };
    }

    getMessageNodeById(messageId) {
        const normalizedId = Number(messageId || 0);
        if (!Number.isFinite(normalizedId) || normalizedId <= 0 || !this.messagesBox) {
            return null;
        }

        return this.messagesBox.querySelector(`.chat-message[data-message-id="${normalizedId}"]`);
    }

    clearQuotedSourceHighlight() {
        const highlightedNode = this.messagesBox?.querySelector('.chat-message.is-quoted-source');
        highlightedNode?.classList.remove('is-quoted-source');
        if (this.messageHighlightTimer) {
            window.clearTimeout(this.messageHighlightTimer);
            this.messageHighlightTimer = null;
        }
    }

    highlightQuotedSource(messageNode) {
        if (!(messageNode instanceof HTMLElement)) {
            return;
        }

        this.clearQuotedSourceHighlight();
        messageNode.classList.remove('is-quoted-source');
        void messageNode.offsetWidth;
        messageNode.classList.add('is-quoted-source');
        this.messageHighlightTimer = window.setTimeout(() => {
            messageNode.classList.remove('is-quoted-source');
            this.messageHighlightTimer = null;
        }, MESSAGE_SOURCE_HIGHLIGHT_MS);
    }

    revealQuotedMessage(messageId, options = {}) {
        const normalizedId = Number(messageId || 0);
        if (!Number.isFinite(normalizedId) || normalizedId <= 0) {
            return;
        }

        const messageNode = this.getMessageNodeById(normalizedId);
        if (!messageNode) {
            if (options.showMissingToast !== false) {
                this.showToast(DISCUSSION_UI_TEXT.quoteSourceMissing, 'info');
            }
            return;
        }

        messageNode.scrollIntoView({
            block: 'center',
            behavior: options.behavior || 'smooth',
        });
        this.highlightQuotedSource(messageNode);
    }

    getMessageRichContentLabel(message) {
        const labels = [];
        if (Array.isArray(message?.attachments) && message.attachments.length) {
            labels.push('图片');
        }
        if (Array.isArray(message?.custom_emojis) && message.custom_emojis.length) {
            labels.push('表情');
        }
        return labels.length ? `${labels.join(' + ')}消息` : '';
    }

    createMessageActionBar(messageId) {
        const normalizedId = Number(messageId || 0);
        if (!Number.isFinite(normalizedId) || normalizedId <= 0) {
            return document.createDocumentFragment();
        }

        const bar = document.createElement('div');
        bar.className = 'chat-message-actions';
        bar.setAttribute('aria-label', DISCUSSION_UI_TEXT.messageActionsLabel);

        const quoteButton = document.createElement('button');
        quoteButton.type = 'button';
        quoteButton.className = 'chat-message-action-btn';
        quoteButton.dataset.messageAction = 'quote';
        quoteButton.dataset.messageId = String(normalizedId);
        quoteButton.textContent = DISCUSSION_UI_TEXT.quoteActionLabel;
        quoteButton.title = DISCUSSION_UI_TEXT.quoteActionTitle;
        bar.appendChild(quoteButton);

        const copyButton = document.createElement('button');
        copyButton.type = 'button';
        copyButton.className = 'chat-message-action-btn';
        copyButton.dataset.messageAction = 'copy';
        copyButton.dataset.messageId = String(normalizedId);
        copyButton.textContent = DISCUSSION_UI_TEXT.copyActionLabel;
        copyButton.title = DISCUSSION_UI_TEXT.copyActionTitle;
        bar.appendChild(copyButton);

        return bar;
    }

    openMessageActionMenu(messageNode, anchor = null) {
        if (!this.messageMenu || !messageNode) {
            return;
        }

        const messageId = Number(messageNode.dataset.messageId || 0);
        if (!Number.isFinite(messageId) || messageId <= 0) {
            return;
        }

        this.activeMessageMenuId = messageId;
        this.pendingMessageMenuId = messageId;
        this.pendingMessageMenuAnchor = anchor;
        this.messageMenu.hidden = false;

        const anchorNode = anchor instanceof Element
            ? anchor
            : messageNode.querySelector('.chat-message-main');
        const rect = anchorNode?.getBoundingClientRect();
        if (!rect) {
            return;
        }

        window.requestAnimationFrame(() => {
            const menuWidth = this.messageMenu?.offsetWidth || 0;
            const menuHeight = this.messageMenu?.offsetHeight || 0;
            const gap = 10;
            const preferLeft = messageNode.classList.contains('chat-self')
                ? rect.left
                : rect.right - menuWidth;
            const preferTop = rect.top - menuHeight - gap;
            const fallbackTop = rect.bottom + gap;

            const clampedLeft = Math.min(
                Math.max(12, preferLeft),
                Math.max(12, window.innerWidth - menuWidth - 12),
            );
            const clampedTop = Math.min(
                Math.max(12, preferTop >= 12 ? preferTop : fallbackTop),
                Math.max(12, window.innerHeight - menuHeight - 12),
            );
            if (this.messageMenu) {
                this.messageMenu.style.left = `${clampedLeft}px`;
                this.messageMenu.style.top = `${clampedTop}px`;
            }
        });
    }

    closeMessageActionMenu() {
        if (!this.messageMenu) {
            return;
        }
        this.messageMenu.hidden = true;
        this.activeMessageMenuId = null;
        this.pendingMessageMenuId = null;
        this.pendingMessageMenuAnchor = null;
    }

    buildAbsoluteUrl(url) {
        const normalizedUrl = String(url || '').trim();
        if (!normalizedUrl) {
            return '';
        }

        try {
            return new URL(normalizedUrl, window.location.origin).href;
        } catch (error) {
            return normalizedUrl;
        }
    }

    buildAttachmentCopyLines(items, options = {}) {
        const attachments = Array.isArray(items) ? items : [];
        const label = options.label || '[图片]';
        return attachments.map((item, index) => {
            const name = String(item?.name || `图片 ${index + 1}`);
            const absoluteUrl = this.buildAbsoluteUrl(item?.url || '');
            return absoluteUrl ? `${label} ${name} (${absoluteUrl})` : `${label} ${name}`;
        });
    }

    buildEmojiCopyLine(items, label = '[表情]') {
        const names = Array.isArray(items)
            ? items.map((item) => item?.name).filter(Boolean)
            : [];
        if (!names.length) {
            return '';
        }
        return `${label} ${names.join(', ')}`;
    }

    buildMessageCopyHtmlAttachmentList(items, options = {}) {
        const attachments = Array.isArray(items) ? items : [];
        if (!attachments.length) {
            return '';
        }

        const title = options.title || '图片';
        const itemWidth = options.compact ? 108 : 144;
        const cards = attachments.map((item, index) => {
            const name = this.escape(item?.name || `${title} ${index + 1}`);
            const absoluteUrl = this.buildAbsoluteUrl(item?.url || '');
            const safeUrl = this.escape(absoluteUrl || item?.url || '');
            return [
                `<a href="${safeUrl}" style="display:flex;flex-direction:column;gap:6px;width:${itemWidth}px;padding:6px;border:1px solid rgba(148,163,184,0.28);border-radius:14px;text-decoration:none;background:#fff;color:#0f172a;">`,
                `<img src="${safeUrl}" alt="${name}" style="display:block;width:100%;aspect-ratio:1 / 1;object-fit:cover;border-radius:10px;background:#e2e8f0;">`,
                `<span style="font-size:12px;line-height:1.35;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${name}</span>`,
                '</a>',
            ].join('');
        }).join('');

        return [
            `<div style="margin-top:${options.compact ? '8px' : '12px'};">`,
            `<div style="margin-bottom:6px;font-size:12px;font-weight:600;color:#475569;">${this.escape(title)}</div>`,
            `<div style="display:flex;flex-wrap:wrap;gap:8px;">${cards}</div>`,
            '</div>',
        ].join('');
    }

    buildMessageCopyHtml(message) {
        const normalizedMessage = this.normalizeMessagePayload(message);
        if (!normalizedMessage) {
            return '';
        }

        const quoteEmojiLine = this.buildEmojiCopyLine(normalizedMessage.quote?.custom_emojis, '引用表情：');
        const emojiLine = this.buildEmojiCopyLine(normalizedMessage.custom_emojis, '表情：');
        const quotePlaceholder = normalizedMessage.quote && !normalizedMessage.quote.message
            ? this.getMessageRichContentLabel(normalizedMessage.quote)
            : '';
        const messagePlaceholder = !normalizedMessage.message
            ? this.getMessageRichContentLabel(normalizedMessage)
            : '';

        return [
            '<div style="font-family:Segoe UI,Arial,sans-serif;color:#0f172a;line-height:1.6;">',
            `<div style="font-size:14px;font-weight:700;">${this.escape(normalizedMessage.sender)} <span style="font-weight:400;color:#64748b;">${this.escape(normalizedMessage.timestamp)}</span></div>`,
            normalizedMessage.quote
                ? [
                    '<blockquote style="margin:12px 0 0;padding:10px 12px;border-left:3px solid #60a5fa;border-radius:12px;background:#eff6ff;">',
                    `<div style="margin-bottom:6px;font-size:12px;color:#64748b;"><strong style="color:#0f172a;">引用消息</strong> ${this.escape(normalizedMessage.quote.sender)} ${this.escape(normalizedMessage.quote.timestamp)}</div>`,
                    normalizedMessage.quote.message
                        ? `<div style="font-size:13px;color:#334155;">${this.escape(normalizedMessage.quote.message).replace(/\n/g, '<br>')}</div>`
                        : (quotePlaceholder ? `<div style="font-size:12px;color:#64748b;">${this.escape(quotePlaceholder)}</div>` : ''),
                    this.buildMessageCopyHtmlAttachmentList(normalizedMessage.quote.attachments, {
                        title: '引用图片',
                        compact: true,
                    }),
                    quoteEmojiLine
                        ? `<div style="margin-top:8px;font-size:12px;color:#475569;">${this.escape(quoteEmojiLine)}</div>`
                        : '',
                    '</blockquote>',
                ].join('')
                : '',
            normalizedMessage.message
                ? `<div style="margin-top:12px;font-size:14px;color:#0f172a;">${this.escape(normalizedMessage.message).replace(/\n/g, '<br>')}</div>`
                : (messagePlaceholder ? `<div style="margin-top:12px;font-size:12px;color:#64748b;">${this.escape(messagePlaceholder)}</div>` : ''),
            this.buildMessageCopyHtmlAttachmentList(normalizedMessage.attachments),
            emojiLine
                ? `<div style="margin-top:10px;font-size:12px;color:#475569;">${this.escape(emojiLine)}</div>`
                : '',
            '</div>',
        ].join('');
    }

    buildMessageCopyText(message) {
        const normalizedMessage = this.normalizeMessagePayload(message);
        if (!normalizedMessage) {
            return '';
        }

        const nextLines = [];
        nextLines.push(`${normalizedMessage.sender || '课堂成员'} ${normalizedMessage.timestamp || ''}`.trim());

        if (normalizedMessage.quote) {
            nextLines.push(`引用 ${normalizedMessage.quote.sender || '课堂成员'} ${normalizedMessage.quote.timestamp || ''}`.trim());
            if (normalizedMessage.quote.message) {
                nextLines.push(...String(normalizedMessage.quote.message).split('\n').map((line) => `> ${line}`));
            } else {
                const quotePlaceholder = this.getMessageRichContentLabel(normalizedMessage.quote);
                if (quotePlaceholder) {
                    nextLines.push(`> [${quotePlaceholder}]`);
                }
            }

            nextLines.push(...this.buildAttachmentCopyLines(normalizedMessage.quote.attachments, {
                label: '> [引用图片]',
            }));
            const quoteEmojiLine = this.buildEmojiCopyLine(normalizedMessage.quote.custom_emojis, '> [引用表情]');
            if (quoteEmojiLine) {
                nextLines.push(quoteEmojiLine);
            }
        }

        if (normalizedMessage.message) {
            nextLines.push(normalizedMessage.message);
        } else {
            const messagePlaceholder = this.getMessageRichContentLabel(normalizedMessage);
            if (messagePlaceholder) {
                nextLines.push(`[${messagePlaceholder}]`);
            }
        }

        nextLines.push(...this.buildAttachmentCopyLines(normalizedMessage.attachments));
        const emojiLine = this.buildEmojiCopyLine(normalizedMessage.custom_emojis);
        if (emojiLine) {
            nextLines.push(emojiLine);
        }

        return nextLines.filter(Boolean).join('\n');
    }

    async copyTextToClipboard(text) {
        const normalizedText = String(text || '');
        if (!normalizedText) {
            return;
        }

        if (navigator.clipboard?.writeText) {
            await navigator.clipboard.writeText(normalizedText);
            return;
        }

        const textarea = document.createElement('textarea');
        textarea.value = normalizedText;
        textarea.setAttribute('readonly', 'readonly');
        textarea.style.position = 'fixed';
        textarea.style.opacity = '0';
        textarea.style.pointerEvents = 'none';
        document.body.appendChild(textarea);
        textarea.select();
        document.execCommand('copy');
        textarea.remove();
    }

    async copyClipboardPayload(text, html = '') {
        const normalizedText = String(text || '');
        if (!normalizedText) {
            return;
        }

        if (navigator.clipboard?.write && typeof window.ClipboardItem === 'function' && html) {
            try {
                const clipboardItem = new window.ClipboardItem({
                    'text/plain': new Blob([normalizedText], { type: 'text/plain' }),
                    'text/html': new Blob([String(html)], { type: 'text/html' }),
                });
                await navigator.clipboard.write([clipboardItem]);
                return;
            } catch (error) {
                // Fallback to plain text copy when rich clipboard payload is blocked.
            }
        }

        await this.copyTextToClipboard(normalizedText);
    }

    async copyMessageToClipboard(message) {
        const normalizedMessage = this.normalizeMessagePayload(message);
        const plainText = this.buildMessageCopyText(normalizedMessage);
        const html = this.buildMessageCopyHtml(normalizedMessage);

        try {
            await this.copyClipboardPayload(plainText, html);
            this.showToast(DISCUSSION_UI_TEXT.copySuccess, 'success');
            return;
        } catch (error) {
            console.error('Failed to copy message:', error);
            this.showToast(DISCUSSION_UI_TEXT.copyFailed, 'error');
        }
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
