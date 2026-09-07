import { expect, test, type Page, type Locator } from '@playwright/test';
import { loginStudent, readFixture, type P03Fixture } from '../fixtures/p03';

type Fixture = P03Fixture & { uiV3Synthetic: boolean; visualSessionIds: number[] };
const fixture = () => readFixture() as Fixture;

// Sample actual rendered frames around a real mouse click, without extending
// animation durations in test CSS or pausing the browser's animation clock.
async function framesAfterClick(page: Page, button: Locator, selector: string) {
  await page.evaluate(selector => {
    (window as any).__motionFrames = new Promise(resolve => document.addEventListener('click', () => {
      const frames: { opacity: number; height: number; text: string }[] = [];
      const start = performance.now();
      const tick = () => {
        const node = document.querySelector<HTMLElement>(selector);
        if (node && !node.hidden) frames.push({ opacity: Number(getComputedStyle(node).opacity), height: node.getBoundingClientRect().height, text: node.textContent || '' });
        if (performance.now() - start < 700) requestAnimationFrame(tick);
        else resolve(frames);
      };
      requestAnimationFrame(tick);
    }, { capture: true, once: true }));
  }, selector);
  await button.click();
  return await page.evaluate(() => (window as any).__motionFrames) as { opacity: number; height: number; text: string }[];
}
const hasIntermediate = (frames: { opacity: number }[]) => frames.some(frame => frame.opacity > .03 && frame.opacity < .97);

test.beforeAll(() => expect(fixture().uiV3Synthetic).toBe(true));

