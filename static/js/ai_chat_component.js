/**
 * AIChatComponent (V3)
 * 封装了课堂AI助手的所有功能, 包括：
 * ...
 * 7. 消息/代码块复制
 *
 * 依赖:
 * - safeMarkedParse() (来自 tools.js)
 * - showMessage() (来自 classroom_main_v4.html 或 tools.js)
 */
class AIChatComponent {
    constructor(options) {
        this.classOfferingId = options.classOfferingId;
        if (!this.classOfferingId) {
            console.error("AIChatComponent: classOfferingId is required.");
        }

        // --- 状态管理 ---
        this.currentSessionUUID = null;
        this.pendingFiles = [];
        this.isLoading = false;

        // --- 新增: 深度思考状态 ---
        this.isDeepThinking = false;

        // 获取深度思考按钮
        this.deepThinkBtn = document.getElementById('ai-deep-think-btn');

        // --- 新增: 思考过程状态 ---

        // --- 窗口交互状态 ---
        this.isResizing = false;
        this.resizeInfo = {};

        // --- DOM 元素 ---
        this.fab = document.getElementById('ai-chat-fab');
        this.modal = document.getElementById('ai-chat-modal');
        this.modalContainer = this.modal.querySelector('.ai-chat-container');
        this.closeBtn = document.getElementById('ai-chat-btn-close');
        this.newSessionBtn = document.getElementById('ai-chat-btn-new');
        this.fullscreenBtn = document.getElementById('ai-chat-btn-fullscreen');
        this.messagesBox = document.getElementById('ai-chat-messages-box');
        this.textarea = document.getElementById('ai-chat-textarea');
        this.sendBtn = document.getElementById('ai-chat-btn-send');
        this.attachBtn = document.getElementById('ai-chat-btn-attach');
        this.fileInput = document.getElementById('ai-chat-file-input');
        this.previewsBox = document.getElementById('ai-chat-previews');

        // --- SVG 图标 ---
        this.iconMaximize = `... (内容同 V2) ...`;
        this.iconMinimize = `... (内容同 V2) ...`;

        // *** 新增: 复制图标 ***
        this.iconCopy = `
            <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
                <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
            </svg>`;
        this.iconCheck = `
            <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M20 6L9 17l-5-5"></path>
            </svg>`;

        // 绑定 resize 拖拽事件处理器
        this.doResizeHandler = this.doResize.bind(this);
        this.stopResizeHandler = this.stopResize.bind(this);
    }

    /**
     * 初始化组件，绑定所有事件
     */
    init() {
        // (从构造函数中填充 SVG)
        this.iconMaximize = `
            <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M8 3H5a2 2 0 0 0-2 2v3m18 0V5a2 2 0 0 0-2-2h-3m0 18h3a2 2 0 0 0 2-2v-3M3 16v3a2 2 0 0 0 2 2h3"/>
            </svg>`;
        this.iconMinimize = `
            <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M8 3v3a2 2 0 0 1-2 2H3m18 0h-3a2 2 0 0 1-2-2V3m0 18v-3a2 2 0 0 1 2-2h3M3 16h3a2 2 0 0 1 2 2v3"/>
            </svg>`;

        this.bindWindowEvents();
        this.bindChatEvents();
        this.bindResizeEvents();
    }

    /**
     * 安全的消息渲染方法
     */
    safeRenderMessage(role, content, attachments = [], thinkingContent = '') {
        try {
            this.renderMessage(role, content, attachments, thinkingContent);
        } catch (error) {
            console.error('渲染消息时出错:', error);
            // 降级处理：使用最简单的渲染方式
            const msgDiv = document.createElement('div');
            msgDiv.className = `ai-chat-message ${role}`;

            const bubble = document.createElement('div');
            bubble.className = 'bubble';

            const p = document.createElement('p');
            p.textContent = content;
            bubble.appendChild(p);

            msgDiv.appendChild(bubble);
            this.messagesBox.appendChild(msgDiv);
            this.scrollToBottom();
        }
    }

    // ==========================================================
    // 1. 窗口控制 (打开/关闭/全屏)
    // ==========================================================

