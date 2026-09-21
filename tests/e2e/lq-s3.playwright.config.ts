import { defineConfig, devices } from '@playwright/test';
import path from 'node:path';

// Start serve_ui_v3.py explicitly with a new S3 fixture and pilot flag. No
// webServer hook may seed/copy a configured database as a side effect of testing.
const output = path.resolve(process.env.LQ_S3_OUTPUT || '.codex-temp/lq-s3-app-e2e');
export default defineConfig({
  testDir: './specs',
  globalSetup: './lq-s3.global-setup.ts',
  testMatch: [
    'manage-pilot.spec.ts', 'report-card-pilot.spec.ts', 'exam-authoring.spec.ts',
    'assignment-student-draft.spec.ts', 'exam-take.spec.ts', 'grading-concurrency.spec.ts',
    'grading-return-resubmit.spec.ts', 'wrong-summary.spec.ts', 'layout-stability.spec.ts',
    'pilot-fallback.spec.ts', 'pilot-performance.spec.ts', 'pilot-compat.spec.ts',
    'teacher-app-shell.spec.ts', 'manual-grade-revisions.spec.ts',
    'assignment-submission.spec.ts', 'assignment-classification-modal.spec.ts',
  ],
  workers: 1, fullyParallel: false, timeout: 90000, expect: { timeout: 10000 },
  outputDir: path.join(output, 'test-results'),
  reporter: [['list'], ['json', { outputFile: path.join(output, 'results.json') }]],
  use: { ...devices['Desktop Chrome'], channel: 'chrome',
    baseURL: `http://127.0.0.1:${process.env.LQ_S3_PORT || '8160'}`,
    viewport: { width: 1440, height: 900 }, screenshot: 'only-on-failure', trace: 'retain-on-failure' },
});
