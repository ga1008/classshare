import { test, expect, type Page, type Locator } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { buildReactFixture } from './lq-react-fixture';

const python = process.env.LQ_TEST_PYTHON || (process.platform === 'win32' ? 'venv/Scripts/python.exe' : 'venv/bin/python');
const fixture = JSON.parse(execFileSync(python, ['tests/e2e/scripts/render_lq_presentation.py'], { encoding: 'utf8' }));
const variants = ['prominent', 'glass', 'soft', 'ghost', 'destructive', 'link'];
const cases = fixture.cases.filter((item: any) => variants.some(variant => [ `button-${variant}-md`, `button-${variant}-md-busy` ].includes(item.id))
  || ['button-prominent-md-busy', 'button-native-disabled', 'filter-on', 'filter-off', 'filter-disabled'].includes(item.id));
let css: string;
let react: string;

// Exercise production source before the release build, without overwriting any
// shared asset or starting an application/database. Same Tailwind config as CLI.
function source(file: string): string {
  return fs.readFileSync(file, 'utf8').replace(/@import\s+["']([^"']+)["'];/g,
    (_, relative) => source(path.resolve(path.dirname(file), relative)));
}
test.beforeAll(async () => {
  const postcss = require('postcss');
  const tailwind = require('tailwindcss');
  css = process.env.LQ_CONTROL_BUILT_CSS === '1' ? fs.readFileSync('static/css/tailwind-app.css', 'utf8')
    : (await postcss([tailwind(require(path.resolve('tailwind.config.js')))])
      .process(source(path.resolve('static/css/ui-system.src.css')), { from: path.resolve('static/css/ui-system.src.css') })).css;
  react = await buildReactFixture(`
    import React from 'react'; import {createRoot} from 'react-dom/client'; import {flushSync} from 'react-dom';
    import {LqButton} from '@/components/lq-presentation';
    window.mountReact=items=>{const root=createRoot(document.querySelector('#react'));flushSync(()=>root.render(<>
      {items.filter(item=>item.kind==='button').map(item=><div data-case={item.id} key={item.id}><LqButton {...item.props}/></div>)}
    </>));};window.reactReady=true;
  `);
});

async function mount(page: Page, palette = 'indigo', appearance = 'light') {
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin !== 'http://lq-control-states.test') return route.abort();
    if (url.pathname === '/source.css') return route.fulfill({ contentType: 'text/css', body: css });
    if (url.pathname === '/react.js') return route.fulfill({ contentType: 'text/javascript', body: react });
    if (url.pathname.startsWith('/static/')) {
      const file = path.resolve(`.${url.pathname}`);
      if (file.startsWith(path.resolve('static') + path.sep) && fs.existsSync(file))
        return route.fulfill({ contentType: 'text/javascript', body: fs.readFileSync(file) });
    }
    if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: `<!doctype html>
      <html lang="zh-CN" data-ui-palette="${palette}" data-appearance="${appearance}" data-lq-tier="A" data-lq-glass="tinted">
      <head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/source.css"><style>
      body{background:hsl(var(--ls-surface-0));padding:20px}section{display:flex;gap:14px;flex-wrap:wrap;padding:14px}
      .scene{padding:18px;display:flex;gap:12px;align-items:center}.lq-menu{position:static}
      </style></head><body><section id="jinja">${cases.map((item: any) => `<div data-case="${item.id}">${item.html}</div>`).join('')}</section>
      <section id="native"></section><section id="react"></section>
      <section id="legacy"><button class="btn btn-primary">登录</button><button class="btn btn-secondary">取消</button>
      <button class="btn btn-primary loading" disabled>登录中</button><button class="btn btn-secondary" disabled>不可用</button></section>
      <header data-lq-navbar-topbar><div class="lq-report-card-tools"><button id="nav" class="lq-btn lq-btn--ghost lq-nav-menu__trigger" aria-expanded="true">课堂菜单</button></div></header>
      <section class="lq-menu"><button id="menu-item" class="lq-btn lq-btn--ghost lq-menu__item">打开课堂</button><button id="danger-item" class="lq-btn lq-btn--ghost lq-menu__item" data-danger="true">删除</button></section>
      <section><button id="selected-link" class="lq-chip lq-chip--filter is-selected" aria-current="true">当前筛选</button>
      <div id="selected-option" class="lq-selection__option" role="option" aria-selected="true">选中项</div></section>
      <section class="scene" data-lq-tone="dark" style="background:#182330"><button class="lq-btn lq-btn--glass">深色场景</button><input class="lq-input" value="可读输入"></section>
      <section class="scene" data-lq-tone="light" style="background:#e4e8ed"><button class="lq-btn lq-btn--glass">浅色场景</button><input class="lq-input" value="可读输入"></section>
      <script type="module" src="/react.js"></script><script type="module">
      import {createComponent,enhanceComponents} from '/static/js/lq/components.js';
      window.nativeApi={createComponent};enhanceComponents(document);window.nativeReady=true;
      </script></body></html>` });
    return route.fulfill({ status: 404, body: 'missing fixture asset' });
  });
  await page.goto('http://lq-control-states.test/');
  await page.waitForFunction(() => (window as any).nativeReady && (window as any).reactReady);
  await page.evaluate(items => {
    for (const item of items) {
      const node = document.createElement('div'); node.dataset.case = item.id;
      node.append((window as any).nativeApi.createComponent(item.kind, item.props));
      document.querySelector('#native')!.append(node);
    }
    (window as any).mountReact(items);
  }, cases);
}

