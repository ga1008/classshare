/**
 * Generic approval workflow UI (通用审批流前端模板).
 *
 *   mountLauncher(root, opts)  – applicant side: a "?" trigger that opens a small
 *                                popover (hover 1 s / click) to raise a request.
 *   mountPanel(root, opts)     – reviewer side: a right-hand drawer listing requests
 *                                plus a large detail modal with approve / reject.
 *
 * New request types only need a detail renderer in DETAIL_RENDERERS (optional) and
 * a decision-form builder in DECISION_FORMS (optional). Everything else — API calls,
 * state badges, permissions, timeline — is shared.
 */
import { showToast, escapeHtml } from './ui.js';
import { adoptDomainControl } from './lq/domain-controls.js';
import { getLayerSystem } from './lq/layer.js';

const API = '/api/approvals';
const STATUS_TONE = { pending: 'warning', approved: 'success', rejected: 'danger', cancelled: 'muted', expired: 'muted' };

// Called only at this module's render boundaries. Native values and the existing
// delegated data-apr actions keep their owner; LQ owns controls and layer motion.
function adoptControls(container) {
    for (const node of container.querySelectorAll('button,input,textarea,select')) {
        const choice = node.matches('.apr-item');
        adoptDomainControl(node, { ...(choice ? { kind: 'choice' } : {}),
            variant: node.classList.contains('btn-primary') ? 'prominent' : node.matches('.apr-drawer-close,.modal-close') ? 'ghost' : 'glass' });
        if (choice) node.dataset.lqShape = 'surface';
        if (node.tagName === 'BUTTON' && node.querySelector('svg')) node.classList.add('lq-btn--icon');
    }
    for (const field of container.querySelectorAll('.form-group')) field.classList.add('lq-field');
    for (const label of container.querySelectorAll('.form-label')) label.classList.add('lq-field__label');
}

function fmtDate(value) {
    if (!value) return '-';
    try {
        return new Date(String(value).replace(' ', 'T')).toLocaleString('zh-CN', {
            year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
        });
    } catch {
        return String(value);
    }
}

