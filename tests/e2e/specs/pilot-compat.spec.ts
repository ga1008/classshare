import type { Locator, Page, TestInfo } from '@playwright/test';
import { test, expect, guardS3Page, type S3Fixture } from '../fixtures/lq-s3';
import { loginStudent, loginTeacher } from '../fixtures/p03';

type Kind = 'manage' | 'report';
const barSelector = (kind: Kind) => kind === 'manage' ? '#manage-pilot-topbar' : '[data-lq-report-card-topbar]';
const destination = (kind: Kind, fixture: S3Fixture) => kind === 'manage'
  ? '/manage/library/courses' : `/report-card?class_offering_id=${fixture.reportCard.offeringId}`;
// One navigation per role; these are resize transitions, not a cross-product of
// business actions or palettes. Include both sides of all documented breakpoints.
const widths = [1440, 320, 375, 390, 639, 640, 641, 767, 768, 769, 1023, 1024, 1025, 1279, 1280, 1281, 1535, 1536, 1537, 1440];
const draft = 'S3 跨断点保留的课程草稿';

async function frame(page: Page) {
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
}

async function waitForSceneGeometry(chip: Locator) {
  await expect.poll(() => chip.evaluate(element => {
    const chain = new Set<Element>();
    for (let node: Element | null = element; node; node = node.parentElement) chain.add(node);
    return document.getAnimations().every(animation => {
      const effect = animation.effect;
      if (!(effect instanceof KeyframeEffect) || !(effect.target instanceof Element) || !chain.has(effect.target)
          || !Number.isFinite(effect.getComputedTiming().endTime)) return true;
      return !animation.pending && ['finished', 'idle'].includes(animation.playState);
    });
  }), { timeout: 2000, intervals: [16, 32, 64], message: 'Scene chip and ancestor finite entrance animations must finish before measuring' }).toBe(true);
}

async function login(page: Page, kind: Kind) {
  const fixture = await guardS3Page(page);
  if (kind === 'manage') await loginTeacher(page, fixture); else await loginStudent(page, fixture);
  return fixture;
}

async function openPilot(page: Page, kind: Kind, fixture: S3Fixture, touch = false) {
  expect((await page.goto(destination(kind, fixture)))?.status()).toBe(200);
  await expect(page.locator(barSelector(kind))).toHaveAttribute('data-lq-enhanced', 'true');
  if (kind === 'manage') await expect(page.locator('.course-card[tabindex="0"]').first()).toBeVisible();
  else {
    await expect(page.locator('[data-report-chart]')).toHaveCount(1);
    await expect.poll(() => page.locator('[data-report-chart]').evaluate(element =>
      !!(window as any).echarts?.getInstanceByDom(element))).toBe(true);
  }
  await page.evaluate(() => document.fonts.ready.then(() => undefined));
  await frame(page);
  if (kind === 'report') {
    const chip = page.locator(`${barSelector(kind)} .topbar-scene-chip`);
    await expect(chip).toHaveCount(1); await expect(chip).toBeVisible(); await expect(chip).toBeEnabled();
    await waitForSceneGeometry(chip);
    const box = (await chip.boundingBox())!;
    expect(box.width).toBeCloseTo(44, 4); expect(box.height).toBeCloseTo(44, 4);
    await activate(chip, touch);
    const pop = page.locator('.topbar-scene-pop');
    await expect(pop).toHaveCount(1); await expect(pop).toBeVisible(); await expect(pop).toContainText('今日一言');
    // Real click/tap in the body gutter exercises the original outside listener.
    if (touch) await page.touchscreen.tap(2, 220); else await page.mouse.click(2, 220);
    await expect(pop).toHaveCount(0);
    await scenePopoverLifecycles(page, touch);
  }
}

