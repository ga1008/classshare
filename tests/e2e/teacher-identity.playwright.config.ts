import { defineConfig, devices } from '@playwright/test';
import path from 'node:path';

export default defineConfig({
  testDir: './specs', testMatch: 'teacher-identity.spec.ts',
  workers: 1, retries: 0, timeout: 60_000,
  outputDir: path.resolve('.codex-temp/teacher-identity-browser/test-results'),
  reporter: [['list'], ['json', { outputFile: path.resolve('.codex-temp/teacher-identity-browser/results.json') }]],
  use: { ...devices['Desktop Chrome'], channel: 'chrome', baseURL: 'http://127.0.0.1:8367',
    screenshot: 'only-on-failure', trace: 'retain-on-failure' },
});
