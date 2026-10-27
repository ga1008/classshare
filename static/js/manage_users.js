import { createDomainModal } from './lq/domain-modal.js';
import { confirm as confirmDialog } from './lq/dialogs.js';
import { escapeHtml as esc } from './lq/html.js';
import { bindOrganizationSuggestions } from './organization_suggestions.js';

// One controller owns account drafts; LQ owns all layers, focus and scroll locks.
const $ = (id) => document.getElementById(id);
const initial = JSON.parse($('teacher-management-data').textContent);
const teachers = new Map(initial.teachers.map(teacher => [Number(teacher.id), teacher]));
const cards = new Map(), owners = new Map(), baselines = new WeakMap(), suggestions = new Map();
const currentUserId = Number(initial.currentTeacherId);
const createForm = $('teacher-create-form'), editForm = $('teacher-edit-form');
const membershipForm = $('teacher-membership-form'), passwordForm = $('teacher-password-form');
let current = null, detailGeneration = 0, detailLoaded = false;

const statusOf = t => t.account_status || (t.cancelled_at ? 'cancelled' : t.is_active ? 'active' : 'inactive');
const statusName = t => ({ active: '启用中', inactive: '已停用', cancelled: '已注销' })[statusOf(t)];
const status = (text, tone = 'neutral') => `<span class="lq-status" data-lq-status="badge" data-lq-tone-level="${tone}">${esc(String(text))}</span>`;
const button = (label, attrs, variant = 'glass', disabled = false) =>
    `<button type="button" data-lq-component="button" class="lq-btn lq-btn--sm lq-btn--${variant}" ${attrs}${disabled ? ' disabled' : ''}>${esc(label)}</button>`;
