import { apiFetch } from '/static/js/api.js';
import { showMessage } from '/static/js/ui.js';

function escapeHtml(value) {
    return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#039;');
}

function count(result, key) {
    return Number(result?.[key] || 0) || 0;
}

function resultCard(label, value, detail) {
    return `<article><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong><small>${escapeHtml(detail)}</small></article>`;
}

function resultList(title, items, className = '') {
    const safeItems = Array.isArray(items) ? items.filter(Boolean).slice(0, 8) : [];
    if (!safeItems.length) return '';
    return `
        <section class="academic-sync-dialog__result-list ${className}">
            <h4>${escapeHtml(title)}</h4>
            <ul>${safeItems.map((item) => `<li>${escapeHtml(item)}</li>`).join('')}</ul>
        </section>
    `;
}

function rosterHighlights(rosters) {
    const items = Array.isArray(rosters) ? rosters.slice(0, 5) : [];
    if (!items.length) return '';
    return `
        <section class="academic-sync-dialog__highlights">
            <h4>已识别的真实班级</h4>
            <div>${items.map((item) => `
                <article>
                    <strong>${escapeHtml(item.class_composition || '班级名称待确认')}</strong>
                    <span>${escapeHtml(item.course_name || '未命名课程')}</span>
                    <small>教务代号：${escapeHtml(item.teaching_class_name || '—')} · ${count(item, 'imported_student_count')} 人</small>
                </article>
            `).join('')}</div>
        </section>
    `;
}

function renderResult(result) {
    const unresolved = Array.isArray(result?.unresolved_course_fields) ? result.unresolved_course_fields.length : 0;
    const aiAccepted = count(result?.ai_enrichment, 'accepted_count');
    return `
        <div class="academic-sync-dialog__success">
            <span aria-hidden="true">✓</span>
            <div><strong>${escapeHtml(result?.semester_name || '所选学期')}同步完成</strong><p>${escapeHtml(result?.message || '课程、班级和学生已完成合并。')}</p></div>
        </div>
        <div class="academic-sync-dialog__result-grid">
            ${resultCard('课程', count(result, 'course_count'), `新增 ${count(result, 'courses_created')} · 合并 ${count(result, 'courses_updated')}`)}
            ${resultCard('班级', count(result, 'touched_class_count'), `新增 ${count(result, 'classes_created')} · 合并/复用 ${count(result, 'classes_updated') + count(result, 'classes_reused')}`)}
            ${resultCard('学生', count(result, 'roster_student_count'), `新增 ${count(result, 'students_created')} · 更新/复用 ${count(result, 'students_updated') + count(result, 'students_reused')}`)}
            ${resultCard('真实课次', count(result, 'occurrence_count'), `课堂排课更新 ${count(result, 'offering_update_count')} · AI 补充 ${aiAccepted}`)}
        </div>
        ${rosterHighlights(result?.rosters)}
        ${resultList('需要教师补充或复核', result?.warnings, 'is-warning')}
        ${unresolved ? `<p class="academic-sync-dialog__unresolved">有 ${unresolved} 门课程包含可靠来源未提供的空白字段，已保留为空，未让 AI 猜测事实。</p>` : ''}
        <div data-offering-bootstrap-slot hidden></div>
        ${resultList('接下来', result?.remaining_setup || result?.follow_up_items)}
    `;
}

const statusLabels = {
    conflict: '需要确认',
    update: '可自动合并',
    new: '将新建',
    unchanged: '无需变更',
};

const actionLabels = {
    merge: '合并到本地对象',
    create: '作为新对象创建',
    skip: '本次跳过',
};

