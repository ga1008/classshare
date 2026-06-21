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
    const target = new Date(poll.deadline_at);
    if (Number.isNaN(target.getTime())) return poll.deadline_at;
    const now = new Date();
    const diff = target.getTime() - now.getTime();
    const stamp = formatDate(poll.deadline_at);
    if (diff <= 0) return `已截止 · ${stamp}`;
    const minutes = Math.floor(diff / 60000);
    if (minutes < 60) return `剩 ${minutes} 分钟 · ${stamp}`;
    const hours = Math.floor(minutes / 60);
    if (hours < 48) return `剩 ${hours} 小时 · ${stamp}`;
    const days = Math.floor(hours / 24);
    return `剩 ${days} 天 · ${stamp}`;
}

function visibilityLabel(value) {
    const item = VISIBILITY_OPTIONS.find((option) => option.value === value);
    return item ? item.label : '参与投票后';
}

function ownerText(poll) {
    if (poll.is_mine) return '我创建';
    const roleLabel = poll.owner_role === 'teacher' ? '教师' : '同学';
    return `${escapeHtml(poll.owner_name || roleLabel)} · ${roleLabel}`;
}

function dispatchPollCounts(snapshot) {
    const summary = snapshot?.summary || {};
    const active = Number(summary.active || 0);
    const badge = document.querySelector('[data-poll-tab-count]');
    if (badge) {
        badge.textContent = String(active);
        badge.hidden = active <= 0;
    }
    window.dispatchEvent(new CustomEvent('classroom:poll-counts', {
        detail: {
            counts: { polls: active },
            notes: { polls: active ? `${active} 个进行中` : '暂无进行中' },
        },
    }));
}

function renderCard(poll) {
    const meta = statusMeta(poll.effective_status);
    const typeLabel = poll.vote_type === 'multiple' ? '多选' : '单选';
    const voteline = poll.show_results
        ? `${poll.total_voters} 人已投`
        : (poll.has_voted ? '你已投票' : '尚未投票');
    return `
        <button type="button" class="poll-card ${meta.tone}" data-poll-open="${poll.id}">
            <div class="poll-card__top">
                <span class="poll-status-badge ${meta.tone}">${meta.label}</span>
                <span class="poll-type-badge">${typeLabel}</span>
                ${poll.is_mine ? '<span class="poll-owner-badge">我创建</span>' : ''}
            </div>
            <strong class="poll-card__title">${escapeHtml(poll.title)}</strong>
            ${poll.description ? `<p class="poll-card__desc">${escapeHtml(poll.description)}</p>` : ''}
            <div class="poll-card__meta">
                <span>${escapeHtml(ownerText(poll))}</span>
                <span>${poll.participant_total} 人可参与</span>
            </div>
            <div class="poll-card__foot">
                <small>${escapeHtml(deadlineText(poll))}</small>
                <small>${voteline}</small>
            </div>
        </button>
    `;
}

function renderList(snapshot, state) {
    const polls = snapshot.polls || [];
    const createBtn = `
        <div class="poll-launch-strip">
            <div>
                <strong>${snapshot.role === 'teacher' ? '本课堂投票' : '投票活动'}</strong>
                <span>${snapshot.role === 'teacher'
                    ? '在此创建仅本班级的投票；跨班级投票请到管理中心 · 投票。'
                    : '可创建你自己的投票并邀请同学参与（被你拉黑或拉黑你的同学不可邀请）。'}</span>
            </div>
            <button type="button" class="btn btn-primary btn-sm" data-poll-create-open>新建投票</button>
        </div>
    `;
    if (!polls.length) {
        return `
            ${createBtn}
            <div class="poll-empty">
                <strong>还没有投票活动</strong>
                <p>${snapshot.role === 'teacher'
                    ? '点击「新建投票」创建本班级投票，或到管理中心创建跨班级投票。'
                    : '点击「新建投票」发起你的投票，或等待进行中的投票出现。'}</p>
            </div>
        `;
    }
    return `
        ${createBtn}
        <div class="poll-card-grid">
            ${polls.map(renderCard).join('')}
        </div>
    `;
}

