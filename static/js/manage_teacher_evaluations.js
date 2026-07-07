import { apiFetch } from './api.js';
import { showToast, escapeHtml, formatDate } from './ui.js';

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
const state = {
    evaluations: [],
    offerings: [],
    scopeOptions: [],
    search: '',
    scopeFilter: '',
    sort: 'updated_desc',
    polling: null,
};

const STATUS_META = {
    draft: { label: '草稿', tone: 'is-draft' },
    ready: { label: '可用', tone: 'is-ready' },
    generating: { label: '生成中', tone: 'is-busy' },
    parsing: { label: '解析中', tone: 'is-busy' },
    failed: { label: '失败', tone: 'is-failed' },
};

const SOURCE_LABEL = { blank: '表单新建', classroom: '按班级生成', import: '导入解析' };
const RATING_TONE = { 优秀: 'is-ready', 良好: 'is-ready', 一般: 'is-draft', 较差: 'is-failed' };

const root = document.querySelector('[data-te-root]');

function statusMeta(status) {
    return STATUS_META[status] || STATUS_META.draft;
}

function isBusy(evaluation) {
    return evaluation.status === 'generating' || evaluation.status === 'parsing';
}

// ---------------------------------------------------------------------------
// Modal helper (reuses .lp-modal* styling)
// ---------------------------------------------------------------------------
function openModal(title, bodyHtml, { footerHtml = '', onMount, wide = false } = {}) {
    const overlay = document.createElement('div');
    overlay.className = 'lp-modal-overlay';
    overlay.innerHTML = `
        <div class="lp-modal${wide ? ' lp-modal--wide' : ''}" role="dialog" aria-modal="true">
            <header class="lp-modal__head">
                <h3>${escapeHtml(title)}</h3>
                <button type="button" class="lp-modal__close" data-te-close aria-label="关闭">×</button>
            </header>
            <div class="lp-modal__body">${bodyHtml}</div>
            <footer class="lp-modal__foot">${footerHtml}</footer>
        </div>`;
    document.body.appendChild(overlay);
    const close = () => overlay.remove();
    overlay.addEventListener('click', (e) => {
        if (e.target === overlay || e.target.closest('[data-te-close]')) close();
    });
    document.addEventListener('keydown', function onEsc(e) {
        if (e.key === 'Escape') { close(); document.removeEventListener('keydown', onEsc); }
    });
    if (onMount) onMount(overlay, close);
    return { overlay, close };
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------
function renderSummary() {
    const summaryEl = root.querySelector('[data-te-summary]');
    const total = state.evaluations.length;
    const mine = state.evaluations.filter((p) => p.is_owned).length;
    const busy = state.evaluations.filter(isBusy).length;
    const shared = state.evaluations.filter((p) => !p.is_owned).length;
    const items = [['全部', total], ['我的', mine], ['进行中', busy], ['他人公开', shared]];
    summaryEl.innerHTML = items
        .map(([label, value]) => `<span class="manage-lp__summary-item"><strong>${value}</strong><small>${label}</small></span>`)
        .join('');
}

function matchesFilters(evaluation) {
    const q = state.search.trim().toLowerCase();
    if (q) {
        const hay = [evaluation.title, evaluation.course_name, evaluation.class_name, (evaluation.tags || []).join(' ')]
            .join(' ').toLowerCase();
        if (!hay.includes(q)) return false;
    }
    const f = state.scopeFilter;
    if (!f) return true;
    if (f === 'mine') return evaluation.is_owned;
    if (f === 'shared') return !evaluation.is_owned;
    return evaluation.scope_level === f;
}

function sortEvaluations(evaluations) {
    const copy = [...evaluations];
    switch (state.sort) {
        case 'updated_asc': return copy.sort((a, b) => (a.updated_at || '').localeCompare(b.updated_at || ''));
        case 'title_asc': return copy.sort((a, b) => (a.title || '').localeCompare(b.title || '', 'zh'));
        default: return copy.sort((a, b) => (b.updated_at || '').localeCompare(a.updated_at || ''));
    }
}

function progressText(evaluation) {
    const p = evaluation.ai_gen_progress || {};
    const label = p.current_label ? escapeHtml(p.current_label) : '';
    if (label) return label;
    return evaluation.status === 'parsing' ? 'AI 正在解析文件…' : 'AI 正在归集班级表现…';
}

function ratingBadge(evaluation) {
    const rating = evaluation.rating;
    if (rating) {
        const tone = RATING_TONE[rating] || 'is-draft';
        return `<span class="lp-status ${tone}" title="综合评价（总分 ${evaluation.score_total || 0}）">${escapeHtml(rating)} · ${evaluation.score_total || 0}</span>`;
    }
    if (evaluation.is_complete === false) {
        return `<span class="lp-status is-draft" title="尚未填写完整">待完善</span>`;
    }
    return '';
}

function renderCard(evaluation) {
    const meta = statusMeta(evaluation.status);
    const tags = (evaluation.tags || []).map((t) => `<span class="lp-tag">${escapeHtml(t)}</span>`).join('');
    const sourceBadge = `<span class="lp-source">${SOURCE_LABEL[evaluation.source_type] || '评学表'}</span>`;
    const scopeBadge = `<span class="lp-scope">${escapeHtml(evaluation.scope_label || '私有')}</span>`;

    if (isBusy(evaluation)) {
        const p = evaluation.ai_gen_progress || {};
        const pct = p.total ? Math.round((Number(p.done || 0) / Number(p.total)) * 100) : 35;
        return `
        <article class="lp-card lp-card--busy ${meta.tone}" data-te-card="${evaluation.id}">
            <div class="lp-card__top"><span class="lp-status ${meta.tone}">${meta.label}</span>${sourceBadge}</div>
            <strong class="lp-card__title">${escapeHtml(evaluation.title)}</strong>
            <div class="lp-progress"><div class="lp-progress__bar" style="width:${pct}%"></div></div>
            <p class="lp-card__busy">${progressText(evaluation)}</p>
            <div class="lp-card__foot">
                <small>请稍候，可能需要一会儿…</small>
                <button type="button" class="lp-btn lp-btn--ghost" data-action="delete" data-id="${evaluation.id}">取消并删除</button>
            </div>
        </article>`;
    }

    if (evaluation.status === 'failed') {
        return `
        <article class="lp-card lp-card--failed" data-te-card="${evaluation.id}">
            <div class="lp-card__top"><span class="lp-status is-failed">失败</span>${sourceBadge}</div>
            <strong class="lp-card__title">${escapeHtml(evaluation.title)}</strong>
            <p class="lp-card__error">${escapeHtml(evaluation.ai_gen_error || '生成/解析失败')}</p>
            <div class="lp-card__foot">
                <button type="button" class="lp-btn" data-action="retry" data-id="${evaluation.id}">一键重试</button>
                <button type="button" class="lp-btn lp-btn--danger" data-action="delete" data-id="${evaluation.id}">删除</button>
            </div>
        </article>`;
    }

    const owner = evaluation.is_owned ? '' : `<span class="lp-owner">来自 ${escapeHtml(evaluation.owner_teacher_name || '其他老师')}</span>`;
    const manageActions = evaluation.can_manage ? `
        <button type="button" class="lp-btn" data-action="edit" data-id="${evaluation.id}">编辑</button>
        <button type="button" class="lp-btn" data-action="preview" data-id="${evaluation.id}">预览/导出</button>
        <button type="button" class="lp-btn lp-btn--ghost" data-action="attributes" data-id="${evaluation.id}">属性</button>
        <button type="button" class="lp-btn lp-btn--ghost" data-action="tags" data-id="${evaluation.id}">标签</button>
        <button type="button" class="lp-btn lp-btn--danger" data-action="delete" data-id="${evaluation.id}">删除</button>
    ` : `
        <button type="button" class="lp-btn" data-action="preview" data-id="${evaluation.id}">预览/导出</button>
        <button type="button" class="lp-btn lp-btn--primary" data-action="inherit" data-id="${evaluation.id}">一键继承</button>
    `;

    return `
        <article class="lp-card" data-te-card="${evaluation.id}">
            <div class="lp-card__top"><span class="lp-status ${meta.tone}">${meta.label}</span>${sourceBadge}${scopeBadge}${ratingBadge(evaluation)}</div>
            <strong class="lp-card__title">${escapeHtml(evaluation.title)}</strong>
            <div class="lp-card__meta">
                ${evaluation.course_name ? `<span>${escapeHtml(evaluation.course_name)}</span>` : ''}
                ${evaluation.class_name ? `<span>${escapeHtml(evaluation.class_name)}</span>` : ''}
                ${evaluation.college ? `<span>${escapeHtml(evaluation.college)}</span>` : ''}
                ${evaluation.semester_label ? `<span>${escapeHtml(evaluation.semester_label)}</span>` : ''}
            </div>
            ${tags ? `<div class="lp-card__tags">${tags}</div>` : ''}
            <div class="lp-card__foot">
                <small>${owner || ('更新于 ' + escapeHtml(formatDate(evaluation.updated_at)))}</small>
            </div>
            <div class="lp-card__actions">${manageActions}</div>
        </article>`;
}

function render() {
    renderSummary();
    const grid = root.querySelector('[data-te-grid]');
    const loading = root.querySelector('[data-te-loading]');
    const empty = root.querySelector('[data-te-empty]');
    loading.hidden = true;
    const visible = sortEvaluations(state.evaluations.filter(matchesFilters));
    if (!state.evaluations.length) {
        grid.hidden = true;
        empty.hidden = false;
        return;
    }
    empty.hidden = true;
    grid.hidden = false;
    grid.innerHTML = visible.length
        ? visible.map(renderCard).join('')
        : `<div class="manage-lp__empty" style="grid-column:1/-1">没有符合筛选条件的教师评学表。</div>`;
}

// ---------------------------------------------------------------------------
// Data
// ---------------------------------------------------------------------------
async function loadEvaluations() {
    try {
        const data = await apiFetch('/api/teacher-evaluations');
        state.evaluations = data.teacher_evaluations || [];
        render();
        managePolling();
    } catch (err) {
        showToast(err.message || '加载教师评学表失败', 'error');
    }
}

function managePolling() {
    const hasBusy = state.evaluations.some(isBusy);
    if (hasBusy && !state.polling) {
        state.polling = setInterval(loadEvaluations, 4000);
    } else if (!hasBusy && state.polling) {
        clearInterval(state.polling);
        state.polling = null;
    }
}

// ---------------------------------------------------------------------------
// Modals
// ---------------------------------------------------------------------------
function offeringOptionsHtml(includeBlank) {
    const blank = includeBlank ? ['<option value="">不绑定课堂（手动填写）</option>'] : [];
    return blank
        .concat(state.offerings.map((o) => {
            const sem = o.semester_label ? ` · ${escapeHtml(o.semester_label)}` : '';
            return `<option value="${o.id}">${escapeHtml(o.course_name)} · ${escapeHtml(o.display_class_name || o.class_name)}${sem}</option>`;
        }))
        .join('');
}

function openCreateBlankModal() {
    const body = `
        <form data-te-form-blank class="lp-form">
            <label>标题<input name="title" placeholder="如：服务器配置与管理 教师评学表"></label>
            <label>绑定课堂（自动带入课程/班级/学院/学年学期，可选）
                <select name="class_offering_id">${offeringOptionsHtml(true)}</select>
            </label>
            <p class="lp-form__hint">创建后进入编辑器，用完整表单填写基础信息、为 10 项指标打分并撰写学习情况分析。</p>
        </form>`;
    const footer = `<button type="button" class="lp-btn lp-btn--primary" data-te-submit>创建并编辑</button>`;
    openModal('空白新建教师评学表', body, {
        footerHtml: footer,
        onMount: (overlay, close) => {
            overlay.querySelector('[data-te-submit]').addEventListener('click', async () => {
                const fd = new FormData(overlay.querySelector('[data-te-form-blank]'));
                const payload = {
                    title: (fd.get('title') || '').trim(),
                    class_offering_id: fd.get('class_offering_id') || null,
                };
                try {
                    const res = await apiFetch('/api/teacher-evaluations', { method: 'POST', body: payload });
                    close();
                    window.location.href = `/teacher-evaluation/${res.id}/edit`;
                } catch (err) { showToast(err.message || '创建失败', 'error'); }
            });
        },
    });
}

function openGenerateModal() {
    if (!state.offerings.length) {
        showToast('你还没有可用的教学班级，请先在「开设课堂」创建。', 'error');
        return;
    }
    const body = `
        <form data-te-form-generate class="lp-form">
            <label>选择教学班级（默认本学年学期在教的班级，可下拉选其他学期）
                <select name="class_offering_id" required>${offeringOptionsHtml(false)}</select>
            </label>
            <label>给 AI 的补充要求（可选）
                <textarea name="prompt" rows="3" placeholder="如：该班整体学习积极性较高，作业完成度好，请客观评分。"></textarea>
            </label>
            <p class="lp-form__hint">系统将<strong>归集该班级本学期在这门课的全部表现</strong>（作业/考试成绩、课堂互动、修炼等级等），用<strong>快速 AI</strong> 为 10 项指标公平打分（总分 60-95），自动计算综合评价并撰写学习情况分析。可关闭窗口，列表以占位卡显示进度。</p>
        </form>`;
    const footer = `<button type="button" class="lp-btn lp-btn--primary" data-te-submit>开始生成</button>`;
    openModal('按班级生成教师评学表', body, {
        footerHtml: footer,
        onMount: (overlay, close) => {
            overlay.querySelector('[data-te-submit]').addEventListener('click', async () => {
                const fd = new FormData(overlay.querySelector('[data-te-form-generate]'));
                const offeringId = fd.get('class_offering_id');
                if (!offeringId) { showToast('请选择教学班级', 'error'); return; }
                try {
                    await apiFetch('/api/teacher-evaluations/generate', {
                        method: 'POST',
                        body: { class_offering_id: Number(offeringId), prompt: (fd.get('prompt') || '').trim() },
                    });
                    close();
                    showToast('已开始生成，列表中将显示进度。', 'success');
                    loadEvaluations();
                } catch (err) { showToast(err.message || '启动生成失败', 'error'); }
            });
        },
    });
}

function openImportModal() {
    const body = `
        <div class="lp-import">
            <div class="lp-dropzone" data-te-dropzone>
                <p>拖拽文件到此处，或<button type="button" class="lp-link" data-te-pick>点击选择文件</button></p>
                <small>支持 doc / docx / pdf / png / jpg 等，可多选；单文件 ≤ 30MB，最多 8 个。</small>
                <input type="file" data-te-file multiple hidden
                       accept=".doc,.docx,.pdf,.png,.jpg,.jpeg,.webp,.bmp,.gif,.md,.txt">
            </div>
            <ul class="lp-filelist" data-te-filelist></ul>
            <label class="lp-form__full">给 AI 的额外提示（可选）
                <textarea data-te-extra rows="3" placeholder="如：这是《服务器配置与管理》软工231班的教师评学表，请忠实还原各项得分与评语。"></textarea>
            </label>
            <p class="lp-form__hint">点击导入后将调用<strong>思考 + 多模态 AI</strong>解析字段、10 项得分与评语。窗口会关闭并在列表中以占位卡显示「解析中」。</p>
        </div>`;
    const footer = `<button type="button" class="lp-btn lp-btn--primary" data-te-submit>开始导入解析</button>`;
    openModal('导入教师评学表文件', body, {
        footerHtml: footer,
        onMount: (overlay, close) => {
            const picked = [];
            const input = overlay.querySelector('[data-te-file]');
            const listEl = overlay.querySelector('[data-te-filelist]');
            const dz = overlay.querySelector('[data-te-dropzone]');
            const renderFiles = () => {
                listEl.innerHTML = picked.map((f, i) => `
                    <li><span>${escapeHtml(f.name)}</span>
                    <button type="button" class="lp-link" data-rm="${i}">移除</button></li>`).join('');
            };
            const addFiles = (files) => {
                for (const f of files) {
                    if (picked.length >= 8) { showToast('最多 8 个文件', 'error'); break; }
                    picked.push(f);
                }
                renderFiles();
            };
            overlay.querySelector('[data-te-pick]').addEventListener('click', () => input.click());
            input.addEventListener('change', () => { addFiles(input.files); input.value = ''; });
            listEl.addEventListener('click', (e) => {
                const rm = e.target.closest('[data-rm]');
                if (rm) { picked.splice(Number(rm.dataset.rm), 1); renderFiles(); }
            });
            ['dragover', 'dragenter'].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add('is-over'); }));
            ['dragleave', 'drop'].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove('is-over'); }));
            dz.addEventListener('drop', (e) => { if (e.dataTransfer?.files) addFiles(e.dataTransfer.files); });
            overlay.querySelector('[data-te-submit]').addEventListener('click', async () => {
                if (!picked.length) { showToast('请先选择文件', 'error'); return; }
                const fd = new FormData();
                picked.forEach((f) => fd.append('files', f));
                fd.append('extra_prompt', overlay.querySelector('[data-te-extra]').value || '');
                try {
                    await apiFetch('/api/teacher-evaluations/import', { method: 'POST', body: fd });
                    close();
                    showToast('已开始解析，列表中将显示进度。', 'success');
                    loadEvaluations();
                } catch (err) { showToast(err.message || '导入失败', 'error'); }
            });
        },
    });
}

