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
    const safeItems = Array.isArray(items) ? items.filter(Boolean).slice(0, 6) : [];
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
            ${resultCard('真实课次', count(result, 'occurrence_count'), `教学班 ${count(result, 'teaching_class_count')} · AI 补充 ${aiAccepted}`)}
        </div>
        ${rosterHighlights(result?.rosters)}
        ${resultList('需要教师补全或复核', result?.warnings, 'is-warning')}
        ${unresolved ? `<p class="academic-sync-dialog__unresolved">有 ${unresolved} 门课程包含可靠来源未提供的空白字段，已保留为空，未让 AI 猜测事实。</p>` : ''}
        ${resultList('接下来', result?.remaining_setup || result?.follow_up_items)}
    `;
}

export function initAcademicSyncDialog({
    buttons = [],
    endpoint,
    semesters = [],
    defaultSemesterId = null,
} = {}) {
    const root = document.querySelector('[data-academic-sync-dialog]');
    const triggers = buttons.filter(Boolean);
    if (!root || !triggers.length || !endpoint) return null;

    // The manage shell uses transformed layout containers; keep the fixed
    // backdrop at document level so it is centered on the viewport instead of
    // being constrained and vertically offset by that ancestor.
    if (root.parentElement !== document.body) {
        document.body.appendChild(root);
    }

    const panel = root.querySelector('.academic-sync-dialog');
    const title = root.querySelector('[data-academic-sync-title]');
    const lead = root.querySelector('[data-academic-sync-lead]');
    const selectView = root.querySelector('[data-academic-sync-select-view]');
    const progressView = root.querySelector('[data-academic-sync-progress-view]');
    const resultView = root.querySelector('[data-academic-sync-result-view]');
    const resultSummary = root.querySelector('[data-academic-sync-result-summary]');
    const select = root.querySelector('[data-academic-sync-semester]');
    const preview = root.querySelector('[data-academic-sync-term-preview]');
    const empty = root.querySelector('[data-academic-sync-empty]');
    const confirm = root.querySelector('[data-academic-sync-confirm]');
    const cancel = root.querySelector('[data-academic-sync-cancel]');
    const stay = root.querySelector('[data-academic-sync-stay]');
    const reload = root.querySelector('[data-academic-sync-reload]');
    const close = root.querySelector('[data-academic-sync-close]');
    let activeTrigger = null;
    let running = false;
    let finished = false;
    let succeeded = false;

    const semesterMap = new Map((Array.isArray(semesters) ? semesters : []).map((item) => [String(item.id), item]));
    select.innerHTML = [...semesterMap.values()].map((item) => `
        <option value="${escapeHtml(item.id)}">${escapeHtml(item.name)}${item.is_current ? '（当前）' : ''}</option>
    `).join('');
    if (defaultSemesterId && semesterMap.has(String(defaultSemesterId))) {
        select.value = String(defaultSemesterId);
    }

    function selectedSemester() {
        return semesterMap.get(String(select.value || '')) || null;
    }

    function updatePreview() {
        const semester = selectedSemester();
        const hasSemester = Boolean(semester);
        preview.hidden = !hasSemester;
        empty.hidden = hasSemester;
        select.closest('.academic-sync-dialog__semester-field').hidden = !semesterMap.size;
        confirm.disabled = !hasSemester || running;
        if (!semester) return;
        root.querySelector('[data-academic-sync-semester-name]').textContent = `${semester.academic_year} ${semester.term_label}`;
        root.querySelector('[data-academic-sync-semester-range]').textContent = semester.start_date && semester.end_date
            ? `${semester.start_date} 至 ${semester.end_date}`
            : '起止日期待确认';
        root.querySelector('[data-academic-sync-xnm]').textContent = `xnm=${semester.xnm}`;
        root.querySelector('[data-academic-sync-xqm]').textContent = `xqm=${semester.xqm}`;
    }

    function setView(view) {
        selectView.hidden = view !== 'select';
        progressView.hidden = view !== 'progress';
        resultView.hidden = view !== 'result';
        confirm.hidden = view !== 'select';
        cancel.hidden = view !== 'select';
        stay.hidden = view !== 'result';
        reload.hidden = view !== 'result';
        close.disabled = view === 'progress';
    }

    function openDialog(trigger) {
        activeTrigger = trigger || null;
        finished = false;
        succeeded = false;
        stay.textContent = '留在当前页';
        reload.textContent = '刷新查看结果';
        title.textContent = '同步教务课程与班级';
        lead.textContent = '选择本平台已建立的学年学期，确认后一次同步课程、班级和学生。';
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
        if (refresh) {
            window.location.reload();
            return;
        }
        root.classList.remove('is-open');
        root.setAttribute('aria-hidden', 'true');
        document.body.classList.remove('has-academic-sync-dialog');
        window.setTimeout(() => {
            if (!root.classList.contains('is-open')) root.hidden = true;
        }, 170);
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

    async function runSync() {
        const semester = selectedSemester();
        if (!semester || running) return;
        running = true;
        setButtonsBusy(true);
        setView('progress');
        title.textContent = `正在同步 ${semester.name}`;
        lead.textContent = `使用 xnm=${semester.xnm}、xqm=${semester.xqm} 查询课程与全部教学班学生名单。`;
        try {
            const result = await apiFetch(endpoint, {
                method: 'POST',
                body: { semester_id: Number(semester.id) },
                silent: true,
            });
            finished = true;
            succeeded = true;
            title.textContent = '教务同步完成';
            lead.textContent = '课程、班级、学生和真实课次已完成幂等合并。';
            resultSummary.innerHTML = renderResult(result);
            setView('result');
            showMessage(result.message || '教务数据同步完成', 'success', 5200);
        } catch (error) {
            succeeded = false;
            title.textContent = '教务同步未完成';
            lead.textContent = error.message || '请检查教务账号和所选学期后重试。';
            resultSummary.innerHTML = resultList('未能完成同步', [error.message || '未知错误'], 'is-warning');
            stay.textContent = '关闭';
            reload.textContent = '返回重试';
            setView('result');
            showMessage(error.message || '教务同步失败', 'error', 5200);
        } finally {
            running = false;
            close.disabled = false;
            setButtonsBusy(false);
        }
    }

    triggers.forEach((button) => button.addEventListener('click', () => openDialog(button)));
    select.addEventListener('change', updatePreview);
    confirm.addEventListener('click', runSync);
    cancel.addEventListener('click', () => closeDialog());
    close.addEventListener('click', () => closeDialog());
    stay.addEventListener('click', () => closeDialog());
    reload.addEventListener('click', () => {
        if (succeeded) {
            closeDialog({ refresh: true });
            return;
        }
        title.textContent = '同步教务课程与班级';
        lead.textContent = '选择本平台已建立的学年学期，确认后一次同步课程、班级和学生。';
        setView('select');
        updatePreview();
        select.focus({ preventScroll: true });
    });
    root.addEventListener('click', (event) => {
        if (event.target === root) closeDialog();
    });
    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && !root.hidden && !running) closeDialog();
    });
    updatePreview();
    return { open: openDialog, close: closeDialog, hasFinished: () => finished };
}
