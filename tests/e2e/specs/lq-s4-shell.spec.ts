import AxeBuilder from '@axe-core/playwright';
import fs from 'node:fs';
import { test, expect, readS3Fixture } from '../fixtures/lq-s3';
import { loginStudent, loginTeacher } from '../fixtures/p03';

const graph = process.env.LQ_S4_GRAPH || JSON.parse(fs.readFileSync('static/assets/manifest.json', 'utf8')).revision;

for (const width of [1440, 390]) test(`S4 student shared shell actual routes at ${width}`, async ({ page }, info) => {
  const fixture = readS3Fixture();
  await page.setViewportSize({ width, height: 900 });
  await loginStudent(page, fixture);
  const routes = ['/dashboard', '/profile?section=settings', '/profile?section=notifications', '/profile?section=private',
    `/report-card?class_offering_id=${fixture.reportCard.offeringId}`, '/learning-path', '/wrong-book', '/feedback-review', '/achievements', '/blog'];
  for (let index = 0; index < routes.length; index++) {
    expect((await page.goto(routes[index]))?.status(), routes[index]).toBe(200);
    const topbar = page.locator('[data-lq-navbar-topbar]');
    await expect(topbar).toHaveCount(1); await expect(topbar).toHaveAttribute('data-lq-enhanced', 'true');
    await expect(page.locator('[data-lq-report-card-topbar]')).toHaveCount(0);
    await expect(page.locator('[data-app-bottomnav]')).toHaveCount(1);
    await expect(page.locator('[data-ui-palette-select]')).toHaveCount(1);
    expect(await page.locator('script[src$="/js/navbar_lq.js"]').getAttribute('src')).toContain(graph);
    if (width < 768) await expect(page.locator('#navbar-dock')).toBeVisible();
    else await expect(page.locator('#navbar-dock')).toBeHidden();
    if (index === 2 || index === 3) await expect(page.locator('#navbar-dock [aria-current]')).toHaveAttribute('href', '/message-center');
    if (index === 1) await expect(page.locator('#navbar-dock [aria-current]')).toHaveAttribute('href', '/profile');
    if (width < 1024) {
      await topbar.locator('[data-lq-pane-open]').click();
      await expect(page.locator('#navbar-topbar--lq-actions')).toBeVisible();
    }
    const scan = await new AxeBuilder({ page }).include('[data-lq-navbar-topbar]').include('#navbar-dock').analyze();
    expect(scan.violations, routes[index]).toEqual([]);
    if (width < 1024) await page.locator('#navbar-topbar--lq-actions [data-lq-pane-close]').last().click();
    expect(await page.evaluate(() => document.documentElement.scrollWidth), routes[index]).toBeLessThanOrEqual(width);
    if ([0, 1, 4].includes(index)) await page.screenshot({ path: info.outputPath(`student-${index}-${width}.png`), fullPage: true });
  }
});

for (const width of [1440, 390]) test(`S4 teacher shared shell actual routes at ${width}`, async ({ page }, info) => {
  await page.setViewportSize({ width, height: 900 });
  await loginTeacher(page, readS3Fixture());
  for (const [index, route] of ['/dashboard', '/manage/me', '/manage/me/security', '/manage/me/private'].entries()) {
    expect((await page.goto(route))?.status()).toBe(200);
    await expect(page.locator('body')).toHaveClass(/lq-manage-shell/);
    await expect(page.locator('body')).not.toHaveClass(/lq-manage-pilot/);
    await expect(page.locator('#manage-pilot-topbar')).toHaveAttribute('data-lq-enhanced', 'true');
    await expect(page.locator('[data-ui-palette-select]')).toHaveCount(1);
    if (width < 1024) {
      await page.locator('[data-lq-manage-sidebar] > [data-lq-pane-open]').click();
      await expect(page.locator('#manageNavSearch')).toBeVisible();
      await page.locator('#manageNavSearch').fill('我的');
      await page.keyboard.press('Escape'); await expect(page.locator('#manageNavSearch')).toHaveValue('');
      await page.keyboard.press('Escape'); await expect(page.locator('#manage-pilot-nav')).toBeHidden();
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth), route).toBeLessThanOrEqual(width);
    if (index < 2) await page.screenshot({ path: info.outputPath(`teacher-${index}-${width}.png`), fullPage: true });
  }
});

