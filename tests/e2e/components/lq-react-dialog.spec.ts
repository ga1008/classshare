import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { buildReactFixture } from './lq-react-fixture';

let script: string;
test.beforeAll(async () => {
  script = await buildReactFixture(`
    import React, {useState, useLayoutEffect, useRef} from 'react';
    import {Dialog,DialogContent,DialogTitle,DialogDescription} from '@/components/ui/dialog';
    import {mountReactIslands,unmountReactIsland} from '@/lib/mount-react-island';
    import {loadLayerSystem} from '@/lib/lq-layer';
    function OriginalSurface(){
      const host=useRef(null);
      useLayoutEffect(()=>{
        const node=document.getElementById('original'),parent=node.parentNode,next=node.nextSibling,hidden=node.hidden;
        host.current.append(node);node.hidden=false;
        return ()=>{node.hidden=hidden;parent.insertBefore(node,next?.parentNode===parent?next:null);};
      },[]);
      return <div ref={host}/>;
    }
    function Fixture(){
      const [open,setOpen]=useState(false);
      const [revision,setRevision]=useState(0);
      window.setDialogOpen=setOpen;
      window.dialogOpen=open;
      window.rerenderDialog=()=>setRevision(value=>value+1);
      return <><button id="trigger" onClick={()=>setOpen(true)}>open</button>
        <Dialog open={open} onOpenChange={setOpen}>
          <DialogContent data-revision={revision} beforeClose={()=>window.beforeClose?.()}
            onOpenAutoFocus={event=>{window.stats.initial++;if(window.cancelInitial){event.preventDefault();document.getElementById('chosen').focus();}}}
            onCloseAutoFocus={event=>{window.stats.closeFocus++;if(window.cancelReturn)event.preventDefault();}}
            onAfterClose={()=>{window.stats.closed++;window.stats.restored=document.getElementById('original').parentElement.id==='storage';}}>
            <DialogTitle>Fixture dialog</DialogTitle><DialogDescription>Real shared native coordinator</DialogDescription>
            <input id="first" aria-label="first"/><input id="chosen" aria-label="chosen"/><button id="react-hit" onClick={()=>{window.stats.reactHits=(window.stats.reactHits||0)+1;}}>React hit</button><OriginalSurface/>
          </DialogContent>
        </Dialog></>;
    }
    window.fixtureApi={
      mount:()=>mountReactIslands({islandName:'fixture',getProps:()=>({}),render:()=> <Fixture/>}),
      unmount:()=>unmountReactIsland(document.getElementById('island')),
      system:()=>loadLayerSystem(document),
    };
    window.fixtureApi.mount();window.fixtureLoaded=true;
  `);
});

async function setup(page: Page, delayImport = false) {
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  const requests: string[] = [];
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.origin !== 'http://lq-react.test') { await route.abort(); throw new Error(`Unexpected network: ${url.origin}`); }
    if (url.pathname === '/fixture.js') { await route.fulfill({ contentType:'text/javascript',body:script }); return; }
    if (url.pathname === '/static/css/tailwind-app.css') {
      await route.fulfill({contentType:'text/css',body:fs.readFileSync(path.resolve('static/css/tailwind-app.css'),'utf8')}); return;
    }
    const match = url.pathname.match(/^\/static\/(?:assets\/[a-f0-9]{64}\/)?js\/(lq\/layer\.js|ui\.js|ui_overlay_motion\.js|ui_popover\.js|ui_popover_geometry\.js)$/);
    if (match) {
      requests.push(url.pathname);
      if (delayImport && url.pathname.includes('/assets/') && match[1] === 'lq/layer.js') await gate;
      await route.fulfill({ contentType:'text/javascript',body:fs.readFileSync(path.resolve('static/js',match[1]),'utf8') }); return;
    }
    if (url.pathname !== '/') { await route.abort(); throw new Error(`Unexpected fixture path: ${url.pathname}`); }
    await route.fulfill({contentType:'text/html',body:`<!doctype html><meta charset="utf-8"><link rel="stylesheet" href="/static/css/tailwind-app.css"><style>
      /* A bounded exit lets races be exercised; geometry and order are product CSS. */
      [data-ui-dialog-overlay],[data-ui-dialog-content]{opacity:1;transition:opacity .16s}
      [data-state="closed"]{opacity:0;pointer-events:none}
      dialog{width:80%;height:80%}[hidden]{display:none!important}
    </style><body style="overflow:clip;padding-right:7px"><button id="outside">outside</button>
      <dialog id="native"><button id="native-trigger">native opener</button><div id="native-slot"></div></dialog>
      <div id="native-ui" class="modal-backdrop" hidden style="display:none"><div class="modal-content"><button id="native-ui-hit">Native UI hit</button></div></div>
      <div id="island" data-lanshare-island="fixture"></div><div id="storage"><span id="before"></span><div id="original" hidden><input id="draft" value="preserved draft"></div><span id="after"></span></div>
      <script>window.__LS_ASSET_REV='${'a'.repeat(64)}';window.stats={initial:0,closeFocus:0,closed:0};</script>
      <script type="module" src="/fixture.js"></script></body>`});
  });
  await page.goto('http://lq-react.test/');
  await page.waitForFunction(() => (window as any).fixtureLoaded);
  return { release, requests, errors };
}

