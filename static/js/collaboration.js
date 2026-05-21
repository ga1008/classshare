import { apiFetch } from './api.js';
import { showToast, escapeHtml, formatSize, formatDate } from './ui.js';

const SCORE_OPTIONS = [5, 4, 3, 2, 1];
const DETAIL_TABS = [
    { key: 'members', label: '成员' },
    { key: 'files', label: '文件' },
    { key: 'submission', label: '成果' },
    { key: 'reviews', label: '互评' },
];

function normalizeId(value) {
    const text = String(value ?? '').trim();
    return text || '';
}

function selectGroup(snapshot, selectedId) {
    const groups = snapshot?.groups || [];
    if (!groups.length) return null;
    const exact = groups.find((group) => String(group.id) === String(selectedId));
    if (exact) return exact;
    return groups.find((group) => group.my_membership) || groups[0];
}

function assignmentOptions(snapshot, selected = '') {
    const options = [`<option value="">不绑定具体任务</option>`];
    (snapshot.assignments || []).forEach((item) => {
        options.push(`<option value="${escapeHtml(item.id)}"${String(item.id) === String(selected) ? ' selected' : ''}>${escapeHtml(item.title)}</option>`);
    });
    return options.join('');
}

function studentOptions(snapshot, selected = '') {
    return (snapshot.students || [])
        .map((student) => `<option value="${student.id}"${String(student.id) === String(selected) ? ' selected' : ''}>${escapeHtml(student.name)}${student.student_id_number ? ` · ${escapeHtml(student.student_id_number)}` : ''}</option>`)
        .join('');
}

function scoreSelect(name, label) {
    return `
        <label class="collaboration-form-field">
            <span>${label}</span>
            <select name="${name}" required>
                ${SCORE_OPTIONS.map((score) => `<option value="${score}">${score} 分</option>`).join('')}
            </select>
        </label>
    `;
}

function statusLabel(group) {
    if (group.status === 'archived') return '已归档';
    if (group.join_policy === 'open') return '开放加入';
    if (group.join_policy === 'teacher_assigned') return '教师分配';
    return '锁定';
}

function normalizeDetailTab(value) {
    const key = normalizeId(value);
    return DETAIL_TABS.some((item) => item.key === key) ? key : 'members';
}

function detailTabCount(group, key) {
    if (key === 'members') return group.member_count || 0;
    if (key === 'files') return group.file_count || 0;
    if (key === 'submission') return group.submission_count || 0;
    if (key === 'reviews') return (group.peer_reviews || []).length;
    return 0;
}

function renderStats(snapshot) {
    const summary = snapshot.summary || {};
    const role = snapshot.role;
    const items = role === 'teacher'
        ? [
            ['活跃小组', summary.group_count || 0, '课堂协作空间'],
            ['成果提交', summary.submission_count || 0, '组长归档记录'],
            ['组内文件', summary.file_count || 0, '可追溯材料'],
            ['互评待看', summary.pending_peer_review_count || 0, '学生互评进展'],
        ]
        : [
            ['我的小组', summary.my_group_count || 0, '当前参与'],
            ['待互评', summary.pending_peer_review_count || 0, '需要补完'],
            ['组内文件', summary.file_count || 0, '资料沉淀'],
            ['成果提交', summary.submission_count || 0, '小组归档'],
        ];
    return `
        <div class="collaboration-stat-grid">
            ${items.map(([label, value, note]) => `
                <article class="collaboration-stat-card">
                    <span>${label}</span>
                    <strong>${value}</strong>
                    <small>${note}</small>
                </article>
            `).join('')}
        </div>
    `;
}