test('real timetable mouse hover expands and contracts smoothly, and cultivation fades out', async ({ page }, testInfo) => {
  await loginStudent(page, fixture());
  await page.locator('[data-csd-stage] .cs-card.is-active').click();
  const lesson = page.locator('.cs-expand .cs-lesson--cell').first();
  await expect(lesson).toBeVisible();
  await page.mouse.move(2, 2);
  const width = await lesson.evaluate(node => node.getBoundingClientRect().width);
  await page.evaluate(() => {
    (window as any).__courseWidths = [];
    const end = performance.now() + 1400;
    const tick = () => {
      const node = document.querySelector('.cs-expand .cs-lesson--cell');
      if (node) (window as any).__courseWidths.push(node.getBoundingClientRect().width);
      if (performance.now() < end) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  });
  await lesson.hover();
  await expect.poll(() => lesson.evaluate(node => node.getAnimations().filter(a => a.playState === 'running').length)).toBe(0);
  const expanded = await lesson.evaluate(node => node.getBoundingClientRect().width);
  expect(expanded).toBeGreaterThan(width + 5);
  await testInfo.attach('real-timetable-preview', { body: await page.screenshot(), contentType: 'image/png' });
  await page.mouse.move(2, 2);
  await expect.poll(() => lesson.evaluate(node => Math.abs(node.getBoundingClientRect().width - (node.parentElement!.getBoundingClientRect().width)))) .toBeLessThan(2);
  const widths = await page.evaluate(() => (window as any).__courseWidths) as number[];
  expect(widths.filter(value => value > width + 2 && value < expanded - 2).length).toBeGreaterThan(3);
  await testInfo.attach('actual-hover-widths', { body: JSON.stringify(widths), contentType: 'application/json' });
  await page.goto(`/classroom/${fixture().classOfferingId}`);
  const cultivation = page.locator('.cw-cultivation-entry');
  await cultivation.click();
  await expect(page.locator('#learning-progress-modal')).toHaveCSS('opacity', '1');
  const leaving = await framesAfterClick(page, page.locator('#learning-modal-close'), '#learning-progress-modal');
  expect(hasIntermediate(leaving)).toBe(true);
  await expect(page.locator('#learning-progress-modal')).toBeHidden();
  await expect(cultivation).toBeFocused();
});

test('a dialog reverses from its current frame and restores fixed positioning at rest', async ({ page }, testInfo) => {
  await loginStudent(page, fixture());
  const result = await page.evaluate(async () => {
    const wait = (time: number) => new Promise(resolve => setTimeout(resolve, time));
    const nextFrame = () => new Promise(resolve => requestAnimationFrame(resolve));
    const trigger = [...document.querySelectorAll<HTMLButtonElement>('button')].find(button => button.textContent === '全部事项与历史')!;
    trigger.click();
    await wait(75);
    const dialog = document.querySelector<HTMLElement>('.dw-dialog')!;
    const beforeClose = Number(getComputedStyle(dialog).opacity);
    dialog.querySelector<HTMLButtonElement>('.ui-dialog-close')!.click();
    await nextFrame();
    const afterClose = Number(getComputedStyle(dialog).opacity);
    await wait(55);
    const beforeReopen = Number(getComputedStyle(dialog).opacity);
    trigger.click();
    await nextFrame();
    const afterReopen = Number(getComputedStyle(dialog).opacity);
    await wait(350);
    return { beforeClose, afterClose, beforeReopen, afterReopen, sameNode: dialog === document.querySelector('.dw-dialog'), scale: getComputedStyle(dialog).scale, opacity: getComputedStyle(dialog).opacity };
  });
  expect(result.beforeClose).toBeGreaterThan(.03);
  expect(result.beforeClose).toBeLessThan(.97);
  expect(Math.abs(result.afterClose - result.beforeClose)).toBeLessThan(.2);
  expect(Math.abs(result.afterReopen - result.beforeReopen)).toBeLessThan(.2);
  expect(result.sameNode).toBe(true);
  expect(result.scale).toBe('none');
  expect(result.opacity).toBe('1');
  await testInfo.attach('rapid-reversal', { body: JSON.stringify(result), contentType: 'application/json' });
});

test('dashboard dialogs, todo and help retain intermediate opening and closing frames', async ({ page }, testInfo) => {
  await loginStudent(page, fixture());
  const history = page.getByRole('button', { name: '全部事项与历史', exact: true });
  const entering = await framesAfterClick(page, history, '.dw-dialog');
  expect(hasIntermediate(entering), JSON.stringify(entering.map(f => ({opacity:f.opacity,height:f.height})))).toBe(true);
  const leaving = await framesAfterClick(page, page.locator('.dw-dialog .ui-dialog-close'), '.dw-dialog');
  expect(hasIntermediate(leaving)).toBe(true);
  await expect(page.locator('.dw-dialog')).toHaveCount(0);
  await expect(history).toBeFocused();
  const add = page.locator('.dw-focus [data-agenda-add-todo]');
  const todo = page.locator('.agenda-todo-modal').filter({ has: page.locator('#agendaTodoForm') });
  const todoIn = await framesAfterClick(page, add, '.agenda-todo-modal__card');
  expect(hasIntermediate(todoIn)).toBe(true);
  await todo.locator('[name="title"]').fill('动画退出期间仍保留的内容');
  const todoOut = await framesAfterClick(page, todo.locator('button[data-todo-close]').first(), '.agenda-todo-modal__card');
  expect(hasIntermediate(todoOut)).toBe(true);
  await expect(todo).toBeHidden();
  await expect(add).toBeFocused();
  // Shared help also lives inside owning dialog focus scopes.
  await page.evaluate(async () => {
    const module = await import('/static/js/ui_explanation.js');
    module.openExplanation(document.querySelector('.dw-focus [data-agenda-add-todo]'), { title: '待办说明', text: '帮助提示双向过渡验证' });
  });
  await expect(page.locator('.ui-explain-popover')).toHaveCSS('opacity', '1');
  const helpOut = await framesAfterClick(page, page.locator('.ui-explain-popover__close'), '.ui-explain-popover');
  expect(hasIntermediate(helpOut)).toBe(true);
  await expect(page.locator('.ui-explain-popover')).toBeHidden();
  await testInfo.attach('actual-motion-frames', { body: JSON.stringify({ entering, leaving, todoIn, todoOut, helpOut }), contentType: 'application/json' });
});

test('lesson contents remain mounted during exit and materials fade before returning focus', async ({ page }, testInfo) => {
  const f = fixture();
  await loginStudent(page, f);
  await page.goto(`/classroom/${f.classOfferingId}`);
  const lesson = page.locator(`#teachingTimelineScroll [data-session-id="${f.visualSessionIds[2]}"]`);
  const entering = await framesAfterClick(page, lesson, '.cw-dialog');
  expect(hasIntermediate(entering), JSON.stringify(entering.map(f => ({opacity:f.opacity,height:f.height})))).toBe(true);
  const title = await page.locator('.cw-dialog-heading').innerText();
  await page.evaluate(() => {
    const overlay = document.querySelector<HTMLElement>('[data-ui-dialog-overlay]')!;
    (window as any).__closingBackdrop = null;
    const observer = new MutationObserver(() => {
      if (overlay.dataset.state === 'closed') {
        (window as any).__closingBackdrop = getComputedStyle(overlay).zIndex;
        observer.disconnect();
      }
    });
    observer.observe(overlay, { attributes: true, attributeFilter: ['data-state'] });
  });
  const leaving = await framesAfterClick(page, page.locator('.cw-dialog .ui-dialog-close'), '.cw-dialog');
  expect(hasIntermediate(leaving)).toBe(true);
  expect(await page.evaluate(() => (window as any).__closingBackdrop)).toBe('10040');
  expect(leaving.filter(frame => frame.opacity > 0).every(frame => frame.text.includes(title.split('\n')[0]))).toBe(true);
  await expect(lesson).toBeFocused();
  await lesson.click();
  await page.locator('#teachingSessionOpenMaterialBtn').click();
  const list = page.locator('.ls-mat-popup');
  await expect(list).toHaveCSS('opacity', '1');
  await expect(list.locator('[data-open-material]')).toHaveCount(2);
  const materialOut = await framesAfterClick(page, list.locator('[data-close-mat-popup]'), '.ls-mat-popup');
  expect(hasIntermediate(materialOut)).toBe(true);
  await expect(list).toBeHidden();
  await expect(page.locator('#teachingSessionOpenMaterialBtn')).toBeVisible();
  await testInfo.attach('lesson-motion-frames', { body: JSON.stringify({ entering, leaving, materialOut }), contentType: 'application/json' });
});

test('activity panels crossfade without replacing drafts or losing rapid tab selection', async ({ page }, testInfo) => {
  await loginStudent(page, fixture());
  await page.goto(`/classroom/${fixture().classOfferingId}`);
  const discussion = page.locator('#classroom-activity-tab-discussion');
  await discussion.click();
  await page.locator('#chat-input').fill('切换活动时保留这个草稿');
  await page.evaluate(() => { (window as any).__draftNode = document.querySelector('#chat-input'); });
  const frames = await framesAfterClick(page, page.locator('#classroom-activity-tab-polls'), '[data-classroom-activity-panel="discussion"]');
  expect(hasIntermediate(frames), JSON.stringify(frames.map(f => ({opacity:f.opacity,height:f.height})))).toBe(true);
  await page.locator('#classroom-activity-tab-resources').click();
  await discussion.click();
  await page.locator('#classroom-activity-tab-polls').click();
  await discussion.click();
  await expect(page.locator('.classroom-activity-panels')).not.toHaveClass(/is-switching/);
  await expect(page.locator('#chat-input')).toHaveValue('切换活动时保留这个草稿');
  expect(await page.evaluate(() => (window as any).__draftNode === document.querySelector('#chat-input'))).toBe(true);
  await expect(page.locator('[data-classroom-activity-panel]:not([hidden])')).toHaveCount(1);
  await expect(page.locator('[data-classroom-activity-panel="discussion"]')).toHaveAttribute('aria-hidden', 'false');
  await testInfo.attach('activity-motion-frames', { body: JSON.stringify(frames), contentType: 'application/json' });
});

test('reduced motion keeps dialogs and activity navigation functional without an exit delay', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await loginStudent(page, fixture());
  await page.getByRole('button', { name: '全部事项与历史', exact: true }).click();
  await expect(page.locator('.dw-dialog')).toHaveCSS('animation-name', 'none');
  await page.keyboard.press('Escape');
  await expect(page.locator('.dw-dialog')).toHaveCount(0);
  await page.goto(`/classroom/${fixture().classOfferingId}`);
  await page.locator('#classroom-activity-tab-polls').click();
  await expect(page.locator('.classroom-activity-panels')).not.toHaveClass(/is-switching/);
  await expect(page.locator('[data-classroom-activity-panel]:not([hidden])')).toHaveCount(1);
});
