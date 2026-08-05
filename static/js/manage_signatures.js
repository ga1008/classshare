import { apiFetch } from './api.js';
import { closeModal, escapeHtml, formatDate, formatSize, openModal, showMessage } from './ui.js';

const state = {
    items: [],
    selectedId: null,
    actor: null,
    selectedSchoolCode: '',
    schoolOptions: [],
    ownerTeacherOptions: [],
    functionPoints: [],
    pendingRequests: [],
    outgoingRequests: [],
};

const els = {};

const debounce = (fn, delay = 220) => {
    let timer = null;
    return (...args) => {
        window.clearTimeout(timer);
        timer = window.setTimeout(() => fn(...args), delay);
    };
};

const byId = (id) => document.getElementById(id);

const pageEl = () => document.querySelector('[data-signature-page]');
const isSuperAdmin = () => pageEl()?.dataset.isSuperAdmin === '1';
const actorSchoolCode = () => pageEl()?.dataset.actorSchoolCode || '';
const actorSchoolName = () => pageEl()?.dataset.actorSchoolName || '';

function optionLabel(option) {
    if (!option) return '';
    return `${option.school_name || option.school_code}（${option.school_code}）`;
}

function schoolCodeFromInput(value) {
    const text = String(value || '').trim();
    if (!text) return '';
    const matched = state.schoolOptions.find((item) => optionLabel(item) === text || item.school_code === text);
    if (matched?.school_code) return matched.school_code;
    return /^[a-z0-9_.-]+$/i.test(text) ? text : '';
}

function ownerTeacherIdFromInput(value) {
    const text = String(value || '').trim();
    if (!text) return '';
    const matched = state.ownerTeacherOptions.find((item) => teacherOptionLabel(item) === text || String(item.id) === text);
    return matched?.id || (/^\d+$/.test(text) ? Number(text) : '');
}

function teacherOptionLabel(item) {
    if (!item) return '';
    const org = [item.college, item.department].filter(Boolean).join(' / ');
    return `${item.name || item.email}（${item.id}${org ? ` · ${org}` : ''}）`;
}

function cacheElements() {
    [
        'signature-search-input',
        'signature-school-switcher',
        'signature-school-field',
        'signature-school-search-input',
        'signature-school-options',
        'signature-school-note',
        'signature-scope-filter',
        'signature-identity-filter',
        'signature-owner-filter',
        'signature-grid',
        'signature-result-summary',
        'signature-clear-filter-btn',
        'signature-refresh-btn',
        'signature-open-upload-btn',
        'signature-detail-preview',
        'signature-detail-title',
        'signature-detail-chips',
        'signature-detail-list',
        'signature-download-link',
        'signature-request-btn',
        'signature-edit-btn',
        'signature-replace-image-btn',
        'signature-replace-image-input',
        'signature-unbind-btn',
        'signature-batch-select-all-label',
        'signature-batch-select-all',
        'signature-batch-approve-btn',
        'signature-batch-reject-btn',
        'signature-delete-btn',
        'signature-open-claim-btn',
        'signature-claim-search-input',
        'signature-claim-list',
        'signature-requests-refresh-btn',
        'signature-request-list',
        'signature-outgoing-request-list',
        'signature-request-modal',
        'signature-request-form',
        'signature-request-subtitle',
        'signature-function-point-list',
        'signature-request-note',
        'signature-request-status',
        'signature-request-submit-btn',
        'signature-upload-form',
        'signature-file-input',
        'signature-file-label',
        'signature-upload-status',
        'signature-upload-submit-btn',
        'signature-subject-role-field',
        'signature-subject-name-field',
        'signature-subject-account-field',
        'signature-scope-level-field',
        'signature-subject-role-input',
        'signature-subject-name-input',
        'signature-subject-account-input',
        'signature-scope-level-input',
        'signature-identity-input',
        'signature-name-input',
        'signature-description-input',
        'signature-edit-form',
        'signature-edit-name-input',
        'signature-edit-subject-name-input',
        'signature-edit-subject-input',
        'signature-edit-subject-role-input',
        'signature-edit-identity-input',
        'signature-edit-scope-level-input',
        'signature-edit-school-field',
        'signature-edit-school-input',
        'signature-edit-college-input',
        'signature-edit-department-input',
        'signature-edit-owner-input',
        'signature-owner-teacher-options',
        'signature-edit-description-input',
        'signature-edit-status',
        'signature-edit-submit-btn',
        'signature-stat-total',
        'signature-stat-mine',
        'signature-stat-college',
        'signature-stat-usage',
    ].forEach((id) => {
        els[id] = byId(id);
    });
    if (!els['signature-request-btn']) {
        const actions = document.querySelector('.signature-actions');
        if (actions) {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'btn btn-outline btn-sm';
            button.id = 'signature-request-btn';
            button.hidden = true;
            button.textContent = '申请使用';
            actions.insertBefore(button, els['signature-edit-btn'] || null);
            els['signature-request-btn'] = button;
        }
    }
    if (!els['signature-claim-btn']) {
        const actions = document.querySelector('.signature-actions');
        if (actions) {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'btn btn-primary btn-sm';
            button.id = 'signature-claim-btn';
            button.hidden = true;
            button.textContent = '认领为本人签名';
            actions.insertBefore(button, els['signature-request-btn'] || null);
            els['signature-claim-btn'] = button;
        }
    }
}

