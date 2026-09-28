import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

async function mount(page: Page) {
  await page.route('https://overflow-label.test/**', route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/release.css') return route.fulfill({ contentType: 'text/css', body: fs.readFileSync(process.env.LQ_OVERFLOW_CSS || 'static/css/tailwind-app.css') });
    if (url.pathname.startsWith('/static/js/')) {
      const file = path.resolve('.' + url.pathname);
      if (!file.startsWith(path.resolve('static/js') + path.sep)) throw new Error('Unexpected module path');
      return route.fulfill({ contentType: 'text/javascript', body: fs.readFileSync(file) });
    }
    return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh-CN" data-lq-motion="standard"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/release.css">
      <style>body{padding:16px}#scope{display:grid;gap:12px;width:180px;max-width:100%}#scope button{width:160px;min-width:0}#outside{margin-bottom:12px}</style>
      <button id="outside">外部操作</button><section id="scope"></section>
      <script type="module">
      import {createComponent} from '/static/js/lq/components.js';
      import {bindOverflowLabels} from '/static/js/lq/overflow-label.js';
      window.bind=bindOverflowLabels;window.clicks=0;
      window.make=(id,label,options={})=>{
        const button=createComponent('button',{id,label,variant:'glass',size:'sm',...options});
        button.dataset.lqOverflowLabel='';
        const viewport=button.querySelector('.lq-btn__label');viewport.dataset.lqOverflowViewport='';
        const text=document.createElement('span');text.dataset.lqOverflowText='';text.textContent=viewport.textContent;viewport.replaceChildren(text);
        button.addEventListener('click',()=>window.clicks++);return button;
      };
      const scope=document.getElementById('scope');
      scope.append(make('short','短文字'),make('long','原因：这是一段需要完整查看的调课说明与证明材料名称'),
        make('disabled','禁用的很长很长很长的证明材料文件名称',{disabled:true}),
        make('loading','读取中的很长很长很长的证明材料名称',{loading:true}),make('rtl','ملف طويل جدا لعرض النص الكامل في المرفقات'));
      document.getElementById('rtl').dir='rtl';
      window.counts={raf:0,timers:0,observers:0,labelListeners:0};
      const raf=window.requestAnimationFrame,timer=window.setTimeout,listen=EventTarget.prototype.addEventListener;
      window.requestAnimationFrame=(...args)=>{counts.raf++;return raf(...args)};
      window.setTimeout=(...args)=>{counts.timers++;return timer(...args)};
      EventTarget.prototype.addEventListener=function(...args){if(this instanceof Element&&this.matches('[data-lq-overflow-label]'))counts.labelListeners++;return listen.apply(this,args)};
      for(const name of ['MutationObserver','ResizeObserver']) { const Native=window[name];window[name]=class extends Native{constructor(...args){counts.observers++;super(...args)}}; }
      window.reveal=bindOverflowLabels(scope);window.bindingCounts={...window.counts};window.ready=true;
      </script></html>` });
  });
  await page.goto('https://overflow-label.test/');
  await page.waitForFunction(() => (window as any).ready);
}

const text = (page: Page, id = 'long') => page.locator(`#${id} [data-lq-overflow-text]`);
const shift = (page: Page, id = 'long') => text(page, id).evaluate(element => new DOMMatrixReadOnly(getComputedStyle(element).transform).m41);
const animations = (page: Page, id = 'long') => text(page, id).evaluate(element => element.getAnimations().length);

test('LQ overflow label reveals once, holds the full end, and never transforms the button or short text', async ({ page }, testInfo) => {
  await mount(page);
  await page.locator('#scope').screenshot({ path: testInfo.outputPath('labels-at-rest.png') });
  await page.locator('#short').hover();
  await expect(page.locator('#short')).not.toHaveAttribute('data-lq-overflow-active');
  expect(await animations(page, 'short')).toBe(0);
  await page.locator('#long').evaluate(element => (element as HTMLElement).style.transform = 'translateX(11px)');
  await page.locator('#long').hover();
  await expect.poll(() => shift(page)).toBeLessThan(-1);
  const end = await text(page).evaluate(element => {
    const target = parseFloat((element as HTMLElement).style.getPropertyValue('--lq-overflow-shift'));
    element.getAnimations().forEach(animation => animation.finish());
    return target;
  });
  await expect.poll(() => shift(page)).toBeCloseTo(end, 1);
  await expect.poll(() => animations(page)).toBe(0);
  const endGap = await text(page).evaluate(element => element.getBoundingClientRect().right - element.parentElement!.getBoundingClientRect().right);
  expect(Math.abs(endGap)).toBeLessThanOrEqual(1);
  await page.locator('#scope').screenshot({ path: testInfo.outputPath('label-revealed-end.png') });
  expect(await page.locator('#long').evaluate(element => new DOMMatrixReadOnly(getComputedStyle(element).transform).m41)).toBe(11);
  await page.waitForTimeout(80);
  expect(await animations(page)).toBe(0);
  await page.mouse.move(0, 0);
  expect(await shift(page)).toBe(0);
  expect(await text(page).getAttribute('style')).not.toContain('--lq-overflow-shift');
});

test('LQ overflow label preserves native click and keyboard focus, with one active label and RTL direction', async ({ page }) => {
  await mount(page);
  await page.locator('#outside').focus();
  await page.keyboard.press('Tab');
  await page.keyboard.press('Tab');
  await expect(page.locator('#long')).toBeFocused();
  await expect.poll(() => shift(page)).toBeLessThan(-1);
  await page.keyboard.press('Enter');
  expect(await page.evaluate(() => (window as any).clicks)).toBe(1);
  await page.locator('#outside').focus();
  expect(await shift(page)).toBe(0);
  await page.locator('#rtl').hover();
  await expect.poll(() => shift(page, 'rtl')).toBeGreaterThan(1);
  await expect(page.locator('[data-lq-overflow-active]')).toHaveCount(1);
  await page.mouse.move(0, 0);
  expect(await shift(page, 'rtl')).toBe(0);
});

test('LQ overflow label immediately obeys live off, quiet and reduced motion inside nested expressive scope', async ({ page }) => {
  await mount(page);
  await page.locator('#scope').evaluate(element => element.setAttribute('data-lq-motion', 'expressive'));
  await page.locator('#long').hover();
  await expect.poll(() => shift(page)).toBeLessThan(-1);
  await page.locator('#scope').evaluate(element => element.setAttribute('data-lq-motion', 'off'));
  expect(await shift(page)).toBe(0);
  expect(await animations(page)).toBe(0);
  await page.mouse.move(0, 0);
  await page.locator('#scope').evaluate(element => element.setAttribute('data-lq-motion', 'quiet'));
  await page.locator('#long').hover();
  await expect(page.locator('#long')).not.toHaveAttribute('data-lq-overflow-active');
  expect(await animations(page)).toBe(0);
  await page.mouse.move(0, 0);
  await page.locator('#scope').evaluate(element => element.setAttribute('data-lq-motion', 'expressive'));
  await page.locator('#long').hover();
  await expect.poll(() => shift(page)).toBeLessThan(-1);
  await page.emulateMedia({ reducedMotion: 'reduce' });
  expect(await shift(page)).toBe(0);
  expect(await animations(page)).toBe(0);
  await page.locator('#long').click();
  expect(await page.evaluate(() => (window as any).clicks)).toBe(1);
});

test('LQ overflow label cancels on refresh, resize and destroy, restores styles, and delegates to newly rendered slots', async ({ page }) => {
  await mount(page);
  await text(page).evaluate(element => (element as HTMLElement).style.setProperty('--lq-overflow-shift', '7px', 'important'));
  expect(await page.evaluate(() => (window as any).reveal === (window as any).bind(document.getElementById('scope')))).toBe(true);
  await page.locator('#long').hover();
  await expect(page.locator('#long')).toHaveAttribute('data-lq-overflow-active');
  await page.evaluate(() => (window as any).reveal.refresh());
  expect(await shift(page)).toBe(0);
  expect(await text(page).evaluate(element => [(element as HTMLElement).style.getPropertyValue('--lq-overflow-shift'), (element as HTMLElement).style.getPropertyPriority('--lq-overflow-shift')])).toEqual(['7px', 'important']);
  await page.mouse.move(0, 0);
  await page.evaluate(() => { const w = window as any; document.getElementById('scope')!.replaceChildren(w.make('long', '重新渲染后的很长很长很长的证明材料名称')); });
  await page.locator('#long').hover();
  await expect.poll(() => shift(page)).toBeLessThan(-1);
  await page.setViewportSize({ width: 320, height: 720 });
  expect(await shift(page)).toBe(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
  await page.mouse.move(0, 0);
  await page.locator('#long').hover();
  await page.evaluate(() => { const w = window as any; w.reveal.destroy(); w.reveal.destroy(); });
  expect(await shift(page)).toBe(0);
  expect(await animations(page)).toBe(0);
  await page.mouse.move(0, 0);
  await page.locator('#long').hover();
  await expect(page.locator('#long')).not.toHaveAttribute('data-lq-overflow-active');
});

test('LQ overflow label excludes disabled and loading states and adds no idle work or per-label listeners', async ({ page }) => {
  await mount(page);
  const before = await page.evaluate(() => ({ ...(window as any).counts }));
  await page.waitForTimeout(120);
  expect(await page.evaluate(() => (window as any).counts)).toEqual(before);
  // Playwright's later injected selector helper may construct its own observer;
  // binding is measured synchronously, while the idle delta still stays zero.
  expect(await page.evaluate(() => (window as any).bindingCounts)).toEqual({ raf: 0, timers: 0, observers: 0, labelListeners: 0 });
  expect(before).toMatchObject({ raf: 0, timers: 0, labelListeners: 0 });
  for (const id of ['disabled', 'loading']) {
    await page.locator(`#${id}`).hover();
    await expect(page.locator(`#${id}`)).not.toHaveAttribute('data-lq-overflow-active');
    expect(await animations(page, id)).toBe(0);
  }
  await page.locator('#long').hover();
  await expect.poll(() => shift(page)).toBeLessThan(-1);
  await page.locator('#long').evaluate(element => element.setAttribute('aria-busy', 'true'));
  expect(await shift(page)).toBe(0);
  expect(await animations(page)).toBe(0);
});

test.describe('touch overflow label', () => {
  test.use({ hasTouch: true, isMobile: true, viewport: { width: 320, height: 740 } });
  test('LQ overflow label leaves tap access and complete accessible name intact without hover motion', async ({ page }, testInfo) => {
    await mount(page);
    await page.locator('#long').tap();
    expect(await page.evaluate(() => (window as any).clicks)).toBe(1);
    expect(await shift(page)).toBe(0);
    expect(await animations(page)).toBe(0);
    await expect(page.locator('#long')).toHaveAccessibleName('原因：这是一段需要完整查看的调课说明与证明材料名称');
    await expect(page.locator('#long')).not.toHaveAttribute('data-lq-overflow-active');
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
    await page.screenshot({ path: testInfo.outputPath('labels-touch-320.png') });
  });
});
