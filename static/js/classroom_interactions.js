import { apiFetch } from './api.js';
import { showToast, escapeHtml, formatDate } from './ui.js';

const ACTIVITY_KINDS = [
    { key: 'poll', label: '投票', note: '快速收集判断' },
    { key: 'quiz', label: '随堂测', note: '一题检查理解' },
    { key: 'qna', label: '提问', note: '匿名问题入口' },
];

const RESULT_VISIBILITY = [
    { key: 'after_submit', label: '提交后可见' },
    { key: 'after_close', label: '结束后可见' },
    { key: 'teacher_only', label: '仅教师可见' },
    { key: 'always', label: '实时可见' },
];

const DEFAULT_OPTIONS = ['我理解了', '还需要例子', '节奏偏快', '希望现场演示'];

function normalizeId(value) {
    const text = String(value ?? '').trim();
    return text || '';
}

function safePercent(value) {
    const number = Number(value || 0);
    if (!Number.isFinite(number)) return 0;
    return Math.max(0, Math.min(100, number));
}

function selectedActivity(snapshot, selectedId) {
    const activities = [...(snapshot?.active_activities || []), ...(snapshot?.recent_activities || [])];
    if (!activities.length) return null;
    const exact = activities.find((item) => String(item.id) === String(selectedId));
    return exact || activities[0];
}

function signalTone(key) {
    if (key === 'help') return 'is-help';
    if (key === 'slow') return 'is-slow';
    if (key === 'done') return 'is-done';
    return 'is-hand';
}

function visibilityOptions(selected, kind) {
    const effectiveSelected = selected || (kind === 'quiz' ? 'after_close' : 'after_submit');
    return RESULT_VISIBILITY.map((item) => `
        <option value="${item.key}"${item.key === effectiveSelected ? ' selected' : ''}>${item.label}</option>
    `).join('');
}

function renderStats(snapshot) {
    const summary = snapshot.summary || {};
    const signalCounts = summary.signal_counts || {};
    const signalText = [
        signalCounts.hand ? `举手 ${signalCounts.hand}` : '',
        signalCounts.help ? `求助 ${signalCounts.help}` : '',
        signalCounts.slow ? `跟不上 ${signalCounts.slow}` : '',
        signalCounts.done ? `已完成 ${signalCounts.done}` : '',
    ].filter(Boolean).join(' · ') || '现场状态稳定';
    const items = [
        ['活跃互动', summary.active_activity_count || 0, '正在进行'],
        ['未处理提问', summary.open_question_count || 0, '课堂疑问'],
        ['现场信号', summary.active_signal_count || 0, signalText],
        ['累计回应', summary.response_count || 0, '投票与测验'],
    ];
    return `
        <div class="interaction-stat-grid">
            ${items.map(([label, value, note]) => `
                <article class="interaction-stat-card">
                    <span>${label}</span>
                    <strong>${value}</strong>
                    <small>${escapeHtml(note)}</small>
                </article>
            `).join('')}
        </div>
    `;
}

function renderCreateToggle(snapshot, state) {
    if (!snapshot.can_create) return '';
    if (state.createOpen) return '';
    return `
        <div class="interaction-launch-strip">
            <div>
                <strong>发起课堂互动</strong>
                <span>投票、随堂测、匿名提问会同步到所有在线成员。</span>
            </div>
            <button type="button" class="btn btn-primary btn-sm" data-interaction-create-open>新建互动</button>
        </div>
    `;
}

