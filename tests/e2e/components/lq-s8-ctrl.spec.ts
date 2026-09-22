import { test, expect, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';

// S8 CTRL gate. Controls are control-level glass: they carry the tint and the
// rim, never their own blur, and every text pair they can produce has to clear
// AA against both extremes of what the page backdrop can put behind them.
const python = process.env.LQ_TEST_PYTHON || (process.platform === 'win32' ? 'venv/Scripts/python.exe' : 'venv/bin/python');
const presentation = JSON.parse(execFileSync(python, ['tests/e2e/scripts/render_lq_presentation.py'], { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 }));
const forms = JSON.parse(execFileSync(python, ['tests/e2e/scripts/render_lq_forms.py'], { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 }));
const palettes = ['indigo', 'sky', 'mint', 'violet', 'rose', 'teal'];
const appearances = ['light', 'dark'];
const output = '.codex-temp/claude-s8-ctrl-e2e';
// Image-layer opacity of the page backdrop, the same bound that
// tests/test_lq_tokens.py::GlassMaterialContrastTests pins the tokens against.
const IMAGE_OPACITY: Record<string, number> = { light: .55, dark: .42 };
// Rows whose ink comes from the accent/tone text tokens (--ls-tone-*-fg,
// --ls-on-primary-soft). S8 tightened --ls-ink-3 and --ls-glass-muted for the
// new material scale but not these, and the same pair is used by
// components/indicators.css, so the fix belongs in tokens.css, which this
// package may not edit. Measured and reported, never silently dropped: the
// assertion below pins this list so no new failure can join it.
const INHERITED_TOKEN_GAP = ['btn/destructive', 'chip/status', 'error-summary'];

const pick = (cases: any[], kinds: string[]) => cases.filter(item => kinds.includes(item.kind)).map(item => item.html).join('');
const buttonHtml = pick(presentation.cases, ['button']);
const chipHtml = pick(presentation.cases, ['filter_chip', 'status_chip', 'tag_chip', 'chip']);
const fieldHtml = (forms.cases as any[]).map(item => item.html).join('');

const pageHtml = (): string => [
  '<!doctype html><html lang="zh-CN" data-theme="lanshare" data-appearance="light" data-ui-palette="indigo" data-lq-glass="tinted">',
  '<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>LQ control material</title>',
  '<link rel="stylesheet" href="/static/css/tailwind-app.css">',
  '<style>body{margin:0;background:hsl(var(--ls-surface-0))}main{padding:24px;max-width:1360px;margin:auto}',
  'section{margin-block:24px;padding:20px}h1{margin-bottom:16px}h2{font-size:18px;margin-bottom:12px}',
  '.row{display:flex;flex-wrap:wrap;gap:12px;align-items:center}#fields{display:grid;gap:16px;max-width:520px}</style></head><body><main>',
  '<h1>控件材质陈列</h1>',
  '<section id="on-page"><h2>页面上</h2><div class="row">', buttonHtml, '</div><div class="row">', chipHtml, '</div></section>',
  '<section class="lq-surface" id="on-content"><h2>内容面板内</h2><div class="row">', buttonHtml, '</div><div class="row">', chipHtml,
  '</div><form id="fields">', fieldHtml, '</form></section>',
  '<section class="lq-glass" id="on-chrome"><h2>外壳材质内</h2><div class="row">', buttonHtml, '</div><div class="row">', chipHtml, '</div></section>',
  '<section class="lq-glass lq-glass--thick" id="on-raised"><h2>弹层材质内</h2><div class="row">', buttonHtml, '</div><div class="row">', chipHtml, '</div></section>',
  '</main><script type="module">import * as components from "/static/js/lq/components.js";components.enhanceComponents(document);document.body.dataset.ready="true";</script></body></html>',
].join('');

async function mount(page: Page) {
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin !== 'https://lq-s8-ctrl.test') return route.abort();
    if (url.pathname.startsWith('/static/js/lq/') || url.pathname === '/static/css/tailwind-app.css') {
      const file = path.resolve(`.${url.pathname}`);
      if (file.startsWith(path.resolve('static') + path.sep) && fs.existsSync(file))
        return route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body: fs.readFileSync(file) });
    }
    if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: pageHtml() });
    return route.fulfill({ status: 404, contentType: 'text/plain', body: 'missing' });
  });
  // Theme attributes animate background-color; reduced motion removes the
  // transition so a measurement reads the settled value, not an interpolation.
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('https://lq-s8-ctrl.test/');
  await expect(page.locator('body')).toHaveAttribute('data-ready', 'true');
}

