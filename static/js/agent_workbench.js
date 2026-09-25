// Agent workbench: the teacher's virtual assistant inside the AI window.
// One task at a time is shown as a timeline (思考/决定/工具/操作/疑问/结果),
// the composer adapts to the task (new task / answer / supplement / follow-up),
// and super admins manage the platform-wide queue from a drawer.

import { html as lq } from './lq/components.js';
import { choose as chooseGlass, confirm as confirmGlass } from './lq/dialogs.js';
import { collectPageContext, contextLabel } from './ai_workspace_context.js';
import {
    buildTimeline, escapeHtml, formatTime, icon, renderAdminRow, renderInstruction, renderLiveState,
    renderQuestionCard, renderResult, renderTaskListItem, statusChip,
} from './agent_workbench_render.js';

const LIST_REFRESH_MS = 5000;
const IDLE_REFRESH_MS = 30000;
const EVENT_POLL_MS = 2500;
const STREAM_RETRY_COOLDOWN_MS = 60000;
const MAX_FILES = 5;
const MAX_FILE_BYTES = 10 * 1024 * 1024;
const MAX_TOTAL_BYTES = 20 * 1024 * 1024;
const ALLOWED_EXTENSIONS = new Set(['.txt', '.md', '.markdown', '.csv', '.json', '.xml', '.yaml', '.yml', '.py', '.js', '.ts',
    '.html', '.htm', '.css', '.sql', '.log', '.docx', '.doc', '.pdf', '.pptx', '.ppt', '.xlsx', '.xls',
    '.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp']);
const COMPOSER_COPY = {
    new: ['描述你要 Agent 完成的事，例如“把 3 班未交第 5 次作业的同学列出来并私信提醒”…', 'Enter 发送 · Shift+Enter 换行 · 可附图片/文档（Agent 会直接看图）', '加入队列'],
    answer: ['也可以直接输入你的回答（作为“自定义输入”提交）…', '可在上方选项中点选后提交，或直接在这里输入', '回答'],
    supplement: ['给正在执行的任务补充说明…', '补充说明会在 Agent 的下一步被读取', '补充'],
    followup: ['继续追问或提新要求（基于这次结果继续）…', '会创建一个延续本次上下文的新任务', '追问'],
};

function inferTaskType(instruction, context) {
    const text = `${instruction || ''} ${context.page?.title || ''}`.toLowerCase();
    if (/公文|红头|文号|校发|院发|(学校|学院).{0,6}(规定|通知|文件|要求)|规章|办法|细则/.test(text)) return 'gongwen_lookup';
    if (/学习文档|导学|下一节课|下次课|第\s*\d+\s*(课|次)/.test(text)) return 'lesson_document';
    if (/作业|考试|试卷|题目|出题|测验/.test(text)) return 'assignment_blueprint';
    if (/博客|博文|反思|发布文章/.test(text)) return 'blog_draft';
    if (/通知|提醒|未交|私信/.test(text)) return 'student_notification';
    if (/(材料|课件|资料|教材)/.test(text) && /(整理|收集|归档|汇总|重命名|移动|删除)/.test(text)) return 'course_material_digest';
    return 'general_teaching_task';
}

function recommendedWorkflowKeys(context) {
    if (context.assignmentId) return ['assignment_exam_workflow', 'submission_grading_feedback', 'student_support', 'classroom_preparation'];
    if (context.materialId) return ['lesson_document_generation', 'material_operations', 'classroom_preparation', 'blog_and_reflection'];
    if (context.classOfferingId) return ['classroom_preparation', 'lesson_document_generation', 'assignment_exam_workflow', 'discussion_collaboration'];
    if (context.manageContext?.pageTitle) return ['course_roster_setup', 'operations_admin', 'gongwen_lookup', 'material_operations'];
    return ['classroom_preparation', 'assignment_exam_workflow', 'gongwen_lookup', 'blog_and_reflection'];
}

function extensionOf(name) {
    const text = String(name || '').toLowerCase();
    const index = text.lastIndexOf('.');
    return index > -1 ? text.slice(index) : '';
}