async function measured(control: Locator) {
  return control.evaluate(node => {
    const style = getComputedStyle(node);
    const label = node.querySelector('.lq-btn__label,.lq-chip__label') || node;
    const labelStyle = getComputedStyle(label);
    const rgba = (value: string) => {
      const values = value.match(/[\d.]+/g)!.map(Number);
      return [values[0], values[1], values[2], values[3] ?? 1];
    };
    const blend = (fg: number[], bg: number[]) => fg.slice(0, 3).map((value, i) => value * fg[3] + bg[i] * (1 - fg[3])).concat(1);
    let backdrop = [255, 255, 255, 1];
    const parents: Element[] = [];
    for (let parent = node.parentElement; parent; parent = parent.parentElement) parents.unshift(parent);
    for (const parent of parents) backdrop = blend(rgba(getComputedStyle(parent).backgroundColor), backdrop);
    let fill = blend(rgba(style.backgroundColor), backdrop);
    // Soft/destructive controls use a flat tint gradient above the glass fill.
    // Include that actual composited layer rather than testing only its base.
    const tint = style.backgroundImage.match(/^linear-gradient\((rgba?\([^)]*\))/);
    if (tint) fill = blend(rgba(tint[1]), fill);
    const ink = rgba(labelStyle.color);
    const luminance = (color: number[]) => color.slice(0, 3).map(value => value / 255)
      .map(value => value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4)
      .reduce((sum, value, index) => sum + value * [.2126, .7152, .0722][index], 0);
    const light = luminance(blend(ink, fill)), dark = luminance(fill);
    return { color: labelStyle.color, background: style.backgroundColor, image: style.backgroundImage,
      contrast: (Math.max(light, dark) + .05) / (Math.min(light, dark) + .05),
      alpha: ink[3], opacity: Number(style.opacity) * Number(labelStyle.opacity),
      active: node.matches(':active'), text: label.textContent, busy: node.getAttribute('aria-busy') };
  });
}

async function assertReadable(control: Locator, context: string) {
  const value = await measured(control);
  expect(value.alpha, context).toBe(1);
  expect(value.opacity, context).toBe(1);
  expect(value.contrast, `${context}: ${JSON.stringify(value)}`).toBeGreaterThanOrEqual(4.5);
  return value;
}

async function tokenColor(control: Locator, token: string) {
  return control.evaluate((node, token) => {
    const probe = document.createElement('span');
    probe.style.color = `hsl(var(${token}))`; node.append(probe);
    const color = getComputedStyle(probe).color; probe.remove(); return color;
  }, token);
}

