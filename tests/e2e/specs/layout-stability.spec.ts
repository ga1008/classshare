import type { Page, TestInfo } from '@playwright/test';
import { test, expect, guardS3Page, type S3Fixture } from '../fixtures/lq-s3';
import { loginStudent, loginTeacher } from '../fixtures/p03';

// Plan §20.3: fixed-scene CLS <= .05. The 1px geometry tolerance below is
// subpixel/scroll rounding tolerance, not an additional performance budget.
const CLS_LIMIT = 0.05;
const GEOMETRY_EPSILON = 1;
type Geometry = Record<string, { x: number; y: number; width: number; height: number }>;
type Measurement = {
  url: string; supported: boolean; cls: number; totalUnexpectedShift: number;
  shiftCount: number; recentInputShiftCount: number; entries: unknown[];
  timestamp: number; viewport: { width: number; height: number };
  theme: { palette: string | null; appearance: string | null; glass: string | null };
};

async function installProbe(page: Page) {
  // Installed before navigation, never reset at application/font readiness.
  await page.addInitScript(() => {
    type Shift = PerformanceEntry & {
      value: number; hadRecentInput: boolean;
      sources?: { node?: Node; previousRect: DOMRectReadOnly; currentRect: DOMRectReadOnly }[];
    };
    const entries: { value: number; startTime: number; hadRecentInput: boolean; sources: unknown[] }[] = [];
    const supported = PerformanceObserver.supportedEntryTypes.includes('layout-shift');
    const rect = (value: DOMRectReadOnly) => ({ x: value.x, y: value.y, width: value.width, height: value.height });
    const consume = (records: PerformanceEntry[]) => {
      for (const record of records) {
        const shift = record as Shift;
        entries.push({ value: shift.value, startTime: shift.startTime, hadRecentInput: shift.hadRecentInput,
          sources: (shift.sources || []).map(source => ({
            node: source.node instanceof Element ? `${source.node.tagName.toLowerCase()}${source.node.id ? `#${source.node.id}` : ''}.${Array.from(source.node.classList).join('.')}` : null,
            previousRect: rect(source.previousRect), currentRect: rect(source.currentRect),
          })),
        });
      }
    };
    const observer = supported ? new PerformanceObserver(list => consume(list.getEntries())) : null;
    observer?.observe({ type: 'layout-shift', buffered: true });
    const probe = {
      dashboardReady: false,
      snapshot() {
        if (observer) consume(observer.takeRecords());
        // Standard CLS: maximum session window, gaps <1s and duration <5s,
        // excluding hadRecentInput. Keep excluded entries in the diagnostics.
        let cls = 0, value = 0, start = -Infinity, previous = -Infinity, total = 0;
        for (const shift of entries) {
          if (shift.hadRecentInput) continue;
          if (shift.startTime - previous < 1000 && shift.startTime - start < 5000) value += shift.value;
          else { value = shift.value; start = shift.startTime; }
          previous = shift.startTime; total += shift.value; cls = Math.max(cls, value);
        }
        return { url: location.href, supported, cls, totalUnexpectedShift: total,
          shiftCount: entries.length, recentInputShiftCount: entries.filter(entry => entry.hadRecentInput).length,
          entries: entries.slice(), timestamp: performance.now(), viewport: { width: innerWidth, height: innerHeight },
          theme: { palette: document.documentElement.dataset.uiPalette || null, appearance: document.documentElement.dataset.appearance || null, glass: document.documentElement.dataset.lqGlass || null },
        };
      },
    };
    (window as any).__lqLayoutProbe = probe;
    window.addEventListener('lanshare:dashboard-ready', () => { probe.dashboardReady = true; });
  });
}

async function painted(page: Page) {
  await page.waitForLoadState('load');
  await page.evaluate(async () => {
    await document.fonts.ready;
    await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
  });
}

