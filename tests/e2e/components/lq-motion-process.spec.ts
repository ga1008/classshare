import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { armFrames, collectedFrames, expectFade } from '../fixtures/motion-frames';

const manifest = JSON.parse(fs.readFileSync('static/assets/manifest.json', 'utf8'));
const asset = (name: string) => `/static/${manifest.entries[name]}`;
const moduleUrl = (name: string) => asset(`js/${name}.js`);

async function mount(page: Page) {
  await page.addInitScript(() => {
    const w = window as any, native = requestAnimationFrame.bind(window);
    w.__motionNativeRAF = native; w.__motionRaf = { scheduled: 0, fired: 0 };
    window.requestAnimationFrame = callback => { w.__motionRaf.scheduled++; return native(time => { w.__motionRaf.fired++; callback(time); }); };
  });
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin !== 'https://motion-process.test') return route.abort();
    if (url.pathname.startsWith(`/static/assets/${manifest.revision}/`)) {
      const file = path.resolve(`.${url.pathname}`);
      if (!file.startsWith(path.resolve('static/assets') + path.sep) || !fs.existsSync(file)) throw Error(`Unexpected asset ${file}`);
      return route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body: fs.readFileSync(file) });
    }
    if (url.pathname !== '/') return route.fulfill({ status: 404, body: 'Only immutable production assets are allowed' });
    return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh-CN" data-theme="lanshare" data-lq-glass="tinted" data-lq-motion="standard"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Motion process</title><link rel="stylesheet" href="${asset('css/tailwind-app.css')}"><style>body{margin:0;padding:24px}main{display:grid;gap:20px;max-width:660px}#field{max-width:280px}#actions{display:flex;gap:12px;flex-wrap:wrap}</style></head><body><main><h1>动画过程验收</h1><div id="actions"></div><input id="field" class="lq-input" aria-label="保留原生输入" value="原生草稿"><div id="navigation"></div><div id="disclosure"></div></main><dialog id="native" class="lq-native-dialog lq-domain-raised" data-lq-material="raised"><input aria-label="原生对话框草稿" value="保留原生内容"></dialog><script type="module">
      import {createComponent} from '${moduleUrl('lq/components')}';
      import * as dialogs from '${moduleUrl('lq/dialogs')}';
      import {getLayerSystem} from '${moduleUrl('lq/layer')}';
      import {createMenu,bindMenu} from '${moduleUrl('lq/menus')}';
      import {createNavigation,tabs} from '${moduleUrl('lq/navigation')}';
      import {createCollapsible,enhanceCollapsible} from '${moduleUrl('lq/collapsible')}';
      import {toast} from '${moduleUrl('lq/toast')}';
      import {createDomainPopoverSystem} from '${moduleUrl('lq/domain-controls')}';
      import {createDomainModal} from '${moduleUrl('lq/domain-modal')}';
      import {openProcessMaterialModal} from '${moduleUrl('process_material_modal')}';
      import {openModal,closeModal} from '${moduleUrl('ui')}';
      const w=window;document.documentElement.dataset.appearance=matchMedia('(prefers-color-scheme:dark)').matches?'dark':'light';
      const nativeSurface=document.querySelector('#native').cloneNode(true);nativeSurface.id='native-surface';nativeSurface.dataset.lqComponent='surface';document.body.append(nativeSurface);
      const actions=document.querySelector('#actions');
      for(const p of [{id:'press',label:'按压释放',variant:'glass'},{id:'disabled',label:'不可用',disabled:true},{id:'loading',label:'正在保存',loading:true},{id:'menu-trigger',label:'动作菜单'}])actions.append(createComponent('button',p));
      const layer=getLayerSystem(document), roots={}, handles={};
      for(const type of ['modal','sheet','drawer','popover']){roots[type]=dialogs.createDialog({id:'presence-'+type,type,title:'共享'+type,body:'退出完成前保留的内容'});document.body.append(roots[type]);}
      const menu=createMenu({id:'motion-menu',label:'动作',items:[{id:'first',label:'第一个动作'},{id:'second',label:'第二个动作'}]});
      const menuHandle=bindMenu(document.querySelector('#menu-trigger'),menu);
      const nav=createNavigation('segment',{id:'views',label:'视图切换',items:[{key:'a',label:'课程',panel:'课程正文'},{key:'b',label:'资料',panel:'资料正文'}]});document.querySelector('#navigation').append(nav);const navigation=tabs(nav);
      const draft=document.createElement('input');draft.className='lq-input';draft.value='展开保留草稿';draft.setAttribute('aria-label','展开草稿');
      const details=createCollapsible({id:'details',title:'详情展开',mode:'always',open:false},[draft]);document.querySelector('#disclosure').append(details);const disclosure=enhanceCollapsible(details);
      const popoverSystem=createDomainPopoverSystem({prefix:'ls'}),domainPanel=document.createElement('div');domainPanel.id='domain-panel';domainPanel.innerHTML='<input class="lq-input" aria-label="领域草稿" value="课件白板原生草稿">';
      const domainEvents=[];const domainPopover=popoverSystem.createPopover({panel:domainPanel,anchor:document.querySelector('#press'),modal:true,label:'领域编辑器',onClose:reason=>domainEvents.push(['close',reason]),onAfterClose:reason=>domainEvents.push(['after',reason])});
      const domainRoot=document.createElement('div');domainRoot.id='adapter-root';domainRoot.className='lq-domain-region';domainRoot.hidden=true;domainRoot.innerHTML='<section class="lq-domain-raised" data-lq-component="surface" data-lq-material="raised"><input class="lq-input" aria-label="管理弹窗草稿" value="保持原节点"></section>';document.querySelector('main').append(domainRoot);
      const adapterEvents=[];let allowAdapterClose=false;const adapter=createDomainModal(domainRoot,{surface:domainRoot.firstElementChild,beforeClose:()=>allowAdapterClose,onClose:()=>adapterEvents.push('closed'),onDestroy:()=>adapterEvents.push('destroyed')});
      const legacy=document.createElement('div');legacy.id='legacy-modal';legacy.className='modal-backdrop';legacy.style.display='none';legacy.innerHTML='<section class="modal-dialog"><input class="lq-input" aria-label="旧入口草稿" value="旧入口原生内容"></section>';document.body.append(legacy);
      w.motion={layer,roots,handles,menuHandle,navigation,disclosure,draft,toast:null,domainPopover,domainEvents,adapter,adapterEvents,setAdapterClose:value=>allowAdapterClose=value,openProcessMaterialModal,openModal,closeModal,
        open(type){document.querySelector('#press').focus();if(type.startsWith('native'))handles[type]=layer.open(document.querySelector('#'+type),{type:'modal',trigger:document.querySelector('#press')});else handles[type]=dialogs.openDialog(roots[type],{trigger:document.querySelector('#press'),anchor:document.querySelector('#press')});},
        close(type){return layer.close(handles[type],'test');},menu(open){return open?menuHandle.open():menuHandle.close('test');},
        notify(){this.toast=toast('保存完成',{duration:0});return this.toast;}};
      document.body.dataset.ready='true';
    </script></body></html>` });
  });
  await page.goto('https://motion-process.test/');
  await expect(page.locator('body')).toHaveAttribute('data-ready', 'true');
}

async function actionFrames(page: Page, selector: string, action: string, value?: string | boolean) {
  await armFrames(page, selector);
  await page.evaluate(({ action, value }) => { const m = (window as any).motion; if (action === 'close') void m.close(value); else if(action==='toast-close') void m.toast.close(); else if(action==='details') m.disclosure.setOpen(value); else m[action](value); }, { action, value });
  return collectedFrames(page);
}
test.beforeEach(async ({ page }) => mount(page));
test.afterEach(async ({ page }, info) => {
  const revisions=await page.evaluate(()=>[...new Set(performance.getEntriesByType('resource').map(e=>e.name.match(/\/static\/assets\/([a-f0-9]{64})\//)?.[1]).filter(Boolean))]);
  expect(revisions).toEqual([manifest.revision]);
  await info.attach('asset-graph',{body:JSON.stringify(revisions),contentType:'application/json'});
});

test('LQ immutable domain modal cancels stale exits and separates immediate cancellation from cleanup',async({page},info)=>{
  await armFrames(page,'#domain-panel');await page.evaluate(()=>(window as any).motion.domainPopover.open());const entering=await collectedFrames(page);expectFade(entering,'domain modal enter');
  await page.getByRole('textbox',{name:'领域草稿'}).fill('快速反向仍保留的课件草稿');
  const result=await page.evaluate(async()=>{
    const m=(window as any).motion;const pending=m.domainPopover.close('test');const immediate=[...m.domainEvents];
    await new Promise(resolve=>(window as any).__motionNativeRAF(resolve));m.domainPopover.open();
    return {immediate,cancelled:await pending,visible:!m.domainPopover.panel.hidden,backdrops:document.querySelectorAll('.ls-backdrop').length};
  });
  expect(result.immediate).toEqual([['close','test']]);expect(result.cancelled).toBe(false);expect(result.visible).toBe(true);expect(result.backdrops).toBe(1);
  await expect(page.getByRole('textbox',{name:'领域草稿'})).toHaveValue('快速反向仍保留的课件草稿');
  await armFrames(page,'#domain-panel');await page.evaluate(()=>{void (window as any).motion.domainPopover.close('finished');});const leaving=await collectedFrames(page);expectFade(leaving,'domain modal exit');
  expect(await page.evaluate(()=>(window as any).motion.domainEvents)).toEqual([['close','test'],['close','finished'],['after','finished']]);
  await page.evaluate(()=>{const p=(window as any).motion.domainPopover;p.open();p.destroy();});
  await expect(page.locator('#domain-panel,.ls-backdrop')).toHaveCount(0);
  await info.attach('domain-modal-process',{body:JSON.stringify({entering,leaving,result}),contentType:'application/json'});
});

test('LQ immutable domain adapter keeps veto reversal and destroy restoration under one owner',async({page},info)=>{
  await armFrames(page,'#adapter-root > section');await page.evaluate(()=>(window as any).motion.adapter.open({trigger:document.querySelector('#press')}));const entering=await collectedFrames(page);expectFade(entering,'domain adapter enter');
  await expect(page.getByRole('textbox',{name:'管理弹窗草稿'})).toHaveValue('保持原节点');
  expect(await page.evaluate(()=>(window as any).motion.adapter.close())).toBe(false);
  const reversal=await page.evaluate(async()=>{
    const m=(window as any).motion;m.setAdapterClose(true);const closing=m.adapter.close();
    await new Promise(resolve=>(window as any).__motionNativeRAF(resolve));m.adapter.open();
    return {completed:await closing,events:[...m.adapterEvents],state:m.adapter.handle.state};
  });expect(reversal.completed).toBe(false);expect(reversal.events).toEqual([]);
  await expect(page.locator('#adapter-root > section')).toHaveCSS('opacity','1');
  await armFrames(page,'#adapter-root > section');await page.evaluate(()=>{void (window as any).motion.adapter.close();});const leaving=await collectedFrames(page);expectFade(leaving,'domain adapter exit');
  await page.evaluate(()=>{const m=(window as any).motion;m.adapter.open();m.adapter.destroy();});
  await expect(page.locator('main > #adapter-root')).toHaveCount(1);
  expect(await page.evaluate(()=>(window as any).motion.adapterEvents)).toEqual(['closed','destroyed']);
  await page.evaluate(()=>(window as any).motion.adapter.open());await expect(page.locator('#adapter-root')).toBeVisible();await page.evaluate(()=>(window as any).motion.adapter.close());
  await info.attach('domain-adapter-process',{body:JSON.stringify({entering,leaving,reversal}),contentType:'application/json'});
});

test('LQ immutable process force bypasses veto but keeps exit content and one final cleanup',async({page},info)=>{
  await armFrames(page,'.lp-modal');await page.evaluate(()=>{
    const m=(window as any).motion;m.processClosed=0;m.process=m.openProcessMaterialModal('过程材料', '<input class="lq-input" value="已保存的合成内容">',{canClose:()=>false,onClose:()=>m.processClosed++});
  });const entering=await collectedFrames(page);expectFade(entering,'process modal enter');
  expect(await page.evaluate(()=>(window as any).motion.process.close())).toBe(false);
  await armFrames(page,'.lp-modal');await page.evaluate(()=>{const m=(window as any).motion;m.forcePromise=Promise.all([m.process.close({force:true}),m.process.close({force:true})]);});
  const leaving=await collectedFrames(page);expectFade(leaving,'forced process modal exit');
  expect(leaving.filter(f=>f.visible&&f.opacity>.03).every(f=>f.text.includes('过程材料'))).toBe(true);
  await page.evaluate(()=>(window as any).motion.forcePromise);await expect(page.locator('.lp-modal-overlay')).toHaveCount(0);
  expect(await page.evaluate(()=>(window as any).motion.processClosed)).toBe(1);
  await info.attach('forced-process-presence',{body:JSON.stringify({entering,leaving}),contentType:'application/json'});
});

test('LQ immutable legacy UI bridge primes the first frame and reverses without resetting content',async({page},info)=>{
  await page.locator('#press').focus();
  await armFrames(page,'#legacy-modal > .modal-dialog');
  await page.evaluate(()=>(window as any).motion.openModal('legacy-modal'));
  const entering=await collectedFrames(page);expectFade(entering,'legacy UI bridge first open');
  await page.getByRole('textbox',{name:'旧入口草稿'}).fill('中文原生草稿不丢失');
  const reversal=await page.evaluate(async()=>{
    const m=(window as any).motion, root=document.querySelector('#legacy-modal')!, surface=root.firstElementChild!;
    const input=root.querySelector('input');const closing=m.closeModal('legacy-modal');
    await new Promise(resolve=>(window as any).__motionNativeRAF(resolve));
    const before=Number(getComputedStyle(surface).opacity);m.openModal('legacy-modal');
    const after=Number(getComputedStyle(surface).opacity);return {before,after,completed:await closing,sameInput:input===root.querySelector('input')};
  });
  expect(reversal.completed).toBe(false);expect(reversal.sameInput).toBe(true);expect(Math.abs(reversal.before-reversal.after)).toBeLessThan(.08);
  await expect(page.locator('#legacy-modal > .modal-dialog')).toHaveCSS('opacity','1');
  await expect(page.getByRole('textbox',{name:'旧入口草稿'})).toHaveValue('中文原生草稿不丢失');
  await armFrames(page,'#legacy-modal > .modal-dialog');await page.evaluate(()=>{void (window as any).motion.closeModal('legacy-modal');});
  const leaving=await collectedFrames(page);expectFade(leaving,'legacy UI bridge exit');
  await expect(page.locator('#legacy-modal')).toBeHidden();await expect(page.locator('#press')).toBeFocused();
  await info.attach('legacy-ui-bridge-process',{body:JSON.stringify({entering,leaving,reversal}),contentType:'application/json'});
});

test('LQ immutable button and field states traverse real intermediate frames',async({page,isMobile},info)=>{
  const button=page.locator('#press'), box=(await button.boundingBox())!;
  await armFrames(page,'#press');
  if(isMobile){const cdp=await page.context().newCDPSession(page);await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:box.x+box.width/2,y:box.y+box.height/2}]});await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});await cdp.detach();}
  else {await page.mouse.move(box.x+box.width/2,box.y+box.height/2);await page.mouse.down();}
  const press=await collectedFrames(page);expect(press.some(f=>f.d<.999&&f.d>.966)).toBe(true);
  if(!isMobile){await page.mouse.up();await page.mouse.move(0,0);}
  await expect.poll(()=>button.evaluate(n=>new DOMMatrixReadOnly(getComputedStyle(n).transform).d)).toBe(1);
  await armFrames(page,'#field');await page.locator('#field').focus();const field=await collectedFrames(page);
  await info.attach('control-process',{body:JSON.stringify({press,field}),contentType:'application/json'});
  expect(new Set(field.map(f=>f.shadow+'|'+f.border)).size).toBeGreaterThan(2);
  expect(field.every(f=>f.a===1&&f.d===1&&f.x===0&&f.y===0)).toBe(true);
  await page.locator('#field').fill('中文输入草稿');await expect(page.locator('#field')).toHaveValue('中文输入草稿');
});

test('LQ immutable modal sheet drawer popover and native dialog animate the first open and exit',async({page},info)=>{
  const records:Record<string,unknown>={};
  for(const type of ['modal','sheet','drawer','popover','native','native-surface']){
    const selector=type.startsWith('native')?'#'+type:`#presence-${type} .lq-dialog__surface`;
    const entering=await actionFrames(page,selector,'open',type);expectFade(entering,`${type} first open`);
    const leaving=await actionFrames(page,selector,'close',type);expectFade(leaving,`${type} exit`);
    await expect(page.locator(selector)).toBeHidden();records[type]={entering,leaving};
    expect(await page.evaluate(()=>document.body.style.overflow)).toBe('');
  }
  await info.attach('layer-process',{body:JSON.stringify(records),contentType:'application/json'});
});