function renderGroupCard(group, selectedGroup) {
    const isSelected = selectedGroup && String(selectedGroup.id) === String(group.id);
    const title = group.assignment_title || '自主学习小组';
    return `
        <article class="collaboration-group-card${isSelected ? ' is-selected' : ''}" data-collab-group-card="${group.id}">
            <button type="button" class="collaboration-group-card__body" data-collab-select-group="${group.id}">
                <span class="collaboration-group-card__status">${escapeHtml(statusLabel(group))}</span>
                <strong>${escapeHtml(group.name)}</strong>
                <small>${escapeHtml(title)}</small>
                <p>${escapeHtml(group.description || '这个小组还没有补充说明。')}</p>
                <span class="collaboration-group-card__meta">${group.member_count}/${group.max_members} 人 · 文件 ${group.file_count} · 成果 ${group.submission_count}</span>
            </button>
            <div class="collaboration-group-card__actions">
                ${group.can_join ? `<button type="button" class="btn btn-primary btn-sm" data-collab-join="${group.id}">加入</button>` : ''}
                ${group.can_leave ? `<button type="button" class="btn btn-outline btn-sm" data-collab-leave="${group.id}">退出</button>` : ''}
            </div>
        </article>
    `;
}

function renderGroupList(snapshot, selectedGroup) {
    const groups = snapshot.groups || [];
    if (!groups.length) {
        return `
            <div class="collaboration-empty">
                <strong>还没有小组</strong>
                <p>${snapshot.role === 'teacher' ? '先创建一个小组，或让学生发起开放小组。' : '可以先发起一个学习小组，也可以等待教师分配。'}</p>
            </div>
        `;
    }
    const mine = groups.filter((group) => group.my_membership || snapshot.role === 'teacher');
    const joinable = groups.filter((group) => group.can_join);
    return `
        <div class="collaboration-group-list">
            <div class="collaboration-group-list__head">
                <div>
                    <strong>${snapshot.role === 'teacher' ? '课堂小组' : '我的协作'}</strong>
                    <span>${mine.length} 个可查看小组</span>
                </div>
            </div>
            <div class="collaboration-group-grid">
                ${(mine.length ? mine : groups).map((group) => renderGroupCard(group, selectedGroup)).join('')}
            </div>
            ${joinable.length ? `
                <div class="collaboration-joinable">
                    <strong>可加入小组</strong>
                    <div class="collaboration-group-grid">
                        ${joinable.map((group) => renderGroupCard(group, selectedGroup)).join('')}
                    </div>
                </div>
            ` : ''}
        </div>
    `;
}

function renderMembers(snapshot, group) {
    const currentRole = snapshot.role;
    const removeButton = (member) => (
        currentRole === 'teacher'
            ? `<button type="button" class="collaboration-member-remove" data-collab-remove-member="${group.id}" data-student-id="${member.student_id}" aria-label="移出${escapeHtml(member.name)}">移出</button>`
            : ''
    );
    const members = (group.members || []).map((member) => `
        <span class="collaboration-member-pill${member.member_role === 'leader' ? ' is-leader' : ''}">
            <strong>${escapeHtml(member.name)}</strong>
            <small>${member.member_role === 'leader' ? '组长' : '成员'}</small>
            ${removeButton(member)}
        </span>
    `).join('');

    return `
        <section class="collaboration-detail-card">
            <div class="collaboration-detail-card__head">
                <div>
                    <strong>成员与分工</strong>
                    <span>${group.member_count}/${group.max_members} 人</span>
                </div>
            </div>
            <div class="collaboration-member-list">
                ${members || '<span class="collaboration-muted">暂时没有成员。</span>'}
            </div>
            ${snapshot.role === 'teacher' && group.can_manage ? `
                <form class="collaboration-inline-form" data-collab-add-member="${group.id}">
                    <select name="student_id" required>
                        <option value="">选择学生加入小组</option>
                        ${studentOptions(snapshot)}
                    </select>
                    <button type="submit" class="btn btn-outline btn-sm">加入</button>
                </form>
            ` : ''}
        </section>
    `;
}

