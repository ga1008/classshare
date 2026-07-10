import { apiFetch } from './api.js';
import { showToast, escapeHtml } from './ui.js';
import { enhancePromptPoolInput, isPromptShareEnabled } from './prompt_pool.js';
import {
    closePendingPreviewWindow,
    isPreviewLinkBusy,
    movePendingPreviewWindow,
    openPendingPreviewWindow,
    setPreviewLinkBusy,
    startProcessMaterialExportDownload,
} from './process_material_editor_preview.js';

// ---------------------------------------------------------------------------
// Boot state
// ---------------------------------------------------------------------------
const boot = (() => {
    try {
        return JSON.parse(document.getElementById('te-editor-boot').textContent);
    } catch (_) {
        return {};
    }
})();

const state = {
    id: boot.id,
    fields: { ...(boot.fields || {}) },
    items: Array.isArray(boot.items) ? boot.items.map((it) => ({ ...it })) : [],
    analysis: boot.analysis || '',
    analysisRewriting: false,
    dirty: false,
    saving: false,
};

const FIELD_DEFS = [
    { key: 'course_name', label: '课程名称', type: 'text', full: true },
    { key: 'class_name', label: '授课班级', type: 'text', full: true },
    { key: 'college', label: '所在二级学院', type: 'text', full: true },
    { key: 'teacher_name', label: '任课教师', type: 'text' },
    { key: 'teacher_title', label: '教师职称', type: 'text', placeholder: '如：讲师 / 副教授' },
    { key: 'evaluate_date', label: '评价时间', type: 'text', placeholder: '如：2026年06月20日' },
    { key: 'academic_year', label: '学年', type: 'text', placeholder: '如：2025-2026' },
    { key: 'semester', label: '学期', type: 'select', options: ['第一学期', '第二学期'] },
];

const GROUP_LABELS = { 学习态度: '学习态度', 学习过程: '学习过程', 学习效果: '学习效果（结合试卷、作业分析）' };
const RATING_TONE = { 优秀: 'is-ok', 良好: 'is-ok', 一般: 'is-warn', 较差: 'is-warn' };

function setSaveState(kind, text) {
    const el = document.getElementById('te-save-state');
    if (!el) return;
    el.classList.remove('is-clean', 'is-dirty', 'is-saving');
    el.classList.add(kind);
    el.textContent = text;
}

function markDirty() {
    state.dirty = true;
    setSaveState('is-dirty', '未保存');
}

function markClean() {
    state.dirty = false;
    setSaveState('is-clean', '已保存');
}

function restoreSaveState() {
    if (state.dirty) {
        setSaveState('is-dirty', '未保存');
    } else {
        setSaveState('is-clean', '已保存');
    }
}


// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------
function renderFields() {
    const grid = document.getElementById('te-field-grid');
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
    const wrap = document.getElementById('te-items');
    let lastGroup = null;
    const rows = state.items.map((item, index) => {
        let groupHead = '';
        if (item.group !== lastGroup) {
            lastGroup = item.group;
            groupHead = `<div class="te-item-group">${escapeHtml(GROUP_LABELS[item.group] || item.group)}</div>`;
        }
        const score = item.score === 0 || item.score ? String(item.score) : '';
        return `${groupHead}
            <div class="te-item-row" data-row="${index}">
                <span class="te-item-text">${escapeHtml(item.indicator || '')}</span>
                <span class="te-item-max">${item.max_score ?? 10}</span>
                <input class="te-item-score" data-item-score="${index}" value="${escapeHtml(score)}"
                       inputmode="decimal" placeholder="0-10" aria-label="第${index + 1}项评价得分">
            </div>`;
    }).join('');
    wrap.innerHTML = `
        <div class="te-item-row te-item-row--head">
            <span>评价指标</span><span>总分值</span><span>评价得分</span>
        </div>
        ${rows}`;
    renderRating();
}

function scoreTotal() {
    return state.items.reduce((sum, it) => {
        const n = parseFloat(String(it.score ?? '').trim());
        return sum + (Number.isFinite(n) ? n : 0);
    }, 0);
}

function allScored() {
    return state.items.length === 10 && state.items.every((it) => String(it.score ?? '').trim() !== '');
}

function computeRating(total) {
    if (total >= 90) return '优秀';
    if (total >= 80) return '良好';
    if (total >= 70) return '一般';
    return '较差';
}