for (const appearance of ['light', 'dark']) for (const palette of ['indigo', 'sky', 'mint', 'violet', 'rose', 'teal']) {
  test(`LQ paired control states ${palette}/${appearance}: held pointer, held Space, selected and busy`, async ({ page }) => {
    await mount(page, palette, appearance);
    for (const entry of ['jinja', 'native', 'react']) {
      for (const variant of variants) {
        const control = page.locator(`#${entry} [data-case="button-${variant}-md"] > button`);
        await control.hover();
        await page.mouse.down();
        try {
          const value = await assertReadable(control, `${entry}/${variant} first pressed frame`);
          expect(value.active).toBe(true);
          expect(value.background).toBe(await tokenColor(control, variant === 'prominent' ? '--ls-on-primary'
            : variant === 'destructive' ? '--ls-tone-danger-solid' : '--ls-primary'));
        } finally { await page.mouse.up(); }
        await control.focus();
        await page.keyboard.down('Space');
        try {
          const value = await assertReadable(control, `${entry}/${variant} held Space`);
          expect(value.active).toBe(true);
        } finally { await page.keyboard.up('Space'); }
        await control.evaluate((node: HTMLButtonElement) => { node.disabled = true; });
        await assertReadable(control, `${entry}/${variant} disabled`);
        await control.evaluate((node: HTMLButtonElement) => { node.disabled = false; });
        await assertReadable(page.locator(`#${entry} [data-case="button-${variant}-md-busy"] > button`), `${entry}/${variant} busy`);
      }
      await assertReadable(page.locator(`#${entry} [data-case="button-prominent-md-busy"] > button`), `${entry} busy label`);
      await assertReadable(page.locator(`#${entry} [data-case="button-native-disabled"] > button`), `${entry} disabled label`);
    }
    for (const selector of ['#selected-link', '#selected-option', '#nav']) {
      const control = page.locator(selector);
      await control.hover(); // header hover must not erase selected fill
      const selected = await assertReadable(control, `${selector} selected/expanded + hover`);
      expect(selected.background).toBe(await tokenColor(control, '--ls-primary'));
    }
    for (const selector of ['#menu-item', '#danger-item']) {
      const control = page.locator(selector);
      await page.keyboard.press('Tab'); await control.focus();
      expect(await control.evaluate(node => node.matches(':focus-visible'))).toBe(true);
      const focused = await assertReadable(control, `${selector} keyboard focus`);
      expect(focused.background).toBe(await tokenColor(control, selector === '#danger-item' ? '--ls-tone-danger-solid' : '--ls-primary'));
      expect(focused.color).toBe(await tokenColor(control, selector === '#danger-item' ? '--ls-tone-danger-on-solid' : '--ls-on-primary'));
    }
    for (const selector of ['#legacy .btn-primary:not(:disabled)', '#legacy .btn-secondary:not(:disabled)']) {
      const control = page.locator(selector); await control.hover(); await page.mouse.down();
      try { await assertReadable(control, `${selector} pressed`); } finally { await page.mouse.up(); }
    }
    await assertReadable(page.locator('#legacy .loading'), 'legacy loading text stays visible');
    await assertReadable(page.locator('#legacy .btn-secondary:disabled'), 'legacy disabled text stays visible');
    for (const tone of ['dark', 'light']) {
      for (const selector of ['button', 'input'])
        await assertReadable(page.locator(`.scene[data-lq-tone="${tone}"] ${selector}`), `${tone} scene ${selector} on ${appearance} root`);
    }
  });
}

test('LQ real login submitting preserves label, pair and spinner until retry', async ({ page }) => {
  await mount(page, 'teal', 'dark');
  const control = page.locator('#native [data-case="button-prominent-md"] > button');
  const before = await assertReadable(control, 'login ready');
  await control.evaluate(async node => {
    const { setLoginSubmitting } = await import(/* @vite-ignore */ '/static/js/login_scene.js');
    setLoginSubmitting(node, true);
  });
  await expect(control).toBeDisabled();
  await expect(control.locator('[data-login-spinner]')).toBeVisible();
  const during = await assertReadable(control, 'login submitting');
  expect(during.text).toBe(before.text); expect(during.color).toBe(before.color); expect(during.background).toBe(before.background);
  await control.evaluate(async node => {
    const { setLoginSubmitting } = await import(/* @vite-ignore */ '/static/js/login_scene.js');
    setLoginSubmitting(node, false);
  });
  await expect(control).toBeEnabled();
  await expect(control.locator('[data-login-spinner]')).toHaveCount(0);
});

test('LQ touch holds and forced-colour keyboard states keep a readable pair', async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  try {
    const page = await context.newPage();
    await mount(page, 'sky', 'dark');
    const session = await context.newCDPSession(page);
    for (const variant of ['prominent', 'glass']) {
      const control = page.locator(`#native [data-case="button-${variant}-md"] > button`);
      await control.scrollIntoViewIfNeeded();
      await control.evaluate(node => node.addEventListener('pointerdown', () => node.setAttribute('data-touch-seen', 'true'), { once: true }));
      const box = (await control.boundingBox())!;
      await session.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: box.x + box.width / 2, y: box.y + box.height / 2 }] });
      try {
        await expect(control).toHaveAttribute('data-touch-seen', 'true');
        // CDP touchStart delivers trusted pointer/touch events but does not set
        // :active even on a bare native button. Measure the actual held touch,
        // then separately exercise :active with this same coarse-pointer media.
        await assertReadable(control, `${variant} touch held`);
      } finally { await session.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] }); }
      await control.hover(); await page.mouse.down();
      try {
        const value = await assertReadable(control, `${variant} coarse-pointer active`);
        expect(value.active).toBe(true);
      } finally { await page.mouse.up(); }
    }
    await page.emulateMedia({ forcedColors: 'active', reducedMotion: 'reduce' });
    const selected = page.locator('#selected-link');
    await assertReadable(selected, 'forced colours selected');
    const control = page.locator('#native [data-case="button-prominent-md"] > button');
    await control.focus(); await page.keyboard.down('Space');
    try { await assertReadable(control, 'forced colours held Space'); }
    finally { await page.keyboard.up('Space'); }
  } finally { await context.close(); }
});
