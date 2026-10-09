/** Shared teacher-only 「教务同步」 menu: 立即同步 + 申请列表. No schedule facts are persisted in the browser. */
import { createComponent } from './lq/components.js';
import { bindMenu, createMenu } from './lq/menus.js';
import { createDialog, openDialog } from './lq/dialogs.js';
import { createChangeComparison } from './course_schedule_changes.js';

const activeRequests = new Map();
const validTerms = ['1', '2', '3'];
const ZF_ENTRY_FALLBACK = 'https://jwxt.gxufl.com/tkgl/ttksq_cxTtksqIndex.html?information=1&doType=details&gnmkdm=N2122&layout=default';
const STATUS = {
    pending: { label: '审核中', tone: 'warning', action: '在教务查看进度' },
    returned: { label: '已退回', tone: 'danger', action: '去教务修改' },
    draft: { label: '草稿 · 未提交', tone: 'neutral', action: '去教务提交' },
    approved: { label: '已通过', tone: 'success', action: '在教务查看' },
    rejected: { label: '未通过', tone: 'danger', action: '在教务查看' },
};
const DAYS = ['一', '二', '三', '四', '五', '六', '日'];

export function normalizeAcademicSyncTerm(value = {}) {
    const year = String(value.year || '').trim();
    const term = String(value.term || '').trim();
    if (!year && !term) return { year: '', term: '' };
    const match = /^(\d{4})-(\d{4})$/.exec(year);
    if (!match || Number(match[2]) !== Number(match[1]) + 1 || !validTerms.includes(term)) {
        throw new Error('请选择有效学年（如 2026-2027）及第一、第二或夏季学期。');
    }
    return { year, term };
}

export function syncAcademicSchedule(term, fetcher = fetch) {
    const target = normalizeAcademicSyncTerm(term);
    const key = `${target.year}|${target.term}`;
    if (activeRequests.has(key)) return activeRequests.get(key);
    const task = (async () => {
        const response = await fetcher('/api/manage/academic/course-schedule/academic-sync', {
            method: 'POST', credentials: 'same-origin', cache: 'no-store',
            headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
            body: JSON.stringify(target),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || data.status !== 'success' || !data.overview) {
            const error = new Error(data.detail || data.message || '教务同步未完成，仍保留上次有效课表。');
            error.status = data.status || 'failed';
            throw error;
        }
        return data;
    })().finally(() => { if (activeRequests.get(key) === task) activeRequests.delete(key); });
    activeRequests.set(key, task);
    return task;
}

/** A selected, valid term is synced as-is; anything else lets the server discover the current term. */
export function syncTargetFor(term = {}) {
    try { return normalizeAcademicSyncTerm(term); } catch { return { year: '', term: '' }; }
}

const warningCount = overview => {
    const warnings = overview?.warnings || overview?.sync_state?.warnings || [];
    return Array.isArray(warnings) ? warnings.length : 0;
};

/** One short sentence; the full warning list stays with the timetable it describes. */
export function syncSummary(data) {
    const count = warningCount(data?.overview);
    return `${data?.message || '教务同步完成。'}${count ? `（${count} 条提示见课表下方）` : ''}`;
}

function ensureStyle() {
    if (document.getElementById('academic-schedule-sync-style')) return;
    const style = document.createElement('style'); style.id = 'academic-schedule-sync-style';
    // Layout and data presentation only; materials, buttons and motion come from LQ.
    style.textContent = `.cs-sync-feedback{font-size:12px;line-height:1.6;overflow-wrap:anywhere;color:var(--lq-material-muted,inherit)}.cs-sync-feedback:empty{display:none}
.ls-course-search{flex-wrap:wrap}.ls-course-search [data-dashboard-search]{flex:1 1 180px}[data-academic-schedule-sync]{white-space:nowrap}.ls-course-search .cs-sync-feedback{flex-basis:100%}
.cs-req-note{margin:0 0 12px;font-size:13px;line-height:1.6;color:var(--lq-material-muted,inherit)}
.cs-req-list{display:grid;gap:10px;margin:0;padding:0;list-style:none}
.cs-req>summary{display:grid;grid-template-columns:auto minmax(0,1fr) auto;align-items:center;gap:4px 10px;cursor:pointer;list-style:none}
.cs-req>summary::-webkit-details-marker{display:none}
.cs-req__title{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.cs-req__title small{margin-left:6px;font-weight:500;color:var(--lq-material-muted,inherit)}
.cs-req__meta{font-size:12px;color:var(--lq-material-muted,inherit);white-space:nowrap}
.cs-req__body{display:grid;gap:10px;margin-top:10px}
.cs-req__reason{margin:0;font-size:13px;line-height:1.6;overflow-wrap:anywhere}
.cs-req__actions{display:flex;flex-wrap:wrap;gap:8px}
.cs-req .cse-comparison{max-width:100%;overflow-x:auto}
.cs-req .cse-comparison .lq-table :is(th,td){padding:3px 4px;border:0;white-space:nowrap}
.cs-req .cse-comparison .lq-table :is(caption,thead){position:absolute;width:1px;height:1px;margin:-1px;overflow:hidden;clip-path:inset(50%)}
.cs-req .cse-comparison .lq-chip{font-weight:500}.cs-req .cse-comparison .is-changed .lq-chip{font-weight:800}
.cs-req-empty{margin:0;padding:24px 0;text-align:center;color:var(--lq-material-muted,inherit)}
.cs-req-foot{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:8px;width:100%}
@media (max-width:560px){.cs-req>summary{grid-template-columns:auto minmax(0,1fr)}.cs-req__meta{grid-column:1 / -1}}`;
    document.head.appendChild(style);
}

const slotText = slot => slot ? `第${slot.week}周 周${DAYS[Number(slot.weekday) - 1] || '?'} 第${(slot.sections || []).join('、')}节${slot.room ? ` · ${slot.room}` : ''}` : '';
const safeId = value => String(value || '').replace(/[^A-Za-z0-9_-]/g, '').slice(0, 40) || 'x';
const shortTime = value => String(value || '').replace(/^\d{4}-/, '').replace('T', ' ').slice(0, 11);

function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
}

