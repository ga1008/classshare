import { defineConfig, devices } from '@playwright/test';
import path from 'node:path';

const output = path.resolve(process.env.LQ_S7_B_OUTPUT || '.codex-temp/claude-s7-b-e2e');
export default defineConfig({
  testDir: './specs', testMatch: ['lq-s7-backdrop.spec.ts'], workers: 1, fullyParallel: false,
  timeout: 120000, expect: { timeout: 12000 }, outputDir: path.join(output, 'test-results'),
  reporter: [['list'], ['json', { outputFile: path.join(output, 'results.json') }]],
  use: {
    ...devices['Desktop Chrome'], channel: 'chrome',
    baseURL: `http://127.0.0.1:${process.env.LQ_S7_B_PORT || '8233'}`,
    viewport: { width: 1440, height: 900 }, screenshot: 'only-on-failure', trace: 'retain-on-failure',
  },
});
