import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';

// Render the actual AI widget + authored navbar/dock, without app/DB imports.
const python = process.env.LQ_TEST_PYTHON || (process.platform === 'win32' ? 'venv/Scripts/python.exe' : 'venv/bin/python');
const fixture = JSON.parse(execFileSync(python, ['-c', String.raw`
import importlib.util, json, sys, types
from pathlib import Path
from jinja2 import Environment, FileSystemLoader, StrictUndefined
root=Path.cwd(); package=types.ModuleType('fab_fixture'); package.__path__=[str(root/'classroom_app')]; sys.modules[package.__name__]=package
def load(name):
    spec=importlib.util.spec_from_file_location(package.__name__+'.'+name,root/'classroom_app'/(name+'.py'))
    module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module);return module
components,menus,navmenus,shells=[load(name) for name in ['lq_components','lq_menu_tooltip','lq_nav_menu','lq_shells']]
def props(kind,**values):
    if kind in shells.SHELL_KINDS:return shells.lq_shell_props(kind,**values)
    if kind in navmenus.NAV_MENU_KINDS:return navmenus.lq_nav_menu_kind_props(kind,**values)
    if kind in menus.MENU_TOOLTIP_KINDS:return menus.lq_menu_tooltip_props(kind,**values)
    return components.lq_props(kind,**values)
env=Environment(loader=FileSystemLoader(root/'templates'),autoescape=True,undefined=StrictUndefined)
env.globals.update(lq_props=props,lq_family_enabled=lambda _:True,asset_url=lambda name:'/static/'+name)
context=dict(user_info=dict(role='student',name='合成学生',nickname=''),ui_palette=dict(enabled=False),navbar_shell_id='navbar-topbar',navbar_shell_report=False,navbar_shell_title='学习台',navbar_shell_actions='',request=types.SimpleNamespace(url=types.SimpleNamespace(path='/dashboard'),query_params={}))
render=lambda name,**values:env.get_template(name).render(**context,**values)
result=dict(topbar=render('partials/lq_navbar_topbar.html'),dock=render('partials/lq_app_bottomnav.html'),widget=render('partials/ai_workspace_widget.html'),deferred=render('partials/ai_workspace_widget.html',ai_widget_deferred=True))
assert not any(name in sys.modules for name in ['app','core','classroom_app','sqlite3','psycopg','dotenv'])
sys.stdout.reconfigure(encoding='utf-8');print(json.dumps(result,ensure_ascii=False))
`], { encoding: 'utf8' }));

