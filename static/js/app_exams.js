import { apiFetch } from './api.js';
import { showToast, escapeHtml } from './ui.js';

let config = null;
const DEFAULT_SCHEDULE_MODE = 'permanent';

function getTrimmedInputValue(elementId) {
    const element = document.getElementById(elementId);
    return element ? element.value.trim() : '';
}

function isChecked(elementId) {
    return document.getElementById(elementId)?.checked === true;
}

function resetEmailNotificationChoice(prefix) {
    const checkbox = document.getElementById(`${prefix}-send-email-notification`);
    if (checkbox) {
        checkbox.checked = false;
    }
}

function getExamAssignFeedbackEl() {
    return document.getElementById('exam-assign-feedback');
}

function setExamAssignFeedback(type, message) {
    const feedback = getExamAssignFeedbackEl();
    if (!feedback) return;

    if (!message) {
        feedback.textContent = '';
        feedback.classList.add('hidden');
        feedback.removeAttribute('data-type');
        return;
    }

    feedback.textContent = message;
    feedback.dataset.type = type;
    feedback.classList.remove('hidden');
}

function getQuestionCount(paper) {
    try {
        const raw = typeof paper.questions_json === 'string'
            ? JSON.parse(paper.questions_json)
            : (paper.questions_json || {});
        return (raw.pages || []).reduce((total, page) => total + ((page.questions || []).length), 0);
    } catch {
        return 0;
    }
}

function getPageCount(paper) {
    try {
        const raw = typeof paper.questions_json === 'string'
            ? JSON.parse(paper.questions_json)
            : (paper.questions_json || {});
        return (raw.pages || []).length;
    } catch {
        return 0;
    }
}

function getConfirmButton() {
    return document.getElementById('exam-assign-confirm-btn');
}

function toDateTimeLocalValue(raw) {
    if (!raw) return '';
    const text = String(raw).trim();
    if (!text) return '';
    return text.replace(' ', 'T').slice(0, 16);
}

function syncScheduleFields(prefix) {
    const mode = getTrimmedInputValue(`${prefix}-availability-mode`) || DEFAULT_SCHEDULE_MODE;
    const deadlineGroup = document.getElementById(`${prefix}-deadline-group`);
    const countdownGroup = document.getElementById(`${prefix}-countdown-group`);
    const lateEnabledEl = document.getElementById(`${prefix}-late-submission-enabled`);

    if (deadlineGroup) {
        deadlineGroup.style.display = mode === 'deadline' ? '' : 'none';
    }
    if (countdownGroup) {
        countdownGroup.style.display = mode === 'countdown' ? '' : 'none';
    }
    if (lateEnabledEl) {
        lateEnabledEl.disabled = mode === DEFAULT_SCHEDULE_MODE;
        if (mode === DEFAULT_SCHEDULE_MODE) {
            lateEnabledEl.checked = false;
        }
    }
    syncLatePolicyFields(prefix);
}

function syncLatePolicyFields(prefix) {
    const enabled = document.getElementById(`${prefix}-late-submission-enabled`)?.checked || false;
    const group = document.getElementById(`${prefix}-late-policy-group`);
    const strategy = getTrimmedInputValue(`${prefix}-late-penalty-strategy`) || 'fixed';
    if (group) {
        group.style.display = enabled ? '' : 'none';
    }
    group?.querySelectorAll('[data-late-gradient-field]').forEach((node) => {
        node.style.display = enabled && strategy === 'gradient' ? '' : 'none';
    });
}

function bindScheduleMode(prefix) {
    const modeEl = document.getElementById(`${prefix}-availability-mode`);
    if (modeEl && modeEl.dataset.bound !== '1') {
        modeEl.dataset.bound = '1';
        modeEl.addEventListener('change', () => syncScheduleFields(prefix));
    }
    const lateEnabledEl = document.getElementById(`${prefix}-late-submission-enabled`);
    if (lateEnabledEl && lateEnabledEl.dataset.bound !== '1') {
        lateEnabledEl.dataset.bound = '1';
        lateEnabledEl.addEventListener('change', () => syncLatePolicyFields(prefix));
    }
    const strategyEl = document.getElementById(`${prefix}-late-penalty-strategy`);
    if (strategyEl && strategyEl.dataset.bound !== '1') {
        strategyEl.dataset.bound = '1';
        strategyEl.addEventListener('change', () => syncLatePolicyFields(prefix));
    }
    syncScheduleFields(prefix);
}

