import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

async function mount(page: Page, query = '') {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('https://workspace.test/**', route => {
    const url = new URL(route.request().url());
    if (url.pathname.startsWith('/static/')) {
      const file = path.resolve('.' + url.pathname);
      if (!file.startsWith(path.resolve('static') + path.sep) || !fs.existsSync(file)) throw new Error(`Unexpected static file ${file}`);
      return route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body: fs.readFileSync(file) });
    }
    return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html data-appearance="light" data-glass="tinted" data-glass-tier="A"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/static/css/ui-system.src.css"><link rel="stylesheet" href="/static/css/ai_workspace.css"><style>body{min-height:2400px}#page-area{width:360px;padding:24px}#page-text{user-select:text}#page-action{margin-bottom:16px}</style></head><body class="ls-page"><main id="page-area"><button id="page-action">页面按钮</button><p id="page-text">这段页面文字应当能够在助手打开时选中复制。</p></main><button id="ai-chat-fab" class="ai-workspace-fab" type="button">打开助手</button><div id="ai-chat-modal" class="ai-workspace-modal" aria-hidden="true" style="display:none"><div class="ai-chat-container ai-workspace-container" role="dialog"><div class="ai-chat-header ai-workspace-header"><div class="ai-workspace-title"><h3>AI 助手</h3></div><div class="header-buttons"><button id="ai-chat-btn-new" type="button">新对话</button><button id="ai-chat-btn-fullscreen" type="button">最大化</button><button id="ai-chat-btn-close" type="button">关闭</button></div></div><section class="ai-workspace-panel is-active"><div class="ai-chat-messages" id="ai-chat-messages-box"><p>窗口内容</p></div><div class="ai-chat-input-area"><textarea aria-label="对话草稿"></textarea></div></section>${['top','right','bottom','left','top-left','top-right','bottom-left','bottom-right'].map(direction => `<div class="resizer resizer-${direction}"></div>`).join('')}</div></div><script type="module">
      import {createAssistantWindow} from '/static/js/ai_workspace_window.js';
      import {createWorkspaceState} from '/static/js/ai_workspace_state.js';
      window.createWorkspaceState=createWorkspaceState;
      const query=new URLSearchParams(location.search), owner=query.get('owner')||'teacher:21';
      if(query.has('corrupt'))sessionStorage.setItem('lanshare.aiWorkspace.v2.'+owner,'null');
      if(query.has('blocked-storage'))indexedDB.open=()=>{throw new DOMException('Storage unavailable','SecurityError');};
      window.stats={pageClicks:0,newClicks:0,opens:0,closes:0};
      const modal=document.getElementById('ai-chat-modal'),container=modal.firstElementChild,fab=document.getElementById('ai-chat-fab');
      window.state=createWorkspaceState(owner);
      window.api=createAssistantWindow({modal,container,fab,state,onOpen:({focus})=>{stats.opens++;if(focus)container.querySelector('textarea').focus({preventScroll:true});},onClose:()=>stats.closes++});
      document.getElementById('page-action').onclick=()=>stats.pageClicks++;
      document.getElementById('ai-chat-btn-new').onclick=()=>stats.newClicks++;
      api.restore();window.ready=true;
    </script></body></html>` });
  });
  await page.goto(`https://workspace.test/fixture${query}`);
  await page.waitForFunction(() => (window as any).ready);
  return errors;
}

async function openAt(page: Page, rect = { left: 500, top: 160, width: 460, height: 520 }) {
  await page.evaluate(rect => { const w = window as any; w.state.patch({ rect }); w.api.open(); }, rect);
  await expect(page.locator('.ai-workspace-container')).toBeVisible();
  await page.locator('.ai-workspace-container').evaluate(async node => { await Promise.all(node.getAnimations().map(animation => animation.finished.catch(() => {}))); });
}

async function box(page: Page) {
  return (await page.locator('.ai-workspace-container').boundingBox())!;
}

async function drag(page: Page, x: number, y: number, dx: number, dy: number) {
  await page.mouse.move(x, y); await page.mouse.down(); await page.mouse.move(x + dx, y + dy, { steps: 6 }); await page.mouse.up();
}

