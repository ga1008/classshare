import { defineConfig, devices } from '@playwright/test';
import path from 'node:path';

const output = path.resolve(process.env.LQ_UI_REPAIR_OUTPUT || '.codex-temp/lq-ui-20261008-qa');
export default defineConfig({
  testDir: './specs', testMatch: 'lq-ui-repair.spec.ts',
  globalSetup: './lq-s3.global-setup.ts',
  workers: 1, fullyParallel: false, retries: 0, timeout: 90_000,
  outputDir: path.join(output, 'test-results'),
  reporter: [['list'], ['json', { outputFile: path.join(output, 'playwright-results.json') }]],
  use: { ...devices['Desktop Chrome'], channel: 'chrome',
    baseURL: `http://127.0.0.1:${process.env.LQ_UI_REPAIR_PORT || '8358'}`,
    screenshot: 'only-on-failure', trace: 'retain-on-failure', actionTimeout: 10_000 },
  projects: [
    { name: 'desktop', use: { viewport: { width: 1440, height: 980 } } },
    { name: 'touch', use: { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true } },
  ],
});
