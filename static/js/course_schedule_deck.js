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

const COURSE_PALETTE = [
    '#4f46e5', '#0ea5e9', '#059669', '#d97706', '#db2777',
    '#7c3aed', '#0891b2', '#65a30d', '#ea580c', '#e11d48',
];

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

const DECK_CSS = `
.cs-deck { display: grid; gap: 12px; }
/* 头部悬于后排堆叠卡片之上，避免被 Flip3D 上浮的卡片遮住 */
.cs-deck-head { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; position: relative; z-index: 520; }
.cs-deck-head__copy h3 { margin: 0; font-size: 1.05rem; font-weight: 800; color: var(--text-primary, #0f172a); }
.cs-deck-head__copy p { margin: 0; font-size: 0.78rem; color: var(--text-muted, #64748b); }
.cs-deck-term {
    min-width: 190px;
    padding: 8px 12px;
    border: 1px solid rgba(148, 163, 184, 0.4);
    border-radius: 10px;
    background: #fff;
    font-size: 0.86rem;
    font-weight: 700;
    color: var(--text-primary, #0f172a);
}
.cs-deck-nav { margin-left: auto; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.cs-deck-nav__btn {
    width: 34px; height: 34px;
    border-radius: 50%;
    border: 1px solid rgba(148, 163, 184, 0.4);
    background: #fff;
    color: var(--text-secondary, #334155);
    font-size: 1rem;
    cursor: pointer;
    display: inline-flex; align-items: center; justify-content: center;
}
.cs-deck-nav__btn:hover { border-color: #6366f1; color: #4f46e5; }
.cs-deck-nav__btn:disabled { opacity: 0.4; cursor: default; }
.cs-week-indicator { font-size: 0.86rem; font-weight: 800; color: #312e81; min-width: 120px; text-align: center; }
.cs-week-indicator small { display: block; font-weight: 600; color: var(--text-muted, #64748b); font-size: 0.7rem; }
.cs-deck-slider { width: 180px; accent-color: #6366f1; }

.cs-stage {
    position: relative;
    /* 后排 3D 投影只能留在画布内，避免窄屏出现整页横向滚动。
       clip 不创建滚动容器；头部控件和独立整周对话框不受此裁切影响。 */
    overflow: clip;
    height: 460px;
    perspective: 1500px;
    perspective-origin: 50% 38%;
    border-radius: 16px;
    background:
        radial-gradient(1200px 400px at 70% -10%, rgba(99, 102, 241, 0.14), transparent 60%),
        radial-gradient(900px 380px at 10% 110%, rgba(14, 165, 233, 0.12), transparent 55%),
        linear-gradient(180deg, #eef2ff 0%, #f8fafc 100%);
    border: 1px solid rgba(148, 163, 184, 0.18);
    touch-action: pan-y;
}
.cs-stage__hint {
    position: absolute;
    left: 14px; bottom: 10px;
    z-index: 400;
    font-size: 0.72rem;
    color: var(--text-muted, #64748b);
    background: rgba(255, 255, 255, 0.78);
    border-radius: 999px;
    padding: 4px 12px;
    pointer-events: none;
}

.cs-card {
    position: absolute;
    left: 50%; top: 50%;
    width: min(680px, 82%);
    height: 380px;
    border-radius: 14px;
    background: rgba(255, 255, 255, 0.97);
    border: 1px solid rgba(148, 163, 184, 0.35);
    box-shadow: 0 22px 44px rgba(30, 41, 59, 0.22);
    transition: transform 0.5s cubic-bezier(0.22, 0.8, 0.3, 1), opacity 0.4s ease;
    transform-style: preserve-3d;
    overflow: hidden;
    display: grid;
    grid-template-rows: auto 1fr;
    will-change: transform, opacity;
}
.cs-card[hidden] { display: none; }
.cs-card__bar {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 10px 16px;
    background: linear-gradient(120deg, #4f46e5, #6366f1 55%, #0ea5e9);
    color: #fff;
}
.cs-card__bar strong { font-size: 0.98rem; font-weight: 900; letter-spacing: 0.04em; }
.cs-card__bar span { font-size: 0.74rem; opacity: 0.92; font-weight: 700; }
.cs-card__badge {
    margin-left: auto;
    font-size: 0.7rem;
    font-weight: 900;
    background: rgba(255, 255, 255, 0.22);
    border-radius: 999px;
    padding: 3px 10px;
}
.cs-card__badge.is-current { background: #fbbf24; color: #713f12; }
.cs-card__body { padding: 10px 12px 12px; min-height: 0; position: relative; }
.cs-card.is-active { cursor: zoom-in; }
.cs-card.is-active:hover { box-shadow: 0 28px 56px rgba(30, 41, 59, 0.32); }
/* 无排课周的水印 */
.cs-week-empty-mark {
    position: absolute;
    inset: 0;
    display: grid;
    place-items: center;
    pointer-events: none;
    font-size: clamp(1.2rem, 4vw, 2rem);
    font-weight: 900;
    letter-spacing: 0.3em;
    color: rgba(100, 116, 139, 0.18);
    transform: rotate(-8deg);
    user-select: none;
}

/* ---- 课表网格（迷你卡片与放大视图共用） ---- */
/* 网格用绝对定位铺满 body 的内容区（inset 精确等于各 body 的 padding）。
   这样网格拿到一个明确的高度（body 内容盒），grid-template-rows 的 fr 就
   按这个真实高度定轨、绝不溢出。之前用 height:100% 或 flex 都失败：前者在
   border-box 下把 padding 算进高度、后者 flex-basis 取内容高（如 745px）作
   定轨基准却渲染在被压缩的实际盒（605px）里，最后几行按错误高度溢出被裁
   （"挤压的下面看不见了"）。绝对定位 + 明确 inset 从根上消除这个歧义。 */
.cs-grid { display: grid; position: absolute; gap: 3px; overflow: hidden; }
.cs-card__body > .cs-grid { inset: 10px 12px 12px; }
.cs-expand__body > .cs-grid { inset: 16px 20px 20px; }
.cs-grid__corner, .cs-grid__day, .cs-grid__section {
    display: flex; align-items: center; justify-content: center;
    font-weight: 800;
    color: var(--text-muted, #64748b);
    background: rgba(148, 163, 184, 0.1);
    border-radius: 6px;
    font-size: 0.68rem;
}
.cs-grid__cellbg { background: rgba(148, 163, 184, 0.06); border-radius: 6px; }
/* 周末列弱化、今天列强调 */
.cs-grid__day--weekend { color: rgba(100, 116, 139, 0.6); background: rgba(148, 163, 184, 0.06); }
.cs-grid__cellbg--weekend { filter: saturate(0.35) opacity(0.75); }
.cs-grid__day--today { background: #4f46e5; color: #fff; box-shadow: 0 4px 10px rgba(79, 70, 229, 0.35); }
.cs-grid__day--today small { font-weight: 700; opacity: 0.9; margin-left: 4px; }
.cs-grid__cellbg--today { background-image: linear-gradient(rgba(99, 102, 241, 0.12), rgba(99, 102, 241, 0.12)); }
/* 早读 / 上午 / 下午 / 晚上分区背景 */
.cs-grid__cellbg--dawn { background: rgba(251, 191, 36, 0.12); }
.cs-grid__cellbg--am { background: rgba(14, 165, 233, 0.09); }
.cs-grid__cellbg--pm { background: rgba(99, 102, 241, 0.09); }
.cs-grid__cellbg--eve { background: rgba(51, 65, 85, 0.12); }
.cs-grid__section--dawn { background: rgba(251, 191, 36, 0.2); color: #92400e; }
.cs-grid__section--am { background: rgba(14, 165, 233, 0.16); color: #075985; }
.cs-grid__section--pm { background: rgba(99, 102, 241, 0.16); color: #3730a3; }
.cs-grid__section--eve { background: rgba(51, 65, 85, 0.2); color: #1e293b; }
.cs-grid__band {
    display: flex; align-items: center; justify-content: center;
    border-radius: 6px;
    font-weight: 900;
    font-size: 0.72rem;
    letter-spacing: 0.24em;
    writing-mode: vertical-lr;
    text-orientation: upright;
}
.cs-grid__band--dawn { background: rgba(251, 191, 36, 0.24); color: #92400e; }
.cs-grid__band--am { background: rgba(14, 165, 233, 0.18); color: #075985; }
.cs-grid__band--pm { background: rgba(99, 102, 241, 0.18); color: #3730a3; }
.cs-grid__band--eve { background: rgba(51, 65, 85, 0.24); color: #f8fafc; }
/* Glass is applied to the surface, never as opacity on its text. The proposed
   arrangement retains an opaque white tint so underlying lessons cannot bleed. */
.cs-lesson {
    --cs-radius: 14px;
    border-radius: var(--cs-radius); color: #fff; background: transparent;
    min-width: 0; min-height: 0; padding: 0; box-sizing: border-box;
    text-decoration: none; overflow: hidden;
    backdrop-filter: blur(14px) saturate(1.15);
    -webkit-backdrop-filter: blur(14px) saturate(1.15);
}
.cs-lesson__surface {
    position: relative; display: grid; grid-template-rows: minmax(0, 1fr) auto;
    width: 100%; height: 100%; min-width: 0; min-height: 0; box-sizing: border-box;
    border-radius: inherit; padding: 7px 8px; gap: 4px; overflow: hidden;
    background-color: var(--cs-accent, #6366f1);
    background-image: linear-gradient(145deg, rgba(255,255,255,.20), rgba(255,255,255,.03) 48%, rgba(255,255,255,.10));
    box-shadow: inset 0 1px 0 rgba(255,255,255,.42), inset 0 0 0 1px rgba(255,255,255,.22), 0 2px 6px rgba(15,23,42,.08);
}
.cs-lesson__main, .cs-lesson__main:link, .cs-lesson__main:visited {
    display: block; align-self: center; min-width: 0; min-height: 0;
    color: inherit; text-decoration: none; overflow: hidden;
}
.cs-lesson__title {
    display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2;
    font-size: .86rem; line-height: 1.3; font-weight: 850; margin: 0;
    min-width: 0; overflow: hidden; overflow-wrap: anywhere; white-space: normal;
}
.cs-lesson__details { display: none; opacity: 0; min-width: 0; }
.cs-lesson__details > span { display: block; font-size: .82rem; line-height: 1.55; overflow-wrap: anywhere; }
.cs-lesson__link-hint { font-weight: 750; }
.cs-lesson__footer { position: relative; display: grid; grid-template-columns: minmax(0, 1fr); align-items: end; min-width: 0; gap: 5px; }
.cs-lesson__footer:has(.cs-adjustment-label) { grid-template-columns: minmax(0, 1fr) auto; }
.cs-lesson__room {
    display: block; min-width: 0; font-size: .68rem; line-height: 1.35;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap; opacity: 1;
}
.cs-lesson__room-short, .cs-lesson__room-full { font: inherit; line-height: inherit; }
.cs-lesson__room-full { display: none; }
.cs-adjustment-label {
    position: relative; justify-self: end; align-self: end; z-index: 2;
    max-width: 100%; min-width: 0; min-height: 23px; box-sizing: border-box;
    border: 1px solid rgba(255,255,255,.62); border-radius: 10px;
    padding: 3px 6px; color: inherit; font: 750 .67rem/1.25 Arial,sans-serif;
    background: linear-gradient(145deg, rgba(255,255,255,.32), rgba(255,255,255,.12));
    backdrop-filter: blur(10px) saturate(1.3); -webkit-backdrop-filter: blur(10px) saturate(1.3);
    box-shadow: inset 0 1px 0 rgba(255,255,255,.45), 0 2px 5px rgba(15,23,42,.12);
    text-align: center; white-space: nowrap; cursor: pointer;
}
.cs-adjustment-label > span { font: inherit; line-height: inherit; }
.cs-adjustment-label__full { display: none; }
.cs-adjustment-label:is(:hover,:focus-visible) { background-color: rgba(255,255,255,.18); }
.cs-adjustment-label:active { box-shadow: inset 0 1px 4px rgba(15,23,42,.2); }
.cs-lesson--mini { container: cs-lesson / size; --cs-radius: 11px; backdrop-filter: none; -webkit-backdrop-filter: none; }
.cs-lesson--mini .cs-lesson__surface { padding: 4px 5px; gap: 2px; }
.cs-lesson--mini .cs-lesson__title { font-size: .76rem; }
.cs-lesson--mini .cs-lesson__room { font-size: .64rem; }
.cs-lesson--mini .cs-adjustment-label { min-height: 19px; padding: 2px 4px; font-size: .6rem; border-radius: 8px; backdrop-filter: none; -webkit-backdrop-filter: none; }
.cs-grid--expanded { overflow: visible; }
.cs-grid--expanded .cs-grid__corner,
.cs-grid--expanded .cs-grid__day,
.cs-grid--expanded .cs-grid__section { font-size: .84rem; }
.cs-lesson-slot { position: relative; min-width: 0; min-height: 0; container: cs-lesson / size; }
.cs-lesson--cell { position: absolute; top: 0; left: 0; width: 100%; height: 100%; z-index: 1; }
a.cs-lesson, a.cs-lesson:link, a.cs-lesson:visited, a.cs-lesson:hover, a.cs-lesson:focus { color: #fff; }
a.cs-lesson--cell { cursor: pointer; }
a.cs-lesson--create .cs-lesson__surface { box-shadow: inset 0 0 0 2px rgba(255,255,255,.55); }
a.cs-lesson--create .cs-lesson__link-hint { text-decoration: underline dashed; text-underline-offset: 3px; }
/* Pending frames keep a 4px gap; extremely narrow/short lanes use 2px below
   to retain readable title and action rows. */
.cs-lesson.cs-lesson--pending { border: 2px dashed var(--cs-accent); padding: 4px; }
.cs-lesson--pending .cs-lesson__surface { border-radius: max(6px, calc(var(--cs-radius) - 6px)); }
.cs-lesson--pending.cs-lesson--proposed { color: #172554; }
.cs-lesson--proposed .cs-lesson__surface { background-color: color-mix(in srgb, var(--cs-accent) 25%, #fff 75%); }
.cs-lesson--proposed .cs-adjustment-label { border-color: rgba(23,37,84,.3); background-color: rgba(255,255,255,.35); }
/* Title gets the flexible space. The footer keeps the action at the right edge;
   the lower-priority room yields its width instead of pushing the action away. */
.cs-lesson__footer:has(.cs-adjustment-label) .cs-adjustment-label { max-width: var(--cs-compact-action-width, 96px); }
@container cs-lesson (max-height: 92px) {
    .cs-lesson--cell:not(.is-preview,.is-preview-closing) .cs-lesson__surface { padding: 3px 5px; gap: 2px; }
}
@container cs-lesson (max-height: 72px) {
    .cs-lesson--cell:not(.is-preview,.is-preview-closing) .cs-lesson__title { -webkit-line-clamp: 1; }
    .cs-lesson--cell:not(.is-preview,.is-preview-closing) .cs-lesson__surface { padding: 1px 4px; gap: 1px; }
    .cs-lesson--cell:not(.is-preview,.is-preview-closing) .cs-adjustment-label { min-height: 20px; padding: 2px 4px; font-size: .64rem; }
}
@container cs-lesson (max-width: 100px) {
    .cs-lesson:not(.is-preview,.is-preview-closing) .cs-lesson__footer:has(.cs-adjustment-label) { grid-template-columns: minmax(0, 1fr); }
    .cs-lesson:not(.is-preview,.is-preview-closing) .cs-lesson__footer:has(.cs-adjustment-label) .cs-lesson__room { grid-area: 1/1; }
    .cs-lesson:not(.is-preview,.is-preview-closing) .cs-adjustment-label { grid-area: 1/1; max-width: 100%; white-space: normal; }
    .cs-lesson:not(.is-preview,.is-preview-closing) .cs-lesson__footer:has(.cs-adjustment-label) .cs-lesson__room { visibility: hidden; }
}
@container cs-lesson (max-height: 52px) {
    .cs-lesson--cell:not(.is-preview,.is-preview-closing) .cs-lesson__surface { grid-template-columns: minmax(0,1fr) auto; grid-template-rows: minmax(0,1fr); gap: 3px; }
    .cs-lesson--cell.cs-lesson--pending:not(.is-preview,.is-preview-closing) .cs-lesson__room { display: none; }
    .cs-lesson--cell:not(.is-preview,.is-preview-closing) .cs-lesson__room { max-width: 76px; }
    .cs-lesson--cell:not(.is-preview,.is-preview-closing) .cs-lesson__title { -webkit-line-clamp: 1; }
}
@container cs-lesson (max-width: 80px) and (min-height: 53px) and (max-height: 72px) {
    .cs-lesson--cell.cs-lesson--pending:not(.is-preview,.is-preview-closing) { padding: 2px; }
    .cs-lesson--cell:not(.is-preview,.is-preview-closing) .cs-lesson__surface { padding: 0 3px; gap: 1px; }
    .cs-lesson--cell:not(.is-preview,.is-preview-closing) .cs-adjustment-label { padding: 0 3px; line-height: 1.1; }
}
@container cs-lesson (max-height: 57px) {
    .cs-lesson--mini .cs-lesson__surface { padding: 1px 3px; gap: 1px; }
    .cs-lesson--mini.cs-lesson--pending .cs-lesson__title { -webkit-line-clamp: 1; }
}
@container cs-lesson (max-height: 39px) {
    .cs-lesson--mini .cs-lesson__title { -webkit-line-clamp: 1; }
}
@container cs-lesson (max-height: 30px) {
    .cs-lesson--mini .cs-lesson__surface { grid-template-columns: minmax(0,1fr) auto; grid-template-rows: minmax(0,1fr); }
    .cs-lesson--mini .cs-lesson__room { display: none; }
}
.cs-lesson-slot.is-preview { z-index: 60; }
.cs-lesson--cell:is(.is-preview,.is-preview-closing) {
    --cs-radius: 22px;
    top: var(--cs-preview-top, 0px); left: var(--cs-preview-left, 0px);
    width: max-content; height: auto;
    min-width: var(--cs-preview-min-width, 240px); max-width: var(--cs-preview-max-width, 420px);
    max-height: var(--cs-preview-max-height, 80vh);
    overflow-x: hidden; overflow-y: auto; overscroll-behavior: contain; touch-action: pan-y;
    box-shadow: 0 18px 42px rgba(15,23,42,.32), 0 2px 8px rgba(15,23,42,.15);
}
.cs-lesson--cell:is(.is-preview,.is-preview-closing) .cs-lesson__surface {
    display: flex; flex-direction: column; width: auto; height: auto; min-height: 0;
    padding: 13px 14px; gap: 10px; overflow: visible;
}
.cs-lesson--cell:is(.is-preview,.is-preview-closing) .cs-lesson__main { align-self: stretch; overflow: visible; }
.cs-lesson--cell:is(.is-preview,.is-preview-closing) .cs-lesson__title {
    display: block; font-size: 1.02rem; line-height: 1.3; overflow: visible; -webkit-line-clamp: unset;
}
.cs-lesson--cell:is(.is-preview,.is-preview-closing) .cs-lesson__details { display: grid; gap: 3px; opacity: 1; padding-top: 8px; }
.cs-lesson--cell:is(.is-preview,.is-preview-closing) .cs-lesson__footer { flex: 0 0 auto; grid-template-columns: minmax(0,1fr); gap: 10px; }
.cs-lesson--cell:is(.is-preview,.is-preview-closing) .cs-lesson__footer:has(.cs-adjustment-label) { grid-template-columns: minmax(0,1fr) minmax(0,1.15fr); }
.cs-lesson--cell:is(.is-preview,.is-preview-closing) .cs-lesson__room { font-size: .8rem; line-height: 1.5; white-space: normal; overflow: visible; overflow-wrap: anywhere; }
.cs-lesson--cell:is(.is-preview,.is-preview-closing) .cs-lesson__room-short { display: none; }
.cs-lesson--cell:is(.is-preview,.is-preview-closing) .cs-lesson__room-full { display: inline; }
.cs-lesson--cell:is(.is-preview,.is-preview-closing) .cs-adjustment-label { border-radius: 13px; font-size: .78rem; line-height: 1.4; padding: 6px 8px; max-width: 100%; text-align: left; white-space: normal; }
.cs-lesson--cell:is(.is-preview,.is-preview-closing) .cs-adjustment-label__short { display: none; }
.cs-lesson--cell:is(.is-preview,.is-preview-closing) .cs-adjustment-label__full { display: inline; }
.cs-adjustment-details { font-size: .75rem; line-height: 1.6; padding-top: 6px; border-top: 1px solid currentColor; }
.cs-adjustment-details[hidden], .cs-lesson:not(.is-preview,.is-preview-closing) .cs-adjustment-details { display: none; }
/* During the 120ms transition, independent text boxes travel inside the moving
   card. Their text is never scaled; anchors, buttons and event identities stay. */
.cs-lesson.is-preview-moving .cs-lesson__surface { position: static; display: block; height: 100%; padding: 0; }
.cs-lesson.is-preview-moving .cs-lesson__main, .cs-lesson.is-preview-moving .cs-lesson__footer { position: static; display: block; padding: 0; }
.cs-lesson.is-preview-moving .cs-lesson__title { display: block; -webkit-line-clamp: unset; overflow: hidden; }
.cs-lesson.is-preview-moving .cs-lesson__room-short, .cs-lesson.is-preview-moving .cs-lesson__room-full,
.cs-lesson.is-preview-moving .cs-adjustment-label__short, .cs-lesson.is-preview-moving .cs-adjustment-label__full {
    display: block; position: absolute; inset: 0; white-space: normal; overflow: hidden;
}
.cs-lesson.is-preview-moving .cs-adjustment-label > span { inset: 4px 6px; }
.cs-lesson--cell.is-preview-closing { pointer-events: none; }
.cs-lesson--cell:focus-visible, .cs-lesson__main:focus-visible, .cs-adjustment-label:focus-visible { outline: 2px solid #fbbf24; outline-offset: 1px; }
.cs-lesson.is-counterpart-focus { outline: 3px solid #f59e0b; outline-offset: 1px; }
.cs-lesson.is-counterpart-focus::after { content: '已定位'; position: absolute; right: 0; top: -18px; background: #713f12; color: #fff; padding: 1px 4px; border-radius: 6px; font-size: 10px; pointer-events: none; }
@media (prefers-reduced-transparency: reduce), (prefers-contrast: more) {
    .cs-lesson, .cs-adjustment-label { backdrop-filter: none; -webkit-backdrop-filter: none; }
    .cs-lesson__surface { background-image: none; }
    .cs-adjustment-label { background: var(--cs-accent); color: #fff; border-color: currentColor; }
    .cs-lesson--proposed .cs-adjustment-label { background: #fff; color: #172554; }
}
.cs-deck-feedback { font-size: .8rem; color: var(--text-muted,#64748b); line-height: 1.6; }
.cs-deck-feedback:empty { display: none; }
.cs-lesson__main:focus-visible,.cs-adjustment-label:focus-visible { outline: 3px solid #fbbf24; outline-offset: 1px; }
.cs-lesson-slot[data-cs-lanes] { padding-right: 2px; }

/* ---- 放大视图 ---- */
.cs-expand {
    position: fixed;
    inset: 0;
    z-index: 1200;
    background: rgba(15, 23, 42, 0.55);
    backdrop-filter: blur(6px);
    display: grid;
    place-items: center;
    opacity: 0;
    pointer-events: none;
    transition: opacity 0.25s ease;
}
.cs-expand.is-open { opacity: 1; pointer-events: auto; }
.cs-expand[hidden] { display: none; }
.cs-expand__card {
    width: min(1240px, 94vw);
    height: min(86vh, 900px);
    border-radius: 18px;
    background: #fff;
    box-shadow: 0 40px 90px rgba(2, 6, 23, 0.5);
    display: grid;
    grid-template-rows: auto 1fr;
    overflow: hidden;
    transform: scale(0.82) rotateX(8deg);
    transition: transform 0.32s cubic-bezier(0.22, 0.8, 0.3, 1);
}
.cs-expand.is-open .cs-expand__card { transform: scale(1) rotateX(0deg); }
.cs-expand__bar {
    display: flex; align-items: center; gap: 12px;
    padding: 14px 20px;
    background: linear-gradient(120deg, #4f46e5, #6366f1 55%, #0ea5e9);
    color: #fff;
    flex-wrap: wrap;
}
.cs-expand__bar strong { font-size: 1.15rem; font-weight: 900; }
.cs-expand__bar span { font-size: 0.8rem; opacity: 0.92; }
.cs-expand__nav { margin-left: auto; display: flex; gap: 8px; }
.cs-expand__nav button {
    border: 1px solid rgba(255, 255, 255, 0.5);
    background: rgba(255, 255, 255, 0.14);
    color: #fff;
    border-radius: 10px;
    padding: 6px 14px;
    font-size: 0.82rem;
    font-weight: 800;
    cursor: pointer;
}
.cs-expand__nav button:hover { background: rgba(255, 255, 255, 0.28); }
.cs-expand__body { padding: 16px 20px 20px; min-height: 0; position: relative; }
/* Routing space belongs to the timetable's scrollable canvas. Endpoints follow
   the visible lesson bounds; previews stay above lines and remain clickable. */
.cs-expand__body:has(.cs-change-map) { overflow: auto; overscroll-behavior: contain; }
.cs-change-map { position: absolute; inset: 0 8px; min-height: 580px; }
.cs-change-map > .cs-grid { inset: 32px 48px 28px; gap: 12px; min-width: 0; }
.cs-change-lines { position: absolute; inset: 0; width: 100%; height: 100%; overflow: visible; z-index: 2; pointer-events: none; }
.cs-change-line { fill: none; stroke-width: 2; stroke-linejoin: round; stroke-linecap: round; }
.cs-change-line-label { pointer-events: all; cursor: pointer; outline: none; }
.cs-change-line-label rect { fill: #fff; stroke: currentColor; stroke-width: 1; }
.cs-change-line-label text { fill: #172554; font: 600 12px Arial, sans-serif; text-anchor: middle; dominant-baseline: central; }
.cs-change-line-label:is(:hover,:focus-visible) rect { fill: #eef2ff; stroke-width: 2; }
.cs-change-line-origin { fill: #fff; stroke-width: 1.7; }
.cs-change-line-fallback { position: absolute; top: 3px; left: 100px; right: 48px; font-size: 11px; color: #475569; pointer-events: none; }
@media (prefers-reduced-motion: reduce) {
    .cs-card, .cs-expand, .cs-expand__card { transition: none; }
}

.cs-empty {
    display: grid;
    place-items: center;
    gap: 8px;
    padding: 60px 20px;
    text-align: center;
    color: var(--text-muted, #64748b);
}
.cs-empty strong { color: var(--text-secondary, #334155); font-size: 1rem; }
.cs-empty a { color: #4f46e5; font-weight: 800; }

@media (max-width: 860px) {
    .cs-stage { height: 400px; }
    .cs-card { height: 330px; }
    .cs-deck-slider { width: 110px; }
    .cs-expand__body { overflow: auto; overscroll-behavior: contain; }
    .cs-grid--expanded { min-width: 850px; }
    .cs-grid--expanded.cs-grid--overlaps { min-width: 1100px; }
    .cs-change-map { min-width: 960px; }
    .cs-change-map.cs-change-map--overlaps { min-width: 1210px; }
    .cs-change-map > .cs-grid--expanded { min-width: 0; }
}
`;

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

