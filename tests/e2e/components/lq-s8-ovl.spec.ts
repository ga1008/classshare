import { test, expect, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import fs from 'node:fs';
import path from 'node:path';

// S8 OVL gate. Every layer is raised glass, so the questions this file answers
// are: does the layer read as one substance across kinds, does the blur stay on
// the layer boundary instead of leaking inside it, and does the body/secondary
// text still clear AA on that material in all six palettes.
const OUT = '.codex-temp/claude-s8-ovl-e2e';
const PALETTES = ['teal', 'indigo', 'sky', 'mint', 'violet', 'rose'];
const measurements: any[] = [];

// A page that already spends a realistic blur budget: a glass top bar plus
// three content-material panels. Whatever the layer adds shows up on top.
const PAGE = [
  '<!doctype html><html lang="zh-CN" data-theme="lanshare" data-ui-palette="teal" data-appearance="light" data-lq-glass="tinted">',
  '<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>LQ overlay material</title>',
  '<link rel="stylesheet" href="/static/css/tailwind-app.css">',
  '<style>body{margin:0;min-height:100dvh;background:linear-gradient(140deg,hsl(var(--ls-primary)),hsl(var(--ls-surface-2)) 45%,hsl(var(--ls-surface-0)));}',
  '.page{display:grid;grid-template-columns:240px 1fr;gap:16px;padding:16px;}',
  '.bar{position:sticky;top:0;display:flex;gap:12px;align-items:center;padding:12px 16px;margin:16px;}',
  '.card{padding:16px;margin-bottom:16px;}</style></head><body>',
  '<header class="bar lq-topbar lq-glass"><strong>顶栏</strong>',
  '<button id="opener" class="lq-btn lq-btn--glass">打开</button>',
  '<button id="anchor" class="lq-btn lq-btn--glass">锚点</button>',
  '<span id="tip-host"></span></header>',
  '<div class="page"><nav class="lq-surface card" aria-label="侧栏"><p>侧栏正文</p><p class="lq-material-muted">侧栏次级文字</p></nav>',
  '<main><section class="lq-surface card"><h2>内容卡片一</h2><p>正文文字用于对照。</p><p class="lq-material-muted">次级说明文字。</p></section>',
  '<section class="lq-surface card"><h2>内容卡片二</h2><p>正文文字用于对照。</p><p class="lq-material-muted">次级说明文字。</p></section></main></div>',
  '<script type="module">import * as dialogs from "/static/js/lq/dialogs.js";import * as menus from "/static/js/lq/menus.js";',
  'import {createComponent} from "/static/js/lq/components.js";',
  'import * as tips from "/static/js/lq/tooltips.js";import * as toast from "/static/js/lq/toast.js";',
  'import {getLayerSystem} from "/static/js/lq/layer.js";',
  'window.dialogs=dialogs;window.menus=menus;window.tips=tips;window.system=toast.getToastSystem(document);',
  'window.layer=getLayerSystem(document);',
  // bindTooltip only accepts an icon-only button, so build the anchor through the factory.
  'const tipButton=createComponent("button",{icon:"info",variant:"glass",attrs:{id:"tip","aria-label":"提示锚点"}});',
  'document.querySelector("#tip-host").append(tipButton);',
  'document.body.dataset.ready="true";</script></body></html>',
].join('');

async function mount(page: Page) {
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin !== 'https://lq-s8-ovl.test') return route.abort();
    if (url.pathname.startsWith('/static/')) {
      const file = path.resolve(`.${url.pathname}`);
      if (file.startsWith(path.resolve('static') + path.sep) && fs.existsSync(file)) {
        return route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body: fs.readFileSync(file) });
      }
      return route.abort();
    }
    if (url.pathname === '/') return route.fulfill({ contentType: 'text/html; charset=utf-8', body: PAGE });
    return route.abort();
  });
  await page.goto('https://lq-s8-ovl.test/');
  await expect(page.locator('body')).toHaveAttribute('data-ready', 'true');
}

// Every element whose computed backdrop-filter is a real filter, split into the
// ones inside the open layer and the ones that belong to the page behind it.
async function blurHosts(page: Page, overlaySelector: string) {
  return page.evaluate(selector => {
    const overlay = document.querySelector(selector);
    const hosts = [...document.querySelectorAll('*')].filter(node => {
      const value = getComputedStyle(node).backdropFilter;
      return !!value && value !== 'none';
    });
    const name = (node: Element) => `${node.tagName.toLowerCase()}.${[...node.classList].join('.')}`;
    return {
      total: hosts.length,
      pageHosts: hosts.filter(node => !overlay || !overlay.contains(node)).map(name),
      insideOverlay: hosts.filter(node => !!overlay && overlay.contains(node) && node !== overlay).map(name),
      overlayIsHost: !!overlay && hosts.includes(overlay),
    };
  }, overlaySelector);
}