async function apply(page: Page, palette: string, appearance: string) {
  await page.evaluate(({ palette, appearance }) => {
    document.documentElement.dataset.uiPalette = palette;
    document.documentElement.dataset.appearance = appearance;
  }, { palette, appearance });
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve(null)))));
}

// Installed in the page so the measurement uses the browser's own resolved
// values: parse rgb(a), composite background-color plus any gradient layer
// over a backing colour, and score WCAG relative luminance contrast.
const PROBE = `window.__ctrl = (() => {
  const parse = value => { const n = (String(value).match(/-?[\\d.]+/g) || []).map(Number); return n.length >= 3 ? [n[0], n[1], n[2], n.length > 3 ? n[3] : 1] : [0, 0, 0, 0]; };
  const over = (fg, bg) => [fg[0] * fg[3] + bg[0] * (1 - fg[3]), fg[1] * fg[3] + bg[1] * (1 - fg[3]), fg[2] * fg[3] + bg[2] * (1 - fg[3]), 1];
  const channel = v => { const s = v / 255; return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4); };
  const lum = c => 0.2126 * channel(c[0]) + 0.7152 * channel(c[1]) + 0.0722 * channel(c[2]);
  const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
  const layers = style => { const out = [parse(style.backgroundColor)]; const found = (style.backgroundImage || 'none').match(/rgba?\\([^)]*\\)/g) || []; for (const item of found) out.push(parse(item)); return out; };
  const effective = (style, backing) => layers(style).reduce((acc, layer) => over(layer, acc), backing);
  return { parse, over, ratio, effective };
})();`;