function renderCreatePanel(state) {
    const kind = state.createKind || 'poll';
    const isQna = kind === 'qna';
    const optionValues = state.createOptions?.length ? state.createOptions : DEFAULT_OPTIONS;
    return `
        <section class="interaction-create-panel">
            <div class="interaction-create-head">
                <div>
                    <span class="interaction-kicker">Live Control</span>
                    <strong>新建课堂互动</strong>
                </div>
                <button type="button" class="interaction-icon-btn" data-interaction-create-close aria-label="关闭新建面板">×</button>
            </div>
            <div class="interaction-kind-tabs" role="tablist" aria-label="互动类型">
                ${ACTIVITY_KINDS.map((item) => `
                    <button type="button" class="interaction-kind-tab${item.key === kind ? ' is-active' : ''}" data-interaction-kind="${item.key}" aria-selected="${item.key === kind ? 'true' : 'false'}">
                        <strong>${item.label}</strong>
                        <span>${item.note}</span>
                    </button>
                `).join('')}
            </div>
            <form class="interaction-create-form" data-interaction-create-form data-kind="${kind}">
                <label class="interaction-field">
                    <span>标题</span>
                    <input name="title" type="text" maxlength="80" placeholder="${kind === 'quiz' ? '例如：第 2 题小测' : kind === 'qna' ? '例如：本节匿名提问' : '例如：你更想先看哪部分'}">
                </label>
                <label class="interaction-field">
                    <span>${isQna ? '提问主题' : '问题'}</span>
                    <textarea name="prompt" rows="3" maxlength="500" required placeholder="${isQna ? '例如：本节课哪里还没有讲清楚？' : '输入要让全班即时回应的问题'}"></textarea>
                </label>
                ${!isQna ? `
                    <div class="interaction-option-editor" data-interaction-option-editor>
                        <div class="interaction-option-editor__head">
                            <span>选项</span>
                            <button type="button" class="btn btn-ghost btn-sm" data-interaction-add-option>增加选项</button>
                        </div>
                        <div class="interaction-option-list">
                            ${optionValues.map((value, index) => renderOptionEditorRow(value, index, kind)).join('')}
                        </div>
                    </div>
                ` : `
                    <label class="interaction-checkbox">
                        <input type="checkbox" name="allow_anonymous" checked>
                        <span>允许匿名提交</span>
                    </label>
                `}
                <label class="interaction-field">
                    <span>结果可见</span>
                    <select name="show_results">${visibilityOptions('', kind)}</select>
                </label>
                <button type="submit" class="btn btn-primary">发布互动</button>
            </form>
        </section>
    `;
}

function renderOptionEditorRow(value, index, kind) {
    const correctInput = kind === 'quiz'
        ? `<label class="interaction-correct-radio"><input type="radio" name="correct_option" value="${index}"${index === 0 ? ' checked' : ''}><span>正确</span></label>`
        : '';
    return `
        <div class="interaction-option-editor-row">
            <input type="text" name="option_label" maxlength="120" value="${escapeHtml(value || '')}" placeholder="选项 ${index + 1}">
            ${correctInput}
            <button type="button" class="interaction-icon-btn" data-interaction-remove-option aria-label="删除选项">×</button>
        </div>
    `;
}

function renderActivityList(snapshot, currentActivity) {
    const active = snapshot.active_activities || [];
    const recent = snapshot.recent_activities || [];
    if (!active.length && !recent.length) {
        return `
            <div class="interaction-empty">
                <strong>课堂互动还没有开始</strong>
                <p>${snapshot.role === 'teacher' ? '可以先发起一个投票、随堂测或匿名提问入口。' : '教师发起后，这里会出现可参与的互动。'}</p>
            </div>
        `;
    }
    const group = (title, items) => items.length ? `
        <div class="interaction-activity-group">
            <div class="interaction-activity-group__head">
                <strong>${title}</strong>
                <span>${items.length} 个</span>
            </div>
            <div class="interaction-activity-list">
                ${items.map((item) => {
                    const selected = currentActivity && String(currentActivity.id) === String(item.id);
                    return `
                        <button type="button" class="interaction-activity-pill${selected ? ' is-selected' : ''}" data-interaction-select="${item.id}">
                            <span>${escapeHtml(item.kind_label)}</span>
                            <strong>${escapeHtml(item.title)}</strong>
                            <small>${item.kind === 'qna' ? `${item.open_question_count} 个问题` : `${item.response_count} 人回应`}</small>
                        </button>
                    `;
                }).join('')}
            </div>
        </div>
    ` : '';
    return `
        ${group('进行中', active)}
        ${group('最近结束', recent)}
    `;
}