function renderFiles(group) {
    const files = group.files || [];
    return `
        <section class="collaboration-detail-card">
            <div class="collaboration-detail-card__head">
                <div>
                    <strong>组内文件</strong>
                    <span>${group.file_count} 个证据材料</span>
                </div>
            </div>
            <div class="collaboration-file-list">
                ${files.length ? files.map((file) => `
                    <a class="collaboration-file-row" href="${escapeHtml(file.download_url)}">
                        <span class="collaboration-file-icon" aria-hidden="true"></span>
                        <span>
                            <strong>${escapeHtml(file.name)}</strong>
                            <small>${escapeHtml(file.uploaded_by_name || '成员')} · ${formatSize(file.file_size)} · ${formatDate(file.created_at)}</small>
                        </span>
                    </a>
                `).join('') : '<p class="collaboration-muted">还没有上传组内文件。</p>'}
            </div>
            ${group.can_upload ? `
                <form class="collaboration-upload-form" data-collab-upload="${group.id}">
                    <label class="collaboration-file-drop">
                        <input type="file" name="file" required>
                        <span>选择文件</span>
                        <small>实验截图、报告草稿、项目代码压缩包等，单个不超过 100MB。</small>
                    </label>
                    <input type="text" name="description" placeholder="文件说明，可选">
                    <button type="submit" class="btn btn-primary btn-sm">上传</button>
                </form>
            ` : ''}
        </section>
    `;
}

function renderSubmissions(snapshot, group) {
    const submissions = group.submissions || [];
    const latest = submissions[0] || {};
    return `
        <section class="collaboration-detail-card">
            <div class="collaboration-detail-card__head">
                <div>
                    <strong>组长提交</strong>
                    <span>${submissions.length ? `最近更新 ${formatDate(latest.updated_at)}` : '尚未提交'}</span>
                </div>
            </div>
            ${submissions.length ? `
                <div class="collaboration-submission-list">
                    ${submissions.map((item) => `
                        <article class="collaboration-submission-row">
                            <strong>${escapeHtml(item.title || '小组成果')}</strong>
                            <small>${escapeHtml(item.assignment_title || '自主成果')} · ${formatDate(item.updated_at)}</small>
                            <p>${escapeHtml(item.summary_md || '暂无说明')}</p>
                            ${item.final_file_name ? `<span>最终文件：${escapeHtml(item.final_file_name)}</span>` : ''}
                            ${item.blog_url ? `<a class="path-source-link" href="${escapeHtml(item.blog_url)}">打开博客草稿</a>` : ''}
                        </article>
                    `).join('')}
                </div>
            ` : '<p class="collaboration-muted">组长可以在这里整理最终说明和归档文件。</p>'}
            ${group.can_submit ? `
                <form class="collaboration-submission-form" data-collab-submit-work="${group.id}">
                    <input type="text" name="title" value="${escapeHtml(latest.title || group.name)}" placeholder="成果标题" required>
                    <select name="assignment_id">
                        ${assignmentOptions(snapshot, latest.assignment_id || group.assignment_id)}
                    </select>
                    <select name="final_file_id">
                        <option value="">不指定最终文件</option>
                        ${(group.files || []).map((file) => `<option value="${file.id}"${String(file.id) === String(latest.final_file_id || '') ? ' selected' : ''}>${escapeHtml(file.name)}</option>`).join('')}
                    </select>
                    <textarea name="summary_md" rows="4" placeholder="写清楚本组完成了什么、谁负责了什么、还有什么待改进">${escapeHtml(latest.summary_md || '')}</textarea>
                    <div class="collaboration-submission-actions">
                        <button type="submit" class="btn btn-primary btn-sm">保存成果</button>
                        ${latest.id ? `<button type="button" class="btn btn-outline btn-sm" data-collab-blog-draft="${group.id}" data-submission-id="${latest.id}">${latest.blog_url ? '更新博客草稿入口' : '生成博客草稿'}</button>` : ''}
                    </div>
                </form>
            ` : ''}
        </section>
    `;
}

