import { apiFetch } from './api.js';
import { escapeHtml, showToast } from './ui.js';

const root = document.querySelector('[data-signature-app]');

const state = {
    signatures: [],
    incoming: [],
    outgoing: [],
    usage: [],
    claimCandidates: [],
    claimPanelOpen: false,
    claimSearch: '',
    busy: false,
};

const requestStatusText = {
    pending: '待审批',
    approved: '已批准',
    partially_used: '部分已使用',
    consumed: '已使用',
    rejected: '已拒绝',
    cancelled: '已撤销',
};

const reviewerStatusText = {
    pending: '待处理',
    approved: '已同意',
    rejected: '已拒绝',
    superseded: '无需处理',
    cancelled: '已结束',
};

async function loadAll() {
    const [mine, incoming, outgoing, usage] = await Promise.all([
        apiFetch('/api/signatures?limit=200', { silent: true }),
        apiFetch('/api/signatures/requests?direction=incoming', { silent: true }),
        apiFetch('/api/signatures/requests?direction=outgoing', { silent: true }),
        apiFetch('/api/signatures/usage-logs?limit=50', { silent: true }),
    ]);
    state.signatures = mine.items || [];
    state.incoming = incoming.items || [];
    state.outgoing = outgoing.items || [];
    state.usage = usage.items || [];
}

function requestSummary(item) {
    if (item.request_kind === 'claim') {
        return '签名认领申请（批准后归属权转移并绑定申请人账号）';
    }
    const points = (item.items || []).map((entry) => entry.function_point_label).filter(Boolean);
    const place = points.length ? points.join('、') : (item.context_label || '未注明位置');
    const material = item.context_label && points.length ? ` · ${item.context_label}` : '';
    return `${place}${material}`;
}

function renderClaimPanel() {
    if (!state.claimPanelOpen) return '';
    const rows = state.claimCandidates.map((item) => {
        const chips = [
            item.identity_label ? `<span class="psig-badge">${escapeHtml(item.identity_label)}</span>` : '',
            item.subject_bound ? '<span class="psig-badge">已绑定他人账号</span>' : '<span class="psig-badge is-claim">未绑定账号</span>',
        ].filter(Boolean).join('');
        const action = item.has_pending_claim
            ? '<span class="psig-badge">审批中</span>'
            : `<button type="button" class="psig-link is-claim" data-psig-claim-apply="${item.id}">${item.can_direct_claim ? '直接认领' : '申请认领'}</button>`;
        return `<article class="psig-item psig-item--claim">
            <div class="psig-item__meta">
                <strong>${escapeHtml(item.subject_name || '未命名签名')}</strong>
                <span>${chips}</span>
                <small>${item.owner_name ? `归属：${escapeHtml(item.owner_name)}` : '按姓名展示，不显示签名图片'}</small>
            </div>
            ${action}
        </article>`;
    }).join('');
    return `<div class="psig-claim-panel" style="display:grid;gap:8px;margin-top:12px;">
        <input type="search" class="form-control" data-psig-claim-search placeholder="输入姓名模糊搜索可认领签名" value="${escapeHtml(state.claimSearch)}">
        ${rows || '<div class="psig-empty">没有可认领的签名。</div>'}
    </div>`;
}

function renderSignatureCard(item) {
    const badges = [];
    if (item.subject_role === 'student' && item.subject_id) badges.push('<span class="psig-badge is-self">本人签名</span>');
    if (item.is_owner) badges.push('<span class="psig-badge">归属于我</span>');
    if (item.can_claim) badges.push('<span class="psig-badge is-claim">待认领</span>');
    const action = item.can_claim
        ? `<button type="button" class="psig-link is-claim" data-psig-claim="${item.id}">认领</button>`
        : (item.can_delete ? `<button type="button" class="psig-link is-danger" data-psig-delete="${item.id}">删除</button>` : '');
    return `<article class="psig-item">
        <img src="${escapeHtml(item.image_url)}" alt="${escapeHtml(item.subject_name || item.name)}" loading="lazy">
        <div class="psig-item__meta">
            <strong>${escapeHtml(item.subject_name || item.name)}</strong>
            <span>${badges.join('')}</span>
            <small>${item.can_claim ? '识别为你的签名，认领后与账号绑定' : (item.usage_count ? `已被使用 ${item.usage_count} 次` : '尚未被使用')}</small>
        </div>
        ${action}
    </article>`;
}

