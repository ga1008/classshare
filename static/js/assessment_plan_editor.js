import { apiFetch } from './api.js';
import { showToast, escapeHtml } from './ui.js';

// ---------------------------------------------------------------------------
// Boot state
// ---------------------------------------------------------------------------
const boot = (() => {
    try {
        return JSON.parse(document.getElementById('ap-editor-boot').textContent);
    } catch (_) {
        return {};
    }
})();

const state = {
    id: boot.id,
    fields: { ...(boot.fields || {}) },
    items: Array.isArray(boot.items) ? boot.items.map((it) => ({ ...it })) : [],
    notes: Array.isArray(boot.notes) ? boot.notes : [],
    examinerSignature: boot.examiner_signature || null,
    reviewerSignature: boot.reviewer_signature || null,
    signatureOptions: { mine: [], usable: [] },
};

const FIELD_DEFS = [
    { key: 'course_name', label: '课程名称', type: 'text', full: true },
    { key: 'class_name', label: '专业年级班级', type: 'text', full: true },
    { key: 'assessment_type', label: '考核类型', type: 'select', options: ['考试', '考查'] },
    { key: 'assessment_method', label: '考核形式', type: 'text', placeholder: '如：机试 / 闭卷笔试 / 项目实操' },
    { key: 'academic_year', label: '学年', type: 'text', placeholder: '如：2025-2026' },
    { key: 'semester', label: '学期', type: 'select', options: ['第一学期', '第二学期'] },
    { key: 'date', label: '命题日期', type: 'text', placeholder: '如：2025年10月13日' },
    { key: 'school', label: '学校', type: 'text', placeholder: '广西外国语学院' },
    { key: 'examiner_name', label: '命题教师', type: 'text' },
    { key: 'reviewer_name', label: '系（教研室）主任审核签字', type: 'text' },
];

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------
function renderFields() {
    const grid = document.getElementById('ap-field-grid');
    grid.innerHTML = FIELD_DEFS.map((def) => {
        const value = state.fields[def.key] || '';
        let control;
        if (def.type === 'select') {
            const opts = def.options.map((o) => `<option value="${escapeHtml(o)}"${o === value ? ' selected' : ''}>${escapeHtml(o)}</option>`).join('');
            control = `<select data-field="${def.key}"><option value="">未填写</option>${opts}</select>`;
        } else {
            control = `<input data-field="${def.key}" value="${escapeHtml(value)}" placeholder="${escapeHtml(def.placeholder || '')}">`;
        }
        return `<label class="ap-field${def.full ? ' ap-field--full' : ''}"><span>${escapeHtml(def.label)}</span>${control}</label>`;
    }).join('');
}

function renderItems() {
    const wrap = document.getElementById('ap-items');
    const rows = state.items.map((item, index) => `
        <div class="ap-item-row" data-row="${index}">
            <input class="ap-item-form" data-item-field="assessment_form" data-row="${index}" value="${escapeHtml(item.assessment_form || '')}" placeholder="考核形式">
            <textarea class="ap-item-content" data-item-field="content" data-row="${index}" rows="2" placeholder="考核技能/内容">${escapeHtml(item.content || '')}</textarea>
            <input class="ap-item-score" data-item-field="score" data-row="${index}" value="${escapeHtml(item.score || '')}" inputmode="decimal" placeholder="分值">
            <button type="button" class="lp-link lp-link--danger" data-remove-item="${index}">删除</button>
        </div>`).join('');
    wrap.innerHTML = `
        <div class="ap-item-row ap-item-row--head">
            <span>考核形式</span><span>考核技能/内容</span><span>分值</span><span></span>
        </div>
        ${rows || '<div class="ap-empty">还没有考核项，点击下方“添加考核项”。</div>'}`;
    renderTotal();
}

function scoreTotal() {
    return state.items.reduce((sum, it) => {
        const n = parseFloat(String(it.score || '').trim());
        return sum + (Number.isFinite(n) ? n : 0);
    }, 0);
}

function renderTotal() {
    const total = scoreTotal();
    const ok = Math.abs(total - 100) < 1e-6;
    const totalEl = document.getElementById('ap-items-total');
    totalEl.textContent = `合计 ${Number.isInteger(total) ? total : total.toFixed(1)} 分`;
    totalEl.className = `ap-items__total ${ok ? 'is-ok' : 'is-warn'}`;
    const pill = document.getElementById('ap-score-pill');
    pill.textContent = `分值合计 ${Number.isInteger(total) ? total : total.toFixed(1)}${ok ? '' : ' ≠100'}`;
    pill.className = `ap-score-pill ${ok ? 'is-ok' : 'is-warn'}`;
}

function signatureSubjectName(role) {
    const key = role === 'reviewer' ? 'reviewer_name' : 'examiner_name';
    return String(state.fields[key] || state.fields.teacher_name || '').trim();
}