function renderPeerReviews(snapshot, group) {
    if (snapshot.role === 'teacher') {
        return `
            <section class="collaboration-detail-card">
                <div class="collaboration-detail-card__head">
                    <div>
                        <strong>互评概览</strong>
                        <span>${(group.peer_reviews || []).length} 条评价</span>
                    </div>
                </div>
                <div class="collaboration-peer-summary">
                    ${(group.peer_summary || []).map((item) => `
                        <div>
                            <span>${escapeHtml(item.name)}</span>
                            <strong>${item.average_score || '--'}</strong>
                            <small>${item.review_count} 条</small>
                        </div>
                    `).join('') || '<p class="collaboration-muted">学生还没有提交互评。</p>'}
                </div>
                <div class="collaboration-review-list">
                    ${(group.peer_reviews || []).slice(0, 8).map((review) => `
                        <article>
                            <strong>${escapeHtml(review.reviewer_name)} → ${escapeHtml(review.reviewee_name)}</strong>
                            <small>平均 ${review.average_score} 分 · ${formatDate(review.updated_at)}</small>
                            <p>${escapeHtml(review.comment || '未填写文字反馈')}</p>
                        </article>
                    `).join('')}
                </div>
            </section>
        `;
    }

    const currentUserId = Number(window.APP_CONFIG?.userInfo?.id || 0);
    const reviewTargets = (group.members || []).filter((member) => Number(member.student_id) !== currentUserId);
    return `
        <section class="collaboration-detail-card">
            <div class="collaboration-detail-card__head">
                <div>
                    <strong>同伴互评</strong>
                    <span>${group.can_review ? '给组员留下可执行反馈' : '需要至少两名成员'}</span>
                </div>
            </div>
            <div class="collaboration-review-list">
                ${(group.peer_reviews || []).map((review) => `
                    <article>
                        <strong>${escapeHtml(review.reviewer_name)} → ${escapeHtml(review.reviewee_name)}</strong>
                        <small>平均 ${review.average_score} 分 · ${formatDate(review.updated_at)}</small>
                        <p>${escapeHtml(review.comment || '已完成评分')}</p>
                    </article>
                `).join('') || '<p class="collaboration-muted">还没有互评记录。</p>'}
            </div>
            ${group.can_review ? `
                <form class="collaboration-review-form" data-collab-peer-review="${group.id}">
                    <label class="collaboration-form-field">
                        <span>评价对象</span>
                        <select name="reviewee_student_id" required>
                            <option value="">选择组员</option>
                            ${reviewTargets.map((member) => `<option value="${member.student_id}">${escapeHtml(member.name)}</option>`).join('')}
                        </select>
                    </label>
                    <div class="collaboration-score-grid">
                        ${scoreSelect('responsibility_score', '责任投入')}
                        ${scoreSelect('collaboration_score', '协作沟通')}
                        ${scoreSelect('quality_score', '贡献质量')}
                    </div>
                    <textarea name="comment" rows="3" placeholder="写一句具体反馈：对方做得好的地方、可以继续改进的地方"></textarea>
                    <label class="collaboration-checkbox">
                        <input type="checkbox" name="share_with_reviewee" value="1">
                        <span>允许对方看到这条文字反馈</span>
                    </label>
                    <button type="submit" class="btn btn-primary btn-sm">提交互评</button>
                </form>
            ` : ''}
        </section>
    `;
}

function renderCreateForm(snapshot, open) {
    if (!open) return '';
    const isTeacher = snapshot.role === 'teacher';
    return `
        <section class="collaboration-create-panel">
            <div class="collaboration-detail-card__head">
                <div>
                    <strong>${isTeacher ? '创建课堂小组' : '发起学习小组'}</strong>
                    <span>${isTeacher ? '可直接分配成员与组长' : '先创建开放小组，再邀请同学加入'}</span>
                </div>
                <button type="button" class="collaboration-close-btn" data-collab-create-close aria-label="关闭">×</button>
            </div>
            <form class="collaboration-create-form" data-collab-create-form>
                <input name="name" type="text" placeholder="小组名称，例如：网络实验 A 组" required maxlength="60">
                <select name="assignment_id">${assignmentOptions(snapshot)}</select>
                <textarea name="description" rows="3" placeholder="小组目标、分工建议或约定"></textarea>
                <div class="collaboration-form-row">
                    <label class="collaboration-form-field">
                        <span>人数上限</span>
                        <input name="max_members" type="number" min="2" max="${snapshot.limits?.max_group_members || 12}" value="6">
                    </label>
                    ${isTeacher ? `
                        <label class="collaboration-form-field">
                            <span>加入方式</span>
                            <select name="join_policy">
                                <option value="teacher_assigned">教师分配</option>
                                <option value="open">开放加入</option>
                                <option value="locked">锁定</option>
                            </select>
                        </label>
                    ` : ''}
                </div>
                ${isTeacher ? `
                    <label class="collaboration-form-field">
                        <span>初始成员</span>
                        <select name="member_student_ids" multiple size="6">${studentOptions(snapshot)}</select>
                    </label>
                    <label class="collaboration-form-field">
                        <span>组长</span>
                        <select name="leader_student_id">
                            <option value="">稍后指定</option>
                            ${studentOptions(snapshot)}
                        </select>
                    </label>
                ` : ''}
                <button type="submit" class="btn btn-primary btn-sm">${isTeacher ? '创建小组' : '发起小组'}</button>
            </form>
        </section>
    `;
}

