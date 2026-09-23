import { test, expect, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
const { inspectTextPixels } = require('../../../tools/ui/contrast_probe.cjs');

const python = process.env.LQ_TEST_PYTHON || 'venv/Scripts/python.exe';
const fixture = JSON.parse(execFileSync(python, ['tests/e2e/scripts/render_lq_centered.py'], { encoding: 'utf8' }));
const palettes = ['teal', 'indigo', 'sky', 'mint', 'violet', 'rose'];
const origin = 'https://centered-lq.test';
const assetRoot = path.resolve(process.env.LQ_CENTERED_ASSET_ROOT || 'static');
if (assetRoot !== path.resolve('static') && !assetRoot.startsWith(path.resolve('static/assets') + path.sep)) throw new Error('Centered fixture assets must be local source or an immutable graph.');

async function mount(page: Page, name = 'student_login_v4', options: { image?: 'success'|'failure'|'held', sceneFill?: 'black'|'white', sceneFile?: string, manifestHeld?: boolean, legacy?: boolean, modules?: boolean } = {}) {
  expect(fixture.isolated).toBe(true);
  let releaseImage!: () => void;
  const imageWait = new Promise<void>(resolve => { releaseImage = resolve; });
  const posts: string[] = [];
  let releasePost: (() => void) | null = null;
  let holdPost = false;
  let postResult = { status: 400, body: { detail: '登录失败：账号或密码错误。' } as any };
  await page.addInitScript(() => {
    Object.defineProperty(navigator, 'hardwareConcurrency', { value: 8 });
    Object.defineProperty(navigator, 'deviceMemory', { value: 8 });
  });
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.origin !== origin) return route.abort();
    if (url.pathname === '/static/img/life_tips/manifest.json') {
      if (options.manifestHeld) await imageWait;
      return route.fulfill({ json: { images: [{ file: options.sceneFile || 'scene-sunny-fixture.svg', categories: [] }] } });
    }
    if (url.pathname.endsWith('scene-sunny-fixture.svg')) {
      if (options.image === 'held') await imageWait;
      if (options.image === 'failure') return route.fulfill({ status: 404, body: '' });
      return route.fulfill({ contentType: 'image/svg+xml', body: `<svg xmlns="http://www.w3.org/2000/svg" width="1440" height="980"><defs><linearGradient id="g"><stop stop-color="#fff"/><stop offset="1" stop-color="#333"/></linearGradient></defs><rect width="1440" height="980" fill="${options.sceneFill || 'url(#g)'}"/></svg>` });
    }
    if (url.pathname.startsWith('/static/')) {
      if (options.modules === false && url.pathname.endsWith('.js')) return route.abort();
      const file = /^\/static\/(css|js)\//.test(url.pathname)
        ? path.resolve(assetRoot, '.' + url.pathname.slice('/static'.length)) : path.resolve('.' + url.pathname);
      if (file.startsWith(path.resolve('static') + path.sep) && fs.existsSync(file)) return route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : file.endsWith('.js') ? 'text/javascript' : 'application/octet-stream', body: fs.readFileSync(file) });
    }
    if (route.request().method() === 'POST') {
      posts.push(route.request().postData() || '');
      if (holdPost) await new Promise<void>(resolve => { releasePost = resolve; });
      if (url.pathname === '/teacher/login') return route.fulfill({ status: postResult.status, contentType: 'text/html', body: fixture.pages.teacher_error });
      return route.fulfill({ status: postResult.status, json: postResult.body });
    }
    if (url.pathname === '/api/learning/cultivation-profile') return route.fulfill({ json: {} });
    if (url.pathname === '/done') return route.fulfill({ contentType: 'text/html', body: '<!doctype html><html lang="zh-CN"><title>完成</title><body><main><h1>已登录</h1></main></body></html>' });
    if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: (options.legacy ? fixture.legacy : fixture.pages)[name] });
    return route.fulfill({ status: 404, body: 'Isolated centered fixture only' });
  });
  await page.goto(origin);
  if (options.modules !== false && name.includes('login')) {
    const owner = name.startsWith('teacher') ? '#teacher-login-form' : '[data-student-login-root]';
    await expect(page.locator(owner)).toHaveAttribute('data-login-mounted', 'true');
  }
  return { posts, releaseImage, hold: () => { holdPost = true; }, release: () => { holdPost = false; releasePost?.(); }, succeed: () => { postResult = { status: 200, body: { redirect_to: '/done', login_count: 1 } }; } };
}