test('LQ immutable menu and toast retain both presence directions and loading stays named',async({page},info)=>{
  const entering=await actionFrames(page,'#motion-menu','menu',true);expectFade(entering,'menu enter');
  const leaving=await actionFrames(page,'#motion-menu','menu',false);expectFade(leaving,'menu exit');
  const toastIn=await actionFrames(page,'.lq-toast','notify');expectFade(toastIn,'toast enter');
  const toastOut=await actionFrames(page,'.lq-toast','toast-close');expectFade(toastOut,'toast exit');
  await expect(page.locator('#loading')).toHaveAttribute('aria-busy','true');await expect(page.locator('#loading')).toContainText('正在保存');
  expect(await page.locator('#loading .lq-spinner').evaluate(n=>n.getAnimations().some(a=>a.playState==='running'))).toBe(true);
  await info.attach('menu-feedback-process',{body:JSON.stringify({entering,leaving,toastIn,toastOut}),contentType:'application/json'});
});

test('LQ immutable details and tab panels animate content without replacing drafts',async({page},info)=>{
  const entering=await actionFrames(page,'#details::details-content','details',true);expectFade(entering,'details enter');
  const leaving=await actionFrames(page,'#details::details-content','details',false);expectFade(leaving,'details exit');
  await armFrames(page,'#views--lq-panel-b','click');await page.locator('#views--lq-tab-b').click();const tab=await collectedFrames(page);expectFade(tab,'tab enter');
  await page.evaluate(()=>{const m=(window as any).motion;m.disclosure.setOpen(true);m.disclosure.setOpen(false);m.disclosure.setOpen(true);});
  await expect(page.getByRole('textbox',{name:'展开草稿'})).toHaveValue('展开保留草稿');
  expect(await page.evaluate(()=>(window as any).motion.draft===document.querySelector('#details input'))).toBe(true);
  await expect(page.locator('#views [role="tabpanel"]:visible')).toHaveCount(1);
  await info.attach('content-process',{body:JSON.stringify({entering,leaving,tab}),contentType:'application/json'});
});