// --------------------------------------------------------------------------- #
// overlay helpers
// --------------------------------------------------------------------------- #
function closeOverlay() {
    document.querySelectorAll('[data-poll-overlay]').forEach((node) => node.remove());
    document.removeEventListener('keydown', onOverlayKeydown);
}

function onOverlayKeydown(event) {
    if (event.key === 'Escape') closeOverlay();
}

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

// --------------------------------------------------------------------------- #
// option-bar (result) + vote inputs
// --------------------------------------------------------------------------- #
function renderResultOption(poll, option) {
    const percent = safePercent(option.percent);
    const selected = option.selected ? ' is-selected' : '';
    return `
        <div class="poll-bar${selected}">
            <div class="poll-bar__head">
                <span class="poll-bar__label">${escapeHtml(option.label)}${option.selected ? ' · 我的选择' : ''}</span>
                <span class="poll-bar__value">${option.count || 0} 人 · ${percent}%</span>
            </div>
            <div class="poll-bar__track"><span class="poll-bar__fill" style="width:${percent}%"></span></div>
        </div>
    `;
}

function renderVoteOption(poll, option) {
    const inputType = poll.vote_type === 'multiple' ? 'checkbox' : 'radio';
    const checked = option.selected ? ' checked' : '';
    const disabled = poll.can_vote ? '' : ' disabled';
    return `
        <label class="poll-vote-option${option.selected ? ' is-selected' : ''}">
            <input type="${inputType}" name="poll_option" value="${option.id}"${checked}${disabled}>
            <span>${escapeHtml(option.label)}</span>
        </label>
    `;
}

function renderDetailBody(poll) {
    const meta = statusMeta(poll.effective_status);
    const typeLabel = poll.vote_type === 'multiple' ? '多选' : '单选';
    const showResults = poll.show_results;

    // Result bars (read-only) — only when statistics are visible to this viewer.
    const resultsHtml = showResults
        ? `<div class="poll-detail__options">${(poll.options || []).map((option) => renderResultOption(poll, option)).join('')}</div>`
        : '';
    const votedLine = showResults
        ? `<p class="poll-detail__voted">已投 ${poll.total_voters} / 共 ${poll.participant_total} 人</p>`
        : '';

    // Voting form — the inputs ALWAYS live inside the form so the submit handler
    // can read them. When results are already shown, the input list is collapsed
    // behind a "修改投票/去投票" toggle to avoid duplicating the bars.
    let voteControls = '';
    if (poll.can_vote) {
        const voteList = (poll.options || []).map((option) => renderVoteOption(poll, option)).join('');
        const changeHint = poll.allow_change
            ? `<small>${poll.max_changes ? `可修改 ${Math.max(0, poll.max_changes - poll.change_count)} 次` : '可随时修改'}</small>`
            : '<small>提交后不可修改</small>';
        voteControls = `
            <form class="poll-vote-form" data-poll-vote="${poll.id}">
                ${showResults ? `<button type="button" class="poll-revote-toggle" data-poll-revote-toggle>${poll.has_voted ? '修改投票' : '去投票'}</button>` : ''}
                <div class="poll-vote-list" data-poll-vote-list${showResults ? ' hidden' : ''}>
                    ${voteList}
                    <div class="poll-vote-actions">
                        <button type="submit" class="btn btn-primary btn-sm">${poll.has_voted ? '更新投票' : '提交投票'}</button>
                        ${changeHint}
                    </div>
                </div>
            </form>
        `;
    } else if (poll.has_voted && !showResults) {
        voteControls = '<p class="poll-detail-note">你已完成投票。</p>';
    } else if (poll.effective_status === 'closed' && !showResults) {
        voteControls = '<p class="poll-detail-note">该投票已结束。</p>';
    } else if (poll.is_participant === false && poll.owner_role) {
        voteControls = '';
    }

    const resultNote = (!showResults && poll.is_participant)
        ? `<p class="poll-detail-note poll-detail-note--muted">统计将于「${visibilityLabel(poll.result_visibility)}」后可见。</p>`
        : '';

    const ownerControls = poll.can_manage ? `
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
    ` : '';

    const classScope = (poll.assigned_classes || []).map((cls) => `${escapeHtml(cls.course_name)} · ${escapeHtml(cls.class_name)}`).join('，');

    return `
        <div class="poll-detail">
            <div class="poll-detail__head">
                <div class="poll-detail__titles">
                    <div class="poll-detail__badges">
                        <span class="poll-status-badge ${meta.tone}">${meta.label}</span>
                        <span class="poll-type-badge">${typeLabel}</span>
                    </div>
                    <h3>${escapeHtml(poll.title)}</h3>
                    <span class="poll-detail__owner">${escapeHtml(ownerText(poll))} · ${escapeHtml(deadlineText(poll))}</span>
                </div>
                <button type="button" class="poll-overlay__close" data-poll-overlay-close aria-label="关闭">×</button>
            </div>
            ${poll.description ? `<p class="poll-detail__desc">${escapeHtml(poll.description)}</p>` : ''}
            ${classScope ? `<p class="poll-detail__scope">参与范围：${classScope}（${poll.participant_total} 人）</p>` : ''}
            ${resultsHtml}
            ${votedLine}
            ${resultNote}
            ${voteControls}
            ${ownerControls}
        </div>
    `;
}