function signatureQuery() {
    const params = new URLSearchParams();
    const search = els['signature-search-input']?.value?.trim();
    const scope = els['signature-scope-filter']?.value;
    const identityCategory = els['signature-identity-filter']?.value;
    const ownerRole = els['signature-owner-filter']?.value;
    const schoolCode = state.selectedSchoolCode
        || schoolCodeFromInput(els['signature-school-search-input']?.value)
        || (isSuperAdmin() ? actorSchoolCode() : '');
    if (search) params.set('q', search);
    if (schoolCode) params.set('school_code', schoolCode);
    if (scope) params.set('scope', scope);
    if (identityCategory) params.set('identity_category', identityCategory);
    if (ownerRole) params.set('owner_role', ownerRole);
    params.set('limit', '500');
    return params.toString();
}

async function loadSignatures({ keepSelection = true } = {}) {
    const grid = els['signature-grid'];
    if (grid) {
        grid.innerHTML = '<div class="signature-empty">正在加载签名...</div>';
    }
    try {
        const payload = await apiFetch(`/api/signatures?${signatureQuery()}`, { method: 'GET' });
        state.items = Array.isArray(payload.items) ? payload.items : [];
        state.actor = payload.actor || null;
        state.schoolOptions = Array.isArray(payload.school_options) ? payload.school_options : [];
        if (payload.selected_school?.school_code) {
            state.selectedSchoolCode = payload.selected_school.school_code;
        }
        renderSchoolControls(payload.selected_school || null);
        updateStats(payload.stats || {});
        renderGrid();
        if (keepSelection && state.selectedId && state.items.some((item) => item.id === state.selectedId)) {
            selectSignature(state.selectedId);
        } else if (state.items.length > 0) {
            selectSignature(state.items[0].id);
        } else {
            state.selectedId = null;
            renderDetail(null);
        }
        await loadSignatureRequests();
    } catch (error) {
        if (grid) {
            grid.innerHTML = '<div class="signature-empty">签名加载失败，请稍后重试。</div>';
        }
    }
}

function updateStats(stats) {
    const pairs = [
        ['signature-stat-total', stats.visible_total ?? 0],
        ['signature-stat-mine', stats.mine ?? 0],
        ['signature-stat-college', stats.college ?? 0],
        ['signature-stat-usage', stats.usage_total ?? 0],
    ];
    pairs.forEach(([id, value]) => {
        if (els[id]) els[id].textContent = String(value);
    });
}

function renderSchoolControls(selectedSchool = null) {
    const schoolField = els['signature-school-field'];
    const schoolInput = els['signature-school-search-input'];
    const schoolOptions = els['signature-school-options'];
    if (schoolOptions) {
        schoolOptions.innerHTML = state.schoolOptions
            .map((item) => `<option value="${escapeHtml(optionLabel(item))}" data-code="${escapeHtml(item.school_code)}"></option>`)
            .join('');
    }
    if (schoolField) {
        schoolField.hidden = !isSuperAdmin();
    }
    const school = selectedSchool || state.schoolOptions.find((item) => item.school_code === state.selectedSchoolCode);
    if (schoolInput && isSuperAdmin() && school && selectedSchool) {
        schoolInput.value = optionLabel(school);
    }
    if (els['signature-school-note']) {
        const display = school?.school_name || actorSchoolName() || '未记录';
        els['signature-school-note'].textContent = isSuperAdmin()
            ? `当前学校：${display}。切换学校后仅显示该校签名。`
            : `当前学校：${display}。普通账号只能使用本校签名。`;
    }
}

async function fetchSchoolOptions(query = '') {
    if (!isSuperAdmin()) return;
    const params = new URLSearchParams();
    if (query) params.set('q', query);
    const payload = await apiFetch(`/api/signatures/schools?${params.toString()}`, { method: 'GET', silent: true });
    state.schoolOptions = Array.isArray(payload.items) ? payload.items : [];
    renderSchoolControls();
}

async function fetchOwnerTeachers(query = '') {
    const params = new URLSearchParams();
    const schoolCode = state.selectedSchoolCode || schoolCodeFromInput(els['signature-edit-school-input']?.value) || actorSchoolCode();
    if (query) params.set('q', query);
    if (schoolCode) params.set('school_code', schoolCode);
    const payload = await apiFetch(`/api/signatures/teachers?${params.toString()}`, { method: 'GET', silent: true });
    state.ownerTeacherOptions = Array.isArray(payload.items) ? payload.items : [];
    if (els['signature-owner-teacher-options']) {
        els['signature-owner-teacher-options'].innerHTML = state.ownerTeacherOptions
            .map((item) => `<option value="${escapeHtml(teacherOptionLabel(item))}" data-id="${item.id}"></option>`)
            .join('');
    }
}

function renderGrid() {
    const grid = els['signature-grid'];
    if (!grid) return;
    const countText = `${state.items.length} 个签名`;
    if (els['signature-result-summary']) {
        els['signature-result-summary'].textContent = countText;
    }
    if (!state.items.length) {
        grid.innerHTML = '<div class="signature-empty">没有找到符合条件的签名。</div>';
        return;
    }
    grid.innerHTML = state.items.map(renderCard).join('');
    grid.querySelectorAll('[data-signature-card]').forEach((card) => {
        card.addEventListener('click', () => {
            selectSignature(Number(card.dataset.signatureId || 0));
        });
    });
}

function renderCard(item) {
    const activeClass = item.id === state.selectedId ? ' is-active' : '';
    const chipClass = item.owner_role === 'system' ? ' is-system' : (item.is_owner ? ' is-owner' : '');
    return `
        <article class="signature-card${activeClass}" data-signature-card data-signature-id="${item.id}">
            <div class="signature-preview-tile">
                <img src="${escapeHtml(item.image_url)}" alt="${escapeHtml(item.name)}">
            </div>
            <div class="signature-card-main">
                <strong class="signature-card-title" title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</strong>
                <div class="signature-meta-line">
                    <span class="signature-chip${chipClass}">${escapeHtml(item.scope_label)}</span>
                    ${item.identity_label ? `<span class="signature-chip">${escapeHtml(item.identity_label)}${item.identity_verified ? ' ✓' : ''}</span>` : `<span class="signature-chip">${escapeHtml(item.subject_role_label)}</span>`}
                    ${item.is_owner ? '<span class="signature-chip is-owner">归属我</span>' : `<span class="signature-chip">${escapeHtml(item.owner_name || '未归属')}</span>`}
                </div>
            </div>
        </article>
    `;
}

