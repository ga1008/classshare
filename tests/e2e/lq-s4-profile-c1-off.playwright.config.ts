import { defineConfig, devices } from '@playwright/test';
import path from 'node:path';

const output = path.resolve(process.env.LQ_S4_C1_OFF_OUTPUT || '.codex-temp/claude-s4-c1-e2e-off');
export default defineConfig({
  testDir: './specs', testMatch: ['lq-s4-profile-family-off.spec.ts'], workers: 1, fullyParallel: false,
  timeout: 90000, expect: { timeout: 12000 }, outputDir: path.join(output, 'test-results'),
  reporter: [['list'], ['json', { outputFile: path.join(output, 'results.json') }]],
  use: { ...devices['Desktop Chrome'], channel: 'chrome', baseURL: 'http://127.0.0.1:8176',
    viewport: { width: 1440, height: 900 }, screenshot: 'only-on-failure', trace: 'retain-on-failure' },
});
