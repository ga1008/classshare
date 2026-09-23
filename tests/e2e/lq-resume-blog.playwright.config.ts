import { defineConfig, devices } from '@playwright/test';
import path from 'node:path';

const output = path.resolve('.codex-temp/lq-resume-blog-20260923-e2e');
export default defineConfig({
  testDir: './specs', testMatch: ['lq-resume-blog.spec.ts'], workers: 1,
  timeout: 90000, expect: { timeout: 10000 }, outputDir: path.join(output, 'artifacts'),
  reporter: [['list'], ['json', { outputFile: path.join(output, 'results.json') }]],
  use: { ...devices['Desktop Chrome'], channel: 'chrome', baseURL: 'http://127.0.0.1:8295',
    reducedMotion: 'reduce', screenshot: 'only-on-failure', trace: 'retain-on-failure' },
});