async function openAttributesModal(id) {
    let data;
    try { data = await apiFetch(`/api/teacher-evaluations/${id}/attributes`); }
    catch (err) { showToast(err.message || '读取属性失败', 'error'); return; }
    const scopeOptions = (data.scope_options || state.scopeOptions)
        .map((o) => `<option value="${o.value}"${o.value === data.scope_level ? ' selected' : ''}>${escapeHtml(o.label)}</option>`)
        .join('');
    const body = `
        <form data-te-form-attr class="lp-form">
            <label>标题<input name="title" value="${escapeHtml(data.title || '')}"></label>
            <label>公开范围
                <select name="scope_level">${scopeOptions}</select>
            </label>
            <p class="lp-form__hint">默认私有；可设为本系部 / 本院级 / 全校公开，公开后其他老师可一键继承。</p>
        </form>`;
    const footer = `<button type="button" class="lp-btn lp-btn--primary" data-te-submit>保存</button>`;
    openModal('教师评学表属性', body, {
        footerHtml: footer,
        onMount: (overlay, close) => {
            overlay.querySelector('[data-te-submit]').addEventListener('click', async () => {
                const fd = new FormData(overlay.querySelector('[data-te-form-attr]'));
                try {
                    await apiFetch(`/api/teacher-evaluations/${id}/attributes`, {
                        method: 'PATCH',
                        body: { title: (fd.get('title') || '').trim(), scope_level: fd.get('scope_level') },
                    });
                    close();
                    showToast('已保存', 'success');
                    loadEvaluations();
                } catch (err) { showToast(err.message || '保存失败', 'error'); }
            });
        },
    });
}

