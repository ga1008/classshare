/**
 * 可移植 3D 周课表模块（Win7 Flip3D 风格）。
 *
 * 自带样式（首次实例化时注入 <style>），调用方只需提供一个挂载容器：
 *
 *   import { createScheduleDeck } from '/static/js/course_schedule_deck.js';
 *   const deck = createScheduleDeck(mountEl, {
 *       title: '周课程时间轴',
 *       showTermSelect: true,                 // 头部显示学年学期下拉
 *       onTermChange: (year, term) => {...},  // 学期切换回调
 *       emptyHtml: () => '<strong>暂无课表数据</strong>',
 *   });
 *   deck.setOverview(overview, { keepWeek: false });
 *
 * overview 结构即 /api/manage/academic/course-schedule/overview 的返回值：
 * terms / selected_term / weeks[{week_index,label,is_current,lessons[]}] /
 * section_range / filters.course_options。lesson.classroom_url 存在时课程块
 * 在放大视图中可点击跳转对应课堂。
 *
 * 放大视图按节次给出早读(1)/上午(2-5)/下午(6-9)/晚上(10-11+)的背景分区。
 */

import { scheduleChangeConnections, scheduleChangeColors } from './course_schedule_change_links.js?v=change-lines-glass-20260920';
import { routeScheduleChanges, roundedScheduleRoute } from './course_schedule_change_routes.js?v=change-lines-simple-20260920';

import { compactClassroomName, adjustmentActionText } from './course_schedule_presentation.js?v=schedule-glass-20260920';

import { DECK_CSS } from './course_schedule_styles.js';

const STYLE_ID = 'course-schedule-deck-style';
let changeMapSequence = 0;

/** Normalize mouse/trackpad intent without trapping page scrolling or zoom. */
export function scheduleWheelIntent({ deltaY = 0, deltaMode = 0, ctrlKey = false, metaKey = false, index = 0, length = 0, pending = 0 }) {
    const direction = Math.sign(deltaY);
    if (!direction || ctrlKey || metaKey || length < 2 || index + direction < 0 || index + direction >= length) return { consume: false, step: 0, pending: 0 };
    const pixels = deltaY * (deltaMode === 1 ? 16 : deltaMode === 2 ? 400 : 1);
    const total = (Math.sign(pending) === direction ? pending : 0) + pixels;
    return Math.abs(total) >= 32 ? { consume: true, step: direction, pending: 0 } : { consume: true, step: 0, pending: total };
}

const BAND_LABELS = { dawn: '早读', am: '上午', pm: '下午', eve: '晚上' };

export function pendingScheduleChange(lesson) {
    const change = lesson?.adjustment;
    return change?.phase === 'pending' && ['move', 'cancel', 'room'].includes(change.kind)
        && ['original', 'proposed'].includes(change.endpoint) ? change : null;
}

export function countScheduleLessons(lessons = []) {
    const official = lessons.filter(lesson => lesson.counts_towards_total !== false
        && pendingScheduleChange(lesson)?.endpoint !== 'proposed');
    return { lesson_count: official.length, total_hours: official.reduce((sum, lesson) => sum + Number(lesson.hours || lesson.sections?.length || 0), 0),
        proposed_count: lessons.filter(lesson => pendingScheduleChange(lesson)?.endpoint === 'proposed').length };
}

/** Separate overlapping intervals without changing their actual grid positions. */
export function scheduleLessonLanes(lessons = []) {
    const result = new Map();
    for (let day = 1; day <= 7; day += 1) {
        const rows = lessons.map((lesson, index) => ({ lesson, index, start: Number(lesson.sections?.[0] || 1), end: Number(lesson.sections?.at(-1) || 1) }))
            .filter(row => Number(row.lesson.weekday) === day).sort((a, b) => a.start - b.start || a.end - b.end || a.index - b.index);
        let cluster = [], clusterEnd = 0;
        const flush = () => {
            const ends = [];
            cluster.forEach(row => { let lane = ends.findIndex(end => end < row.start); if (lane < 0) lane = ends.length; ends[lane] = row.end; row.lane = lane; });
            cluster.forEach(row => result.set(row.index, { lane: row.lane, count: ends.length }));
            cluster = [];
        };
        rows.forEach(row => { if (cluster.length && row.start > clusterEnd) flush(); cluster.push(row); clusterEnd = Math.max(cluster.length === 1 ? 0 : clusterEnd, row.end); });
        flush();
    }
    return result;
}

export function scheduleChangeLabel(lesson) {
    const change = pendingScheduleChange(lesson);
    if (!change) return '';
    if (change.endpoint === 'proposed') return '正在申请变更';
    return ({ move: '调课待审', cancel: '停课待审', room: '更换教室待审' })[change.kind];
}


function ensureStyles() {
    if (document.getElementById(STYLE_ID)) return;
    const style = document.createElement('style');
    style.id = STYLE_ID;
    style.textContent = DECK_CSS;
    document.head.appendChild(style);
}

function escapeHtml(value) {
    return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;');
}

/** A live token keeps cards, their previews and course filters in the same palette. */
export function courseAccentFor() {
    return 'hsl(var(--ls-primary, 243 75% 59%))';
}

/** 节次 → 时段：1=早读，2-5=上午，6-9=下午，10+=晚上。 */
function sectionBand(section) {
    if (section <= 1) return 'dawn';
    if (section <= 5) return 'am';
    if (section <= 9) return 'pm';
    return 'eve';
}

/** 今天对应的远端星期（1=周一 .. 7=周日）。 */
function todayRemoteWeekday() {
    return ((new Date().getDay() + 6) % 7) + 1;
}

function weekEmptyMarkHtml(week) {
    return week && !(week.lessons || []).length ? '<div class="cs-week-empty-mark">本周无排课</div>' : '';
}

