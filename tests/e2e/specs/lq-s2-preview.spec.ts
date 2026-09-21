import { expect, test } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import fs from 'node:fs';
import path from 'node:path';
import { loginTeacher, loginStudent, readFixture, expectHealthUsesRuntimeDb, type P03Fixture } from '../fixtures/p03';

type Fixture = P03Fixture & { uiV3Synthetic: boolean };
const fixture = () => readFixture() as Fixture;
test.beforeAll(() => expect(fixture().uiV3Synthetic).toBe(true));
test.beforeEach(async ({ context }) => {
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    return ['127.0.0.1', 'localhost'].includes(url.hostname) || ['data:', 'blob:'].includes(url.protocol) ? route.continue() : route.abort();
  });
});

for (const width of [1440, 390]) test(`S2 actual teacher preview preserves drafts, layer exits and local-only preferences at ${width}`, async ({ page }) => {
  test.setTimeout(90000);
  await page.setViewportSize({ width, height: 900 });
  await loginTeacher(page, fixture());
  await expectHealthUsesRuntimeDb(page, fixture());
  const before = await (await page.request.get('/api/profile/ui-preferences')).json();
  const mutations: string[] = [], errors: string[] = [];
  page.on('request', request => { if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method())) mutations.push(`${request.method()} ${new URL(request.url()).pathname}`); });
  page.on('pageerror', error => errors.push(error.message));
  const response = await page.goto('/dev/lq');
  expect(response?.status()).toBe(200);
  expect(response?.headers()['cache-control']).toContain('no-store');
  await expect(page.locator('[data-lq-preview]')).toHaveAttribute('data-lq-interactions-ready', 'true');
  await expect.poll(() => page.locator('#preview-segment > [role=tablist]').getAttribute('data-lq-thumb')).not.toBeNull();
  const chips = page.getByRole('group', { name: '更多筛选示例', exact: true });
  await chips.getByRole('button', { name: /更多/ }).click();
  await chips.getByRole('button', { name: '最近更新', exact: true }).click();
  await expect(chips.getByRole('button', { name: '最近更新', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await chips.getByRole('button', { name: '收起', exact: true }).click();
  await expect(page.getByRole('progressbar', { name: '环形示例进度', exact: true })).toHaveAttribute('aria-valuenow', '40');
  await expect(page.getByRole('progressbar', { name: '未确定的环形进度', exact: true })).not.toHaveAttribute('aria-valuenow');

  await page.locator('#preview-name').fill('实际预览里的草稿');
  await page.getByRole('button', { name: '检查填写', exact: true }).click();
  await expect(page.locator('[data-lq-demo-form-status]')).toContainText('实际预览里的草稿');
  await page.locator('#preview-tab-draft').fill('切换后保留的输入');
  const tabs = page.getByRole('tablist', { name: '学习内容' });
  await tabs.getByRole('tab', { name: '资料', exact: true }).click();
  await tabs.getByRole('tab', { name: '草稿 2' }).click();
  await expect(page.locator('#preview-tab-draft')).toHaveValue('切换后保留的输入');
  await page.getByRole('link', { name: '内容版本有变化，请核对后再提交。' }).click();
  await expect(page.locator('#preview-version')).toBeFocused();
  await expect(page.locator('#preview-version')).toHaveValue('这份输入需要保留');

  const guardedFold = page.locator('#preview-fold-draft');
  await page.locator('#preview-fold-input').fill('折叠保护保留此处输入');
  await guardedFold.locator('summary').click();
  await expect(guardedFold).toHaveAttribute('open', '');
  await expect(page.locator('#preview-fold-input')).toHaveValue('折叠保护保留此处输入');
  if (width < 768) {
    const helpFold = page.locator('#preview-fold-help');
    await helpFold.locator('summary').click();
    await expect(helpFold).not.toHaveAttribute('open', '');
    await helpFold.locator('summary').click();
    await expect(helpFold).toHaveAttribute('open', '');
  }

  const opener = page.getByRole('button', { name: '打开对话框', exact: true });
  await opener.click();
  const parent = page.getByRole('dialog', { name: '弹层示例', exact: true });
  await expect(parent).toBeVisible();
  await parent.getByRole('textbox', { name: '弹层内输入' }).fill('弹层内尚未离开的输入');
  await parent.getByRole('button', { name: '打开子确认', exact: true }).click();
  await expect(page.getByRole('dialog', { name: '子确认', exact: true })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog', { name: '子确认', exact: true })).toHaveCount(0);
  await expect(parent.getByRole('textbox', { name: '弹层内输入' })).toHaveValue('弹层内尚未离开的输入');
  await expect(parent.getByRole('button', { name: '打开子确认', exact: true })).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(parent).toHaveCount(0);
  await expect(opener).toBeFocused();
  await expect(page.locator('body')).not.toHaveCSS('overflow', 'hidden');
  await page.getByRole('button', { name: '演示三项选择', exact: true }).click();
  await page.getByRole('dialog', { name: '选择一种后续操作' }).getByRole('button', { name: '返回', exact: true }).click();
  await expect(page.locator('[data-lq-demo-dialog-status]')).toHaveText('已返回，未执行选项。');

  await page.getByRole('button', { name: '带操作的通知', exact: true }).click();
  await page.locator('#lq-toasts').getByRole('button', { name: '查看示例', exact: true }).click();
  await expect(page.locator('[data-lq-demo-dialog-status]')).toHaveText('已查看通知中的示例。');
  await expect(page.locator('#lq-toasts')).toHaveCount(0);

  await page.getByRole('button', { name: '更多示例操作', exact: true }).click();
  await page.getByRole('menuitem', { name: '保留示例副本', exact: true }).click();
  await expect(page.locator('[data-lq-demo-menu-status]')).toContainText('保留示例副本');
  await expect(page.getByRole('button', { name: '更多示例操作', exact: true })).toBeFocused();
  await page.locator('#preview-tooltip-trigger').focus();
  await expect(page.getByRole('tooltip', { name: '刷新预览' })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('tooltip')).toBeHidden();
  await page.locator('#preview-save-state').selectOption('local_saved');
  await expect(page.locator('#preview-save-status')).toContainText('已保存到本机');
  await page.locator('#preview-save-state').selectOption('conflict');
  await page.locator('#preview-save-status').getByRole('button', { name: '重新核对', exact: true }).click();
  await expect(page.locator('[data-lq-demo-save-feedback]')).toContainText('本地输入保持不变');
  await expect(page.locator('#preview-version')).toHaveValue('这份输入需要保留');
  await page.locator('#preview-save-state').selectOption('synced');
  await expect(page.locator('#preview-save-status')).toContainText('已同步到服务器');
  await expect(page.locator('.lq-card--stat')).toContainText('0');

  const records = page.locator('#preview-records');
  await records.locator('[data-lq-select-all]').check();
  await expect(records.locator('[data-lq-select-row]:checked')).toHaveCount(3);
  await expect(records.locator('[data-lq-select-row][value=locked]')).toBeDisabled();
  await expect(page.locator('[data-lq-demo-bulk-host]')).toContainText('3');
  await records.getByRole('button', { name: '按得分排序' }).click();
  await expect(page.locator('[data-lq-demo-table-status]')).toContainText('按得分排序');
  await records.locator('[data-lq-select-all]').uncheck();
  await expect(records.locator('[data-lq-select-row]:checked')).toHaveCount(1);
  const course = page.getByRole('combobox', { name: '搜索示例课程' });
  await course.fill('Python');
  await course.press('ArrowDown');
  await page.getByRole('option', { name: 'Python 程序设计', exact: true }).click();
  await expect(page.locator('#preview-course-select')).toHaveValue('python');
  await page.getByRole('listbox', { name: '选择示例资料' }).getByRole('option', { name: '课后练习' }).click();
  await page.getByRole('button', { name: '检查选择', exact: true }).click();
  await expect(page.locator('[data-lq-demo-selection-status]')).toContainText('资料 2 项');
  await page.getByRole('button', { name: '重置选择', exact: true }).click();
  await expect(page.locator('#preview-course-select')).toHaveValue('');
  await expect(course).toHaveValue('请选择课程');
  await page.getByRole('button', { name: '演示离开保护', exact: true }).click();
  await page.getByRole('textbox', { name: '受保护的示例草稿' }).fill('此处还有未保存输入');
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: '继续编辑', exact: true }).click();
  await expect(page.getByRole('textbox', { name: '受保护的示例草稿' })).toHaveValue('此处还有未保存输入');
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: '放弃并离开', exact: true }).click();
  await expect(page.getByRole('dialog', { name: '离开保护示例', exact: true })).toHaveCount(0);
  await page.locator('#preview-job-state').selectOption('superseded');
  await expect(page.locator('#preview-job')).toContainText('旧结果不会自动应用');
  await page.locator('#preview-job').getByRole('button', { name: '查看示例状态' }).click();
  await expect(page.locator('[data-lq-demo-job-status]')).toContainText('第 1 版');
  for (const state of ['queued', 'running', 'waiting_input', 'question_expired', 'unverified', 'partial', 'committed', 'completed', 'failed', 'canceled']) {
    await page.locator('#preview-agent-state').selectOption(state);
    await expect(page.locator('#preview-agent')).toHaveAttribute('data-lq-job-state', state);
  }
  await page.locator('#preview-agent').getByRole('button', { name: '查看助手状态示例' }).click();
  await expect(page.locator('[data-lq-demo-agent-status]')).toContainText('未执行任务');
  await expect(page.locator('#preview-upload-busy')).toHaveAttribute('data-upload-state', 'busy');
  await expect(page.locator('#preview-upload-partial')).toHaveAttribute('data-upload-state', 'partial-failed');
  await expect(page.locator('#preview-upload-partial')).toContainText('示例网络中断');
  await expect(page.locator('#preview-upload-partial-input')).toBeDisabled();
  await page.locator('#preview-pill-tabs').getByRole('tab', { name: '收藏', exact: true }).click();
  await expect(page.locator('#preview-pill-tabs').getByRole('tabpanel')).toHaveText('收藏资料示例。');
  await page.locator('#preview-vertical-tabs').getByRole('tab', { name: '大纲', exact: true }).focus();
  await page.keyboard.press('ArrowDown');
  await expect(page.locator('#preview-vertical-tabs').getByRole('tabpanel')).toHaveText('课程文件示例。');
  await page.locator('#preview-questions [data-lq-question=q3]').click();
  await expect(page.locator('[data-lq-demo-question-status]')).toContainText('第 3 题');
  await expect(page.locator('#preview-questions [aria-current]')).toHaveCount(1);
  await page.locator('#preview-upload-input').setInputFiles({ name: '本地示例.txt', mimeType: 'text/plain', buffer: Buffer.from('fixture only') });
  await expect(page.locator('#preview-upload')).toContainText('本地示例.txt');
  await expect(page.locator('#preview-upload')).toContainText('已选择，尚未上传');
  await page.locator('#preview-upload [data-lq-upload-action=remove]').click();
  await expect(page.locator('#preview-upload')).not.toContainText('本地示例.txt');
  const split = page.locator('#preview-workspace');
  if (width < 1024) await split.getByRole('tab', { name: '阅读笔记' }).click();
  await page.locator('#preview-split-draft').fill('分栏切换仍然保留的笔记');
  if (width < 1024) await split.getByRole('tab', { name: '嵌入文档' }).click();
  await page.frameLocator('iframe[title="原有白纸文档"]').getByRole('textbox', { name: '文档内笔记' }).fill('iframe 内输入仍然保留');
  if (width < 1024) {
    await split.getByRole('tab', { name: '阅读笔记' }).click();
    await expect(page.locator('#preview-split-draft')).toHaveValue('分栏切换仍然保留的笔记');
    await split.getByRole('tab', { name: '嵌入文档' }).click();
  } else {
    await split.getByRole('separator').focus();
    const beforeWidth = Number(await split.getByRole('separator').getAttribute('aria-valuenow'));
    await page.keyboard.press('ArrowRight');
    await expect(split.getByRole('separator')).toHaveAttribute('aria-valuenow', String(beforeWidth + 8));
  }
  await expect(page.frameLocator('iframe[title="原有白纸文档"]').getByRole('textbox', { name: '文档内笔记' })).toHaveValue('iframe 内输入仍然保留');

  const swipeRow = page.locator('#preview-swipe-row');
  await swipeRow.getByRole('button', { name: '演示移除', exact: true }).click();
  await expect(page.locator('[data-lq-demo-row-status]')).toContainText('记录保留');
  await expect(swipeRow).toBeVisible();
  const message = page.getByRole('textbox', { name: '预览消息草稿', exact: true });
  await message.fill('本地输入');
  await message.press('End'); await message.press('Enter'); await message.press('X');
  await expect(message).toHaveValue('本地输入\nX');
  await page.getByRole('button', { name: '插入笑脸示例' }).click();
  await expect(message).toHaveValue('本地输入\nX😄');
  await page.locator('#preview-composer-state').selectOption('busy');
  await expect(message).toHaveAttribute('readonly', '');
  await expect(page.getByRole('button', { name: '检查预览消息' })).toBeDisabled();
  await page.locator('#preview-composer-state').selectOption('ready');
  await page.getByRole('button', { name: '检查预览消息' }).click();
  await expect(page.locator('[data-lq-demo-composer-status]')).toContainText('草稿保留，未发送');
  await expect(message).toHaveValue('本地输入\nX😄');
  await page.evaluate(() => Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: async (text: string) => { (window as any).previewCopiedCode = text; } } }));
  await page.getByRole('button', { name: '复制代码', exact: true }).click();
  await expect.poll(() => page.evaluate(() => (window as any).previewCopiedCode)).toBe('for item in items:\n    print(item.title)');
  const imageTrigger = page.getByRole('button', { name: '打开星星图标示例' });
  await imageTrigger.click();
  const lightbox = page.getByRole('dialog', { name: '图片预览', exact: true });
  await expect(lightbox).toBeVisible();
  await expect.poll(() => lightbox.locator('img').evaluate((image: HTMLImageElement) => image.naturalWidth)).toBeGreaterThan(0);
  await lightbox.locator('img').click();
  await expect(lightbox).toBeVisible();
  await lightbox.getByRole('button', { name: '下一张', exact: true }).click();
  await expect(lightbox.locator('[data-ref=title]')).toHaveText('爱心图标示例');
  await page.keyboard.press('Escape');
  await expect(lightbox).toBeHidden();
  await expect(imageTrigger).toBeFocused();

  const directory = path.resolve('.codex-temp/lq-s2-app-preview');
  fs.mkdirSync(directory, { recursive: true });
  for (const appearance of ['light', 'dark']) {
    await page.locator('[data-lq-preview-controls] select[name=appearance]').selectOption(appearance);
    await page.locator('[data-lq-preview-controls] select[name=palette_key]').selectOption('teal');
    await expect(page.locator('html')).toHaveAttribute('data-appearance', appearance);
    await expect(page.locator('html')).toHaveAttribute('data-ui-palette', 'teal');
    await page.locator('h1').scrollIntoViewIfNeeded();
    const scan = await new AxeBuilder({ page }).analyze();
    expect(scan.violations.filter(item => ['serious', 'critical'].includes(item.impact || ''))).toEqual([]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    // Chromium's very long fullPage bitmap repeats content after y=16384 on
    // this host. Capture bounded sections so the lower components are evidence.
    for (const section of ['chips', 'indicators', 'forms', 'navigation-variants', 'content', 'save-status', 'business', 'upload']) {
      const region = page.locator(`section[aria-labelledby="lq-${section}-title"]`);
      await region.scrollIntoViewIfNeeded();
      await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
      expect((await region.boundingBox())!.height).toBeLessThan(8192);
      await region.screenshot({ path: path.join(directory, `audited-${section}-${appearance}-${width}.png`), animations: 'disabled' });
    }
  }
  const after = await (await page.request.get('/api/profile/ui-preferences')).json();
  expect(after.preferences).toEqual(before.preferences);
  expect(mutations).toEqual([]);
  expect(errors).toEqual([]);
});