const content = (page: Page) => page.locator('[data-ui-dialog-content]');

test('StrictMode uses the native singleton, preserves original DOM and restores focus after exit', async ({page}) => {
  const {errors,requests}=await setup(page);
  await page.evaluate(()=>{(window as any).original=document.getElementById('original');});
  await page.locator('#trigger').click();
  await expect(content(page)).toBeVisible();
  await expect(page.locator('[data-ui-dialog-root]')).not.toHaveAttribute('role');
  await expect(page.locator('[data-ui-dialog-root]')).not.toHaveAttribute('aria-modal');
  await expect(content(page)).toHaveAttribute('role','dialog');
  await expect(content(page)).toHaveAttribute('aria-modal','true');
  await expect(page.locator('#first')).toBeFocused();
  const singleton = await page.evaluate(async()=>{
    const w=window as any;
    const native=await import(/* @vite-ignore */ '/static/js/lq/layer.js?native');
    return (await w.fixtureApi.system())===native.getLayerSystem(document);
  });
  expect(singleton).toBe(true);
  expect(requests.some(url=>url.includes('/assets/')&&url.endsWith('lq/layer.js'))).toBe(true);
  await expect(page.locator('body')).toHaveCSS('overflow','hidden');
  await page.locator('#draft').fill('edited draft');
  await page.keyboard.press('Escape');
  await expect(content(page)).toHaveCount(0);
  await expect(page.locator('#trigger')).toBeFocused();
  expect(await page.evaluate(()=>({same:(window as any).original===document.getElementById('original'),next:document.getElementById('original')?.nextElementSibling?.id,hidden:document.getElementById('original')?.hidden,...(window as any).stats}))).toMatchObject({same:true,next:'after',hidden:true,closed:1,closeFocus:1,restored:true});
  await expect(page.locator('#draft')).toHaveValue('edited draft');
  await expect(page.locator('body')).toHaveCSS('overflow','clip');
  await expect(page.locator('body')).toHaveCSS('padding-right','7px');
  expect(errors).toEqual([]);
});

test('native modal parent hosts the React portal and Escape releases only the top layer',async({page})=>{
  const {errors}=await setup(page);
  await page.evaluate(async()=>{
    const native=await import(/* @vite-ignore */ '/static/js/lq/layer.js?parent');
    const w=window as any;
    w.parentSystem=native.getLayerSystem(document);
    document.getElementById('native-slot')!.append(document.getElementById('island')!);
    w.parentHandle=w.parentSystem.open(document.getElementById('native'),{trigger:document.getElementById('outside')});
    document.getElementById('trigger')!.focus();
  });
  await page.locator('#trigger').click();
  await expect(page.locator('#native [data-ui-dialog-content]')).toBeVisible();
  await page.locator('#chosen').click();
  await expect(page.locator('#chosen')).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(content(page)).toHaveCount(0);
  expect(await page.locator('#native').evaluate((node:HTMLDialogElement)=>node.open)).toBe(true);
  await expect(page.locator('#trigger')).toBeFocused();
  await expect(page.locator('body')).toHaveCSS('overflow','hidden');
  await page.keyboard.press('Escape');
  await expect(page.locator('#native')).not.toBeVisible();
  await expect(page.locator('#outside')).toBeFocused();
  await expect(page.locator('body')).toHaveCSS('overflow','clip');
  expect(errors).toEqual([]);
});

test('cancelable autofocus and close veto remain owned by the coordinator',async({page})=>{
  await setup(page);
  await page.evaluate(()=>{const w=window as any;w.cancelInitial=true;w.beforeClose=()=>false;});
  await page.locator('#trigger').click();
  await expect(page.locator('#chosen')).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(content(page)).toBeVisible();
  expect(await page.evaluate(()=>(window as any).stats.closed)).toBe(0);
  await page.evaluate(()=>{const w=window as any;w.beforeClose=()=>true;w.cancelReturn=true;});
  await page.keyboard.press('Escape');
  await expect(content(page)).toHaveCount(0);
  await expect(page.locator('#trigger')).not.toBeFocused();
  expect(await page.evaluate(()=>(window as any).stats.closeFocus)).toBe(1);
});