function selectSignature(signatureId) {
    const item = state.items.find((entry) => entry.id === signatureId);
    state.selectedId = item ? item.id : null;
    renderGrid();
    renderDetail(item || null);
}

function renderDetail(item) {
    if (!item) {
        if (els['signature-detail-preview']) {
            els['signature-detail-preview'].innerHTML = '<div class="signature-empty">选择签名后查看预览与调用信息。</div>';
        }
        if (els['signature-detail-title']) els['signature-detail-title'].textContent = '未选择签名';
        if (els['signature-detail-chips']) els['signature-detail-chips'].innerHTML = '';
        if (els['signature-detail-list']) els['signature-detail-list'].innerHTML = '';
        setActionVisibility(false, false, false, false);
        return;
    }
    if (els['signature-detail-preview']) {
        els['signature-detail-preview'].innerHTML = `<img src="${escapeHtml(item.image_url)}" alt="${escapeHtml(item.name)}">`;
    }
    if (els['signature-detail-title']) {
        els['signature-detail-title'].textContent = item.name || '电子签名';
    }
    if (els['signature-detail-chips']) {
        els['signature-detail-chips'].innerHTML = `
            <span class="signature-chip${item.is_owner ? ' is-owner' : ''}">${escapeHtml(item.scope_label)}</span>
            <span class="signature-chip">${escapeHtml(item.subject_role_label)}</span>
            ${item.identity_label ? `<span class="signature-chip">${escapeHtml(item.identity_label)}${item.identity_verified ? '（已核验）' : '（未核验）'}</span>` : ''}
            ${item.is_owner ? '<span class="signature-chip is-owner">归属我</span>' : ''}
            ${item.owner_role === 'system' ? '<span class="signature-chip is-system">平台导入</span>' : ''}
            ${item.owner_role !== 'system' && !item.subject_bound ? '<span class="signature-chip">未绑定账号</span>' : ''}
        `;
    }
    if (els['signature-detail-list']) {
        els['signature-detail-list'].innerHTML = [
            ['签名人', item.subject_name || item.name],
            ['职务身份', item.identity_label || '未设置'],
            ['归属人', item.owner_name || '平台导入'],
            ['上传者', item.uploaded_by_name || item.owner_name || '平台导入'],
            ['学校', item.school_name || '未记录'],
            ['学院', item.college || '未记录'],
            ['系别', item.department || '未记录'],
            ['文件大小', formatSize(item.file_size || 0)],
            ['已调用', `${item.usage_count || 0} 次`],
            ['最近调用', item.last_used_at ? formatDate(item.last_used_at) : '暂无'],
            ['上传时间', item.created_at ? formatDate(item.created_at) : '暂无'],
        ].map(([label, value]) => `
            <div class="signature-detail-row">
                <span>${escapeHtml(label)}</span>
                <strong>${escapeHtml(value)}</strong>
            </div>
        `).join('');
    }
    setActionVisibility(
        Boolean(item.can_use),
        Boolean(item.can_delete),
        Boolean(item.can_edit),
        Boolean(item.can_request_use),
        Boolean(item.can_claim),
    );
    if (els['signature-download-link']) {
        els['signature-download-link'].href = item.download_url || '#';
    }
}

function canReplaceImage(item) {
    if (!item) return false;
    if (item.can_edit) return true;
    return item.subject_role === state.actor?.role && Number(item.subject_id) === Number(state.actor?.id);
}

function setActionVisibility(canUse, canDelete, canEdit = false, canRequestUse = false, canClaim = false) {
    if (els['signature-claim-btn']) els['signature-claim-btn'].hidden = !canClaim;
    if (els['signature-replace-image-btn']) {
        const item = state.items.find((entry) => entry.id === state.selectedId);
        els['signature-replace-image-btn'].hidden = !canReplaceImage(item);
    }
    if (els['signature-unbind-btn']) {
        const item = state.items.find((entry) => entry.id === state.selectedId);
        els['signature-unbind-btn'].hidden = !item?.can_unbind;
    }
    if (els['signature-download-link']) els['signature-download-link'].hidden = !canUse;
    if (els['signature-request-btn']) {
        els['signature-request-btn'].hidden = !canRequestUse;
        const item = state.items.find((entry) => entry.id === state.selectedId);
        els['signature-request-btn'].disabled = item?.request_status === 'pending';
        els['signature-request-btn'].textContent = item?.request_status === 'pending' ? '已申请' : '申请使用';
    }
    if (els['signature-edit-btn']) els['signature-edit-btn'].hidden = !canEdit;
    if (els['signature-delete-btn']) els['signature-delete-btn'].hidden = !canDelete;
}

