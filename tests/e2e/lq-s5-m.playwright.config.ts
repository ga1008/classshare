import { defineConfig, devices } from '@playwright/test';
import path from 'node:path';

// S5 M package (the "我的" domain inside the manage-pages family). Mirrors the
// F package config: the shared lq-s4 config restricts testMatch to the shell
// specs and requires the S3-extended fixture through its globalSetup, which
// these routes do not need. Ports 8207 (family on) / 8208 (family off) per
// runbook §8.
const output = path.resolve(process.env.LQ_S5_OUTPUT || '.codex-temp/claude-s5-m-e2e');
export default defineConfig({
  testDir: './specs', testMatch: ['lq-s5-me.spec.ts'],
  workers: 1, fullyParallel: false, timeout: 120000, expect: { timeout: 10000 },
  outputDir: path.join(output, 'test-results'),
  reporter: [['list'], ['json', { outputFile: path.join(output, 'results.json') }]],
  use: { ...devices['Desktop Chrome'], channel: 'chrome',
    baseURL: `http://127.0.0.1:${process.env.LQ_S5_PORT || '8207'}`,
    viewport: { width: 1440, height: 900 }, screenshot: 'only-on-failure', trace: 'retain-on-failure' },
});
