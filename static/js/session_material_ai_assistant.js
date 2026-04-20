import { apiFetch } from '/static/js/api.js';
import { closeModal, formatSize, openModal, showToast } from '/static/js/ui.js';

const MODAL_ID = 'sessionMaterialAiModal';
const POLL_INTERVAL_MS = 4500;
const AUTO_REFERENCE_SOFT_LIMIT = 4;

function refs() {
    return {
        modal: document.getElementById(MODAL_ID),
        closeBtn: document.getElementById('sessionMaterialAiCloseBtn'),
        cancelBtn: document.getElementById('sessionMaterialAiCancelBtn'),
        guidedSubmitBtn: document.getElementById('sessionMaterialAiGuidedSubmitBtn'),
        autoBtn: document.getElementById('sessionMaterialAiAutoBtn'),
        fileInput: document.getElementById('sessionMaterialAiFileInput'),
        pickFilesBtn: document.getElementById('sessionMaterialAiPickFilesBtn'),
        fileList: document.getElementById('sessionMaterialAiFileList'),
        guidedDocumentType: document.getElementById('sessionMaterialAiGuidedDocumentType'),
        guidedRequirementText: document.getElementById('sessionMaterialAiGuidedRequirementText'),
        autoDocumentType: document.getElementById('sessionMaterialAiAutoDocumentType'),
        autoRequirementText: document.getElementById('sessionMaterialAiAutoRequirementText'),
        footerCopy: document.getElementById('sessionMaterialAiFooterCopy'),
        sessionNumber: document.getElementById('sessionMaterialAiSessionNumber'),
        sessionTitle: document.getElementById('sessionMaterialAiSessionTitle'),
        sessionMeta: document.getElementById('sessionMaterialAiSessionMeta'),
        referenceHint: document.getElementById('sessionMaterialAiReferenceHint'),
        existingMaterialHint: document.getElementById('sessionMaterialAiExistingMaterialHint'),
        guidedHint: document.getElementById('sessionMaterialAiGuidedHint'),
        autoHint: document.getElementById('sessionMaterialAiAutoHint'),
        autoScopeHint: document.getElementById('sessionMaterialAiAutoScopeHint'),
        aiBtn: document.getElementById('teachingTimelineAiMaterialBtn'),
        taskStrip: document.getElementById('teachingTimelineAiTaskStrip'),
        taskPill: document.getElementById('teachingTimelineAiTaskPill'),
        taskCopy: document.getElementById('teachingTimelineAiTaskCopy'),
    };
}

function inferDocumentType(session) {
    const existing = String(session?.material_generation_task?.document_type || '').trim();
    if (existing) return existing;
    const context = `${session?.title || ''}\n${session?.content || ''}`;
    if (context.includes('实验')) return '实验指导';
    if (context.includes('复习') || context.includes('总结')) return '复习提纲';
    if (context.includes('案例')) return '案例讲义';
    return '课堂学习文档';
}

function buildTaskCopy(task) {
    if (!task) {
        return {
            hidden: true,
            pill: '',
            copy: '',
        };
    }

    if (task.status === 'queued' || task.status === 'running') {
        return {
            hidden: false,
            pill: task.status_label || '助教在思考',
            copy: 'AI 助教正在参考历史文档、课堂信息和你的要求，完成后会自动绑定到当前课时。',
        };
    }

    if (task.status === 'completed') {
        const materialPath = String(task.generated_material_path || '').trim();
        return {
            hidden: false,
            pill: task.status_label || '已生成',
            copy: materialPath
                ? `最近一次生成已完成，并自动绑定到 ${materialPath}。`
                : '最近一次生成已完成，可以直接打开文档，也可以再次发起生成。',
        };
    }

    if (task.status === 'failed') {
        return {
            hidden: false,
            pill: task.status_label || '生成失败',
            copy: task.error_message || '上一次生成未完成，可以调整要求后重新尝试。',
        };
    }

    return {
        hidden: true,
        pill: '',
        copy: '',
    };
}

