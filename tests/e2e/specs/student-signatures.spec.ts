import { expect, test } from '@playwright/test';
import {
  apiJson,
  collectBrowserErrors,
  expectNoBrowserErrors,
  loginStudent,
  readFixture,
} from '../fixtures/p03';

// 1x1 transparent PNG
const PNG_BYTES = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==',
  'base64',
);

test.describe('student signature center', () => {
  test('student uploads own signature, reviews an incoming request, and sees usage trail', async ({ page }, testInfo) => {
    const fixture = readFixture();
    const errors = collectBrowserErrors(page);
    await loginStudent(page, fixture);

    let approveCalled = false;
    let mockRequestStatus = 'pending';
    const mockIncoming = () => ({
      items: [
        {
          id: 7001,
          signature_id: 1,
          signature_name: '学生签名',
          signature_subject_name: fixture.student.name,
          requester_role: 'teacher',
          requester_id: 1,
          requester_name: 'QA P03 Teacher',
          status: mockRequestStatus,
          request_note: '成绩登记表需要你的签名',
          context_label: 'P03 期末成绩登记表',
          items: [
            {
              id: 1,
              function_point_key: 'academic_final_material.grade_register.teacher_signature',
              function_point_label: '期末成绩登记表 · 底部任课教师签字处',
              status: mockRequestStatus === 'approved' ? 'available' : 'pending',
            },
          ],
          reviewers: [
            {
              role: 'student',
              id: fixture.student.id,
              kind: 'signer',
              name: fixture.student.name,
              status: mockRequestStatus === 'approved' ? 'approved' : 'pending',
            },
            { role: 'teacher', id: 2, kind: 'owner', name: '归属教师', status: mockRequestStatus === 'approved' ? 'superseded' : 'pending' },
          ],
        },
      ],
    });
    await page.route('**/api/signatures/requests?direction=incoming*', async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(mockIncoming()) });
    });
    await page.route('**/api/signatures/requests/7001/approve', async (route) => {
      approveCalled = true;
      mockRequestStatus = 'approved';
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'success' }) });
    });

    await page.goto('/profile?section=signatures');
    const app = page.locator('[data-signature-app]');
    await expect(page.locator('.profile-section-head h2')).toContainText('电子签名');
    await expect(app.getByRole('button', { name: '上传签名' })).toBeVisible();

    // Real upload against the real API: subject is forced to the student.
    await app.locator('[data-psig-file]').setInputFiles({
      name: 'signature.png',
      mimeType: 'image/png',
      buffer: PNG_BYTES,
    });
    const uploadedCard = app.locator('.psig-item').first();
    await expect(uploadedCard).toContainText(fixture.student.name);
    await expect(uploadedCard).toContainText('本人签名');
    await expect(uploadedCard).toContainText('归属于我');

    const listed = await apiJson<{ status: number; body: any }>(page, '/api/signatures?limit=50');
    expect(listed.status).toBe(200);
    const mine = (listed.body.items || []).filter(
      (item: any) => item.subject_role === 'student' && Number(item.subject_id) === Number(fixture.student.id),
    );
    expect(mine.length).toBeGreaterThan(0);
    const signatureId = mine[0].id;

    // Incoming approval panel renders the pending request and 同意 posts approve.
    await expect(app).toContainText('待我审批');
    await expect(app).toContainText('QA P03 Teacher');
    await expect(app).toContainText('期末成绩登记表 · 底部任课教师签字处');
    await app.getByRole('button', { name: '同意' }).click();
    await expect.poll(() => approveCalled).toBe(true);
    const settled = app.locator('details.psig-history');
    await expect(settled).toBeVisible();
    await settled.locator('summary').click();
    await expect(settled.locator('.psig-chip.is-approved').first()).toBeVisible();

    // Usage trail card is always present.
    await expect(app).toContainText('使用记录');

    // Mobile layout stays inside the viewport.
    await page.setViewportSize({ width: 390, height: 844 });
    const mobileOverflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
    expect(mobileOverflow).toBeFalsy();

    // Cleanup: delete the uploaded signature through the real API.
    const removed = await apiJson<{ status: number }>(page, `/api/signatures/${signatureId}`, { method: 'DELETE' });
    expect(removed.status).toBe(200);
    await expectNoBrowserErrors(errors, testInfo);
  });
});