async function loadClaimCandidates() {
    const list = els['signature-claim-list'];
    if (!list) return;
    list.innerHTML = '<div class="signature-empty">正在加载可认领签名...</div>';
    try {
        const params = new URLSearchParams();
        const query = els['signature-claim-search-input']?.value?.trim();
        if (query) params.set('q', query);
        const payload = await apiFetch(`/api/signatures/claim-candidates?${params.toString()}`, { method: 'GET', silent: true });
        const items = Array.isArray(payload.items) ? payload.items : [];
        if (!items.length) {
            list.innerHTML = '<div class="signature-empty">没有可认领的签名。</div>';
            return;
        }
        list.innerHTML = items.map((item) => {
            const chips = [
                item.identity_label ? `<span class="signature-chip">${escapeHtml(item.identity_label)}</span>` : '',
                item.subject_bound ? '<span class="signature-chip is-system">已绑定他人账号</span>' : '<span class="signature-chip">未绑定账号</span>',
                item.owner_name ? `<span class="signature-chip">归属：${escapeHtml(item.owner_name)}</span>` : '',
            ].filter(Boolean).join('');
            const action = item.has_pending_claim
                ? '<span class="signature-chip">认领申请审批中</span>'
                : `<button type="button" class="btn ${item.can_direct_claim ? 'btn-primary' : 'btn-outline'} btn-sm" data-signature-claim-apply="${item.id}">${item.can_direct_claim ? '直接认领' : '申请认领'}</button>`;
            return `
                <article class="signature-request-item" data-signature-claim-id="${item.id}">
                    <div class="signature-request-main">
                        <p class="signature-request-title">${escapeHtml(item.subject_name || '未命名签名')}</p>
                        <div class="signature-request-meta">${chips}</div>
                    </div>
                    <div class="signature-request-actions">${action}</div>
                </article>
            `;
        }).join('');
    } catch (error) {
        list.innerHTML = `<div class="signature-empty">${escapeHtml(error.message || '加载失败，请稍后重试。')}</div>`;
    }
}

async function applyClaim(signatureId, button) {
    if (!signatureId) return;
    if (button) button.disabled = true;
    try {
        const result = await apiFetch(`/api/signatures/${signatureId}/claim-requests`, { method: 'POST', body: {} });
        if (result.mode === 'direct') {
            showMessage('已直接认领并绑定到你的账号。', 'success');
        } else {
            showMessage('认领申请已提交，归属人或管理员批准后签名将转移到你名下。', 'success');
        }
        await loadClaimCandidates();
        await loadSignatures({ keepSelection: true });
    } catch (error) {
        if (button) button.disabled = false;
    }
}

async function fetchSignatureRefs(signatureId) {
    try {
        return await apiFetch(`/api/signatures/${signatureId}/refs`, { method: 'GET', silent: true });
    } catch {
        return null;
    }
}

async function replaceCurrentSignatureImage(file) {
    if (!state.selectedId || !file) return;
    const refs = await fetchSignatureRefs(state.selectedId);
    if (refs?.active_binding_count > 0) {
        const proceed = window.confirm(
            `该签名当前被 ${refs.active_binding_count} 处材料签名点引用；更换图片后这些材料重新导出将使用新图片，相关人员会收到通知。确定更换？`,
        );
        if (!proceed) return;
    }
    const formData = new FormData();
    formData.append('file', file);
    try {
        await apiFetch(`/api/signatures/${state.selectedId}/image`, { method: 'POST', body: formData });
        showMessage('签名图片已更换。', 'success');
        await loadSignatures({ keepSelection: true });
    } catch {
        // apiFetch already surfaces the error.
    }
}

async function unbindCurrentSignature() {
    if (!state.selectedId) return;
    const item = state.items.find((entry) => entry.id === state.selectedId);
    if (!item?.can_unbind) return;
    if (!window.confirm(`确定解除“${item.subject_name || item.name}”与账号的绑定？解除后该签名的使用申请将由归属人或管理员审批。`)) {
        return;
    }
    try {
        await apiFetch(`/api/signatures/${state.selectedId}/unbind`, { method: 'POST' });
        showMessage('绑定已解除。', 'success');
        await loadSignatures({ keepSelection: true });
    } catch {
        // apiFetch already surfaces the error.
    }
}

function selectedBatchRequestIds() {
    return Array.from(document.querySelectorAll('input[data-signature-batch-check]:checked'))
        .map((input) => Number(input.dataset.signatureBatchCheck || 0))
        .filter(Boolean);
}

function updateBatchToolbar() {
    const isAdmin = Boolean(state.adminRequestView);
    const hasPending = state.pendingRequests.length > 0;
    ['signature-batch-select-all-label', 'signature-batch-approve-btn', 'signature-batch-reject-btn'].forEach((id) => {
        if (els[id]) els[id].hidden = !(isAdmin && hasPending);
    });
}

async function batchReviewRequests(action) {
    const ids = selectedBatchRequestIds();
    if (!ids.length) {
        showMessage('请先勾选要处理的申请。', 'warning');
        return;
    }
    const verb = action === 'approve' ? '批准' : '拒绝';
    if (!window.confirm(`确定批量${verb}选中的 ${ids.length} 条申请？`)) return;
    const buttons = [els['signature-batch-approve-btn'], els['signature-batch-reject-btn']];
    buttons.forEach((button) => { if (button) button.disabled = true; });
    try {
        const result = await apiFetch('/api/signatures/requests/batch-review', {
            method: 'POST',
            body: { request_ids: ids, action },
        });
        const failNote = result.failed ? `，${result.failed} 条失败（如认领须签名者本人审批）` : '';
        showMessage(`已${verb} ${result.processed} 条申请${failNote}。`, result.failed ? 'warning' : 'success');
        await loadSignatureRequests();
        await loadSignatures({ keepSelection: true });
    } finally {
        buttons.forEach((button) => { if (button) button.disabled = false; });
    }
}

async function claimCurrentSignature() {
    if (!state.selectedId) return;
    const item = state.items.find((entry) => entry.id === state.selectedId);
    if (!item || !item.can_claim) return;
    const button = els['signature-claim-btn'];
    if (button) button.disabled = true;
    try {
        await apiFetch(`/api/signatures/${state.selectedId}/claim`, { method: 'POST' });
        showMessage('已认领并绑定到你的账号；后续使用申请将由你审批。', 'success');
        await loadSignatures({ keepSelection: true });
    } finally {
        if (button) button.disabled = false;
    }
}