async function scenePopoverLifecycles(page: Page, touch: boolean) {
  // Count only document listeners added during this real controller exercise.
  // Restore the original method ownership afterward; no controller is injected.
  await page.evaluate(() => {
    const doc = document as any;
    const types = ['pointerdown', 'click', 'keydown'];
    const active = new Map<string, Map<unknown, Set<boolean>>>(types.map(type => [type, new Map()]));
    const ownAdd = Object.getOwnPropertyDescriptor(doc, 'addEventListener');
    const ownRemove = Object.getOwnPropertyDescriptor(doc, 'removeEventListener');
    const add = doc.addEventListener, remove = doc.removeEventListener;
    const capture = (options: boolean | AddEventListenerOptions | undefined) => typeof options === 'boolean' ? options : !!options?.capture;
    doc.addEventListener = function(type: string, listener: unknown, options?: boolean | AddEventListenerOptions) {
      const entries = active.get(type);
      if (entries && listener) {
        if (!entries.has(listener)) entries.set(listener, new Set());
        entries.get(listener)!.add(capture(options));
      }
      return add.call(this, type, listener, options);
    };
    doc.removeEventListener = function(type: string, listener: unknown, options?: boolean | EventListenerOptions) {
      const entries = active.get(type), flags = entries?.get(listener);
      flags?.delete(capture(options)); if (flags?.size === 0) entries!.delete(listener);
      return remove.call(this, type, listener, options);
    };
    (window as any).__lqSceneListenerProbe = {
      snapshot: () => ({ popovers: document.querySelectorAll('.topbar-scene-pop').length,
        listeners: Object.fromEntries([...active].map(([type, entries]) => [type, [...entries.values()].reduce((sum, flags) => sum + flags.size, 0)])) }),
      restore: () => {
        if (ownAdd) Object.defineProperty(doc, 'addEventListener', ownAdd); else delete doc.addEventListener;
        if (ownRemove) Object.defineProperty(doc, 'removeEventListener', ownRemove); else delete doc.removeEventListener;
        delete (window as any).__lqSceneListenerProbe;
      },
    };
  });
  try {
    const chip = page.locator('[data-lq-report-card-topbar] .topbar-scene-chip');
    for (let cycle = 0; cycle < 20; cycle++) for (const close of ['toggle', 'outside', 'escape'] as const) {
      await activate(chip, touch);
      expect(await page.evaluate(() => (window as any).__lqSceneListenerProbe.snapshot()), `${cycle}/${close} one popup owner`).toEqual({
        popovers: 1, listeners: { pointerdown: 1, click: 1, keydown: 1 },
      });
      if (close === 'toggle') await activate(chip, touch);
      else if (close === 'escape') await page.keyboard.press('Escape');
      else if (touch) await page.touchscreen.tap(2, 220);
      else await page.mouse.click(2, 220);
      expect(await page.evaluate(() => (window as any).__lqSceneListenerProbe.snapshot()), `${cycle}/${close} no retained listeners`).toEqual({
        popovers: 0, listeners: { pointerdown: 0, click: 0, keydown: 0 },
      });
    }
  } finally { await page.evaluate(() => (window as any).__lqSceneListenerProbe.restore()); }
}

function observeWrites(page: Page) {
  const writes: string[] = [];
  page.on('request', request => {
    const path = new URL(request.url()).pathname;
    if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method()) &&
        (/ui-preferences/.test(path) || path.startsWith('/api/manage/courses') || path.startsWith('/api/report-card'))) {
      writes.push(`${request.method()} ${path}`);
    }
  });
  return writes;
}

async function localTheme(page: Page, appearance: 'light' | 'dark') {
  await page.evaluate(appearance => {
    const theme = (document as any)[Symbol.for('lanshare.theme.installation')];
    if (!theme) throw new Error('Shared theme owner missing');
    theme.refresh({ palette_key: 'indigo', appearance, glass: 'off' });
  }, appearance);
  await expect(page.locator('html')).toHaveAttribute('data-appearance', appearance);
  await expect(page.locator('html')).toHaveAttribute('data-lq-glass', 'off');
}

async function activate(node: Locator, touch: boolean) {
  if (touch) await node.tap(); else await node.click();
}

async function coarseTarget(node: Locator) {
  await expect(node).toBeVisible();
  const box = (await node.boundingBox())!;
  expect(box.width, `${await node.getAttribute('aria-label')} target width`).toBeGreaterThanOrEqual(44);
  expect(box.height, `${await node.getAttribute('aria-label')} target height`).toBeGreaterThanOrEqual(44);
}

