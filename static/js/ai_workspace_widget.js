// AI window shell: glass window geometry, the page-aware chat, and — for
// teachers — the Agent workbench panel. Agent logic lives in agent_workbench.js.
import { createWorkspaceState } from './ai_workspace_state.js';
import { createAssistantWindow } from './ai_workspace_window.js';
import { capturePageImage } from './ai_workspace_capture.js';
import { createConversationHistory } from './ai_workspace_history.js';
import { collectPageContext, contextLabel, formatContextForPrompt } from './ai_workspace_context.js';
import { createAgentWorkbench } from './agent_workbench.js';

const CONFIG = window.AI_WORKSPACE_WIDGET_CONFIG || {};
const workspaceState = createWorkspaceState(CONFIG.userKey);
const $ = (selector, root = document) => root.querySelector(selector);
const $all = (selector, root = document) => Array.from(root.querySelectorAll(selector));

let windowManager = null;
let chatComponent = null;
let conversationHistory = null;
let workbench = null;
let draftsReady = false;
let mode = 'chat';

function notify(message, type = 'info') {
    const notifier = window.showMessage || window.showToast || window.UI?.showToast || window.UI?.showMessage;
    if (typeof notifier === 'function') notifier(message, type);
    else if (type === 'error') console.error(message);
}

async function apiJson(url, options = {}) {
    const { headers = {}, ...rest } = options;
    const isFormData = typeof FormData !== 'undefined' && rest.body instanceof FormData;
    const response = await fetch(url, {
        credentials: 'same-origin',
        ...rest,
        headers: { Accept: 'application/json', ...(rest.body && !isFormData ? { 'Content-Type': 'application/json' } : {}), ...headers },
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
        if (window.handleAuthFailureResponse) await window.handleAuthFailureResponse(response, data);
        const detail = data.detail && typeof data.detail === 'object' ? data.detail.message : data.detail;
        throw new Error(detail || data.message || `请求失败：${response.status}`);
    }
    return data;
}

function refreshSubtitle() {
    const subtitle = $('#ai-workspace-subtitle');
    if (subtitle) subtitle.textContent = mode === 'agent' ? `Agent · 全平台队列 · ${contextLabel()}` : `AI 对话 · ${contextLabel()}`;
}

function captureForAgent() {
    return capturePageImage({
        hideAssistant: () => {
            windowManager.suspend();
            return new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
        },
        restoreAssistant: () => windowManager.resume(),
        notify,
    });
}

function setMode(next, { persist = true } = {}) {
    mode = next === 'agent' && workbench ? 'agent' : 'chat';
    $('.ai-workspace-container')?.classList.toggle('is-agent-mode', mode === 'agent');
    document.body.dataset.aiAgentMode = mode;
    $all('[data-ai-workspace-panel]').forEach((panel) => {
        const active = panel.dataset.aiWorkspacePanel === mode;
        panel.hidden = !active;
        panel.classList.toggle('is-active', active);
    });
    $all('[data-ai-mode-select]').forEach((button) => {
        const active = button.dataset.aiModeSelect === mode;
        button.classList.toggle('is-active', active);
        button.setAttribute('aria-pressed', active ? 'true' : 'false');
    });
    conversationHistory?.close();
    refreshSubtitle();
    if (persist) {
        try { workspaceState.patch({ agentMode: mode === 'agent' }); } catch { /* storage may be blocked */ }
    }
    if (mode === 'agent') void workbench.activate().catch((error) => notify(error.message || 'Agent 加载失败', 'error'));
    else workbench?.deactivate();
}

function persistDraft() {
    if (!draftsReady) return;
    workspaceState.patch({ draft: $('#ai-chat-textarea')?.value || '', deepThinking: Boolean(chatComponent?.isDeepThinking) });
    void workspaceState.saveFiles(chatComponent?.pendingFiles || []);
}

function initChatComponent() {
    if (typeof window.AIChatComponent !== 'function') return false;
    try {
        chatComponent = new window.AIChatComponent({
            classOfferingId: CONFIG.classOfferingId,
            contextOnly: true, workspace: true, managedWindow: true,
            sessionUUID: workspaceState.value.sessionUUID,
            pendingRequest: workspaceState.value.pendingRequest,
            onSessionChange: (sessionUUID) => workspaceState.patch({ sessionUUID }),
            onRequestChange: (pendingRequest) => workspaceState.patch({ pendingRequest }),
            onDraftChange: persistDraft,
            getContextPromptExtra: () => formatContextForPrompt(collectPageContext()),
        });
        chatComponent.init();
        window.aiChat = chatComponent;
        return true;
    } catch (error) {
        console.error('Failed to init AI workspace chat', error);
        return false;
    }
}

function bindChatCapture(textarea) {
    const captureButton = $('#ai-chat-btn-capture');
    captureButton?.addEventListener('click', () => {
        if (!chatComponent || captureButton.disabled) return;
        captureButton.disabled = true;
        // Keep this direct call in the click activation: browser consent cannot be deferred.
        capturePageImage({
            hideAssistant: () => { windowManager.suspend(); return new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))); },
            restoreAssistant: () => { windowManager.resume(); textarea.focus({ preventScroll: true }); }, notify,
        }).then((file) => {
            if (!file) return;
            chatComponent.onFileSelected({ target: { files: [file] } });
            textarea.focus({ preventScroll: true });
        }).catch((error) => notify(error.message || '截图失败，请重试或上传截图。', 'error'))
            .finally(() => { captureButton.disabled = false; });
    });
}