function resetScheduleFields(prefix, mode = DEFAULT_SCHEDULE_MODE) {
    const modeEl = document.getElementById(`${prefix}-availability-mode`);
    const dueEl = document.getElementById(`${prefix}-due-at`);
    const durationEl = document.getElementById(`${prefix}-duration-minutes`);
    const startsEl = document.getElementById(`${prefix}-starts-at`);
    const lateEnabledEl = document.getElementById(`${prefix}-late-submission-enabled`);
    const lateUntilEl = document.getElementById(`${prefix}-late-submission-until`);
    const lateStrategyEl = document.getElementById(`${prefix}-late-penalty-strategy`);
    const lateIntervalEl = document.getElementById(`${prefix}-late-penalty-interval-hours`);
    const latePointsEl = document.getElementById(`${prefix}-late-penalty-points`);
    const lateMinScoreEl = document.getElementById(`${prefix}-late-penalty-min-score`);
    const lateScoreCapEl = document.getElementById(`${prefix}-late-score-cap`);

    if (modeEl) modeEl.value = mode;
    if (dueEl) dueEl.value = '';
    if (durationEl) durationEl.value = '';
    if (startsEl) startsEl.value = '';
    if (lateEnabledEl) lateEnabledEl.checked = false;
    if (lateUntilEl) lateUntilEl.value = '';
    if (lateStrategyEl) lateStrategyEl.value = 'fixed';
    if (lateIntervalEl) lateIntervalEl.value = '1';
    if (latePointsEl) latePointsEl.value = '0';
    if (lateMinScoreEl) lateMinScoreEl.value = '0';
    if (lateScoreCapEl) lateScoreCapEl.value = '';
    syncScheduleFields(prefix);
}

function readNumericValue(elementId, label, { required = false, min = null, max = null, fallback = null } = {}) {
    const raw = getTrimmedInputValue(elementId);
    if (!raw) {
        if (required) return { error: `${label}不能为空` };
        return { value: fallback };
    }
    const value = Number(raw);
    if (!Number.isFinite(value)) {
        return { error: `${label}必须是数字` };
    }
    if (min !== null && value < min) {
        return { error: `${label}不能小于 ${min}` };
    }
    if (max !== null && value > max) {
        return { error: `${label}不能大于 ${max}` };
    }
    return { value };
}

function readLatePolicyPayload(prefix, mode) {
    const enabled = document.getElementById(`${prefix}-late-submission-enabled`)?.checked || false;
    const strategy = getTrimmedInputValue(`${prefix}-late-penalty-strategy`) === 'gradient' ? 'gradient' : 'fixed';
    const lateUntil = getTrimmedInputValue(`${prefix}-late-submission-until`);
    const interval = readNumericValue(`${prefix}-late-penalty-interval-hours`, '梯度间隔小时', {
        required: enabled && strategy === 'gradient',
        min: 0.1,
        fallback: 1,
    });
    if (interval.error) return { error: interval.error };
    const points = readNumericValue(`${prefix}-late-penalty-points`, '补交扣分', {
        required: enabled,
        min: 0,
        max: 100,
        fallback: 0,
    });
    if (points.error) return { error: points.error };
    const minScore = readNumericValue(`${prefix}-late-penalty-min-score`, '最低保留分', {
        min: 0,
        max: 100,
        fallback: 0,
    });
    if (minScore.error) return { error: minScore.error };
    const scoreCap = readNumericValue(`${prefix}-late-score-cap`, '补交最高分', {
        min: 0,
        max: 100,
        fallback: null,
    });
    if (scoreCap.error) return { error: scoreCap.error };
    if (enabled && mode === DEFAULT_SCHEDULE_MODE) {
        return { error: '补交扣分需要先设置首次截止时间或倒计时' };
    }
    if (enabled && scoreCap.value !== null && scoreCap.value < minScore.value) {
        return { error: '补交最高分不能低于最低保留分' };
    }
    return {
        payload: {
            late_submission_enabled: enabled ? 1 : 0,
            late_submission_until: enabled && lateUntil ? lateUntil : null,
            late_penalty_strategy: strategy,
            late_penalty_interval_hours: interval.value,
            late_penalty_points: points.value,
            late_penalty_min_score: minScore.value,
            late_score_cap: scoreCap.value,
        }
    };
}