for (const width of [1440, 390]) test(`S2 actual centered shell keeps local drafts and account preferences at ${width}`, async ({ page }) => {
  await page.setViewportSize({ width, height: 900 });
  await loginTeacher(page, fixture());
  await expectHealthUsesRuntimeDb(page, fixture());
  const before = await (await page.request.get('/api/profile/ui-preferences')).json();
  const mutations: string[] = [], errors: string[] = [];
  page.on('request', request => { if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method())) mutations.push(request.method()); });
  page.on('pageerror', error => errors.push(error.message));
  const response = await page.goto('/dev/lq?shell=centered');
  expect(response?.status()).toBe(200);
  expect(response?.headers()['cache-control']).toContain('no-store');
  await expect(page.locator('[data-lq-shell-preview=centered]')).toHaveAttribute('data-lq-shell-ready', 'true');
  await page.getByLabel('保留的本地内容').fill('居中页草稿保留');
  const directory = path.resolve('.codex-temp/lq-s2-app-preview');
  fs.mkdirSync(directory, { recursive: true });
  for (const appearance of ['light', 'dark']) {
    await page.locator('[data-lq-shell-theme] select[name=appearance]').selectOption(appearance);
    await expect(page.locator('html')).toHaveAttribute('data-appearance', appearance);
    await page.getByRole('button', { name: '演示本地操作' }).click();
    await expect(page.getByLabel('保留的本地内容')).toHaveValue('居中页草稿保留');
    await expect(page.locator('[data-lq-shell-feedback]')).toContainText('未发送保存请求');
    const scan = await new AxeBuilder({ page }).analyze();
    expect(scan.violations.filter(item => ['serious', 'critical'].includes(item.impact || ''))).toEqual([]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: path.join(directory, `centered-${appearance}-${width}.png`), fullPage: true, animations: 'disabled' });
  }
  expect((await (await page.request.get('/api/profile/ui-preferences')).json()).preferences).toEqual(before.preferences);
  expect(mutations).toEqual([]); expect(errors).toEqual([]);
});