async function initWindow() {
    const modal = $('#ai-chat-modal');
    const fab = $('#ai-chat-fab');
    const container = $('.ai-chat-container', modal);
    const textarea = $('#ai-chat-textarea');
    textarea.value = workspaceState.value.draft || '';
    if (chatComponent) {
        chatComponent.pendingFiles = await workspaceState.loadFiles();
        chatComponent.isDeepThinking = Boolean(workspaceState.value.deepThinking);
        $('#ai-deep-think-btn')?.classList.toggle('active', chatComponent.isDeepThinking);
        chatComponent.renderPreviews();
    }
    draftsReady = true;
    windowManager = createAssistantWindow({
        modal, container, fab, state: workspaceState,
        onOpen: ({ focus }) => {
            refreshSubtitle();
            if (chatComponent) {
                if (!chatComponent.currentSessionUUID) void chatComponent.loadOrCreateSession();
                else if (!chatComponent.isLoading || chatComponent.historyPollTimer || chatComponent.historyPaused) void chatComponent.loadSession(chatComponent.currentSessionUUID);
            }
            workbench?.windowOpened();
            if (focus && mode === 'chat') textarea.focus({ preventScroll: true });
        },
        onClose: () => {
            persistDraft();
            chatComponent?.pauseHistory();
            workbench?.windowClosed();
        },
    });
    if (chatComponent) chatComponent.windowManager = windowManager;
    conversationHistory = createConversationHistory(container, chatComponent, notify);
    $('#ai-chat-history-toggle')?.addEventListener('click', () => { void conversationHistory?.toggle(); });
    textarea.addEventListener('input', persistDraft);
    $('#ai-deep-think-btn')?.addEventListener('click', persistDraft);
    window.addEventListener('pagehide', persistDraft);
    bindChatCapture(textarea);
    windowManager.restore();
    document.addEventListener('visibilitychange', () => {
        if (document.hidden) chatComponent?.pauseHistory();
        else if (windowManager.isOpen && chatComponent?.historyPaused) void chatComponent.loadSession(chatComponent.currentSessionUUID);
    });
}

function readDeepLink() {
    try {
        const params = new URLSearchParams(window.location.search);
        const taskId = Number(params.get('agent_task') || params.get('agentTask') || 0);
        const subscriptions = ['1', 'true', 'open'].includes(params.get('agent_subscriptions') || params.get('agentSubscriptions') || '');
        return { taskId: Number.isInteger(taskId) && taskId > 0 ? taskId : 0, subscriptions };
    } catch {
        return { taskId: 0, subscriptions: false };
    }
}

function clearDeepLink() {
    try {
        const url = new URL(window.location.href);
        ['agent_task', 'agentTask', 'agent_subscriptions', 'agentSubscriptions'].forEach((key) => url.searchParams.delete(key));
        window.history.replaceState(window.history.state, '', `${url.pathname}${url.search}${url.hash}`);
    } catch {
        // Cosmetic only.
    }
}

async function handleDeepLinks() {
    const { taskId, subscriptions } = readDeepLink();
    if ((!taskId && !subscriptions) || !workbench) return;
    try {
        windowManager.open();
        setMode('agent', { persist: false });
        if (taskId) await workbench.openTask(taskId);
        if (subscriptions) await workbench.openSubscriptions();
    } catch (error) {
        notify(error.message || '无法打开对应的 Agent 任务', 'error');
    } finally {
        clearDeepLink();
    }
}

function bindModes() {
    $all('[data-ai-mode-select]').forEach((button) => {
        button.addEventListener('click', () => setMode(button.dataset.aiModeSelect));
    });
    window.addEventListener('lanshare:agent-handoff', (event) => {
        if (!workbench) return;
        windowManager.open();
        setMode('agent');
        void workbench.prefill(event.detail?.instruction || '');
        notify('已切换到 Agent，可补充说明后加入队列。', 'success');
    });
}

let initialized = false;

async function initAIWorkspaceWidget() {
    if (initialized || !$('#ai-chat-modal') || window.top !== window.self) return;
    initialized = true;
    window.buildAIWorkspacePageContext = collectPageContext;
    window.formatAIWorkspaceContextForPrompt = formatContextForPrompt;
    if (!initChatComponent()) {
        notify('AI 助手加载失败，请刷新页面重试。', 'error');
        return;
    }
    const agentRoot = $('[data-ai-workspace-panel="agent"]');
    if (CONFIG.taskCenterEnabled && agentRoot) {
        workbench = createAgentWorkbench({ root: agentRoot, config: CONFIG, notify, apiJson, capture: captureForAgent, fab: $('#ai-chat-fab') });
    }
    await initWindow();
    const deferredLauncher = $('#ai-chat-fab[data-ai-deferred]');
    if (deferredLauncher) {
        deferredLauncher.disabled = false;
        deferredLauncher.removeAttribute('aria-busy');
        deferredLauncher.removeAttribute('data-ai-deferred');
    }
    bindModes();
    let preferAgent = false;
    try { preferAgent = workspaceState.value.agentMode === true; } catch { /* storage may be blocked */ }
    setMode(preferAgent ? 'agent' : 'chat', { persist: false });
    await handleDeepLinks();
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initAIWorkspaceWidget, { once: true });
else initAIWorkspaceWidget();
