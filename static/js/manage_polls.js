import { apiFetch } from './api.js';
import { showToast, escapeHtml, formatDate } from './ui.js';

const STATUS_META = {
    draft: { label: '草稿', tone: 'is-draft' },
    active: { label: '进行中', tone: 'is-active' },
    closed: { label: '已结束', tone: 'is-closed' },
};

const VISIBILITY_OPTIONS = [
    { value: 'always', label: '任何时间' },
    { value: 'after_vote', label: '参与投票后' },
    { value: 'after_close', label: '截止后' },
];

const DEFAULT_OPTIONS = ['选项一', '选项二'];
const MAX_OPTIONS = 12;

function statusMeta(status) {
    return STATUS_META[status] || STATUS_META.draft;
}

function safePercent(value) {
    const number = Number(value || 0);
    if (!Number.isFinite(number)) return 0;
    return Math.max(0, Math.min(100, number));
}

function deadlineText(poll) {
    if (!poll.deadline_at) return '无截止时间';
    const stamp = formatDate(poll.deadline_at);
    return poll.deadline_passed ? `已截止 · ${stamp}` : `截止 ${stamp}`;
}

function classesText(poll) {
    const classes = poll.assigned_classes || [];
    if (!classes.length) return '未分配班级';
    return classes.map((cls) => `${cls.course_name} · ${cls.class_name}`).join('，');
}

function renderSummary(summary) {
    const items = [
        ['全部', summary.total || 0],
        ['进行中', summary.active || 0],
        ['草稿', summary.draft || 0],
        ['已结束', summary.closed || 0],
    ];
    return items.map(([label, value]) => `
        <span class="manage-polls__summary-item"><strong>${value}</strong><small>${label}</small></span>
    `).join('');
}

function renderCard(poll) {
    const meta = statusMeta(poll.effective_status);
    const typeLabel = poll.vote_type === 'multiple' ? '多选' : '单选';
    return `
        <button type="button" class="poll-card ${meta.tone}" data-poll-open="${poll.id}">
            <div class="poll-card__top">
                <span class="poll-status-badge ${meta.tone}">${meta.label}</span>
                <span class="poll-type-badge">${typeLabel}</span>
            </div>
            <strong class="poll-card__title">${escapeHtml(poll.title)}</strong>
            ${poll.description ? `<p class="poll-card__desc">${escapeHtml(poll.description)}</p>` : ''}
            <div class="poll-card__meta">
                <span>${escapeHtml(classesText(poll))}</span>
            </div>
            <div class="poll-card__foot">
                <small>${escapeHtml(deadlineText(poll))}</small>
                <small>${poll.total_voters} 人已投</small>
            </div>
        </button>
    `;
}

function renderList(data) {
    const polls = data.polls || [];
    if (!polls.length) {
        return `
            <div class="poll-empty">
                <strong>还没有投票活动</strong>
                <p>点击右上角「新建投票」创建跨班级投票活动。</p>
            </div>
        `;
    }
    return `<div class="poll-card-grid">${polls.map(renderCard).join('')}</div>`;
}

// --------------------------------------------------------------------------- #
// overlay
// --------------------------------------------------------------------------- #
function closeOverlay() {
    document.querySelectorAll('[data-poll-overlay]').forEach((node) => node.remove());
    document.removeEventListener('keydown', onOverlayKeydown);
}
function onOverlayKeydown(event) { if (event.key === 'Escape') closeOverlay(); }
function openOverlay(html) {
    closeOverlay();
    const overlay = document.createElement('div');
    overlay.className = 'poll-overlay';
    overlay.setAttribute('data-poll-overlay', '');
    overlay.innerHTML = `<div class="poll-overlay__backdrop" data-poll-overlay-close></div><div class="poll-overlay__shell" role="dialog" aria-modal="true">${html}</div>`;
    document.body.appendChild(overlay);
    document.addEventListener('keydown', onOverlayKeydown);
    return overlay;
}

function renderResultOption(option) {
    const percent = safePercent(option.percent);
    return `
        <div class="poll-bar">
            <div class="poll-bar__head">
                <span class="poll-bar__label">${escapeHtml(option.label)}</span>
                <span class="poll-bar__value">${option.count || 0} 人 · ${percent}%</span>
            </div>
            <div class="poll-bar__track"><span class="poll-bar__fill" style="width:${percent}%"></span></div>
        </div>
    `;
}