function readSchedulePayload(prefix) {
    const mode = (getTrimmedInputValue(`${prefix}-availability-mode`) || DEFAULT_SCHEDULE_MODE).toLowerCase();
    const dueAt = getTrimmedInputValue(`${prefix}-due-at`);
    const durationText = getTrimmedInputValue(`${prefix}-duration-minutes`);
    const startsAt = getTrimmedInputValue(`${prefix}-starts-at`);
    const lateResult = readLatePolicyPayload(prefix, mode);
    if (lateResult.error) {
        return { error: lateResult.error };
    }

    if (mode === 'deadline') {
        if (!dueAt) {
            return { error: '请设置截止时间' };
        }
        return {
            payload: {
                availability_mode: mode,
                due_at: dueAt,
                duration_minutes: null,
                starts_at: null,
                ...lateResult.payload,
            }
        };
    }

    if (mode === 'countdown') {
        if (!durationText) {
            return { error: '请设置倒计时分钟数' };
        }
        const duration = Number(durationText);
        if (!Number.isFinite(duration) || duration <= 0) {
            return { error: '倒计时分钟数必须大于 0' };
        }
        return {
            payload: {
                availability_mode: mode,
                due_at: null,
                duration_minutes: Math.floor(duration),
                starts_at: startsAt || null,
                ...lateResult.payload,
            }
        };
    }

    return {
        payload: {
            availability_mode: DEFAULT_SCHEDULE_MODE,
            due_at: null,
            duration_minutes: null,
            starts_at: null,
            ...lateResult.payload,
        }
    };
}

export function init(appConfig) {
    config = appConfig;
    bindScheduleMode('assignment');
    bindScheduleMode('exam');
}

export async function loadExamPapers() {
    const container = document.getElementById('exam-list-container');
    if (!container) return;

    const allowedTypesEl = document.getElementById('exam-allowed-file-types');
    if (allowedTypesEl) {
        allowedTypesEl.value = '';
    }
    const stageEl = document.getElementById('exam-learning-stage-key');
    if (stageEl) {
        stageEl.value = '';
    }
    resetScheduleFields('exam');
    resetEmailNotificationChoice('exam');

    setExamAssignFeedback(null, '');
    container.innerHTML = '<div class="text-center p-4"><div class="spinner"></div></div>';

    try {
        const data = await apiFetch('/api/exam-papers', { silent: true });
        const papers = Array.isArray(data?.papers) ? data.papers : [];

        if (papers.length === 0) {
            container.innerHTML = `
                <div class="empty-state assignment-empty-state">
                    <h3>试卷库为空</h3>
                    <p class="text-muted">请先前往管理中心创建试卷，然后再发布到当前课堂。</p>
                    <a href="/manage/exams" class="btn btn-outline btn-sm">前往试卷库</a>
                </div>
            `;
            return;
        }

        container.innerHTML = papers.map((paper) => {
            const pageCount = getPageCount(paper);
            const questionCount = getQuestionCount(paper);
            const desc = paper.description
                ? escapeHtml(String(paper.description).slice(0, 80))
                : '未填写试卷说明。';

            return `
                <label class="exam-paper-option">
                    <input type="radio" name="exam-paper" value="${escapeHtml(paper.id)}" class="exam-paper-radio shrink-0">
                    <div class="exam-paper-option-main">
                        <div class="exam-paper-option-title">${escapeHtml(paper.title)}</div>
                        <p class="exam-paper-option-desc">${desc}</p>
                        <div class="exam-paper-option-meta">
                            <span class="badge badge-primary">${pageCount} 个部分</span>
                            <span class="badge badge-outline">${questionCount} 道题</span>
                            <span class="badge badge-outline">${paper.status === 'published' ? '已发布' : '试卷库'}</span>
                        </div>
                    </div>
                </label>
            `;
        }).join('');
    } catch (error) {
        console.error('Failed to load exam papers:', error);
        container.innerHTML = `
            <div class="inline-feedback" data-type="error">
                试卷列表加载失败，请刷新后重试。
            </div>
        `;
        setExamAssignFeedback('error', error.message || '试卷列表加载失败，请稍后重试。');
    }
}