async function requestCurrentSignatureUse() {
    if (!state.selectedId) return;
    const item = state.items.find((entry) => entry.id === state.selectedId);
    if (!item || !item.can_request_use) return;
    if (!state.functionPoints.length) {
        const payload = await apiFetch('/api/signatures/function-points', { method: 'GET' });
        state.functionPoints = Array.isArray(payload.items) ? payload.items : [];
    }
    if (els['signature-request-subtitle']) {
        els['signature-request-subtitle'].textContent = `为“${item.subject_name || item.name}”选择一个或多个一次性使用功能点。`;
    }
    if (els['signature-function-point-list']) {
        els['signature-function-point-list'].innerHTML = state.functionPoints.map((point) => `
            <label class="signature-function-point-option">
                <input type="checkbox" name="signature_function_point" value="${escapeHtml(point.key)}">
                <span><strong>${escapeHtml(point.label)}</strong><small>${escapeHtml(point.description || point.key)}</small></span>
            </label>
        `).join('') || '<div class="signature-empty">后台尚未登记可申请的签名功能点。</div>';
    }
    if (els['signature-request-note']) els['signature-request-note'].value = '';
    if (els['signature-request-status']) els['signature-request-status'].textContent = '';
    openModal('signature-request-modal');
}

async function submitSignatureRequest(event) {
    event.preventDefault();
    if (!state.selectedId) return;
    const keys = Array.from(document.querySelectorAll('input[name="signature_function_point"]:checked'))
        .map((input) => input.value);
    if (!keys.length) {
        showMessage('请至少选择一个签名功能点。', 'warning');
        return;
    }
    const button = els['signature-request-submit-btn'];
    if (button) button.disabled = true;
    try {
        await apiFetch(`/api/signatures/${state.selectedId}/requests`, {
            method: 'POST',
            body: {
                function_point_keys: keys,
                note: els['signature-request-note']?.value?.trim() || '',
            },
        });
        closeModal('signature-request-modal');
        showMessage('申请已提交；归属人和签名者均会收到通知，任一人批准即可。', 'success');
        await loadSignatures({ keepSelection: true });
    } finally {
        if (button) button.disabled = false;
    }
}

async function loadSignatureRequests() {
    const list = els['signature-request-list'];
    if (!list) return;
    try {
        const [incoming, outgoing] = await Promise.all([
            apiFetch('/api/signatures/requests?direction=incoming&status=pending', { method: 'GET', silent: true }),
            apiFetch('/api/signatures/requests?direction=outgoing', { method: 'GET', silent: true }),
        ]);
        state.pendingRequests = Array.isArray(incoming.items) ? incoming.items : [];
        state.adminRequestView = Boolean(incoming.admin_view);
        state.outgoingRequests = Array.isArray(outgoing.items) ? outgoing.items : [];
        renderSignatureRequests();
    } catch (error) {
        list.innerHTML = '<div class="signature-empty">申请加载失败，请稍后重试。</div>';
    }
}

function renderSignatureRequests() {
    const list = els['signature-request-list'];
    if (!list) return;
    if (!state.pendingRequests.length) {
        list.innerHTML = '<div class="signature-empty">暂无待审批申请。</div>';
        updateBatchToolbar();
        renderOutgoingRequests();
        return;
    }
    list.innerHTML = state.pendingRequests.map((item) => {
        const isClaim = item.request_kind === 'claim';
        const signatureName = escapeHtml((item.signature_name || '未命名签名') + (isClaim ? ' · 认领申请' : ''));
        const requester = escapeHtml(item.requester_name || `教师 ${item.requester_teacher_id}`);
        const pointLabels = (item.items || []).map((entry) => entry.function_point_label).filter(Boolean).join('、');
        const purpose = isClaim ? '申请认领签名（批准后归属权转移并绑定其账号）' : (pointLabels || '未登记功能点');
        const material = !isClaim && item.context_label ? ` · ${item.context_label}` : '';
        const meta = escapeHtml(`${requester} · ${purpose}${material} · ${item.requested_at ? formatDate(item.requested_at) : ''}`);
        const mine = (item.reviewers || []).find((reviewer) => (
            reviewer.role === state.actor?.role && Number(reviewer.id) === Number(state.actor?.id)
        ));
        const canAct = mine?.status === 'pending'
            || (state.adminRequestView && !mine && item.status === 'pending');
        const adminBadge = state.adminRequestView && !mine
            ? '<span class="signature-chip is-system">管理员代批</span>'
            : '';
        const batchCheck = state.adminRequestView
            ? `<input type="checkbox" data-signature-batch-check="${item.id}" style="width:16px;height:16px;accent-color:#0f766e;" aria-label="选择此申请">`
            : '';
        const actions = canAct ? `
            ${batchCheck}
            ${adminBadge}
            <button type="button" class="btn btn-primary btn-sm" data-signature-request-action="approve">批准</button>
            <button type="button" class="btn btn-outline btn-sm" data-signature-request-action="reject">拒绝</button>
        ` : `${batchCheck}<span class="signature-chip">${escapeHtml(mine?.status || item.status)}</span>`;
        const claimPreview = isClaim
            ? `<img class="signature-request-preview" src="/api/signatures/${item.signature_id}/image" alt="签名图" loading="lazy" style="max-height:44px;max-width:120px;object-fit:contain;background:#fff;border:1px solid rgba(148,163,184,.3);border-radius:6px;padding:2px;margin-top:6px;">`
            : '';
        return `
            <article class="signature-request-item" data-signature-request-id="${item.id}">
                <div class="signature-request-main">
                    <p class="signature-request-title">${signatureName}</p>
                    <div class="signature-request-meta">${meta}</div>
                    ${claimPreview}
                </div>
                <div class="signature-request-actions">
                    ${actions}
                </div>
            </article>
        `;
    }).join('');
    updateBatchToolbar();
    renderOutgoingRequests();
}