test('close then reopen keeps the same surface and cancels stale completion/focus',async({page})=>{
  await setup(page);
  await page.locator('#trigger').click();
  await expect(content(page)).toBeVisible();
  await page.evaluate(()=>{const w=window as any;w.savedSurface=document.querySelector('[data-ui-dialog-content]');w.setDialogOpen(false);});
  await expect(page.locator('[data-ui-dialog-root]')).toHaveAttribute('data-lq-layer-state','closing');
  await page.evaluate(()=>(window as any).setDialogOpen(true));
  await expect(page.locator('[data-ui-dialog-root]')).toHaveAttribute('data-lq-layer-state','open');
  expect(await page.evaluate(()=>({same:(window as any).savedSurface===document.querySelector('[data-ui-dialog-content]'),closed:(window as any).stats.closed,closeFocus:(window as any).stats.closeFocus}))).toEqual({same:true,closed:0,closeFocus:0});
  await page.keyboard.press('Escape');
  await expect(content(page)).toHaveCount(0);
  expect(await page.evaluate(()=>(window as any).stats.closed)).toBe(1);
});

test('late module resolution cannot open a closed or unmounted dialog',async({page})=>{
  const {release,requests,errors}=await setup(page,true);
  await page.locator('#trigger').click();
  await expect.poll(()=>requests.filter(url=>url.endsWith('lq/layer.js')).length).toBe(1);
  await page.evaluate(()=>{const w=window as any;w.setDialogOpen(false);w.fixtureApi.unmount();});
  release();
  await page.evaluate(async()=>{await (window as any).fixtureApi.system();});
  await expect(content(page)).toHaveCount(0);
  expect(await page.evaluate(async()=>((await (window as any).fixtureApi.system()).top()))).toBeNull();
  await expect(page.locator('body')).toHaveCSS('overflow','clip');
  expect(errors).toEqual([]);
});

test('unmount destroys a pending dirty check without closing callbacks or retained locks',async({page})=>{
  await setup(page);
  await page.evaluate(()=>{const w=window as any;w.beforeClose=()=>new Promise(resolve=>{w.allowClose=resolve;});});
  await page.locator('#trigger').click();
  await expect(content(page)).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.locator('[data-ui-dialog-root]')).toHaveAttribute('data-lq-layer-state','checking');
  await page.evaluate(()=>{const w=window as any;w.fixtureApi.unmount();w.allowClose(true);});
  await expect(content(page)).toHaveCount(0);
  await expect(page.locator('body')).toHaveCSS('overflow','clip');
  expect(await page.evaluate(()=>(window as any).stats.closed)).toBe(0);
  expect(await page.evaluate(()=>(window as any).stats.closeFocus)).toBe(0);
});

test('an ordinary rerender updates callbacks without cancelling a pending dirty check',async({page})=>{
  await setup(page);
  await page.evaluate(()=>{const w=window as any;w.beforeClose=()=>new Promise(resolve=>{w.allowClose=resolve;});});
  await page.locator('#trigger').click();await expect(content(page)).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.locator('[data-ui-dialog-root]')).toHaveAttribute('data-lq-layer-state','checking');
  await page.evaluate(()=>(window as any).rerenderDialog());
  await expect(content(page)).toHaveAttribute('data-revision','1');
  await expect(page.locator('[data-ui-dialog-root]')).toHaveAttribute('data-lq-layer-state','checking');
  await page.evaluate(()=>(window as any).allowClose(true));
  await expect(content(page)).toHaveCount(0);
  expect(await page.evaluate(()=>(window as any).stats.closed)).toBe(1);
});

for(const reject of [false,true])test(`a vetoed controlled programmatic close restores open consistently (reject=${reject})`,async({page})=>{
  await setup(page);
  await page.evaluate(fail=>{const w=window as any;w.checks=0;w.beforeClose=()=>{w.checks++;return fail?Promise.reject(new Error('dirty check failed')):false;};},reject);
  await page.locator('#trigger').click();await expect(content(page)).toBeVisible();
  await page.evaluate(()=>(window as any).setDialogOpen(false));
  await expect.poll(()=>page.evaluate(()=>(window as any).dialogOpen)).toBe(true);
  await expect(page.locator('[data-ui-dialog-root]')).toHaveAttribute('data-lq-layer-state','open');
  await page.evaluate(()=>(window as any).rerenderDialog());
  await expect(content(page)).toHaveAttribute('data-revision','1');
  expect(await page.evaluate(()=>(window as any).checks)).toBe(1);
  await expect(page.locator('body')).toHaveCSS('overflow','hidden');
  await page.evaluate(()=>{const w=window as any;w.beforeClose=()=>true;w.setDialogOpen(false);});
  await expect(content(page)).toHaveCount(0);
  expect(await page.evaluate(()=>(window as any).stats.closed)).toBe(1);
});

