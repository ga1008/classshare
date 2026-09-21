import { defineConfig } from '@playwright/test';
import main from './lq-s3.playwright.config';

// The caller starts a second asserted synthetic server with the pilot disabled.
// This config never starts a server or mutates a flag on a running application.
export default defineConfig(main, { testMatch: ['pilot-rollback.spec.ts'] });