async function actions(page: Page, kind: Kind, touch = false) {
  const bar = page.locator(barSelector(kind)), pane = bar.locator('[data-lq-pane="actions"]');
  const trigger = bar.locator(':scope > [data-lq-pane-open="actions"]');
  const drawer = await pane.getAttribute('data-lq-pane-mode') === 'drawer';
  if (drawer) {
    if (touch) await coarseTarget(trigger);
    await activate(trigger, touch);
    await expect(pane).toBeVisible(); await expect(page.locator('dialog:modal')).toHaveCount(1);
  }
  await expect(pane.locator('a[href="/profile"]').first()).toBeVisible();
  const preferences = pane.locator('[data-ui-preferences-details]');
  const summary = preferences.locator('[data-ui-preferences-toggle]');
  if (touch) await coarseTarget(summary);
  await activate(summary, touch);
  await expect(preferences).toHaveAttribute('open', '');
  const panel = preferences.locator('[data-ui-preferences-panel]');
  await expect(panel).toBeVisible(); await expect(panel.locator('select')).toHaveCount(3);
  for (const field of await panel.locator('select').all()) await expect(field).toBeEnabled();
  await summary.scrollIntoViewIfNeeded();
  expect(await summary.evaluate(element => {
    const r = element.getBoundingClientRect(), hit = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
    return !!hit && element.contains(hit);
  }), 'Preference disclosure remains reachable while its panel is open').toBe(true);
  await activate(summary, touch); await expect(preferences).not.toHaveAttribute('open', '');
  if (drawer) {
    const close = pane.getByRole('button', { name: '关闭更多操作', exact: true });
    if (touch) await coarseTarget(close);
    await activate(close, touch);
    await expect(pane).toBeHidden(); await expect(page.locator('dialog:modal')).toHaveCount(0);
    await expect(trigger).toBeFocused();
  }
}

async function navigation(page: Page, touch = false) {
  const pane = page.locator('#manage-pilot-nav');
  const trigger = page.locator('[data-lq-manage-sidebar] > [data-lq-pane-open="nav"]');
  const drawer = await pane.getAttribute('data-lq-pane-mode') === 'drawer';
  if (drawer) {
    if (touch) await coarseTarget(trigger);
    await activate(trigger, touch); await expect(pane).toBeVisible();
  }
  const group = page.locator('#manage-domain-library'), summary = group.locator('summary');
  if (touch) await coarseTarget(summary);
  await expect(group).toHaveAttribute('open', '');
  await activate(summary, touch); await expect(group).not.toHaveAttribute('open', '');
  await activate(summary, touch); await expect(group).toHaveAttribute('open', '');
  const search = page.locator('#manageNavSearch');
  if (touch) await coarseTarget(search);
  await search.fill('S3__无匹配菜单__'); await expect(page.locator('#manageNavEmpty')).toBeVisible();
  await expect(page.locator('#manageNav .manage-nav-item:visible')).toHaveCount(0);
  await search.press('Escape'); await expect(search).toHaveValue('');
  await expect(page.locator('#manageNavEmpty')).toBeHidden();
  await expect(pane).toBeVisible(); // Search consumes the first Escape, not the pane.
  const courseLink = page.locator('#manageNav a[href="/manage/library/courses"]');
  await expect(courseLink).toBeVisible(); if (touch) await coarseTarget(courseLink);
  if (drawer) {
    const close = pane.getByRole('button', { name: '关闭菜单', exact: true });
    if (touch) await coarseTarget(close);
    await activate(close, touch); await expect(pane).toBeHidden(); await expect(trigger).toBeFocused();
  }
}

async function rememberOwnership(page: Page, kind: Kind) {
  await page.evaluate(({ kind, bar }) => {
    const root = kind === 'manage' ? document.body : document.querySelector('[data-lq-report-card]')!;
    const chartNode = document.querySelector('[data-report-chart]');
    (window as any).__lqCompatOwner = {
      root, owner: (root as any)[Symbol.for(kind === 'manage' ? 'lanshare.manage-lq-pilot' : 'lanshare.report-card.pilot')],
      shell: (document.querySelector(bar) as any)[Symbol.for('lanshare.lq.shell-owner')],
      input: document.querySelector('#courseNameInput'),
      chart: chartNode && (window as any).echarts.getInstanceByDom(chartNode),
    };
  }, { kind, bar: barSelector(kind) });
}

