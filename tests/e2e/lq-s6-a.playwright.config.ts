import { defineConfig, devices } from '@playwright/test';
import path from 'node:path';

// S6 A package (attendance report runtime tables, manage-pages family). Own
// config because every shared lq config restricts testMatch to its own package.
// Targets tests/e2e/specs/lq-s6-attendance*.spec.ts against this package's own
// synthetic runtime and ports (runbook §10, 8215 family-on / 8216 family-off).
const output = path.resolve(process.env.LQ_S6_OUTPUT || '.codex-temp/claude-s6-a-e2e');
export default defineConfig({
  testDir: './specs', testMatch: ['lq-s6-attendance*.spec.ts'],
  workers: 1, fullyParallel: false, timeout: 120000, expect: { timeout: 10000 },
  outputDir: path.join(output, 'test-results'),
  reporter: [['list'], ['json', { outputFile: path.join(output, 'results.json') }]],
  use: {
    ...devices['Desktop Chrome'], channel: 'chrome',
    baseURL: `http://127.0.0.1:${process.env.LQ_S6_PORT || '8215'}`,
    viewport: { width: 1440, height: 900 }, screenshot: 'only-on-failure', trace: 'retain-on-failure',
  },
});