function buildCourseGroups(preview) {
    const items = Array.isArray(preview?.items) ? preview.items : [];
    const groupsByKey = new Map();
    items.filter((item) => item.entity_type === 'course').forEach((course) => {
        const key = String(course.course_group_key || course.source_group_key || course.key);
        groupsByKey.set(key, { key, course, items: [course] });
    });

    items.filter((item) => item.entity_type !== 'course').forEach((item) => {
        let keys = Array.isArray(item.course_group_keys) ? item.course_group_keys : [];
        if (!keys.length && item.course_group_key) keys = [item.course_group_key];
        if (!keys.length) {
            const impactCourseNames = new Set((item.impacts || []).map((impact) => String(impact.course_name || '')));
            keys = [...groupsByKey.values()]
                .filter((group) => impactCourseNames.has(String(group.course.title || '')))
                .map((group) => group.key);
        }
        keys.forEach((rawKey) => {
            const group = groupsByKey.get(String(rawKey));
            if (group && !group.items.some((candidate) => candidate.key === item.key)) group.items.push(item);
        });
    });

    return [...groupsByKey.values()];
}

function initialiseResolutionStates(preview) {
    const states = new Map();
    (preview?.items || []).forEach((item) => {
        const choices = {};
        (item.fields || []).forEach((field) => {
            choices[field.name] = item.requires_confirmation
                ? null
                : (field.default_remote ? 'remote' : 'local');
        });
        states.set(String(item.key), {
            action: item.requires_confirmation ? '' : String(item.recommended_action || 'skip'),
            actionConfirmed: !item.requires_confirmation,
            choices,
        });
    });
    return states;
}

function itemPendingCount(item, states) {
    if (!item.requires_confirmation) return 0;
    const state = states.get(String(item.key));
    if (!state?.actionConfirmed || !state.action) return Math.max(1, (item.fields || []).length);
    if (state.action !== 'merge') return 0;
    return (item.fields || []).filter((field) => !['local', 'remote'].includes(state.choices[field.name])).length;
}

function groupReviewStatus(group, states) {
    const reviewItems = group.items.filter((item) => item.requires_confirmation);
    const pendingCount = reviewItems.reduce((sum, item) => sum + itemPendingCount(item, states), 0);
    if (pendingCount) return { key: 'pending', label: '待确认', pendingCount };
    if (reviewItems.length) return { key: 'reviewed', label: '已确认', pendingCount: 0 };
    return { key: 'automatic', label: '自动合并', pendingCount: 0 };
}

function unresolvedChoiceCount(groups, states) {
    const uniqueItems = new Map();
    groups.forEach((group) => group.items.forEach((item) => uniqueItems.set(String(item.key), item)));
    return [...uniqueItems.values()].reduce((sum, item) => sum + itemPendingCount(item, states), 0);
}

function miniCourseSummary(groups, states) {
    const pending = groups.filter((group) => groupReviewStatus(group, states).key === 'pending').length;
    const reviewed = groups.filter((group) => groupReviewStatus(group, states).key === 'reviewed').length;
    const automatic = Math.max(0, groups.length - pending - reviewed);
    return `
        <span class="academic-sync-summary-tag"><small>课程</small><strong>${groups.length}</strong></span>
        <span class="academic-sync-summary-tag is-pending"><small>待确认</small><strong>${pending}</strong></span>
        <span class="academic-sync-summary-tag is-reviewed"><small>已确认</small><strong>${reviewed}</strong></span>
        <span class="academic-sync-summary-tag is-automatic"><small>自动合并</small><strong>${automatic}</strong></span>
    `;
}

function renderCourseNav(groups, states, activeKey) {
    const sorted = [...groups].sort((left, right) => {
        const order = { pending: 0, reviewed: 1, automatic: 2 };
        return order[groupReviewStatus(left, states).key] - order[groupReviewStatus(right, states).key];
    });
    const sections = [
        { key: 'pending', title: '待确认', groups: sorted.filter((group) => groupReviewStatus(group, states).key === 'pending') },
        { key: 'ready', title: '已准备', groups: sorted.filter((group) => groupReviewStatus(group, states).key !== 'pending') },
    ];
    return sections.filter((section) => section.groups.length).map((section) => `
        <section class="academic-sync-course-section is-${section.key}">
            <h5><span>${section.title}</span><b>${section.groups.length}</b></h5>
            <div>${section.groups.map((group) => {
                const status = groupReviewStatus(group, states);
                const differenceCount = group.items.reduce((sum, item) => sum + (item.fields || []).length, 0);
                return `
                    <button type="button" class="academic-sync-course-item is-${status.key} ${group.key === activeKey ? 'is-active' : ''}" data-academic-sync-course-key="${escapeHtml(group.key)}">
                        <span><strong>${escapeHtml(group.course.title || '未命名课程')}</strong><small>${escapeHtml(group.course.subtitle || '课程号待核验')}</small></span>
                        <i>${escapeHtml(status.label)}${status.pendingCount ? ` · ${status.pendingCount}` : ''}</i>
                        <em>${differenceCount} 处差异</em>
                    </button>
                `;
            }).join('')}</div>
        </section>
    `).join('');
}

