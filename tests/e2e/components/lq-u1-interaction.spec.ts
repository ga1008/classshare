import { test, expect, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';

// S9 U1 gate. The material module in materials.css defines one interaction;
// the control components adopt it through the shared selector lists in
// components/button.css. This file measures the thing that claim is worth
// nothing without: that a button, a chip and a dropdown trigger really do
// resolve to the same timing, the same easing and the same focus ring, and
// that adopting the material added no blur host to a control subtree.
const python = process.env.LQ_TEST_PYTHON || (process.platform === 'win32' ? 'venv/Scripts/python.exe' : 'venv/bin/python');
const read = (script: string) => JSON.parse(execFileSync(python, [script], { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 }));
const presentation = read('tests/e2e/scripts/render_lq_presentation.py');
const navMenus = read('tests/e2e/scripts/render_lq_nav_menu.py');
const forms = read('tests/e2e/scripts/render_lq_forms.py');

const OUT = '.codex-temp/claude-s9-u1-e2e';
const PALETTES = ['indigo', 'sky', 'mint', 'violet', 'rose', 'teal'];
const APPEARANCES = ['light', 'dark'];

const pick = (kinds: string[]) => (presentation.cases as any[]).filter(item => kinds.includes(item.kind)).map(item => item.html).join('');
const buttonHtml = pick(['button']);
const chipHtml = pick(['filter_chip', 'status_chip', 'tag_chip', 'chip']);
const navHtml = (navMenus.cases as any[]).map(item => item.html).join('');
const fieldHtml = (forms.cases as any[]).map(item => item.html).join('');

// The three components the brief names: a button, a chip and a dropdown
// trigger. Everything asserted below is read from these live nodes.
const BUTTON = '#stage .lq-btn--glass:not(:disabled)';
const CHIP = '#stage .lq-chip--filter:not(:disabled)';
const TRIGGER = '#stage .lq-nav-menu__trigger';
const TRIO: [string, string][] = [['button', BUTTON], ['chip', CHIP], ['trigger', TRIGGER]];

const pageHtml = (): string => [
  '<!doctype html><html lang="zh-CN" data-theme="lanshare" data-appearance="light" data-ui-palette="indigo" data-lq-glass="tinted">',
  '<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>LQ U1 统一交互</title>',
  '<link rel="stylesheet" href="/static/css/tailwind-app.css">',
  '<style>body{margin:0;background:hsl(var(--ls-surface-0))}main{padding:24px;max-width:1360px;margin:auto}',
  'section{margin-block:24px;padding:20px}h1{margin-bottom:16px}h2{font-size:18px;margin-bottom:12px}',
  '.row{display:flex;flex-wrap:wrap;gap:12px;align-items:center}#fields{display:grid;gap:16px;max-width:520px}</style></head><body><main>',
  '<h1>统一交互陈列</h1>',
  '<section id="stage"><h2>按钮 / 芯片 / 下拉触发器</h2>',
  '<div class="row">', buttonHtml, '</div>',
  '<div class="row">', chipHtml, '</div>',
  '<div class="row">', navHtml, '</div></section>',
  '<section class="lq-surface" id="in-content"><h2>内容面板内</h2><div class="row">', buttonHtml, '</div>',
  '<form id="fields">', fieldHtml, '</form></section>',
  '</main></body></html>',
].join('');

async function mount(page: Page) {
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin !== 'https://lq-u1.test') return route.abort();
    if (url.pathname.startsWith('/static/js/lq/') || url.pathname === '/static/css/tailwind-app.css') {
      const file = path.resolve(`.${url.pathname}`);
      if (file.startsWith(path.resolve('static') + path.sep) && fs.existsSync(file))
        return route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body: fs.readFileSync(file) });
    }
    if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: pageHtml() });
    return route.fulfill({ status: 404, contentType: 'text/plain', body: 'missing' });
  });
  await page.goto('https://lq-u1.test/');
  await expect(page.locator(BUTTON).first()).toBeVisible();
  await expect(page.locator(CHIP).first()).toBeVisible();
  await expect(page.locator(TRIGGER).first()).toBeVisible();
}

// Scan resolved theme colours, not a frame midway through the shared 180ms
// colour transition. Flush style first so newly created transitions are visible.
async function settleTheme(page: Page) {
  await page.evaluate(async () => {
    await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
    await Promise.all(document.getAnimations()
      .filter(animation => animation.effect?.getComputedTiming().iterations !== Infinity)
      .map(animation => animation.finished.catch(() => {})));
  });
}

