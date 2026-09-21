import {test,expect,type Page} from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

async function mount(page:Page,options:{holdRead?:boolean;holdSave?:boolean;conflict?:boolean}={}) {
  let releaseRead!:()=>void,releaseSave!:()=>void;
  const readGate=new Promise<void>(resolve=>{releaseRead=resolve;});
  const saveGate=new Promise<void>(resolve=>{releaseSave=resolve;});
  const requests:{method:string;body:any}[]=[];
  let kind='homework',version=3;
  const errors:string[]=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/*',async route=>{
    const url=new URL(route.request().url());
    if(url.origin!=='http://lq-assessment.test')throw new Error(`Unexpected network ${url.origin}`);
    if(url.pathname==='/api/assignments/42/assessment-kind'){
      const method=route.request().method();
      requests.push({method,body:method==='PATCH'?route.request().postDataJSON():null});
      if(method==='GET'&&options.holdRead)await readGate;
      if(method==='PATCH'){
        if(options.holdSave)await saveGate;
        if(options.conflict){kind='final';version=4;await route.fulfill({status:409,json:{detail:'分类冲突'}});return;}
        kind=route.request().postDataJSON().assessment_kind;version++;
      }
      await route.fulfill({json:{assignment_id:42,assessment_kind:kind,assessment_kind_version:version}});return;
    }
    if(url.pathname.startsWith('/static/js/')||url.pathname==='/static/css/tailwind-app.css'){
      const file=path.resolve(`.${url.pathname}`);
      if(!file.startsWith(path.resolve('static')+path.sep)||!fs.existsSync(file))throw new Error(`Unexpected file ${url.pathname}`);
      await route.fulfill({contentType:file.endsWith('.css')?'text/css':'text/javascript',body:fs.readFileSync(file)});return;
    }
    if(url.pathname!=='/')throw new Error(`Unexpected fixture URL ${url.pathname}`);
    await route.fulfill({contentType:'text/html',body:`<!doctype html><html><meta charset="utf-8"><link rel="stylesheet" href="/static/css/tailwind-app.css"><body style="overflow:clip;padding-right:7px">
      <button id="parent-trigger">parent</button><dialog id="parent"><div id="slot"></div><button id="parent-hit">parent hit</button></dialog>
      <details id="menu"><summary id="opener">操作</summary><button data-assessment-kind-open="assignment-kind-modal">作业分类</button></details>
      <dialog id="assignment-kind-modal" class="assignment-kind-dialog" data-assessment-kind-dialog aria-labelledby="title">
        <h3 id="title">作业分类</h3><button data-assessment-kind-close>关闭</button>
        <div data-assessment-kind-control><select data-assessment-kind-select data-assignment-id="42" data-version="3" aria-label="设置任务分类">
          <option value="homework">平时作业</option><option value="midterm">期中测验</option><option value="final">期末测验</option>
        </select><small data-assessment-kind-note role="status"></small><button data-assessment-kind-close>取消</button><button data-assessment-kind-save disabled>保存分类</button></div>
        <button id="child-open">open child</button>
      </dialog><span data-assessment-detail-label data-assignment-id="42">平时作业</span>
      <script type="module">
        import '/static/js/assessment_kind_controls.js';import {getLayerSystem} from '/static/js/lq/layer.js';
        window.layers=getLayerSystem(document);
        document.getElementById('parent-trigger').onclick=()=>{document.getElementById('slot').append(document.getElementById('menu'));window.parentLayer=layers.open(document.getElementById('parent'),{trigger:document.getElementById('parent-trigger')});};
        document.getElementById('child-open').onclick=()=>{const child=document.createElement('div');child.innerHTML='<button id="child-hit">child hit</button>';window.child=layers.open(child,{trigger:document.getElementById('child-open'),type:'popover',modality:'non-modal',anchor:document.getElementById('child-open')});document.getElementById('child-hit').onclick=()=>window.hits=(window.hits||0)+1;};
        window.ready=true;
      </script></body></html>`});
  });
  await page.goto('http://lq-assessment.test/');await page.waitForFunction(()=>(window as any).ready);
  return {requests,errors,releaseRead,releaseSave};
}
const dialog=(page:Page)=>page.locator('#assignment-kind-modal');
const select=(page:Page)=>dialog(page).locator('select');
async function open(page:Page){await page.locator('#opener').click();await page.locator('[data-assessment-kind-open]').click();await expect(dialog(page)).toBeVisible();}

test('native classification delegates focus/locks and saves the original CAS payload once',async({page})=>{
  const {requests,errors}=await mount(page);await open(page);
  await expect(select(page)).toBeEnabled();await expect(select(page)).toBeFocused();
  await select(page).selectOption('midterm');await dialog(page).getByRole('button',{name:'保存分类',exact:true}).click();
  await expect(dialog(page)).toBeHidden();await expect(page.locator('#opener')).toBeFocused();
  await expect(page.locator('[data-assessment-detail-label]')).toHaveText('期中测验');
  expect(requests).toEqual([{method:'GET',body:null},{method:'PATCH',body:{assessment_kind:'midterm',expected_version:3}}]);
  await expect(page.locator('body')).toHaveCSS('overflow','clip');await expect(page.locator('body')).toHaveCSS('padding-right','7px');expect(errors).toEqual([]);
});

test('saving vetoes Escape and native backdrop until the actual response completes',async({page})=>{
  const {releaseSave,requests}=await mount(page,{holdSave:true});await open(page);await expect(select(page)).toBeEnabled();
  await select(page).selectOption('midterm');await dialog(page).getByRole('button',{name:'保存分类',exact:true}).click();
  await expect.poll(()=>requests.length).toBe(2);await page.keyboard.press('Escape');await page.mouse.click(3,3);
  await expect(dialog(page)).toBeVisible();await expect(dialog(page)).toHaveAttribute('data-saving','true');
  releaseSave();await expect(dialog(page)).toBeHidden();await expect(page.locator('#opener')).toBeFocused();
});

test('conflict refresh preserves the native layer for explicit reselection',async({page})=>{
  const {requests}=await mount(page,{conflict:true});await open(page);await expect(select(page)).toBeEnabled();
  await select(page).selectOption('midterm');await dialog(page).getByRole('button',{name:'保存分类',exact:true}).click();
  await expect(dialog(page).getByRole('status')).toContainText('请重新选择并保存');await expect(select(page)).toHaveValue('final');
  expect(requests.map(request=>request.method)).toEqual(['GET','PATCH','GET']);
  await page.keyboard.press('Escape');await expect(dialog(page)).toBeHidden();
});

test('late read never steals focus from a native child; Escape closes child then classification then parent',async({page})=>{
  const {releaseRead,errors}=await mount(page,{holdRead:true});await page.locator('#parent-trigger').click();await open(page);
  await page.locator('#child-open').click();await page.locator('#child-hit').click();expect(await page.evaluate(()=>(window as any).hits)).toBe(1);
  releaseRead();await expect(select(page)).toBeEnabled();await expect(page.locator('#child-hit')).toBeFocused();
  await page.keyboard.press('Escape');await expect(page.locator('#child-hit')).toHaveCount(0);await expect(dialog(page)).toBeVisible();
  await page.keyboard.press('Escape');await expect(dialog(page)).toBeHidden();await expect(page.locator('#parent')).toBeVisible();
  await expect(page.locator('#opener')).toBeFocused();await expect(page.locator('body')).toHaveCSS('overflow','hidden');
  await page.keyboard.press('Escape');await expect(page.locator('#parent')).toBeHidden();await expect(page.locator('#parent-trigger')).toBeFocused();expect(errors).toEqual([]);
});

test('forced parent destroy aborts pending read and cannot refocus or reopen the detached classification',async({page})=>{
  const {releaseRead,errors}=await mount(page,{holdRead:true});await page.locator('#parent-trigger').click();await open(page);
  await page.evaluate(()=>(window as any).parentLayer.destroy());releaseRead();
  await expect(dialog(page)).toBeHidden();await expect(page.locator('#parent')).toBeHidden();
  expect(await page.evaluate(()=>(window as any).layers.top())).toBeNull();await expect(page.locator('body')).toHaveCSS('overflow','clip');expect(errors).toEqual([]);
});
