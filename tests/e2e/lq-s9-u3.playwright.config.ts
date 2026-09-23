import { defineConfig, devices } from '@playwright/test';
import path from 'node:path';

// S9 U3 包专用：只跑遗留浅色填充的清扫验收 spec，端口与产物目录都归本包所有。
const output = path.resolve(process.env.LQ_S9_OUTPUT || '.codex-temp/claude-s9-u3-e2e');
export default defineConfig({
  testDir: './specs', testMatch: ['lq-s9-legacy-fills.spec.ts'],
  globalSetup: './lq-s3.global-setup.ts',
  workers: 1, fullyParallel: false, timeout: 240000, expect: { timeout: 10000 },
  outputDir: path.join(output, 'test-results'),
  reporter: [['list'], ['json', { outputFile: path.join(output, 'results.json') }]],
  use: {
    ...devices['Desktop Chrome'], channel: 'chrome',
    baseURL: `http://127.0.0.1:${process.env.LQ_S9_PORT || '8265'}`,
    viewport: { width: 1440, height: 900 }, screenshot: 'only-on-failure', trace: 'retain-on-failure',
  },
});
