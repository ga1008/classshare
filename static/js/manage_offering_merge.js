// 双开课堂合并向导（从 manage_offerings.js 迁出为独立页）：
// 检测卡 → 预检 → 输入主课堂班级名确认执行。
import { apiFetch } from '/static/js/api.js';
import { showMessage } from '/static/js/ui.js';

const mergeContainer = document.getElementById('offeringMergeCandidates');
const loadingEl = document.getElementById('offeringMergeLoading');
const emptyEl = document.getElementById('offeringMergeEmpty');
const reviews = new WeakMap();
const stateFor = (element) => {
    if (!reviews.has(element)) reviews.set(element, { sequence: 0, hash: '', selection: '', saving: false });
    return reviews.get(element);
};
const selectionKey = ({ targetId, sourceIds }) => JSON.stringify([targetId, [...sourceIds].sort((a, b) => a - b)]);
const strategyLabels = { repoint: '迁入主课堂', repoint_guarded: '保留并迁入', dedup_skip: '去重后迁入，原记录留档', keep_target: '保留主课堂配置，来源留档', session_structure: '映射课次后留档', assignment_coexist: '各份作业并存', grade_publications: '保留成绩历史', keep_billing_scope: '保留原计费范围', scope_review: '先核对读者范围' };

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
    const state = stateFor(groupEl);
    if (state.saving) return;
    const sequence = ++state.sequence;
    const selected = selectionKey({ targetId, sourceIds });
    state.hash = '';
    groupEl.querySelector('[data-merge-execute]')?.setAttribute('disabled', '');
    try {
        const data = await apiFetch('/api/manage/class_offerings/merge/preview', {
            method: 'POST',
            body: { target_offering_id: targetId, source_offering_ids: sourceIds },
        });
        if (state.sequence !== sequence || selected !== selectionKey(mergeGroupState(groupEl, candidates))) return;
        const preview = data.preview;
        state.hash = preview.can_execute ? preview.review_hash : '';
        state.selection = selected;
        const tableRows = preview.tables.map((t) => `
            <tr><td>${escapeHtml(t.label || '课堂关联记录')}</td><td>${escapeHtml(strategyLabels[t.strategy] || '按预览处理')}</td><td>${t.source_rows}</td></tr>`).join('');
        const warnings = preview.warnings.map((w) => `<li>${escapeHtml(w)}</li>`).join('');
        const blockers = preview.blockers.map((b) => `<li class="text-danger">${escapeHtml(b)}</li>`).join('');
        resultEl.hidden = false;
        resultEl.innerHTML = `
            <p>共 ${preview.total_source_rows} 行数据将迁入主课堂「${escapeHtml(preview.target.class_name)}」。</p>
            ${blockers ? `<ul>${blockers}</ul><p class="text-danger">存在阻断项，无法执行。</p>` : ''}
            ${warnings ? `<ul>${warnings}</ul>` : ''}
            <details><summary>关联内容明细（${preview.tables.length} 类）</summary>
                <table class="offering-merge-table"><thead><tr><th>关联内容</th><th>处理方式</th><th>数量</th></tr></thead>
                <tbody>${tableRows}</tbody></table>
            </details>
            ${preview.can_execute ? `
            <div class="offering-merge-confirm">
                <input type="text" class="form-control" data-merge-confirm-input
                       placeholder="输入主课堂班级名「${escapeHtml(preview.target.class_name || targetName)}」确认">
                <label class="offering-merge-ack"><input type="checkbox" data-merge-ack> 我已知晓该操作不可逆（已生成数据快照兜底）</label>
                <button type="button" class="btn btn-sm text-danger" data-merge-execute>确认合并（不可逆）</button>
            </div>` : ''}
        `;
    } catch (error) {
        if (state.sequence !== sequence) return;
        showMessage(error.message || '合并预检失败', 'error');
    }
}

async function handleMergeExecute(groupEl, candidates) {
    const { targetId, sourceIds } = mergeGroupState(groupEl, candidates);
    const confirmInput = groupEl.querySelector('[data-merge-confirm-input]');
    const ack = groupEl.querySelector('[data-merge-ack]');
    const state = stateFor(groupEl);
    if (state.saving) return;
    if (!state.hash || state.selection !== selectionKey({ targetId, sourceIds })) {
        showMessage('课堂选择或预览已变化，请重新预检。', 'error');
        return;
    }
    if (!ack?.checked) {
        showMessage('请先勾选"我已知晓该操作不可逆"。', 'error');
        return;
    }
    const button = groupEl.querySelector('[data-merge-execute]');
    state.saving = true;
    button.disabled = true;
    groupEl.querySelectorAll('input,[data-merge-preview]').forEach((input) => { input.disabled = true; });
    button.textContent = '合并中...';
    try {
        const result = await apiFetch('/api/manage/class_offerings/merge/execute', {
            method: 'POST',
            body: {
                target_offering_id: targetId,
                source_offering_ids: sourceIds,
                confirm_class_name: confirmInput?.value || '',
                expected_review_hash: state.hash,
                acknowledged_irreversible: true,
            },
        });
        showMessage(result.message || '合并完成', 'success');
        window.setTimeout(() => window.location.reload(), 1200);
    } catch (error) {
        state.hash = '';
        showMessage(error.message || '未收到确定结果，请重新预检核对课堂状态。', 'error');
        button.disabled = true;
        button.textContent = '请重新预检后确认';
    } finally {
        state.saving = false;
        groupEl.querySelectorAll('input,[data-merge-preview]').forEach((input) => { input.disabled = false; });
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
    mergeContainer.addEventListener('change', (event) => {
        if (!event.target.matches('input[type="radio"]')) return;
        const groupEl = event.target.closest('[data-merge-group]');
        if (!groupEl) return;
        const state = stateFor(groupEl); state.sequence += 1; state.hash = '';
        const result = groupEl.querySelector('[data-merge-preview-result]');
        result.hidden = false; result.textContent = '已更换主课堂，请重新预检后确认。';
    });
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
