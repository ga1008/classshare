/**
 * 课表编辑模式样式（液态玻璃）。只读共享 LQ/LS 令牌；材质分层与
 * course_schedule_styles.js 一致：面板 = content，浮起卡/抽屉 = raised，
 * 控件 = control，网格底 = inset。拖拽态、放置态、草稿态用主色/警示色的
 * color-mix 派生，不写字面颜色。
 */
export const EDITOR_CSS = `
.cse-page .manage-content { max-width: none; }
.cse-root {
    --cse-fill: hsl(var(--ls-glass-fill-content));
    --cse-raised: hsl(var(--ls-glass-fill-strong));
    --cse-inset: hsl(var(--ls-glass-fill-inset));
    --cse-control: hsl(var(--ls-glass-fill-control));
    --cse-blur: blur(var(--ls-blur-regular)) saturate(var(--ls-glass-saturate));
    display: grid; gap: 14px; color: hsl(var(--ls-ink)); min-width: 0;
}
.cse-toolbar {
    display: flex; flex-wrap: wrap; align-items: center; gap: 12px 16px;
    padding: 12px 16px; border-radius: var(--ls-r-lg);
    background: var(--cse-fill); background-image: var(--ls-glass-sheen);
    border: 1px solid hsl(var(--ls-glass-line)); box-shadow: var(--ls-glass-shadow);
}
.cse-toolbar__term { display: flex; align-items: center; gap: 8px; }
.cse-toolbar__term label { font-size: .8rem; font-weight: 700; color: hsl(var(--ls-ink-2)); }
.cse-select {
    min-height: 36px; padding: 6px 30px 6px 12px; font: 600 .86rem/1.3 var(--ls-font-sans); color: hsl(var(--ls-ink));
    border: 1px solid hsl(var(--ls-glass-line)); border-radius: var(--ls-r-capsule); background: var(--cse-control);
    box-shadow: inset 0 1px 0 hsl(var(--ls-glass-rim)); appearance: none; -webkit-appearance: none;
    background-image: linear-gradient(45deg, transparent 50%, currentColor 50%), linear-gradient(135deg, currentColor 50%, transparent 50%);
    background-position: calc(100% - 16px) 55%, calc(100% - 11px) 55%; background-size: 5px 5px; background-repeat: no-repeat;
}
.cse-select:focus-visible, .cse-input:focus-visible, .cse-textarea:focus-visible { outline: 2px solid hsl(var(--ls-ring)); outline-offset: 2px; }
.cse-toolbar__meta { display: grid; gap: 2px; min-width: 0; }
.cse-toolbar__meta strong { font-size: .95rem; }
.cse-toolbar__meta span { font-size: .76rem; color: hsl(var(--ls-ink-2)); }
.cse-toolbar__spacer { flex: 1 1 auto; }
.cse-toolbar__actions { display: flex; flex-wrap: wrap; gap: 8px; }
.cse-btn {
    display: inline-flex; align-items: center; justify-content: center; gap: 6px; min-height: 36px; padding: 6px 16px;
    border-radius: var(--ls-r-capsule); border: 1px solid hsl(var(--ls-glass-line)); background: var(--cse-control);
    box-shadow: inset 0 1px 0 hsl(var(--ls-glass-rim)), var(--ls-shadow-1); color: hsl(var(--ls-ink));
    font: 750 .84rem/1.2 var(--ls-font-sans); cursor: pointer; text-decoration: none; white-space: nowrap;
    transition: background-color .18s ease, box-shadow .18s ease, transform .18s ease;
}
.cse-btn:hover { background: color-mix(in srgb, hsl(var(--ls-primary)) 12%, var(--cse-control)); }
.cse-btn:active { transform: translateY(1px); box-shadow: inset 0 1px 4px hsl(var(--ls-ink) / .16); }
.cse-btn:focus-visible { outline: 2px solid hsl(var(--ls-ring)); outline-offset: 2px; }
.cse-btn--primary { background: hsl(var(--ls-primary)); border-color: hsl(var(--ls-primary)); color: hsl(var(--ls-primary-foreground)); }
.cse-btn--primary:hover { background: color-mix(in srgb, hsl(var(--ls-primary)) 88%, black); }
.cse-btn--danger { color: hsl(var(--ls-destructive)); border-color: hsl(var(--ls-destructive) / .4); }
.cse-btn--danger:hover { background: color-mix(in srgb, hsl(var(--ls-destructive)) 12%, var(--cse-control)); }
.cse-btn--sm { min-height: 28px; padding: 3px 10px; font-size: .76rem; }
.cse-btn[disabled], .cse-btn[aria-disabled="true"] { opacity: .55; cursor: not-allowed; transform: none; }
.cse-btn__badge {
    min-width: 20px; padding: 1px 6px; border-radius: var(--ls-r-capsule); font-size: .7rem; text-align: center;
    background: hsl(var(--ls-surface-1) / .35); color: inherit;
}
.cse-btn--primary .cse-btn__badge { background: hsl(0 0% 100% / .25); }

.cse-layout { display: grid; grid-template-columns: 200px minmax(0, 1fr); gap: 14px; align-items: start; min-height: 640px; }
.cse-layout.has-drawer { grid-template-columns: 200px minmax(0, 1fr) 360px; }
.cse-weeks {
    position: sticky; top: 76px; display: grid; gap: 6px; max-height: calc(100dvh - 100px); overflow: auto; overscroll-behavior: contain;
    padding: 10px; border-radius: var(--ls-r-lg); background: var(--cse-fill); background-image: var(--ls-glass-sheen);
    border: 1px solid hsl(var(--ls-glass-line)); box-shadow: var(--ls-glass-shadow);
}
.cse-weeks__title { font-size: .74rem; font-weight: 800; letter-spacing: .08em; color: hsl(var(--ls-ink-2)); padding: 2px 6px 4px; }
.cse-week {
    display: flex; flex-direction: column; align-items: center; justify-content: flex-start; gap: 4px; padding: 12px 10px 10px; text-align: center;
    border-radius: var(--ls-r-md); border: 1px solid transparent; background: transparent; color: hsl(var(--ls-ink));
    text-align: left; cursor: pointer; font: inherit; transition: background-color .16s ease, border-color .16s ease, transform .16s ease;
}
.cse-week:hover { background: color-mix(in srgb, hsl(var(--ls-primary)) 8%, var(--cse-inset)); }
.cse-week:focus-visible { outline: 2px solid hsl(var(--ls-ring)); outline-offset: 1px; }
.cse-week strong { font-size: 1.02rem; font-weight: 850; line-height: 1.2; }
.cse-week small { font-size: .7rem; color: hsl(var(--ls-ink-2)); line-height: 1.3; }
.cse-week__count { font-size: .72rem; font-weight: 700; color: hsl(var(--ls-ink-2)); }
.cse-week__draft { display: inline-block; min-width: 18px; padding: 0 5px; border-radius: var(--ls-r-capsule); font-size: .66rem; text-align: center; background: color-mix(in srgb, hsl(var(--ls-warning)) 30%, var(--cse-control)); color: hsl(var(--ls-ink)); }
.cse-week.is-active { background: color-mix(in srgb, hsl(var(--ls-primary)) 16%, var(--cse-control)); border-color: hsl(var(--ls-primary) / .6); box-shadow: inset 0 1px 0 hsl(var(--ls-glass-rim)); }
.cse-week.is-current strong::after { content: '本周'; display: inline-block; margin-left: 6px; vertical-align: 2px; font-size: .62rem; font-weight: 700; padding: 1px 5px; border-radius: var(--ls-r-capsule); background: color-mix(in srgb, hsl(var(--ls-primary)) 22%, var(--cse-control)); }
.cse-week.is-drop-hover { background: color-mix(in srgb, hsl(var(--ls-primary)) 26%, var(--cse-control)); border-color: hsl(var(--ls-primary)); transform: scale(1.02); }

.cse-stage {
    position: relative; display: grid; grid-template-rows: auto auto minmax(0, 1fr); min-height: 640px; border-radius: var(--ls-r-lg);
    background: var(--cse-fill); background-image: var(--ls-glass-sheen); border: 1px solid hsl(var(--ls-glass-line)); box-shadow: var(--ls-glass-shadow);
    overflow: hidden;
}
.cse-stage__bar { display: flex; flex-wrap: wrap; align-items: center; gap: 10px 14px; padding: 12px 18px; border-bottom: 1px solid hsl(var(--ls-glass-line)); background: color-mix(in srgb, hsl(var(--ls-primary)) 10%, var(--cse-inset)); }
.cse-stage__bar strong { font-size: 1.3rem; font-weight: 850; letter-spacing: -.02em; }
.cse-stage__bar span { font-size: .84rem; color: hsl(var(--ls-ink-2)); }
.cse-stage__hint { margin-left: auto; font-size: .74rem; color: hsl(var(--ls-ink-2)); }
.cse-stage__body { position: relative; display: grid; gap: 12px; min-height: 600px; padding: 16px 20px 20px; }
.cse-stage__body > .cs-grid { position: relative; inset: auto; min-height: 560px; }
.cse-request-relations .lq-btn { max-width: 100%; white-space: normal; text-align: start; }
.cse-grid .cs-grid__cellbg { transition: background-color .12s ease, box-shadow .12s ease; }
.cse-grid .cs-grid__cellbg--locked { background: repeating-linear-gradient(135deg, transparent 0 6px, hsl(var(--ls-ink-3) / .12) 6px 8px), color-mix(in srgb, hsl(var(--ls-warning)) 8%, var(--cse-inset)); }
.cse-grid .cs-grid__cellbg.is-drop-ok { background: color-mix(in srgb, hsl(var(--ls-primary)) 32%, var(--cse-inset)); box-shadow: inset 0 0 0 2px hsl(var(--ls-primary) / .75); }
.cse-grid .cs-grid__cellbg.is-avail-block, .cse-grid .cs-grid__cellbg.is-avail-teacher { background: repeating-linear-gradient(135deg, transparent 0 5px, hsl(var(--ls-destructive) / .18) 5px 7px), color-mix(in srgb, hsl(var(--ls-destructive)) 10%, var(--cse-inset)); }
.cse-grid .cs-grid__cellbg.is-avail-room { background: color-mix(in srgb, hsl(var(--ls-warning)) 26%, var(--cse-inset)); }
.cse-grid .cs-grid__cellbg.is-avail-ok { background: color-mix(in srgb, hsl(var(--ls-success)) 16%, var(--cse-inset)); }
.cse-grid .cs-grid__cellbg.is-avail-unknown { background: var(--cse-inset); }
.cse-grid .cs-grid__cellbg.is-drop-warn { background: color-mix(in srgb, hsl(var(--ls-warning)) 40%, var(--cse-inset)); box-shadow: inset 0 0 0 2px hsl(var(--ls-warning) / .8); }
.cse-legend { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 12px; padding: 6px 18px 8px; font-size: .72rem; color: hsl(var(--ls-ink-2)); border-bottom: 1px solid hsl(var(--ls-glass-line)); }
.cse-legend[hidden] { display: none; }
.cse-legend__item { display: inline-flex; align-items: center; gap: 5px; font-weight: 700; }
.cse-legend__item::before { content: ''; width: 12px; height: 12px; border-radius: 3px; border: 1px solid hsl(var(--ls-glass-line)); }
.cse-legend__item--block::before { background: repeating-linear-gradient(135deg, transparent 0 3px, hsl(var(--ls-destructive) / .35) 3px 5px), color-mix(in srgb, hsl(var(--ls-destructive)) 12%, var(--cse-inset)); }
.cse-legend__item--room::before { background: color-mix(in srgb, hsl(var(--ls-warning)) 30%, var(--cse-inset)); }
.cse-legend__item--ok::before { background: color-mix(in srgb, hsl(var(--ls-success)) 20%, var(--cse-inset)); }
.cse-legend__item--unknown::before { background: var(--cse-inset); }
.cse-legend__note { flex-basis: 100%; font-weight: 500; }
.cse-tag--room { background: color-mix(in srgb, hsl(var(--ls-warning)) 30%, var(--cse-control)); }
.cse-tag--ok { background: color-mix(in srgb, hsl(var(--ls-success)) 24%, var(--cse-control)); }
.cse-verdict { padding: 8px 12px; border-radius: var(--ls-r-md); border: 1px solid hsl(var(--ls-glass-line)); font-size: .78rem; line-height: 1.5; background: var(--cse-inset); }
.cse-verdict ul { margin: 4px 0 0; padding-left: 18px; }
.cse-verdict--block { background: color-mix(in srgb, hsl(var(--ls-destructive)) 12%, var(--cse-inset)); }
.cse-verdict--room { background: color-mix(in srgb, hsl(var(--ls-warning)) 16%, var(--cse-inset)); }
.cse-verdict--ok { background: color-mix(in srgb, hsl(var(--ls-success)) 14%, var(--cse-inset)); }
.cse-free-rooms__head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.cse-free-rooms__grid { display: grid; gap: 4px; max-height: 240px; overflow: auto; overscroll-behavior: contain; border: 1px solid hsl(var(--ls-glass-line)); border-radius: var(--ls-r-md); background: var(--cse-inset); }
.cse-conflict-list { margin: 4px 0 0; padding-left: 18px; }
.cse-avail-meta { flex-basis: 100%; font-size: .74rem; color: hsl(var(--ls-ink-2)); }
.cse-grid .cs-grid__cellbg.is-drop-bad { background: color-mix(in srgb, hsl(var(--ls-destructive)) 24%, var(--cse-inset)); box-shadow: inset 0 0 0 2px hsl(var(--ls-destructive) / .7); }
.cse-lesson { cursor: grab; touch-action: none; user-select: none; -webkit-user-select: none; }
.cse-lesson:active { cursor: grabbing; }
.cse-lesson .cs-lesson__surface { padding: 6px 8px; }
.cse-lesson.is-selected .cs-lesson__surface { box-shadow: inset 0 0 0 2px hsl(var(--ls-primary)), var(--ls-shadow-1); }
.cse-lesson.is-drag-source { opacity: .35; }
.cse-lesson--moved .cs-lesson__surface { opacity: .55; background-image: repeating-linear-gradient(135deg, transparent 0 8px, hsl(var(--ls-ink-3) / .08) 8px 10px), var(--ls-glass-sheen); }
.cse-lesson--ghost { border: 2px dashed hsl(var(--ls-primary) / .8); padding: 3px; border-radius: var(--ls-r-md); }
.cse-lesson--ghost .cs-lesson__surface { background-color: color-mix(in srgb, hsl(var(--ls-primary)) 14%, var(--cse-raised)); }
.cse-lesson--ghost[data-status="pushed"] { border-color: hsl(var(--ls-success) / .85); }
.cse-lesson--ghost[data-status="conflict"], .cse-lesson--ghost[data-status="failed"] { border-color: hsl(var(--ls-destructive) / .8); }
.cse-tag {
    display: inline-block; max-width: 100%; padding: 1px 6px; border-radius: var(--ls-r-capsule); font-size: .64rem; font-weight: 750; line-height: 1.5;
    background: color-mix(in srgb, hsl(var(--ls-primary)) 20%, var(--cse-control)); color: hsl(var(--ls-ink)); white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.cse-tag--pushed { background: color-mix(in srgb, hsl(var(--ls-success)) 24%, var(--cse-control)); }
.cse-tag--conflict, .cse-tag--failed { background: color-mix(in srgb, hsl(var(--ls-destructive)) 20%, var(--cse-control)); }
.cse-tag--muted { background: var(--cse-control); color: hsl(var(--ls-ink-2)); }
.cse-drag-ghost {
    position: fixed; z-index: calc(var(--ls-z-modal) + 5); pointer-events: none; width: 150px; padding: 8px 10px; border-radius: var(--ls-r-md);
    background: var(--cse-raised); background-image: var(--ls-glass-sheen); border: 1px solid hsl(var(--ls-glass-line));
    box-shadow: var(--ls-glass-shadow-strong); backdrop-filter: var(--cse-blur); -webkit-backdrop-filter: var(--cse-blur);
    color: hsl(var(--ls-ink)); font: 700 .8rem/1.3 var(--ls-font-sans); transform: translate(-50%, -50%) rotate(-2deg);
}
.cse-drag-ghost small { display: block; font-weight: 600; font-size: .68rem; color: hsl(var(--ls-ink-2)); }
body.cse-dragging { cursor: grabbing; }
body.cse-dragging .cse-lesson { pointer-events: none; }
body.cse-dragging .cs-lesson-slot { pointer-events: none; }
.cse-empty { display: grid; place-items: center; gap: 8px; padding: 80px 20px; text-align: center; color: hsl(var(--ls-ink-2)); }
.cse-empty strong { color: hsl(var(--ls-ink)); font-size: 1rem; }

.cse-drawer {
    position: sticky; top: 76px; display: grid; grid-template-rows: auto minmax(0, 1fr) auto; max-height: calc(100dvh - 100px);
    border-radius: var(--ls-r-lg); border: 1px solid hsl(var(--ls-glass-line)); box-shadow: var(--ls-glass-shadow-strong);
    background: var(--cse-raised); background-image: var(--ls-glass-sheen); overflow: hidden; min-width: 0;
    animation: cseDrawerIn .26s cubic-bezier(.22,.8,.3,1) backwards;
}
@keyframes cseDrawerIn { from { opacity: 0; transform: translateX(24px); } to { opacity: 1; transform: none; } }
.cse-drawer__head { display: flex; align-items: flex-start; gap: 10px; padding: 14px 16px 10px; border-bottom: 1px solid hsl(var(--ls-glass-line)); }
.cse-drawer__head h3 { margin: 0; font-size: 1.02rem; font-weight: 850; line-height: 1.3; }
.cse-drawer__head p { margin: 2px 0 0; font-size: .76rem; color: hsl(var(--ls-ink-2)); }
.cse-drawer__close { margin-left: auto; width: 30px; height: 30px; padding: 0; border-radius: 50%; border: 1px solid hsl(var(--ls-glass-line)); background: var(--cse-control); color: hsl(var(--ls-ink)); cursor: pointer; font-size: 1rem; line-height: 1; }
.cse-drawer__close:hover { background: color-mix(in srgb, hsl(var(--ls-primary)) 12%, var(--cse-control)); }
.cse-drawer__body { overflow: auto; overscroll-behavior: contain; padding: 12px 16px; display: grid; gap: 14px; }
.cse-drawer__foot { display: flex; flex-wrap: wrap; gap: 8px; padding: 12px 16px; border-top: 1px solid hsl(var(--ls-glass-line)); background: color-mix(in srgb, hsl(var(--ls-primary)) 6%, var(--cse-inset)); }
.cse-section { display: grid; gap: 8px; }
.cse-section__title { font-size: .72rem; font-weight: 800; letter-spacing: .08em; color: hsl(var(--ls-ink-2)); text-transform: uppercase; }
.cse-kv { display: grid; grid-template-columns: 64px minmax(0, 1fr); gap: 4px 10px; font-size: .82rem; }
.cse-kv dt { color: hsl(var(--ls-ink-2)); }
.cse-kv dd { margin: 0; overflow-wrap: anywhere; }
.cse-field { display: grid; gap: 4px; }
.cse-field label { font-size: .76rem; font-weight: 700; color: hsl(var(--ls-ink-2)); }
.cse-field-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px 12px; }
.cse-field-grid .cse-field--full { grid-column: 1 / -1; }
.cse-input, .cse-textarea {
    width: 100%; min-height: 36px; padding: 7px 12px; font: 600 .86rem/1.4 var(--ls-font-sans); color: hsl(var(--ls-ink));
    border: 1px solid hsl(var(--ls-glass-line)); border-radius: var(--ls-r-md); background: var(--cse-control); box-shadow: inset 0 1px 0 hsl(var(--ls-glass-rim));
}
.cse-textarea { min-height: 72px; resize: vertical; }
.cse-field__hint { font-size: .7rem; color: hsl(var(--ls-ink-2)); }
.cse-rooms { position: relative; }
.cse-rooms__list {
    position: absolute; left: 0; right: 0; top: calc(100% + 4px); z-index: 5; max-height: 220px; overflow: auto; display: grid;
    border-radius: var(--ls-r-md); border: 1px solid hsl(var(--ls-glass-line)); background: var(--cse-raised); background-image: var(--ls-glass-sheen);
    box-shadow: var(--ls-glass-shadow-strong); backdrop-filter: var(--cse-blur); -webkit-backdrop-filter: var(--cse-blur);
}
.cse-rooms__list[hidden] { display: none; }
.cse-rooms__item { display: grid; gap: 1px; padding: 7px 12px; text-align: left; border: 0; background: transparent; color: hsl(var(--ls-ink)); font: 600 .8rem/1.3 var(--ls-font-sans); cursor: pointer; }
.cse-rooms__item small { font-weight: 500; font-size: .68rem; color: hsl(var(--ls-ink-2)); }
.cse-rooms__item:hover, .cse-rooms__item:focus-visible { background: color-mix(in srgb, hsl(var(--ls-primary)) 12%, transparent); outline: none; }
.cse-rooms__empty { padding: 8px 12px; font-size: .74rem; color: hsl(var(--ls-ink-2)); }
.cse-materials { display: grid; gap: 6px; }
.cse-materials__item { display: flex; align-items: center; gap: 8px; padding: 6px 10px; border-radius: var(--ls-r-md); background: var(--cse-inset); border: 1px solid hsl(var(--ls-glass-line)); font-size: .78rem; }
.cse-materials__item a { color: hsl(var(--ls-primary)); font-weight: 700; text-decoration: none; }
.cse-materials__empty { font-size: .76rem; color: hsl(var(--ls-ink-2)); }
.cse-status {
    padding: 8px 12px; border-radius: var(--ls-r-md); font-size: .78rem; line-height: 1.5; border: 1px solid hsl(var(--ls-glass-line));
    background: color-mix(in srgb, hsl(var(--ls-primary)) 10%, var(--cse-inset));
}
.cse-status--pushed { background: color-mix(in srgb, hsl(var(--ls-success)) 14%, var(--cse-inset)); }
.cse-status--conflict, .cse-status--failed { background: color-mix(in srgb, hsl(var(--ls-destructive)) 12%, var(--cse-inset)); }

.cse-drafts {
    display: grid; grid-template-columns: minmax(0, 1fr); gap: 12px; min-width: 0; padding: 14px 16px; border-radius: var(--ls-r-lg);
}
.cse-drafts__head { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 14px; }
.cse-drafts__head h3 { margin: 0; font-size: .98rem; font-weight: 850; }
.cse-drafts__head p { margin: 0; font-size: .76rem; color: hsl(var(--ls-ink-2)); }
.cse-drafts__list, .cse-change-group { display: grid; grid-template-columns: minmax(0, 1fr); min-width: 0; gap: 8px; }
.cse-change-group + .cse-change-group { margin-top: 6px; }
.cse-change-group__head { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 12px; min-width: 0; font-size: .76rem; color: hsl(var(--ls-ink-2)); }
.cse-change-group__head > span { overflow-wrap: anywhere; }
.cse-draft {
    display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 8px 12px; min-width: 0; padding: 12px;
    border-radius: var(--ls-r-md);
}
.cse-draft__title { grid-column: 1 / -1; display: flex; flex-wrap: wrap; align-items: center; gap: 8px 12px; min-width: 0; font-size: .88rem; }
.cse-draft__title > strong { overflow-wrap: anywhere; }
.cse-draft__details { display: contents; }
.cse-draft__status { grid-column: 1; display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
.cse-detail-chip { flex: 0 0 11rem; width: 11rem; max-width: 100%; justify-content: flex-start; text-align: start; }
.cse-detail-chip[data-cse-detail="remote"] { flex-basis: 6.5rem; width: 6.5rem; }
.cse-draft__compare, .cse-comparison { grid-column: 1; min-width: 0; max-width: 100%; }
.cse-comparison .lq-table { min-width: 0; width: max-content; table-layout: auto; }
.cse-comparison .lq-table :is(th, td) { min-width: 0; max-width: none; height: auto; padding: 3px 4px; border: 0; white-space: nowrap; }
.cse-comparison .lq-table caption, .cse-comparison .lq-table thead { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip-path: inset(50%); white-space: nowrap; }
.cse-comparison .lq-table tbody th { padding-inline: 6px 10px; }
.cse-comparison .lq-chip { box-sizing: border-box; width: 100%; justify-content: flex-start; font-weight: 500; }
.cse-comparison .is-changed .lq-chip { font-weight: 800; }
.cse-detail-body { display: grid; gap: 10px; min-width: 0; overflow-wrap: anywhere; white-space: pre-wrap; }
.cse-detail-body p { margin: 0; }
.cse-detail-body > .lq-btn { height: auto; white-space: normal; justify-content: flex-start; text-align: start; }
.cse-detail-body .lq-btn__label { white-space: normal; overflow-wrap: anywhere; }
.cse-draft__msg { grid-column: 1; font-size: .74rem; color: hsl(var(--ls-ink-2)); overflow-wrap: anywhere; }
.cse-draft__actions { grid-column: 2; grid-row: 2 / span 2; display: flex; flex-direction: column; gap: 6px; align-items: stretch; justify-content: center; }
@media (max-width: 900px) {
    .cse-draft { grid-template-columns: minmax(0, 1fr); }
    .cse-draft__actions { grid-column: 1; grid-row: auto; flex-direction: row; flex-wrap: wrap; justify-content: flex-start; }
}
.cse-drafts__next {
    display: flex; flex-wrap: wrap; align-items: center; gap: 8px 12px; padding: 6px 0; font-size: .8rem;
}
.cse-drafts__next a { color: hsl(var(--ls-primary)); font-weight: 800; }
.cse-feedback { font-size: .8rem; color: hsl(var(--ls-ink-2)); }
.cse-feedback:empty { display: none; }

:root:is([data-lq-glass="off"], [data-lq-tier="C"], [data-lq-contrast="more"]) .cse-root,
:root:is([data-lq-glass="off"], [data-lq-tier="C"], [data-lq-contrast="more"]) .cse-drag-ghost {
    --cse-fill: hsl(var(--ls-surface-1)); --cse-raised: hsl(var(--ls-surface-1)); --cse-inset: hsl(var(--ls-surface-2));
    --cse-control: hsl(var(--ls-surface-2)); --cse-blur: none; --ls-glass-sheen: none; --ls-glass-line: var(--ls-line);
}
@supports not ((backdrop-filter: blur(1px)) or (-webkit-backdrop-filter: blur(1px))) {
    .cse-root, .cse-drag-ghost { --cse-fill: hsl(var(--ls-surface-1)); --cse-raised: hsl(var(--ls-surface-1)); --cse-inset: hsl(var(--ls-surface-2)); --cse-control: hsl(var(--ls-surface-2)); --cse-blur: none; }
}
@media (prefers-reduced-motion: reduce) {
    .cse-drawer { animation: none; }
    .cse-btn, .cse-week, .cse-grid .cs-grid__cellbg { transition: none; }
}
@media (max-width: 1100px) {
    .cse-layout, .cse-layout.has-drawer { grid-template-columns: 160px minmax(0, 1fr); }
    .cse-drawer { position: fixed; inset: auto 12px 12px 12px; top: auto; max-height: 70dvh; z-index: var(--ls-z-modal); }
}
@media (max-width: 760px) {
    .cse-layout, .cse-layout.has-drawer { grid-template-columns: minmax(0, 1fr); }
    .cse-weeks { position: static; max-height: 160px; grid-auto-flow: column; grid-auto-columns: 140px; overflow-x: auto; }
    .cse-stage__body { overflow: auto; }
    .cse-stage__body > .cs-grid { position: relative; inset: auto; min-width: 760px; min-height: 560px; }
}

/* ---- 节假日 / 已过去 / 调休 ---- */
.cse-layout { position: relative; }
.cse-swaps { position: absolute; inset: 0; width: 100%; height: 100%; pointer-events: none; z-index: 6; overflow: visible; }
.cse-swaps[hidden] { display: none; }
.cse-swap__line { fill: none; stroke-width: 2.2; stroke-linecap: round; stroke-linejoin: round; stroke-dasharray: 7 5; opacity: .92; filter: drop-shadow(0 1px 2px hsl(var(--ls-ink) / .18)); }
.cse-swap__origin { fill: var(--cse-raised); stroke-width: 2; }
.cse-swap__label rect { fill: var(--cse-raised); stroke: currentColor; stroke-width: 1.2; }
.cse-swap__label text { fill: currentColor; font: 800 .68rem/1 var(--ls-font-sans); text-anchor: middle; dominant-baseline: central; }
.cse-dayhead { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 2px; line-height: 1.15; }
.cse-dayhead__name small { display: block; font-size: .64rem; font-weight: 600; color: hsl(var(--ls-ink-3)); }
.cse-dayhead__tag { display: inline-block; max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; padding: 1px 6px; border-radius: var(--ls-r-capsule); font-size: .62rem; font-weight: 800; line-height: 1.4; }
.cse-dayhead__tag--holiday { background: color-mix(in srgb, hsl(var(--ls-destructive)) 18%, var(--cse-control)); color: hsl(var(--ls-destructive)); }
.cse-dayhead__tag--workday { background: color-mix(in srgb, var(--cse-swap, hsl(var(--ls-primary))) 20%, var(--cse-control)); color: var(--cse-swap, hsl(var(--ls-primary))); border: 1px dashed var(--cse-swap, hsl(var(--ls-primary))); }
.cse-dayhead__tag--past { background: var(--cse-control); color: hsl(var(--ls-ink-3)); }
.cse-dayhead--holiday .cse-dayhead__name, .cse-dayhead--past .cse-dayhead__name { color: hsl(var(--ls-ink-3)); }
.cse-grid .cs-grid__cellbg--holiday { background: repeating-linear-gradient(135deg, transparent 0 6px, hsl(var(--ls-destructive) / .16) 6px 8px), color-mix(in srgb, hsl(var(--ls-destructive)) 7%, var(--cse-inset)); cursor: not-allowed; }
.cse-grid .cs-grid__cellbg--past { background: repeating-linear-gradient(135deg, transparent 0 6px, hsl(var(--ls-ink-3) / .14) 6px 8px), var(--cse-inset); opacity: .7; cursor: not-allowed; }
.cse-grid .cs-grid__cellbg--workday { box-shadow: inset 2px 0 0 color-mix(in srgb, hsl(var(--ls-primary)) 45%, transparent); }
.cse-grid .cs-grid__section.cse-section--pair { font-weight: 900; color: hsl(var(--ls-ink)); }
.cse-lesson--mirror .cs-lesson__surface { opacity: .72; border-style: dashed; background-image: repeating-linear-gradient(45deg, transparent 0 8px, hsl(var(--ls-primary) / .08) 8px 10px), var(--ls-glass-sheen); cursor: pointer; }
.cse-lesson--past .cs-lesson__surface { opacity: .6; filter: grayscale(.5); cursor: not-allowed; }
.cse-tag--workday { background: color-mix(in srgb, hsl(var(--ls-primary)) 22%, var(--cse-control)); color: hsl(var(--ls-primary)); }
.cse-status--workday { background: color-mix(in srgb, hsl(var(--ls-primary)) 12%, var(--cse-control)); color: hsl(var(--ls-ink)); }
.cse-status--muted { background: var(--cse-control); color: hsl(var(--ls-ink-2)); }
.cse-legend__item--holiday::before { background: repeating-linear-gradient(135deg, transparent 0 3px, hsl(var(--ls-destructive) / .3) 3px 5px), color-mix(in srgb, hsl(var(--ls-destructive)) 8%, var(--cse-inset)); }
.cse-legend__item--past::before { background: repeating-linear-gradient(135deg, transparent 0 3px, hsl(var(--ls-ink-3) / .3) 3px 5px), var(--cse-inset); }
.cse-legend__item--workday::before { background: var(--cse-inset); box-shadow: inset 3px 0 0 hsl(var(--ls-primary)); }
.cse-calnote { display: flex; flex-wrap: wrap; gap: 6px 10px; padding: 6px 10px; border-radius: var(--ls-r-md); background: var(--cse-inset); font-size: .74rem; }
.cse-calnote[hidden] { display: none; }
.cse-calnote__item { display: inline-flex; align-items: center; gap: 5px; padding: 2px 8px; border-radius: var(--ls-r-capsule); background: var(--cse-control); color: hsl(var(--ls-ink-2)); }
.cse-calnote__item--holiday { color: hsl(var(--ls-destructive)); font-weight: 700; }
.cse-calnote__item--swap { border: 1px dashed var(--cse-swap); color: hsl(var(--ls-ink)); }
.cse-calnote__item--swap i { width: 9px; height: 9px; border-radius: 50%; background: var(--cse-swap); }
.cse-calnote__item small { font-size: .62rem; color: hsl(var(--ls-ink-3)); }
.cse-week__marks { display: flex; flex-wrap: wrap; justify-content: center; gap: 3px; margin-top: 2px; }
.cse-week__holiday, .cse-week__swap { display: inline-block; padding: 0 5px; border-radius: var(--ls-r-capsule); font-size: .6rem; font-weight: 800; line-height: 1.5; }
.cse-week__holiday { background: color-mix(in srgb, hsl(var(--ls-destructive)) 16%, var(--cse-control)); color: hsl(var(--ls-destructive)); }
.cse-week__swap { background: color-mix(in srgb, var(--cse-swap) 18%, var(--cse-control)); color: var(--cse-swap); border: 1px dashed var(--cse-swap); }
.cse-week__past { font-style: normal; margin-left: 4px; font-size: .6rem; font-weight: 700; color: hsl(var(--ls-ink-3)); }
.cse-week.is-past { opacity: .72; }
.cse-reseq__course { padding: 10px 12px; border-radius: var(--ls-r-md); background: var(--cse-control); border: 1px solid hsl(var(--ls-glass-line)); }
.cse-reseq__course + .cse-reseq__course { margin-top: 8px; }
.cse-reseq__title { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 6px 12px; font-size: .82rem; }
.cse-reseq__title span { color: hsl(var(--ls-ink-3)); font-size: .74rem; }
.cse-reseq__list { margin: 8px 0 0; padding-left: 18px; font-size: .78rem; display: grid; gap: 4px; }
.cse-reseq__list li.is-direct { font-weight: 700; }
.cse-reseq__list i { margin-left: 6px; font-style: normal; font-size: .64rem; padding: 0 5px; border-radius: var(--ls-r-capsule); background: color-mix(in srgb, hsl(var(--ls-primary)) 20%, var(--cse-control)); color: hsl(var(--ls-primary)); }
.cse-reseq__old { color: hsl(var(--ls-ink-3)); text-decoration: line-through; }
.cse-reseq__new { color: hsl(var(--ls-ink)); font-weight: 700; }

/* ---- 第四轮：周卡发光、工具栏放大、抽屉布局、证明材料、保存弹窗 ---- */
.cse-toolbar--lg { padding: 16px 20px; gap: 14px 20px; }
.cse-toolbar--lg .cse-btn { min-height: 42px; padding: 8px 20px; font-size: .92rem; }
.cse-toolbar--lg .cse-toolbar__term label { font-size: .9rem; }
.cse-toolbar--lg .cse-toolbar__term .lq-dropdown { min-width: 260px; }
.cse-toolbar--lg .cse-toolbar__meta strong { font-size: 1.04rem; }
.cse-toolbar--lg .cse-toolbar__meta span { font-size: .84rem; }
.cse-week { position: relative; border-radius: 18px; }
.cse-week.is-droppable { box-shadow: 0 0 0 1px hsl(var(--ls-success) / .55), 0 0 14px 2px hsl(var(--ls-success) / .38), inset 0 1px 0 hsl(var(--ls-glass-rim)); border-color: hsl(var(--ls-success) / .6); }
.cse-week.is-droppable.is-active { box-shadow: 0 0 0 1px hsl(var(--ls-success) / .7), 0 0 18px 3px hsl(var(--ls-success) / .45), inset 0 1px 0 hsl(var(--ls-glass-rim)); }
.cse-week.is-blocked, .cse-week.is-locked { box-shadow: 0 0 0 1px hsl(var(--ls-ink-3) / .3), 0 0 10px 1px hsl(var(--ls-ink-3) / .18); border-color: hsl(var(--ls-ink-3) / .3); }
.cse-week.is-locked { opacity: .62; cursor: default; }
.cse-week.is-locked .cse-week__title, .cse-week.is-blocked .cse-week__title { color: hsl(var(--ls-ink-3)); }
.cse-dragging .cse-week.is-locked, .cse-dragging .cse-week.is-blocked { cursor: not-allowed; }
.cse-week__range { display: block; }
.cse-grid .cs-grid__cellbg--ended { background: repeating-linear-gradient(135deg, transparent 0 6px, hsl(var(--ls-ink-3) / .14) 6px 8px), var(--cse-inset); opacity: .7; cursor: not-allowed; }
.cse-swap__column { fill: none; stroke-width: 1.2; stroke-dasharray: 4 4; opacity: .85; }
.cse-swap__origin { stroke-width: 1.5; }
.cse-origin { display: grid; gap: 4px; padding: 10px 12px; border-radius: var(--ls-r-md); background: var(--cse-inset); border: 1px solid hsl(var(--ls-glass-line)); }
.cse-origin__time { font-weight: 800; font-size: .92rem; }
.cse-origin__time small { margin-left: 6px; font-weight: 600; color: hsl(var(--ls-ink-2)); }
.cse-origin__meta { display: flex; flex-wrap: wrap; gap: 4px 12px; font-size: .78rem; color: hsl(var(--ls-ink-2)); }
.cse-section__hint { margin-left: 8px; font-size: .7rem; font-weight: 600; letter-spacing: 0; text-transform: none; color: hsl(var(--ls-ink-3)); }
.cse-field__labelrow { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.cse-btn--ai { background: color-mix(in srgb, hsl(var(--ls-primary)) 14%, var(--cse-control)); color: hsl(var(--ls-primary)); }
.cse-drawer .lq-dropdown__trigger { border-radius: var(--ls-r-md); background: var(--cse-control); border-color: hsl(var(--ls-glass-line)); font: 600 .86rem/1.4 var(--ls-font-sans); min-height: 38px; }
.cse-proofs { display: flex; flex-wrap: wrap; gap: 6px; }
.cse-proof { display: inline-flex; align-items: center; gap: 6px; max-width: 100%; padding: 4px 6px 4px 10px; border-radius: var(--ls-r-capsule); background: var(--cse-control); border: 1px solid hsl(var(--ls-glass-line)); font-size: .78rem; }
.cse-proof a { color: hsl(var(--ls-primary)); text-decoration: none; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 180px; }
.cse-proof small { color: hsl(var(--ls-ink-3)); }
.cse-proof__remove { width: 20px; height: 20px; padding: 0; border: 0; border-radius: 50%; background: transparent; color: hsl(var(--ls-ink-2)); cursor: pointer; }
.cse-proof__remove:hover:not(:disabled) { background: color-mix(in srgb, hsl(var(--ls-destructive)) 16%, var(--cse-control)); color: hsl(var(--ls-destructive)); }
.cse-upload { cursor: pointer; }
.cse-upload.is-busy { opacity: .6; pointer-events: none; }
.cse-draft__warn { font-style: normal; color: hsl(var(--ls-warning)); font-weight: 700; }
.cse-push { display: grid; gap: 12px; font-size: .86rem; }
.cse-push__lead { margin: 0; color: hsl(var(--ls-ink-2)); }
.cse-push__list { list-style: none; margin: 0; padding: 0; display: grid; gap: 6px; max-height: 240px; overflow: auto; }
.cse-push__item { display: grid; gap: 2px; padding: 8px 10px; border-radius: var(--ls-r-md); background: hsl(var(--ls-glass-fill-inset)); border: 1px solid hsl(var(--ls-glass-line)); }
.cse-push__item small { color: hsl(var(--ls-ink-2)); }
.cse-push__item em { font-style: normal; color: hsl(var(--ls-warning)); font-weight: 700; }
.cse-push__block { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 12px; padding: 10px 12px; border-radius: var(--ls-r-md); background: hsl(var(--ls-glass-fill-inset)); border: 1px solid hsl(var(--ls-glass-line)); }
.cse-push__block > strong { flex: 0 0 auto; }
.cse-push__block .cse-reseq__list { width: 100%; }
.cse-push__foot { display: flex; justify-content: flex-end; gap: 8px; }
.cse-reseq__list { margin: 6px 0 0; padding-left: 18px; font-size: .8rem; display: grid; gap: 4px; }

/* ---- 第五轮：教务预检结论与保存结果 ---- */
.cse-push__head { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 10px; }
.cse-push__detail { margin-top: 4px; font-size: .8rem; color: hsl(var(--ls-ink)); }
.cse-push__next { margin-top: 4px; padding: 6px 10px; border-radius: var(--ls-r-md); font-size: .78rem; line-height: 1.5; color: hsl(var(--ls-ink)); background: color-mix(in srgb, hsl(var(--ls-warning)) 14%, var(--cse-control)); border-left: 3px solid hsl(var(--ls-warning)); }
.cse-push__block > span { flex: 1 1 240px; min-width: 0; font-size: .82rem; line-height: 1.5; }
.cse-push[data-phase="checking"] .cse-push__list, .cse-push[data-phase="saving"] .cse-push__list { opacity: .7; }
`;
