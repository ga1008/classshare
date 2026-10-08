import { defineConfig, devices } from '@playwright/test';
import path from 'node:path';
const out = path.resolve(process.env.SCHEDULE_MATERIAL_OUTPUT || '.codex-temp/schedule-material-20261008-qa');
export default defineConfig({
  testDir: './specs', testMatch: 'schedule-material-rebind.spec.ts', workers: 1,
  fullyParallel: false, retries: 0, timeout: 180_000,
  outputDir: path.join(out, 'test-results'),
  reporter: [['list'], ['json', { outputFile: path.join(out, 'results.json') }]],
  use: { ...devices['Desktop Chrome'], channel: 'chrome', baseURL: 'http://127.0.0.1:8358',
    viewport: { width: 1440, height: 980 }, screenshot: 'only-on-failure', trace: 'retain-on-failure' },
});