for (const width of [1440, 390]) test(`S2 actual all-component palette matrix at ${width}`, async ({ page }) => {
  test.setTimeout(240000);
  await page.setViewportSize({ width, height: 900 });
  await loginTeacher(page, fixture());
  await expectHealthUsesRuntimeDb(page, fixture());
  const before = await (await page.request.get('/api/profile/ui-preferences')).json();
  const mutations: string[] = [], errors: string[] = [];
  page.on('request', request => { if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method())) mutations.push(`${request.method()} ${new URL(request.url()).pathname}`); });
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/dev/lq');
  await expect(page.locator('[data-lq-preview]')).toHaveAttribute('data-lq-interactions-ready', 'true');
  const directory = path.resolve('.codex-temp/lq-s2-palette-preview');
  fs.mkdirSync(directory, { recursive: true });
  for (const palette of ['teal', 'indigo', 'sky', 'mint', 'violet', 'rose']) for (const appearance of ['light', 'dark']) {
    await page.locator('[data-lq-preview-controls] select[name=palette_key]').selectOption(palette);
    await page.locator('[data-lq-preview-controls] select[name=appearance]').selectOption(appearance);
    await expect(page.locator('html')).toHaveAttribute('data-ui-palette', palette);
    await expect(page.locator('html')).toHaveAttribute('data-appearance', appearance);
    await page.locator('h1').scrollIntoViewIfNeeded();
    const scan = await new AxeBuilder({ page }).analyze();
    expect(scan.violations.filter(item => ['serious', 'critical'].includes(item.impact || '')), `${palette}/${appearance}/${width}`).toEqual([]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: path.join(directory, `${palette}-${appearance}-${width}.png`), fullPage: true, animations: 'disabled' });
  }
  await page.locator('[data-lq-preview-controls] select[name=glass]').selectOption('off');
  await expect(page.locator('html')).toHaveAttribute('data-lq-glass', 'off');
  expect(await page.locator('.lq-glass').evaluateAll(nodes => nodes.every(node => getComputedStyle(node).backdropFilter === 'none'))).toBe(true);
  const off = await new AxeBuilder({ page }).analyze();
  expect(off.violations.filter(item => ['serious', 'critical'].includes(item.impact || ''))).toEqual([]);
  const after = await (await page.request.get('/api/profile/ui-preferences')).json();
  expect(after.preferences).toEqual(before.preferences);
  expect(mutations).toEqual([]);
  expect(errors).toEqual([]);
});

