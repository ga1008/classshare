import { test, expect, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import fs from 'node:fs';
import path from 'node:path';

async function installProcessModal(page: Page) {
  await page.evaluate(async () => {
    const w = window as any;
    w.processModal = await import('/static/js/process_material_modal.js');
    w.processClosed = 0; w.allowProcessClose = true;
    for (const id of ['modal-last', 'native-before']) document.getElementById(id)!.addEventListener('click', () => {
      w.processChild = w.processModal.openProcessMaterialModal('材料删除检查', '<input id="process-draft" value="保留内容">', {
        footerHtml: '<button data-pm-close>取消材料删除</button>',
        canClose: () => w.allowProcessClose,
        onClose: () => w.processClosed++,
      });
    });
  });
}

test.describe('LQ nested process-material compatibility', () => {
  test.beforeEach(async ({ page }) => { await mount(page); await installProcessModal(page); });

  test('existing modal parent keeps its draft and one lock across twenty child cancellations', async ({ page }) => {
    await page.locator('#opener').click();
    await page.locator('#modal-date').fill('2026-11-09');
    await page.keyboard.press('Escape');
    await expect(page.locator('.ls-dp-pop')).toHaveCount(0);
    await expect(page.locator('#modal-a')).toBeVisible();
    for (let cycle = 0; cycle < 20; cycle++) {
      await page.locator('#modal-last').click();
      await expect(page.locator('.lp-modal-overlay')).toHaveAttribute('data-lq-layer-state', /open/);
      await page.getByRole('button', { name: '取消材料删除', exact: true }).click();
      await expect(page.locator('.lp-modal-overlay')).toHaveCount(0);
      await expect(page.locator('#modal-last')).toBeFocused();
      await expect(page.locator('#modal-date')).toHaveValue('2026-11-09');
      expect(await page.evaluate(() => document.body.style.overflow)).toBe('hidden');
    }
    expect(await page.evaluate(() => (window as any).processClosed)).toBe(20);
    await page.keyboard.press('Escape');
    await expect(page.locator('#modal-a')).toBeHidden();
    await expect(page.locator('#opener')).toBeFocused();
    expect(await page.evaluate(() => document.body.style.overflow)).toBe('');
  });

  test('native parent owns child portal, dirty veto, Escape ordering and parent removal cleanup', async ({ page }) => {
    await page.evaluate(() => (window as any).layer.open(document.getElementById('native')));
    await page.locator('#native-before').click();
    expect(await page.locator('.lp-modal-overlay').evaluate(node => node.closest('dialog')?.id)).toBe('native');
    await page.evaluate(() => { (window as any).allowProcessClose = false; });
    await page.keyboard.press('Escape');
    await expect(page.locator('.lp-modal-overlay')).toBeVisible();
    await expect(page.locator('#native')).toBeVisible();
    await page.evaluate(() => { (window as any).allowProcessClose = true; });
    await page.keyboard.press('Escape');
    await expect(page.locator('.lp-modal-overlay')).toHaveCount(0);
    await expect(page.locator('#native-before')).toBeFocused();
    await expect(page.locator('#native')).toBeVisible();
    await page.locator('#native-before').click();
    await page.evaluate(() => document.getElementById('native')!.remove());
    await expect.poll(() => page.evaluate(() => (window as any).processClosed)).toBe(2);
    await expect.poll(() => page.evaluate(() => (window as any).layer.top())).toBeNull();
    expect(await page.evaluate(() => document.body.style.overflow)).toBe('');
  });

  test('standalone legacy close remains synchronous and force bypasses its existing veto', async ({ page }) => {
    const result = await page.evaluate(() => {
      const w = window as any;
      document.getElementById('outside')!.focus();
      const dialog = w.processModal.openProcessMaterialModal('独立旧弹层', '<input autofocus>', { canClose: () => false, onClose: () => w.processClosed++ });
      dialog.close(); const retained = dialog.overlay.isConnected;
      dialog.close({ force: true }); dialog.close({ force: true });
      return { retained, removed: !dialog.overlay.isConnected, closed: w.processClosed, focus: document.activeElement?.id, top: w.layer.top() };
    });
    expect(result).toEqual({ retained: true, removed: true, closed: 1, focus: 'outside', top: null });
  });

  test('nested force cancels an in-flight veto and a synchronously removed parent leaves no orphan', async ({ page }) => {
    await page.locator('#opener').click();
    await page.locator('#modal-last').click();
    const result = await page.evaluate(async () => {
      const w = window as any;
      w.allowProcessClose = false;
      const pending = w.processChild.close();
      w.processChild.close({ force: true });
      return { pending: await pending, connected: w.processChild.overlay.isConnected,
        closed: w.processClosed, focus: document.activeElement?.id, top: w.layer.top()?.root.id };
    });
    expect(result).toEqual({ pending: false, connected: false, closed: 1, focus: 'modal-last', top: 'modal-a' });
    const selfRemoved = await page.evaluate(() => {
      const w = window as any;
      const child = w.processModal.openProcessMaterialModal('同步移除子层', '<button>临时</button>', {
        onMount: (overlay: HTMLElement) => overlay.remove(), onClose: () => w.processClosed++,
      });
      return { connected: child.overlay.isConnected, closed: w.processClosed, top: w.layer.top()?.root.id };
    });
    expect(selfRemoved).toEqual({ connected: false, closed: 2, top: 'modal-a' });
    const removed = await page.evaluate(() => {
      const w = window as any, parent = w.layer.top();
      const child = w.processModal.openProcessMaterialModal('同步移除父层', '<button>临时</button>', {
        onMount: () => parent.destroy(), onClose: () => w.processClosed++,
      });
      return { connected: child.overlay.isConnected, closed: w.processClosed, top: w.layer.top() };
    });
    expect(removed).toEqual({ connected: false, closed: 3, top: null });
    expect(await page.evaluate(() => document.body.style.overflow)).toBe('');
  });
});

async function mount(page: Page, fixture = 'lq-legacy-bridges.html') {
  await page.route('**/*', route => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname.startsWith('/static/js/') || pathname === '/static/css/tailwind-app.css') {
      const file = path.resolve(`.${pathname}`);
      if (file.startsWith(path.resolve('static') + path.sep) && fs.existsSync(file)) return route.fulfill({ contentType: pathname.endsWith('.css') ? 'text/css' : 'text/javascript', body: fs.readFileSync(file) });
    }
    if (pathname === '/') return route.fulfill({ contentType: 'text/html', body: fs.readFileSync(path.join('tests/e2e/components/fixtures', fixture)) });
    return route.abort();
  });
  await page.goto('http://lq-bridges.test/');
  await expect(page.locator('body')).toHaveAttribute('data-ready', 'true');
  await expect(page.locator('#date-a + .ls-dp-display')).toBeVisible();
}

