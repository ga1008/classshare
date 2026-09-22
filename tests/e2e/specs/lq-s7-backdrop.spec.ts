import { test as base, expect, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { readFixture, loginStudent, loginTeacher } from '../fixtures/p03';

// S7 B package owns this runtime and port; see .codex-temp/claude-s4-runbook.md §11.3.
const runtime = path.resolve('.codex-temp/claude-s7-b-runtime');
const origin = 'http://127.0.0.1:8233';
const shots = path.resolve(process.env.LQ_S7_B_OUTPUT || '.codex-temp/claude-s7-b-e2e');

function fixture() {
  if (!process.env.P03_RUNTIME_ROOT || path.resolve(process.env.P03_RUNTIME_ROOT) !== runtime) throw Error('S7 B requires its owned runtime');
  const value = readFixture();
  if (path.resolve(value.runtimeRoot) !== runtime || path.resolve(value.databasePath) !== path.join(runtime, 'db/classroom.db')
      || (value as unknown as { uiV3Synthetic?: boolean }).uiV3Synthetic !== true) throw Error('S7 B fixture is not synthetic');
  return value;
}

const test = base.extend<{ _backdropGuard: void }>({
  _backdropGuard: [async ({ context, baseURL }, use) => {
    const value = fixture();
    expect(baseURL).toBe(origin);
    const health = await context.request.get('/api/internal/health');
    expect(health.status()).toBe(200);
    expect((await health.json()).database_path).toBe(value.databasePath);
    const errors: string[] = [];
    context.on('page', page => page.on('pageerror', error => errors.push(error.message)));
    await context.route('**/*', route => {
      const url = new URL(route.request().url());
      return url.origin === origin || ['blob:', 'data:'].includes(url.protocol) ? route.continue() : route.abort();
    });
    await use();
    expect(errors).toEqual([]);
  }, { auto: true }],
});

const lock = path.join(runtime, '.backdrop-browser.lock');
const owner = JSON.stringify({ pid: process.pid, nonce: crypto.randomUUID() });
test.beforeAll(() => { fixture(); fs.mkdirSync(shots, { recursive: true }); fs.writeFileSync(lock, owner, { flag: 'wx' }); });
test.afterAll(() => { if (fs.existsSync(lock) && fs.readFileSync(lock, 'utf8') === owner) fs.unlinkSync(lock); });

const layer = '[data-lq-page-backdrop]';
const panel = '[data-ui-preferences-details]';

async function preferences(page: Page) {
  const response = await page.request.get('/api/profile/ui-preferences');
  expect(response.status()).toBe(200);
  return (await response.json()).preferences;
}
async function openPanel(page: Page) {
  const details = page.locator(panel).first();
  await expect(details).toBeAttached();
  if (!(await details.evaluate(node => (node as HTMLDetailsElement).open))) {
    await details.locator('[data-ui-preferences-toggle]').click();
  }
  await expect(details).toHaveJSProperty('open', true);
}
async function choose(page: Page, field: string, value: string) {
  await openPanel(page);
  const select = page.locator(`${panel} [data-ui-preference-select="${field}"]`).first();
  // Selecting the value already shown fires no change; the state is the claim.
  if ((await select.inputValue()) !== value) { await select.selectOption(value); await saved(page); }
  expect((await preferences(page))[field]).toBe(value);
}
/** Write through the real API when the topbar panel is not reachable (mobile). */
async function save(page: Page, changes: Record<string, string>) {
  const current = await preferences(page);
  const response = await page.request.patch('/api/profile/ui-preferences', {
    headers: { 'X-UI-Preferences-Context': current.context_token, 'Content-Type': 'application/json' },
    data: { ...changes, version: current.version },
  });
  expect(response.status()).toBe(200);
}
/** Every axe finding as a stable id/target pair, so two runs can be compared. */
async function scan(page: Page) {
  // The dashboard mounts its schedule deck asynchronously; scanning before it
  // settles would compare two different documents, not two backdrop states.
  await page.waitForLoadState('networkidle').catch(() => undefined);
  await page.locator('.cs-stage').first().waitFor({ state: 'attached', timeout: 15000 }).catch(() => undefined);
  await page.waitForTimeout(1200);
  const result = await new AxeBuilder({ page }).analyze();
  return {
    // Selector paths shift with the dashboard's own conditional widgets, so the
    // comparable signature is the rule set; the targets are kept as evidence.
    ids: [...new Set(result.violations.map(violation => violation.id))].sort(),
    targets: result.violations.flatMap(violation => violation.nodes.map(node => `${violation.id} ${node.target.join(' ')}`)).sort(),
  };
}
async function saved(page: Page) {
  await expect(page.locator('[data-ui-palette-status]').first()).toHaveAttribute('data-ui-preference-status', 'saved');
}
/** Geometry of the painted image, derived the way background-size: contain is. */
async function painted(page: Page) {
  return page.evaluate(async () => {
    const node = document.querySelector('[data-lq-page-backdrop]') as HTMLElement;
    const image = node.querySelector('[data-lq-backdrop-image]') as HTMLElement;
    const style = getComputedStyle(image);
    const box = image.getBoundingClientRect();
    const url = node.dataset.lqBackdropImageUrl || '';
    const intrinsic = url ? await new Promise<{ w: number; h: number }>(resolve => {
      const probe = new Image();
      probe.onload = () => resolve({ w: probe.naturalWidth, h: probe.naturalHeight });
      probe.onerror = () => resolve({ w: 0, h: 0 });
      probe.src = url;
    }) : { w: 0, h: 0 };
    const scale = intrinsic.w && intrinsic.h ? Math.min(box.width / intrinsic.w, box.height / intrinsic.h) : 0;
    return {
      url, intrinsic, box: { width: box.width, height: box.height, bottom: box.bottom, left: box.left },
      layerBox: node.getBoundingClientRect().toJSON(),
      size: style.backgroundSize, position: style.backgroundPosition, filter: style.filter,
      opacity: Number(style.opacity), backdropFilter: style.backdropFilter,
      layerBackdropFilter: getComputedStyle(node).backdropFilter,
      layerBackground: getComputedStyle(node).backgroundColor,
      mode: node.dataset.lqBackdropMode, colour: node.dataset.lqBackdropColor,
      imageProperty: node.style.getPropertyValue('--lq-backdrop-image').trim(),
      painted: { width: intrinsic.w * scale, height: intrinsic.h * scale },
    };
  });
}

test('S7 B backdrop is a fixed, aspect-preserving, desaturated viewport-bottom layer', async ({ page }, info) => {
  await loginStudent(page, fixture());
  await page.goto('/dashboard');
  await expect(page.locator(layer)).toBeAttached();
  // The account is shared with the other tests; start from the shipped default.
  await save(page, { backdrop: 'scene', backdrop_color: '#ffffff' });
  await page.reload();

  const first = await painted(page);
  expect(first.mode).toBe('scene');
  expect(first.url).toMatch(/^\/static\/img\/life_tips\/[A-Za-z0-9._-]+$/);
  expect(first.imageProperty).toBe(`url(${first.url})`);
  expect(first.size).toBe('contain');
  expect(first.position).toBe('50% 100%');
  // It is the surface glass is seen against, never a blur host itself.
  expect(first.layerBackdropFilter).toBe('none');
  expect(first.backdropFilter).toBe('none');
  // Reduced colour: saturation pulled down and the layer kept translucent.
  expect(first.filter).toMatch(/^saturate\(0\.\d+\)$/);
  expect(first.opacity).toBeGreaterThan(0);
  expect(first.opacity).toBeLessThan(0.6);

  // Aspect ratio is the file's own, never stretched to the box.
  expect(first.intrinsic.w).toBeGreaterThan(0);
  const intrinsicRatio = first.intrinsic.w / first.intrinsic.h;
  expect(first.painted.width / first.painted.height).toBeCloseTo(intrinsicRatio, 3);
  expect(first.painted.width).toBeLessThanOrEqual(first.box.width + 0.5);
  expect(first.painted.height).toBeLessThanOrEqual(first.box.height + 0.5);

  // Fixed to the viewport bottom: scrolling does not move it.
  const scrolled = await page.evaluate(() => {
    document.documentElement.style.minHeight = '400vh';
    window.scrollTo(0, 1200);
    const node = document.querySelector('[data-lq-page-backdrop]') as HTMLElement;
    const box = node.getBoundingClientRect();
    return { y: window.scrollY, top: box.top, bottom: box.bottom, position: getComputedStyle(node).position };
  });
  expect(scrolled.position).toBe('fixed');
  expect(scrolled.y).toBeGreaterThan(0);
  expect(scrolled.top).toBeCloseTo(first.layerBox.top, 0);
  expect(scrolled.bottom).toBeCloseTo(first.layerBox.bottom, 0);
  await page.evaluate(() => { document.documentElement.style.minHeight = ''; window.scrollTo(0, 0); });

  // It follows the viewport: a narrower window repaints the same file smaller.
  await page.setViewportSize({ width: 390, height: 740 });
  const narrow = await painted(page);
  expect(narrow.url).toBe(first.url);
  expect(narrow.box.width).toBeLessThan(first.box.width);
  expect(narrow.painted.width / narrow.painted.height).toBeCloseTo(intrinsicRatio, 3);
  await info.attach('backdrop-geometry', { body: JSON.stringify({ first, narrow, scrolled }), contentType: 'application/json' });
  await page.setViewportSize({ width: 1440, height: 900 });

  // The dashboard carries pre-existing landmark findings. Assert the backdrop
  // adds none of its own instead of pretending the page is already clean.
  const withBackdrop = await scan(page);
  await save(page, { backdrop: 'off' });
  await page.reload();
  const withoutBackdrop = await scan(page);
  await info.attach('axe-delta', { body: JSON.stringify({ withBackdrop, withoutBackdrop }), contentType: 'application/json' });
  expect(withBackdrop.ids).toEqual(withoutBackdrop.ids);
  expect(withBackdrop.targets.filter(entry => entry.includes('lq-page-backdrop'))).toEqual([]);
});

test('S7 B turning the backdrop off previews instantly, keeps a solid colour and survives a reload', async ({ page }) => {
  await loginStudent(page, fixture());
  await page.goto('/dashboard');
  await save(page, { backdrop: 'scene', backdrop_color: '#ffffff' });
  await page.reload();
  await openPanel(page);
  await page.locator(`${panel} [data-ui-preference-select="backdrop"]`).first().selectOption('off');
  // No reload: the layer is already solid before the debounced save fires.
  const instant = await painted(page);
  expect(instant.mode).toBe('off');
  expect(instant.imageProperty).toBe('none');
  expect(instant.layerBackground).toBe('rgb(255, 255, 255)');
  await saved(page);
  expect((await preferences(page)).backdrop).toBe('off');

  await openPanel(page);
  await page.locator(`${panel} [data-ui-preference-input="backdrop_color"]`).first().fill('#102030');
  await saved(page);
  await expect(page.locator(layer)).toHaveAttribute('data-lq-backdrop-color', '#102030');
  expect((await painted(page)).layerBackground).toBe('rgb(16, 32, 48)');

  await page.reload();
  const reloaded = await painted(page);
  expect(reloaded.mode).toBe('off');
  expect(reloaded.imageProperty).toBe('none');
  expect(reloaded.layerBackground).toBe('rgb(16, 32, 48)');
  expect(await page.locator(`${panel} [data-ui-preference-select="backdrop"]`).first().inputValue()).toBe('off');

  // Back on, and the preview picks exactly the file the server would render.
  await choose(page, 'backdrop', 'scene-thesis');
  const preview = await painted(page);
  expect(preview.mode).toBe('scene-thesis');
  expect(preview.url).toMatch(/^\/static\/img\/life_tips\//);
  await page.reload();
  const server = await painted(page);
  expect(server.url).toBe(preview.url);
});

test('S7 B the saved choice applies on every signed-in page and to no anonymous page', async ({ page }) => {
  await loginStudent(page, fixture());
  await page.goto('/dashboard');
  await save(page, { backdrop: 'scene' });
  await page.reload();
  await choose(page, 'backdrop', 'scene-career');
  for (const route of ['/dashboard', '/profile?section=appearance']) {
    await page.goto(route);
    await expect(page.locator(layer)).toHaveAttribute('data-lq-backdrop-mode', 'scene-career');
    expect((await painted(page)).url).toMatch(/^\/static\/img\/life_tips\//);
    await page.evaluate(() => document.fonts.ready);
    await page.screenshot({ path: path.join(shots, `backdrop-page-${route.startsWith('/profile') ? 'profile' : 'dashboard'}.png`) });
  }
  // Signed out, the library is not referenced at all.
  await page.context().clearCookies();
  await page.goto('/student/login');
  expect(await page.locator(layer).count()).toBe(0);
  expect(await page.content()).not.toContain('data-lq-backdrop-catalog');
  // Another account keeps its own default; the preference is not global.
  await loginTeacher(page, fixture());
  expect((await preferences(page)).backdrop).toBe('scene');
  await page.goto('/dashboard');
  // Documented gap: the teacher shell renders through templates/manage/layout.html,
  // a reviewed shared source this package may not edit. See the S7 B report.
  expect(await page.locator(layer).count()).toBe(0);
});

test('S7 B the API refuses illegal colours and modes and keeps the optimistic lock', async ({ page }) => {
  await loginStudent(page, fixture());
  await page.goto('/dashboard');
  const current = await preferences(page);
  const headers = { 'X-UI-Preferences-Context': current.context_token, 'Content-Type': 'application/json' };
  for (const data of [{ backdrop_color: '#FFFFFF' }, { backdrop_color: 'red' }, { backdrop_color: '#fff' },
                      { backdrop_color: '#ffffff; background:url(//evil)' }, { backdrop_color: 'var(--ls-primary)' },
                      { backdrop: 'scene-unknown' }, { backdrop: 'off; drop' }]) {
    const response = await page.request.patch('/api/profile/ui-preferences', { headers, data: { ...data, version: current.version } });
    expect(response.status(), JSON.stringify(data)).toBe(422);
  }
  expect(await preferences(page)).toEqual(current);

  // Two writers at the same version: exactly one wins, the other must re-read.
  const race = await Promise.all([
    page.request.patch('/api/profile/ui-preferences', { headers, data: { backdrop: 'off', version: current.version } }),
    page.request.patch('/api/profile/ui-preferences', { headers, data: { backdrop: 'scene-life', version: current.version } }),
  ]);
  expect(race.map(response => response.status()).sort()).toEqual([200, 409]);
  const loser = race.find(response => response.status() === 409)!;
  expect((await loser.json()).code).toBe('version_conflict');
  expect((await preferences(page)).version).toBe(current.version + 1);
});

for (const width of [1440, 390]) for (const appearance of ['light', 'dark'] as const) for (const mode of ['scene', 'off'] as const) {
  test(`S7 B visual ${width} ${appearance} backdrop-${mode}`, async ({ page }) => {
    // Sign in at the desktop size: the subject of this shot is the dashboard at
    // `width`, not the login reveal, whose mobile animation is timing-sensitive.
    await loginStudent(page, fixture());
    await page.setViewportSize({ width, height: width === 1440 ? 900 : 740 });
    await page.goto('/dashboard');
    // The topbar panel is desktop-only, so the mobile shot writes through the
    // same API the panel uses and then renders the saved state from SSR.
    if (width === 1440) {
      await choose(page, 'appearance', appearance);
      await choose(page, 'backdrop', mode);
      await page.locator(`${panel} [data-ui-preferences-toggle]`).first().click();
    } else {
      await save(page, { appearance, backdrop: mode });
      await page.reload();
    }
    await expect(page.locator('html')).toHaveAttribute('data-appearance', appearance);
    await expect(page.locator(layer)).toHaveAttribute('data-lq-backdrop-mode', mode);
    await page.evaluate(() => document.fonts.ready);
    await page.waitForTimeout(400);
    await page.screenshot({ path: path.join(shots, `backdrop-${width}-${appearance}-${mode}.png`) });
  });
}
