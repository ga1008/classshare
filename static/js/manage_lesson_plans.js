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

const SOURCE_LABEL = { blank: '空白新建', classroom: '按课堂生成', import: '导入解析' };

const root = document.querySelector('[data-lp-root]');

function statusMeta(status) {
    return STATUS_META[status] || STATUS_META.draft;
}

function isBusy(plan) {
    return plan.status === 'generating' || plan.status === 'parsing';
}

// ---------------------------------------------------------------------------
// Lightweight modal helper
// ---------------------------------------------------------------------------
function openModal(title, bodyHtml, { footerHtml = '', onMount, wide = false } = {}) {
    const overlay = document.createElement('div');
    overlay.className = 'lp-modal-overlay';
    overlay.innerHTML = `
        <div class="lp-modal${wide ? ' lp-modal--wide' : ''}" role="dialog" aria-modal="true">
            <header class="lp-modal__head">
                <h3>${escapeHtml(title)}</h3>
                <button type="button" class="lp-modal__close" data-lp-close aria-label="关闭">×</button>
            </header>
            <div class="lp-modal__body">${bodyHtml}</div>
            <footer class="lp-modal__foot">${footerHtml}</footer>
        </div>`;
    document.body.appendChild(overlay);
    const close = () => overlay.remove();
    overlay.addEventListener('click', (e) => {
        if (e.target === overlay || e.target.closest('[data-lp-close]')) close();
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
    const summaryEl = root.querySelector('[data-lp-summary]');
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
        case 'sessions_desc': return copy.sort((a, b) => (b.session_count || 0) - (a.session_count || 0));
        default: return copy.sort((a, b) => (b.updated_at || '').localeCompare(a.updated_at || ''));
    }
}

function progressText(plan) {
    const p = plan.ai_gen_progress || {};
    const total = Number(p.total || 0);
    const done = Number(p.done || 0);
    const label = p.current_label ? `：${escapeHtml(p.current_label)}` : '';
    if (total > 0) return `第 ${done}/${total} 次课${label}`;
    return plan.status === 'parsing' ? 'AI 正在解析文件…' : 'AI 正在准备…';
}

function renderCard(plan) {
    const meta = statusMeta(plan.status);
    const tags = (plan.tags || []).map((t) => `<span class="lp-tag">${escapeHtml(t)}</span>`).join('');
    const sourceBadge = `<span class="lp-source">${SOURCE_LABEL[plan.source_type] || '教案'}</span>`;
    const scopeBadge = `<span class="lp-scope">${escapeHtml(plan.scope_label || '私有')}</span>`;

    if (isBusy(plan)) {
        const p = plan.ai_gen_progress || {};
        const pct = p.total ? Math.round((Number(p.done || 0) / Number(p.total)) * 100) : 25;
        return `
        <article class="lp-card lp-card--busy ${meta.tone}" data-lp-card="${plan.id}">
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
        <article class="lp-card lp-card--failed" data-lp-card="${plan.id}">
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
    const manageActions = plan.can_manage ? `
        <button type="button" class="lp-btn" data-action="edit" data-id="${plan.id}">内容编辑</button>
        <button type="button" class="lp-btn" data-action="preview" data-id="${plan.id}">预览/导出</button>
        <button type="button" class="lp-btn lp-btn--ghost" data-action="attributes" data-id="${plan.id}">属性</button>
        <button type="button" class="lp-btn lp-btn--ghost" data-action="tags" data-id="${plan.id}">标签</button>
        <button type="button" class="lp-btn lp-btn--danger" data-action="delete" data-id="${plan.id}">删除</button>
    ` : `
        <button type="button" class="lp-btn" data-action="preview" data-id="${plan.id}">预览/导出</button>
        <button type="button" class="lp-btn lp-btn--primary" data-action="inherit" data-id="${plan.id}">一键继承</button>
    `;

    return `
        <article class="lp-card" data-lp-card="${plan.id}">
            <div class="lp-card__top"><span class="lp-status ${meta.tone}">${meta.label}</span>${sourceBadge}${scopeBadge}</div>
            <strong class="lp-card__title">${escapeHtml(plan.title)}</strong>
            <div class="lp-card__meta">
                ${plan.course_name ? `<span>${escapeHtml(plan.course_name)}</span>` : ''}
                ${plan.class_name ? `<span>${escapeHtml(plan.class_name)}</span>` : ''}
                <span>${plan.session_count || 0} 次课</span>
            </div>
            ${tags ? `<div class="lp-card__tags">${tags}</div>` : ''}
            <div class="lp-card__foot">
                <small>${owner || ('更新于 ' + escapeHtml(formatDate(plan.updated_at)))}</small>
            </div>
            <div class="lp-card__actions">${manageActions}</div>
        </article>`;
}

function render() {
    renderSummary();
    const grid = root.querySelector('[data-lp-grid]');
    const loading = root.querySelector('[data-lp-loading]');
    const empty = root.querySelector('[data-lp-empty]');
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
        : `<div class="manage-lp__empty" style="grid-column:1/-1">没有符合筛选条件的教案。</div>`;
}

// ---------------------------------------------------------------------------
// Data
// ---------------------------------------------------------------------------
async function loadPlans() {
    try {
        const data = await apiFetch('/api/lesson-plans');
        state.plans = data.lesson_plans || [];
        render();
        managePolling();
    } catch (err) {
        showToast(err.message || '加载教案失败', 'error');
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
// Modals: create blank / generate / import / attributes / tags / preview
// ---------------------------------------------------------------------------
function openCreateBlankModal() {
    const offerings = state.offerings;
    const offeringOptions = ['<option value="">不绑定课堂（手动填写封面）</option>']
        .concat(offerings.map((o) => `<option value="${o.id}">${escapeHtml(o.course_name)} · ${escapeHtml(o.class_name)}</option>`))
        .join('');
    const body = `
        <form data-lp-form-blank class="lp-form">
            <label>教案标题<input name="title" placeholder="如：服务器配置与管理 教案" required></label>
            <label>绑定课堂（自动带出封面信息，可选）
                <select name="class_offering_id">${offeringOptions}</select>
            </label>
            <label>课次数量<input name="session_count" type="number" min="0" max="60" value="16"></label>
            <p class="lp-form__hint">创建后进入编辑器逐项填写；也可改用「按课堂生成」让 AI 自动生成整学期内容。</p>
        </form>`;
    const footer = `<button type="button" class="lp-btn lp-btn--primary" data-lp-submit>创建并编辑</button>`;
    openModal('空白新建教案', body, {
        footerHtml: footer,
        onMount: (overlay, close) => {
            overlay.querySelector('[data-lp-submit]').addEventListener('click', async () => {
                const form = overlay.querySelector('[data-lp-form-blank]');
                const fd = new FormData(form);
                const payload = {
                    title: (fd.get('title') || '').trim(),
                    class_offering_id: fd.get('class_offering_id') || null,
                    session_count: Number(fd.get('session_count') || 0),
                };
                if (!payload.title) { showToast('请填写教案标题', 'error'); return; }
                try {
                    const res = await apiFetch('/api/lesson-plans', { method: 'POST', body: payload });
                    close();
                    window.location.href = `/lesson-plan/${res.id}/edit`;
                } catch (err) { showToast(err.message || '创建失败', 'error'); }
            });
        },
    });
}

function normalizePlannerSessions(sessions) {
    return (sessions || []).map((session, index) => {
        const schedule = session.schedule && typeof session.schedule === 'object' ? session.schedule : {};
        return {
            ...session,
            client_id: session.client_id || `session-${Date.now()}-${index}`,
            source_type: session.source_type || 'classroom',
            source_session_id: Number(session.source_session_id || session.session_id || 0),
            chapter: session.chapter || session.title || `第 ${index + 1} 次课`,
            title: session.title || session.chapter || `第 ${index + 1} 次课`,
            schedule,
            schedule_text: session.schedule_text || schedule.text || '',
            section_minutes: Number(session.section_minutes || 80),
            source_material_ids: Array.isArray(session.source_material_ids) ? session.source_material_ids : [],
            materials: Array.isArray(session.materials) ? session.materials : [],
            material_summary: session.material_summary || '',
            prompt_hint: session.prompt_hint || '',
            manual_outline: session.manual_outline || '',
        };
    });
}

function plannerSessionContext(session) {
    return [
        session.chapter || session.title || '',
        session.schedule_text || '',
        session.material_summary || session.manual_outline || '',
        session.prompt_hint || '',
    ].filter(Boolean).join('\n').slice(0, 1600);
}

function renderPlannerCourseCard(offering, selectedId) {
    const active = Number(offering.id) === Number(selectedId) ? ' is-active' : '';
    const count = Number(offering.session_count || 0);
    const className = offering.display_class_name || offering.class_name || '';
    const meta = [
        offering.semester_label || '',
        offering.textbook_title ? `教材：${offering.textbook_title}` : '',
        `${count} 次课`,
    ].filter(Boolean).join(' · ');
    return `
        <button type="button" class="lp-gen-course${active}" data-offering-id="${offering.id}">
            <strong>${escapeHtml(offering.course_name || '未命名课程')}</strong>
            <span>${escapeHtml(className || '未绑定班级')}</span>
            <small>${escapeHtml(meta)}</small>
        </button>`;
}

function renderPlannerSessionCard(session, index) {
    const materials = (session.materials || []).slice(0, 4)
        .map((item) => `<span>${escapeHtml(item.name || item.material_path || '教学文档')}</span>`)
        .join('');
    const scheduleText = session.schedule_text || session.schedule?.text || '';
    return `
        <article class="lp-gen-session" data-session-key="${escapeHtml(session.client_id)}" draggable="true">
            <div class="lp-gen-session__head">
                <span class="lp-gen-session__grab" title="拖动排序">↕</span>
                <strong>第 ${index + 1} 次课</strong>
                <div class="lp-gen-session__actions">
                    <button type="button" class="lp-btn lp-btn--ghost" data-action="move-session" data-dir="-1" title="上移">↑</button>
                    <button type="button" class="lp-btn lp-btn--ghost" data-action="move-session" data-dir="1" title="下移">↓</button>
                    <button type="button" class="lp-link lp-link--danger" data-action="remove-session">删除</button>
                </div>
            </div>
            <div class="lp-gen-session__grid">
                <label>课次主题
                    <input data-field="chapter" value="${escapeHtml(session.chapter || '')}">
                </label>
                <label>时间/节次
                    <input data-field="schedule_text" value="${escapeHtml(scheduleText)}" placeholder="第几周、星期、节次">
                </label>
                <label>课时分钟
                    <input data-field="section_minutes" type="number" min="40" max="240" step="10" value="${Number(session.section_minutes || 80)}">
                </label>
                <label class="lp-form__full">课堂绑定材料摘要
                    <textarea data-field="material_summary" rows="3" placeholder="课堂页绑定材料会自动带出摘要，可在此微调">${escapeHtml(session.material_summary || '')}</textarea>
                </label>
                <label class="lp-form__full">给 AI 的课次提示
                    <textarea data-field="prompt_hint" rows="2" placeholder="例如：强调组件通信、课堂演示、分层练习">${escapeHtml(session.prompt_hint || '')}</textarea>
                </label>
            </div>
            ${materials ? `<div class="lp-gen-session__materials">${materials}</div>` : ''}
        </article>`;
}

function renderPlannerDetail(plan, offering, loading = false) {
    if (loading) {
        return `<div class="lp-gen-placeholder"><span></span><p>正在读取课堂课次与教学文档...</p></div>`;
    }
    if (!plan) {
        return `<div class="lp-gen-placeholder"><span></span><p>请选择左侧课程</p></div>`;
    }
    const cover = plan.cover || {};
    const classroom = plan.classroom || {};
    const sessions = plan.sessions || [];
    const titleValue = `${cover.course_name || offering?.course_name || '课程'} · ${cover.class_name || offering?.display_class_name || offering?.class_name || '班级'} 教案`;
    const infoItems = [
        ['课程', cover.course_name || offering?.course_name || ''],
        ['班级', cover.class_name || offering?.display_class_name || offering?.class_name || ''],
        ['教师', cover.teacher_name || classroom.teacher_name || ''],
        ['学期', cover.semester_label || offering?.semester_label || classroom.semester_name || ''],
        ['教材', cover.textbook || offering?.textbook_title || classroom.textbook_title || ''],
        ['出版社', cover.publisher || offering?.textbook_publisher || ''],
    ].filter(([, value]) => value);
    const insertOptions = [
        `<option value="${sessions.length}">追加到最后</option>`,
        ...sessions.map((session, index) => `<option value="${index}">插入到第 ${index + 1} 次课前：${escapeHtml(session.chapter || session.title || '')}</option>`),
    ].join('');
    return `
        <div class="lp-gen-detail">
            <div class="lp-gen-detail__top">
                <label>教案标题
                    <input data-gen-title value="${escapeHtml(titleValue)}">
                </label>
                <div class="lp-gen-detail__meta">
                    ${infoItems.map(([label, value]) => `<span><b>${escapeHtml(label)}</b>${escapeHtml(value)}</span>`).join('')}
                </div>
            </div>
            <div class="lp-gen-sessions-head">
                <div>
                    <strong>${sessions.length} 次课</strong>
                    <small>拖动卡片即可调整生成顺序</small>
                </div>
                <button type="button" class="lp-btn lp-btn--ghost" data-action="reload-plan">重新读取课堂</button>
            </div>
            <div class="lp-gen-session-list" data-gen-session-list>
                ${sessions.map((session, index) => renderPlannerSessionCard(session, index)).join('') || '<div class="lp-gen-empty">该课堂暂无课次，可在下方新增。</div>'}
            </div>
            <div class="lp-gen-add">
                <label>插入位置
                    <select data-gen-insert-index>${insertOptions}</select>
                </label>
                <label class="lp-form__full">新增课次提示
                    <textarea data-gen-new-prompt rows="2" placeholder="输入本次课主要内容，AI 会结合前后课次润色成可生成的课次卡片"></textarea>
                </label>
                <button type="button" class="lp-btn" data-action="draft-session">新增课次</button>
            </div>
        </div>`;
}

function openGeneratePlannerModal() {
    const offerings = state.offerings;
    if (!offerings.length) {
        showToast('你还没有可用课堂，请先在开设课堂中创建并安排课次。', 'error');
        return;
    }
    const planner = {
        selectedId: Number(offerings[0].id),
        loadingId: null,
        plans: new Map(),
        draggingKey: '',
    };
    const body = `
        <div class="lp-gen-planner">
            <aside class="lp-gen-sidebar" data-gen-course-list></aside>
            <section class="lp-gen-main" data-gen-main></section>
        </div>`;
    const footer = `
        <button type="button" class="lp-btn lp-btn--ghost" data-lp-close>取消</button>
        <button type="button" class="lp-btn lp-btn--primary" data-gen-submit>开始生成</button>`;

    function currentOffering() {
        return offerings.find((item) => Number(item.id) === Number(planner.selectedId)) || offerings[0];
    }

    function currentPlan() {
        return planner.plans.get(String(planner.selectedId));
    }

    function render(overlay) {
        const courseList = overlay.querySelector('[data-gen-course-list]');
        const main = overlay.querySelector('[data-gen-main]');
        const submit = overlay.querySelector('[data-gen-submit]');
        courseList.innerHTML = offerings.map((offering) => renderPlannerCourseCard(offering, planner.selectedId)).join('');
        main.innerHTML = renderPlannerDetail(
            currentPlan(),
            currentOffering(),
            Number(planner.loadingId) === Number(planner.selectedId),
        );
        const canSubmit = Boolean(currentPlan()?.sessions?.length);
        if (submit) submit.disabled = !canSubmit || Number(planner.loadingId) === Number(planner.selectedId);
    }

    async function loadPlan(overlay, offeringId, { force = false } = {}) {
        planner.selectedId = Number(offeringId);
        if (!force && planner.plans.has(String(offeringId))) {
            render(overlay);
            return;
        }
        planner.loadingId = Number(offeringId);
        render(overlay);
        try {
            const data = await apiFetch(`/api/lesson-plans/classroom/${offeringId}/generation-plan`);
            data.sessions = normalizePlannerSessions(data.sessions || []);
            planner.plans.set(String(offeringId), data);
        } catch (err) {
            showToast(err.message || '读取课堂生成计划失败', 'error');
        } finally {
            planner.loadingId = null;
            render(overlay);
        }
    }

    function updateSessionField(target) {
        const card = target.closest('[data-session-key]');
        const plan = currentPlan();
        if (!card || !plan) return;
        const session = plan.sessions.find((item) => item.client_id === card.dataset.sessionKey);
        if (!session) return;
        const field = target.dataset.field;
        if (field === 'chapter') {
            session.chapter = target.value;
            session.title = target.value;
        } else if (field === 'schedule_text') {
            session.schedule_text = target.value;
            session.schedule = { ...(session.schedule || {}), text: target.value };
        } else if (field === 'section_minutes') {
            session.section_minutes = Math.max(40, Number(target.value || 80));
        } else if (field === 'material_summary' || field === 'prompt_hint') {
            session[field] = target.value;
        }
    }

    function moveSession(key, delta) {
        const plan = currentPlan();
        if (!plan) return;
        const from = plan.sessions.findIndex((item) => item.client_id === key);
        const to = from + delta;
        if (from < 0 || to < 0 || to >= plan.sessions.length) return;
        const [item] = plan.sessions.splice(from, 1);
        plan.sessions.splice(to, 0, item);
    }

    function payloadSessions(plan) {
        return (plan.sessions || []).map((session, index) => ({
            client_id: session.client_id,
            index: index + 1,
            source_type: session.source_type || 'classroom',
            source_session_id: Number(session.source_session_id || 0),
            chapter: session.chapter || session.title || `第 ${index + 1} 次课`,
            title: session.title || session.chapter || `第 ${index + 1} 次课`,
            schedule: session.schedule || {},
            schedule_text: session.schedule_text || session.schedule?.text || '',
            section_minutes: Number(session.section_minutes || 80),
            source_material_ids: Array.isArray(session.source_material_ids) ? session.source_material_ids : [],
            material_summary: session.material_summary || '',
            prompt_hint: session.prompt_hint || '',
            manual_outline: session.manual_outline || '',
            content: session.content || '',
        }));
    }

    openModal('按课堂生成整学期教案', body, {
        footerHtml: footer,
        wide: true,
        onMount: (overlay, close) => {
            render(overlay);
            loadPlan(overlay, planner.selectedId);

            overlay.addEventListener('click', async (e) => {
                const offeringBtn = e.target.closest('[data-offering-id]');
                if (offeringBtn) {
                    await loadPlan(overlay, Number(offeringBtn.dataset.offeringId));
                    return;
                }
                const actionBtn = e.target.closest('[data-action]');
                if (!actionBtn) return;
                const action = actionBtn.dataset.action;
                if (action === 'reload-plan') {
                    await loadPlan(overlay, planner.selectedId, { force: true });
                    return;
                }
                const card = actionBtn.closest('[data-session-key]');
                if (action === 'remove-session' && card) {
                    const plan = currentPlan();
                    plan.sessions = plan.sessions.filter((item) => item.client_id !== card.dataset.sessionKey);
                    render(overlay);
                    return;
                }
                if (action === 'move-session' && card) {
                    moveSession(card.dataset.sessionKey, Number(actionBtn.dataset.dir || 0));
                    render(overlay);
                    return;
                }
                if (action === 'draft-session') {
                    const plan = currentPlan();
                    const promptEl = overlay.querySelector('[data-gen-new-prompt]');
                    const prompt = (promptEl?.value || '').trim();
                    if (!plan || !prompt) {
                        showToast('请先输入新增课次的主要内容。', 'error');
                        return;
                    }
                    const insertIndex = Math.min(
                        plan.sessions.length,
                        Math.max(0, Number(overlay.querySelector('[data-gen-insert-index]')?.value || plan.sessions.length)),
                    );
                    actionBtn.disabled = true;
                    try {
                        const data = await apiFetch(`/api/lesson-plans/classroom/${planner.selectedId}/session-draft`, {
                            method: 'POST',
                            body: {
                                prompt,
                                previous_context: insertIndex > 0 ? plannerSessionContext(plan.sessions[insertIndex - 1]) : '',
                                next_context: insertIndex < plan.sessions.length ? plannerSessionContext(plan.sessions[insertIndex]) : '',
                            },
                        });
                        const [draft] = normalizePlannerSessions([data.session || {}]);
                        draft.source_type = 'manual';
                        draft.source_session_id = 0;
                        plan.sessions.splice(insertIndex, 0, draft);
                        if (promptEl) promptEl.value = '';
                        render(overlay);
                    } catch (err) {
                        showToast(err.message || '新增课次失败', 'error');
                    } finally {
                        actionBtn.disabled = false;
                    }
                    return;
                }
            });

            overlay.addEventListener('input', (e) => {
                if (e.target.matches('[data-field]')) updateSessionField(e.target);
            });
            overlay.addEventListener('change', (e) => {
                if (e.target.matches('[data-field]')) updateSessionField(e.target);
            });
            overlay.addEventListener('dragstart', (e) => {
                const card = e.target.closest('[data-session-key]');
                if (!card) return;
                planner.draggingKey = card.dataset.sessionKey;
                card.classList.add('is-dragging');
                e.dataTransfer.effectAllowed = 'move';
            });
            overlay.addEventListener('dragover', (e) => {
                if (planner.draggingKey && e.target.closest('[data-session-key]')) e.preventDefault();
            });
            overlay.addEventListener('drop', (e) => {
                const target = e.target.closest('[data-session-key]');
                const plan = currentPlan();
                if (!target || !plan || !planner.draggingKey || target.dataset.sessionKey === planner.draggingKey) return;
                e.preventDefault();
                const from = plan.sessions.findIndex((item) => item.client_id === planner.draggingKey);
                const to = plan.sessions.findIndex((item) => item.client_id === target.dataset.sessionKey);
                if (from >= 0 && to >= 0) {
                    const [item] = plan.sessions.splice(from, 1);
                    plan.sessions.splice(to, 0, item);
                    render(overlay);
                }
            });
            overlay.addEventListener('dragend', () => {
                planner.draggingKey = '';
                overlay.querySelectorAll('.is-dragging').forEach((item) => item.classList.remove('is-dragging'));
            });
            overlay.querySelector('[data-gen-submit]').addEventListener('click', async () => {
                const plan = currentPlan();
                if (!plan || !plan.sessions.length) {
                    showToast('请至少保留 1 次课。', 'error');
                    return;
                }
                const title = (overlay.querySelector('[data-gen-title]')?.value || '').trim();
                const submit = overlay.querySelector('[data-gen-submit]');
                submit.disabled = true;
                try {
                    await apiFetch('/api/lesson-plans/generate', {
                        method: 'POST',
                        body: {
                            class_offering_id: Number(planner.selectedId),
                            title,
                            sessions: payloadSessions(plan),
                        },
                    });
                    close();
                    showToast('已开始分课次生成，列表中会显示进度。', 'success');
                    loadPlans();
                } catch (err) {
                    showToast(err.message || '启动生成失败', 'error');
                    submit.disabled = false;
                }
            });
        },
    });
}

function openGenerateModal() {
    const offerings = state.offerings;
    if (!offerings.length) {
        showToast('你还没有可用课堂，请先在「开设课堂」创建并安排课次。', 'error');
        return;
    }
    const options = offerings
        .map((o) => `<option value="${o.id}">${escapeHtml(o.course_name)} · ${escapeHtml(o.class_name)}（${o.session_count || 0} 次课）</option>`)
        .join('');
    const body = `
        <form data-lp-form-generate class="lp-form">
            <label>选择课堂
                <select name="class_offering_id" required>${options}</select>
            </label>
            <p class="lp-form__hint">系统将读取该课堂的课次安排与绑定教学文档，<strong>逐课次用思考型 AI 生成</strong>完整教案（导入/讲授 PBL 表格/小结/作业）。缺少文档的课次会先由 AI 依据前后课补全。整学期生成可能需要几分钟，可关闭此窗口，列表会以占位卡显示进度。</p>
        </form>`;
    const footer = `<button type="button" class="lp-btn lp-btn--primary" data-lp-submit>开始生成</button>`;
    openModal('按课堂生成整学期教案', body, {
        footerHtml: footer,
        onMount: (overlay, close) => {
            overlay.querySelector('[data-lp-submit]').addEventListener('click', async () => {
                const form = overlay.querySelector('[data-lp-form-generate]');
                const offeringId = new FormData(form).get('class_offering_id');
                if (!offeringId) { showToast('请选择课堂', 'error'); return; }
                try {
                    await apiFetch('/api/lesson-plans/generate', { method: 'POST', body: { class_offering_id: Number(offeringId) } });
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
            <div class="lp-dropzone" data-lp-dropzone>
                <p>拖拽文件到此处，或<button type="button" class="lp-link" data-lp-pick>点击选择文件</button></p>
                <small>支持 doc / docx / pdf / png / jpg 等，可多选；单文件 ≤ 30MB，最多 8 个。</small>
                <input type="file" data-lp-file multiple hidden
                       accept=".doc,.docx,.pdf,.png,.jpg,.jpeg,.webp,.bmp,.gif,.md,.txt">
            </div>
            <ul class="lp-filelist" data-lp-filelist></ul>
            <label class="lp-form__full">给 AI 的额外提示（可选）
                <textarea data-lp-extra rows="3" placeholder="如：这是 Linux 课程教案，请重点保留每节课的 PBL 表格与作业分层。"></textarea>
            </label>
            <p class="lp-form__hint">点击导入后将调用<strong>思考 + 多模态 AI</strong>解析，可能需要几分钟。窗口会关闭并在列表中以占位卡显示「解析中」，完成后自动出现。</p>
        </div>`;
    const footer = `<button type="button" class="lp-btn lp-btn--primary" data-lp-submit>开始导入解析</button>`;
    openModal('导入教案文件', body, {
        footerHtml: footer,
        onMount: (overlay, close) => {
            const picked = [];
            const input = overlay.querySelector('[data-lp-file]');
            const listEl = overlay.querySelector('[data-lp-filelist]');
            const dz = overlay.querySelector('[data-lp-dropzone]');
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
            overlay.querySelector('[data-lp-pick]').addEventListener('click', () => input.click());
            input.addEventListener('change', () => { addFiles(input.files); input.value = ''; });
            listEl.addEventListener('click', (e) => {
                const rm = e.target.closest('[data-rm]');
                if (rm) { picked.splice(Number(rm.dataset.rm), 1); renderFiles(); }
            });
            ['dragover', 'dragenter'].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add('is-over'); }));
            ['dragleave', 'drop'].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove('is-over'); }));
            dz.addEventListener('drop', (e) => { if (e.dataTransfer?.files) addFiles(e.dataTransfer.files); });
            overlay.querySelector('[data-lp-submit]').addEventListener('click', async () => {
                if (!picked.length) { showToast('请先选择文件', 'error'); return; }
                const fd = new FormData();
                picked.forEach((f) => fd.append('files', f));
                fd.append('extra_prompt', overlay.querySelector('[data-lp-extra]').value || '');
                try {
                    await apiFetch('/api/lesson-plans/import', { method: 'POST', body: fd });
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
    try { data = await apiFetch(`/api/lesson-plans/${id}/attributes`); }
    catch (err) { showToast(err.message || '读取属性失败', 'error'); return; }
    const scopeOptions = (data.scope_options || state.scopeOptions)
        .map((o) => `<option value="${o.value}"${o.value === data.scope_level ? ' selected' : ''}>${escapeHtml(o.label)}</option>`)
        .join('');
    const body = `
        <form data-lp-form-attr class="lp-form">
            <label>教案标题<input name="title" value="${escapeHtml(data.title || '')}"></label>
            <label>公开范围
                <select name="scope_level">${scopeOptions}</select>
            </label>
            <p class="lp-form__hint">教案默认私有；可设为本系部 / 本院级 / 全校公开，公开后其他老师可在自己的教案库看到并一键继承。</p>
        </form>`;
    const footer = `<button type="button" class="lp-btn lp-btn--primary" data-lp-submit>保存</button>`;
    openModal('教案属性', body, {
        footerHtml: footer,
        onMount: (overlay, close) => {
            overlay.querySelector('[data-lp-submit]').addEventListener('click', async () => {
                const fd = new FormData(overlay.querySelector('[data-lp-form-attr]'));
                try {
                    await apiFetch(`/api/lesson-plans/${id}/attributes`, {
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
        <form data-lp-form-tags class="lp-form">
            <label>标签（用、或逗号分隔）
                <input name="tags" value="${escapeHtml(current)}" placeholder="如：Linux、专业核心、2025秋">
            </label>
        </form>`;
    const footer = `<button type="button" class="lp-btn lp-btn--primary" data-lp-submit>保存标签</button>`;
    openModal('设置标签', body, {
        footerHtml: footer,
        onMount: (overlay, close) => {
            overlay.querySelector('[data-lp-submit]').addEventListener('click', async () => {
                const raw = new FormData(overlay.querySelector('[data-lp-form-tags]')).get('tags') || '';
                const tags = raw.split(/[、,，\s]+/).map((t) => t.trim()).filter(Boolean);
                try {
                    await apiFetch(`/api/lesson-plans/${id}/tags`, { method: 'PUT', body: { tags } });
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
    const title = plan ? plan.title : '教案预览';
    const body = `
        <div class="lp-preview">
            <div class="lp-preview__bar">
                <span>导出：</span>
                <a class="lp-btn" href="/api/lesson-plans/${id}/export?fmt=docx">Word (.docx)</a>
                <a class="lp-btn" href="/api/lesson-plans/${id}/export?fmt=pdf" target="_blank" rel="noopener">PDF</a>
                <a class="lp-btn" href="/api/lesson-plans/${id}/export?fmt=png" target="_blank" rel="noopener">PNG</a>
                <a class="lp-btn lp-btn--ghost" href="/lesson-plan/${id}/preview" target="_blank" rel="noopener">在新标签页打开</a>
            </div>
            <iframe class="lp-preview__frame" src="/lesson-plan/${id}/preview" title="教案预览"></iframe>
        </div>`;
    openModal(`${title} · 渲染预览`, body, { wide: true });
}

async function inheritPlan(id) {
    if (!confirm('将这份公开教案继承为你自己的私有教案？继承后封面会替换为你的信息，内容可自行调整。')) return;
    try {
        const res = await apiFetch(`/api/lesson-plans/${id}/inherit`, { method: 'POST' });
        showToast('已继承到你的教案库', 'success');
        window.location.href = `/lesson-plan/${res.id}/edit`;
    } catch (err) { showToast(err.message || '继承失败', 'error'); }
}

async function retryPlan(id) {
    try {
        await apiFetch(`/api/lesson-plans/${id}/retry`, { method: 'POST' });
        showToast('已重新开始', 'success');
        loadPlans();
    } catch (err) { showToast(err.message || '重试失败', 'error'); }
}

async function deletePlan(id) {
    if (!confirm('确定删除该教案？此操作不可恢复。')) return;
    try {
        await apiFetch(`/api/lesson-plans/${id}`, { method: 'DELETE' });
        showToast('已删除', 'success');
        loadPlans();
    } catch (err) { showToast(err.message || '删除失败', 'error'); }
}

// ---------------------------------------------------------------------------
// Events
// ---------------------------------------------------------------------------
function bindEvents() {
    document.addEventListener('click', (e) => {
        if (e.target.closest('[data-lp-create-blank]')) {
            e.preventDefault();
            openCreateBlankModal();
            return;
        }
        if (e.target.closest('[data-lp-generate-open]')) {
            e.preventDefault();
            openGeneratePlannerModal();
            return;
        }
        if (e.target.closest('[data-lp-import-open]')) {
            e.preventDefault();
            openImportModal();
        }
    });

    const search = root.querySelector('[data-lp-search]');
    search.addEventListener('input', () => { state.search = search.value; render(); });
    root.querySelector('[data-lp-filter-scope]').addEventListener('change', (e) => { state.scopeFilter = e.target.value; render(); });
    root.querySelector('[data-lp-sort]').addEventListener('change', (e) => { state.sort = e.target.value; render(); });

    root.querySelector('[data-lp-grid]').addEventListener('click', (e) => {
        const btn = e.target.closest('[data-action]');
        if (!btn) return;
        const id = btn.dataset.id;
        switch (btn.dataset.action) {
            case 'edit': window.location.href = `/lesson-plan/${id}/edit`; break;
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
        const bootEl = document.getElementById('lesson-plan-boot');
        const data = bootEl ? JSON.parse(bootEl.textContent) : {};
        state.offerings = data.offerings || [];
        state.scopeOptions = data.scope_options || [];
    } catch (_) { /* ignore boot parse errors */ }
    bindEvents();
    loadPlans();
}

boot();
