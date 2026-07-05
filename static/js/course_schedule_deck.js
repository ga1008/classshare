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
 * overview 结构即 /api/manage/teaching/course-schedule/overview 的返回值：
 * terms / selected_term / weeks[{week_index,label,is_current,lessons[]}] /
 * section_range / filters.course_options。lesson.classroom_url 存在时课程块
 * 在放大视图中可点击跳转对应课堂。
 *
 * 放大视图按节次给出早读(1)/上午(2-5)/下午(6-9)/晚上(10-11+)的背景分区。
 */

const STYLE_ID = 'course-schedule-deck-style';

const COURSE_PALETTE = [
    '#4f46e5', '#0ea5e9', '#059669', '#d97706', '#db2777',
    '#7c3aed', '#0891b2', '#65a30d', '#ea580c', '#e11d48',
];

const BAND_LABELS = { dawn: '早读', am: '上午', pm: '下午', eve: '晚上' };

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
.cs-lesson {
    border-radius: 8px;
    padding: 6px 8px;
    color: #fff;
    background: var(--cs-accent, #6366f1);
    display: flex;
    flex-direction: column;
    justify-content: center;
    gap: 2px;
    overflow: hidden;
    min-height: 0;
    min-width: 0;
    box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.18);
    text-decoration: none;
}
.cs-lesson strong {
    font-size: 0.76rem; line-height: 1.25; font-weight: 900;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.cs-lesson span { font-size: 0.64rem; opacity: 0.94; line-height: 1.3; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.cs-lesson__link-hint { font-weight: 800; opacity: 0.95; }
/* 卡片文字始终白色：cs-lesson--cell 是 a 标签，全局 a:link/:visited/:hover
   的默认色（蓝/紫）特异性高于 .cs-lesson 的 color，会让悬停时文字变蓝，
   这里用足够高特异性的选择器覆盖，任何状态都保持白字。 */
a.cs-lesson, a.cs-lesson:link, a.cs-lesson:visited, a.cs-lesson:hover, a.cs-lesson:focus,
a.cs-lesson strong, a.cs-lesson span { color: #fff; }

/* 形式一：3D 缩略卡片（网格定位，最简、无悬停交互）。 */
.cs-lesson--mini { justify-content: center; }

/* 放大视图：网格允许悬停放大的卡片溢出格子显示（其它情况仍裁切）。 */
.cs-grid--expanded { overflow: visible; }
.cs-grid--expanded .cs-grid__corner,
.cs-grid--expanded .cs-grid__day,
.cs-grid--expanded .cs-grid__section { font-size: 0.84rem; }

/* 形式二：放大课表内的卡片。cs-lesson-slot 才是网格定位、作为**尺寸恒定
   的稳定悬停锚点**；内层卡片绝对定位填满槽（基态占满格子）。卡片放大缩小
   都不改变锚点尺寸。内容顶对齐、始终完整渲染，放不下才逐行省略。 */
.cs-lesson-slot { position: relative; }
.cs-lesson--cell {
    position: absolute;
    top: 50%; left: 50%;
    width: 100%; height: 100%;
    transform: translate(-50%, -50%);
    justify-content: flex-start;
    padding: 6px 9px;
    transition: width 0.18s cubic-bezier(0.22, 0.8, 0.3, 1),
                height 0.18s cubic-bezier(0.22, 0.8, 0.3, 1),
                box-shadow 0.18s ease, padding 0.18s ease, gap 0.18s ease;
    will-change: width, height;
    z-index: 1;
}
.cs-lesson--cell strong { font-size: 0.86rem; margin-bottom: 1px; }
.cs-lesson--cell span { font-size: 0.72rem; }
a.cs-lesson--cell { cursor: pointer; }
/* 尚无对应课堂：虚线描边提示可创建 */
a.cs-lesson--create {
    box-shadow: inset 0 0 0 2px rgba(255, 255, 255, 0.55);
    background-image: linear-gradient(rgba(255, 255, 255, 0.12), rgba(255, 255, 255, 0.12));
}
a.cs-lesson--create .cs-lesson__link-hint { text-decoration: underline dashed; text-underline-offset: 3px; }

/* 形式三：悬停放大（原地，居中于自身格子）。放大态由 slot:hover 与
   cell:hover 共同维持（自维持）——鼠标在格子或放大后的卡片上都保持放大，
   放大卡超出格子后在圆角/边角处也不会掉出悬停区，彻底消除抽风闪烁。
   只改字号/行距/内边距/尺寸，颜色不变。 */
.cs-lesson-slot:hover { z-index: 60; }
.cs-lesson-slot:hover .cs-lesson--cell,
.cs-lesson--cell:hover {
    width: max(100%, 208px);
    height: max(100%, 176px);
    padding: 11px 13px;
    gap: 4px;
    z-index: 60;
    overflow: visible;
    box-shadow: 0 22px 46px rgba(15, 23, 42, 0.45), inset 0 0 0 1px rgba(255, 255, 255, 0.32);
}
.cs-lesson-slot:hover .cs-lesson--cell strong,
.cs-lesson--cell:hover strong { font-size: 1.02rem; line-height: 1.3; white-space: normal; margin-bottom: 3px; }
.cs-lesson-slot:hover .cs-lesson--cell span,
.cs-lesson--cell:hover span { font-size: 0.86rem; line-height: 1.55; white-space: normal; }

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
    return week && !week.lesson_count ? '<div class="cs-week-empty-mark">本周无排课</div>' : '';
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
        </div>`;

    const expand = document.createElement('div');
    expand.className = 'cs-expand';
    expand.setAttribute('role', 'dialog');
    expand.setAttribute('aria-modal', 'true');
    expand.innerHTML = `
        <div class="cs-expand__card">
            <div class="cs-expand__bar">
                <strong data-csd-expand-title>第1周</strong>
                <span data-csd-expand-sub></span>
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
        expand,
        expandTitle: expand.querySelector('[data-csd-expand-title]'),
        expandSub: expand.querySelector('[data-csd-expand-sub]'),
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
     * - 悬停放大（expanded 悬停）：卡片**原地**放大（居中于自身格子、不滑向
     *   别处）、阴影跟随，字号/行距/内边距加大以适配放大版面，露出被裁的
     *   全部内容；可点击跳课堂或新建课堂。字体颜色始终为白（不变蓝）。
     *   放大态由 `slot:hover` 与 `cell:hover` 共同维持（自维持），消除放大
     *   卡超出格子后在圆角/边角处的悬停死区造成的抽风闪烁。
     */
    function lessonHtml(lesson, { expanded, minSection, maxSection, columnBase }) {
        const sections = lesson.sections || [];
        const start = Math.max(minSection, sections[0] || minSection);
        const end = Math.min(maxSection, sections[sections.length - 1] || start);
        const rowStart = start - minSection + 2;
        const rowSpan = Math.max(1, end - start + 1);
        const column = Math.min(7, Math.max(1, lesson.weekday || 1)) + columnBase - 1;
        const accent = courseAccentFor(state.overview, lesson.course_name);
        const gridPos = `grid-column:${column};grid-row:${rowStart} / span ${rowSpan};`;
        const roomText = escapeHtml(lesson.classroom_short || lesson.classroom || '教室待定');

        if (!expanded) {
            // 形式一：3D 缩略卡片（最简、无交互）。
            return `
            <div class="cs-lesson cs-lesson--mini" style="--cs-accent:${accent};${gridPos}"
                 title="${escapeHtml(`${lesson.course_name} ${roomText} ${lesson.class_label || ''}`)}">
                <strong>${escapeHtml(lesson.course_name)}</strong>
                <span>${roomText}</span>
                <span>${escapeHtml(lesson.class_label || '')}${lesson.session_no ? ` · 第${lesson.session_no}次` : ''}</span>
            </div>`;
        }

        // 形式二/三：放大课表内的卡片 + 悬停放大。
        const classroomHref = String(lesson.classroom_url || '');
        const createHref = String(lesson.create_url || '');
        const href = classroomHref || createHref;
        const isCreate = Boolean(href) && !classroomHref;
        const sdLabel = lesson.single_or_double_label ? ` · ${escapeHtml(lesson.single_or_double_label)}` : '';
        const studentText = lesson.student_count ? ` · ${lesson.student_count}人` : '';
        const sessionText = lesson.session_no
            ? `第${lesson.session_no}次课${lesson.session_total ? `（共${lesson.session_total}次）` : ''}`
            : '';
        const hintText = href ? (isCreate ? '尚无对应课堂 · 点击创建 +' : '点击进入课堂 →') : '';
        const titleText = `${lesson.course_name} ${lesson.section_label} ${lesson.classroom || ''} ${lesson.class_label || ''}`
            + (sessionText ? ` · ${sessionText}` : '')
            + (href ? (isCreate ? ' · 点击创建课堂' : ' · 点击进入课堂') : '');
        const tag = href ? 'a' : 'div';
        const hrefAttr = href ? ` href="${escapeHtml(href)}"` : '';
        // 始终渲染完整内容行，顶对齐；格子放得下就全显示，放不下由 overflow
        // 裁切 + 逐行省略号；悬停时卡片原地放大、字号加大即可看全。
        const detailLines = [
            `<span>教室 ${roomText}</span>`,
            `<span>班级 ${escapeHtml(lesson.class_label || '')}${studentText}</span>`,
            sessionText ? `<span>${escapeHtml(sessionText)}${sdLabel}</span>` : '',
            hintText ? `<span class="cs-lesson__link-hint">${hintText}</span>` : '',
        ].filter(Boolean).join('');
        return `
        <div class="cs-lesson-slot" style="${gridPos}">
            <${tag} class="cs-lesson cs-lesson--cell${isCreate ? ' cs-lesson--create' : ''}"${hrefAttr}
                 style="--cs-accent:${accent};" title="${escapeHtml(titleText)}">
                <strong>${escapeHtml(lesson.course_name)}</strong>
                ${detailLines}
            </${tag}>
        </div>`;
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
            ? `30px ${labelCol} repeat(7, 1fr)`
            : `${labelCol} repeat(7, 1fr)`;

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

        const lessons = (week?.lessons || [])
            .map((lesson) => lessonHtml(lesson, { expanded, minSection, maxSection, columnBase }))
            .join('');
        const corner = expanded
            ? '<div class="cs-grid__corner" style="grid-column:1 / span 2;grid-row:1;">节</div>'
            : '<div class="cs-grid__corner" style="grid-column:1;grid-row:1;">节</div>';

        return `
        <div class="cs-grid ${expanded ? 'cs-grid--expanded' : ''}"
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
                <div class="cs-card__body">${renderWeekGrid(week)}${weekEmptyMarkHtml(week)}</div>`;
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
    }

    function goToWeek(index) {
        const weeks = state.overview?.weeks || [];
        if (!weeks.length) return;
        const next = Math.min(Math.max(index, 0), weeks.length - 1);
        if (next === state.activeWeekIndex) return;
        state.activeWeekIndex = next;
        layoutDeck();
    }

    /* ---------------- 放大视图 ---------------- */

    function renderExpanded() {
        const weeks = state.overview?.weeks || [];
        const week = weeks[state.activeWeekIndex];
        if (!week || !refs.expandBody) return;
        if (refs.expandTitle) refs.expandTitle.textContent = week.label + (week.is_current ? '（本周）' : '');
        if (refs.expandSub) {
            const termLabel = state.overview?.selected_term?.label || '';
            const dateRange = week.date_range_label ? ` · ${week.date_range_label}` : '';
            refs.expandSub.textContent = `${termLabel}${dateRange} · ${week.lesson_count} 节安排 · ${week.total_hours} 课时`;
        }
        refs.expandBody.innerHTML = renderWeekGrid(week, { expanded: true }) + weekEmptyMarkHtml(week);
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
        if (!state.overview?.weeks?.length) return;
        event.preventDefault();
        const now = Date.now();
        if (now < wheelLockUntil) return;
        wheelLockUntil = now + 240;
        goToWeek(state.activeWeekIndex + (event.deltaY > 0 ? 1 : -1));
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
        const card = event.target.closest('.cs-card');
        if (card && card.classList.contains('is-active')) openExpanded();
    }

    /* 触摸 / 鼠标水平拖拽翻周（移动端没有滚轮）。每拖 90px 翻一周；
       拖动超过阈值后抑制随后的点击放大。 */
    let dragState = null;
    let dragMoved = false;

    function onStagePointerDown(event) {
        if (!state.overview?.weeks?.length || event.button > 0) return;
        dragState = { pointerId: event.pointerId, startX: event.clientX, consumedSteps: 0 };
        dragMoved = false;
    }

    function onStagePointerMove(event) {
        if (!dragState || event.pointerId !== dragState.pointerId) return;
        const delta = event.clientX - dragState.startX;
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
        if (event.target.closest('a.cs-lesson')) return;
        event.preventDefault();
        const now = Date.now();
        if (now < wheelLockUntil) return;
        wheelLockUntil = now + 260;
        goToWeek(state.activeWeekIndex + (event.deltaY > 0 ? 1 : -1));
        renderExpanded();
    }

    function onExpandBackdrop(event) {
        if (event.target === refs.expand) closeExpanded();
    }

    function onExpandBodyClick(event) {
        const link = event.target.closest('a.cs-lesson');
        if (!link) return;
        event.preventDefault();
        config.onNavigate(link.getAttribute('href'));
    }

    function onDocumentKeydown(event) {
        if (event.key === 'Escape' && state.expanded) closeExpanded();
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
    refs.termSelect?.addEventListener('change', onTermSelectChange);
    document.addEventListener('keydown', onDocumentKeydown);

    /* ---------------- 公开 API ---------------- */

    return {
        setOverview(overview, { keepWeek = false } = {}) {
            const previousWeek = state.overview?.weeks?.[state.activeWeekIndex]?.week_index;
            state.overview = overview || null;
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
            document.removeEventListener('keydown', onDocumentKeydown);
            expand.remove();
            container.classList.remove('cs-deck');
            container.innerHTML = '';
        },
    };
}
