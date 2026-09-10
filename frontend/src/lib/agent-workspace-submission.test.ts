import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import vm from 'node:vm';
import { describe, expect, it, vi } from 'vitest';

const source = readFileSync(resolve('static/js/ai_workspace_widget.js'), 'utf8');

function submissionHarness() {
  const textarea = { value: '  请生成课堂讨论报告  ', focus: vi.fn() };
  const surface = { textarea, sendBtn: { disabled: false }, renderMessage: vi.fn() };
  const originalFile = { name: 'lesson.txt', size: 8 };
  const chat = {
    pendingFiles: [originalFile], currentSessionUUID: 'chat-1', isDeepThinking: false,
    renderPreviews: vi.fn(), updateSendButtonState: vi.fn(),
    clearPendingFiles: vi.fn(() => { chat.pendingFiles = []; }),
  };
  const request = vi.fn();
  const refresh = vi.fn(async () => undefined);
  const notify = vi.fn();
  const context = vm.createContext({
    window: { AI_WORKSPACE_WIDGET_CONFIG: { taskCenterEnabled: true } },
    document: { readyState: 'loading', addEventListener: vi.fn(), querySelector: () => null },
    FormData: class { entries: unknown[] = []; append(...values: unknown[]) { this.entries.push(values); } },
    testSurface: surface, testChat: chat, testRequest: request, testRefresh: refresh, testNotify: notify,
    console,
  });
  vm.runInContext(source, context);
  vm.runInContext(`
    chatComponent = testChat;
    currentChatSurface = () => testSurface;
    currentAgentComposerTargetTask = () => null;
    collectPageContext = () => ({page: {path: '/classroom/31'}});
    inferAgentTaskType = () => 'general_teaching_task';
    validateAgentAttachments = () => '';
    agentAttachmentPreviews = files => files.map(file => ({name: file.name}));
    resetTextareaHeight = () => {};
    updateComposerPresence = async () => {};
    renderAgentTaskMessage = () => {};
    renderAgentStarters = () => {};
    apiJson = testRequest;
    refreshTasks = testRefresh;
    notify = testNotify;
  `, context);
  return { textarea, surface, chat, originalFile, request, refresh, notify, context,
    submit: () => context.submitAgentTaskFromChat() as Promise<void> };
}

describe('Agent workspace submission', () => {
  it('retains the draft and attachments on failure and clears them only after a successful retry', async () => {
    const h = submissionHarness();
    let rejectRequest!: (reason: Error) => void;
    h.request.mockImplementationOnce(() => new Promise((_, reject) => { rejectRequest = reject; }));
    const originalInput = h.textarea.value;
    const pending = h.submit();
    expect(h.textarea.value).toBe(originalInput);
    expect(h.chat.pendingFiles).toEqual([h.originalFile]);
    expect(h.surface.sendBtn.disabled).toBe(true);
    await h.submit();
    expect(h.request).toHaveBeenCalledTimes(1);
    rejectRequest(new Error('503 unavailable'));
    await pending;
    expect(h.textarea.value).toBe(originalInput);
    expect(h.chat.clearPendingFiles).not.toHaveBeenCalled();
    expect(h.surface.renderMessage.mock.calls.filter(([role]) => role === 'user')).toHaveLength(0);
    expect(h.surface.sendBtn.disabled).toBe(false);

    h.request.mockResolvedValueOnce({ task: { id: 9 } });
    await h.submit();
    expect(h.textarea.value).toBe('');
    expect(h.chat.pendingFiles).toEqual([]);
    expect(h.surface.renderMessage.mock.calls.filter(([role]) => role === 'user')).toHaveLength(1);
    expect(h.request.mock.calls[1][1].body.entries[0][1]).toContain('"path":"/classroom/31"');
  });

  it('does not discard edits and files added while a successful request is pending', async () => {
    const h = submissionHarness();
    let resolveRequest!: (result: unknown) => void;
    h.request.mockImplementationOnce(() => new Promise(resolve => { resolveRequest = resolve; }));
    const pending = h.submit();
    const nextFile = { name: 'next.txt', size: 5 };
    h.textarea.value = '接下来补充另一份报告';
    h.chat.pendingFiles.push(nextFile);
    resolveRequest({ task: { id: 10 } });
    await pending;
    expect(h.textarea.value).toBe('接下来补充另一份报告');
    expect(h.chat.pendingFiles).toEqual([nextFile]);
    expect(h.chat.renderPreviews).toHaveBeenCalledOnce();
    expect(h.chat.clearPendingFiles).not.toHaveBeenCalled();
  });

  it('does not report an accepted task as a failed submission when queue refresh fails', async () => {
    const h = submissionHarness();
    h.request.mockResolvedValueOnce({ task: { id: 11 } });
    h.refresh.mockRejectedValueOnce(new Error('refresh disconnected'));
    await h.submit();
    expect(h.notify).toHaveBeenCalledWith('Agent 任务已加入全平台队列。', 'success');
    expect(h.notify.mock.calls.some(([, type]) => type === 'error')).toBe(false);
  });
});