/** 与课程筛选下拉一致的稳定配色：按 course_options 顺序取色。 */
export function courseAccentFor(overview, courseName) {
    const options = overview?.filters?.course_options || [];
    const index = options.indexOf(courseName);
    return COURSE_PALETTE[(index >= 0 ? index : 0) % COURSE_PALETTE.length];
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
     * - 3D 缩略（!expanded）：最简内容（课程/教室/班级·第N次），无交互，
     *   直接以网格定位的单个 div 呈现。
     * - 放大课表内（expanded 基态）：卡片绝对定位**填满**格子槽（cs-lesson-slot
     *   才是网格定位并作为稳定的悬停锚点；卡片尺寸变化不影响锚点，杜绝
     *   反复放大缩小的"抽风箱"闪烁）。内容**始终完整渲染**、顶对齐，格子
     *   放得下就全部显示，放不下才逐行省略号——不再无谓隐藏内容。
     * - 悬停 / 聚焦 / 触屏首次轻点：仍展开同一链接，宽高由内容决定，
     *   尽量围绕格子展开并向课表内部避让；只有超出可用高度才内部滚动。
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
        const details = [
            lesson.actual_date ? `<span class="cs-lesson__meta">${escapeHtml(lesson.actual_date)} · ${escapeHtml(weekday)} · ${escapeHtml(lesson.section_label || '')}</span>` : '',
            lesson.class_label ? `<span class="cs-lesson__meta">班级 ${escapeHtml(lesson.class_label)}${lesson.student_count ? ` · ${escapeHtml(lesson.student_count)}人` : ''}</span>` : '',
            sessionText ? `<span class="cs-lesson__meta">${escapeHtml(sessionText)}${lesson.single_or_double_label ? ` · ${escapeHtml(lesson.single_or_double_label)}` : ''}</span>` : '',
            linkHint ? `<span class="cs-lesson__meta cs-lesson__link-hint">${escapeHtml(linkHint)}</span>` : '',
        ].filter(Boolean).join('');
        let button = '', comparison = '';
        if (change) {
            const counterpart = change.kind === 'move' && change.counterpart_event_key;
            const jump = counterpart ? ` ${proposed ? '↩ 原位置' : '↗ 新位置'}${change.counterpart_week_index ? ` · 第${change.counterpart_week_index}周` : ''}` : (change.kind === 'room' ? ' · 查看对照' : ' · 查看说明');
            const actionLabel = scheduleChangeLabel(lesson) + jump;
            const shortLabel = adjustmentActionText(lesson);
            button = `<button type="button" class="cs-adjustment-label" data-csd-change="${escapeHtml(eventKey)}" aria-label="${escapeHtml(actionLabel)}" title="${escapeHtml(actionLabel)}"><span class="cs-adjustment-label__short" aria-hidden="true">${escapeHtml(shortLabel).replace('+', '+<wbr>')}</span><span class="cs-adjustment-label__full" aria-hidden="true">${escapeHtml(actionLabel)}</span></button>`;
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
        return `<div class="cs-card__compact">${lessons.length ? `<ol>${lessons.slice(0, 3).map(lesson => `<li><span>${escapeHtml(lesson.weekday_label)} · ${escapeHtml(lesson.section_label)}</span><strong>${escapeHtml(lesson.course_name)}</strong></li>`).join('')}</ol><small>${lessons.length > 3 ? `还有 ${lessons.length - 3} 次安排 · ` : ''}点击放大查看整周课表</small>` : '<strong>这一周没有已排定课程</strong><small>其他课堂可从“全部课程”进入</small>'}</div>`;
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

    function announce(message) {
        refs.feedback.textContent = message;
        refs.expandFeedback.textContent = message;
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
                detail.hidden = false;
                cell.querySelector('.cs-adjustment-label')?.setAttribute('aria-expanded', 'true');
                openLessonPreview(cell); positionLessonPreview();
                cell.querySelector('.cs-adjustment-label')?.focus({ preventScroll: true });
            }
        }
        return true;
    }

    /* ---------------- 放大视图 ---------------- */

    let previewCell = null;
    let pendingTouchPreview = null;
    const previewMotions = new Map();
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');

    function lessonFrame(cell) {
        const style = getComputedStyle(cell);
        const rect = cell.getBoundingClientRect();
        const bodyRect = refs.expandBody.getBoundingClientRect();
        const scaleX = bodyRect.width / refs.expandBody.offsetWidth || 1;
        const scaleY = bodyRect.height / refs.expandBody.offsetHeight || 1;
        const properties = ['width', 'height', 'left', 'top', 'paddingTop', 'paddingRight', 'paddingBottom', 'paddingLeft', 'borderRadius', 'boxShadow'];
        const parts = [...cell.querySelectorAll('.cs-lesson__title, .cs-lesson__details, .cs-lesson__room, .cs-adjustment-label, .cs-adjustment-details')].map(node => {
            const css = getComputedStyle(node), box = node.getBoundingClientRect();
            const visible = box.width > 0 && box.height > 0 && css.display !== 'none' && css.visibility !== 'hidden';
            return { node, visible, box: { left: `${(box.left - rect.left) / scaleX - parseFloat(style.borderLeftWidth)}px`, top: `${(box.top - rect.top) / scaleY - parseFloat(style.borderTopWidth)}px`,
                width: `${box.width / scaleX}px`, height: `${box.height / scaleY}px`, fontSize: css.fontSize, lineHeight: css.lineHeight },
                opacity: visible ? Number(css.opacity) : 0 };
        });
        const labels = [...cell.querySelectorAll('.cs-lesson__room-short, .cs-lesson__room-full, .cs-adjustment-label__short, .cs-adjustment-label__full')].map(node => {
            const css = getComputedStyle(node);
            return { node, opacity: css.display === 'none' ? 0 : Number(css.opacity) };
        });
        return { card: Object.fromEntries(properties.map(key => [key, style[key]])), parts, labels,
            surfaceRadius: getComputedStyle(cell.querySelector('.cs-lesson__surface')).borderRadius };
    }

    function cancelLessonMotion(cell) {
        const motion = previewMotions.get(cell);
        previewMotions.delete(cell);
        motion?.animations.forEach(animation => animation.cancel());
        cell.classList.remove('is-preview-moving');
    }

    function clearLessonPreviews() {
        previewCell = null;
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
        // offset 尺寸处于 CSS 坐标系，避免打开动画中的 scale 影响边界计算。
        const scaleX = bodyRect.width / body.offsetWidth || 1;
        const scaleY = bodyRect.height / body.offsetHeight || 1;
        const availableWidth = Math.max(1, body.clientWidth - 24);
        const availableHeight = Math.max(1, body.clientHeight - 24);
        const maxWidth = Math.min(420, availableWidth);
        const minWidth = Math.min(maxWidth, Math.max(240, slot.offsetWidth));
        cell.style.setProperty('--cs-preview-min-width', `${minWidth}px`);
        cell.style.setProperty('--cs-preview-max-width', `${maxWidth}px`);
        cell.style.setProperty('--cs-preview-max-height', `${availableHeight}px`);
        const width = cell.offsetWidth;
        const height = cell.offsetHeight;
        const slotLeft = (slotRect.left - bodyRect.left) / scaleX;
        const slotTop = (slotRect.top - bodyRect.top) / scaleY;
        const clamp = (value, min, max) => Math.max(min, Math.min(value, max));
        const left = clamp(slotLeft + (slot.offsetWidth - width) / 2, 12, body.clientWidth - width - 12);
        const top = clamp(slotTop + (slot.offsetHeight - height) / 2, 12, body.clientHeight - height - 12);
        cell.style.setProperty('--cs-preview-left', `${left - slotLeft}px`);
        cell.style.setProperty('--cs-preview-top', `${top - slotTop}px`);
    }

    function animateLessonPreview(cell, opening) {
        if (!cell.isConnected) return;
        // Snapshot the currently painted boxes before cancelling. Rapid reversal
        // starts from these positions and opacity values, not either endpoint.
        const from = lessonFrame(cell);
        const priorScroll = previewMotions.get(cell)?.openScroll;
        const openScroll = priorScroll || { left: cell.scrollLeft, top: cell.scrollTop };
        cancelLessonMotion(cell);
        cell.classList.remove('is-preview-closing');
        cell.classList.toggle('is-preview', opening);
        if (opening) {
            measureLessonPreview(cell);
            if (priorScroll) { cell.scrollLeft = priorScroll.left; cell.scrollTop = priorScroll.top; }
        }
        const to = lessonFrame(cell);
        const targetScroll = { left: cell.scrollLeft, top: cell.scrollTop };
        const finish = () => {
            cell.classList.remove('is-preview-closing');
            cancelLessonMotion(cell);
            cell.scrollLeft = previewCell === cell ? targetScroll.left : 0;
            cell.scrollTop = previewCell === cell ? targetScroll.top : 0;
            cell.parentElement?.classList.toggle('is-preview', previewCell === cell);
            if (previewCell === cell) cell.dataset.previewState = 'open';
            else delete cell.dataset.previewState;
            scheduleChangeLines();
        };
        if (reducedMotion.matches || typeof cell.animate !== 'function') { finish(); return; }
        if (!opening) cell.classList.add('is-preview-closing');
        cell.classList.add('is-preview-moving');
        cell.dataset.previewState = opening ? 'opening' : 'closing';
        const timing = { duration: 120, easing: 'cubic-bezier(.2,.75,.25,1)', fill: 'both' };
        const fadeTiming = { duration: 80, easing: 'linear', fill: 'both' };
        const moving = { minWidth: '0px', maxWidth: 'none', minHeight: '0px', maxHeight: 'none', overflow: 'hidden' };
        const animations = [cell.animate([{ ...from.card, ...moving }, { ...to.card, ...moving }], timing)];
        const absolute = { position: 'absolute', margin: '0px', right: 'auto', bottom: 'auto', minWidth: '0px', maxWidth: 'none',
            minHeight: '0px', maxHeight: 'none', boxSizing: 'border-box', overflow: 'hidden', visibility: 'visible' };
        to.parts.forEach((target, index) => {
            const source = from.parts[index];
            if (!source.visible && !target.visible) return;
            // A newly revealed detail fades in at its destination; a disappearing
            // detail fades out in place. The title/room/button travel continuously.
            const startBox = source.visible ? source.box : target.box;
            const endBox = target.visible ? target.box : source.box;
            animations.push(target.node.animate([{ ...startBox, ...absolute }, { ...endBox, ...absolute }], timing));
            animations.push(target.node.animate([{ opacity: source.opacity }, { opacity: target.opacity }], fadeTiming));
        });
        to.labels.forEach((target, index) => animations.push(target.node.animate([
            { opacity: from.labels[index].opacity }, { opacity: target.opacity },
        ], fadeTiming)));
        animations.push(cell.querySelector('.cs-lesson__surface').animate([
            { borderRadius: from.surfaceRadius }, { borderRadius: to.surfaceRadius },
        ], timing));
        // DOMRect snapshots describe painted (scrolled) coordinates. Animate in
        // a zero-scroll coordinate space, then restore the open target scroll.
        // This also covers a resize while a long preview is already scrolled.
        cell.scrollLeft = 0; cell.scrollTop = 0;
        const motion = { animations, openScroll: opening ? targetScroll : openScroll };
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
            const dateRange = week.date_range_label ? ` · ${week.date_range_label}` : '';
            refs.expandSub.textContent = `${termLabel}${dateRange} · ${week.lesson_count} 节安排 · ${week.total_hours} 课时`;
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
        if (!state.overview?.weeks?.length || !refs.expand) return;
        if (state.expanded) return;
        expandMotionGeneration += 1;
        if (refs.expand.hidden) expandedTrigger = document.activeElement;
        state.expanded = true;
        if (refs.expand.hidden || renderedExpandedWeek !== state.overview.weeks[state.activeWeekIndex]) renderExpanded();
        refs.expand.hidden = false;
        refs.expand.inert = false;
        // Resolve the closed frame before opening; a reopening transition keeps
        // its current transform instead of reconstructing the course links.
        refs.expand.getBoundingClientRect();
        refs.expand.classList.add('is-open');
        scheduleChangeLines();
        refs.expandClose.focus({ preventScroll: true });
    }

    function closeExpanded() {
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
        };
        const animations = reducedMotion.matches ? [] : refs.expand.getAnimations({ subtree: true });
        if (!animations.length) finish();
        else Promise.allSettled(animations.map((animation) => animation.finished)).then(finish);
        (expandedTrigger?.isConnected ? expandedTrigger : refs.stage)?.focus({ preventScroll: true });
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

    function onLessonPointerOver(event) {
        if (event.pointerType === 'touch') return;
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
        if (!state.expanded) return;
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
                const when = sync?.last_success_at || state.overview.selected_term?.synced_at;
                const pending = state.overview.weeks.reduce((sum, week) => sum + week.proposed_count, 0);
                const warnings = state.overview.warnings || sync?.warnings || [];
                announce([...new Set([state.overview.message, when ? `最近成功同步：${when}` : '', pending ? `${pending} 项待审预测不计入正式课时` : '', Array.isArray(warnings) ? warnings.map(item => typeof item === 'string' ? item : item.message || '').filter(Boolean).join('；') : ''].filter(Boolean))].join(' · '));
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
            destroyed = true;
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
            expand.remove();
            container.classList.remove('cs-deck');
            container.innerHTML = '';
        },
    };
}