function renderDetailBody(poll) {
    const meta = statusMeta(poll.effective_status);
    const typeLabel = poll.vote_type === 'multiple' ? '多选' : '单选';
    const optionsHtml = (poll.options || []).map(renderResultOption).join('');
    return `
        <div class="poll-detail">
            <div class="poll-detail__head">
                <div class="poll-detail__titles">
                    <div class="poll-detail__badges">
                        <span class="poll-status-badge ${meta.tone}">${meta.label}</span>
                        <span class="poll-type-badge">${typeLabel}</span>
                    </div>
                    <h3>${escapeHtml(poll.title)}</h3>
                    <span class="poll-detail__owner">${escapeHtml(classesText(poll))} · ${escapeHtml(deadlineText(poll))}</span>
                </div>
                <button type="button" class="poll-overlay__close" data-poll-overlay-close aria-label="关闭">×</button>
            </div>
            ${poll.description ? `<p class="poll-detail__desc">${escapeHtml(poll.description)}</p>` : ''}
            <p class="poll-detail__scope">参与人数：${poll.participant_total} 人 · 已投票 ${poll.total_voters} 人</p>
            <div class="poll-detail__options">${optionsHtml || '<p class="poll-detail-note">暂无选项。</p>'}</div>
            <div class="poll-detail-admin" data-poll-admin="${poll.id}">
                <div class="poll-detail-admin__status">
                    ${['draft', 'active', 'closed'].map((status) => `
                        <button type="button" class="poll-status-btn${poll.status === status ? ' is-current' : ''}" data-poll-status="${status}" ${poll.status === status ? 'disabled' : ''}>
                            ${statusMeta(status).label}
                        </button>
                    `).join('')}
                </div>
                <div class="poll-detail-admin__ops">
                    <button type="button" class="btn btn-ghost btn-sm" data-poll-edit="${poll.id}">编辑</button>
                    <button type="button" class="btn btn-ghost btn-sm poll-danger" data-poll-delete="${poll.id}">删除</button>
                </div>
            </div>
        </div>
    `;
}

// --------------------------------------------------------------------------- #
// form
// --------------------------------------------------------------------------- #
function optionRow(value, index) {
    return `
        <div class="poll-option-row">
            <input type="text" name="poll_option_label" maxlength="160" value="${escapeHtml(value || '')}" placeholder="选项 ${index + 1}">
            <button type="button" class="poll-icon-btn" data-poll-remove-option aria-label="删除选项">×</button>
        </div>
    `;
}

function classPicker(offerings, selectedIds) {
    if (!offerings || !offerings.length) {
        return '<p class="poll-detail-note">你还没有可分配的课堂。</p>';
    }
    const selected = new Set((selectedIds || []).map((id) => String(id)));
    return `
        <div class="poll-participant-list">
            ${offerings.map((off) => `
                <label class="poll-participant">
                    <input type="checkbox" name="poll_class" value="${off.id}" ${selected.has(String(off.id)) ? 'checked' : ''}>
                    <span>${escapeHtml(off.course_name)} · ${escapeHtml(off.class_name)}</span>
                </label>
            `).join('')}
        </div>
    `;
}

