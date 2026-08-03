import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import {
  apiJson,
  collectBrowserErrors,
  expectNoBrowserErrors,
  loginTeacher,
  readFixture,
} from '../fixtures/p03';

test.describe('material-scoped signature points', () => {
  test('multi-signature order, request flow, end flow, and mobile layout stay coherent', async ({ page }, testInfo) => {
    const fixture = readFixture();
    const errors = collectBrowserErrors(page);
    await loginTeacher(page, fixture);

    const created = await apiJson<{ status: number; body: { id: string } }>(page, '/api/assessment-plans', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        title: 'P03 签名点验收计划表',
        fields: {
          course_name: '服务器配置与管理',
          class_name: '软工2406班',
          examiner_name: fixture.teacher.name,
          reviewer_name: fixture.otherTeacher.name,
        },
        items: [
          { assessment_form: '操作考试', content: '服务器部署与验证', score: 60 },
          { assessment_form: '综合报告', content: '配置说明与复盘', score: 40 },
        ],
      }),
    });
    expect(created.status).toBe(200);
    const planId = created.body.id;

    const examinerPoint = 'assessment_plan.examiner_signature';
    const reviewerPoint = 'assessment_plan.reviewer_signature';
    const labels: Record<string, string> = {
      [examinerPoint]: '课程考核计划表 · 命题教师签名处',
      [reviewerPoint]: '课程考核计划表 · 系（教研室）主任审核签名处',
    };
    const boundByPoint: Record<string, number[]> = {
      [examinerPoint]: [],
      [reviewerPoint]: [],
    };
    let activeReviewerFlow: any = null;
    let createdRequestOrder: number[] = [];

    const signature = (id: number, name: string, options: { usable?: boolean; requestable?: boolean } = {}) => ({
      id,
      name: `${name}签名`,
      subject_name: name,
      owner_name: `${name}本人`,
      owner_role: 'teacher',
      scope_label: '软件工程系',
      can_use: Boolean(options.usable),
      can_request: Boolean(options.requestable),
      authorization_mode: options.usable ? 'self' : '',
    });
    const signaturesByPoint: Record<string, any[]> = {
      [examinerPoint]: [
        signature(11, '命题教师甲', { usable: true }),
        signature(12, '命题教师乙', { usable: true }),
      ],
      [reviewerPoint]: [
        signature(21, '审核教师甲', { requestable: true }),
        signature(22, '审核教师乙', { requestable: true }),
      ],
    };

    await page.route('**/assessment-plan/*/preview*', async (route) => {
      await route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: '<!doctype html><title>QA preview</title><p>同源预览占位</p>' });
    });
    await page.route('**/api/signatures/points/**', async (route) => {
      const url = new URL(route.request().url());
      const parts = url.pathname.split('/');
      const point = decodeURIComponent(parts[4] || '');
      const items = signaturesByPoint[point] || [];
      if (route.request().method() === 'POST' && url.pathname.endsWith('/flows')) {
        const body = route.request().postDataJSON() as { signature_ids?: number[] };
        createdRequestOrder = body.signature_ids || [];
        activeReviewerFlow = {
          id: 501,
          function_point_key: reviewerPoint,
          material_type: 'assessment_plan',
          material_id: planId,
          material_revision: planId,
          material_label: 'P03 签名点验收计划表',
          status: 'pending',
          request_note: '',
          items: createdRequestOrder.map((id, index) => ({
            id: 800 + index,
            signature_id: id,
            signature_name: items.find((item) => item.id === id)?.subject_name || String(id),
            display_order: index,
            status: 'pending',
            request_id: 900 + index,
            request: {
              reviewers: [{ name: `审批人${index + 1}`, kind: 'signer', status: 'pending' }],
            },
          })),
        };
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'success', flow: activeReviewerFlow }) });
        return;
      }
      const usable = items.filter((item) => item.can_use);
      const requestable = items.filter((item) => item.can_request);
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'success',
          point: { key: point, label: labels[point] || point },
          material: { type: 'assessment_plan', id: planId, revision: planId, label: 'P03 签名点验收计划表' },
          signatures: items,
          usable_signatures: usable,
          requestable_signatures: requestable,
          selected_signature_ids: boundByPoint[point] || [],
          active_flow: point === reviewerPoint ? activeReviewerFlow : null,
        }),
      });
    });
    await page.route('**/api/signatures/point-flows/501/end', async (route) => {
      activeReviewerFlow = null;
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'success', flow_id: 501 }) });
    });
    await page.route(`**/api/assessment-plans/${planId}/signature`, async (route) => {
      const body = route.request().postDataJSON() as { role: 'examiner' | 'reviewer'; signature_ids?: number[] };
      const point = body.role === 'examiner' ? examinerPoint : reviewerPoint;
      boundByPoint[point] = body.signature_ids || [];
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          ok: true,
          examiner_signature_ids: boundByPoint[examinerPoint],
          reviewer_signature_ids: boundByPoint[reviewerPoint],
          examiner_signatures: [],
          reviewer_signatures: [],
        }),
      });
    });

    try {
      await page.goto(`/assessment-plan/${planId}/edit`);
      const points = page.locator('.spw-point');
      await expect(points).toHaveCount(2);
      await expect(points.nth(0)).toContainText(labels[examinerPoint]);
      await expect(points.nth(1)).toContainText(labels[reviewerPoint]);
      await expect(points.getByRole('button', { name: '申请签名' })).toHaveCount(2);

      const examiner = page.locator('[data-ap-signature-point="examiner"]');
      const examinerSelect = examiner.locator('[data-spw-available]');
      await examinerSelect.selectOption('11');
      await examiner.locator('[data-spw-add]').click();
      await examinerSelect.selectOption('12');
      await examiner.locator('[data-spw-add]').click();
      const bindingResponse = page.waitForResponse((response) => (
        response.url().includes(`/api/assessment-plans/${planId}/signature`)
        && response.request().method() === 'PUT'
      ));
      await examiner.locator('[data-spw-selected="12"] [data-spw-move="up"]').click();
      await bindingResponse;
      await expect(examiner.locator('[data-spw-selected]').nth(0)).toContainText('命题教师乙');
      await expect(examiner.locator('[data-spw-selected]').nth(1)).toContainText('命题教师甲');
      expect(boundByPoint[examinerPoint]).toEqual([12, 11]);

      const reviewer = page.locator('[data-ap-signature-point="reviewer"]');
      await reviewer.locator('[data-spw-apply]').click();
      const dialog = page.locator('dialog.spw-dialog[open]');
      await expect(dialog.getByRole('heading', { name: labels[reviewerPoint] })).toBeVisible();
      await expect(dialog).toContainText('新建签名申请');
      await dialog.locator('input[value="22"]').check();
      await dialog.locator('input[value="21"]').check();
      await expect(dialog.locator('input[value="22"] + .spw-candidate-order')).toHaveText('1');
      await expect(dialog.locator('input[value="21"] + .spw-candidate-order')).toHaveText('2');
      await dialog.locator('[data-spw-create]').click();
      await expect(dialog).toContainText('签名申请流程');
      await expect(dialog).toContainText('审批人1 · 待审批');
      expect(createdRequestOrder).toEqual([22, 21]);
      await dialog.locator('[data-spw-end]').click();
      await expect(dialog).toContainText('新建签名申请');
      await expect(dialog.locator('[data-spw-create]')).toBeVisible();

      const artifactDir = path.join(process.cwd(), '.codex-temp', 'signature-workflow-qa');
      fs.mkdirSync(artifactDir, { recursive: true });
      await page.screenshot({ path: path.join(artifactDir, 'signature-points-desktop.png'), fullPage: true });
      await page.setViewportSize({ width: 390, height: 844 });
      await expect(page.locator('dialog.spw-dialog[open]')).toBeVisible();
      const mobileOverflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
      expect(mobileOverflow).toBeFalsy();
      await page.screenshot({ path: path.join(artifactDir, 'signature-points-mobile.png'), fullPage: true });
      await expectNoBrowserErrors(errors, testInfo);
    } finally {
      await apiJson(page, `/api/assessment-plans/${planId}`, { method: 'DELETE' }).catch(() => undefined);
    }
  });

  test('academic analysis editor exposes two independent responsive signature points', async ({ page }, testInfo) => {
    const fixture = readFixture();
    const errors = collectBrowserErrors(page);
    await loginTeacher(page, fixture);

    const batchId = 'p03-signature-analysis';
    const recordId = 8801;
    const pointLabels: Record<string, string> = {
      'academic_final_material.exam_analysis.department_review_signature': '试卷分析表 · 系（教研室）审核意见签名处',
      'academic_final_material.exam_analysis.dean_review_signature': '试卷分析表 · 教学院长审核意见签名处',
    };
    await page.route('**/api/academic-final-materials?document_type=academic_exam_analysis', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'success',
          items: [{
            id: batchId,
            class_offering_id: fixture.classOfferingId,
            course_name: '服务器配置与管理',
            teaching_class_name: '软工2406班',
            academic_year: '2025-2026',
            academic_term: '第2学期',
            synced_at: '2026-08-03T10:00:00',
            grade_entry_status: '已提交',
            validation_status: 'passed',
            validation: { passed: true, errors: [] },
            sync_status: 'completed',
            record_id: recordId,
            edit_state: { analysis_complete: false },
            preview_url: '/qa/analysis-preview',
            export_url: '/qa/analysis-export',
          }],
        }),
      });
    });
    await page.route(`**/api/academic-final-materials/${batchId}`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'success',
          batch: { id: batchId },
          grade: null,
          analysis: {
            id: recordId,
            preview_url: '/qa/analysis-preview',
            fields: {
              course_name: '服务器配置与管理',
              class_name: '软工2406班',
              teacher_name: fixture.teacher.name,
              proposition_form: '教师组题',
              exam_form: '闭卷',
              separate_teaching_exam: '否',
              course_nature: '必修',
              marking_form: '本人阅卷',
            },
            structured: { analysis_text: '本次考核成绩分布总体合理，后续继续强化综合配置与故障排查训练。' },
          },
        }),
      });
    });
    await page.route('**/api/signatures/points/**/state?*', async (route) => {
      const url = new URL(route.request().url());
      const point = decodeURIComponent(url.pathname.split('/')[4] || '');
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'success',
          point: { key: point, label: pointLabels[point] || point },
          material: { type: 'academic_final_material', id: String(recordId), revision: 'qa-revision', label: '试卷分析表 · 服务器配置与管理 · 软工2406班' },
          signatures: [],
          usable_signatures: [],
          requestable_signatures: [],
          selected_signature_ids: [],
          active_flow: null,
        }),
      });
    });

    await page.goto('/manage/teaching/academic-exam-analyses');
    await page.locator(`[data-afm-edit="${batchId}"]`).click();
    const editor = page.locator('[data-afm-editor-dialog][open]');
    await expect(editor).toBeVisible();
    await expect(editor.locator('.spw-point')).toHaveCount(2);
    await expect(editor).toContainText(pointLabels['academic_final_material.exam_analysis.department_review_signature']);
    await expect(editor).toContainText(pointLabels['academic_final_material.exam_analysis.dean_review_signature']);
    await expect(editor.getByRole('button', { name: '申请签名' })).toHaveCount(2);

    const artifactDir = path.join(process.cwd(), '.codex-temp', 'signature-workflow-qa');
    fs.mkdirSync(artifactDir, { recursive: true });
    await editor.locator('.spw-point').first().scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(artifactDir, 'academic-signature-points-desktop.png'), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(editor).toBeVisible();
    await editor.locator('.spw-point').first().scrollIntoViewIfNeeded();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
    expect(overflow).toBeFalsy();
    await page.screenshot({ path: path.join(artifactDir, 'academic-signature-points-mobile.png'), fullPage: true });
    await expectNoBrowserErrors(errors, testInfo);
  });
});
