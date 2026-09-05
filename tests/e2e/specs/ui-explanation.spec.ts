import { expect, test } from '@playwright/test';
import { collectBrowserErrors, expectNoBrowserErrors, loginTeacher, readFixture } from '../fixtures/p03';

// The shared explanation popover replaces native titles, topbar captions and
// long page leads. These checks pin the end-to-end contract: hover opens after
// the configured delay, content renders, and simplified headers stay short.

test.describe('unified explanation popover', () => {
  test('keyboard can enter help links and resume the original document order', async ({ page }) => {
    await page.goto('/teacher/login');
    await page.waitForFunction(() => Boolean((window as any).LanShareExplanation));
    await page.evaluate(() => {
      const fixture = document.createElement('section');
      fixture.id = 'explanation-keyboard-fixture';
      fixture.innerHTML = '<button id="help-before">前一个</button><button id="help-trigger" data-explain-toggle>说明</button><button id="help-after">下一个</button>';
      document.body.prepend(fixture);
      (window as any).LanShareExplanation.attach('#help-trigger', {
        title: '完整说明', text: '说明可以用键盘阅读，也可以返回原来的位置。',
        links: [{label: '查看材料', href: '/dashboard'}, {label: '查看课堂', href: '/dashboard#classes'}],
      });
    });
    const trigger = page.locator('#help-trigger');
    const panel = page.locator('#ui-explanation-popover');
    await trigger.focus();
    await expect(panel).toBeVisible();
    await expect(trigger).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(panel.getByRole('button', {name: '关闭说明'})).toBeFocused();
    await page.keyboard.press('Tab');
    await expect(panel.getByRole('link', {name: '查看材料'})).toBeFocused();
    await page.mouse.move(2, 2);
    await expect(panel).toBeVisible();
    await page.keyboard.press('Tab');
    await expect(panel.getByRole('link', {name: '查看课堂'})).toBeFocused();
    await page.keyboard.press('Tab');
    await expect(page.locator('#help-after')).toBeFocused();
    await expect(panel).toBeHidden();
    await trigger.focus();
    await trigger.press('Enter');
    await page.keyboard.press('Escape');
    await expect(trigger).toBeFocused();
    await expect(panel).toBeHidden();
    await expect(page.locator('#ui-explanation-popover')).toHaveCount(1);
  });

  test('ordinary annotated controls keep their normal Tab order', async ({ page }) => {
    await page.goto('/teacher/login');
    await page.waitForFunction(() => Boolean((window as any).LanShareExplanation));
    await page.evaluate(() => {
      const fixture = document.createElement('section');
      fixture.innerHTML = '<a id="annotated-action" href="/dashboard">进入课堂</a><button id="ordinary-next">下一项</button>';
      document.body.prepend(fixture);
      (window as any).LanShareExplanation.attach('#annotated-action', {text: '打开我的课堂。'});
    });
    await page.locator('#annotated-action').focus();
    await expect(page.locator('#ui-explanation-popover')).toBeVisible();
    await page.keyboard.press('Tab');
    await expect(page.locator('#ordinary-next')).toBeFocused();
    await expect(page.locator('#ui-explanation-popover')).toBeHidden();
  });

  test('long help stays inside the viewport and touch movement cancels long press', async ({ page }) => {
    await page.setViewportSize({width: 320, height: 600});
    await page.goto('/teacher/login');
    await page.waitForFunction(() => Boolean((window as any).LanShareExplanation));
    await page.evaluate(() => {
      const button = document.createElement('button');
      button.id = 'edge-help'; button.textContent = '说明'; button.dataset.explainToggle = '';
      button.style.cssText = 'position:fixed;right:0;bottom:0;width:44px;height:44px';
      document.body.append(button);
      (window as any).LanShareExplanation.attach(button, {text: '材料范围与学习记录的计算说明。'.repeat(45)});
    });
    const trigger = page.locator('#edge-help');
    await trigger.dispatchEvent('pointerdown', {pointerType: 'touch', pointerId: 7, clientX: 300, clientY: 580});
    await trigger.dispatchEvent('pointermove', {pointerType: 'touch', pointerId: 7, clientX: 300, clientY: 550});
    await page.waitForTimeout(750);
    await expect(page.locator('#ui-explanation-popover')).not.toBeVisible();
    await trigger.dispatchEvent('pointerdown', {pointerType: 'touch', pointerId: 8, clientX: 300, clientY: 580});
    const panel = page.locator('#ui-explanation-popover');
    await expect(panel).toBeVisible();
    await trigger.dispatchEvent('pointerup', {pointerType: 'touch', pointerId: 8});
    const bounds = await panel.boundingBox();
    expect(bounds).not.toBeNull();
    expect(bounds!.x).toBeGreaterThanOrEqual(0);
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(320);
    expect(bounds!.y).toBeGreaterThanOrEqual(0);
    expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(600);
    await page.keyboard.press('Escape');
    await expect(panel).toBeHidden();
  });
  test('manage page headers are short and explain triggers open the popover', async ({ page }, testInfo) => {
    const fixture = readFixture();
    const errors = collectBrowserErrors(page);
    await loginTeacher(page, fixture);

    await page.goto('/manage/teaching/classes');
    await page.waitForLoadState('networkidle').catch(() => undefined);

    const heading = page.locator('.manage-pagehead__title-row h2').first();
    await expect(heading).toHaveText('班级');

    const trigger = page.locator('.ui-explain-trigger').first();
    await expect(trigger).toBeVisible();
    await trigger.click();

    const popover = page.locator('#ui-explanation-popover');
    await expect(popover).toBeVisible();
    await expect(popover).toContainText('班级');
    await expect(popover).toContainText('学生名单');

    await page.keyboard.press('Escape');
    await expect(popover).toBeHidden();

    await expectNoBrowserErrors(errors, testInfo);
  });

  test('topbar actions expose captions through the popover instead of inline text', async ({ page }, testInfo) => {
    const fixture = readFixture();
    const errors = collectBrowserErrors(page);
    await loginTeacher(page, fixture);

    await page.goto('/manage/teaching/classes');
    await page.waitForLoadState('networkidle').catch(() => undefined);

    const action = page.locator('.app-topbar-action[data-explain]').first();
    await expect(action).toBeVisible();
    await expect(action.locator('small')).toHaveCount(0);
    await expect(action).toHaveAttribute('data-explain-text', /.+/);

    // Hover-and-hold matches the 2s desktop contract.
    await action.hover();
    const popover = page.locator('#ui-explanation-popover');
    await expect(popover).toBeVisible({ timeout: 5_000 });

    await page.mouse.move(4, 4);
    await expect(popover).toBeHidden();

    await expectNoBrowserErrors(errors, testInfo);
  });

  test('dashboard domain cards move descriptions into the popover', async ({ page }, testInfo) => {
    const fixture = readFixture();
    const errors = collectBrowserErrors(page);
    await loginTeacher(page, fixture);

    await page.goto('/dashboard');
    await page.waitForLoadState('networkidle').catch(() => undefined);

    await expect(page.locator('.dashboard-hero__eyebrow')).toHaveCount(0);

    const card = page.locator('.dashboard-domain-card[data-explain]').first();
    await expect(card).toBeVisible();
    await expect(card.locator('small')).toHaveCount(0);
    await expect(card).toHaveAttribute('data-explain-text', /.+/);

    await expectNoBrowserErrors(errors, testInfo);
  });

  test('sidebar navigation items explain themselves without native tooltips', async ({ page }, testInfo) => {
    const fixture = readFixture();
    const errors = collectBrowserErrors(page);
    await loginTeacher(page, fixture);

    await page.goto('/manage/teaching/materials');
    await page.waitForLoadState('networkidle').catch(() => undefined);

    const navItem = page.locator('.manage-nav-item[data-explain]').first();
    await expect(navItem).toBeVisible();
    await expect(navItem).not.toHaveAttribute('title', /.+/);
    await expect(navItem).toHaveAttribute('data-explain-title', /.+/);

    await expectNoBrowserErrors(errors, testInfo);
  });
});