function renderOutgoingRequests() {
    const list = els['signature-outgoing-request-list'];
    if (!list) return;
    if (!state.outgoingRequests.length) {
        list.innerHTML = '<div class="signature-empty">暂无签名使用申请。</div>';
        return;
    }
    const statusLabels = {
        pending: '待审批', approved: '已批准·当前材料可用', partially_used: '旧版授权部分已使用',
        consumed: '旧版授权已使用', rejected: '已拒绝', cancelled: '已结束',
    };
    list.innerHTML = state.outgoingRequests.map((item) => {
        const isClaim = item.request_kind === 'claim';
        const points = isClaim
            ? '认领申请（批准后归属权转移并绑定我的账号）'
            : (item.items || []).map((entry) => `${entry.function_point_label}（${entry.status}）`).join('、');
        return `
            <article class="signature-request-item" data-signature-request-id="${item.id}">
                <div class="signature-request-main">
                    <p class="signature-request-title">${escapeHtml(item.signature_name || '未命名签名')} · ${escapeHtml(statusLabels[item.status] || item.status)}</p>
                <div class="signature-request-meta">${escapeHtml(points || '未登记功能点')}${!isClaim && item.context_label ? ` · ${escapeHtml(item.context_label)}` : ''} · ${escapeHtml(item.requested_at ? formatDate(item.requested_at) : '')}</div>
                </div>
                <div class="signature-request-actions">
                    ${item.status === 'pending' ? '<button type="button" class="btn btn-outline btn-sm" data-signature-request-action="cancel">撤销</button>' : ''}
                </div>
            </article>
        `;
    }).join('');
}

async function reviewSignatureRequest(requestId, action) {
    if (!requestId || !['approve', 'reject', 'cancel'].includes(action)) return;
    try {
        await apiFetch(`/api/signatures/requests/${requestId}/${action}`, {
            method: 'POST',
            body: {},
        });
        showMessage(action === 'approve' ? '已批准签名使用申请。' : action === 'reject' ? '已记录拒绝意见。' : '申请已撤销。', 'success');
        await loadSignatureRequests();
        await loadSignatures({ keepSelection: true });
    } catch {
        // apiFetch already surfaces the error.
    }
}

async function deleteCurrentSignature() {
    if (!state.selectedId) return;
    const item = state.items.find((entry) => entry.id === state.selectedId);
    if (!item) return;
    const refs = await fetchSignatureRefs(state.selectedId);
    const bindingWarning = refs?.active_binding_count > 0
        ? `\n注意：该签名当前被 ${refs.active_binding_count} 处材料签名点引用，删除后这些材料重新导出将缺少此签名。`
        : '';
    const pendingWarning = refs?.pending_request_count > 0
        ? `\n另有 ${refs.pending_request_count} 条待审批申请与它关联。`
        : '';
    if (!window.confirm(`确定删除“${item.name}”？删除后不会再出现在可用签名中。${bindingWarning}${pendingWarning}`)) {
        return;
    }
    try {
        await apiFetch(`/api/signatures/${state.selectedId}`, { method: 'DELETE' });
        showMessage('签名已删除', 'success');
        state.selectedId = null;
        await loadSignatures({ keepSelection: false });
    } catch {
        // apiFetch already surfaces the error.
    }
}

async function openEditModal() {
    const item = state.items.find((entry) => entry.id === state.selectedId);
    if (!item || !item.can_edit) return;
    if (els['signature-edit-name-input']) els['signature-edit-name-input'].value = item.name || '';
    if (els['signature-edit-subject-name-input']) els['signature-edit-subject-name-input'].value = item.subject_name || '';
    if (els['signature-edit-subject-input']) {
        const subject = state.ownerTeacherOptions.find((entry) => Number(entry.id) === Number(item.subject_id));
        els['signature-edit-subject-input'].value = subject ? teacherOptionLabel(subject) : (item.subject_id ? String(item.subject_id) : '');
    }
    if (els['signature-edit-subject-role-input']) els['signature-edit-subject-role-input'].value = item.subject_role || 'teacher';
    if (els['signature-edit-identity-input']) els['signature-edit-identity-input'].value = item.identity_category || '';
    const scopeLevel = item.scope_level === 'college' ? 'department' : (item.scope_level || 'department');
    if (els['signature-edit-scope-level-input']) els['signature-edit-scope-level-input'].value = scopeLevel;
    if (els['signature-edit-college-input']) els['signature-edit-college-input'].value = item.college || '';
    if (els['signature-edit-department-input']) els['signature-edit-department-input'].value = item.department || '';
    if (els['signature-edit-description-input']) els['signature-edit-description-input'].value = item.description || '';
    if (els['signature-edit-school-field']) els['signature-edit-school-field'].hidden = !isSuperAdmin();
    if (els['signature-edit-school-input']) {
        const school = state.schoolOptions.find((entry) => entry.school_code === item.school_code) || {
            school_code: item.school_code,
            school_name: item.school_name,
        };
        els['signature-edit-school-input'].value = optionLabel(school);
    }
    if (els['signature-edit-owner-input']) {
        els['signature-edit-owner-input'].value = item.owner_role === 'teacher'
            ? `${item.owner_name || '教师'}（${item.owner_id}）`
            : '';
    }
    if (els['signature-edit-status']) {
        els['signature-edit-status'].textContent = item.is_owner ? '你是当前归属人，可以维护此签名。' : '超管正在维护此签名。';
    }
    await fetchOwnerTeachers('');
    if (els['signature-edit-subject-input']) {
        const subject = state.ownerTeacherOptions.find((entry) => Number(entry.id) === Number(item.subject_id));
        els['signature-edit-subject-input'].value = subject ? teacherOptionLabel(subject) : (item.subject_id ? String(item.subject_id) : '');
    }
    openModal('signature-edit-modal');
}

