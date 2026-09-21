import { expect, type Page } from '@playwright/test';

export async function waitForClassroomControls(page: Page) {
  // These controls are SSR-visible before the native modules finish loading.
  // initLearningProgressModal adds aria-expanded after initTeachingTimeline has
  // bound its listeners; the island's mounted flag only means loading started.
  await expect(page.locator('.cw-cultivation-entry[data-learning-modal-open]'))
    .toHaveAttribute('aria-expanded', 'false');
}
