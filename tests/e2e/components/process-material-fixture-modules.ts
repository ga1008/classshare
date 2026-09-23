import fs from 'node:fs';
import path from 'node:path';
import type { Route } from '@playwright/test';

// Process-material dialogs join the production LQ layer stack when nested.
// Keep its real dependency graph in these fixtures: replacing the layer with
// a stub would stop testing focus ownership, Escape and pending-close guards.
const modules = new Map([
  ['/lq/layer.js', 'lq/layer.js'],
  ['/ui_overlay_motion.js', 'ui_overlay_motion.js'],
  ['/ui_popover_geometry.js', 'ui_popover_geometry.js'],
].map(([url, file]) => [url, fs.readFileSync(path.resolve('static/js', file), 'utf8')]));

export async function serveProcessMaterialModule(route: Route): Promise<boolean> {
  const source = modules.get(new URL(route.request().url()).pathname);
  if (source === undefined) return false;
  await route.fulfill({ contentType: 'text/javascript', body: source });
  return true;
}
