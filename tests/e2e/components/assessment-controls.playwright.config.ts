import { defineConfig } from '@playwright/test';
import path from 'node:path';
import base from './playwright.config';
const output = path.resolve(process.env.ASSESSMENT_CONTROLS_EVIDENCE || process.env.ASSESSMENT_EVIDENCE || '.codex-temp/evidence/assessment-controls-20261116');
export default defineConfig({
  ...base,
  testMatch: ['assessment-editor.spec.ts', 'assessment-toolbar.spec.ts', 'assessment-drawing.spec.ts', 'lq-menu-tooltip.spec.ts'],
  outputDir: path.join(output, 'components'),
  reporter: [['list'], ['json', { outputFile: path.join(output, 'component-results.json') }]],
});