function renderCourseHeader(group, states) {
    const status = groupReviewStatus(group, states);
    const visibleItems = group.items.filter((item) => item.status !== 'unchanged');
    return `
        <div>
            <span>当前课程</span>
            <strong>${escapeHtml(group.course.title || '未命名课程')}</strong>
            <small>${escapeHtml(group.course.subtitle || '课程号待核验')}</small>
        </div>
        <div>
            <span class="academic-sync-review-detail__count">${visibleItems.length} 组差异</span>
            <span class="academic-sync-review-detail__status is-${status.key}">${escapeHtml(status.label)}</span>
        </div>
    `;
}

function actionOptions(item, state) {
    const allowedActions = Array.isArray(item.allowed_actions) ? item.allowed_actions : ['skip'];
    const placeholder = item.requires_confirmation && !state.action
        ? '<option value="" selected disabled>请选择处理方式</option>'
        : '';
    return placeholder + allowedActions.map((action) => `
        <option value="${escapeHtml(action)}" ${action === state.action ? 'selected' : ''}>${escapeHtml(actionLabels[action] || action)}</option>
    `).join('');
}

function renderField(item, field, state) {
    const choice = state.choices[field.name];
    const choicesDisabled = Boolean(state.action && state.action !== 'merge');
    const fieldKey = `${item.key}:${field.name}`;
    return `
        <div class="academic-sync-diff-field ${choice ? 'is-confirmed' : 'is-unresolved'}" data-academic-sync-field-row data-item-key="${escapeHtml(item.key)}" data-field-name="${escapeHtml(field.name)}" tabindex="0" role="group" aria-label="${escapeHtml(field.label)}差异，点击空白处查看完整对比">
            <span class="academic-sync-diff-field__label"><b>${escapeHtml(field.label)}</b><small>展开完整对比 ↗</small></span>
            <button type="button" class="academic-sync-diff-field__value is-local ${choice === 'local' ? 'is-selected' : ''}" data-academic-sync-choice="local" data-item-key="${escapeHtml(item.key)}" data-field-name="${escapeHtml(field.name)}" aria-pressed="${choice === 'local'}" ${choicesDisabled ? 'disabled' : ''}>
                <small>本地</small><b>${escapeHtml(field.local)}</b><span>${choice === 'local' ? '已采用' : '采用本地'}</span>
            </button>
            <span class="academic-sync-diff-field__versus" aria-hidden="true">VS</span>
            <button type="button" class="academic-sync-diff-field__value is-remote ${choice === 'remote' ? 'is-selected' : ''}" data-academic-sync-choice="remote" data-item-key="${escapeHtml(item.key)}" data-field-name="${escapeHtml(field.name)}" aria-pressed="${choice === 'remote'}" ${choicesDisabled ? 'disabled' : ''}>
                <small>教务</small><b>${escapeHtml(field.remote)}</b><span>${choice === 'remote' ? '已采用' : '采用教务'}</span>
            </button>
            <span class="academic-sync-diff-field__resolution ${choice ? 'is-done' : ''}" data-field-key="${escapeHtml(fieldKey)}">${choice ? '已确认' : '请选择一侧'}</span>
        </div>
    `;
}