async function settleCourseModal(page: Page) {
  const evidence = await page.locator('#courseModal').evaluate(async root => {
    // Visibility and two frames do not finish the existing .modal-content scale
    // transition. Observe only its authored surfaces, never spinners/descendants.
    const surfaces = [root, ...root.querySelectorAll('.modal-dialog,.modal-content')];
    const styles = () => surfaces.map(node => ({ className: node.className, transform: getComputedStyle(node).transform, opacity: getComputedStyle(node).opacity }));
    const before = styles(), observed: { type: string; endTime: number }[] = [];
    const deadline = performance.now() + 2000;
    for (let pass = 0; pass < 4; pass++) {
      const animations = surfaces.flatMap(node => node.getAnimations()).filter(animation => !['idle', 'finished'].includes(animation.playState));
      if (!animations.length) return { before, after: styles(), observed };
      for (const animation of animations) {
        const endTime = Number(animation.effect?.getComputedTiming().endTime);
        if (!Number.isFinite(endTime) || animation.playState === 'paused' || animation.playbackRate === 0) throw Error('Course modal opening motion is not finite/running');
        observed.push({ type: animation.constructor.name, endTime });
      }
      const remaining = deadline - performance.now();
      if (remaining <= 0) throw Error('Course modal opening motion did not settle');
      let timer: ReturnType<typeof setTimeout> | undefined;
      try {
        await Promise.race([
          Promise.allSettled(animations.map(animation => animation.finished)),
          new Promise<never>((_, reject) => { timer = setTimeout(() => reject(Error('Course modal opening motion did not settle')), remaining); }),
        ]);
      } finally { clearTimeout(timer); }
      // A canceled/replaced transition is re-observed, never treated as success.
    }
    throw Error('Course modal opening motion repeatedly changed');
  });
  await painted(page);
  return evidence;
}

async function settleManageEntrance(page: Page) {
  const evidence = await page.locator('.manage-content').evaluate(async root => {
    const before = getComputedStyle(root).transform, observed: { currentTime: number | null; endTime: number }[] = [];
    const deadline = performance.now() + 2000;
    for (let pass = 0; pass < 4; pass++) {
      // Only the existing finite content entrance; never wait for descendants'
      // spinners/ambient animations or disable motion to make geometry pass.
      const animations = root.getAnimations().filter(animation => animation instanceof CSSAnimation
        && animation.animationName === 'managePageEnter' && !['idle', 'finished'].includes(animation.playState));
      if (!animations.length) return { before, after: getComputedStyle(root).transform, observed };
      for (const animation of animations) {
        const endTime = Number(animation.effect?.getComputedTiming().endTime);
        if (!Number.isFinite(endTime) || animation.playState === 'paused' || animation.playbackRate <= 0) throw Error('Manage entrance is not finite/running');
        observed.push({ currentTime: typeof animation.currentTime === 'number' ? animation.currentTime : null, endTime });
      }
      const remaining = deadline - performance.now();
      if (remaining <= 0) throw Error('Manage entrance did not settle');
      let timer: ReturnType<typeof setTimeout> | undefined;
      try {
        await Promise.race([
          Promise.allSettled(animations.map(animation => animation.finished)),
          new Promise<never>((_, reject) => { timer = setTimeout(() => reject(Error('Manage entrance did not settle')), remaining); }),
        ]);
      } finally { clearTimeout(timer); }
      // A canceled/replaced animation is re-observed before using its geometry.
    }
    throw Error('Manage entrance repeatedly changed');
  });
  await painted(page);
  return evidence;
}

async function snapshot(page: Page): Promise<Measurement> {
  return page.evaluate(() => {
    const probe = (window as any).__lqLayoutProbe;
    if (!probe) throw Error('Layout observer was not installed before navigation');
    return probe.snapshot();
  });
}

async function geometry(page: Page, selectors: Record<string, string>): Promise<Geometry> {
  return page.evaluate(selectors => Object.fromEntries(Object.entries(selectors).map(([key, selector]) => {
    const element = document.querySelector(selector);
    if (!element || !element.getClientRects().length) throw Error(`Missing measured surface: ${selector}`);
    const box = element.getBoundingClientRect();
    return [key, { x: box.x + scrollX, y: box.y + scrollY, width: box.width, height: box.height }];
  })), selectors);
}

function unchanged(before: Geometry, after: Geometry, label: string) {
  expect(Object.keys(after)).toEqual(Object.keys(before));
  for (const key of Object.keys(before)) for (const axis of ['x', 'y', 'width', 'height'] as const) {
    expect(Math.abs(after[key][axis] - before[key][axis]), `${label}: ${key}.${axis}; before=${before[key][axis]}, after=${after[key][axis]}`).toBeLessThanOrEqual(GEOMETRY_EPSILON);
  }
}

async function attach(info: TestInfo, name: string, value: unknown) {
  await info.attach(name, { body: JSON.stringify(value, null, 2), contentType: 'application/json' });
}

