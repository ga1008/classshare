import { apiFetch } from './api.js';
import { closeModal, escapeHtml, formatDate, formatSize, openModal, showMessage } from './ui.js';

const state = {
    items: [],
    selectedId: null,
    actor: null,
    selectedSchoolCode: '',
    schoolOptions: [],
    ownerTeacherOptions: [],
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
    return matched?.id || '';
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
        'signature-edit-btn',
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
        'signature-edit-form',
        'signature-edit-name-input',
        'signature-edit-subject-name-input',
        'signature-edit-subject-role-input',
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
}

function signatureQuery() {
    const params = new URLSearchParams();
    const search = els['signature-search-input']?.value?.trim();
    const scope = els['signature-scope-filter']?.value;
    const subjectRole = els['signature-subject-filter']?.value;
    const ownerRole = els['signature-owner-filter']?.value;
    const schoolCode = state.selectedSchoolCode || schoolCodeFromInput(els['signature-school-search-input']?.value) || actorSchoolCode();
    if (search) params.set('q', search);
    if (schoolCode) params.set('school_code', schoolCode);
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
                    <span class="signature-chip">${escapeHtml(item.subject_role_label)}</span>
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
            ${item.is_owner ? '<span class="signature-chip is-owner">归属我</span>' : ''}
            ${item.owner_role === 'system' ? '<span class="signature-chip is-system">平台导入</span>' : ''}
        `;
    }
    if (els['signature-detail-list']) {
        els['signature-detail-list'].innerHTML = [
            ['签名人', item.subject_name || item.name],
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
    setActionVisibility(Boolean(item.can_use), Boolean(item.can_delete), Boolean(item.can_edit));
    if (els['signature-download-link']) {
        els['signature-download-link'].href = item.download_url || '#';
    }
}

function setActionVisibility(canUse, canDelete, canEdit = false) {
    if (els['signature-download-link']) els['signature-download-link'].hidden = !canUse;
    if (els['signature-use-btn']) els['signature-use-btn'].hidden = !canUse;
    if (els['signature-edit-btn']) els['signature-edit-btn'].hidden = !canEdit;
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

async function openEditModal() {
    const item = state.items.find((entry) => entry.id === state.selectedId);
    if (!item || !item.can_edit) return;
    if (els['signature-edit-name-input']) els['signature-edit-name-input'].value = item.name || '';
    if (els['signature-edit-subject-name-input']) els['signature-edit-subject-name-input'].value = item.subject_name || '';
    if (els['signature-edit-subject-role-input']) els['signature-edit-subject-role-input'].value = item.subject_role || 'teacher';
    if (els['signature-edit-scope-level-input']) els['signature-edit-scope-level-input'].value = item.scope_level || 'college';
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
            scope_level: els['signature-edit-scope-level-input']?.value || '',
            college: els['signature-edit-college-input']?.value?.trim() || '',
            department: els['signature-edit-department-input']?.value?.trim() || '',
            description: els['signature-edit-description-input']?.value?.trim() || '',
        };
        const ownerTeacherId = ownerTeacherIdFromInput(els['signature-edit-owner-input']?.value);
        if (ownerTeacherId) payload.owner_teacher_id = ownerTeacherId;
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
    ['signature-subject-role-field', 'signature-subject-name-field', 'signature-scope-level-field'].forEach((id) => {
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
        'signature-subject-filter',
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
    els['signature-use-btn']?.addEventListener('click', recordCurrentUse);
    els['signature-edit-btn']?.addEventListener('click', openEditModal);
    els['signature-delete-btn']?.addEventListener('click', deleteCurrentSignature);
    const ownerDebounced = debounce(() => fetchOwnerTeachers(els['signature-edit-owner-input']?.value?.trim() || ''), 220);
    els['signature-edit-owner-input']?.addEventListener('input', ownerDebounced);
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
