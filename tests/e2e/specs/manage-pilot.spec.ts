import AxeBuilder from '@axe-core/playwright';
import type { Locator, Page } from '@playwright/test';
import { test, expect, guardS3Page, readS3Fixture, readS3Rows, type S3Fixture } from '../fixtures/lq-s3';
import { loginTeacher } from '../fixtures/p03';

const pages = [
  ['courses', '/manage/library/courses', '#courseCardGrid'],
  ['classes', '/manage/teaching/classes', '#classList'],
  ['classroom-hub', '/manage/teaching/classroom-hub', '#offeringHubList'],
  ['semesters', '/manage/teaching/semesters', '#semesterList'],
  ['textbooks', '/manage/library/textbooks', '#textbookCardGrid'],
  ['lesson-plans', '/manage/library/lesson-plans', '[data-lp-grid]'],
  ['materials', '/manage/library/materials', '[data-testid="p03-materials-list"]'],
  ['users', '/manage/system/users', '#teacher-table-body'],
] as const;
const more = (page: Page) => page.locator('#manage-pilot-topbar > [data-lq-pane-open="actions"]');
const actions = (page: Page) => page.locator('#manage-pilot-topbar--lq-actions');
const repairedText: Record<string, (string | { selector: string; pseudo: string })[]> = {
  courses: ['.course-card-metric strong', '.course-card-alignment strong', '.course-card-sync-summary'],
  classes: ['.class-card-title-block h4', '.class-form-note', { selector: '#classRosterInput', pseudo: '::file-selector-button' }],
  semesters: ['.academic-context-block h4', '#semesterSummaryText', '.semester-calendar-overview-card strong', '.semester-header-cell.month', '#semesterList .academic-list-item:not(.is-active) .academic-list-main > strong', '#semesterList .academic-list-item:not(.is-active) .academic-list-main > p', '.semester-band-cell.is-gap .semester-band-cell__label', '#semesterList .academic-badge.is-success', '#semesterList .academic-badge.is-accent'],
  textbooks: ['.textbook-catalog-preview'],
  materials: ['.materials-name-copy strong', '.materials-row-time strong', '.materials-meta-item', '#materials-filters-toggle', '.materials-breadcrumb-count'],
  users: ['.um-hero-main h3', '.um-summary-item strong', '.um-panel tbody strong', '.um-panel .um-badge.is-super'],
};

// Only the exact neutral surfaces repaired in this pilot; no claim that every
// legacy control or authored document has migrated or passed a full-page audit.
async function expectRepairedContrast(page: Page, name: string) {
  for (const probe of repairedText[name] || []) {
    const { selector, pseudo } = typeof probe === 'string' ? { selector: probe, pseudo: null } : probe;
    const node = page.locator(selector).first(); await expect(node).toBeVisible();
    const result = await node.evaluate((element, pseudo) => {
      const rgb = (color: string): number[] => {
        const values = color.match(/[\d.]+/g)?.map(Number);
        if (!values || values.length < 3 || !color.startsWith('rgb')) throw Error(`unhandled computed color ${color}`);
        return [values[0], values[1], values[2], values[3] ?? 1];
      };
      const over = (fg: number[], bg: number[]) => fg.slice(0, 3).map((value, i) => value * fg[3] + bg[i] * (1 - fg[3]));
      const chain: Element[] = []; for (let node: Element | null = element; node; node = node.parentElement) chain.unshift(node);
      let background = [255, 255, 255], images: string[] = [];
      for (const node of chain) {
        const style = getComputedStyle(node), fill = rgb(style.backgroundColor);
        background = over(fill, background); if (fill[3] === 1) images = [];
        if (style.backgroundImage !== 'none') images.push(style.backgroundImage);
      }
      const style = getComputedStyle(element, pseudo);
      if (pseudo) {
        const fill = rgb(style.backgroundColor); background = over(fill, background);
        if (fill[3] === 1) images = [];
        if (style.backgroundImage !== 'none') images.push(style.backgroundImage);
      }
      const color = over(rgb(style.color), background);
      const luminance = (value: number[]) => value.map(v => v / 255).map(v => v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4).reduce((sum, v, i) => sum + v * [0.2126, 0.7152, 0.0722][i], 0);
      const a = luminance(color), b = luminance(background);
      return { ratio: (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05), images };
    }, pseudo);
    expect(result.images, `${name} ${selector}${pseudo || ''} probe requires solid/composited neutral surfaces`).toEqual([]);
    expect(result.ratio, `${name} ${selector}${pseudo || ''} foreground/background`).toBeGreaterThanOrEqual(4.5);
  }
}

