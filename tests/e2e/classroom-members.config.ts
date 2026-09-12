import { defineConfig, devices } from '@playwright/test';
import fs from 'node:fs';

export default defineConfig({
  testDir: './specs', testMatch: 'classroom-members-tabs.spec.ts', workers: 1,
  timeout: 30_000, reporter: 'list', outputDir: '../../.codex-temp/attendance-plan/member-browser-results',
  use: { ...devices['Desktop Chrome'], ...(fs.existsSync('C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe') ? { channel: 'chrome' } : {}),
    screenshot: 'only-on-failure', trace: 'retain-on-failure', viewport: { width: 1366, height: 900 } },
});