// --------------------------------------------------------------------------- #
// create / edit form
// --------------------------------------------------------------------------- #
function optionRow(value, index) {
    return `
        <div class="poll-option-row">
            <input type="text" name="poll_option_label" maxlength="160" value="${escapeHtml(value || '')}" placeholder="选项 ${index + 1}">
            <button type="button" class="poll-icon-btn" data-poll-remove-option aria-label="删除选项">×</button>
        </div>
    `;
}

function participantPicker(candidates, selectedIds) {
    if (!candidates || !candidates.length) {
        return '<p class="poll-detail-note">该班级暂无可邀请的同学。</p>';
    }
    const selected = new Set((selectedIds || []).map((id) => String(id)));
    return `
        <div class="poll-participant-picker" data-poll-participants>
            <div class="poll-participant-picker__head">
                <span>选择参与者</span>
                <button type="button" class="btn btn-ghost btn-sm" data-poll-select-all>全选可邀请</button>
            </div>
            <div class="poll-participant-list">
                ${candidates.map((cand) => `
                    <label class="poll-participant${cand.blocked ? ' is-blocked' : ''}">
                        <input type="checkbox" name="poll_participant" value="${cand.id}" ${cand.blocked ? 'disabled' : ''} ${selected.has(String(cand.id)) ? 'checked' : ''}>
                        <span>${escapeHtml(cand.name)}${cand.student_id_number ? ` · ${escapeHtml(cand.student_id_number)}` : ''}</span>
                        ${cand.blocked ? '<em>黑名单，不可邀请</em>' : ''}
                    </label>
                `).join('')}
            </div>
        </div>
    `;
}