async function openPilot(page: Page, route: string, admin = false) {
  const fixture = await guardS3Page(page);
  await loginTeacher(page, fixture, admin ? fixture.superTeacher : fixture.teacher);
  expect((await page.goto(route))?.status()).toBe(200);
  await expect(page.locator('body')).toHaveClass(/lq-manage-pilot/);
  await expect(page.locator('#manage-pilot-topbar')).toHaveAttribute('data-lq-enhanced', 'true');
  await expect(page.locator('[data-lq-manage-shell-status]')).toBeHidden();
  return fixture;
}

async function topbarClick(page: Page, selector: string) {
  if (await more(page).isVisible() && !await actions(page).isVisible()) await more(page).click();
  await page.locator(selector).click();
}

// These substitutes target a single authored endpoint and method. They prove
// failure retention through the real controller, not server authorization.
async function failEndpoint(page: Page, pathname: string, method: string, status = 503) {
  const matcher = (url: URL) => url.pathname === pathname;
  const state = { requests: 0, dispose: () => page.unroute(matcher) };
  await page.route(matcher, async route => {
    if (route.request().method() !== method) return route.fallback();
    state.requests++;
    await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify({ status: 'error', detail: 'S3 有界失败：输入保留', message: 'S3 有界失败：输入保留' }) });
  });
  return state;
}

async function expectFailure(page: Page, state: { requests: number }, input: Locator, value: string, submit: Locator) {
  await submit.click();
  await expect.poll(() => state.requests).toBe(1);
  await expect(input).toHaveValue(value);
  await expect(submit).toBeEnabled();
  await expect(page.getByText('S3 有界失败：输入保留', { exact: false }).first()).toBeVisible();
}

async function cancelNativeDelete(page: Page, button: Locator, pathname: string) {
  const failure = await failEndpoint(page, pathname, 'DELETE', 409);
  page.once('dialog', dialog => dialog.dismiss());
  await button.click();
  expect(failure.requests).toBe(0);
  await expect(button).toBeVisible();
  page.once('dialog', dialog => dialog.accept());
  await button.click();
  await expect.poll(() => failure.requests).toBe(1);
  await expect(button).toBeVisible();
  await expect(page.getByText('S3 有界失败：输入保留', { exact: false }).first()).toBeVisible();
}

function resourceIds(fixture: S3Fixture) {
  const rows = readS3Rows<{ course_id: number; class_id: number; semester_id: number }>(
    'SELECT course_id,class_id,semester_id FROM class_offerings WHERE id=?', [fixture.classOfferingId]);
  expect(rows).toHaveLength(1); return rows[0];
}

