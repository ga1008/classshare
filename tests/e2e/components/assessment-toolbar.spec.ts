import { test, expect } from '@playwright/test';
import { mountAssessmentControls } from '../fixtures/assessment-controls';

const names = ['加粗', '左对齐', '居中', '右对齐', '自动编号', '增加层级', '减少层级', '插入代码块', '预览'];

test('assessment icon toolbar retains native selection, actions and preview semantics', async ({ page }) => {
  await mountAssessmentControls(page);
  const toolbar = page.getByRole('group', { name: '格式工具', exact: true });
  await expect(toolbar.getByRole('button')).toHaveCount(names.length);
  for (const name of names) {
    const control = toolbar.getByRole('button', { name, exact: true });
    await expect(control.locator('svg')).toHaveCount(1);
    expect((await control.textContent())?.trim()).toBe('');
    expect(await control.getAttribute('title')).toBeNull();
    const glyph = await control.locator('svg').evaluate(svg => { const b = (svg as SVGSVGElement).getBBox(); return { x: b.x, y: b.y, right: b.x + b.width, bottom: b.y + b.height }; });
    expect(glyph.x).toBeGreaterThanOrEqual(0); expect(glyph.y).toBeGreaterThanOrEqual(0);
    expect(glyph.right).toBeLessThanOrEqual(24); expect(glyph.bottom).toBeLessThanOrEqual(24);
  }
  const input = page.locator('#answer');
  await input.fill('保留选区'); await input.selectText();
  await toolbar.getByRole('button', { name: '加粗', exact: true }).click();
  await expect(input).toHaveValue('**保留选区**');
  await input.press('Control+z'); await expect(input).toHaveValue('保留选区');
  await input.fill('步骤'); await input.selectText();
  await toolbar.getByRole('button', { name: '自动编号', exact: true }).click();
  await expect(input).toHaveValue('1. 步骤');
  await input.press('End'); await input.press('Enter'); await input.pressSequentially('Next');
  await expect(input).toHaveValue('1. 步骤\n2. Next');
  await toolbar.getByRole('button', { name: '增加层级', exact: true }).click();
  await expect(input).toHaveValue('1. 步骤\n    1. Next');
  await toolbar.getByRole('button', { name: '减少层级', exact: true }).click();
  await expect(input).toHaveValue('1. 步骤\n2. Next');
  await input.fill('居中内容'); await input.selectText();
  await toolbar.getByRole('button', { name: '居中', exact: true }).click();
  await toolbar.getByRole('button', { name: '预览', exact: true }).click();
  await expect(page.locator('.assessment-align-center')).toHaveText('居中内容');
  const resume = toolbar.getByRole('button', { name: '继续编辑', exact: true });
  await expect(resume).toHaveAttribute('aria-pressed', 'true');
  await expect(resume.locator('svg')).toHaveCount(1);
  expect((await resume.textContent())?.trim()).toBe('');
  await resume.click(); await expect(input).toBeFocused();
  expect(await page.evaluate(() => (window as any).original === document.getElementById('answer'))).toBe(true);
});

test('assessment tooltip waits one second, cancels hover, focuses immediately and tears down pending work', async ({ page }) => {
  await mountAssessmentControls(page);
  await page.clock.install(); await page.clock.pauseAt(new Date());
  const bold = page.getByRole('button', { name: '加粗', exact: true });
  await page.locator('#answer').focus();
  await bold.hover(); await page.clock.runFor(999);
  await expect(page.getByRole('tooltip')).toHaveCount(0);
  await page.clock.runFor(1); await expect(page.getByRole('tooltip')).toContainText('加粗');
  expect(await bold.getAttribute('aria-describedby')).toBe(await page.getByRole('tooltip').getAttribute('id'));
  await page.mouse.move(5, 5); await page.clock.runFor(200);
  await expect(page.getByRole('tooltip')).toHaveCount(0);
  await bold.hover(); await page.clock.runFor(100); await bold.click();
  await expect(page.getByRole('tooltip')).toHaveCount(0);
  await page.clock.runFor(500); await expect(page.getByRole('tooltip')).toHaveCount(0);
  await page.mouse.move(5, 5); await page.clock.runFor(200);
  await bold.hover(); await page.clock.runFor(350); await page.mouse.move(5, 5); await page.clock.runFor(1500);
  await expect(page.getByRole('tooltip')).toHaveCount(0);
  await bold.focus(); await expect(page.getByRole('tooltip')).toContainText('加粗');
  await page.keyboard.press('Escape'); await page.clock.runFor(200);
  await expect(page.getByRole('tooltip')).toHaveCount(0);
  await page.locator('#answer').focus(); await bold.hover(); await page.clock.runFor(300);
  await page.evaluate(() => (window as any).editor.destroy()); await page.clock.runFor(1500);
  await expect(page.getByRole('tooltip')).toHaveCount(0);
  await expect(page.locator('#answer')).toHaveCount(1);
});

