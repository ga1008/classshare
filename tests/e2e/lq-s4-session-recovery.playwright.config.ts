import { defineConfig, devices } from '@playwright/test';
import path from 'node:path';

const output = path.resolve(process.env.LQ_S4_OUTPUT || '.codex-temp/lq-s4-session-recovery-e2e');
export default defineConfig({
  testDir: './specs', testMatch: ['lq-s4-session-recovery.spec.ts'],
  workers: 1, fullyParallel: false, timeout: 90000, expect: { timeout: 10000 },
  outputDir: path.join(output, 'test-results'),
  reporter: [['list'], ['json', { outputFile: path.join(output, 'results.json') }]],
  use: { ...devices['Desktop Chrome'], channel: 'chrome',
    baseURL: `http://127.0.0.1:${process.env.LQ_S4_PORT || '8179'}`,
    screenshot: 'only-on-failure', trace: 'retain-on-failure' },
});
