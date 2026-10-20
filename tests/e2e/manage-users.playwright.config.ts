import { defineConfig, devices } from '@playwright/test';
import path from 'node:path';

const output = path.resolve(process.env.MANAGE_USERS_OUTPUT || '.codex-temp/manage-users-browser');

export default defineConfig({
  testDir: './specs',
  testMatch: 'manage-users.spec.ts',
  workers: 1,
  retries: 0,
  timeout: 60_000,
  outputDir: path.join(output, 'test-results'),
  reporter: [['list'], ['json', { outputFile: path.join(output, 'results.json') }]],
  use: {
    ...devices['Desktop Chrome'],
    channel: 'chrome',
    baseURL: process.env.MANAGE_USERS_BASE_URL || 'http://127.0.0.1:8371',
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
});