function toDatetimeLocalValue(value) {
    if (!value) return '';
    const date = value instanceof Date ? value : new Date(String(value).replace(' ', 'T'));
    if (Number.isNaN(date.getTime())) return '';
    const pad = (n) => String(n).padStart(2, '0');
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

async function api(path, options = {}) {
    const response = await fetch(`${API}${path}`, {
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        credentials: 'same-origin',
        ...options,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
        const detail = data.detail;
        throw new Error(typeof detail === 'string' ? detail : (detail?.message || data.message || '请求失败'));
    }
    return data;
}

function statusBadge(item) {
    return `<span class="apr-badge apr-badge--${STATUS_TONE[item.status] || 'muted'}">${escapeHtml(item.status_label || item.status)}</span>`;
}

/* ------------------------------------------------------------------ detail renderers */
const DETAIL_RENDERERS = {
    submission_withdraw(item) {
        const d = item.detail || {};
        const rows = [
            ['学生', d.student_name || item.applicant_name],
            ['作业', d.assignment_title],
            ['当前状态', d.already_returned ? '已开放重交' : (d.current_status === 'graded' ? `已批改，得分 ${d.current_score ?? '-'}` : d.current_status)],
            ['提交时间', fmtDate(d.submitted_at)],
            ['作业截止', d.assignment_effective_deadline ? fmtDate(d.assignment_effective_deadline) : '未设置'],
            ['默认重交截止', d.recommended_resubmission_due_at ? fmtDate(d.recommended_resubmission_due_at) : '-'],
        ];
        const table = rows.map(([k, v]) => `<div class="apr-kv"><span>${escapeHtml(k)}</span><strong>${escapeHtml(String(v ?? '-'))}</strong></div>`).join('');
        const frame = d.review_url
            ? `<details class="apr-review" open><summary data-lq-component="disclosure" class="lq-disclosure-trigger">学生答题与批改详情</summary>
                 <iframe class="apr-review-frame" src="${escapeHtml(d.review_url)}" title="答题与批改详情" loading="lazy"></iframe>
               </details>`
            : '';
        return `<div class="apr-kv-grid">${table}</div>${frame}`;
    },
};

function defaultDetail(item) {
    const entries = Object.entries(item.detail || {}).filter(([, v]) => v !== null && typeof v !== 'object');
    if (!entries.length) return '';
    return `<div class="apr-kv-grid">${entries.map(([k, v]) => `<div class="apr-kv"><span>${escapeHtml(k)}</span><strong>${escapeHtml(String(v))}</strong></div>`).join('')}</div>`;
}

/* ------------------------------------------------------------------ decision forms */
const DECISION_FORMS = {
    submission_withdraw(item) {
        const recommended = item.detail?.recommended_resubmission_due_at || '';
        return {
            html: `
                <div class="apr-form-grid">
                    <label class="form-group">
                        <span class="form-label">重交截止时间</span>
                        <input type="datetime-local" class="form-control" data-apr-field="resubmission_due_at" value="${escapeHtml(toDatetimeLocalValue(recommended))}">
                        <small class="text-muted">留空则按作业截止时间；作业已截止则从现在起 24 小时。两者取更晚的一个。</small>
                    </label>
                    <label class="form-group">
                        <span class="form-label">或延后（分钟）</span>
                        <input type="number" min="1" step="1" class="form-control" data-apr-field="extension_minutes" placeholder="例如 120">
                    </label>
                </div>`,
            collect(form) {
                const payload = {};
                const due = form.querySelector('[data-apr-field="resubmission_due_at"]')?.value?.trim();
                const minutes = form.querySelector('[data-apr-field="extension_minutes"]')?.value?.trim();
                if (due) payload.resubmission_due_at = due;
                if (minutes) payload.extension_minutes = Number(minutes);
                return payload;
            },
        };
    },
};

/* ------------------------------------------------------------------ launcher (applicant) */
export function mountLauncher(root, options) {
    const {
        requestType, subjectId, currentRequest = null, hoverDelay = 1000,
        title = '分数有异议？', text = '如果你认为分数不合理，可以申请撤回本次提交并重新作答。教师审批通过后会开放重交窗口。',
        actionLabel = '申请撤回重做', placeholder = '请说明申请理由（必填），例如：截图未上传完整、作答内容被误判……',
        onSubmitted = null, disabledReason = '', beforeClose = null,
    } = options;
    let request = currentRequest;
    let timer = null;
    let open = false, busy = false, destroyed = false, editing = false, draft = '';
    let handle = null;
    const layer = getLayerSystem(root.ownerDocument);
    const abort = new AbortController();
    const listen = (node, event, callback) => node.addEventListener(event, callback, { signal: abort.signal });

    root.classList.add('apr-launcher');
    root.innerHTML = `
        <button type="button" class="apr-trigger" aria-haspopup="dialog" aria-expanded="false" aria-label="${escapeHtml(title)}">
            <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"></circle><path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.7.4-1 .9-1 1.7"></path><path d="M12 17h.01"></path></svg>
        </button>
        <div class="apr-popover" role="dialog" aria-label="${escapeHtml(title)}" hidden></div>`;
    const trigger = root.querySelector('.apr-trigger');
    const popover = root.querySelector('.apr-popover');
    popover.classList.add('lq-popover', 'lq-glass');
    popover.dataset.lqComponent = 'popover';
    popover.dataset.lqMaterial = 'raised';
    popover.remove();
    adoptControls(root);
    const captureDraft = () => { const input = popover.querySelector('textarea'); if (input) draft = input.value; };
    const reposition = () => { if (handle && open) handle.update({}); };

    function render() {
        captureDraft(); editing = false;
        let body = '';
        if (request && request.status === 'pending') {
            body = `<p class="apr-pop-state">${statusBadge(request)} 已于 ${escapeHtml(fmtDate(request.created_at))} 提交申请，等待教师处理。</p>
                    <p class="apr-pop-reason">${escapeHtml(request.reason || '')}</p>
                    <div class="apr-pop-actions"><button type="button" class="btn btn-outline btn-sm" data-apr-cancel>撤销申请</button></div>`;
        } else if (request && request.status === 'rejected') {
            body = `<p class="apr-pop-state">${statusBadge(request)} 教师意见：${escapeHtml(request.decision_note || '未填写')}</p>
                    <div class="apr-pop-actions"><button type="button" class="btn btn-primary btn-sm" data-apr-start>再次申请</button></div>`;
        } else if (request && request.status === 'approved') {
            const due = request.decision_payload?.resubmission_due_at;
            body = `<p class="apr-pop-state">${statusBadge(request)} 教师已同意撤回${due ? `，请在 ${escapeHtml(fmtDate(due))} 前重新提交` : ''}。</p>
                    <div class="apr-pop-actions"><button type="button" class="btn btn-primary btn-sm" data-apr-reload>刷新页面</button></div>`;
        } else if (disabledReason) {
            body = `<p class="apr-pop-text">${escapeHtml(disabledReason)}</p>`;
        } else {
            body = `<p class="apr-pop-text">${escapeHtml(text)}</p>
                    <div class="apr-pop-actions"><button type="button" class="btn btn-primary btn-sm" data-apr-start>${escapeHtml(actionLabel)}</button></div>`;
        }
        popover.innerHTML = `<div class="apr-pop-title">${escapeHtml(title)}</div>${body}`;
        adoptControls(popover); reposition();
    }

    function renderForm() {
        cancelHide(); editing = true;
        popover.innerHTML = `
            <div class="apr-pop-title">${escapeHtml(actionLabel)}</div>
            <textarea class="form-control apr-pop-textarea" rows="4" maxlength="1000" placeholder="${escapeHtml(placeholder)}"></textarea>
            <div class="apr-pop-actions">
                <button type="button" class="btn btn-outline btn-sm" data-apr-back>返回</button>
                <button type="button" class="btn btn-primary btn-sm" data-apr-submit>提交申请</button>
            </div>`;
        adoptControls(popover);
        popover.querySelector('textarea').value = draft;
        reposition(); popover.querySelector('textarea')?.focus();
    }

    function show() {
        if (destroyed || open) return;
        cancelHide();
        if (editing) renderForm(); else render();
        open = true;
        handle = layer.open(popover, { type: 'popover', modality: 'non-modal', trigger, anchor: trigger, owner: root,
            placement: 'bottom-start', initialFocus: false,
            beforeClose: reason => { captureDraft(); return busy ? false : beforeClose?.(reason); },
            onClose: () => { open = false; trigger.setAttribute('aria-expanded', 'false'); },
            onDestroy: () => { open = false; trigger.setAttribute('aria-expanded', 'false'); cancelTimer(); cancelHide(); },
        });
        trigger.setAttribute('aria-expanded', 'true');
    }
    function hide(reason = 'programmatic') {
        cancelTimer(); cancelHide();
        return open ? layer.close(handle, reason) : Promise.resolve(true);
    }
    function cancelTimer() { if (timer) { clearTimeout(timer); timer = null; } }

    listen(trigger, 'mouseenter', () => { cancelTimer(); timer = setTimeout(show, hoverDelay); });
    listen(trigger, 'mouseleave', cancelTimer);
    listen(trigger, 'click', () => { cancelTimer(); open ? hide() : show(); });
    let leaveTimer = null;
    const scheduleHide = () => {
        if (leaveTimer) clearTimeout(leaveTimer);
        leaveTimer = setTimeout(() => { if (open && !popover.querySelector('textarea')) hide(); }, 250);
    };
    const cancelHide = () => { if (leaveTimer) { clearTimeout(leaveTimer); leaveTimer = null; } };
    listen(root, 'mouseleave', () => { cancelTimer(); scheduleHide(); });
    listen(popover, 'mouseenter', cancelHide);
    listen(popover, 'mouseleave', scheduleHide);
    listen(trigger, 'mouseenter', cancelHide);
    listen(popover, 'input', event => { if (event.target.matches('textarea')) draft = event.target.value; });

    listen(popover, 'click', async (event) => {
        const target = event.target.closest('button');
        if (!target || busy || destroyed) return;
        if (target.hasAttribute('data-apr-start')) { renderForm(); return; }
        if (target.hasAttribute('data-apr-back')) { render(); return; }
        if (target.hasAttribute('data-apr-reload')) { window.location.reload(); return; }
        if (target.hasAttribute('data-apr-cancel')) {
            busy = true; target.disabled = true;
            try {
                const data = await api(`/${request.id}/cancel`, { method: 'POST', body: JSON.stringify({}) });
                if (destroyed) return;
                request = data.request;
                showToast('已撤销申请', 'info');
                render();
            } catch (error) {
                if (!destroyed) { showToast(error.message, 'error'); target.disabled = false; }
            } finally { busy = false; }
            return;
        }
        if (target.hasAttribute('data-apr-submit')) {
            const reason = popover.querySelector('textarea')?.value.trim();
            if (!reason) { showToast('请填写申请理由', 'warning'); return; }
            busy = true; target.disabled = true;
            target.textContent = '提交中…';
            try {
                const data = await api('', { method: 'POST', body: JSON.stringify({ request_type: requestType, subject_id: subjectId, reason }) });
                if (destroyed) return;
                request = data.request; draft = '';
                popover.querySelector('textarea').value = '';
                showToast('申请已提交，教师会收到消息和邮件提醒', 'success');
                render();
                if (typeof onSubmitted === 'function') onSubmitted(request);
            } catch (error) {
                if (!destroyed) { showToast(error.message, 'error'); target.disabled = false; target.textContent = '提交申请'; }
            } finally { busy = false; }
        }
    });

    return { getRequest: () => request, refresh: () => { if (!busy && !destroyed) render(); }, open: show, close: hide,
        destroy() { destroyed = true; cancelTimer(); cancelHide(); abort.abort(); handle?.destroy(); popover.remove(); } };
}

/* ------------------------------------------------------------------ panel (reviewer) */
export function mountPanel(root, options) {
    const {
        scope = 'incoming', assignmentId = '', requestType = '', autoOpenId = null,
        title = '申请办理', onDecided = null, startOpen = false, limit = 50, beforeClose = null,
    } = options;
    let items = [];
    let pendingCount = 0;
    let activeId = null, busy = false, destroyed = false, detailVersion = 0, listVersion = 0;
    let drawerLayer = null, modalLayer = null;
    const drafts = new Map();
    const layer = getLayerSystem(root.ownerDocument);
    const abort = new AbortController();
    const listen = (node, event, callback) => node.addEventListener(event, callback, { signal: abort.signal });
    const identity = Symbol.for('lanshare.approval.drawer-id');
    const sequence = root.ownerDocument[identity] = (root.ownerDocument[identity] || 0) + 1;
    const drawerId = sequence === 1 ? 'apr-drawer' : `apr-drawer-${sequence}`;

    root.classList.add('apr-drawer-root');
    root.innerHTML = `
        <button type="button" class="apr-drawer-toggle" aria-expanded="false" aria-controls="${drawerId}" title="${escapeHtml(title)}">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 11l3 3L22 4"></path><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"></path></svg>
            <span class="apr-drawer-toggle-label">${escapeHtml(title)}</span>
            <span class="apr-drawer-count" hidden>0</span>
        </button>
        <aside class="apr-drawer lq-glass" data-lq-component="drawer" data-lq-material="raised" id="${drawerId}" aria-label="${escapeHtml(title)}" hidden>
            <div class="apr-drawer-header">
                <div><strong>${escapeHtml(title)}</strong><small class="apr-drawer-sub">待办优先，最新在前</small></div>
                <div class="apr-drawer-tools">
                    <button type="button" class="btn btn-outline btn-sm" data-apr-refresh title="刷新">刷新</button>
                    <button type="button" class="apr-drawer-close" data-apr-close aria-label="收起">&times;</button>
                </div>
            </div>
            <div class="apr-list" data-apr-list><div class="apr-empty">正在加载…</div></div>
        </aside>
        <div class="lq-dialog-root apr-modal" data-lq-dialog="modal" data-lq-component="dialog" data-apr-modal hidden>
            <div class="lq-scrim" aria-hidden="true"></div>
            <div class="modal-dialog modal-dialog-wide lq-dialog__surface lq-modal lq-modal--xl lq-glass" role="dialog" aria-label="申请详情">
                <div class="apr-modal-content">
                    <div class="modal-header">
                        <h3 class="modal-title" data-apr-modal-title>申请详情</h3>
                        <button class="modal-close" type="button" data-apr-modal-close aria-label="关闭">&times;</button>
                    </div>
                    <div class="modal-body apr-modal-body" data-apr-modal-body></div>
                </div>
            </div>
        </div>`;
    const toggle = root.querySelector('.apr-drawer-toggle');
    const drawer = root.querySelector('.apr-drawer');
    const countEl = root.querySelector('.apr-drawer-count');
    const listEl = root.querySelector('[data-apr-list]');
    const modal = root.querySelector('[data-apr-modal]');
    const modalTitle = root.querySelector('[data-apr-modal-title]');
    const modalBody = root.querySelector('[data-apr-modal-body]');

    const modalSurface = modal.querySelector('.lq-dialog__surface');
    adoptControls(root);
    // The layer portal keeps all three surfaces outside clipping ancestors and
    // establishes their parent relationship; only it owns Escape/scroll/focus.
    const drawerClose = root.querySelector('[data-apr-close]');
    const refresh = root.querySelector('[data-apr-refresh]');
    const modalClose = root.querySelector('[data-apr-modal-close]');
    drawer.remove(); modal.remove();
    const drawerClosed = () => { toggle.setAttribute('aria-expanded', 'false'); root.classList.remove('is-open'); };
    function setDrawer(openState) {
        if (destroyed) return Promise.resolve(false);
        if (!openState) return drawerLayer ? layer.close(drawerLayer, 'button') : Promise.resolve(true);
        drawerLayer = layer.open(drawer, { type: 'drawer', modality: 'non-modal', owner: root, trigger: toggle,
            closeOnOutside: false, initialFocus: false, onClose: drawerClosed, onDestroy: drawerClosed });
        toggle.setAttribute('aria-expanded', 'true'); root.classList.add('is-open');
        return Promise.resolve(true);
    }

    function captureDraft() {
        if (activeId === null || !modalBody.querySelector('[data-apr-decision]')) return;
        drafts.set(activeId, Object.fromEntries([...modalBody.querySelectorAll('[data-apr-field]')].map(node => [node.dataset.aprField, node.value])));
    }
    function modalClosed() {
        detailVersion++; modalBody.replaceChildren(); modalBody._decisionForm = null; activeId = null;
    }

    function renderList() {
        countEl.hidden = pendingCount <= 0;
        countEl.textContent = String(pendingCount);
        if (!items.length) {
            listEl.innerHTML = '<div class="apr-empty">暂无申请</div>';
            return;
        }
        listEl.innerHTML = items.map((item) => `
            <button type="button" class="apr-item${item.status === 'pending' ? ' is-pending' : ''}" data-apr-open="${item.id}">
                <div class="apr-item-top">
                    <span class="apr-item-type">${escapeHtml(item.request_type_label || '')}</span>
                    ${statusBadge(item)}
                </div>
                <div class="apr-item-title">${escapeHtml(item.applicant_name || '申请人')}</div>
                <div class="apr-item-reason">${escapeHtml(item.reason || '')}</div>
                <div class="apr-item-meta">${escapeHtml(fmtDate(item.created_at))}</div>
            </button>`).join('');
        adoptControls(listEl);
    }

    async function load() {
        if (destroyed) return;
        const version = ++listVersion;
        const params = new URLSearchParams({ scope, limit: String(limit) });
        if (assignmentId) params.set('assignment_id', String(assignmentId));
        if (requestType) params.set('request_type', requestType);
        try {
            const data = await api(`?${params.toString()}`);
            if (destroyed || version !== listVersion) return;
            items = data.items || [];
            pendingCount = Number(data.pending_count || 0);
        } catch (error) {
            if (destroyed || version !== listVersion) return;
            items = [];
            pendingCount = 0;
            listEl.innerHTML = `<div class="apr-empty">${escapeHtml(error.message)}</div>`;
            return;
        }
        renderList();
    }

    function closeModal(reason = 'button') {
        return modalLayer ? layer.close(modalLayer, reason) : Promise.resolve(true);
    }

    function renderTimeline(item) {
        const labels = { created: '发起申请', approved: '通过', rejected: '拒绝', cancelled: '撤销', auto_cancelled: '自动结束', expired: '过期', reminded: '提醒审批人' };
        return `<ol class="apr-timeline">${(item.events || []).map((e) => `
            <li><span class="apr-timeline-when">${escapeHtml(fmtDate(e.created_at))}</span>
                <strong>${escapeHtml(labels[e.event_type] || e.event_type)}</strong>
                ${e.actor_name ? `<span class="text-muted">· ${escapeHtml(e.actor_name)}</span>` : ''}
                ${e.note ? `<div class="apr-timeline-note">${escapeHtml(e.note)}</div>` : ''}</li>`).join('')}</ol>`;
    }

    function renderModal(item) {
        const renderer = DETAIL_RENDERERS[item.request_type] || defaultDetail;
        const decisionForm = item.can_decide && DECISION_FORMS[item.request_type] ? DECISION_FORMS[item.request_type](item) : null;
        modalTitle.textContent = item.title || '申请详情';
        modalBody.innerHTML = `
            <div class="apr-modal-head">
                ${statusBadge(item)}
                <span class="text-muted">${escapeHtml(item.request_type_label || '')} · ${escapeHtml(item.applicant_name || '')} · ${escapeHtml(fmtDate(item.created_at))}</span>
            </div>
            <section class="apr-section">
                <h4>申请理由</h4>
                <p class="apr-reason">${escapeHtml(item.reason || '')}</p>
            </section>
            <section class="apr-section">
                <h4>相关信息</h4>
                ${renderer(item)}
            </section>
            ${item.status !== 'pending' ? `
            <section class="apr-section">
                <h4>处理结果</h4>
                <p>${statusBadge(item)} ${escapeHtml(item.decided_by_name || '')} ${escapeHtml(fmtDate(item.decided_at))}
                ${item.decision_note ? `<br>意见：${escapeHtml(item.decision_note)}` : ''}
                ${item.decision_payload?.resubmission_due_at ? `<br>重交截止：${escapeHtml(fmtDate(item.decision_payload.resubmission_due_at))}` : ''}</p>
            </section>` : ''}
            ${item.can_decide ? `
            <section class="apr-section apr-decision" data-apr-decision>
                <h4>处理</h4>
                ${decisionForm ? decisionForm.html : ''}
                <label class="form-group">
                    <span class="form-label">审批意见 <small class="text-muted">（拒绝时必填）</small></span>
                    <textarea class="form-control" rows="3" maxlength="1000" data-apr-field="note" placeholder="给学生的说明"></textarea>
                </label>
                <div class="apr-decision-actions">
                    <button type="button" class="btn btn-outline" data-apr-decide="reject">${escapeHtml(item.reject_label || '拒绝')}</button>
                    <button type="button" class="btn btn-primary" data-apr-decide="approve">${escapeHtml(item.approve_label || '同意')}</button>
                </div>
            </section>` : ''}
            <section class="apr-section">
                <h4>流转记录</h4>
                ${renderTimeline(item)}
            </section>`;
        modalBody._decisionForm = decisionForm;
        adoptControls(modalBody);
        const draft = item.can_decide ? drafts.get(Number(item.id)) : null;
        if (!item.can_decide) drafts.delete(Number(item.id));
        if (draft) for (const node of modalBody.querySelectorAll('[data-apr-field]')) {
            if (Object.hasOwn(draft, node.dataset.aprField)) node.value = draft[node.dataset.aprField];
        }
    }

    async function openItem(id) {
        if (destroyed || busy) return false;
        captureDraft();
        const version = ++detailVersion;
        activeId = Number(id);
        modalTitle.textContent = '加载中…';
        modalBody.innerHTML = '<div class="apr-empty">正在加载申请详情…</div>';
        modalBody._decisionForm = null;
        const parent = drawerLayer && !['closed', 'destroyed'].includes(drawerLayer.state) ? drawerLayer : null;
        modalLayer = layer.open(modal, { type: 'modal', surface: modalSurface, owner: root,
            trigger: parent ? drawer : toggle, parentLayer: parent,
            returnFocus: () => listEl.querySelector(`[data-apr-open="${activeId}"]`) || toggle,
            beforeClose: reason => { captureDraft(); return busy ? false : beforeClose?.(reason); },
            onCloseRequested: () => { detailVersion++; },
            onClose: modalClosed, onDestroy: modalClosed,
        });
        try {
            const data = await api(`/${id}`);
            if (destroyed || version !== detailVersion || activeId !== Number(id)) return false;
            renderModal(data.request);
        } catch (error) {
            if (destroyed || version !== detailVersion) return false;
            modalBody.innerHTML = `<div class="apr-empty">${escapeHtml(error.message)}</div>`;
        }
        return true;
    }

    async function decide(decision, button) {
        if (busy || destroyed || activeId === null) return;
        const id = activeId, section = modalBody.querySelector('[data-apr-decision]');
        if (!section) return;
        const note = section.querySelector('[data-apr-field="note"]')?.value.trim() || '';
        if (decision === 'reject' && !note) { showToast('拒绝时请填写审批意见', 'warning'); return; }
        const decisionPayload = modalBody._decisionForm ? modalBody._decisionForm.collect(section) : {};
        const originalLabel = button.textContent;
        captureDraft(); busy = true;
        section.querySelectorAll('button').forEach((b) => { b.disabled = true; });
        button.textContent = '处理中…';
        let decided = false, decidedRequest;
        try {
            const data = await api(`/${id}/${decision}`, {
                method: 'POST', body: JSON.stringify({ note, decision_payload: decisionPayload }),
            });
            decided = true; decidedRequest = data.request; drafts.delete(id);
            if (destroyed) return;
            showToast(decision === 'approve' ? '已通过申请，学生将收到通知' : '已拒绝申请，学生将收到通知', 'success');
            await load();
            // A committed decision is never exposed as a retryable action just
            // because refreshing its detail fails afterwards.
            if (destroyed) return;
            const fresh = await api(`/${id}`);
            if (!destroyed && activeId === id) renderModal(fresh.request);
        } catch (error) {
            if (destroyed) return;
            showToast(error.message, 'error');
            if (!decided) {
                section.querySelectorAll('button').forEach((b) => { b.disabled = false; });
                button.textContent = originalLabel;
            } else {
                modalBody.innerHTML = '<div class="apr-empty">处理已完成，详情刷新失败。请关闭后重新打开查看。</div>';
            }
        } finally {
            busy = false;
            if (decided && !destroyed && typeof onDecided === 'function') onDecided(decidedRequest);
        }
    }

    listen(toggle, 'click', () => { const opening = drawer.hidden; setDrawer(opening); if (opening) load(); });
    listen(drawerClose, 'click', () => setDrawer(false));
    listen(refresh, 'click', load);
    listen(listEl, 'click', (event) => {
        const target = event.target.closest('[data-apr-open]');
        if (target) openItem(target.getAttribute('data-apr-open'));
    });
    listen(modalClose, 'click', () => closeModal());
    listen(modalBody, 'input', captureDraft);
    listen(modalBody, 'click', (event) => {
        const target = event.target.closest('[data-apr-decide]');
        if (target) decide(target.getAttribute('data-apr-decide'), target);
    });

    load();
    if (startOpen) setDrawer(true);
    if (autoOpenId) { setDrawer(true); openItem(autoOpenId); }
    return { reload: load, open: openItem, close: closeModal, setDrawer,
        destroy() {
            destroyed = true; detailVersion++; listVersion++; abort.abort();
            modalLayer?.destroy(); drawerLayer?.destroy(); modal.remove(); drawer.remove(); drafts.clear();
        } };
}

export default { mountLauncher, mountPanel };
