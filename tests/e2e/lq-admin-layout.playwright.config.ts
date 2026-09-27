import { defineConfig } from '@playwright/test';
import path from 'node:path';
import base from './lq-platform-audit.playwright.config';

// Shares the owned-fixture identity checks and exclusive session lock.
export default defineConfig({ ...base, testMatch: 'lq-admin-layout.spec.ts', timeout: 90_000,
  outputDir: path.resolve('.codex-temp/lq-admin-layout-tests'), reporter: 'list' });