async function expectTimeSelectionVisible(page: Page) {
  await expect.poll(() => page.locator('.ls-dp-time-item.is-selected').evaluateAll(nodes => nodes.length === 2 && nodes.every(node => {
    const item = node.getBoundingClientRect();
    const column = node.parentElement!.getBoundingClientRect();
    return item.top >= column.top && item.bottom <= column.bottom;
  }))).toBe(true);
}

test.describe('LQ legacy bridges', () => {
  test.beforeEach(async ({ page }) => mount(page));

  test('ui.js nested close/reopen keeps the later modal, exact lock and one dynamic delegation', async ({ page }) => {
    await page.locator('#opener').click();
    await page.evaluate(() => { const w = window as any; w.ui.closeModal('modal-a'); w.ui.openModal('modal-a'); });
    await expect(page.locator('#modal-a')).toBeVisible();
    await page.evaluate(() => (window as any).ui.openModal('modal-b'));
    await page.keyboard.press('Escape');
    await expect(page.locator('#modal-b')).toBeHidden();
    await expect(page.locator('#modal-a')).toBeVisible();
    expect(await page.evaluate(() => document.body.style.overflow)).toBe('hidden');
    await page.locator('#modal-close').click();
    await expect(page.locator('#modal-a')).toBeHidden();
    await expect(page.locator('#opener')).toBeFocused();
    expect(await page.evaluate(() => document.body.style.overflow)).toBe('');
    await page.evaluate(async () => {
      await import('/static/js/ui.js?duplicate');
      const root = document.createElement('div'); root.id = 'late'; root.className = 'modal-backdrop'; root.innerHTML = '<div class="modal"><button data-dismiss="modal">动态关闭</button></div>'; document.body.append(root);
      (window as any).ui.openModal('late');
    });
    await page.getByRole('button', { name: '动态关闭' }).click();
    await expect(page.locator('#late')).toBeHidden();
  });

  test('feedback-managed remains owned by feedback and legacy backdrop fallback still closes', async ({ page }) => {
    await page.evaluate(() => { const node = document.querySelector<HTMLElement>('#feedback')!; node.style.display = 'flex'; node.classList.add('show'); });
    await page.locator('#feedback-close').click();
    await expect(page.locator('#feedback')).toBeVisible();
    await page.evaluate(() => { document.querySelector<HTMLElement>('#feedback')!.style.display = 'none'; const node = document.querySelector<HTMLElement>('#modal-a')!; node.style.display = 'flex'; node.classList.add('show'); });
    await page.locator('#modal-a').click({ position: { x: 4, y: 4 } });
    await expect(page.locator('#modal-a')).toBeHidden();
  });

  test('one Escape closes explanation then date then modal; display focus never reopens native input', async ({ page }) => {
    await page.locator('#opener').click();
    await page.locator('#modal-date + .ls-dp-display').click();
    await expect(page.locator('.ls-dp-pop')).toBeVisible();
    await page.evaluate(() => { const target = document.querySelector('.ls-dp-nav')!; target.setAttribute('data-explain', ''); (window as any).help.openExplanation(target, { text: '日期说明' }); });
    await expect(page.locator('#ui-explanation-popover')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.locator('#ui-explanation-popover')).toBeHidden();
    await expect(page.locator('.ls-dp-pop')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.locator('.ls-dp-pop')).toHaveCount(0);
    await expect(page.locator('#modal-date + .ls-dp-display')).toBeFocused();
    await expect(page.locator('#modal-a')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.locator('#modal-a')).toBeHidden();
  });

  test('date A commit timer cannot close B; date event contract and parent removal remain intact', async ({ page }) => {
    await page.locator('#date-a + .ls-dp-display').click();
    await page.evaluate(() => {
      const target = [...document.querySelectorAll<HTMLButtonElement>('.ls-dp-day')].find(node => !node.disabled && !node.classList.contains('is-outside'))!;
      target.click(); document.querySelector<HTMLButtonElement>('#date-b + .ls-dp-display')!.click();
    });
    await page.waitForTimeout(230);
    await expect(page.locator('.ls-dp-pop')).toHaveCount(1);
    await expect(page.locator('.ls-dp-pop')).toBeVisible();
    expect(await page.evaluate(() => (window as any).events)).toEqual(['date-a:input', 'date-a:change']);
    await page.evaluate(() => document.querySelector('#dates')!.remove());
    await expect(page.locator('.ls-dp-pop')).toHaveCount(0);
    expect(await page.evaluate(() => (window as any).layer.top())).toBeNull();
  });

  test('native showModal owns date/help/viewer portals and keeps controls genuinely clickable', async ({ page }) => {
    await page.evaluate(() => document.querySelector<HTMLDialogElement>('#native')!.showModal());
    await page.locator('#native-date + .ls-dp-display').click();
    expect(await page.locator('.ls-dp-pop').evaluate(node => node.closest('dialog')?.id)).toBe('native');
    await page.locator('.ls-dp-day:not(.is-outside):not([disabled])').first().click();
    await expect(page.locator('.ls-dp-pop')).toHaveCount(0);
    await expect(page.locator('#native-date + .ls-dp-display')).toBeFocused();
    await page.locator('#native-help').click();
    await expect(page.locator('#ui-explanation-popover')).toBeVisible();
    expect(await page.locator('#ui-explanation-popover').evaluate(node => node.closest('dialog')?.id)).toBe('native');
    await page.keyboard.press('Escape');
    await expect(page.locator('#native')).toBeVisible();
    await page.locator('#native-image').click();
    expect(await page.locator('.ls-lightbox').evaluate(node => node.closest('dialog')?.id)).toBe('native');
    await page.locator('.ls-lightbox__close').click();
    await expect(page.locator('.ls-lightbox')).toBeHidden();
    await expect(page.locator('#native-image')).toBeFocused();
  });

  test('product CSS orders modal, date, viewer and external help with real pointer hits', async ({ page }) => {
    const receivesPointer = async (selector: string) => {
      await expect.poll(() => page.locator(selector).first().evaluate(node => {
        const box = node.getBoundingClientRect();
        const hit = document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2);
        return node === hit || node.contains(hit);
      })).toBe(true);
    };
    await page.locator('#opener').click();
    await page.locator('#modal-date + .ls-dp-display').click();
    await receivesPointer('.ls-dp-nav');
    await page.locator('.ls-dp-nav').first().click();
    await page.evaluate(() => {
      const w = window as any;
      const opener = document.querySelector<HTMLButtonElement>('.ls-dp-nav')!;
      opener.focus(); w.images.openImageLightbox({ items: [{ src: w.pixel }] });
    });
    await receivesPointer('.ls-lightbox__close');
    await page.locator('.ls-lightbox__close').click();
    await expect(page.locator('.ls-lightbox')).toBeHidden();
    await expect(page.locator('.ls-dp-pop')).toBeVisible();
    await page.evaluate(() => (window as any).help.openExplanation(document.querySelector('.ls-dp-nav'), { text: '顶层可点击说明' }));
    await receivesPointer('.ui-explain-popover__close');
    await page.locator('.ui-explain-popover__close').click();
    await expect(page.locator('#ui-explanation-popover')).toBeHidden();
    await receivesPointer('.ls-dp-nav');
    await page.keyboard.press('Escape');
    await expect(page.locator('.ls-dp-pop')).toHaveCount(0);
    await receivesPointer('#modal-close');
    await page.locator('#modal-close').click();
    await expect(page.locator('#modal-a')).toBeHidden();
    expect(await page.evaluate(() => document.body.style.overflow)).toBe('');
  });

  test('a new owned layer dismisses help without delayed focus or borrowed attributes', async ({ page }) => {
    await page.locator('#opener').click();
    await page.locator('#help').focus(); await page.keyboard.press('Enter');
    await expect(page.locator('.ui-explain-popover__close')).toBeFocused();
    await expect(page.locator('#ui-explanation-popover')).toHaveAttribute('data-lq-layer-state', 'open');
    await page.evaluate(() => { const w = window as any; w.images.openImageLightbox({ items: [{ src: w.pixel }] }); });
    await expect(page.locator('#ui-explanation-popover')).toBeHidden();
    await expect(page.locator('.ls-lightbox__close')).toBeFocused();
    await page.waitForTimeout(300);
    await expect(page.locator('.ls-lightbox__close')).toBeFocused();
    expect(await page.locator('#ui-explanation-popover').evaluate(node => ({
      state: node.getAttribute('data-lq-layer-state'), order: (node as HTMLElement).style.getPropertyValue('--lq-layer-order'),
    }))).toEqual({ state: null, order: '' });
  });

  test('viewer preserves first opener through repeated open and ignores composing/consumed controls', async ({ page }) => {
    await page.locator('#opener').click(); await page.locator('#image').click();
    await expect(page.locator('.ls-lightbox')).toBeVisible();
    await page.evaluate(() => { const w = window as any; w.images.openImageLightbox({ items: [{ src: w.pixel, title: '替换图' }] }); });
    await page.locator('.ls-lightbox__close').dispatchEvent('keydown', { key: 'Escape', isComposing: true });
    await expect(page.locator('.ls-lightbox')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.locator('.ls-lightbox')).toBeHidden();
    await expect(page.locator('#image')).toBeFocused();
    await expect(page.locator('#modal-a')).toBeVisible();
    expect(await page.evaluate(() => document.body.style.overflow)).toBe('hidden');
  });

  test('viewer late image load cannot revive closed state and close/reopen has no stale reveal', async ({ page }) => {
    expect(await page.evaluate(async () => {
      const w = window as any; const NativeImage = window.Image; const probes: any[] = [];
      window.Image = class { naturalWidth = 20; naturalHeight = 30; onload?: () => void; onerror?: () => void; constructor() { probes.push(this); } set src(_value: string) {} } as any;
      document.querySelector<HTMLElement>('#opener')!.focus();
      w.images.openImageLightbox({ items: [{ src: '/late-image' }] });
      await w.images.closeImageLightbox();
      probes[0].onload();
      const closed = !w.images.isImageLightboxOpen() && !document.querySelector('.ls-lightbox__img')!.hasAttribute('src');
      w.images.openImageLightbox({ items: [{ src: '/fresh-image' }] });
      const pending = w.images.closeImageLightbox();
      w.images.openImageLightbox({ items: [{ src: '/new-image' }] });
      await pending;
      await new Promise(resolve => setTimeout(resolve, 250));
      const open = w.images.isImageLightboxOpen() && !document.querySelector<HTMLElement>('.ls-lightbox')!.hidden;
      window.Image = NativeImage; return { closed, open };
    })).toEqual({ closed: true, open: true });
  });

  test('explanation pending hover/longpress is cancelled when its owner disappears', async ({ page }) => {
    await page.locator('#help-outside').dispatchEvent('pointerover', { pointerType: 'mouse' });
    await page.evaluate(() => document.querySelector('#help-outside')!.remove());
    await page.waitForTimeout(320);
    expect(await page.evaluate(() => (window as any).help.isExplanationOpen())).toBe(false);
    await page.locator('#opener').click();
    await page.locator('#help').dispatchEvent('pointerdown', { pointerType: 'touch', pointerId: 22, clientX: 30, clientY: 30 });
    await page.evaluate(() => (window as any).ui.closeModal('modal-a'));
    await page.waitForTimeout(700);
    expect(await page.evaluate(() => (window as any).help.isExplanationOpen())).toBe(false);
  });

  test('scoped image delegation is idempotent/disposable and destruction clears owned resources', async ({ page }) => {
    expect(await page.evaluate(async () => {
      const w = window as any; const scope = document.createElement('div'); const button = document.createElement('button');
      button.dataset.lsLightbox = ''; button.dataset.lsLightboxSrc = w.pixel; scope.append(button); document.body.append(scope);
      const dispose = w.images.bindImageLightboxDelegation(scope);
      const same = dispose === w.images.bindImageLightboxDelegation(scope);
      button.click(); const first = w.images.isImageLightboxOpen();
      await w.images.closeImageLightbox();
      w.images.destroyImageLightbox(); button.click();
      return { same, first, after: w.images.isImageLightboxOpen(), roots: document.querySelectorAll('.ls-lightbox').length, lock: document.body.style.overflow, top: w.layer.top() };
    })).toEqual({ same: true, first: true, after: false, roots: 0, lock: '', top: null });
  });

  test('native owner close force-cleans its registered date and external explanation', async ({ page }) => {
    await page.evaluate(() => document.querySelector<HTMLDialogElement>('#native')!.showModal());
    await page.locator('#native-date + .ls-dp-display').click();
    await page.evaluate(() => document.querySelector<HTMLDialogElement>('#native')!.close());
    await expect(page.locator('.ls-dp-pop')).toHaveCount(0);
    expect(await page.evaluate(() => (window as any).layer.top())).toBeNull();
    await page.evaluate(() => document.querySelector<HTMLDialogElement>('#native')!.showModal());
    await page.locator('#native-help').click();
    await expect(page.locator('#ui-explanation-popover')).toBeVisible();
    await page.evaluate(() => document.querySelector<HTMLDialogElement>('#native')!.close());
    await expect(page.locator('#ui-explanation-popover')).toBeHidden();
    expect(await page.evaluate(() => (window as any).help.isExplanationOpen())).toBe(false);
  });

  test('help link Tab order remains local and closing/consumed Escape never also dismisses modal', async ({ page }) => {
    await page.locator('#opener').click();
    await page.locator('#help').focus();
    await page.keyboard.press('Enter');
    await expect(page.locator('.ui-explain-popover__close')).toBeFocused();
    await page.keyboard.press('Tab');
    await expect(page.locator('.ui-explain-popover__links a')).toBeFocused();
    await page.keyboard.press('Tab');
    await expect(page.locator('#image')).toBeFocused();
    await expect(page.locator('#ui-explanation-popover')).toBeHidden();
    await page.locator('#image').evaluate(node => node.addEventListener('keydown', event => event.preventDefault(), { once: true }));
    await page.keyboard.press('Escape');
    await expect(page.locator('#modal-a')).toBeVisible();
  });

  test('mobile date backdrop is in the native host and dismisses exactly the date', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.locator('#opener').click();
    await page.locator('#modal-date + .ls-dp-display').click();
    await expect(page.locator('.ls-dp-pop')).toHaveClass(/is-sheet/);
    await expect(page.locator('.ls-dp-backdrop')).toBeVisible();
    await page.locator('.ls-dp-backdrop').click({ position: { x: 10, y: 10 } });
    await expect(page.locator('.ls-dp-pop')).toHaveCount(0);
    await expect(page.locator('#modal-a')).toBeVisible();
    expect(await page.evaluate(() => document.body.style.overflow)).toBe('hidden');
  });
});