test('a superseded controlled close result cannot resurrect a later completed close',async({page})=>{
  await setup(page);
  await page.evaluate(()=>{const w=window as any;w.beforeClose=()=>new Promise(resolve=>{w.oldDecision=resolve;});});
  await page.locator('#trigger').click();await expect(content(page)).toBeVisible();
  await page.evaluate(()=>(window as any).setDialogOpen(false));
  await expect(page.locator('[data-ui-dialog-root]')).toHaveAttribute('data-lq-layer-state','checking');
  await page.evaluate(()=>(window as any).setDialogOpen(true));
  await expect(page.locator('[data-ui-dialog-root]')).toHaveAttribute('data-lq-layer-state','open');
  await page.evaluate(()=>{const w=window as any;w.beforeClose=()=>true;w.setDialogOpen(false);});
  await expect(content(page)).toHaveCount(0);
  await page.evaluate(()=>(window as any).oldDecision(false));
  expect(await page.evaluate(()=>(window as any).dialogOpen)).toBe(false);
  await expect(content(page)).toHaveCount(0);
  await expect(page.locator('body')).toHaveCSS('overflow','clip');
});

async function assertPhysicalHit(page: Page, selector: string) {
  const hit = await page.locator(selector).evaluate(node=>{
    const rect=node.getBoundingClientRect();
    const actual=document.elementFromPoint(rect.left+rect.width/2,rect.top+rect.height/2);
    return actual===node||node.contains(actual);
  });
  expect(hit).toBe(true);
  await page.locator(selector).click();
}

test('product layer CSS puts the current React/native UI layer above the previous one in both directions',async({page})=>{
  const {errors}=await setup(page);
  await page.evaluate(async()=>{
    const w=window as any;w.nativeUi=await import(/* @vite-ignore */ '/static/js/ui.js');
    document.getElementById('native-ui-hit')!.addEventListener('click',()=>{w.stats.nativeHits=(w.stats.nativeHits||0)+1;});
  });
  await page.locator('#trigger').click();await expect(content(page)).toBeVisible();
  await page.locator('#react-hit').focus();
  await page.evaluate(()=>(window as any).nativeUi.openModal('native-ui'));
  await assertPhysicalHit(page,'#native-ui-hit');
  expect(await page.evaluate(()=>(window as any).stats.nativeHits)).toBe(1);
  await page.keyboard.press('Escape');await expect(page.locator('#native-ui')).not.toBeVisible();
  await expect(page.locator('#react-hit')).toBeFocused();
  await assertPhysicalHit(page,'#react-hit');
  await page.keyboard.press('Escape');await expect(content(page)).toHaveCount(0);

  await page.evaluate(()=>(window as any).nativeUi.openModal('native-ui'));
  await page.locator('#native-ui-hit').focus();
  await page.evaluate(()=>(window as any).setDialogOpen(true));await expect(content(page)).toBeVisible();
  await assertPhysicalHit(page,'#react-hit');
  expect(await page.evaluate(()=>(window as any).stats.reactHits)).toBe(2);
  await page.keyboard.press('Escape');await expect(content(page)).toHaveCount(0);
  await expect(page.locator('#native-ui-hit')).toBeFocused();
  await assertPhysicalHit(page,'#native-ui-hit');
  expect(await page.evaluate(()=>(window as any).stats.nativeHits)).toBe(2);
  await page.keyboard.press('Escape');await expect(page.locator('#native-ui')).not.toBeVisible();
  await expect(page.locator('body')).toHaveCSS('overflow','clip');
  expect(errors).toEqual([]);
});

test('an unregistered native modal closing destroys its React child without synthetic user-close callbacks',async({page})=>{
  const {errors}=await setup(page);
  await page.evaluate(()=>{
    document.getElementById('native-slot')!.append(document.getElementById('island')!);
    (document.getElementById('native') as HTMLDialogElement).showModal();
  });
  await page.locator('#trigger').click();await expect(content(page)).toBeVisible();
  await page.evaluate(()=>(document.getElementById('native') as HTMLDialogElement).close());
  await expect(content(page)).toHaveCount(0);
  await expect(page.locator('body')).toHaveCSS('overflow','clip');
  expect(await page.evaluate(()=>(window as any).stats.closed)).toBe(0);
  expect(await page.evaluate(()=>(window as any).stats.closeFocus)).toBe(0);
  expect(errors).toEqual([]);
});
