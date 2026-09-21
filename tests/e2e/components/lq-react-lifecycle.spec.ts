import { test, expect, type Page } from '@playwright/test';
import path from 'node:path';

// Compile the real lifecycle modules with development React so StrictMode's
// setup -> cleanup -> setup runs. Only unrelated workspace UI/legacy modules
// are fixtures. All browser URLs are intercepted; no app server or DB is used.
let fixtureScript: string;
test.beforeAll(async () => {
  const { build } = await import('vite');
  const react = (await import('@vitejs/plugin-react')).default;
  const sourceRoot = path.resolve('frontend/src').replaceAll('\\', '/');
  const entry = '\0lq-react-lifecycle-fixture.tsx';
  const result = await build({
    configFile: false,
    logLevel: 'error',
    publicDir: false,
    define: { 'process.env.NODE_ENV': JSON.stringify('development') },
    resolve: { alias: { '@': sourceRoot } },
    plugins: [react(), {
      name: 'lq-react-lifecycle-fixture',
      enforce: 'pre',
      resolveId(id, importer) {
        if (id.replaceAll('\\', '/').endsWith('/lq-react-lifecycle-fixture') || id === 'lq-react-lifecycle-fixture') return entry;
        if (importer?.replaceAll('\\', '/').endsWith('/islands/classroom-page.tsx')) {
          if (id === './classroom-workspace') return '\0lq-workspace-fixture';
          if (id === './assignment-authoring-sync' || id === './exam-assign-sync') return '\0lq-secondary-fixture';
        }
      },
      load(id) {
        if (id === '\0lq-secondary-fixture') return 'export {};';
        if (id === '\0lq-workspace-fixture') return `
          import { createElement, useEffect } from 'react';
          export function ClassroomWorkspace() {
            useEffect(() => {
              window.fixture.classroomSetups++;
              window.fixture.classroomLive++;
              return () => { window.fixture.classroomLive--; };
            }, []);
            return createElement('div', {id:'classroom-view'}, 'workspace fixture');
          }`;
        if (id !== entry) return;
        return `
          import { createElement, useEffect } from 'react';
          import { mountReactIslands, mountReactIslandsWhenReady, unmountReactIsland } from '${sourceRoot}/lib/mount-react-island.tsx';
          import { classroomReadiness } from '${sourceRoot}/lib/classroom-bootstrap-ready.ts';
          function Probe() {
            useEffect(() => {
              window.fixture.probeSetups++;
              window.fixture.probeLive++;
              const listener = () => { window.fixture.probeEvents++; };
              window.addEventListener('probe-event', listener);
              const timer = window.setInterval(() => {}, 100000);
              return () => {
                window.fixture.probeLive--;
                window.fixture.probeCleanups++;
                window.removeEventListener('probe-event', listener);
                window.clearInterval(timer);
              };
            }, []);
            return createElement('button', {id:'probe-button'}, 'probe');
          }
          const probeOptions = { islandName:'probe', getProps:()=>({}), render:()=>createElement(Probe) };
          window.lifecycle = {
            mountProbe: () => mountReactIslands(probeOptions),
            readyProbe: () => mountReactIslandsWhenReady(probeOptions),
            unmount: id => unmountReactIsland(document.getElementById(id)),
            loadClassroom: () => import('${sourceRoot}/islands/classroom-page.tsx'),
            remountClassroom: async () => {
              const { ClassroomPageController } = await import('${sourceRoot}/islands/classroom-page.tsx');
              return mountReactIslands({islandName:'classroom-page',getProps:()=>({}),render:()=>createElement(ClassroomPageController)});
            },
            wait: () => classroomReadiness.wait(),
          };
          window.fixtureLoaded = true;`;
      },
    }],
    build: {
      write: false,
      minify: false,
      lib: { entry: 'lq-react-lifecycle-fixture', formats: ['es'] },
      rolldownOptions: { output: { codeSplitting: false } },
    },
  });
  const outputs = (Array.isArray(result) ? result : [result]).flatMap(item => 'output' in item ? item.output : []);
  const chunk = outputs.find(item => item.type === 'chunk' && item.isEntry);
  if (!chunk || chunk.type !== 'chunk') throw new Error('Lifecycle fixture did not compile');
  fixtureScript = chunk.code;
});

