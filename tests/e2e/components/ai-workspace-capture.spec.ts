import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

async function mount(page: Page, mode = 'current') {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('https://capture.test/**', route => {
    const url = new URL(route.request().url());
    if (url.pathname.startsWith('/static/')) {
      const file = path.resolve('.' + url.pathname);
      if (!file.startsWith(path.resolve('static') + path.sep) || !fs.existsSync(file)) throw new Error(`Unexpected static file ${file}`);
      return route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body: fs.readFileSync(file) });
    }
    return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html data-appearance="light"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/static/css/lq/tokens.css"><style>body{margin:0;height:2200px;background:#d8eef0;font:16px Arial}#assistant{position:fixed;right:20px;top:20px}#page-action{position:fixed;top:70px;left:10px}</style></head><body><button id="page-action">原页面操作</button><div id="assistant"><button id="capture">截图</button></div><script type="module">
      import {capturePageImage,capturePixelRect} from '/static/js/ai_workspace_capture.js';
      window.capturePixelRect=capturePixelRect;
      window.stats={hide:0,restore:0,stops:0,requests:0,notifications:[],pageClicks:0,pageKeys:0};
      document.getElementById('page-action').onclick=()=>stats.pageClicks++;
      document.addEventListener('keydown',()=>stats.pageKeys++);
      window.captureMode=${JSON.stringify(mode)};
      let config;
      navigator.mediaDevices.setCaptureHandleConfig=value=>{config=value;window.captureConfig=value;};
      Object.defineProperty(MediaStreamTrack.prototype,'getCaptureHandle',{configurable:true,value(){return null;}});
      const createStream=()=>{
        const bitmap=document.createElement('canvas');bitmap.width=innerWidth*2;bitmap.height=innerHeight*2;
        const ctx=bitmap.getContext('2d');ctx.fillStyle='rgb(12,34,56)';ctx.fillRect(0,0,bitmap.width,bitmap.height);
        ctx.fillStyle='rgb(70,140,210)';ctx.fillRect(bitmap.width/2,0,bitmap.width/2,bitmap.height);
        const stream=bitmap.captureStream(10),track=stream.getVideoTracks()[0],originalStop=track.stop.bind(track);
        const identity={handle:config?.handle,origin:location.origin};
        let identityReads=0;
        track.getCaptureHandle=()=>captureMode==='other-tab'||(captureMode==='changed-tab'&&++identityReads>1)?{...identity,handle:'other'}:identity;
        track.getSettings=()=>({displaySurface:['monitor','window'].includes(captureMode)?captureMode:'browser'});
        track.stop=()=>{stats.stops++;originalStop();};
        window.fakeStream=stream;return stream;
      };
      navigator.mediaDevices.getDisplayMedia=options=>{
        stats.requests++;window.requestOptions=options;
        if(captureMode==='denied')return Promise.reject(new DOMException('Denied','NotAllowedError'));
        if(captureMode==='pending')return new Promise(resolve=>window.acceptPending=()=>resolve(createStream()));
        return Promise.resolve(createStream());
      };
      if(captureMode==='unsupported')navigator.mediaDevices.setCaptureHandleConfig=undefined;
      window.ownerController=new AbortController();
      window.startCapture=()=>capturePageImage({signal:ownerController.signal,hideAssistant:()=>{stats.hide++;document.getElementById('assistant').hidden=true;},restoreAssistant:()=>{stats.restore++;document.getElementById('assistant').hidden=false;document.getElementById('capture').focus();},notify:message=>stats.notifications.push(message)});
      document.getElementById('capture').onclick=()=>{window.done=false;window.capturePromise=startCapture().then(value=>{window.file=value;window.done=true;});};
      window.ready=true;
    </script></body></html>` });
  });
  await page.goto('https://capture.test/');
  await page.waitForFunction(() => (window as any).ready);
  return errors;
}

async function openCapture(page: Page) {
  await page.locator('#capture').click();
  await expect(page.locator('.ai-workspace-capture')).toHaveAttribute('data-phase', 'select');
}

async function drag(page: Page, from: { x: number; y: number }, to: { x: number; y: number }) {
  await page.mouse.move(from.x, from.y); await page.mouse.down();
  await page.mouse.move(to.x, to.y, { steps: 5 }); await page.mouse.up();
}

test('current-tab frame is stopped before cropping, supports all annotations and returns only a PNG attachment', async ({ page }, info) => {
  await page.setViewportSize({ width: 1000, height: 760 });
  const errors = await mount(page);
  await openCapture(page);
  expect(await page.evaluate(() => (window as any).stats.stops)).toBe(1);
  expect(await page.evaluate(() => (window as any).requestOptions)).toMatchObject({ audio: false, preferCurrentTab: true, monitorTypeSurfaces: 'exclude', surfaceSwitching: 'exclude' });
  await drag(page, { x: 60, y: 100 }, { x: 420, y: 340 });
  const canvas = page.locator('.ai-capture-canvas');
  await expect(page.locator('.ai-workspace-capture')).toHaveAttribute('data-phase', 'edit');
  expect(await canvas.evaluate((node: HTMLCanvasElement) => [node.width, node.height])).toEqual([720, 480]);
  await expect(page.getByLabel('标注颜色')).toHaveValue('#ef4444'); await expect(page.getByLabel('标注粗细')).toHaveValue('5');
  const baseline = await canvas.evaluate((node: HTMLCanvasElement) => node.toDataURL());
  const rect = (await canvas.boundingBox())!;
  const from = { x: rect.x + 35, y: rect.y + 35 }, to = { x: rect.x + 125, y: rect.y + 95 };
  await drag(page, from, to);
  expect(await canvas.evaluate((node: HTMLCanvasElement) => node.toDataURL())).not.toBe(baseline);
  await page.getByRole('button', { name: '撤销', exact: true }).click();
  expect(await canvas.evaluate((node: HTMLCanvasElement) => node.toDataURL())).toBe(baseline);
  await expect(page.getByRole('button', { name: '撤销', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: '自由笔', exact: true }).click(); await drag(page, from, to);
  await page.keyboard.press('Control+z');
  expect(await canvas.evaluate((node: HTMLCanvasElement) => node.toDataURL())).toBe(baseline);
  await page.getByRole('button', { name: '箭头', exact: true }).click();
  await page.getByLabel('标注颜色').fill('#3b82f6'); await page.getByLabel('标注粗细').fill('10');
  await drag(page, from, to);
  const expectedPixels = await canvas.evaluate(async (node: HTMLCanvasElement) => {
    const pixels = node.getContext('2d')!.getImageData(0, 0, node.width, node.height).data;
    return { base: [...pixels.slice(0, 4)], digest: [...new Uint8Array(await crypto.subtle.digest('SHA-256', pixels))] };
  });
  await page.screenshot({ path: info.outputPath('capture-annotated.png') });
  await page.getByRole('button', { name: '插入附件', exact: true }).click();
  await page.waitForFunction(() => (window as any).done);
  const output = await page.evaluate(async () => {
    const w = window as any, file = w.file as File, bitmap = await createImageBitmap(file);
    const output = document.createElement('canvas'); output.width = bitmap.width; output.height = bitmap.height;
    const ctx = output.getContext('2d')!; ctx.drawImage(bitmap, 0, 0); bitmap.close();
    const pixels = ctx.getImageData(0, 0, output.width, output.height).data;
    let bluePixels = 0;
    for (let i = 0; i < pixels.length; i += 4) if (pixels[i] === 59 && pixels[i + 1] === 130 && pixels[i + 2] === 246) bluePixels++;
    return { type: file.type, name: file.name, width: output.width, height: output.height, base: [...pixels.slice(0, 4)], digest: [...new Uint8Array(await crypto.subtle.digest('SHA-256', pixels))], bluePixels, stats: w.stats };
  });
  // Video decoding may convert RGB by one level; PNG must match the actual
  // frozen-and-annotated bitmap exactly, without any subsequent pixel loss.
  expect(output).toMatchObject({ type: 'image/png', width: 720, height: 480, ...expectedPixels });
  expect(output.name).toMatch(/\.png$/); expect(output.bluePixels).toBeGreaterThan(100);
  expect(output.stats).toMatchObject({ hide: 1, restore: 1, stops: 1, requests: 1, notifications: [] });
  await expect(page.locator('.ai-workspace-capture')).toHaveCount(0);
  await expect(page.locator('#capture')).toBeFocused(); expect(errors).toEqual([]);
});

test('small selections retry, frozen page cannot scroll or receive keys, Escape restores without a file', async ({ page }) => {
  const errors = await mount(page); await page.evaluate(() => scrollTo(0, 180));
  await openCapture(page);
  await drag(page, { x: 60, y: 100 }, { x: 63, y: 102 });
  await expect(page.locator('[data-capture-hint]')).toContainText('区域太小');
  await expect(page.locator('.ai-workspace-capture')).toHaveAttribute('data-phase', 'select');
  const y = await page.evaluate(() => scrollY), keyCount = await page.evaluate(() => (window as any).stats.pageKeys);
  await page.mouse.move(300, 250); await page.mouse.wheel(0, 500); await page.keyboard.press('ArrowDown');
  await page.locator('[data-capture-toolbar]').hover(); await page.mouse.wheel(0, 500);
  expect(await page.evaluate(() => scrollY)).toBe(y);
  expect(await page.evaluate(() => (window as any).stats.pageKeys)).toBe(keyCount);
  await page.keyboard.press('Escape'); await page.waitForFunction(() => (window as any).done);
  expect(await page.evaluate(() => ({ file: (window as any).file, stats: (window as any).stats }))).toMatchObject({ file: null, stats: { hide: 1, restore: 1, stops: 1, pageClicks: 0 } });
  await expect(page.locator('.ai-workspace-capture')).toHaveCount(0); expect(errors).toEqual([]);
});

for (const mode of ['monitor', 'window', 'other-tab', 'changed-tab', 'denied', 'unsupported']) test(`capture ${mode} exits safely and restores its owner`, async ({ page }) => {
  const errors = await mount(page, mode); await page.locator('#capture').click();
  await page.waitForFunction(() => (window as any).done);
  const state = await page.evaluate(() => ({ file: (window as any).file, stats: (window as any).stats, config: (window as any).captureConfig }));
  expect(state.file).toBeNull(); expect(state.stats.hide).toBe(mode === 'unsupported' ? 0 : 1);
  expect(state.stats.restore).toBe(state.stats.hide); expect(state.stats.stops).toBe(['monitor', 'window', 'other-tab', 'changed-tab'].includes(mode) ? 1 : 0);
  expect(state.stats.notifications.length).toBe(mode === 'denied' ? 0 : 1);
  if (mode !== 'unsupported') expect(state.config).toMatchObject({ handle: '', permittedOrigins: [] });
  await expect(page.locator('.ai-workspace-capture')).toHaveCount(0); expect(errors).toEqual([]);
});

test('PNG export failure retains annotations for retry and owner abort releases the editor', async ({ page }) => {
  const errors = await mount(page); await openCapture(page);
  await page.getByRole('button', { name: '选择整个可见区域', exact: true }).click();
  const canvas = page.locator('.ai-capture-canvas');
  const rect = (await canvas.boundingBox())!;
  await drag(page, { x: rect.x + 20, y: rect.y + 20 }, { x: rect.x + 120, y: rect.y + 80 });
  const annotated = await canvas.evaluate((node: HTMLCanvasElement) => {
    node.toBlob = callback => callback(null);
    return node.toDataURL();
  });
  await page.getByRole('button', { name: '插入附件', exact: true }).click();
  await expect.poll(() => page.evaluate(() => (window as any).stats.notifications)).toEqual(['截图生成失败，请重试。']);
  await expect(page.getByRole('button', { name: '插入附件', exact: true })).toBeEnabled();
  expect(await canvas.evaluate((node: HTMLCanvasElement) => node.toDataURL())).toBe(annotated);
  await page.evaluate(() => (window as any).ownerController.abort()); await page.waitForFunction(() => (window as any).done);
  expect(await page.evaluate(() => ({ file: (window as any).file, stats: (window as any).stats }))).toMatchObject({ file: null, stats: { hide: 1, restore: 1, stops: 1 } });
  await expect(page.locator('.ai-workspace-capture')).toHaveCount(0); expect(errors).toEqual([]);
});

test('cancellation while the browser chooser is pending restores immediately and stops a late granted stream', async ({ page }) => {
  const errors = await mount(page, 'pending'); await page.locator('#capture').click();
  await expect(page.locator('.ai-workspace-capture')).toHaveAttribute('data-phase', 'capture');
  await page.keyboard.press('Escape'); await page.waitForFunction(() => (window as any).done);
  expect(await page.evaluate(() => (window as any).stats.restore)).toBe(1);
  await page.evaluate(() => (window as any).acceptPending());
  await expect.poll(() => page.evaluate(() => (window as any).stats.stops)).toBe(1);
  expect(await page.evaluate(() => (window as any).file)).toBeNull(); expect(errors).toEqual([]);
});

test('mobile-sized frozen frame, reverse drags and DPR mapping retain exact pixel bounds', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 }); const errors = await mount(page); await openCapture(page);
  const mapped = await page.evaluate(() => (window as any).capturePixelRect({ x: 420, y: 320 }, { x: -5, y: 40 }, { width: 390, height: 844 }, { width: 780, height: 1688 }));
  expect(mapped).toEqual({ x: 0, y: 80, width: 780, height: 560 });
  await drag(page, { x: 350, y: 330 }, { x: 40, y: 100 });
  await expect(page.locator('.ai-workspace-capture')).toHaveAttribute('data-phase', 'edit');
  const canvas = page.locator('.ai-capture-canvas');
  expect(await canvas.evaluate((node: HTMLCanvasElement) => [node.width, node.height])).toEqual([620, 460]);
  const rect = (await canvas.boundingBox())!; expect(rect.width / rect.height).toBeCloseTo(620 / 460, 2);
  expect(rect.x).toBeGreaterThanOrEqual(0); expect(rect.x + rect.width).toBeLessThanOrEqual(390);
  await page.getByRole('button', { name: '取消', exact: true }).click(); await page.waitForFunction(() => (window as any).done);
  expect(await page.evaluate(() => (window as any).stats.restore)).toBe(1); expect(errors).toEqual([]);
});