async function submitEdit(event) {
    event.preventDefault();
    if (!state.selectedId) return;
    const submitBtn = els['signature-edit-submit-btn'];
    if (submitBtn) submitBtn.disabled = true;
    try {
        const payload = {
            name: els['signature-edit-name-input']?.value?.trim() || '',
            subject_name: els['signature-edit-subject-name-input']?.value?.trim() || '',
            subject_role: els['signature-edit-subject-role-input']?.value || '',
            identity_category: els['signature-edit-identity-input']?.value || '',
            scope_level: els['signature-edit-scope-level-input']?.value || '',
            college: els['signature-edit-college-input']?.value?.trim() || '',
            department: els['signature-edit-department-input']?.value?.trim() || '',
            description: els['signature-edit-description-input']?.value?.trim() || '',
        };
        const ownerTeacherId = ownerTeacherIdFromInput(els['signature-edit-owner-input']?.value);
        if (ownerTeacherId) payload.owner_teacher_id = ownerTeacherId;
        const subjectTeacherId = ownerTeacherIdFromInput(els['signature-edit-subject-input']?.value);
        if (subjectTeacherId && payload.subject_role === 'teacher') payload.subject_teacher_id = subjectTeacherId;
        if (isSuperAdmin()) {
            const schoolCode = schoolCodeFromInput(els['signature-edit-school-input']?.value);
            if (schoolCode) payload.school_code = schoolCode;
        }
        await apiFetch(`/api/signatures/${state.selectedId}`, {
            method: 'PATCH',
            body: payload,
        });
        showMessage('签名属性已更新', 'success');
        closeModal('signature-edit-modal');
        await loadSignatures({ keepSelection: true });
    } finally {
        if (submitBtn) submitBtn.disabled = false;
    }
}

function resetFilters() {
    [
        'signature-search-input',
        'signature-scope-filter',
        'signature-identity-filter',
        'signature-owner-filter',
    ].forEach((id) => {
        if (els[id]) els[id].value = '';
    });
    loadSignatures({ keepSelection: false });
}

function updateFileLabel() {
    const files = Array.from(els['signature-file-input']?.files || []);
    if (!els['signature-file-label']) return;
    if (!files.length) {
        els['signature-file-label'].textContent = '选择签名图片';
        return;
    }
    els['signature-file-label'].textContent = files.length === 1 ? files[0].name : `已选择 ${files.length} 个文件`;
}

async function submitUpload(event) {
    event.preventDefault();
    const files = Array.from(els['signature-file-input']?.files || []);
    if (!files.length) {
        showMessage('请先选择签名图片', 'warning');
        return;
    }
    const submitBtn = els['signature-upload-submit-btn'];
    const status = els['signature-upload-status'];
    if (submitBtn) submitBtn.disabled = true;
    let successCount = 0;
    let failCount = 0;
    let lastError = null;
    try {
        for (const file of files) {
            if (status) status.textContent = `正在上传 ${file.name}...`;
            const formData = new FormData();
            formData.append('file', file);
            const typedName = els['signature-name-input']?.value?.trim() || '';
            formData.append('name', files.length === 1 && typedName ? typedName : file.name.replace(/\.[^.]+$/, ''));
            formData.append('subject_role', els['signature-subject-role-input']?.value || '');
            formData.append('subject_name', els['signature-subject-name-input']?.value?.trim() || '');
            const subjectTeacherId = ownerTeacherIdFromInput(els['signature-subject-account-input']?.value);
            if (subjectTeacherId) formData.append('subject_id', String(subjectTeacherId));
            formData.append('scope_level', els['signature-scope-level-input']?.value || '');
            formData.append('identity_category', els['signature-identity-input']?.value || '');
            formData.append('description', els['signature-description-input']?.value?.trim() || '');
            try {
                await apiFetch('/api/signatures/upload', {
                    method: 'POST',
                    body: formData,
                    silent: true,
                });
                successCount += 1;
            } catch (error) {
                failCount += 1;
                lastError = error;
            }
        }
        if (status) status.textContent = lastError ? String(lastError.message || '上传失败') : `上传完成：成功 ${successCount}，失败 ${failCount}`;
        if (failCount && lastError) {
            // 同名签名等业务原因必须原样透出（如“请通过认领获得归属”）。
            showMessage(lastError.message || `上传完成：${successCount} 成功，${failCount} 失败`, 'warning');
        } else {
            showMessage('签名上传成功', 'success');
        }
        if (!failCount) {
            els['signature-upload-form']?.reset();
            updateFileLabel();
            closeModal('signature-upload-modal');
        }
        await loadSignatures({ keepSelection: false });
    } finally {
        if (submitBtn) submitBtn.disabled = false;
    }
}

function configureUploadFormForActor() {
    ['signature-subject-role-field', 'signature-subject-name-field', 'signature-subject-account-field', 'signature-scope-level-field'].forEach((id) => {
        if (els[id]) els[id].hidden = !isSuperAdmin();
    });
    if (!isSuperAdmin() && els['signature-school-field']) {
        els['signature-school-field'].hidden = true;
    }
    if (!isSuperAdmin() && els['signature-edit-scope-level-input']) {
        Array.from(els['signature-edit-scope-level-input'].options).forEach((option) => {
            if (option.value === 'platform') option.hidden = true;
        });
    }
}