// The shared language: same stroke, same rim, same sheen. Radius and shadow are
// the only things allowed to differ, and only between tiers.
async function materialOf(page: Page, selector: string) {
  return page.evaluate(target => {
    const node = document.querySelector(target)!;
    const own = getComputedStyle(node);
    const rim = getComputedStyle(node, '::before');
    return {
      radius: own.borderTopLeftRadius,
      border: `${own.borderTopWidth} ${own.borderTopStyle} ${own.borderTopColor}`,
      shadow: own.boxShadow,
      background: own.backgroundColor,
      rimTop: rim.borderTopColor,
      rimBottom: rim.borderBottomColor,
      sheen: rim.backgroundImage !== 'none',
      blurred: !!own.backdropFilter && own.backdropFilter !== 'none',
    };
  }, selector);
}

const DIALOGS: Record<string, any> = {
  modal: { id: 'ovl-modal', title: '标准弹窗', body: '弹窗正文文字，用于核对 raised 材质上的可读性。', footer: '脚注说明' },
  drawer: { id: 'ovl-drawer', type: 'drawer', title: '抽屉', body: '抽屉正文文字。' },
  sheet: { id: 'ovl-sheet', type: 'sheet', side: 'bottom', title: '底部面板', body: '底部面板正文文字。' },
  popover: { id: 'ovl-popover', type: 'popover', title: '气泡', body: '锚定气泡正文文字。' },
};

async function openDialog(page: Page, kind: string) {
  await page.evaluate(props => {
    const w = window as any;
    const trigger = document.querySelector('#opener');
    // A popover is anchored by contract; the other kinds take the viewport.
    const options = props.type === 'popover' ? { trigger, anchor: trigger } : { trigger };
    w.handle = w.dialogs.openDialog(w.dialogs.createDialog(props), options);
  }, DIALOGS[kind]);
  await expect(page.locator('.lq-dialog__surface')).toBeVisible();
}

async function openMenu(page: Page) {
  await page.evaluate(() => {
    const w = window as any;
    const root = w.menus.createMenu({
      id: 'ovl-menu', label: '文档操作', items: [
        { id: 'open', label: '打开' }, { id: 'rename', label: '重命名' },
        { id: 'share', label: '分享给同事' }, { id: 'delete', label: '删除', danger: true },
      ],
    });
    w.binding = w.menus.bindMenu(document.querySelector('#anchor'), root, {});
  });
  await page.locator('#anchor').click();
  await expect(page.locator('.lq-menu')).toBeVisible();
}

async function openTooltip(page: Page) {
  await page.evaluate(() => {
    const w = window as any;
    w.tip = w.tips.bindTooltip(document.querySelector('#tip'), { id: 'ovl-tip', text: '这是一条提示文字' });
  });
  await page.locator('#tip').hover();
  await expect(page.locator('.lq-tooltip')).toBeVisible();
}

async function openToasts(page: Page) {
  await page.evaluate(() => {
    const w = window as any;
    w.system.show('保存成功，改动已同步。', { tone: 'success', duration: 0 });
    w.system.show('有一条待处理的提醒。', { tone: 'info', duration: 0 });
    w.system.show('导出失败，请重试。', { tone: 'danger', duration: 0 });
  });
  await expect(page.locator('.lq-toast').first()).toBeVisible();
}

const SCENES: [string, (page: Page) => Promise<void>, string][] = [
  ['modal', page => openDialog(page, 'modal'), '.lq-dialog__surface'],
  ['drawer', page => openDialog(page, 'drawer'), '.lq-dialog__surface'],
  ['sheet', page => openDialog(page, 'sheet'), '.lq-dialog__surface'],
  ['popover', page => openDialog(page, 'popover'), '.lq-dialog__surface'],
  ['menu', openMenu, '.lq-menu'],
  ['tooltip', openTooltip, '.lq-tooltip'],
  ['toast', openToasts, '.lq-toast'],
];

