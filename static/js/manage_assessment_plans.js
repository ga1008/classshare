import { apiFetch } from './api.js';
import { showToast, escapeHtml, formatDate } from './ui.js';

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
const state = {
    plans: [],
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

const SOURCE_LABEL = { blank: '表单新建', classroom: '按课堂生成', import: '导入解析', exam_reverse: '试卷反推' };

const root = document.querySelector('[data-ap-root]');

function statusMeta(status) {
    return STATUS_META[status] || STATUS_META.draft;
}

function isBusy(plan) {
    return plan.status === 'generating' || plan.status === 'parsing';
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
                <button type="button" class="lp-modal__close" data-ap-close aria-label="关闭">×</button>
            </header>
            <div class="lp-modal__body">${bodyHtml}</div>
            <footer class="lp-modal__foot">${footerHtml}</footer>
        </div>`;
    document.body.appendChild(overlay);
    const close = () => overlay.remove();
    overlay.addEventListener('click', (e) => {
        if (e.target === overlay || e.target.closest('[data-ap-close]')) close();
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
    const summaryEl = root.querySelector('[data-ap-summary]');
    const total = state.plans.length;
    const mine = state.plans.filter((p) => p.is_owned).length;
    const busy = state.plans.filter(isBusy).length;
    const shared = state.plans.filter((p) => !p.is_owned).length;
    const items = [['全部', total], ['我的', mine], ['进行中', busy], ['他人公开', shared]];
    summaryEl.innerHTML = items
        .map(([label, value]) => `<span class="manage-lp__summary-item"><strong>${value}</strong><small>${label}</small></span>`)
        .join('');
}

function matchesFilters(plan) {
    const q = state.search.trim().toLowerCase();
    if (q) {
        const hay = [plan.title, plan.course_name, plan.class_name, (plan.tags || []).join(' ')]
            .join(' ').toLowerCase();
        if (!hay.includes(q)) return false;
    }
    const f = state.scopeFilter;
    if (!f) return true;
    if (f === 'mine') return plan.is_owned;
    if (f === 'shared') return !plan.is_owned;
    return plan.scope_level === f;
}

function sortPlans(plans) {
    const copy = [...plans];
    switch (state.sort) {
        case 'updated_asc': return copy.sort((a, b) => (a.updated_at || '').localeCompare(b.updated_at || ''));
        case 'title_asc': return copy.sort((a, b) => (a.title || '').localeCompare(b.title || '', 'zh'));
        default: return copy.sort((a, b) => (b.updated_at || '').localeCompare(a.updated_at || ''));
    }
}

function progressText(plan) {
    const p = plan.ai_gen_progress || {};
    const label = p.current_label ? escapeHtml(p.current_label) : '';
    if (label) return label;
    return plan.status === 'parsing' ? 'AI 正在解析文件…' : 'AI 正在准备…';
}

function scoreBadge(plan) {
    const total = plan.score_total || 0;
    const ok = plan.score_balanced;
    const tone = ok ? 'is-ready' : 'is-failed';
    const text = ok ? `分值 ${total}` : `分值 ${total}≠100`;
    return `<span class="lp-status ${tone}" title="考核项分值合计">${text}</span>`;
}

function renderCard(plan) {
    const meta = statusMeta(plan.status);
    const tags = (plan.tags || []).map((t) => `<span class="lp-tag">${escapeHtml(t)}</span>`).join('');
    const sourceBadge = `<span class="lp-source">${SOURCE_LABEL[plan.source_type] || '考核计划表'}</span>`;
    const scopeBadge = `<span class="lp-scope">${escapeHtml(plan.scope_label || '私有')}</span>`;

    if (isBusy(plan)) {
        const p = plan.ai_gen_progress || {};
        const pct = p.total ? Math.round((Number(p.done || 0) / Number(p.total)) * 100) : 35;
        return `
        <article class="lp-card lp-card--busy ${meta.tone}" data-ap-card="${plan.id}">
            <div class="lp-card__top"><span class="lp-status ${meta.tone}">${meta.label}</span>${sourceBadge}</div>
            <strong class="lp-card__title">${escapeHtml(plan.title)}</strong>
            <div class="lp-progress"><div class="lp-progress__bar" style="width:${pct}%"></div></div>
            <p class="lp-card__busy">${progressText(plan)}</p>
            <div class="lp-card__foot">
                <small>请稍候，可能需要几分钟…</small>
                <button type="button" class="lp-btn lp-btn--ghost" data-action="delete" data-id="${plan.id}">取消并删除</button>
            </div>
        </article>`;
    }

    if (plan.status === 'failed') {
        return `
        <article class="lp-card lp-card--failed" data-ap-card="${plan.id}">
            <div class="lp-card__top"><span class="lp-status is-failed">失败</span>${sourceBadge}</div>
            <strong class="lp-card__title">${escapeHtml(plan.title)}</strong>
            <p class="lp-card__error">${escapeHtml(plan.ai_gen_error || '生成/解析失败')}</p>
            <div class="lp-card__foot">
                <button type="button" class="lp-btn" data-action="retry" data-id="${plan.id}">一键重试</button>
                <button type="button" class="lp-btn lp-btn--danger" data-action="delete" data-id="${plan.id}">删除</button>
            </div>
        </article>`;
    }

    const owner = plan.is_owned ? '' : `<span class="lp-owner">来自 ${escapeHtml(plan.owner_teacher_name || '其他老师')}</span>`;
    const sig = [];
    if (plan.examiner_signature) sig.push('命题签名');
    if (plan.reviewer_signature) sig.push('系主任签名');
    const sigBadge = sig.length ? `<span class="lp-tag">${sig.join(' · ')}</span>` : '';
    const manageActions = plan.can_manage ? `
        <button type="button" class="lp-btn" data-action="edit" data-id="${plan.id}">编辑</button>
        <button type="button" class="lp-btn" data-action="preview" data-id="${plan.id}">预览/导出</button>
        <button type="button" class="lp-btn lp-btn--ghost" data-action="attributes" data-id="${plan.id}">属性</button>
        <button type="button" class="lp-btn lp-btn--ghost" data-action="tags" data-id="${plan.id}">标签</button>
        <button type="button" class="lp-btn lp-btn--danger" data-action="delete" data-id="${plan.id}">删除</button>
    ` : `
        <button type="button" class="lp-btn" data-action="preview" data-id="${plan.id}">预览/导出</button>
        <button type="button" class="lp-btn lp-btn--primary" data-action="inherit" data-id="${plan.id}">一键继承</button>
    `;

    return `
        <article class="lp-card" data-ap-card="${plan.id}">
            <div class="lp-card__top"><span class="lp-status ${meta.tone}">${meta.label}</span>${sourceBadge}${scopeBadge}${scoreBadge(plan)}</div>
            <strong class="lp-card__title">${escapeHtml(plan.title)}</strong>
            <div class="lp-card__meta">
                ${plan.course_name ? `<span>${escapeHtml(plan.course_name)}</span>` : ''}
                ${plan.class_name ? `<span>${escapeHtml(plan.class_name)}</span>` : ''}
                ${plan.assessment_type ? `<span>${escapeHtml(plan.assessment_type)}</span>` : ''}
                <span>${plan.item_count || 0} 考核项</span>
            </div>
            ${(tags || sigBadge) ? `<div class="lp-card__tags">${tags}${sigBadge}</div>` : ''}
            <div class="lp-card__foot">
                <small>${owner || ('更新于 ' + escapeHtml(formatDate(plan.updated_at)))}</small>
            </div>
            <div class="lp-card__actions">${manageActions}</div>
        </article>`;
}

function render() {
    renderSummary();
    const grid = root.querySelector('[data-ap-grid]');
    const loading = root.querySelector('[data-ap-loading]');
    const empty = root.querySelector('[data-ap-empty]');
    loading.hidden = true;
    const visible = sortPlans(state.plans.filter(matchesFilters));
    if (!state.plans.length) {
        grid.hidden = true;
        empty.hidden = false;
        return;
    }
    empty.hidden = true;
    grid.hidden = false;
    grid.innerHTML = visible.length
        ? visible.map(renderCard).join('')
        : `<div class="manage-lp__empty" style="grid-column:1/-1">没有符合筛选条件的考核计划表。</div>`;
}

// ---------------------------------------------------------------------------
// Data
// ---------------------------------------------------------------------------
async function loadPlans() {
    try {
        const data = await apiFetch('/api/assessment-plans');
        state.plans = data.assessment_plans || [];
        render();
        managePolling();
    } catch (err) {
        showToast(err.message || '加载考核计划表失败', 'error');
    }
}

function managePolling() {
    const hasBusy = state.plans.some(isBusy);
    if (hasBusy && !state.polling) {
        state.polling = setInterval(loadPlans, 4000);
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
        .concat(state.offerings.map((o) => `<option value="${o.id}">${escapeHtml(o.course_name)} · ${escapeHtml(o.display_class_name || o.class_name)}</option>`))
        .join('');
}

function openCreateBlankModal() {
    const body = `
        <form data-ap-form-blank class="lp-form">
            <label>标题<input name="title" placeholder="如：服务器配置与管理 考核计划表"></label>
            <label>绑定课堂（自动带入课程/班级/考核类型，可选）
                <select name="class_offering_id">${offeringOptionsHtml(true)}</select>
            </label>
            <p class="lp-form__hint">创建后进入编辑器，用完整表单填写基础信息和考核项目（分值合计须为 100）。</p>
        </form>`;
    const footer = `<button type="button" class="lp-btn lp-btn--primary" data-ap-submit>创建并编辑</button>`;
    openModal('空白新建考核计划表', body, {
        footerHtml: footer,
        onMount: (overlay, close) => {
            overlay.querySelector('[data-ap-submit]').addEventListener('click', async () => {
                const fd = new FormData(overlay.querySelector('[data-ap-form-blank]'));
                const payload = {
                    title: (fd.get('title') || '').trim(),
                    class_offering_id: fd.get('class_offering_id') || null,
                };
                try {
                    const res = await apiFetch('/api/assessment-plans', { method: 'POST', body: payload });
                    close();
                    window.location.href = `/assessment-plan/${res.id}/edit`;
                } catch (err) { showToast(err.message || '创建失败', 'error'); }
            });
        },
    });
}

function openGenerateModal() {
    if (!state.offerings.length) {
        showToast('你还没有可用课堂，请先在「开设课堂」创建。', 'error');
        return;
    }
    const body = `
        <form data-ap-form-generate class="lp-form">
            <label>选择课堂
                <select name="class_offering_id" required>${offeringOptionsHtml(false)}</select>
            </label>
            <label>给 AI 的补充要求（可选）
                <textarea name="prompt" rows="3" placeholder="如：以机试为主，重点考核 Linux 与数据库部署，分值合计 100。"></textarea>
            </label>
            <p class="lp-form__hint">系统将整合课堂内容、绑定文档、教材与教务考核形式，<strong>用思考型 AI 生成</strong>考核计划表（注意分值合计 100、命题教师签名将自动带入本人签名）。可关闭窗口，列表以占位卡显示进度。</p>
        </form>`;
    const footer = `<button type="button" class="lp-btn lp-btn--primary" data-ap-submit>开始生成</button>`;
    openModal('按课堂生成考核计划表', body, {
        footerHtml: footer,
        onMount: (overlay, close) => {
            overlay.querySelector('[data-ap-submit]').addEventListener('click', async () => {
                const fd = new FormData(overlay.querySelector('[data-ap-form-generate]'));
                const offeringId = fd.get('class_offering_id');
                if (!offeringId) { showToast('请选择课堂', 'error'); return; }
                try {
                    await apiFetch('/api/assessment-plans/generate', {
                        method: 'POST',
                        body: { class_offering_id: Number(offeringId), prompt: (fd.get('prompt') || '').trim() },
                    });
                    close();
                    showToast('已开始生成，列表中将显示进度。', 'success');
                    loadPlans();
                } catch (err) { showToast(err.message || '启动生成失败', 'error'); }
            });
        },
    });
}

function openImportModal() {
    const body = `
        <div class="lp-import">
            <div class="lp-dropzone" data-ap-dropzone>
                <p>拖拽文件到此处，或<button type="button" class="lp-link" data-ap-pick>点击选择文件</button></p>
                <small>支持 doc / docx / pdf / png / jpg 等，可多选；单文件 ≤ 30MB，最多 8 个。docx 中的签名图片会自动入签名库（去重）。</small>
                <input type="file" data-ap-file multiple hidden
                       accept=".doc,.docx,.pdf,.png,.jpg,.jpeg,.webp,.bmp,.gif,.md,.txt">
            </div>
            <ul class="lp-filelist" data-ap-filelist></ul>
            <label class="lp-form__full">给 AI 的额外提示（可选）
                <textarea data-ap-extra rows="3" placeholder="如：这是《服务器配置与管理》机试考核计划表，请忠实还原考核项与分值。"></textarea>
            </label>
            <p class="lp-form__hint">点击导入后将调用<strong>思考 + 多模态 AI</strong>解析，并自动归集签名图片。窗口会关闭并在列表中以占位卡显示「解析中」。</p>
        </div>`;
    const footer = `<button type="button" class="lp-btn lp-btn--primary" data-ap-submit>开始导入解析</button>`;
    openModal('导入考核计划表文件', body, {
        footerHtml: footer,
        onMount: (overlay, close) => {
            const picked = [];
            const input = overlay.querySelector('[data-ap-file]');
            const listEl = overlay.querySelector('[data-ap-filelist]');
            const dz = overlay.querySelector('[data-ap-dropzone]');
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
            overlay.querySelector('[data-ap-pick]').addEventListener('click', () => input.click());
            input.addEventListener('change', () => { addFiles(input.files); input.value = ''; });
            listEl.addEventListener('click', (e) => {
                const rm = e.target.closest('[data-rm]');
                if (rm) { picked.splice(Number(rm.dataset.rm), 1); renderFiles(); }
            });
            ['dragover', 'dragenter'].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add('is-over'); }));
            ['dragleave', 'drop'].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove('is-over'); }));
            dz.addEventListener('drop', (e) => { if (e.dataTransfer?.files) addFiles(e.dataTransfer.files); });
            overlay.querySelector('[data-ap-submit]').addEventListener('click', async () => {
                if (!picked.length) { showToast('请先选择文件', 'error'); return; }
                const fd = new FormData();
                picked.forEach((f) => fd.append('files', f));
                fd.append('extra_prompt', overlay.querySelector('[data-ap-extra]').value || '');
                try {
                    await apiFetch('/api/assessment-plans/import', { method: 'POST', body: fd });
                    close();
                    showToast('已开始解析，列表中将显示进度。', 'success');
                    loadPlans();
                } catch (err) { showToast(err.message || '导入失败', 'error'); }
            });
        },
    });
}

async function openAttributesModal(id) {
    let data;
    try { data = await apiFetch(`/api/assessment-plans/${id}/attributes`); }
    catch (err) { showToast(err.message || '读取属性失败', 'error'); return; }
    const scopeOptions = (data.scope_options || state.scopeOptions)
        .map((o) => `<option value="${o.value}"${o.value === data.scope_level ? ' selected' : ''}>${escapeHtml(o.label)}</option>`)
        .join('');
    const body = `
        <form data-ap-form-attr class="lp-form">
            <label>标题<input name="title" value="${escapeHtml(data.title || '')}"></label>
            <label>公开范围
                <select name="scope_level">${scopeOptions}</select>
            </label>
            <p class="lp-form__hint">默认私有；可设为本系部 / 本院级 / 全校公开，公开后其他老师可一键继承。</p>
        </form>`;
    const footer = `<button type="button" class="lp-btn lp-btn--primary" data-ap-submit>保存</button>`;
    openModal('考核计划表属性', body, {
        footerHtml: footer,
        onMount: (overlay, close) => {
            overlay.querySelector('[data-ap-submit]').addEventListener('click', async () => {
                const fd = new FormData(overlay.querySelector('[data-ap-form-attr]'));
                try {
                    await apiFetch(`/api/assessment-plans/${id}/attributes`, {
                        method: 'PATCH',
                        body: { title: (fd.get('title') || '').trim(), scope_level: fd.get('scope_level') },
                    });
                    close();
                    showToast('已保存', 'success');
                    loadPlans();
                } catch (err) { showToast(err.message || '保存失败', 'error'); }
            });
        },
    });
}

async function openTagsModal(id) {
    const plan = state.plans.find((p) => p.id === id);
    const current = (plan?.tags || []).join('、');
    const body = `
        <form data-ap-form-tags class="lp-form">
            <label>标签（用、或逗号分隔）
                <input name="tags" value="${escapeHtml(current)}" placeholder="如：机试、2025秋、专升本">
            </label>
        </form>`;
    const footer = `<button type="button" class="lp-btn lp-btn--primary" data-ap-submit>保存标签</button>`;
    openModal('设置标签', body, {
        footerHtml: footer,
        onMount: (overlay, close) => {
            overlay.querySelector('[data-ap-submit]').addEventListener('click', async () => {
                const raw = new FormData(overlay.querySelector('[data-ap-form-tags]')).get('tags') || '';
                const tags = raw.split(/[、,，\s]+/).map((t) => t.trim()).filter(Boolean);
                try {
                    await apiFetch(`/api/assessment-plans/${id}/tags`, { method: 'PUT', body: { tags } });
                    close();
                    showToast('标签已更新', 'success');
                    loadPlans();
                } catch (err) { showToast(err.message || '保存失败', 'error'); }
            });
        },
    });
}

function openPreviewModal(id) {
    const plan = state.plans.find((p) => p.id === id);
    const title = plan ? plan.title : '考核计划表预览';
    const body = `
        <div class="lp-preview">
            <div class="lp-preview__bar">
                <span>导出：</span>
                <a class="lp-btn" href="/api/assessment-plans/${id}/export?fmt=docx">Word (.docx)</a>
                <a class="lp-btn lp-btn--ghost" href="/assessment-plan/${id}/preview" target="_blank" rel="noopener">在新标签页打开</a>
            </div>
            <iframe class="lp-preview__frame" src="/assessment-plan/${id}/preview" title="考核计划表预览"></iframe>
        </div>`;
    openModal(`${title} · 渲染预览`, body, { wide: true });
}

async function inheritPlan(id) {
    if (!confirm('将这份公开考核计划表继承为你自己的私有副本？继承后命题信息会替换为你的信息。')) return;
    try {
        const res = await apiFetch(`/api/assessment-plans/${id}/inherit`, { method: 'POST' });
        showToast('已继承到你的库', 'success');
        window.location.href = `/assessment-plan/${res.id}/edit`;
    } catch (err) { showToast(err.message || '继承失败', 'error'); }
}

async function retryPlan(id) {
    try {
        await apiFetch(`/api/assessment-plans/${id}/retry`, { method: 'POST' });
        showToast('已重新开始', 'success');
        loadPlans();
    } catch (err) { showToast(err.message || '重试失败', 'error'); }
}

async function deletePlan(id) {
    if (!confirm('确定删除该考核计划表？此操作不可恢复。')) return;
    try {
        await apiFetch(`/api/assessment-plans/${id}`, { method: 'DELETE' });
        showToast('已删除', 'success');
        loadPlans();
    } catch (err) { showToast(err.message || '删除失败', 'error'); }
}

// ---------------------------------------------------------------------------
// Events
// ---------------------------------------------------------------------------
function bindEvents() {
    document.addEventListener('click', (e) => {
        if (e.target.closest('[data-ap-create-blank]')) { e.preventDefault(); openCreateBlankModal(); return; }
        if (e.target.closest('[data-ap-generate-open]')) { e.preventDefault(); openGenerateModal(); return; }
        if (e.target.closest('[data-ap-import-open]')) { e.preventDefault(); openImportModal(); }
    });

    const search = root.querySelector('[data-ap-search]');
    search.addEventListener('input', () => { state.search = search.value; render(); });
    root.querySelector('[data-ap-filter-scope]').addEventListener('change', (e) => { state.scopeFilter = e.target.value; render(); });
    root.querySelector('[data-ap-sort]').addEventListener('change', (e) => { state.sort = e.target.value; render(); });

    root.querySelector('[data-ap-grid]').addEventListener('click', (e) => {
        const btn = e.target.closest('[data-action]');
        if (!btn) return;
        const id = btn.dataset.id;
        switch (btn.dataset.action) {
            case 'edit': window.location.href = `/assessment-plan/${id}/edit`; break;
            case 'preview': openPreviewModal(id); break;
            case 'attributes': openAttributesModal(id); break;
            case 'tags': openTagsModal(id); break;
            case 'delete': deletePlan(id); break;
            case 'retry': retryPlan(id); break;
            case 'inherit': inheritPlan(id); break;
        }
    });
}

function boot() {
    if (!root) return;
    try {
        const bootEl = document.getElementById('assessment-plan-boot');
        const data = bootEl ? JSON.parse(bootEl.textContent) : {};
        state.offerings = data.offerings || [];
        state.scopeOptions = data.scope_options || [];
    } catch (_) { /* ignore boot parse errors */ }
    bindEvents();
    loadPlans();
}

boot();
