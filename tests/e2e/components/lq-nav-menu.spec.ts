import { test, expect, type Page, type Locator } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { settleEntranceAnimations } from '../fixtures/lq-s3';

type Case = { props: Record<string, any>; normalized?: any; html?: string; error?: string };
const python = process.env.LQ_TEST_PYTHON || (process.platform === 'win32' ? 'venv/Scripts/python.exe' : 'venv/bin/python');
const fixture: { cases: Case[]; invalid: Case[]; isolated: boolean } =
  JSON.parse(execFileSync(python, ['tests/e2e/scripts/render_lq_nav_menu.py'], { encoding: 'utf8' }));
const tokens = JSON.parse(fs.readFileSync('docs/lq-tokens.json', 'utf8'));
const palettes = ['indigo', 'sky', 'mint', 'violet', 'rose', 'teal'];
const entries = ['jinja', 'html', 'element'];
const screenshots = '.codex-temp/claude-s7-n-e2e';
const ASSETS = ['/static/css/tailwind-app.css', '/static/css/lq/components/menus.css', '/static/css/lq/components/nav-menu.css'];

async function mount(page: Page, entry = 'jinja', enhance = true, cases = fixture.cases) {
  await page.context().route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin !== 'https://lq-nav-menu.test') return route.abort();
    if (url.pathname === '/details') return route.fulfill({ contentType: 'text/html; charset=utf-8', body: '<!doctype html><html lang="zh"><meta charset="utf-8"><title>菜单链接目标</title><body><h1>链接目标</h1></body></html>' });
    if (url.pathname.startsWith('/static/js/') || ASSETS.includes(url.pathname)) {
      const file = path.resolve(`.${url.pathname}`);
      if (file.startsWith(path.resolve('static') + path.sep) && fs.existsSync(file)) return route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body: fs.readFileSync(file) });
    }
    if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh-CN" data-theme="lanshare" data-ui-palette="indigo" data-appearance="light" data-lq-glass="tinted"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>LQ nav menu</title>${ASSETS.map(href => `<link rel="stylesheet" href="${href}">`).join('')}<style>body{margin:0}main{padding:40px 24px}#bar{display:flex;gap:12px;align-items:center;overflow-x:auto}#note{margin-top:32px;display:block;width:240px}</style></head><body><main><h1>导航菜单按钮</h1><nav id="bar" aria-label="主导航"></nav><label for="note">备注</label><input id="note" name="note"><button id="after">后一个</button></main><script type="module">import * as navMenu from '/static/js/lq/nav-menu.js';import {getLayerSystem} from '/static/js/lq/layer.js';window.navMenu=navMenu;window.layer=getLayerSystem(document);window.actions=[];document.body.dataset.ready='true';</script></body></html>` });
    return route.abort();
  });
  await page.goto('https://lq-nav-menu.test/');
  await expect(page.locator('body')).toHaveAttribute('data-ready', 'true');
  await page.evaluate(({ cases, entry, enhance }) => {
    const api = (window as any).navMenu, bar = document.querySelector('#bar')!;
    for (const item of cases) {
      let element: Element | null;
      if (entry === 'element') element = api.createNavMenu(item.props);
      else { const t = document.createElement('template'); t.innerHTML = entry === 'jinja' ? item.html! : api.navMenuMarkup(item.props); element = t.content.firstElementChild; }
      bar.append(element!);
    }
    if (enhance) (window as any).lease = api.enhanceNavMenus(document, { onAction: (id: string) => (window as any).actions.push(id) });
  }, { cases, entry, enhance });
}

const trigger = (page: Page, id: string) => page.locator(`#${id}--lq-trigger`);
const panel = (page: Page, id: string) => page.locator(`#${id}`);