function signatureRow(role, label, bound, options) {
    const optsHtml = ['<option value="">未绑定</option>']
        .concat(options.map((o) => {
            const selected = bound && Number(bound.id) === Number(o.id) ? ' selected' : '';
            return `<option value="${o.id}"${selected}>${escapeHtml(o.subject_name || o.name)}</option>`;
        }))
        .join('');
    const preview = bound && bound.image_url
        ? `<img class="ap-sign-img" src="${escapeHtml(bound.image_url)}" alt="签名预览">`
        : '<span class="ap-sign-empty">未绑定签名</span>';
    const subjectName = signatureSubjectName(role);
    let notice = '';
    if (role === 'reviewer' && !subjectName) {
        notice = '请先填写系主任姓名；保存后会从签名库匹配，也可以上传新签名并自动入库。';
    } else if (role === 'reviewer' && !bound) {
        notice = '未绑定系主任签名。若签名库没有自动匹配，请在这里上传并绑定。';
    }
    return `
        <div class="ap-sign-row">
            <div class="ap-sign-label">${escapeHtml(label)}</div>
            <div class="ap-sign-picker">
                <select data-signature-role="${role}">${optsHtml}</select>
                <div class="ap-sign-upload">
                    <input type="file" accept="image/png,image/jpeg" data-signature-file="${role}">
                    <button type="button" class="lp-link" data-upload-signature="${role}">上传并绑定</button>
                </div>
                ${notice ? `<div class="ap-sign-notice">${escapeHtml(notice)}</div>` : ''}
            </div>
            <div class="ap-sign-preview">${preview}</div>
        </div>`;
}

function renderSignatures() {
    const wrap = document.getElementById('ap-signatures');
    wrap.innerHTML =
        signatureRow('examiner', '命题教师签名', state.examinerSignature, state.signatureOptions.mine) +
        signatureRow('reviewer', '系（教研室）主任审核签字', state.reviewerSignature, state.signatureOptions.usable);
}

function renderImportDetails() {
    const preview = boot.import_preview;
    if (!preview || !Object.keys(preview).length) return;
    const wrap = document.getElementById('ap-import');
    const details = document.getElementById('ap-import-details');
    details.hidden = false;
    const signatures = (preview.signatures || []).map((s) => {
        const role = s.role === 'examiner' ? '命题教师' : (s.role === 'reviewer' ? '系主任' : '其他');
        const img = s.image_url ? `<img class="ap-sign-img" src="${escapeHtml(s.image_url)}">` : '';
        const dedup = s.deduped ? '（已存在，复用）' : '（新入库）';
        const err = s.error ? `<span class="is-warn">入库失败：${escapeHtml(s.error)}</span>` : `${escapeHtml(s.subject_name || role)} ${dedup}`;
        return `<li>${role}：${img} ${err}</li>`;
    }).join('');
    const warnings = (preview.warnings || []).map((w) => `<li class="is-warn">${escapeHtml(w)}</li>`).join('');
    wrap.innerHTML = `
        <div class="ap-import__block"><strong>来源文件</strong><div>${(preview.source_files || []).map(escapeHtml).join('、') || '—'}</div></div>
        <div class="ap-import__block"><strong>识别分值合计</strong><div class="${preview.score_balanced ? 'is-ok' : 'is-warn'}">${escapeHtml(String(preview.score_total ?? ''))} ${preview.score_balanced ? '' : '（≠100，请核对）'}</div></div>
        <div class="ap-import__block"><strong>归集签名</strong><ul class="ap-import__list">${signatures || '<li>未发现可入库的签名图片</li>'}</ul></div>
        ${warnings ? `<div class="ap-import__block"><strong>提示</strong><ul class="ap-import__list">${warnings}</ul></div>` : ''}`;
}

// ---------------------------------------------------------------------------
// Signature options
// ---------------------------------------------------------------------------
async function loadSignatureOptions() {
    try {
        const mine = await apiFetch('/api/signatures?scope=mine&limit=200');
        state.signatureOptions.mine = (mine.items || []).filter((s) => s.can_use !== false);
    } catch (_) { state.signatureOptions.mine = []; }
    try {
        const all = await apiFetch('/api/signatures?limit=200');
        state.signatureOptions.usable = (all.items || []).filter((s) => s.can_use);
    } catch (_) { state.signatureOptions.usable = []; }
    // Ensure currently-bound signatures appear even if not in the filtered lists.
    for (const [bound, bucket] of [[state.examinerSignature, 'mine'], [state.reviewerSignature, 'usable']]) {
        if (bound && !state.signatureOptions[bucket].some((s) => Number(s.id) === Number(bound.id))) {
            state.signatureOptions[bucket] = [bound, ...state.signatureOptions[bucket]];
        }
    }
    renderSignatures();
}

// ---------------------------------------------------------------------------
// Persistence
// ---------------------------------------------------------------------------
function reloadPreview() {
    const frame = document.getElementById('ap-preview-frame');
    if (frame) frame.src = `/assessment-plan/${state.id}/preview?t=${Date.now()}`;
}

