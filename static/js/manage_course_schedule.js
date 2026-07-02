/**
 * 课时统计（教师课程表）页面。
 *
 * - 查询 + 信息归集板块：学年学期 / 课程 / 班级筛选，汇总卡片与按课程课时统计；
 * - 周课程时间轴板块：Win7 Flip3D 风格的 3D 周卡片堆栈，滚轮 / 方向键 / 滑杆
 *   切换前后周，点击最前卡片放大铺满页面查看整周课表；
 * - 顶栏「同步智慧课堂」立即拉取 teacherSchedule/list 并替换本地学期数据。
 */

const bootElement = document.getElementById('course-schedule-boot');
const boot = bootElement ? JSON.parse(bootElement.textContent || '{}') : {};

const COURSE_PALETTE = [
    '#4f46e5', '#0ea5e9', '#059669', '#d97706', '#db2777',
    '#7c3aed', '#0891b2', '#65a30d', '#ea580c', '#e11d48',
];

const state = {
    overview: boot.overview || null,
    hasCredential: Boolean(boot.has_credential),
    activeWeekIndex: 0,     // index into overview.weeks
    expanded: false,
    syncing: false,
    loading: false,
};

const refs = {
    termSelect: document.querySelector('[data-cs-term]'),
    courseSelect: document.querySelector('[data-cs-course]'),
    classSelect: document.querySelector('[data-cs-class]'),
    resetBtn: document.querySelector('[data-cs-reset]'),
    syncBtn: document.querySelector('[data-cs-sync]'),
    syncTime: document.querySelector('[data-cs-sync-time]'),
    summary: document.querySelector('[data-cs-summary]'),
    courses: document.querySelector('[data-cs-courses]'),
    stage: document.querySelector('[data-cs-stage]'),
    indicator: document.querySelector('[data-cs-indicator]'),
    prevBtn: document.querySelector('[data-cs-prev]'),
    nextBtn: document.querySelector('[data-cs-next]'),
    slider: document.querySelector('[data-cs-slider]'),
    expand: document.querySelector('[data-cs-expand]'),
    expandTitle: document.querySelector('[data-cs-expand-title]'),
    expandSub: document.querySelector('[data-cs-expand-sub]'),
    expandBody: document.querySelector('[data-cs-expand-body]'),
    expandPrev: document.querySelector('[data-cs-expand-prev]'),
    expandNext: document.querySelector('[data-cs-expand-next]'),
    expandClose: document.querySelector('[data-cs-expand-close]'),
    toast: document.querySelector('[data-cs-toast]'),
};

let toastTimer = null;

function showToast(message, tone = 'info') {
    if (!refs.toast) return;
    refs.toast.textContent = message;
    refs.toast.className = `cs-toast is-show${tone === 'success' ? ' cs-toast--success' : ''}${tone === 'error' ? ' cs-toast--error' : ''}`;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => refs.toast.classList.remove('is-show'), 4200);
}

function escapeHtml(value) {
    return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;');
}

function courseAccent(courseName) {
    const options = state.overview?.filters?.course_options || [];
    const index = options.indexOf(courseName);
    return COURSE_PALETTE[(index >= 0 ? index : 0) % COURSE_PALETTE.length];
}

function currentFilters() {
    const selected = state.overview?.selected_term;
    return {
        year: selected?.year || '',
        term: selected?.term || '',
        course: refs.courseSelect?.value || '',
        class_label: refs.classSelect?.value || '',
    };
}

async function apiFetch(url, options = {}) {
    const response = await fetch(url, {
        headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
        credentials: 'same-origin',
        ...options,
    });
    if (!response.ok) {
        let detail = `请求失败（${response.status}）`;
        try {
            const data = await response.json();
            detail = data.detail || data.message || detail;
        } catch { /* keep default */ }
        throw new Error(detail);
    }
    return response.json();
}

/* ------------------------------------------------------------------ *
 * 查询 + 信息归集板块
 * ------------------------------------------------------------------ */

