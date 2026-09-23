import { defineConfig, devices } from '@playwright/test';
import path from 'node:path';

export default defineConfig({
  testDir: './specs', testMatch: ['student-assessment-material.spec.ts'],
  globalSetup: './lq-s3.global-setup.ts',
  workers: 1, fullyParallel: false, timeout: 90000,
  outputDir: path.resolve(process.env.LQ_STUDENT_OUTPUT || '.codex-temp/lq-student-assessment/test-results'),
  use: { ...devices['Desktop Chrome'], channel: 'chrome',
    baseURL: `http://127.0.0.1:${process.env.LQ_STUDENT_PORT || '8267'}`,
    screenshot: 'only-on-failure', trace: 'retain-on-failure' },
});