async function theme(page: Page, palette = 'indigo', appearance = 'light', glass = 'tinted', tier = 'A') {
  await page.evaluate(async values => {
    const w = window as any;
    w.LanShareTheme.applyTheme({ preferences: { palette_key: values.palette, appearance: values.appearance, glass: values.glass }, capabilities: w.LanShareTheme.detectCapabilities(window) });
    document.documentElement.dataset.lqTier = values.tier;
    document.documentElement.dispatchEvent(new CustomEvent('lq:theme-change', { bubbles: true }));
    await document.fonts.ready;
    await Promise.all(document.getAnimations().filter(a => Number.isFinite(a.effect?.getComputedTiming().iterations)).map(a => a.finished.catch(() => {})));
    await new Promise(requestAnimationFrame);
  }, { palette, appearance, glass, tier });
}

test.describe('LQ centered actual templates and native owners', () => {
  test('failure status displays its registered alert icon with the same accessible action', async ({ page }, info) => {
    await page.setViewportSize({ width: 390, height: 660 });
    await mount(page, 'status');
    await expect(page.getByRole('heading', { name: '操作失败' })).toBeVisible();
    await expect(page.locator('.lq-status-card__icon svg line')).toHaveCount(2);
    await expect(page.getByRole('link', { name: '返回主页' })).toHaveAttribute('href', '/dashboard?source=status');
    for (const appearance of ['light','dark']) {
      await theme(page, 'indigo', appearance, 'off');
      expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
      await page.screenshot({ path: info.outputPath(`status-alert-${appearance}-390.png`), fullPage: true });
    }
  });

  for (const kind of ['student', 'teacher']) for (const source of ['SSR', 'JS']) test(`${kind} ${source} errors retain readable semantic surfaces over the scene`, async ({ page }, info) => {
    await page.setViewportSize({ width: 390, height: 900 });
    await mount(page, source === 'SSR' ? `${kind}_error` : `${kind}_login_v4`, { sceneFill: 'white' });
    await expect(page.locator('.lq-login-card')).toHaveAttribute('data-lq-scene-state', 'ready');
    const form = page.locator(kind === 'student' ? '#student-password-login-form' : '#teacher-login-form');
    if (source === 'JS') {
      await page.locator(kind === 'student' ? '#identifier' : '#email').fill(kind === 'student' ? '合成学生' : 'qa@example.test');
      await page.locator('#password').fill('wrong-password');
      await form.locator('[type=submit]').click();
    }
    const feedback = form.locator('[data-login-feedback]');
    await expect(feedback).toBeVisible();
    await expect(form).toHaveAttribute('aria-describedby', await feedback.getAttribute('id') as string);
    const results = [];
    for (const appearance of ['light', 'dark']) for (const palette of palettes) {
      await theme(page, palette, appearance);
      await feedback.evaluate(el => el.setAttribute('data-lq-pixel-text', ''));
      const result = await inspectTextPixels(page);
      expect(result.samples.length).toBe(1);
      expect(result.status, JSON.stringify(result.samples)).toBe('passed');
      results.push({ appearance, palette, ...result });
    }
    await page.screenshot({ path: info.outputPath(`${kind}-${source}-visible-error.png`), fullPage: true });
    const evidence = info.outputPath('visible-error-pixel-pairs.json');
    fs.writeFileSync(evidence, JSON.stringify({ assetRoot, results }, null, 2));
    await info.attach('visible-error-pixel-pairs', { path: evidence, contentType: 'application/json' });
  });

  for (const appearance of ['light', 'dark']) for (const sceneFill of ['black', 'white'] as const) test(`material switching keeps pixel-proven foreground pairs in every frame ${appearance} ${sceneFill}`, async ({ page }, info) => {
    await page.setViewportSize({ width: 390, height: 740 });
    await page.emulateMedia({ reducedMotion: 'no-preference' });
    const control = await mount(page, 'student_login_v4', { image: 'held', sceneFill });
    await theme(page, 'indigo', appearance, 'off');
    control.releaseImage();
    await expect(page.locator('.lq-login-card')).toHaveAttribute('data-lq-scene-state', 'ready');
    const original = await page.locator('.lq-login-card').boundingBox();
    const records = [];
    for (const [glass, tier] of [['tinted', 'A'], ['tinted', 'B'], ['tinted', 'A'], ['off', 'A'], ['tinted', 'A']]) {
      // Observe the real owner synchronously and on each of 20 painted frames.
      // No reduced-motion shortcut, animation fast-forward, or settling sleep.
      const frames = await page.evaluate(async values => {
        const card = document.querySelector<HTMLElement>('[data-lq-login-card]')!;
        const host = document.querySelector<HTMLElement>('.lq-login-frame')!;
        const texts = [...card.querySelectorAll<HTMLElement>('h1,.subtitle,.lq-field__label,.lq-btn__label,.link-button,.footer-links a')].filter(el => !el.closest('[hidden]') && el.getClientRects().length && !el.children.length);
        texts.forEach(el => el.setAttribute('data-lq-pixel-text', ''));
        const read = () => {
          const c = getComputedStyle(card);
          return { background: c.backgroundColor, foreground: c.color, sheen: getComputedStyle(card, '::before').backgroundImage, material: host.dataset.lqLoginMaterial, tone: card.dataset.lqTone, text: texts.map(el => ({ color: getComputedStyle(el).color, fill: getComputedStyle(el).webkitTextFillColor })), transition: c.transitionProperty };
        };
        const w = window as any;
        w.LanShareTheme.applyTheme({ preferences: { palette_key: 'indigo', appearance: values.appearance, glass: values.glass }, capabilities: w.LanShareTheme.detectCapabilities(window) });
        document.documentElement.dataset.lqTier = values.tier;
        document.documentElement.dispatchEvent(new CustomEvent('lq:theme-change', { bubbles: true }));
        const result = [read()];
        for (let i = 0; i < 20; i++) { await new Promise(requestAnimationFrame); result.push(read()); }
        return result;
      }, { appearance, glass, tier });
      const endpoint = frames.at(-1)!;
      expect(endpoint.transition).toBe('border-color');
      for (const frame of frames) expect(frame).toEqual(endpoint);
      const pixels = await inspectTextPixels(page);
      expect(pixels.samples.length).toBe(8);
      expect(pixels.status, `${glass}/${tier}/${appearance}/${sceneFill}: ${JSON.stringify(pixels.samples.filter((s: any) => s.status !== 'passed'))}`).toBe('passed');
      expect(await page.locator('.lq-login-card').boundingBox()).toEqual(original);
      records.push({ glass, tier, frames, pixels });
    }
    const evidence = info.outputPath('actual-material-frames-and-pixel-endpoints.json');
    fs.mkdirSync(path.dirname(evidence), { recursive: true });
    fs.writeFileSync(evidence, JSON.stringify({ assetRoot, appearance, sceneFill, records }, null, 2));
    await info.attach('actual-material-frames-and-pixel-endpoints', { path: evidence, contentType: 'application/json' });
  });

  test('image readiness changes only material, off/tier/media revert the same form, and 20 owners clean up', async ({ page }) => {
    const control = await mount(page, 'student_login_v4', { image: 'held' });
    await page.locator('#identifier').fill('保留的姓名');
    await page.locator('#password').fill('retained-password');
    await page.evaluate(() => { (window as any).original = document.querySelector('#student-password-login-form'); });
    const before = await page.locator('.lq-login-card').boundingBox();
    await expect(page.locator('.lq-login-card')).toHaveClass(/lq-glass--thick/);
    control.releaseImage();
    await expect(page.locator('.lq-login-card')).toHaveAttribute('data-lq-scene-state', 'ready');
    await theme(page);
    await expect(page.locator('.lq-login-card')).toHaveClass(/lq-glass--clear/);
    const after = await page.locator('.lq-login-card').boundingBox();
    expect(after).toEqual(before);
    for (const [glass, tier] of [['off','A'], ['tinted','B'], ['tinted','C'], ['tinted','A']]) {
      await theme(page, 'rose', 'dark', glass, tier);
      await expect(page.locator('.lq-login-card')).toHaveClass(glass === 'tinted' && tier === 'A' ? /lq-glass--clear/ : /lq-glass--thick/);
    }
    await page.emulateMedia({ forcedColors: 'active' });
    await expect(page.locator('.lq-login-card')).toHaveClass(/lq-glass--thick/);
    await page.emulateMedia({ forcedColors: 'none' });
    await page.evaluate(async () => {
      const module = await import('/static/js/login_scene.js');
      for (let i = 0; i < 20; i++) {
        const [a,b] = await Promise.all([module.initLoginScene(), module.initLoginScene()]);
        if (a !== b) throw new Error('Duplicate scene owner');
        a?.dispose(); a?.dispose();
        if (document.querySelectorAll('.login-scene-backdrop').length) throw new Error('Leaked backdrop');
      }
      await module.initLoginScene();
    });
    expect(await page.evaluate(() => (window as any).original === document.querySelector('#student-password-login-form'))).toBe(true);
    await expect(page.locator('#identifier')).toHaveValue('保留的姓名');
    await expect(page.locator('#password')).toHaveValue('retained-password');
    expect(control.posts).toEqual([]);
  });

  for (const failure of ['failure', 'timeout', 'manifest-timeout'] as const) test(`${failure} stays thick and late completion cannot reveal a stale scene`, async ({ page }) => {
    const control = await mount(page, 'student_login_v4', { image: failure === 'failure' ? 'failure' : 'held', manifestHeld: failure === 'manifest-timeout' });
    await expect(page.locator('.lq-login-card')).toHaveAttribute('data-lq-scene-state', 'unavailable', { timeout: 6500 });
    control.releaseImage();
    await page.evaluate(() => new Promise(requestAnimationFrame));
    await expect(page.locator('.login-scene-backdrop')).toHaveCount(0);
    await expect(page.locator('.lq-login-card')).toHaveClass(/lq-glass--thick/);
    await expect(page.locator('#identifier')).toBeEditable();
  });

  for (const kind of ['student', 'teacher']) test(`${kind} held submission preserves label/layout and failure permits exactly one corrected retry`, async ({ page }) => {
    const control = await mount(page, `${kind}_login_v4`, { image: 'failure' });
    const form = page.locator(kind === 'student' ? '#student-password-login-form' : '#teacher-login-form');
    const identifier = page.locator(kind === 'student' ? '#identifier' : '#email');
    await identifier.fill(kind === 'student' ? '合成学生' : 'qa@example.test');
    await page.locator('#password').fill('wrong-password');
    control.hold();
    const button = form.locator('button[type=submit]');
    const before = await button.boundingBox();
    await button.click();
    await expect(button).toBeDisabled();
    await expect(button).toHaveAttribute('aria-busy', 'true');
    await expect(button).toHaveAccessibleName('登录');
    await form.evaluate(node => node.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })));
    expect(control.posts).toHaveLength(1);
    expect(await button.boundingBox()).toEqual(before);
    control.release();
    await expect(form.locator('[data-login-feedback]')).toBeVisible();
    await expect(form.locator('[data-login-feedback]')).toBeFocused();
    await expect(page.locator('#password')).toHaveValue('wrong-password');
    await expect(button).toBeEnabled();
    await page.locator('#password').fill('corrected-password');
    await button.click();
    await expect.poll(() => control.posts.length).toBe(2);
    expect(control.posts[1]).toContain('corrected-password');
    await expect(form.locator('[data-login-feedback]')).toBeVisible();
  });

  test('teacher and student share the clear scene material without an extra scrim', async ({ page }) => {
    await mount(page, 'teacher_login_v4');
    await expect(page.locator('.lq-login-card')).toHaveAttribute('data-lq-scene-state', 'ready');
    await theme(page);
    await expect(page.locator('.login-scene-backdrop')).toHaveCount(1);
    await expect(page.locator('.lq-login-card')).toHaveClass(/lq-glass--clear/);
    await expect(page.locator('.lq-login-frame')).toHaveAttribute('data-lq-login-material', 'clear');
    await expect(page.locator('.lq-login-frame > .lq-scrim')).toHaveCount(0);
  });

  const photos = [
    ['sunny-campus', 'biye-sunny-lawn-reunion02-9204e697.webp', 'light'],
    ['sunny-bay', 'chengshi-sunny-azure-bay02-b3842a14.webp', 'light'],
    ['rain-platform', 'biye-rain-platform04-f30cae6e.webp', 'dark'],
    ['snow-platform', 'chengshi-snow-platform08-5c837636.webp', 'dark'],
  ] as const;
  for (const role of ['student', 'teacher']) test(`tier B preserves photo tone independently of the account theme ${role}`, async ({ page }, info) => {
    test.setTimeout(60000);
    await page.setViewportSize({ width: 390, height: 900 });
    for (const [label, file, tone] of photos) {
      await page.unrouteAll({ behavior: 'wait' });
      await mount(page, `${role}_login_v4`, { sceneFile: file });
      const card = page.locator('.lq-login-card');
      await expect(card).toHaveAttribute('data-lq-scene-state', 'ready');
      await theme(page, 'indigo', tone === 'light' ? 'dark' : 'light', 'tinted', 'B');
      await expect(card).toHaveClass(/lq-glass--thick/);
      await expect(card).toHaveAttribute('data-lq-tone', tone);
      await card.evaluate(node => node.querySelectorAll<HTMLElement>('h1,.subtitle,.lq-field__label,.lq-btn__label,.link-button,.footer-links a').forEach(el => {
        if (el.getClientRects().length && !el.children.length) el.setAttribute('data-lq-pixel-text', '');
      }));
      const pixels = await inspectTextPixels(page);
      expect(pixels.status, `${label}: ${JSON.stringify(pixels.samples)}`).toBe('passed');
      expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
      await page.screenshot({ path: info.outputPath(`${label}-tier-b.png`), fullPage: true });
    }
  });
  for (const role of ['student', 'teacher']) for (const width of [390, 1440]) {
    test(`actual scene photos preserve paired glass and readable login ${role} ${width}`, async ({ page }, info) => {
      test.setTimeout(120000);
      await page.setViewportSize({ width, height: 900 });
      const records = [];
      for (const [label, file, tone] of photos) {
        await page.unrouteAll({ behavior: 'wait' });
        await mount(page, `${role}_login_v4`, { sceneFile: file });
        const card = page.locator('.lq-login-card');
        await expect(card).toHaveAttribute('data-lq-scene-state', 'ready');
        for (const appearance of ['light', 'dark']) {
          await theme(page, 'indigo', appearance);
          await expect(card).toHaveClass(/lq-glass--clear/);
          await expect(card).toHaveAttribute('data-lq-tone', tone);
          await expect(page.locator('.lq-login-frame > .lq-scrim')).toHaveCount(0);
          expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
          await card.evaluate(node => node.querySelectorAll<HTMLElement>('h1,.subtitle,.lq-field__label,.lq-btn__label,.link-button,.footer-links a').forEach(el => {
            if (el.getClientRects().length && !el.children.length) el.setAttribute('data-lq-pixel-text', '');
          }));
          await page.locator('.site-record-footer :is(a,p,span)').evaluateAll(nodes => nodes.forEach(node => node.setAttribute('data-lq-pixel-text', '')));
          const pixels = await inspectTextPixels(page);
          expect(pixels.status, `${label}/${appearance}: ${JSON.stringify(pixels.samples)}`).toBe('passed');
          expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
          const material = await card.evaluate(node => ({ fill: getComputedStyle(node).backgroundColor, ink: getComputedStyle(node).color, filter: getComputedStyle(node).backdropFilter }));
          records.push({ label, file, appearance, tone, material, pixels });
          await page.screenshot({ path: info.outputPath(`${label}-${appearance}.png`), fullPage: true });
        }
      }
      const evidence = info.outputPath('photo-material-pixels.json');
      fs.writeFileSync(evidence, JSON.stringify({ assetRoot, role, width, records }, null, 2));
      await info.attach('photo-material-pixels', { path: evidence, contentType: 'application/json' });
    });
  }

  test('disabled JavaScript and failed modules retain SSR fields and native POST without a scene', async ({ browser }) => {
    for (const script of [false, true]) {
      const context = await browser.newContext({ javaScriptEnabled: script, viewport: { width: 390, height: 660 } });
      const page = await context.newPage();
      try {
        await mount(page, 'student_login_v4', { modules: false });
        await expect(page.locator('#student-password-login-form')).toHaveAttribute('method', 'post');
        await expect(page.locator('#student-password-login-form')).toHaveAttribute('action', '/student/login');
        await page.locator('#password').fill('typed-without-module');
        await page.locator('button[type=submit]').first().scrollIntoViewIfNeeded();
        await expect(page.locator('button[type=submit]').first()).toBeInViewport();
        await expect(page.locator('.lq-login-card')).toHaveClass(/lq-glass--thick/);
        await expect(page.locator('.login-scene-backdrop')).toHaveCount(0);
      } finally { await context.close(); }
    }
  });

  for (const width of [1440, 390]) for (const appearance of ['light','dark']) test(`real student form and all status pages remain readable and reachable ${appearance} ${width}`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: width === 390 ? 660 : 980 });
    await mount(page);
    await expect(page.locator('.lq-login-card')).toHaveAttribute('data-lq-scene-state', 'ready');
    for (const palette of palettes) {
      await theme(page, palette, appearance);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
    }
    await page.screenshot({ path: info.outputPath(`student-${appearance}-${width}.png`), fullPage: true });
    for (const name of ['teacher_login_v4','teacher_register_v4','permission_denied','status','status_success','error','session_expired']) {
      await page.unrouteAll({ behavior: 'wait' });
      await mount(page, name, { image: 'failure' });
      await theme(page, 'indigo', appearance, 'off');
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
      await page.screenshot({ path: info.outputPath(`${name}-${appearance}-${width}.png`), fullPage: true });
    }
  });
});
