/** Conversation picker: authenticated server data, no HTML interpolation. */
import { confirm as confirmGlass } from './lq/dialogs.js';

export function createConversationHistory(container, chat, notify) {
    const panel = document.createElement('aside');
    panel.className = 'ai-agent-history-drawer ai-conversation-history';
    panel.hidden = true;
    panel.setAttribute('aria-label', '我的 AI 对话');
    panel.innerHTML = '<header><strong>我的对话</strong><button type="button" class="chat-btn" aria-label="收起对话历史">×</button></header><div class="ai-task-list" role="list"></div>';
    container.append(panel);
    const close = () => { panel.hidden = true; };
    panel.querySelector('header button').addEventListener('click', close);
    panel.addEventListener('keydown', event => {
        if (event.key === 'Escape') { event.stopPropagation(); close(); container.querySelector('#ai-agent-history-toggle')?.focus(); }
    });
    const api = {
        close,
        async toggle() {
            if (!panel.hidden) return close();
            panel.hidden = false;
            const list = panel.querySelector('.ai-task-list');
            list.textContent = '正在读取…';
            try {
                const response = await fetch('/api/ai/workspace/sessions', { credentials: 'same-origin' });
                const data = await response.json();
                if (!response.ok) throw new Error(data.detail || '读取对话失败');
                list.replaceChildren();
                for (const session of [...(data.sessions || []), ...(data.legacy_sessions || []).map(item => ({ ...item, legacy: true }))]) {
                    const row = document.createElement('div');
                    row.className = 'ai-conversation-row';
                    const button = document.createElement('button');
                    button.type = 'button';
                    button.className = 'ai-conversation-entry';
                    button.setAttribute('aria-current', String(session.session_uuid === chat.currentSessionUUID));
                    const title = document.createElement('strong'), date = document.createElement('small');
                    title.textContent = `${session.legacy ? '课堂对话 · ' : ''}${session.title || '新对话'}`;
                    date.textContent = String(session.updated_at || session.created_at || '').replace('T', ' ').slice(0, 16);
                    button.append(title, date);
                    button.addEventListener('click', async () => {
                        if (chat.isLoading) { notify('当前回复完成后可切换对话。'); return; }
                        button.disabled = true;
                        try {
                            let uuid = session.session_uuid;
                            if (session.legacy) {
                                const imported = await fetch(`/api/ai/workspace/session/import/${encodeURIComponent(uuid)}`, { method: 'POST', credentials: 'same-origin' });
                                const result = await imported.json();
                                if (!imported.ok) throw new Error(result.detail || '读取课堂对话失败');
                                uuid = result.session.session_uuid;
                            }
                            await chat.loadSession(uuid);
                            close();
                        } catch (error) { notify(error.message, 'error'); }
                        finally { button.disabled = false; }
                    });
                    row.append(button);
                    if (!session.legacy) {
                        const remove = document.createElement('button');
                        remove.className = 'chat-btn'; remove.type = 'button';
                        remove.setAttribute('aria-label', `删除对话：${session.title || '新对话'}`);
                        remove.textContent = '×';
                        remove.addEventListener('click', async () => {
                            if (!await confirmGlass({ title: '删除对话', message: '删除这段对话及上传的图片？此操作无法恢复。', confirmLabel: '删除', danger: true })) return;
                            remove.disabled = true;
                            try {
                                const response = await fetch(`/api/ai/workspace/session/${encodeURIComponent(session.session_uuid)}`, { method: 'DELETE', credentials: 'same-origin' });
                                const result = await response.json();
                                if (!response.ok) throw new Error(result.detail || '删除失败');
                                if (chat.currentSessionUUID === session.session_uuid) {
                                    chat.currentSessionUUID = null; chat.onSessionChange?.(null);
                                    chat.pendingRequest = null; chat.onRequestChange?.(null);
                                    await chat.loadOrCreateSession();
                                }
                                notify('对话和图片已删除。', 'success');
                                close(); await api.toggle();
                            } catch (error) { notify(error.message, 'error'); }
                            finally { remove.disabled = false; }
                        });
                        row.append(remove);
                    }
                    list.append(row);
                }
                if (!list.childElementCount) list.textContent = '尚无对话，开始提问即可。';
                panel.querySelector('header button').focus({ preventScroll: true });
            } catch (error) { list.textContent = '暂时无法读取对话，请稍后重试。'; notify(error.message, 'error'); }
        },
    };
    return api;
}
