import { defineConfig, devices } from '@playwright/test';
import path from 'node:path';

// Start serve_ui_v3.py explicitly with LANSHARE_LQ_PREVIEW=true. The spec
// asserts the synthetic fixture and live DB identity before writing preferences.
const output = path.resolve(process.env.LQ_S1_OUTPUT || '.codex-temp/lq-s1-e2e');
export default defineConfig({
  testDir: './specs', testMatch: 'lq-s1-theme.spec.ts', workers: 1, fullyParallel: false,
  timeout: 60000, expect: { timeout: 10000 }, outputDir: path.join(output, 'test-results'),
  reporter: [['list'], ['json', { outputFile: path.join(output, 'results.json') }]],
  use: { ...devices['Desktop Chrome'], channel: 'chrome',
    baseURL: `http://127.0.0.1:${process.env.UI_V3_PORT || '8158'}`,
    viewport: { width: 1440, height: 900 }, screenshot: 'only-on-failure', trace: 'retain-on-failure' },
});