test('LQ immutable reversal continues from the live frame and settles without idle scheduling',async({page},info)=>{
  const result=await page.evaluate(async()=>{
    const m=(window as any).motion, frame=()=>new Promise(resolve=>(window as any).__motionNativeRAF(resolve));
    m.open('drawer');const surface=m.roots.drawer.querySelector('.lq-dialog__surface');
    const read=()=>Number(getComputedStyle(surface).opacity);const start=performance.now();
    while((read()<.05||read()>.90)&&performance.now()-start<800)await frame();
    const before=read();const closing=m.close('drawer');await frame();
    const beforeReopen=read();m.open('drawer');const afterReopen=read();
    const cancelled=await closing;return {before,beforeReopen,afterReopen,cancelled};
  });
  expect(result.before).toBeGreaterThan(.03);expect(result.before).toBeLessThan(.97);
  expect(Math.abs(result.afterReopen-result.beforeReopen)).toBeLessThan(.08);expect(result.cancelled).toBe(false);
  await expect(page.locator('#presence-drawer .lq-dialog__surface')).toHaveCSS('opacity','1');
  await page.evaluate(async()=>{await (window as any).motion.close('drawer');document.querySelector('#loading')!.remove();document.querySelector('#press')!.blur();});
  await page.mouse.move(0,0);await page.waitForTimeout(350);
  const before=await page.evaluate(()=>(window as any).__motionRaf);await page.waitForTimeout(350);
  expect(await page.evaluate(()=>(window as any).__motionRaf)).toEqual(before);
  expect(await page.evaluate(()=>document.getAnimations().filter(a=>a.playState==='running').length)).toBe(0);
  await info.attach('reversal-and-idle',{body:JSON.stringify({result,raf:before}),contentType:'application/json'});
});

