/**
 * 课时统计（教师课程表）页面。
 *
 * - 查询 + 信息归集板块：学年学期 / 课程 / 班级筛选，汇总卡片与按课程课时统计；
 * - 周课程时间轴板块：复用可移植的 course_schedule_deck 模块（Win7 Flip3D
 *   风格 3D 周卡片堆栈，放大视图含早读/上午/下午/晚上分区与课堂跳转）；
 * - 顶栏「同步智慧课堂」立即拉取 teacherSchedule/list 并替换本地学期数据。
 */

import { createScheduleDeck, courseAccentFor } from '/static/js/course_schedule_deck.js?v=deck3d-20260705';

const bootElement = document.getElementById('course-schedule-boot');
const boot = bootElement ? JSON.parse(bootElement.textContent || '{}') : {};

const state = {
    overview: boot.overview || null,
    hasCredential: Boolean(boot.has_credential),
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
    deckMount: document.querySelector('[data-cs-deck]'),
    toast: document.querySelector('[data-cs-toast]'),
};

const deck = createScheduleDeck(refs.deckMount, {
    title: '周课程时间轴',
    description: '滚轮或方向键切换周次，点击最前面的周卡片放大查看整周课表；放大后点击课程块可进入对应课堂。',
    emptyHtml: () => (state.hasCredential
        ? '<strong>暂无课表数据</strong><p>点击右上角「同步智慧课堂」拉取本学期排课。</p>'
        : '<strong>还未配置智慧课堂账号</strong><p>请先到 <a href="/manage/teaching/smart-classroom-integrations">智慧课堂对接</a> 保存并验证账号，再回来同步课程表。</p>'),
});

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
    const statusSuffix = (term) => (
        term.status === 'current' ? '（进行中）' : term.status === 'ended' ? '（已结束）' : term.status === 'future' ? '（未开始）' : ''
    );
    refs.termSelect.innerHTML = terms.length
        ? terms.map((term) => `
            <option value="${escapeHtml(term.year)}|${escapeHtml(term.term)}"
                ${selected && term.year === selected.year && term.term === selected.term ? 'selected' : ''}>
                ${escapeHtml(term.label)}${statusSuffix(term)}
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
    if (refs.resetBtn) {
        refs.resetBtn.disabled = !(filters.course || filters.class_label);
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
        { value: summary.total_hours ?? 0, label: '学期总课时', hint: '筛选条件下 节数 × 周数 合计', tone: 'indigo' },
        { value: summary.current_week_hours ?? 0, label: `本周课时${summary.cur_week ? `（第${summary.cur_week}周）` : ''}`, hint: '当前教学周安排', tone: 'amber' },
        { value: summary.course_count ?? 0, label: '课程数', hint: '筛选后统计', tone: 'sky' },
        { value: summary.class_count ?? 0, label: '教学班数', hint: '含智慧课堂教学班标注', tone: 'emerald' },
        { value: summary.slot_count ?? 0, label: '排课记录', hint: '每条 = 固定星期与节次', tone: 'violet' },
        { value: summary.weekly_average_hours ?? 0, label: '周均课时', hint: '仅计入有课的周', tone: 'slate' },
    ];
    // 教学周进度条：跨满整行，展示本学期推进程度与教学周锚点来源。
    const curWeek = Number(summary.cur_week) || 0;
    const maxWeek = Number(summary.max_week) || 0;
    const termStatus = summary.term_status || '';
    const anchorParts = [];
    if (summary.week1_monday) anchorParts.push(`第1周周一 ${summary.week1_monday}`);
    if (summary.anchor_label) anchorParts.push(summary.anchor_label);
    const anchorHtml = anchorParts.length
        ? `<small class="cs-progress__anchor">${escapeHtml(anchorParts.join(' · '))}</small>`
        : '';
    let progressText = '';
    let progressWidth = 0;
    if (curWeek > 0 && maxWeek > 0) {
        progressText = `第${curWeek}周 / 共${maxWeek}周`;
        progressWidth = Math.min(100, Math.round((curWeek / maxWeek) * 100));
    } else if (termStatus === 'ended' && maxWeek > 0) {
        progressText = `学期已结束 · 共${maxWeek}周`;
        progressWidth = 100;
    } else if (termStatus === 'future' && maxWeek > 0) {
        progressText = `学期未开始 · 共${maxWeek}周`;
        progressWidth = 0;
    }
    const progressHtml = progressText
        ? `<div class="cs-progress">
               <span class="cs-progress__label">教学周进度</span>
               <div class="cs-progress__bar" role="progressbar" aria-valuemin="0" aria-valuemax="${maxWeek}" aria-valuenow="${Math.min(Math.max(curWeek, 0), maxWeek)}">
                   <i style="width:${progressWidth}%"></i>
               </div>
               <strong>${escapeHtml(progressText)}</strong>
               ${anchorHtml}
           </div>`
        : '';
    refs.summary.innerHTML = progressHtml + cards.map((card) => `
        <div class="cs-summary__card cs-summary__card--${card.tone}">
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
        const accent = courseAccentFor(overview, course.course_name);
        const metaChips = [
            course.week_span,
            `${course.week_count} 个教学周`,
            `${course.slot_count} 条排课`,
            course.max_student_count ? `最多 ${course.max_student_count} 人` : '',
            ...(course.classes || []).slice(0, 2),
        ].filter(Boolean);
        const isActive = course.course_name === activeCourse;
        return `
        <div class="cs-course-card ${isActive ? 'is-active' : ''}"
             style="--cs-accent:${accent}" data-course="${escapeHtml(course.course_name)}" role="button" tabindex="0"
             aria-pressed="${isActive}"
             title="${isActive ? '再次点击取消筛选' : '点击筛选该课程'}">
            ${isActive ? '<span class="cs-course-card__check">✓ 筛选中</span>' : ''}
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
 * 数据加载与同步
 * ------------------------------------------------------------------ */

function applyOverview(overview, { keepWeek = false } = {}) {
    state.overview = overview;
    renderFilters();
    renderSummary();
    renderCourses();
    deck?.setOverview(overview, { keepWeek });
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

refs.courses?.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    const card = event.target.closest('.cs-course-card');
    if (!card) return;
    event.preventDefault();
    card.click();
});

/* ------------------------------------------------------------------ *
 * 启动
 * ------------------------------------------------------------------ */

if (state.overview) {
    applyOverview(state.overview, { keepWeek: false });
} else {
    reloadOverview({ keepWeek: false });
}