function renderRating() {
    const total = scoreTotal();
    const scored = allScored();
    const rating = scored ? computeRating(total) : '';
    const totalText = Number.isInteger(total) ? total : total.toFixed(1);

    const ratingEl = document.getElementById('te-rating');
    ratingEl.innerHTML = scored
        ? `<span class="te-rating__total">合计 <strong>${totalText}</strong> / 100</span>
           <span class="te-rating__badge ${RATING_TONE[rating] || ''}">综合评价：${rating}</span>`
        : `<span class="te-rating__total is-warn">合计 ${totalText} / 100（尚有指标未打分）</span>`;

    const pill = document.getElementById('te-score-pill');
    pill.textContent = scored ? `总分 ${totalText} · 综合评价 ${rating}` : `总分 ${totalText} · 综合评价 —`;
    pill.className = `ap-score-pill ${scored ? 'is-ok' : 'is-warn'}`;
}

function analysisCount() {
    const el = document.getElementById('te-analysis-count');
    const len = (state.analysis || '').length;
    el.textContent = `${len} 字`;
    el.className = `te-analysis__count${len > 1500 ? ' is-warn' : ''}`;
}

function missingFields() {
    const missing = [];
    const required = { course_name: '课程名称', class_name: '授课班级', college: '所在二级学院', teacher_name: '任课教师', evaluate_date: '评价时间' };
    for (const [key, label] of Object.entries(required)) {
        if (!String(state.fields[key] || '').trim()) missing.push(label);
    }
    if (!allScored()) missing.push('评价得分（部分指标未打分）');
    if (!String(state.analysis || '').trim()) missing.push('学习情况分析与教学改革建议');
    return missing;
}

function setExportButtons({ busy = false } = {}) {
    const missing = missingFields();
    const reason = missing.length ? `评学表尚未填写完整，请先补全：${missing.join('、')}` : '';
    const disabled = busy || state.saving || Boolean(reason) || state.analysisRewriting;
    const title = state.analysisRewriting ? 'AI 正在重新编写分析建议，请稍候。' : ((busy || state.saving) ? '正在保存当前修改，请稍候。' : reason);
    [
        document.getElementById('te-export-word'),
        document.getElementById('te-export-pdf'),
    ].filter(Boolean).forEach((button) => {
        button.disabled = disabled;
        button.classList.toggle('lp-btn--disabled', disabled);
        if (disabled) {
            button.setAttribute('aria-disabled', 'true');
            button.title = title;
        } else {
            button.removeAttribute('aria-disabled');
            button.removeAttribute('title');
        }
    });
    const gate = document.getElementById('te-export-gate');
    if (!gate) return;
    if (reason) {
        gate.hidden = false;
        gate.innerHTML = `<strong>导出前校验</strong><span>${escapeHtml(reason)}</span>`;
    } else {
        gate.hidden = true;
        gate.textContent = '';
    }
}

function setSavePreviewButtonsBusy(busy) {
    [
        document.getElementById('te-save'),
        document.getElementById('te-refresh-preview'),
    ].filter(Boolean).forEach((button) => {
        button.disabled = busy;
        button.classList.toggle('lp-btn--disabled', busy);
        if (busy) {
            button.setAttribute('aria-disabled', 'true');
        } else {
            button.removeAttribute('aria-disabled');
        }
    });
    const rewriteBtn = document.getElementById('te-analysis-rewrite');
    if (rewriteBtn) rewriteBtn.disabled = busy || state.analysisRewriting;
}

function setEditorBusy(busy) {
    state.saving = Boolean(busy);
    const form = document.getElementById('te-editor-form');
    form?.classList.toggle('is-saving', state.saving);
    form?.querySelectorAll('input, select, textarea, button').forEach((control) => {
        control.disabled = state.saving;
    });
    setPreviewLinkBusy(document.getElementById('te-open-preview'), state.saving);
    setSavePreviewButtonsBusy(state.saving);
    setExportButtons({ busy: state.saving });
}

function renderIncomplete() {
    const el = document.getElementById('te-incomplete');
    const missing = missingFields();
    if (!missing.length) {
        el.hidden = true;
        setExportButtons();
        return;
    }
    el.hidden = false;
    el.innerHTML = `⚠ 导出前请补全：${missing.map(escapeHtml).join('、')}`;
    setExportButtons();
}

