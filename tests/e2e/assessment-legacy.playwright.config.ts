import { defineConfig } from '@playwright/test';
import assessment from './assessment-editor.playwright.config';

export default defineConfig({
  ...assessment,
  testMatch: ['assessment-editor.spec.ts', 'assessment-legacy-resubmit.spec.ts', 'assessment-legacy-existing-return.spec.ts'],
});
