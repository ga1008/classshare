import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';

// Actual production renderer and the immutable, compiled stylesheet. No app,
// database, network-backed repository or AI service participates in this fixture.
for (const width of [320, 390, 1024]) {
  test(`repository binding results preserve long labels at ${width}px`, async ({ page }, testInfo) => {
    const manifest = JSON.parse(fs.readFileSync('static/assets/manifest.json', 'utf8'));
    const sourcePath = 'js/materials_manage.js';
    expect(createHash('sha256').update(fs.readFileSync(`static/${sourcePath}`)).digest('hex')).toBe(manifest.sourceHashes[sourcePath]);
    const script = fs.readFileSync(path.join('static', manifest.entries[sourcePath]), 'utf8');
    const renderer = script.match(/^function renderRepositoryAutoBindAssignments\([\s\S]*?^}/m)?.[0];
    expect(renderer).toBeTruthy();
    await page.setViewportSize({ width, height: 844 });
    await page.setContent('<!doctype html><html lang="zh-CN"><meta name="viewport" content="width=device-width,initial-scale=1"><body><main style="margin:16px;max-width:900px"><div id="bindings"></div></main></body></html>');
    await page.addStyleTag({ path: path.join('static', manifest.entries['css/tailwind-app.css']) });
    await page.addScriptTag({ content: `function escapeHtml(value) { return String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('"', '&quot;'); }\n${renderer}` });
    const labels = [
      '计算机网络原理 / 人工智能2601班（专升本）·人工智能2602班（专升本）',
      'ComputerNetworksAndDistributedSystemsIntroductoryLaboratory20262027',
    ];
    await page.evaluate(labels => {
      document.getElementById('bindings')!.innerHTML = (window as any).renderRepositoryAutoBindAssignments(labels.map(course_name => ({
        source: 'repository_ordinal', order_index: 123, course_name,
        material_path: 'python-course/lesson_123/lesson_123.html',
      })));
    }, labels);
    const rows = page.locator('.materials-ai-assign-item');
    await expect(rows).toHaveCount(2);
    for (let index = 0; index < labels.length; index++) {
      const row = rows.nth(index);
      await expect(row.locator('.materials-ai-assign-session-title')).toHaveText(labels[index]);
      const geometry = await row.evaluate(element => {
        const row = element.getBoundingClientRect();
        const title = element.querySelector('.materials-ai-assign-session-title')!.getBoundingClientRect();
        const badge = element.querySelector('.materials-ai-confidence')!.getBoundingClientRect();
        return { width: element.clientWidth, scrollWidth: element.scrollWidth, rowRight: row.right,
          titleRight: title.right, titleBottom: title.bottom, badgeLeft: badge.left, badgeRight: badge.right,
          badgeTop: badge.top, badgeBottom: badge.bottom, titleTop: title.top, height: row.height };
      });
      expect(geometry.scrollWidth).toBeLessThanOrEqual(geometry.width + 1);
      expect(geometry.titleRight).toBeLessThanOrEqual(geometry.rowRight);
      expect(geometry.badgeRight).toBeLessThanOrEqual(geometry.rowRight);
      expect(geometry.titleBottom <= geometry.badgeTop || geometry.titleTop >= geometry.badgeBottom || geometry.titleRight <= geometry.badgeLeft).toBe(true);
      expect(geometry.height).toBeLessThan(190);
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    await page.screenshot({ path: testInfo.outputPath('repository-bindings.png') });
    await testInfo.attach('build-graph', { body: manifest.revision, contentType: 'text/plain' });
  });
}