async function openTagsModal(id) {
    const evaluation = state.evaluations.find((p) => p.id === id);
    const current = (evaluation?.tags || []).join('、');
    const body = `
        <form data-te-form-tags class="lp-form">
            <label>标签（用、或逗号分隔）
                <input name="tags" value="${escapeHtml(current)}" placeholder="如：软工231、2025秋、优秀">
            </label>
        </form>`;
    const footer = `<button type="button" class="lp-btn lp-btn--primary" data-te-submit>保存标签</button>`;
    openModal('设置标签', body, {
        footerHtml: footer,
        onMount: (overlay, close) => {
            overlay.querySelector('[data-te-submit]').addEventListener('click', async () => {
                const raw = new FormData(overlay.querySelector('[data-te-form-tags]')).get('tags') || '';
                const tags = raw.split(/[、,，\s]+/).map((t) => t.trim()).filter(Boolean);
                try {
                    await apiFetch(`/api/teacher-evaluations/${id}/tags`, { method: 'PUT', body: { tags } });
                    close();
                    showToast('标签已更新', 'success');
                    loadEvaluations();
                } catch (err) { showToast(err.message || '保存失败', 'error'); }
            });
        },
    });
}

function openPreviewModal(id) {
    const evaluation = state.evaluations.find((p) => p.id === id);
    const title = evaluation ? evaluation.title : '教师评学表预览';
    const body = `
        <div class="lp-preview">
            <div class="lp-preview__bar">
                <span>导出：</span>
                <a class="lp-btn" href="/api/teacher-evaluations/${id}/export?fmt=docx">Word (.docx)</a>
                <a class="lp-btn lp-btn--ghost" href="/teacher-evaluation/${id}/preview" target="_blank" rel="noopener">在新标签页打开</a>
            </div>
            <iframe class="lp-preview__frame" src="/teacher-evaluation/${id}/preview" title="教师评学表预览"></iframe>
        </div>`;
    openModal(`${title} · 渲染预览`, body, { wide: true });
}

