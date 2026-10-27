import { expect, test, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';

const manifest = JSON.parse(fs.readFileSync('static/assets/manifest.json', 'utf8'));
const root = { id: 1, name: '网络实验资料', summary: '课前阅读实验说明，完成网络拓扑设计、设备配置和连通性验证。文件夹收录本周实验材料、操作步骤与课堂讨论记录。', node_type: 'folder', parent_id: null, material_path: '网络实验资料', preview_mode: 'folder', document_kind: 'folder', can_manage: true, child_count: 2 };
const editable = { id: 2, name: '实验说明.txt', node_type: 'file', parent_id: 1, material_path: '网络实验资料/实验说明.txt', preview_mode: 'text', preview_content_url: '/api/materials/2/content', document_kind: 'text', file_ext: 'txt', is_text: true, editable: true, can_manage: true, file_size: 64 };
const readonly = { ...editable, id: 3, name: '共享资料.txt', can_manage: false, editable: false, download_allowed: false };

async function mount(page: Page, { appearance = 'light', glass = 'on' } = {}) {
  for (const source of ['js/materials_manage.js', 'js/materials_explorer.js', 'js/materials_library_cards.js', 'js/materials_menus.js', 'css/materials_library.css']) {
    expect(createHash('sha256').update(fs.readFileSync(`static/${source}`)).digest('hex')).toBe(manifest.sourceHashes[source]);
  }
  const markup = execFileSync('python', ['tests/e2e/scripts/render_materials_lq.py'], { encoding: 'utf8', env: { ...process.env, PYTHONIOENCODING: 'utf-8' } });
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  const requests: string[] = [];
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin !== 'https://materials.test') return route.abort();
    if (url.pathname.startsWith(`/static/assets/${manifest.revision}/`)) {
      const file = path.resolve(`.${url.pathname}`);
      if (!file.startsWith(path.resolve('static/assets') + path.sep)) throw Error('Unexpected asset');
      return route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body: fs.readFileSync(file) });
    }
    if (url.pathname.startsWith('/api/')) {
      requests.push(`${route.request().method()} ${url.pathname}`);
      if (url.pathname === '/api/materials/library') return route.fulfill({ json: { items: [root, readonly], filters: {}, stats: {}, facets: {} } });
      if (/\/api\/materials\/\d+\/tree$/.test(url.pathname)) return route.fulfill({ json: { tree: { ...root, children: [editable, readonly] }, selected_id: Number(url.pathname.split('/')[3]), stats: { folder_count: 1, file_count: 2 } } });
      if (/\/api\/materials\/\d+\/content$/.test(url.pathname)) {
        if (route.request().method() === 'PUT') return route.fulfill({ status: 409, json: { detail: '文件版本已更新，请保留草稿' } });
        return route.fulfill({ json: { content: '原始文档', encoding: 'utf-8', material: { ...editable, revision: 'r1', source_revision: 's1' } } });
      }
      if (/\/api\/materials\/[123]$/.test(url.pathname)) return route.fulfill({ json: { material: [root, editable, readonly][Number(url.pathname.split('/').at(-1)) - 1] } });
      if (url.pathname === '/api/materials/folders' && route.request().method() === 'POST') return route.fulfill({ status: 409, json: { detail: '目录已存在，请修改名称' } });
      return route.fulfill({ json: { folders: [], items: [], tasks: [], options: [], categories: [], summary: {} } });
    }
    if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh-CN" data-theme="lanshare" data-appearance="${appearance}" data-lq-glass="${glass}" data-lq-motion="off"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/static/${manifest.entries['css/tailwind-app.css']}"><style>.fixture-header{padding:16px;margin:12px}.fixture-main{padding:12px;max-width:1200px;margin:auto}.materials-header-actions{display:flex;gap:8px;flex-wrap:wrap}</style></head><body>${markup}</body></html>` });
    return route.fulfill({ status: 404, body: 'Only synthetic resources are served.' });
  });
  await page.goto('https://materials.test/');
  await expect(page.locator('.mcard')).toHaveCount(2);
  return { errors, requests };
}

async function openExplorer(page: Page) {
  await page.getByRole('button', { name: '浏览 网络实验资料', exact: true }).click();
  await expect(page.locator('[data-materials-explorer]')).toBeVisible();
  await expect(page.locator('[data-mx-open="2"]')).toBeVisible();
}

test('LQ materials header has one menu owner, keeps picker activation and failed create draft', async ({ page }) => {
  await page.addInitScript(() => {
    const click = HTMLInputElement.prototype.click;
    HTMLInputElement.prototype.click = function () {
      if (this.id === 'materials-file-input') (window as any).picks = [...((window as any).picks || []), navigator.userActivation.isActive];
      return click.call(this);
    };
  });
  const result = await mount(page);
  const upload = page.locator('#materials-upload-menu-btn');
  await expect(upload).toHaveAttribute('data-explain-text', '单个资料 / zip 自动解压');
  await upload.click();
  await expect(page.locator('#materials-upload-dropdown')).toHaveClass(/lq-menu/);
  const chooser = page.waitForEvent('filechooser');
  await page.locator('#materials-upload-direct-btn').click();
  await (await chooser).setFiles([]);
  await expect(page.locator('#materials-upload-dropdown')).toBeHidden();
  expect(await page.evaluate(() => (window as any).picks)).toEqual([true]);
  await page.locator('#materials-create-menu-btn').focus();
  await page.keyboard.press('ArrowDown');
  await expect(page.locator('#materials-create-folder-btn')).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.locator('#materials-create-node-modal')).toBeVisible();
  await page.locator('#materials-create-node-name').fill('保留的目录草稿');
  await page.locator('#materials-create-node-submit-btn').click();
  await expect(page.locator('#materials-create-node-status')).toContainText('目录已存在');
  await expect(page.locator('#materials-create-node-name')).toHaveValue('保留的目录草稿');
  expect(result.requests.filter(value => value === 'POST /api/materials/folders')).toHaveLength(1);
  await page.locator('#materials-create-node-modal').getByRole('button', { name: '取消', exact: true }).click();
  await expect(page.locator('#materials-create-node-modal')).toBeHidden();
  await upload.click();
  await expect(page.locator('#materials-upload-dropdown')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(upload).toBeFocused();
  expect(result.errors).toEqual([]);
});

test('LQ material card uses native keyboard actions and shared selected surface', async ({ page }) => {
  await mount(page);
  await page.getByRole('checkbox', { name: '选择 网络实验资料', exact: true }).check();
  await expect(page.locator('.mcard[data-id="1"] .mcard__paper')).toHaveAttribute('data-lq-selected', 'true');
  await expect(page.locator('[data-materials-explorer]')).toHaveCount(0);
  const open = page.getByRole('button', { name: '浏览 网络实验资料', exact: true });
  await expect(open).toHaveAttribute('data-lq-component', 'choice');
  await open.focus(); await page.keyboard.press('Enter');
  await expect(page.locator('[data-materials-explorer]')).toBeVisible();
});

test('LQ materials card menu preserves readonly permissions and restores focus repeatedly', async ({ page }) => {
  await mount(page);
  const more = page.locator('.mcard[data-id="3"] [data-action="card-menu"]');
  for (let i = 0; i < 4; i++) {
    await more.click();
    const menu = page.getByRole('menu', { name: '共享资料.txt的操作' });
    await expect(menu).toBeVisible();
    await expect(menu.getByRole('menuitem', { name: '删除', exact: true })).toHaveCount(0);
    await expect(menu.getByRole('menuitem', { name: '下载', exact: true })).toHaveCount(0);
    await page.keyboard.press('Escape');
    await expect(menu).toBeHidden(); await expect(more).toBeFocused();
  }
  await page.locator('#materials-refresh-btn').click();
  await expect(page.locator('.lq-menu').filter({ hasText: '在资源管理器中打开' })).toHaveCount(0);
});

test('LQ explorer context menu is a child layer and Escape leaves the viewer open', async ({ page }) => {
  const result = await mount(page); await openExplorer(page);
  await expect(page.locator('#materials-detail-modal')).toHaveAttribute('aria-hidden', 'false');
  const row = page.locator('[data-mx-item="2"]');
  await row.click({ button: 'right' });
  const menu = page.getByRole('menu', { name: '实验说明.txt的操作' });
  await expect(menu).toBeVisible();
  await expect(menu.getByRole('menuitem', { name: '删除', exact: true })).toBeVisible();
  await page.keyboard.press('End');
  await expect(menu.getByRole('menuitem', { name: '删除', exact: true })).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(page.locator('#materials-detail-modal')).toBeVisible();
  await expect(row.locator('[data-mx-menu-trigger]')).toBeFocused();
  await page.keyboard.press('Shift+F10'); await expect(menu).toBeVisible();
  await page.keyboard.press('Escape');
  expect(result.errors).toEqual([]);
});

test('LQ explorer arrows follow the focused native item and Enter opens that selection', async ({ page }) => {
  await mount(page); await openExplorer(page);
  const search = page.locator('[data-mx-search]');
  await search.evaluate((input: HTMLInputElement) => {
    input.dispatchEvent(new CompositionEvent('compositionstart', { bubbles: true }));
    input.value = '实验';
    input.dispatchEvent(new InputEvent('input', { bubbles: true, isComposing: true }));
  });
  await expect(page.locator('[data-mx-open]')).toHaveCount(2);
  await search.evaluate((input: HTMLInputElement) => {
    input.dispatchEvent(new CompositionEvent('compositionend', { bubbles: true }));
    input.dispatchEvent(new InputEvent('input', { bubbles: true, isComposing: false }));
  });
  await expect(page.locator('[data-mx-open]')).toHaveCount(1);
  await search.fill('');
  await expect(page.locator('[data-mx-open]')).toHaveCount(2);
  await page.locator('[data-mx-open="3"]').focus();
  await page.keyboard.press('ArrowDown');
  await expect(page.locator('[data-mx-open="2"]')).toBeFocused();
  await expect(page.locator('[data-mx-open="2"]')).toHaveAttribute('aria-pressed', 'true');
  await page.keyboard.press('Enter');
  await expect(page.locator('.mx__content-title h3')).toHaveText('实验说明.txt');
});

test('LQ explorer commits a delayed repeated Back only once and preserves Forward history', async ({ page }) => {
  const result = await mount(page); await openExplorer(page);
  await page.locator('[data-mx-open="2"]').click();
  await expect(page.locator('.mx__content-title h3')).toHaveText('实验说明.txt');
  await page.locator('[data-mx-row="3"]').click();
  await expect(page.locator('.mx__content-title h3')).toHaveText('共享资料.txt');
  let release!: () => void;
  const pending = new Promise<void>(resolve => { release = resolve; });
  let requests = 0;
  await page.route('**/api/materials/2', async route => {
    requests++;
    await pending;
    await route.fulfill({ json: { material: editable } });
  });
  const back = page.locator('[data-mx="back"]');
  await back.click();
  await expect.poll(() => requests).toBe(1);
  await back.click();
  release();
  await expect(page.locator('.mx__content-title h3')).toHaveText('实验说明.txt');
  expect(requests).toBe(1);
  await back.click();
  await expect(page.locator('.mx__content-title h3')).toHaveText('网络实验资料');
  await expect(back).toBeDisabled();
  await page.locator('[data-mx="forward"]').click();
  await expect(page.locator('.mx__content-title h3')).toHaveText('实验说明.txt');
  await page.locator('[data-mx="forward"]').click();
  await expect(page.locator('.mx__content-title h3')).toHaveText('共享资料.txt');
  await expect(page.locator('[data-mx="forward"]')).toBeDisabled();
  expect(result.errors).toEqual([]);
});

test('LQ explorer editor preserves failed save and cancels discard without changing navigation', async ({ page }) => {
  const result = await mount(page); await openExplorer(page);
  await page.locator('[data-mx-open="2"]').click();
  await page.locator('[data-mx="edit"]').click();
  const draft = page.locator('[data-editor-textarea]');
  await draft.fill('需要保留的编辑草稿');
  await page.locator('[data-editor-save]').click();
  await expect(page.getByText('文件版本已更新，请保留草稿').first()).toBeVisible();
  await expect(draft).toHaveValue('需要保留的编辑草稿');
  await page.locator('[data-mx="up"]').click();
  await expect(page.getByRole('dialog', { name: '放弃未保存的修改？' })).toBeVisible();
  await page.getByRole('button', { name: '取消', exact: true }).click();
  await expect(draft).toHaveValue('需要保留的编辑草稿');
  expect(result.requests.filter(value => value === 'PUT /api/materials/2/content')).toHaveLength(1);
  expect(result.errors).toEqual([]);
});

for (const scenario of [{ width: 1440, appearance: 'light', glass: 'on' }, { width: 360, appearance: 'dark', glass: 'off' }]) {
  test(`LQ materials ${scenario.width}px ${scenario.appearance}: bounded surfaces and touch menu`, async ({ browser }, info) => {
    const context = await browser.newContext({ viewport: { width: scenario.width, height: 820 }, hasTouch: scenario.width === 360, isMobile: scenario.width === 360, reducedMotion: 'reduce' });
    const page = await context.newPage(); const result = await mount(page, scenario);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(scenario.width);
    const headBox = await page.locator('.mcard[data-id="1"] .mcard__head').boundingBox();
    const titleBox = await page.locator('.mcard[data-id="1"] .mcard__title').boundingBox();
    expect(titleBox!.y - (headBox!.y + headBox!.height)).toBeLessThan(30);
    await page.screenshot({ path: info.outputPath('library.png'), fullPage: true });
    await openExplorer(page);
    await page.locator('[data-mx="view-grid"]').click();
    const more = page.locator('[data-mx-item="3"] [data-mx-menu-trigger]');
    if (scenario.width === 360) await more.tap(); else await more.click();
    const menu = page.getByRole('menu', { name: '共享资料.txt的操作' }); await expect(menu).toBeVisible();
    await expect(menu.getByRole('menuitem', { name: '删除', exact: true })).toHaveCount(0);
    const box = await menu.boundingBox(); expect(box!.x).toBeGreaterThanOrEqual(0); expect(box!.x + box!.width).toBeLessThanOrEqual(scenario.width);
    expect(box!.y).toBeGreaterThanOrEqual(0); expect(box!.y + box!.height).toBeLessThanOrEqual(820);
    const nestedBlur = await page.locator('[data-materials-explorer]').evaluate(node => [...node.querySelectorAll('*')].filter(el => getComputedStyle(el).backdropFilter !== 'none').map(el => el.className));
    expect(nestedBlur).toEqual([]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(scenario.width);
    await page.screenshot({ path: info.outputPath('explorer-menu.png') });
    await info.attach('resource-graph', { body: manifest.revision, contentType: 'text/plain' });
    expect(result.errors).toEqual([]); await context.close();
  });
}