function renderForm(poll, offerings) {
    const isEdit = Boolean(poll);
    const options = isEdit ? (poll.options || []).map((o) => o.label) : DEFAULT_OPTIONS;
    const voteType = isEdit ? poll.vote_type : 'single';
    const visibility = isEdit ? poll.result_visibility : 'after_vote';
    const allowChange = isEdit ? poll.allow_change : false;
    const maxChanges = isEdit ? poll.max_changes : 0;
    const deadline = isEdit && poll.deadline_at ? poll.deadline_at.slice(0, 16) : '';
    const selectedClasses = isEdit ? (poll.assigned_classes || []).map((c) => c.id) : [];
    return `
        <div class="poll-form-wrap">
            <div class="poll-detail__head">
                <h3>${isEdit ? '编辑投票' : '新建投票'}</h3>
                <button type="button" class="poll-overlay__close" data-poll-overlay-close aria-label="关闭">×</button>
            </div>
            <form class="poll-form" data-poll-form data-poll-id="${isEdit ? poll.id : ''}">
                <div class="poll-form-field poll-form-field--full">
                    <span class="poll-form-label">标题</span>
                    <input type="text" name="title" maxlength="120" required value="${isEdit ? escapeHtml(poll.title) : ''}" placeholder="例如：xx课程期末考核形式">
                </div>
                <div class="poll-form-field poll-form-field--full">
                    <span class="poll-form-label">说明（可选）</span>
                    <textarea name="description" rows="2" maxlength="1000" placeholder="补充投票背景或说明">${isEdit ? escapeHtml(poll.description) : ''}</textarea>
                </div>
                <div class="poll-form-field">
                    <span class="poll-form-label">投票形式</span>
                    <select name="vote_type">
                        <option value="single"${voteType === 'single' ? ' selected' : ''}>单选</option>
                        <option value="multiple"${voteType === 'multiple' ? ' selected' : ''}>多选</option>
                    </select>
                </div>
                <div class="poll-form-field">
                    <span class="poll-form-label">统计可见时机</span>
                    <select name="result_visibility">
                        ${VISIBILITY_OPTIONS.map((opt) => `<option value="${opt.value}"${visibility === opt.value ? ' selected' : ''}>${opt.label}</option>`).join('')}
                    </select>
                </div>
                <div class="poll-form-field">
                    <span class="poll-form-label">截止时间（可选）</span>
                    <input type="datetime-local" name="deadline_at" value="${deadline}">
                </div>
                <div class="poll-form-field">
                    <span class="poll-form-label">可修改</span>
                    <label class="poll-inline-check"><input type="checkbox" name="allow_change" ${allowChange ? 'checked' : ''}> 截止前允许修改</label>
                    <input type="number" name="max_changes" min="0" max="20" value="${maxChanges || 0}" placeholder="0 = 不限次数" data-poll-max-changes ${allowChange ? '' : 'disabled'}>
                </div>
                <div class="poll-form-field poll-form-field--full">
                    <span class="poll-form-label">选项</span>
                    <div class="poll-option-editor" data-poll-option-list>
                        ${options.map((value, index) => optionRow(value, index)).join('')}
                    </div>
                    <button type="button" class="btn btn-ghost btn-sm" data-poll-add-option>增加选项</button>
                </div>
                <div class="poll-form-field poll-form-field--full">
                    <span class="poll-form-label">分配班级（可多选，跨班级共享数据）</span>
                    ${classPicker(offerings, selectedClasses)}
                </div>
                <div class="poll-form-actions">
                    <button type="submit" class="btn btn-outline btn-sm" data-poll-save-status="draft">保存为草稿</button>
                    <button type="submit" class="btn btn-primary btn-sm" data-poll-save-status="active">${isEdit ? '保存并开始' : '创建并开始'}</button>
                </div>
            </form>
        </div>
    `;
}

