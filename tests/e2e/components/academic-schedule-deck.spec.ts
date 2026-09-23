import { test, expect, type Page } from '@playwright/test';
import { serveScheduleModule, settleScheduleMotion } from './schedule-fixture-modules';

async function mount(page: Page) {
  await page.route('http://academic-schedule.test/**', async route => {
    if (await serveScheduleModule(route)) return;
    await route.fulfill({ contentType: 'text/html', body: `<!doctype html><html><meta charset="utf-8"><link rel="stylesheet" href="/schedule-tokens.css"><meta name="viewport" content="width=device-width,initial-scale=1"><style>*{box-sizing:border-box}body{margin:20px;font-family:Arial,sans-serif;background:hsl(var(--ls-surface-0));color:hsl(var(--ls-ink))}</style><div id="outside-schedule">课表外页面内容</div><div id="deck"></div><script type="module">
    import { createScheduleDeck } from '/deck.js';
    window.navigations=[];
    const move={request_id:'R1',kind:'move',phase:'pending',endpoint:'original',counterpart_event_key:'new-A',counterpart_week_index:3,original:{date:'2026-08-31',sections:[2,3],room:'B416-1'},proposed:{date:'2026-09-17',sections:[6,7],room:'B210'}};
    const lesson=(key,name,day,sections,id,adjustment)=>({event_key:key,course_name:name,weekday:day,sections,hours:sections.length,classroom:'知新楼B416-1',classroom_short:'B416-1',class_label:'人工智能2601班、人工智能2602班',session_id:id,session_no:8,classroom_url:'/classroom/11?session_id='+id,adjustment,counts_towards_total:adjustment?.endpoint!=='proposed'});
    window.fixture={selected_term:{year:'2026-2027',term:'1',label:'第一学期'},filters:{course_options:['跨周课程','换教室课程','停课课程','同时段正式课']},section_range:{min:1,max:11},weeks:[
      {week_index:1,label:'第1周',is_current:true,lessons:[lesson('old-A','跨周课程',1,[2,3],101,move),lesson('room','换教室课程',2,[4,5],102,{...move,kind:'room',counterpart_event_key:null,counterpart_week_index:null,original:{date:'2026-09-01',sections:[4,5],room:'B416-1'},proposed:{date:'2026-09-01',sections:[4,5],room:'B210'}}),lesson('cancel','停课课程',3,[8,9],103,{...move,kind:'cancel',counterpart_event_key:null,proposed:null})]},
      {week_index:2,label:'第2周',lessons:[]},
      {week_index:3,label:'第3周',lessons:[lesson('new-A','跨周课程',4,[6,7],101,{...move,endpoint:'proposed',counterpart_event_key:'old-A',counterpart_week_index:1}),lesson('official-B','同时段正式课',4,[6,7],104,null)]}
    ]};
    window.deck=createScheduleDeck(document.getElementById('deck'),{onNavigate:url=>window.navigations.push(url)});window.deck.setOverview(window.fixture);
  </script></html>` });
  });
  await page.goto('http://academic-schedule.test/');
  await page.locator('.cs-card.is-active .cs-card__bar').click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await settleScheduleMotion(page);
}