function renderActivityDetail(snapshot, activity) {
    if (!activity) {
        return `
            <section class="interaction-detail-card">
                <span class="interaction-kicker">Live Room</span>
                <strong>等待课堂互动</strong>
                <p>当前没有可查看的互动。</p>
            </section>
        `;
    }
    return `
        <section class="interaction-detail-card" data-interaction-activity="${activity.id}">
            <div class="interaction-detail-head">
                <div>
                    <span class="interaction-kicker">${escapeHtml(activity.kind_label)} · ${activity.status === 'active' ? '进行中' : '已结束'}</span>
                    <strong>${escapeHtml(activity.title)}</strong>
                    <p>${escapeHtml(activity.prompt)}</p>
                </div>
                ${activity.can_close ? `<button type="button" class="btn btn-outline btn-sm" data-interaction-close="${activity.id}">结束</button>` : ''}
            </div>
            ${activity.kind === 'qna' ? renderQnaActivity(activity) : renderChoiceActivity(activity, snapshot)}
        </section>
    `;
}

function renderChoiceActivity(activity, snapshot) {
    const canSubmit = Boolean(activity.can_respond);
    const hasResponded = Boolean(activity.has_responded);
    const optionsHtml = (activity.options || []).map((option) => renderChoiceOption(activity, option, canSubmit)).join('');
    return `
        <form class="interaction-response-form" data-interaction-respond="${activity.id}">
            <div class="interaction-option-grid" role="radiogroup" aria-label="${escapeHtml(activity.title)}">
                ${optionsHtml}
            </div>
            ${snapshot.role === 'student' ? `
                <div class="interaction-response-footer">
                    <span>${hasResponded ? `已选择：${escapeHtml(activity.my_response?.option_label || '')}` : '选择后提交，可在开放期间修改。'}</span>
                    <button type="submit" class="btn btn-primary btn-sm"${canSubmit ? '' : ' disabled'}>${hasResponded ? '更新回应' : '提交回应'}</button>
                </div>
            ` : `
                <div class="interaction-response-footer">
                    <span>${activity.response_count} 人已回应</span>
                </div>
            `}
        </form>
    `;
}

function renderChoiceOption(activity, option, canSubmit) {
    const percent = safePercent(option.response_percent);
    const showResult = Boolean(activity.can_show_results);
    const correctClass = option.is_correct ? ' is-correct' : '';
    const selectedClass = option.selected ? ' is-selected' : '';
    return `
        <label class="interaction-option-card${selectedClass}${correctClass}">
            <input type="radio" name="option_id" value="${option.id}"${option.selected ? ' checked' : ''}${canSubmit ? '' : ' disabled'}>
            <span class="interaction-option-main">
                <strong>${escapeHtml(option.label)}</strong>
                ${showResult ? `<small>${option.response_count || 0} 人 · ${percent}%</small>` : ''}
            </span>
            ${showResult ? `<span class="interaction-option-bar" style="--option-percent:${percent}%"></span>` : ''}
        </label>
    `;
}

function renderQnaActivity(activity) {
    const questions = activity.questions || [];
    return `
        ${activity.can_ask ? `
            <form class="interaction-question-form" data-interaction-question="${activity.id}">
                <textarea name="question_text" rows="3" maxlength="500" placeholder="把没听懂、想追问或希望老师再演示的点写下来" required></textarea>
                <div class="interaction-question-actions">
                    ${activity.allow_anonymous ? `
                        <label class="interaction-checkbox">
                            <input type="checkbox" name="is_anonymous" checked>
                            <span>匿名</span>
                        </label>
                    ` : '<span></span>'}
                    <button type="submit" class="btn btn-primary btn-sm">提交问题</button>
                </div>
            </form>
        ` : ''}
        <div class="interaction-question-list">
            ${questions.length ? questions.map(renderQuestion).join('') : '<p class="interaction-muted">还没有问题。</p>'}
        </div>
    `;
}

function renderQuestion(question) {
    return `
        <article class="interaction-question-card${question.status === 'addressed' ? ' is-addressed' : ''}">
            <div>
                <strong>${escapeHtml(question.display_name)}</strong>
                <span>${question.status === 'addressed' ? '已回应' : '待回应'} · ${formatDate(question.created_at)}</span>
            </div>
            <p>${escapeHtml(question.question_text)}</p>
            ${question.can_resolve ? `
                <button type="button" class="btn btn-ghost btn-sm" data-interaction-resolve-question="${question.id}" data-status="${question.status === 'addressed' ? 'open' : 'addressed'}">
                    ${question.status === 'addressed' ? '重新打开' : '标记已回应'}
                </button>
            ` : ''}
        </article>
    `;
}

