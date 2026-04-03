export class ClassroomChat {
    constructor(options) {
        this.classOfferingId = options.classOfferingId;
        this.messagesBox = document.getElementById(options.chatMessagesContainerId);
        this.chatInput = document.getElementById(options.chatInputId);
        this.chatForm = document.getElementById(options.chatFormId);
        this.statusIndicator = document.getElementById(options.statusIndicatorId);
        this.statusText = document.getElementById(options.statusTextId);
        this.currentUser = options.currentUser || {};

        this.ws = null;
        this.onFileEvent = null;
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
            if (window.UI) {
                window.UI.showToast('课堂研讨室连接出现错误', 'error');
            }
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
                if (Array.isArray(data.data)) {
                    data.data.forEach((item) => {
                        if (item.type === 'chat') this.appendChatMessage(item);
                        if (item.type === 'system') this.appendSystemMessage(item.message);
                    });
                }
                return;
            }

            if (data.type === 'chat') {
                this.appendChatMessage(data);
                return;
            }

            if (data.type === 'system') {
                this.appendSystemMessage(data.message);
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

    appendChatMessage(message) {
        this.removeEmptyState();

        const wrapper = document.createElement('div');
        const sender = String(message.sender || '课堂成员');
        const text = String(message.message || '');
        const role = String(message.role || '');
        const isCurrentUser = sender === this.currentUser.name && role === this.currentUser.role;
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

        this.messagesBox.appendChild(wrapper);
        this.scrollToBottom();
    }

    appendSystemMessage(text) {
        this.removeEmptyState();

        const wrapper = document.createElement('div');
        wrapper.className = 'chat-message system';
        wrapper.innerHTML = `<span class="message-content">${this.escape(String(text || '系统消息'))}</span>`;

        this.messagesBox.appendChild(wrapper);
        this.scrollToBottom();
    }

    sendMessage() {
        const message = this.chatInput.value.trim();
        if (!message || !this.ws || this.ws.readyState !== WebSocket.OPEN) return;

        this.ws.send(message);
        this.chatInput.value = '';
        this.chatInput.style.height = '';
    }

    removeEmptyState() {
        const emptyState = document.getElementById('chat-empty-state');
        if (emptyState) {
            emptyState.remove();
        }
    }

    scrollToBottom() {
        this.messagesBox.scrollTop = this.messagesBox.scrollHeight;
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