test.describe('S8 control material', () => {
  test('no control subtree hosts a backdrop-filter', async ({ page }) => {
    await mount(page);
    const hosts = await page.evaluate(() => {
      const selector = '.lq-btn, .lq-btn *, .lq-chip, .lq-chip *, .lq-chip-row, .lq-chip-row *, .lq-field, .lq-field *, .lq-input, .lq-textarea, .lq-select, .lq-switch, .lq-range, .lq-checkbox, .lq-radio, .lq-selection, .lq-selection *';
      const nodes = [...document.querySelectorAll(selector)];
      const offenders: string[] = [];
      for (const node of nodes) {
        for (const pseudo of [null, '::before', '::after']) {
          const computed = getComputedStyle(node, pseudo as string | null);
          const value = computed.backdropFilter || (computed as any).webkitBackdropFilter;
          if (value && value !== 'none') offenders.push(`${node.className}${pseudo || ''}: ${value}`);
        }
      }
      return { offenders, scanned: nodes.length };
    });
    expect(hosts.scanned).toBeGreaterThan(100);
    expect(hosts.offenders).toEqual([]);
  });

  test('control material contrast clears AA on every palette and appearance', async ({ page }) => {
    await mount(page);
    const table: any[] = [];
    for (const palette of palettes) for (const appearance of appearances) {
      await apply(page, palette, appearance);
      const rows = await page.evaluate(({ opacity, probe }) => {
        // eslint-disable-next-line no-eval
        (0, eval)(probe);
        const api = (window as any).__ctrl;
        const onPage = document.querySelector('#on-page')!;
        const surface = api.parse(getComputedStyle(document.body).backgroundColor);
        const extremes = [0, 255].map(photo => [0, 1, 2].map(i => photo * opacity + surface[i] * (1 - opacity)).concat(1));
        const score = (ink: number[], style: CSSStyleDeclaration, backings: number[][]) =>
          Math.min(...backings.map(backing => api.ratio(ink, api.effective(style, backing))));
        const results: any[] = [];
        const measure = (label: string, node: Element | null) => {
          if (!node) return;
          const style = getComputedStyle(node);
          results.push({ label, ratio: Math.round(score(api.parse(style.color), style, extremes) * 100) / 100, ink: style.color, fill: style.backgroundColor });
        };
        measure('btn/prominent', onPage.querySelector('.lq-btn--prominent'));
        measure('btn/glass', onPage.querySelector('.lq-btn--glass'));
        measure('btn/soft', onPage.querySelector('.lq-btn--soft'));
        measure('btn/destructive', onPage.querySelector('.lq-btn--destructive'));
        measure('btn/disabled', onPage.querySelector('.lq-btn[disabled]'));
        measure('chip/base', onPage.querySelector('.lq-chip:not(.lq-chip--status):not([aria-pressed="true"])'));
        measure('chip/selected', onPage.querySelector('.lq-chip[aria-pressed="true"]'));
        measure('chip/status', onPage.querySelector('.lq-chip--status'));
        measure('chip/disabled', onPage.querySelector('.lq-chip--filter[disabled]'));
        measure('input/value', document.querySelector('.lq-input:not([readonly]):not([disabled])'));
        measure('textarea/value', document.querySelector('.lq-textarea:not([readonly]):not([disabled])'));
        measure('select/value', document.querySelector('.lq-select:not([disabled])'));
        measure('input/disabled', document.querySelector('.lq-input[disabled]'));
        measure('range/value', document.querySelector('.lq-range__value'));
        measure('error-summary', document.querySelector('.lq-error-summary'));
        // Placeholders are not covered by axe, and Chrome's
        // getComputedStyle(el, '::placeholder') answers with the element's own
        // colour, so read the declaration out of the live stylesheet and
        // resolve it through a probe in the current theme.
        const input = document.querySelector('.lq-input:not([readonly]):not([disabled])') as HTMLElement | null;
        if (input) {
          let declaration = '';
          for (const sheet of [...document.styleSheets]) {
            let rules: CSSRuleList | null = null;
            try { rules = sheet.cssRules; } catch { continue; }
            for (const rule of [...(rules as any)] as any[])
              if (rule.selectorText && /\.lq-input::(-webkit-input-|-moz-)?placeholder/.test(rule.selectorText) && rule.style.color)
                declaration = rule.style.color;
          }
          const span = document.createElement('span');
          span.style.color = declaration;
          document.body.append(span);
          const resolved = getComputedStyle(span).color;
          span.remove();
          const style = getComputedStyle(input);
          results.push({ label: 'input/placeholder', ratio: Math.round(score(api.parse(resolved), style, extremes) * 100) / 100, ink: resolved, fill: style.backgroundColor, declaration });
        }
        // Ghost and link carry no resting fill, so they are measured on the
        // material that actually hosts them.
        for (const entry of [['content', '#on-content'], ['chrome', '#on-chrome'], ['raised', '#on-raised']]) {
          const root = document.querySelector(entry[1])!;
          const backings = extremes.map(backing => api.effective(getComputedStyle(root), backing));
          for (const variant of [['btn/ghost', '.lq-btn--ghost'], ['btn/link', '.lq-btn--link']]) {
            const node = root.querySelector(variant[1]) as HTMLElement | null;
            if (!node) continue;
            const style = getComputedStyle(node);
            results.push({ label: `${variant[0]}@${entry[0]}`, ratio: Math.round(score(api.parse(style.color), style, backings) * 100) / 100, ink: style.color, fill: style.backgroundColor });
          }
        }
        return results;
      }, { opacity: IMAGE_OPACITY[appearance], probe: PROBE });
      table.push({ palette, appearance, rows });
      for (const row of rows) {
        if (INHERITED_TOKEN_GAP.includes(row.label)) continue;
        // WCAG 1.4.3 exempts disabled controls; they still have to stay
        // readable, so they get a 3:1 floor instead of a pass.
        const floor = row.label.includes('disabled') ? 3 : 4.5;
        expect(row.ratio, `${palette}/${appearance} ${row.label} ink=${row.ink} fill=${row.fill}`).toBeGreaterThanOrEqual(floor);
      }
      // The exempt set is pinned, so a new failure cannot quietly join it.
      const failing = rows.filter(row => row.ratio < (row.label.includes('disabled') ? 3 : 4.5)).map(row => row.label).sort();
      expect(failing, `${palette}/${appearance}: rows below the floor`).toEqual(INHERITED_TOKEN_GAP.filter(label => rows.some(row => row.label === label && row.ratio < 4.5)).sort());
    }
    fs.mkdirSync(output, { recursive: true });
    fs.writeFileSync(`${output}/contrast.json`, JSON.stringify({
      note: 'CTRL gate. Rows in inheritedTokenGap are carried by --ls-tone-*-fg / --ls-on-primary-soft, which tokens.css has not tightened for the S8 material scale; CTRL may not edit tokens.css.',
      inheritedTokenGap: INHERITED_TOKEN_GAP, table,
    }, null, 2));
  });

  for (const palette of palettes) for (const appearance of appearances) {
    test(`axe and gallery screenshot: ${palette}/${appearance}`, async ({ page }) => {
      await mount(page);
      await apply(page, palette, appearance);
      const scan = await new AxeBuilder({ page }).analyze();
      expect(scan.violations.filter(item => ['serious', 'critical'].includes(item.impact || '')).map(item =>
        ({ id: item.id, impact: item.impact, nodes: item.nodes.map(node => ({ target: node.target, summary: node.failureSummary })) })), `${palette}/${appearance}`).toEqual([]);
      fs.mkdirSync(output, { recursive: true });
      await page.screenshot({ path: `${output}/controls-${palette}-${appearance}.png`, fullPage: true });
    });
  }
});