async function hover(page: Page, locator: Locator) {
  const box = (await locator.boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
}

test.describe('LQ nav menu', () => {
  test('real Jinja props, HTML and DOM factories produce exactly the same component', async ({ page }) => {
    expect(fixture.isolated).toBe(true);
    expect(fixture.cases.filter(item => item.error)).toEqual([]);
    await mount(page, 'jinja', false, []);
    const result = await page.evaluate(cases => {
      const api = (window as any).navMenu;
      const semantic = (n: Node): any => n.nodeType === Node.TEXT_NODE ? (n.textContent?.trim() ? { text: n.textContent.trim() } : null) : ({ tag: (n as Element).tagName.toLowerCase(), attrs: Object.fromEntries([...(n as Element).attributes].map(a => [a.name, a.value]).sort()), children: [...n.childNodes].map(semantic).filter(Boolean) });
      const parse = (markup: string) => { const t = document.createElement('template'); t.innerHTML = markup; return semantic(t.content.firstElementChild!); };
      return cases.map(item => {
        const p = api.navMenuProps(item.props);
        return { id: item.props.id, props: { ...p, menu: undefined }, menuAttrs: p.menu.attrs,
          menuItems: p.menu.items.map((i: any) => [i.id, i.label, i.disabled, i.danger, i.group]),
          jinja: parse(item.html!), html: parse(api.navMenuMarkup(item.props)), element: semantic(api.createNavMenu(item.props)) };
      });
    }, fixture.cases);
    result.forEach((item, index) => {
      const normalized = fixture.cases[index].normalized;
      // `menu` is the delegated lq_menu product; the two sides model it with
      // different intermediate shapes, so its equality is asserted on the panel
      // identity, item list and rendered markup rather than the raw sub-object.
      expect(item.props, `${item.id} props`).toEqual({ ...normalized, menu: undefined });
      expect(item.menuAttrs, `${item.id} panel attrs`).toEqual(normalized.menu.attrs);
      expect(item.menuItems, `${item.id} panel items`).toEqual(normalized.menu.items.map((i: any) => [i.id, i.label, i.disabled, i.danger, i.group]));
      expect(item.html, `${item.id} HTML`).toEqual(item.jinja);
      expect(item.element, `${item.id} Element`).toEqual(item.jinja);
    });
  });

  test('invalid variant, tone, size, shape, align, identity and items reject at every entrance', async ({ page }) => {
    expect(fixture.invalid.every(item => item.error === 'ValueError')).toBe(true);
    await mount(page, 'jinja', false, []);
    const rejected = await page.evaluate(cases => cases.map(item => {
      const api = (window as any).navMenu;
      return ['navMenuProps', 'navMenuMarkup', 'createNavMenu'].map(method => {
        try { api[method](item.props); return false; } catch (error) { return error instanceof TypeError; }
      });
    }), fixture.invalid);
    rejected.forEach((value, index) => expect(value, JSON.stringify(fixture.invalid[index].props)).toEqual([true, true, true]));
  });

  for (const entry of entries) {
    test(`${entry}: hover opens after the intent delay, survives the trip to the panel and closes on leave`, async ({ page }) => {
      await mount(page, entry);
      const study = trigger(page, 'nav-study');
      await hover(page, study);
      await expect(panel(page, 'nav-study')).toBeVisible();
      await expect(study).toHaveAttribute('aria-expanded', 'true');
      const box = (await panel(page, 'nav-study').boundingBox())!;
      const start = (await study.boundingBox())!;
      // Cross the gap the way a reader does: leave the trigger, land on the panel.
      await page.mouse.move(start.x + start.width / 2, start.y + start.height + 2);
      await page.mouse.move(box.x + box.width / 2, box.y + 8);
      await page.waitForTimeout(500);
      await expect(panel(page, 'nav-study')).toBeVisible();
      await page.mouse.move(box.x + box.width / 2, box.y + box.height - 8);
      await page.waitForTimeout(400);
      await expect(panel(page, 'nav-study')).toBeVisible();
      await page.mouse.move(4, 4);
      await expect(panel(page, 'nav-study')).toBeHidden();
      await expect(study).toHaveAttribute('aria-expanded', 'false');
    });

    test(`${entry}: keyboard opens, walks and closes back onto the trigger`, async ({ page }) => {
      await mount(page, entry);
      await trigger(page, 'nav-study').focus();
      await page.keyboard.press('Enter');
      await expect(page.getByRole('menuitem', { name: '我的作业' })).toBeFocused();
      await page.keyboard.press('Escape');
      await expect(panel(page, 'nav-study')).toBeHidden();
      await expect(trigger(page, 'nav-study')).toBeFocused();
      await page.keyboard.press('ArrowDown');
      await expect(page.getByRole('menuitem', { name: '我的作业' })).toBeFocused();
      await page.keyboard.press('ArrowDown');
      await expect(page.getByRole('menuitem', { name: '暂不可用' })).toBeFocused();
      await page.keyboard.press('Enter');
      expect(await page.evaluate(() => (window as any).actions)).toEqual([]);
      await page.keyboard.press('Escape');
      await expect(panel(page, 'nav-study')).toBeHidden();
      await expect(trigger(page, 'nav-study')).toBeFocused();
    });
  }

  test('only the hovered trigger reacts; siblings keep their resting appearance', async ({ page }) => {
    await mount(page);
    const resting = await page.locator('.lq-nav-menu__trigger').evaluateAll(nodes => nodes.map(node => getComputedStyle(node).boxShadow));
    await hover(page, trigger(page, 'nav-study'));
    await expect(panel(page, 'nav-study')).toBeVisible();
    const hovered = await page.locator('.lq-nav-menu__trigger').evaluateAll(nodes => nodes.map(node => getComputedStyle(node).boxShadow));
    const index = await page.locator('.lq-nav-menu__trigger').evaluateAll(nodes => nodes.findIndex(node => node.id === 'nav-study--lq-trigger'));
    expect(hovered[index]).not.toBe(resting[index]);
    expect(hovered[index]).not.toBe('none');
    hovered.forEach((value, at) => { if (at !== index) expect(value, `sibling ${at}`).toBe(resting[at]); });
    expect(await page.locator('.lq-nav-menu__trigger').evaluateAll(nodes => nodes.map(node => node.getAttribute('aria-expanded'))))
      .toEqual(fixture.cases.map(item => (item.props.id === 'nav-study' ? 'true' : 'false')));
  });

  test('hover never steals focus, while click and keyboard still place it on the first item', async ({ page }) => {
    await mount(page);
    await page.locator('#note').fill('正在输入');
    await hover(page, trigger(page, 'nav-study'));
    await expect(panel(page, 'nav-study')).toBeVisible();
    await expect(page.locator('#note')).toBeFocused();
    await page.mouse.move(4, 4);
    await expect(panel(page, 'nav-study')).toBeHidden();
    await expect(page.locator('#note')).toBeFocused();
    await trigger(page, 'nav-study').click();
    await expect(page.getByRole('menuitem', { name: '我的作业' })).toBeFocused();
  });

  test('the caret rotates only while expanded and no nav-menu node adds a blur host', async ({ page }) => {
    await mount(page);
    const caret = page.locator('#nav-study--lq-trigger .lq-nav-menu__caret');
    expect(await caret.evaluate(node => getComputedStyle(node).transform)).toBe('none');
    await trigger(page, 'nav-study').click();
    await expect(panel(page, 'nav-study')).toBeVisible();
    await expect.poll(() => caret.evaluate(node => getComputedStyle(node).transform)).toBe('matrix(-1, 0, 0, -1, 0, 0)');
    expect(await page.locator('.lq-nav-menu, .lq-nav-menu__trigger, .lq-nav-menu__caret').evaluateAll(nodes => [...new Set(nodes.map(node => getComputedStyle(node).backdropFilter))])).toEqual(['none']);
    await page.keyboard.press('Escape');
    await expect.poll(() => caret.evaluate(node => getComputedStyle(node).transform)).toBe('none');
  });

  test('reduced motion removes the caret transition and the hover delays', async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await mount(page);
    expect(await page.locator('#nav-study--lq-trigger .lq-nav-menu__caret').evaluate(node => parseFloat(getComputedStyle(node).transitionDuration))).toBeLessThanOrEqual(0.001);
    await hover(page, trigger(page, 'nav-study'));
    await expect(panel(page, 'nav-study')).toBeVisible({ timeout: 200 });
    expect(await page.locator('.lq-menu').first().evaluate(node => parseFloat(getComputedStyle(node).transitionDuration))).toBeLessThanOrEqual(0.001);
  });

  test('custom hover delays are honoured and disposal releases the binding', async ({ page }) => {
    await mount(page, 'jinja', false);
    await page.evaluate(() => { (window as any).lease = (window as any).navMenu.enhanceNavMenus(document, { hoverOpenDelay: 600, hoverCloseDelay: 10 }); });
    await hover(page, trigger(page, 'nav-study'));
    await page.waitForTimeout(300);
    await expect(panel(page, 'nav-study')).toBeHidden();
    await expect(panel(page, 'nav-study')).toBeVisible({ timeout: 2000 });
    await page.mouse.move(4, 4);
    await expect(panel(page, 'nav-study')).toBeHidden();
    await page.evaluate(() => (window as any).lease.dispose());
    await hover(page, trigger(page, 'nav-study'));
    await page.waitForTimeout(500);
    await expect(panel(page, 'nav-study')).toBeHidden();
    expect(await page.evaluate(() => (window as any).layer.top())).toBeNull();
    expect(await page.evaluate(() => [...document.querySelectorAll('.lq-nav-menu')].every(host => host.querySelector('.lq-menu') !== null))).toBe(true);
    expect(await page.evaluate(() => { try { (window as any).navMenu.enhanceNavMenus(document, { hoverOpenDelay: -1 }); return false; } catch (error) { return error instanceof TypeError; } })).toBe(true);
  });

  test('duplicate enhancement is reference counted and shares one binding per host', async ({ page }) => {
    await mount(page);
    await page.evaluate(async () => {
      const duplicate = await import('/static/js/lq/nav-menu.js?duplicate');
      (window as any).second = duplicate.enhanceNavMenus(document);
      (window as any).lease.dispose();
    });
    // One lease released; the second still owns the same binding.
    await trigger(page, 'nav-study').click();
    await expect(panel(page, 'nav-study')).toHaveAttribute('data-lq-layer-state', 'open');
    await page.keyboard.press('Escape');
    await expect(panel(page, 'nav-study')).toBeHidden();
    await page.evaluate(() => (window as any).second.dispose());
    await hover(page, trigger(page, 'nav-study'));
    await page.waitForTimeout(500);
    await expect(panel(page, 'nav-study')).toBeHidden();
    expect(await page.evaluate(() => document.querySelectorAll('.lq-menu').length)).toBe(fixture.cases.length);
  });

  test('a coarse pointer never opens on hover and still opens on tap', async ({ browser }) => {
    const context = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
    try {
      const page = await context.newPage();
      await mount(page);
      expect(await page.evaluate(() => matchMedia('(hover: hover) and (pointer: fine)').matches)).toBe(false);
      await page.locator('#nav-default--lq-trigger').hover();
      await page.waitForTimeout(500);
      await expect(page.locator('#nav-default')).toBeHidden();
      await page.locator('#nav-default--lq-trigger').tap();
      await expect(page.locator('#nav-default')).toHaveAttribute('data-lq-layer-state', 'open');
      for (const height of await page.getByRole('menuitem').evaluateAll(nodes => nodes.map(node => node.getBoundingClientRect().height))) expect(height).toBeGreaterThanOrEqual(44);
    } finally { await context.close(); }
  });

  for (const palette of palettes) for (const appearance of ['light', 'dark']) {
    test(`axe ${palette}/${appearance} with an open nav menu at desktop and mobile widths`, async ({ page }) => {
      expect(createHash('sha256').update(fs.readFileSync('static/css/lq/tokens.css')).digest('hex')).toBe(tokens.source_sha256);
      await mount(page);
      await page.evaluate(({ palette, appearance }) => { document.documentElement.dataset.uiPalette = palette; document.documentElement.dataset.appearance = appearance; }, { palette, appearance });
      for (const width of [1440, 390]) {
        await page.setViewportSize({ width, height: 980 });
        const channels = await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--ls-primary').trim());
        expect(channels.replace(/\s+/g, ' ')).toBe(tokens.themes[palette][appearance]['--ls-primary']);
        await settleEntranceAnimations(page);
        const closed = await new AxeBuilder({ page }).analyze();
        expect(closed.violations.map(v => ({ id: v.id, impact: v.impact, nodes: v.nodes.map(n => ({ target: n.target, reason: n.failureSummary })) })), `closed ${palette}/${appearance}/${width}`).toEqual([]);
        await trigger(page, 'nav-study').click();
        await expect(panel(page, 'nav-study')).toHaveAttribute('data-lq-layer-state', 'open');
        await settleEntranceAnimations(page);
        const results = await new AxeBuilder({ page }).analyze();
        expect(results.violations.filter(v => ['serious', 'critical'].includes(v.impact || ''))
          .map(v => ({ id: v.id, nodes: v.nodes.map(n => ({ target: n.target, reason: n.failureSummary })) })), `${palette}/${appearance}/${width}`).toEqual([]);
        // The only remaining finding is the shared layer portal host sitting
        // outside this bare fixture's landmarks; it belongs to LQ.layer, not to
        // the nav menu, and every layer-opening component spec scopes axe the
        // same way (lq-menu-tooltip, lq-dialogs, lq-toast, lq-selection).
        expect(results.violations.map(v => ({ id: v.id, targets: v.nodes.map(n => n.target) })), `${palette}/${appearance}/${width} residual`)
          .toEqual(results.violations.length ? [{ id: 'region', targets: [['#lq-layers']] }] : []);
        if (palette === 'teal') {
          fs.mkdirSync(screenshots, { recursive: true });
          await page.screenshot({ path: `${screenshots}/${appearance}-open-${width}.png`, fullPage: true });
        }
        await page.keyboard.press('Escape');
        await expect(panel(page, 'nav-study')).toBeHidden();
      }
    });
  }
});
