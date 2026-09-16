import { expect, test, type Page } from '@playwright/test';
import { apiJson, loginStudent, loginTeacher, readFixture } from '../fixtures/p03';

const post = (page: Page, url: string, body: unknown) => apiJson(page, url, {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
});

test.describe('Feedback conversations', () => {
  test('desktop admin and mobile author can continue the same conversation', async ({ browser, baseURL }, testInfo) => {
    test.setTimeout(120_000);
    const fixture = readFixture();
    const adminContext = await browser.newContext({ baseURL, viewport: { width: 1440, height: 980 } });
    const studentContext = await browser.newContext({ baseURL, viewport: { width: 390, height: 844 } });
    const admin = await adminContext.newPage();
    const student = await studentContext.newPage();
    const errors: string[] = [];
    admin.on('pageerror', error => errors.push(error.message));
    student.on('pageerror', error => errors.push(error.message));
    try {
      await loginStudent(student, fixture);
      await student.locator('details.app-topbar-menu').filter({ has: student.locator('[data-open-feedback]') }).locator('summary').click();
      await student.locator('[data-open-feedback]:visible').first().click();
      await expect(student.locator('#feedback-modal')).toBeVisible();
      await student.locator('#feedback-title').fill('移动端作业提交反馈');
      await student.locator('#feedback-description').fill('请帮我确认重新提交作业的方法。');
      await student.locator('#feedback-attachment-input').setInputFiles({
        name: 'screenshot.png', mimeType: 'image/png',
        buffer: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aIuEAAAAASUVORK5CYII=', 'base64'),
      });
      const createdResponse = student.waitForResponse(response => response.url().endsWith('/api/feedback') && response.request().method() === 'POST');
      await student.locator('#feedback-submit-btn').click();
      const created = await (await createdResponse).json();
      const id = created.feedback_id;
      expect(id).toBeTruthy();
      await expect(student.locator('#feedback-success')).toBeVisible();
      await loginTeacher(admin, fixture, fixture.superTeacher);
      await admin.goto(`/manage/system/feedback?feedback_id=${id}`);
      const adminThread = admin.locator('.fb-thread').filter({ has: admin.locator('.fb-thread-composer textarea') });
      await expect(adminThread).toBeVisible();
      await expect(adminThread.locator('.fb-thread-attachments img')).toHaveCount(1);
      await adminThread.locator('.fb-thread-composer textarea').fill('请打开作业详情，选择重新提交；如果没有按钮，请告诉我作业名称。');
      await adminThread.locator('.fb-thread-composer button[type=submit]').click();
      await expect(adminThread.locator('.fb-thread-history')).toContainText('请打开作业详情');
      await admin.screenshot({ path: testInfo.outputPath('feedback-admin-desktop.png'), fullPage: true });
      await student.goto(`/dashboard?feedback_id=${id}`);
      const studentThread = student.locator('#feedback-modal .fb-thread');
      await expect(studentThread.locator('.fb-thread-history')).toContainText('请打开作业详情');
      await studentThread.locator('.fb-thread-composer textarea').fill('已找到按钮，谢谢！<img src=x onerror=alert(1)>');
      await student.route(`**/api/feedback/${id}/messages`, route => route.abort('failed'), { times: 1 });
      await studentThread.locator('.fb-thread-composer button[type=submit]').click();
      await expect(studentThread.locator('.fb-thread-notice')).toContainText('输入内容已保留');
      await expect(studentThread.locator('.fb-thread-composer textarea')).toHaveValue('已找到按钮，谢谢！<img src=x onerror=alert(1)>');
      await studentThread.locator('.fb-thread-composer button[type=submit]').click();
      await expect(studentThread.locator('.fb-thread-history')).toContainText('已找到按钮，谢谢！<img src=x onerror=alert(1)>');
      await expect(studentThread.locator('.fb-thread-history img[src="x"]')).toHaveCount(0);
      await student.screenshot({ path: testInfo.outputPath('feedback-author-mobile.png'), fullPage: true });
      expect(await student.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      await admin.reload();
      await expect(adminThread.locator('.fb-thread-history')).toContainText('已找到按钮');
      await adminThread.locator('.fb-thread-admin-actions button').click();
      await adminThread.locator('.fb-thread-confirm').getByRole('button', { name: '确认关闭', exact: true }).click();
      await expect(adminThread.locator('.fb-thread-composer textarea')).toBeDisabled();
      await student.reload();
      await expect(studentThread).toContainText('已关闭');
      await expect(studentThread.locator('.fb-thread-composer textarea')).toBeDisabled();
      await adminThread.locator('.fb-thread-admin-actions button').click();
      await adminThread.locator('.fb-thread-confirm').getByRole('button', { name: '确认开启', exact: true }).click();
      await expect(adminThread.locator('.fb-thread-composer textarea')).toBeEnabled();
      await student.reload();
      await expect(studentThread.locator('.fb-thread-composer textarea')).toBeEnabled();
      expect(errors).toEqual([]);
    } finally {
      await Promise.all([adminContext.close(), studentContext.close()]);
    }
  });

  test('isolates participants, deduplicates replies and preserves closed history', async ({ browser, baseURL }) => {
    test.setTimeout(120_000);
    const fixture = readFixture();
    const contexts = await Promise.all([0, 1, 2].map(() => browser.newContext({ baseURL })));
    const [student, admin, outsider] = await Promise.all(contexts.map(context => context.newPage()));
    try {
      await loginStudent(student, fixture);
      await loginTeacher(admin, fixture, fixture.superTeacher);
      await loginTeacher(outsider, fixture);
      const created = await post(student, '/api/feedback', {
        feedback_type: 'bug', title: 'Feedback conversation integration', description: 'Original retained content',
      });
      expect(created.status).toBe(201);
      const id = created.body.feedback_id;
      const url = `/api/feedback/${id}`;
      expect((await apiJson(outsider, `${url}/detail`)).status).toBe(403);
      expect((await post(outsider, `${url}/messages`, { content: 'forbidden', client_message_id: 'outsider-001' })).status).toBe(403);
      expect((await post(student, `${url}/status`, { status: 'closed', client_message_id: 'owner-close-001', expected_last_message_id: 0 })).status).toBe(403);
      const reply = { content: 'We received your feedback.', client_message_id: 'admin-reply-001' };
      const sent = await post(admin, `${url}/messages`, reply);
      expect(sent.ok).toBeTruthy();
      const retry = await post(admin, `${url}/messages`, reply);
      expect(retry.ok).toBeTruthy();
      expect(retry.body.message.id).toBe(sent.body.message.id);
      const detail = await apiJson(student, `${url}/detail`);
      expect(detail.body.feedback.unread_count).toBe(1);
      expect(detail.body.feedback.can_withdraw).toBe(false);
      expect((await apiJson(student, url, { method: 'DELETE' })).status).toBe(409);
      const studentReply = await post(student, `${url}/messages`, { content: 'Additional details.', client_message_id: 'student-reply-001' });
      expect(studentReply.ok).toBeTruthy();
      const staleClose = await post(admin, `${url}/status`, {
        status: 'closed', content: 'Done', client_message_id: 'stale-close-001', expected_last_message_id: sent.body.message.id,
      });
      expect(staleClose.status).toBe(409);
      const closed = await post(admin, `${url}/status`, {
        status: 'closed', content: 'Resolved after discussion', client_message_id: 'admin-close-001', expected_last_message_id: studentReply.body.message.id,
      });
      expect(closed.ok).toBeTruthy();
      expect((await post(student, `${url}/messages`, { content: 'after close', client_message_id: 'after-close-001' })).status).toBe(409);
      const closedDetail = await apiJson(student, `${url}/detail`);
      expect(closedDetail.body.feedback.status).toBe('closed');
      expect(closedDetail.body.feedback.description).toBe('Original retained content');
      expect(closedDetail.body.feedback.can_reply).toBe(false);
      const reopened = await post(admin, `${url}/status`, {
        status: 'open', client_message_id: 'admin-reopen-001', expected_last_message_id: closedDetail.body.feedback.last_message_id,
      });
      expect(reopened.ok).toBeTruthy();
      expect((await post(student, `${url}/messages`, { content: 'It works now.', client_message_id: 'student-final-001' })).ok).toBeTruthy();
      const history = await apiJson(student, `${url}/messages?limit=2`);
      expect(history.body.items).toHaveLength(2);
      expect(history.body.has_more).toBe(true);
      const older = await apiJson(student, `${url}/messages?limit=2&before_id=${history.body.next_before_id}`);
      expect(older.body.items.map((item: any) => item.id).some((id: number) => history.body.items.some((item: any) => item.id === id))).toBe(false);
      const latest = await apiJson(student, `${url}/detail`);
      expect((await post(student, `${url}/read`, { last_message_id: latest.body.feedback.last_message_id })).ok).toBeTruthy();
      expect((await apiJson(student, `${url}/detail`)).body.feedback.unread_count).toBe(0);
    } finally {
      await Promise.all(contexts.map(context => context.close()));
    }
  });
});
