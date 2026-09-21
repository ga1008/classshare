import { defineConfig } from '@playwright/test';
import base from './lq-s3.playwright.config';

// One runner owns the fixture across both engines; no parallel logins.
const { channel: _channel, ...use } = base.use || {};
export default defineConfig({
  ...base,
  testMatch: 'pilot-compat.spec.ts',
  use,
  projects: [
    { name: 'chromium', use: { browserName: 'chromium', channel: 'chrome' } },
    { name: 'webkit', use: { browserName: 'webkit' } },
  ],
});