function renderDetailTabs(group, activeTab) {
    return `
        <div class="collaboration-detail-tabs" role="tablist" aria-label="小组工作区">
            ${DETAIL_TABS.map((tab) => `
                <button
                    type="button"
                    class="collaboration-detail-tab${activeTab === tab.key ? ' is-active' : ''}"
                    data-collab-detail-tab="${tab.key}"
                    role="tab"
                    aria-selected="${activeTab === tab.key ? 'true' : 'false'}"
                >
                    <span>${tab.label}</span>
                    <strong>${detailTabCount(group, tab.key)}</strong>
                </button>
            `).join('')}
        </div>
    `;
}

function renderDetailPanel(snapshot, group, activeTab) {
    if (activeTab === 'files') return renderFiles(group);
    if (activeTab === 'submission') return renderSubmissions(snapshot, group);
    if (activeTab === 'reviews') return renderPeerReviews(snapshot, group);
    return renderMembers(snapshot, group);
}

function renderDetail(snapshot, group, activeTab = 'members') {
    if (!group) {
        return `
            <aside class="collaboration-detail">
                <div class="collaboration-empty">
                    <strong>选择一个小组</strong>
                    <p>小组的成员、文件、成果和互评会在这里展开。</p>
                </div>
            </aside>
        `;
    }
    const normalizedTab = normalizeDetailTab(activeTab);
    return `
        <aside class="collaboration-detail">
            <div class="collaboration-detail-hero">
                <span>${escapeHtml(statusLabel(group))}</span>
                <strong>${escapeHtml(group.name)}</strong>
                <p>${escapeHtml(group.assignment_title || '未绑定具体任务')}</p>
            </div>
            <div class="collaboration-detail-summary" aria-label="小组概览">
                <div><span>成员</span><strong>${group.member_count || 0}/${group.max_members || 0}</strong></div>
                <div><span>文件</span><strong>${group.file_count || 0}</strong></div>
                <div><span>成果</span><strong>${group.submission_count || 0}</strong></div>
                <div><span>互评</span><strong>${(group.peer_reviews || []).length}</strong></div>
            </div>
            ${renderDetailTabs(group, normalizedTab)}
            <div class="collaboration-detail-panel" role="tabpanel">
                ${renderDetailPanel(snapshot, group, normalizedTab)}
            </div>
        </aside>
    `;
}

function snapshotFromResponse(response) {
    return response?.snapshot || response?.data?.snapshot || response;
}

function applySnapshot(state, response) {
    const snapshot = snapshotFromResponse(response);
    if (snapshot && snapshot.groups) {
        state.snapshot = snapshot;
        const selected = selectGroup(snapshot, state.selectedGroupId);
        state.selectedGroupId = selected ? String(selected.id) : '';
    }
}

