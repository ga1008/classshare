import { expect, test, type Locator, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import {
  expectHealthUsesRuntimeDb,
  loginStudent,
  loginTeacher,
  readFixture,
} from '../fixtures/p03';

// Runs against the real app, authenticated page, database and file store supplied
// by the existing isolated P03 webServer. No production sessions are used.
const evidenceDir = path.resolve('artifacts/classroom-group-qr');
const introduction = '课程通知与答疑群\n入群后请将群昵称修改为「学号 + 姓名」。\n课堂资料与答疑将在群内同步。';
const layoutReports: unknown[] = [];

function qrImage(): Buffer {
  const result = spawnSync(path.resolve('venv/Scripts/python.exe'), ['-c',
    'import io,sys,qrcode; out=io.BytesIO(); qrcode.make("https://example.com/classroom-group-qr-acceptance").save(out,format="PNG"); sys.stdout.buffer.write(out.getvalue())',
  ], { windowsHide: true });
  if (result.status !== 0) throw new Error(result.stderr.toString());
  return result.stdout;
}

async function decoded(image: Locator) {
  await expect(image).toBeVisible();
  await expect.poll(() => image.evaluate((element: HTMLImageElement) => (
    element.complete && element.naturalWidth > 0 && element.naturalHeight > 0
  ))).toBe(true);
}

async function screenshot(page: Page, name: string) {
  await page.screenshot({ path: path.join(evidenceDir, name), fullPage: false });
}

async function openTeacherQr(page: Page) {
  await page.locator('[data-group-qr-open]').click();
  await expect(page.locator('#classroom-group-qr-dialog')).toBeVisible();
  await expect(page.locator('[name="description"]')).toBeEnabled();
}

async function expectDialogFits(page: Page) {
  const dimensions = await page.locator('#classroom-group-qr-dialog').evaluate(element => {
    const rect = element.getBoundingClientRect();
    return {
      horizontalOverflow: element.scrollWidth > element.clientWidth + 1,
      left: rect.left,
      right: rect.right,
      top: rect.top,
      bottom: rect.bottom,
      viewportWidth: innerWidth,
      viewportHeight: innerHeight,
    };
  });
  expect(dimensions.horizontalOverflow).toBe(false);
  expect(dimensions.left).toBeGreaterThanOrEqual(0);
  expect(dimensions.right).toBeLessThanOrEqual(dimensions.viewportWidth + 1);
  expect(dimensions.top).toBeGreaterThanOrEqual(0);
  expect(dimensions.bottom).toBeLessThanOrEqual(dimensions.viewportHeight + 1);
}

async function inspectClassroomLayout(page: Page) {
  const layout = await page.evaluate(() => {
    const hero = document.querySelector('#hero-info-card')!.getBoundingClientRect();
    const qr = document.querySelector('[data-group-qr-open]')!.getBoundingClientRect();
    return {
      viewport: { width: innerWidth, height: innerHeight },
      documentWidth: document.documentElement.scrollWidth,
      hero: { left: hero.left, right: hero.right },
      qr: { left: qr.left, right: qr.right },
      overflow: Array.from(document.querySelectorAll('body *')).map(element => {
        const rect = element.getBoundingClientRect();
        const style = getComputedStyle(element);
        return { tag: element.tagName, id: element.id, class: element.getAttribute('class'),
          left: rect.left, right: rect.right, width: rect.width, top: rect.top,
          position: style.position, opacity: style.opacity, hidden: element.hasAttribute('hidden') };
      }).filter(item => item.width && item.right > innerWidth + 1).slice(0, 15),
    };
  });
  layoutReports.push(layout);
  fs.writeFileSync(path.join(evidenceDir, 'full-page-layout-diagnostics.json'), JSON.stringify(layoutReports, null, 2));
  expect(layout.documentWidth).toBeLessThanOrEqual(layout.viewport.width + 1);
  expect(layout.hero.left).toBeGreaterThanOrEqual(0);
  expect(layout.hero.right).toBeLessThanOrEqual(layout.viewport.width + 1);
  expect(layout.qr.left).toBeGreaterThanOrEqual(layout.hero.left);
  expect(layout.qr.right).toBeLessThanOrEqual(layout.hero.right + 1);
}

async function exerciseOriginalHeroActions(page: Page) {
  for (const selector of ['#hero-stats-btn', '#hero-course-detail-btn']) {
    await page.locator(selector).click();
    await expect(page.locator('#course-info-popover')).toHaveClass(/popover-open/);
    await page.locator('#course-popover-close').click();
    // Existing popovers animate their card opacity while retaining a full-size
    // pointer-inert wrapper, so wrapper bounding-box visibility is misleading.
    await expect(page.locator('#course-info-popover')).toHaveAttribute('aria-hidden', 'true');
    await expect(page.locator('#course-info-popover')).not.toHaveClass(/popover-open/);
    await expect(page.locator('#course-info-popover .course-popover-card')).toHaveCSS('opacity', '0');
  }
  const members = page.locator('#hero-learning-btn');
  await expect(members).toBeVisible();
  await members.click();
  await expect(page.locator('#learning-progress-modal')).toBeVisible();
  await page.locator('#learning-modal-close').click();
  await expect(page.locator('#learning-progress-modal')).toHaveAttribute('aria-hidden', 'true');
  await expect(page.locator('#learning-progress-modal')).not.toHaveClass(/is-open/);
}

test.describe.serial('Classroom group QR full-page acceptance', () => {
  test.use({ reducedMotion: 'no-preference' });
  test.beforeAll(() => { fs.mkdirSync(evidenceDir, { recursive: true }); });

  test('teacher publishes and edits QR inside the complete classroom without breaking existing controls', async ({ page }, testInfo) => {
    test.setTimeout(180_000);
    const fixture = readFixture();
    const browserErrors: string[] = [];
    page.on('pageerror', error => browserErrors.push(error.message));
    await loginTeacher(page, fixture);
    await expectHealthUsesRuntimeDb(page, fixture);
    const endpoint = `/api/classrooms/${fixture.classOfferingId}/group-qr`;
    const initial = await (await page.request.get(endpoint)).json();
    const reset = await page.request.post(endpoint, {
      multipart: { description: '', revision: initial.revision, remove_image: 'true' },
    });
    expect(reset.status()).toBe(200);
    await page.goto(`/classroom/${fixture.classOfferingId}`);
    await expect(page.locator('[data-lanshare-island="classroom-page"]')).toBeAttached();
    await expect(page.locator('#assignment-panel')).toBeVisible();
    await expect(page.locator('#materials-panel')).toBeVisible();
    await expect(page.locator('#discussion-room')).toBeAttached();
    await exerciseOriginalHeroActions(page);

    const trigger = page.locator('[data-group-qr-open]');
    const dialog = page.locator('#classroom-group-qr-dialog');
    const description = page.locator('[name="description"]');
    const save = page.locator('[data-group-qr-save]');
    const preview = page.locator('[data-group-qr-preview]');
    const status = page.locator('#classroom-group-qr-status');
    await expect(trigger).toContainText('点击设置');
    const cardBox = await trigger.boundingBox();
    const actionsBox = await page.locator('.workspace-hero-actions').boundingBox();
    expect(actionsBox!.x + actionsBox!.width).toBeLessThanOrEqual(cardBox!.x + 1);
    await screenshot(page, 'full-teacher-empty-desktop.png');

    await openTeacherQr(page);
    await expect(save).toBeDisabled();
    await expect(page.locator('[data-group-qr-empty]')).toBeVisible();
    await page.locator('[data-group-qr-file]').setInputFiles({
      name: '班群二维码.png', mimeType: 'image/png', buffer: qrImage(),
    });
    await decoded(preview);
    await description.fill(introduction);
    await save.click();
    await expect(status).toContainText('已保存');
    await expect(save).toBeDisabled();
    await decoded(page.locator('[data-group-qr-thumbnail]'));
    await screenshot(page, 'full-teacher-dialog-desktop.png');
    await expectDialogFits(page);

    // Native Chromium dialogs may briefly return activeElement=BODY while Tab
    // reaches browser chrome; classroom controls must never receive that focus.
    await dialog.locator('[data-group-qr-close]').first().focus();
    const focusSequence: unknown[] = [];
    let modalFocusCount = 0;
    for (let index = 0; index < 15; index += 1) {
      await page.keyboard.press('Tab');
      const focus = await dialog.evaluate(element => ({
        inDialog: element.contains(document.activeElement),
        tag: document.activeElement?.tagName,
        id: document.activeElement?.id,
      }));
      focusSequence.push(focus);
      if (focus.inDialog) modalFocusCount += 1;
      expect(focus.inDialog || focus.tag === 'BODY', JSON.stringify(focus)).toBe(true);
    }
    expect(modalFocusCount).toBeGreaterThanOrEqual(10);
    await testInfo.attach('native-dialog-tab-focus', { body: JSON.stringify(focusSequence), contentType: 'application/json' });
    await page.keyboard.press('Escape');
    await expect(dialog).toBeHidden();
    await expect(trigger).toBeFocused();
    await screenshot(page, 'full-teacher-hero-desktop.png');
    await exerciseOriginalHeroActions(page);

    await openTeacherQr(page);
    await description.fill('暂存的修改，请勿意外丢失');
    await page.keyboard.press('Escape');
    await expect(page.locator('[data-group-qr-discard-panel]')).toBeVisible();
    await expect(description).toHaveValue('暂存的修改，请勿意外丢失');
    await page.locator('[data-group-qr-keep]').click();
    await expect(page.locator('[data-group-qr-discard-panel]')).toBeHidden();
    await dialog.locator('[data-group-qr-close]').first().click();
    await page.locator('[data-group-qr-discard]').click();
    await expect(dialog).toBeHidden();
    await openTeacherQr(page);
    await expect(description).toHaveValue(introduction);

    const originalImage = await preview.getAttribute('src');
    await page.locator('[data-group-qr-remove]').click();
    await expect(preview).toBeHidden();
    await page.locator('[data-group-qr-undo-image]').click();
    await decoded(preview);
    await expect(preview).toHaveAttribute('src', originalImage!);
    await expect(save).toBeDisabled();
    await page.keyboard.press('Escape');

    // Check real classroom responsive layout with normal animations, including
    // a short landscape viewport and narrow phone. Screenshots stay in artifacts.
    for (const viewport of [
      { width: 1024, height: 768 },
      { width: 390, height: 844 },
      { width: 320, height: 700 },
      { width: 844, height: 390 },
    ]) {
      await page.setViewportSize(viewport);
      await trigger.scrollIntoViewIfNeeded();
      await inspectClassroomLayout(page);
      if (viewport.width === 390) await screenshot(page, 'full-teacher-hero-mobile.png');
      await openTeacherQr(page);
      await decoded(preview);
      await expectDialogFits(page);
      if (viewport.width === 390) await screenshot(page, 'full-teacher-dialog-mobile.png');
      if (viewport.height === 390) await screenshot(page, 'full-teacher-dialog-landscape.png');
      await save.scrollIntoViewIfNeeded();
      await expect(save).toBeInViewport();
      await page.keyboard.press('Escape');
      await expect(dialog).toBeHidden();
    }
    await page.setViewportSize({ width: 1440, height: 980 });
    await page.reload();
    await expect(trigger).toContainText('点击放大');
    await openTeacherQr(page);
    await expect(description).toHaveValue(introduction);
    await decoded(preview);
    await page.keyboard.press('Escape');
    expect(await page.evaluate(() => getComputedStyle(document.body).overflow)).not.toBe('hidden');
    await testInfo.attach('full-classroom-browser-errors', { body: JSON.stringify(browserErrors), contentType: 'application/json' });
    expect(browserErrors).toEqual([]);
  });

  test('student enlarges, downloads and reads persisted QR without editing permissions', async ({ page }, testInfo) => {
    test.setTimeout(90_000);
    const fixture = readFixture();
    const browserErrors: string[] = [];
    page.on('pageerror', error => browserErrors.push(error.message));
    await loginStudent(page, fixture);
    await page.goto(`/classroom/${fixture.classOfferingId}`);
    await expect(page.locator('[data-lanshare-island="classroom-page"]')).toBeAttached();
    const trigger = page.locator('[data-group-qr-open]');
    const dialog = page.locator('#classroom-group-qr-dialog');
    await trigger.click();
    await expect(page.locator('[data-group-qr-description]')).toHaveText(introduction);
    await decoded(page.locator('[data-group-qr-preview]'));
    await expect(page.locator('[data-group-qr-form], [data-group-qr-upload], [data-group-qr-save], [data-group-qr-remove]')).toHaveCount(0);
    const endpoint = `/api/classrooms/${fixture.classOfferingId}/group-qr`;
    const forbidden = await page.request.post(endpoint, { multipart: { description: 'student overwrite', remove_image: 'true' } });
    expect(forbidden.status()).toBe(403);
    const downloadPromise = page.waitForEvent('download');
    await page.locator('[data-group-qr-download]').click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toMatch(/group-qr\.png$/);
    expect(await download.failure()).toBeNull();
    await screenshot(page, 'full-student-dialog-desktop.png');
    await page.keyboard.press('Escape');
    await expect(dialog).toBeHidden();
    await expect(trigger).toBeFocused();
    await exerciseOriginalHeroActions(page);
    await page.setViewportSize({ width: 390, height: 844 });
    await screenshot(page, 'full-student-hero-mobile.png');
    await trigger.click();
    await decoded(page.locator('[data-group-qr-preview]'));
    await expectDialogFits(page);
    await screenshot(page, 'full-student-dialog-mobile.png');
    await dialog.locator('[data-group-qr-close]').last().scrollIntoViewIfNeeded();
    await dialog.locator('[data-group-qr-close]').last().click();
    await expect(dialog).toBeHidden();
    await testInfo.attach('full-classroom-browser-errors', { body: JSON.stringify(browserErrors), contentType: 'application/json' });
    expect(browserErrors).toEqual([]);
  });

  test('unrelated student, teacher and anonymous browser cannot read QR or its image', async ({ browser, baseURL }) => {
    test.setTimeout(90_000);
    const fixture = readFixture();
    const context = await browser.newContext({ baseURL });
    const page = await context.newPage();
    const endpoint = `/api/classrooms/${fixture.classOfferingId}/group-qr`;
    try {
      // Seed independently so this permission scenario can be run on its own.
      await loginTeacher(page, fixture);
      const current = await (await page.request.get(endpoint)).json();
      const published = await page.request.post(endpoint, { multipart: {
        description: introduction,
        revision: current.revision,
        file: { name: 'classroom-group.png', mimeType: 'image/png', buffer: qrImage() },
      } });
      expect(published.status()).toBe(200);
      await context.clearCookies();
      await loginStudent(page, fixture, fixture.otherStudent);
      for (const suffix of ['', '/image', '/image?download=true']) {
        expect([403, 404]).toContain((await page.request.get(endpoint + suffix)).status());
      }
      await context.clearCookies();
      await loginTeacher(page, fixture, fixture.otherTeacher);
      for (const suffix of ['', '/image']) {
        expect([403, 404]).toContain((await page.request.get(endpoint + suffix)).status());
      }
      await context.clearCookies();
      for (const suffix of ['', '/image']) {
        const response = await page.request.get(endpoint + suffix, { maxRedirects: 0 });
        expect([302, 303, 401, 403]).toContain(response.status());
      }
    } finally {
      await context.close();
    }
  });
});