function buildButtonLabel(task) {
    if (!task) return 'AI助教';
    if (task.status === 'queued' || task.status === 'running') return '助教在思考';
    if (task.status === 'completed') return '再次生成';
    if (task.status === 'failed') return '重新生成';
    return 'AI助教';
}

function dedupeFiles(files) {
    const seen = new Set();
    return files.filter((file) => {
        const key = `${file.name}::${file.size}::${file.lastModified}`;
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
    });
}

export function initSessionMaterialAiAssistant({
    classOfferingId,
    getSessions,
    getCurrentSession,
    onSessionPatch,
}) {
    const state = {
        initialized: false,
        files: [],
        submitting: false,
        pollingTimer: 0,
        knownTaskStates: new Map(),
    };

    const resetDraft = () => {
        state.files = [];
        const dom = refs();
        if (dom.fileInput) dom.fileInput.value = '';
        if (dom.guidedRequirementText) dom.guidedRequirementText.value = '';
        if (dom.autoRequirementText) dom.autoRequirementText.value = '';
        renderFileList();
    };

    const renderFileList = () => {
        const dom = refs();
        if (!dom.fileList) return;
        if (!state.files.length) {
            dom.fileList.innerHTML = '<div class="session-material-ai-empty">未选择示例文档</div>';
            return;
        }

        dom.fileList.innerHTML = state.files.map((file, index) => `
            <div class="session-material-ai-file-item" data-file-index="${index}">
                <div class="session-material-ai-file-meta">
                    <strong>${file.name}</strong>
                    <span>${formatSize(file.size)} · ${file.type || 'document'}</span>
                </div>
                <button type="button" class="session-material-ai-file-remove" data-remove-file="${index}">移除</button>
            </div>
        `).join('');
    };

    const syncInlineTaskUi = (session) => {
        const dom = refs();
        const task = session?.material_generation_task || null;
        const nextLabel = buildButtonLabel(task);

        if (dom.aiBtn) {
            dom.aiBtn.textContent = nextLabel;
            dom.aiBtn.disabled = Boolean(task && task.is_active);
            dom.aiBtn.classList.toggle('is-busy', Boolean(task && task.is_active));
        }

        if (dom.taskStrip && dom.taskPill && dom.taskCopy) {
            const copy = buildTaskCopy(task);
            dom.taskStrip.hidden = copy.hidden;
            dom.taskStrip.classList.toggle('is-hidden', copy.hidden);
            dom.taskPill.textContent = copy.pill;
            dom.taskCopy.textContent = copy.copy;
        }
    };

    const syncModalSessionUi = (session) => {
        const dom = refs();
        if (!session) return;

        const previousDocsCount = (getSessions() || []).filter((item) => {
            return Number(item.order_index || 0) < Number(session.order_index || 0) && Boolean(item.learning_material_id);
        }).length;
        const autoReferenceCount = Math.min(previousDocsCount, AUTO_REFERENCE_SOFT_LIMIT);
        const inferredType = inferDocumentType(session);

        if (dom.sessionNumber) {
            dom.sessionNumber.textContent = session.session_number_label || `第 ${session.order_index || ''} 次课`;
        }
        if (dom.sessionTitle) {
            dom.sessionTitle.textContent = session.detail_title || session.title || '当前课时';
        }
        if (dom.sessionMeta) {
            dom.sessionMeta.textContent = session.detail_meta
                || session.session_date
                || '系统会在生成完成后自动绑定到当前课时材料入口。';
        }
        if (dom.referenceHint) {
            dom.referenceHint.textContent = previousDocsCount > 0
                ? `已有 ${previousDocsCount} 篇前序文档可参考`
                : '当前前序课时暂无已绑定文档';
        }
        if (dom.existingMaterialHint) {
            dom.existingMaterialHint.textContent = session.learning_material_name
                ? `当前已绑定：${session.learning_material_name}`
                : '当前未绑定学习文档';
        }
        if (dom.guidedDocumentType) {
            dom.guidedDocumentType.value = inferredType;
        }
        if (dom.autoDocumentType) {
            dom.autoDocumentType.value = inferredType;
        }
        if (dom.guidedHint) {
            dom.guidedHint.textContent = session.learning_material_name
                ? '适合你想在保留当前绑定文档的同时，另生成一份更贴合本次授课节奏的新版本。'
                : '适合你已经有明确的课堂目标、内容结构或参考样例时使用。';
        }
        if (dom.autoHint) {
            dom.autoHint.textContent = previousDocsCount > 0
                ? '系统会直接参考最近几课已绑定文档，延续目录层级、命名方式和文档组织方式。'
                : '当前没有可参考的前序文档时，会退化为依据课程、班级和课时内容自动起稿。';
        }
        if (dom.autoScopeHint) {
            dom.autoScopeHint.textContent = previousDocsCount > AUTO_REFERENCE_SOFT_LIMIT
                ? `本次只会优先参考最近 ${AUTO_REFERENCE_SOFT_LIMIT} 篇已绑定文档，避免历史材料过多拖慢生成并干扰结构判断。`
                : previousDocsCount > 0
                    ? `本次会参考最近 ${autoReferenceCount} 篇已绑定文档，并结合课程、班级和当前课时内容生成。`
                    : '当前没有历史文档可参考，将主要依据课程、班级和当前课时内容生成。';
        }
        if (dom.footerCopy) {
            dom.footerCopy.textContent = session.learning_material_name
                ? '生成完成后，系统会新增一份材料并改绑到当前课时；原先的材料仍保留在材料库中，不会被覆盖或删除。'
                : '生成完成后，系统会创建新材料文件并自动绑定到当前课时，原有材料结构不会被破坏。';
        }
    };

    const setSubmitting = (submitting) => {
        state.submitting = submitting;
        const dom = refs();
        [
            dom.guidedSubmitBtn,
            dom.autoBtn,
            dom.pickFilesBtn,
            dom.cancelBtn,
            dom.closeBtn,
        ].forEach((button) => {
            if (!button) return;
            button.disabled = submitting;
        });
        [
            dom.guidedDocumentType,
            dom.guidedRequirementText,
            dom.autoDocumentType,
            dom.autoRequirementText,
        ].forEach((input) => {
            if (!input) return;
            input.disabled = submitting;
        });
    };

    const closeAssistantModal = () => {
        closeModal(MODAL_ID);
        setSubmitting(false);
        resetDraft();
    };

    const openForSession = (session) => {
        if (!session) {
            showToast('请先选择一个课时。', 'warning');
            return;
        }
        resetDraft();
        syncModalSessionUi(session);
        openModal(MODAL_ID);
    };

    const buildSubmitFormData = (mode) => {
        const dom = refs();
        const formData = new FormData();
        formData.append('mode', mode);

        if (mode === 'guided') {
            formData.append('guided_document_type', String(dom.guidedDocumentType?.value || '').trim());
            formData.append('guided_requirement_text', String(dom.guidedRequirementText?.value || '').trim());
            state.files.forEach((file) => {
                formData.append('example_files', file);
            });
            return formData;
        }

        formData.append('auto_document_type', String(dom.autoDocumentType?.value || '').trim());
        formData.append('auto_requirement_text', String(dom.autoRequirementText?.value || '').trim());
        return formData;
    };

    const submitTask = async (mode) => {
        const session = getCurrentSession();
        if (!session?.id) {
            showToast('当前课时不可用。', 'warning');
            return;
        }

        setSubmitting(true);
        try {
            const result = await apiFetch(
                `/api/classrooms/${classOfferingId}/sessions/${session.id}/ai-material-task`,
                {
                    method: 'POST',
                    body: buildSubmitFormData(mode),
                    silent: true,
                },
            );
            if (result?.session) {
                onSessionPatch(result.session);
            }
            closeAssistantModal();
            showToast(
                mode === 'auto'
                    ? 'AI 助教已开始按最近课时材料自动续写本课文档。'
                    : 'AI 助教已开始根据你的要求和示例生成文档。',
                'success',
            );
            startPolling();
        } catch (error) {
            showToast(error.message || '发起 AI 生成失败。', 'error');
            setSubmitting(false);
        }
    };

    const pollActiveTasks = async () => {
        window.clearTimeout(state.pollingTimer);
        state.pollingTimer = 0;

        const sessions = (getSessions() || []).filter((session) => {
            return Boolean(session?.material_generation_task?.is_active && session?.id);
        });
        if (!sessions.length) {
            return;
        }

        await Promise.all(sessions.map(async (session) => {
            try {
                const result = await apiFetch(
                    `/api/classrooms/${classOfferingId}/sessions/${session.id}/ai-material-task`,
                    { method: 'GET', silent: true },
                );
                if (!result?.session) return;

                const nextSession = result.session;
                const nextTask = nextSession.material_generation_task;
                const previousStateKey = state.knownTaskStates.get(nextSession.id);
                const nextStateKey = nextTask ? `${nextTask.id}:${nextTask.status}` : 'idle';
                state.knownTaskStates.set(nextSession.id, nextStateKey);

                onSessionPatch(nextSession);

                if (previousStateKey !== nextStateKey && nextTask) {
                    if (nextTask.status === 'completed') {
                        if (window.materialsApp && typeof window.materialsApp.refresh === 'function') {
                            window.materialsApp.refresh().catch(() => {});
                        }
                        showToast('AI 助教已完成文档生成，并自动绑定到当前课时。', 'success', 4200);
                    } else if (nextTask.status === 'failed') {
                        showToast(nextTask.error_message || 'AI 文档生成失败，请稍后重试。', 'error', 5200);
                    }
                }
            } catch (_error) {
            }
        }));

        syncInlineTaskUi(getCurrentSession());
        startPolling();
    };

    function startPolling() {
        window.clearTimeout(state.pollingTimer);
        const hasActiveTask = (getSessions() || []).some((session) => session?.material_generation_task?.is_active);
        if (!hasActiveTask) {
            state.pollingTimer = 0;
            return;
        }
        state.pollingTimer = window.setTimeout(() => {
            pollActiveTasks().catch(() => {});
        }, POLL_INTERVAL_MS);
    }

    const bindEvents = () => {
        if (state.initialized) return;
        state.initialized = true;
        const dom = refs();
        if (!dom.modal) return;

        dom.closeBtn?.addEventListener('click', closeAssistantModal);
        dom.cancelBtn?.addEventListener('click', closeAssistantModal);
        dom.pickFilesBtn?.addEventListener('click', () => {
            dom.fileInput?.click();
        });
        dom.fileInput?.addEventListener('change', () => {
            const pickedFiles = Array.from(dom.fileInput?.files || []);
            state.files = dedupeFiles([...state.files, ...pickedFiles]);
            renderFileList();
            if (dom.fileInput) dom.fileInput.value = '';
        });
        dom.fileList?.addEventListener('click', (event) => {
            const button = event.target.closest('[data-remove-file]');
            if (!button) return;
            const index = Number(button.getAttribute('data-remove-file') || -1);
            if (index < 0) return;
            state.files.splice(index, 1);
            renderFileList();
        });
        dom.modal.addEventListener('click', (event) => {
            if (event.target === dom.modal && !state.submitting) {
                closeAssistantModal();
            }
        });
        dom.guidedSubmitBtn?.addEventListener('click', () => {
            submitTask('guided').catch(() => {});
        });
        dom.autoBtn?.addEventListener('click', () => {
            submitTask('auto').catch(() => {});
        });
    };

    bindEvents();
    syncInlineTaskUi(getCurrentSession());
    startPolling();

    return {
        openForCurrentSession() {
            openForSession(getCurrentSession());
        },
        syncSelectedSession(session) {
            syncInlineTaskUi(session);
            if (refs().modal?.classList.contains('show')) {
                syncModalSessionUi(session);
            }
        },
        startPolling,
    };
}
