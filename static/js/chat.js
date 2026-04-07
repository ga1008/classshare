export class ClassroomChat {
    constructor(options) {
        this.classOfferingId = options.classOfferingId;
        this.messagesBox = document.getElementById(options.chatMessagesContainerId);
        this.chatInput = document.getElementById(options.chatInputId);
        this.chatForm = document.getElementById(options.chatFormId);
        this.statusIndicator = document.getElementById(options.statusIndicatorId);
        this.statusText = document.getElementById(options.statusTextId);
        this.displayNameEl = document.getElementById(options.displayNameId);
        this.switchAliasButton = document.getElementById(options.switchAliasButtonId);
        this.historyLoader = document.getElementById(options.historyLoaderId);
        this.historyLoadButton = document.getElementById(options.historyLoadButtonId);
        this.currentUser = options.currentUser || {};

        this.ws = null;
        this.onFileEvent = null;
        this.displayName = null;
        this.oldestMessageId = null;
        this.hasMoreHistory = false;
        this.isLoadingHistory = false;
        this.knownMessageIds = new Set();
    }

    init() {
        if (!this.messagesBox || !this.chatInput || !this.chatForm) {
            console.error('ClassroomChat: required DOM elements not found.');
            return;
        }

        const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        this.ws = new WebSocket(`${wsProtocol}//${window.location.host}/ws/${this.classOfferingId}`);

        this.ws.onmessage = this.handleMessage.bind(this);
        this.ws.onopen = () => this.updateConnectionState(true);
        this.ws.onerror = (error) => {
            console.error('WebSocket error:', error);
            this.updateConnectionState(false, '连接异常');
            this.showToast('课堂研讨室连接出现错误', 'error');
        };
        this.ws.onclose = () => {
            this.updateConnectionState(false, '连接已断开');
            this.appendSystemMessage('连接已断开，请刷新页面后重试。');
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

        this.chatInput.addEventListener('input', () => {
            this.chatInput.style.height = 'auto';
            this.chatInput.style.height = `${Math.min(this.chatInput.scrollHeight, 160)}px`;
        });

        this.messagesBox.addEventListener('scroll', () => this.updateHistoryLoader());
        this.switchAliasButton?.addEventListener('click', () => this.requestAliasSwitch());
        this.historyLoadButton?.addEventListener('click', () => this.requestOlderHistory());
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
    }

    handleMessage(event) {
        try {
            const data = JSON.parse(event.data);

            if (data.type === 'history') {
                this.handleHistoryPayload(data);
                return;
            }

            if (data.type === 'chat') {
                this.appendChatMessage(data, { scrollToBottom: this.isNearBottom() });
                return;
            }

            if (data.type === 'user_display_name') {
                this.updateDisplayName(data);
                return;
            }

            if (data.type === 'alias_switch_result') {
                this.showToast(data.message || (data.success ? '代号已更新' : '代号切换失败'), data.success ? 'success' : 'warning');
                return;
            }

            if (data.type === 'system') {
                this.appendSystemMessage(data.message, { scrollToBottom: this.isNearBottom() });
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

        if (this.displayNameEl) {
            this.displayNameEl.textContent = this.displayName || '分配中...';
        }

        if (this.switchAliasButton) {
            const canSwitch = Boolean(payload.can_switch_alias);
            const remainingCount = Number(payload.remaining_alias_count || 0);
            this.switchAliasButton.disabled = !canSwitch;
            this.switchAliasButton.title = canSwitch
                ? `当前还有 ${remainingCount} 个可选新代号`
                : '当前没有可用的新代号';
        }
    }

    requestAliasSwitch() {
        if (!this.ws || this.ws.readyState !== WebSocket.OPEN || !this.switchAliasButton || this.switchAliasButton.disabled) {
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

    appendChatMessage(message, options = {}) {
        const messageId = Number(message.id || 0);
        if (messageId && this.knownMessageIds.has(messageId)) {
            return;
        }
        if (messageId) {
            this.knownMessageIds.add(messageId);
        }

        const wrapper = document.createElement('div');
        const sender = String(message.sender || '课堂成员');
        const text = String(message.message || '');
        const role = String(message.role || '');
        const myName = this.displayName || this.currentUser.name;
        const isCurrentUser = sender === myName && role === this.currentUser.role;
        const initials = sender.trim().slice(0, 1).toUpperCase() || '?';

        wrapper.className = `chat-message${isCurrentUser ? ' chat-self' : ''}`;
        wrapper.innerHTML = `
            <div class="chat-message-row">
                <div class="chat-avatar" aria-hidden="true">${this.escape(initials)}</div>
                <div class="chat-message-main">
                    <div class="chat-message-header">
                        <span class="sender${role === 'teacher' ? ' teacher' : ''}">${this.escape(sender)}</span>
                        <span class="time">${this.escape(message.timestamp || '')}</span>
                    </div>
                    <div class="message-content">${this.escape(text).replace(/\n/g, '<br>')}</div>
                </div>
            </div>
        `;

        this.insertMessageNode(wrapper, options);
    }

    appendSystemMessage(text, options = {}) {
        const wrapper = document.createElement('div');
        wrapper.className = 'chat-message system';
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
        const message = this.chatInput.value.trim();
        if (!message || !this.ws || this.ws.readyState !== WebSocket.OPEN) {
            return;
        }

        this.ws.send(message);
        this.chatInput.value = '';
        this.chatInput.style.height = '';
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