test('S4 navbar twenty lifecycle cycles retain live draft nodes and keyboard Dock fallback', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 900 });
  await loginStudent(page, readS3Fixture());
  await page.goto('/profile?section=settings');
  await expect(page.locator('[data-lq-navbar-topbar]')).toHaveAttribute('data-lq-enhanced', 'true');
  const result = await page.evaluate(async () => {
    const url = document.querySelector<HTMLScriptElement>('script[src$="/js/navbar_lq.js"]')!.src;
    const module = await import(url);
    const input = document.querySelector<HTMLInputElement>('#profile-basic-form input:not([type=hidden])')!;
    input.value = '保留的资料草稿';
    const topbar = document.querySelector('[data-lq-navbar-topbar]')!, dock = document.querySelector('#navbar-dock')!;
    for (let n = 0; n < 20; n++) { const handle = module.initNavbarLq(); handle.destroy(); handle.destroy(); module.initNavbarLq(); }
    input.focus();
    const viewport = window.visualViewport!;
    const descriptor = Object.getOwnPropertyDescriptor(viewport, 'height');
    Object.defineProperty(viewport, 'height', { configurable: true, get: () => window.innerHeight - 300 });
    viewport.dispatchEvent(new Event('resize'));
    const hidden = (dock as HTMLElement).hidden;
    if (descriptor) Object.defineProperty(viewport, 'height', descriptor); else delete (viewport as any).height;
    viewport.dispatchEvent(new Event('resize'));
    return { sameInput: input === document.querySelector('#profile-basic-form input:not([type=hidden])'), value: input.value,
      sameTopbar: topbar === document.querySelector('[data-lq-navbar-topbar]'), sameDock: dock === document.querySelector('#navbar-dock'),
      hidden, restored: !(dock as HTMLElement).hidden, duplicateIds: [...document.querySelectorAll('[id]')].map(n => n.id).filter((id, i, ids) => ids.indexOf(id) !== i) };
  });
  expect(result).toEqual({ sameInput: true, value: '保留的资料草稿', sameTopbar: true, sameDock: true, hidden: true, restored: true, duplicateIds: [] });
});

test('S4 no-script and failed shell module keep real native navigation and one compensation owner', async ({ browser, baseURL }) => {
  const fixture = readS3Fixture();
  for (const javaScriptEnabled of [false, true]) {
    const context = await browser.newContext({ baseURL, javaScriptEnabled, viewport: { width: 390, height: 900 } });
    try {
      await context.route('**/*', route => {
        const url = new URL(route.request().url());
        if (!['127.0.0.1', 'localhost'].includes(url.hostname)) return route.abort();
        if (javaScriptEnabled && url.pathname.endsWith('/js/navbar_lq.js')) return route.abort();
        return route.continue();
      });
      const login = await context.request.post('/student/login', { form: { identifier: fixture.student.studentNumber, password: fixture.password }, maxRedirects: 0 });
      expect(login.status()).toBe(303);
      const page = await context.newPage(); await page.goto('/profile?section=notifications');
      await expect(page.locator('[data-lq-navbar-topbar]')).not.toHaveAttribute('data-lq-enhanced', 'true');
      await expect(page.locator('#navbar-topbar--lq-actions')).toBeVisible();
      await expect(page.locator('#navbar-dock')).toBeVisible();
      expect(await page.locator('body').evaluate(body => body.classList.contains('has-bottomnav'))).toBe(false);
      await page.locator('#navbar-dock a[href="/learning-path"]').click();
      await expect(page).toHaveURL(/\/learning-path$/);
      await expect(page.locator('#navbar-dock [aria-current]')).toHaveAttribute('href', '/learning-path');
    } finally { await context.close(); }
  }
});