function bindEvents() {
    const reloadDebounced = debounce(() => loadSignatures({ keepSelection: false }));
    [
        'signature-search-input',
        'signature-scope-filter',
        'signature-identity-filter',
        'signature-owner-filter',
    ].forEach((id) => {
        const el = els[id];
        if (!el) return;
        el.addEventListener(id === 'signature-search-input' ? 'input' : 'change', reloadDebounced);
    });
    const schoolSearchDebounced = debounce(async () => {
        await fetchSchoolOptions(els['signature-school-search-input']?.value?.trim() || '');
    }, 220);
    els['signature-school-search-input']?.addEventListener('input', schoolSearchDebounced);
    els['signature-school-search-input']?.addEventListener('change', () => {
        state.selectedSchoolCode = schoolCodeFromInput(els['signature-school-search-input']?.value);
        loadSignatures({ keepSelection: false });
    });
    els['signature-school-search-input']?.addEventListener('keydown', (event) => {
        if (event.key !== 'Enter') return;
        event.preventDefault();
        state.selectedSchoolCode = schoolCodeFromInput(els['signature-school-search-input']?.value);
        loadSignatures({ keepSelection: false });
    });

    els['signature-clear-filter-btn']?.addEventListener('click', resetFilters);
    els['signature-refresh-btn']?.addEventListener('click', () => loadSignatures({ keepSelection: true }));
    els['signature-open-upload-btn']?.addEventListener('click', () => openModal('signature-upload-modal'));
    els['signature-file-input']?.addEventListener('change', updateFileLabel);
    els['signature-upload-form']?.addEventListener('submit', submitUpload);
    els['signature-edit-form']?.addEventListener('submit', submitEdit);
    els['signature-request-btn']?.addEventListener('click', requestCurrentSignatureUse);
    els['signature-claim-btn']?.addEventListener('click', claimCurrentSignature);
    els['signature-request-form']?.addEventListener('submit', submitSignatureRequest);
    els['signature-requests-refresh-btn']?.addEventListener('click', loadSignatureRequests);
    els['signature-request-list']?.addEventListener('click', (event) => {
        const button = event.target.closest?.('[data-signature-request-action]');
        if (!button) return;
        const item = button.closest('[data-signature-request-id]');
        const requestId = Number(item?.dataset.signatureRequestId || 0);
        reviewSignatureRequest(requestId, button.dataset.signatureRequestAction);
    });
    els['signature-outgoing-request-list']?.addEventListener('click', (event) => {
        const button = event.target.closest?.('[data-signature-request-action]');
        if (!button) return;
        const item = button.closest('[data-signature-request-id]');
        reviewSignatureRequest(Number(item?.dataset.signatureRequestId || 0), button.dataset.signatureRequestAction);
    });
    els['signature-edit-btn']?.addEventListener('click', openEditModal);
    els['signature-delete-btn']?.addEventListener('click', deleteCurrentSignature);
    els['signature-open-claim-btn']?.addEventListener('click', () => {
        openModal('signature-claim-modal');
        loadClaimCandidates();
    });
    els['signature-claim-search-input']?.addEventListener('input', debounce(loadClaimCandidates, 260));
    els['signature-claim-list']?.addEventListener('click', (event) => {
        const button = event.target.closest?.('[data-signature-claim-apply]');
        if (!button) return;
        applyClaim(Number(button.dataset.signatureClaimApply || 0), button);
    });
    els['signature-unbind-btn']?.addEventListener('click', unbindCurrentSignature);
    els['signature-batch-select-all']?.addEventListener('change', () => {
        const checked = Boolean(els['signature-batch-select-all']?.checked);
        document.querySelectorAll('input[data-signature-batch-check]').forEach((input) => {
            input.checked = checked;
        });
    });
    els['signature-batch-approve-btn']?.addEventListener('click', () => batchReviewRequests('approve'));
    els['signature-batch-reject-btn']?.addEventListener('click', () => batchReviewRequests('reject'));
    els['signature-replace-image-btn']?.addEventListener('click', () => els['signature-replace-image-input']?.click());
    els['signature-replace-image-input']?.addEventListener('change', () => {
        const file = els['signature-replace-image-input']?.files?.[0];
        if (file) replaceCurrentSignatureImage(file);
        if (els['signature-replace-image-input']) els['signature-replace-image-input'].value = '';
    });
    const ownerDebounced = debounce(() => fetchOwnerTeachers(els['signature-edit-owner-input']?.value?.trim() || ''), 220);
    els['signature-edit-owner-input']?.addEventListener('input', ownerDebounced);
    els['signature-edit-subject-input']?.addEventListener('input', debounce(() => fetchOwnerTeachers(els['signature-edit-subject-input']?.value?.trim() || ''), 220));
    els['signature-subject-account-input']?.addEventListener('input', debounce(() => fetchOwnerTeachers(els['signature-subject-account-input']?.value?.trim() || ''), 220));
    els['signature-edit-school-input']?.addEventListener('change', () => fetchOwnerTeachers(''));
}

document.addEventListener('click', (event) => {
    const trigger = event.target.closest?.('#signature-open-upload-btn');
    if (!trigger) return;
    event.preventDefault();
    openModal('signature-upload-modal');
});

document.addEventListener('DOMContentLoaded', () => {
    cacheElements();
    state.selectedSchoolCode = actorSchoolCode();
    renderSchoolControls({
        school_code: actorSchoolCode(),
        school_name: actorSchoolName(),
    });
    configureUploadFormForActor();
    bindEvents();
    loadSignatures({ keepSelection: false });
});
