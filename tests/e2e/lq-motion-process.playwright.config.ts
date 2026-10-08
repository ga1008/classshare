import { defineConfig, devices } from '@playwright/test';
import path from 'node:path';

const output = path.resolve(process.env.LQ_MOTION_OUTPUT || '.codex-temp/lq-motion-process-20261008');
export default defineConfig({
  testDir: './components', testMatch: 'lq-motion-process.spec.ts', workers: 1,
  fullyParallel: false, retries: 0, timeout: 90_000,
  outputDir: path.join(output, 'test-results'),
  reporter: [['list'], ['json', { outputFile: path.join(output, 'results.json') }]],
  use: { ...devices['Desktop Chrome'], channel: 'chrome', screenshot: 'only-on-failure', trace: 'retain-on-failure' },
  projects: [
    { name: 'desktop-light', use: { viewport: { width: 1440, height: 980 }, colorScheme: 'light' } },
    { name: 'touch-dark', use: { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, colorScheme: 'dark' } },
  ],
});
