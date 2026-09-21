import { defineConfig, devices } from '@playwright/test';
import path from 'node:path';

// F package (manage-pages) own config: the shared lq-s4.playwright.config.ts
// restricts testMatch to the shell specs and its globalSetup requires the
// S3-extended fixture (readS3Fixture) which this package's routes do not
// need. This config targets only tests/e2e/specs/lq-s4-manage-pages.spec.ts
// against the F package's own synthetic runtime/port (runbook §6, port 8183).
const output = path.resolve(process.env.LQ_S4_OUTPUT || '.codex-temp/claude-s4-f-e2e');
export default defineConfig({
  testDir: './specs', testMatch: ['lq-s4-manage-pages.spec.ts'],
  workers: 1, fullyParallel: false, timeout: 120000, expect: { timeout: 10000 },
  outputDir: path.join(output, 'test-results'),
  reporter: [['list'], ['json', { outputFile: path.join(output, 'results.json') }]],
  use: { ...devices['Desktop Chrome'], channel: 'chrome',
    baseURL: `http://127.0.0.1:${process.env.LQ_S4_PORT || '8183'}`,
    viewport: { width: 1440, height: 900 }, screenshot: 'only-on-failure', trace: 'retain-on-failure' },
});