test('S2 actual mobile lower components have bounded screenshots beyond the long-page capture limit', async ({ page }) => {
  test.setTimeout(240000);
  await page.setViewportSize({ width: 390, height: 900 });
  await loginTeacher(page, fixture());
  await expectHealthUsesRuntimeDb(page, fixture());
  const errors: string[] = [], mutations: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => { if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method())) mutations.push(request.method()); });
  const before = await (await page.request.get('/api/profile/ui-preferences')).json();
  await page.goto('/dev/lq');
  await expect(page.locator('[data-lq-preview]')).toHaveAttribute('data-lq-interactions-ready', 'true');
  const directory = path.resolve('.codex-temp/lq-s2-palette-preview/mobile-sections');
  fs.mkdirSync(directory, { recursive: true });
  for (const palette of ['teal', 'indigo', 'sky', 'mint', 'violet', 'rose']) for (const appearance of ['light', 'dark']) {
    await page.locator('[data-lq-preview-controls] select[name=palette_key]').selectOption(palette);
    await page.locator('[data-lq-preview-controls] select[name=appearance]').selectOption(appearance);
    await expect(page.locator('html')).toHaveAttribute('data-ui-palette', palette);
    await expect(page.locator('html')).toHaveAttribute('data-appearance', appearance);
    for (const section of ['business', 'upload', 'workspace', 'insights']) {
      const target = page.locator(`section[aria-labelledby=lq-${section}-title]`);
      await target.scrollIntoViewIfNeeded();
      const bounds = await target.boundingBox();
      expect(bounds).not.toBeNull();
      expect(bounds!.height).toBeLessThan(8192);
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      await target.screenshot({ path: path.join(directory, `${palette}-${appearance}-${section}.png`), animations: 'disabled' });
    }
  }
  expect((await (await page.request.get('/api/profile/ui-preferences')).json()).preferences).toEqual(before.preferences);
  expect(errors).toEqual([]); expect(mutations).toEqual([]);
});