function render(root, state) {
    const loading = root.querySelector('[data-collab-loading]');
    const content = root.querySelector('[data-collab-content]');
    if (!state.snapshot) {
        if (loading) loading.hidden = false;
        if (content) content.hidden = true;
        return;
    }
    if (loading) loading.hidden = true;
    if (!content) return;

    const selectedGroup = selectGroup(state.snapshot, state.selectedGroupId);
    state.selectedGroupId = selectedGroup ? String(selectedGroup.id) : '';
    content.hidden = false;
    content.innerHTML = `
        ${renderStats(state.snapshot)}
        ${renderCreateForm(state.snapshot, state.createOpen)}
        <div class="collaboration-workbench">
            <div class="collaboration-main">
                ${renderGroupList(state.snapshot, selectedGroup)}
            </div>
            ${renderDetail(state.snapshot, selectedGroup, state.detailTab)}
        </div>
    `;
}

async function refresh(root, state, silent = false) {
    const classOfferingId = root.dataset.classOfferingId || window.APP_CONFIG?.classOfferingId;
    const response = await apiFetch(`/api/collaboration/classrooms/${classOfferingId}/snapshot`, { silent });
    applySnapshot(state, response);
    render(root, state);
}

function formValues(form) {
    const data = new FormData(form);
    return Object.fromEntries(data.entries());
}

function selectedValues(select) {
    return Array.from(select?.selectedOptions || []).map((option) => Number(option.value)).filter(Boolean);
}

async function handleCreate(root, state, form) {
    const classOfferingId = root.dataset.classOfferingId || window.APP_CONFIG?.classOfferingId;
    const values = formValues(form);
    const memberSelect = form.querySelector('[name="member_student_ids"]');
    const payload = {
        name: values.name,
        description: values.description,
        assignment_id: values.assignment_id,
        max_members: Number(values.max_members || 6),
        join_policy: values.join_policy || 'open',
        leader_student_id: values.leader_student_id || null,
        member_student_ids: selectedValues(memberSelect),
    };
    const response = await apiFetch(`/api/collaboration/classrooms/${classOfferingId}/groups`, {
        method: 'POST',
        body: payload,
    });
    state.createOpen = false;
    applySnapshot(state, response);
    state.detailTab = 'members';
    showToast(response.message || '小组已创建', 'success');
    render(root, state);
}

async function postAction(root, state, endpoint, message) {
    const response = await apiFetch(endpoint, { method: 'POST' });
    applySnapshot(state, response);
    showToast(response.message || message, 'success');
    render(root, state);
}

async function deleteAction(root, state, endpoint, message) {
    const response = await apiFetch(endpoint, { method: 'DELETE' });
    applySnapshot(state, response);
    showToast(response.message || message, 'success');
    render(root, state);
}

async function handleAddMember(root, state, form, groupId) {
    const values = formValues(form);
    if (!values.student_id) {
        showToast('请先选择学生', 'warning');
        return;
    }
    const response = await apiFetch(`/api/collaboration/groups/${groupId}/members`, {
        method: 'POST',
        body: { student_id: Number(values.student_id) },
    });
    applySnapshot(state, response);
    state.detailTab = 'members';
    showToast(response.message || '成员已加入', 'success');
    render(root, state);
}

async function handleUpload(root, state, form, groupId) {
    const input = form.querySelector('input[type="file"]');
    const file = input?.files?.[0];
    if (!file) {
        showToast('请选择文件', 'warning');
        return;
    }
    const data = new FormData(form);
    const response = await apiFetch(`/api/collaboration/groups/${groupId}/files`, {
        method: 'POST',
        body: data,
    });
    applySnapshot(state, response);
    state.detailTab = 'files';
    showToast(response.message || '文件已上传', 'success');
    render(root, state);
}

async function handleSubmission(root, state, form, groupId) {
    const payload = formValues(form);
    const response = await apiFetch(`/api/collaboration/groups/${groupId}/submission`, {
        method: 'PUT',
        body: payload,
    });
    applySnapshot(state, response);
    state.detailTab = 'submission';
    showToast(response.message || '成果已保存', 'success');
    render(root, state);
}

