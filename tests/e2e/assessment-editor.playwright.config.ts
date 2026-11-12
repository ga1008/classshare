import { defineConfig, devices } from '@playwright/test';
import path from 'node:path';
const output = path.resolve(process.env.ASSESSMENT_EVIDENCE || '.codex-temp/evidence/assessment-editor-20261112');
export default defineConfig({
  testDir: './specs', testMatch: ['assessment-editor.spec.ts', 'exam-authoring.spec.ts', 'assessment-authoring-controls.spec.ts', 'assessment-ai-assistance.spec.ts'],
  globalSetup: './lq-s3.global-setup.ts', workers: 1, fullyParallel: false,
  timeout: 90000, expect: { timeout: 10000 }, outputDir: path.join(output, 'browser'),
  reporter: [['list'], ['json', { outputFile: path.join(output, 'browser-results.json') }]],
  use: { ...devices['Desktop Chrome'], channel: 'chrome',
    baseURL: `http://127.0.0.1:${process.env.LQ_S3_PORT || '8279'}`,
    viewport: {width: 1440, height: 1000}, screenshot: 'only-on-failure', trace: 'retain-on-failure' },
});
