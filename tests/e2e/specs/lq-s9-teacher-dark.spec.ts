/**
 * Teacher and manage views in dark mode with the backdrop on.
 *
 * The legacy sweep converted around two hundred declarations that live in
 * classroom.css and manage_classes.css, but its synthetic runtime only reached
 * student routes, so those never ran anywhere. Dark mode with a photo behind
 * the page is the single condition under which a fill that does not flip shows
 * up, so that is what this asserts.
 */
import { expect, test } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { loginTeacher, readFixture } from '../fixtures/p03';

const ROUTES = [
  ['manage-classes', '/manage/teaching/classes'],
  ['manage-hub', '/manage/teaching/classroom-hub'],
  ['manage-semesters', '/manage/teaching/semesters'],
  ['teacher-home', '/dashboard'],
] as const;

async function useSceneDark(page: import('@playwright/test').Page) {
  const current = await (await page.request.get('/api/profile/ui-preferences')).json();
  const pref = current.preferences;
  const res = await page.request.patch('/api/profile/ui-preferences', {
    headers: { 'X-UI-Preferences-Context': pref.context_token },
    data: { backdrop: 'scene', appearance: 'dark', version: pref.version },
  });
  expect(res.status(), 'the preference must actually be saved, or this tests the wrong state').toBe(200);
}

for (const [id, route] of ROUTES) {
  test(`${id} has no near-white panel and no contrast violation in dark mode`, async ({ page }) => {
    await loginTeacher(page, readFixture());
    await useSceneDark(page);
    await page.goto(route);
    await page.waitForLoadState('networkidle');

    const nearWhite = await page.evaluate(() => {
      const lin = (c: number) => (c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4));
      const out: string[] = [];
      for (const node of Array.from(document.querySelectorAll<HTMLElement>('body *'))) {
        const rect = node.getBoundingClientRect();
        if (rect.width < 120 || rect.height < 60) continue;
        const bg = getComputedStyle(node).backgroundColor;
        const parts = bg.match(/[\d.]+/g)?.map(Number);
        if (!parts || parts.length < 3) continue;
        if ((parts[3] ?? 1) < 0.5) continue;
        const l = 0.2126 * lin(parts[0] / 255) + 0.7152 * lin(parts[1] / 255) + 0.0722 * lin(parts[2] / 255);
        if (l > 0.8) out.push(`${node.tagName.toLowerCase()}.${(node.className || '').toString().split(' ')[0]} ${bg}`);
      }
      return [...new Set(out)];
    });
    expect(nearWhite, `${id}: 暗色下仍有近白面板`).toEqual([]);

    const scan = await new AxeBuilder({ page }).analyze();
    const severe = scan.violations
      .filter(v => v.impact === 'serious' || v.impact === 'critical')
      .map(v => `${v.id}:${v.nodes.length}`);
    for (const violation of scan.violations.filter(v => v.impact === 'serious' || v.impact === 'critical')) {
      for (const node of violation.nodes) {
        console.log(`OFFENDER ${id} ${violation.id} ${JSON.stringify(node.target)} :: ${(node.any[0]?.message || '').slice(0, 130)}`);
      }
    }
    // Pinned, not filtered: the semester rows wrap focusable controls in a div
    // that carries aria-current, which predates the material work and needs a
    // template change to fix. Anything beyond this exact signature fails.
    const KNOWN: Record<string, string[]> = { 'manage-semesters': ['nested-interactive:2'] };
    expect(severe, `${id}: 暗色下的严重无障碍违规`).toEqual(KNOWN[id] ?? []);
  });
}
