import { defineConfig, devices } from '@playwright/test';
import path from 'node:path';

const output = path.resolve(process.env.LQ_S4_OUTPUT || '.codex-temp/lq-s4-shell-performance');
export default defineConfig({
  testDir: './specs', testMatch: 'lq-s4-shell-performance.spec.ts',
  globalSetup: './lq-s3.global-setup.ts', // Same exact-runtime, exclusive account lock.
  workers: 1, fullyParallel: false, retries: 0, timeout: 240000,
  expect: { timeout: 10000 }, outputDir: path.join(output, 'test-results'),
  reporter: [['list'], ['json', { outputFile: path.join(output, 'results.json') }]],
  use: { ...devices['Desktop Chrome'], channel: 'chrome',
    baseURL: `http://127.0.0.1:${process.env.LQ_S4_PORT || '8166'}`,
    // CDP traces are attached by the spec. Playwright DOM snapshots inject
    // target-marking work into the same measured renderer at 4x CPU.
    viewport: { width: 1440, height: 900 }, screenshot: 'only-on-failure', trace: 'off' },
});
