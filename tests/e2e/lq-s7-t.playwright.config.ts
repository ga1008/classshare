import { defineConfig, devices } from '@playwright/test';
import path from 'node:path';

// S7 T package owns ports 8235 (navbar-shell on) and 8236 (off).
const output = path.resolve(process.env.LQ_S7_T_OUTPUT || '.codex-temp/claude-s7-t-e2e');
export default defineConfig({
  testDir: './specs', testMatch: ['lq-s7-topbar.spec.ts'], workers: 1, fullyParallel: false,
  timeout: 120000, expect: { timeout: 12000 }, outputDir: path.join(output, 'test-results'),
  reporter: [['list'], ['json', { outputFile: path.join(output, `results-${process.env.LQ_S7_T_MODE || 'on'}.json`) }]],
  use: {
    ...devices['Desktop Chrome'], channel: 'chrome',
    baseURL: `http://127.0.0.1:${process.env.LQ_S7_T_PORT || '8235'}`,
    viewport: { width: 1440, height: 900 }, screenshot: 'only-on-failure', trace: 'retain-on-failure',
  },
});
