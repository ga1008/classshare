import { defineConfig, devices } from '@playwright/test';
import path from 'node:path';

// S8 PAGE 包专用：只跑页面材质验收 spec，端口与产物目录都归本包所有。
const output = path.resolve(process.env.LQ_S8_OUTPUT || '.codex-temp/claude-s8-page-e2e');
export default defineConfig({
  testDir: './specs', testMatch: ['lq-s8-page-material.spec.ts'],
  globalSetup: './lq-s3.global-setup.ts',
  workers: 1, fullyParallel: false, timeout: 240000, expect: { timeout: 10000 },
  outputDir: path.join(output, 'test-results'),
  reporter: [['list'], ['json', { outputFile: path.join(output, 'results.json') }]],
  use: { ...devices['Desktop Chrome'], channel: 'chrome',
    baseURL: `http://127.0.0.1:${process.env.LQ_S8_PORT || '8245'}`,
    viewport: { width: 1440, height: 900 }, screenshot: 'only-on-failure', trace: 'retain-on-failure' },
});
