import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import {
  collectBrowserErrors,
  expectNoBrowserErrors,
  loginStudent,
  loginTeacher,
  readFixture,
} from '../fixtures/p03';

test.describe('P03 materials management', () => {
  test('teacher renders materials page and uploads a temporary QA material', async ({ page }, testInfo) => {
    const fixture = readFixture();
    const errors = collectBrowserErrors(page);
    const uploadDir = path.join(fixture.runtimeRoot, 'uploads');
    fs.mkdirSync(uploadDir, { recursive: true });
    const uploadPath = path.join(uploadDir, `p03-material-${Date.now()}.md`);
    fs.writeFileSync(uploadPath, '# P03 material\n\nThis file is created only for the copied runtime database.\n', 'utf8');

    await loginTeacher(page, fixture);
    await page.goto('/manage/teaching/materials');
    await expect(page.locator('[data-lanshare-island="materials-manage-page"]')).toBeAttached();
    await expect(page.getByTestId('p03-materials-list')).toBeVisible();
    await expect(page.getByTestId('p03-materials-refresh')).toBeVisible();
    await page.getByTestId('p03-materials-refresh').click();

    const uploadResponsePromise = page.waitForResponse((response) =>
      response.url().includes('/api/materials/upload') && response.request().method() === 'POST',
    );
    await page.getByTestId('p03-materials-file-input').setInputFiles(uploadPath);
    const uploadResponse = await uploadResponsePromise;
    expect(uploadResponse.ok()).toBeTruthy();

    await expect(page.getByTestId('p03-materials-list')).toContainText(path.basename(uploadPath), { timeout: 15_000 });
    await page.getByTestId('p03-materials-search').fill('p03-material');
    await expect(page.getByTestId('p03-materials-list')).toContainText(path.basename(uploadPath));

    await expectNoBrowserErrors(errors, testInfo);
  });

  test('teacher can open the final grade transcript import and generation workflows', async ({ page }, testInfo) => {
    const fixture = readFixture();
    const errors = collectBrowserErrors(page);

    await loginTeacher(page, fixture);
    await page.goto('/manage/teaching/final-grade-transcripts');
    await expect(page.getByRole('heading', { name: '期末成绩单', exact: true })).toBeVisible();
    await expect(page.locator('[data-process-classroom-generate]')).toBeVisible();
    await expect(page.locator('[data-process-ai-import]')).toBeVisible();

    let releasePrepare!: () => void;
    const prepareGate = new Promise<void>((resolve) => {
      releasePrepare = resolve;
    });
    await page.route(/\/api\/classrooms\/\d+\/final-grade-transcript\/prepare$/, async (route) => {
      await prepareGate;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'academic_query_failed',
          ready: false,
          message: '测试中的教务名单暂不可用',
        }),
      });
    });

    await page.locator('[data-process-classroom-generate]').click();
    await expect(page.locator('#materials-classroom-generate-modal')).toBeVisible();
    const offering = page.locator('[data-materials-final-grade-offering-id]').first();
    await expect(offering).toBeVisible();
    await offering.click();
    await expect(page.locator('#materials-final-grade-wizard')).toBeVisible();
    await expect(page.locator('#materials-final-grade-wizard')).toContainText('仅展示 1 份教师需要的期末成绩单');
    await expect(page.locator('#materials-final-grade-wizard')).toContainText('教务考试名单');
    await expect(page.locator('#materials-final-grade-refresh-btn')).toBeDisabled();
    await expect(page.locator('#materials-final-grade-refresh-btn')).toHaveClass(/is-loading/);
    await expect(page.locator('#materials-classroom-generate-submit-btn')).toBeDisabled();
    await expect(page.locator('#materials-classroom-generate-submit-btn')).toHaveClass(/is-loading/);
    releasePrepare();
    await expect(page.locator('#materials-final-grade-source-grid')).toContainText('来源状态尚未判定');
    await expect(page.locator('#materials-final-grade-source-grid')).not.toContainText('缺少来源');
    await expect(page.locator('#materials-final-grade-refresh-btn')).toBeEnabled();

    await page.unroute(/\/api\/classrooms\/\d+\/final-grade-transcript\/prepare$/);
    await page.route(/\/api\/classrooms\/\d+\/final-grade-transcript\/prepare$/, async (route) => {
      const requestBody = route.request().postDataJSON() as { exam_grade_record_id?: number | null };
      const manualExam = Number(requestBody?.exam_grade_record_id || 0) === 72;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'success',
          verification_status: manualExam ? 'ready' : 'exam_source_incomplete',
          ready: manualExam,
          message: manualExam ? '两份来源已核对完成。' : '请选择可用的考核登分表。',
          roster: {
            ready: true,
            student_count: 2,
            signature: 'a'.repeat(64),
            students: [
              { row_order: 1, student_number: '20240101', student_name: '学生一' },
              { row_order: 2, student_number: '20240102', student_name: '学生二' },
            ],
          },
          sources: {
            ordinary_grade_record: {
              ready: true,
              record_found: true,
              record_id: 70,
              label: '平时成绩表',
              source_name: '2025-2026-2 平时成绩表.xlsx',
              matched_count: 2,
              selection_mode: 'automatic',
              similarity_score: 100,
            },
            exam_grade_record: manualExam ? {
              ready: true,
              record_found: true,
              record_id: 72,
              label: '考核登分表',
              source_name: '导入-考核登分表-软工2302班.xlsx',
              matched_count: 2,
              selection_mode: 'manual',
              similarity_score: 95,
              context_mismatches: [],
            } : {
              ready: false,
              record_found: false,
              label: '考核登分表',
              message: '未找到严格对应材料。',
              generate_url: '/manage/teaching/exam-grade-records',
            },
          },
          roster_sync: { status: 'success', cache_hit: true, freshness: { remaining_seconds: 1200 } },
        }),
      });
    });
    await page.route(/\/api\/classrooms\/\d+\/final-grade-transcript\/source-candidates\?document_type=exam_grade_record$/, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'success',
          items: [
            {
              record_id: 72,
              document_type: 'exam_grade_record',
              title: '导入-考核登分表-软工2302班.xlsx',
              similarity_score: 95,
              selectable: true,
              matched_count: 2,
              fields: {
                academic_year: '2025-2026',
                semester: '第二学期',
                course_name: '计算机网络',
                class_name: '软工2302班',
              },
              context_mismatches: [],
            },
            {
              record_id: 73,
              document_type: 'exam_grade_record',
              title: '错误名单.xlsx',
              similarity_score: 61,
              selectable: false,
              matched_count: 1,
              conflict_count: 1,
              selection_message: '考核登分表有 1 名学生学号相同但姓名不一致。',
              fields: { course_name: '计算机网络', class_name: '软工2303班' },
              context_mismatches: ['班级：材料“软工2303班” / 当前“软工2302班”'],
            },
          ],
        }),
      });
    });
    await page.locator('#materials-final-grade-refresh-btn').click();
    await expect(page.locator('#materials-final-grade-source-grid')).toContainText('手动选择');
    await page.locator('[data-materials-final-grade-manual-source="exam_grade_record"]').click();
    await expect(page.locator('.lp-modal')).toContainText('按近似度从高到低');
    const manualCandidates = page.locator('[data-materials-final-grade-source-record-id]');
    await expect(manualCandidates).toHaveCount(2);
    await expect(manualCandidates.nth(1)).toBeDisabled();
    await manualCandidates.nth(0).click();
    await expect(page.locator('#materials-final-grade-source-grid')).toContainText('导入-考核登分表-软工2302班.xlsx');
    await expect(page.locator('#materials-final-grade-source-grid')).toContainText('已就绪 · 手动选择');

    await page.setViewportSize({ width: 390, height: 844 });
    await expect(page.locator('#materials-final-grade-wizard')).toBeVisible();
    await expect(page.locator('#materials-final-grade-refresh-btn')).toBeVisible();
    const hasHorizontalOverflow = await page.evaluate(
      () => document.documentElement.scrollWidth > window.innerWidth + 1,
    );
    expect(hasHorizontalOverflow).toBeFalsy();
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.locator('#materials-classroom-generate-modal [data-dismiss="modal"]').first().click();

    await page.locator('[data-process-ai-import]').click();
    await expect(page.locator('#materials-ai-import-modal')).toBeVisible();
    await expect(page.locator('#materials-final-grade-import-context')).toBeVisible();
    await expect(page.locator('#materials-final-grade-import-year')).toHaveAttribute('required', '');
    await expect(page.locator('#materials-final-grade-import-semester')).toHaveAttribute('required', '');
    await expect(page.locator('#materials-final-grade-import-offering')).toBeVisible();
    await expect(page.locator('#materials-ai-import-format-hint')).toContainText('学校模板 Excel');

    await expectNoBrowserErrors(errors, testInfo);
  });

  test('student cannot open teacher materials management page', async ({ page }, testInfo) => {
    const fixture = readFixture();
    const errors = collectBrowserErrors(page);

    await loginStudent(page, fixture);
    await page.goto('/manage/teaching/materials', { waitUntil: 'domcontentloaded' });
    await page.waitForLoadState('networkidle').catch(() => undefined);
    await expect(page.locator('[data-lanshare-island="materials-manage-page"]')).toHaveCount(0);
    await expect(page.getByTestId('p03-materials-list')).toHaveCount(0);

    await page.goto('/manage/teaching/final-grade-transcripts', { waitUntil: 'domcontentloaded' });
    await page.waitForLoadState('networkidle').catch(() => undefined);
    await expect(page.locator('[data-lanshare-island="materials-manage-page"]')).toHaveCount(0);
    await expect(page.getByRole('heading', { name: '期末成绩单', exact: true })).toHaveCount(0);

    await expectNoBrowserErrors(errors, testInfo);
  });
});