// --------------------------------------------------------------------------- #
// controller
// --------------------------------------------------------------------------- #
export function initManagePolls() {
    const root = document.querySelector('[data-poll-manage-root]');
    if (!root) return null;

    const content = root.querySelector('[data-poll-content]');
    const loading = root.querySelector('[data-poll-loading]');
    const summaryEl = root.querySelector('[data-poll-summary]');

    const state = { data: null, offerings: null, pending: false };

    const render = () => {
        if (!content || !state.data) return;
        content.innerHTML = renderList(state.data);
        content.hidden = false;
        if (loading) loading.hidden = true;
        if (summaryEl) summaryEl.innerHTML = renderSummary(state.data.summary || {});
    };

    const refresh = async () => {
        if (state.pending) return;
        state.pending = true;
        try {
            const data = await apiFetch('/api/polls/manage/list', { silent: true });
            state.data = data;
            render();
        } catch (error) {
            if (content) {
                content.innerHTML = `<div class="poll-empty"><strong>投票暂时不可用</strong><p>${escapeHtml(error.message || '请稍后刷新重试。')}</p></div>`;
                content.hidden = false;
            }
            if (loading) loading.hidden = true;
        } finally {
            state.pending = false;
        }
    };

    const loadOfferings = async () => {
        if (state.offerings) return state.offerings;
        try {
            const data = await apiFetch('/api/polls/manage/offerings', { silent: true });
            state.offerings = data?.offerings || [];
        } catch (error) {
            state.offerings = [];
        }
        return state.offerings;
    };

    const findPoll = (pollId) => (state.data?.polls || []).find((p) => String(p.id) === String(pollId));

    const openForm = async (poll) => {
        const offerings = await loadOfferings();
        openOverlay(renderForm(poll, offerings));
    };

    const openDetail = async (pollId) => {
        try {
            const data = await apiFetch(`/api/polls/${pollId}`, { silent: true });
            openOverlay(renderDetailBody(data.poll || findPoll(pollId)));
        } catch (error) {
            showToast(error.message || '加载详情失败', 'error');
        }
    };

    const collectFormPayload = (form, status) => {
        const fd = new FormData(form);
        const options = Array.from(form.querySelectorAll('input[name="poll_option_label"]'))
            .map((input) => String(input.value || '').trim())
            .filter(Boolean)
            .map((label) => ({ label }));
        return {
            title: String(fd.get('title') || '').trim(),
            description: String(fd.get('description') || '').trim(),
            vote_type: String(fd.get('vote_type') || 'single'),
            result_visibility: String(fd.get('result_visibility') || 'after_vote'),
            deadline_at: String(fd.get('deadline_at') || ''),
            allow_change: fd.get('allow_change') === 'on',
            max_changes: Number(fd.get('max_changes') || 0),
            options,
            status,
            class_offering_ids: Array.from(form.querySelectorAll('input[name="poll_class"]:checked')).map((i) => Number(i.value)),
        };
    };

    document.querySelectorAll('[data-poll-create-open]').forEach((btn) => {
        btn.addEventListener('click', () => openForm(null));
    });

    root.addEventListener('click', async (event) => {
        const openId = event.target.closest('[data-poll-open]')?.dataset.pollOpen;
        if (openId) { await openDetail(openId); }
    });

    document.addEventListener('click', async (event) => {
        if (event.target.closest('[data-poll-overlay-close]')) { closeOverlay(); return; }

        const addOption = event.target.closest('[data-poll-add-option]');
        if (addOption) {
            const list = document.querySelector('[data-poll-option-list]');
            const count = list ? list.querySelectorAll('.poll-option-row').length : 0;
            if (count >= MAX_OPTIONS) { showToast(`最多 ${MAX_OPTIONS} 个选项`, 'warning'); return; }
            list?.insertAdjacentHTML('beforeend', optionRow('', count));
            return;
        }
        const removeOption = event.target.closest('[data-poll-remove-option]');
        if (removeOption) {
            const list = removeOption.closest('[data-poll-option-list]');
            if ((list?.querySelectorAll('.poll-option-row').length || 0) <= 2) { showToast('至少保留两个选项', 'warning'); return; }
            removeOption.closest('.poll-option-row')?.remove();
            return;
        }

        const statusBtn = event.target.closest('[data-poll-status]');
        if (statusBtn) {
            const pollId = statusBtn.closest('[data-poll-admin]')?.dataset.pollAdmin;
            try {
                await apiFetch(`/api/polls/${pollId}/status`, { method: 'POST', body: { status: statusBtn.dataset.pollStatus }, silent: true });
                showToast('状态已更新', 'success');
                closeOverlay();
                await refresh();
            } catch (error) { showToast(error.message || '操作失败', 'error'); }
            return;
        }

        const editBtn = event.target.closest('[data-poll-edit]');
        if (editBtn) {
            try {
                const data = await apiFetch(`/api/polls/${editBtn.dataset.pollEdit}`, { silent: true });
                await openForm(data?.poll || findPoll(editBtn.dataset.pollEdit));
            } catch (error) { showToast(error.message || '加载失败', 'error'); }
            return;
        }

        const delBtn = event.target.closest('[data-poll-delete]');
        if (delBtn) {
            if (!window.confirm('确认删除该投票活动？所有投票记录将一并清除。')) return;
            try {
                await apiFetch(`/api/polls/${delBtn.dataset.pollDelete}`, { method: 'DELETE', silent: true });
                showToast('已删除', 'success');
                closeOverlay();
                await refresh();
            } catch (error) { showToast(error.message || '删除失败', 'error'); }
            return;
        }
    });

    document.addEventListener('change', (event) => {
        if (event.target.matches('input[name="allow_change"]')) {
            const maxInput = event.target.closest('.poll-form-field')?.querySelector('[data-poll-max-changes]');
            if (maxInput) maxInput.disabled = !event.target.checked;
        }
    });

    document.addEventListener('submit', async (event) => {
        const pollForm = event.target.closest('[data-poll-form]');
        if (!pollForm) return;
        event.preventDefault();
        const status = event.submitter?.dataset.pollSaveStatus || 'draft';
        const pollId = pollForm.dataset.pollId;
        const payload = collectFormPayload(pollForm, status);
        if (!payload.title) { showToast('请填写标题', 'warning'); return; }
        if (payload.options.length < 2) { showToast('请至少填写两个选项', 'warning'); return; }
        if (status === 'active' && !payload.class_offering_ids.length) { showToast('开始投票前请至少分配一个班级', 'warning'); return; }
        try {
            if (pollId) {
                await apiFetch(`/api/polls/${pollId}`, { method: 'PUT', body: payload, silent: true });
                await apiFetch(`/api/polls/${pollId}/status`, { method: 'POST', body: { status }, silent: true });
            } else {
                await apiFetch('/api/polls/manage/polls', { method: 'POST', body: payload, silent: true });
            }
            showToast('投票已保存', 'success');
            closeOverlay();
            await refresh();
        } catch (error) { showToast(error.message || '保存失败', 'error'); }
    });

    refresh().catch(() => {});
    return { refresh };
}

initManagePolls();
