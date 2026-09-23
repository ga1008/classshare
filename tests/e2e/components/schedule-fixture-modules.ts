import fs from 'node:fs';
import path from 'node:path';
import type { Page, Route } from '@playwright/test';

/** Serve the actual shared deck modules, including imports with cache versions. */
export async function serveScheduleModule(route: Route): Promise<boolean> {
  const file = path.posix.basename(new URL(route.request().url()).pathname);
  if (file === 'schedule-tokens.css') {
    await route.fulfill({ contentType: 'text/css', body: fs.readFileSync(path.resolve('static/css/lq/tokens.css'), 'utf8') });
    return true;
  }
  const module = file === 'deck.js' ? 'course_schedule_deck.js' : file;
  if (!['course_schedule_deck.js', 'course_schedule_styles.js', 'course_schedule_change_routes.js', 'course_schedule_change_links.js', 'course_schedule_presentation.js'].includes(module)) return false;
  await route.fulfill({ contentType: 'text/javascript', body: fs.readFileSync(path.resolve('static/js', module), 'utf8') });
  return true;
}

/** Include chained geometry and text phases, not just the first animation. */
export async function settleScheduleMotion(page: Page) {
  await page.locator('.cs-expand__card').evaluate(async card => {
    let quietFrames = 0;
    for (let frame = 0; frame < 150 && quietFrames < 2; frame++) {
      await new Promise(requestAnimationFrame);
      const moving = card.querySelector('[data-preview-state="opening"],[data-preview-state="closing"],.is-preview-moving');
      const animated = card.getAnimations({ subtree: true }).some(animation => ['running', 'pending'].includes(animation.playState));
      quietFrames = moving || animated ? 0 : quietFrames + 1;
    }
    if (quietFrames < 2) throw new Error('Schedule motion did not settle');
  });
}
