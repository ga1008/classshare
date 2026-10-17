// 文档与知识树：一个开课班级全部课次的学习文档与知识树管理浮窗（教师）。
// 列表、筛选、单课/批量（未生成 / 文档已更新 / 全部重新生成）生成，生成中自动轮询，
// 打开单课知识树（嵌套 LQ Dialog），并把进度回写到课堂页课次卡片。
import { apiFetch } from '/static/js/api.js';
import { showToast } from '/static/js/ui.js';
import { createDialog, openDialog, confirm as lqConfirm } from './lq/dialogs.js';
import { createComponent } from './lq/components.js';
import { createDomainButton } from './lq/domain-controls.js';
import { openKnowledgeTree } from './session_knowledge_tree.js';

const POLL_MS = 4000;
const FILTERS = Object.freeze([
    { key: 'all', label: '全部' },
    { key: 'missing', label: '未生成' },
    { key: 'stale', label: '需更新' },
    { key: 'generating', label: '生成中' },
    { key: 'failed', label: '失败' },
    { key: 'unbound', label: '未绑定文档' },
    { key: 'completed', label: '已完成' },
]);

const el = (tag, className = '', text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
};

/** Display state of one session row (pure; shared by filters, chips and counts). */
export function rowState(row) {
    if (row.status === 'generating') return 'generating';
    if (!row.has_material && !row.has_tree) return 'unbound';
    if (row.error && !row.has_tree) return 'failed';
    if (!row.has_tree) return 'missing';
    if (row.is_stale) return 'stale';
    if (row.lifecycle === 'completed') return 'completed';
    return row.lifecycle === 'in_progress' ? 'in_progress' : 'not_started';
}

export function matchesFilter(row, filter) {
    if (filter === 'all') return true;
    if (filter === 'failed') return rowState(row) === 'failed' || Boolean(row.error);
    return rowState(row) === filter;
}

export function summarize(rows) {
    const trees = rows.filter(row => row.has_tree);
    return {
        total: rows.length,
        bound: rows.filter(row => row.has_material).length,
        generated: trees.length,
        missing: rows.filter(row => rowState(row) === 'missing').length,
        stale: rows.filter(row => rowState(row) === 'stale').length,
        generating: rows.filter(row => row.status === 'generating').length,
        completed: rows.filter(row => rowState(row) === 'completed').length,
        withProgress: trees.filter(row => Number(row.completed_count) > 0).length,
        average: trees.length ? Math.round(trees.reduce((sum, row) => sum + (Number(row.progress) || 0), 0) / trees.length) : 0,
    };
}

const STATE_CHIP = {
    generating: { label: '生成中', tone: 'info' },
    unbound: { label: '未绑定文档', tone: 'neutral' },
    failed: { label: '生成失败', tone: 'danger' },
    missing: { label: '未生成', tone: 'neutral' },
    stale: { label: '文档已更新', tone: 'warning' },
    completed: { label: '已完成', tone: 'success' },
    in_progress: { label: '进行中', tone: 'warning' },
    not_started: { label: '未开始', tone: 'neutral' },
};

/**
 * @param {{classOfferingId: number|string, trigger?: Element,
 *          resolveSession?: (id: number) => object|null,
 *          onProgress?: (sessionId: number, state: object) => void,
 *          onOpenSession?: (sessionId: number) => void}} options
 */