function panelMarkup(isAdmin, isTeacher) {
    const closeButton = lq.button({ label: '', size: 'sm', variant: 'ghost', icon: 'x', attrs: { 'data-awb-drawer-close': '', 'aria-label': '收起' } });
    return `
        <div class="awb-bar">
            <div class="awb-bar__queue" data-awb-queue aria-live="polite"></div>
            <div class="awb-bar__actions">
                ${lq.button({ label: '新任务', size: 'sm', variant: 'ghost', icon: 'plus', attrs: { 'data-awb-new': '' } })}
                ${lq.button({ label: '我的任务', size: 'sm', variant: 'ghost', icon: 'list', attrs: { 'data-awb-drawer-toggle': 'history', 'aria-expanded': 'false' } })}
                ${isAdmin ? lq.button({ label: '队列管理', size: 'sm', variant: 'ghost', icon: 'settings', attrs: { 'data-awb-drawer-toggle': 'admin', 'aria-expanded': 'false' } }) : ''}
            </div>
        </div>
        <div class="awb-scroll" data-awb-scroll>
            <div class="awb-head" data-awb-head></div>
            <div class="awb-timeline" data-awb-timeline></div>
        </div>
        <form class="awb-composer" data-awb-composer>
            <div class="awb-composer__files" data-awb-files hidden></div>
            <textarea class="awb-composer__input" data-awb-input rows="2" aria-label="给 Agent 的指令"></textarea>
            <div class="awb-composer__bar">
                <div class="awb-composer__toggles" data-awb-toggles>
                    <label class="awb-toggle" title="更充分的推理与核对，耗时更长"><input type="checkbox" data-awb-deep><span>深度思考</span></label>
                    <label class="awb-toggle" title="不参考你以往的 Agent 任务记录"><input type="checkbox" data-awb-nohistory><span>不带历史</span></label>
                </div>
                <div class="awb-composer__tools">
                    ${lq.button({ label: '截图', size: 'sm', variant: 'ghost', icon: 'layout-dashboard', attrs: { 'data-awb-capture': '', title: '截取并标注当前页面，作为任务附件' } })}
                    ${lq.button({ label: '附件', size: 'sm', variant: 'ghost', icon: 'paperclip', attrs: { 'data-awb-attach': '', title: 'txt/md/csv/json/docx/pdf/xlsx/pptx/图片，最多 5 个' } })}
                    ${lq.button({ label: '加入队列', size: 'sm', variant: 'prominent', icon: 'send', type: 'submit', attrs: { 'data-awb-send': '' } })}
                </div>
            </div>
            <small class="awb-composer__hint" data-awb-hint></small>
            <input type="file" data-awb-file-input multiple hidden accept="image/*,.txt,.md,.markdown,.csv,.json,.xml,.yaml,.yml,.py,.js,.ts,.html,.htm,.css,.sql,.log,.docx,.doc,.pdf,.pptx,.ppt,.xlsx,.xls">
        </form>
        <aside class="awb-drawer" data-awb-drawer="history" hidden aria-label="我的 Agent 任务">
            <header class="awb-drawer__head"><strong>我的任务</strong>
                <span class="awb-drawer__tools">${lq.button({ label: '清理已结束', size: 'sm', variant: 'ghost', icon: 'trash-2', attrs: { 'data-awb-clear-history': '' } })}${closeButton}</span></header>
            ${isTeacher ? '<details class="awb-subs" data-awb-subs><summary>定时任务（每天自动执行）</summary><div data-awb-subs-list class="awb-subs__list"></div></details>' : ''}
            <ul class="awb-list" data-awb-list></ul>
        </aside>
        ${isAdmin ? `<aside class="awb-drawer awb-drawer--admin" data-awb-drawer="admin" hidden aria-label="Agent 队列管理">
            <header class="awb-drawer__head"><strong>全平台 Agent 队列</strong>${closeButton}</header>
            <div class="awb-admin__state" data-awb-admin-state></div>
            <div class="awb-admin__controls" data-awb-admin-controls></div>
            <ul class="awb-admin__list" data-awb-admin-list></ul>
        </aside>` : ''}`;
}

