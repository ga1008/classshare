import { defineConfig, devices } from '@playwright/test';
import path from 'node:path';

// S5 X package (academic domain, manage-pages family). Mirrors
// lq-s4-f.playwright.config.ts: the shared lq-s4.playwright.config.ts restricts
// testMatch to the shell specs and its globalSetup demands the S3-extended
// fixture (readS3Fixture), which these routes do not need. Ports 8205 (family
// on) / 8206 (family off) per runbook §8, X row.
const output = path.resolve(process.env.LQ_S5_OUTPUT || '.codex-temp/claude-s5-x-e2e');
export default defineConfig({
  testDir: './specs', testMatch: ['lq-s5-academic*.spec.ts'],
  workers: 1, fullyParallel: false, timeout: 120000, expect: { timeout: 10000 },
  outputDir: path.join(output, 'test-results'),
  reporter: [['list'], ['json', { outputFile: path.join(output, 'results.json') }]],
  use: { ...devices['Desktop Chrome'], channel: 'chrome',
    baseURL: `http://127.0.0.1:${process.env.LQ_S5_PORT || '8205'}`,
    viewport: { width: 1440, height: 900 }, screenshot: 'only-on-failure', trace: 'retain-on-failure' },
});
