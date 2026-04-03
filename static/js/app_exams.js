/**
 * app_exams.js
 * Assignment and exam management module for classroom workspace.
 * Handles CRUD operations for assignments and exam paper assignment.
 */

import { apiFetch } from './api.js';
import { showToast, escapeHtml } from './ui.js';

let config = null;

/**
 * Initialize the exam app module
 * @param {object} appConfig - window.APP_CONFIG from template
 */
export function init(appConfig) {
    config = appConfig;
}

/**
 * Load exam papers for the exam-assign modal
 */
export async function loadExamPapers() {
    const container = document.getElementById('exam-list-container');
    if (!container) return;

    container.innerHTML = '<div class="text-center p-4"><div class="spinner"></div></div>';

    try {
        const data = await apiFetch('/api/exam-papers');
        const papers = data.papers || data || [];

        if (papers.length === 0) {
            container.innerHTML = '<p class="text-muted text-center p-4">暂无试卷，请先在管理中心创建试卷。</p>';
            return;
        }

        container.innerHTML = papers.map(p => `
            <label class="card card-interactive" style="padding: var(--spacing-md); cursor: pointer;">
                <div class="flex items-center gap-3">
                    <input type="radio" name="exam-paper" value="${p.id}" class="exam-paper-radio shrink-0">
                    <div class="flex-1 min-w-0">
                        <div class="font-semibold truncate">${escapeHtml(p.title)}</div>
                        <div class="text-muted text-sm">${p.question_count || 0} 道题${p.description ? ' · ' + escapeHtml(p.description).substring(0, 40) : ''}</div>
                    </div>
                </div>
            </label>
        `).join('');
    } catch (e) {
        console.error('Failed to load exam papers:', e);
        container.innerHTML = '<p class="text-danger text-center p-4">加载试卷列表失败</p>';
    }
}

/**
 * Confirm assigning selected exam paper to current classroom
 */
export async function confirmExamAssign() {
    const selected = document.querySelector('input[name="exam-paper"]:checked');
    if (!selected) {
        showToast('请先选择一份试卷', 'warning');
        return;
    }

    const paperId = (selected.value || '').trim();
    if (!paperId) {
        showToast('试卷标识无效，请重新选择', 'warning');
        return;
    }
    const btn = document.getElementById('exam-assign-confirm-btn');
    if (btn) { btn.disabled = true; btn.textContent = '发布中...'; }

    try {
        await apiFetch(`/api/exam-papers/${paperId}/assign`, {
            method: 'POST',
            body: {
                paper_id: paperId,
                class_offering_id: config.classOfferingId
            }
        });

        showToast('试卷已发布', 'success');
        if (window.UI) window.UI.closeModal('exam-assign-modal');
        // Reload page to show new assignment
        setTimeout(() => location.reload(), 500);
    } catch (e) {
        showToast('发布失败: ' + (e.message || '未知错误'), 'error');
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = '确认发布'; }
    }
}

/**
 * Save or update an assignment from the modal form
 */
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

    const assignmentId = idEl ? idEl.value : '';
    const btn = document.getElementById('btn-save-assignment');
    if (btn) { btn.disabled = true; btn.textContent = '保存中...'; }

    const body = {
        title,
        requirements_md: reqEl ? reqEl.value : '',
        rubric_md: rubricEl ? rubricEl.value : '',
        grading_mode: modeEl ? modeEl.value : 'manual',
        class_offering_id: config.classOfferingId
    };

    try {
        if (assignmentId) {
            // Update existing
            await apiFetch(`/api/assignments/${assignmentId}`, {
                method: 'PUT',
                body
            });
            showToast('作业已更新', 'success');
        } else {
            // Create new
            await apiFetch(`/api/courses/${config.classOfferingId}/assignments`, {
                method: 'POST',
                body
            });
            showToast('作业已创建', 'success');
        }

        if (window.UI) window.UI.closeModal('assignment-modal');
        setTimeout(() => location.reload(), 500);
    } catch (e) {
        showToast('保存失败: ' + (e.message || '未知错误'), 'error');
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = '保存作业'; }
    }
}

/**
 * Open assignment modal for editing
 */
export function editAssignment(assignmentId, title, requirements, rubric, gradingMode) {
    const idEl = document.getElementById('assignment-id');
    const titleEl = document.getElementById('assignment-title');
    const reqEl = document.getElementById('assignment-requirements');
    const rubricEl = document.getElementById('assignment-rubric');
    const modeEl = document.getElementById('assignment-grading-mode');

    if (idEl) idEl.value = assignmentId || '';
    if (titleEl) titleEl.value = title || '';
    if (reqEl) reqEl.value = requirements || '';
    if (rubricEl) rubricEl.value = rubric || '';
    if (modeEl) modeEl.value = gradingMode || 'manual';

    if (window.UI) window.UI.openModal('assignment-modal');
}

/**
 * Reset and open assignment modal for new assignment
 */
export function newAssignment() {
    editAssignment('', '', '', '', 'manual');
}