function renderFilters() {
    const overview = state.overview;
    if (!refs.termSelect || !overview) return;
    const terms = overview.terms || [];
    const selected = overview.selected_term;
    refs.termSelect.innerHTML = terms.length
        ? terms.map((term) => `
            <option value="${escapeHtml(term.year)}|${escapeHtml(term.term)}"
                ${selected && term.year === selected.year && term.term === selected.term ? 'selected' : ''}>
                ${escapeHtml(term.label)}
            </option>`).join('')
        : '<option value="">暂无学期数据</option>';

    const filters = overview.filters || {};
    const courseOptions = filters.course_options || [];
    refs.courseSelect.innerHTML = '<option value="">全部课程</option>' + courseOptions
        .map((name) => `<option value="${escapeHtml(name)}" ${name === filters.course ? 'selected' : ''}>${escapeHtml(name)}</option>`)
        .join('');
    const classOptions = filters.class_options || [];
    refs.classSelect.innerHTML = '<option value="">全部班级</option>' + classOptions
        .map((name) => `<option value="${escapeHtml(name)}" ${name === filters.class_label ? 'selected' : ''}>${escapeHtml(name)}</option>`)
        .join('');

    if (refs.syncTime) {
        refs.syncTime.textContent = selected?.synced_at ? `最近同步：${selected.synced_at}` : '尚未同步';
    }
}

function renderSummary() {
    const overview = state.overview;
    if (!refs.summary) return;
    const summary = overview?.summary || {};
    if (!overview || !overview.terms?.length) {
        refs.summary.innerHTML = '';
        return;
    }
    const cards = [
        { value: summary.total_hours ?? 0, label: '学期总课时', hint: '筛选条件下 节数 × 周数 合计' },
        { value: summary.current_week_hours ?? 0, label: `本周课时${summary.cur_week ? `（第${summary.cur_week}周）` : ''}`, hint: '当前教学周安排' },
        { value: summary.course_count ?? 0, label: '课程数', hint: '筛选后统计' },
        { value: summary.class_count ?? 0, label: '教学班数', hint: '含智慧课堂教学班标注' },
        { value: summary.slot_count ?? 0, label: '排课记录', hint: '每条 = 固定星期与节次' },
        { value: summary.weekly_average_hours ?? 0, label: '周均课时', hint: '仅计入有课的周' },
    ];
    refs.summary.innerHTML = cards.map((card) => `
        <div class="cs-summary__card">
            <strong>${escapeHtml(card.value)}</strong>
            <span>${escapeHtml(card.label)}</span>
            <small>${escapeHtml(card.hint)}</small>
        </div>`).join('');
}

function renderCourses() {
    const overview = state.overview;
    if (!refs.courses) return;
    const courses = overview?.courses || [];
    if (!courses.length) {
        refs.courses.innerHTML = '<div class="cs-empty" style="padding:24px;">暂无课程统计，请先同步或调整筛选。</div>';
        return;
    }
    const activeCourse = overview.filters?.course || '';
    refs.courses.innerHTML = courses.map((course) => {
        const accent = courseAccent(course.course_name);
        const metaChips = [
            course.week_span,
            `${course.week_count} 个教学周`,
            `${course.slot_count} 条排课`,
            course.max_student_count ? `最多 ${course.max_student_count} 人` : '',
            ...(course.classes || []).slice(0, 2),
        ].filter(Boolean);
        return `
        <div class="cs-course-card ${course.course_name === activeCourse ? 'is-active' : ''}"
             style="--cs-accent:${accent}" data-course="${escapeHtml(course.course_name)}" role="button" tabindex="0">
            <div class="cs-course-card__head">
                <h4>${escapeHtml(course.course_name)}</h4>
                <div class="cs-course-card__hours">${escapeHtml(course.total_hours)}<small> 课时</small></div>
            </div>
            <div class="cs-course-card__meta">
                ${metaChips.map((chip) => `<span>${escapeHtml(chip)}</span>`).join('')}
            </div>
        </div>`;
    }).join('');
}

/* ------------------------------------------------------------------ *
 * 课表网格（迷你卡片与放大视图共用）
 * ------------------------------------------------------------------ */

