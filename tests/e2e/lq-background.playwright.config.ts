import { defineConfig, devices } from '@playwright/test';
import path from 'node:path';

export default defineConfig({
  testDir: './specs', testMatch: ['lq-background.spec.ts'], workers: 1,
  timeout: 90000, expect: { timeout: 10000 },
  outputDir: path.resolve('.codex-temp/lq-background-20260924-e2e'), reporter: 'list',
  use: { ...devices['Desktop Chrome'], channel: 'chrome', baseURL: 'http://127.0.0.1:8295',
    screenshot: 'only-on-failure', trace: 'retain-on-failure' },
});
