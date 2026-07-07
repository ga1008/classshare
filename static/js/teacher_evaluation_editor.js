import { apiFetch } from './api.js';
import { showToast, escapeHtml } from './ui.js';

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

function renderIncomplete() {
    const el = document.getElementById('te-incomplete');
    const missing = missingFields();
    if (!missing.length) {
        el.hidden = true;
        return;
    }
    el.hidden = false;
    el.innerHTML = `⚠ 导出前请补全：${missing.map(escapeHtml).join('、')}`;
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
    const btn = document.getElementById('te-save');
    btn.disabled = true;
    try {
        const res = await persistContent();
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
        btn.disabled = false;
    }
}

async function exportWord() {
    const missing = missingFields();
    if (missing.length) {
        showToast('请先补全后再导出：' + missing.join('、'), 'warning');
        renderIncomplete();
        return;
    }
    try {
        await persistContent();
    } catch (err) {
        showToast(err.message || '保存失败，无法导出', 'error');
        return;
    }
    window.location.href = `/api/teacher-evaluations/${state.id}/export?fmt=docx`;
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
                <textarea id="te-ai-rewrite-prompt" rows="7"
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
    modal.querySelector('#te-ai-rewrite-prompt').addEventListener('keydown', (event) => {
        if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') {
            event.preventDefault();
            submitRewritePrompt();
        }
    });
    return modal;
}

function openRewriteModal() {
    if (state.analysisRewriting) return;
    const modal = ensureRewriteModal();
    modal.hidden = false;
    document.body.style.overflow = 'hidden';
    requestAnimationFrame(() => {
        modal.classList.add('is-open');
        modal.querySelector('#te-ai-rewrite-prompt')?.focus();
    });
}

function closeRewriteModal() {
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
    const saveBtn = document.getElementById('te-save');
    const exportBtn = document.getElementById('te-export-word');
    if (analysisEl) analysisEl.disabled = state.analysisRewriting;
    if (box) box.classList.toggle('is-loading', state.analysisRewriting);
    if (loading) loading.hidden = !state.analysisRewriting;
    if (rewriteBtn) rewriteBtn.disabled = state.analysisRewriting;
    if (saveBtn) saveBtn.disabled = state.analysisRewriting;
    if (exportBtn) exportBtn.disabled = state.analysisRewriting;
}

async function submitRewritePrompt() {
    if (state.analysisRewriting) return;
    const modal = ensureRewriteModal();
    const promptEl = modal.querySelector('#te-ai-rewrite-prompt');
    const confirmBtn = modal.querySelector('#te-ai-rewrite-confirm');
    const prompt = promptEl?.value?.trim() || '';
    confirmBtn.disabled = true;
    closeRewriteModal();
    try {
        await rewriteAnalysis(prompt);
        if (promptEl) promptEl.value = '';
    } finally {
        confirmBtn.disabled = false;
    }
}

async function rewriteAnalysis(extraPrompt) {
    const analysisEl = document.getElementById('te-analysis');
    setAnalysisRewriteLoading(true);
    try {
        await persistContent();
        const res = await apiFetch(`/api/teacher-evaluations/${state.id}/rewrite-analysis`, {
            method: 'POST',
            body: { prompt: extraPrompt || '' },
            silent: true,
        });
        state.analysis = res.analysis || '';
        if (analysisEl) analysisEl.value = state.analysis;
        analysisCount();
        renderIncomplete();
        reloadPreview();
        showToast('AI已重新编写分析建议', 'success');
    } catch (err) {
        showToast(err.message || 'AI重新编写失败，请稍后再试', 'error', 4500);
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
        const fieldEl = e.target.closest('[data-field]');
        if (fieldEl) {
            state.fields[fieldEl.dataset.field] = fieldEl.value;
            renderIncomplete();
            return;
        }
        const scoreEl = e.target.closest('[data-item-score]');
        if (scoreEl) {
            const row = Number(scoreEl.dataset.itemScore);
            if (state.items[row]) {
                state.items[row].score = scoreEl.value;
                renderRating();
                renderIncomplete();
            }
        }
    });

    form.addEventListener('change', (e) => {
        const fieldEl = e.target.closest('[data-field]');
        if (fieldEl) { state.fields[fieldEl.dataset.field] = fieldEl.value; renderIncomplete(); }
    });

    const analysisEl = document.getElementById('te-analysis');
    analysisEl.value = state.analysis || '';
    analysisEl.addEventListener('input', () => {
        state.analysis = analysisEl.value;
        analysisCount();
        renderIncomplete();
    });

    document.getElementById('te-save').addEventListener('click', saveContent);
    document.getElementById('te-refresh-preview').addEventListener('click', reloadPreview);
    document.getElementById('te-export-word').addEventListener('click', exportWord);
    document.getElementById('te-analysis-rewrite').addEventListener('click', openRewriteModal);

    window.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') closeRewriteModal();
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
    analysisCount();
    renderIncomplete();
    renderImportDetails();
    bindEvents();
}

init();