function renderWeekGrid(week, { expanded = false } = {}) {
    const range = state.overview?.section_range || { min: 1, max: 11 };
    const minSection = Math.max(1, Number(range.min) || 1);
    const maxSection = Math.max(minSection, Number(range.max) || 11);
    const sectionCount = maxSection - minSection + 1;
    const headerRow = expanded ? '34px' : '24px';
    const labelCol = expanded ? '54px' : '30px';

    const dayHeads = ['一', '二', '三', '四', '五', '六', '日']
        .map((day, index) => `<div class="cs-grid__day" style="grid-column:${index + 2};grid-row:1;">周${day}</div>`)
        .join('');
    const sectionLabels = Array.from({ length: sectionCount }, (_, offset) => {
        const section = minSection + offset;
        return `<div class="cs-grid__section" style="grid-column:1;grid-row:${offset + 2};">${section}</div>`;
    }).join('');
    const cellBackgrounds = Array.from({ length: sectionCount * 7 }, (_, cell) => {
        const row = Math.floor(cell / 7) + 2;
        const column = (cell % 7) + 2;
        return `<div class="cs-grid__cellbg" style="grid-column:${column};grid-row:${row};"></div>`;
    }).join('');

    const lessons = (week?.lessons || []).map((lesson) => {
        const sections = lesson.sections || [];
        const start = Math.max(minSection, sections[0] || minSection);
        const end = Math.min(maxSection, sections[sections.length - 1] || start);
        const rowStart = start - minSection + 2;
        const rowSpan = Math.max(1, end - start + 1);
        const column = Math.min(7, Math.max(1, lesson.weekday || 1)) + 1;
        const accent = courseAccent(lesson.course_name);
        const detailLines = expanded
            ? `<span>${escapeHtml(lesson.section_label)} · ${escapeHtml(lesson.classroom || '教室待定')}</span>
               <span>${escapeHtml(lesson.class_label || '')}${lesson.student_count ? ` · ${lesson.student_count}人` : ''}</span>`
            : `<span>${escapeHtml(lesson.classroom || '')}</span>
               <span>${escapeHtml(lesson.class_label || '')}</span>`;
        return `
        <div class="cs-lesson" style="--cs-accent:${accent};grid-column:${column};grid-row:${rowStart} / span ${rowSpan};"
             title="${escapeHtml(`${lesson.course_name} ${lesson.section_label} ${lesson.classroom || ''} ${lesson.class_label || ''}`)}">
            <strong>${escapeHtml(lesson.course_name)}</strong>
            ${detailLines}
        </div>`;
    }).join('');

    return `
    <div class="cs-grid ${expanded ? 'cs-grid--expanded' : ''}"
         style="grid-template-columns:${labelCol} repeat(7, 1fr);grid-template-rows:${headerRow} repeat(${sectionCount}, 1fr);">
        <div class="cs-grid__corner" style="grid-column:1;grid-row:1;">节</div>
        ${dayHeads}
        ${sectionLabels}
        ${cellBackgrounds}
        ${lessons}
    </div>`;
}

/* ------------------------------------------------------------------ *
 * 3D 周卡片堆栈
 * ------------------------------------------------------------------ */

function renderDeck() {
    const overview = state.overview;
    if (!refs.stage) return;
    refs.stage.querySelectorAll('.cs-card, .cs-empty').forEach((node) => node.remove());
    const weeks = overview?.weeks || [];

    if (!weeks.length) {
        const empty = document.createElement('div');
        empty.className = 'cs-empty';
        empty.innerHTML = state.hasCredential
            ? '<strong>暂无课表数据</strong><p>点击右上角「同步智慧课堂」拉取本学期排课。</p>'
            : '<strong>还未配置智慧课堂账号</strong><p>请先到 <a href="/manage/teaching/smart-classroom-integrations">智慧课堂对接</a> 保存并验证账号，再回来同步课程表。</p>';
        refs.stage.appendChild(empty);
        updateDeckNav();
        return;
    }

    state.activeWeekIndex = Math.min(Math.max(state.activeWeekIndex, 0), weeks.length - 1);
    weeks.forEach((week, index) => {
        const card = document.createElement('div');
        card.className = 'cs-card';
        card.dataset.weekIndex = String(index);
        card.innerHTML = `
            <div class="cs-card__bar">
                <strong>${escapeHtml(week.label)}</strong>
                <span>${week.lesson_count} 节安排 · ${week.total_hours} 课时</span>
                <span class="cs-card__badge ${week.is_current ? 'is-current' : ''}">${week.is_current ? '本周' : escapeHtml(week.label)}</span>
            </div>
            <div class="cs-card__body">${renderWeekGrid(week)}</div>`;
        refs.stage.appendChild(card);
    });
    layoutDeck();
}