function statusChip(status) {
    const meta = STATUS[status] || { label: '状态未识别', tone: 'neutral' };
    return createComponent('chip', { label: meta.label, kind: 'status', tone: meta.tone, size: 'sm' });
}

/** One card per 教务 application; the body mirrors the editor's 原/新 change list. */
export function createRequestCard(request, { entryUrl = ZF_ENTRY_FALLBACK, editorUrl = '' } = {}) {
    const card = element('details', 'lq-surface cs-req');
    card.dataset.lqComponent = 'surface'; card.dataset.lqPadding = 'sm'; card.dataset.status = request.status || '';
    const summary = element('summary');
    const title = element('strong', 'cs-req__title', request.course_name || '课程');
    title.append(element('small', '', request.class_label || request.teaching_class_name || ''));
    const kind = request.kind === 'cancel' ? '停课' : '调课';
    const details = request.details || [];
    summary.append(statusChip(request.status), title,
        element('span', 'cs-req__meta', [kind, details.length > 1 ? `${details.length} 项` : '', shortTime(request.applied_at) || '尚未提交'].filter(Boolean).join(' · ')));
    const body = element('div', 'cs-req__body');
    body.append(element('p', 'cs-req__reason', `原因：${request.reason || '未填写'}${request.serial ? `　流水号 ${request.serial}` : ''}`));
    details.forEach((detail, index) => {
        if (request.kind === 'cancel' || !detail.proposed) body.append(element('p', 'cs-req__reason', `停课：${slotText(detail.original)}`));
        else body.append(createChangeComparison({ id: `${safeId(request.request_id)}-${index}`, course_name: request.course_name || '课程',
            original: detail.original, proposed: detail.proposed }));
    });
    const actions = element('div', 'cs-req__actions');
    actions.append(createComponent('button', { label: (STATUS[request.status] || STATUS.approved).action, icon: 'external-link', variant: 'glass', size: 'sm',
        href: entryUrl, attrs: { target: '_blank', 'data-cs-req-jump': '' } }));
    if (request.status === 'draft' && editorUrl) {
        actions.append(createComponent('button', { label: '编辑草稿', icon: 'pencil', variant: 'glass', size: 'sm', href: editorUrl, attrs: { 'data-cs-req-edit': '' } }));
    }
    body.append(actions);
    card.append(summary, body);
    return card;
}

