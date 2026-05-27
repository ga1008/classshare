import { apiFetch } from './api.js';
import { closeModal, escapeHtml, formatDate, formatSize, openModal, showMessage } from './ui.js';

const state = {
    items: [],
    selectedId: null,
    actor: null,
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

function cacheElements() {
    [
        'signature-search-input',
        'signature-scope-filter',
        'signature-subject-filter',
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
        'signature-use-btn',
        'signature-delete-btn',
        'signature-upload-form',
        'signature-file-input',
        'signature-file-label',
        'signature-upload-status',
        'signature-upload-submit-btn',
        'signature-subject-role-field',
        'signature-subject-name-field',
        'signature-scope-level-field',
        'signature-subject-role-input',
        'signature-subject-name-input',
        'signature-scope-level-input',
        'signature-name-input',
        'signature-description-input',
        'signature-stat-total',
        'signature-stat-mine',
        'signature-stat-college',
        'signature-stat-usage',
    ].forEach((id) => {
        els[id] = byId(id);
    });
}

function signatureQuery() {
    const params = new URLSearchParams();
    const search = els['signature-search-input']?.value?.trim();
    const scope = els['signature-scope-filter']?.value;
    const subjectRole = els['signature-subject-filter']?.value;
    const ownerRole = els['signature-owner-filter']?.value;
    if (search) params.set('q', search);
    if (scope) params.set('scope', scope);
    if (subjectRole) params.set('subject_role', subjectRole);
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
                    <span class="signature-chip">${escapeHtml(item.subject_role_label)}</span>
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
        setActionVisibility(false, false);
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
            ${item.owner_role === 'system' ? '<span class="signature-chip is-system">平台导入</span>' : ''}
        `;
    }
    if (els['signature-detail-list']) {
        els['signature-detail-list'].innerHTML = [
            ['签名人', item.subject_name || item.name],
            ['上传者', item.owner_name || '平台导入'],
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
    setActionVisibility(Boolean(item.can_use), Boolean(item.can_delete));
    if (els['signature-download-link']) {
        els['signature-download-link'].href = item.download_url || '#';
    }
}

function setActionVisibility(canUse, canDelete) {
    if (els['signature-download-link']) els['signature-download-link'].hidden = !canUse;
    if (els['signature-use-btn']) els['signature-use-btn'].hidden = !canUse;
    if (els['signature-delete-btn']) els['signature-delete-btn'].hidden = !canDelete;
}

async function recordCurrentUse() {
    if (!state.selectedId) return;
    try {
        await apiFetch(`/api/signatures/${state.selectedId}/use`, {
            method: 'POST',
            body: {
                action: 'use',
                context_type: 'signature_library',
                context_label: '管理中心签名库',
            },
        });
        showMessage('已记录本次签名调用', 'success');
        await loadSignatures({ keepSelection: true });
    } catch {
        // apiFetch already surfaces the error.
    }
}

async function deleteCurrentSignature() {
    if (!state.selectedId) return;
    const item = state.items.find((entry) => entry.id === state.selectedId);
    if (!item) return;
    if (!window.confirm(`确定删除“${item.name}”？删除后不会再出现在可用签名中。`)) {
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

function resetFilters() {
    [
        'signature-search-input',
        'signature-scope-filter',
        'signature-subject-filter',
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
    try {
        for (const file of files) {
            if (status) status.textContent = `正在上传 ${file.name}...`;
            const formData = new FormData();
            formData.append('file', file);
            const typedName = els['signature-name-input']?.value?.trim() || '';
            formData.append('name', files.length === 1 && typedName ? typedName : file.name.replace(/\.[^.]+$/, ''));
            formData.append('subject_role', els['signature-subject-role-input']?.value || '');
            formData.append('subject_name', els['signature-subject-name-input']?.value?.trim() || '');
            formData.append('scope_level', els['signature-scope-level-input']?.value || '');
            formData.append('description', els['signature-description-input']?.value?.trim() || '');
            try {
                await apiFetch('/api/signatures/upload', {
                    method: 'POST',
                    body: formData,
                    silent: true,
                });
                successCount += 1;
            } catch (error) {
                console.error('Signature upload failed:', error);
                failCount += 1;
            }
        }
        if (status) status.textContent = `上传完成：成功 ${successCount}，失败 ${failCount}`;
        showMessage(failCount ? `上传完成：${successCount} 成功，${failCount} 失败` : '签名上传成功', failCount ? 'warning' : 'success');
        els['signature-upload-form']?.reset();
        updateFileLabel();
        closeModal('signature-upload-modal');
        await loadSignatures({ keepSelection: false });
    } finally {
        if (submitBtn) submitBtn.disabled = false;
    }
}

function configureUploadFormForActor() {
    const isSuperAdmin = document.querySelector('[data-signature-page]')?.dataset.isSuperAdmin === '1';
    ['signature-subject-role-field', 'signature-subject-name-field', 'signature-scope-level-field'].forEach((id) => {
        if (els[id]) els[id].hidden = !isSuperAdmin;
    });
}

function bindEvents() {
    const reloadDebounced = debounce(() => loadSignatures({ keepSelection: false }));
    [
        'signature-search-input',
        'signature-scope-filter',
        'signature-subject-filter',
        'signature-owner-filter',
    ].forEach((id) => {
        const el = els[id];
        if (!el) return;
        el.addEventListener(id === 'signature-search-input' ? 'input' : 'change', reloadDebounced);
    });

    els['signature-clear-filter-btn']?.addEventListener('click', resetFilters);
    els['signature-refresh-btn']?.addEventListener('click', () => loadSignatures({ keepSelection: true }));
    els['signature-open-upload-btn']?.addEventListener('click', () => openModal('signature-upload-modal'));
    els['signature-file-input']?.addEventListener('change', updateFileLabel);
    els['signature-upload-form']?.addEventListener('submit', submitUpload);
    els['signature-use-btn']?.addEventListener('click', recordCurrentUse);
    els['signature-delete-btn']?.addEventListener('click', deleteCurrentSignature);
}

document.addEventListener('click', (event) => {
    const trigger = event.target.closest?.('#signature-open-upload-btn');
    if (!trigger) return;
    event.preventDefault();
    openModal('signature-upload-modal');
});

document.addEventListener('DOMContentLoaded', () => {
    cacheElements();
    configureUploadFormForActor();
    bindEvents();
    loadSignatures({ keepSelection: false });
});