async function openFixture(page: Page) {
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.origin !== 'http://lq-lifecycle.test') {
      await route.abort();
      throw new Error(`Unexpected fixture request: ${url.origin}`);
    }
    if (url.pathname === '/fixture.js') {
      await route.fulfill({ contentType: 'text/javascript', body: fixtureScript });
    } else if (url.pathname.startsWith('/static/js/')) {
      const name = path.posix.basename(url.pathname);
      await route.fulfill({ contentType: 'text/javascript', body: `
        window.fixture.moduleLoads++;
        ${name === 'ui.js' ? 'await window.nativeGate;' : ''}
        const record = name => { window.fixture.nativeCalls.push(name); };
        export const showToast = () => { window.fixture.toasts++; };
        export const init = () => record(${JSON.stringify(name)});
        export const initClassroomPage = () => record('classroom');
        export const initLearningProgress = () => record('progress');
        export const initClassroomInteractions = () => record('interactions');
        export const initCollaborationPanel = () => record('collaboration');
        export const initClassroomPolls = () => record('polls');
        export class ClassroomChat { init() { record('chat'); } scheduleDiscussionRoomResize() {} }
        export class ClassroomPrivateMessages { init() { record('private'); } }
      ` });
    } else if (url.pathname === '/') {
      await route.fulfill({ contentType: 'text/html', body: `<!doctype html><meta charset="utf-8">
        <div id="probe" data-lanshare-island="probe"></div>
        <div id="classroom" data-lanshare-island="classroom-page" data-classroom-page-app></div>
        <script>
          window.fixture = {probeSetups:0,probeCleanups:0,probeLive:0,probeEvents:0,classroomSetups:0,classroomLive:0,moduleLoads:0,nativeCalls:[],toasts:0,ready:'pending'};
          window.nativeGate = new Promise((resolve,reject) => {window.finishNative=resolve;window.failNative=reject;});
          window.UI = {showToast:()=>{window.fixture.toasts++;}};
          window.APP_CONFIG = {classOfferingId:123,userInfo:{role:'student'}};
        </script><script type="module" src="/fixture.js"></script>` });
    } else {
      await route.abort();
      throw new Error(`Unexpected fixture path: ${url.pathname}`);
    }
  });
  await page.goto('http://lq-lifecycle.test/');
  await page.waitForFunction(() => (window as any).fixtureLoaded);
}

test('real StrictMode cleanup, duplicate owners and old disposers preserve the current root', async ({ page }) => {
  await openFixture(page);
  await page.evaluate(() => {
    const w = window as any;
    w.first = w.lifecycle.mountProbe();
  });
  await expect.poll(() => page.evaluate(() => (window as any).fixture.probeSetups)).toBe(2);
  expect(await page.evaluate(() => (window as any).fixture.probeCleanups)).toBe(1);
  await page.evaluate(() => (window as any).lifecycle.mountProbe().dispose());
  expect(await page.evaluate(() => (window as any).fixture.probeLive)).toBe(1);
  await page.evaluate(() => {
    const w = window as any;
    w.lifecycle.unmount('probe');
    window.dispatchEvent(new Event('probe-event'));
  });
  expect(await page.evaluate(() => (window as any).fixture.probeLive)).toBe(0);
  expect(await page.evaluate(() => (window as any).fixture.probeEvents)).toBe(0);
  await page.evaluate(() => {
    const w = window as any;
    w.replacement = w.lifecycle.mountProbe();
    w.first.dispose();
    w.first.dispose();
  });
  await expect(page.locator('#probe-button')).toBeVisible();
  await expect.poll(() => page.evaluate(() => (window as any).fixture.probeLive)).toBe(1);
  await expect(page.locator('#probe')).toHaveAttribute('data-react-mounted', 'true');
  await page.evaluate(() => (window as any).replacement.dispose());
  await expect(page.locator('#probe-button')).toHaveCount(0);
  expect(await page.evaluate(() => (window as any).fixture.probeLive)).toBe(0);
});