    // (bindWindowEvents, openChat, closeChat, toggleFullscreen 保持不变)
    bindWindowEvents() {
        this.fab.addEventListener('click', this.openChat.bind(this));
        this.closeBtn.addEventListener('click', this.closeChat.bind(this));
        this.fullscreenBtn.addEventListener('click', this.toggleFullscreen.bind(this));
    }
    openChat() {
        this.modal.style.display = 'block';
        this.fab.style.display = 'none';
        if (!this.currentSessionUUID) {
            this.loadOrCreateSession();
        }
        this.textarea.focus();
    }
    closeChat() {
        this.modal.style.display = 'none';
        this.fab.style.display = 'block';
    }
    toggleFullscreen() {
        const isFullscreen = this.modalContainer.classList.toggle('fullscreen');
        if (isFullscreen) {
            this.fullscreenBtn.innerHTML = this.iconMinimize;
            this.fullscreenBtn.title = '退出全屏';
            this.modalContainer.style.width = '';
            this.modalContainer.style.height = '';
            this.modalContainer.style.top = '';
            this.modalContainer.style.bottom = '';
            this.modalContainer.style.left = '';
            this.modalContainer.style.right = '';
        } else {
            this.fullscreenBtn.innerHTML = this.iconMaximize;
            this.fullscreenBtn.title = '全屏';
            this.modalContainer.style.width = '400px';
            this.modalContainer.style.height = '600px';
            this.modalContainer.style.top = '';
            this.modalContainer.style.bottom = '30px';
            this.modalContainer.style.left = '';
            this.modalContainer.style.right = '30px';
        }
    }

    // ==========================================================
    // 2. 拖拽调整大小 (Resize Logic)
    // ==========================================================

    // (bindResizeEvents, initResize, doResize, stopResize 保持不变)
    bindResizeEvents() {
        this.modalContainer.querySelectorAll('.resizer').forEach(resizer => {
            resizer.addEventListener('mousedown', this.initResize.bind(this));
        });
    }
    initResize(e) {
        e.preventDefault();
        if (this.modalContainer.classList.contains('fullscreen')) {
            return;
        }
        this.isResizing = true;
        const rect = this.modalContainer.getBoundingClientRect();
        this.resizeInfo = {
            startX: e.clientX,
            startY: e.clientY,
            startWidth: rect.width,
            startHeight: rect.height,
            startTop: rect.top,
            startBottom: rect.bottom,
            startLeft: rect.left,
            startRight: rect.right,
            direction: e.target.classList.contains('resizer-top-left') ? 'top-left' :
                         e.target.classList.contains('resizer-top-right') ? 'top-right' :
                         e.target.classList.contains('resizer-bottom-left') ? 'bottom-left' :
                         e.target.classList.contains('resizer-bottom-right') ? 'bottom-right' :
                         e.target.classList.contains('resizer-top') ? 'top' :
                         e.target.classList.contains('resizer-bottom') ? 'bottom' :
                         e.target.classList.contains('resizer-left') ? 'left' :
                         'right'
        };
        this.modalContainer.style.top = `${this.resizeInfo.startTop}px`;
        this.modalContainer.style.left = `${this.resizeInfo.startLeft}px`;
        this.modalContainer.style.bottom = 'auto';
        this.modalContainer.style.right = 'auto';
        document.addEventListener('mousemove', this.doResizeHandler);
        document.addEventListener('mouseup', this.stopResizeHandler);
    }
    doResize(e) {
        if (!this.isResizing) return;
        const dx = e.clientX - this.resizeInfo.startX;
        const dy = e.clientY - this.resizeInfo.startY;
        let newWidth = this.resizeInfo.startWidth;
        let newHeight = this.resizeInfo.startHeight;
        let newTop = this.resizeInfo.startTop;
        let newLeft = this.resizeInfo.startLeft;
        const dir = this.resizeInfo.direction;
        if (dir.includes('left')) { newWidth -= dx; } else if (dir.includes('right')) { newWidth += dx; }
        if (dir.includes('top')) { newHeight -= dy; } else if (dir.includes('bottom')) { newHeight += dy; }
        const minWidth = 350; const minHeight = 400;
        const maxWidth = window.innerWidth - newLeft; const maxHeight = window.innerHeight - newTop;
        if (newWidth < minWidth) newWidth = minWidth;
        if (newHeight < minHeight) newHeight = minHeight;
        if (newWidth > maxWidth) newWidth = maxWidth;
        if (newHeight > maxHeight) newHeight = maxHeight;
        if (dir.includes('left')) { if (newWidth > minWidth) { newLeft = this.resizeInfo.startLeft + dx; } }
        if (dir.includes('top')) { if (newHeight > minHeight) { newTop = this.resizeInfo.startTop + dy; } }
        this.modalContainer.style.width = `${newWidth}px`;
        this.modalContainer.style.height = `${newHeight}px`;
        this.modalContainer.style.top = `${newTop}px`;
        this.modalContainer.style.left = `${newLeft}px`;
    }
    stopResize() {
        if (!this.isResizing) return;
        this.isResizing = false;
        document.removeEventListener('mousemove', this.doResizeHandler);
        document.removeEventListener('mouseup', this.stopResizeHandler);
    }


    // ==========================================================
    // 3. 聊天核心逻辑 (API, Event)
    // ==========================================================

