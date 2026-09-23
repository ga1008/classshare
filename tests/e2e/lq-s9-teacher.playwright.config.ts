import { defineConfig, devices } from '@playwright/test';
import path from 'node:path';

// Teacher and manage views in dark mode with the backdrop on. The legacy sweep
// only ever ran against student routes, so the conversions in classroom.css and
// manage_classes.css had no coverage at all.
const output = path.resolve(process.env.LQ_S9_TEACHER_OUTPUT || '.codex-temp/claude-s9-teacher-e2e');
export default defineConfig({
  testDir: './specs', testMatch: ['lq-s9-teacher-dark.spec.ts'],
  workers: 1, fullyParallel: false, timeout: 120000, expect: { timeout: 10000 },
  outputDir: path.join(output, 'test-results'),
  reporter: [['list'], ['json', { outputFile: path.join(output, 'results.json') }]],
  use: {
    ...devices['Desktop Chrome'], channel: 'chrome',
    baseURL: `http://127.0.0.1:${process.env.LQ_S9_TEACHER_PORT || '8271'}`,
    viewport: { width: 1440, height: 900 }, screenshot: 'only-on-failure', trace: 'retain-on-failure',
  },
});