async function geometry(page: Page, kind: Kind) {
  const width = page.viewportSize()!.width;
  const inline = await page.locator('html').getAttribute('data-lq-shell-fallback') === 'inline';
  const expected = inline || width >= 1024 ? 'inline' : 'drawer';
  const bar = page.locator(barSelector(kind));
  await expect(bar.locator('[data-lq-pane="actions"]')).toHaveAttribute('data-lq-pane-mode', expected);
  if (kind === 'manage') await expect(page.locator('#manage-pilot-nav')).toHaveAttribute('data-lq-pane-mode', expected);
  await frame(page);
  const result = await page.evaluate(({ kind, bar }) => {
    const b = document.querySelector(bar)!.getBoundingClientRect(), saved = (window as any).__lqCompatOwner;
    const currentChart = document.querySelector('[data-report-chart]');
    const chart = currentChart && (window as any).echarts.getInstanceByDom(currentChart);
    return { viewport: innerWidth, scrollWidth: document.documentElement.scrollWidth,
      bar: { left: b.left, right: b.right, width: b.width, height: b.height },
      appearance: document.documentElement.dataset.appearance, inline: document.documentElement.dataset.lqShellFallback === 'inline',
      sameOwner: !!saved.owner && saved.owner === saved.root[Symbol.for(kind === 'manage' ? 'lanshare.manage-lq-pilot' : 'lanshare.report-card.pilot')],
      sameShell: !!saved.shell && saved.shell === (document.querySelector(bar) as any)[Symbol.for('lanshare.lq.shell-owner')],
      sameInput: saved.input === document.querySelector('#courseNameInput'), sameChart: saved.chart === chart,
      scores: chart?.getOption().series[0].data };
  }, { kind, bar: barSelector(kind) });
  expect(result.scrollWidth).toBeLessThanOrEqual(width + 1);
  expect(result.bar.left).toBeGreaterThanOrEqual(-1); expect(result.bar.right).toBeLessThanOrEqual(width + 1);
  expect(result.sameOwner).toBe(true); expect(result.sameShell).toBe(true);
  if (kind === 'manage') { expect(result.sameInput).toBe(true); await expect(page.locator('#courseNameInput')).toHaveValue(draft); }
  else {
    expect(result.sameChart).toBe(true);
    await expect(page.getByRole('region', { name: '已公布课程成绩' })).toContainText(/总评\s+0(?:\.0+)?\s+分/);
  }
  return result;
}

async function courseDraft(page: Page, touch = false) {
  const pane = page.locator('#manage-pilot-topbar [data-lq-pane="actions"]');
  if (await pane.getAttribute('data-lq-pane-mode') === 'drawer') {
    await activate(page.locator('#manage-pilot-topbar > [data-lq-pane-open="actions"]'), touch);
  }
  await activate(page.locator('#openCourseCreateBtn'), touch);
  await expect(page.locator('#courseModal')).toBeVisible();
  await expect(page.locator('dialog:modal')).toHaveCount(0); // Original controller owns the modal after handoff.
  await page.locator('#courseNameInput').fill(draft);
  await activate(page.locator('#courseModal button[data-dismiss="modal"]').filter({ hasText: '取消' }), touch);
  await expect(page.locator('#courseModal')).toBeHidden();
  await expect(page.locator('#courseNameInput')).toHaveValue(draft);
}

async function evidence(info: TestInfo, name: string, value: unknown) {
  await info.attach(name, { body: JSON.stringify(value, null, 2), contentType: 'application/json' });
}

