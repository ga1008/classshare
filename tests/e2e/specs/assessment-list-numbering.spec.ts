import { test, expect, readS3Fixture } from '../fixtures/lq-s3';
import { loginStudent } from '../fixtures/p03';

for (const kind of ['exam', 'ordinary']) test(`list numbering survives ${kind} server draft save and recovery`, async ({ page }, testInfo) => {
  const fixture = readS3Fixture(); await loginStudent(page, fixture);
  const assignment = kind === 'exam' ? fixture.s3.examDeadlineAssignmentId : fixture.s3.legacyReturnedHomeworkAssignmentId;
  const url = kind === 'exam' ? `/exam/take/${assignment}` : `/assignment/${assignment}`;
  const loaded = page.waitForResponse(response => response.url().endsWith(`/api/assignments/${assignment}/draft`) && response.request().method() === 'GET');
  expect((await page.goto(url))?.status()).toBe(200); expect((await loaded).status()).toBe(200);
  const input = kind === 'exam' ? page.locator('textarea[data-text-input="q1"]') : page.locator('.answer-textarea').first();
  if (kind === 'ordinary') await expect(page.locator('#file-chips .file-chip')).toHaveCount(1);
  await input.fill('3. 分析问题\n4. 具体步骤'); await input.press('End'); await input.press('Tab');
  await expect(input).toHaveValue('3. 分析问题\n    1. 具体步骤');
  await input.press('Enter'); await input.pressSequentially('Next');
  await expect(input).toHaveValue('3. 分析问题\n    1. 具体步骤\n    2. Next');
  const saved = page.waitForResponse(async response => {
    if (!response.url().endsWith(`/api/assignments/${assignment}/draft`) || response.request().method() !== 'POST') return false;
    return (response.request().postData() || '').includes('4. Next');
  });
  await input.press('Shift+Tab');
  const finalAnswer = '3. 分析问题\n    1. 具体步骤\n4. Next';
  await expect(input).toHaveValue(finalAnswer); expect((await saved).status()).toBe(200);
  await page.goto('/dashboard'); await page.evaluate(() => localStorage.clear());
  await page.goto(url); await expect(input).toHaveValue(finalAnswer);
  if (kind === 'ordinary') await expect(page.locator('#file-chips')).toContainText('legacy-returned-homework.txt');
  await testInfo.attach('restored-numbered-answer.txt', { contentType: 'text/plain', body: await input.inputValue() });
});