function renderIncoming(item) {
    const myReview = (item.reviewers || []).find((reviewer) => reviewer.status === 'pending');
    const actionable = item.status === 'pending' && Boolean(myReview);
    const reviewers = (item.reviewers || [])
        .map((reviewer) => `<span class="psig-chip is-${escapeHtml(reviewer.status)}">${escapeHtml(reviewer.name || reviewer.kind)} · ${escapeHtml(reviewerStatusText[reviewer.status] || reviewer.status)}</span>`)
        .join('');
    return `<article class="psig-request" data-psig-request="${item.id}">
        <div class="psig-request__head">
            <strong>${escapeHtml(item.requester_name || '申请人')}</strong>
            <em class="psig-chip is-${escapeHtml(item.status)}">${escapeHtml(requestStatusText[item.status] || item.status)}</em>
        </div>
        <p>申请在「${escapeHtml(requestSummary(item))}」使用「${escapeHtml(item.signature_subject_name || item.signature_name)}」签名。批准后仅限该材料当前版本使用。</p>
        ${item.request_note ? `<p class="psig-note">留言：${escapeHtml(item.request_note)}</p>` : ''}
        <div class="psig-request__foot">
            <span>${reviewers}</span>
            ${actionable ? `<span class="psig-actions">
                <button type="button" class="btn btn-primary btn-sm" data-psig-review="approve">同意</button>
                <button type="button" class="btn btn-outline btn-sm" data-psig-review="reject">拒绝</button>
            </span>` : ''}
        </div>
    </article>`;
}

function renderOutgoing(item) {
    return `<article class="psig-request" data-psig-request="${item.id}">
        <div class="psig-request__head">
            <strong>「${escapeHtml(item.signature_subject_name || item.signature_name)}」签名</strong>
            <em class="psig-chip is-${escapeHtml(item.status)}">${escapeHtml(requestStatusText[item.status] || item.status)}</em>
        </div>
        <p>用于「${escapeHtml(requestSummary(item))}」。</p>
        ${item.status === 'pending' ? `<div class="psig-request__foot"><span></span><button type="button" class="psig-link is-danger" data-psig-cancel="${item.id}">撤销申请</button></div>` : ''}
    </article>`;
}

function renderUsage(item) {
    const who = item.is_self_use ? '我' : escapeHtml(item.used_by_name || '使用者');
    return `<li>
        <span>${who} 在「${escapeHtml(item.context_label || item.context_type || '未知位置')}」使用了「${escapeHtml(item.signature_name)}」</span>
        <time>${escapeHtml(String(item.used_at || '').replace('T', ' ').slice(0, 16))}</time>
    </li>`;
}