export function createScheduleDeck(container, options = {}) {
    if (!container) return null;
    ensureStyles();

    const config = {
        title: options.title || '周课程时间轴',
        description: options.description
            || '滚轮或方向键切换周次，点击最前面的周卡片放大查看整周课表。',
        showTermSelect: Boolean(options.showTermSelect),
        onTermChange: typeof options.onTermChange === 'function' ? options.onTermChange : null,
        emptyHtml: typeof options.emptyHtml === 'function'
            ? options.emptyHtml
            : () => '<strong>暂无课表数据</strong><p>请先同步智慧课堂课程表。</p>',
        onNavigate: typeof options.onNavigate === 'function'
            ? options.onNavigate
            : (url) => window.location.assign(url),
    };

    const state = { overview: null, activeWeekIndex: 0, expanded: false };
    let wheelLockUntil = 0;
    let wheelPending = 0;
    let wheelPendingAt = 0;
    let wheelGestureConsumed = false;
    let expandMotionGeneration = 0;
    let expandedTrigger = null;
    let renderedExpandedWeek = null;
    let highlightTimer = null;
    let lineFrame = null;
    let destroyed = false;
    // Optional host integration only. Standalone decks keep their own focus,
    // key handling and animation without importing an application layer system.
    let overlayCoordinator = null;
    const notifyOverlay = () => overlayCoordinator?.onStateChange?.();
    const changeMapId = `cs-change-map-${++changeMapSequence}`;
    const changeColorsByTerm = new Map();
    let changeColors = new Map();
    let lineConnections = [];
    let previousLineRoutes = [];
    let lastLineMap = null;
    let lastLineGeometry = '';

    container.classList.add('cs-deck');
    container.innerHTML = `
        <div class="cs-deck-head">
            <div class="cs-deck-head__copy">
                <h3>${escapeHtml(config.title)}</h3>
                <p>${escapeHtml(config.description)}</p>
            </div>
            ${config.showTermSelect ? '<select class="cs-deck-term" data-csd-term aria-label="学年学期"></select>' : ''}
            <div class="cs-deck-nav">
                <button type="button" class="cs-deck-nav__btn" data-csd-prev title="上一周">‹</button>
                <div class="cs-week-indicator" data-csd-indicator aria-live="polite">—</div>
                <button type="button" class="cs-deck-nav__btn" data-csd-next title="下一周">›</button>
                <input type="range" class="cs-deck-slider" data-csd-slider min="1" max="1" value="1" aria-label="周次选择滑杆" />
            </div>
        </div>
        <div class="cs-stage" data-csd-stage tabindex="0" aria-label="按周课程表，使用滚轮、方向键或左右拖拽切换周次">
            <div class="cs-stage__hint">滚轮/拖拽切换周次 · 点击卡片放大</div>
        </div><div class="cs-deck-feedback" data-csd-feedback role="status"></div>`;

    const expand = document.createElement('div');
    expand.className = 'cs-expand';
    expand.hidden = true;
    expand.setAttribute('role', 'dialog');
    expand.setAttribute('aria-modal', 'true');
    expand.setAttribute('aria-label', '整周课表');
    expand.innerHTML = `
        <div class="cs-expand__card">
            <div class="cs-expand__bar">
                <strong data-csd-expand-title>第1周</strong>
                <span data-csd-expand-sub></span>
                <span data-csd-expand-feedback role="status"></span>
                <div class="cs-expand__nav">
                    <button type="button" data-csd-expand-prev>‹ 上一周</button>
                    <button type="button" data-csd-expand-next>下一周 ›</button>
                    <button type="button" data-csd-expand-close>返回 3D 视图</button>
                </div>
            </div>
            <div class="cs-expand__body" data-csd-expand-body></div>
        </div>`;
    document.body.appendChild(expand);

    const refs = {
        termSelect: container.querySelector('[data-csd-term]'),
        stage: container.querySelector('[data-csd-stage]'),
        indicator: container.querySelector('[data-csd-indicator]'),
        prevBtn: container.querySelector('[data-csd-prev]'),
        nextBtn: container.querySelector('[data-csd-next]'),
        slider: container.querySelector('[data-csd-slider]'),
        feedback: container.querySelector('[data-csd-feedback]'),
        expand,
        expandTitle: expand.querySelector('[data-csd-expand-title]'),
        expandSub: expand.querySelector('[data-csd-expand-sub]'),
        expandFeedback: expand.querySelector('[data-csd-expand-feedback]'),
        expandBody: expand.querySelector('[data-csd-expand-body]'),
        expandPrev: expand.querySelector('[data-csd-expand-prev]'),
        expandNext: expand.querySelector('[data-csd-expand-next]'),
        expandClose: expand.querySelector('[data-csd-expand-close]'),
    };

    /* ---------------- 课表网格 ---------------- */

    /**
     * 课程卡片三态：
     * Compact cards share the course/room summary. A stable grid slot anchors
     * the same link while its glass surface grows proportionally. Full metadata
     * appears only after expansion; unusually long details scroll inside it.
     */
    function lessonHtml(lesson, { expanded, minSection, maxSection, columnBase, lane = { lane: 0, count: 1 }, eventKey = '' }) {
        const sections = lesson.sections || [];
        const start = Math.max(minSection, sections[0] || minSection);
        const end = Math.min(maxSection, sections[sections.length - 1] || start);
        const rowStart = start - minSection + 2;
        const rowSpan = Math.max(1, end - start + 1);
        const column = Math.min(7, Math.max(1, lesson.weekday || 1)) + columnBase - 1;
        const accent = courseAccentFor(state.overview, lesson.course_name);
        const laneStyle = lane.count > 1 ? `width:calc(100% / ${lane.count} - 2px);margin-left:calc(100% / ${lane.count} * ${lane.lane});` : '';
        const gridPos = `grid-column:${column};grid-row:${rowStart} / span ${rowSpan};${laneStyle}`;
        const change = pendingScheduleChange(lesson);
        const proposed = change?.endpoint === 'proposed';
        const href = String(lesson.classroom_url || lesson.create_url || '');
        const isCreate = Boolean(href) && !lesson.classroom_url;
        const fullRoom = String(lesson.classroom || lesson.classroom_short || '教室待定');
        const extractedRoom = compactClassroomName(fullRoom);
        const shortRoom = extractedRoom === fullRoom && lesson.classroom_short ? compactClassroomName(lesson.classroom_short) : extractedRoom;
        const linkHint = !lesson.classroom_url && lesson.class_offering_id ? '课次尚未精确关联，请同步教务课表核对'
            : isCreate ? '尚无对应课堂 · 点击创建；创建后请再次同步关联课次' : href ? '点击进入课堂 →' : '';
        const sessionText = lesson.session_no ? `第${lesson.session_no}次课${lesson.session_total ? `（共${lesson.session_total}次）` : ''}` : '';
        const weekday = lesson.weekday_label || `星期${['一','二','三','四','五','六','日'][Math.min(6, Math.max(0, Number(lesson.weekday || 1) - 1))]}`;
        const time = [lesson.actual_date, weekday, lesson.time_label || [lesson.start_time, lesson.end_time].filter(Boolean).join('–'), lesson.section_label].filter(Boolean).join(' · ');
        const details = [
            `<span class="cs-lesson__meta">${escapeHtml(time)}</span>`,
            sessionText ? `<span class="cs-lesson__meta">${escapeHtml(sessionText)}${lesson.single_or_double_label ? ` · ${escapeHtml(lesson.single_or_double_label)}` : ''}</span>` : '',
            lesson.class_label ? `<span class="cs-lesson__meta">班级 ${escapeHtml(lesson.class_label)}${lesson.student_count ? ` · ${escapeHtml(lesson.student_count)}人` : ''}</span>` : '',
            linkHint ? `<span class="cs-lesson__meta cs-lesson__link-hint">${escapeHtml(linkHint)}</span>` : '',
        ].filter(Boolean).join('');
        let button = '', comparison = '';
        if (change) {
            const counterpart = change.kind === 'move' && change.counterpart_event_key;
            const jump = counterpart ? ` ${proposed ? '↩ 原位置' : '↗ 新位置'}${change.counterpart_week_index ? ` · 第${change.counterpart_week_index}周` : ''}` : (change.kind === 'room' ? ' · 查看对照' : ' · 查看说明');
            const actionLabel = scheduleChangeLabel(lesson) + jump;
            const shortLabel = adjustmentActionText(lesson);
            button = `<button type="button" class="cs-adjustment-label" data-csd-change="${escapeHtml(eventKey)}" aria-expanded="false" aria-label="${escapeHtml(actionLabel)}" title="${escapeHtml(actionLabel)}"><span class="cs-adjustment-label__short" aria-hidden="true">${escapeHtml(shortLabel).replace('+', '+<wbr>')}</span><span class="cs-adjustment-label__full" aria-hidden="true">${escapeHtml(actionLabel)}</span></button>`;
            const positionText = value => value ? `${value.date || ''} ${(value.sections || []).join('、')}节 ${value.room || ''}`.trim() : '无补课去向';
            const detail = `原安排：${positionText(change.original)}。${change.kind === 'cancel' ? '停课申请尚待批准，不自动安排补课。' : `拟安排：${positionText(change.proposed)}。`}当前为待审核，正式安排以审批及课表生效为准。`;
            comparison = `<div class="cs-adjustment-details" hidden>${escapeHtml(detail)}</div>`;
        }
        const mainTag = change && href ? 'a' : 'div';
        const mainAttrs = change ? href ? ` href="${escapeHtml(href)}" aria-label="${escapeHtml(lesson.course_name + ' · ' + fullRoom)}"` : ' tabindex="0"' : '';
        const content = `<div class="cs-lesson__surface"><${mainTag} class="cs-lesson__main"${mainAttrs}><strong class="cs-lesson__title">${escapeHtml(lesson.course_name)}</strong><div class="cs-lesson__details">${details}</div></${mainTag}>${comparison}<div class="cs-lesson__footer"><span class="cs-lesson__room" title="${escapeHtml(fullRoom)}"><span class="cs-lesson__room-short">${escapeHtml(shortRoom)}</span><span class="cs-lesson__room-full">教室 ${escapeHtml(fullRoom)}</span></span>${button}</div></div>`;
        const classes = `cs-lesson cs-lesson--${expanded ? 'cell' : 'mini'}${change ? ' cs-lesson--pending' : ''}${proposed ? ' cs-lesson--proposed' : ''}${isCreate ? ' cs-lesson--create' : ''}`;
        const keyAttr = ` data-event-key="${escapeHtml(eventKey)}"`;
        if (!expanded) return `<div class="${classes}"${keyAttr} style="--cs-accent:${accent};${gridPos}" title="${escapeHtml(`${lesson.course_name} · ${fullRoom}`)}">${content}</div>`;
        const tag = !change && href ? 'a' : 'div';
        const outerAttrs = !change ? href ? ` href="${escapeHtml(href)}"` : ' tabindex="0"' : '';
        return `<div class="cs-lesson-slot"${lane.count > 1 ? ` data-cs-lanes="${lane.count}"` : ''} style="${gridPos}"><${tag} class="${classes}"${outerAttrs}${keyAttr} style="--cs-accent:${accent}">${content}</${tag}></div>`;
    }

    /**
     * 纵轴节次行高自适应（课表整体高度固定）：
     * - 每一行（1-11 节）都可见，都有最小高度，确保行内说明可读；
     * - 有课行给足权重（fr）分摊剩余空间，尽量展示卡片内容；
     * - 空堂行压到很小（仅够显示节次序号）让位给有课行。
     *
     * 放大视图给有课行一个真实 px 下限（34px，≈课程名 + 一行说明）：因为
     * 网格已改为绝对定位、拿到确定高度，即便 11 行全有课，11×34 + 表头 +
     * 间隙 ≈ 440px 仍小于容器，不会溢出（不再有历史上"下面看不见了"的
     * 问题）。迷你 3D 卡很小，有课行仍用 0 下限避免撑破 380px 卡片。
     */
    function buildRowSizes(week, { minSection, maxSection, expanded }) {
        const sectionCount = maxSection - minSection + 1;
        const hasLesson = new Array(sectionCount).fill(false);
        (week?.lessons || []).forEach((lesson) => {
            const sections = lesson.sections || [];
            if (!sections.length) return;
            const start = Math.max(minSection, sections[0]);
            const end = Math.min(maxSection, sections[sections.length - 1]);
            for (let section = start; section <= end; section += 1) {
                hasLesson[section - minSection] = true;
            }
        });
        const emptyRow = expanded ? 'minmax(20px, 0.45fr)' : 'minmax(12px, 0.35fr)';
        const lessonRow = expanded ? 'minmax(34px, 3fr)' : 'minmax(0, 2.4fr)';
        return hasLesson
            .map((occupied) => (occupied ? lessonRow : emptyRow))
            .join(' ');
    }

    function renderWeekGrid(week, { expanded = false } = {}) {
        const range = state.overview?.section_range || { min: 1, max: 11 };
        const minSection = Math.max(1, Number(range.min) || 1);
        const maxSection = Math.max(minSection, Number(range.max) || 11);
        const sectionCount = maxSection - minSection + 1;
        const rowSizes = buildRowSizes(week, { minSection, maxSection, expanded });
        const headerRow = expanded ? '34px' : '24px';
        const labelCol = expanded ? '54px' : '30px';
        // 放大视图额外加一列时段（早读/上午/下午/晚上）纵向标签。
        const columnBase = expanded ? 3 : 2;
        const columnsTemplate = expanded
            ? `30px ${labelCol} repeat(7, minmax(0, 1fr))`
            : `${labelCol} repeat(7, minmax(0, 1fr))`;

        // 仅"本周"卡片高亮今天所在列；周六/日弱化。
        const todayColumn = week?.is_current ? todayRemoteWeekday() : 0;
        const dayHeads = ['一', '二', '三', '四', '五', '六', '日']
            .map((day, index) => {
                const weekday = index + 1;
                const classes = ['cs-grid__day'];
                if (weekday >= 6) classes.push('cs-grid__day--weekend');
                if (weekday === todayColumn) classes.push('cs-grid__day--today');
                const todayTag = weekday === todayColumn && expanded ? '<small>今天</small>' : '';
                return `<div class="${classes.join(' ')}" style="grid-column:${index + columnBase};grid-row:1;">周${day}${todayTag}</div>`;
            })
            .join('');
        const sectionLabels = Array.from({ length: sectionCount }, (_, offset) => {
            const section = minSection + offset;
            const band = sectionBand(section);
            return `<div class="cs-grid__section cs-grid__section--${band}" style="grid-column:${columnBase - 1};grid-row:${offset + 2};">${section}</div>`;
        }).join('');
        const cellBackgrounds = Array.from({ length: sectionCount * 7 }, (_, cell) => {
            const rowOffset = Math.floor(cell / 7);
            const band = sectionBand(minSection + rowOffset);
            const row = rowOffset + 2;
            const weekday = (cell % 7) + 1;
            const column = weekday - 1 + columnBase;
            const classes = ['cs-grid__cellbg', `cs-grid__cellbg--${band}`];
            if (weekday >= 6) classes.push('cs-grid__cellbg--weekend');
            if (weekday === todayColumn) classes.push('cs-grid__cellbg--today');
            return `<div class="${classes.join(' ')}" style="grid-column:${column};grid-row:${row};"></div>`;
        }).join('');
        let bandBlocks = '';
        if (expanded) {
            const blocks = [];
            let blockStart = 0;
            for (let offset = 1; offset <= sectionCount; offset += 1) {
                const prevBand = sectionBand(minSection + blockStart);
                const band = offset < sectionCount ? sectionBand(minSection + offset) : '';
                if (offset === sectionCount || band !== prevBand) {
                    blocks.push(
                        `<div class="cs-grid__band cs-grid__band--${prevBand}"
                              style="grid-column:1;grid-row:${blockStart + 2} / span ${offset - blockStart};">${BAND_LABELS[prevBand]}</div>`,
                    );
                    blockStart = offset;
                }
            }
            bandBlocks = blocks.join('');
        }

        const laneMap = scheduleLessonLanes(week?.lessons || []);
        const lessons = (week?.lessons || [])
            .map((lesson, index) => lessonHtml(lesson, { expanded, minSection, maxSection, columnBase, lane: laneMap.get(index), eventKey: lesson.event_key || `${week.week_index}:${lesson.id || index}:${index}` }))
            .join('');
        const corner = expanded
            ? '<div class="cs-grid__corner" style="grid-column:1 / span 2;grid-row:1;">节</div>'
            : '<div class="cs-grid__corner" style="grid-column:1;grid-row:1;">节</div>';

        return `
        <div class="cs-grid ${expanded ? 'cs-grid--expanded' : ''} ${[...laneMap.values()].some(value => value.count > 1) ? 'cs-grid--overlaps' : ''}"
             style="grid-template-columns:${columnsTemplate};grid-template-rows:${headerRow} ${rowSizes};">
            ${corner}
            ${dayHeads}
            ${bandBlocks}
            ${sectionLabels}
            ${cellBackgrounds}
            ${lessons}
        </div>`;
    }

    /* ---------------- 3D 卡片堆栈 ---------------- */

    function renderDeck() {
        if (!refs.stage) return;
        refs.stage.querySelectorAll('.cs-card, .cs-empty').forEach((node) => node.remove());
        const weeks = state.overview?.weeks || [];

        if (!weeks.length) {
            closeExpanded();
            clearLessonPreviews();
            refs.expandBody.replaceChildren();
            renderedExpandedWeek = null;
            lineConnections = [];
            previousLineRoutes = [];
            lastLineMap = null;
            lastLineGeometry = '';
            const empty = document.createElement('div');
            empty.className = 'cs-empty';
            empty.innerHTML = config.emptyHtml(state.overview);
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
                    <span>${week.date_range_label ? `${escapeHtml(week.date_range_label)} · ` : ''}${week.lesson_count} 节安排 · ${week.total_hours} 课时</span>
                    <span class="cs-card__badge ${week.is_current ? 'is-current' : ''}">${week.is_current ? '本周' : escapeHtml(week.label)}</span>
                </div>
                <div class="cs-card__body">${renderWeekGrid(week)}${weekEmptyMarkHtml(week)}${options.compactSummary ? renderCompactWeek(week) : ''}</div>`;
            refs.stage.appendChild(card);
        });
        layoutDeck();
    }

    function renderCompactWeek(week) {
        const lessons = week.lessons || [];
        return `<div class="cs-card__compact">${lessons.length ? `<ol>${lessons.slice(0, 3).map(lesson => `<li><span>${escapeHtml(lesson.weekday_label)} · ${escapeHtml(lesson.section_label)} · ${escapeHtml(compactClassroomName(lesson.classroom || lesson.classroom_short || '教室待定'))}</span><strong>${escapeHtml(lesson.course_name)}</strong></li>`).join('')}</ol><small>${lessons.length > 3 ? `还有 ${lessons.length - 3} 次安排 · ` : ''}点击放大查看整周课表</small>` : '<strong>这一周没有已排定课程</strong><small>其他课堂可从“全部课程”进入</small>'}</div>`;
    }

    function layoutDeck() {
        const cards = refs.stage ? refs.stage.querySelectorAll('.cs-card') : [];
        cards.forEach((card) => {
            const index = Number(card.dataset.weekIndex);
            const offset = index - state.activeWeekIndex;
            card.classList.toggle('is-active', offset === 0);
            if (offset < -1 || offset > 5) {
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
                // 上浮幅度收窄（-24px/张），避免后排卡片顶出面板遮住标题。
                transform = `translate(-50%, -50%) translate3d(${offset * 72}px, ${offset * -24}px, ${-offset * 170 + 60}px) rotateY(-7deg)`;
                opacity = Math.max(0.22, 1 - offset * 0.16);
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
                ? `${escapeHtml(active.label)}${active.is_current ? ' · 本周' : ''}<small>${active.date_range_label ? `${escapeHtml(active.date_range_label)} · ` : ''}${active.lesson_count} 节安排 · ${active.total_hours} 课时</small>`
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
        options.onWeekChange?.(active || null, state.activeWeekIndex);
    }

    function goToWeek(index) {
        const weeks = state.overview?.weeks || [];
        if (!weeks.length) return;
        const next = Math.min(Math.max(index, 0), weeks.length - 1);
        if (next === state.activeWeekIndex) return;
        state.activeWeekIndex = next;
        layoutDeck();
    }

    function announce(message, expanded = true) {
        refs.feedback.textContent = message;
        refs.expandFeedback.textContent = expanded ? message : '';
    }

    function focusLesson(eventKey, weekIndex) {
        const weeks = state.overview?.weeks || [];
        const index = weeks.findIndex(week => (!weekIndex || Number(week.week_index) === Number(weekIndex))
            && week.lessons?.some(lesson => lesson.event_key === eventKey));
        if (index < 0) { announce('对应课程未在当前筛选结果中显示，请核对筛选条件；已保留当前学期和筛选。'); return false; }
        goToWeek(index);
        const surface = state.expanded ? refs.expandBody : refs.stage.querySelector('.cs-card.is-active');
        const card = [...(surface?.querySelectorAll('[data-event-key]') || [])].find(node => node.dataset.eventKey === eventKey);
        if (!card) return false;
        if (state.expanded) card.scrollIntoView({ block: 'nearest', inline: 'center', behavior: 'instant' });
        container.querySelectorAll('.is-counterpart-focus').forEach(node => node.classList.remove('is-counterpart-focus'));
        refs.expand.querySelectorAll('.is-counterpart-focus').forEach(node => node.classList.remove('is-counterpart-focus'));
        card.classList.add('is-counterpart-focus');
        (card.querySelector('.cs-adjustment-label') || card.querySelector('a, [tabindex]') || card).focus({ preventScroll: true });
        announce(`已定位 ${weeks[index].label} 的对应课程。`);
        window.clearTimeout(highlightTimer);
        highlightTimer = window.setTimeout(() => card.classList.remove('is-counterpart-focus'), 3000);
        return true;
    }

    function handleChangeClick(event) {
        const button = event.target.closest('[data-csd-change]');
        if (!button) return false;
        event.preventDefault(); event.stopPropagation(); pendingTouchPreview = null;
        const key = button.dataset.csdChange;
        activateChange(key);
        return true;
    }

    function activateChange(key) {
        const lesson = state.overview?.weeks?.flatMap(week => week.lessons || []).find(item => item.event_key === key);
        const change = pendingScheduleChange(lesson);
        if (!change) return true;
        if (change.kind === 'move' && change.counterpart_event_key) {
            focusLesson(change.counterpart_event_key, change.counterpart_week_index);
        } else {
            if (!state.expanded) openExpanded();
            const cell = [...refs.expandBody.querySelectorAll('[data-event-key]')].find(node => node.dataset.eventKey === key);
            const detail = cell?.querySelector('.cs-adjustment-details');
            if (detail) {
                const open = detail.hidden;
                setChangeDetail(cell, open);
                if (open) { openLessonPreview(cell); positionLessonPreview(); }
                else closeLessonPreview();
                cell.querySelector('.cs-adjustment-label')?.focus({ preventScroll: true });
            }
        }
        return true;
    }

    /* ---------------- 放大视图 ---------------- */

    let previewCell = null;
    let pendingTouchPreview = null;
    // Chromium re-dispatches pointerover under a stationary cursor once the
    // expanded grid lays out beneath it; a hover preview must only follow a
    // real movement, otherwise the first Escape closes a preview nobody asked for.
    let hoverOrigin = null;
    let hoverArmed = true;
    let lastPointer = null;
    const trackPointer = (event) => { if (event.pointerType !== 'touch') lastPointer = { x: event.clientX, y: event.clientY }; };
    document.addEventListener('pointermove', trackPointer, { passive: true });
    document.addEventListener('pointerdown', trackPointer, { passive: true });
    const previewMotions = new Map();
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');

    function lessonFrame(cell) {
        return {
            rect: cell.getBoundingClientRect(),
            opacity: Number(getComputedStyle(cell.querySelector('.cs-lesson__main')).opacity),
        };
    }

    function cancelLessonMotion(cell) {
        const motion = previewMotions.get(cell);
        previewMotions.delete(cell);
        motion?.animations.forEach(animation => animation.cancel());
        cell.classList.remove('is-preview-moving');
    }

    /** The comparison belongs to the preview and shares its reveal/close phases. */
    function setChangeDetail(cell, open) {
        const detail = cell?.querySelector('.cs-adjustment-details');
        if (!detail) return;
        detail.hidden = !open;
        cell.querySelector('.cs-adjustment-label')?.setAttribute('aria-expanded', open ? 'true' : 'false');
    }

    function clearLessonPreviews() {
        previewCell = null;
        refs.expandBody.querySelectorAll('.cs-adjustment-details:not([hidden])').forEach((node) => setChangeDetail(node.closest('[data-event-key]'), false));
        for (const cell of [...previewMotions.keys()]) cancelLessonMotion(cell);
        refs.expandBody.querySelectorAll('.is-preview, .is-preview-closing').forEach((node) => {
            node.classList.remove('is-preview', 'is-preview-closing');
            delete node.dataset.previewState;
        });
    }

    function closeLessonPreview() {
        if (!previewCell) return;
        const cell = previewCell;
        previewCell = null;
        animateLessonPreview(cell, false);
    }

    function measureLessonPreview(cell) {
        const body = refs.expandBody;
        const slot = cell.parentElement;
        const bodyRect = body.getBoundingClientRect();
        const slotRect = slot.getBoundingClientRect();
        const scaleX = bodyRect.width / body.offsetWidth || 1;
        const scaleY = bodyRect.height / body.offsetHeight || 1;
        const availableWidth = Math.max(1, body.clientWidth - 24);
        const availableHeight = Math.max(1, body.clientHeight - 24);
        const baseWidth = Math.max(1, slot.clientWidth);
        const baseHeight = Math.max(1, slot.clientHeight);
        // Size the readable layout once, then fit ONE scale to both dimensions.
        // Content never changes the slot's aspect ratio; excess text scrolls.
        const desiredWidth = Math.min(380, availableWidth);
        cell.style.setProperty('--cs-preview-width', `${desiredWidth}px`);
        cell.style.setProperty('--cs-preview-height', `${baseHeight * desiredWidth / baseWidth}px`);
        const naturalHeight = cell.querySelector('.cs-lesson__surface').scrollHeight;
        const desiredScale = Math.max(1.25, desiredWidth / baseWidth, naturalHeight / baseHeight);
        const scale = Math.min(desiredScale, Math.min(600, availableWidth) / baseWidth, availableHeight / baseHeight);
        const width = baseWidth * scale;
        const height = baseHeight * scale;
        cell.style.setProperty('--cs-preview-width', `${width}px`);
        cell.style.setProperty('--cs-preview-height', `${height}px`);
        const slotLeft = (slotRect.left - bodyRect.left) / scaleX;
        const slotTop = (slotRect.top - bodyRect.top) / scaleY;
        const clamp = (value, min, max) => Math.max(min, Math.min(value, max));
        const left = clamp(slotLeft + (baseWidth - width) / 2, 12, body.clientWidth - width - 12);
        const top = clamp(slotTop + (baseHeight - height) / 2, 12, body.clientHeight - height - 12);
        cell.style.setProperty('--cs-preview-left', `${left - slotLeft}px`);
        cell.style.setProperty('--cs-preview-top', `${top - slotTop}px`);
    }

    function animateLessonPreview(cell, opening) {
        if (!cell.isConnected) return;
        // Capture the actual painted surface before cancellation, so reversal
        // continues from the current point rather than snapping to an endpoint.
        const from = lessonFrame(cell);
        const priorScroll = previewMotions.get(cell)?.openScroll;
        const openScroll = priorScroll || { left: cell.scrollLeft, top: cell.scrollTop };
        cancelLessonMotion(cell);
        cell.classList.remove('is-preview-closing');
        cell.classList.add('is-preview');
        if (opening) {
            setChangeDetail(cell, true);
            measureLessonPreview(cell);
        }
        const layout = cell.getBoundingClientRect();
        const target = opening ? layout : cell.parentElement.getBoundingClientRect();
        const bodyRect = refs.expandBody.getBoundingClientRect();
        const bodyScale = bodyRect.width / refs.expandBody.offsetWidth || 1;
        const transformFor = rect => `translate(${(rect.left - layout.left) / bodyScale}px, ${(rect.top - layout.top) / bodyScale}px) scale(${rect.width / layout.width})`;
        const moved = Math.abs(from.rect.width - target.width) + Math.abs(from.rect.height - target.height)
            + Math.abs(from.rect.left - target.left) + Math.abs(from.rect.top - target.top) > 1;
        const finish = () => {
            cancelLessonMotion(cell);
            cell.classList.remove('is-preview-closing');
            cell.classList.toggle('is-preview', opening);
            setChangeDetail(cell, opening);
            cell.scrollLeft = opening ? openScroll.left : 0;
            cell.scrollTop = opening ? openScroll.top : 0;
            cell.parentElement?.classList.toggle('is-preview', opening);
            if (opening) cell.dataset.previewState = 'open';
            else delete cell.dataset.previewState;
            scheduleChangeLines();
        };
        if (reducedMotion.matches || typeof cell.animate !== 'function' || !moved) { finish(); return; }
        if (!opening) {
            cell.classList.remove('is-preview');
            cell.classList.add('is-preview-closing');
        }
        cell.classList.add('is-preview-moving');
        cell.dataset.previewState = opening ? 'opening' : 'closing';
        const fadeOut = opening || from.opacity < .01 ? 0 : 70;
        const duration = opening ? 190 : 150;
        const motionTiming = { duration, delay: fadeOut, easing: 'cubic-bezier(.2,.75,.25,1)', fill: 'both' };
        const animations = [cell.animate([
            { transform: transformFor(from.rect), transformOrigin: '0 0', overflow: 'hidden' },
            { transform: transformFor(target), transformOrigin: '0 0', overflow: 'hidden' },
        ], motionTiming)];
        // No per-letter/line position or font-size interpolation: all text stays
        // invisible during geometric motion, then resolves quickly at full size.
        for (const part of cell.querySelector('.cs-lesson__surface').children) {
            animations.push(part.animate(opening ? [{ opacity: 0 }, { opacity: 1 }] : [{ opacity: from.opacity }, { opacity: 0 }], {
                duration: opening ? 80 : fadeOut || 1,
                delay: opening ? duration : 0,
                easing: 'linear', fill: 'both',
            }));
        }
        const motion = { animations, openScroll };
        previewMotions.set(cell, motion);
        scheduleChangeLines();
        Promise.allSettled(animations.map(animation => animation.finished)).then(() => {
            if (previewMotions.get(cell) !== motion) return;
            finish();
        });
    }

    function positionLessonPreview() {
        scheduleChangeLines();
        if (previewCell?.isConnected && state.expanded) animateLessonPreview(previewCell, true);
    }

    function onReducedMotionChange() {
        if (!reducedMotion.matches) return;
        for (const cell of [...previewMotions.keys()]) animateLessonPreview(cell, cell === previewCell);
    }

    function openLessonPreview(cell) {
        if (!cell || !state.expanded) return;
        if (cell !== previewCell) {
            closeLessonPreview();
            previewCell = cell;
            cell.parentElement.classList.add('is-preview');
            if (!previewMotions.has(cell)) { cell.scrollLeft = 0; cell.scrollTop = 0; }
            animateLessonPreview(cell, true);
        }
    }

    // 页面缩放、旋转屏幕、对话框标题换行都会改变可用空间。
    const previewResizeObserver = typeof ResizeObserver === 'function'
        ? new ResizeObserver(positionLessonPreview) : null;
    previewResizeObserver?.observe(refs.expandBody);

    function scheduleChangeLines() {
        if (destroyed || lineFrame !== null || !state.expanded) return;
        lineFrame = window.requestAnimationFrame(() => {
            lineFrame = null;
            renderChangeLines();
            // Read the browser's actual animated box each frame. No polling is
            // left running after the card/dialog reaches its resting state.
            const dialogMoving = refs.expand.querySelector('.cs-expand__card')?.getAnimations()
                .some(animation => animation.playState === 'running');
            if (previewMotions.size || dialogMoving) scheduleChangeLines();
        });
    }

    function renderChangeLines() {
        if (destroyed || !state.expanded || refs.expand.hidden) return;
        const map = refs.expandBody.querySelector('.cs-change-map');
        const svg = map?.querySelector('.cs-change-lines');
        if (!map || !svg || !map.offsetWidth || !map.offsetHeight) return;
        const connections = lineConnections;
        const rect = map.getBoundingClientRect();
        const scaleX = rect.width / map.offsetWidth, scaleY = rect.height / map.offsetHeight;
        const obstacles = [...map.querySelectorAll('.cs-lesson--cell')].map(card => {
            const bounds = card.getBoundingClientRect();
            return { key: card.dataset.eventKey,
                left: (bounds.left - rect.left) / scaleX, right: (bounds.right - rect.left) / scaleX,
                top: (bounds.top - rect.top) / scaleY, bottom: (bounds.bottom - rect.top) / scaleY };
        });
        if (lastLineMap !== map) { lastLineMap = map; lastLineGeometry = ''; previousLineRoutes = []; }
        const geometry = [map.offsetWidth, map.offsetHeight, Boolean(previewCell || previewMotions.size), ...obstacles.flatMap(box =>
            [box.key, ...[box.left, box.top, box.right, box.bottom].map(value => Math.round(value * 100))])].join('|');
        if (geometry === lastLineGeometry) return;
        lastLineGeometry = geometry;
        const routes = routeScheduleChanges({ width: map.offsetWidth, height: map.offsetHeight, obstacles, connections, previousRoutes: previousLineRoutes });
        previousLineRoutes = routes;
        const focusedLine = svg.contains(document.activeElement)
            ? document.activeElement.closest('[data-change-key]')?.getAttribute('data-change-key') : null;
        const ns = 'http://www.w3.org/2000/svg';
        const element = (tag, attrs = {}, text = '') => {
            const node = document.createElementNS(ns, tag);
            for (const [name, value] of Object.entries(attrs)) node.setAttribute(name, String(value));
            if (text) node.textContent = text;
            return node;
        };
        const defs = element('defs');
        const nodes = [defs];
        const missing = [];
        svg.setAttribute('viewBox', `0 0 ${map.offsetWidth} ${map.offsetHeight}`);
        routes.forEach((route, index) => {
            const connection = connections.find(item => item.key === route.key);
            if (!connection) return;
            if (route.points.length < 2) { missing.push(connection.label); return; }
            const markerId = `${changeMapId}-arrow-${index}`;
            const marker = element('marker', { id: markerId, markerWidth: 12, markerHeight: 12, refX: 10, refY: 6, orient: 'auto', markerUnits: 'userSpaceOnUse', overflow: 'visible' });
            marker.append(element('path', { d: 'M 2 2 L 10 6 L 2 10', fill: 'none', stroke: connection.color, 'stroke-width': 2, 'stroke-linejoin': 'round', 'stroke-linecap': 'round' }));
            defs.append(marker);
            const group = element('g', { 'data-change-key': route.key, 'data-source-key': connection.sourceKey || '', 'data-target-key': connection.targetKey || '', 'data-boundary': ['incoming', 'outgoing'].includes(connection.direction) ? connection.direction : '', 'data-edge': connection.edge || '', 'aria-label': connection.title || connection.label });
            group.append(element('title', {}, connection.title || connection.label));
            group.append(element('path', { class: 'cs-change-line', d: roundedScheduleRoute(route.points, obstacles), stroke: connection.color, 'marker-end': `url(#${markerId})` }));
            group.append(element('circle', { class: 'cs-change-line-origin', cx: route.points[0].x, cy: route.points[0].y, r: 2.8, stroke: connection.color }));
            const placement = route.labelPlacement;
            if (placement) {
                const label = element('g', { class: 'cs-change-line-label', role: 'button', tabindex: 0,
                    transform: `translate(${placement.x} ${placement.y})${placement.vertical ? ' rotate(-90)' : ''}`,
                    'aria-label': [connection.label, connection.title].filter(Boolean).join('，'), style: `color:${connection.color}`,
                    ...(connection.jumpKey && connection.direction !== 'room' ? { 'data-csd-line-jump': connection.jumpKey, 'data-csd-line-week': connection.jumpWeek }
                        : { 'data-csd-line-detail': connection.sourceKey }) });
                const width = placement.vertical ? placement.height : placement.width;
                const height = placement.vertical ? placement.width : placement.height;
                label.append(element('rect', { x: -width / 2, y: -height / 2, width, height, rx: 6 }));
                label.append(element('text', { x: 0, y: 0 }, connection.label));
                group.append(label);
            } else missing.push(connection.label);
            nodes.push(group);
        });
        // Keep an explicit explanation when a packed layout has no safe label
        // corridor; never draw through another lesson to force an annotation.
        map.querySelector('.cs-change-line-fallback').textContent = missing.length
            ? previewCell || previewMotions.size ? '展开时部分连线或提示暂时隐藏；可收起卡片，或点击卡片标签查看对应安排。'
                : `部分连线空间有限：${[...new Set(missing)].join('；')}。可点卡片标签查看对应安排。` : '';
        svg.replaceChildren(...nodes);
        if (focusedLine) [...svg.querySelectorAll('[data-change-key]')]
            .find(node => node.getAttribute('data-change-key') === focusedLine)
            ?.querySelector('.cs-change-line-label')?.focus({ preventScroll: true });
    }

    function handleLineAction(event) {
        const label = event.target.closest('[data-csd-line-jump], [data-csd-line-detail]');
        if (!label) return false;
        event.preventDefault();
        if (label.dataset.csdLineJump) focusLesson(label.dataset.csdLineJump, Number(label.dataset.csdLineWeek));
        else activateChange(label.dataset.csdLineDetail);
        return true;
    }

    function onLineKeydown(event) {
        if (event.key === 'Enter' || event.key === ' ') handleLineAction(event);
    }

    function renderExpanded() {
        const weeks = state.overview?.weeks || [];
        const week = weeks[state.activeWeekIndex];
        if (!week || !refs.expandBody) return;
        clearLessonPreviews();
        pendingTouchPreview = null;
        renderedExpandedWeek = week;
        if (refs.expandTitle) refs.expandTitle.textContent = week.label + (week.is_current ? '（本周）' : '');
        if (refs.expandSub) {
            const termLabel = state.overview?.selected_term?.label || '';
            refs.expandSub.innerHTML = `${week.date_range_label ? `<span class="cs-expand__dates">${escapeHtml(week.date_range_label)}</span>` : ''}<small>${escapeHtml(termLabel)} · ${week.lesson_count} 节安排 · ${week.total_hours} 课时</small>`;
        }
        const grid = renderWeekGrid(week, { expanded: true });
        const connections = scheduleChangeConnections(state.overview, week);
        lineConnections = connections.map(connection => ({ ...connection,
            label: [connection.label, connection.boundaryLabel].filter(Boolean).join(' · '),
            color: changeColors.get(connection.key),
        }));
        refs.expandBody.innerHTML = connections.length
            ? `<div class="cs-change-map${grid.includes('cs-grid--overlaps') ? ' cs-change-map--overlaps' : ''}">${grid}<svg class="cs-change-lines" aria-label="从原安排指向新安排的变更连线"></svg><div class="cs-change-line-fallback" role="status"></div></div>`
            : grid + weekEmptyMarkHtml(week);
        scheduleChangeLines();
    }

    function openExpanded() {
        if (destroyed || !state.overview?.weeks?.length || !refs.expand) return;
        if (state.expanded) return;
        expandMotionGeneration += 1;
        if (refs.expand.hidden) expandedTrigger = document.activeElement;
        overlayCoordinator?.beforeOpen?.();
        state.expanded = true;
        if (refs.expand.hidden || renderedExpandedWeek !== state.overview.weeks[state.activeWeekIndex]) renderExpanded();
        refs.expand.hidden = false;
        refs.expand.inert = false;
        // Resolve the closed frame before opening; a reopening transition keeps
        // its current transform instead of reconstructing the course links.
        refs.expand.getBoundingClientRect();
        refs.expand.classList.add('is-open');
        hoverArmed = false;
        hoverOrigin = lastPointer ? { ...lastPointer } : null;
        scheduleChangeLines();
        notifyOverlay();
        refs.expandClose.focus({ preventScroll: true });
    }

    function closeExpanded({ restoreFocus = true, immediate = false } = {}) {
        if (!state.expanded) return;
        state.expanded = false;
        const generation = ++expandMotionGeneration;
        closeLessonPreview();
        refs.expand.classList.remove('is-open');
        refs.expand.inert = true;
        const finish = () => {
            if (state.expanded || generation !== expandMotionGeneration) return;
            clearLessonPreviews();
            refs.expand.hidden = true;
            notifyOverlay();
        };
        const animations = immediate || reducedMotion.matches ? [] : refs.expand.getAnimations({ subtree: true });
        if (!animations.length) finish();
        else Promise.allSettled(animations.map((animation) => animation.finished)).then(finish);
        notifyOverlay();
        if (restoreFocus) (expandedTrigger?.isConnected ? expandedTrigger : refs.stage)?.focus({ preventScroll: true });
    }

    /* ---------------- 学期下拉 ---------------- */

    function renderTermSelect() {
        if (!refs.termSelect) return;
        const terms = state.overview?.terms || [];
        const selected = state.overview?.selected_term;
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
        refs.termSelect.disabled = !terms.length;
    }

    /* ---------------- 事件 ---------------- */

    function onStageWheel(event) {
        const weeks = state.overview?.weeks || [];
        if (state.expanded || event.defaultPrevented || event.target.closest('button, input, select, textarea')) return;
        const now = Date.now();
        if (event.ctrlKey || event.metaKey || !event.deltaY) { wheelPending = 0; wheelGestureConsumed = false; return; }
        const freshGesture = now - wheelPendingAt > 200;
        if (freshGesture) { wheelPending = 0; wheelGestureConsumed = false; wheelLockUntil = 0; }
        wheelPendingAt = now;
        const intent = scheduleWheelIntent({ deltaY: event.deltaY, deltaMode: event.deltaMode, ctrlKey: event.ctrlKey, metaKey: event.metaKey, index: state.activeWeekIndex, length: weeks.length, pending: wheelPending });
        if (!intent.consume) {
            // Keep the inertia tail of an already-consumed gesture in the deck;
            // a new gesture at the boundary scrolls the page immediately.
            if (wheelGestureConsumed) event.preventDefault();
            wheelPending = 0;
            return;
        }
        event.preventDefault();
        if (now < wheelLockUntil) return;
        wheelPending = intent.pending;
        if (!intent.step) return;
        wheelGestureConsumed = true;
        wheelLockUntil = now + 480;
        goToWeek(state.activeWeekIndex + intent.step);
    }

    function onStageKeydown(event) {
        if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
            event.preventDefault();
            goToWeek(state.activeWeekIndex + 1);
        } else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
            event.preventDefault();
            goToWeek(state.activeWeekIndex - 1);
        } else if (event.key === 'Enter') {
            openExpanded();
        }
    }

    function onStageClick(event) {
        if (dragMoved) return;
        if (handleChangeClick(event)) return;
        const link = event.target.closest('a.cs-lesson__main');
        if (link) {
            if (!event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey) {
                event.preventDefault(); config.onNavigate(link.getAttribute('href'));
            }
            return;
        }
        const card = event.target.closest('.cs-card');
        if (card && card.classList.contains('is-active')) openExpanded();
    }

    /* 触摸 / 鼠标水平拖拽翻周（移动端没有滚轮）。每拖 90px 翻一周；
       拖动超过阈值后抑制随后的点击放大。 */
    let dragState = null;
    let dragMoved = false;

    function onStagePointerDown(event) {
        if (event.target.closest('a,button,input,select')) return;
        if (!state.overview?.weeks?.length || event.button > 0) return;
        dragState = { pointerId: event.pointerId, startX: event.clientX, startY: event.clientY, consumedSteps: 0 };
        dragMoved = false;
    }

    function onStagePointerMove(event) {
        if (!dragState || event.pointerId !== dragState.pointerId) return;
        const delta = event.clientX - dragState.startX;
        const vertical = event.clientY - dragState.startY;
        if (!dragMoved && Math.abs(vertical) > Math.max(10, Math.abs(delta))) { dragState = null; return; }
        if (!dragMoved && Math.abs(delta) > 8) {
            dragMoved = true;
            // 拖动确立后才捕获指针：轻点仍按 click 冒泡到卡片（放大）。
            try {
                refs.stage.setPointerCapture(event.pointerId);
            } catch { /* pointer capture unsupported */ }
        }
        const steps = Math.trunc(delta / 90);
        if (steps !== dragState.consumedSteps) {
            // 向右拖 = 翻回上一周（把前面的卡片拉回来）。
            goToWeek(state.activeWeekIndex - (steps - dragState.consumedSteps));
            dragState.consumedSteps = steps;
        }
    }

    function onStagePointerUp(event) {
        if (!dragState || event.pointerId !== dragState.pointerId) return;
        dragState = null;
        // 让 click 事件先读取 dragMoved，再复位。
        setTimeout(() => { dragMoved = false; }, 0);
    }

    function onExpandWheel(event) {
        // Expanded content owns its vertical scroll; week buttons remain available.
    }

    function onExpandBackdrop(event) {
        if (event.target === refs.expand) closeExpanded();
    }

    function onExpandBodyClick(event) {
        if (handleLineAction(event)) return;
        if (handleChangeClick(event)) return;
        const cell = event.target.closest('.cs-lesson--cell');
        const touch = pendingTouchPreview;
        pendingTouchPreview = null;
        if (cell && touch?.cell === cell && !touch.wasOpen) {
            event.preventDefault();
            openLessonPreview(cell);
            return;
        }
        const link = event.target.closest('a.cs-lesson__main') || cell?.closest('a.cs-lesson') || cell?.querySelector('a.cs-lesson__main');
        if (!link) return;
        if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        config.onNavigate(link.getAttribute('href'));
    }

    function onExpandPointerMove(event) {
        if (hoverArmed || event.pointerType === 'touch') return;
        if (hoverOrigin && Math.abs(event.clientX - hoverOrigin.x) + Math.abs(event.clientY - hoverOrigin.y) < 4) return;
        hoverArmed = true;
        const cell = event.target.closest('.cs-lesson--cell')
            || event.target.closest('.cs-lesson-slot')?.querySelector('.cs-lesson--cell');
        if (cell && !event.target.closest('[data-csd-change]')) openLessonPreview(cell);
    }

    function onLessonPointerOver(event) {
        if (event.pointerType === 'touch' || !hoverArmed) return;
        if (event.target.closest('[data-csd-change]')) return;
        const cell = event.target.closest('.cs-lesson--cell')
            || event.target.closest('.cs-lesson-slot')?.querySelector('.cs-lesson--cell');
        if (cell && !cell.contains(event.relatedTarget)) openLessonPreview(cell);
    }

    function onLessonPointerOut(event) {
        if (event.pointerType === 'touch' || !previewCell) return;
        if (event.target.closest('.cs-lesson-slot') !== previewCell.parentElement) return;
        if (previewCell.parentElement.contains(event.relatedTarget)) return;
        if (previewCell.contains(document.activeElement)) return;
        closeLessonPreview();
    }

    function onLessonPointerDown(event) {
        if (event.target.closest('[data-csd-change]')) { pendingTouchPreview = null; return; }
        const cell = event.target.closest('.cs-lesson--cell');
        pendingTouchPreview = event.pointerType === 'touch' && cell
            ? { cell, wasOpen: previewCell === cell } : null;
        if (!cell) closeLessonPreview();
    }

    function onLessonFocusIn(event) {
        // A tag's first tap/Enter must execute its command at a stable location.
        if (event.target.closest('[data-csd-change]')) return;
        openLessonPreview(event.target.closest('.cs-lesson--cell'));
    }

    function onLessonFocusOut(event) {
        if (previewCell && !previewCell.contains(event.relatedTarget)) closeLessonPreview();
    }

    function onDocumentKeydown(event) {
        if (!state.expanded || event.defaultPrevented || event.isComposing || event.keyCode === 229 || (overlayCoordinator && !overlayCoordinator.isTop())) return;
        if (event.key === 'Escape') {
            event.preventDefault();
            if (previewCell) closeLessonPreview();
            else closeExpanded();
        } else if (event.key === 'Tab') {
            const focusable = [...refs.expand.querySelectorAll('button:not(:disabled), a[href], [tabindex="0"]')];
            const index = focusable.indexOf(document.activeElement);
            if (event.shiftKey && index <= 0) {
                event.preventDefault();
                focusable[focusable.length - 1]?.focus();
            } else if (!event.shiftKey && (index < 0 || index === focusable.length - 1)) {
                event.preventDefault();
                focusable[0]?.focus();
            }
        }
    }

    function onTermSelectChange() {
        if (!config.onTermChange || !refs.termSelect) return;
        const [year, term] = String(refs.termSelect.value || '').split('|');
        config.onTermChange(year || '', term || '');
    }

    refs.stage.addEventListener('wheel', onStageWheel, { passive: false });
    refs.stage.addEventListener('keydown', onStageKeydown);
    refs.stage.addEventListener('click', onStageClick);
    refs.stage.addEventListener('pointerdown', onStagePointerDown);
    refs.stage.addEventListener('pointermove', onStagePointerMove);
    refs.stage.addEventListener('pointerup', onStagePointerUp);
    refs.stage.addEventListener('pointercancel', onStagePointerUp);
    refs.prevBtn.addEventListener('click', () => goToWeek(state.activeWeekIndex - 1));
    refs.nextBtn.addEventListener('click', () => goToWeek(state.activeWeekIndex + 1));
    refs.slider.addEventListener('input', () => goToWeek(Number(refs.slider.value) - 1));
    refs.expandPrev.addEventListener('click', () => { goToWeek(state.activeWeekIndex - 1); renderExpanded(); });
    refs.expandNext.addEventListener('click', () => { goToWeek(state.activeWeekIndex + 1); renderExpanded(); });
    refs.expandClose.addEventListener('click', closeExpanded);
    refs.expand.addEventListener('click', onExpandBackdrop);
    refs.expand.addEventListener('wheel', onExpandWheel, { passive: false });
    refs.expandBody.addEventListener('click', onExpandBodyClick);
    refs.expandBody.addEventListener('pointermove', onExpandPointerMove);
    refs.expandBody.addEventListener('keydown', onLineKeydown);
    refs.expandBody.addEventListener('pointerover', onLessonPointerOver);
    refs.expandBody.addEventListener('pointerout', onLessonPointerOut);
    refs.expandBody.addEventListener('pointerdown', onLessonPointerDown);
    refs.expandBody.addEventListener('focusin', onLessonFocusIn);
    refs.expandBody.addEventListener('focusout', onLessonFocusOut);
    refs.expand.addEventListener('transitionend', positionLessonPreview);
    window.addEventListener('resize', positionLessonPreview);
    reducedMotion.addEventListener('change', onReducedMotionChange);
    refs.termSelect?.addEventListener('change', onTermSelectChange);
    document.addEventListener('keydown', onDocumentKeydown);

    /* ---------------- 公开 API ---------------- */

    return {
        overlay: {
            getOwner: () => container,
            getRoot: () => refs.expand,
            getTrigger: () => expandedTrigger?.isConnected ? expandedTrigger : refs.stage,
            isExpanded: () => state.expanded && !destroyed,
            isPresent: () => !refs.expand.hidden && !destroyed,
            dismissTop(reason = 'programmatic') {
                if (destroyed) return false;
                if (reason === 'parent-destroyed' && !state.expanded && !refs.expand.hidden) {
                    expandMotionGeneration += 1;
                    clearLessonPreviews();
                    refs.expand.hidden = true;
                    notifyOverlay();
                    return true;
                }
                if (!state.expanded) return false;
                if (reason === 'parent-destroyed') closeExpanded({ restoreFocus: false, immediate: true });
                else if (previewCell) closeLessonPreview();
                else closeExpanded();
                return true;
            },
            connect(coordinator) {
                if (destroyed) throw new Error('Cannot connect a destroyed schedule');
                if (overlayCoordinator) throw new Error('Schedule overlay already has a coordinator');
                overlayCoordinator = coordinator;
                return () => { if (overlayCoordinator === coordinator) overlayCoordinator = null; };
            },
        },
        goToWeek,
        focusLesson,
        showAdjustment(eventKey) { if (!state.expanded) openExpanded(); return activateChange(eventKey); },
        openExpanded,
        setOverview(overview, { keepWeek = false } = {}) {
            const previousWeek = state.overview?.weeks?.[state.activeWeekIndex]?.week_index;
            state.overview = overview || null;
            if (state.overview) {
                state.overview = { ...state.overview, weeks: (state.overview.weeks || []).map(week => ({ ...week, ...countScheduleLessons(week.lessons || []) })) };
                const term = state.overview.selected_term || {};
                const colorScope = `${term.year || ''}|${term.term || ''}`;
                changeColors = scheduleChangeColors(state.overview, changeColorsByTerm.get(colorScope));
                changeColorsByTerm.set(colorScope, changeColors);
                const sync = state.overview.sync_state;
                const pending = state.overview.weeks.reduce((sum, week) => sum + week.proposed_count, 0);
                const warnings = state.overview.warnings || sync?.warnings || [];
                announce([...new Set([state.overview.message, pending ? `${pending} 项待审预测不计入正式课时` : '', Array.isArray(warnings) ? warnings.map(item => typeof item === 'string' ? item : item.message || '').filter(Boolean).join('；') : ''].filter(Boolean))].join(' · '), false);
            } else announce('');
            const weeks = state.overview?.weeks || [];
            // 打开定位：后端 focus_week（本周 / 假期→上学期最后教学周 / 未开学→第1周）
            // 优先，其次"本周"标记。
            const focusWeek = Number(state.overview?.selected_term?.focus_week) || 0;
            let nextIndex = focusWeek > 0
                ? weeks.findIndex((week) => week.week_index === focusWeek)
                : -1;
            if (nextIndex < 0) {
                nextIndex = weeks.findIndex((week) => week.is_current);
            }
            if (keepWeek && previousWeek) {
                const kept = weeks.findIndex((week) => week.week_index === previousWeek);
                if (kept >= 0) nextIndex = kept;
            }
            state.activeWeekIndex = nextIndex >= 0 ? nextIndex : 0;
            renderTermSelect();
            renderDeck();
        },
        getActiveWeekIndex() {
            return state.activeWeekIndex;
        },
        destroy() {
            if (destroyed) return;
            destroyed = true;
            state.expanded = false;
            overlayCoordinator?.onDestroy?.();
            overlayCoordinator = null;
            window.cancelAnimationFrame(lineFrame);
            previousLineRoutes = [];
            changeColorsByTerm.clear();
            window.clearTimeout(highlightTimer);
            expandMotionGeneration += 1;
            clearLessonPreviews();
            previewResizeObserver?.disconnect();
            window.removeEventListener('resize', positionLessonPreview);
            reducedMotion.removeEventListener('change', onReducedMotionChange);
            document.removeEventListener('keydown', onDocumentKeydown);
            document.removeEventListener('pointermove', trackPointer);
            document.removeEventListener('pointerdown', trackPointer);
            expand.remove();
            container.classList.remove('cs-deck');
            container.innerHTML = '';
        },
    };
}