function renderImportDetails() {
    const preview = boot.import_preview;
    if (!preview || !Object.keys(preview).length) return;
    const wrap = document.getElementById('te-import');
    const details = document.getElementById('te-import-details');
    details.hidden = false;
    const warnings = (preview.warnings || []).map((w) => `<li class="is-warn">${escapeHtml(w)}</li>`).join('');
    const perf = preview.performance_summary
        ? `<div class="ap-import__block"><strong>班级表现归集</strong><div style="white-space:pre-wrap">${escapeHtml(preview.performance_summary)}</div></div>`
        : '';
    const source = preview.source_files
        ? `<div class="ap-import__block"><strong>来源文件</strong><div>${(preview.source_files || []).map(escapeHtml).join('、') || '—'}</div></div>`
        : '';
    wrap.innerHTML = `
        ${source}
        ${perf}
        ${preview.rating ? `<div class="ap-import__block"><strong>生成结果</strong><div>总分 ${escapeHtml(String(preview.score_total ?? ''))} · 综合评价 ${escapeHtml(preview.rating)}</div></div>` : ''}
        ${warnings ? `<div class="ap-import__block"><strong>提示</strong><ul class="ap-import__list">${warnings}</ul></div>` : ''}`;
}

// ---------------------------------------------------------------------------
// Persistence
// ---------------------------------------------------------------------------
function reloadPreview() {
    const frame = document.getElementById('te-preview-frame');
    if (frame) frame.src = `/teacher-evaluation/${state.id}/preview?t=${Date.now()}`;
}

async function persistContent() {
    return await apiFetch(`/api/teacher-evaluations/${state.id}/content`, {
        method: 'PUT',
        body: { fields: state.fields, items: state.items, analysis: state.analysis },
    });
}

async function saveContent() {
    if (state.saving) return;
    setEditorBusy(true);
    setSaveState('is-saving', '保存中');
    try {
        const res = await persistContent();
        markClean();
        renderIncomplete();
        if (res && res.is_complete === false) {
            showToast('已保存。' + (res.missing_fields?.length ? '仍需补全：' + res.missing_fields.join('、') : ''), 'warning');
        } else {
            showToast('已保存', 'success');
        }
        reloadPreview();
    } catch (err) {
        showToast(err.message || '保存失败', 'error');
    } finally {
        setEditorBusy(false);
        restoreSaveState();
    }
}

async function refreshPreview() {
    if (state.saving) return;
    setEditorBusy(true);
    setSaveState('is-saving', '保存中');
    try {
        const res = await persistContent();
        markClean();
        renderIncomplete();
        if (res && res.is_complete === false) {
            showToast('已保存并刷新预览。' + (res.missing_fields?.length ? '仍需补全：' + res.missing_fields.join('、') : ''), 'warning');
        } else {
            showToast('已保存并刷新预览', 'success');
        }
        reloadPreview();
    } catch (err) {
        showToast(err.message || '保存失败，无法刷新预览', 'error');
    } finally {
        setEditorBusy(false);
        restoreSaveState();
    }
}

async function exportEvaluation(format = 'docx') {
    if (state.saving) return;
    const missing = missingFields();
    if (missing.length) {
        showToast('请先补全后再导出：' + missing.join('、'), 'warning');
        renderIncomplete();
        return;
    }
    setEditorBusy(true);
    setSaveState('is-saving', '保存中');
    try {
        await persistContent();
        markClean();
    } catch (err) {
        showToast(err.message || '保存失败，无法导出', 'error');
        return;
    } finally {
        setEditorBusy(false);
        restoreSaveState();
    }
    const normalized = format === 'pdf' ? 'pdf' : 'docx';
    startProcessMaterialExportDownload(
        `/api/teacher-evaluations/${state.id}/export?fmt=${normalized}`,
        showToast,
        normalized === 'pdf' ? 'PDF' : 'Word',
    );
}

async function openSavedPreview(event) {
    const link = event.currentTarget;
    if (state.saving || isPreviewLinkBusy(link)) {
        event.preventDefault();
        return;
    }
    if (!state.dirty) return;
    event.preventDefault();
    const previewWindow = openPendingPreviewWindow(showToast);
    if (!previewWindow) return;
    setEditorBusy(true);
    setSaveState('is-saving', '保存中');
    try {
        const res = await persistContent();
        markClean();
        renderIncomplete();
        reloadPreview();
        movePendingPreviewWindow(previewWindow, link.href);
        if (res && res.is_complete === false) {
            showToast('已保存并打开预览。' + (res.missing_fields?.length ? '仍需补全：' + res.missing_fields.join('、') : ''), 'warning');
        } else {
            showToast('已保存并打开预览', 'success');
        }
    } catch (err) {
        closePendingPreviewWindow(previewWindow);
        showToast(err.message || '保存失败，无法打开预览', 'error');
    } finally {
        setEditorBusy(false);
        restoreSaveState();
    }
}

