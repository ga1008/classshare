import { defineConfig, devices } from '@playwright/test';
import path from 'node:path';

// S6 P package (poll form runtime assembly). The F package config restricts
// testMatch to lq-s4-manage-pages.spec.ts, so this package needs its own entry
// point. Two servers must already be running against the same synthetic
// runtime (runbook §10, ports 8211/8212):
//   8211 LANSHARE_LQ_FAMILIES=manage-shell,manage-pages LANSHARE_LQ_PILOT=true
//   8212 no families,                                   LANSHARE_LQ_PILOT=false
const output = path.resolve(process.env.LQ_S6_OUTPUT || '.codex-temp/claude-s6-p-e2e');
export default defineConfig({
  testDir: './specs', testMatch: ['lq-s6-polls.spec.ts'],
  workers: 1, fullyParallel: false, timeout: 120000, expect: { timeout: 10000 },
  outputDir: path.join(output, 'test-results'),
  reporter: [['list'], ['json', { outputFile: path.join(output, 'results.json') }]],
  use: {
    ...devices['Desktop Chrome'], channel: 'chrome',
    baseURL: `http://127.0.0.1:${process.env.LQ_S6_PORT || '8211'}`,
    viewport: { width: 1440, height: 900 }, screenshot: 'only-on-failure', trace: 'retain-on-failure',
  },
});