test('pending cards keep a 4px transparent gap and proposed previews use a readable glass tint', async ({ page }, testInfo) => {
  await mount(page);
  const old = page.locator('.cs-expand [data-event-key="old-A"]');
  const metrics = await old.evaluate(node => {
    const surface = node.querySelector('.cs-lesson__surface')!;
    const outer = node.getBoundingClientRect(), inner = surface.getBoundingClientRect(), css = getComputedStyle(node);
    return { gap: inner.left - outer.left - parseFloat(css.borderLeftWidth), border: css.borderStyle, background: css.backgroundColor, surface: getComputedStyle(surface).backgroundColor };
  });
  expect(metrics.gap).toBeCloseTo(4, 0); expect(metrics.border).toBe('dashed');
  expect(metrics.background).toBe('rgba(0, 0, 0, 0)');
  await old.getByRole('button').click();
  const target = page.locator('.cs-expand [data-event-key="new-A"]');
  await expect(target).toHaveClass(/is-counterpart-focus/);
  await target.locator('.cs-lesson__main').hover();
  await expect(target).toHaveClass(/is-preview/);
  await expect(target).toHaveAttribute('data-preview-state', 'open');
  const colors = await target.evaluate(node => {
    const surface = getComputedStyle(node.querySelector('.cs-lesson__surface')!);
    const text = getComputedStyle(node.querySelector('.cs-lesson__title')!);
    const canvas = document.createElement('canvas'); canvas.width = canvas.height = 1;
    const context = canvas.getContext('2d')!;
    const rgba = (color: string) => { context.clearRect(0, 0, 1, 1); context.fillStyle = color; context.fillRect(0, 0, 1, 1); return Array.from(context.getImageData(0, 0, 1, 1).data); };
    const background = rgba(surface.backgroundColor), foreground = rgba(text.color);
    // Measure the tint composited over the fixture's light page, rather than
    // treating an alpha channel as an opaque colour.
    const composited = background.slice(0, 3).map(channel => channel * background[3] / 255 + 255 * (1 - background[3] / 255));
    const luminance = (rgb: number[]) => rgb.slice(0, 3).map(value => { const v = value / 255; return v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4; }).reduce((sum, value, index) => sum + value * [.2126, .7152, .0722][index], 0);
    return { opacity: getComputedStyle(node).opacity, textOpacity: text.opacity, background, contrast: (luminance(composited) + .05) / (luminance(foreground) + .05) };
  });
  expect(colors.opacity).toBe('1'); expect(colors.textOpacity).toBe('1');
  expect(colors.background[3]).toBeGreaterThan(0);
  expect(colors.background[3]).toBeLessThan(255);
  expect(colors.contrast).toBeGreaterThanOrEqual(7);
  await page.screenshot({ path: testInfo.outputPath('pending-tint-desktop.png') });
  await expect(page.locator('[data-csd-expand-sub]')).toContainText('1 节安排 · 2 课时');
});

test('labels jump both ways without navigation and both course links keep the same real session', async ({ page }) => {
  await mount(page);
  await page.locator('.cs-expand [data-event-key="old-A"] .cs-adjustment-label').click();
  await expect(page.locator('[data-csd-expand-title]')).toHaveText('第3周');
  expect(await page.evaluate(() => (window as any).navigations)).toEqual([]);
  await page.locator('.cs-expand [data-event-key="new-A"] .cs-lesson__main').click();
  await page.locator('.cs-expand [data-event-key="new-A"] .cs-adjustment-label').click();
  await expect(page.locator('[data-csd-expand-title]')).toContainText('第1周');
  await page.locator('.cs-expand [data-event-key="old-A"] .cs-lesson__main').click();
  expect(await page.evaluate(() => (window as any).navigations)).toEqual(['/classroom/11?session_id=101', '/classroom/11?session_id=101']);
});