export async function confirmExamAssign() {
    const selected = document.querySelector('input[name="exam-paper"]:checked');
    if (!selected) {
        setExamAssignFeedback('error', '请先从试卷库中选择一份试卷。');
        showToast('请先选择一份试卷', 'warning');
        return;
    }

    const paperId = String(selected.value || '').trim();
    if (!paperId) {
        setExamAssignFeedback('error', '试卷标识无效，请重新选择。');
        showToast('试卷标识无效，请重新选择', 'warning');
        return;
    }

    const scheduleResult = readSchedulePayload('exam');
    if (scheduleResult.error) {
        setExamAssignFeedback('error', scheduleResult.error);
        showToast(scheduleResult.error, 'warning');
        return;
    }

    const btn = getConfirmButton();
    if (btn) {
        btn.disabled = true;
        btn.textContent = '发布中...';
    }

    setExamAssignFeedback(null, '');

    try {
        const result = await apiFetch(`/api/exam-papers/${encodeURIComponent(paperId)}/assign`, {
            method: 'POST',
            body: {
                paper_id: paperId,
                class_offering_id: config.classOfferingId,
                allowed_file_types: getTrimmedInputValue('exam-allowed-file-types'),
                learning_stage_key: getTrimmedInputValue('exam-learning-stage-key'),
                send_email_notification: isChecked('exam-send-email-notification'),
                ...scheduleResult.payload,
            },
            silent: true
        });

        const message = result?.message || '试卷已成功加入当前课堂。';
        setExamAssignFeedback('success', message);
        showToast('试卷已发布', 'success');

        if (window.UI) {
            window.UI.closeModal('exam-assign-modal');
        }
        setTimeout(() => window.location.reload(), 500);
    } catch (error) {
        console.error('Failed to assign exam paper:', error);
        const message = error?.message || '发布失败，请稍后重试。';
        setExamAssignFeedback('error', message);
        showToast(`发布失败：${message}`, 'error');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = '确认发布';
        }
    }
}

export async function saveAssignment() {
    const idEl = document.getElementById('assignment-id');
    const titleEl = document.getElementById('assignment-title');
    const reqEl = document.getElementById('assignment-requirements');
    const rubricEl = document.getElementById('assignment-rubric');
    const modeEl = document.getElementById('assignment-grading-mode');

    const title = titleEl ? titleEl.value.trim() : '';
    if (!title) {
        showToast('请输入作业标题', 'warning');
        return;
    }
    const scheduleResult = readSchedulePayload('assignment');
    if (scheduleResult.error) {
        showToast(scheduleResult.error, 'warning');
        return;
    }

    const assignmentId = idEl ? idEl.value : '';
    const btn = document.getElementById('btn-save-assignment');
    if (btn) {
        btn.disabled = true;
        btn.textContent = '保存中...';
    }

    const body = {
        title,
        requirements_md: reqEl ? reqEl.value : '',
        rubric_md: rubricEl ? rubricEl.value : '',
        grading_mode: modeEl ? modeEl.value : 'manual',
        class_offering_id: config.classOfferingId,
        allowed_file_types: getTrimmedInputValue('assignment-allowed-file-types'),
        learning_stage_key: getTrimmedInputValue('assignment-learning-stage-key'),
        send_email_notification: isChecked('assignment-send-email-notification'),
        ...scheduleResult.payload,
    };

    try {
        if (assignmentId) {
            await apiFetch(`/api/assignments/${assignmentId}`, {
                method: 'PUT',
                body
            });
            showToast('作业已更新', 'success');
        } else {
            await apiFetch(`/api/courses/${config.courseId}/assignments`, {
                method: 'POST',
                body
            });
            showToast('作业已创建', 'success');
        }

        if (window.UI) {
            window.UI.closeModal('assignment-modal');
        }
        setTimeout(() => window.location.reload(), 500);
    } catch (error) {
        console.error('Failed to save assignment:', error);
        showToast(`保存失败：${error.message || '未知错误'}`, 'error');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = '保存作业';
        }
    }
}

