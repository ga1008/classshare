import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';

const python = process.env.LQ_TEST_PYTHON || (process.platform === 'win32' ? 'venv/Scripts/python.exe' : 'venv/bin/python');
const fixture = JSON.parse(execFileSync(python, ['tests/e2e/scripts/render_manage_lq_pilot.py'], { encoding: 'utf8' }));

async function mount(page: Page, { script = true, brokenStorage = false, header = '', inlineFallback = false } = {}) {
  expect(fixture.isolated).toBe(true);
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin !== 'https://manage-lq.test') return route.abort();
    if (url.pathname.startsWith('/static/js/') || url.pathname.startsWith('/static/css/')) {
      const file = path.resolve(`.${url.pathname}`);
      if (file.startsWith(path.resolve('static') + path.sep) && fs.existsSync(file)) {
        const body = url.pathname === '/static/css/tailwind-app.css' && process.env.LQ_MANAGE_SOURCE_CSS === '1'
          ? Buffer.concat([fs.readFileSync(file), Buffer.from('\n'), fs.readFileSync('static/css/lq/manage-shell.css'), Buffer.from('\n'), fs.readFileSync('static/css/lq/manage-pilot.css')]) : fs.readFileSync(file);
        return route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body });
      }
    }
    if (url.pathname === '/frame') return route.fulfill({ contentType: 'text/html', body: '<!doctype html><html><body><input id="frame-draft" aria-label="预览草稿" value="frame原值"><script>window.identity={};parent.postMessage("frame-ready",location.origin)</script></body></html>' });
    if (url.pathname === '/') {
      const setup = `<script>window.frameLoads=0;window.addEventListener('message',e=>{if(e.origin===location.origin&&e.data==='frame-ready')window.frameLoads++});${brokenStorage ? "Object.defineProperty(window,'localStorage',{get(){throw Error('denied')}});" : ''}</script>`;
      const behavior = `<script type="module">import {initManageLqPilot} from '/static/js/manage_lq_pilot.js';window.mountPilot=initManageLqPilot;window.pilot=initManageLqPilot();window.originals=[document.querySelector('#retained-input'),document.querySelector('#retained-frame'),document.querySelector('#action-form')];window.clicks=0;window.submits=[];document.addEventListener('click',e=>{if(e.target.closest('#business-open,#materials-create-file-btn')){if(e.target.closest('#materials-create-file-btn'))e.stopPropagation();window.clicks++;document.querySelector('#business-modal').showModal();document.querySelector('#business-focus').focus();}if(e.target.closest('#business-close'))document.querySelector('#business-modal').close();if(e.target.closest('#materials-create-menu-btn')){const d=document.querySelector('#materials-create-dropdown');d.hidden=!d.hidden;e.target.setAttribute('aria-expanded',String(!d.hidden));}},true);document.querySelector('#action-form').addEventListener('submit',e=>{e.preventDefault();window.submits.push([...new FormData(e.target,e.submitter).entries()]);});document.body.dataset.ready='true';</script>`;
      const enhanced = header ? `<script type="module">import {initManageLqPilot} from '/static/js/manage_lq_pilot.js';window.pilot=initManageLqPilot();document.body.dataset.ready='true';</script>` : behavior;
      const html = (header ? fixture.headers[header] : fixture.html).replace('<html', inlineFallback ? '<html data-lq-shell-fallback="inline"' : '<html');
      return route.fulfill({ contentType: 'text/html', body: html.replace('</head>', `<link rel="stylesheet" href="/static/css/tailwind-app.css"><link rel="stylesheet" href="/static/css/user_ui_preferences.css">${setup}</head>`).replace('</body>', (script ? enhanced : '') + '</body>') });
    }
    return route.fulfill({ status: 404, body: 'local fixture only' });
  });
  await page.goto('https://manage-lq.test/');
  if (script) await expect(page.locator('body')).toHaveAttribute('data-ready', 'true');
  if (!header) await expect(page.frameLocator('#retained-frame').locator('#frame-draft')).toBeAttached();
}

const more = (page: Page) => page.locator('#manage-pilot-topbar > [data-lq-pane-open="actions"]');
const pane = (page: Page) => page.locator('#manage-pilot-topbar--lq-actions');