function render() {
    const pendingIncoming = state.incoming.filter((item) => item.status === 'pending');
    const settledIncoming = state.incoming.filter((item) => item.status !== 'pending').slice(0, 5);
    root.innerHTML = `
        <section class="profile-band profile-reveal psig-card">
            <div class="profile-band__head">
                <div><h3>我的签名</h3></div>
                <div class="psig-upload">
                    <input type="file" accept="image/png,image/jpeg" data-psig-file hidden>
                    <button type="button" class="btn btn-outline btn-sm" data-psig-claim-toggle>${state.claimPanelOpen ? '收起认领' : '认领签名'}</button>
                    <button type="button" class="btn btn-primary btn-sm" data-psig-upload>上传签名</button>
                </div>
            </div>
            <p class="psig-hint">上传白底或透明底的手写签名图片（PNG/JPG）。签名者固定为你本人，归属权在你手上；他人使用前必须经过你的批准。若系统里已有你名字的签名，请使用“认领签名”而不是重复上传。</p>
            <div class="psig-grid">
                ${state.signatures.map(renderSignatureCard).join('') || '<div class="psig-empty">还没有签名，点击右上角上传或认领。</div>'}
            </div>
            ${renderClaimPanel()}
        </section>

        <section class="profile-band profile-reveal psig-card" ${pendingIncoming.length || settledIncoming.length ? '' : 'hidden'}>
            <div class="profile-band__head">
                <div><h3>待我审批${pendingIncoming.length ? ` <i class="psig-count">${pendingIncoming.length}</i>` : ''}</h3></div>
            </div>
            <div class="psig-list">
                ${pendingIncoming.map(renderIncoming).join('') || '<div class="psig-empty">暂无待审批申请。</div>'}
            </div>
            ${settledIncoming.length ? `<details class="psig-history"><summary>最近已处理</summary><div class="psig-list">${settledIncoming.map(renderIncoming).join('')}</div></details>` : ''}
        </section>

        <section class="profile-band profile-reveal psig-card" ${state.outgoing.length ? '' : 'hidden'}>
            <div class="profile-band__head">
                <div><h3>我的申请</h3></div>
            </div>
            <div class="psig-list">${state.outgoing.map(renderOutgoing).join('')}</div>
        </section>

        <section class="profile-band profile-reveal psig-card">
            <div class="profile-band__head">
                <div><h3>使用记录</h3></div>
            </div>
            <ul class="psig-usage">
                ${state.usage.map(renderUsage).join('') || '<li class="psig-empty">你的签名还没有被使用过。</li>'}
            </ul>
        </section>`;
    bindEvents();
}

function setBusy(busy) {
    state.busy = busy;
    root.classList.toggle('is-busy', busy);
}

async function refresh() {
    await loadAll();
    render();
}

async function uploadSignature(file) {
    if (!file || state.busy) return;
    setBusy(true);
    try {
        const formData = new FormData();
        formData.append('file', file);
        await apiFetch('/api/signatures/upload', { method: 'POST', body: formData });
        showToast('签名已上传。', 'success');
        await refresh();
    } catch (error) {
        showToast(error.message || '上传签名失败。', 'error');
    } finally {
        setBusy(false);
    }
}