test('DOMContentLoaded registration can be cancelled before mounting real React', async ({ page }) => {
  await openFixture(page);
  await page.evaluate(() => {
    const w = window as any;
    Object.defineProperty(document, 'readyState', {configurable:true, get:()=> 'loading'});
    const pending = w.lifecycle.readyProbe();
    pending.dispose();
    document.dispatchEvent(new Event('DOMContentLoaded'));
    delete (document as any).readyState;
  });
  expect(await page.evaluate(() => (window as any).fixture.probeSetups)).toBe(0);
  await page.evaluate(() => { (window as any).readyHandle = (window as any).lifecycle.readyProbe(); });
  await expect.poll(() => page.evaluate(() => (window as any).fixture.probeSetups)).toBe(2);
  await page.evaluate(() => (window as any).readyHandle.dispose());
  expect(await page.evaluate(() => (window as any).fixture.probeLive)).toBe(0);
});

async function startDelayedClassroom(page: Page) {
  await openFixture(page);
  await page.evaluate(async () => {
    const w = window as any;
    void w.lifecycle.wait().then(() => {w.fixture.ready='success';}, (error: Error) => {w.fixture.ready=error.message;});
    await w.lifecycle.loadClassroom();
  });
  await expect.poll(() => page.evaluate(() => (window as any).fixture.classroomSetups)).toBe(2);
  await expect.poll(() => page.evaluate(() => (window as any).fixture.moduleLoads)).toBe(11);
  expect(await page.evaluate(() => (window as any).fixture.ready)).toBe('pending');
  expect(await page.evaluate(() => (window as any).fixture.nativeCalls)).toEqual([]);
}

test('StrictMode waits for the real bootstrap and React unmount does not duplicate native initialization', async ({ page }) => {
  await startDelayedClassroom(page);
  await page.evaluate(() => {
    const w = window as any;
    w.lifecycle.unmount('classroom');
    w.finishNative();
  });
  await expect.poll(() => page.evaluate(() => (window as any).fixture.ready)).toBe('success');
  expect(await page.evaluate(() => (window as any).fixture.classroomLive)).toBe(0);
  const expected = ['classroom','progress','interactions','collaboration','polls','chat','private','app_files.js','classroom_materials.js','app_exams.js'];
  expect(await page.evaluate(() => (window as any).fixture.nativeCalls)).toEqual(expected);
  await page.evaluate(async () => { (window as any).newClassroom = await (window as any).lifecycle.remountClassroom(); });
  await expect.poll(() => page.evaluate(() => (window as any).fixture.classroomSetups)).toBe(4);
  expect(await page.evaluate(() => (window as any).fixture.nativeCalls)).toEqual(expected);
  await page.evaluate(() => (window as any).newClassroom.dispose());
  expect(await page.evaluate(() => (window as any).fixture.classroomLive)).toBe(0);
});

test('a failed delayed bootstrap stays failed across unmount and remount without duplicate reporting', async ({ page }) => {
  await startDelayedClassroom(page);
  await page.evaluate(() => {
    const w = window as any;
    w.lifecycle.unmount('classroom');
    w.failNative(new Error('fixture module unavailable'));
  });
  await expect.poll(() => page.evaluate(() => (window as any).fixture.ready)).toBe('fixture module unavailable');
  await page.evaluate(async () => { (window as any).newClassroom = await (window as any).lifecycle.remountClassroom(); });
  await expect.poll(() => page.evaluate(() => (window as any).fixture.classroomSetups)).toBe(4);
  const result = await page.evaluate(async () => {
    const w = window as any;
    let error;
    try { await w.lifecycle.wait(); } catch (failure) { error = (failure as Error).message; }
    w.newClassroom.dispose();
    return {error, ready:w.fixture.ready, toasts:w.fixture.toasts, calls:w.fixture.nativeCalls, loads:w.fixture.moduleLoads};
  });
  expect(result).toEqual({error:'fixture module unavailable',ready:'fixture module unavailable',toasts:1,calls:[],loads:11});
  await expect(page.locator('#classroom')).toHaveAttribute('data-classroom-page-controller-mounted', 'false');
});
