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

const API = '/api/approvals';
const STATUS_TONE = { pending: 'warning', approved: 'success', rejected: 'danger', cancelled: 'muted', expired: 'muted' };

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
            ? `<details class="apr-review" open><summary>学生答题与批改详情</summary>
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
        onSubmitted = null, disabledReason = '',
    } = options;
    let request = currentRequest;
    let timer = null;
    let open = false;

    root.classList.add('apr-launcher');
    root.innerHTML = `
        <button type="button" class="apr-trigger" aria-haspopup="dialog" aria-expanded="false" aria-label="${escapeHtml(title)}">
            <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"></circle><path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.7.4-1 .9-1 1.7"></path><path d="M12 17h.01"></path></svg>
        </button>
        <div class="apr-popover" role="dialog" aria-label="${escapeHtml(title)}" hidden></div>`;
    const trigger = root.querySelector('.apr-trigger');
    const popover = root.querySelector('.apr-popover');
    // Body-level, fixed-position popover: never clipped by card overflow.
    document.body.appendChild(popover);

    function place() {
        const rect = trigger.getBoundingClientRect();
        const width = Math.min(352, window.innerWidth - 24);
        let left = Math.min(Math.max(12, rect.left), window.innerWidth - width - 12);
        popover.style.width = `${width}px`;
        popover.style.left = `${left}px`;
        popover.style.top = '0px';
        const height = popover.offsetHeight || 160;
        const below = rect.bottom + 8;
        const top = below + height > window.innerHeight - 12 ? Math.max(12, rect.top - height - 8) : below;
        popover.style.top = `${top}px`;
    }

    function render() {
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
    }

    function renderForm() {
        cancelHide();
        popover.innerHTML = `
            <div class="apr-pop-title">${escapeHtml(actionLabel)}</div>
            <textarea class="form-control apr-pop-textarea" rows="4" maxlength="1000" placeholder="${escapeHtml(placeholder)}"></textarea>
            <div class="apr-pop-actions">
                <button type="button" class="btn btn-outline btn-sm" data-apr-back>返回</button>
                <button type="button" class="btn btn-primary btn-sm" data-apr-submit>提交申请</button>
            </div>`;
        popover.querySelector('textarea')?.focus();
    }

    function show() {
        if (open) return;
        open = true;
        render();
        popover.hidden = false;
        place();
        trigger.setAttribute('aria-expanded', 'true');
    }
    window.addEventListener('resize', () => { if (open) place(); });
    window.addEventListener('scroll', () => { if (open) place(); }, true);
    function hide() {
        if (!open) return;
        open = false;
        popover.hidden = true;
        trigger.setAttribute('aria-expanded', 'false');
    }
    function cancelTimer() { if (timer) { clearTimeout(timer); timer = null; } }

    trigger.addEventListener('mouseenter', () => { cancelTimer(); timer = setTimeout(show, hoverDelay); });
    trigger.addEventListener('mouseleave', cancelTimer);
    trigger.addEventListener('click', () => { cancelTimer(); open ? hide() : show(); });
    let leaveTimer = null;
    const scheduleHide = () => {
        if (leaveTimer) clearTimeout(leaveTimer);
        leaveTimer = setTimeout(() => { if (open && !popover.querySelector('textarea')) hide(); }, 250);
    };
    const cancelHide = () => { if (leaveTimer) { clearTimeout(leaveTimer); leaveTimer = null; } };
    root.addEventListener('mouseleave', () => { cancelTimer(); scheduleHide(); });
    popover.addEventListener('mouseenter', cancelHide);
    popover.addEventListener('mouseleave', scheduleHide);
    trigger.addEventListener('mouseenter', cancelHide);
    document.addEventListener('click', (event) => {
        // composedPath() still lists the popover when the clicked button was re-rendered away.
        const path = typeof event.composedPath === 'function' ? event.composedPath() : [];
        if (open && !path.includes(root) && !path.includes(popover)) hide();
    });
    document.addEventListener('keydown', (event) => { if (event.key === 'Escape') hide(); });

    popover.addEventListener('click', async (event) => {
        const target = event.target.closest('button');
        if (!target) return;
        if (target.hasAttribute('data-apr-start')) { renderForm(); return; }
        if (target.hasAttribute('data-apr-back')) { render(); return; }
        if (target.hasAttribute('data-apr-reload')) { window.location.reload(); return; }
        if (target.hasAttribute('data-apr-cancel')) {
            target.disabled = true;
            try {
                const data = await api(`/${request.id}/cancel`, { method: 'POST', body: JSON.stringify({}) });
                request = data.request;
                showToast('已撤销申请', 'info');
                render();
            } catch (error) {
                showToast(error.message, 'error');
                target.disabled = false;
            }
            return;
        }
        if (target.hasAttribute('data-apr-submit')) {
            const reason = popover.querySelector('textarea')?.value.trim();
            if (!reason) { showToast('请填写申请理由', 'warning'); return; }
            target.disabled = true;
            target.textContent = '提交中…';
            try {
                const data = await api('', { method: 'POST', body: JSON.stringify({ request_type: requestType, subject_id: subjectId, reason }) });
                request = data.request;
                showToast('申请已提交，教师会收到消息和邮件提醒', 'success');
                render();
                if (typeof onSubmitted === 'function') onSubmitted(request);
            } catch (error) {
                showToast(error.message, 'error');
                target.disabled = false;
                target.textContent = '提交申请';
            }
        }
    });

    return { getRequest: () => request, refresh: render, open: show, close: hide };
}

/* ------------------------------------------------------------------ panel (reviewer) */
export function mountPanel(root, options) {
    const {
        scope = 'incoming', assignmentId = '', requestType = '', autoOpenId = null,
        title = '申请办理', onDecided = null, startOpen = false, limit = 50,
    } = options;
    let items = [];
    let pendingCount = 0;
    let activeId = null;

    root.classList.add('apr-drawer-root');
    root.innerHTML = `
        <button type="button" class="apr-drawer-toggle" aria-expanded="false" aria-controls="apr-drawer" title="${escapeHtml(title)}">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 11l3 3L22 4"></path><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"></path></svg>
            <span class="apr-drawer-toggle-label">${escapeHtml(title)}</span>
            <span class="apr-drawer-count" hidden>0</span>
        </button>
        <aside class="apr-drawer" id="apr-drawer" aria-label="${escapeHtml(title)}" hidden>
            <div class="apr-drawer-header">
                <div><strong>${escapeHtml(title)}</strong><small class="apr-drawer-sub">待办优先，最新在前</small></div>
                <div class="apr-drawer-tools">
                    <button type="button" class="btn btn-outline btn-sm" data-apr-refresh title="刷新">刷新</button>
                    <button type="button" class="apr-drawer-close" data-apr-close aria-label="收起">&times;</button>
                </div>
            </div>
            <div class="apr-list" data-apr-list><div class="apr-empty">正在加载…</div></div>
        </aside>
        <div class="modal-backdrop apr-modal" data-apr-modal>
            <div class="modal-dialog modal-dialog-wide">
                <div class="modal-content">
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

    function setDrawer(openState) {
        drawer.hidden = !openState;
        toggle.setAttribute('aria-expanded', String(openState));
        root.classList.toggle('is-open', openState);
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
    }

    async function load() {
        const params = new URLSearchParams({ scope, limit: String(limit) });
        if (assignmentId) params.set('assignment_id', String(assignmentId));
        if (requestType) params.set('request_type', requestType);
        try {
            const data = await api(`?${params.toString()}`);
            items = data.items || [];
            pendingCount = Number(data.pending_count || 0);
        } catch (error) {
            items = [];
            pendingCount = 0;
            listEl.innerHTML = `<div class="apr-empty">${escapeHtml(error.message)}</div>`;
            return;
        }
        renderList();
    }

    function closeModal() {
        modal.classList.remove('show');
        setTimeout(() => { modal.style.display = 'none'; document.body.style.overflow = ''; }, 250);
        modalBody.innerHTML = '';
        activeId = null;
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
    }

    async function openItem(id) {
        activeId = Number(id);
        modalTitle.textContent = '加载中…';
        modalBody.innerHTML = '<div class="apr-empty">正在加载申请详情…</div>';
        modal.style.display = 'flex';
        requestAnimationFrame(() => modal.classList.add('show'));
        document.body.style.overflow = 'hidden';
        try {
            const data = await api(`/${id}`);
            renderModal(data.request);
        } catch (error) {
            modalBody.innerHTML = `<div class="apr-empty">${escapeHtml(error.message)}</div>`;
        }
    }

    async function decide(decision, button) {
        const section = modalBody.querySelector('[data-apr-decision]');
        const note = section?.querySelector('[data-apr-field="note"]')?.value.trim() || '';
        if (decision === 'reject' && !note) { showToast('拒绝时请填写审批意见', 'warning'); return; }
        const decisionPayload = modalBody._decisionForm ? modalBody._decisionForm.collect(section) : {};
        const originalLabel = button.textContent;
        section.querySelectorAll('button').forEach((b) => { b.disabled = true; });
        button.textContent = '处理中…';
        try {
            const data = await api(`/${activeId}/${decision}`, {
                method: 'POST', body: JSON.stringify({ note, decision_payload: decisionPayload }),
            });
            showToast(decision === 'approve' ? '已通过申请，学生将收到通知' : '已拒绝申请，学生将收到通知', 'success');
            await load();
            const fresh = await api(`/${activeId}`);
            renderModal(fresh.request);
            if (typeof onDecided === 'function') onDecided(data.request);
        } catch (error) {
            showToast(error.message, 'error');
            section.querySelectorAll('button').forEach((b) => { b.disabled = false; });
            button.textContent = originalLabel;
        }
    }

    toggle.addEventListener('click', () => { setDrawer(drawer.hidden); if (!drawer.hidden) load(); });
    root.querySelector('[data-apr-close]').addEventListener('click', () => setDrawer(false));
    root.querySelector('[data-apr-refresh]').addEventListener('click', load);
    listEl.addEventListener('click', (event) => {
        const target = event.target.closest('[data-apr-open]');
        if (target) openItem(target.getAttribute('data-apr-open'));
    });
    root.querySelector('[data-apr-modal-close]').addEventListener('click', closeModal);
    modal.addEventListener('click', (event) => { if (event.target === modal) closeModal(); });
    modalBody.addEventListener('click', (event) => {
        const target = event.target.closest('[data-apr-decide]');
        if (target) decide(target.getAttribute('data-apr-decide'), target);
    });
    document.addEventListener('keydown', (event) => { if (event.key === 'Escape' && activeId !== null) closeModal(); });

    load();
    if (startOpen) setDrawer(true);
    if (autoOpenId) { setDrawer(true); openItem(autoOpenId); }
    return { reload: load, open: openItem, setDrawer };
}

export default { mountLauncher, mountPanel };