test('modeless assistant leaves outside controls, text selection and body scrolling usable', async ({ page }) => {
  const errors = await mount(page); await openAt(page);
  await expect(page.locator('.ai-workspace-container')).toHaveAttribute('aria-modal', 'false');
  await page.locator('#page-action').click(); expect(await page.evaluate(() => (window as any).stats.pageClicks)).toBe(1);
  await page.locator('#page-text').dblclick(); expect(await page.evaluate(() => getSelection()?.toString().length)).toBeGreaterThan(0);
  await expect(page.locator('#ai-chat-modal')).toHaveAttribute('aria-hidden', 'false');
  await page.mouse.move(150, 330); await page.mouse.wheel(0, 400);
  await expect.poll(() => page.evaluate(() => scrollY)).toBeGreaterThan(0);
  expect(await page.evaluate(() => getComputedStyle(document.body).overflow)).not.toBe('hidden');
  expect(await page.evaluate(() => document.body.inert)).toBe(false);
  expect(errors).toEqual([]);
});

test('header moves the window but its buttons never start a drag', async ({ page }) => {
  const errors = await mount(page); await openAt(page);
  const before = await box(page), title = (await page.locator('.ai-workspace-title').boundingBox())!;
  await drag(page, title.x + 20, title.y + 20, -90, 50);
  const moved = await box(page); expect(moved.x).toBeCloseTo(before.x - 90, 0); expect(moved.y).toBeCloseTo(before.y + 50, 0);
  const button = (await page.locator('#ai-chat-btn-new').boundingBox())!;
  await page.mouse.move(button.x + button.width / 2, button.y + button.height / 2); await page.mouse.down();
  await page.mouse.move(button.x + button.width / 2 + 30, button.y + button.height / 2 + 20); await page.mouse.up();
  expect(await box(page)).toEqual(moved);
  await page.locator('#ai-chat-btn-new').click(); expect(await page.evaluate(() => (window as any).stats.newClicks)).toBe(1);
  await expect(page.locator('.ai-workspace-container')).not.toHaveClass(/is-manipulating/); expect(errors).toEqual([]);
});

for (const direction of ['top','right','bottom','left','top-left','top-right','bottom-left','bottom-right']) {
  test(`window resize ${direction} moves only its intended edges`, async ({ page }) => {
    const errors = await mount(page); await openAt(page);
    const before = await box(page), handle = (await page.locator(`.resizer-${direction}`).boundingBox())!;
    const dx = direction.includes('left') ? -44 : direction.includes('right') ? 44 : 0;
    const dy = direction.includes('top') ? -36 : direction.includes('bottom') ? 36 : 0;
    await drag(page, handle.x + handle.width / 2, handle.y + handle.height / 2, dx, dy);
    const after = await box(page);
    expect(after.width).toBeCloseTo(before.width + Math.abs(dx), 0); expect(after.height).toBeCloseTo(before.height + Math.abs(dy), 0);
    expect(after.x).toBeCloseTo(before.x + Math.min(0, dx), 0); expect(after.y).toBeCloseTo(before.y + Math.min(0, dy), 0);
    expect(await page.evaluate(() => (window as any).state.value.rect)).toMatchObject({ left: after.x, top: after.y, width: after.width, height: after.height });
    expect(errors).toEqual([]);
  });
}

test('mobile viewport clamps restored geometry and maximization never locks the document', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 }); const errors = await mount(page);
  await openAt(page, { left: 1200, top: 1000, width: 900, height: 1000 });
  let rect = await box(page); expect(rect.x).toBeGreaterThanOrEqual(8); expect(rect.y).toBeGreaterThanOrEqual(8);
  expect(rect.x + rect.width).toBeLessThanOrEqual(382); expect(rect.y + rect.height).toBeLessThanOrEqual(836);
  await page.locator('#ai-chat-btn-fullscreen').click(); rect = await box(page);
  expect(rect).toMatchObject({ x: 8, y: 8, width: 374, height: 828 });
  expect(await page.evaluate(() => getComputedStyle(document.body).overflow)).not.toBe('hidden');
  await page.setViewportSize({ width: 844, height: 390 });
  await expect.poll(() => box(page)).toMatchObject({ x: 8, y: 8, width: 828, height: 374 });
  await page.locator('#ai-chat-btn-fullscreen').click(); rect = await box(page);
  expect(rect.y + rect.height).toBeLessThanOrEqual(382); expect(errors).toEqual([]);
});

