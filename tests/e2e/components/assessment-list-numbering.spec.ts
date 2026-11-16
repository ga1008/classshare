import { test, expect } from '@playwright/test';
import { mountAssessmentControls } from '../fixtures/assessment-controls';

test('list Tab starts a fresh child level and continues only siblings of the same parent', async ({ page }) => {
  await mountAssessmentControls(page);
  const input = page.locator('#answer');
  for (const [before, after] of [
    ['1. 父项\n2. 新子项', '1. 父项\n    1. 新子项'],
    ['1. 父项\n    3. 已有子项\n        9. 更深子项\n2. 新子项', '1. 父项\n    3. 已有子项\n        9. 更深子项\n    4. 新子项'],
    ['1. 前父项\n    8. 前父子项\n2. 新父项\n3. 新子项', '1. 前父项\n    8. 前父子项\n2. 新父项\n    1. 新子项'],
    ['1. 前列表\n    8. 旧子项\n\n新段落\n2. 当前项', '1. 前列表\n    8. 旧子项\n\n新段落\n    1. 当前项'],
  ]) {
    await input.fill(before); await input.press('End'); await input.press('Tab'); await expect(input).toHaveValue(after);
    expect(await input.evaluate(node => (node as HTMLTextAreaElement).selectionStart)).toBe(after.length);
  }
});

test('list Shift Tab and empty child Enter continue the destination parent level', async ({ page }) => {
  await mountAssessmentControls(page); const input = page.locator('#answer');
  await input.fill('9) 父项\n    8) 子项'); await input.press('End'); await input.press('Shift+Tab');
  await expect(input).toHaveValue('9) 父项\n10) 子项');
  await input.press('Shift+Tab'); await expect(input).toHaveValue('9) 父项\n10) 子项');
  await input.fill('4. 父项\n    8. '); await input.press('End'); await input.press('Enter');
  await expect(input).toHaveValue('4. 父项\n5. ');
  await input.press('Enter'); await expect(input).toHaveValue('4. 父项\n');
  await input.fill('3. 父项\n    6. 子项\n        9. '); await input.press('End'); await input.press('Enter');
  await expect(input).toHaveValue('3. 父项\n    6. 子项\n    7. ');
  await input.fill('1. 父项\n    1. 子项\n    继续说明\n    2. 当前项'); await input.press('End'); await input.press('Shift+Tab');
  await expect(input).toHaveValue('1. 父项\n    1. 子项\n    继续说明\n2. 当前项');
});

test('list level buttons preserve the content selection through number-width changes and native undo', async ({ page }) => {
  await mountAssessmentControls(page); const input = page.locator('#answer');
  const original = '9. 父项\n10. 子项正文', nested = '9. 父项\n    1. 子项正文';
  await input.fill(original);
  await input.evaluate((node: HTMLTextAreaElement) => { const start = node.value.indexOf('正文'); node.setSelectionRange(start, start + 2); });
  await page.getByRole('button', { name: '增加层级', exact: true }).click();
  await expect(input).toHaveValue(nested);
  expect(await input.evaluate((node: HTMLTextAreaElement) => node.value.slice(node.selectionStart, node.selectionEnd))).toBe('正文');
  await input.press('Control+z'); await expect(input).toHaveValue(original);
  await input.press('Control+y'); await expect(input).toHaveValue(nested);
  await page.getByRole('button', { name: '减少层级', exact: true }).click();
  await expect(input).toHaveValue(original);
  await input.press('End'); await input.press('Tab'); await expect(input).toHaveValue(nested);
  await input.press('Shift+Tab'); await expect(input).toHaveValue(original);
});

test('list multiline selection renumbers its own siblings and excludes the next unselected line', async ({ page }) => {
  await mountAssessmentControls(page); const input = page.locator('#answer');
  const original = '2. 父项\n7. 子甲\n8. 子乙\n9. 不应改动';
  await input.fill(original);
  await input.evaluate((node: HTMLTextAreaElement) => node.setSelectionRange(node.value.indexOf('子甲'), node.value.indexOf('9.')));
  await input.press('Tab');
  await expect(input).toHaveValue('2. 父项\n    1. 子甲\n    2. 子乙\n9. 不应改动');
  expect(await input.evaluate((node: HTMLTextAreaElement) => node.value.slice(node.selectionStart, node.selectionEnd))).toBe('子甲\n    2. 子乙\n');
  await page.getByRole('button', { name: '减少层级', exact: true }).click();
  await expect(input).toHaveValue('2. 父项\n3. 子甲\n4. 子乙\n9. 不应改动');
  await input.press('Control+z');
  await expect(input).toHaveValue('2. 父项\n    1. 子甲\n    2. 子乙\n9. 不应改动');
});