function layoutDeck() {
    const cards = refs.stage ? refs.stage.querySelectorAll('.cs-card') : [];
    cards.forEach((card) => {
        const index = Number(card.dataset.weekIndex);
        const offset = index - state.activeWeekIndex;
        card.classList.toggle('is-active', offset === 0);
        if (offset < -1 || offset > 6) {
            card.hidden = true;
            return;
        }
        card.hidden = false;
        let transform;
        let opacity;
        let zIndex;
        if (offset === 0) {
            transform = 'translate(-50%, -50%) translateZ(60px)';
            opacity = 1;
            zIndex = 300;
        } else if (offset > 0) {
            // 后面的周：像 Win7 Flip3D 一样向右上方纵深堆叠。
            transform = `translate(-50%, -50%) translate3d(${offset * 64}px, ${offset * -34}px, ${-offset * 170 + 60}px) rotateY(-7deg)`;
            opacity = Math.max(0.28, 1 - offset * 0.14);
            zIndex = 300 - offset;
        } else {
            // 刚翻过去的周：滑向左前方并淡出。
            transform = 'translate(-50%, -50%) translate3d(-420px, 120px, 240px) rotateY(18deg)';
            opacity = 0;
            zIndex = 301;
        }
        card.style.transform = transform;
        card.style.opacity = String(opacity);
        card.style.zIndex = String(zIndex);
        card.style.pointerEvents = offset === 0 ? 'auto' : 'none';
    });
    updateDeckNav();
    if (state.expanded) renderExpanded();
}

function updateDeckNav() {
    const weeks = state.overview?.weeks || [];
    const active = weeks[state.activeWeekIndex];
    if (refs.indicator) {
        refs.indicator.innerHTML = active
            ? `${escapeHtml(active.label)}${active.is_current ? ' · 本周' : ''}<small>${active.lesson_count} 节安排 · ${active.total_hours} 课时</small>`
            : '—';
    }
    if (refs.slider) {
        refs.slider.min = '1';
        refs.slider.max = String(Math.max(1, weeks.length));
        refs.slider.value = String(state.activeWeekIndex + 1);
        refs.slider.disabled = !weeks.length;
    }
    if (refs.prevBtn) refs.prevBtn.disabled = state.activeWeekIndex <= 0;
    if (refs.nextBtn) refs.nextBtn.disabled = state.activeWeekIndex >= weeks.length - 1;
}

function goToWeek(index) {
    const weeks = state.overview?.weeks || [];
    if (!weeks.length) return;
    const next = Math.min(Math.max(index, 0), weeks.length - 1);
    if (next === state.activeWeekIndex) return;
    state.activeWeekIndex = next;
    layoutDeck();
}

/* ------------------------------------------------------------------ *
 * 放大视图
 * ------------------------------------------------------------------ */

function renderExpanded() {
    const weeks = state.overview?.weeks || [];
    const week = weeks[state.activeWeekIndex];
    if (!week || !refs.expandBody) return;
    if (refs.expandTitle) refs.expandTitle.textContent = week.label + (week.is_current ? '（本周）' : '');
    if (refs.expandSub) {
        const termLabel = state.overview?.selected_term?.label || '';
        refs.expandSub.textContent = `${termLabel} · ${week.lesson_count} 节安排 · ${week.total_hours} 课时`;
    }
    refs.expandBody.innerHTML = renderWeekGrid(week, { expanded: true });
}

function openExpanded() {
    if (!state.overview?.weeks?.length || !refs.expand) return;
    state.expanded = true;
    renderExpanded();
    refs.expand.classList.add('is-open');
}

function closeExpanded() {
    state.expanded = false;
    refs.expand?.classList.remove('is-open');
    refs.stage?.focus({ preventScroll: true });
}

/* ------------------------------------------------------------------ *
 * 数据加载与同步
 * ------------------------------------------------------------------ */

function applyOverview(overview, { keepWeek = false } = {}) {
    const previousWeek = state.overview?.weeks?.[state.activeWeekIndex]?.week_index;
    state.overview = overview;
    const weeks = overview?.weeks || [];
    let nextIndex = weeks.findIndex((week) => week.is_current);
    if (keepWeek && previousWeek) {
        const kept = weeks.findIndex((week) => week.week_index === previousWeek);
        if (kept >= 0) nextIndex = kept;
    }
    state.activeWeekIndex = nextIndex >= 0 ? nextIndex : 0;
    renderFilters();
    renderSummary();
    renderCourses();
    renderDeck();
}

async function reloadOverview({ keepWeek = true } = {}) {
    if (state.loading) return;
    state.loading = true;
    try {
        const params = new URLSearchParams(currentFilters());
        const data = await apiFetch(`/api/manage/teaching/course-schedule/overview?${params.toString()}`);
        applyOverview(data.overview, { keepWeek });
    } catch (error) {
        showToast(error.message || '课表数据加载失败。', 'error');
    } finally {
        state.loading = false;
    }
}

