import { defineConfig, devices } from '@playwright/test';
import path from 'node:path';

// S6 G package (公文列表表格运行时拼装迁移, manage-pages family). Ports 8213
// (family on) / 8214 (family off) per runbook §10, G row. Mirrors
// lq-s5-x.playwright.config.ts: the shared lq-s4.playwright.config.ts restricts
// testMatch to the shell specs and its globalSetup demands the S3-extended
// fixture (readS3Fixture), which this route does not need.
const output = path.resolve(process.env.LQ_S6_OUTPUT || '.codex-temp/claude-s6-g-e2e');
export default defineConfig({
  testDir: './specs', testMatch: ['lq-s6-gongwen*.spec.ts'],
  workers: 1, fullyParallel: false, timeout: 120000, expect: { timeout: 10000 },
  outputDir: path.join(output, 'test-results'),
  reporter: [['list'], ['json', { outputFile: path.join(output, 'results.json') }]],
  use: {
    ...devices['Desktop Chrome'], channel: 'chrome',
    baseURL: `http://127.0.0.1:${process.env.LQ_S6_PORT || '8213'}`,
    viewport: { width: 1440, height: 900 }, screenshot: 'only-on-failure', trace: 'retain-on-failure',
  },
});