for (const kind of ['manage', 'report'] as const) {
  test(`S3 ${kind} actual pilot responsive walk preserves owners at documented widths and CSS 200 percent reflow`, async ({ page, browserName }, info) => {
    test.setTimeout(180_000);
    await page.setViewportSize({ width: 1440, height: 900 });
    const fixture = await login(page, kind), writes = observeWrites(page);
    await openPilot(page, kind, fixture); await localTheme(page, 'light');
    if (kind === 'manage') await courseDraft(page);
    await rememberOwnership(page, kind);
    const measurements = [];
    for (const width of widths) {
      await page.setViewportSize({ width, height: 900 });
      if (width === 390) await localTheme(page, 'dark');
      const result = await geometry(page, kind); measurements.push(result);
      if (kind === 'report') expect(result.scores).toEqual(fixture.reportCard.expectedMine);
      if ([320, 768, 1024].includes(width)) {
        await actions(page, kind);
        if (kind === 'manage' && width < 1024) await navigation(page);
        await page.screenshot({ path: info.outputPath(`${kind}-${width}.png`), fullPage: true });
      }
    }
    await page.setViewportSize({ width: 768, height: 1024 });
    // Explicit CSS-only presentation probe. This is neither browser toolbar
    // zoom nor a DPR/pageScaleFactor substitute for a real 200% browser check.
    expect(await page.evaluate(() => CSS.supports('zoom', '2'))).toBe(true);
    await page.evaluate(() => { document.documentElement.style.zoom = '2'; });
    try {
      measurements.push(await geometry(page, kind)); await actions(page, kind);
      await page.screenshot({ path: info.outputPath(`${kind}-css-zoom-2.png`), fullPage: true });
    } finally { await page.evaluate(() => { document.documentElement.style.removeProperty('zoom'); }); }
    expect(writes).toEqual([]);
    await evidence(info, 'responsive-walk.json', { browserName, widths, measurements, writes,
      scope: 'Actual pilot shell + preserved existing course input/report data; CSS zoom only, real browser zoom and physical devices untested.' });
  });

  test(`S3 ${kind} actual pilot 768 touch opens and closes migrated controls at 44px without business writes`, async ({ page, browser, baseURL, browserName }, info) => {
    const fixture = await login(page, kind);
    // storageState preserves authentication/localStorage, but sessionStorage is
    // tab-local. Carry the actual login-produced scene into this touch clone so
    // its chip assertion tests the same logged-in journey, not a fresh-tab empty
    // scene. Do not fabricate a tip or insert the button/controller ourselves.
    let loginScene: string | null = null;
    if (kind === 'report') {
      await expect(page.locator('.app-topbar .topbar-scene-chip')).toHaveCount(1);
      await expect(page.locator('.app-topbar .topbar-scene-chip')).toBeEnabled();
      loginScene = await page.evaluate(() => sessionStorage.getItem('lanshareTopbarScene'));
      expect(loginScene).not.toBeNull();
      const scene = JSON.parse(loginScene!);
      expect(typeof scene.image).toBe('string'); expect(scene.image.length).toBeGreaterThan(0);
      expect(typeof scene.tip).toBe('string'); expect(scene.tip.trim().length).toBeGreaterThan(0);
    }
    const context = await browser.newContext({ baseURL, storageState: await page.context().storageState(),
      viewport: { width: 768, height: 1024 }, hasTouch: true });
    try {
      const origin = new URL(baseURL!).origin;
      if (loginScene !== null) await context.addInitScript(({ origin, scene }) => {
        if (location.origin === origin) sessionStorage.setItem('lanshareTopbarScene', scene);
      }, { origin, scene: loginScene });
      await context.route('**/*', route => {
        const target = new URL(route.request().url());
        return target.origin === origin || ['data:', 'blob:'].includes(target.protocol) ? route.continue() : route.abort();
      });
      const touchPage = await context.newPage(); await guardS3Page(touchPage);
      const writes = observeWrites(touchPage);
      await openPilot(touchPage, kind, fixture, true); await localTheme(touchPage, 'dark');
      expect(await touchPage.evaluate(() => matchMedia('(pointer:coarse)').matches)).toBe(true);
      if (kind === 'manage') await courseDraft(touchPage, true);
      await rememberOwnership(touchPage, kind);
      await actions(touchPage, kind, true);
      if (kind === 'manage') await navigation(touchPage, true);
      const measurement = await geometry(touchPage, kind);
      if (kind === 'report') expect(measurement.scores).toEqual(fixture.reportCard.expectedMine);
      await touchPage.screenshot({ path: info.outputPath(`${kind}-768-touch.png`), fullPage: true });
      expect(writes).toEqual([]);
      await evidence(info, 'touch.json', { browserName, hasTouch: true, viewport: touchPage.viewportSize(), measurement, writes,
        scope: 'Playwright engine touch emulation, not physical iPad/Safari validation.' });
    } finally { await context.close(); }
  });
}
