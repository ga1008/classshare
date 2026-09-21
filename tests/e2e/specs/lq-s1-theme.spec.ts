import { expect, test, type Page } from '@playwright/test';
import { loginStudent, loginTeacher, readFixture, expectHealthUsesRuntimeDb, type P03Fixture } from '../fixtures/p03';

type Fixture = P03Fixture & { uiV3Synthetic: boolean; lqCaptureRoutes: { teacher: [string, string][]; student: [string, string][] } };
const fixture = () => readFixture() as Fixture;
const getPreferences = async (page: Page) => {
  const response = await page.request.get('/api/profile/ui-preferences');
  expect(response.status()).toBe(200);
  expect(response.headers()['cache-control']).toContain('no-store');
  return (await response.json()).preferences;
};
async function savePreferences(page: Page, changes: Record<string, string>) {
  const current = await getPreferences(page);
  const response = await page.request.patch('/api/profile/ui-preferences', {
    headers: { 'X-UI-Preferences-Context': current.context_token }, data: { ...changes, version: current.version },
  });
  expect(response.status()).toBe(200);
  return (await response.json()).preferences;
}
async function signIn(page: Page, role: 'teacher' | 'student') {
  if (role === 'teacher') await loginTeacher(page, fixture()); else await loginStudent(page, fixture());
  await expectHealthUsesRuntimeDb(page, fixture());
}
async function choose(page: Page, field: string, value: string) {
  const details = page.locator('[data-ui-preferences-details]');
  if (await details.getAttribute('open') === null) await details.locator('summary').click();
  const control = page.locator(field === 'palette_key' ? '[data-ui-palette-select]' : `[data-ui-preference-select="${field}"]`);
  const saved = page.waitForResponse(response => response.url().endsWith('/api/profile/ui-preferences') && response.request().method() === 'PATCH');
  await control.selectOption(value);
  expect((await saved).status()).toBe(200);
  await expect.poll(async () => (await getPreferences(page))[field]).toBe(value);
}

test.beforeAll(() => expect(fixture().uiV3Synthetic).toBe(true));
test.beforeEach(async ({ context }) => {
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    return ['127.0.0.1', 'localhost'].includes(url.hostname) || ['data:', 'blob:'].includes(url.protocol) ? route.continue() : route.abort();
  });
});

for (const role of ['teacher', 'student'] as const) {
  test(`${role} can save all three preferences through the compact control`, async ({ page }) => {
    await signIn(page, role);
    await savePreferences(page, { palette_key: role === 'teacher' ? 'teal' : 'indigo', appearance: 'light', glass: 'tinted' });
    await page.reload();
    await choose(page, 'palette_key', 'rose');
    await choose(page, 'appearance', 'dark');
    await choose(page, 'glass', 'off');
    await page.goto(role === 'teacher' ? '/manage/teaching/classroom-hub' : '/resume');
    await expect(page.locator('html')).toHaveAttribute('data-ui-palette', 'rose');
    await expect(page.locator('html')).toHaveAttribute('data-appearance', 'dark');
    await expect(page.locator('html')).toHaveAttribute('data-lq-glass', 'off');
    const current = await getPreferences(page);
    await savePreferences(page, { palette_key: 'sky' });
    const after = await getPreferences(page);
    expect(after.appearance).toBe('dark'); expect(after.glass).toBe('off');
    expect(after.version).toBe(current.version + 1);
  });
}

test('every document root renders explicit dark preferences with JavaScript disabled', async ({ page, browser }) => {
  for (const role of ['teacher', 'student'] as const) {
    await signIn(page, role);
    await savePreferences(page, { palette_key: 'violet', appearance: 'dark', glass: 'off' });
    const context = await browser.newContext({ storageState: await page.context().storageState(), javaScriptEnabled: false });
    try {
      const readOnly = await context.newPage();
      const routes = role === 'teacher'
        ? ['/dashboard', '/manage/teaching/classroom-hub', '/exam/new', ...fixture().lqCaptureRoutes.teacher.filter(([name]) => name.endsWith('editor')).map(([, route]) => route)]
        : ['/dashboard', '/resume', ...fixture().lqCaptureRoutes.student.filter(([name]) => name === 'exam-take').map(([, route]) => route)];
      for (const route of routes) {
        const response = await readOnly.goto(new URL(route, page.url()).href);
        expect(response?.status(), route).toBe(200);
        await expect(readOnly.locator('html')).toHaveAttribute('data-ui-palette', 'violet');
        await expect(readOnly.locator('html')).toHaveAttribute('data-appearance', 'dark');
        await expect(readOnly.locator('html')).toHaveAttribute('data-lq-glass', 'off');
        expect(await readOnly.locator('html').evaluate(node => getComputedStyle(node).colorScheme)).toBe('dark');
      }
    } finally { await context.close(); }
  }
});