test('default shared tooltip keeps 400ms delay and removal ownership; editors add no per-button observers', async ({ page }) => {
  await mountAssessmentControls(page);
  await page.clock.install(); await page.clock.pauseAt(new Date());
  const observed = await page.evaluate(() => {
    const w = window as any; w.editor.destroy();
    const Native = window.MutationObserver;
    let observers = 0;
    window.MutationObserver = class extends Native { observe(...args: Parameters<MutationObserver['observe']>) { observers++; super.observe(...args); } };
    w.editor = w.editorApi.mountAssessmentEditor(w.original, { code: true });
    window.MutationObserver = Native;
    const icon = document.createElement('button'); icon.id = 'legacy-tooltip'; icon.setAttribute('aria-label', '旧提示');
    icon.style.cssText = 'position:fixed;left:20px;bottom:20px';
    icon.innerHTML = '<svg aria-hidden="true" width="24" height="24"><path d="M2 2h20v20H2z"/></svg>';
    document.body.append(icon); w.legacyTip = w.tooltipApi.bindTooltip(icon, { text: '默认提示仍为400ms' });
    return observers;
  });
  expect(observed).toBe(0);
  const legacy = page.locator('#legacy-tooltip');
  await page.clock.runFor(32);
  await legacy.hover(); await page.clock.runFor(399); await expect(page.getByRole('tooltip')).toHaveCount(0);
  await page.clock.runFor(1); await expect(page.getByRole('tooltip')).toHaveText('默认提示仍为400ms');
  await legacy.evaluate(node => node.remove()); await page.clock.runFor(200);
  await expect(page.getByRole('tooltip')).toHaveCount(0);
});

test.describe('assessment touch toolbar', () => {
  test.use({ hasTouch: true, viewport: { width: 390, height: 844 } });
  for (const appearance of ['light', 'dark']) test(`44px icon controls and editable content fit narrow ${appearance}`, async ({ page }, testInfo) => {
    await mountAssessmentControls(page, { appearance });
    const buttons = page.locator('.assessment-editor__toolbar button');
    for (const button of await buttons.all()) {
      const box = (await button.boundingBox())!;
      expect(box.width).toBeGreaterThanOrEqual(44); expect(box.height).toBeGreaterThanOrEqual(44);
      expect(box.x).toBeGreaterThanOrEqual(0); expect(box.x + box.width).toBeLessThanOrEqual(390);
    }
    const metrics = await page.evaluate(() => ({ innerWidth, scrollWidth: document.documentElement.scrollWidth,
      toolbarWidth: document.querySelector('.assessment-editor__toolbar')!.getBoundingClientRect().width }));
    expect(metrics.scrollWidth).toBeLessThanOrEqual(metrics.innerWidth);
    await testInfo.attach('layout.json', { contentType: 'application/json', body: JSON.stringify(metrics) });
    await page.locator('#answer').fill('触摸编辑'); await page.locator('#answer').selectText();
    await page.getByRole('button', { name: '加粗', exact: true }).tap();
    await expect(page.locator('#answer')).toHaveValue('**触摸编辑**');
    await page.screenshot({ path: testInfo.outputPath(`toolbar-${appearance}-390.png`), fullPage: true });
  });
});
