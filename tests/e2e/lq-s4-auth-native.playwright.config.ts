import { defineConfig, devices } from '@playwright/test';
import path from 'node:path';

// Prepared explicitly from a new synthetic runtime; never start the main app.
const output = path.resolve(process.env.LQ_S4_AUTH_NATIVE_OUTPUT || '.codex-temp/lq-s4-auth-native-browser');
export default defineConfig({
  testDir: './specs', testMatch: ['lq-s4-auth-native.spec.ts'], workers: 1, fullyParallel: false,
  timeout: 90000, expect: { timeout: 12000 }, outputDir: path.join(output, 'test-results'),
  reporter: [['list'], ['json', { outputFile: path.join(output, 'results.json') }]],
  use: { ...devices['Desktop Chrome'], channel: 'chrome', baseURL: `http://127.0.0.1:${process.env.LQ_S4_AUTH_NATIVE_PORT || '8168'}`,
    screenshot: 'only-on-failure', trace: 'retain-on-failure' },
});