for (const width of [1440, 390]) for (const layout of ['list', 'dashboard', 'detail', 'editor', 'take', 'immersive', 'reading']) test(`S2 actual shell preview ${layout} retains local actions and original panes at ${width}`, async ({ page }) => {
  await page.setViewportSize({ width, height: 900 });
  await loginTeacher(page, fixture());
  await expectHealthUsesRuntimeDb(page, fixture());
  const before = await (await page.request.get('/api/profile/ui-preferences')).json();
  const mutations: string[] = [], errors: string[] = [];
  page.on('request', request => { if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method())) mutations.push(`${request.method()} ${new URL(request.url()).pathname}`); });
  page.on('pageerror', error => errors.push(error.message));
  const response = await page.goto(`/dev/lq?layout=${layout}`);
  expect(response?.status()).toBe(200);
  expect(response?.headers()['cache-control']).toContain('no-store');
  await expect(page.locator('body')).toHaveAttribute('data-lq-shell-ready', 'true');
  await page.locator('#shell-primary-fallback').click();
  await expect(page.locator('[data-lq-shell-feedback]')).toContainText('输入保留');
  if (['editor', 'take'].includes(layout)) {
    await page.locator('#shell-main-draft').fill('保留主区草稿');
    if (width < 1280) await page.locator('button[data-lq-pane-open=rail]:visible').click();
    await page.locator('#shell-rail-draft').fill('保留辅助草稿');
    if (width < 1280) {
      await page.keyboard.press('Escape');
      await page.locator('button[data-lq-pane-open=aside]:visible').click();
    }
    await page.frameLocator('iframe[title="骨架独立文档"]').getByRole('textbox', { name: '文档内草稿' }).fill('保留文档内输入');
    if (width < 1280) {
      // Child-document keys do not bubble into the host; its own shortcuts stay
      // untouched. The host close control remains available beside the frame.
      await page.getByRole('button', { name: '关闭属性与预览', exact: true }).click();
      await page.locator('button[data-lq-pane-open=rail]:visible').click();
      await expect(page.locator('#shell-rail-draft')).toHaveValue('保留辅助草稿');
      await page.keyboard.press('Escape');
      await page.locator('button[data-lq-pane-open=aside]:visible').click();
      await expect(page.frameLocator('iframe[title="骨架独立文档"]').getByRole('textbox', { name: '文档内草稿' })).toHaveValue('保留文档内输入');
      await page.keyboard.press('Escape');
    }
    await expect(page.locator('#shell-main-draft')).toHaveValue('保留主区草稿');
  } else if (width < 768) {
    await page.locator('#shell-dock summary').click();
    await page.getByRole('button', { name: '演示更多操作', exact: true }).click();
    await page.keyboard.press('Escape');
  }
  if (['list', 'dashboard', 'detail'].includes(layout) && width < 1024) {
    await page.locator('[data-lq-pane-open=nav]').click();
    await page.getByRole('searchbox', { name: '搜索菜单' }).fill('列表');
    await expect(page.locator('#shell-sidebar').getByRole('link', { name: '列表工作台', exact: true })).toBeVisible();
    await page.keyboard.press('Escape');
  }
  const directory = path.resolve('.codex-temp/lq-s2-shell-preview');
  fs.mkdirSync(directory, { recursive: true });
  for (const appearance of ['light', 'dark']) {
    await page.locator('[data-lq-shell-theme] select[name=appearance]').selectOption(appearance);
    await expect(page.locator('html')).toHaveAttribute('data-appearance', appearance);
    await page.locator('h1').scrollIntoViewIfNeeded();
    const scan = await new AxeBuilder({ page }).analyze();
    expect(scan.violations.filter(item => ['serious', 'critical'].includes(item.impact || ''))).toEqual([]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: path.join(directory, `${layout}-${appearance}-${width}.png`), fullPage: true, animations: 'disabled' });
  }
  const after = await (await page.request.get('/api/profile/ui-preferences')).json();
  expect(after.preferences).toEqual(before.preferences);
  expect(mutations).toEqual([]);
  expect(errors).toEqual([]);
});