test.describe('LQ S8 overlay material', () => {
  test.beforeEach(async ({ page }) => mount(page));

  for (const appearance of ['light', 'dark']) {
    for (const width of [1440, 390]) {
      test(`${appearance} ${width}: blur budget holds and every layer kind is captured open`, async ({ page }) => {
        await page.setViewportSize({ width, height: width === 390 ? 844 : 980 });
        for (const [name, open, selector] of SCENES) {
          await page.evaluate(value => { document.documentElement.dataset.appearance = value; }, appearance);
          await open(page);
          await page.waitForTimeout(340);
          const hosts = await blurHosts(page, selector);
          const material = await materialOf(page, selector);
          measurements.push({ scene: name, appearance, width, ...hosts, material });
          // Engineering rule 1: a layer is the boundary, so nothing inside it blurs.
          expect(hosts.insideOverlay, `${name} ${appearance} ${width}`).toEqual([]);
          // Budget: glass top bar + 3 content panels + at most 2 hosts from the layer.
          expect(hosts.total, `${name} ${appearance} ${width}`).toBeLessThanOrEqual(6);
          // Shared language: every layer carries the rim and the sheen.
          expect(material.rimTop, `${name} rim`).not.toEqual(material.rimBottom);
          expect(material.sheen, `${name} sheen`).toBe(true);
          await page.mouse.move(0, 0);
          await page.screenshot({ path: `${OUT}/${name}-${appearance}-${width}.png` });
          await page.reload();
          await expect(page.locator('body')).toHaveAttribute('data-ready', 'true');
        }
      });
    }
  }

  test('same tier, same geometry: radius, stroke and shadow do not drift within a tier', async ({ page }) => {
    const seen: Record<string, any> = {};
    for (const [name, open, selector] of SCENES) {
      await open(page);
      await page.waitForTimeout(200);
      seen[name] = await materialOf(page, selector);
      await page.reload();
      await expect(page.locator('body')).toHaveAttribute('data-ready', 'true');
    }
    measurements.push({ tiers: seen });
    // O1 focus layers: same stroke, same shadow.
    expect(seen.drawer.shadow).toEqual(seen.modal.shadow);
    expect(seen.drawer.border).toEqual(seen.modal.border);
    expect(seen.sheet.border).toEqual(seen.modal.border);
    // O2 floating panels: the deepest shadow too, one step tighter radius.
    expect(seen.menu.shadow).toEqual(seen.modal.shadow);
    expect(seen.toast.shadow).toEqual(seen.menu.shadow);
    expect(seen.toast.radius).toEqual(seen.menu.radius);
    expect(seen.popover.radius).toEqual(seen.menu.radius);
    expect(seen.menu.border).toEqual(seen.modal.border);
    expect(seen.toast.border).toEqual(seen.menu.border);
    // O3 hint: same stroke, lighter shadow, tighter radius, never a blur host.
    expect(seen.tooltip.border).toEqual(seen.menu.border);
    expect(seen.tooltip.shadow).not.toEqual(seen.menu.shadow);
    expect(seen.tooltip.blurred).toBe(false);
    expect(seen.toast.blurred).toBe(false);
    // Fill: every layer lands on the same raised colour.
    expect(seen.menu.background).toEqual(seen.modal.background);
    expect(seen.toast.background).toEqual(seen.modal.background);
    expect(seen.tooltip.background).toEqual(seen.modal.background);
    expect(seen.popover.background).toEqual(seen.modal.background);
  });

  for (const appearance of ['light', 'dark']) {
    test(`${appearance}: six palettes, no serious or critical axe finding on dialog, menu or toast text`, async ({ page }) => {
      const findings: any[] = [];
      for (const palette of PALETTES) {
        for (const [name, open, selector] of [SCENES[0], SCENES[4], SCENES[6]] as typeof SCENES) {
          await page.evaluate(({ appearance, palette }) => {
            document.documentElement.dataset.appearance = appearance;
            document.documentElement.dataset.uiPalette = palette;
          }, { appearance, palette });
          await open(page);
          await page.waitForTimeout(340);
          const audit = await new AxeBuilder({ page }).include(selector).analyze();
          const serious = audit.violations.filter(item => ['serious', 'critical'].includes(item.impact || ''));
          findings.push({ palette, appearance, scene: name, serious: serious.map(item => `${item.id}:${item.nodes.length}`) });
          expect(serious, `${name} ${palette} ${appearance}`).toEqual([]);
          await page.reload();
          await expect(page.locator('body')).toHaveAttribute('data-ready', 'true');
        }
      }
      measurements.push({ axe: findings });
    });
  }

  test.afterAll(() => {
    fs.mkdirSync(OUT, { recursive: true });
    fs.writeFileSync(`${OUT}/measurements.json`, JSON.stringify(measurements, null, 2));
  });
});
