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
        <p class="academic-sync-dialog__integrity-result">课程、班级、课堂和教材使用原有稳定 ID；未选择的冲突字段已保留本地版本。</p>
        ${rosterHighlights(result?.rosters)}
        ${resultList('需要教师补充或复核', result?.warnings, 'is-warning')}
        ${unresolved ? `<p class="academic-sync-dialog__unresolved">有 ${unresolved} 门课程包含可靠来源未提供的空白字段，已保留为空，未让 AI 猜测事实。</p>` : ''}
        ${resultList('接下来', result?.remaining_setup || result?.follow_up_items)}
    `;
}

const statusLabels = {
    conflict: '需要确认',
    update: '可合并更新',
    new: '将新建',
    unchanged: '无变化',
};

const actionLabels = {
    merge: '合并到本地对象',
    create: '作为新对象创建',
    skip: '本次跳过',
};

function diffSummary(preview) {
    const summary = preview?.summary || {};
    return `
        ${resultCard('课程', count(summary, 'course_count'), '按真实课程号与稳定来源键匹配')}
        ${resultCard('班级', count(summary, 'class_count'), '按行政班编号、名单重合度匹配')}
        ${resultCard('已有课堂', count(summary, 'offering_count'), '教材与课堂 ID 始终保留')}
        ${resultCard('需确认', count(summary, 'conflict_count'), `${count(summary, 'student_count')} 条学生关系待同步`)}
    `;
}

function renderField(item, field) {
    const checked = field.default_remote ? 'checked' : '';
    const disabled = item.recommended_action === 'skip' ? 'disabled' : '';
    return `
        <label class="academic-sync-diff-field" data-academic-sync-field>
            <span class="academic-sync-diff-field__label">${escapeHtml(field.label)}</span>
            <span class="academic-sync-diff-field__value is-local"><small>本地</small><b>${escapeHtml(field.local)}</b></span>
            <span class="academic-sync-diff-field__arrow" aria-hidden="true">→</span>
            <span class="academic-sync-diff-field__value is-remote"><small>教务</small><b>${escapeHtml(field.remote)}</b></span>
            <span class="academic-sync-diff-field__choice">
                <input type="checkbox" data-academic-sync-remote-field="${escapeHtml(field.name)}" ${checked} ${disabled}>
                <span>采用教务值</span>
            </span>
        </label>
    `;
}

function renderDiffItem(item) {
    const allowedActions = Array.isArray(item.allowed_actions) ? item.allowed_actions : ['skip'];
    const actionOptions = allowedActions.map((action) => `
        <option value="${escapeHtml(action)}" ${action === item.recommended_action ? 'selected' : ''}>${escapeHtml(actionLabels[action] || action)}</option>
    `).join('');
    const impacts = Array.isArray(item.impacts) ? item.impacts : [];
    const impactRows = impacts.slice(0, 4).map((impact) => `
        <li>
            <b>${escapeHtml(impact.course_name || item.title)} · ${escapeHtml(impact.class_name || '')}</b>
            <span>课堂 #${escapeHtml(impact.offering_id || '—')} · 教材：${escapeHtml(impact.textbook_title || '未选择')} · ${escapeHtml(impact.session_count || 0)} 次课</span>
        </li>
    `).join('');
    return `
        <article class="academic-sync-diff-item is-${escapeHtml(item.status || 'update')}" data-academic-sync-diff-item data-key="${escapeHtml(item.key)}">
            <header>
                <div>
                    <span class="academic-sync-diff-item__type">${escapeHtml(item.entity_label || item.entity_type)}</span>
                    <strong>${escapeHtml(item.title)}</strong>
                    <small>${escapeHtml(item.subtitle || '')}</small>
                </div>
                <span class="academic-sync-diff-item__status">${escapeHtml(statusLabels[item.status] || item.status)}</span>
            </header>
            <div class="academic-sync-diff-item__toolbar">
                <label><span>本次处理</span><select data-academic-sync-action>${actionOptions}</select></label>
                ${item.local_id ? `<span>本地 ID ${escapeHtml(item.local_id)} 将被保留</span>` : '<span>尚无本地对象</span>'}
            </div>
            <div class="academic-sync-diff-item__fields">
                ${(item.fields || []).map((field) => renderField(item, field)).join('') || '<p class="academic-sync-diff-item__empty">身份信息一致，无字段需要覆盖。</p>'}
            </div>
            <div class="academic-sync-diff-item__impact ${item.requires_confirmation ? 'is-warning' : ''}">
                <strong>${item.requires_confirmation ? '关联影响，需要确认' : '关系检查'}</strong>
                <p>${escapeHtml(item.impact_message || '不会改变既有课堂关系。')}</p>
                ${impactRows ? `<ul>${impactRows}</ul>` : ''}
            </div>
        </article>
    `;
}

function renderDiff(preview) {
    const items = Array.isArray(preview?.items) ? preview.items : [];
    const visibleItems = items.filter((item) => item.status !== 'unchanged');
    return visibleItems.length
        ? visibleItems.map(renderDiffItem).join('')
        : '<div class="academic-sync-dialog__no-diff"><strong>本地数据与教务数据一致</strong><p>仍可确认同步学生名单和最新排课时间戳。</p></div>';
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
    const diffList = root.querySelector('[data-academic-sync-diff-list]');
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
    let activeTrigger = null;
    let running = false;
    let succeeded = false;
    let currentPlan = null;

    const semesterMap = new Map((Array.isArray(semesters) ? semesters : []).map((item) => [String(item.id), item]));
    select.innerHTML = [...semesterMap.values()].map((item) => `
        <option value="${escapeHtml(item.id)}">${escapeHtml(item.name)}${item.is_current ? '（当前）' : ''}</option>
    `).join('');
    if (defaultSemesterId && semesterMap.has(String(defaultSemesterId))) select.value = String(defaultSemesterId);

    const selectedSemester = () => semesterMap.get(String(select.value || '')) || null;

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
        stay.hidden = view !== 'result';
        reload.hidden = view !== 'result';
        close.disabled = view === 'progress';
    }

    function openDialog(trigger) {
        activeTrigger = trigger || null;
        succeeded = false;
        currentPlan = null;
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

    function closeDialog({ refresh = false } = {}) {
        if (running) return;
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
        progressCopy.textContent = '随后会追踪到已有课堂、教材和课次，生成可逐字段选择的差异。';
        try {
            currentPlan = await apiFetch(previewEndpoint, {
                method: 'POST', body: { semester_id: Number(semester.id) }, silent: true,
            });
            diffSummaryNode.innerHTML = diffSummary(currentPlan);
            diffList.innerHTML = renderDiff(currentPlan);
            title.textContent = '确认同步差异';
            lead.textContent = currentPlan.requires_confirmation
                ? '检测到会影响既有课堂的属性或排课变化。请像代码版本对比一样，逐项选择要采用的教务字段。'
                : '已完成全链路比对。系统给出了安全默认值，你仍可逐项调整。';
            setView('diff');
            apply.focus({ preventScroll: true });
        } catch (error) {
            showFailure(error);
        } finally {
            running = false;
            close.disabled = false;
            setButtonsBusy(false);
        }
    }

    function collectResolution() {
        return Array.from(diffList.querySelectorAll('[data-academic-sync-diff-item]')).map((node) => ({
            key: node.dataset.key,
            action: node.querySelector('[data-academic-sync-action]')?.value || 'skip',
            remote_fields: Array.from(node.querySelectorAll('[data-academic-sync-remote-field]:checked')).map((input) => input.dataset.academicSyncRemoteField),
        }));
    }

    async function applyDiff() {
        if (!currentPlan?.plan_id || running) return;
        running = true;
        setButtonsBusy(true);
        setView('progress');
        title.textContent = '正在应用已确认的同步方案';
        lead.textContent = '使用同一份教务快照原地合并，保持已有课堂的课程、班级与教材关系。';
        progressTitle.textContent = '写入课程、班级、名单与排课';
        progressCopy.textContent = '未勾选的字段保留本地版本；关联学习记录的停排课次不会物理删除。';
        try {
            const result = await apiFetch(applyEndpoint, {
                method: 'POST',
                body: { plan_id: Number(currentPlan.plan_id), items: collectResolution() },
                silent: true,
            });
            succeeded = true;
            title.textContent = '教务同步完成';
            lead.textContent = '同步已按确认方案落库，并完成课堂关系完整性保护。';
            resultSummary.innerHTML = renderResult(result);
            setView('result');
            showMessage(result.message || '教务数据同步完成', 'success', 5200);
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

    diffList.addEventListener('change', (event) => {
        const action = event.target.closest?.('[data-academic-sync-action]');
        if (!action) return;
        const item = action.closest('[data-academic-sync-diff-item]');
        item.querySelectorAll('[data-academic-sync-remote-field]').forEach((input) => {
            input.disabled = action.value === 'skip';
        });
        item.classList.toggle('is-skipped', action.value === 'skip');
    });
    triggers.forEach((button) => button.addEventListener('click', () => openDialog(button)));
    select.addEventListener('change', updatePreview);
    confirm.addEventListener('click', loadDiff);
    apply.addEventListener('click', applyDiff);
    back.addEventListener('click', () => { currentPlan = null; setView('select'); updatePreview(); });
    cancel.addEventListener('click', () => closeDialog());
    close.addEventListener('click', () => closeDialog());
    stay.addEventListener('click', () => closeDialog());
    reload.addEventListener('click', () => succeeded ? closeDialog({ refresh: true }) : setView('select'));
    root.addEventListener('click', (event) => { if (event.target === root) closeDialog(); });
    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && !root.hidden && !running) closeDialog();
    });
    updatePreview();
    return { open: openDialog, close: closeDialog };
}