function requestListBody(overview, options) {
    const fragment = document.createDocumentFragment();
    const requests = Array.isArray(overview?.academic_requests) ? overview.academic_requests : null;
    const synced = overview?.sync_state?.last_success_at;
    fragment.append(element('p', 'cs-req-note', `${synced ? `数据来自 ${shortTime(synced)} 的教务同步。` : ''}这里只读展示；提交、修改与审批请到教务系统操作。`));
    if (!requests) { fragment.append(element('p', 'cs-req-empty', '还没有教务申请数据，请先「立即同步」。')); return fragment; }
    if (!requests.length) { fragment.append(element('p', 'cs-req-empty', '本学期没有调停课申请。')); return fragment; }
    const list = element('ul', 'cs-req-list');
    requests.forEach((request, index) => {
        const item = element('li');
        const card = createRequestCard(request, { entryUrl: overview.academic_entry_url || ZF_ENTRY_FALLBACK, editorUrl: options.editorUrl });
        // In-progress applications (sorted first by the server) open by default.
        card.open = index < 3 && ['pending', 'returned', 'draft'].includes(request.status);
        item.append(card);
        list.append(item);
    });
    fragment.append(list);
    return fragment;
}

export function createAcademicScheduleSync({ button, getTerm, getContext, getOverview, getEditorUrl, onStart, onSuccess, onError, onMessage } = {}) {
    if (!button) return null;
    ensureStyle();
    let busy = false;
    let listDialog = null;
    const feedback = element('span', 'cs-sync-feedback'); feedback.setAttribute('role', 'status');
    button.insertAdjacentElement('afterend', feedback);
    const menu = createMenu({ label: '教务同步', items: [
        { id: 'sync', label: '立即同步', icon: 'refresh-cw' },
        { id: 'requests', label: '申请列表', icon: 'list' },
    ] });
    document.body.append(menu);

    async function runSync() {
        if (busy) return;
        const target = syncTargetFor(getTerm?.() || {});
        const context = getContext?.();
        const label = button.querySelector('.app-topbar-action__text strong') || button.querySelector('.lq-btn__label') || button;
        const previous = label.textContent;
        busy = true; button.disabled = true; button.setAttribute('aria-busy', 'true'); label.textContent = '同步中…';
        feedback.textContent = '';
        try {
            onStart?.(target, context);
            const data = await syncAcademicSchedule(target);
            await onSuccess?.(data, { target, context });
            feedback.textContent = syncSummary(data);
            onMessage?.(feedback.textContent, 'success');
            renderList();
        } catch (error) {
            feedback.textContent = `${error.message || '教务同步失败。'} 原有课表已保留。`;
            onError?.(error, { target, context }); onMessage?.(feedback.textContent, 'error');
        } finally { busy = false; button.disabled = false; button.removeAttribute('aria-busy'); label.textContent = previous; }
    }

    function listOptions() {
        return { editorUrl: typeof getEditorUrl === 'function' ? getEditorUrl(getTerm?.() || {}) || '' : '' };
    }

    function renderList() {
        const body = listDialog?.querySelector('.lq-dialog__body');
        if (body) body.replaceChildren(requestListBody(getOverview?.(), listOptions()));
    }

    function openList() {
        const overview = getOverview?.();
        const foot = element('div', 'cs-req-foot');
        const count = Array.isArray(overview?.academic_requests) ? overview.academic_requests.length : 0;
        const sync = createComponent('button', { label: '立即同步', icon: 'refresh-cw', variant: 'glass', size: 'sm', attrs: { 'data-cs-req-sync': '' } });
        sync.addEventListener('click', () => { void runSync(); });
        foot.append(element('span', 'cs-req-note', count ? `共 ${count} 个申请` : ''), sync);
        listDialog = createDialog({ title: '教务调停课申请', size: 'lg', body: requestListBody(overview, listOptions()), footer: foot,
            attrs: { 'data-cs-request-list': '' } });
        document.body.append(listDialog);
        openDialog(listDialog, { trigger: button, onClose: () => { listDialog = null; } });
    }

    const binding = bindMenu(button, menu, { onAction: id => (id === 'sync' ? runSync() : openList()) });
    return {
        isBusy: () => busy, sync: runSync, openRequests: openList,
        destroy() { binding.destroy(); menu.remove(); feedback.remove(); },
    };
}
