import { defineConfig } from '@playwright/test';
import base from './assessment-editor.playwright.config';
export default defineConfig({ ...base, testMatch: ['assessment-controls.spec.ts', 'assessment-list-numbering.spec.ts'] });