function renderDiffItem(item, states) {
    const state = states.get(String(item.key));
    const impacts = Array.isArray(item.impacts) ? item.impacts : [];
    const impactLabel = impacts.length ? `影响 ${impacts.length} 个课堂` : '无既有课堂';
    const impactHelp = escapeHtml(item.impact_message || '不会改变既有课堂关系。');
    return `
        <article class="academic-sync-diff-item is-${escapeHtml(item.status || 'update')} ${state.action === 'skip' ? 'is-skipped' : ''}" data-academic-sync-diff-item data-key="${escapeHtml(item.key)}">
            <header>
                <div>
                    <span class="academic-sync-diff-item__type">${escapeHtml(item.entity_label || item.entity_type)}</span>
                    <strong>${escapeHtml(item.title)}</strong>
                    <small>${escapeHtml(item.subtitle || '')}</small>
                </div>
                <div class="academic-sync-diff-item__badges">
                    <span class="academic-sync-diff-item__impact-tag ${item.requires_confirmation ? 'is-warning' : ''}" tabindex="0" data-explain data-explain-title="关系影响" data-explain-text="${impactHelp}" data-explain-placement="left">${impactLabel}</span>
                    <span class="academic-sync-diff-item__status">${escapeHtml(statusLabels[item.status] || item.status)}</span>
                </div>
            </header>
            <div class="academic-sync-diff-item__toolbar">
                <label><span>本次处理</span><select data-academic-sync-action>${actionOptions(item, state)}</select></label>
                ${item.local_id ? `<span>保留本地 ID ${escapeHtml(item.local_id)}</span>` : '<span>尚无本地对象</span>'}
            </div>
            <div class="academic-sync-diff-item__fields">
                ${(item.fields || []).map((field) => renderField(item, field, state)).join('') || '<p class="academic-sync-diff-item__empty">身份信息一致，无字段需要覆盖。</p>'}
            </div>
        </article>
    `;
}

function renderCourseDiff(group, states) {
    const visibleItems = group.items.filter((item) => item.status !== 'unchanged');
    return visibleItems.length
        ? visibleItems.map((item) => renderDiffItem(item, states)).join('')
        : '<div class="academic-sync-dialog__no-diff"><strong>本课程无需变更</strong><p>学生名单和同步时间仍会按安全默认规则更新。</p></div>';
}

function renderDiffLines(value, marker) {
    const normalized = String(value ?? '').replaceAll('\r\n', '\n').replaceAll('\r', '\n');
    const lines = (normalized || '（空）').split('\n');
    return lines.map((line, index) => `
        <div class="academic-sync-detail-line"><span>${index + 1}</span><i>${marker}</i><code>${escapeHtml(line || ' ')}</code></div>
    `).join('');
}