// ---------------------------------------------------------------------------
// AI rewrite modal
// ---------------------------------------------------------------------------
function ensureRewriteModal() {
    let modal = document.getElementById('te-ai-rewrite-modal');
    if (modal) return modal;
    modal = document.createElement('div');
    modal.id = 'te-ai-rewrite-modal';
    modal.className = 'te-ai-modal-backdrop';
    modal.hidden = true;
    modal.innerHTML = `
        <section class="te-ai-modal" role="dialog" aria-modal="true" aria-labelledby="te-ai-rewrite-title">
            <header class="te-ai-modal__header">
                <div>
                    <p>分析建议重写</p>
                    <h3 id="te-ai-rewrite-title">AI重新编写</h3>
                </div>
                <button type="button" class="te-ai-modal__close" id="te-ai-rewrite-close" aria-label="关闭">×</button>
            </header>
            <label class="te-ai-modal__field">
                <span>额外提示（可选，优先级更高）</span>
                <textarea id="te-ai-rewrite-prompt" rows="7" data-prompt-pool-key="teacher_evaluation.rewrite_analysis"
                    placeholder="例如：写得更详细一些，分 3 点，每点结合课堂表现、作业考试和后续改革建议，总字数约 600 字。"></textarea>
            </label>
            <footer class="te-ai-modal__footer">
                <button type="button" class="lp-btn lp-btn--ghost" id="te-ai-rewrite-cancel">取消</button>
                <button type="button" class="lp-btn lp-btn--primary" id="te-ai-rewrite-confirm">确认并重新编写</button>
            </footer>
        </section>`;
    document.body.appendChild(modal);
    modal.addEventListener('click', (event) => {
        if (event.target === modal) closeRewriteModal();
    });
    modal.querySelector('#te-ai-rewrite-close').addEventListener('click', closeRewriteModal);
    modal.querySelector('#te-ai-rewrite-cancel').addEventListener('click', closeRewriteModal);
    modal.querySelector('#te-ai-rewrite-confirm').addEventListener('click', submitRewritePrompt);
    const promptInput = modal.querySelector('#te-ai-rewrite-prompt');
    enhancePromptPoolInput(promptInput);
    promptInput.addEventListener('keydown', (event) => {
        if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') {
            event.preventDefault();
            submitRewritePrompt();
        }
    });
    return modal;
}

function openRewriteModal() {
    if (state.saving || state.analysisRewriting) return;
    const modal = ensureRewriteModal();
    modal.hidden = false;
    document.body.style.overflow = 'hidden';
    requestAnimationFrame(() => {
        modal.classList.add('is-open');
        modal.querySelector('#te-ai-rewrite-prompt')?.focus();
    });
}

function closeRewriteModal() {
    if (state.analysisRewriting) return;
    const modal = document.getElementById('te-ai-rewrite-modal');
    if (!modal) return;
    modal.classList.remove('is-open');
    document.body.style.overflow = '';
    setTimeout(() => {
        if (!modal.classList.contains('is-open')) modal.hidden = true;
    }, 180);
}

function setAnalysisRewriteLoading(active) {
    state.analysisRewriting = Boolean(active);
    const analysisEl = document.getElementById('te-analysis');
    const box = document.getElementById('te-analysis-box');
    const loading = document.getElementById('te-analysis-loading');
    const rewriteBtn = document.getElementById('te-analysis-rewrite');
    const closeBtn = document.getElementById('te-ai-rewrite-close');
    const cancelBtn = document.getElementById('te-ai-rewrite-cancel');
    const saveBtn = document.getElementById('te-save');
    const refreshBtn = document.getElementById('te-refresh-preview');
    if (analysisEl) analysisEl.disabled = state.analysisRewriting || state.saving;
    if (box) box.classList.toggle('is-loading', state.analysisRewriting);
    if (loading) loading.hidden = !state.analysisRewriting;
    if (rewriteBtn) rewriteBtn.disabled = state.analysisRewriting || state.saving;
    if (closeBtn) closeBtn.disabled = state.analysisRewriting;
    if (cancelBtn) cancelBtn.disabled = state.analysisRewriting;
    if (saveBtn) saveBtn.disabled = state.analysisRewriting || state.saving;
    if (refreshBtn) refreshBtn.disabled = state.analysisRewriting || state.saving;
    setExportButtons();
}

