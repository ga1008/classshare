import { defineConfig, devices } from '@playwright/test';
export default defineConfig({
  testDir: './specs', testMatch: 'lq-unified-material.spec.ts',
  globalSetup: './lq-s3.global-setup.ts', workers: 1, fullyParallel: false,
  timeout: 180000, expect: { timeout: 12000 },
  outputDir: '../../.codex-temp/glass-unified-browser/results', reporter: 'list',
  use: { ...devices['Desktop Chrome'], channel: 'chrome',
    baseURL: `http://127.0.0.1:${process.env.LQ_UNIFIED_PORT || '8291'}`,
    trace: 'retain-on-failure', screenshot: 'only-on-failure' },
});