export function editAssignment(
    assignmentId,
    title,
    requirements,
    rubric,
    gradingMode,
    allowedFileTypes = '',
    schedule = null,
    learningStageKey = '',
    latePolicy = null,
) {
    const idEl = document.getElementById('assignment-id');
    const titleEl = document.getElementById('assignment-title');
    const reqEl = document.getElementById('assignment-requirements');
    const rubricEl = document.getElementById('assignment-rubric');
    const modeEl = document.getElementById('assignment-grading-mode');
    const allowedTypesEl = document.getElementById('assignment-allowed-file-types');
    const stageEl = document.getElementById('assignment-learning-stage-key');
    const scheduleModeEl = document.getElementById('assignment-availability-mode');
    const dueAtEl = document.getElementById('assignment-due-at');
    const durationEl = document.getElementById('assignment-duration-minutes');
    const startsAtEl = document.getElementById('assignment-starts-at');
    const lateEnabledEl = document.getElementById('assignment-late-submission-enabled');
    const lateUntilEl = document.getElementById('assignment-late-submission-until');
    const lateStrategyEl = document.getElementById('assignment-late-penalty-strategy');
    const lateIntervalEl = document.getElementById('assignment-late-penalty-interval-hours');
    const latePointsEl = document.getElementById('assignment-late-penalty-points');
    const lateMinScoreEl = document.getElementById('assignment-late-penalty-min-score');
    const lateScoreCapEl = document.getElementById('assignment-late-score-cap');

    if (idEl) idEl.value = assignmentId || '';
    if (titleEl) titleEl.value = title || '';
    if (reqEl) reqEl.value = requirements || '';
    if (rubricEl) rubricEl.value = rubric || '';
    if (modeEl) modeEl.value = gradingMode || 'manual';
    if (allowedTypesEl) allowedTypesEl.value = allowedFileTypes || '';
    if (stageEl) stageEl.value = learningStageKey || '';
    if (scheduleModeEl) scheduleModeEl.value = schedule?.availability_mode || DEFAULT_SCHEDULE_MODE;
    if (dueAtEl) dueAtEl.value = toDateTimeLocalValue(schedule?.due_at);
    if (durationEl) durationEl.value = schedule?.duration_minutes || '';
    if (startsAtEl) startsAtEl.value = toDateTimeLocalValue(schedule?.starts_at);
    if (lateEnabledEl) lateEnabledEl.checked = Boolean(latePolicy?.late_submission_enabled);
    if (lateUntilEl) lateUntilEl.value = toDateTimeLocalValue(latePolicy?.late_submission_until);
    if (lateStrategyEl) lateStrategyEl.value = latePolicy?.late_penalty_strategy || 'fixed';
    if (lateIntervalEl) lateIntervalEl.value = latePolicy?.late_penalty_interval_hours || '1';
    if (latePointsEl) latePointsEl.value = latePolicy?.late_penalty_points ?? '0';
    if (lateMinScoreEl) lateMinScoreEl.value = latePolicy?.late_penalty_min_score ?? '0';
    if (lateScoreCapEl) lateScoreCapEl.value = latePolicy?.late_score_cap ?? '';
    resetEmailNotificationChoice('assignment');
    syncScheduleFields('assignment');

    setExamAssignFeedback(null, '');
    if (window.UI) {
        window.UI.openModal('assignment-modal');
    }
}

export function newAssignment() {
    editAssignment('', '', '', '', 'manual', '', { availability_mode: DEFAULT_SCHEDULE_MODE }, '');
}
