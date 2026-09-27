import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

async function mount(page: Page) {
  await page.route('https://motion.test/**', route => {
    const url = new URL(route.request().url());
    if (url.pathname.startsWith('/static/')) {
      const file = path.resolve('.' + url.pathname);
      if (!file.startsWith(path.resolve('static') + path.sep) || !fs.existsSync(file)) throw new Error(`Unexpected file ${file}`);
      return route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body: fs.readFileSync(file) });
    }
    return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh-CN" data-appearance="light" data-lq-glass="tinted" data-lq-tier="A"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/static/css/tailwind-app.css"><style>body{padding:24px}#stage{display:flex;flex-wrap:wrap;gap:16px;align-items:start}#scope{display:grid;gap:20px}#fields{display:grid;gap:12px;max-width:320px}.lq-menu{left:24px;top:480px}#domain{transform:translateX(13px) rotate(2deg)}#surface{transform:translateX(11px)}</style></head><body>
      <main id="stage"><section id="scope"></section><section id="fields"><input class="lq-input" aria-label="标题"><textarea class="lq-textarea" aria-label="正文"></textarea><select class="lq-select" aria-label="类型"><option>默认</option></select></section><div id="domain">领域位置</div><button id="surface" class="lq-btn" data-lq-shape="surface">领域卡片</button></main>
      <div id="thumb" class="lq-segment"><div class="lq-tabs__list" data-lq-thumb style="--lq-thumb-x:0px;--lq-thumb-y:0px;--lq-thumb-w:80px;--lq-thumb-h:40px"></div></div>
      <div id="menu" class="lq-menu lq-glass lq-glass--thick" data-ui-overlay-state="closed" hidden><button class="lq-btn">菜单操作</button></div>
      <script type="module">
        import {createComponent} from '/static/js/lq/components.js';
        import {setOverlayOpen} from '/static/js/ui_overlay_motion.js';
        window.counts={raf:0,timers:0,controlListeners:0};
        const raf=window.requestAnimationFrame.bind(window),timer=window.setTimeout.bind(window),listen=EventTarget.prototype.addEventListener;
        window.requestAnimationFrame=(...args)=>{counts.raf++;return raf(...args)};
        window.setTimeout=(...args)=>{counts.timers++;return timer(...args)};
        EventTarget.prototype.addEventListener=function(...args){if(this instanceof Element&&this.matches('.lq-btn'))counts.controlListeners++;return listen.apply(this,args)};
        const scope=document.getElementById('scope');
        for(const options of [{id:'normal',label:'液态按钮',variant:'glass'},{id:'disabled',label:'不可用',disabled:true},{id:'loading',label:'正在保存',loading:true}])scope.append(createComponent('button',options));
        window.showMenu=value=>setOverlayOpen(document.getElementById('menu'),value);
        window.ready=true;
      </script></body></html>` });
  });
  await page.goto('https://motion.test/');
  await page.waitForFunction(() => (window as any).ready);
}

const matrix = (page: Page, selector = '#normal') => page.locator(selector).evaluate(element => {
  const value = new DOMMatrixReadOnly(getComputedStyle(element).transform);
  return { x: value.a, y: value.d, left: value.e, top: value.f };
});
const neutral = { x: 1, y: 1, left: 0, top: 0 };
async function press(page: Page, selector = '#normal') {
  const bounds = (await page.locator(selector).boundingBox())!;
  await page.mouse.move(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2);
  await page.mouse.down();
}

test('LQ motion gives quick liquid press and reversible release without changing fields or domain geometry', async ({ page }) => {
  await mount(page);
  await press(page);
  await expect.poll(() => matrix(page)).toMatchObject({ x: 1.012, y: .965, top: 0 });
  await page.mouse.up();
  await page.mouse.move(0, 0);
  await expect.poll(() => matrix(page)).toEqual(neutral);
  for (const selector of ['.lq-input', '.lq-textarea', '.lq-select']) {
    await page.locator(selector).focus();
    await page.locator(selector).hover();
    expect(await matrix(page, selector)).toEqual(neutral);
  }
  const domain = await matrix(page, '#domain');
  await page.locator('#surface').hover();
  expect((await matrix(page, '#surface')).left).toBe(11);
  expect(await matrix(page, '#domain')).toEqual(domain);
  const properties = await page.locator('#normal, .lq-input, #menu').evaluateAll(elements => elements.map(element => getComputedStyle(element).transitionProperty));
  properties.forEach(value => expect(value).not.toMatch(/(?:^|, )(?:(?:backdrop-)?filter|width|height|all)(?:,|$)/));
  expect(await page.locator('#thumb .lq-tabs__list').evaluate(element => getComputedStyle(element, '::before').transitionProperty)).toBe('transform');
});

test('LQ motion modes inherit, can be customized, and switch off immediately during a press', async ({ page }) => {
  await mount(page);
  for (const [mode, expected] of [['quiet', [.995, .99]], ['standard', [1.012, .965]], ['expressive', [1.025, .94]]] as const) {
    await page.locator('html').evaluate((element, mode) => element.setAttribute('data-lq-motion', mode), mode);
    await press(page);
    await expect.poll(() => matrix(page)).toMatchObject({ x: expected[0], y: expected[1] });
    await page.mouse.up();
    await page.mouse.move(0, 0);
  }
  await page.locator('#scope').evaluate(element => { element.setAttribute('data-lq-motion', 'quiet'); element.style.setProperty('--lq-motion-control-duration', '73ms'); });
  expect(await page.locator('#normal').evaluate(element => getComputedStyle(element).transitionDuration)).toBe('0.073s, 0.073s, 0.073s');
  await press(page);
  await expect.poll(() => matrix(page)).toMatchObject({ x: .995, y: .99 });
  await page.locator('#scope').evaluate(element => { element.style.removeProperty('--lq-motion-control-duration'); element.setAttribute('data-lq-motion', 'off'); });
  expect(await matrix(page)).toEqual(neutral);
  expect(await page.locator('#normal').evaluate(element => element.getAnimations().length)).toBe(0);
  await page.mouse.up();
  expect(await page.locator('#loading .lq-spinner').evaluate(element => getComputedStyle(element).animationPlayState)).toBe('paused');
});

test('LQ motion respects reduced motion changed during active input, even inside an expressive scope', async ({ page }) => {
  await mount(page);
  await page.locator('#scope').evaluate(element => element.setAttribute('data-lq-motion', 'expressive'));
  await press(page);
  await expect.poll(() => matrix(page)).toMatchObject({ y: .94 });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await expect.poll(() => matrix(page), { timeout: 250 }).toEqual(neutral);
  expect(await page.locator('#normal').evaluate(element => element.getAnimations().length)).toBe(0);
  await page.mouse.up();
  await page.locator('#normal').hover();
  expect(await matrix(page)).toEqual(neutral);
  expect(await page.evaluate(async () => await (window as any).showMenu(true))).toBe(true);
  expect(await page.locator('#menu').evaluate(element => element.getAnimations().length)).toBe(0);
});

test('LQ motion reverses rapid input and excludes disabled and loading controls', async ({ page }) => {
  await mount(page);
  for (let count = 0; count < 4; count++) {
    await press(page);
    await page.waitForTimeout(20);
    await page.mouse.up();
    await page.waitForTimeout(20);
  }
  await page.mouse.move(0, 0);
  await expect.poll(() => matrix(page)).toEqual(neutral);
  expect(await page.locator('#normal').evaluate(element => element.getAnimations().length)).toBe(0);
  for (const selector of ['#disabled', '#loading']) {
    await press(page, selector);
    expect(await matrix(page, selector)).toEqual(neutral);
    await page.mouse.up();
  }
  await expect(page.locator('#loading')).toContainText('正在保存');
  await expect(page.locator('#loading')).toHaveAttribute('aria-busy', 'true');
});

test('LQ overlay interruption keeps the final owner and mode off completes an in-flight exit', async ({ page }) => {
  await mount(page);
  const outcome = await page.evaluate(async () => {
    const w = window as any;
    await w.showMenu(true);
    const exited = w.showMenu(false);
    await new Promise(resolve => setTimeout(resolve, 30));
    const reopened = w.showMenu(true);
    return Promise.all([true, exited, reopened]);
  });
  expect(outcome[1]).toBe(false);
  expect(outcome[2]).toBe(true);
  await expect(page.locator('#menu')).toBeVisible();
  expect(await page.locator('#menu').evaluate(element => getComputedStyle(element).backdropFilter)).toContain('blur(24px)');
  expect(await page.evaluate(async () => {
    const pending = (window as any).showMenu(false);
    document.documentElement.setAttribute('data-lq-motion', 'off');
    return pending;
  })).toBe(true);
  await expect(page.locator('#menu')).toBeHidden();
});

test('LQ touch feedback has no sticky hover and static controls schedule no idle work', async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
  try {
    const page = await context.newPage();
    await mount(page);
    const bounds = (await page.locator('#normal').boundingBox())!;
    const session = await context.newCDPSession(page);
    // Sample each rendered frame: an 80ms press can finish between Playwright's
    // default 100/250ms polling intervals, especially under parallel suites.
    await page.evaluate(() => {
      const samples: { x: number; y: number; top: number }[] = [];
      const end = performance.now() + 650;
      (window as any).touchSamples = new Promise(resolve => {
        const sample = () => {
          const value = new DOMMatrixReadOnly(getComputedStyle(document.getElementById('normal')!).transform);
          samples.push({ x: value.a, y: value.d, top: value.f });
          if (performance.now() < end) requestAnimationFrame(sample); else resolve(samples);
        };
        requestAnimationFrame(sample);
      });
    });
    await session.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: bounds.x + bounds.width / 2, y: bounds.y + bounds.height / 2 }] });
    await session.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
    // Chromium delays the active feedback until it recognizes the completed
    // tap, then releases it without retaining the emulated hover position.
    const samples = await page.evaluate(() => (window as any).touchSamples) as { x: number; y: number; top: number }[];
    expect(samples.some(value => value.x > 1.001 && value.y < .999)).toBe(true);
    expect(samples.every(value => value.top === 0)).toBe(true);
    await expect.poll(() => matrix(page)).toEqual(neutral);
    const before = await page.evaluate(() => ({ ...(window as any).counts }));
    await page.evaluate(() => {
      const batch = document.createDocumentFragment();
      for (let index = 0; index < 400; index++) { const node = document.createElement('button'); node.className = 'lq-btn'; node.textContent = `操作 ${index}`; batch.append(node); }
      document.body.append(batch);
    });
    await page.waitForTimeout(150);
    expect(await page.evaluate(() => (window as any).counts)).toEqual(before);
    expect(await page.locator('#normal').evaluate(element => getComputedStyle(element).willChange)).toBe('auto');
    await session.detach();
  } finally { await context.close(); }
});


test('LQ native dialog motion preserves immediate browser close events, returnValue and rapid reopening', async ({ page }) => {
  await mount(page);
  await page.evaluate(() => {
    const w = window as any;
    const trigger = document.createElement('button'); trigger.id = 'native-trigger'; trigger.textContent = '打开原生对话框';
    const dialog = document.createElement('dialog');
    dialog.className = 'lq-native-dialog lq-domain-raised'; dialog.dataset.lqMaterial = 'raised';
    dialog.innerHTML = '<form method="dialog"><input name="draft" value="草稿保留"><button value="saved">完成</button></form>';
    document.body.append(trigger, dialog); w.nativeDialog = dialog; w.nativeCloses = 0;
    dialog.addEventListener('close', () => w.nativeCloses++);
    trigger.onclick = () => dialog.showModal();
  });
  await page.locator('#native-trigger').click();
  const dialog = page.locator('dialog');
  await expect(dialog).toBeVisible();
  await expect.poll(() => dialog.evaluate(el => getComputedStyle(el).opacity)).toBe('1');
  await dialog.locator('input').fill('本次草稿');
  await dialog.getByRole('button', { name: '完成' }).click();
  await expect.poll(() => page.evaluate(() => (window as any).nativeCloses)).toBe(1);
  expect(await dialog.evaluate(el => ({ open: (el as HTMLDialogElement).open, value: (el as HTMLDialogElement).returnValue }))).toEqual({ open: false, value: 'saved' });
  await expect(page.locator('#native-trigger')).toBeFocused();
  await page.evaluate(() => { const el = (window as any).nativeDialog; el.showModal(); el.close('cancelled'); el.showModal(); });
  await expect(dialog).toBeVisible();
  await expect(dialog.locator('input')).toHaveValue('本次草稿');
  await page.keyboard.press('Escape');
  await expect(dialog).not.toHaveAttribute('open');
  await page.locator('html').evaluate(el => el.setAttribute('data-lq-motion', 'off'));
  await page.locator('#native-trigger').click();
  expect(await dialog.evaluate(el => getComputedStyle(el).transitionDuration.split(',').every(value => value.trim() === '0s'))).toBe(true);
  await page.keyboard.press('Escape');
  await expect(dialog).toBeHidden();
});