function renderSignals(snapshot) {
    const options = snapshot.signal_options || [];
    const mySignal = snapshot.my_signal;
    if (snapshot.role === 'student') {
        return `
            <section class="interaction-side-card">
                <div class="interaction-side-head">
                    <span class="interaction-kicker">Signal</span>
                    <strong>我的课堂状态</strong>
                </div>
                <div class="interaction-signal-grid">
                    ${options.map((item) => `
                        <button type="button" class="interaction-signal-btn ${signalTone(item.key)}${mySignal?.signal_type === item.key ? ' is-active' : ''}" data-interaction-signal="${item.key}">
                            <span>${escapeHtml(item.label)}</span>
                        </button>
                    `).join('')}
                </div>
                <form class="interaction-signal-note" data-interaction-signal-note>
                    <input name="message" type="text" maxlength="160" value="${escapeHtml(mySignal?.message || '')}" placeholder="可补充一句给老师">
                    <button type="submit" class="btn btn-outline btn-sm" ${mySignal ? '' : 'disabled'}>更新</button>
                </form>
                ${mySignal ? `
                    <button type="button" class="btn btn-ghost btn-sm interaction-clear-signal" data-interaction-clear-signal>清除当前状态</button>
                ` : '<p class="interaction-muted">状态只显示给教师，用于课堂节奏调整。</p>'}
            </section>
        `;
    }

    const signals = snapshot.signals || [];
    return `
        <section class="interaction-side-card">
            <div class="interaction-side-head">
                <span class="interaction-kicker">Signal</span>
                <strong>现场状态</strong>
            </div>
            <div class="interaction-signal-queue">
                ${signals.length ? signals.map((signal) => `
                    <article class="interaction-signal-row ${signalTone(signal.signal_type)}">
                        <div>
                            <strong>${escapeHtml(signal.display_name)}</strong>
                            <span>${escapeHtml(signal.signal_label)} · ${formatDate(signal.updated_at)}</span>
                            ${signal.message ? `<p>${escapeHtml(signal.message)}</p>` : ''}
                        </div>
                        <button type="button" class="btn btn-ghost btn-sm" data-interaction-resolve-signal="${signal.id}">处理</button>
                    </article>
                `).join('') : '<p class="interaction-muted">暂时没有学生举手或求助。</p>'}
            </div>
        </section>
    `;
}

function renderSnapshot(snapshot, state) {
    const currentActivity = selectedActivity(snapshot, state.selectedActivityId);
    if (currentActivity) {
        state.selectedActivityId = currentActivity.id;
    }
    return `
        ${renderStats(snapshot)}
        <div class="interaction-workbench">
            <div class="interaction-main">
                ${renderCreateToggle(snapshot, state)}
                ${snapshot.can_create && state.createOpen ? renderCreatePanel(state) : ''}
                <section class="interaction-list-card">
                    ${renderActivityList(snapshot, currentActivity)}
                </section>
            </div>
            <div class="interaction-detail">
                ${renderActivityDetail(snapshot, currentActivity)}
                ${renderSignals(snapshot)}
            </div>
        </div>
    `;
}

function setLoading(root, loading) {
    root.querySelector('[data-interaction-loading]')?.toggleAttribute('hidden', !loading);
    root.querySelector('[data-interaction-content]')?.toggleAttribute('hidden', loading);
}

function showPanelError(root, message) {
    const content = root.querySelector('[data-interaction-content]');
    if (!content) return;
    content.innerHTML = `
        <div class="interaction-empty">
            <strong>课堂互动暂时不可用</strong>
            <p>${escapeHtml(message || '请稍后刷新重试。')}</p>
        </div>
    `;
    content.hidden = false;
}

function collectCreatePayload(form) {
    const kind = form.dataset.kind || 'poll';
    const formData = new FormData(form);
    const payload = {
        kind,
        title: String(formData.get('title') || ''),
        prompt: String(formData.get('prompt') || ''),
        allow_anonymous: formData.get('allow_anonymous') === 'on',
        show_results: String(formData.get('show_results') || ''),
        options: [],
    };
    if (kind !== 'qna') {
        const rows = Array.from(form.querySelectorAll('.interaction-option-editor-row'));
        const labels = rows.map((row) => String(row.querySelector('input[name="option_label"]')?.value || '').trim());
        const checkedRowIndex = rows.findIndex((row) => Boolean(row.querySelector('input[name="correct_option"]')?.checked));
        const correctIndex = checkedRowIndex >= 0 ? checkedRowIndex : 0;
        payload.options = labels
            .map((label, index) => ({
                label,
                is_correct: kind === 'quiz' && index === correctIndex,
            }))
            .filter((item) => item.label);
    }
    return payload;
}