test('list edits retain unordered markers, IME and fenced code boundaries', async ({ page }) => {
  await mountAssessmentControls(page); const input = page.locator('#answer');
  await input.fill('- 父项\n- 子项'); await input.press('End'); await input.press('Tab');
  await expect(input).toHaveValue('- 父项\n    - 子项');
  await input.press('Shift+Tab'); await expect(input).toHaveValue('- 父项\n- 子项');
  await input.fill('1. 父项\n2. 输入法未完成'); await input.press('End');
  await input.dispatchEvent('compositionstart');
  await input.dispatchEvent('keydown', { key: 'Tab', code: 'Tab', isComposing: true });
  await expect(input).toHaveValue('1. 父项\n2. 输入法未完成'); await input.dispatchEvent('compositionend');
  const code = '```text\n1. 代码中的编号\n2. 仍是代码';
  await input.fill(code); await input.press('End'); await input.press('Tab');
  await expect(input).toHaveValue(code); await expect(page.locator('#after')).toBeFocused();
});

test('list selected nested levels each restart while a selection crossing fences remains untouched', async ({ page }) => {
  await mountAssessmentControls(page); const input = page.locator('#answer');
  await input.fill('1. 父项\n4. 移动项\n    7. 子项\n5. 未选项');
  await input.evaluate((node: HTMLTextAreaElement) => node.setSelectionRange(node.value.indexOf('4.'), node.value.indexOf('5.')));
  await input.press('Tab');
  await expect(input).toHaveValue('1. 父项\n    1. 移动项\n        1. 子项\n5. 未选项');
  await input.press('Control+z'); await expect(input).toHaveValue('1. 父项\n4. 移动项\n    7. 子项\n5. 未选项');
  const fenced = '1. 文本项\n```text\n2. 代码内容\n```\n3. 后续项';
  await input.fill(fenced); await input.selectText(); await input.press('Tab');
  await expect(input).toHaveValue(fenced); await expect(input).not.toBeFocused();
  const indentedCode = '1. 父项\n    ```text\n    7. 原样代码\n    ```';
  await input.fill(indentedCode);
  await input.evaluate((node: HTMLTextAreaElement) => { const caret = node.value.indexOf('原样代码') + 4; node.setSelectionRange(caret, caret); });
  await input.press('Tab'); await expect(input).toHaveValue(indentedCode); await expect(input).not.toBeFocused();
  const merging = '1. 顶层甲\n    8. 子项\n2. 顶层乙';
  await input.fill(merging); await input.selectText(); await input.press('Shift+Tab');
  await expect(input).toHaveValue('1. 顶层甲\n2. 子项\n3. 顶层乙');
  await input.press('Control+z'); await expect(input).toHaveValue(merging);
});

test('list CRLF source keeps line endings and the textarea handles pasted Windows text natively', async ({ page }) => {
  await mountAssessmentControls(page); const input = page.locator('#answer');
  const windowsText = '9. 父项\r\n10. 子项';
  const result = await page.evaluate(value => {
    const change = (window as any).editorApi.markdownKeyEdit(value, value.length, value.length, 'Tab');
    return value.slice(0, change.start) + change.text + value.slice(change.end);
  }, windowsText);
  expect(result).toBe('9. 父项\r\n    1. 子项');
  await input.fill(windowsText); await input.press('End'); await input.press('Tab');
  await expect(input).toHaveValue('9. 父项\n    1. 子项');
  await input.press('Shift+Tab'); await expect(input).toHaveValue('9. 父项\n10. 子项');
});

test('list nested preview keeps ordered hierarchy readable in narrow light and dark', async ({ page }, testInfo) => {
  await mountAssessmentControls(page); const input = page.locator('#answer');
  await input.fill('1. 分析问题\n2. 第一步'); await input.press('End'); await input.press('Tab');
  await input.press('Enter'); await input.pressSequentially('Next');
  await expect(input).toHaveValue('1. 分析问题\n    1. 第一步\n    2. Next');
  await page.getByRole('button', { name: '预览', exact: true }).click();
  const preview = page.locator('.assessment-editor__preview');
  await expect(preview.locator('ol ol > li')).toHaveCount(2);
  await expect(preview.locator('ol ol > li').first()).toHaveText('第一步');
  await expect(preview.locator('ol ol > li').last()).toHaveText('Next');
  await page.setViewportSize({ width: 390, height: 844 });
  for (const appearance of ['light', 'dark']) {
    await page.evaluate(appearance => document.documentElement.dataset.appearance = appearance, appearance);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
    await page.screenshot({ path: testInfo.outputPath(`numbered-preview-${appearance}-390.png`) });
  }
  await page.getByRole('button', { name: '继续编辑', exact: true }).click();
  await expect(input).toHaveValue('1. 分析问题\n    1. 第一步\n    2. Next');
});
