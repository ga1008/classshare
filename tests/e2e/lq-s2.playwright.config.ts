import { defineConfig, devices } from '@playwright/test';
import path from 'node:path';

// Only the explicitly started, guarded synthetic service may be used here.
// Every test verifies the fixture and database identity before interaction.
const output = path.resolve(process.env.LQ_S2_OUTPUT || '.codex-temp/lq-s2-app-e2e');
export default defineConfig({
  testDir: './specs', testMatch: 'lq-s2-preview.spec.ts', workers: 1, fullyParallel: false,
  timeout: 60000, expect: { timeout: 10000 }, outputDir: path.join(output, 'test-results'),
  reporter: [['list'], ['json', { outputFile: path.join(output, 'results.json') }]],
  use: { ...devices['Desktop Chrome'], channel: 'chrome',
    baseURL: `http://127.0.0.1:${process.env.UI_V3_PORT || '8158'}`,
    viewport: { width: 1440, height: 900 }, screenshot: 'only-on-failure', trace: 'retain-on-failure' },
});