export function createAgentWorkbench({ root, config, notify, apiJson, capture, fab }) {
    const isTeacher = config.userRole === 'teacher';
    const state = {
        active: false, windowOpen: false, bootstrap: null, bootstrapping: null, isAdmin: false, tasks: [], queue: {},
        currentId: null, current: null, lastEventId: 0, stream: null,
        files: [], busy: false, drawer: null, workflowKey: '', subs: null, timers: {}, streamRetryAt: 0,
    };
    const el = {};

    function mount(isAdmin) {
        root.innerHTML = panelMarkup(isAdmin, isTeacher);
        ['queue', 'head', 'timeline', 'scroll', 'input', 'files', 'hint', 'file-input', 'list', 'subs-list', 'toggles',
            'admin-state', 'admin-controls', 'admin-list', 'deep', 'nohistory', 'send'].forEach((name) => {
            el[name] = root.querySelector(`[data-awb-${name}]`);
        });
        bindEvents();
        renderHead();
        renderComposer();
    }

    // ------------------------------------------------------------ data
    function loadBootstrap() {
        if (state.bootstrap) return Promise.resolve(state.bootstrap);
        state.bootstrapping ||= apiJson('/api/agent-tasks/bootstrap').then((data) => {
            state.bootstrap = data;
            state.isAdmin = Boolean(data.is_super_admin);
            mount(state.isAdmin);
            applyList(data);
            return data;
        }).finally(() => { state.bootstrapping = null; });
        return state.bootstrapping;
    }

    function applyList(data) {
        state.tasks = Array.isArray(data.tasks) ? data.tasks : [];
        state.queue = data.queue_state || {};
        renderQueueBar();
        renderList();
        updateFab();
        if (!state.currentId) renderHead();
        else if (state.current) {
            const fresh = state.tasks.find((task) => task.id === state.current.id);
            if (fresh && (fresh.queue_position !== state.current.queue_position || fresh.elapsed_seconds !== state.current.elapsed_seconds)) {
                Object.assign(state.current, { queue_position: fresh.queue_position, estimated_wait_label: fresh.estimated_wait_label, elapsed_seconds: fresh.elapsed_seconds });
                renderTask();
            }
        }
    }

    async function refreshList() {
        try {
            applyList(await apiJson('/api/agent-tasks?limit=40'));
        } catch {
            // Advisory refresh; the next tick retries.
        }
    }

    async function openTask(taskId, { scroll = true } = {}) {
        if (!taskId) return;
        await loadBootstrap();
        if (Number(state.currentId) !== Number(taskId)) {
            closeStream();
            state.currentId = Number(taskId);
            state.current = null;
            state.lastEventId = 0;
            el.timeline.innerHTML = '';
        }
        const data = await apiJson(`/api/agent-tasks/${taskId}`);
        if (Number(state.currentId) !== Number(taskId)) return;
        state.current = data.task;
        state.lastEventId = Math.max(0, ...(data.task.events || []).map((event) => Number(event.id) || 0));
        renderTask({ forceScroll: scroll });
        renderList();
        syncStream();
    }

    function newTask({ prefill = '' } = {}) {
        closeStream();
        state.currentId = null;
        state.current = null;
        el.timeline.innerHTML = '';
        renderHead();
        renderComposer();
        renderList();
        if (prefill) {
            el.input.value = prefill;
            autoSize();
        }
        el.input.focus({ preventScroll: true });
    }

    // ------------------------------------------------------------ live updates
    function closeStream() {
        state.stream?.close();
        state.stream = null;
        window.clearInterval(state.timers.events);
        state.timers.events = null;
    }

    function syncStream() {
        const task = state.current;
        if (!task || task.is_terminal || !state.active || !state.windowOpen || document.hidden) {
            closeStream();
            return;
        }
        if (state.stream) return;
        // SSE first; after an error, poll for a cooldown and then try SSE again
        // (a transient proxy/network blip must not mean polling forever).
        if (Date.now() >= state.streamRetryAt && typeof EventSource === 'function') {
            window.clearInterval(state.timers.events);
            state.timers.events = null;
            const source = new EventSource(`/api/agent-tasks/${task.id}/stream?after=${state.lastEventId}`);
            source.onmessage = (message) => {
                try { handleEvents(JSON.parse(message.data)); } catch { /* ignore a malformed frame */ }
            };
            source.onerror = () => {
                source.close();
                if (state.stream === source) state.stream = null;
                state.streamRetryAt = Date.now() + STREAM_RETRY_COOLDOWN_MS;
                syncStream();
            };
            state.stream = source;
            return;
        }
        if (!state.timers.events) state.timers.events = window.setInterval(pollEvents, EVENT_POLL_MS);
    }

    async function pollEvents() {
        if (!state.current) return;
        try {
            handleEvents(await apiJson(`/api/agent-tasks/${state.current.id}/events?after=${state.lastEventId}`));
        } catch {
            // Keep polling; transient failures are expected on flaky networks.
        }
    }

    // The server writes a message-center notification when a task finishes or
    // asks a question; refresh the bell once so the teacher sees it at once.
    function refreshFinishNotification(taskId) {
        const key = `${taskId}:${state.lastEventId}`;
        if (state.bellRefreshed === key) return;
        state.bellRefreshed = key;
        if (typeof window.refreshMessageCenterBell === 'function') {
            Promise.resolve(window.refreshMessageCenterBell({ allowPopup: true })).catch(() => {});
            return;
        }
        window.dispatchEvent(new CustomEvent('message-center:refresh-requested', { detail: { source: 'agent-task', taskId, allowPopup: true } }));
    }

    function handleEvents(payload = {}) {
        const task = state.current;
        if (!task) return;
        const events = (payload.events || []).filter((event) => Number(event.id) > state.lastEventId);
        if (events.length) {
            task.events = [...(task.events || []), ...events];
            state.lastEventId = Math.max(state.lastEventId, ...events.map((event) => Number(event.id)));
        }
        const statusChanged = (payload.status && payload.status !== task.status)
            || (payload.runtime_status !== undefined && payload.runtime_status !== task.runtime_status);
        const lifecycle = events.some((event) => /^(question_|task_|pause_)|^(resumed|canceled|cancel_requested)$/.test(event.event_type));
        if (payload.is_terminal || events.some((event) => event.event_type === 'question_requested')) refreshFinishNotification(task.id);
        if (statusChanged || payload.is_terminal || lifecycle) {
            closeStream();
            void openTask(task.id, { scroll: false }).then(refreshList).catch(() => {});
        } else if (events.length) {
            renderTask();
        }
    }

    // ------------------------------------------------------------ rendering
    function nearBottom() {
        const box = el.scroll;
        return box.scrollHeight - box.scrollTop - box.clientHeight < 80;
    }

    function patchTimeline(entries, { forceScroll = false } = {}) {
        const stick = forceScroll || nearBottom();
        const container = el.timeline;
        const existing = new Map(Array.from(container.children).map((node) => [node.dataset.key, node]));
        let previous = null;
        entries.filter(Boolean).forEach((entry) => {
            let node = existing.get(entry.key);
            if (node) {
                existing.delete(entry.key);
            } else {
                const template = document.createElement('template');
                template.innerHTML = entry.html.trim();
                node = template.content.firstElementChild || document.createElement('div');
                node.dataset.key = entry.key;
                node.querySelectorAll('.ai-chat-markdown table').forEach((table) => table.classList.add('ai-chat-markdown-table'));
            }
            const expected = previous ? previous.nextElementSibling : container.firstElementChild;
            if (node !== expected) container.insertBefore(node, expected);
            previous = node;
        });
        existing.forEach((node) => node.remove());
        if (stick) el.scroll.scrollTop = el.scroll.scrollHeight;
    }

    function renderTask(options = {}) {
        const task = state.current;
        if (!task) return;
        renderHead();
        const entries = [renderInstruction(task), ...buildTimeline(task)];
        if (task.pending_question && task.runtime_status === 'waiting_input') entries.push(renderQuestionCard(task.pending_question));
        entries.push(renderLiveState(task, state.queue), renderResult(task));
        patchTimeline(entries, options);
        renderComposer();
    }

    function renderHead() {
        if (!el.head) return;
        const task = state.current;
        if (!task) {
            renderWelcome();
            return;
        }
        const buttons = [];
        const parkedPaused = task.is_parked && task.runtime_status !== 'waiting_input';
        if (!task.is_terminal && !task.is_parked && (task.status === 'running' || task.started_at)) {
            buttons.push(lq.button({ label: task.pause_requested ? '撤回暂停' : '暂停', size: 'sm', variant: 'soft',
                attrs: { 'data-awb-action': task.pause_requested ? 'resume' : 'pause' } }));
        }
        if (parkedPaused) buttons.push(lq.button({ label: '继续', size: 'sm', variant: 'prominent', icon: 'refresh-cw', attrs: { 'data-awb-action': 'resume' } }));
        if (!task.is_terminal) buttons.push(lq.button({ label: '取消', size: 'sm', variant: 'ghost', icon: 'x', attrs: { 'data-awb-action': 'cancel' } }));
        if (task.is_terminal) buttons.push(lq.button({ label: '删除', size: 'sm', variant: 'ghost', icon: 'trash-2', attrs: { 'data-awb-action': 'delete' } }));
        const meta = [`#${task.id}`, task.origin_label, formatTime(task.created_at)].filter(Boolean).join(' · ');
        el.head.innerHTML = `
            <div class="awb-task-head">
                <div class="awb-task-head__main">
                    <strong class="awb-task-head__title">${escapeHtml(task.title || task.task_type_label || 'Agent 任务')}</strong>
                    <span class="awb-task-head__meta">${statusChip(task)}<small>${escapeHtml(meta)}</small></span>
                </div>
                <div class="awb-task-head__actions">${buttons.join('')}</div>
            </div>`;
    }

    function renderWelcome() {
        const context = collectPageContext();
        const catalog = Array.isArray(state.bootstrap?.workflow_catalog) ? state.bootstrap.workflow_catalog : [];
        const preferred = recommendedWorkflowKeys(context).map((key) => catalog.find((item) => item.key === key)).filter(Boolean);
        const starters = [...new Map([...preferred, ...catalog].map((item) => [item.key, item])).values()].slice(0, 4);
        const recent = state.tasks.filter((task) => task.is_owner).slice(0, 3);
        el.head.innerHTML = `
            <section class="awb-welcome">
                <div class="awb-welcome__hero">${icon('decision')}<div><strong>你的平台全能助手</strong>
                    <p>以你的身份和权限操作平台、整理数据、生成材料，并可联网查证。任务在全平台队列中排队执行；需要你确认时会列出选项让你点选。</p></div></div>
                <p class="awb-welcome__safety">安全边界：删除账号、清空数据等高危操作会被硬性拦截；批量修改或删除前 Agent 会自检，必要时先问你。</p>
                ${starters.length ? `<div class="awb-welcome__label">适合「${escapeHtml(contextLabel(context))}」的任务</div>
                <div class="awb-starters">${starters.map((item) => `
                    <button type="button" class="awb-starter${item.key === state.workflowKey ? ' is-selected' : ''}" data-awb-starter="${escapeHtml(item.key)}">
                        <strong>${escapeHtml(item.name || '教学事务')}</strong>
                        <small>${escapeHtml((item.steps || [])[0] || item.agent_capability || '')}</small>
                    </button>`).join('')}</div>` : ''}
                ${recent.length ? `<div class="awb-welcome__label">最近的任务</div><ul class="awb-list awb-list--inline">${recent.map((task) => renderTaskListItem(task, null)).join('')}</ul>` : ''}
            </section>`;
    }

    function renderQueueBar() {
        if (!el.queue) return;
        const queue = state.queue || {};
        const chips = [];
        if (queue.queue_paused) chips.push(lq.chip({ label: '队列已暂停', kind: 'status', tone: 'warning', size: 'sm' }));
        chips.push(lq.chip({ label: `执行中 ${queue.running_count || 0}/${queue.global_concurrency || 1}`, kind: 'status', tone: queue.running_count ? 'info' : 'success', size: 'sm' }));
        chips.push(lq.chip({ label: `排队 ${queue.queued_count || 0}`, kind: 'status', tone: 'neutral', size: 'sm' }));
        if (queue.parked_count) chips.push(lq.chip({ label: `暂停/待回答 ${queue.parked_count}`, kind: 'status', tone: 'neutral', size: 'sm' }));
        el.queue.innerHTML = chips.join('');
    }

    function renderList() {
        if (!el.list) return;
        const mine = state.tasks.filter((task) => task.is_owner);
        el.list.innerHTML = mine.length ? mine.map((task) => renderTaskListItem(task, state.currentId)).join('')
            : '<li class="awb-empty">还没有 Agent 任务。</li>';
    }

    function updateFab() {
        const queue = state.queue || {};
        const light = document.getElementById('ai-agent-fab-light');
        const badge = document.getElementById('ai-agent-fab-queue-badge');
        const tone = queue.queue_paused ? 'is-red' : queue.available_slots > 0 ? 'is-green' : 'is-yellow';
        if (light) {
            light.className = `ai-workspace-fab__light ${tone}`;
            light.title = queue.queue_paused ? 'Agent 队列已暂停' : queue.available_slots > 0 ? '可以执行新任务' : '任务排队中';
        }
        if (badge) {
            badge.textContent = String(queue.queued_count || 0);
            badge.hidden = !queue.queued_count;
        }
        const waiting = state.tasks.some((task) => task.is_owner && task.runtime_status === 'waiting_input' && !task.is_terminal);
        fab?.classList.toggle('has-agent-question', waiting);
    }

    function composerMode() {
        const task = state.current;
        if (!task) return 'new';
        if (task.is_terminal) return 'followup';
        if (task.runtime_status === 'waiting_input' && task.pending_question) return 'answer';
        return 'supplement';
    }

    function renderComposer() {
        if (!el.input) return;
        const mode = composerMode();
        const [placeholder, hint, sendLabel] = COMPOSER_COPY[mode];
        el.input.placeholder = placeholder;
        el.hint.textContent = hint;
        const canAttach = mode === 'new';
        root.querySelectorAll('[data-awb-attach],[data-awb-capture]').forEach((button) => { button.disabled = !canAttach; });
        el.toggles.hidden = mode !== 'new';
        if (el.send) {
            el.send.disabled = state.busy;
            const label = el.send.querySelector('.lq-btn__label');
            if (label) label.textContent = sendLabel;
        }
        renderFiles();
    }

    function renderFiles() {
        if (!el.files) return;
        el.files.hidden = !state.files.length;
        el.files.innerHTML = state.files.map((file, index) => `
            <span class="awb-filechip">${escapeHtml(file.name)}<button type="button" data-awb-file-remove="${index}" aria-label="移除 ${escapeHtml(file.name)}">×</button></span>`).join('');
    }

    function autoSize() {
        el.input.style.height = 'auto';
        el.input.style.height = `${Math.min(el.input.scrollHeight, 180)}px`;
    }

    // ------------------------------------------------------------ actions
    function addFiles(list) {
        const next = [...state.files, ...Array.from(list || [])];
        if (next.length > MAX_FILES) return notify(`最多携带 ${MAX_FILES} 个附件。`, 'warning');
        let total = 0;
        for (const file of next) {
            if (!ALLOWED_EXTENSIONS.has(extensionOf(file.name)) && !String(file.type).startsWith('image/')) return notify(`附件 ${file.name} 类型暂不支持。`, 'warning');
            if (file.size > MAX_FILE_BYTES) return notify(`附件 ${file.name} 超过 10MB。`, 'warning');
            total += file.size;
        }
        if (total > MAX_TOTAL_BYTES) return notify('附件总大小超过 20MB。', 'warning');
        state.files = next;
        renderFiles();
        return undefined;
    }

    async function submitComposer() {
        const text = el.input.value.trim();
        const mode = composerMode();
        if (state.busy) return;
        if (mode === 'answer') {
            await submitAnswer(root.querySelector('[data-awb-question-form]'), text);
            return;
        }
        if (text.length < (mode === 'new' ? 6 : 2)) {
            notify(mode === 'new' ? '请把任务描述得更具体一些。' : '请输入要补充的内容。', 'warning');
            return;
        }
        state.busy = true;
        renderComposer();
        try {
            if (mode === 'new') {
                const context = collectPageContext(state.workflowKey ? { agentWorkflowKey: state.workflowKey } : {});
                const workflow = (state.bootstrap?.workflow_catalog || []).find((item) => item.key === state.workflowKey);
                const payload = { task_type: workflow?.task_type || inferTaskType(text, context), instruction: text, page_context: context,
                    deep_thinking: Boolean(el.deep?.checked), no_history: Boolean(el.nohistory?.checked) };
                let body = JSON.stringify(payload);
                if (state.files.length) {
                    body = new FormData();
                    body.append('payload', JSON.stringify(payload));
                    state.files.forEach((file) => body.append('files', file));
                }
                const data = await apiJson('/api/agent-tasks', { method: 'POST', body });
                state.files = [];
                state.workflowKey = '';
                el.input.value = '';
                if (el.nohistory) el.nohistory.checked = false;
                notify('已加入全平台 Agent 队列。', 'success');
                await openTask(data.task.id);
            } else {
                const data = await apiJson(`/api/agent-tasks/${state.current.id}/follow-up`, { method: 'POST', body: JSON.stringify({ instruction: text }) });
                el.input.value = '';
                notify(data.supplemented ? '补充说明已送达当前任务。' : '追问任务已加入队列。', 'success');
                await openTask(data.task?.id || state.current.id);
            }
            await refreshList();
        } catch (error) {
            notify(error.message || '提交失败', 'error');
        } finally {
            state.busy = false;
            autoSize();
            renderComposer();
        }
    }

    function collectAnswers(form, fallbackText) {
        const answers = [];
        let missing = '';
        form?.querySelectorAll('[data-awb-qid]').forEach((group) => {
            const selected = Array.from(group.querySelectorAll('[data-awb-option][aria-pressed="true"]')).map((node) => node.dataset.awbOption);
            const customOn = group.querySelector('[data-awb-custom]')?.getAttribute('aria-pressed') === 'true';
            let custom = customOn ? (group.querySelector('[data-awb-custom-input]')?.value || '').trim() : '';
            if (!selected.length && !custom && fallbackText) custom = fallbackText;
            if (!selected.length && !custom) missing ||= group.querySelector('legend')?.textContent?.trim() || '问题';
            answers.push({ id: group.dataset.awbQid, selected, custom });
        });
        return { answers, missing };
    }

    async function submitAnswer(form, fallbackText = '') {
        const task = state.current;
        if (!task?.pending_question || state.busy) return;
        const { answers, missing } = collectAnswers(form, fallbackText);
        if (missing) {
            notify(`请先回答：${missing}`, 'warning');
            return;
        }
        state.busy = true;
        renderComposer();
        try {
            await apiJson(`/api/agent-tasks/${task.id}/answer`, {
                method: 'POST', body: JSON.stringify({ question_id: task.pending_question.id, answers }),
            });
            el.input.value = '';
            notify('已提交回答，Agent 会优先继续。', 'success');
            await openTask(task.id, { scroll: true });
            await refreshList();
        } catch (error) {
            notify(error.message || '回答提交失败', 'error');
        } finally {
            state.busy = false;
            renderComposer();
        }
    }

    function toggleOption(button) {
        const group = button.closest('[data-awb-qid]');
        if (!group) return;
        const pressed = button.getAttribute('aria-pressed') !== 'true';
        if (group.dataset.multi !== '1') group.querySelectorAll('.awb-option').forEach((node) => node.setAttribute('aria-pressed', 'false'));
        button.setAttribute('aria-pressed', pressed ? 'true' : 'false');
        const customInput = group.querySelector('[data-awb-custom-input]');
        const customOn = group.querySelector('[data-awb-custom]').getAttribute('aria-pressed') === 'true';
        customInput.hidden = !customOn;
        if (button.hasAttribute('data-awb-custom') && customOn) customInput.focus();
    }

    async function taskAction(action) {
        const task = state.current;
        if (!task) return;
        try {
            if (action === 'cancel') {
                if (!await confirmGlass({ title: '取消任务', message: '确定取消这个 Agent 任务吗？已经执行的操作不会回滚。', confirmLabel: '取消任务', danger: true })) return;
                await apiJson(`/api/agent-tasks/${task.id}/cancel`, { method: 'POST' });
            } else if (action === 'pause' || action === 'resume') {
                await apiJson(`/api/agent-tasks/${task.id}/${action}`, { method: 'POST' });
                notify(action === 'pause' ? (task.status === 'running' ? '将在当前步骤完成后暂停。' : '任务已暂停。') : '任务将继续执行。', 'success');
            } else if (action === 'delete') {
                if (!await confirmGlass({ title: '删除任务记录', message: '从历史中删除这条 Agent 任务？', confirmLabel: '删除', danger: true })) return;
                await apiJson(`/api/agent-tasks/${task.id}`, { method: 'DELETE' });
                newTask();
                await refreshList();
                return;
            } else if (action === 'retry-edit') {
                newTask({ prefill: task.private_instruction || '' });
                return;
            } else if (action === 'retry') {
                const data = await apiJson(`/api/agent-tasks/${task.id}/retry`, { method: 'POST', body: JSON.stringify({}) });
                notify('已重新加入队列。', 'success');
                await openTask(data.task.id);
                await refreshList();
                return;
            }
            await openTask(task.id, { scroll: false });
            await refreshList();
        } catch (error) {
            notify(error.message || '操作失败', 'error');
        }
    }

    // ------------------------------------------------------------ drawers
    function setDrawer(name) {
        state.drawer = state.drawer === name ? null : name;
        root.querySelectorAll('[data-awb-drawer]').forEach((drawer) => { drawer.hidden = drawer.dataset.awbDrawer !== state.drawer; });
        root.querySelectorAll('[data-awb-drawer-toggle]').forEach((button) => button.setAttribute('aria-expanded', String(button.dataset.awbDrawerToggle === state.drawer)));
        if (state.drawer === 'history') {
            renderList();
            void loadSubscriptions();
        }
        if (state.drawer === 'admin') void loadAdmin();
    }

    async function loadSubscriptions() {
        if (!el['subs-list']) return;
        try {
            state.subs = await apiJson('/api/agent-tasks/subscriptions');
            renderSubscriptions();
        } catch (error) {
            el['subs-list'].innerHTML = `<p class="awb-dim">${escapeHtml(error.message || '定时任务加载失败')}</p>`;
        }
    }

    function renderSubscriptions() {
        const subscriptions = Array.isArray(state.subs?.subscriptions) ? state.subs.subscriptions : [];
        const hours = Array.from({ length: 24 }, (_, hour) => `<option value="${hour}">${String(hour).padStart(2, '0')}:00</option>`).join('');
        el['subs-list'].innerHTML = subscriptions.length ? subscriptions.map((item) => `
            <label class="awb-sub${item.enabled ? ' is-on' : ''}">
                <input type="checkbox" data-awb-sub="${escapeHtml(item.key)}" ${item.enabled ? 'checked' : ''}>
                <span class="awb-sub__copy"><strong>${escapeHtml(item.label || item.key)}</strong>
                <small>${escapeHtml(item.enabled ? `下次 ${item.next_run_at || ''}${item.last_run_message ? ` · 上次：${item.last_run_message}` : ''}` : (item.description || ''))}</small>
                ${item.attention_message ? `<small class="awb-warn">${escapeHtml(item.attention_message)}</small>` : ''}</span>
                <select data-awb-sub-hour="${escapeHtml(item.key)}" aria-label="执行时间">${hours}</select>
            </label>`).join('') : '<p class="awb-dim">暂无可用的定时任务模板。</p>';
        subscriptions.forEach((item) => {
            const select = el['subs-list'].querySelector(`[data-awb-sub-hour="${CSS.escape(String(item.key))}"]`);
            if (select) select.value = String(Math.max(0, Math.min(Number(item.hour ?? 7), 23)));
        });
    }

    async function saveSubscription(key) {
        const toggle = el['subs-list'].querySelector(`[data-awb-sub="${CSS.escape(key)}"]`);
        const hour = el['subs-list'].querySelector(`[data-awb-sub-hour="${CSS.escape(key)}"]`);
        try {
            state.subs = await apiJson('/api/agent-tasks/subscriptions', { method: 'POST',
                body: JSON.stringify({ template_key: key, enabled: Boolean(toggle?.checked), hour: Number(hour?.value || 0) }) });
            renderSubscriptions();
            notify(toggle?.checked ? '定时任务已更新。' : '定时任务已关闭。', 'success');
        } catch (error) {
            notify(error.message || '定时任务更新失败', 'error');
            void loadSubscriptions();
        }
    }

    async function loadAdmin() {
        if (!state.isAdmin || !el['admin-list']) return;
        try {
            renderAdmin(await apiJson('/api/agent-tasks/admin/queue'));
        } catch (error) {
            el['admin-list'].innerHTML = `<li class="awb-empty">${escapeHtml(error.message || '队列加载失败')}</li>`;
        }
    }

    function renderAdmin(data) {
        const queue = data.queue_state || {};
        const pausedBy = queue.queue_paused ? `<strong class="awb-warn">队列已暂停</strong>（${escapeHtml(queue.queue_paused_by || '')} ${escapeHtml(formatTime(queue.queue_paused_at))}）· ` : '';
        el['admin-state'].innerHTML = `<p>${pausedBy}执行中 ${queue.running_count || 0}/${queue.global_concurrency || 1} · 排队 ${queue.queued_count || 0} · 暂停/待回答 ${queue.parked_count || 0}</p>`;
        el['admin-controls'].innerHTML = [
            queue.queue_paused
                ? lq.button({ label: '恢复队列', size: 'sm', variant: 'prominent', icon: 'refresh-cw', attrs: { 'data-awb-queue-action': 'resume' } })
                : lq.button({ label: '暂停队列', size: 'sm', variant: 'soft', attrs: { 'data-awb-queue-action': 'pause' } }),
            lq.button({ label: '清空排队', size: 'sm', variant: 'destructive', icon: 'trash-2', attrs: { 'data-awb-queue-action': 'clear' } }),
        ].join('');
        const tasks = Array.isArray(data.tasks) ? data.tasks : [];
        el['admin-list'].innerHTML = tasks.length ? tasks.map(renderAdminRow).join('') : '<li class="awb-empty">当前没有排队或执行中的任务。</li>';
        state.queue = queue;
        renderQueueBar();
        updateFab();
    }

    async function queueAction(action) {
        try {
            let body = '{}';
            if (action === 'clear') {
                const choice = await chooseGlass({ title: '清空 Agent 排队', message: '将取消尚未开始的排队任务，并通知任务所有者。已暂停或等待回答的任务是否也一并取消？',
                    cancelLabel: '返回', choices: [{ value: 'unstarted', label: '只清空未开始的' }, { value: 'all', label: '全部清空', danger: true }] });
                if (choice.status !== 'chosen') return;
                body = JSON.stringify({ include_parked: choice.value === 'all' });
            }
            const data = await apiJson(`/api/agent-tasks/admin/queue/${action}`, { method: 'POST', body });
            notify(data.message || '已更新', 'success');
            renderAdmin(data);
            await refreshList();
        } catch (error) {
            notify(error.message || '队列操作失败', 'error');
        }
    }

    async function adminTaskAction(action, taskId) {
        try {
            if (action === 'stop' && !await confirmGlass({ title: '停止任务', message: '停止后任务会被取消，所有者会在任务记录中看到。已执行的操作不会回滚。', confirmLabel: '停止', danger: true })) return;
            renderAdmin(await apiJson(`/api/agent-tasks/admin/tasks/${taskId}/${action}`, { method: 'POST' }));
            await refreshList();
        } catch (error) {
            notify(error.message || '操作失败', 'error');
        }
    }

    // ------------------------------------------------------------ events
    function onClick(event) {
        const target = event.target;
        const option = target.closest('.awb-option');
        if (option) return toggleOption(option);
        const open = target.closest('[data-awb-open]');
        if (open) {
            if (state.drawer === 'history') setDrawer('history');
            return void openTask(Number(open.dataset.awbOpen)).catch((error) => notify(error.message, 'error'));
        }
        const starter = target.closest('[data-awb-starter]');
        if (starter) {
            const workflow = (state.bootstrap?.workflow_catalog || []).find((item) => item.key === starter.dataset.awbStarter);
            state.workflowKey = workflow?.key || '';
            el.input.value = workflow?.starter_prompt || (workflow?.steps || [])[0] || '';
            autoSize();
            renderWelcome();
            return el.input.focus();
        }
        const action = target.closest('[data-awb-action]');
        if (action) return void taskAction(action.dataset.awbAction);
        if (target.closest('[data-awb-new]')) return newTask();
        const drawerToggle = target.closest('[data-awb-drawer-toggle]');
        if (drawerToggle) return setDrawer(drawerToggle.dataset.awbDrawerToggle);
        if (target.closest('[data-awb-drawer-close]')) return setDrawer(state.drawer);
        if (target.closest('[data-awb-attach]')) return el['file-input'].click();
        if (target.closest('[data-awb-capture]')) {
            return void capture().then((file) => { if (file) addFiles([file]); }).catch((error) => notify(error.message || '截图失败', 'error'));
        }
        const remove = target.closest('[data-awb-file-remove]');
        if (remove) {
            state.files.splice(Number(remove.dataset.awbFileRemove), 1);
            return renderFiles();
        }
        const queueButton = target.closest('[data-awb-queue-action]');
        if (queueButton) return void queueAction(queueButton.dataset.awbQueueAction);
        const adminButton = target.closest('[data-awb-admin]');
        if (adminButton) return void adminTaskAction(adminButton.dataset.awbAdmin, adminButton.dataset.taskId);
        if (target.closest('[data-awb-clear-history]')) {
            return void confirmGlass({ title: '清理任务历史', message: '删除你所有已结束的 Agent 任务记录？排队或执行中的任务会保留。', confirmLabel: '删除', danger: true })
                .then((ok) => ok && apiJson('/api/agent-tasks/history', { method: 'DELETE' }).then((data) => {
                    applyList(data);
                    if (state.current?.is_terminal) newTask();
                }))
                .catch((error) => notify(error.message || '清理失败', 'error'));
        }
        return undefined;
    }

    function bindEvents() {
        root.addEventListener('click', onClick);
        root.addEventListener('submit', (event) => {
            event.preventDefault();
            if (event.target.matches('[data-awb-question-form]')) void submitAnswer(event.target);
            else void submitComposer();
        });
        root.addEventListener('change', (event) => {
            if (event.target.matches('[data-awb-file-input]')) {
                addFiles(event.target.files);
                event.target.value = '';
                return;
            }
            const sub = event.target.closest('[data-awb-sub],[data-awb-sub-hour]');
            if (sub) void saveSubscription(sub.dataset.awbSub || sub.dataset.awbSubHour);
        });
        el.input.addEventListener('input', autoSize);
        el.input.addEventListener('keydown', (event) => {
            if (event.key === 'Enter' && !event.shiftKey && !event.isComposing && event.keyCode !== 229) {
                event.preventDefault();
                void submitComposer();
            }
        });
        el.input.addEventListener('paste', (event) => {
            const files = Array.from(event.clipboardData?.files || []);
            if (files.length && composerMode() === 'new') {
                event.preventDefault();
                addFiles(files);
            }
        });
    }

    // ------------------------------------------------------------ lifecycle
    // A closed window never polls: ~200 concurrent users must not generate
    // background Agent traffic (the message center notifies finished tasks).
    function schedule() {
        window.clearInterval(state.timers.list);
        state.timers.list = null;
        if (!state.windowOpen) return;
        const interval = state.active ? LIST_REFRESH_MS : IDLE_REFRESH_MS;
        state.timers.list = window.setInterval(() => {
            if (document.hidden || !state.bootstrap) return;
            void refreshList();
            if (state.drawer === 'admin') void loadAdmin();
            if (state.current && !state.current.is_terminal) syncStream();
        }, interval);
    }

    document.addEventListener('visibilitychange', () => {
        if (document.hidden) closeStream();
        else if (state.active && state.windowOpen) {
            syncStream();
            void refreshList();
        }
    });

    return {
        get active() { return state.active; },
        async activate() {
            state.active = true;
            const bootstrap = await loadBootstrap();
            if (!bootstrap.runtime_configured && !state.runtimeWarned) {
                state.runtimeWarned = true;
                notify('Agent 模型尚未配置，任务会先排队，请联系超级管理员配置 Agent API Key。', 'warning');
            }
            schedule();
            await refreshList();
            if (!state.currentId) {
                const pending = state.tasks.find((task) => task.is_owner && task.runtime_status === 'waiting_input' && !task.is_terminal)
                    || state.tasks.find((task) => task.is_owner && task.is_active);
                if (pending) await openTask(pending.id);
            } else {
                syncStream();
            }
            el.input?.focus({ preventScroll: true });
        },
        deactivate() {
            state.active = false;
            closeStream();
            schedule();
        },
        windowOpened() {
            state.windowOpen = true;
            schedule();
            if (state.active) {
                syncStream();
                void refreshList();
            }
        },
        windowClosed() {
            state.windowOpen = false;
            closeStream();
            schedule();
        },
        openTask,
        async prefill(text) {
            await loadBootstrap();
            newTask({ prefill: text });
        },
        async openSubscriptions() {
            await loadBootstrap();
            if (state.drawer !== 'history') setDrawer('history');
            root.querySelector('[data-awb-subs]')?.setAttribute('open', '');
        },
    };
}
