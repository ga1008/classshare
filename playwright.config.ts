import { defineConfig, devices } from '@playwright/test';
import fs from 'node:fs';

const port = process.env.P03_PORT || '8023';
const aiPort = process.env.P03_AI_PORT || '8024';
const baseURL = `http://127.0.0.1:${port}`;
const browserChannel = process.env.P03_BROWSER_CHANNEL
  || (fs.existsSync('C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe') ? 'chrome' : undefined);

export default defineConfig({
  testDir: './tests/e2e/specs',
  fullyParallel: false,
  workers: 1,
  timeout: 60_000,
  expect: {
    timeout: 10_000,
  },
  reporter: [
    ['list'],
    ['html', { outputFolder: '.codex-temp/p03-artifacts/html-report', open: 'never' }],
  ],
  outputDir: '.codex-temp/p03-artifacts/test-results',
  use: {
    baseURL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    navigationTimeout: 30_000,
    actionTimeout: 15_000,
  },
  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        ...(browserChannel ? { channel: browserChannel } : {}),
        viewport: { width: 1440, height: 980 },
      },
    },
  ],
  webServer: {
    command: `powershell -NoProfile -ExecutionPolicy Bypass -File tests\\e2e\\scripts\\start-p03-server.ps1 -Port ${port} -AiPort ${aiPort}`,
    url: `${baseURL}/api/internal/health`,
    reuseExistingServer: false,
    timeout: 120_000,
    stdout: 'pipe',
    stderr: 'pipe',
  },
});
