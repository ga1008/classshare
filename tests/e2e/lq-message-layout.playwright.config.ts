import { defineConfig, devices } from '@playwright/test';
import path from 'node:path';

export default defineConfig({
  testDir: './specs', testMatch: 'lq-message-layout.spec.ts', workers: 1,
  timeout: 90000, expect: { timeout: 10000 }, reporter: 'list',
  outputDir: path.resolve('.codex-temp/lq-message-layout-results'),
  use: { ...devices['Desktop Chrome'], channel: 'chrome', baseURL: 'http://127.0.0.1:8295',
    screenshot: 'only-on-failure', trace: 'retain-on-failure' },
});
