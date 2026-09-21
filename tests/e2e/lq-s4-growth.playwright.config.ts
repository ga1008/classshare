import { defineConfig, devices } from '@playwright/test';
import path from 'node:path';

// S4 package E (growth family) config: same shape as lq-s4.playwright.config.ts
// but pointed at lq-s4-growth.spec.ts only, per the runbook's allowance to
// "新建/指定的 config" for a package's own spec.
const output = path.resolve(process.env.LQ_S4_OUTPUT || '.codex-temp/claude-s4-e-e2e');
export default defineConfig({
  testDir: './specs', testMatch: ['lq-s4-growth.spec.ts'],
  globalSetup: './lq-s3.global-setup.ts',
  workers: 1, fullyParallel: false, timeout: 120000, expect: { timeout: 10000 },
  outputDir: path.join(output, 'test-results'),
  reporter: [['list'], ['json', { outputFile: path.join(output, 'results.json') }]],
  use: { ...devices['Desktop Chrome'], channel: 'chrome',
    baseURL: `http://127.0.0.1:${process.env.LQ_S4_PORT || '8181'}`,
    viewport: { width: 1440, height: 900 }, screenshot: 'only-on-failure', trace: 'retain-on-failure' },
});