function snapshotFromResponse(data) {
    return data?.snapshot || null;
}

export function initClassroomInteractions(config = {}) {
    const root = document.querySelector('[data-interaction-root]');
    if (!root) return null;

    const classOfferingId = Number(root.dataset.classOfferingId || config.classOfferingId || 0);
    if (!classOfferingId) return null;

    const state = {
        snapshot: null,
        selectedActivityId: null,
        createOpen: false,
        createKind: 'poll',
        createOptions: DEFAULT_OPTIONS,
        refreshTimer: null,
        pending: false,
    };

    const content = root.querySelector('[data-interaction-content]');

    const render = () => {
        if (!content || !state.snapshot) return;
        content.innerHTML = renderSnapshot(state.snapshot, state);
        content.hidden = false;
    };

    const refresh = async ({ silent = false } = {}) => {
        if (state.pending && silent) return;
        state.pending = true;
        if (!silent) setLoading(root, true);
        try {
            const data = await apiFetch(`/api/classroom-interactions/classrooms/${classOfferingId}/snapshot`, { silent: true });
            state.snapshot = snapshotFromResponse(data);
            render();
        } catch (error) {
            console.error('Failed to load classroom interactions:', error);
            if (!silent) showPanelError(root, error.message || '课堂互动加载失败');
        } finally {
            state.pending = false;
            setLoading(root, false);
        }
    };

    const scheduleRefresh = () => {
        if (state.refreshTimer) window.clearTimeout(state.refreshTimer);
        state.refreshTimer = window.setTimeout(() => {
            refresh({ silent: true }).catch(() => {});
        }, 260);
    };

    root.addEventListener('click', async (event) => {
        const target = event.target.closest('button, [data-interaction-select]');
        if (!target) return;

        const selectedId = target.dataset.interactionSelect;
        if (selectedId) {
            state.selectedActivityId = selectedId;
            render();
            return;
        }

        if (target.matches('[data-interaction-refresh]')) {
            await refresh();
            return;
        }

        if (target.matches('[data-interaction-create-open]')) {
            state.createOpen = true;
            render();
            return;
        }

        if (target.matches('[data-interaction-create-close]')) {
            state.createOpen = false;
            render();
            return;
        }

        const kind = target.dataset.interactionKind;
        if (kind) {
            const labels = Array.from(root.querySelectorAll('input[name="option_label"]'))
                .map((input) => String(input.value || '').trim())
                .filter(Boolean);
            state.createOptions = labels.length ? labels : DEFAULT_OPTIONS;
            state.createKind = kind;
            render();
            return;
        }

        if (target.matches('[data-interaction-add-option]')) {
            const list = root.querySelector('.interaction-option-list');
            const form = target.closest('form');
            const optionCount = list?.querySelectorAll('.interaction-option-editor-row').length || 0;
            if (!list || optionCount >= 8) {
                showToast('最多支持 8 个选项', 'warning');
                return;
            }
            list.insertAdjacentHTML('beforeend', renderOptionEditorRow('', optionCount, form?.dataset.kind || 'poll'));
            return;
        }

        if (target.matches('[data-interaction-remove-option]')) {
            const list = target.closest('.interaction-option-list');
            if ((list?.querySelectorAll('.interaction-option-editor-row').length || 0) <= 2) {
                showToast('至少保留两个选项', 'warning');
                return;
            }
            target.closest('.interaction-option-editor-row')?.remove();
            return;
        }

        const closeId = target.dataset.interactionClose;
        if (closeId) {
            const data = await apiFetch(`/api/classroom-interactions/activities/${closeId}/close`, { method: 'POST' });
            state.snapshot = snapshotFromResponse(data);
            render();
            showToast(data.message || '互动已结束', 'success');
            return;
        }

        const questionId = target.dataset.interactionResolveQuestion;
        if (questionId) {
            const data = await apiFetch(`/api/classroom-interactions/questions/${questionId}/resolve`, {
                method: 'POST',
                body: { status: target.dataset.status || 'addressed' },
            });
            state.snapshot = snapshotFromResponse(data);
            render();
            showToast(data.message || '问题状态已更新', 'success');
            return;
        }

        const signalType = target.dataset.interactionSignal;
        if (signalType) {
            const noteInput = root.querySelector('[data-interaction-signal-note] input[name="message"]');
            const data = await apiFetch(`/api/classroom-interactions/classrooms/${classOfferingId}/signals`, {
                method: 'POST',
                body: {
                    signal_type: signalType,
                    message: noteInput?.value || '',
                },
            });
            state.snapshot = snapshotFromResponse(data);
            render();
            showToast(data.message || '课堂状态已更新', 'success');
            return;
        }

        if (target.matches('[data-interaction-clear-signal]')) {
            const data = await apiFetch(`/api/classroom-interactions/classrooms/${classOfferingId}/signals/clear`, {
                method: 'POST',
            });
            state.snapshot = snapshotFromResponse(data);
            render();
            showToast(data.message || '课堂状态已清除', 'success');
            return;
        }

        const signalId = target.dataset.interactionResolveSignal;
        if (signalId) {
            const data = await apiFetch(`/api/classroom-interactions/signals/${signalId}/resolve`, { method: 'POST' });
            state.snapshot = snapshotFromResponse(data);
            render();
            showToast(data.message || '学生状态已处理', 'success');
        }
    });

    root.addEventListener('submit', async (event) => {
        const form = event.target;
        if (!(form instanceof HTMLFormElement)) return;

        if (form.matches('[data-interaction-create-form]')) {
            event.preventDefault();
            const data = await apiFetch(`/api/classroom-interactions/classrooms/${classOfferingId}/activities`, {
                method: 'POST',
                body: collectCreatePayload(form),
            });
            state.snapshot = snapshotFromResponse(data);
            state.createOpen = false;
            state.selectedActivityId = data.activity?.id || state.selectedActivityId;
            render();
            showToast(data.message || '课堂互动已发起', 'success');
            return;
        }

        const respondId = form.dataset.interactionRespond;
        if (respondId) {
            event.preventDefault();
            const formData = new FormData(form);
            const optionId = normalizeId(formData.get('option_id'));
            if (!optionId) {
                showToast('请选择一个选项', 'warning');
                return;
            }
            const data = await apiFetch(`/api/classroom-interactions/activities/${respondId}/respond`, {
                method: 'POST',
                body: { option_id: Number(optionId) },
            });
            state.snapshot = snapshotFromResponse(data);
            state.selectedActivityId = data.activity?.id || respondId;
            render();
            showToast(data.message || '回应已提交', 'success');
            return;
        }

        const questionId = form.dataset.interactionQuestion;
        if (questionId) {
            event.preventDefault();
            const formData = new FormData(form);
            const questionText = String(formData.get('question_text') || '').trim();
            if (!questionText) {
                showToast('请先写下问题', 'warning');
                return;
            }
            const data = await apiFetch(`/api/classroom-interactions/activities/${questionId}/questions`, {
                method: 'POST',
                body: {
                    question_text: questionText,
                    is_anonymous: formData.get('is_anonymous') === 'on',
                },
            });
            state.snapshot = snapshotFromResponse(data);
            state.selectedActivityId = questionId;
            render();
            showToast(data.message || '问题已提交', 'success');
            return;
        }

        if (form.matches('[data-interaction-signal-note]')) {
            event.preventDefault();
            const signalType = state.snapshot?.my_signal?.signal_type;
            if (!signalType) return;
            const formData = new FormData(form);
            const data = await apiFetch(`/api/classroom-interactions/classrooms/${classOfferingId}/signals`, {
                method: 'POST',
                body: {
                    signal_type: signalType,
                    message: String(formData.get('message') || ''),
                },
            });
            state.snapshot = snapshotFromResponse(data);
            render();
            showToast(data.message || '课堂状态已更新', 'success');
        }
    });

    window.addEventListener('classroom:interaction-ws', (event) => {
        const detail = event.detail || {};
        if (Number(detail.class_offering_id || 0) !== classOfferingId) return;
        scheduleRefresh();
    });

    refresh().catch(() => {});
    return {
        refresh,
        getSnapshot: () => state.snapshot,
    };
}