async function runSync() {
    if (state.syncing) return;
    if (!state.hasCredential) {
        showToast('请先在「智慧课堂对接」页面保存并验证智慧课堂账号。', 'error');
        return;
    }
    state.syncing = true;
    const syncLabel = refs.syncBtn?.querySelector('.app-topbar-action__text strong') || refs.syncBtn;
    const originalText = syncLabel?.textContent;
    if (syncLabel) syncLabel.textContent = '同步中…';
    refs.syncBtn?.setAttribute('disabled', 'disabled');
    try {
        const data = await apiFetch('/api/manage/teaching/course-schedule/sync', {
            method: 'POST',
            body: JSON.stringify(currentFilters()),
        });
        applyOverview(data.overview, { keepWeek: false });
        const ok = ['success', 'partial_success'].includes(data.status);
        showToast(data.message || '同步完成。', ok ? 'success' : (data.status === 'empty' ? 'info' : 'error'));
    } catch (error) {
        showToast(error.message || '同步失败，请稍后重试。', 'error');
    } finally {
        state.syncing = false;
        if (syncLabel && originalText) syncLabel.textContent = originalText;
        refs.syncBtn?.removeAttribute('disabled');
    }
}

/* ------------------------------------------------------------------ *
 * 事件绑定
 * ------------------------------------------------------------------ */

refs.termSelect?.addEventListener('change', () => {
    const [year, term] = String(refs.termSelect.value || '').split('|');
    if (state.overview) {
        state.overview = { ...state.overview, selected_term: { ...(state.overview.selected_term || {}), year: year || '', term: term || '' } };
    }
    if (refs.courseSelect) refs.courseSelect.value = '';
    if (refs.classSelect) refs.classSelect.value = '';
    reloadOverview({ keepWeek: false });
});
refs.courseSelect?.addEventListener('change', () => reloadOverview());
refs.classSelect?.addEventListener('change', () => reloadOverview());
refs.resetBtn?.addEventListener('click', () => {
    if (refs.courseSelect) refs.courseSelect.value = '';
    if (refs.classSelect) refs.classSelect.value = '';
    reloadOverview();
});
refs.syncBtn?.addEventListener('click', (event) => {
    event.preventDefault();
    runSync();
});

refs.courses?.addEventListener('click', (event) => {
    const card = event.target.closest('.cs-course-card');
    if (!card || !refs.courseSelect) return;
    const courseName = card.dataset.course || '';
    refs.courseSelect.value = refs.courseSelect.value === courseName ? '' : courseName;
    reloadOverview();
});

let wheelLockUntil = 0;
refs.stage?.addEventListener('wheel', (event) => {
    if (!state.overview?.weeks?.length) return;
    event.preventDefault();
    const now = Date.now();
    if (now < wheelLockUntil) return;
    wheelLockUntil = now + 240;
    goToWeek(state.activeWeekIndex + (event.deltaY > 0 ? 1 : -1));
}, { passive: false });

refs.stage?.addEventListener('keydown', (event) => {
    if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
        event.preventDefault();
        goToWeek(state.activeWeekIndex + 1);
    } else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
        event.preventDefault();
        goToWeek(state.activeWeekIndex - 1);
    } else if (event.key === 'Enter') {
        openExpanded();
    }
});

refs.stage?.addEventListener('click', (event) => {
    const card = event.target.closest('.cs-card');
    if (card && card.classList.contains('is-active')) openExpanded();
});

refs.prevBtn?.addEventListener('click', () => goToWeek(state.activeWeekIndex - 1));
refs.nextBtn?.addEventListener('click', () => goToWeek(state.activeWeekIndex + 1));
refs.slider?.addEventListener('input', () => goToWeek(Number(refs.slider.value) - 1));

refs.expandPrev?.addEventListener('click', () => { goToWeek(state.activeWeekIndex - 1); renderExpanded(); });
refs.expandNext?.addEventListener('click', () => { goToWeek(state.activeWeekIndex + 1); renderExpanded(); });
refs.expandClose?.addEventListener('click', closeExpanded);
refs.expand?.addEventListener('click', (event) => {
    if (event.target === refs.expand) closeExpanded();
});
refs.expand?.addEventListener('wheel', (event) => {
    event.preventDefault();
    const now = Date.now();
    if (now < wheelLockUntil) return;
    wheelLockUntil = now + 260;
    goToWeek(state.activeWeekIndex + (event.deltaY > 0 ? 1 : -1));
    renderExpanded();
}, { passive: false });

document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && state.expanded) closeExpanded();
});

/* ------------------------------------------------------------------ *
 * 启动
 * ------------------------------------------------------------------ */

if (state.overview) {
    applyOverview(state.overview, { keepWeek: false });
} else {
    reloadOverview({ keepWeek: false });
}
