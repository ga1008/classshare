import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './specs', testMatch: ['lq-teacher-materials.spec.ts', 'manual-grade-revisions.spec.ts', 'grading-concurrency.spec.ts'],
  workers: 1, fullyParallel: false, timeout: 90000, expect: { timeout: 10000 },
  outputDir: '../../.codex-temp/glass-teacher-materials-results', reporter: 'list',
  use: { ...devices['Desktop Chrome'], channel: 'chrome',
    baseURL: `http://127.0.0.1:${process.env.LQ_TEACHER_PORT || '8293'}`,
    viewport: { width: 1440, height: 900 }, screenshot: 'only-on-failure', trace: 'retain-on-failure' },
});