export function initAcademicSyncDialog({
    buttons = [],
    previewEndpoint = '/api/manage/academic-sync/preview',
    applyEndpoint = '/api/manage/academic-sync/apply',
    semesters = [],
    defaultSemesterId = null,
} = {}) {
    const root = document.querySelector('[data-academic-sync-dialog]');
    const triggers = buttons.filter(Boolean);
    if (!root || !triggers.length || !previewEndpoint || !applyEndpoint) return null;
    if (root.parentElement !== document.body) document.body.appendChild(root);

    const panel = root.querySelector('.academic-sync-dialog');
    const title = root.querySelector('[data-academic-sync-title]');
    const lead = root.querySelector('[data-academic-sync-lead]');
    const selectView = root.querySelector('[data-academic-sync-select-view]');
    const progressView = root.querySelector('[data-academic-sync-progress-view]');
    const diffView = root.querySelector('[data-academic-sync-diff-view]');
    const resultView = root.querySelector('[data-academic-sync-result-view]');
    const resultSummary = root.querySelector('[data-academic-sync-result-summary]');
    const diffSummaryNode = root.querySelector('[data-academic-sync-diff-summary]');
    const courseList = root.querySelector('[data-academic-sync-course-list]');
    const courseHeader = root.querySelector('[data-academic-sync-course-header]');
    const diffList = root.querySelector('[data-academic-sync-diff-list]');
    const reviewState = root.querySelector('[data-academic-sync-review-state]');
    const progressTitle = root.querySelector('[data-academic-sync-progress-title]');
    const progressCopy = root.querySelector('[data-academic-sync-progress-copy]');
    const select = root.querySelector('[data-academic-sync-semester]');
    const preview = root.querySelector('[data-academic-sync-term-preview]');
    const empty = root.querySelector('[data-academic-sync-empty]');
    const confirm = root.querySelector('[data-academic-sync-confirm]');
    const apply = root.querySelector('[data-academic-sync-apply]');
    const back = root.querySelector('[data-academic-sync-back]');
    const cancel = root.querySelector('[data-academic-sync-cancel]');
    const stay = root.querySelector('[data-academic-sync-stay]');
    const reload = root.querySelector('[data-academic-sync-reload]');
    const close = root.querySelector('[data-academic-sync-close]');
    const detailBackdrop = root.querySelector('[data-academic-sync-detail]');
    const detailDialog = root.querySelector('.academic-sync-detail-dialog');
    const detailTitle = root.querySelector('[data-academic-sync-detail-title]');
    const detailSubtitle = root.querySelector('[data-academic-sync-detail-subtitle]');
    const detailLocal = root.querySelector('[data-academic-sync-detail-local]');
    const detailRemote = root.querySelector('[data-academic-sync-detail-remote]');
    const detailClose = root.querySelector('[data-academic-sync-detail-close]');
    const detailCancel = root.querySelector('[data-academic-sync-detail-cancel]');
    const detailLocalChoice = root.querySelector('[data-academic-sync-detail-local-choice]');
    const detailRemoteChoice = root.querySelector('[data-academic-sync-detail-remote-choice]');
    let activeTrigger = null;
    let running = false;
    let succeeded = false;
    let currentPlan = null;
    let courseGroups = [];
    let resolutionStates = new Map();
    let activeCourseKey = '';
    let detailContext = null;

    const semesterMap = new Map((Array.isArray(semesters) ? semesters : []).map((item) => [String(item.id), item]));
    select.innerHTML = [...semesterMap.values()].map((item) => `
        <option value="${escapeHtml(item.id)}">${escapeHtml(item.name)}${item.is_current ? '（当前）' : ''}</option>
    `).join('');
    if (defaultSemesterId && semesterMap.has(String(defaultSemesterId))) select.value = String(defaultSemesterId);

    const selectedSemester = () => semesterMap.get(String(select.value || '')) || null;
    const currentGroup = () => courseGroups.find((group) => group.key === activeCourseKey) || courseGroups[0] || null;
    const currentItem = (key) => (currentPlan?.items || []).find((item) => String(item.key) === String(key)) || null;

    function updatePreview() {
        const semester = selectedSemester();
        preview.hidden = !semester;
        empty.hidden = Boolean(semester);
        select.closest('.academic-sync-dialog__semester-field').hidden = !semesterMap.size;
        confirm.disabled = !semester || running;
        if (!semester) return;
        root.querySelector('[data-academic-sync-semester-name]').textContent = `${semester.academic_year} ${semester.term_label}`;
        root.querySelector('[data-academic-sync-semester-range]').textContent = semester.start_date && semester.end_date
            ? `${semester.start_date} 至 ${semester.end_date}` : '起止日期待确认';
        root.querySelector('[data-academic-sync-xnm]').textContent = `xnm=${semester.xnm}`;
        root.querySelector('[data-academic-sync-xqm]').textContent = `xqm=${semester.xqm}`;
    }

    function setView(view) {
        selectView.hidden = view !== 'select';
        progressView.hidden = view !== 'progress';
        diffView.hidden = view !== 'diff';
        resultView.hidden = view !== 'result';
        confirm.hidden = view !== 'select';
        cancel.hidden = view !== 'select';
        back.hidden = view !== 'diff';
        apply.hidden = view !== 'diff';
        reviewState.hidden = view !== 'diff';
        stay.hidden = view !== 'result';
        reload.hidden = view !== 'result';
        close.disabled = view === 'progress';
        root.classList.toggle('is-reviewing', view === 'diff');
    }

    function renderReview() {
        if (!courseGroups.length) {
            diffSummaryNode.innerHTML = '';
            courseList.innerHTML = '';
            courseHeader.innerHTML = '<strong>没有可显示的课程</strong>';
            diffList.innerHTML = '<div class="academic-sync-dialog__no-diff">本次教务快照没有返回课程。</div>';
            apply.disabled = true;
            reviewState.textContent = '没有可合并的课程';
            return;
        }
        const pendingGroups = courseGroups.filter((group) => groupReviewStatus(group, resolutionStates).key === 'pending');
        if (!activeCourseKey || !courseGroups.some((group) => group.key === activeCourseKey)) {
            activeCourseKey = (pendingGroups[0] || courseGroups[0]).key;
        }
        const group = currentGroup();
        diffSummaryNode.innerHTML = miniCourseSummary(courseGroups, resolutionStates);
        courseList.innerHTML = renderCourseNav(courseGroups, resolutionStates, activeCourseKey);
        courseHeader.innerHTML = renderCourseHeader(group, resolutionStates);
        diffList.innerHTML = renderCourseDiff(group, resolutionStates);
        const unresolved = unresolvedChoiceCount(courseGroups, resolutionStates);
        apply.disabled = running || unresolved > 0;
        apply.textContent = '确认合并';
        apply.title = unresolved ? `还需确认 ${unresolved} 处差异` : '所有待确认差异均已选择';
        reviewState.textContent = unresolved
            ? `还需确认 ${unresolved} 处差异`
            : '全部课程已准备，可以确认合并';
        reviewState.classList.toggle('is-ready', unresolved === 0);
    }

    function openDialog(trigger) {
        activeTrigger = trigger || null;
        succeeded = false;
        currentPlan = null;
        courseGroups = [];
        resolutionStates = new Map();
        activeCourseKey = '';
        title.textContent = '同步教务课程、班级与课堂关系';
        lead.textContent = '先比较教务快照与本地课堂，再逐项选择采用本地或教务字段。';
        setView('select');
        updatePreview();
        root.hidden = false;
        root.setAttribute('aria-hidden', 'false');
        document.body.classList.add('has-academic-sync-dialog');
        requestAnimationFrame(() => {
            root.classList.add('is-open');
            (semesterMap.size ? select : panel)?.focus({ preventScroll: true });
        });
    }

    function closeDetail({ restoreFocus = true } = {}) {
        if (detailBackdrop.hidden) return;
        const trigger = detailContext?.trigger;
        detailBackdrop.hidden = true;
        detailBackdrop.setAttribute('aria-hidden', 'true');
        root.classList.remove('has-detail-diff');
        detailContext = null;
        if (restoreFocus) trigger?.focus?.({ preventScroll: true });
    }

    function closeDialog({ refresh = false } = {}) {
        if (running) return;
        closeDetail({ restoreFocus: false });
        if (refresh) return window.location.reload();
        root.classList.remove('is-open');
        root.setAttribute('aria-hidden', 'true');
        document.body.classList.remove('has-academic-sync-dialog');
        window.setTimeout(() => { if (!root.classList.contains('is-open')) root.hidden = true; }, 170);
        activeTrigger?.focus?.({ preventScroll: true });
    }

    function setButtonsBusy(isBusy) {
        triggers.forEach((button) => {
            if (isBusy) {
                button.dataset.academicSyncLabel = button.innerHTML;
                button.disabled = true;
            } else {
                button.disabled = false;
                if (button.dataset.academicSyncLabel) {
                    button.innerHTML = button.dataset.academicSyncLabel;
                    delete button.dataset.academicSyncLabel;
                }
            }
        });
    }

    async function loadDiff() {
        const semester = selectedSemester();
        if (!semester || running) return;
        running = true;
        setButtonsBusy(true);
        setView('progress');
        title.textContent = `正在比较 ${semester.name}`;
        lead.textContent = `使用 xnm=${semester.xnm}、xqm=${semester.xqm} 读取教务快照，全程暂不写入本地数据。`;
        progressTitle.textContent = '读取课程、教学班和全部学生名单';
        progressCopy.textContent = '随后会追踪到已有课堂、教材和课次，生成课程级差异列表。';
        try {
            currentPlan = await apiFetch(previewEndpoint, {
                method: 'POST', body: { semester_id: Number(semester.id) }, silent: true,
            });
            resolutionStates = initialiseResolutionStates(currentPlan);
            courseGroups = buildCourseGroups(currentPlan);
            const firstPending = courseGroups.find((group) => groupReviewStatus(group, resolutionStates).key === 'pending');
            activeCourseKey = (firstPending || courseGroups[0])?.key || '';
            title.textContent = '确认同步差异';
            lead.textContent = currentPlan.requires_confirmation
                ? '待确认课程已置顶。逐项点击左侧保留本地，或点击右侧采用教务；点击差异空白处可查看完整对比。'
                : '全部课程均可按安全默认值自动合并，你仍可逐门检查或调整。';
            setView('diff');
            renderReview();
            requestAnimationFrame(() => courseList.querySelector('[data-academic-sync-course-key]')?.focus({ preventScroll: true }));
        } catch (error) {
            showFailure(error);
        } finally {
            running = false;
            close.disabled = false;
            setButtonsBusy(false);
            if (!diffView.hidden) renderReview();
        }
    }

    function setFieldChoice(itemKey, fieldName, choice) {
        const item = currentItem(itemKey);
        const state = resolutionStates.get(String(itemKey));
        if (!item || !state || !['local', 'remote'].includes(choice)) return;
        if (!state.action || state.action !== 'merge') {
            if (!(item.allowed_actions || []).includes('merge')) return;
            state.action = 'merge';
            state.actionConfirmed = true;
        }
        state.choices[fieldName] = choice;
        renderReview();
    }

    function openFieldDetail(itemKey, fieldName, trigger) {
        const item = currentItem(itemKey);
        const field = (item?.fields || []).find((candidate) => String(candidate.name) === String(fieldName));
        if (!item || !field) return;
        detailContext = { itemKey: String(itemKey), fieldName: String(fieldName), trigger };
        detailTitle.textContent = field.label || '字段差异';
        detailSubtitle.textContent = `${item.entity_label || item.entity_type} · ${item.title}`;
        detailLocal.innerHTML = renderDiffLines(field.local, '−');
        detailRemote.innerHTML = renderDiffLines(field.remote, '+');
        detailBackdrop.hidden = false;
        detailBackdrop.setAttribute('aria-hidden', 'false');
        root.classList.add('has-detail-diff');
        requestAnimationFrame(() => detailDialog.focus({ preventScroll: true }));
    }

    function chooseFromDetail(choice) {
        if (!detailContext) return;
        const context = { ...detailContext };
        setFieldChoice(context.itemKey, context.fieldName, choice);
        closeDetail({ restoreFocus: false });
        requestAnimationFrame(() => {
            Array.from(diffList.querySelectorAll('[data-academic-sync-field-row]'))
                .find((node) => node.dataset.itemKey === context.itemKey && node.dataset.fieldName === context.fieldName)
                ?.focus?.({ preventScroll: true });
        });
    }

    function collectResolution() {
        return (currentPlan?.items || []).map((item) => {
            const state = resolutionStates.get(String(item.key));
            const fieldChoices = { ...(state?.choices || {}) };
            return {
                key: item.key,
                action: state?.action || item.recommended_action || 'skip',
                field_choices: fieldChoices,
                remote_fields: Object.entries(fieldChoices)
                    .filter(([, choice]) => choice === 'remote')
                    .map(([name]) => name),
            };
        });
    }

    async function applyDiff() {
        if (!currentPlan?.plan_id || running || unresolvedChoiceCount(courseGroups, resolutionStates) > 0) return;
        running = true;
        setButtonsBusy(true);
        setView('progress');
        title.textContent = '正在应用已确认的同步方案';
        lead.textContent = '使用同一份教务快照原地合并，保持已有课堂的课程、班级与教材关系。';
        progressTitle.textContent = '写入课程、班级、名单与排课';
        progressCopy.textContent = '明确选择本地的字段不会被覆盖；停排课次只标记取消，不会破坏历史关联。';
        try {
            const result = await apiFetch(applyEndpoint, {
                method: 'POST',
                body: { plan_id: Number(currentPlan.plan_id), items: collectResolution() },
                silent: true,
            });
            succeeded = true;
            title.textContent = '教务同步完成';
            lead.textContent = '同步已按确认方案落库，课程、班级与课堂关联保持完整。';
            resultSummary.innerHTML = renderResult(result);
            setView('result');
            showMessage(result.message || '教务数据同步完成', 'success', 5200);
            // 同步完成即检测可一键开设的课堂（候选为空时槽位保持隐藏）
            try {
                const slot = resultSummary.querySelector('[data-offering-bootstrap-slot]');
                if (slot && result?.semester_id) {
                    const { mountOfferingBootstrap } = await import('/static/js/offering_bootstrap.js?v=20260831-obs');
                    await mountOfferingBootstrap(slot, { semesterId: result.semester_id, variant: 'sync-dialog' });
                }
            } catch (bootstrapError) {
                // 一键开课检测失败不影响同步结果展示
            }
        } catch (error) {
            showFailure(error);
        } finally {
            running = false;
            close.disabled = false;
            setButtonsBusy(false);
        }
    }

    function showFailure(error) {
        succeeded = false;
        title.textContent = '教务同步未完成';
        lead.textContent = error.message || '请检查教务账号和所选学期后重试。';
        resultSummary.innerHTML = resultList('未能完成同步', [error.message || '未知错误'], 'is-warning');
        stay.textContent = '关闭';
        reload.textContent = '返回重试';
        setView('result');
        showMessage(error.message || '教务同步失败', 'error', 5200);
    }

    courseList.addEventListener('click', (event) => {
        const button = event.target.closest?.('[data-academic-sync-course-key]');
        if (!button) return;
        activeCourseKey = String(button.dataset.academicSyncCourseKey || '');
        renderReview();
        requestAnimationFrame(() => courseHeader.querySelector('strong')?.focus?.({ preventScroll: true }));
    });

    diffList.addEventListener('change', (event) => {
        const action = event.target.closest?.('[data-academic-sync-action]');
        if (!action) return;
        const itemNode = action.closest('[data-academic-sync-diff-item]');
        const state = resolutionStates.get(String(itemNode?.dataset.key || ''));
        if (!state) return;
        state.action = String(action.value || '');
        state.actionConfirmed = Boolean(action.value);
        renderReview();
    });

    diffList.addEventListener('click', (event) => {
        const choice = event.target.closest?.('[data-academic-sync-choice]');
        if (choice) {
            setFieldChoice(choice.dataset.itemKey, choice.dataset.fieldName, choice.dataset.academicSyncChoice);
            return;
        }
        const row = event.target.closest?.('[data-academic-sync-field-row]');
        if (row && !event.target.closest('button, select, input, a')) {
            openFieldDetail(row.dataset.itemKey, row.dataset.fieldName, row);
        }
    });

    diffList.addEventListener('keydown', (event) => {
        const row = event.target.closest?.('[data-academic-sync-field-row]');
        if (row && event.target === row && ['Enter', ' '].includes(event.key)) {
            event.preventDefault();
            openFieldDetail(row.dataset.itemKey, row.dataset.fieldName, row);
        }
    });

    triggers.forEach((button) => button.addEventListener('click', () => openDialog(button)));
    select.addEventListener('change', updatePreview);
    confirm.addEventListener('click', loadDiff);
    apply.addEventListener('click', applyDiff);
    back.addEventListener('click', () => {
        currentPlan = null;
        courseGroups = [];
        resolutionStates = new Map();
        activeCourseKey = '';
        setView('select');
        updatePreview();
    });
    cancel.addEventListener('click', () => closeDialog());
    close.addEventListener('click', () => closeDialog());
    stay.addEventListener('click', () => closeDialog());
    reload.addEventListener('click', () => succeeded ? closeDialog({ refresh: true }) : setView('select'));
    detailClose.addEventListener('click', () => closeDetail());
    detailCancel.addEventListener('click', () => closeDetail());
    detailLocalChoice.addEventListener('click', () => chooseFromDetail('local'));
    detailRemoteChoice.addEventListener('click', () => chooseFromDetail('remote'));
    detailBackdrop.addEventListener('click', (event) => { if (event.target === detailBackdrop) closeDetail(); });
    root.addEventListener('click', (event) => { if (event.target === root) closeDialog(); });
    document.addEventListener('keydown', (event) => {
        if (event.key !== 'Escape' || root.hidden || running) return;
        if (!detailBackdrop.hidden) closeDetail();
        else closeDialog();
    });
    updatePreview();
    return { open: openDialog, close: closeDialog };
}
