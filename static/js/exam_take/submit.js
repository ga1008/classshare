/**
 * Submission operations for one exam page. The caller owns the live state,
 * serialized draft queue, upload managers, DOM and browser side effects.
 * Importing this module does not initialize any browser or request resources.
 */
export function createExamSubmissionController({ context, state, live, ports }) {
    // The opened round and start time must never follow a later server response.
    const { assignmentId, submissionVersion, examStartedAt, submissionExists, localDraftKey, restoreSyncDelayMs } = context;

    async function loadServerDraft() {
        if (submissionExists && !live.isEditingResubmission) return;
        try {
            const draft = await ports.apiFetch(`/api/assignments/${assignmentId}/draft`, { method: 'GET', silent: true });
            live.serverDraftLoadDone = true;
            if (draft?.submission_version && draft.submission_version !== submissionVersion) {
                live.serverDraftConflict = true;
                ports.setSaveStatus('error', '提交轮次已变化，本页作答仍保留；请刷新后核对。');
                return;
            }
            if (!draft?.exists) {
                if (live.lastLocalSavedAt) ports.scheduleServerDraftSave(restoreSyncDelayMs);
                return;
            }
            const serverTime = Date.parse(draft.server_updated_at || '') || 0;
            const localTime = Date.parse(live.lastLocalSavedAt || '') || 0;
            if (serverTime >= localTime) {
                ports.applyServerDraft(draft);
                ports.showMessage('已恢复服务器自动保存的作答进度。', 'success');
            } else {
                ports.applyServerDraftFiles(draft);
                ports.scheduleServerDraftSave(restoreSyncDelayMs);
                ports.showMessage('检测到本机草稿更新，正在同步到服务器。', 'info');
            }
        } catch (error) {
            live.serverDraftLoadDone = true;
            ports.warn('Failed to load server draft:', error);
            ports.setSaveStatus('error', '服务器草稿暂不可用');
        }
    }

    async function performServerDraftSave({ uploadItems = [], replaceQuestionIds = [] } = {}) {
        if (live.submissionSucceeded) return null;
        if (submissionExists && !live.isEditingResubmission) return null;
        if (live.serverDraftConflict) {
            const error = new Error('提交轮次已变化，本页作答仍保留；请刷新后核对。');
            error.status = 409;
            throw error;
        }
        const normalizedUploadItems = Array.isArray(uploadItems) ? uploadItems : [];
        const normalizedReplaceQuestionIds = Array.isArray(replaceQuestionIds) ? replaceQuestionIds : [];
        const formData = ports.createFormData();
        const manifest = [];
        const extraAttachments = [];
        normalizedUploadItems.forEach((item) => {
            if (!item?.file) return;
            formData.append('files', item.file, item.file.name || 'upload.bin');
            manifest.push({
                relative_path: item.relative_path,
                content_type: item.file.type || item.mime_type || '',
                kind: item.kind || 'file',
                question_id: item.question_id || '',
                question: item.question || '',
            });
            extraAttachments.push({
                kind: item.kind === 'exam_drawing' ? 'drawing' : ((item.file.type || '').startsWith('image/') ? 'image' : 'file'),
                file_name: item.file.name || 'upload.bin',
                relative_path: item.relative_path,
                mime_type: item.file.type || item.mime_type || '',
                file_size: item.file.size || 0,
                question_id: item.question_id || '',
                question: item.question || '',
            });
        });
        const answersList = ports.buildAnswersListForDraft(extraAttachments, normalizedReplaceQuestionIds);
        const uploadSignature = normalizedUploadItems.map((item) => ({
            relative_path: item?.relative_path || '',
            name: item?.file?.name || '',
            size: item?.file?.size || 0,
            kind: item?.kind || '',
            question_id: item?.question_id || '',
        }));
        const draftSignature = JSON.stringify({
            answers: answersList,
            current_page: state.currentPageIdx || 0,
            replace_question_ids: normalizedReplaceQuestionIds,
            uploads: uploadSignature,
        });
        const hasFileSideEffects = uploadSignature.length > 0 || normalizedReplaceQuestionIds.length > 0;
        if (!hasFileSideEffects && draftSignature === live.lastServerDraftSignature) {
            return {
                status: 'success',
                files_by_question: state.serverQuestionFiles || {},
                stored_file_count: 0,
                dropped_file_count: 0,
            };
        }
        formData.append('answers_json', JSON.stringify({ answers: answersList }));
        formData.append('expected_submission_version', submissionVersion);
        formData.append('current_page', String(state.currentPageIdx || 0));
        formData.append('client_updated_at', live.lastLocalSavedAt || ports.nowIso());
        formData.append('replace_question_ids', JSON.stringify(normalizedReplaceQuestionIds));
        formData.append('manifest', JSON.stringify(manifest));

        ports.setSaveStatus('syncing', '正在同步到服务器...');
        live.serverDraftInFlight = ports.apiFetch(`/api/assignments/${assignmentId}/draft`, {
            method: 'POST',
            body: formData,
            silent: true,
        });
        try {
            const resp = await live.serverDraftInFlight;
            live.lastServerDraftSignature = draftSignature;
            state.serverQuestionFiles = resp.files_by_question || {};
            ports.applyServerFilesToManagers();
            ports.setSaveStatus('saved', '已保存到服务器');
            ports.setTimeout(() => {
                if (live.submissionSucceeded) return;
                ports.setSaveStatus('', '本地与服务器自动保存');
            }, 2000);
            return resp;
        } catch (error) {
            const message = ports.getApiFailureMessage(error, '服务器保存失败，本地草稿仍在。请按提示调整后重新保存。');
            if (error?.status === 409) live.serverDraftConflict = true;
            ports.setSaveStatus('error', ports.escapeStatusText(message));
            error.message = message;
            throw error;
        } finally {
            live.serverDraftInFlight = null;
        }
    }

    async function handleSubmission() {
        if (live.submissionSucceeded) return;
        if (!live.assignmentAcceptingSubmissions) {
            ports.showMessage('已超过允许提交时间，系统正在以服务器时间为准拦截交卷。', 'warning');
            return;
        }
        const allQuestions = [];
        state.pages.forEach(p => (p.questions || []).forEach(q => allQuestions.push(q)));

        const unanswered = allQuestions.filter(q => !ports.answerHasContent(q));
        const hasAnswered = allQuestions.some(q => ports.answerHasContent(q));
        const hasQuestionUploads = allQuestions.some(q => ports.hasDrawing(q.id) || ports.hasQuestionFiles(q.id));
        const hasFiles = (live.uploadManager?.hasFiles() ?? false) || hasQuestionUploads;
        ports.getBehaviorTracker()?.log('exam_submit_attempt', '尝试提交试卷', {
            total_questions: allQuestions.length,
            unanswered_count: unanswered.length,
            has_answered: hasAnswered,
            has_files: hasFiles
        }, 'exam_take');

        if (!hasAnswered && !hasFiles) {
            ports.showMessage('请至少作答一道题或上传附件后再提交。', 'warning');
            return;
        }

        const attachmentRequirementError = ports.validateQuestionAttachmentRequirements(allQuestions);
        if (attachmentRequirementError) {
            ports.showMessage(attachmentRequirementError.message, 'warning');
            ports.scrollToQuestion(attachmentRequirementError.question.id);
            return;
        }

        if (unanswered.length > 0) {
            if (!ports.confirm(`还有 ${unanswered.length} 道题未作答，确定要强行交卷吗？`)) return;
        } else {
            const confirmText = live.isEditingResubmission
                ? '确定要重新提交试卷吗？新的提交会替换当前版本。'
                : '确定要提交试卷吗？提交后不可修改。';
            if (!ports.confirm(confirmText)) return;
        }

        ports.setSubmitBusy(true);

        try {
            ports.clearTimeout(live.serverDraftSaveTimer);
            ports.clearQuestionDraftUploadTimers();
            let useServerDraftFiles = false;
            try {
                const pendingPayload = await ports.buildPendingQuestionDraftPayload();
                const draftResp = await ports.saveServerDraft(pendingPayload);
                ports.markPendingQuestionDraftsClean(pendingPayload);
                useServerDraftFiles = Boolean(draftResp);
                if (Number(draftResp?.dropped_file_count || 0) > 0) {
                    const message = ports.collectUploadIssueMessage(
                        draftResp,
                        '部分题目附件没有保存到服务器草稿，请检查文件类型、大小和题目附件要求后重新提交。',
                    );
                    throw new Error(message);
                }
                const unsyncedQuestion = useServerDraftFiles ? ports.findUnsyncedQuestionDraft(allQuestions) : null;
                if (unsyncedQuestion) {
                    throw new Error(`第 ${ports.getQuestionDisplayLabel(unsyncedQuestion)} 题附件还没有完整同步到服务器草稿，请稍后重试。`);
                }
            } catch (draftError) {
                ports.warn('Final draft sync failed before submit:', draftError);
                if (draftError?.status === 409) throw draftError;
                const hasServerDraftFiles = Object.values(state.serverQuestionFiles || {}).some((files) => Array.isArray(files) && files.length > 0);
                if (hasServerDraftFiles) {
                    const message = ports.getApiFailureMessage(
                        draftError,
                        '服务器草稿同步失败，已暂停提交，避免旧附件被误交。请检查网络后重新提交。',
                    );
                    throw new Error(`${message} 已暂停提交，避免旧附件被误交。请按提示调整后重新提交。`);
                }
                ports.showMessage('服务器草稿同步失败，将继续使用当前页面内容提交。', 'warning');
            }
            const refreshedAttachmentRequirementError = ports.validateQuestionAttachmentRequirements(allQuestions);
            if (refreshedAttachmentRequirementError) {
                ports.showMessage(refreshedAttachmentRequirementError.message, 'warning');
                ports.scrollToQuestion(refreshedAttachmentRequirementError.question.id);
                ports.setSubmitBusy(false);
                return;
            }
            const formData = live.uploadManager ? live.uploadManager.buildFormData() : ports.createFormData();
            let questionAttachmentEntries = [];
            let drawingEntries = [];
            if (!useServerDraftFiles) {
                questionAttachmentEntries = ports.appendQuestionAttachmentFiles(formData, allQuestions);
                drawingEntries = await ports.appendDrawingFiles(formData, allQuestions);
            }
            ports.validateFormDataFileLimits(formData);
            const attachmentMap = new Map();
            if (useServerDraftFiles) {
                Object.entries(state.serverQuestionFiles || {}).forEach(([qid, files]) => {
                    (files || []).forEach((file) => {
                        if (!attachmentMap.has(qid)) attachmentMap.set(qid, []);
                        attachmentMap.get(qid).push({
                            kind: file.kind === 'exam_drawing' ? 'drawing' : (file.kind || (file.is_image ? 'image' : 'file')),
                            file_name: file.file_name || file.original_filename || '题目附件',
                            relative_path: file.relative_path || '',
                            mime_type: file.mime_type || '',
                            file_size: file.file_size || 0,
                            question_id: qid,
                            question: ports.findQuestionById(qid)?.text || '',
                        });
                    });
                });
            }
            [...questionAttachmentEntries, ...drawingEntries].forEach((entry) => {
                if (!attachmentMap.has(entry.question_id)) attachmentMap.set(entry.question_id, []);
                attachmentMap.get(entry.question_id).push(entry);
            });
            const answersList = allQuestions.map(q => {
                return {
                    question_id: q.id,
                    question: q.text,
                    type: q.type || '',
                    answer: state.answers[q.id] || '',
                    attachments: attachmentMap.get(q.id) || [],
                };
            });
            formData.append('answers_json', JSON.stringify({ answers: answersList }));
            formData.append('started_at', examStartedAt);
            formData.append('use_server_draft', useServerDraftFiles ? '1' : '0');
            formData.append('expected_submission_version', submissionVersion);

            const resp = await ports.apiFetch(`/api/assignments/${assignmentId}/submit`, {
                method: 'POST',
                body: formData
            });
            ports.onSubmissionSucceeded();

            const droppedCount = Number(resp?.dropped_file_count || 0);
            ports.showMessage(
                droppedCount > 0
                    ? ports.collectUploadIssueMessage(resp, `交卷成功，但 ${droppedCount} 个不符合要求的文件已被过滤。`)
                    : (resp?.is_late_submission ? '交卷成功，已按补交规则记录，批改后会自动扣分。' : '交卷成功！'),
                droppedCount > 0 ? 'warning' : 'success'
            );
            ports.removeLocalDraft(localDraftKey);

            // Group exam: prompt teammate peer-evaluation before showing result.
            const openGroupPeerEval = ports.getOpenGroupPeerEval();
            if (openGroupPeerEval) {
                try { await openGroupPeerEval(assignmentId); } catch (err) {}
            }

            // Reload to show submitted state
            ports.setTimeout(() => ports.reload(), 1000);

        } catch(e) {
            ports.showMessage(ports.getApiFailureMessage(e, '提交失败，请按提示调整后重试。'), 'error');
            ports.setSubmitBusy(false);
        }
    }

    return { loadServerDraft, performServerDraftSave, handleSubmission };
}
