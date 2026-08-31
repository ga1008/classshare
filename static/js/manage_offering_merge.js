// 双开课堂合并向导（从 manage_offerings.js 迁出为独立页）：
// 检测卡 → 预检 → 输入主课堂班级名确认执行。
import { apiFetch } from '/static/js/api.js';
import { showMessage } from '/static/js/ui.js';

const mergeContainer = document.getElementById('offeringMergeCandidates');
const loadingEl = document.getElementById('offeringMergeLoading');
const emptyEl = document.getElementById('offeringMergeEmpty');

function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, (ch) => (
        { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]
    ));
}

function renderMergeCandidates(candidates) {
    mergeContainer.insertAdjacentHTML('beforeend', candidates.map((group, index) => {
        const badge = group.academic_confirmed_combined
            ? '<span class="academic-badge is-accent">教务确认合班</span>'
            : '<span class="academic-badge">请自行确认确为合班</span>';
        const rows = group.offerings.map((o) => `
            <label class="offering-merge-option">
                <input type="radio" name="mergeTarget-${index}" value="${o.offering_id}" ${o.offering_id === group.recommended_target_id ? 'checked' : ''}>
                <span><strong>${escapeHtml(o.class_name)}</strong>（课堂 #${o.offering_id} · ${o.student_count} 人 · ${o.assignment_count} 作业 · ${o.session_count} 课次）</span>
            </label>`).join('');
        return `
        <article class="offering-merge-group" data-merge-group data-index="${index}">
            <div class="offering-merge-group__head">
                <strong>${escapeHtml(group.course_name)}</strong>
                <span>${escapeHtml(group.semester || '')} · ${group.offerings.length} 个课堂</span>
                ${badge}
            </div>
            <p class="offering-merge-hint">选择保留为主课堂的一项（其余课堂的数据将迁入主课堂）：</p>
            ${rows}
            <div class="offering-merge-actions">
                <button type="button" class="btn btn-secondary btn-sm" data-merge-preview>预检合并</button>
            </div>
            <div class="offering-merge-preview" data-merge-preview-result hidden></div>
        </article>`;
    }).join(''));
}

function mergeGroupState(groupEl, candidates) {
    const index = Number(groupEl.dataset.index || 0);
    const group = candidates[index];
    const targetId = Number(groupEl.querySelector(`input[name="mergeTarget-${index}"]:checked`)?.value || 0);
    const sourceIds = group.offerings.map((o) => o.offering_id).filter((id) => id !== targetId);
    const targetName = group.offerings.find((o) => o.offering_id === targetId)?.class_name || '';
    return { group, targetId, sourceIds, targetName };
}

async function handleMergePreview(groupEl, candidates) {
    const { targetId, sourceIds, targetName } = mergeGroupState(groupEl, candidates);
    const resultEl = groupEl.querySelector('[data-merge-preview-result]');
    try {
        const data = await apiFetch('/api/manage/class_offerings/merge/preview', {
            method: 'POST',
            body: { target_offering_id: targetId, source_offering_ids: sourceIds },
        });
        const preview = data.preview;
        const tableRows = preview.tables.map((t) => `
            <tr><td>${escapeHtml(t.table)}</td><td>${escapeHtml(t.strategy)}</td><td>${t.source_rows}</td></tr>`).join('');
        const warnings = preview.warnings.map((w) => `<li>${escapeHtml(w)}</li>`).join('');
        const blockers = preview.blockers.map((b) => `<li class="text-danger">${escapeHtml(b)}</li>`).join('');
        resultEl.hidden = false;
        resultEl.innerHTML = `
            <p>共 ${preview.total_source_rows} 行数据将迁入主课堂「${escapeHtml(preview.target.class_name)}」。</p>
            ${blockers ? `<ul>${blockers}</ul><p class="text-danger">存在阻断项，无法执行。</p>` : ''}
            ${warnings ? `<ul>${warnings}</ul>` : ''}
            <details><summary>各表迁移明细（${preview.tables.length} 张表）</summary>
                <table class="offering-merge-table"><thead><tr><th>表</th><th>策略</th><th>迁移行数</th></tr></thead>
                <tbody>${tableRows}</tbody></table>
            </details>
            ${preview.can_execute ? `
            <div class="offering-merge-confirm">
                <input type="text" class="form-control" data-merge-confirm-input
                       placeholder="输入主课堂班级名「${escapeHtml(targetName)}」确认">
                <label class="offering-merge-ack"><input type="checkbox" data-merge-ack> 我已知晓该操作不可逆（已生成数据快照兜底）</label>
                <button type="button" class="btn btn-sm text-danger" data-merge-execute>确认合并（不可逆）</button>
            </div>` : ''}
        `;
    } catch (error) {
        showMessage(error.message || '合并预检失败', 'error');
    }
}

async function handleMergeExecute(groupEl, candidates) {
    const { targetId, sourceIds } = mergeGroupState(groupEl, candidates);
    const confirmInput = groupEl.querySelector('[data-merge-confirm-input]');
    const ack = groupEl.querySelector('[data-merge-ack]');
    if (!ack?.checked) {
        showMessage('请先勾选"我已知晓该操作不可逆"。', 'error');
        return;
    }
    const button = groupEl.querySelector('[data-merge-execute]');
    button.disabled = true;
    button.textContent = '合并中...';
    try {
        const result = await apiFetch('/api/manage/class_offerings/merge/execute', {
            method: 'POST',
            body: {
                target_offering_id: targetId,
                source_offering_ids: sourceIds,
                confirm_class_name: confirmInput?.value || '',
            },
        });
        showMessage(result.message || '合并完成', 'success');
        window.setTimeout(() => window.location.reload(), 1200);
    } catch (error) {
        showMessage(error.message || '合并失败，已整体回滚', 'error');
        button.disabled = false;
        button.textContent = '确认合并（不可逆）';
    }
}

async function initMergeWizard() {
    if (!mergeContainer) return;
    let candidates = [];
    try {
        const data = await apiFetch('/api/manage/class_offerings/merge/candidates', { silent: true });
        candidates = data.candidates || [];
    } catch (error) {
        if (loadingEl) loadingEl.hidden = true;
        showMessage('双开课堂检测失败，请刷新重试。', 'error');
        return;
    }
    if (loadingEl) loadingEl.hidden = true;
    if (!candidates.length) {
        if (emptyEl) emptyEl.hidden = false;
        return;
    }
    renderMergeCandidates(candidates);
    mergeContainer.addEventListener('click', (event) => {
        const groupEl = event.target.closest('[data-merge-group]');
        if (!groupEl) return;
        if (event.target.closest('[data-merge-preview]')) {
            handleMergePreview(groupEl, candidates);
        } else if (event.target.closest('[data-merge-execute]')) {
            handleMergeExecute(groupEl, candidates);
        }
    });
}

initMergeWizard();