for (const role of ['teacher', 'student'] as const) test(`S2 actual ${role} React launchers retain destinations and the existing feedback entry`, async ({ page }) => {
  const errors: string[] = [], mutations: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  if (role === 'teacher') await loginTeacher(page, fixture()); else await loginStudent(page, fixture());
  await expectHealthUsesRuntimeDb(page, fixture());
  page.on('request', request => { if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method())) mutations.push(`${request.method()} ${new URL(request.url()).pathname}`); });
  await page.goto(role === 'teacher' ? `/submission/${fixture().teacherReviewSubmissionId}` : `/assignment/${fixture().studentSubmissionAssignmentId}`);
  for (const name of ['feedback-launcher', 'profile-launcher']) await expect(page.locator(`[data-lanshare-island="${name}"]`)).toHaveAttribute('data-react-mounted', 'true');
  const profile = page.getByRole('link', { name: '打开个人中心', exact: true });
  const feedback = page.getByRole('button', { name: '打开问题反馈', exact: true });
  await expect(profile).toHaveAttribute('href', '/profile');
  await expect(profile.locator('.lq-avatar img')).toHaveAttribute('src', '/api/profile/avatar');
  await expect(feedback).toHaveAttribute('type', 'button');
  await expect(feedback).toHaveClass(/lq-btn/);
  await feedback.click();
  await expect(page.locator('#feedback-modal')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.locator('#feedback-modal')).toBeHidden();
  expect(errors).toEqual([]);
  expect(mutations).toEqual([]);
});