    // (bindChatEvents 保持不变)
    bindChatEvents() {
        this.newSessionBtn.addEventListener('click', () => {
            if (confirm('确定要开始一个新对话吗？当前对话将被保存。')) {
                this.startNewSession();
            }
        });
        this.sendBtn.addEventListener('click', this.handleSendMessage.bind(this));
        this.textarea.addEventListener('keypress', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                this.handleSendMessage();
            }
        });
        this.attachBtn.addEventListener('click', this.handleFileAttachment.bind(this));
        this.fileInput.addEventListener('change', this.onFileSelected.bind(this));
        this.textarea.addEventListener('input', () => {
            this.textarea.style.height = 'auto';
            this.textarea.style.height = (this.textarea.scrollHeight) + 'px';
        });

        // 新增: 深度思考按钮点击事件
        this.deepThinkBtn.addEventListener('click', this.toggleDeepThinking.bind(this));
    }

    /**
     * 切换深度思考模式
     */
    toggleDeepThinking() {
        this.isDeepThinking = !this.isDeepThinking;
        this.deepThinkBtn.classList.toggle('active', this.isDeepThinking);

        if (this.isDeepThinking) {
            showMessage('已开启深度思考模式', 'success');
        } else {
            showMessage('已关闭深度思考模式', 'success');
        }
    }

    scrollToBottom() {
        this.messagesBox.scrollTop = this.messagesBox.scrollHeight;
    }

    /**
     * *** 修改 (V3): 渲染消息
     * 现在会为助手消息添加复制按钮
     */
    renderMessage(role, content, attachments = [], thinkingContent = '') {
        // 确保参数都有默认值
        content = content || '';
        thinkingContent = thinkingContent || '';
        attachments = attachments || [];

        const msgDiv = document.createElement('div');
        msgDiv.className = `ai-chat-message ${role}`;

        const bubble = document.createElement('div');
        bubble.className = 'bubble';

        if (role === 'assistant') {
            this.renderAssistantBubble(bubble, {
                content,
                thinkingContent,
                thinkingState: thinkingContent && thinkingContent.trim() ? 'done' : 'none',
                showStreamingCursor: false
            });
            this.addCodeCopyButtons(bubble);
        } else {
            const p = document.createElement('p');
            p.textContent = content;
            bubble.appendChild(p);
        }

        // 渲染附件 (用户)
        attachments.forEach(att => {
            if (att.type === 'image' && att.previewUrl) {
                const img = document.createElement('img');
                img.src = att.previewUrl;
                img.alt = att.name;
                bubble.appendChild(img);
            } else if (att && att.name) {
                const attachmentPill = document.createElement('div');
                attachmentPill.className = 'ai-chat-attachment-pill';
                attachmentPill.textContent = `附件: ${att.name}`;
                bubble.appendChild(attachmentPill);
            }
        });

        msgDiv.appendChild(bubble);

        // 3. (仅助手) 添加"复制Markdown"按钮
        if (role === 'assistant') {
            this.addMessageActions(msgDiv, content);
        }

        this.messagesBox.appendChild(msgDiv);

        const loading = this.messagesBox.querySelector('.loading');
        if (loading) loading.remove();

        this.scrollToBottom();
    }

    getThinkingStatusMeta(status) {
        if (status === 'thinking') {
            return {
                statusClass: 'thinking-status--thinking',
                label: '正在思考中',
                hint: '思考',
                icon: '<span class="thinking-status-spinner" aria-hidden="true"></span>'
            };
        }

        return {
            statusClass: 'thinking-status--done',
            label: '思考完成',
            hint: '展开',
            icon: `
                <svg class="thinking-status-check" xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                    <path d="M20 6 9 17l-5-5"></path>
                </svg>
            `
        };
    }

    renderAssistantBubble(bubble, options = {}) {
        const {
            content = '',
            thinkingContent = '',
            thinkingState = 'none',
            showStreamingCursor = false
        } = options;

        bubble.innerHTML = '';
        const hasThinking = thinkingState !== 'none' && (thinkingContent.trim() || thinkingState === 'thinking');
        const shouldShowAnswer = thinkingState !== 'thinking' && (Boolean(content) || !hasThinking || showStreamingCursor);

        if (hasThinking) {
            const thinkingSection = this.createThinkingSection(thinkingContent, { status: thinkingState });
            bubble.appendChild(thinkingSection);
        }

        if (shouldShowAnswer) {
            const answerSection = document.createElement('div');
            answerSection.className = 'final-answer';
            answerSection.innerHTML = safeMarkedParse(content, '');
            if (showStreamingCursor) {
                answerSection.innerHTML += '<span class="streaming-cursor"></span>';
            }
            bubble.appendChild(answerSection);
        }
    }

    /* *** 新增 (V5): 创建思考过程区域
     */
    createThinkingSection(thinkingContent, options = {}) {
        const { status = 'done', expanded = false } = options;
        const statusMeta = this.getThinkingStatusMeta(status);
        const thinkingContainer = document.createElement('div');
        thinkingContainer.className = 'thinking-container';
        thinkingContainer.classList.toggle('is-thinking', status === 'thinking');
        thinkingContainer.classList.toggle('is-done', status === 'done');
        thinkingContainer.classList.toggle('is-expanded', expanded);

        const thinkingHeader = document.createElement('button');
        thinkingHeader.type = 'button';
        thinkingHeader.className = 'thinking-header';

        const toggleBtn = document.createElement('span');
        toggleBtn.className = 'thinking-toggle';
        toggleBtn.innerHTML = `
            <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="m6 9 6 6 6-6"/>
            </svg>
            <span>思考过程</span>
            <span class="thinking-toggle-hint"></span>
        `;

        const thinkingIndicator = document.createElement('span');
        thinkingIndicator.className = `thinking-status ${statusMeta.statusClass}`;
        thinkingIndicator.innerHTML = `${statusMeta.icon}<span>${statusMeta.label}</span>`;

        thinkingHeader.appendChild(toggleBtn);
        thinkingHeader.appendChild(thinkingIndicator);

        const thinkingContentDiv = document.createElement('div');
        thinkingContentDiv.className = 'thinking-content';
        thinkingContentDiv.hidden = !expanded;

        const thinkingText = document.createElement('div');
        thinkingText.className = 'thinking-text';
        thinkingText.textContent = thinkingContent && thinkingContent.trim()
            ? thinkingContent
            : (status === 'thinking' ? '模型正在整理思路...' : '本次没有可展示的思考文本。');
        thinkingContentDiv.appendChild(thinkingText);

        const updateExpandedState = (isExpanded) => {
            thinkingContainer.classList.toggle('is-expanded', isExpanded);
            thinkingContentDiv.hidden = !isExpanded;

            const arrow = toggleBtn.querySelector('svg');
            if (arrow) {
                arrow.style.transform = isExpanded ? 'rotate(180deg)' : 'rotate(0deg)';
            }

            const hint = toggleBtn.querySelector('.thinking-toggle-hint');
            if (hint) {
                hint.textContent = isExpanded ? '收起' : statusMeta.hint;
            }
        };

        updateExpandedState(expanded);
        thinkingHeader.addEventListener('click', () => {
            updateExpandedState(thinkingContentDiv.hidden);
        });

        thinkingContainer.appendChild(thinkingHeader);
        thinkingContainer.appendChild(thinkingContentDiv);

        return thinkingContainer;
    }

    createStreamState() {
        return {
            thinkingSupported: false,
            thinkingContent: '',
            finalAnswer: '',
            thinkingState: 'none',
            errorMessage: '',
            meta: null
        };
    }

    applyStreamEvent(streamState, event) {
        if (!event || typeof event !== 'object') {
            return;
        }

        switch (event.event) {
            case 'meta':
                streamState.meta = event;
                streamState.thinkingSupported = Boolean(event.thinking_supported);
                streamState.thinkingState = streamState.thinkingSupported ? 'thinking' : 'none';
                break;
            case 'thinking_delta':
                streamState.thinkingContent += event.delta || '';
                streamState.thinkingState = 'thinking';
                break;
            case 'thinking_end':
                streamState.thinkingState = streamState.thinkingContent.trim() ? 'done' : 'none';
                break;
            case 'answer_delta':
                if (streamState.thinkingState === 'thinking') {
                    streamState.thinkingState = streamState.thinkingContent.trim() ? 'done' : 'none';
                }
                streamState.finalAnswer += event.delta || '';
                break;
            case 'error':
                streamState.errorMessage = event.message || '请求失败';
                if (!streamState.finalAnswer) {
                    streamState.finalAnswer = `抱歉，请求出错了: ${streamState.errorMessage}`;
                }
                if (streamState.thinkingState === 'thinking' && !streamState.thinkingContent.trim()) {
                    streamState.thinkingState = 'none';
                }
                break;
            case 'done':
                if (streamState.thinkingState === 'thinking') {
                    streamState.thinkingState = streamState.thinkingContent.trim() ? 'done' : 'none';
                }
                break;
            default:
                break;
        }
    }

    renderStreamState(bubble, streamState) {
        bubble.style.color = '';
        this.renderAssistantBubble(bubble, {
            content: streamState.finalAnswer,
            thinkingContent: streamState.thinkingContent,
            thinkingState: streamState.thinkingState,
            showStreamingCursor: streamState.thinkingState === 'none' || Boolean(streamState.finalAnswer)
        });
    }

    finalizeStreamMessage(messageDiv, streamState) {
        const bubble = messageDiv.querySelector('.bubble') || document.createElement('div');
        bubble.className = 'bubble';
        if (!bubble.parentNode) {
            messageDiv.appendChild(bubble);
        }

        this.renderAssistantBubble(bubble, {
            content: streamState.finalAnswer,
            thinkingContent: streamState.thinkingContent,
            thinkingState: streamState.thinkingContent.trim() ? 'done' : 'none',
            showStreamingCursor: false
        });
        bubble.style.color = streamState.errorMessage ? 'var(--danger-color, #f44336)' : '';

        this.addCodeCopyButtons(bubble);
        this.addMessageActions(messageDiv, streamState.finalAnswer);
        this.scrollToBottom();
    }

    // (loadOrCreateSession, loadSession, startNewSession 保持不变)
    async loadOrCreateSession() {
        try {
            const data = window.apiFetch
                ? await window.apiFetch(`/api/ai/chat/sessions/${this.classOfferingId}`, { silent: true })
                : null;
            if (!data) throw new Error('获取会话失败');
            if (data.sessions && data.sessions.length > 0) {
                await this.loadSession(data.sessions[0].session_uuid);
            } else {
                await this.startNewSession();
            }
        } catch (err) {
            showMessage(`AI 助手加载失败: ${err.message}`, 'error');
        }
    }
    async loadSession(uuid) {
        if (!uuid) return;
        this.currentSessionUUID = uuid;
        this.messagesBox.innerHTML = '';
        try {
            const response = await fetch(`/api/ai/chat/history/${uuid}`);
            const data = await response.json();
            if (!response.ok && window.handleAuthFailureResponse) {
                await window.handleAuthFailureResponse(response, data);
            }
            if (!response.ok) throw new Error(data.detail || '加载历史失败');

            data.messages.forEach(msg => {
                // 对于历史消息，我们需要检查是否有思考过程
                // 假设历史消息存储时已经分离了思考过程和最终回答
                let thinkingContent = '';
                let finalContent = msg.message;
                let thinkingState = 'none';

                if (msg.thinking_content || msg.final_answer) {
                    thinkingContent = msg.thinking_content || '';
                    finalContent = msg.final_answer || msg.message;
                    thinkingState = thinkingContent ? 'done' : 'none';
                } else if (typeof msg.message === 'object') {
                    // 检查是否是新的消息格式（包含思考过程）
                    // 新格式：消息是对象，包含 thinking 和 answer
                    thinkingContent = msg.message.thinking || '';
                    finalContent = msg.message.answer || msg.message;
                    thinkingState = thinkingContent ? 'done' : 'none';
                } else if (typeof msg.message === 'string') {
                    // 旧格式：纯文本，尝试解析是否有思考过程标记
                    const parsed = this.parseThinkingContent(msg.message);
                    if (parsed.hasThinking) {
                        thinkingContent = parsed.thinkingContent;
                        finalContent = parsed.finalAnswer;
                        thinkingState = parsed.thinkingState;
                    }
                }

                this.renderMessage(
                    msg.role,
                    finalContent,
                    msg.attachments || [],
                    thinkingState === 'none' ? '' : thinkingContent
                );
            });
        } catch (err) {
            showMessage(`加载历史失败: ${err.message}`, 'error');
        }
    }
    async startNewSession() {
        try {
            const response = await fetch(`/api/ai/chat/session/new/${this.classOfferingId}`, { method: 'POST' });
            const data = await response.json();
            if (!response.ok && window.handleAuthFailureResponse) {
                await window.handleAuthFailureResponse(response, data);
            }
            if (!response.ok) throw new Error(data.detail || '创建新会话失败');
            this.currentSessionUUID = data.session.session_uuid;
            this.messagesBox.innerHTML = '';
            this.renderMessage('system', '已开始新对话。');
        } catch (err) {
            showMessage(`创建新会话失败: ${err.message}`, 'error');
        }
    }

    /**
     * *** 修改 (V4): 处理流式响应 - 分离思考过程和最终回答
     * 在流结束后添加复制按钮
     */
    async handleSendMessage() {
        const message = this.textarea.value.trim();
        if (!message && this.pendingFiles.length === 0) return;
        if (this.isLoading) return;
        if (!this.currentSessionUUID) {
            showMessage('请先开始一个新会话。', 'error');
            return;
        }

        this.isLoading = true;
        this.sendBtn.disabled = true;

        // 1. 渲染用户消息 (无变化)
        const userAttachments = this.pendingFiles.map(file => ({
            type: 'image',
            name: file.name,
            previewUrl: URL.createObjectURL(file)
        }));
        this.renderMessage('user', message, userAttachments);

        // 2. 准备 FormData (无变化)
        const formData = new FormData();
        formData.append('message', message);
        formData.append('session_uuid', this.currentSessionUUID);
        formData.append('class_offering_id', this.classOfferingId);
        formData.append('deep_thinking', this.isDeepThinking); // 新增参数
        this.pendingFiles.forEach(file => {
            formData.append('files', file);
        });

        // 3. 清空输入 (无变化)
        this.textarea.value = '';
        this.textarea.style.height = 'auto'; // (重置高度)
        this.clearPendingFiles();

        // 4. 创建流式占位符 (无变化)
        const aiMsgDiv = document.createElement('div');
        aiMsgDiv.className = 'ai-chat-message assistant';
        const aiBubble = document.createElement('div');
        aiBubble.className = 'bubble';
        aiBubble.innerHTML = '<span class="streaming-cursor"></span>';
        aiMsgDiv.appendChild(aiBubble);
        this.messagesBox.appendChild(aiMsgDiv);
        this.scrollToBottom();

        // 重置思考过程状态
        const streamState = this.createStreamState();
        let streamBuffer = '';
        const decoder = new TextDecoder("utf-8");

        try {
            // 5. 发送 API (无变化)
            const response = await fetch('/api/ai/chat', {
                method: 'POST',
                body: formData
            });

            if (!response.ok) {
                const errorText = await response.text();
                try {
                    const errorJson = JSON.parse(errorText);
                    if (window.handleAuthFailureResponse) {
                        await window.handleAuthFailureResponse(response, errorJson);
                    }
                    throw new Error(errorJson.detail || `服务器错误: ${response.status}`);
                } catch (e) {
                     throw new Error(errorText || `服务器错误: ${response.status}`);
                }
            }
            if (!response.body) {
                throw new Error("浏览器不支持流式响应。");
            }

            const reader = response.body.getReader();

            // 6. 循环读取流 (修改: 分离思考过程)
            while (true) {
                const { done, value } = await reader.read();
                if (done) break;

                const chunk = decoder.decode(value, { stream: true });
                streamBuffer += chunk;

                // *** 新增: 检测思考过程标记并分离内容 ***
                const lines = streamBuffer.split('\n');
                streamBuffer = lines.pop() || '';

                for (const line of lines) {
                    if (!line.trim()) {
                        continue;
                    }

                    let event = null;
                    try {
                        event = JSON.parse(line);
                    } catch (parseError) {
                        event = { event: 'answer_delta', delta: line };
                    }

                    this.applyStreamEvent(streamState, event);
                    this.renderStreamState(aiBubble, streamState);
                }

                this.scrollToBottom();
            }

            // *** 核心修改 (V4): 流结束后，根据是否有思考过程来渲染最终消息 ***
            if (streamBuffer.trim()) {
                let trailingEvent = null;
                try {
                    trailingEvent = JSON.parse(streamBuffer);
                } catch (parseError) {
                    trailingEvent = { event: 'answer_delta', delta: streamBuffer };
                }
                this.applyStreamEvent(streamState, trailingEvent);
            }

            this.finalizeStreamMessage(aiMsgDiv, streamState);

        } catch (err) {
            console.error("AI 流式处理失败:", err);

            // 错误情况下也尝试显示已收集的内容
            /* legacy streaming error handling removed
                // legacy placeholder
            } else {
                aiBubble.innerHTML = `抱歉，请求出错了: ${err.message}`;
                message: err.message
            }
            */
            this.applyStreamEvent(streamState, {
                event: 'error',
                message: err.message
            });
            this.finalizeStreamMessage(aiMsgDiv, streamState);
        } finally {
            this.isLoading = false;
            this.sendBtn.disabled = false;
            this.textarea.focus();
        }
    }

    /**
     * *** 新增 (V4): 解析思考内容
     * 根据后端返回的标记分离思考过程和最终回答
     */
    parseThinkingContent(text) {
        if (!text) {
            return {
                thinkingContent: '',
                finalAnswer: '',
                hasThinking: false,
                thinkingState: 'none'
            };
        }
        // 假设后端使用特定标记来分隔思考过程和最终回答
        // 例如: 【思考过程开始】...思考内容...【思考过程结束】最终回答
        const thinkOpenTag = '<think>';
        const thinkCloseTag = '</think>';
        const openTagIndex = text.indexOf(thinkOpenTag);
        const closeTagIndex = text.indexOf(thinkCloseTag);

        if (openTagIndex !== -1 && closeTagIndex !== -1 && closeTagIndex > openTagIndex) {
            const thinkingContent = text.substring(openTagIndex + thinkOpenTag.length, closeTagIndex).trim();
            const finalAnswer = (
                text.substring(0, openTagIndex) +
                text.substring(closeTagIndex + thinkCloseTag.length)
            ).trim();
            return {
                thinkingContent,
                finalAnswer,
                hasThinking: Boolean(thinkingContent),
                thinkingState: thinkingContent ? 'done' : 'none'
            };
        } else if (openTagIndex !== -1) {
            const thinkingContent = text.substring(openTagIndex + thinkOpenTag.length).trim();
            return {
                thinkingContent,
                finalAnswer: text.substring(0, openTagIndex).trim(),
                hasThinking: true,
                thinkingState: 'thinking'
            };
        }

        const thinkingStart = '【思考过程开始】';
        const thinkingEnd = '【思考过程结束】';

        const startIndex = text.indexOf(thinkingStart);
        const endIndex = text.indexOf(thinkingEnd);

        if (startIndex !== -1 && endIndex !== -1) {
            // 找到完整的思考过程
            const thinkingContent = text.substring(startIndex + thinkingStart.length, endIndex).trim();
            const finalAnswer = text.substring(endIndex + thinkingEnd.length).trim();
            return {
                thinkingContent,
                finalAnswer,
                hasThinking: true,
                thinkingState: 'done'
            };
        } else if (startIndex !== -1) {
            // 正在接收思考过程，但尚未结束
            const thinkingContent = text.substring(startIndex + thinkingStart.length).trim();
            return {
                thinkingContent,
                finalAnswer: '',
                hasThinking: true,
                thinkingState: 'thinking'
            };
        }

        // 格式2: JSON 格式存储的历史记录
        try {
            const parsed = JSON.parse(text);
            if (parsed.thinking && parsed.answer) {
                return {
                    thinkingContent: parsed.thinking,
                    finalAnswer: parsed.answer,
                    hasThinking: true,
                    thinkingState: 'done'
                };
            } else if (parsed.answer) {
                // 只有最终回答，没有思考过程
                return {
                    thinkingContent: '',
                    finalAnswer: parsed.answer,
                    hasThinking: false,
                    thinkingState: 'none'
                };
            }
        } catch (e) {
            // 不是 JSON 格式，继续处理
        }

        // 格式3: 旧的历史记录格式（纯文本）
        // 检查是否有明显的思考过程模式（例如包含"思考"、"推理"等关键词）
        const thinkingPatterns = [
            /首先[,，].*然后[,，].*最后[,，]/,
            /让我想想[,，].*/,
            /思考过程[:：]/,
            /推理[:：]/
        ];

        for (const pattern of thinkingPatterns) {
            if (pattern.test(text)) {
                // 尝试智能分割思考过程和最终回答
                const lines = text.split('\n');
                let thinkingLines = [];
                let answerLines = [];
                let foundAnswer = false;

                for (const line of lines) {
                    if (line.includes('答案：') || line.includes('回答：') || line.includes('所以')) {
                        foundAnswer = true;
                    }

                    if (foundAnswer) {
                        answerLines.push(line);
                    } else {
                        thinkingLines.push(line);
                    }
                }

                if (answerLines.length > 0) {
                    return {
                        thinkingContent: thinkingLines.join('\n').trim(),
                        finalAnswer: answerLines.join('\n').trim(),
                        hasThinking: true,
                        thinkingState: 'done'
                    };
                }
            }
        }

        // 没有思考过程标记
        return {
            thinkingContent: '',
            finalAnswer: text,
            hasThinking: false,
            thinkingState: 'none'
        };
    }

    /**
     * *** 新增 (V4): 渲染流式消息（带思考过程）
     */
    renderStreamingMessage(bubble, thinkingContent, finalAnswer, thinkingState) {
        this.renderAssistantBubble(bubble, {
            content: finalAnswer,
            thinkingContent,
            thinkingState,
            showStreamingCursor: thinkingState === 'none' || Boolean(finalAnswer)
        });
    }

    /**
     * *** 新增 (V4): 渲染完整的消息（带思考过程）
     */
    renderCompleteMessageWithThinking(messageDiv, thinkingContent, finalAnswer) {
        // 移除原有的bubble
        const oldBubble = messageDiv.querySelector('.bubble');
        if (oldBubble) {
            oldBubble.remove();
        }

        // 创建新的bubble
        const bubble = document.createElement('div');
        bubble.className = 'bubble';

        this.renderAssistantBubble(bubble, {
            content: finalAnswer,
            thinkingContent,
            thinkingState: 'done',
            showStreamingCursor: false
        });

        // 添加到消息
        messageDiv.appendChild(bubble);

        // 添加复制按钮
        this.addCodeCopyButtons(bubble);
        this.addMessageActions(messageDiv, finalAnswer);

        this.scrollToBottom();
    }

    // ==========================================================
    // 4. 附件处理
    // ==========================================================

    // (handleFileAttachment, onFileSelected, renderPreviews, clearPendingFiles 保持不变)
    handleFileAttachment() {
        this.fileInput.click();
    }
    onFileSelected(e) {
        for (const file of e.target.files) {
            if (file.type.startsWith('image/')) {
                if (this.pendingFiles.length < 5) {
                    this.pendingFiles.push(file);
                } else {
                    showMessage('一次最多上传5张图片。', 'error');
                }
            }
        }
        this.renderPreviews();
        this.fileInput.value = '';
    }
    renderPreviews() {
        this.previewsBox.innerHTML = '';
        this.pendingFiles.forEach((file, index) => {
            const item = document.createElement('div');
            item.className = 'preview-item';
            const img = document.createElement('img');
            img.src = URL.createObjectURL(file);
            const removeBtn = document.createElement('button');
            removeBtn.className = 'remove-preview';
            removeBtn.innerHTML = '&times;';
            removeBtn.onclick = () => {
                this.pendingFiles.splice(index, 1);
                this.renderPreviews();
            };
            item.appendChild(img);
            item.appendChild(removeBtn);
            this.previewsBox.appendChild(item);
        });
    }
    clearPendingFiles() {
        this.pendingFiles = [];
        this.renderPreviews();
    }


    // ==========================================================
    // 5. *** 新增 (V3): 复制功能 ***
    // ==========================================================

    /**
     * 通用剪贴板写入函数
     * @param {string | Event} textOrEvent - 要复制的文本，或按钮的点击事件
     */
    copyToClipboard(textOrEvent) {
        let textToCopy;
        let btn;

        if (typeof textOrEvent === 'string') {
            textToCopy = textOrEvent;
        } else {
            btn = textOrEvent.currentTarget;
            textToCopy = btn.dataset.rawMarkdown;
        }

        navigator.clipboard.writeText(textToCopy).then(() => {
            // 如果是按钮点击，显示"已复制"
            if (btn) {
                const originalContent = btn.innerHTML;
                btn.innerHTML = this.iconCheck + ' 已复制';
                btn.classList.add('copied');
                setTimeout(() => {
                    btn.innerHTML = originalContent;
                    btn.classList.remove('copied');
                }, 2000);
            } else {
                // 如果是代码块复制 (没有传入事件)，使用 showMessage
                showMessage('代码已复制!', 'success');
            }
        }).catch(err => {
            console.error('复制失败: ', err);
            showMessage('复制失败，请检查浏览器权限。', 'error');
        });
    }

    /**
     * (V3) 查找 bubble 内的所有 <pre> 块并添加复制按钮
     * @param {HTMLElement} bubble - 消息气泡元素
     */
    addCodeCopyButtons(bubble) {
        const pres = bubble.querySelectorAll('pre');
        pres.forEach(pre => {
            const btn = document.createElement('button');
            btn.className = 'copy-code-btn';
            btn.textContent = '复制';

            btn.addEventListener('click', (e) => {
                e.stopPropagation(); // 防止触发其他事件
                const codeToCopy = pre.querySelector('code')?.textContent || pre.textContent;
                this.copyToClipboard(codeToCopy);

                // (代码块按钮的“已复制”状态)
                btn.textContent = '已复制!';
                btn.classList.add('copied');
                setTimeout(() => {
                    btn.textContent = '复制';
                    btn.classList.remove('copied');
                }, 2000);
            });
            pre.appendChild(btn);
        });
    }

    /**
     * (V3) 向消息 DOM 添加操作栏 (例如 "复制 Markdown")
     * @param {HTMLElement} messageDiv - 消息的顶层 .ai-chat-message 元素
     * @param {string} rawMarkdown - 要复制的原始 Markdown 文本
     */
    addMessageActions(messageDiv, rawMarkdown) {
        const existingActions = messageDiv.querySelector('.message-actions');
        if (existingActions) {
            existingActions.remove();
        }

        if (!rawMarkdown) {
            return;
        }

        const actionsDiv = document.createElement('div');
        actionsDiv.className = 'message-actions';

        const copyBtn = document.createElement('button');
        copyBtn.className = 'copy-btn';
        copyBtn.innerHTML = this.iconCopy + ' 复制 Markdown';
        copyBtn.dataset.rawMarkdown = rawMarkdown; // 存储原始文本

        copyBtn.addEventListener('click', this.copyToClipboard.bind(this));

        actionsDiv.appendChild(copyBtn);
        messageDiv.appendChild(actionsDiv);
    }
}

window.AIChatComponent = AIChatComponent;