const organization = t => t.organization_label || [t.school_name, t.college, t.department].filter(Boolean).join(' / ') || '尚未填写任教归属';
const snapshot = form => JSON.stringify([...new FormData(form)].filter(([key]) => key !== 'expected_revision'));
const markClean = form => baselines.set(form, snapshot(form));
const isDirty = form => baselines.has(form) && baselines.get(form) !== snapshot(form);
const formError = (form, message = '') => {
    const node = form.querySelector('[data-form-error]');
    node.textContent = message; node.hidden = !message;
    if (message && !form.closest('.lq-dialog-root').hidden) node.scrollIntoView({ block: 'nearest' });
};
const notice = (message = '', tone = 'info') => {
    const node = $('teacher-detail-notice');
    node.textContent = message; node.hidden = !message; node.dataset.lqToneLevel = tone;
};
const formValues = (form, values) => {
    for (const element of form.elements) {
        if (!element.name || !(element.name in values)) continue;
        if (element.type === 'checkbox') element.checked = Boolean(values[element.name]);
        else element.value = values[element.name] ?? '';
    }
};
const formatTime = value => {
    if (!value) return '暂无';
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value).slice(0, 16) : date.toLocaleString('zh-CN', { hour12: false, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
};

for (const form of [createForm, editForm, membershipForm]) {
    suggestions.set(form, bindOrganizationSuggestions(form));
}
for (const [id, form] of [
    ['teacher-create-modal', createForm], ['teacher-detail-modal', editForm],
    ['teacher-membership-modal', membershipForm], ['teacher-password-modal', passwordForm],
]) {
    const root = $(id);
    let discardPending = false, discardApproved = false;
    const owner = createDomainModal(root, {
        surface: root.querySelector('.lq-dialog__surface'),
        ...(id === 'teacher-detail-modal' ? {
            returnFocus: () => {
                const trigger = cards.get(Number(current?.id));
                return trigger && !trigger.hidden ? trigger : $('teacher-search');
            },
        } : {}),
        beforeClose: () => {
            if (root.dataset.saving === 'true') return false;
            if (discardApproved || !isDirty(form)) return true;
            if (!discardPending) {
                discardPending = true;
                // Veto immediately: a human decision must not expire with the
                // layer's bounded async-close timeout.
                void confirmDialog({ title: '放弃未保存的修改？', message: '关闭后，本次尚未保存的内容会丢失。', confirmLabel: '放弃修改', cancelLabel: '继续编辑', danger: true }, { parentLayer: owner.handle })
                    .then(async approved => {
                        discardPending = false;
                        if (!approved) return;
                        discardApproved = true;
                        await owner.close('discarded');
                        discardApproved = false;
                    });
            }
            return false;
        },
        onClose: () => {
            if (id === 'teacher-detail-modal') { detailGeneration++; detailLoaded = false; }
            if (id === 'teacher-password-modal') { passwordForm.reset(); markClean(passwordForm); }
            if (id === 'teacher-create-modal') { createForm.reset(); markClean(createForm); }
        },
    });
    owners.set(id, owner);
    root.addEventListener('click', event => {
        if (event.target.classList.contains('lq-scrim')) void owner.close('backdrop');
    });
}
document.querySelectorAll('[data-close-modal]').forEach(node => node.addEventListener('click', () => {
    void owners.get(node.dataset.closeModal).close('button');
}));

function renderCards() {
    for (const [id, teacher] of teachers) {
        let card = cards.get(id);
        if (!card) {
            card = document.createElement('button');
            card.type = 'button'; card.className = 'lq-domain-choice lq-btn lq-btn--glass um-teacher-card';
            Object.assign(card.dataset, { lqComponent: 'choice', lqShape: 'surface', teacherCard: '', openTeacher: '', id: String(id) });
            card.addEventListener('click', () => void openDetail(id, card));
            cards.set(id, card); $('teacher-card-grid').append(card);
        }
        card.setAttribute('aria-label', `查看 ${teacher.name} 的用户详情`);
        card.innerHTML = `<span class="um-card-heading"><strong>${esc(teacher.name)}</strong>${status(statusName(teacher), statusOf(teacher) === 'active' ? 'success' : 'neutral')}</span>
            <span class="um-card-email">${esc(teacher.email)}</span>
            <span class="um-card-number">职工号 <strong>${esc(teacher.employee_number || '待补充')}</strong>${teacher.is_super_admin ? status('超管', 'primary') : ''}</span>
            <span class="um-card-organization">${esc(organization(teacher))}</span>
            ${(teacher.membership_count || 0) > 1 ? `<span class="text-muted text-sm">共 ${teacher.membership_count} 个任教归属</span>` : ''}
            <span class="um-card-counts"><span>班级 <b>${Number(teacher.class_count || 0)}</b></span><span>课程 <b>${Number(teacher.course_count || 0)}</b></span><span>材料 <b>${Number(teacher.material_count || 0)}</b></span></span>
            <span class="um-card-footer"><span>${teacher.pending_reset_count ? `找回待审 ${Number(teacher.pending_reset_count)}` : `更新 ${esc(formatTime(teacher.updated_at || teacher.created_at))}`}</span><span aria-hidden="true">查看详情 ↗</span></span>`;
    }
    const values = [...teachers.values()];
    const counts = { total: values.length, active: 0, inactive: 0, cancelled: 0, super: 0 };
    for (const t of values) { counts[statusOf(t)]++; if (t.is_active && t.is_super_admin) counts.super++; }
    document.querySelectorAll('[data-account-stat]').forEach(node => { node.textContent = counts[node.dataset.accountStat]; });
    applyFilters();
}
function applyFilters() {
    const query = $('teacher-search').value.trim().toLocaleLowerCase();
    const filter = $('teacher-status-filter').value;
    let count = 0;
    for (const [id, card] of cards) {
        const t = teachers.get(id);
        const haystack = [t.name, t.email, t.employee_number, t.phone, t.wechat, t.qq, organization(t),
            ...(t.memberships || []).map(m => `${organization(m)} ${m.employee_number || ''}`)].join(' ').toLocaleLowerCase();
        card.hidden = !(haystack.includes(query) && (filter === 'all' || filter === statusOf(t) || (filter === 'super' && t.is_active && t.is_super_admin)));
        if (!card.hidden) count++;
    }
    $('teacher-result-count').textContent = `${count} / ${teachers.size}`;
    $('teacher-empty-state').hidden = count !== 0;
}
$('teacher-search').addEventListener('input', applyFilters);
$('teacher-status-filter').addEventListener('change', applyFilters);

function renderMemberships(teacher) {
    const list = teacher.memberships || [], activeCount = list.filter(m => m.is_active).length;
    const canEdit = statusOf(teacher) === 'active';
    $('teacher-membership-list').innerHTML = list.length ? list.map(m => {
        const attrs = `data-membership-id="${Number(m.id)}"`;
        return `<article class="lq-surface um-membership-item" data-lq-component="surface" data-lq-padding="sm"${m.is_primary ? ' data-lq-selected="true"' : ''}>
            <div class="um-section-heading"><strong>${esc(organization(m))}</strong>${status(m.is_active ? m.is_primary ? '默认' : '启用' : '已停用', m.is_active ? 'success' : 'neutral')}</div>
            <p class="text-muted text-sm">职工号：${esc(m.employee_number || '待补充')}</p>
            <div class="um-membership-actions">${button(m.is_active ? '编辑归属' : '恢复并编辑', `${attrs} data-membership-action="edit"`, 'glass', !canEdit)}
            ${button('设为默认', `${attrs} data-membership-action="primary"`, 'glass', !canEdit || !m.is_active || m.is_primary)}
            ${button('停用归属', `${attrs} data-membership-action="delete"`, 'destructive', !canEdit || !m.is_active || activeCount <= 1)}</div></article>`;
    }).join('') : '<p class="text-muted">暂无任教归属。</p>';
}
function renderLinks(summary) {
    const node = $('teacher-timetable-links');
    const items = summary?.items || [];
    if (statusOf(current) !== 'active') {
        node.textContent = statusOf(current) === 'cancelled' ? '账号已注销，课表关联已失效；已同步课表仍保留。' : '账号已停用，课表关联暂时失效；启用后会重新匹配。';
        return;
    }
    if (!items.length) { node.textContent = '尚未匹配到已同步的教师课表。请核实学校与职工号；下次课表同步后会自动匹配。'; return; }
    node.innerHTML = items.map(item => {
        // Only platform-local URLs from the permission-checked API are navigable.
        const url = typeof item.query_url === 'string' && item.query_url.startsWith('/manage/') ? item.query_url : '';
        const linked = item.account_status === 'linked', ambiguous = item.account_status === 'ambiguous';
        const unavailable = ambiguous ? '存在多个身份匹配，请核实学校与工号。'
            : !linked ? '尚未匹配到已同步的教师目录，请核实学校与职工号。'
                : !item.can_view_school ? '当前账号没有该学校课表的查看权限。'
                    : item.query_supported === false ? '已匹配教师目录，该学校尚未开放课表查询。'
                        : '已匹配教师目录，具体课表尚未同步。';
        return `<article class="lq-surface um-membership-item" data-lq-component="surface" data-lq-padding="sm">
            <div class="um-section-heading"><strong>${esc(item.school_name || item.school_code)} · ${esc(item.name || item.teacher_no || item.employee_number)}</strong>${status(ambiguous ? '关联冲突' : linked ? '已关联' : '未匹配', ambiguous ? 'warning' : linked ? 'success' : 'neutral')}</div>
            <p class="text-muted text-sm">工号 ${esc(item.teacher_no || item.employee_number)}${item.college_name ? ` · ${esc(item.college_name)}` : ''}</p>
            ${item.can_view && url ? `<a class="lq-btn lq-btn--sm lq-btn--glass" data-lq-component="button" href="${esc(url)}" target="_blank" rel="noopener">查看教师课表<span class="sr-only">（新窗口）</span></a>` : `<p class="form-hint">${unavailable}</p>`}
        </article>`;
    }).join('');
}
function renderDetail(result) {
    current = result.teacher;
    teachers.set(Number(current.id), current);
    detailLoaded = true;
    $('teacher-detail-modal').dataset.detailTeacherId = String(current.id);
    $('teacher-detail-modal-title').textContent = `${current.name} · 用户详情`;
    $('teacher-detail-summary').innerHTML = `${status(statusName(current), current.is_active ? 'success' : 'neutral')}${status(current.is_super_admin ? '超管' : '普通教师')}<span class="text-muted text-sm">班级 ${Number(current.class_count || 0)} · 课程 ${Number(current.course_count || 0)} · 课堂 ${Number(current.offering_count || 0)} · 材料 ${Number(current.material_count || 0)}</span>`;
    formValues(editForm, { ...current, teacher_id: current.id, expected_revision: current.revision });
    $('edit-school-name').readOnly = true;
    const active = statusOf(current) === 'active', cancelled = statusOf(current) === 'cancelled';
    $('teacher-edit-fields').disabled = !active;
    $('teacher-detail-save').disabled = !active;
    formError(editForm); markClean(editForm);
    suggestions.get(editForm)?.refresh();
    renderMemberships(current); renderLinks(result.timetable_links);
    const self = Number(current.id) === currentUserId;
    const lastAdmin = current.is_super_admin && [...teachers.values()].filter(t => t.is_active && t.is_super_admin).length <= 1;
    const visibility = { memberships: !cancelled, reset: !cancelled, grant: !cancelled && !current.is_super_admin, revoke: !cancelled && current.is_super_admin, activate: !cancelled && !active, deactivate: active, cancel: !cancelled };
    document.querySelectorAll('[data-teacher-action]').forEach(node => {
        const action = node.dataset.teacherAction;
        node.hidden = !visibility[action];
        node.disabled = cancelled || (['memberships', 'reset', 'grant', 'revoke'].includes(action) && !active)
            || (['deactivate', 'cancel'].includes(action) && (self || lastAdmin)) || (action === 'revoke' && lastAdmin);
    });
    $('teacher-lifecycle-hint').textContent = cancelled ? '账号已注销，登录、权限和课表关联均已撤销。教学历史和原身份占用继续保留，不能直接恢复。'
        : self ? '当前登录账号不能停用或注销。至少保留一名启用中的超管。'
            : '停用可恢复，但不会自动恢复超管权限；注销不可直接恢复。两者都会使已有登录会话失效，教学历史保留。';
    $('teacher-save-status').textContent = active ? '保存后自动重新匹配课表' : cancelled ? '已注销 · 只读' : '启用后可编辑资料';
    renderCards();
}
async function openDetail(id, trigger, savedResult = null) {
    const generation = ++detailGeneration;
    current = teachers.get(Number(id)); detailLoaded = false;
    editForm.reset(); baselines.delete(editForm); formError(editForm);
    $('teacher-edit-fields').disabled = true; $('teacher-detail-save').disabled = true;
    $('teacher-detail-modal').dataset.detailTeacherId = String(id);
    $('teacher-detail-modal-title').textContent = `${current?.name || '教师'} · 用户详情`;
    for (const key of ['teacher-detail-summary', 'teacher-membership-list', 'teacher-timetable-links']) $(key).replaceChildren();
    document.querySelectorAll('[data-teacher-action]').forEach(node => { node.disabled = true; });
    notice('正在读取最新资料…');
    owners.get('teacher-detail-modal').open({ trigger });
    $('teacher-detail-modal').querySelector('.lq-dialog__body').scrollTop = 0;
    try {
        const result = savedResult || await window.apiFetch(`/api/manage/system/teachers/${id}`);
        if (generation !== detailGeneration) return;
        renderDetail(result); notice();
    } catch (error) {
        if (generation !== detailGeneration) return;
        notice(error.message || '读取失败，请点击重新载入。', 'danger');
    }
}
async function runBusy(form, operation) {
    const root = form.closest('.lq-dialog-root');
    if (root.dataset.saving === 'true') return null;
    root.dataset.saving = form.dataset.saving = 'true';
    root.setAttribute('aria-busy', 'true'); formError(form);
    const previousFocus = document.activeElement;
    const controls = [...root.querySelectorAll('input,select,textarea,button:not([data-close-modal])')];
    const previous = controls.map(node => node.disabled);
    controls.forEach(node => { node.disabled = true; });
    try { return await operation(); }
    catch (error) {
        formError(form, error.message || '操作失败，请保留当前内容后重试。');
        return null;
    } finally {
        controls.forEach((node, index) => { node.disabled = previous[index]; });
        delete root.dataset.saving; delete form.dataset.saving; root.removeAttribute('aria-busy');
        if (previousFocus?.isConnected && root.contains(previousFocus)
            && !previousFocus.matches(':disabled') && !previousFocus.closest('[hidden]')) {
            previousFocus.focus({ preventScroll: true });
        }
    }
}
function requireClean() {
    if (!detailLoaded || $('teacher-detail-modal').dataset.saving === 'true') return false;
    if (!isDirty(editForm)) return true;
    notice('基础信息有未保存修改，请先保存；也可以“重新载入”后继续其他操作。', 'warning');
    return false;
}
function mutationData(form = null) {
    const data = form ? new FormData(form) : new FormData();
    if (!form || form !== createForm) data.set('expected_revision', form?.elements.expected_revision?.value || current.revision);
    return data;
}
$('teacher-create-open').addEventListener('click', event => {
    createForm.reset(); formError(createForm); markClean(createForm);
    owners.get('teacher-create-modal').open({ trigger: event.currentTarget, initialFocus: $('create-name') });
    suggestions.get(createForm)?.refresh();
});
createForm.addEventListener('submit', async event => {
    event.preventDefault();
    const data = mutationData(createForm);
    const result = await runBusy(createForm, () => window.apiFetch('/api/manage/system/teachers', { method: 'POST', body: data }));
    if (!result) return;
    $('teacher-search').value = ''; $('teacher-status-filter').value = 'all';
    $('teacher-status-filter').dispatchEvent(new Event('change', { bubbles: true }));
    teachers.set(Number(result.teacher.id), result.teacher); renderCards(); markClean(createForm);
    await owners.get('teacher-create-modal').close('saved');
    await openDetail(result.teacher.id, cards.get(Number(result.teacher.id)), result);
    notice(result.message || '用户已创建，课表匹配结果如下。', 'success');
});
editForm.addEventListener('submit', async event => {
    event.preventDefault();
    if (!detailLoaded || statusOf(current) !== 'active') return;
    const data = mutationData(editForm);
    const result = await runBusy(editForm, () => window.apiFetch(`/api/manage/system/teachers/${current.id}`, { method: 'POST', body: data }));
    if (!result) return;
    renderDetail(result); notice(result.message || '资料已保存，课表关联已重新匹配。', 'success');
    if (result.requires_relogin) window.location.assign('/teacher/login');
});
$('teacher-detail-reload').addEventListener('click', async event => {
    if ($('teacher-detail-modal').dataset.saving === 'true') return;
    if (isDirty(editForm) && !(await confirmDialog({ title: '重新载入最新资料？', message: '当前未保存的修改会被丢弃。', confirmLabel: '重新载入', cancelLabel: '继续编辑', danger: true }))) return;
    await openDetail(current.id, owners.get('teacher-detail-modal').handle?.trigger || event.currentTarget);
});

function openMembership(member, trigger) {
    if (!requireClean()) return;
    membershipForm.reset();
    membershipForm.dataset.membershipId = member ? String(member.id) : '';
    formValues(membershipForm, { ...(member || {}), expected_revision: current.revision });
    $('membership-school-name').readOnly = Boolean(member);
    $('membership-teacher-id').value = current.id;
    $('membership-target-label').textContent = member ? `${current.name} · 编辑${member.is_active ? '' : '并恢复'}该学校归属。学校保持不变；新增学校请使用“新增归属”。` : `${current.name} · 新增任教归属`;
    formError(membershipForm); markClean(membershipForm);
    owners.get('teacher-membership-modal').open({ trigger, parentLayer: owners.get('teacher-detail-modal').handle, initialFocus: $('membership-school-name') });
    suggestions.get(membershipForm)?.refresh();
}
membershipForm.addEventListener('submit', async event => {
    event.preventDefault();
    const data = mutationData(membershipForm);
    if (!membershipForm.dataset.membershipId) {
        const name = String(data.get('school_name') || '').normalize('NFKC').trim();
        const code = String(data.get('school_code') || '').trim().toLowerCase();
        const existing = current.memberships.some(member =>
            (code && member.school_code === code) || member.school_name.normalize('NFKC').trim() === name);
        if (existing) {
            formError(membershipForm, '该学校已有任教归属，请返回并使用“编辑归属”或“恢复并编辑”，以免覆盖已有资料。');
            return;
        }
    }
    const result = await runBusy(membershipForm, () => window.apiFetch(`/api/manage/system/teachers/${current.id}/memberships`, { method: 'POST', body: data }));
    if (!result) return;
    markClean(membershipForm);
    await owners.get('teacher-membership-modal').close('saved');
    renderDetail(result); notice(result.message || '任教归属已保存。', 'success');
    $('teacher-detail-modal').querySelector('[data-teacher-action="memberships"]').focus();
    if (result.requires_relogin) window.location.assign('/teacher/login');
});
$('teacher-membership-list').addEventListener('click', async event => {
    const node = event.target.closest('[data-membership-action]');
    if (!node || node.disabled || !requireClean()) return;
    const member = current.memberships.find(m => Number(m.id) === Number(node.dataset.membershipId));
    if (!member) return;
    if (node.dataset.membershipAction === 'edit') { openMembership(member, node); return; }
    const removing = node.dataset.membershipAction === 'delete';
    if (!(await confirmDialog({ title: removing ? '停用任教归属？' : '设为默认任教归属？', message: removing ? '该学校的职工号登录及课表关联将失效，课表和教学历史继续保留。' : '默认学校及职工号将同步到个人信息，其他学校归属继续保留。', confirmLabel: removing ? '停用归属' : '设为默认', danger: removing }))) return;
    const data = mutationData();
    const result = await runBusy(editForm, () => window.apiFetch(`/api/manage/system/teachers/${current.id}/memberships/${member.id}${removing ? '' : '/primary'}`, { method: removing ? 'DELETE' : 'POST', body: data }));
    if (result) {
        renderDetail(result); notice(result.message || '任教归属已更新。', 'success');
        $('teacher-membership-list').querySelector(`[data-membership-id="${Number(member.id)}"][data-membership-action="edit"]`)?.focus({ preventScroll: true });
        if (result.requires_relogin) window.location.assign('/teacher/login');
    }
});
document.querySelectorAll('[data-teacher-action]').forEach(node => node.addEventListener('click', async () => {
    if (node.disabled || !requireClean()) return;
    const action = node.dataset.teacherAction;
    if (action === 'memberships') { openMembership(null, node); return; }
    if (action === 'reset') {
        passwordForm.reset(); formValues(passwordForm, { expected_revision: current.revision });
        $('password-teacher-id').value = current.id;
        $('password-target-label').textContent = `为 ${current.name} 设置新登录密码。`;
        formError(passwordForm); markClean(passwordForm);
        owners.get('teacher-password-modal').open({ trigger: node, parentLayer: owners.get('teacher-detail-modal').handle, initialFocus: $('reset-password') });
        return;
    }
    const actions = {
        deactivate: ['停用账号', '', 'DELETE', '将立即撤销登录与课表关联。教学历史保留，之后可重新启用。'],
        activate: ['启用账号', '/activate', 'POST', '恢复登录并重新匹配课表。超管权限需另行授予。'],
        cancel: ['注销账号', '/cancel', 'POST', '将撤销登录、权限和课表关联。课程、班级、材料及历史继续保留，邮箱与职工号保持占用，账号不能直接恢复。'],
        grant: ['授予超管', '/super-admin/grant', 'POST', '该用户将能够管理全平台账号和系统设置。'],
        revoke: ['撤销超管', '/super-admin/revoke', 'POST', '撤销管理权限，已有会话会失效，普通教学数据保留。'],
    };
    const [label, path, method, message] = actions[action];
    if (!(await confirmDialog({ title: `${label} · ${current.name}`, message, confirmLabel: label, danger: ['cancel', 'deactivate', 'revoke'].includes(action) }))) return;
    const data = mutationData();
    const result = await runBusy(editForm, () => window.apiFetch(`/api/manage/system/teachers/${current.id}${path}`, { method, body: data }));
    if (!result) return;
    renderDetail(result); notice(result.message || '操作已完成。', 'success');
    if (!node.hidden && !node.disabled) node.focus({ preventScroll: true });
    else $('teacher-detail-modal').querySelector('[data-close-modal]').focus({ preventScroll: true });
    if (result.requires_relogin) window.location.assign('/teacher/login');
}));
passwordForm.addEventListener('submit', async event => {
    event.preventDefault();
    const data = mutationData(passwordForm);
    const result = await runBusy(passwordForm, () => window.apiFetch(`/api/manage/system/teachers/${current.id}/reset-password`, { method: 'POST', body: data }));
    if (!result) return;
    markClean(passwordForm); await owners.get('teacher-password-modal').close('saved');
    renderDetail(result); notice(result.message || '密码已重置，原登录会话已失效。', 'success');
    if (result.requires_relogin) window.location.assign('/teacher/login');
});
editForm.addEventListener('input', () => {
    $('teacher-save-status').textContent = isDirty(editForm) ? '有未保存修改' : '保存后自动重新匹配课表';
});
window.addEventListener('beforeunload', event => {
    const dirty = [createForm, editForm, membershipForm, passwordForm].some(form => !form.closest('.lq-dialog-root').hidden && isDirty(form));
    if (dirty) { event.preventDefault(); event.returnValue = ''; }
});
renderCards();