test('close completes its animation and rapid reopen preserves a consistent window mode', async ({ page }) => {
  const errors = await mount(page); await openAt(page);
  await page.locator('#ai-chat-btn-close').click();
  await expect(page.locator('#ai-chat-modal')).toHaveAttribute('aria-hidden', 'true'); await expect(page.locator('#ai-chat-fab')).toBeVisible();
  await page.locator('#ai-chat-fab').click(); await page.locator('#ai-chat-btn-fullscreen').click();
  await page.evaluate(() => { const w = window as any; w.api.close(); w.api.open(); });
  await page.waitForTimeout(180);
  await expect(page.locator('#ai-chat-modal')).toHaveAttribute('aria-hidden', 'false');
  const mode = await page.locator('.ai-workspace-container').evaluate(node => ({ full: node.classList.contains('fullscreen'), pressed: node.querySelector('#ai-chat-btn-fullscreen')?.getAttribute('aria-pressed'), rect: node.getBoundingClientRect().toJSON() }));
  expect(mode.pressed).toBe(String(mode.full));
  if (mode.full) { expect(mode.rect.x).toBe(8); expect(mode.rect.width).toBe(1424); }
  else { expect(mode.rect.x).toBe(500); expect(mode.rect.width).toBe(460); }
  await expect(page.locator('.ai-workspace-container')).not.toHaveAttribute('inert', ''); expect(errors).toEqual([]);
});

test('same-account navigation restores open geometry, selected session, text draft and IndexedDB Files', async ({ page }) => {
  const errors = await mount(page); await openAt(page);
  await page.evaluate(async () => { const w = window as any; w.state.patch({ sessionUUID: 'fixture-session', draft: '待发送草稿' }); await w.state.saveFiles([new File(['截图内容'], '课堂.png', { type: 'image/png', lastModified: 1234 })]); });
  await page.goto('https://workspace.test/second'); await page.waitForFunction(() => (window as any).ready);
  const restored = await page.evaluate(async () => { const w = window as any, files = await w.state.loadFiles(); return { value: w.state.value, files: await Promise.all(files.map(async (file: File) => ({ name: file.name, type: file.type, modified: file.lastModified, text: await file.text(), isFile: file instanceof File }))) }; });
  expect(restored.value).toMatchObject({ open: true, rect: { left: 500, top: 160, width: 460, height: 520 }, sessionUUID: 'fixture-session', draft: '待发送草稿' });
  expect(restored.files).toEqual([{ name: '课堂.png', type: 'image/png', modified: 1234, text: '截图内容', isFile: true }]);
  await expect(page.locator('#ai-chat-modal')).toHaveAttribute('aria-hidden', 'false'); expect(errors).toEqual([]);
});

test('account change clears prior tab state and attachment drafts, including reused numeric IDs', async ({ page }) => {
  const errors = await mount(page); await openAt(page);
  await page.evaluate(async () => { const w = window as any; w.state.patch({ sessionUUID: 'teacher-private', draft: '教师私有草稿' }); await w.state.saveFiles([new File(['teacher secret'], 'secret.txt')]); });
  await page.goto('https://workspace.test/second?owner=student:21'); await page.waitForFunction(() => (window as any).ready);
  expect(await page.evaluate(async () => ({ value: (window as any).state.value, files: (await (window as any).state.loadFiles()).length }))).toEqual({ value: {}, files: 0 });
  await page.goto('https://workspace.test/third?owner=teacher:21'); await page.waitForFunction(() => (window as any).ready);
  expect(await page.evaluate(async () => ({ value: (window as any).state.value, files: (await (window as any).state.loadFiles()).length }))).toEqual({ value: {}, files: 0 });
  await expect(page.locator('#ai-chat-modal')).toHaveAttribute('aria-hidden', 'true'); expect(errors).toEqual([]);
});

test('corrupt stored metadata falls back to an operable closed window', async ({ page }) => {
  const errors = await mount(page, '?corrupt=1');
  await page.locator('#ai-chat-fab').click(); await expect(page.locator('#ai-chat-modal')).toHaveAttribute('aria-hidden', 'false');
  expect(await page.evaluate(() => (window as any).state.value.open)).toBe(true); expect(errors).toEqual([]);
});

test('restricted IndexedDB degrades to an operable window without persisted attachments', async ({ page }) => {
  const errors = await mount(page, '?blocked-storage=1');
  expect(await page.evaluate(async () => {
    const state = (window as any).state;
    await state.saveFiles([new File(['draft'], 'draft.txt')]);
    return state.loadFiles();
  })).toEqual([]);
  await page.locator('#ai-chat-fab').click(); await expect(page.locator('#ai-chat-modal')).toHaveAttribute('aria-hidden', 'false');
  expect(errors).toEqual([]);
});