test.describe('S3 LQ eight explicit manage pilots', () => {
  for (const width of [1440, 390]) for (const appearance of ['light', 'dark']) {
    test(`eight real pages preserve controller hooks and migrated shell axe ${width}/${appearance}`, async ({ page }, info) => {
      test.setTimeout(180_000);
      await page.setViewportSize({ width, height: 900 });
      const fixture = await guardS3Page(page);
      await loginTeacher(page, fixture);
      const writes: string[] = [];
      page.on('request', request => {
        if (request.method() === 'PATCH' && new URL(request.url()).pathname.includes('ui-preferences')) writes.push(request.url());
      });
      for (const [name, url, hook] of pages) {
        if (name === 'users') await loginTeacher(page, fixture, fixture.superTeacher);
        expect((await page.goto(url))?.status()).toBe(200);
        await expect(page.locator('#manage-pilot-topbar')).toHaveAttribute('data-lq-enhanced', 'true');
        await expect(page.locator(hook)).toBeAttached();
        await expect(page.locator('[data-page-head].lq-page-head')).toHaveCount(1);
        await page.evaluate(appearance => {
          const owner = (document as any)[Symbol.for('lanshare.theme.installation')];
          if (!owner) throw Error('shared theme installation missing');
          owner.refresh({ palette_key: 'indigo', appearance, glass: 'off' });
        }, appearance);
        await expect(page.locator('html')).toHaveAttribute('data-appearance', appearance);
        await expect(page.locator('.app-topbar-menu')).toHaveCount(0);
        await expect(page.locator('[data-lq-manage-shell-status]')).toBeHidden();
        expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width + 1);
        if (width === 1440) {
          expect((await page.locator('#manage-pilot-topbar').boundingBox())!.height, `${name} compact desktop topbar`).toBeLessThanOrEqual(64);
          const insights = page.locator('[data-page-head] > .page-head__aside:has(> .manage-pagehead__insights)');
          if (await insights.count()) {
            const head = (await page.locator('[data-page-head]').boundingBox())!, aside = (await insights.boundingBox())!;
            expect(aside.width, `${name} original full-width insight grid`).toBeGreaterThanOrEqual(head.width - 1);
          }
        }
        if (appearance === 'dark') {
          await expectRepairedContrast(page, name);
          const surfaces: Record<string, [string, string]> = {
            courses: ['.course-card-metric', '--ls-surface-2'], classes: ['.class-manage-card', '--ls-surface-1'],
            semesters: ['.semester-calendar-panel', '--ls-surface-1'], textbooks: ['.textbook-catalog-preview', '--ls-surface-2'],
            materials: ['.materials-card', '--ls-surface-1'], users: ['.um-hero-main', '--ls-surface-1'],
          };
          if (surfaces[name]) {
            const [selector, token] = surfaces[name], node = page.locator(selector).first();
            await expect(node).toBeVisible();
            const pair = await node.evaluate((element, token) => {
              const probe = document.createElement('span'); probe.style.background = `hsl(var(${token}))`; element.append(probe);
              const expected = getComputedStyle(probe).backgroundColor; probe.remove();
              const style = getComputedStyle(element); return [style.backgroundColor, expected, style.backgroundImage];
            }, token);
            expect(pair[0], `${name} exact neutral token background`).toBe(pair[1]); expect(pair[2]).toBe('none');
          }
        }
        if (name === 'courses') {
          const rects = await page.locator('.course-card').first().locator('.course-card-metric,.course-card-alignment').evaluateAll(nodes => nodes.map(node => {
            const r = node.getBoundingClientRect(); return { x: r.x, y: r.y, right: r.right, bottom: r.bottom };
          }));
          expect(rects.length).toBeGreaterThan(1);
          for (let i = 0; i < rects.length; i++) for (let j = i + 1; j < rects.length; j++) {
            const a = rects[i], b = rects[j];
            expect(Math.min(a.right, b.right) - Math.max(a.x, b.x) > 1 && Math.min(a.bottom, b.bottom) - Math.max(a.y, b.y) > 1, 'course metadata cells overlap').toBe(false);
          }
        }
        if (name === 'materials' && width === 390) {
          const row = page.locator('.materials-manage-row').first();
          const main = (await row.locator('.materials-row-main').boundingBox())!, time = (await row.locator('.materials-row-time').boundingBox())!, controls = (await row.locator('.materials-row-actions').boundingBox())!;
          expect(time.y).toBeGreaterThanOrEqual(main.y + main.height - 1);
          expect(controls.y).toBeGreaterThanOrEqual(time.y + time.height - 1);
        }
        const scan = await new AxeBuilder({ page }).include('#manage-pilot-topbar').include('[data-page-head]').include('[data-lq-manage-sidebar]').analyze();
        expect(scan.violations, `${name} migrated shell/head: ${JSON.stringify(scan.violations)}`).toEqual([]);
        await page.screenshot({ path: info.outputPath(`${name}-${appearance}-${width}.png`), fullPage: true });
        if (width === 390) {
          await more(page).click();
          await expect(actions(page)).toBeVisible();
          expect((await new AxeBuilder({ page }).include('#manage-pilot-topbar').analyze()).violations).toEqual([]);
          await actions(page).getByRole('button', { name: '关闭更多操作', exact: true }).click();
          await expect(more(page)).toBeFocused();
        }
      }
      expect(writes).toEqual([]);
    });
  }

  test('course edit failure retains draft; real lifecycle review cancellation sends no delete', async ({ page }) => {
    const fixture = await openPilot(page, '/manage/library/courses');
    const { course_id: id } = resourceIds(fixture);
    const before = readS3Rows('SELECT * FROM courses WHERE id=?', [id]);
    await page.locator(`[data-action="edit-course"][data-course-id="${id}"]`).click();
    const input = page.locator('#courseNameInput'); await input.fill('S3 未保存课程草稿');
    const state = await failEndpoint(page, '/api/manage/courses/save', 'POST');
    await expectFailure(page, state, input, 'S3 未保存课程草稿', page.locator('#courseSaveBtn'));
    await page.reload();
    const deletion = await failEndpoint(page, `/api/manage/courses/${id}`, 'DELETE');
    await page.locator(`[data-action="delete-course"][data-course-id="${id}"]`).click();
    await expect(page.locator('[data-teaching-delete-review]')).toBeVisible();
    await expect(page.locator('[data-teaching-delete-content]')).not.toBeEmpty();
    await page.locator('.lp-modal').getByRole('button', { name: '取消', exact: true }).click();
    expect(deletion.requests).toBe(0);
    expect(readS3Rows('SELECT * FROM courses WHERE id=?', [id])).toEqual(before);
  });

  test('class real custom form failure and student drawer remain usable; delete review cancel is read-only', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 900 });
    const fixture = await openPilot(page, '/manage/teaching/classes');
    const { class_id: id } = resourceIds(fixture);
    await topbarClick(page, '#customClassCreateTopBtn');
    await expect(actions(page)).toBeHidden();
    const input = page.locator('#classCustomNameInput'); await input.fill('S3 未保存班级草稿');
    const state = await failEndpoint(page, '/api/manage/classes/custom', 'POST');
    await expectFailure(page, state, input, 'S3 未保存班级草稿', page.locator('#classCustomCreateForm button[type="submit"]'));
    await page.locator('#classCustomCreateCancel').click();
    await page.locator(`[data-action="open-students"][data-class-id="${id}"]`).click();
    await expect(page.locator('#classStudentDrawer')).toBeVisible();
    await page.locator('#classStudentDrawerClose').click();
    const deletion = await failEndpoint(page, `/api/manage/classes/${id}`, 'DELETE');
    await page.locator(`[data-action="delete-class"][data-class-id="${id}"]`).click();
    await expect(page.locator('[data-teaching-delete-review]')).toBeVisible();
    await page.locator('.lp-modal').getByRole('button', { name: '取消', exact: true }).click();
    expect(deletion.requests).toBe(0);
    await more(page).click(); await expect(actions(page)).toBeVisible();
  });

  test('offering original iframe keeps its form owner and failed save; cancel/409 delete preserves card', async ({ page }) => {
    const fixture = await openPilot(page, '/manage/teaching/classroom-hub');
    const id = fixture.s3.manageOfferingId;
    const preview = page.waitForResponse(response => new URL(response.url()).pathname === '/api/manage/class_offerings/preview' && response.request().method() === 'POST');
    await page.locator(`[data-action="edit-config"][href*="offering_id=${id}"]`).click();
    expect((await preview).ok()).toBe(true);
    const frame = page.frameLocator('#offeringHubDrawerFrame');
    await expect(frame.locator('#offeringIdInput')).toHaveValue(String(id));
    await expect(frame.locator('.manage-sidebar')).toHaveCount(0);
    await expect(frame.locator('#manage-pilot-topbar')).toHaveCount(0);
    // The first response headers can precede the editor accepting its preview.
    // Review an explicit refresh through the original control before saving.
    const reviewedPreview = page.waitForResponse(response => new URL(response.url()).pathname === '/api/manage/class_offerings/preview' && response.request().method() === 'POST');
    await frame.locator('#offeringPreviewBtn').click();
    const reviewed = await reviewedPreview;
    expect(reviewed.ok()).toBe(true);
    await reviewed.finished();
    const projection = await reviewed.json();
    expect(projection.plan_revision).toMatch(/^[0-9a-f]{64}$/);
    expect(projection.edit_impact.blockers).toEqual([]);
    await expect(frame.locator('#offeringPreviewList')).toContainText('S3 课堂内容 2');
    await frame.locator('#offeringPreviewList').evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    const formBefore = await frame.locator('#offeringSaveForm').evaluate(form => {
      (window as any).s3Form = form; return Array.from(form.querySelectorAll('input,select')).map((node: any) => [node.id, node.value]);
    });
    const failure = await failEndpoint(page, '/api/manage/class_offerings/save', 'POST');
    await frame.locator('#offeringSaveBtn').click();
    await expect.poll(() => failure.requests).toBe(1);
    await expect(frame.locator('#offeringSaveBtn')).toBeEnabled();
    expect(await frame.locator('#offeringSaveForm').evaluate(form => form === (window as any).s3Form)).toBe(true);
    expect(await frame.locator('#offeringSaveForm').evaluate(form => Array.from(form.querySelectorAll('input,select')).map((node: any) => [node.id, node.value]))).toEqual(formBefore);
    await page.locator('#offeringHubDrawerClose').click();
    await expect(page.locator('#offeringHubEditDrawer')).toBeHidden();
    await cancelNativeDelete(page, page.locator(`[data-action="delete-offering"][data-offering-id="${id}"]`), `/api/manage/class_offerings/${id}`);
  });

  test('semester actual edit/validation and failure preserve dates; delete cancellation/409 preserves row', async ({ page }) => {
    const fixture = await openPilot(page, '/manage/teaching/semesters');
    const id = fixture.s3.semesterId;
    await page.locator(`[data-action="edit"][data-semester-id="${id}"]`).click();
    const dates = await page.locator('#semesterStartInput,#semesterEndInput').evaluateAll(nodes => nodes.map((node: any) => node.value));
    const input = page.locator('#semesterNameInput'); await input.fill('S3 未保存学期草稿');
    const state = await failEndpoint(page, '/api/manage/semesters/save', 'POST');
    await expectFailure(page, state, input, 'S3 未保存学期草稿', page.locator('#semesterSubmitBtn'));
    expect(await page.locator('#semesterStartInput,#semesterEndInput').evaluateAll(nodes => nodes.map((node: any) => node.value))).toEqual(dates);
    await page.locator('#semesterModalCloseBtn').click();
    await cancelNativeDelete(page, page.locator(`[data-action="delete"][data-semester-id="${id}"]`), `/api/manage/semesters/${id}`);
  });

  test('textbook failure preserves native File and JSON values; dedicated fixture saves and reads back', async ({ page }) => {
    const fixture = await openPilot(page, '/manage/library/textbooks'); const id = fixture.s3.textbookId;
    const edit = page.locator(`[data-action="edit"][data-textbook-id="${id}"]`);
    await edit.click();
    const original = await page.locator('#textbookTitleInput').inputValue();
    const input = page.locator('#textbookTitleInput'); await input.fill('S3 教材失败草稿');
    await page.locator('#textbookAttachmentInput').setInputFiles({ name: 's3-local-note.txt', mimeType: 'text/plain', buffer: Buffer.from('synthetic only') });
    const chips = await page.locator('#textbookAuthorsJsonInput,#textbookTagsJsonInput,#textbookRemoveAttachmentInput').evaluateAll(nodes => nodes.map((node: any) => node.value));
    const state = await failEndpoint(page, '/api/manage/textbooks/save', 'POST');
    await expectFailure(page, state, input, 'S3 教材失败草稿', page.locator('#textbookSubmitBtn'));
    expect(await page.locator('#textbookAttachmentInput').evaluate((node: HTMLInputElement) => node.files?.[0]?.name)).toBe('s3-local-note.txt');
    expect(await page.locator('#textbookAuthorsJsonInput,#textbookTagsJsonInput,#textbookRemoveAttachmentInput').evaluateAll(nodes => nodes.map((node: any) => node.value))).toEqual(chips);
    await state.dispose();
    await page.locator('#textbookAttachmentInput').setInputFiles([]);
    const savedTitle = `${original} S3读回`;
    await input.fill(savedTitle);
    const saved = page.waitForResponse(response => new URL(response.url()).pathname === '/api/manage/textbooks/save' && response.request().method() === 'POST');
    const savedNavigation = page.waitForNavigation({ waitUntil: 'domcontentloaded' });
    await page.locator('#textbookSubmitBtn').click(); expect((await saved).ok()).toBe(true); await savedNavigation;
    await expect.poll(() => readS3Rows<{ title: string }>('SELECT title FROM textbooks WHERE id=?', [id])[0]?.title).toBe(savedTitle);
    await edit.click(); await expect(input).toHaveValue(savedTitle);
    await input.fill(original);
    const restored = page.waitForResponse(response => new URL(response.url()).pathname === '/api/manage/textbooks/save' && response.request().method() === 'POST');
    const restoredNavigation = page.waitForNavigation({ waitUntil: 'domcontentloaded' });
    await page.locator('#textbookSubmitBtn').click(); expect((await restored).ok()).toBe(true); await restoredNavigation;
    await cancelNativeDelete(page, page.locator(`[data-action="delete"][data-textbook-id="${id}"]`), `/api/manage/textbooks/${id}`);
  });

  test('lesson-plan real attribute form preserves draft on failure and delete cancel/failure retains card', async ({ page }) => {
    const fixture = await openPilot(page, '/manage/library/lesson-plans');
    const capture = fixture as S3Fixture & { lqCaptureRoutes: { teacher: [string, string][] } };
    const editor = capture.lqCaptureRoutes.teacher.find(([name]) => name === 'lesson-plan-editor')?.[1];
    expect(editor).toBeTruthy(); const id = editor!.split('/')[2];
    await page.locator(`[data-action="attributes"][data-id="${id}"]`).click();
    const input = page.locator('[data-lp-form-attr] [name="title"]'); await input.fill('S3 未保存教案草稿');
    const failure = await failEndpoint(page, `/api/lesson-plans/${id}/attributes`, 'PATCH');
    await expectFailure(page, failure, input, 'S3 未保存教案草稿', page.locator('[data-lp-submit]'));
    await page.locator('.lp-modal__close').click();
    const deletion = await failEndpoint(page, `/api/lesson-plans/${id}`, 'DELETE');
    const button = page.locator(`[data-action="delete"][data-id="${id}"]`);
    await button.click(); await page.locator('[data-pm-confirm-cancel]').click(); expect(deletion.requests).toBe(0);
    await button.click(); await page.locator('[data-pm-confirm-ok]').click();
    await expect.poll(() => deletion.requests).toBe(1); await expect(button).toBeEnabled();
  });

  test('materials mobile submenu retains its single owner; original create failure preserves input and more reopens', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 900 });
    await openPilot(page, '/manage/library/materials');
    await expect(page.getByTestId('p03-materials-list')).toBeVisible();
    await more(page).click(); await page.locator('#materials-create-menu-btn').click();
    await expect(actions(page)).toBeVisible(); await expect(page.locator('#materials-create-dropdown')).toBeVisible();
    await page.locator('#materials-create-folder-btn').click(); await expect(actions(page)).toBeHidden();
    const input = page.locator('#materials-create-node-name'); await input.fill('S3 未保存资料目录');
    const failure = await failEndpoint(page, '/api/materials/folders', 'POST');
    await expectFailure(page, failure, input, 'S3 未保存资料目录', page.locator('#materials-create-node-submit-btn'));
    await page.locator('#materials-create-node-modal [data-dismiss="modal"]').first().click();
    await more(page).click(); await expect(actions(page)).toBeVisible();
  });

  test('materials original properties and delete review retain existing item after explicit failure', async ({ page }) => {
    await openPilot(page, '/manage/library/materials');
    const row = page.locator('.materials-manage-row').first(); await expect(row).toBeVisible();
    const id = await row.getAttribute('data-id'); expect(id).toMatch(/^\d+$/);
    await row.locator('[data-action="details"]').click();
    const input = page.locator('[data-property-name]'); await input.fill('S3 未保存资料属性');
    const failure = await failEndpoint(page, `/api/materials/${id}/attributes`, 'PATCH');
    await expectFailure(page, failure, input, 'S3 未保存资料属性', page.locator('[data-detail-action="save-properties"]'));
    const deletion = await failEndpoint(page, `/api/materials/${id}`, 'DELETE', 409);
    const button = page.locator('[data-detail-action="delete"]');
    await button.click();
    await page.locator('[data-material-delete-cancel],[data-pm-confirm-cancel]').click(); expect(deletion.requests).toBe(0);
    await button.click(); await page.locator('[data-material-delete-confirm],[data-pm-confirm-ok]').click();
    await expect.poll(() => deletion.requests).toBe(1); await expect(button).toBeEnabled();
    await expect(input).toHaveValue('S3 未保存资料属性');
  });

  test('superadmin real account edit fails without clearing draft, own delete stays disabled, other delete cancels/409', async ({ page }) => {
    const fixture = await openPilot(page, '/manage/system/users', true), id = fixture.otherTeacher.id;
    await expect(page.locator(`[data-teacher-row][data-id="${fixture.superTeacher.id}"] [data-action="delete"]`)).toBeDisabled();
    const row = page.locator(`[data-teacher-row][data-id="${id}"]`);
    await row.locator('[data-action="edit"]').click();
    const input = page.locator('#edit-name'); await input.fill('S3 未保存教师资料');
    if (!await page.locator('#edit-school-name').inputValue()) await page.locator('#edit-school-name').fill('S3合成学校');
    if (!await page.locator('#edit-department').inputValue()) await page.locator('#edit-department').fill('S3合成系部');
    const failure = await failEndpoint(page, `/api/manage/system/teachers/${id}`, 'POST');
    await expectFailure(page, failure, input, 'S3 未保存教师资料', page.locator('#teacher-edit-form button[type="submit"]'));
    await page.locator('#teacher-edit-modal [data-close-modal]').first().click();
    await cancelNativeDelete(page, row.locator('[data-action="delete"]'), `/api/manage/system/teachers/${id}`);
  });
});