async function inheritEvaluation(id) {
    if (!confirm('将这份公开评学表继承为你自己的私有副本？继承后任课教师会替换为你的信息。')) return;
    try {
        const res = await apiFetch(`/api/teacher-evaluations/${id}/inherit`, { method: 'POST' });
        showToast('已继承到你的库', 'success');
        window.location.href = `/teacher-evaluation/${res.id}/edit`;
    } catch (err) { showToast(err.message || '继承失败', 'error'); }
}

async function retryEvaluation(id) {
    try {
        await apiFetch(`/api/teacher-evaluations/${id}/retry`, { method: 'POST' });
        showToast('已重新开始', 'success');
        loadEvaluations();
    } catch (err) { showToast(err.message || '重试失败', 'error'); }
}

async function deleteEvaluation(id) {
    if (!confirm('确定删除该教师评学表？此操作不可恢复。')) return;
    try {
        await apiFetch(`/api/teacher-evaluations/${id}`, { method: 'DELETE' });
        showToast('已删除', 'success');
        loadEvaluations();
    } catch (err) { showToast(err.message || '删除失败', 'error'); }
}

// ---------------------------------------------------------------------------
// Events
// ---------------------------------------------------------------------------
function bindEvents() {
    document.addEventListener('click', (e) => {
        if (e.target.closest('[data-te-create-blank]')) { e.preventDefault(); openCreateBlankModal(); return; }
        if (e.target.closest('[data-te-generate-open]')) { e.preventDefault(); openGenerateModal(); return; }
        if (e.target.closest('[data-te-import-open]')) { e.preventDefault(); openImportModal(); }
    });

    const search = root.querySelector('[data-te-search]');
    search.addEventListener('input', () => { state.search = search.value; render(); });
    root.querySelector('[data-te-filter-scope]').addEventListener('change', (e) => { state.scopeFilter = e.target.value; render(); });
    root.querySelector('[data-te-sort]').addEventListener('change', (e) => { state.sort = e.target.value; render(); });

    root.querySelector('[data-te-grid]').addEventListener('click', (e) => {
        const btn = e.target.closest('[data-action]');
        if (!btn) return;
        const id = btn.dataset.id;
        switch (btn.dataset.action) {
            case 'edit': window.location.href = `/teacher-evaluation/${id}/edit`; break;
            case 'preview': openPreviewModal(id); break;
            case 'attributes': openAttributesModal(id); break;
            case 'tags': openTagsModal(id); break;
            case 'delete': deleteEvaluation(id); break;
            case 'retry': retryEvaluation(id); break;
            case 'inherit': inheritEvaluation(id); break;
        }
    });
}

function boot() {
    if (!root) return;
    try {
        const bootEl = document.getElementById('teacher-evaluation-boot');
        const data = bootEl ? JSON.parse(bootEl.textContent) : {};
        state.offerings = data.offerings || [];
        state.scopeOptions = data.scope_options || [];
    } catch (_) { /* ignore boot parse errors */ }
    bindEvents();
    loadEvaluations();
}

boot();