async function persistContent() {
    return await apiFetch(`/api/assessment-plans/${state.id}/content`, {
        method: 'PUT',
        body: { fields: state.fields, items: state.items, notes: state.notes },
    });
}

async function saveContent() {
    const btn = document.getElementById('ap-save');
    btn.disabled = true;
    try {
        const res = await persistContent();
        if (res && res.score_balanced === false) {
            showToast(`已保存，但考核项分值合计为 ${res.score_total}，未达到 100。`, 'warning');
        } else {
            showToast('已保存', 'success');
        }
        reloadPreview();
    } catch (err) {
        showToast(err.message || '保存失败', 'error');
    } finally {
        btn.disabled = false;
    }
}

async function bindSignature(role, signatureId) {
    try {
        const res = await apiFetch(`/api/assessment-plans/${state.id}/signature`, {
            method: 'PUT',
            body: { role, signature_id: signatureId ? Number(signatureId) : null },
        });
        state.examinerSignature = res.examiner_signature || null;
        state.reviewerSignature = res.reviewer_signature || null;
        renderSignatures();
        reloadPreview();
        showToast('签名已更新', 'success');
    } catch (err) {
        showToast(err.message || '绑定签名失败', 'error');
        renderSignatures();
    }
}

async function uploadSignature(role) {
    const input = document.querySelector(`[data-signature-file="${role}"]`);
    const file = input && input.files ? input.files[0] : null;
    if (!file) {
        showToast('请先选择签名图片。', 'warning');
        return;
    }
    const subjectName = signatureSubjectName(role);
    if (!subjectName) {
        showToast(role === 'reviewer' ? '请先填写系主任姓名。' : '请先填写命题教师姓名。', 'warning');
        const field = document.querySelector(`[data-field="${role === 'reviewer' ? 'reviewer_name' : 'examiner_name'}"]`);
        if (field) field.focus();
        return;
    }
    try {
        await persistContent();
        const formData = new FormData();
        formData.append('file', file);
        formData.append('name', `${subjectName}签名`);
        formData.append('subject_role', 'teacher');
        formData.append('subject_name', subjectName);
        formData.append('scope_level', 'department');
        formData.append('description', '从课程考核计划表编辑器上传并绑定。');
        const res = await apiFetch('/api/signatures/upload', {
            method: 'POST',
            body: formData,
        });
        const signature = res && res.signature ? res.signature : null;
        if (!signature || !signature.id) {
            throw new Error('签名上传成功但未返回签名编号。');
        }
        if (input) input.value = '';
        await loadSignatureOptions();
        await bindSignature(role, signature.id);
        showToast('签名已上传、入库并绑定。', 'success');
    } catch (err) {
        showToast(err.message || '上传签名失败', 'error');
    }
}

// ---------------------------------------------------------------------------
// Events
// ---------------------------------------------------------------------------
function bindEvents() {
    const form = document.getElementById('ap-editor-form');

    form.addEventListener('input', (e) => {
        const fieldEl = e.target.closest('[data-field]');
        if (fieldEl) {
            state.fields[fieldEl.dataset.field] = fieldEl.value;
            return;
        }
        const itemEl = e.target.closest('[data-item-field]');
        if (itemEl) {
            const row = Number(itemEl.dataset.row);
            if (state.items[row]) {
                state.items[row][itemEl.dataset.itemField] = itemEl.value;
                if (itemEl.dataset.itemField === 'score') renderTotal();
            }
        }
    });

    form.addEventListener('change', (e) => {
        const fieldEl = e.target.closest('[data-field]');
        if (fieldEl) { state.fields[fieldEl.dataset.field] = fieldEl.value; return; }
        const sigEl = e.target.closest('[data-signature-role]');
        if (sigEl) { bindSignature(sigEl.dataset.signatureRole, sigEl.value); }
    });

    form.addEventListener('click', (e) => {
        const uploadBtn = e.target.closest('[data-upload-signature]');
        if (uploadBtn) {
            uploadSignature(uploadBtn.dataset.uploadSignature);
            return;
        }
        const removeBtn = e.target.closest('[data-remove-item]');
        if (removeBtn) {
            state.items.splice(Number(removeBtn.dataset.removeItem), 1);
            renderItems();
        }
    });

    document.getElementById('ap-add-item').addEventListener('click', () => {
        state.items.push({ assessment_form: state.fields.assessment_method || '机试', content: '', score: '' });
        renderItems();
    });

    document.getElementById('ap-save').addEventListener('click', saveContent);
    document.getElementById('ap-refresh-preview').addEventListener('click', reloadPreview);

    window.addEventListener('keydown', (e) => {
        if ((e.ctrlKey || e.metaKey) && e.key === 's') { e.preventDefault(); saveContent(); }
    });
}

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------
function init() {
    if (!state.id) return;
    renderFields();
    renderItems();
    renderSignatures();
    renderImportDetails();
    bindEvents();
    loadSignatureOptions();
}

init();
