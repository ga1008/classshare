import AxeBuilder from '@axe-core/playwright';
import { createRequire } from 'node:module';
import path from 'node:path';
import type { Locator } from '@playwright/test';
import { test, expect, readS3Fixture } from '../fixtures/lq-s3';
import { loginStudent, loginTeacher } from '../fixtures/p03';

const { inspectGlass } = createRequire(path.resolve('package.json'))('./tools/ui/audit_glass_layers.cjs');
async function readablePreferences(header: Locator) {
  const fields = await header.locator('[data-ui-preferences-panel] select').evaluateAll(selects => selects.map(node => {
    const select = node as HTMLSelectElement, style = getComputedStyle(select);
    const canvas = document.createElement('canvas'), context = canvas.getContext('2d')!;
    context.font = style.font;
    const panel = select.closest('[data-ui-preferences-panel]')!.getBoundingClientRect(), rect = select.getBoundingClientRect();
    return { name: select.getAttribute('aria-label'), width: rect.width, textWidth: context.measureText(select.selectedOptions[0].text).width,
      padding: parseFloat(style.paddingLeft) + Math.max(20, parseFloat(style.paddingRight)),
      contained: rect.left >= panel.left && rect.right <= panel.right, };
  }));
  expect(fields).toHaveLength(3);
  for (const field of fields) {
    expect(field.contained, `${field.name} stays inside its preferences panel`).toBe(true);
    expect(field.width, `${field.name} has room for its current value and native arrow`).toBeGreaterThanOrEqual(field.textWidth + field.padding);
  }
}
for (const role of ['student', 'teacher'] as const) for (const width of [1440, 390]) {
  test(`S4 ${role} shell palette contrast and whole-page glass at ${width}`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: 900 });
    const fixture = readS3Fixture();
    await (role === 'teacher' ? loginTeacher : loginStudent)(page, fixture);
    const selector = role === 'teacher' ? '#manage-pilot-topbar' : '[data-lq-navbar-topbar]';
    const measurements = [];
    for (const appearance of ['light', 'dark']) for (const palette_key of ['teal', 'indigo', 'sky', 'mint', 'violet', 'rose']) {
      await page.evaluate(async preferences => {
        const owner = (document as any)[Symbol.for('lanshare.theme.installation')];
        if (!owner) throw Error('Missing actual theme owner');
        owner.refresh({ ...preferences, glass: 'tinted' });
        await document.fonts.ready;
        await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      }, { palette_key, appearance });
      const rest = await inspectGlass(page);
      measurements.push({ palette_key, appearance, state: 'rest', ...rest });
      await info.attach(`${role}-${width}-${appearance}-${palette_key}-rest`, { body: JSON.stringify(rest, null, 2), contentType: 'application/json' });
      expect(rest.visibleViewportLayerCount, `${role}/${palette_key}/${appearance}/rest`).toBeLessThanOrEqual(2);
      const header = page.locator(selector);
      if (width < 1024) await header.locator(':scope > [data-lq-pane-open="actions"]').click();
      await header.locator('[data-ui-preferences-toggle]').click();
      await expect(header.locator('[data-ui-preferences-panel]')).toBeVisible();
      await readablePreferences(header);
      const scanner = new AxeBuilder({ page }).include(selector);
      if (role === 'student') scanner.include('#navbar-dock');
      expect((await scanner.analyze()).violations, `${role}/${palette_key}/${appearance}`).toEqual([]);
      const open = await inspectGlass(page);
      measurements.push({ palette_key, appearance, state: 'open', ...open });
      expect(open.visibleViewportLayerCount).toBeLessThanOrEqual(3);
      if (palette_key === 'indigo') await page.screenshot({ path: info.outputPath(`${role}-${width}-${appearance}-preferences.png`) });
      await page.keyboard.press('Escape');
      await expect(header.locator('[data-ui-preferences-panel]')).toBeHidden();
      if (width < 1024) { await page.keyboard.press('Escape'); await expect(header.locator('[data-lq-pane="actions"]')).toBeHidden(); }
    }
    await info.attach('palette-glass-observations', { body: JSON.stringify(measurements, null, 2), contentType: 'application/json' });
  });
}

test('S4 student narrow preferences retain readable current values at 320', async ({ page }, info) => {
  await page.setViewportSize({ width: 320, height: 660 });
  await loginStudent(page, readS3Fixture());
  const header = page.locator('[data-lq-navbar-topbar]');
  await header.locator(':scope > [data-lq-pane-open="actions"]').click();
  await header.locator('[data-ui-preferences-toggle]').click();
  await expect(header.locator('[data-ui-preferences-panel]')).toBeVisible();
  await readablePreferences(header);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(320);
  await page.screenshot({ path: info.outputPath('student-320-preferences.png') });
});