for(const mode of ['off','reduced'])test(`LQ immutable ${mode} removes geometry processes while preserving actions`,async({page})=>{
  if(mode==='reduced')await page.emulateMedia({reducedMotion:'reduce'});
  else await page.locator('html').evaluate(n=>n.setAttribute('data-lq-motion','off'));
  for(const type of ['modal','drawer','native','native-surface']){
    await page.evaluate(type=>(window as any).motion.open(type),type);
    const selector=type.startsWith('native')?'#'+type:`#presence-${type} .lq-dialog__surface`;
    await expect(page.locator(selector)).toBeVisible();
    expect(await page.locator(selector).evaluate(n=>n.getAnimations().filter(a=>a.playState==='running').length)).toBe(0);
    await page.evaluate(async type=>await (window as any).motion.close(type),type);await expect(page.locator(selector)).toBeHidden();
  }
  await page.evaluate(()=>{(window as any).motion.disclosure.setOpen(true);});await expect(page.getByRole('textbox',{name:'展开草稿'})).toBeVisible();
  await page.locator('#views--lq-tab-b').click();await expect(page.locator('#views--lq-panel-b')).toBeVisible();
  expect(await page.locator('#loading .lq-spinner').evaluate(n=>getComputedStyle(n).animationPlayState)).toBe('paused');
});