test.describe('LQ date compatibility accessibility', () => {
  test.use({ hasTouch: true });

  for (const width of [320, 390, 768]) test(`coarse ${width}: date targets, datetime commit and open help chain`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 844 });
    await mount(page, 'lq-date-accessibility.html');
    expect(await page.evaluate(() => matchMedia('(pointer: coarse)').matches)).toBe(true);
    await page.locator('#opener').click();
    const checkTargets = async (phase: string) => {
      await expect.poll(() => page.locator('.ls-dp-pop').evaluate(node => getComputedStyle(node).transform)).toBe('matrix(1, 0, 0, 1, 0, 0)');
      const sizes = await page.locator('.ls-dp-pop button').evaluateAll(nodes => nodes.map(node => {
        const box = node.getBoundingClientRect();
        return { className: node.className, name: node.getAttribute('aria-label') || node.textContent, width: box.width, height: box.height };
      }));
      fs.writeFileSync(testInfo.outputPath(`${phase}-targets.json`), JSON.stringify(sizes, null, 2));
      await testInfo.attach(`${phase}-targets`, { body: JSON.stringify(sizes, null, 2), contentType: 'application/json' });
      expect.soft(sizes.filter(size => size.width < 43.99 || size.height < 43.99), `${phase}: every date target is at least 44px`).toEqual([]);
      const bounds = await page.locator('.ls-dp-pop').evaluate(node => {
        const box = node.getBoundingClientRect(); return { left: box.left, right: box.right, width: node.clientWidth, scroll: node.scrollWidth };
      });
      expect.soft(bounds.left).toBeGreaterThanOrEqual(0);
      expect.soft(bounds.right).toBeLessThanOrEqual(width);
      expect.soft(bounds.scroll).toBeLessThanOrEqual(bounds.width + 1);
    };
    const checkAxe = async (phase: string) => {
      const results = await new AxeBuilder({ page }).analyze();
      const failures = results.violations.filter(item => ['serious', 'critical'].includes(item.impact || ''));
      fs.writeFileSync(testInfo.outputPath(`${phase}-axe.json`), JSON.stringify(failures, null, 2));
      await testInfo.attach(`${phase}-axe`, { body: JSON.stringify(failures, null, 2), contentType: 'application/json' });
      expect.soft(failures, `${phase}: no serious/critical axe findings`).toEqual([]);
    };

    await page.locator('#modal-date + .ls-dp-display').click();
    await checkTargets('days');
    await checkAxe('date-open');
    await page.screenshot({ path: testInfo.outputPath(`date-${width}.png`) });
    await page.locator('.ls-dp-nav.next').click();
    await expect(page.locator('.ls-dp-title')).toHaveText('2026年10月');
    await page.locator('.ls-dp-nav.prev').click();
    await expect(page.locator('.ls-dp-title')).toHaveText('2026年9月');
    await page.locator('.ls-dp-title').click();
    await expect(page.locator('.ls-dp-cell')).toHaveCount(12);
    await checkTargets('months');
    await page.locator('.ls-dp-title').click();
    await checkTargets('years');
    await page.keyboard.press('Escape');
    await expect(page.locator('#modal-date + .ls-dp-display')).toBeFocused();

    await page.locator('#modal-datetime + .ls-dp-display').click();
    await checkTargets('datetime');
    await checkAxe('datetime-open');
    await expectTimeSelectionVisible(page);
    const hours = page.getByRole('group', { name: '小时', exact: true });
    const minutes = page.getByRole('group', { name: '分钟', exact: true });
    await expect(hours.getByRole('button', { name: '12', exact: true })).toHaveAttribute('aria-pressed', 'true');
    await expect(minutes.getByRole('button', { name: '30', exact: true })).toHaveAttribute('aria-pressed', 'true');
    await hours.getByRole('button', { name: '13', exact: true }).click();
    await expect(hours.getByRole('button', { name: '13', exact: true })).toHaveAttribute('aria-pressed', 'true');
    await expect(hours.locator('[aria-pressed="true"]')).toHaveCount(1);
    await expect(hours.getByRole('button', { name: '12', exact: true })).toHaveAttribute('aria-pressed', 'false');
    await expect(page.locator('#modal-datetime')).toHaveValue('2026-09-20T13:30');
    await page.evaluate(() => {
      const trigger = document.querySelector('.ls-dp-nav') as HTMLElement;
      trigger.setAttribute('data-explain', ''); trigger.focus();
      (window as any).help.openExplanation(trigger, { title: '日期导航说明', text: '上一页切换月份；关闭说明后日期选择仍然保留。' });
    });
    await expect(page.locator('#ui-explanation-popover')).toBeVisible();
    await checkAxe('date-help-chain');
    if (width === 390) await page.screenshot({ path: testInfo.outputPath('datetime-help-390.png') });
    await page.keyboard.press('Escape');
    await expect(page.locator('#ui-explanation-popover')).toBeHidden();
    await expect(page.locator('.ls-dp-pop')).toBeVisible();
    await page.getByRole('button', { name: '确定', exact: true }).click();
    await expect(page.locator('.ls-dp-pop')).toHaveCount(0);
    await expect(page.locator('#modal-datetime + .ls-dp-display')).toBeFocused();
    await expect(page.locator('#modal-a')).toBeVisible();
    expect(await page.evaluate(() => (window as any).events)).toEqual(['modal-datetime:input', 'modal-datetime:change', 'modal-datetime:input', 'modal-datetime:change']);
    await page.keyboard.press('Escape');
    await expect(page.locator('#modal-a')).toBeHidden();
    await expect(page.locator('#opener')).toBeFocused();
  });

  for (const palette of ['indigo', 'sky', 'mint', 'violet', 'rose', 'teal']) for (const appearance of ['light', 'dark']) {
    test(`${palette}/${appearance}: open datetime and explanation chain has no serious axe findings`, async ({ page }, testInfo) => {
      await page.setViewportSize({ width: 390, height: 844 });
      await mount(page, 'lq-date-accessibility.html');
      await page.evaluate(({ palette, appearance }) => {
        document.documentElement.dataset.uiPalette = palette;
        document.documentElement.dataset.appearance = appearance;
      }, { palette, appearance });
      await page.locator('#opener').click();
      await page.locator('#modal-datetime + .ls-dp-display').click();
      await expect(page.locator('.ls-dp-layer')).toHaveAttribute('data-lq-layer-state', 'open');
      await expectTimeSelectionVisible(page);
      for (const phase of ['datetime', 'help']) {
        if (phase === 'help') {
          await page.evaluate(() => {
            const trigger = document.querySelector('.ls-dp-nav') as HTMLElement;
            trigger.setAttribute('data-explain', ''); trigger.focus();
            (window as any).help.openExplanation(trigger, { title: '日期说明', text: '说明关闭后继续编辑，日期值和输入状态不受影响。' });
          });
          await expect(page.locator('#ui-explanation-popover')).toBeVisible();
        }
        const result = await new AxeBuilder({ page }).analyze();
        const failures = result.violations.filter(item => ['serious', 'critical'].includes(item.impact || ''));
        fs.writeFileSync(testInfo.outputPath(`${phase}-axe.json`), JSON.stringify(failures, null, 2));
        expect(failures).toEqual([]);
        if (palette === 'teal') await page.screenshot({ path: testInfo.outputPath(`${appearance}-${phase}-390.png`) });
      }
    });
  }
});
