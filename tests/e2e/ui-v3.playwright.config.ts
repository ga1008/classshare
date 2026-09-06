import { defineConfig, devices } from '@playwright/test';
import path from 'node:path';

// The caller starts serve_ui_v3.py explicitly on an asserted synthetic fixture.
// Never fall back to the production application or its startup workers.
const output = path.resolve(process.env.UI_V3_OUTPUT || 'artifacts/home-classroom-ui-v3-2026-09-07/e2e');
export default defineConfig({
  testDir: './specs', testMatch: [
    'home-classroom-ui-v3.spec.ts', 'home-classroom-workspace.spec.ts',
    'dashboard-schedule.spec.ts', 'classroom.spec.ts', 'assignment-submission.spec.ts',
  ],
  workers: 1, fullyParallel: false, timeout: 60000,
  expect: { timeout: 10000 },
  outputDir: path.join(output, 'test-results'),
  reporter: [['list'], ['json', { outputFile: path.join(output, 'results.json') }]],
  use: {
    ...devices['Desktop Chrome'], channel: 'chrome',
    baseURL: `http://127.0.0.1:${process.env.UI_V3_PORT || '8152'}`,
    viewport: { width: 1440, height: 900 },
    screenshot: 'only-on-failure', trace: 'retain-on-failure',
  },
});