test('auto resolves before the first frame without modules and media changes do not save', async ({ page }) => {
  await signIn(page, 'student');
  const saved = await savePreferences(page, { appearance: 'auto', glass: 'tinted' });
  await page.emulateMedia({ colorScheme: 'dark' });
  await page.addInitScript(() => requestAnimationFrame(() => {
    (window as any).__lqFirstFrame = document.documentElement.dataset.appearance;
  }));
  await page.route(/\/static\/.*\.js(?:[?#]|$)/, route => route.abort());
  await page.goto('/resume');
  await expect.poll(() => page.evaluate(() => (window as any).__lqFirstFrame)).toBe('dark');
  await expect(page.locator('html')).toHaveAttribute('data-appearance', 'dark');
  await page.unroute(/\/static\/.*\.js(?:[?#]|$)/);
  await page.reload();
  await expect(page.locator('[data-ui-palette-select]')).toHaveCount(0);
  let mutations = 0;
  page.on('request', request => { if (request.url().includes('/api/profile/ui-preferences') && request.method() === 'PATCH') mutations++; });
  await page.emulateMedia({ colorScheme: 'light' });
  await expect(page.locator('html')).toHaveAttribute('data-appearance', 'light');
  await page.emulateMedia({ colorScheme: 'dark' });
  await expect(page.locator('html')).toHaveAttribute('data-appearance', 'dark');
  expect(mutations).toBe(0); expect((await getPreferences(page)).version).toBe(saved.version);
});

test('off covers late legacy hosts, pseudo elements and native dialog backdrops', async ({ page }) => {
  await signIn(page, 'teacher');
  await savePreferences(page, { appearance: 'dark', glass: 'off' });
  await page.goto(`/classroom/${fixture().classOfferingId}`);
  await page.evaluate(() => {
    const style = document.createElement('style');
    style.textContent = '#lq-late-legacy, #lq-late-legacy::before, #lq-late-dialog::backdrop {backdrop-filter:blur(33px);-webkit-backdrop-filter:blur(33px)} #lq-late-legacy::before {content:"late"}';
    document.head.append(style);
    const div = document.createElement('div'); div.id = 'lq-late-legacy'; div.textContent = 'Legacy host'; document.body.append(div);
    const dialog = document.createElement('dialog'); dialog.id = 'lq-late-dialog'; dialog.textContent = 'Native dialog'; document.body.append(dialog); dialog.showModal();
  });
  const active = await page.evaluate(() => [...document.querySelectorAll('*')].flatMap(node => [null, '::before', '::after', ...(node.tagName === 'DIALOG' ? ['::backdrop'] : [])].flatMap(pseudo => {
    const style = getComputedStyle(node, pseudo), value = style.backdropFilter || style.getPropertyValue('-webkit-backdrop-filter');
    return value && value !== 'none' ? [`${node.tagName}#${node.id}${pseudo || ''}: ${value}`] : [];
  })));
  expect(active).toEqual([]);
});

test('preview is local, covers six palettes and the controls remain inside a phone viewport', async ({ page }) => {
  await signIn(page, 'teacher');
  await savePreferences(page, { appearance: 'light', glass: 'tinted' });
  const before = await getPreferences(page);
  await page.goto('/dev/lq');
  for (const palette of ['teal', 'indigo', 'sky', 'mint', 'violet', 'rose']) {
    await page.locator('[data-lq-preview-controls] [name="palette_key"]').selectOption(palette);
    await expect(page.locator('html')).toHaveAttribute('data-ui-palette', palette);
    expect(await page.locator('[data-probe-primary]').evaluate(node => getComputedStyle(node).color)).not.toBe('rgba(0, 0, 0, 0)');
  }
  expect(await getPreferences(page)).toEqual(before);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/dashboard');
  await page.locator('[data-ui-preferences-toggle]').click();
  const panel = await page.locator('[data-ui-preferences-panel]').boundingBox();
  expect(panel).not.toBeNull(); expect(panel!.x).toBeGreaterThanOrEqual(0); expect(panel!.x + panel!.width).toBeLessThanOrEqual(390);
  await page.keyboard.press('Escape');
  await expect(page.locator('[data-ui-preferences-details]')).not.toHaveAttribute('open', '');
  await expect(page.locator('[data-ui-preferences-toggle]')).toBeFocused();
});