test('room-only and cancel stay single cards and their labels reveal an accessible comparison', async ({ page }) => {
  await mount(page);
  await expect(page.locator('.cs-expand [data-event-key="room"]')).toHaveCount(1);
  const room = page.locator('.cs-expand [data-event-key="room"]');
  await room.getByRole('button').focus(); await page.keyboard.press('Enter');
  await expect(room.locator('.cs-adjustment-details')).toBeVisible();
  await expect(room.locator('.cs-adjustment-details')).toContainText('B210');
  await expect(room.getByRole('button')).toHaveAttribute('aria-expanded', 'true');
  await page.keyboard.press('Enter');
  await expect(room.locator('.cs-adjustment-details')).toBeHidden();
  await expect(room.getByRole('button')).toHaveAttribute('aria-expanded', 'false');
  await page.keyboard.press('Enter');
  await expect(room.locator('.cs-adjustment-details')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(room.locator('.cs-adjustment-details')).toBeHidden();
  await expect(page.getByRole('dialog')).toBeVisible();
  const cancel = page.locator('.cs-expand [data-event-key="cancel"]');
  await cancel.getByRole('button').click();
  await expect(cancel.locator('.cs-adjustment-details')).toContainText('不自动安排补课');
  expect(await page.evaluate(() => (window as any).navigations)).toEqual([]);
});

test('overlapping projections remain individually reachable and filtered counterparts never clear filters', async ({ page }) => {
  await mount(page);
  await page.locator('.cs-expand [data-event-key="old-A"] button').click();
  // The stable slot remains separated even when a hover preview grows above it.
  const boxes = await page.locator('.cs-expand .cs-lesson-slot[data-cs-lanes]').evaluateAll(nodes => nodes.map(node => { const r = node.getBoundingClientRect(); return { left: r.left, right: r.right }; }));
  expect(boxes).toHaveLength(2); expect(boxes[0].right).toBeLessThanOrEqual(boxes[1].left + 1);
  await page.evaluate(() => { const w = window as any; w.fixture.weeks[0].lessons = w.fixture.weeks[0].lessons.filter((l: any) => l.event_key !== 'old-A'); w.deck.setOverview(w.fixture, { keepWeek: true }); });
  await page.locator('.cs-expand [data-event-key="new-A"] button').click();
  await expect(page.locator('[data-csd-expand-title]')).toHaveText('第3周');
  await expect(page.locator('[data-csd-expand-feedback]')).toContainText('未在当前筛选结果中显示');
});

test('a refreshed approved snapshot removes the pending presentation without changing the session', async ({ page }) => {
  await mount(page);
  await page.evaluate(() => { const w = window as any; const target = w.fixture.weeks[2].lessons[0]; target.adjustment = null; target.counts_towards_total = true; w.fixture.weeks[0].lessons.shift(); w.deck.setOverview(w.fixture); w.deck.goToWeek(2); });
  await expect(page.locator('.cs-expand [data-event-key="new-A"]')).not.toHaveClass(/cs-lesson--pending/);
  await expect(page.locator('.cs-expand [data-event-key="new-A"]')).toHaveAttribute('href', '/classroom/11?session_id=101');
  await expect(page.locator('.cs-expand [data-event-key="old-A"]')).toHaveCount(0);
});

test('touch labels jump on the first tap while course bodies retain first-tap preview', async ({ browser }, testInfo) => {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
  const page = await context.newPage(); await mount(page);
  await page.locator('.cs-expand [data-event-key="old-A"] button').tap();
  await expect(page.locator('[data-csd-expand-title]')).toHaveText('第3周');
  await page.screenshot({ path: testInfo.outputPath('pending-mobile.png') });
  expect(await page.evaluate(() => (window as any).navigations)).toEqual([]);
  const link = page.locator('.cs-expand [data-event-key="new-A"] .cs-lesson__main');
  await link.tap(); expect(await page.evaluate(() => (window as any).navigations)).toEqual([]);
  await link.tap(); expect(await page.evaluate(() => (window as any).navigations)).toEqual(['/classroom/11?session_id=101']);
  await context.close();
});

test('unassociated sessions are readable without a false classroom link and retain creation guidance', async ({ page }) => {
  await mount(page);
  await page.evaluate(() => {
    const w = window as any;
    Object.assign(w.fixture.weeks[0].lessons[0], { classroom_url: '', class_offering_id: 11, session_id: null, adjustment: null });
    Object.assign(w.fixture.weeks[0].lessons[1], { classroom_url: '', class_offering_id: null, create_url: '/manage/courses/new?course_id=1' });
    w.fixture.message = '有课次需核对，请同步关联';
    w.deck.setOverview(w.fixture);
  });
  const existing = page.locator('.cs-expand [data-event-key="old-A"]');
  await existing.focus();
  await expect(existing).toContainText('课次尚未精确关联，请同步教务课表核对');
  await expect(existing.locator('a')).toHaveCount(0);
  const create = page.locator('.cs-expand [data-event-key="room"]');
  await expect(create.locator('a')).toHaveAttribute('href', '/manage/courses/new?course_id=1');
  await expect(create).toContainText('创建后请再次同步关联课次');
  await expect(page.locator('[data-csd-feedback]')).toContainText('有课次需核对，请同步关联');
});

for (const appearance of ['light', 'dark']) test(`mini and expanded schedules share all six ${appearance} glass palettes without blurring the outside page`, async ({ page }, testInfo) => {
  await mount(page);
  const results: any[] = [];
  for (const palette of ['teal', 'indigo', 'sky', 'mint', 'violet', 'rose']) {
    await page.evaluate(({ appearance, palette }) => {
      document.documentElement.dataset.appearance = appearance;
      document.documentElement.dataset.uiPalette = palette;
    }, { appearance, palette });
    await settleScheduleMotion(page);
    const result = await page.evaluate(() => {
      const context = document.createElement('canvas').getContext('2d')!;
      const rgba = (color: string) => {
        context.clearRect(0, 0, 1, 1); context.fillStyle = color; context.fillRect(0, 0, 1, 1);
        return [...context.getImageData(0, 0, 1, 1).data];
      };
      const luminance = (rgb: number[]) => rgb.slice(0, 3).map(channel => {
        const value = channel / 255;
        return value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4;
      }).reduce((sum, value, i) => sum + value * [.2126, .7152, .0722][i], 0);
      const read = (selector: string, pseudo?: string) => {
        const node = document.querySelector<HTMLElement>(selector)!, style = getComputedStyle(node, pseudo);
        const chain: Element[] = []; for (let el: Element | null = node; el; el = el.parentElement) chain.unshift(el);
        const composite = chain.reduce((under, el) => {
          // The panel's glass is painted on its behind-content pseudo layer so
          // the panel itself does not isolate a lesson's backdrop sampling.
          const painted = getComputedStyle(el, el.matches('.cs-expand__card') ? '::before' : undefined);
          const fill = rgba(painted.backgroundColor), alpha = fill[3] / 255;
          return under.map((channel, i) => fill[i] * alpha + channel * (1 - alpha));
        }, [255, 255, 255]);
        const ink = luminance(rgba(style.color)), fill = luminance(composite);
        return { fill: style.backgroundColor, alpha: rgba(style.backgroundColor)[3] / 255, ink: style.color,
          blur: style.backdropFilter, filter: style.filter, contrast: (Math.max(ink, fill) + .05) / (Math.min(ink, fill) + .05) };
      };
      return { mini: read('.cs-card.is-active'), miniBar: read('.cs-card.is-active .cs-card__bar'),
        expanded: read('.cs-expand__card', '::before'), expandedOwner: read('.cs-expand__card'), expandedBar: read('.cs-expand__bar'),
        lesson: read('.cs-expand [data-event-key="old-A"] .cs-lesson__surface'),
        overlay: read('.cs-expand'), outside: read('#outside-schedule'), body: read('body') };
    });
    for (const name of ['mini', 'expanded', 'lesson'] as const) {
      expect(result[name].alpha, `${palette} ${name} retains transparent material`).toBeGreaterThan(0);
      expect(result[name].alpha).toBeLessThan(1);
      expect(result[name].contrast, `${palette} ${name} paired foreground`).toBeGreaterThanOrEqual(4.5);
    }
    expect(result.miniBar.fill).toBe(result.expandedBar.fill);
    expect(result.miniBar.ink).toBe(result.expandedBar.ink);
    expect(result.expandedBar.contrast).toBeGreaterThanOrEqual(4.5);
    expect(result.mini.blur).toContain('blur(');
    expect(result.expanded.blur).not.toBe('none');
    expect(result.expandedOwner.alpha).toBe(0);
    expect(result.overlay.alpha).toBe(0);
    for (const name of ['expandedOwner', 'overlay', 'outside', 'body'] as const) {
      expect(result[name].blur).toBe('none'); expect(result[name].filter).toBe('none');
    }
    results.push({ palette, ...result });
  }
  expect(new Set(results.map(result => result.expandedBar.fill)).size).toBe(6);
  const preview = page.locator('.cs-expand [data-event-key="old-A"]');
  await preview.locator('.cs-lesson__main').focus();
  await settleScheduleMotion(page);
  await expect(preview).toHaveAttribute('data-preview-state', 'open');
  expect(await preview.evaluate(node => getComputedStyle(node).backdropFilter)).toContain('blur(');
  await testInfo.attach('palette-glass', { body: JSON.stringify(results, null, 2), contentType: 'application/json' });
  await page.screenshot({ path: testInfo.outputPath(`schedule-glass-${appearance}.png`) });
});
