/**
 * chat.js
 * Handles WebSocket connection and Chat UI for the classroom.
 */

export class ClassroomChat {
    constructor(classOfferingId, chatMessagesContainerId, chatInputId, sendButtonId) {
        this.classOfferingId = classOfferingId;
        this.messagesBox = document.getElementById(chatMessagesContainerId);
        this.chatInput = document.getElementById(chatInputId);
        this.sendBtn = document.getElementById(sendButtonId);
        this.ws = null;

        // Callbacks for external components
        this.onFileEvent = null; // Called when a file is uploaded/deleted to refresh file list
    }

    init() {
        if (!this.messagesBox || !this.chatInput || !this.sendBtn) {
            console.error("ClassroomChat: Required DOM elements not found.");
            return;
        }

        const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        this.ws = new WebSocket(`${wsProtocol}//${window.location.host}/ws/${this.classOfferingId}`);

        this.ws.onmessage = this.handleMessage.bind(this);

        this.ws.onopen = () => {
            console.log("WebSocket connected.");
            const statusEl = document.getElementById('ws-status');
            if (statusEl) {
                statusEl.classList.add('status-online');
                statusEl.title = '连接正常';
            }
        };

        this.ws.onerror = (error) => {
            console.error("WebSocket error:", error);
            if (window.UI) window.UI.showToast("聊天室连接出现错误", "error");
        };

        this.ws.onclose = () => {
            console.warn("WebSocket connection closed.");
            this.appendSystemMessage("连接已断开，请刷新页面重试。");
            const statusEl = document.getElementById('ws-status');
            if (statusEl) {
                statusEl.classList.remove('status-online');
                statusEl.title = '连接已断开';
            }
        };

        // Bind UI events
        this.sendBtn.addEventListener('submit', (e) => {
            e.preventDefault();
            this.sendMessage();
        });
        this.chatInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                this.sendMessage();
            }
        });
    }

    handleMessage(event) {
        try {
            const data = JSON.parse(event.data);

            if (data.type === 'chat') {
                this.appendChatMessage(data);
            } else if (data.type === 'system') {
                this.appendSystemMessage(data.message);

                // Trigger external file refresh if system message is about files
                if (data.message && (data.message.includes('上传了新文件') || data.message.includes('删除了文件'))) {
                    if (typeof this.onFileEvent === 'function') {
                        this.onFileEvent();
                    }
                }
            } else if (data.type === 'history') {
                // Render history
                if (Array.isArray(data.data)) {
                    data.data.forEach(msg => {
                        if (msg.type === 'chat') this.appendChatMessage(msg);
                        else if (msg.type === 'system') this.appendSystemMessage(msg.message);
                    });
                }
            }
        } catch (error) {
            console.error("Error parsing WebSocket message:", error, event.data);
        }
    }

    appendChatMessage(msg) {
        const msgDiv = document.createElement('div');
        msgDiv.className = 'chat-message';

        // Add special class for teacher
        const isTeacher = msg.role === 'teacher' || msg.sender.includes('教师');
        const senderClass = isTeacher ? 'sender teacher' : 'sender';

        msgDiv.innerHTML = `
            <div class="chat-message-header">
                <span class="${senderClass}">${window.UI ? window.UI.escapeHtml(msg.sender) : msg.sender}</span>
                <span class="time">${msg.timestamp}</span>
            </div>
            <div class="message-content">${window.UI ? window.UI.escapeHtml(msg.message) : msg.message}</div>
        `;

        this.messagesBox.appendChild(msgDiv);
        this.scrollToBottom();
    }

    appendSystemMessage(message) {
        const msgDiv = document.createElement('div');
        msgDiv.className = 'chat-message system';
        msgDiv.innerHTML = `<span class="message-content">${window.UI ? window.UI.escapeHtml(message) : message}</span>`;

        this.messagesBox.appendChild(msgDiv);
        this.scrollToBottom();
    }

    sendMessage() {
        const message = this.chatInput.value.trim();
        if (message && this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(message);
            this.chatInput.value = '';
        }
    }

    scrollToBottom() {
        this.messagesBox.scrollTop = this.messagesBox.scrollHeight;
    }
}
