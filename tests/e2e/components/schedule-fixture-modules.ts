import fs from 'node:fs';
import path from 'node:path';
import type { Route } from '@playwright/test';

/** Serve the actual shared deck modules, including imports with cache versions. */
export async function serveScheduleModule(route: Route): Promise<boolean> {
  const file = path.posix.basename(new URL(route.request().url()).pathname);
  const module = file === 'deck.js' ? 'course_schedule_deck.js' : file;
  if (!['course_schedule_deck.js', 'course_schedule_change_routes.js', 'course_schedule_change_links.js'].includes(module)) return false;
  await route.fulfill({ contentType: 'text/javascript', body: fs.readFileSync(path.resolve('static/js', module), 'utf8') });
  return true;
}