export function openKnowledgeTreeManager({ classOfferingId, trigger, resolveSession, onProgress, onOpenSession }) {
    const base = `/api/classrooms/${encodeURIComponent(classOfferingId)}`;
    let rows = null;
    let filter = 'all';
    let closed = false;
    let pollTimer = 0;
    let busy = false;
    let lastSignature = '';
    // While a child tree dialog is open its trigger row must stay in the DOM
    // (the layer system tears down a layer whose owner disappears).
    let childOpen = false;
    let listDirty = false;

    const shell = el('div', 'ktm-shell');
    const summary = el('div', 'ktm-summary');
    summary.setAttribute('role', 'status');
    const actions = el('div', 'ktm-actions');
    const missingBtn = createDomainButton({ label: '一键生成未生成的', variant: 'prominent', attrs: { 'data-ktm-batch': 'missing' } });
    const staleBtn = createDomainButton({ label: '重新生成文档已更新的', variant: 'glass', attrs: { 'data-ktm-batch': 'stale' } });
    const allBtn = createDomainButton({ label: '全部重新生成', variant: 'glass', attrs: { 'data-ktm-batch': 'all' } });
    const refreshBtn = createDomainButton({ label: '刷新', variant: 'ghost', attrs: { 'data-ktm-refresh': '' } });
    actions.append(missingBtn, staleBtn, allBtn, refreshBtn);
    const filters = el('div', 'ktm-filters');
    filters.setAttribute('role', 'group');
    filters.setAttribute('aria-label', '按状态筛选');
    const filterButtons = FILTERS.map(item => {
        const button = createDomainButton({ label: item.label, variant: 'ghost', attrs: { 'data-ktm-filter': item.key } });
        filters.append(button);
        return button;
    });
    const list = el('div', 'ktm-list');
    list.setAttribute('role', 'list');
    shell.append(summary, actions, filters, list);

    const root = createDialog({ title: '文档与知识树', body: shell, size: 'xl', closeLabel: '关闭文档与知识树' });
    root.querySelector('.lq-dialog__surface').classList.add('ktm-dialog');
    const setLabel = (button, text) => { const label = button.querySelector('.lq-btn__label'); if (label) label.textContent = text; };

    const sessionLabel = row => {
        const session = resolveSession?.(row.session_id) || null;
        return {
            order: session?.session_number_label || `第 ${row.order_index} 次课`,
            title: session?.detail_title || session?.display_title || row.title || '未命名课次',
            date: session?.date_label || row.session_date || '',
            session,
        };
    };

    function renderSummary() {
        const s = summarize(rows || []);
        summary.replaceChildren(...[
            `共 ${s.total} 次课`, `已绑定文档 ${s.bound}`, `已生成 ${s.generated}`,
            s.missing ? `未生成 ${s.missing}` : '', s.stale ? `需更新 ${s.stale}` : '',
            s.generating ? `生成中 ${s.generating}` : '', `已完成 ${s.completed}`, s.generated ? `平均完成 ${s.average}%` : '',
        ].filter(Boolean).map(text => el('span', 'ktm-summary__item', text)));
        missingBtn.disabled = busy || !s.missing;
        staleBtn.disabled = busy || !s.stale;
        allBtn.disabled = busy || !s.bound;
        setLabel(missingBtn, `一键生成未生成的${s.missing ? `（${s.missing}）` : ''}`);
        setLabel(staleBtn, `重新生成文档已更新的${s.stale ? `（${s.stale}）` : ''}`);
        for (const button of filterButtons) {
            const active = button.dataset.ktmFilter === filter;
            button.setAttribute('aria-pressed', active ? 'true' : 'false');
            if (active) button.dataset.lqSelected = 'true'; else delete button.dataset.lqSelected;
        }
    }

    function progressMark(row) {
        const mark = el('span', 'cw-kp');
        mark.dataset.state = row.has_tree ? row.lifecycle : 'none';
        mark.style.setProperty('--kp', `${Number(row.progress) || 0}%`);
        const ring = el('span', 'cw-kp__ring');
        ring.setAttribute('aria-hidden', 'true');
        mark.append(ring, el('span', '', `${Number(row.progress) || 0}%`));
        mark.title = `本课知识点完成 ${Number(row.progress) || 0}%`;
        return mark;
    }

    function renderRow(row) {
        const info = sessionLabel(row);
        const state = rowState(row);
        const item = el('div', 'ktm-row');
        item.setAttribute('role', 'listitem');
        item.dataset.sessionId = String(row.session_id);
        item.dataset.state = state;

        const head = el('div', 'ktm-row__head');
        head.append(el('span', 'ktm-row__order', info.order), el('strong', 'ktm-row__title', info.title));
        if (info.date) head.append(el('span', 'ktm-row__date', info.date));

        const docs = el('div', 'ktm-row__docs');
        if (row.materials?.length) {
            for (const material of row.materials) {
                if (material.open_url) {
                    const link = el('a', 'ktm-doc', material.name);
                    link.href = material.open_url;
                    link.target = '_blank';
                    link.rel = 'noopener';
                    docs.append(link);
                } else docs.append(el('span', 'ktm-doc', material.name));
            }
        } else docs.append(el('span', 'ktm-row__muted', '未绑定学习材料'));

        const status = el('div', 'ktm-row__status');
        const chip = STATE_CHIP[state] || STATE_CHIP.missing;
        if (state === 'generating') status.append(createComponent('spinner', { size: 'sm' }));
        status.append(createComponent('chip', { label: chip.label, kind: 'status', size: 'sm', tone: chip.tone }));
        if (row.has_tree) status.append(progressMark(row), el('span', 'ktm-row__muted', `${row.completed_count || 0}/${row.leaf_count || 0}`));
        if (row.error) {
            const error = el('span', 'ktm-row__error', row.error);
            error.title = row.error;
            status.append(error);
        }

        const ops = el('div', 'ktm-row__ops');
        if (row.has_tree || row.has_material) {
            ops.append(createDomainButton({ label: row.has_tree ? '查看知识树' : '打开知识树', variant: 'soft', attrs: { 'data-ktm-open': String(row.session_id) } }));
        }
        if (row.has_material) {
            const generate = createDomainButton({
                label: row.has_tree ? '重新生成' : '生成',
                variant: state === 'stale' || state === 'missing' || state === 'failed' ? 'prominent' : 'ghost',
                attrs: { 'data-ktm-generate': String(row.session_id) },
            });
            generate.disabled = busy || state === 'generating';
            ops.append(generate);
        }
        if (onOpenSession) ops.append(createDomainButton({ label: '前往课次', variant: 'ghost', attrs: { 'data-ktm-session': String(row.session_id) } }));
        item.append(head, docs, status, ops);
        return item;
    }

    function renderList() {
        if (childOpen) { listDirty = true; return; }
        listDirty = false;
        if (!rows) { list.replaceChildren(el('p', 'ktm-empty', '正在读取课次…')); return; }
        const visible = rows.filter(row => matchesFilter(row, filter));
        if (!visible.length) { list.replaceChildren(el('p', 'ktm-empty', rows.length ? '没有符合筛选条件的课次。' : '本课堂还没有课次。')); return; }
        // Keep keyboard focus on the same control across re-renders (polling).
        const active = document.activeElement;
        const attr = active && list.contains(active) ? [...active.attributes].find(item => item.name.startsWith('data-ktm-')) : null;
        list.replaceChildren(...visible.map(renderRow));
        if (attr) list.querySelector(`[${attr.name}="${CSS.escape(attr.value)}"]`)?.focus();
    }

    function render() {
        renderSummary();
        renderList();
    }

    function applyRows(next) {
        const signature = JSON.stringify(next);
        if (signature !== lastSignature) {
            lastSignature = signature;
            rows = next;
            for (const row of rows) onProgress?.(row.session_id, row);
            render();
        }
        schedulePoll();
    }

    async function load() {
        try {
            const data = await apiFetch(`${base}/knowledge-trees`, { silent: true });
            if (!closed) applyRows(data.sessions || []);
        } catch (error) {
            if (!closed && !rows) list.replaceChildren(el('p', 'ktm-empty', error.message || '读取失败，请稍后重试。'));
        }
    }

    let generatingBefore = new Set();
    function schedulePoll() {
        window.clearTimeout(pollTimer);
        const generating = new Set((rows || []).filter(row => row.status === 'generating').map(row => row.session_id));
        const finished = (rows || []).filter(row => generatingBefore.has(row.session_id) && !generating.has(row.session_id));
        if (finished.length) {
            const failed = finished.filter(row => row.ai_gen_status === 'failed').length;
            showToast(`${finished.length} 个课次的知识树生成结束${failed ? `，其中 ${failed} 个失败` : ''}。`, failed ? 'warning' : 'success');
        }
        generatingBefore = generating;
        if (closed || !generating.size) return;
        pollTimer = window.setTimeout(() => { void load(); }, POLL_MS);
    }

    async function batch(mode) {
        const s = summarize(rows || []);
        const plans = {
            missing: { count: s.missing, title: '一键生成知识树？', message: `将为 ${s.missing} 个已绑定文档但还没有知识树的课次生成知识树。AI 会排队逐个处理，可以关闭浮窗，完成后课次卡片自动显示进度。`, danger: false },
            stale: { count: s.stale, title: '重新生成文档已更新的知识树？', message: `将重新生成 ${s.stale} 个文档已更新课次的知识树，这些课次的完成进度会归零，需要按新知识树重新勾选。`, danger: true },
            all: { count: s.bound, title: '全部重新生成？', message: `将重新生成全部 ${s.bound} 个已绑定文档课次的知识树${s.withProgress ? `，其中 ${s.withProgress} 个课次已有的完成进度会全部清零` : ''}。此操作不可撤销。`, danger: true },
        };
        const plan = plans[mode];
        if (!plan?.count) return;
        const ok = await lqConfirm({ title: plan.title, message: plan.message, confirmLabel: '开始生成', danger: plan.danger });
        if (!ok || closed) return;
        busy = true;
        render();
        try {
            const result = await apiFetch(`${base}/knowledge-trees/generate`, { method: 'POST', body: { mode }, silent: true });
            const skipped = (result.skipped || []).filter(item => item.reason !== '未绑定学习材料');
            const queued = result.queued?.length || 0;
            showToast(`已加入生成队列 ${queued} 个课次${skipped.length ? `，跳过 ${skipped.length} 个（${skipped[0].reason}等）` : ''}。`, queued ? 'success' : 'warning');
            busy = false;
            if (!closed) applyRows(result.sessions || rows);
        } catch (error) {
            showToast(error.message || '批量生成失败，请稍后重试。', 'error');
        } finally {
            busy = false;
            if (!closed) render();
        }
    }

    async function generateOne(sessionId) {
        const row = rows?.find(item => item.session_id === sessionId);
        if (!row) return;
        if (row.has_tree) {
            const hasProgress = Number(row.completed_count) > 0;
            const ok = await lqConfirm({
                title: '重新生成这一课的知识树？',
                message: hasProgress ? `该课次已完成的 ${row.progress}% 进度会清零，生成后需重新勾选。` : '将用 AI 重新生成并替换这一课的知识树。',
                confirmLabel: '重新生成', danger: hasProgress,
            });
            if (!ok || closed) return;
        }
        try {
            await apiFetch(`${base}/sessions/${sessionId}/knowledge-tree/generate`, { method: 'POST', silent: true });
            showToast('已开始生成，可继续操作其他课次。', 'success');
        } catch (error) {
            showToast(error.message || '无法开始生成。', 'error');
        }
        if (!closed) await load();
    }

    function openTree(sessionId, button) {
        const row = rows?.find(item => item.session_id === sessionId);
        const info = row ? sessionLabel(row) : { title: '' };
        const session = info.session || { id: sessionId, title: row?.title || '', detail_title: info.title };
        childOpen = true;
        openKnowledgeTree({
            classOfferingId,
            session: { ...session, id: sessionId },
            canEdit: true,
            trigger: button,
            onClose: () => {
                childOpen = false;
                if (!closed && listDirty) {
                    renderList();
                    list.querySelector(`[data-ktm-open="${sessionId}"]`)?.focus();
                }
            },
            onChange: (id, state) => {
                onProgress?.(id, state);
                if (!rows?.some(item => item.session_id === id)) return;
                rows = rows.map(item => (item.session_id === id ? {
                    ...item, status: state.status, ai_gen_status: state.ai_gen_status, has_tree: state.has_tree,
                    progress: state.progress, lifecycle: state.lifecycle, leaf_count: state.leaf_count,
                    completed_count: state.completed_count, is_stale: state.is_stale, error: state.error,
                } : item));
                lastSignature = JSON.stringify(rows);
                render();
                schedulePoll();
            },
        });
    }

    shell.addEventListener('click', event => {
        const button = event.target.closest('button');
        if (!button || button.disabled || !shell.contains(button)) return;
        if (button.dataset.ktmBatch) void batch(button.dataset.ktmBatch);
        else if (button.hasAttribute('data-ktm-refresh')) { lastSignature = ''; void load(); }
        else if (button.dataset.ktmFilter) { filter = button.dataset.ktmFilter; render(); }
        else if (button.dataset.ktmOpen) openTree(Number(button.dataset.ktmOpen), button);
        else if (button.dataset.ktmGenerate) void generateOne(Number(button.dataset.ktmGenerate));
        else if (button.dataset.ktmSession) {
            // Navigate only after this dialog has fully closed and left the layer stack.
            pendingSession = Number(button.dataset.ktmSession);
            root.querySelector('[data-lq-dialog-close]')?.click();
        }
    });

    let pendingSession = 0;
    const cleanup = () => {
        closed = true;
        window.clearTimeout(pollTimer);
        if (pendingSession) {
            const sessionId = pendingSession;
            pendingSession = 0;
            window.setTimeout(() => onOpenSession?.(sessionId), 0);
        }
    };
    openDialog(root, { trigger, onClose: cleanup, onDestroy: cleanup });
    render();
    void load();
    return { refresh: load };
}
