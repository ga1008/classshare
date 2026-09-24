/** Shared structural and glass styles for both schedule presentations. */
export const DECK_CSS = `
.cs-deck, .cs-expand {
    --cs-fill: hsl(var(--ls-glass-fill-content));
    --cs-raised-fill: hsl(var(--ls-glass-fill-strong));
    --cs-inset-fill: hsl(var(--ls-glass-fill-inset));
    --cs-control-fill: hsl(var(--ls-glass-fill-control));
    --cs-live-blur: blur(var(--ls-blur-regular)) saturate(var(--ls-glass-saturate));
    color: hsl(var(--ls-ink));
}
.cs-deck { display: grid; gap: 12px; }
/* 头部悬于后排堆叠卡片之上，避免被 Flip3D 上浮的卡片遮住 */
.cs-deck-head { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; position: relative; z-index: 520; }
.cs-deck-head__copy h3 { margin: 0; font-size: 1.05rem; font-weight: 800; color: hsl(var(--ls-ink)); }
.cs-deck-head__copy p { margin: 0; font-size: 0.78rem; color: hsl(var(--ls-ink-2)); }
.cs-deck-term {
    min-width: 190px;
    padding: 8px 12px;
    border: 1px solid hsl(var(--ls-glass-line));
    border-radius: var(--ls-r-capsule);
    background: var(--cs-control-fill);
    font-size: 0.86rem;
    font-weight: 700;
    color: hsl(var(--ls-ink));
}
.cs-deck-nav { margin-left: auto; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.cs-deck-nav__btn {
    width: 34px; height: 34px;
    border-radius: var(--ls-r-capsule);
    border: 1px solid hsl(var(--ls-glass-line));
    background: var(--cs-control-fill);
    box-shadow: inset 0 1px 0 hsl(var(--ls-glass-rim));
    color: hsl(var(--ls-ink));
    font-size: 1rem;
    cursor: pointer;
    display: inline-flex; align-items: center; justify-content: center;
}
.cs-deck-nav__btn:hover { border-color: hsl(var(--ls-primary) / .6); background: color-mix(in srgb, hsl(var(--ls-primary)) 12%, var(--cs-control-fill)); }
.cs-deck-nav__btn:disabled { opacity: 0.4; cursor: default; }
.cs-deck-nav__btn--edit, .cs-deck-nav__btn--edit:link, .cs-deck-nav__btn--edit:visited { width: auto; padding: 0 14px; font-size: .8rem; font-weight: 800; text-decoration: none; color: hsl(var(--ls-ink)); background: color-mix(in srgb, hsl(var(--ls-primary)) 16%, var(--cs-control-fill)); }
.cs-deck-nav__btn--edit:hover { background: color-mix(in srgb, hsl(var(--ls-primary)) 28%, var(--cs-control-fill)); color: hsl(var(--ls-ink)); }
.cs-deck-nav__btn--edit[hidden] { display: none; }
.cs-week-indicator { font-size: 0.86rem; font-weight: 800; color: hsl(var(--ls-ink)); min-width: 120px; text-align: center; }
.cs-week-indicator small { display: block; font-weight: 600; color: hsl(var(--ls-ink-2)); font-size: 0.7rem; }
.cs-deck-slider { width: 180px; accent-color: hsl(var(--ls-primary)); }

.cs-stage {
    position: relative;
    /* 后排 3D 投影只能留在画布内，避免窄屏出现整页横向滚动。
       clip 不创建滚动容器；头部控件和独立整周对话框不受此裁切影响。 */
    overflow: clip;
    height: 460px;
    perspective: 1500px;
    perspective-origin: 50% 38%;
    border-radius: var(--ls-r-lg);
    background: var(--cs-inset-fill);
    background-image: var(--ls-glass-sheen);
    border: 1px solid hsl(var(--ls-glass-line));
    touch-action: pan-y;
}
.cs-stage__hint {
    position: absolute;
    left: 14px; bottom: 10px;
    z-index: 400;
    font-size: 0.72rem;
    color: hsl(var(--ls-ink-2));
    background: var(--cs-raised-fill);
    border: 1px solid hsl(var(--ls-glass-line));
    border-radius: var(--ls-r-capsule);
    padding: 4px 12px;
    pointer-events: none;
}

.cs-card {
    position: absolute;
    left: 50%; top: 50%;
    width: min(680px, 82%);
    height: 380px;
    border-radius: var(--ls-r-md);
    background: var(--cs-fill);
    background-image: var(--ls-glass-sheen);
    border: 1px solid hsl(var(--ls-glass-line));
    box-shadow: var(--ls-glass-shadow);
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
    background: color-mix(in srgb, hsl(var(--ls-primary)) 12%, var(--cs-inset-fill));
    border-bottom: 1px solid hsl(var(--ls-glass-line));
    color: hsl(var(--ls-ink));
}
.cs-card__bar strong { font-size: 0.98rem; font-weight: 900; letter-spacing: 0.04em; }
.cs-card__bar span { font-size: 0.74rem; opacity: 0.92; font-weight: 700; }
.cs-card__badge {
    margin-left: auto;
    font-size: 0.7rem;
    font-weight: 900;
    background: var(--cs-control-fill);
    border: 1px solid hsl(var(--ls-glass-line));
    border-radius: var(--ls-r-capsule);
    padding: 3px 10px;
}
.cs-card__badge.is-current { background: color-mix(in srgb, hsl(var(--ls-primary)) 18%, var(--cs-control-fill)); color: hsl(var(--ls-ink)); }
.cs-card__body { padding: 10px 12px 12px; min-height: 0; position: relative; }
.cs-card.is-active {
    cursor: zoom-in;
    backdrop-filter: var(--cs-live-blur); -webkit-backdrop-filter: var(--cs-live-blur);
}
.cs-card.is-active:hover { box-shadow: var(--ls-glass-shadow-strong); }
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
    color: hsl(var(--ls-ink-3) / .20);
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
    color: hsl(var(--ls-ink-2));
    background: var(--cs-inset-fill);
    border: 1px solid hsl(var(--ls-glass-line));
    border-radius: var(--ls-r-xs);
    font-size: 0.68rem;
}
.cs-grid__cellbg { border-radius: var(--ls-r-xs); }
/* 周末列弱化、今天列强调 */
.cs-grid__day--weekend { color: hsl(var(--ls-ink-3)); background: transparent; }
.cs-grid__cellbg--weekend { opacity: .65; }
.cs-grid__day--today { background: color-mix(in srgb, hsl(var(--ls-primary)) 20%, var(--cs-control-fill)); color: hsl(var(--ls-ink)); border-color: hsl(var(--ls-primary) / .6); }
.cs-grid__day--today small { font-weight: 700; opacity: 0.9; margin-left: 4px; }
.cs-grid__cellbg--today { box-shadow: inset 0 0 0 1px hsl(var(--ls-primary) / .26); }
/* 早读 / 上午 / 下午 / 晚上分区背景 */
.cs-grid__cellbg--dawn, .cs-grid__section--dawn, .cs-grid__band--dawn { --cs-period-tone: var(--ls-warning); }
.cs-grid__cellbg--am, .cs-grid__section--am, .cs-grid__band--am { --cs-period-tone: var(--ls-info); }
.cs-grid__cellbg--pm, .cs-grid__section--pm, .cs-grid__band--pm { --cs-period-tone: var(--ls-primary); }
.cs-grid__cellbg--eve, .cs-grid__section--eve, .cs-grid__band--eve { --cs-period-tone: var(--ls-ink-3); }
.cs-grid__cellbg { background: color-mix(in srgb, hsl(var(--cs-period-tone, var(--ls-primary))) 7%, var(--cs-inset-fill)); }
.cs-grid__section, .cs-grid__band { background: color-mix(in srgb, hsl(var(--cs-period-tone, var(--ls-primary))) 12%, var(--cs-inset-fill)); color: hsl(var(--ls-ink-2)); }
.cs-grid__band {
    display: flex; align-items: center; justify-content: center;
    border: 1px solid hsl(var(--ls-glass-line));
    border-radius: var(--ls-r-xs);
    font-weight: 900;
    font-size: 0.72rem;
    letter-spacing: 0.24em;
    writing-mode: vertical-lr;
    text-orientation: upright;
}
/* Repeated lessons use tint and a rim. Only an open preview creates a live
   blur host, sampling the actual cells it covers without fading its text. */
.cs-lesson {
    --cs-radius: var(--ls-r-md);
    border-radius: var(--cs-radius); color: hsl(var(--ls-ink)); background: transparent;
    min-width: 0; min-height: 0; padding: 0; box-sizing: border-box;
    text-decoration: none; overflow: hidden;
}
.cs-lesson__surface {
    position: relative; display: grid; grid-template-rows: minmax(0, 1fr) auto;
    width: 100%; height: 100%; min-width: 0; min-height: 0; box-sizing: border-box;
    border-radius: inherit; padding: 7px 8px; gap: 4px; overflow: hidden;
    background-color: var(--cs-fill);
    background-color: color-mix(in srgb, var(--cs-accent, hsl(var(--ls-primary))) 18%, var(--cs-fill));
    background-image: var(--ls-glass-sheen);
    box-shadow: inset 0 1px 0 hsl(var(--ls-glass-rim)), inset 0 0 0 1px hsl(var(--ls-glass-line)), var(--ls-shadow-1);
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
    border: 1px solid hsl(var(--ls-glass-line)); border-radius: var(--ls-r-capsule);
    padding: 3px 6px; color: hsl(var(--ls-ink)); font: 750 .67rem/1.25 var(--ls-font-sans);
    background: var(--cs-control-fill);
    box-shadow: inset 0 1px 0 hsl(var(--ls-glass-rim)), var(--ls-shadow-1);
    text-align: center; white-space: nowrap; cursor: pointer;
}
.cs-adjustment-label > span { font: inherit; line-height: inherit; }
.cs-adjustment-label__full { display: none; }
.cs-adjustment-label:is(:hover,:focus-visible) { background-color: color-mix(in srgb, hsl(var(--ls-primary)) 12%, var(--cs-control-fill)); }
.cs-adjustment-label:active { box-shadow: inset 0 1px 4px hsl(var(--ls-ink) / .16); }
.cs-lesson--mini { container: cs-lesson / size; --cs-radius: var(--ls-r-sm); }
.cs-lesson--mini .cs-lesson__surface { padding: 4px 5px; gap: 2px; }
.cs-lesson--mini .cs-lesson__title { font-size: .76rem; }
.cs-lesson--mini .cs-lesson__room { font-size: .64rem; }
.cs-lesson--mini .cs-adjustment-label { min-height: 19px; padding: 2px 4px; font-size: .6rem; }
.cs-grid--expanded { overflow: visible; }
.cs-grid--expanded .cs-grid__corner,
.cs-grid--expanded .cs-grid__day,
.cs-grid--expanded .cs-grid__section { font-size: .84rem; }
.cs-lesson-slot { position: relative; min-width: 0; min-height: 0; container: cs-lesson / size; }
.cs-lesson--cell { position: absolute; top: 0; left: 0; width: 100%; height: 100%; z-index: 1; }
a.cs-lesson, a.cs-lesson:link, a.cs-lesson:visited, a.cs-lesson:hover, a.cs-lesson:focus { color: hsl(var(--ls-ink)); }
a.cs-lesson--cell { cursor: pointer; }
a.cs-lesson--create .cs-lesson__surface { box-shadow: inset 0 0 0 2px hsl(var(--ls-primary) / .6); }
a.cs-lesson--create .cs-lesson__link-hint { text-decoration: underline dashed; text-underline-offset: 3px; }
/* Pending frames keep a 4px gap; extremely narrow/short lanes use 2px below
   to retain readable title and action rows. */
.cs-lesson.cs-lesson--pending { border: 2px dashed var(--cs-accent); padding: 4px; }
.cs-lesson--pending .cs-lesson__surface { border-radius: max(6px, calc(var(--cs-radius) - 6px)); }
.cs-lesson--proposed .cs-lesson__surface { background-color: color-mix(in srgb, var(--cs-accent, hsl(var(--ls-primary))) 12%, var(--cs-raised-fill)); }
.cs-lesson--proposed .cs-adjustment-label { border-color: hsl(var(--ls-primary) / .35); }
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
/* A narrow pending lane may wrap its short action to two or three lines.
   Reserve one complete title line instead of clipping a second line into it. */
@container cs-lesson (max-width: 80px) and (min-height: 73px) and (max-height: 100px) {
    .cs-lesson--cell.cs-lesson--pending:not(.is-preview,.is-preview-closing) .cs-lesson__title { -webkit-line-clamp: 1; }
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
    --cs-radius: var(--ls-r-lg);
    top: var(--cs-preview-top, 0px); left: var(--cs-preview-left, 0px);
    width: var(--cs-preview-width, 280px); height: var(--cs-preview-height, auto);
    max-width: var(--cs-preview-max-width, calc(100vw - 32px));
    max-height: var(--cs-preview-max-height, calc(100dvh - 32px));
    overflow-x: hidden; overflow-y: auto; overscroll-behavior: contain; touch-action: pan-y;
    backdrop-filter: var(--cs-live-blur); -webkit-backdrop-filter: var(--cs-live-blur);
    box-shadow: var(--ls-glass-shadow-strong);
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
.cs-lesson--cell:is(.is-preview,.is-preview-closing) .cs-adjustment-label { font-size: .78rem; line-height: 1.4; padding: 6px 8px; max-width: 100%; text-align: left; white-space: normal; }
.cs-lesson--cell:is(.is-preview,.is-preview-closing) .cs-adjustment-label__short { display: none; }
.cs-lesson--cell:is(.is-preview,.is-preview-closing) .cs-adjustment-label__full { display: inline; }
.cs-adjustment-details { font-size: .75rem; line-height: 1.6; padding-top: 6px; border-top: 1px solid currentColor; }
.cs-adjustment-details[hidden], .cs-lesson:not(.is-preview,.is-preview-closing) .cs-adjustment-details { display: none; }
.cs-lesson--cell.is-preview-closing { pointer-events: none; }
.cs-lesson--cell:focus-visible, .cs-lesson__main:focus-visible, .cs-adjustment-label:focus-visible,
.cs-deck-nav__btn:focus-visible, .cs-expand__nav button:focus-visible { outline: 2px solid hsl(var(--ls-ring)); outline-offset: 2px; }
.cs-lesson.is-counterpart-focus { outline: 3px solid hsl(var(--ls-warning)); outline-offset: 1px; }
.cs-lesson.is-counterpart-focus::after { content: '已定位'; position: absolute; right: 4px; top: 4px; background: hsl(var(--ls-surface-1)); color: hsl(var(--ls-ink)); border: 1px solid hsl(var(--ls-warning)); padding: 1px 4px; border-radius: var(--ls-r-xs); font-size: 10px; pointer-events: none; }
.cs-deck-feedback { font-size: .8rem; color: hsl(var(--ls-ink-2)); line-height: 1.6; }
.cs-deck-feedback:empty { display: none; }
.cs-lesson-slot[data-cs-lanes] { padding-right: 2px; }

/* ---- 放大视图 ---- */
.cs-expand {
    position: fixed;
    inset: 0;
    z-index: var(--ls-z-modal);
    background: transparent;
    backdrop-filter: none; -webkit-backdrop-filter: none;
    display: grid;
    place-items: center;
    opacity: 0;
    pointer-events: none;
    transition: opacity 0.25s ease;
}
.cs-expand.is-open { opacity: 1; pointer-events: auto; }
.cs-expand[hidden] { display: none; }
.cs-expand__card {
    position: relative;
    width: min(1240px, 94vw);
    height: min(86vh, 900px);
    border-radius: var(--ls-r-lg);
    border: 1px solid hsl(var(--ls-glass-line));
    background: transparent;
    color: hsl(var(--ls-ink));
    box-shadow: var(--ls-glass-shadow-strong);
    display: grid;
    grid-template-rows: auto 1fr;
    overflow: hidden;
    transform: scale(0.82) rotateX(8deg);
    transition: transform 0.32s cubic-bezier(0.22, 0.8, 0.3, 1);
}
/* Keep the panel's frost behind its content, not on an ancestor backdrop root:
   an open lesson must still sample and blur neighbouring course text. */
.cs-expand__card::before {
    content: ''; position: absolute; inset: 0; z-index: -1;
    border-radius: inherit; pointer-events: none;
    background: var(--cs-raised-fill); background-image: var(--ls-glass-sheen);
    backdrop-filter: var(--cs-live-blur); -webkit-backdrop-filter: var(--cs-live-blur);
}
.cs-expand.is-open .cs-expand__card { transform: scale(1) rotateX(0deg); }
.cs-expand__bar {
    display: flex; align-items: center; gap: 12px;
    padding: 14px 20px;
    background: color-mix(in srgb, hsl(var(--ls-primary)) 12%, var(--cs-inset-fill));
    border-bottom: 1px solid hsl(var(--ls-glass-line));
    color: hsl(var(--ls-ink));
    flex-wrap: wrap;
}
.cs-expand__bar strong { font-size: clamp(1.25rem, 2vw, 1.65rem); line-height: 1.2; font-weight: 850; letter-spacing: -.025em; }
.cs-expand__bar span { font-size: .94rem; font-weight: 650; color: hsl(var(--ls-ink-2)); }
.cs-expand__bar [data-csd-expand-sub] { display: grid; gap: 2px; min-width: 0; }
.cs-expand__bar .cs-expand__dates { font-size: 1.08rem; line-height: 1.3; font-weight: 700; color: hsl(var(--ls-ink)); }
.cs-expand__bar [data-csd-expand-sub] small { font-size: .75rem; line-height: 1.4; font-weight: 600; color: hsl(var(--ls-ink-2)); }
.cs-expand__nav { margin-left: auto; display: flex; gap: 8px; }
.cs-expand__nav button {
    border: 1px solid hsl(var(--ls-glass-line));
    background: var(--cs-control-fill);
    box-shadow: inset 0 1px 0 hsl(var(--ls-glass-rim));
    color: hsl(var(--ls-ink));
    border-radius: var(--ls-r-capsule);
    padding: 6px 14px;
    font-size: 0.82rem;
    font-weight: 800;
    cursor: pointer;
}
.cs-expand__nav button:hover { background: color-mix(in srgb, hsl(var(--ls-primary)) 12%, var(--cs-control-fill)); }
.cs-expand__nav a[data-csd-expand-editor], .cs-expand__nav a[data-csd-expand-editor]:link, .cs-expand__nav a[data-csd-expand-editor]:visited {
    display: inline-flex; align-items: center; border: 1px solid hsl(var(--ls-primary) / .5); background: color-mix(in srgb, hsl(var(--ls-primary)) 16%, var(--cs-control-fill));
    box-shadow: inset 0 1px 0 hsl(var(--ls-glass-rim)); color: hsl(var(--ls-ink)); border-radius: var(--ls-r-capsule); padding: 6px 14px; font-size: 0.82rem; font-weight: 800; text-decoration: none;
}
.cs-expand__nav a[data-csd-expand-editor]:hover { background: color-mix(in srgb, hsl(var(--ls-primary)) 28%, var(--cs-control-fill)); }
.cs-expand__nav a[data-csd-expand-editor][hidden] { display: none; }
.cs-expand__body { padding: 16px 20px 20px; min-height: 0; position: relative; }
/* Routing space belongs to the timetable's scrollable canvas. Endpoints follow
   the visible lesson bounds; previews stay above lines and remain clickable. */
.cs-expand__body:has(.cs-change-map) { overflow: auto; overscroll-behavior: contain; }
.cs-change-map { position: absolute; inset: 0 8px; min-height: 580px; }
.cs-change-map > .cs-grid { inset: 32px 48px 28px; gap: 12px; min-width: 0; }
.cs-change-lines { position: absolute; inset: 0; width: 100%; height: 100%; overflow: visible; z-index: 2; pointer-events: none; }
.cs-change-line { fill: none; stroke-width: 2; stroke-linejoin: round; stroke-linecap: round; }
.cs-change-line-label { pointer-events: all; cursor: pointer; outline: none; }
.cs-change-line-label rect { fill: hsl(var(--ls-surface-1)); stroke: currentColor; stroke-width: 1; }
.cs-change-line-label text { fill: hsl(var(--ls-ink)); font: 600 12px var(--ls-font-sans); text-anchor: middle; dominant-baseline: central; }
.cs-change-line-label:is(:hover,:focus-visible) rect { fill: hsl(var(--ls-surface-2)); stroke-width: 2; }
.cs-change-line-origin { fill: hsl(var(--ls-surface-1)); stroke-width: 1.7; }
.cs-change-line-fallback { position: absolute; top: 3px; left: 100px; right: 48px; font-size: 11px; color: hsl(var(--ls-ink-2)); pointer-events: none; }
@media (prefers-reduced-motion: reduce) {
    .cs-card, .cs-expand, .cs-expand__card { transition: none; will-change: auto; }
    .cs-expand__card { transform: none; isolation: isolate; }
}

/* The local recipe follows the same opaque opt-outs as the shared material
   layer. It changes fills, not the transparent full-screen event boundary. */
:root:is([data-lq-glass="off"], [data-lq-tier="C"], [data-lq-contrast="more"]) :is(.cs-deck, .cs-expand) {
    --cs-fill: hsl(var(--ls-surface-1));
    --cs-raised-fill: hsl(var(--ls-surface-1));
    --cs-inset-fill: hsl(var(--ls-surface-2));
    --cs-control-fill: hsl(var(--ls-surface-2));
    --cs-live-blur: none;
    --ls-glass-sheen: none;
    --ls-glass-line: var(--ls-line);
}
@supports not ((backdrop-filter: blur(1px)) or (-webkit-backdrop-filter: blur(1px))) {
    .cs-deck, .cs-expand {
        --cs-fill: hsl(var(--ls-surface-1));
        --cs-raised-fill: hsl(var(--ls-surface-1));
        --cs-inset-fill: hsl(var(--ls-surface-2));
        --cs-control-fill: hsl(var(--ls-surface-2));
        --cs-live-blur: none;
    }
}
@media (prefers-reduced-transparency: reduce), (prefers-contrast: more) {
    .cs-deck, .cs-expand {
        --cs-fill: hsl(var(--ls-surface-1));
        --cs-raised-fill: hsl(var(--ls-surface-1));
        --cs-inset-fill: hsl(var(--ls-surface-2));
        --cs-control-fill: hsl(var(--ls-surface-2));
        --cs-live-blur: none;
        --ls-glass-sheen: none;
        --ls-glass-line: var(--ls-line);
    }
    .cs-lesson__surface { box-shadow: inset 0 0 0 1px currentColor; }
}
@media (forced-colors: active) {
    .cs-deck, .cs-expand { --cs-live-blur: none; }
    .cs-expand__card::before { background: Canvas; backdrop-filter: none; -webkit-backdrop-filter: none; }
    :is(.cs-card, .cs-expand__card, .cs-stage, .cs-stage__hint, .cs-lesson__surface,
        .cs-card__bar, .cs-expand__bar, .cs-card__badge, .cs-adjustment-label,
        .cs-deck-term, .cs-deck-nav__btn, .cs-expand__nav button,
        .cs-grid__corner, .cs-grid__day, .cs-grid__section, .cs-grid__band) {
        background: Canvas; color: CanvasText; border-color: CanvasText;
        box-shadow: none; text-shadow: none;
    }
    .cs-lesson__surface { border: 1px solid CanvasText; }
    .cs-lesson.cs-lesson--pending { border-color: Highlight; }
    .cs-grid__cellbg { background: Canvas; opacity: 1; }
    .cs-grid__day--today, .cs-card__badge.is-current { border-color: Highlight; }
    .cs-lesson.is-counterpart-focus { outline-color: Highlight; }
    .cs-change-line-label rect, .cs-change-line-origin { fill: Canvas; stroke: CanvasText; }
    .cs-change-line-label text { fill: CanvasText; }
}

.cs-empty {
    display: grid;
    place-items: center;
    gap: 8px;
    padding: 60px 20px;
    text-align: center;
    color: hsl(var(--ls-ink-2));
}
.cs-empty strong { color: hsl(var(--ls-ink)); font-size: 1rem; }
.cs-empty a { color: hsl(var(--ls-primary)); font-weight: 800; }

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