async function handlePeerReview(root, state, form, groupId) {
    const payload = formValues(form);
    payload.share_with_reviewee = form.querySelector('[name="share_with_reviewee"]')?.checked || false;
    ['responsibility_score', 'collaboration_score', 'quality_score', 'reviewee_student_id'].forEach((key) => {
        payload[key] = Number(payload[key] || 0);
    });
    const response = await apiFetch(`/api/collaboration/groups/${groupId}/peer-reviews`, {
        method: 'POST',
        body: payload,
    });
    applySnapshot(state, response);
    state.detailTab = 'reviews';
    showToast(response.message || '互评已保存', 'success');
    render(root, state);
}

async function handleBlogDraft(root, state, groupId, submissionId) {
    const response = await apiFetch(`/api/collaboration/groups/${groupId}/blog-draft`, {
        method: 'POST',
        body: { submission_id: Number(submissionId || 0) },
    });
    applySnapshot(state, response);
    state.detailTab = 'submission';
    showToast(response.message || '博客草稿已生成', 'success');
    render(root, state);
}

function bindEvents(root, state) {
    root.addEventListener('click', async (event) => {
        const target = event.target.closest('button, a');
        if (!target) return;
        try {
            if (target.matches('[data-collab-refresh]')) {
                await refresh(root, state);
                showToast('协作区已刷新', 'success');
            } else if (target.matches('[data-collab-create-open]')) {
                state.createOpen = true;
                render(root, state);
            } else if (target.matches('[data-collab-create-close]')) {
                state.createOpen = false;
                render(root, state);
            } else if (target.dataset.collabDetailTab) {
                state.detailTab = normalizeDetailTab(target.dataset.collabDetailTab);
                render(root, state);
            } else if (target.dataset.collabSelectGroup) {
                const nextGroupId = target.dataset.collabSelectGroup;
                if (nextGroupId !== state.selectedGroupId) {
                    state.detailTab = 'members';
                }
                state.selectedGroupId = nextGroupId;
                render(root, state);
            } else if (target.dataset.collabJoin) {
                await postAction(root, state, `/api/collaboration/groups/${target.dataset.collabJoin}/join`, '已加入小组');
            } else if (target.dataset.collabLeave) {
                await postAction(root, state, `/api/collaboration/groups/${target.dataset.collabLeave}/leave`, '已退出小组');
            } else if (target.dataset.collabRemoveMember) {
                await deleteAction(
                    root,
                    state,
                    `/api/collaboration/groups/${target.dataset.collabRemoveMember}/members/${target.dataset.studentId}`,
                    '成员已移出',
                );
            } else if (target.dataset.collabBlogDraft) {
                await handleBlogDraft(root, state, target.dataset.collabBlogDraft, target.dataset.submissionId);
            }
        } catch (error) {
            showToast(error.message || '协作操作失败', 'error');
        }
    });

    root.addEventListener('submit', async (event) => {
        const form = event.target;
        try {
            if (form.matches('[data-collab-create-form]')) {
                event.preventDefault();
                await handleCreate(root, state, form);
            } else if (form.dataset.collabAddMember) {
                event.preventDefault();
                await handleAddMember(root, state, form, form.dataset.collabAddMember);
            } else if (form.dataset.collabUpload) {
                event.preventDefault();
                await handleUpload(root, state, form, form.dataset.collabUpload);
            } else if (form.dataset.collabSubmitWork) {
                event.preventDefault();
                await handleSubmission(root, state, form, form.dataset.collabSubmitWork);
            } else if (form.dataset.collabPeerReview) {
                event.preventDefault();
                await handlePeerReview(root, state, form, form.dataset.collabPeerReview);
            }
        } catch (error) {
            showToast(error.message || '协作操作失败', 'error');
        }
    });
}

export function initCollaborationPanel() {
    const root = document.querySelector('[data-collaboration-root]');
    if (!root) return;
    const state = {
        snapshot: null,
        selectedGroupId: '',
        detailTab: 'members',
        createOpen: false,
    };
    bindEvents(root, state);
    refresh(root, state, true).catch((error) => {
        const loading = root.querySelector('[data-collab-loading]');
        if (loading) {
            loading.textContent = '协作区加载失败，请稍后刷新。';
        }
        showToast(error.message || '协作区加载失败', 'error');
    });
}
