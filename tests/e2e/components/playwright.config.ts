import { defineConfig, devices } from '@playwright/test';
import fs from 'node:fs';

// Standalone UI fixtures: no application server, database, or external API calls.
export default defineConfig({
  testDir: '.',
  testMatch: '*.spec.ts',
  workers: 1,
  timeout: 30_000,
  reporter: 'list',
  outputDir: '../../../.codex-temp/schedule-component-tests',
  use: {
    ...devices['Desktop Chrome'],
    ...(fs.existsSync('C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe')
      ? { channel: 'chrome' } : {}),
    viewport: { width: 1440, height: 980 },
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
});