function renderForm(state, poll) {
    const isEdit = Boolean(poll);
    const isStudent = state.role === 'student';
    const options = isEdit ? (poll.options || []).map((o) => o.label) : DEFAULT_OPTIONS;
    const voteType = isEdit ? poll.vote_type : 'single';
    const visibility = isEdit ? poll.result_visibility : 'after_vote';
    const allowChange = isEdit ? poll.allow_change : false;
    const maxChanges = isEdit ? poll.max_changes : 0;
    const deadline = isEdit && poll.deadline_at ? poll.deadline_at.slice(0, 16) : '';
    const selectedParticipants = isEdit ? (poll.my_participant_ids || []) : [];

    const participantSection = isStudent ? `
        <div class="poll-form-field poll-form-field--full" data-poll-participant-section>
            <span class="poll-form-label">参与者（班级成员）</span>
            <div data-poll-participant-mount><p class="poll-detail-note">加载中…</p></div>
        </div>
    ` : `
        <p class="poll-detail-note">该投票范围为当前班级。跨班级投票请到「管理中心 · 内容资产 · 投票」创建。</p>
    `;

    return `
        <div class="poll-form-wrap">
            <div class="poll-detail__head">
                <h3>${isEdit ? '编辑投票' : '新建投票'}</h3>
                <button type="button" class="poll-overlay__close" data-poll-overlay-close aria-label="关闭">×</button>
            </div>
            <form class="poll-form" data-poll-form data-poll-id="${isEdit ? poll.id : ''}">
                <div class="poll-form-field poll-form-field--full">
                    <span class="poll-form-label">标题</span>
                    <input type="text" name="title" maxlength="120" required value="${isEdit ? escapeHtml(poll.title) : ''}" placeholder="例如：期末考核形式投票">
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
                ${participantSection}
                <div class="poll-form-actions">
                    <button type="submit" class="btn btn-outline btn-sm" data-poll-save-status="draft">保存为草稿</button>
                    <button type="submit" class="btn btn-primary btn-sm" data-poll-save-status="active">${isEdit ? '保存并开始' : '创建并开始'}</button>
                </div>
            </form>
        </div>
    `;
}

