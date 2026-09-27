import { defineConfig, devices } from '@playwright/test';
import path from 'node:path';

const output = path.resolve(process.env.LQ_PLATFORM_AUDIT_OUTPUT || '.codex-temp/lq-platform-browser-audit');
export default defineConfig({
  testDir: './specs', testMatch: 'lq-platform-component-audit.spec.ts',
  globalSetup: './lq-s3.global-setup.ts', // Exclusive owned-fixture session lock.
  workers: 1, fullyParallel: false, retries: 0, timeout: 600_000,
  outputDir: path.join(output, 'test-results'),
  reporter: [['list'], ['json', { outputFile: path.join(output, 'playwright-results.json') }]],
  use: { ...devices['Desktop Chrome'], channel: 'chrome',
    baseURL: `http://127.0.0.1:${process.env.LQ_PLATFORM_AUDIT_PORT || '8341'}`,
    screenshot: 'only-on-failure', trace: 'retain-on-failure', actionTimeout: 10_000 },
});