async function submitRewritePrompt() {
    if (state.saving || state.analysisRewriting) return;
    const modal = ensureRewriteModal();
    const promptEl = modal.querySelector('#te-ai-rewrite-prompt');
    const confirmBtn = modal.querySelector('#te-ai-rewrite-confirm');
    const prompt = promptEl?.value?.trim() || '';
    const sharePrompt = isPromptShareEnabled(promptEl);
    const originalText = confirmBtn?.textContent || '确认并重新编写';
    confirmBtn.disabled = true;
    confirmBtn.textContent = '正在编写…';
    try {
        const ok = await rewriteAnalysis(prompt, { sharePrompt });
        if (ok) {
            if (promptEl) promptEl.value = '';
            closeRewriteModal();
        }
    } finally {
        confirmBtn.disabled = false;
        confirmBtn.textContent = originalText;
    }
}

async function rewriteAnalysis(extraPrompt, { sharePrompt = true } = {}) {
    const analysisEl = document.getElementById('te-analysis');
    setAnalysisRewriteLoading(true);
    try {
        await persistContent();
        markClean();
        const res = await apiFetch(`/api/teacher-evaluations/${state.id}/rewrite-analysis`, {
            method: 'POST',
            body: { prompt: extraPrompt || '', share_prompt: sharePrompt },
            silent: true,
        });
        state.analysis = res.analysis || '';
        if (analysisEl) analysisEl.value = state.analysis;
        markClean();
        analysisCount();
        renderIncomplete();
        reloadPreview();
        showToast('AI已重新编写分析建议', 'success');
        return true;
    } catch (err) {
        showToast(err.message || 'AI重新编写失败，请稍后再试', 'error', 4500);
        return false;
    } finally {
        setAnalysisRewriteLoading(false);
    }
}

// ---------------------------------------------------------------------------
// Events
// ---------------------------------------------------------------------------
function bindEvents() {
    const form = document.getElementById('te-editor-form');

    form.addEventListener('input', (e) => {
        if (state.saving) return;
        const fieldEl = e.target.closest('[data-field]');
        if (fieldEl) {
            state.fields[fieldEl.dataset.field] = fieldEl.value;
            markDirty();
            renderIncomplete();
            return;
        }
        const scoreEl = e.target.closest('[data-item-score]');
        if (scoreEl) {
            const row = Number(scoreEl.dataset.itemScore);
            if (state.items[row]) {
                state.items[row].score = scoreEl.value;
                markDirty();
                renderRating();
                renderIncomplete();
            }
        }
    });

    form.addEventListener('change', (e) => {
        if (state.saving) return;
        const fieldEl = e.target.closest('[data-field]');
        if (fieldEl) { state.fields[fieldEl.dataset.field] = fieldEl.value; markDirty(); renderIncomplete(); }
    });

    const analysisEl = document.getElementById('te-analysis');
    analysisEl.value = state.analysis || '';
    analysisEl.addEventListener('input', () => {
        if (state.saving) return;
        state.analysis = analysisEl.value;
        markDirty();
        analysisCount();
        renderIncomplete();
    });

    document.getElementById('te-save').addEventListener('click', saveContent);
    document.getElementById('te-open-preview').addEventListener('click', openSavedPreview);
    document.getElementById('te-refresh-preview').addEventListener('click', refreshPreview);
    document.getElementById('te-export-word').addEventListener('click', () => exportEvaluation('docx'));
    document.getElementById('te-export-pdf').addEventListener('click', () => exportEvaluation('pdf'));
    document.getElementById('te-analysis-rewrite').addEventListener('click', openRewriteModal);

    window.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') closeRewriteModal();
        if ((e.ctrlKey || e.metaKey) && e.key === 's') { e.preventDefault(); saveContent(); }
    });
    window.addEventListener('beforeunload', (e) => {
        if (!state.dirty) return;
        e.preventDefault();
        e.returnValue = '';
    });
}

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------
function init() {
    if (!state.id) return;
    renderFields();
    renderItems();
    analysisCount();
    renderIncomplete();
    renderImportDetails();
    bindEvents();
}

init();