test.describe('LQ manage pilot presentation adapter', () => {
  test.describe('real legacy stylesheet mobile navigation hit area', () => {
    test.use({ hasTouch: true });
    for (const width of [320, 390, 768]) test(`native nav tap survives topbar stacking at ${width}`, async ({ page }, info) => {
      await page.setViewportSize({ width: 1440, height: 900 }); await mount(page, { header: 'courses' });
      await page.setViewportSize({ width, height: 900 });
      await page.evaluate(async () => { document.documentElement.dataset.lqGlass = 'tinted'; document.documentElement.dataset.lqTier = 'A'; await document.fonts.ready; await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))); });
      const trigger = page.locator('[data-lq-manage-sidebar] > [data-lq-pane-open="nav"]');
      const hit = await trigger.evaluate(node => {
        const rect = node.getBoundingClientRect(), target = document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2);
        return { matches: target === node || node.contains(target), target: target?.outerHTML.slice(0, 600), chain: [node, node.parentElement!, document.querySelector('.manage-main')!, document.querySelector('#manage-pilot-topbar')!].map(el => { const s = getComputedStyle(el); return { tag: el.tagName, id: el.id, rect: el.getBoundingClientRect().toJSON(), position: s.position, zIndex: s.zIndex, transform: s.transform, filter: s.filter, backdropFilter: s.backdropFilter, isolation: s.isolation }; }) };
      });
      await info.attach('native-nav-hit', { body: JSON.stringify(hit, null, 2), contentType: 'application/json' });
      expect(hit.matches, JSON.stringify(hit)).toBe(true);
      await trigger.tap(); await expect(page.locator('#manageNavSearch')).toBeVisible();
      expect(await page.locator('#manage-pilot-nav').evaluate(node => node.matches(':modal'))).toBe(true);
      await page.locator('#manageNavSearch').fill('不存在的菜单'); await expect(page.locator('#manageNavEmpty')).toBeVisible();
      await page.locator('#manage-pilot-nav button[data-lq-pane-close]').tap();
      await expect(page.locator('#manage-pilot-nav')).toBeHidden();
      await more(page).tap(); await expect(pane(page)).toBeVisible();
      await pane(page).getByRole('button', { name: '关闭更多操作' }).tap();
      await trigger.tap(); await expect(page.locator('#manageNavSearch')).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
    });
  });
  for (const width of [390, 1024, 1440]) test(`unsupported render-blocking keeps SSR inline geometry and one searchable owner at ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 }); await mount(page, { script: false, inlineFallback: true });
    await page.evaluate(async () => {
      await document.fonts.ready;
      await Promise.allSettled(document.getAnimations().filter(animation => Number.isFinite(animation.effect?.getComputedTiming().endTime) && !['idle', 'finished'].includes(animation.playState)).map(animation => animation.finished));
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    });
    const boxes = () => page.locator('#sidebar,#manage-pilot-topbar,.manage-content').evaluateAll(nodes => nodes.map(node => {
      const r = node.getBoundingClientRect(); return [r.x + scrollX, r.y + scrollY, r.width, r.height];
    }));
    const before = await boxes();
    // Seed native draft state without a user scroll/focus changing the sticky
    // header before the specifically measured SSR-to-enhancer transition.
    await page.evaluate(() => { const w = window as any; (document.querySelector('#retained-input') as HTMLInputElement).value = 'fallback草稿'; w.fallbackNodes = [document.querySelector('#retained-input'), document.querySelector('#retained-frame'), document.querySelector('#action-form')]; localStorage.setItem('lanshare:manage-sidebar-collapsed', '1'); });
    await page.evaluate(async () => { const moduleUrl = '/static/js/manage_lq_pilot.js'; const module = await import(moduleUrl); const w = window as any; w.pilot = module.initManageLqPilot(); if (w.pilot !== module.initManageLqPilot()) throw Error('duplicate owner'); });
    await expect(page.locator('#manage-pilot-topbar')).toHaveAttribute('data-lq-enhanced', 'true');
    const after = await boxes();
    for (let i = 0; i < before.length; i++) for (let j = 0; j < 4; j++) expect(Math.abs(after[i][j] - before[i][j]), `surface ${i} axis ${j}`).toBeLessThanOrEqual(1);
    await expect(page.locator('#manage-pilot-nav')).toHaveAttribute('data-lq-pane-mode', 'inline');
    await expect(pane(page)).toHaveAttribute('data-lq-pane-mode', 'inline');
    await expect(more(page)).toBeHidden(); await expect(page.locator('.mobile-toggle')).toBeHidden();
    await expect(page.locator('#sidebarCollapseBtn')).toBeHidden(); await expect(page.locator('#manageNavSearch')).toBeVisible();
    await page.locator('#manageNavSearch').fill('不存在的菜单'); await expect(page.locator('#manageNavEmpty')).toBeVisible();
    await page.locator('#manageNavSearch').press('Escape'); await expect(page.locator('#manageNavSearch')).toHaveValue('');
    await page.locator('[data-lq-nav-group="teaching"] > summary').click(); await expect(page.locator('[data-lq-nav-group="teaching"]')).toHaveAttribute('open', '');
    await expect(page.locator('#retained-input')).toHaveValue('fallback草稿'); await expect(page.locator('#locked-action')).toBeDisabled();
    expect(await page.evaluate(() => { const w = window as any; return w.fallbackNodes.every((node: Element, i: number) => node === document.querySelector(['#retained-input', '#retained-frame', '#action-form'][i])) && w.frameLoads === 1 && !document.querySelector(':modal'); })).toBe(true);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
    await page.evaluate(() => (window as any).pilot.destroy()); await expect(page.locator('#retained-input')).toHaveValue('fallback草稿');
    await expect(page.locator('#manageNavSearch')).toBeVisible(); await expect(pane(page)).toBeVisible();
  });
  test('eight real SSR header-action blocks fit the desktop height contract and tablet width', async ({ page }) => {
    for (const width of [1440, 1024]) for (const header of Object.keys(fixture.headers)) {
      await page.unroute('**/*'); await page.setViewportSize({ width, height: 900 }); await mount(page, { header });
      expect(await page.evaluate(() => document.documentElement.scrollWidth), `${header}/${width} viewport`).toBe(width);
      expect((await page.locator('#manage-pilot-topbar').boundingBox())!.height, `${header}/${width} topbar`).toBeLessThanOrEqual(64);
      await page.locator('[data-ui-preferences-toggle]').click(); await expect(page.locator('[data-ui-preferences-panel]')).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
    }
  });
  test('mobile business action releases only its pane before the original one click and keeps new modal focus', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 900 }); await mount(page);
    for (let i = 0; i < 3; i++) {
      await more(page).click(); await page.locator('#business-open').click();
      await expect(pane(page)).toBeHidden(); await expect(page.locator('#business-focus')).toBeFocused();
      await page.locator('#business-close').click();
    }
    expect(await page.evaluate(() => (window as any).clicks)).toBe(3);
    await more(page).click(); await page.locator('#materials-create-menu-btn').click();
    await expect(pane(page)).toBeVisible(); await expect(page.locator('#materials-create-dropdown')).toBeVisible();
    await page.locator('#materials-create-file-btn').click();
    await expect(pane(page)).toBeHidden(); await expect(page.locator('#business-focus')).toBeFocused();
    expect(await page.evaluate(() => (window as any).clicks)).toBe(4);
  });

  test('twenty mounts retain exact input/form/iframe nodes, values and disabled owner semantics', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 900 }); await mount(page);
    await more(page).click(); await page.locator('#retained-input').fill('保留二十轮草稿');
    await page.frameLocator('#retained-frame').locator('#frame-draft').fill('iframe草稿');
    await expect(page.locator('#locked-action')).toBeDisabled();
    for (let i = 0; i < 20; i++) {
      await page.evaluate(() => { const w = window as any; w.pilot.destroy(); w.pilot = w.mountPilot(); if (w.pilot !== w.mountPilot()) throw Error('duplicate owner'); });
      await more(page).click(); await expect(page.locator('#retained-input')).toHaveValue('保留二十轮草稿');
      await pane(page).getByRole('button', { name: '关闭更多操作' }).click(); await expect(pane(page)).toBeHidden();
    }
    await more(page).click(); await page.locator('#business-submit').click();
    expect(await page.evaluate(() => (window as any).submits)).toEqual([[['draft', '保留二十轮草稿'], ['intent', 'save']]]);
    expect(await page.evaluate(() => { const w = window as any; return [w.originals[0] === document.querySelector('#retained-input'), w.originals[1] === document.querySelector('#retained-frame'), w.originals[2] === document.querySelector('#action-form'), w.frameLoads]; })).toEqual([true, true, true, 1]);
    await expect(page.frameLocator('#retained-frame').locator('#frame-draft')).toHaveValue('iframe草稿');
  });

  test('sidebar search Escape clears first, then closes, and subsequent more still opens', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 900 }); await mount(page);
    await page.locator('.mobile-toggle').click(); await page.locator('#manageNavSearch').fill('不存在的菜单');
    await expect(page.locator('#manageNavEmpty')).toBeVisible(); await page.locator('#manageNavSearch').press('Escape');
    await expect(page.locator('#manageNavSearch')).toHaveValue(''); await expect(page.locator('#manage-pilot-nav')).toBeVisible();
    await page.locator('#manageNavSearch').press('Escape'); await expect(page.locator('#manage-pilot-nav')).toBeHidden();
    await more(page).click(); await expect(pane(page)).toBeVisible();
  });

  test('native SSR fallback and blocked storage leave controls usable without horizontal overflow', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 900 }); await mount(page, { script: false });
    await expect(page.locator('#manageNavSearch')).toBeVisible(); await expect(page.locator('#retained-input')).toBeVisible();
    await page.locator('#retained-input').fill('无JS草稿'); await expect(page.locator('#retained-input')).toHaveValue('无JS草稿');
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(390);
    await page.unroute('**/*'); await mount(page, { brokenStorage: true });
    await page.locator('.mobile-toggle').click(); await expect(page.locator('#manageNavSearch')).toBeVisible();
  });

  for (const width of [768, 769, 1023, 1024]) test(`S2 boundary is explicit at ${width}, never inherited from old 768 controller`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 }); await mount(page);
    if (width < 1024) { await expect(more(page)).toBeVisible(); await expect(page.locator('#manage-pilot-nav')).toBeHidden(); }
    else { await expect(more(page)).toBeHidden(); await expect(page.locator('#manage-pilot-nav')).toBeVisible(); await page.keyboard.press('Control+k'); await expect(page.locator('#manageNavSearch')).toBeVisible(); await expect(page.locator('#manageNavSearch')).toBeFocused(); }
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
  });
});