async function mount(page: Page, { dock = true, reading = false, deferred = false, appearance = 'dark' } = {}) {
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  await page.route('https://ai-fab.test/**', route => {
    const url = new URL(route.request().url());
    if (url.pathname.startsWith('/static/')) {
      const file = path.resolve('.' + url.pathname);
      if (!file.startsWith(path.resolve('static') + path.sep) || !fs.existsSync(file)) throw Error(`Unexpected fixture asset ${file}`);
      return route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body: fs.readFileSync(file) });
    }
    return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh-CN" data-appearance="${appearance}" data-theme="lanshare" data-ui-palette="indigo"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/static/css/tailwind-app.css"><link rel="stylesheet" href="/static/css/ai_workspace.css"></head><body class="${reading ? 'ls-render-shell-page' : ''}">${fixture.topbar}<main data-lq-navbar-content><h1>学习工作台</h1><textarea aria-label="页面草稿">保留原草稿</textarea><div style="min-height:1000px"></div></main>${dock ? fixture.dock : ''}${deferred ? fixture.deferred : fixture.widget}<script type="module">
      import {initNavbarLq} from '/static/js/navbar_lq.js';
      window.handle=initNavbarLq();window.clicked=[];
      document.querySelector('[data-navbar-dock]')?.addEventListener('click',event=>{const link=event.target.closest('a');if(link){event.preventDefault();window.clicked.push(link.getAttribute('href'));}});
      document.getElementById('ai-chat-fab').addEventListener('click',()=>window.clicked.push('ai'));
      window.ready=true;
    </script></body></html>` });
  });
  await page.goto('https://ai-fab.test/'); await page.waitForFunction(() => (window as any).ready); return errors;
}

for (const appearance of ['light', 'dark']) test(`AI FAB leaves all student dock actions clickable: ${appearance}`, async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 390, height: 900 }, hasTouch: true, isMobile: true });
  try {
    const page = await context.newPage(), errors = await mount(page, { appearance });
    const fab = page.locator('#ai-chat-fab'), dock = page.locator('[data-navbar-dock]');
    await expect(fab).toHaveAttribute('data-lq-shell', 'fab'); await expect(fab).toHaveAttribute('data-lq-fab-slot', '0');
    const assertAboveDock = async () => {
      const fabBox = (await fab.boundingBox())!, dockBox = (await dock.boundingBox())!;
      expect(fabBox.y + fabBox.height).toBeLessThan(dockBox.y); expect(fabBox.width).toBeGreaterThanOrEqual(44); expect(fabBox.height).toBeGreaterThanOrEqual(44);
      expect(await page.evaluate(() => document.documentElement.style.getPropertyValue('--lq-dock-h'))).toBe(await page.locator('main').evaluate(el => (el as HTMLElement).style.getPropertyValue('--lq-dock-h')));
    };
    await assertAboveDock();
    fs.mkdirSync('.codex-temp/lq-ai-fab-dock', { recursive: true }); await page.screenshot({ path: `.codex-temp/lq-ai-fab-dock/${appearance}-390.png` });
    for (const label of ['首页', '消息', '学习路', '我的']) await dock.getByRole('link', { name: label, exact: true }).tap();
    await fab.tap(); expect(await page.evaluate(() => (window as any).clicked)).toEqual(['/dashboard', '/message-center', '/learning-path', '/profile', 'ai']);
    await expect(page.getByRole('textbox', { name: '页面草稿' })).toHaveValue('保留原草稿');
    // Responsive and larger type changes reuse the Dock's existing observer.
    await dock.evaluate(el => (el as HTMLElement).style.minHeight = '112px');
    await expect.poll(() => page.evaluate(() => parseFloat(document.documentElement.style.getPropertyValue('--lq-dock-h')))).toBeGreaterThanOrEqual(136);
    await assertAboveDock();
    await page.setViewportSize({ width: 1440, height: 900 });
    await expect.poll(() => fab.evaluate(el => getComputedStyle(el).bottom)).toBe('22px');
    await page.evaluate(() => (window as any).handle.destroy());
    expect(await page.evaluate(() => document.documentElement.style.getPropertyValue('--lq-dock-h'))).toBe('');
    expect(errors).toEqual([]);
  } finally { await context.close(); }
});

for (const reading of [false, true]) test(`AI FAB retains no-dock placement and deferred identity: ${reading ? 'reading' : 'standard'}`, async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 900 }); const errors = await mount(page, { dock: false, reading, deferred: true });
  const fab = page.locator('#ai-chat-fab'); await expect(fab).toBeDisabled(); await expect(fab).toHaveAttribute('aria-busy', 'true'); await expect(fab).toHaveAttribute('data-ai-deferred', '');
  await expect(fab).toHaveAttribute('aria-controls', 'ai-chat-modal'); await expect(fab.locator('svg')).toHaveCount(1);
  expect(await fab.evaluate(el => getComputedStyle(el).bottom)).toBe(reading ? '94px' : '12px');
  await page.setViewportSize({ width: 1440, height: 900 }); expect(await fab.evaluate(el => getComputedStyle(el).bottom)).toBe(reading ? '70px' : '22px');
  expect(await page.evaluate(() => (window as any).clicked)).toEqual([]); expect(errors).toEqual([]);
});

test('Dock floating reservation restores caller values without adding subscriptions or focus reads', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 900 }); await mount(page);
  const result = await page.evaluate(async () => {
    const w = window as any; w.handle.destroy();
    const { enhanceDock } = await import('/static/js/lq/shells.js');
    const root = document.querySelector('[data-navbar-dock]') as HTMLElement, contentRoot = document.querySelector('main')!, floatingRoot = document.documentElement;
    floatingRoot.style.setProperty('--lq-dock-h', '7px', 'important');
    const handle = enhanceDock(root, { contentRoot, floatingRoot });
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    let reads = 0; const original = root.getBoundingClientRect; root.getBoundingClientRect = function () { reads++; return original.call(this); };
    const input = document.querySelector('textarea')!;
    for (let index = 0; index < 20; index++) { input.focus(); input.blur(); }
    const focusReads = reads, subscribers = w[Symbol.for('lanshare.lq.viewport')].subscribers.size;
    handle.refresh(); const refreshReads = reads - focusReads;
    handle.destroy();
    return { focusReads, subscribers, refreshReads, original: floatingRoot.style.getPropertyValue('--lq-dock-h'), priority: floatingRoot.style.getPropertyPriority('--lq-dock-h'), cleaned: !w[Symbol.for('lanshare.lq.viewport')] };
  });
  expect(result).toEqual({ focusReads: 0, subscribers: 1, refreshReads: 1, original: '7px', priority: 'important', cleaned: true });
});
