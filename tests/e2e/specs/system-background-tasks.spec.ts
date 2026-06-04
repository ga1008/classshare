import { expect, test } from '@playwright/test';
import {
  apiJson,
  collectBrowserErrors,
  expectNoBrowserErrors,
  loginTeacher,
  readFixture,
} from '../fixtures/p03';

test.describe('P11 background task diagnostics', () => {
  test('super teacher can render the background task ledger and read the API', async ({ page }, testInfo) => {
    const fixture = readFixture();
    const errors = collectBrowserErrors(page);

    await loginTeacher(page, fixture, fixture.superTeacher);
    await page.goto('/manage/system/diagnostics');
    await expect(page.locator('.manage-main')).toContainText('后台任务运行台账');
    await expect(page.locator('.dg-task-ledger')).toContainText('后台任务运行台账');
    await expect(page.locator('#dg-task-grid')).toContainText('AI 批改');
    await expect(page.locator('#dg-task-grid')).toContainText('行为写入管线');

    const ledger = await apiJson<{ status: number; ok: boolean; body: any }>(
      page,
      '/api/manage/system/background-tasks',
    );
    expect(ledger.status).toBe(200);
    expect(ledger.body.status).toBe('success');
    expect(ledger.body.summary.task_type_count).toBeGreaterThanOrEqual(8);
    expect(ledger.body.items.map((item: any) => item.task_type)).toEqual(
      expect.arrayContaining([
        'ai_grading',
        'material_ai_import',
        'private_message_ai_reply',
        'email_outbox',
        'blog_news_crawler',
        'agent_task',
        'behavior_write_pipeline',
      ]),
    );

    await expectNoBrowserErrors(errors, testInfo);
  });

  test('ordinary teacher cannot read the full background task ledger', async ({ page }, testInfo) => {
    const fixture = readFixture();
    const errors = collectBrowserErrors(page);

    await loginTeacher(page, fixture);
    const ledger = await apiJson<{ status: number; ok: boolean; body: any }>(
      page,
      '/api/manage/system/background-tasks',
    );
    expect(ledger.status).toBe(403);

    await expectNoBrowserErrors(errors, testInfo);
  });
});