async function reviewRequest(requestId, action) {
    if (state.busy) return;
    setBusy(true);
    try {
        await apiFetch(`/api/signatures/requests/${requestId}/${action}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({}),
        });
        showToast(action === 'approve' ? '已同意，对方可在该材料当前版本使用签名。' : '已拒绝该申请。', 'success');
        await refresh();
    } catch (error) {
        showToast(error.message || '审批失败。', 'error');
    } finally {
        setBusy(false);
    }
}

async function cancelRequest(requestId) {
    if (state.busy) return;
    setBusy(true);
    try {
        await apiFetch(`/api/signatures/requests/${requestId}/cancel`, { method: 'POST' });
        showToast('申请已撤销。', 'success');
        await refresh();
    } catch (error) {
        showToast(error.message || '撤销失败。', 'error');
    } finally {
        setBusy(false);
    }
}

async function claimSignature(signatureId) {
    if (state.busy) return;
    setBusy(true);
    try {
        await apiFetch(`/api/signatures/${signatureId}/claim`, { method: 'POST' });
        showToast('已认领并绑定到你的账号；他人使用前将由你审批。', 'success');
        await refresh();
    } catch (error) {
        showToast(error.message || '认领失败。', 'error');
    } finally {
        setBusy(false);
    }
}

async function deleteSignature(signatureId) {
    if (state.busy) return;
    if (!window.confirm('删除后基于它的授权将失效，确定删除该签名？')) return;
    setBusy(true);
    try {
        await apiFetch(`/api/signatures/${signatureId}`, { method: 'DELETE' });
        showToast('签名已删除。', 'success');
        await refresh();
    } catch (error) {
        showToast(error.message || '删除失败。', 'error');
    } finally {
        setBusy(false);
    }
}

async function loadClaimCandidates() {
    const params = new URLSearchParams();
    if (state.claimSearch) params.set('q', state.claimSearch);
    try {
        const payload = await apiFetch(`/api/signatures/claim-candidates?${params.toString()}`, { silent: true });
        state.claimCandidates = payload.items || [];
    } catch (error) {
        state.claimCandidates = [];
        showToast(error.message || '加载可认领签名失败。', 'error');
    }
}

async function toggleClaimPanel() {
    state.claimPanelOpen = !state.claimPanelOpen;
    if (state.claimPanelOpen) await loadClaimCandidates();
    render();
}

let claimSearchTimer = null;

async function applyClaim(signatureId) {
    if (state.busy) return;
    setBusy(true);
    try {
        const result = await apiFetch(`/api/signatures/${signatureId}/claim-requests`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({}),
        });
        showToast(result.mode === 'direct'
            ? '已直接认领并绑定到你的账号。'
            : '认领申请已提交，归属人或管理员批准后签名将转移到你名下。', 'success');
        await loadAll();
        await loadClaimCandidates();
        render();
    } catch (error) {
        showToast(error.message || '认领失败。', 'error');
    } finally {
        setBusy(false);
    }
}

function bindEvents() {
    const fileInput = root.querySelector('[data-psig-file]');
    root.querySelector('[data-psig-upload]')?.addEventListener('click', () => fileInput?.click());
    fileInput?.addEventListener('change', () => {
        uploadSignature(fileInput.files?.[0]);
        fileInput.value = '';
    });
    root.querySelectorAll('[data-psig-delete]').forEach((button) => {
        button.addEventListener('click', () => deleteSignature(Number(button.dataset.psigDelete)));
    });
    root.querySelectorAll('[data-psig-claim]').forEach((button) => {
        button.addEventListener('click', () => claimSignature(Number(button.dataset.psigClaim)));
    });
    root.querySelectorAll('[data-psig-cancel]').forEach((button) => {
        button.addEventListener('click', () => cancelRequest(Number(button.dataset.psigCancel)));
    });
    root.querySelector('[data-psig-claim-toggle]')?.addEventListener('click', toggleClaimPanel);
    const claimSearch = root.querySelector('[data-psig-claim-search]');
    claimSearch?.addEventListener('input', () => {
        state.claimSearch = claimSearch.value || '';
        window.clearTimeout(claimSearchTimer);
        claimSearchTimer = window.setTimeout(async () => {
            await loadClaimCandidates();
            const active = document.activeElement === claimSearch;
            render();
            if (active) {
                const restored = root.querySelector('[data-psig-claim-search]');
                restored?.focus();
                try { restored?.setSelectionRange(restored.value.length, restored.value.length); } catch { /* noop */ }
            }
        }, 260);
    });
    root.querySelectorAll('[data-psig-claim-apply]').forEach((button) => {
        button.addEventListener('click', () => applyClaim(Number(button.dataset.psigClaimApply)));
    });
    root.querySelectorAll('[data-psig-review]').forEach((button) => {
        button.addEventListener('click', () => {
            const card = button.closest('[data-psig-request]');
            const requestId = Number(card?.dataset.psigRequest || 0);
            if (requestId) reviewRequest(requestId, button.dataset.psigReview);
        });
    });
}

async function init() {
    if (!root) return;
    try {
        await refresh();
    } catch (error) {
        root.innerHTML = `<div class="psig-empty">${escapeHtml(error.message || '签名数据加载失败，请刷新重试。')}</div>`;
    }
}

init();