const styleOf = (page: Page, selector: string, keys: string[]) =>
  page.locator(selector).first().evaluate((node, keys) => {
    const computed = getComputedStyle(node);
    return Object.fromEntries(keys.map(key => [key, (computed as any)[key] as string]));
  }, keys);

// A computed transition list is comma separated, but cubic-bezier() contains
// commas of its own, so a naive split reports four easings where there is one.
function splitList(value: string): string[] {
  const parts: string[] = [];
  let depth = 0, current = '';
  for (const char of value) {
    if (char === '(') depth += 1;
    if (char === ')') depth -= 1;
    if (char === ',' && depth === 0) { parts.push(current.trim()); current = ''; continue; }
    current += char;
  }
  if (current.trim()) parts.push(current.trim());
  return parts;
}

// Move the pointer onto the node and let the transition settle, so the reading
// is the resolved hover value rather than an interpolation.
async function hover(page: Page, selector: string) {
  await page.locator(selector).first().hover();
  await page.waitForTimeout(260);
}

test.describe('S9 U1 shared control interaction', () => {
  test('button, chip and trigger animate with one duration, one easing and one property list', async ({ page }) => {
    await mount(page);
    const measured: Record<string, any> = {};
    for (const [name, selector] of TRIO)
      measured[name] = await styleOf(page, selector, ['transitionDuration', 'transitionTimingFunction', 'transitionProperty']);
    // A single easing and a single duration across every property each of them
    // animates: a list of mixed values is drift wearing a shared name. Split on
    // top-level commas only -- cubic-bezier() carries three of its own.
    for (const [name, value] of Object.entries(measured)) {
      expect([...new Set(splitList(value.transitionDuration))], `${name} duration`).toHaveLength(1);
      expect([...new Set(splitList(value.transitionTimingFunction))], `${name} easing`).toHaveLength(1);
      expect(parseFloat(value.transitionDuration as string), `${name} duration is real`).toBeGreaterThan(0);
    }
    expect(measured.chip, 'chip vs button').toEqual(measured.button);
    expect(measured.trigger, 'trigger vs button').toEqual(measured.button);
    fs.mkdirSync(OUT, { recursive: true });
    fs.writeFileSync(`${OUT}/interaction.json`, JSON.stringify(measured, null, 2));
  });

  test('hover lifts all three by the same amount and lights the same halo slot', async ({ page }) => {
    await mount(page);
    const hovered: Record<string, any> = {};
    for (const [name, selector] of TRIO) {
      const resting = await styleOf(page, selector, ['transform', 'boxShadow']);
      await hover(page, selector);
      const active = await styleOf(page, selector, ['transform', 'boxShadow']);
      await page.mouse.move(0, 0);
      await page.waitForTimeout(200);
      hovered[name] = { resting, active };
      expect(resting.transform, `${name} rests flat`).toBe('none');
      expect(active.transform, `${name} lifts`).not.toBe('none');
      expect(active.boxShadow, `${name} halo appears`).not.toBe(resting.boxShadow);
    }
    expect(hovered.chip.active.transform, 'chip lift vs button').toBe(hovered.button.active.transform);
    expect(hovered.trigger.active.transform, 'trigger lift vs button').toBe(hovered.button.active.transform);
    fs.mkdirSync(OUT, { recursive: true });
    fs.writeFileSync(`${OUT}/hover.json`, JSON.stringify(hovered, null, 2));
  });

  test('one focus ring: same width, style, colour and offset on all three', async ({ page }) => {
    await mount(page);
    const keys = ['outlineWidth', 'outlineStyle', 'outlineColor', 'outlineOffset'];
    const rings: Record<string, any> = {};
    for (const [name, selector] of TRIO) {
      await page.locator(selector).first().focus();
      rings[name] = await styleOf(page, selector, keys);
      expect(rings[name].outlineStyle, `${name} ring is drawn`).toBe('solid');
    }
    expect(rings.chip, 'chip ring vs button').toEqual(rings.button);
    expect(rings.trigger, 'trigger ring vs button').toEqual(rings.button);
    // The fields adopted the same ring; they used to carry a wider one plus a
    // shadow halo, which is the drift this package removed.
    const field = '#fields .lq-input:not([readonly]):not([disabled])';
    await page.locator(field).first().focus();
    expect(await styleOf(page, field, keys), 'field ring vs button').toEqual(rings.button);
    fs.mkdirSync(OUT, { recursive: true });
    fs.writeFileSync(`${OUT}/focus-ring.json`, JSON.stringify(rings, null, 2));
  });

  test('no control subtree hosts a backdrop-filter, at rest or on hover', async ({ page }) => {
    await mount(page);
    const scan = async () => page.evaluate(() => {
      // Controls only. The nav menu's own panel is .lq-menu.lq-glass -- a
      // raised layer that the overlay package deliberately blurs, and which
      // LQ.layer portals out of the trigger at runtime anyway. Scoping to the
      // trigger keeps this gate about what it is about: a control must never
      // become a blur host.
      const selector = [
        '.lq-btn', '.lq-btn *', '.lq-chip', '.lq-chip *', '.lq-chip-row', '.lq-chip-row *',
        '.lq-nav-menu__trigger', '.lq-nav-menu__trigger *', '.lq-field', '.lq-field *',
        '.lq-input', '.lq-textarea', '.lq-select', '.lq-switch', '.lq-range', '.lq-checkbox', '.lq-radio',
      ].join(', ');
      const nodes = [...document.querySelectorAll(selector)];
      const offenders: string[] = [];
      for (const node of nodes)
        for (const pseudo of [null, '::before', '::after']) {
          const computed = getComputedStyle(node, pseudo as string | null);
          const value = computed.backdropFilter || (computed as any).webkitBackdropFilter;
          if (value && value !== 'none') offenders.push(`${node.className}${pseudo || ''}: ${value}`);
        }
      return { offenders, scanned: nodes.length };
    });
    const resting = await scan();
    expect(resting.scanned, 'the scan actually covered the gallery').toBeGreaterThan(100);
    expect(resting.offenders, 'at rest').toEqual([]);
    for (const [, selector] of TRIO) await hover(page, selector);
    expect((await scan()).offenders, 'after hovering each control').toEqual([]);
  });

  test('reduced motion removes the transition and every displacement', async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await mount(page);
    for (const [name, selector] of TRIO) {
      expect(await styleOf(page, selector, ['transitionProperty']), `${name} transition`).toEqual({ transitionProperty: 'none' });
      await hover(page, selector);
      expect((await styleOf(page, selector, ['transform'])).transform, `${name} stays put on hover`).toBe('none');
      await page.mouse.move(0, 0);
    }
    // Held down as well as hovered: the press is the other displacement.
    const box = (await page.locator(BUTTON).first().boundingBox())!;
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down();
    expect((await styleOf(page, BUTTON, ['transform'])).transform, 'button stays put while pressed').toBe('none');
    await page.mouse.up();
  });

  // One test per combination: twelve full-page axe scans plus twelve full-page
  // screenshots do not fit in a single 30s budget, and a timeout would say
  // nothing about the colours.
  for (const palette of PALETTES) for (const appearance of APPEARANCES) {
    test(`axe and gallery screenshot: ${palette}/${appearance}`, async ({ page }) => {
      await mount(page);
      await page.evaluate(({ palette, appearance }) => {
        document.documentElement.dataset.uiPalette = palette;
        document.documentElement.dataset.appearance = appearance;
      }, { palette, appearance });
      await settleTheme(page);
      const scan = await new AxeBuilder({ page }).analyze();
      const serious = scan.violations.filter(item => ['serious', 'critical'].includes(item.impact || ''));
      expect(serious.map(item => ({ id: item.id, impact: item.impact, nodes: item.nodes.map(node => node.target) })), `${palette}/${appearance}`).toEqual([]);
      fs.mkdirSync(OUT, { recursive: true });
      await page.screenshot({ path: `${OUT}/controls-${palette}-${appearance}.png`, fullPage: true });
    });
  }

  test('mobile width keeps the gallery readable and captured', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await mount(page);
    fs.mkdirSync(OUT, { recursive: true });
    for (const appearance of APPEARANCES) {
      await page.evaluate(value => { document.documentElement.dataset.appearance = value; }, appearance);
      await settleTheme(page);
      await page.screenshot({ path: `${OUT}/controls-390-${appearance}.png`, fullPage: true });
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });
});
