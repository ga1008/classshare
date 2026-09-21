import { defineConfig, devices } from '@playwright/test';
import path from 'node:path';

// Explicitly started owned fixture only; never start the main app or seed a DB.
const output = path.resolve(process.env.LQ_S4_ROLLBACK_OUTPUT || '.codex-temp/lq-s4-rollback');
export default defineConfig({
  testDir: './specs', testMatch: ['lq-s4-rollback.spec.ts'],
  workers: 1, fullyParallel: false, timeout: 90000, expect: { timeout: 12000 },
  outputDir: path.join(output, 'test-results'),
  reporter: [['list'], ['json', { outputFile: path.join(output, 'results.json') }]],
  use: { ...devices['Desktop Chrome'], channel: 'chrome', baseURL: 'http://127.0.0.1:8167',
    viewport: { width: 1440, height: 900 }, screenshot: 'only-on-failure', trace: 'retain-on-failure' },
});