// --------------------------------------------------------------------------- #
// main controller
// --------------------------------------------------------------------------- #
export function initClassroomPolls(config = {}) {
    const root = document.querySelector('[data-poll-root]');
    if (!root) return null;
    const classOfferingId = Number(root.dataset.classOfferingId || config.classOfferingId || 0);
    if (!classOfferingId) return null;

    const content = root.querySelector('[data-poll-content]');
    const loading = root.querySelector('[data-poll-loading]');

    const state = {
        snapshot: null,
        role: String(root.dataset.role || config.userInfo?.role || ''),
        classOfferingId,
        refreshTimer: null,
        pending: false,
        candidates: null,
    };

    const render = () => {
        if (!content || !state.snapshot) return;
        state.role = state.snapshot.role || state.role;
        content.innerHTML = renderList(state.snapshot, state);
        content.hidden = false;
        if (loading) loading.hidden = true;
        dispatchPollCounts(state.snapshot);
    };

    const refresh = async ({ silent = false } = {}) => {
        if (state.pending && silent) return;
        state.pending = true;
        if (!silent && loading) loading.hidden = false;
        try {
            const data = await apiFetch(`/api/polls/classrooms/${classOfferingId}/snapshot`, { silent: true });
            state.snapshot = data?.snapshot || null;
            render();
        } catch (error) {
            console.error('加载投票失败:', error);
            if (!silent && content) {
                content.innerHTML = `<div class="poll-empty"><strong>投票暂时不可用</strong><p>${escapeHtml(error.message || '请稍后刷新重试。')}</p></div>`;
                content.hidden = false;
                if (loading) loading.hidden = true;
            }
        } finally {
            state.pending = false;
        }
    };

    const scheduleRefresh = () => {
        if (state.refreshTimer) window.clearTimeout(state.refreshTimer);
        state.refreshTimer = window.setTimeout(() => refresh({ silent: true }).catch(() => {}), 280);
    };

    const findPoll = (pollId) => (state.snapshot?.polls || []).find((p) => String(p.id) === String(pollId));

    const openDetail = async (pollId) => {
        let poll = findPoll(pollId);
        try {
            const data = await apiFetch(`/api/polls/${pollId}`, { silent: true });
            poll = data?.poll || poll;
        } catch (error) {
            showToast(error.message || '加载投票详情失败', 'error');
            return;
        }
        if (!poll) return;
        openOverlay(renderDetailBody(poll));
    };

    const loadCandidates = async (mountSelector, selectedIds) => {
        const mount = document.querySelector(mountSelector);
        if (!mount) return;
        try {
            if (!state.candidates) {
                const data = await apiFetch(`/api/polls/classrooms/${classOfferingId}/candidates`, { silent: true });
                state.candidates = data?.candidates || [];
            }
            mount.innerHTML = participantPicker(state.candidates, selectedIds);
        } catch (error) {
            mount.innerHTML = `<p class="poll-detail-note">${escapeHtml(error.message || '加载同学名单失败')}</p>`;
        }
    };

    const openForm = async (poll) => {
        openOverlay(renderForm(state, poll));
        if (state.role === 'student') {
            await loadCandidates('[data-poll-participant-mount]', poll ? (poll.my_participant_ids || []) : []);
        }
    };

    const collectFormPayload = (form, status) => {
        const fd = new FormData(form);
        const options = Array.from(form.querySelectorAll('input[name="poll_option_label"]'))
            .map((input) => String(input.value || '').trim())
            .filter(Boolean)
            .map((label) => ({ label }));
        const payload = {
            title: String(fd.get('title') || '').trim(),
            description: String(fd.get('description') || '').trim(),
            vote_type: String(fd.get('vote_type') || 'single'),
            result_visibility: String(fd.get('result_visibility') || 'after_vote'),
            deadline_at: String(fd.get('deadline_at') || ''),
            allow_change: fd.get('allow_change') === 'on',
            max_changes: Number(fd.get('max_changes') || 0),
            options,
            status,
        };
        if (state.role === 'student') {
            payload.participant_ids = Array.from(form.querySelectorAll('input[name="poll_participant"]:checked'))
                .map((input) => Number(input.value));
        }
        return payload;
    };

    // delegated clicks inside the poll panel
    root.addEventListener('click', async (event) => {
        const openId = event.target.closest('[data-poll-open]')?.dataset.pollOpen;
        if (openId) { await openDetail(openId); return; }
        if (event.target.closest('[data-poll-create-open]')) { await openForm(null); return; }
    });

    // delegated clicks at document level (overlays append to body)
    document.addEventListener('click', async (event) => {
        if (event.target.closest('[data-poll-overlay-close]')) { closeOverlay(); return; }

        const revoteToggle = event.target.closest('[data-poll-revote-toggle]');
        if (revoteToggle) {
            const list = revoteToggle.closest('.poll-vote-form')?.querySelector('[data-poll-vote-list]');
            if (list) list.hidden = !list.hidden;
            return;
        }

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
        if (event.target.closest('[data-poll-select-all]')) {
            document.querySelectorAll('input[name="poll_participant"]:not(:disabled)').forEach((input) => { input.checked = true; });
            return;
        }

        const statusBtn = event.target.closest('[data-poll-status]');
        if (statusBtn) {
            const pollId = statusBtn.closest('[data-poll-admin]')?.dataset.pollAdmin;
            const status = statusBtn.dataset.pollStatus;
            try {
                await apiFetch(`/api/polls/${pollId}/status`, { method: 'POST', body: { status }, silent: true });
                showToast('状态已更新', 'success');
                closeOverlay();
                await refresh({ silent: true });
            } catch (error) { showToast(error.message || '操作失败', 'error'); }
            return;
        }

        const editBtn = event.target.closest('[data-poll-edit]');
        if (editBtn) {
            const pollId = editBtn.dataset.pollEdit;
            try {
                const data = await apiFetch(`/api/polls/${pollId}`, { silent: true });
                await openForm(data?.poll || findPoll(pollId));
            } catch (error) { showToast(error.message || '加载失败', 'error'); }
            return;
        }

        const delBtn = event.target.closest('[data-poll-delete]');
        if (delBtn) {
            const pollId = delBtn.dataset.pollDelete;
            if (!window.confirm('确认删除该投票活动？所有投票记录将一并清除。')) return;
            try {
                await apiFetch(`/api/polls/${pollId}`, { method: 'DELETE', silent: true });
                showToast('已删除', 'success');
                closeOverlay();
                await refresh({ silent: true });
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
        const voteForm = event.target.closest('[data-poll-vote]');
        if (voteForm) {
            event.preventDefault();
            const pollId = voteForm.dataset.pollVote;
            const selected = Array.from(voteForm.querySelectorAll('input[name="poll_option"]:checked')).map((i) => Number(i.value));
            if (!selected.length) { showToast('请选择至少一个选项', 'warning'); return; }
            const submitBtn = voteForm.querySelector('button[type="submit"]');
            if (submitBtn) submitBtn.disabled = true;
            try {
                const data = await apiFetch(`/api/polls/${pollId}/vote`, { method: 'POST', body: { option_ids: selected }, silent: true });
                showToast(data.message || '投票已提交', 'success');
                if (data.poll) openOverlay(renderDetailBody(data.poll));
                await refresh({ silent: true });
            } catch (error) {
                if (submitBtn) submitBtn.disabled = false;
                showToast(error.message || '投票失败', 'error');
            }
            return;
        }

        const pollForm = event.target.closest('[data-poll-form]');
        if (pollForm) {
            event.preventDefault();
            const submitter = event.submitter;
            const status = submitter?.dataset.pollSaveStatus || 'draft';
            const pollId = pollForm.dataset.pollId;
            const payload = collectFormPayload(pollForm, status);
            if (!payload.title) { showToast('请填写标题', 'warning'); return; }
            if (payload.options.length < 2) { showToast('请至少填写两个选项', 'warning'); return; }
            if (state.role === 'student' && (!payload.participant_ids || !payload.participant_ids.length)) {
                showToast('请至少选择一名参与者', 'warning'); return;
            }
            const saveButtons = Array.from(pollForm.querySelectorAll('button[type="submit"]'));
            saveButtons.forEach((btn) => { btn.disabled = true; });
            try {
                if (pollId) {
                    await apiFetch(`/api/polls/${pollId}`, { method: 'PUT', body: payload, silent: true });
                    await apiFetch(`/api/polls/${pollId}/status`, { method: 'POST', body: { status }, silent: true });
                } else {
                    await apiFetch(`/api/polls/classrooms/${classOfferingId}/polls`, { method: 'POST', body: payload, silent: true });
                }
                showToast('投票已保存', 'success');
                closeOverlay();
                await refresh({ silent: true });
            } catch (error) {
                saveButtons.forEach((btn) => { btn.disabled = false; });
                showToast(error.message || '保存失败', 'error');
            }
            return;
        }
    });

    window.addEventListener('classroom:poll-ws', (event) => {
        const detail = event.detail || {};
        if (Number(detail.class_offering_id || 0) !== classOfferingId) return;
        scheduleRefresh();
    });

    // Section tab switch (作业与考试 <-> 投票). Toggles the two views in place.
    // Uses inline display so it wins over class-based `display` rules (which
    // otherwise override the `[hidden]` attribute).
    const setSectionView = (view) => {
        document.querySelectorAll('[data-assignment-section-view]').forEach((node) => {
            const match = node.dataset.assignmentSectionView === view;
            node.hidden = !match;
            node.style.display = match ? '' : 'none';
        });
        document.querySelectorAll('[data-assignment-section-tab]').forEach((btn) => {
            const isActive = btn.dataset.assignmentSectionTab === view;
            btn.classList.toggle('is-active', isActive);
            btn.setAttribute('aria-selected', isActive ? 'true' : 'false');
        });
        if (view === 'polls') refresh({ silent: true }).catch(() => {});
    };
    document.querySelectorAll('[data-assignment-section-tab]').forEach((btn) => {
        btn.addEventListener('click', () => setSectionView(btn.dataset.assignmentSectionTab));
    });
    // Establish the initial view explicitly so the poll panel starts hidden.
    setSectionView('tasks');

    refresh().catch(() => {});
    return { refresh, getSnapshot: () => state.snapshot };
}