describe('Agent artifact rendering', () => {
  it('makes successful and recovered artifacts downloadable and escapes their labels', () => {
    const h = submissionHarness();
    const html = h.context.renderRuntimeDetail({
      artifacts: [{ name: '<report>.md', download_url: '/api/agent-tasks/7/artifacts/report.md', size: 20 }],
      recovered_artifacts: [{ name: 'partial.md', download_url: '/api/agent-tasks/7/artifacts/partial.md' }],
    });
    expect(html).toContain('href="/api/agent-tasks/7/artifacts/report.md"');
    expect(html).toContain('&lt;report&gt;.md');
    expect(html).toContain('href="/api/agent-tasks/7/artifacts/partial.md"');
  });

  it.each(['//untrusted.test/file', '/\\untrusted.test/file', 'javascript:alert(1)', '/\n/untrusted.test'])('does not make unsafe artifact URLs clickable: %s', (url) => {
    const h = submissionHarness();
    const html = h.context.renderAgentArtifact({ name: 'report.md', download_url: url });
    expect(html).not.toContain('<a ');
    expect(html).toContain('report.md');
  });
});

describe('Agent message recipient confirmation', () => {
  function recipientHarness() {
    const h = submissionHarness();
    const select = { dataset: { loaded: '', loading: '', recipientAction: 'send_student_notification', classOfferingId: '40', initialRecipients: '["student:7","student:99"]' }, disabled: false, multiple: true, innerHTML: '', selectedOptions: [] as { value: string }[] };
    const status = { textContent: '' };
    const button = { dataset: { agentActionConfirm: '11', actionIndex: '0' }, disabled: false, closest: () => block };
    const block = { querySelector: (selector: string) => selector === '[data-agent-action-recipients]' ? select : selector === '[data-agent-action-confirm]' ? button : selector === '[data-agent-recipient-status]' ? status : null };
    vm.runInContext('renderTaskDetail = () => {};', h.context);
    return { ...h, select, status, button, block };
  }

  it('loads actual visible recipients and filters blocked, unavailable, and teacher identities for student notifications', async () => {
    const h = recipientHarness();
    h.request.mockResolvedValueOnce({ contacts: [
      { identity: 'student:7', role: 'student', can_send: true, display_name: '<Student>', subtitle: 'Class 30' },
      { identity: 'student:99', role: 'student', can_send: true, is_blocked: true, display_name: 'Blocked' },
      { identity: 'student:8', role: 'student', can_send: false },
      { identity: 'teacher:7', role: 'teacher', can_send: true },
    ] });
    await h.context.loadAgentActionRecipients(h.block);
    expect(h.request).toHaveBeenCalledWith('/api/classrooms/40/private/contacts');
    expect(h.select.innerHTML).toContain('value="student:7" selected');
    expect(h.select.innerHTML).toContain('&lt;Student&gt;');
    expect(h.select.innerHTML).not.toContain('student:99');
    expect(h.select.innerHTML).not.toContain('teacher:7');
    expect(h.select.dataset.loaded).toBe('true');
    expect(h.button.disabled).toBe(false);
  });

  it('keeps failed loading retryable and sends nothing before explicit selection', async () => {
    const h = recipientHarness();
    h.request.mockRejectedValueOnce(new Error('Network error'));
    await expect(h.context.loadAgentActionRecipients(h.block)).rejects.toThrow('Network error');
    expect(h.button.disabled).toBe(true);
    expect(h.select.dataset.loaded).not.toBe('true');
    await expect(h.context.executeAgentAction(h.button)).rejects.toThrow('请先选择');
    expect(h.request).toHaveBeenCalledTimes(1);
    h.request.mockResolvedValueOnce({ contacts: [{ identity: 'student:7', role: 'student', can_send: true, display_name: 'Student' }] });
    await h.context.loadAgentActionRecipients(h.block);
    h.select.selectedOptions = [{ value: 'student:7' }];
    h.request.mockResolvedValueOnce({ confirmation_token: 'confirmed' }).mockResolvedValueOnce({ task: { id: 11 }, result: { label: '已发送' } });
    await h.context.executeAgentAction(h.button);
    expect(JSON.parse(h.request.mock.calls[2][1].body)).toEqual({ params: { recipient_identities: ['student:7'] } });
    expect(JSON.parse(h.request.mock.calls[3][1].body)).toEqual({ params: { recipient_identities: ['student:7'] }, confirmation_token: 'confirmed' });
  });
});
