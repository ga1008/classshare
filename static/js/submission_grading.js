/** Manual grading keeps the reviewed versions until the teacher rechecks the page. */
export function setupSubmissionGrading(options) {
    const {
        submissionId, assignmentId, teacherId, expectedReviewRevision,
        expectedAssignmentRevision, initialScore, initialFeedback, requirements,
        rubric, showToast,
    } = options;
    const byId = (id) => document.getElementById(id);
    const scoreInput = byId('grade-score');
    const feedbackInput = byId('grade-feedback');
    const saveButton = byId('grade-save');
    const card = byId('submission-grading-card');
    const conflict = byId('grade-conflict');
    const refreshButton = byId('grade-conflict-refresh');
    const confirmButton = byId('grade-conflict-confirm');
    const discardButton = byId('grade-conflict-discard');
    const draftKey = `lanshare:manual-grade:${teacherId}:${submissionId}`;
    let busy = false;
    let needsReview = false;
    let recoveredDraft = null;
    let disabledControls = [];
    let explicitNavigation = false;

    function setStatus(message) {
        const status = byId('grade-save-status');
        if (status) {
            status.textContent = message;
            status.hidden = !message;
        }
    }

    function setBusy(value) {
        busy = value;
        card?.setAttribute('aria-busy', String(value));
        if (value) {
            disabledControls = [...(card?.querySelectorAll('button, input, textarea') || [])]
                .filter((control) => !control.disabled);
            disabledControls.forEach((control) => { control.disabled = true; });
        } else {
            disabledControls.forEach((control) => { control.disabled = false; });
            disabledControls = [];
            if (saveButton) saveButton.disabled = needsReview;
        }
        if (saveButton) saveButton.textContent = value ? '正在保存…' : '保存评分';
    }

    function showConflict(message, { refreshed = false } = {}) {
        needsReview = true;
        if (saveButton) saveButton.disabled = true;
        conflict.hidden = false;
        byId('grade-conflict-message').textContent = message;
        refreshButton.hidden = refreshed;
        confirmButton.hidden = !refreshed || !saveButton;
        discardButton.hidden = false;
        if (!refreshed) {
            byId('grade-conflict-summary').textContent = '当前分数和评语已保留，尚未保存。请重新加载最新答卷、评分与作业要求，核对后再保存。';
            // A second conflict must not leave the previous comparison looking current.
            byId('grade-conflict-latest').hidden = true;
        }
    }

    function clearDraft() {
        try { sessionStorage.removeItem(draftKey); } catch { /* The visible form remains usable. */ }
    }

    function allowReviewChange() {
        if (busy) return false;
        if (!needsReview && !recoveredDraft) return true;
        setStatus('评分草稿尚未保存，请先核对并保存评分，或放弃本地修改，再执行此操作。');
        if (conflict) {
            conflict.hidden = false;
            if (!needsReview) {
                byId('grade-conflict-message').textContent = '本地草稿已核对但尚未保存。请先保存评分或放弃本地修改。';
            }
            conflict.focus();
        }
        return false;
    }

    window.addEventListener('beforeunload', (event) => {
        if (explicitNavigation || (!needsReview && !recoveredDraft)) return;
        event.preventDefault();
        event.returnValue = '';
    });

    // Recovery only happens after an explicit conflict refresh, within this tab
    // and teacher account. Never silently adopt a newer token after a failed save.
    try {
        const draft = JSON.parse(sessionStorage.getItem(draftKey) || 'null');
        if (draft && typeof draft.score === 'string' && typeof draft.feedback === 'string') {
            recoveredDraft = draft;
        }
    } catch { /* A corrupt/unavailable cache never clears the current form. */ }

    if (recoveredDraft && conflict) {
        if (scoreInput && feedbackInput) {
            scoreInput.value = recoveredDraft.score;
            feedbackInput.value = recoveredDraft.feedback;
        }
        showConflict(saveButton
            ? '已加载最新答卷，本地评分草稿已恢复；请核对后再保存。'
            : '最新答卷暂不可评分；本地草稿已保留，可复制或放弃。', { refreshed: true });
        const formatScore = (score) => score === null || score === undefined || score === '' ? '未评分' : String(score);
        const changes = [
            `服务器原始分：${formatScore(recoveredDraft.initialScore)} → ${formatScore(initialScore)}`,
            recoveredDraft.reviewRevision !== expectedReviewRevision ? '答卷或评分已变化' : '答卷与评分版本未变化',
            recoveredDraft.assignmentRevision !== expectedAssignmentRevision ? '作业要求或评分标准版本已变化' : '作业版本未变化',
        ];
        byId('grade-conflict-summary').textContent = changes.join('；');
        byId('grade-conflict-latest').hidden = false;
        byId('grade-conflict-server-score').textContent = `最新原始分：${formatScore(initialScore)}`;
        byId('grade-conflict-server-feedback').textContent = initialFeedback || '暂无评语';
        byId('grade-conflict-requirements').textContent = requirements || '未设置作业要求';
        byId('grade-conflict-rubric').textContent = rubric || '未设置评分标准';
        byId('grade-conflict-draft').hidden = false;
        byId('grade-conflict-draft-content').textContent = `本地原始分：${recoveredDraft.score}\n${recoveredDraft.feedback}`;
    }

    refreshButton?.addEventListener('click', () => {
        if (busy) return;
        const draft = {
            score: scoreInput?.value ?? recoveredDraft?.score ?? '',
            feedback: feedbackInput?.value ?? recoveredDraft?.feedback ?? '',
            initialScore,
            reviewRevision: expectedReviewRevision,
            assignmentRevision: expectedAssignmentRevision,
        };
        try {
            sessionStorage.setItem(draftKey, JSON.stringify(draft));
        } catch {
            byId('grade-conflict-summary').textContent = '浏览器无法暂存草稿，未重新加载。请先复制分数和评语，再手动刷新核对。';
            return;
        }
        refreshButton.disabled = true;
        explicitNavigation = true;
        window.location.reload();
    });

    confirmButton?.addEventListener('click', () => {
        if (busy || !saveButton) return;
        needsReview = false;
        saveButton.disabled = false;
        conflict.hidden = true;
        confirmButton.hidden = true;
        setStatus('已核对最新版本；本地评分仍未保存。');
        scoreInput?.focus();
    });

    discardButton?.addEventListener('click', () => {
        if (busy) return;
        clearDraft();
        explicitNavigation = true;
        window.location.reload();
    });

    async function submitGrade() {
        if (busy || needsReview || !scoreInput || !feedbackInput || !saveButton) return;
        const scoreText = scoreInput.value.trim();
        const score = Number(scoreText);
        if (!scoreText || !Number.isFinite(score) || score < 0 || score > 100) {
            setStatus('请输入有效得分（0–100，可填写小数）。空分数不能记为零分。');
            scoreInput.focus();
            return;
        }
        if (!expectedReviewRevision || !expectedAssignmentRevision) {
            showConflict('缺少答卷或作业版本，尚未保存。请重新核对。');
            return;
        }
        setBusy(true);
        setStatus('正在保存评分…');
        let succeeded = false;
        try {
            const response = await fetch(`/api/submissions/${submissionId}/grade`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    score, feedback_md: feedbackInput.value,
                    expected_review_revision: expectedReviewRevision,
                    expected_assignment_revision: expectedAssignmentRevision,
                }),
            });
            const data = await response.json().catch(() => ({}));
            if (response.status === 409) {
                setStatus('');
                showConflict(typeof data.detail === 'string' ? data.detail : '答卷或评分标准已更新，本次修改尚未保存。');
                conflict.focus();
                return;
            }
            if (!response.ok || data.status !== 'success') {
                throw new Error(typeof data.detail === 'string' ? data.detail : '保存评分失败，请稍后重试。');
            }
            succeeded = true;
            clearDraft();
            explicitNavigation = true;
            setStatus('评分已保存，正在返回作业。');
            showToast('评分已保存', 'success');
            setTimeout(() => { window.location.href = `/assignment/${encodeURIComponent(assignmentId)}`; }, 800);
        } catch (error) {
            setStatus(error.message || '保存评分失败，请稍后重试。');
        } finally {
            // Keep the lock through navigation so a second click cannot create
            // another grade ledger entry or repeat grading side effects.
            if (!succeeded) setBusy(false);
        }
    }

    saveButton?.addEventListener('click', submitGrade);
    byId('grade-cancel')?.addEventListener('click', () => {
        if (allowReviewChange()) window.history.back();
    });
    return { submitGrade, isBusy: () => busy, allowReviewChange };
}