async function readyPilot(page: Page, kind: 'manage' | 'report') {
  const topbar = kind === 'manage' ? '#manage-pilot-topbar' : '[data-lq-report-card-topbar]';
  await expect(page.locator(topbar)).toHaveAttribute('data-lq-enhanced', 'true');
  if (kind === 'manage') {
    await expect(page.locator('body')).toHaveClass(/lq-manage-pilot/);
    // syncCardTabState runs at the end of the real courses module, after its
    // listeners are installed. Do not end initial CLS at SSR markup alone.
    await expect(page.locator('.course-card[tabindex="0"]').first()).toBeVisible();
  } else {
    await expect(page.locator('[data-lq-report-card]')).toBeVisible();
    await expect.poll(() => page.locator('[data-lq-report-card] [data-report-chart]').evaluateAll(elements =>
      elements.length > 0 && elements.every(element => !!(window as any).echarts?.getInstanceByDom(element)))).toBe(true);
  }
  await painted(page);
}

async function login(page: Page, kind: 'manage' | 'report'): Promise<S3Fixture> {
  const fixture = await guardS3Page(page);
  if (kind === 'manage') await loginTeacher(page, fixture); else await loginStudent(page, fixture);
  return fixture;
}

test.describe('S3 LQ fixed-scene layout stability', () => {
  for (const width of [1440, 390]) for (const kind of ['manage', 'report'] as const) {
    test(`${kind} initial CLS and scroll-edge shrink preserve document flow at ${width}`, async ({ page }, info) => {
      await page.setViewportSize({ width, height: 900 });
      const fixture = await login(page, kind);
      await installProbe(page);
      const route = kind === 'manage' ? '/manage/library/courses' : `/report-card?class_offering_id=${fixture.reportCard.offeringId}`;
      expect((await page.goto(route))?.status()).toBe(200);
      await readyPilot(page, kind);
      const initial = await snapshot(page);
      await attach(info, 'initial-cls.json', initial);
      expect(initial.supported, 'Chromium Layout Instability API required; unsupported is not zero').toBe(true);
      expect(initial.cls).toBeLessThanOrEqual(CLS_LIMIT);

      const topbar = page.locator(kind === 'manage' ? '#manage-pilot-topbar' : '[data-lq-report-card-topbar]');
      await expect(topbar).toHaveAttribute('data-lq-condensed', 'false');
      const anchors = kind === 'manage'
        ? { content: '.manage-content', head: '[data-page-head]', firstRecord: '.course-card' }
        : { content: '[data-lq-report-card]', head: '[data-lq-report-card] [data-page-head]', firstRecord: '.report-course' };
      if (kind === 'manage') {
        const entrance = await settleManageEntrance(page), afterEntrance = await snapshot(page);
        await attach(info, 'scroll-baseline-entrance.json', { entrance, afterEntrance });
        // Initial CLS was already sampled above. Keep the same observer and
        // cumulative session window so this wait cannot hide any initial shift.
        expect(afterEntrance.cls).toBeLessThanOrEqual(CLS_LIMIT);
      }
      const before = await geometry(page, anchors);
      const scrollTarget = await page.evaluate(() => Math.min(320, document.documentElement.scrollHeight - innerHeight));
      expect(scrollTarget, 'Fixed fixture must be long enough to exercise the real >80px scroll threshold').toBeGreaterThan(80);
      await page.evaluate(y => window.scrollTo({ top: y, behavior: 'instant' }), scrollTarget);
      await expect(topbar).toHaveAttribute('data-lq-condensed', 'true');
      await painted(page);
      const condensed = await geometry(page, anchors), afterScroll = await snapshot(page);
      await attach(info, 'scroll-shrink.json', { before, condensed, afterScroll });
      // Coordinates include actual scrollY: normal scrolling is not counted as
      // layout movement. Header visual shrink itself may change its own box.
      unchanged(before, condensed, 'topbar condensation must not push following content');
      expect(afterScroll.cls).toBeLessThanOrEqual(CLS_LIMIT);
      await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' }));
      await expect(topbar).toHaveAttribute('data-lq-condensed', 'false');
      await painted(page);
      const restored = await geometry(page, anchors);
      await attach(info, 'scroll-restored.json', { restored, measurement: await snapshot(page) });
      unchanged(before, restored, 'expanded header must restore identical document flow');
    });
  }

  for (const width of [1440, 390]) test(`real course save held busy and 503 recovery do not move key controls at ${width}`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: 900 });
    await login(page, 'manage'); await installProbe(page);
    expect((await page.goto('/manage/library/courses'))?.status()).toBe(200);
    await readyPilot(page, 'manage');
    // This existing page-head entry opens the original modal without first
    // introducing the intentional mobile overflow-drawer transition.
    await page.locator('#heroCourseCreateBtn').click();
    await expect(page.locator('#courseModal')).toBeVisible();
    await page.locator('#courseNameInput').fill('S3 布局稳定性保留草稿');
    await painted(page);
    await attach(info, 'modal-ready.json', await settleCourseModal(page));
    const selectors = { topbar: '#manage-pilot-topbar', pageHead: '[data-page-head]', records: '#courseCardGrid', save: '#courseSaveBtn', title: '#courseModalTitle' };
    const before = await geometry(page, selectors), beforeMeasure = await snapshot(page);
    let requests = 0, release!: () => void;
    const gate = new Promise<void>(resolve => { release = resolve; });
    const endpoint = (url: URL) => url.pathname === '/api/manage/courses/save';
    await page.route(endpoint, async route => {
      if (route.request().method() !== 'POST') return route.fallback();
      requests++; await gate;
      await route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ status: 'error', detail: 'S3 布局门禁暂不可用', message: 'S3 布局门禁暂不可用' }) });
    });
    try {
      await page.locator('#courseSaveBtn').click();
      await expect.poll(() => requests).toBe(1);
      await expect(page.locator('#courseSaveBtn')).toBeDisabled();
      await expect(page.locator('#courseSaveBtn')).toHaveText('保存中...');
      await painted(page);
      const busy = await geometry(page, selectors), busyMeasure = await snapshot(page);
      await attach(info, 'busy-held.json', { before, busy, beforeMeasure, busyMeasure, requests });
      unchanged(before, busy, 'held business save');
      release();
      await expect(page.locator('#courseSaveBtn')).toBeEnabled();
      await expect(page.locator('#courseSaveBtn')).toHaveText('保存课程');
      await expect(page.getByText('S3 布局门禁暂不可用', { exact: false }).first()).toBeVisible();
      await expect(page.locator('#courseNameInput')).toHaveValue('S3 布局稳定性保留草稿');
      await painted(page);
      const recovered = await geometry(page, selectors), finalMeasure = await snapshot(page);
      await attach(info, 'busy-recovered.json', { recovered, finalMeasure, requests });
      unchanged(before, recovered, 'failed business save recovery');
      expect(finalMeasure.supported).toBe(true); expect(finalMeasure.cls).toBeLessThanOrEqual(CLS_LIMIT);
      expect(requests).toBe(1);
    } finally {
      release(); await page.unrouteAll({ behavior: 'wait' });
    }
  });

  test('student dashboard 390 height is an S4 baseline, not a report-card height gate', async ({ page }, info) => {
    await page.setViewportSize({ width: 390, height: 900 });
    const fixture = await guardS3Page(page); await loginStudent(page, fixture);
    await installProbe(page);
    const overview = page.waitForResponse(response => new URL(response.url()).pathname === '/api/dashboard/course-schedule/overview');
    expect((await page.goto('/dashboard'))?.status()).toBe(200);
    expect((await overview).ok()).toBe(true);
    await expect(page.locator('[data-dashboard-root]')).toHaveAttribute('data-dashboard-role', 'student');
    await expect(page.locator('[data-student-schedule]')).toHaveAttribute('data-initialized', 'true');
    await expect(page.locator('[data-student-schedule-deck]')).toHaveAttribute('aria-busy', 'false');
    await expect.poll(() => page.evaluate(() => (window as any).__lqLayoutProbe?.dashboardReady)).toBe(true);
    await painted(page);
    const dimensions = await page.evaluate(() => ({
      documentHeight: document.documentElement.scrollHeight, bodyHeight: document.body.scrollHeight,
      dashboardHeight: document.querySelector('[data-dashboard-root]')!.getBoundingClientRect().height,
      width: innerWidth, height: innerHeight,
      courseCount: document.querySelector('[data-student-course-count]')?.textContent?.trim() || null,
      visibleFocusItems: document.querySelectorAll('.ls-focus-list > .ls-focus-item').length,
    }));
    const baseline = { stage: 'S3 observation for S4', route: '/dashboard', plannedS4MobileHeightLimit: 4200,
      passesFutureHeightBudget: dimensions.documentHeight <= 4200, dimensions, measurement: await snapshot(page) };
    await attach(info, 'student-dashboard-S4-height-baseline.json', baseline);
    await page.screenshot({ path: info.outputPath('student-dashboard-390-height-baseline.png'), fullPage: true });
    expect(dimensions.width).toBe(390);
    expect(Number.isFinite(dimensions.documentHeight) && dimensions.documentHeight >= dimensions.height).toBe(true);
    // No <=4200 assertion until the S4 dashboard migration. This observation
    // is never substituted for the two S3 pilot-page CLS assertions above.
  });
});
